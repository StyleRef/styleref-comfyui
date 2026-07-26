"""API client tests — URL building, error mapping, retry policy."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import styleref_api as api
from styleref_api import StyleRefError

BASE = "https://styleref.io/api/v1"


# ── URL building ─────────────────────────────────────────────────────────────


def test_encode_ref_handles_url_refs():
    """A URL ref must survive as ONE path segment or the router sees extras."""
    assert (
        api.encode_ref("https://styleref.io/share/9a2adtz6-cd8d77ee2f51")
        == "https%3A%2F%2Fstyleref.io%2Fshare%2F9a2adtz6-cd8d77ee2f51"
    )


def test_encode_ref_handles_a_builder_url():
    """The /styles/{id} URL is what a signed-in user pastes from the address bar."""
    assert (
        api.encode_ref("https://styleref.io/styles/e7c1f2a9-2f65-4f0e-9a3d-2b1c4d5e6f70")
        == "https%3A%2F%2Fstyleref.io%2Fstyles%2Fe7c1f2a9-2f65-4f0e-9a3d-2b1c4d5e6f70"
    )


def test_build_style_url_omits_default_format():
    assert api.build_style_url(BASE, "abc") == f"{BASE}/styles/abc"
    assert "format=" not in api.build_style_url(BASE, "abc", fmt="default")


def test_build_style_url_adds_params():
    url = api.build_style_url(BASE, "abc", fmt="flux", compact=True)
    assert "format=flux" in url and "compact=1" in url


def test_build_search_url_clamps_limit_to_server_cap():
    """Server caps limit at 25 — clamping client-side turns a 400 into a smaller page."""
    assert "limit=25" in api.build_search_url(BASE, "x", limit=999)
    assert "limit=1" in api.build_search_url(BASE, "x", limit=0)


def test_build_search_url_omits_blank_query():
    assert api.build_search_url(BASE, "   ") == f"{BASE}/styles"


def test_build_search_url_clamps_offset_to_the_ranking_pool():
    """Server caps offset at 200 — the width of its in-memory ranking pool."""
    assert "offset=200" in api.build_search_url(BASE, "x", offset=9999)
    assert "offset=1" in api.build_search_url(BASE, "x", offset=1)


def test_build_search_url_omits_a_zero_offset():
    """Page 1 is the default; sending offset=0 would only pad the URL."""
    assert "offset" not in api.build_search_url(BASE, "x", offset=0)
    assert "offset" not in api.build_search_url(BASE, "x")


def test_list_my_styles_sends_the_query_filter(monkeypatch):
    """The picker's one search box has to narrow the user's own library too."""
    seen: dict[str, str] = {}

    def fake_request_json(url: str, **_kwargs):
        seen["url"] = url
        return {"styles": [], "nextCursor": None}

    monkeypatch.setattr(api, "request_json", fake_request_json)
    api.list_my_styles(limit=8, query="  editorial  ")

    assert "query=editorial" in seen["url"]  # trimmed
    assert "limit=8" in seen["url"]


def test_list_my_styles_omits_a_blank_query(monkeypatch):
    seen: dict[str, str] = {}

    def fake_request_json(url: str, **_kwargs):
        seen["url"] = url
        return {}

    monkeypatch.setattr(api, "request_json", fake_request_json)
    api.list_my_styles(query="   ")

    assert "query" not in seen["url"]


def test_list_saved_styles_hits_the_saved_endpoint(monkeypatch):
    """The picker's "Saved" sort reads a different endpoint, not a sort value."""
    seen: dict[str, str] = {}

    def fake_request_json(url: str, **_kwargs):
        seen["url"] = url
        return {"styles": [], "hasMore": False}

    monkeypatch.setattr(api, "request_json", fake_request_json)
    api.list_saved_styles("warm", category="Photography", limit=8, offset=8)

    assert "/me/saved-styles?" in seen["url"]
    assert "query=warm" in seen["url"]
    assert "category=Photography" in seen["url"]
    assert "limit=8" in seen["url"]
    assert "offset=8" in seen["url"]


def test_list_saved_styles_is_not_anonymous(monkeypatch):
    """Saved styles are per-user, so the bearer token must be attached."""
    seen: dict[str, object] = {}

    def fake_request_json(url: str, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(api, "request_json", fake_request_json)
    api.list_saved_styles()

    assert seen.get("anonymous") is not True


# ── error mapping ────────────────────────────────────────────────────────────


def _http_error(status: int, body: dict | str) -> urllib.error.HTTPError:
    raw = json.dumps(body) if isinstance(body, dict) else body
    return urllib.error.HTTPError(
        url=BASE, code=status, msg="err", hdrs={}, fp=io.BytesIO(raw.encode())
    )


def test_402_message_is_preserved_verbatim():
    """
    The upgrade copy is written server-side for humans. Rewording it in the
    client would fork that copy — the node must render exactly what was sent.
    """
    message = "You're out of extraction credits. Upgrade at https://styleref.io/pricing"
    with pytest.raises(StyleRefError) as caught:
        api._raise_for_error(_http_error(402, {"error": "insufficient_credits", "message": message}))

    assert caught.value.message == message
    assert caught.value.needs_credits is True
    assert caught.value.needs_login is False
    assert caught.value.code == "insufficient_credits"


def test_401_flags_needs_login(monkeypatch):
    monkeypatch.delenv("STYLEREF_TOKEN", raising=False)
    with pytest.raises(StyleRefError) as caught:
        api._raise_for_error(_http_error(401, {"message": "Sign in to load private styles."}))
    assert caught.value.needs_login is True
    assert "STYLEREF_TOKEN" not in caught.value.message


def test_401_with_env_token_hints_at_expiry(monkeypatch):
    """
    The env token never refreshes itself, so a headless install's
    401 hours later needs to say the likely cause — an expired STYLEREF_TOKEN.
    """
    monkeypatch.setenv("STYLEREF_TOKEN", "stale-token")
    with pytest.raises(StyleRefError) as caught:
        api._raise_for_error(_http_error(401, {"message": "Unauthorized."}))
    assert "STYLEREF_TOKEN may have expired" in caught.value.message
    assert "styleref.io/account" in caught.value.message


def test_429_produces_a_wait_instruction():
    err = _http_error(429, {"message": "slow down"})
    err.headers = {"Retry-After": "30"}
    with pytest.raises(StyleRefError) as caught:
        api._raise_for_error(err)
    assert "30s" in caught.value.message


def test_non_json_error_body_falls_back_to_status_line():
    with pytest.raises(StyleRefError) as caught:
        api._raise_for_error(_http_error(500, "<html>gateway</html>"))
    assert caught.value.status == 500


# ── response headers ─────────────────────────────────────────────────────────


def test_response_header_is_case_insensitive():
    """HTTP/1.1 casing isn't guaranteed and a proxy may normalize header names."""
    headers = {"x-styleref-canonical-url": "https://styleref.io/share/abc"}
    assert (
        api.response_header(headers, "X-StyleRef-Canonical-Url")
        == "https://styleref.io/share/abc"
    )
    assert api.response_header({}, "X-StyleRef-Canonical-Url") is None


def test_get_style_spec_reads_the_canonical_url_header(monkeypatch):
    """Attribution uses the server's canonical URL, correct for any ref shape."""

    def fake_raw(url, **_kwargs):
        assert "format=json" in url
        return (
            b'{"name":"Warm","sections":{}}',
            {"X-StyleRef-Canonical-Url": "https://styleref.io/share/real-slug"},
        )

    monkeypatch.setattr(api, "request_raw", fake_raw)
    spec, url, _etag = api.get_style_spec("@ada/Warm Editorial")
    assert spec["name"] == "Warm"
    assert url == "https://styleref.io/share/real-slug"


# ── extraction stays out of this client ──────────────────────────────────────


def test_client_has_no_extraction_endpoints():
    """
    Extraction is a web-app experience; the plugin must not grow it back
    quietly. The REST /extractions tier itself remains for CLI/agents.
    """
    assert not hasattr(api, "create_extraction")
    assert not hasattr(api, "get_extraction")


# ── retry policy ─────────────────────────────────────────────────────────────


def test_post_is_never_retried(monkeypatch):
    """
    A retried POST can repeat a server-side action (or a charge) the caller
    asked for once. This is the test that guards that.
    """
    calls = {"n": 0}

    def always_5xx(*_args, **_kwargs):
        calls["n"] += 1
        raise _http_error(503, {"message": "upstream down"})

    monkeypatch.setattr(api.urllib.request, "urlopen", always_5xx)
    monkeypatch.setattr(api, "_auth_header", lambda anonymous: {})

    with pytest.raises(StyleRefError):
        api.request_raw(f"{BASE}/styles", method="POST", body=b"x")

    assert calls["n"] == 1


def test_get_retries_on_5xx(monkeypatch):
    calls = {"n": 0}

    class _Response:
        headers = {}

        def read(self):
            return b'{"ok":true}'

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def flaky(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise _http_error(500, {"message": "blip"})
        return _Response()

    monkeypatch.setattr(api.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(api, "_auth_header", lambda anonymous: {})
    monkeypatch.setattr(api.time, "sleep", lambda _s: None)

    body, _ = api.request_raw(f"{BASE}/styles", method="GET")
    assert json.loads(body) == {"ok": True}
    assert calls["n"] == 2


def test_client_tag_header_is_sent(monkeypatch):
    """Telemetry attributes API usage to the plugin via X-StyleRef-Client."""
    captured = {}

    class _Response:
        headers = {}

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def capture(req, **_kwargs):
        captured.update(req.headers)
        return _Response()

    monkeypatch.setattr(api.urllib.request, "urlopen", capture)
    monkeypatch.setattr(api, "_auth_header", lambda anonymous: {})

    api.request_raw(f"{BASE}/styles")
    # urllib title-cases header names.
    assert captured.get("X-styleref-client") == "comfyui"


# ── save ─────────────────────────────────────────────────────────────────────


def test_save_style_posts_to_the_save_endpoint(monkeypatch):
    seen = {}

    def fake_json(url, method="GET", **kwargs):
        seen["url"], seen["method"] = url, method
        return {"saved": True}

    monkeypatch.setattr(api, "request_json", fake_json)
    api.save_style("@ada/Warm Editorial")
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/styles/%40ada%2FWarm%20Editorial/save")


# ── conditional GET ──────────────────────────────────────────────────────────


def test_304_is_success_not_error(monkeypatch):
    """urllib models 304 as an HTTPError; for a conditional GET it is success."""

    def not_modified(*_args, **_kwargs):
        raise _http_error(304, "")

    monkeypatch.setattr(api.urllib.request, "urlopen", not_modified)
    monkeypatch.setattr(api, "_auth_header", lambda anonymous: {})

    body, _headers = api.request_raw(f"{BASE}/styles/abc", headers={"If-None-Match": 'W/"x"'})
    assert body == b""


def test_get_style_spec_returns_none_on_304(monkeypatch):
    """A revalidated cache hit must keep the cached spec, not blank it."""

    def fake_raw(url, headers=None, **_kwargs):
        assert headers == {"If-None-Match": 'W/"e1"'}
        return b"", {"ETag": 'W/"e1"'}

    monkeypatch.setattr(api, "request_raw", fake_raw)
    spec, _url, etag = api.get_style_spec("abc", etag='W/"e1"')
    assert spec is None
    assert etag == 'W/"e1"'


def test_get_style_spec_returns_etag_on_200(monkeypatch):
    def fake_raw(url, headers=None, **_kwargs):
        return b'{"name":"Warm","sections":{}}', {"ETag": 'W/"e2"'}

    monkeypatch.setattr(api, "request_raw", fake_raw)
    spec, _url, etag = api.get_style_spec("abc")
    assert spec["name"] == "Warm"
    assert etag == 'W/"e2"'
