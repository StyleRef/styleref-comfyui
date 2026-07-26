"""
Credentials and OAuth 2.1 (auth code + PKCE, loopback) for the StyleRef nodes.

Deliberately a port of the `styleref` CLI's login flow rather than a new one:
the token file, its 0600 permissions, the scopes, and the refresh behaviour are
all the CLI's, so signing in with either tool signs in both.

Pure helpers live here and are unit-tested; `run_login_flow` is the one function
that touches the network and a browser.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from styleref_api import DEFAULT_API_BASE, StyleRefError, request_json, site_origin_from_api_base

# Identical to the CLI's scope set on purpose: the two tools share one
# credentials file, so a login from either must satisfy both. (`extract` is in
# the set for the CLI's sake; the plugin doesn't extract — that lives on the web
# app, where you can preview and refine the result.)
SCOPES = "styles:read styles:write extract"

# Refresh this long before the token actually dies, so a slow extraction that
# started with a valid token doesn't expire mid-poll.
EXPIRY_MARGIN_S = 30


# ── credentials file ─────────────────────────────────────────────────────────


def credentials_path(env: dict[str, str] | None = None) -> str:
    """$XDG_CONFIG_HOME/styleref/credentials.json — identical to the CLI's."""
    environ = os.environ if env is None else env
    base = (environ.get("XDG_CONFIG_HOME") or "").strip()
    if not base:
        base = os.path.join(environ.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "styleref", "credentials.json")


def load_credentials() -> dict[str, Any] | None:
    try:
        with open(credentials_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_credentials(creds: dict[str, Any]) -> None:
    """Write 0600. The mode is set before the token is written, not after."""
    path = credentials_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Open with the restrictive mode up front — writing first and chmod-ing
    # after leaves a window where the token is world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(creds, fh, indent=2)
        fh.write("\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def clear_credentials() -> bool:
    try:
        os.unlink(credentials_path())
        return True
    except OSError:
        return False


def is_expired(creds: dict[str, Any], now_ms: float | None = None) -> bool:
    now = time.time() * 1000 if now_ms is None else now_ms
    expires_at = creds.get("expires_at") or 0
    return not expires_at or now >= expires_at - EXPIRY_MARGIN_S * 1000


def to_stored_credentials(
    tokens: dict[str, Any],
    token_endpoint: str,
    client_id: str,
    now_ms: float | None = None,
) -> dict[str, Any]:
    now = time.time() * 1000 if now_ms is None else now_ms
    return {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": now + float(tokens.get("expires_in") or 0) * 1000,
        "token_endpoint": token_endpoint,
        "client_id": client_id,
    }


# ── PKCE ─────────────────────────────────────────────────────────────────────


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_pkce_pair() -> tuple[str, str]:
    """(verifier, challenge). 64 random bytes → 86 chars, inside RFC 7636's 43–128."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def generate_state() -> str:
    return _b64url(secrets.token_bytes(24))


def build_authorize_url(
    origin: str, client_id: str, redirect_uri: str, challenge: str, state: str
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": SCOPES,
        }
    )
    return f"{origin}/oauth/authorize?{query}"


# ── headless detection ───────────────────────────────────────────────────────


def is_headless(env: dict[str, str] | None = None) -> bool:
    """
    True when we almost certainly cannot open a browser on this machine.

    a large share of ComfyUI installs are remote GPU boxes where
    the loopback flow cannot work *at all* — localhost is the wrong machine.
    Detecting that up front lets the Login node print the STYLEREF_TOKEN
    instructions instead of hanging on a callback that will never arrive.
    """
    environ = os.environ if env is None else env

    # An explicit override wins in both directions — someone port-forwarding
    # into a remote box may genuinely have a working loopback.
    forced = (environ.get("STYLEREF_FORCE_BROWSER_LOGIN") or "").strip().lower()
    if forced in ("1", "true"):
        return False
    if forced in ("0", "false"):
        return True

    # Containers are the common remote-GPU packaging and never have a browser.
    if os.path.exists("/.dockerenv"):
        return True
    if environ.get("SSH_CONNECTION") or environ.get("SSH_TTY"):
        return True

    if sys.platform in ("darwin", "win32"):
        return False

    # Linux: no display server and no opener binary means no browser.
    if environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"):
        return False
    return shutil.which("xdg-open") is None


# Copying the credentials file is the recommended headless path because it
# carries the refresh token, so this code keeps renewing the access token on its
# own. STYLEREF_TOKEN is offered second and warned about: an access token lives
# about an hour and the env path never refreshes it, which turns into
# unexplained 401s later.
HEADLESS_HELP = (
    "This machine can't open a browser, so the sign-in redirect has nowhere to land.\n"
    "That's normal for a rented GPU box, RunPod, Docker, or any headless server.\n"
    "\n"
    "Recommended — copy your credentials from a machine that has a browser:\n"
    "  1. There, run:  npx styleref login\n"
    "  2. Copy ~/.config/styleref/credentials.json to the same path here\n"
    "     (e.g. scp ~/.config/styleref/credentials.json user@host:~/.config/styleref/)\n"
    "  3. chmod 600 the copy, then re-check this node's status.\n"
    "That file carries a refresh token, so the session renews itself and keeps\n"
    "working for weeks.\n"
    "\n"
    "Short-lived alternative — set STYLEREF_TOKEN=<access token> where ComfyUI\n"
    "runs and restart it. STYLEREF_TOKEN always wins over stored credentials, so\n"
    "it is also how you override a login on a shared machine — but an access\n"
    "token expires in about an hour and is never refreshed, so expect 401s after\n"
    "that. Prefer it only for a single short run or a CI job."
)


# ── the interactive flow ─────────────────────────────────────────────────────


_SUCCESS_PAGE = (
    b"<html><body style='font-family:system-ui;padding:40px'>"
    b"<p>Signed in to StyleRef \xe2\x80\x94 you can close this tab and "
    b"return to ComfyUI.</p></body></html>"
)

_FAILURE_PAGE = (
    b"<html><body style='font-family:system-ui;padding:40px'>"
    b"<p>Sign-in didn't complete \xe2\x80\x94 return to ComfyUI and try again.</p>"
    b"</body></html>"
)


class _CallbackHandler(BaseHTTPRequestHandler):
    """Serves exactly one /callback hit and records the code."""

    result: dict[str, Any] = {}
    expected_state = ""

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        code = (params.get("code") or [None])[0]
        state = (params.get("state") or [None])[0]

        # Validate BEFORE choosing the page: the browser must never
        # claim success while the node reports a failed sign-in.
        if not code or state != type(self).expected_state:
            # A state mismatch is the CSRF case: discard the code entirely.
            type(self).result = {"error": "authorization callback missing code or state mismatch"}
            page = _FAILURE_PAGE
        else:
            type(self).result = {"code": code}
            page = _SUCCESS_PAGE

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *_args: Any) -> None:
        """Silence the default stderr access log — it would print the code."""


def _try_open_browser(url: str) -> None:
    """Best effort. Printing the URL is the actual contract."""
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - the printed URL is the fallback
        pass


def run_login_flow(api_base: str = DEFAULT_API_BASE, timeout_s: int = 180) -> tuple[bool, str]:
    """
    Full OAuth 2.1 auth-code + PKCE loopback login. Returns (ok, message).

    Never raises: the Login node renders the message either way, and a raised
    exception inside a ComfyUI node is a red toast with no instructions in it.
    """
    if is_headless():
        return False, HEADLESS_HELP

    origin = site_origin_from_api_base(api_base)
    state = generate_state()

    # 1. Loopback receiver on an ephemeral 127.0.0.1 port.
    _CallbackHandler.expected_state = state
    _CallbackHandler.result = {}
    try:
        server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    except OSError as err:
        return False, f"Could not bind a local callback port: {err}\n\n{HEADLESS_HELP}"
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    try:
        # 2. Dynamic client registration. Explicitly anonymous — a stale stored
        #    token must never be able to block a fresh login.
        try:
            reg = request_json(
                f"{origin}/api/oauth/register",
                method="POST",
                json_body={
                    "client_name": "StyleRef ComfyUI nodes",
                    "redirect_uris": [redirect_uri],
                    "token_endpoint_auth_method": "none",
                    "scope": SCOPES,
                },
                anonymous=True,
            )
        except StyleRefError as err:
            return False, f"Could not register with StyleRef: {err}"

        client_id = reg.get("client_id")
        if not client_id:
            return False, "StyleRef did not return a client id. Try again shortly."

        # 3. Send the user to the consent page.
        verifier, challenge = generate_pkce_pair()
        authorize_url = build_authorize_url(origin, client_id, redirect_uri, challenge, state)

        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        _try_open_browser(authorize_url)

        # 4. Wait for the redirect.
        thread.join(timeout=timeout_s)
        if thread.is_alive():
            return False, (
                f"Timed out after {timeout_s}s waiting for the sign-in redirect.\n"
                f"If your browser never opened, visit this URL manually and retry:\n\n"
                f"  {authorize_url}\n\n{HEADLESS_HELP}"
            )

        result = _CallbackHandler.result
        if "code" not in result:
            return False, result.get("error", "Sign-in was cancelled or failed.")

        # 5. Exchange the code. Anonymous again for the same reason as step 2.
        token_endpoint = f"{origin}/api/oauth/token"
        try:
            tokens = request_json(
                token_endpoint,
                method="POST",
                form_body={
                    "grant_type": "authorization_code",
                    "code": result["code"],
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                },
                anonymous=True,
            )
        except StyleRefError as err:
            return False, f"Could not exchange the authorization code: {err}"

        if not tokens.get("access_token"):
            return False, "StyleRef did not return an access token. Try signing in again."

        save_credentials(to_stored_credentials(tokens, token_endpoint, client_id))
        return True, (
            f"Signed in to StyleRef. Credentials stored at {credentials_path()} (0600).\n"
            "The styleref CLI shares this login. Your private styles now resolve too — "
            "by their share slug, id, or name."
        )
    finally:
        server.server_close()


def refresh_credentials(creds: dict[str, Any]) -> dict[str, Any] | None:
    """Rotate an expired access token. Returns None when the session is dead."""
    endpoint = creds.get("token_endpoint")
    if not endpoint or not creds.get("refresh_token"):
        return None
    try:
        tokens = request_json(
            endpoint,
            method="POST",
            form_body={
                "grant_type": "refresh_token",
                "refresh_token": creds["refresh_token"],
                "client_id": creds.get("client_id", ""),
            },
            anonymous=True,
        )
    except StyleRefError:
        return None
    if not tokens.get("access_token"):
        return None
    nxt = to_stored_credentials(tokens, endpoint, creds.get("client_id", ""))
    save_credentials(nxt)
    return nxt


def bearer_token() -> str | None:
    """
    The token to send, or None for anonymous.

    STYLEREF_TOKEN wins over stored credentials — it is the documented path for
    headless installs, so it must not be second-guessed.
    """
    env_token = (os.environ.get("STYLEREF_TOKEN") or "").strip()
    if env_token:
        return env_token

    creds = load_credentials()
    if not creds:
        return None
    if is_expired(creds):
        creds = refresh_credentials(creds)
        if not creds:
            # Degrade to anonymous rather than hard-failing: the public nodes
            # keep working, and the auth-only nodes give a clear 401 message.
            return None
    return creds.get("access_token")


def auth_status() -> tuple[bool, str]:
    """(signed_in, human summary) for the Login node's status output."""
    if (os.environ.get("STYLEREF_TOKEN") or "").strip():
        return True, "Signed in via the STYLEREF_TOKEN environment variable."
    creds = load_credentials()
    if not creds:
        return False, "Not signed in. Run the StyleRef Login node, or set STYLEREF_TOKEN."
    if is_expired(creds) and not creds.get("refresh_token"):
        return False, "Stored session expired. Run the StyleRef Login node again."
    return True, f"Signed in — credentials at {credentials_path()}."
