"""
Thin client for the StyleRef public REST API v1 (https://styleref.io/api/v1).

Stdlib-only on purpose: ComfyUI installs are wildly varied and a custom node
that drags in dependencies is a node that fails to import on someone's portable
build. `urllib` is enough for JSON and form posts.

The server owns all prompt logic — this client never compiles or rewrites a
style, it only fetches what the server produced. OpenAPI: /api/v1/openapi.json
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_API_BASE = "https://styleref.io/api/v1"
SITE_URL = "https://styleref.io"

# Identifies this surface in StyleRef's API telemetry (recordApiCall reads it).
CLIENT_TAG = "comfyui"
USER_AGENT = "styleref-comfyui/1.0.5 (+https://styleref.io)"

DEFAULT_TIMEOUT_S = 30


def api_base() -> str:
    """Env override exists so the nodes can be pointed at a local dev server."""
    return (os.environ.get("STYLEREF_API") or "").strip() or DEFAULT_API_BASE


def site_origin_from_api_base(base: str) -> str:
    parsed = urllib.parse.urlparse(base)
    return f"{parsed.scheme}://{parsed.netloc}"


class StyleRefError(Exception):
    """
    An API call that failed in a way the user should see.

    `message` is safe to render verbatim in a node — the server writes its 402
    and 401 bodies for humans (upgrade copy, sign-in copy), and rewording them
    in the client would mean maintaining that copy in two places.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        code: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.payload = payload or {}

    @property
    def needs_login(self) -> bool:
        return self.status == 401

    @property
    def needs_credits(self) -> bool:
        return self.status == 402


def encode_ref(ref: str) -> str:
    """URL-encode a style ref into one path segment (handles /share URLs)."""
    return urllib.parse.quote(ref.strip(), safe="")


def build_style_url(
    base: str,
    ref: str,
    fmt: str | None = None,
    sections: str | None = None,
    compact: bool = False,
    raw: bool = False,
) -> str:
    url = f"{base.rstrip('/')}/styles/{encode_ref(ref)}"
    params: dict[str, str] = {}
    if fmt and fmt != "default":
        params["format"] = fmt
    if sections:
        params["sections"] = sections
    if compact:
        params["compact"] = "1"
    if raw:
        params["raw"] = "1"
    return f"{url}?{urllib.parse.urlencode(params)}" if params else url


def build_search_url(
    base: str,
    query: str = "",
    category: str | None = None,
    sort: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> str:
    params: dict[str, str] = {}
    if query.strip():
        params["query"] = query.strip()
    if category:
        params["category"] = category
    if sort:
        params["sort"] = sort
    if limit is not None:
        # Server caps at 25; clamp here so a bad widget value is a smaller page
        # rather than a 400. `is not None` rather than a truthiness check so
        # limit=0 clamps to 1 instead of silently falling back to the default.
        params["limit"] = str(max(1, min(int(limit), 25)))
    if offset:
        # Server caps at 200 (the width of its in-memory ranking pool); clamp so
        # paging past the end is an empty page, not a 400. Falsy skips it: 0 is
        # the default and belongs in the URL no more than None does.
        params["offset"] = str(max(0, min(int(offset), 200)))
    base_url = f"{base.rstrip('/')}/styles"
    return f"{base_url}?{urllib.parse.urlencode(params)}" if params else base_url


# ── transport ────────────────────────────────────────────────────────────────


def response_header(headers: dict[str, str], name: str) -> str | None:
    """
    Case-insensitive lookup into a response's headers.

    `request_raw` hands back `dict(response.headers)`, whose keys keep whatever
    casing arrived on the wire — and an origin or proxy is free to normalize
    `X-StyleRef-Canonical-Url` to any case. An exact-case `.get()` would then
    silently miss a header that is present, so read them case-insensitively.
    """
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _auth_header(anonymous: bool) -> dict[str, str]:
    if anonymous:
        return {}
    # Imported lazily: styleref_auth imports this module, and the login flow
    # must be able to run before any credentials exist.
    from styleref_auth import bearer_token

    token = bearer_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _raise_for_error(err: urllib.error.HTTPError) -> None:
    """Turn an HTTPError into a StyleRefError carrying the server's own copy."""
    raw = ""
    with contextlib.suppress(Exception):
        # Body is best-effort context; the status line alone is still useful.
        raw = err.read().decode("utf-8", "replace")

    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        pass

    message = payload.get("message") or raw.strip() or f"{err.code} {err.reason}"

    if err.code == 401 and (os.environ.get("STYLEREF_TOKEN") or "").strip():
        # The env token never refreshes itself (unlike stored credentials), so
        # on a headless box an expired token is the overwhelmingly likely cause.
        message += (
            "\n\nYour STYLEREF_TOKEN may have expired — copy a fresh token from "
            f"{SITE_URL}/account."
        )

    if err.code == 429:
        retry = err.headers.get("Retry-After") if err.headers else None
        message = (
            f"Rate limited by StyleRef — wait {retry}s and retry."
            if retry
            else "Rate limited by StyleRef — wait a moment and retry."
        )

    raise StyleRefError(
        message, status=err.code, code=payload.get("error"), payload=payload
    ) from err


def request_raw(
    url: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    anonymous: bool = False,
    timeout: int = DEFAULT_TIMEOUT_S,
    retries: int = 2,
) -> tuple[bytes, dict[str, str]]:
    """
    Perform a request, returning (body, response headers).

    Retries only idempotent GETs on transport errors and 5xx — a retried POST
    could repeat a server-side action (or a charge) the caller only asked
    for once.
    """
    final_headers = {"User-Agent": USER_AGENT, "X-StyleRef-Client": CLIENT_TAG}
    final_headers.update(_auth_header(anonymous))
    final_headers.update(headers or {})

    attempts = retries + 1 if method == "GET" else 1
    last_err: Exception | None = None

    for attempt in range(attempts):
        req = urllib.request.Request(url, data=body, headers=final_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read(), dict(res.headers)
        except urllib.error.HTTPError as err:
            # 304 Not Modified is a success for a conditional GET: the caller's
            # cached copy is still current. urllib models it as an error.
            if err.code == 304:
                return b"", dict(err.headers or {})
            # 4xx is the caller's problem and will not change on retry.
            if err.code < 500 or attempt == attempts - 1:
                _raise_for_error(err)
            last_err = err
        except urllib.error.URLError as err:
            if attempt == attempts - 1:
                host = urllib.parse.urlparse(url).netloc
                raise StyleRefError(
                    f"Could not reach {host} ({err.reason}). Check the machine running "
                    "ComfyUI has internet access."
                ) from err
            last_err = err
        time.sleep(0.5 * (2**attempt))

    raise StyleRefError(f"Request to {url} failed: {last_err}")


def request_json(
    url: str,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    form_body: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    anonymous: bool = False,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    body: bytes | None = None
    hdrs = dict(headers or {})

    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif form_body is not None:
        body = urllib.parse.urlencode(form_body).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"

    raw, _ = request_raw(
        url, method=method, body=body, headers=hdrs, anonymous=anonymous, timeout=timeout
    )
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as err:
        raise StyleRefError("StyleRef returned a malformed response.") from err
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def request_text(url: str, anonymous: bool = False, timeout: int = DEFAULT_TIMEOUT_S) -> str:
    raw, _ = request_raw(url, anonymous=anonymous, timeout=timeout)
    return raw.decode("utf-8", "replace")


# ── endpoints ────────────────────────────────────────────────────────────────


def search_styles(
    query: str = "",
    category: str | None = None,
    sort: str | None = None,
    limit: int = 12,
    offset: int = 0,
) -> dict[str, Any]:
    """
    GET /styles — anonymous gallery search.

    The response carries `hasMore` / `nextOffset` alongside `styles`; the picker
    pages by feeding `nextOffset` back as `offset`.
    """
    return request_json(
        build_search_url(api_base(), query, category, sort, limit, offset), anonymous=True
    )


def get_style_text(
    ref: str,
    fmt: str = "default",
    compact: bool = False,
    raw: bool = False,
    sections: str | None = None,
) -> str:
    """
    GET /styles/{ref} — the compiled prose spec for one target.

    `raw=True` returns exactly what the generator produced, with no metadata
    header and no appended attribution line. That is what a prompt encoder
    wants: the header is markdown for humans and would be encoded as literal
    tokens. Attribution is surfaced on the node's own output instead.

    Not anonymous: a private style only resolves with a bearer token, and the
    same call serves both public and private refs.
    """
    return request_text(
        build_style_url(api_base(), ref, fmt=fmt, sections=sections, compact=compact, raw=raw)
    )


def get_style_json(ref: str) -> dict[str, Any]:
    """GET /styles/{ref}?format=json — the structured spec the Facets node reads."""
    return get_style_spec(ref)[0]


def get_style_spec(
    ref: str, etag: str | None = None
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """
    GET /styles/{ref}?format=json → (structured spec, canonical URL, ETag).

    Pass a previously returned `etag` to revalidate a cached copy: when the
    style hasn't changed server-side, the server answers 304 with no body and
    this returns `(None, canonical, etag)` — the caller keeps its cache.

    The canonical URL comes from the server's `X-StyleRef-Canonical-Url`
    response header, which is authoritative for every ref shape — a bare slug,
    a share URL, an own-style id, a /styles/{id} URL. Building it client-side
    as `{SITE_URL}/share/{ref}` is only correct for a bare slug and produces a
    broken link for the others, so the attribution output reads it from here.
    """
    conditional = {"If-None-Match": etag} if etag else {}
    raw, headers = request_raw(build_style_url(api_base(), ref, fmt="json"), headers=conditional)
    canonical = response_header(headers, "X-StyleRef-Canonical-Url")
    new_etag = response_header(headers, "ETag")

    if not raw and etag:
        # 304 Not Modified — the cached spec is still current.
        return None, canonical, etag

    payload: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError as err:
            raise StyleRefError("StyleRef returned a malformed response.") from err
        payload = parsed if isinstance(parsed, dict) else {"data": parsed}
    return payload, canonical, new_etag


def save_style(ref: str) -> dict[str, Any]:
    """
    POST /styles/{ref}/save — save (like) a gallery style to the caller's
    library. Idempotent server-side; requires the styles:write scope.
    """
    url = f"{api_base().rstrip('/')}/styles/{encode_ref(ref)}/save"
    return request_json(url, method="POST", json_body={})


def list_my_styles(
    limit: int = 25, cursor: str | None = None, query: str = ""
) -> dict[str, Any]:
    """
    GET /me/styles — the caller's own library. Requires styles:read.

    `query` narrows by style name, so the picker's one search box filters the
    user's own styles as well as the gallery. Paging is keyset, not offset: pass
    the previous response's `nextCursor` back as `cursor`.

    Each item carries `lastGeneratedAt` — null means the style has never been
    generated on styleref.io, and loading it will fail with 409
    `styleref_not_generated`. The picker flags those rows rather than letting the
    user find out at queue time.
    """
    params: dict[str, str] = {"limit": str(max(1, min(limit, 50)))}
    if cursor:
        params["cursor"] = cursor
    if query.strip():
        params["query"] = query.strip()
    url = f"{api_base().rstrip('/')}/me/styles?{urllib.parse.urlencode(params)}"
    return request_json(url)


def list_saved_styles(
    query: str = "", category: str | None = None, limit: int = 8, offset: int = 0
) -> dict[str, Any]:
    """
    GET /me/saved-styles — gallery styles the caller saved. Requires styles:read.

    The picker's "Saved" sort. Same card shape as `search_styles`, so the dialog
    renders the rows with the same code; offset-paged like the gallery lane.
    """
    params: dict[str, str] = {"limit": str(max(1, min(limit, 25)))}
    if query.strip():
        params["query"] = query.strip()
    if category:
        params["category"] = category
    if offset:
        params["offset"] = str(max(0, min(offset, 500)))
    url = f"{api_base().rstrip('/')}/me/saved-styles?{urllib.parse.urlencode(params)}"
    return request_json(url)


# Note: this client deliberately has no extraction endpoints. Extraction is a
# web-app experience (preview, per-block editing, history, free re-apply), so
# the pack loads styles rather than making them. The REST /extractions tier
# itself remains available for CLI and agent surfaces.
