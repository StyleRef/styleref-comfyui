"""
Backend routes for the Load node's search dialog.

The browser cannot call styleref.io directly from the ComfyUI frontend (CORS
aside, an auth token must never reach page JS), so the widget asks the ComfyUI
server, which asks StyleRef using the same client the nodes use.

Everything here is optional. If ComfyUI's server module isn't importable — a
headless test run, an API-only embed, a future frontend rewrite — importing this
fails, `__init__` catches it, and the nodes carry on with the plain text field.
The JS side probes /styleref/search once at startup and hides its button when
these routes are absent, so both halves degrade together.
"""

from __future__ import annotations

import asyncio
import os

from aiohttp import web
from server import PromptServer

import styleref_api as api
from styleref_api import StyleRefError
from styleref_auth import auth_status, clear_credentials, credentials_path, run_login_flow
from styleref_nodes.load import bump_refresh


def _card(entry: dict) -> dict:
    """The picker-row shape. heroImage feeds the row thumbnail."""
    return {
        "slug": entry.get("slug"),
        "name": entry.get("name"),
        "author": entry.get("author"),
        "category": entry.get("category"),
        "tags": (entry.get("tags") or [])[:4],
        "heroImage": entry.get("heroImage"),
        "url": entry.get("url"),
    }


def _int_param(request: web.Request, name: str, default: int) -> int:
    """A query int that tolerates junk — a bad value is a default, not a 400."""
    try:
        return int(request.query.get(name, str(default)))
    except ValueError:
        return default


@PromptServer.instance.routes.get("/styleref/search")
async def styleref_search(request: web.Request) -> web.Response:
    """GET /styleref/search?query=…&category=…&sort=…&limit=…&offset=… → {styles, hasMore}"""
    query = request.query.get("query", "").strip()
    category = request.query.get("category", "").strip() or None
    sort = request.query.get("sort", "").strip() or None
    if sort not in (None, "recent", "popular"):
        sort = None
    limit = _int_param(request, "limit", 8)
    offset = _int_param(request, "offset", 0)

    try:
        payload = api.search_styles(
            query, category=category, sort=sort, limit=limit, offset=offset
        )
    except StyleRefError as err:
        # 200 with an error field: the dialog renders the message inline rather
        # than the frontend surfacing a generic red toast with no detail.
        return web.json_response({"styles": [], "error": err.message})

    return web.json_response(
        {
            "styles": [_card(s) for s in (payload.get("styles") or [])],
            "availableCategories": payload.get("availableCategories") or [],
            # Drives the dialog's Next button without it having to guess from a
            # short page — a full last page is indistinguishable otherwise.
            "hasMore": bool(payload.get("hasMore")),
        }
    )


@PromptServer.instance.routes.get("/styleref/saved")
async def styleref_saved(request: web.Request) -> web.Response:
    """
    The picker's "Saved" sort: gallery styles this user saved. Signed-in only,
    and reported as such rather than as an error — signing out is not a failure.
    """
    signed_in, message = auth_status()
    if not signed_in:
        return web.json_response({"styles": [], "signedIn": False, "message": message})

    query = request.query.get("query", "").strip()
    category = request.query.get("category", "").strip() or None

    try:
        payload = api.list_saved_styles(
            query,
            category=category,
            limit=_int_param(request, "limit", 8),
            offset=_int_param(request, "offset", 0),
        )
    except StyleRefError as err:
        return web.json_response({"styles": [], "signedIn": True, "error": err.message})

    return web.json_response(
        {
            "signedIn": True,
            "styles": [_card(s) for s in (payload.get("styles") or [])],
            "hasMore": bool(payload.get("hasMore")),
        }
    )


@PromptServer.instance.routes.get("/styleref/my-styles")
async def styleref_my_styles(request: web.Request) -> web.Response:
    """The caller's own library, shown above gallery results when signed in."""
    signed_in, message = auth_status()
    if not signed_in:
        return web.json_response({"styles": [], "signedIn": False, "message": message})

    cursor = request.query.get("cursor", "").strip() or None
    query = request.query.get("query", "").strip()
    try:
        payload = api.list_my_styles(
            limit=_int_param(request, "limit", 8), cursor=cursor, query=query
        )
    except StyleRefError as err:
        return web.json_response({"styles": [], "signedIn": True, "error": err.message})

    return web.json_response(
        {
            "signedIn": True,
            "styles": [
                {
                    "slug": s.get("ref"),
                    "name": s.get("name"),
                    "author": None,
                    "tags": [],
                    "visibility": s.get("visibility"),
                    # null → never generated on styleref.io, so loading it would
                    # 409. The dialog badges these instead of offering them as
                    # ready to use.
                    "lastGeneratedAt": s.get("lastGeneratedAt"),
                }
                for s in (payload.get("styles") or [])
            ],
            # Passed back as ?cursor= for the dialog's Next button.
            "nextCursor": payload.get("nextCursor"),
        }
    )


@PromptServer.instance.routes.post("/styleref/refresh")
async def styleref_refresh(request: web.Request) -> web.Response:
    """
    The ↻ Refresh style button's backend: forget the cached style and advance
    the generation counter IS_CHANGED reads, so the next queue re-downloads.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed body is a client bug, not a crash
        payload = {}
    ref = str(payload.get("ref") or "").strip()
    if not ref:
        return web.json_response({"ok": False, "error": "No style_ref to refresh."})
    generation = bump_refresh(ref)
    return web.json_response({"ok": True, "generation": generation})


# ── auth: the Login node's button backend ────────────────────────────────────
# The Login node's `action` dropdown stays as the queue-driven fallback (it is
# the only path for API-only installs); these routes power the node's buttons.


def _status_payload() -> dict:
    signed_in, message = auth_status()
    return {"signedIn": signed_in, "message": message}


@PromptServer.instance.routes.get("/styleref/auth/status")
async def styleref_auth_status(_request: web.Request) -> web.Response:
    return web.json_response(_status_payload())


@PromptServer.instance.routes.post("/styleref/auth/signin")
async def styleref_auth_signin(_request: web.Request) -> web.Response:
    """
    Run the OAuth loopback flow. Blocking by nature (it waits for the browser
    redirect), so it runs in an executor; the JS button shows a waiting state.
    """
    loop = asyncio.get_event_loop()
    ok, message = await loop.run_in_executor(None, run_login_flow, api.api_base())
    return web.json_response({"ok": ok, "message": message, **_status_payload()})


@PromptServer.instance.routes.post("/styleref/auth/signout")
async def styleref_auth_signout(_request: web.Request) -> web.Response:
    removed = clear_credentials()
    message = (
        f"Signed out — removed {credentials_path()}."
        if removed
        else "No stored credentials to remove."
    )
    if os.environ.get("STYLEREF_TOKEN"):
        message += (
            " Note: STYLEREF_TOKEN is still set in this environment and takes "
            "precedence, so you remain signed in via that token."
        )
    return web.json_response({"ok": True, "message": message, **_status_payload()})
