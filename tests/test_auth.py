"""Auth tests — PKCE, credentials file handling, expiry, headless detection."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat

import styleref_auth as auth

# ── PKCE ─────────────────────────────────────────────────────────────────────


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = auth.generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert challenge == expected


def test_pkce_verifier_length_within_rfc7636():
    verifier, _ = auth.generate_pkce_pair()
    assert 43 <= len(verifier) <= 128


def test_pkce_pairs_are_unique():
    assert auth.generate_pkce_pair()[0] != auth.generate_pkce_pair()[0]


def test_authorize_url_carries_pkce_and_scopes():
    url = auth.build_authorize_url(
        "https://styleref.io", "client-1", "http://127.0.0.1:5000/callback", "chal", "st"
    )
    assert url.startswith("https://styleref.io/oauth/authorize?")
    for fragment in ("response_type=code", "code_challenge=chal", "code_challenge_method=S256", "state=st"):
        assert fragment in url
    # Scopes stay identical to the CLI's so one credentials file serves both.
    assert "extract" in url


# ── credentials ──────────────────────────────────────────────────────────────


def test_credentials_path_prefers_xdg():
    path = auth.credentials_path({"XDG_CONFIG_HOME": "/cfg", "HOME": "/home/u"})
    assert path == os.path.join("/cfg", "styleref", "credentials.json")


def test_credentials_path_matches_the_cli_default():
    """CLI and nodes must share one file, or signing in twice becomes necessary."""
    path = auth.credentials_path({"HOME": "/home/u"})
    assert path == os.path.join("/home/u", ".config", "styleref", "credentials.json")


def test_saved_credentials_are_owner_only(tmp_path, monkeypatch):
    """A world-readable token file would leak the account on a shared box."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    auth.save_credentials({"access_token": "secret", "expires_at": 0})

    path = auth.credentials_path()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["access_token"] == "secret"


def test_load_credentials_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert auth.load_credentials() is None


def test_load_credentials_survives_a_corrupt_file(tmp_path, monkeypatch):
    """A truncated file must degrade to anonymous, not crash every node."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = auth.credentials_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("{not json")
    assert auth.load_credentials() is None


# ── expiry ───────────────────────────────────────────────────────────────────


def test_is_expired_uses_a_safety_margin():
    """
    A token expiring in 10s is treated as expired: an extraction started now
    would outlive it mid-poll.
    """
    now = 1_000_000.0
    assert auth.is_expired({"expires_at": now + 10_000}, now_ms=now) is True
    assert auth.is_expired({"expires_at": now + 120_000}, now_ms=now) is False


def test_missing_expiry_counts_as_expired():
    assert auth.is_expired({}) is True


def test_to_stored_credentials_converts_expires_in_to_absolute_ms():
    stored = auth.to_stored_credentials(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        "https://styleref.io/api/oauth/token",
        "client-1",
        now_ms=1_000_000.0,
    )
    assert stored["expires_at"] == 1_000_000.0 + 3_600_000
    assert stored["client_id"] == "client-1"


# ── token selection ──────────────────────────────────────────────────────────


def test_env_token_wins_over_stored_credentials(tmp_path, monkeypatch):
    """STYLEREF_TOKEN is the documented headless path — it must not be overridden."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    auth.save_credentials({"access_token": "stored", "expires_at": 9e15})
    monkeypatch.setenv("STYLEREF_TOKEN", "from-env")
    assert auth.bearer_token() == "from-env"


def test_bearer_token_is_none_when_signed_out(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("STYLEREF_TOKEN", raising=False)
    assert auth.bearer_token() is None


def test_dead_session_degrades_to_anonymous(tmp_path, monkeypatch):
    """Public nodes must keep working when a refresh fails."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("STYLEREF_TOKEN", raising=False)
    auth.save_credentials({"access_token": "old", "expires_at": 0, "refresh_token": "r"})
    monkeypatch.setattr(auth, "refresh_credentials", lambda _c: None)
    assert auth.bearer_token() is None


# ── headless detection ───────────────────────────────────────────────────────


def test_ssh_session_is_headless():
    assert auth.is_headless({"SSH_CONNECTION": "10.0.0.1 22", "DISPLAY": ":0"}) is True


def test_override_forces_browser_login():
    """Someone port-forwarding into a remote box has a working loopback."""
    assert auth.is_headless({"SSH_CONNECTION": "x", "STYLEREF_FORCE_BROWSER_LOGIN": "1"}) is False


def test_override_can_force_headless_on_a_desktop():
    assert auth.is_headless({"STYLEREF_FORCE_BROWSER_LOGIN": "0"}) is True


def test_headless_help_leads_with_the_credentials_file_then_the_env_var():
    """
    This text is the entire recovery path for remote users — keep it complete.

    Copying credentials.json is the primary path because it carries the refresh
    token, so the session renews itself. STYLEREF_TOKEN must stay documented as
    the short-lived fallback, with its ~1h expiry stated: an access token that
    silently stops working is the failure mode this help text exists to prevent.
    There is no token UI on the account page, so it must not be named here.
    """
    help_text = auth.HEADLESS_HELP
    assert "credentials.json" in help_text
    assert "npx styleref login" in help_text
    assert "STYLEREF_TOKEN" in help_text
    assert "hour" in help_text
    assert "styleref.io/account" not in help_text
    # The refreshing path must come first, or readers take the expiring one.
    assert help_text.index("credentials.json") < help_text.index("STYLEREF_TOKEN")


# ── OAuth callback page ──────────────────────────────────────────────────────


class _FakeWFile:
    def __init__(self):
        self.data = b""

    def write(self, chunk: bytes) -> None:
        self.data += chunk


def _run_callback(path: str, expected_state: str) -> tuple[dict, bytes]:
    """Drive _CallbackHandler.do_GET without a socket."""
    handler = object.__new__(auth._CallbackHandler)
    handler.path = path
    handler.wfile = _FakeWFile()
    handler.send_response = lambda *_a, **_k: None
    handler.send_header = lambda *_a, **_k: None
    handler.end_headers = lambda *_a, **_k: None

    auth._CallbackHandler.expected_state = expected_state
    auth._CallbackHandler.result = {}
    handler.do_GET()
    return auth._CallbackHandler.result, handler.wfile.data


def test_valid_callback_serves_the_success_page():
    result, page = _run_callback("/callback?code=abc&state=st", expected_state="st")
    assert result == {"code": "abc"}
    assert b"Signed in to StyleRef" in page


def test_state_mismatch_serves_the_failure_page_and_discards_the_code():
    """
    CSRF case: the browser must NOT claim success while the node reports a
    failed sign-in — the page and the result must agree.
    """
    result, page = _run_callback("/callback?code=abc&state=WRONG", expected_state="st")
    assert "error" in result and "code" not in result
    assert b"didn't complete" in page
    assert b"Signed in" not in page


def test_missing_code_serves_the_failure_page():
    result, page = _run_callback("/callback?state=st", expected_state="st")
    assert "error" in result
    assert b"didn't complete" in page
