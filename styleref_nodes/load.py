"""
StyleRef Load — fetch a style into the graph.

Widget policy (plan risk 9.1): every widget here is vanilla — a text field, a
combo, a toggle. The live search dialog is a *progressive enhancement* shipped
in web/styleref.js. When that JS fails to load, or a ComfyUI frontend release
breaks it, this node still works exactly as written: type or paste a ref. The
search path is additionally available headlessly via the `search` widget, whose
results land on the `search_results` output and in the node body.

Guidance policy (plan P1-2): Nodes 2.0 does not render STRING placeholders, so
every input's contract must survive on tooltip + default alone. Placeholders are
kept (harmless where supported) but never as the only channel.

Refresh policy: the ↻ Refresh style button (web/styleref.js) POSTs to
/styleref/refresh, which calls `bump_refresh()` — dropping the cached style and
advancing a generation counter that IS_CHANGED folds into its key, so the next
queue re-executes this node and re-downloads. No widget involved. The vanilla
fallback is the `use_cache` toggle.
"""

from __future__ import annotations

from typing import Any

import styleref_api as api
from styleref_api import StyleRefError
from styleref_style import make_style, palette_hexes, summary_line

CATEGORY = "StyleRef"

# The gallery's controlled category vocabulary (src/lib/data/gallery-categories.ts
# server-side). Static on purpose: the server tolerates unknown values, so a
# stale list degrades to an unfiltered search, never an error.
SEARCH_CATEGORIES = [
    "any",
    "Photography",
    "Graphic design",
    "Illustration",
    "Cinematography",
    "UI/UX",
    "Brand identity",
    "Motion design",
    "Concept art",
    "Game art",
    "Product design",
    "Architecture",
    "Interior design",
    "Fine art",
    "Character design",
    "Environmental art",
    "High-end fashion editorial",
    "Book/editorial design",
    "Packaging design",
    "3D visualization",
    "Data visualization",
    "Copywriting",
    "Content writing",
    "Social copy",
    "UX writing",
]

# Cache fetched styles for the session (entry: {"style": ..., "etag": ...}).
# ComfyUI re-runs a node whenever anything upstream changes, and re-fetching an
# unchanged style on every queue would burn the anonymous rate limit for no
# benefit. When the node does re-execute, a stored ETag turns the re-fetch into
# a cheap conditional GET (P5-8): the server answers 304 and the cached style —
# including its compiled-prompt cache — is kept.
_CACHE: dict[str, dict[str, Any]] = {}

# Styles saved to the library this session — saving is idempotent server-side,
# but repeating the call on every queue would be pointless traffic.
_SAVED: set[str] = set()

# Bumped by the ↻ Refresh style button (via /styleref/refresh). IS_CHANGED
# includes the counter, so a bump makes ComfyUI re-execute the node on the next
# queue instead of serving its own cached outputs.
_GENERATION: dict[str, int] = {}


def _cache_key(ref: str) -> str:
    return f"{api.api_base()}::{ref.strip()}"


def refresh_generation(ref: str) -> int:
    return _GENERATION.get(_cache_key(ref), 0)


def bump_refresh(ref: str) -> int:
    """Forget the cached style and mark the node dirty for the next queue."""
    key = _cache_key(ref)
    _CACHE.pop(key, None)
    _GENERATION[key] = _GENERATION.get(key, 0) + 1
    return _GENERATION[key]


def fetch_style(ref: str, use_cache: bool = True) -> dict[str, Any]:
    """Fetch one style as a STYLEREF_STYLE payload."""
    ref = ref.strip()
    if not ref:
        raise StyleRefError(
            "No style reference. Paste a share slug or a styleref.io share URL, "
            "or — signed in — your own style's id or /styles/<id> URL."
        )

    key = _cache_key(ref)
    cached = _CACHE.get(key)
    if use_cache and cached is not None:
        return cached["style"]

    # With use_cache off (or after a refresh) we still send the stored ETag:
    # "re-download every run" becomes "revalidate every run" — a bodyless 304
    # when nothing changed, the full spec when it did (P5-8).
    etag = cached.get("etag") if cached else None
    spec, canonical_url, new_etag = api.get_style_spec(ref, etag=etag)
    if spec is None and cached is not None:
        # 304 — the edit-free style keeps its compiled-prompt cache too.
        return cached["style"]

    spec = spec or {}
    # The server's canonical URL is what tells private from public: a private
    # style canonicalises to the builder (/styles/{id}), a shared one to its
    # public /share/{slug} page. The ref itself doesn't say — own styles are
    # addressed by the same slug or id as any other. A missing header reads as
    # public, which only costs an attribution line, never a wrong save.
    is_private = "/styles/" in (canonical_url or "")
    style = make_style(
        ref=ref,
        name=spec.get("name") or ref,
        sections=spec.get("sections") or {},
        source="library" if is_private else "gallery",
        # A private style has no public URL to attribute to; a public one is
        # attributed to the server's canonical share URL — correct for every ref
        # shape, where a client-built `/share/{ref}` is only right for a slug.
        url=None if is_private else canonical_url,
    )
    _CACHE[key] = {"style": style, "etag": new_etag}
    return style


def format_search_results(payload: dict[str, Any]) -> str:
    """Plain-text results — the unbreakable fallback for the search widget."""
    styles = payload.get("styles") or []
    if not styles:
        cats = payload.get("availableCategories") or []
        hint = f"\nCategories: {', '.join(cats[:12])}." if cats else ""
        return f"No styles matched.{hint}\nBrowse {api.SITE_URL}/gallery"

    lines = ["Copy a slug into the `style_ref` field:", ""]
    for entry in styles:
        author = f"  by {entry['author']}" if entry.get("author") else ""
        tags = entry.get("tags") or []
        tag_line = f" — {', '.join(tags[:4])}" if tags else ""
        lines.append(f"{entry.get('slug')}{author}")
        lines.append(f"    {entry.get('name')}{tag_line}")
    return "\n".join(lines)


class StyleRefLoad:
    """Fetch a StyleRef style by reference, or search the public gallery."""

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "style_ref": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "share slug, share URL, or your own style's id",
                        "tooltip": "Which style to load. Accepts a share slug (from a "
                        "styleref.io/share URL) or the full share URL. Once you sign in "
                        "with the StyleRef Login node, your own private styles resolve by "
                        "their id or their /styles/<id> URL — use the Search styles… "
                        "button to pick one without typing an id.",
                    },
                ),
            },
            "optional": {
                "search": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "e.g. warm editorial photography",
                        "tooltip": "Search the gallery without leaving ComfyUI: type a "
                        "query and queue this node. Matches appear in the node body and "
                        "on the search_results output — copy a slug into style_ref. "
                        "The Search styles… button is the faster path when available.",
                    },
                ),
                "category": (
                    SEARCH_CATEGORIES,
                    {
                        "default": "any",
                        "tooltip": "Narrow the `search` query to one gallery category. "
                        "Ignored when `search` is empty.",
                    },
                ),
                "use_cache": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Off = re-check the style on every run (a cheap "
                        "revalidation; it re-downloads only when the style actually "
                        "changed on styleref.io). Leave on for normal use; the "
                        "↻ Refresh style button re-fetches once without this toggle.",
                    },
                ),
                "save_to_library": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Also save this gallery style to your StyleRef "
                        "library (My Styles → Saved) when it loads. Needs sign-in "
                        "(StyleRef Login node). No effect on your own styles.",
                    },
                ),
            },
        }

    RETURN_TYPES = ("STYLEREF_STYLE", "STRING")
    RETURN_NAMES = ("style", "search_results")
    OUTPUT_TOOLTIPS = (
        "Connect to StyleRef Apply or StyleRef Facets.",
        "Plain-text gallery matches when `search` was used.",
    )
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = "Load a StyleRef style by share slug, id, handle, or URL."
    # An output node so a search-only or load-only Load can be queued alone and
    # still render its node-body text.
    OUTPUT_NODE = True

    def load(
        self,
        style_ref: str,
        search: str = "",
        category: str = "any",
        use_cache: bool = True,
        save_to_library: bool = False,
    ):
        results = ""
        if search.strip():
            cat = None if category in ("", "any") else category
            try:
                results = format_search_results(api.search_styles(search, category=cat, limit=12))
            except StyleRefError as err:
                # A failed search must not sink the run — the ref may be fine.
                results = f"Search failed: {err.message}"

        if not style_ref.strip():
            if results:
                # Search-only use: report results instead of failing the graph.
                return self._result(
                    make_style(ref="", name="(no style loaded)"), results, body=results
                )
            raise StyleRefError(
                "No style reference. Type a query in `search` to find one, or paste a "
                f"slug from {api.SITE_URL}/gallery."
            )

        style = fetch_style(style_ref, use_cache=use_cache)

        summary = f"Loaded {summary_line(style)}"
        # The search card carries no palette data, so the node body is where the
        # palette shows up (plan P3-2's fallback) — as text, the only channel
        # ui.text has.
        hexes = palette_hexes(style)[:5]
        if hexes:
            summary += f"\nPalette: {' '.join(hexes)}"

        if save_to_library:
            summary += f"\n{self._save_to_library(style)}"
        elif style.get("url"):
            # Passive contribution nudge (plan P6-3): the style's public page is
            # where renders made with it can be shared.
            summary += f"\nMade something good with it? Share it on {style['url']}"

        print(f"[StyleRef] Loaded {summary_line(style)}")
        body = f"{summary}\n\n{results}" if results else summary
        return self._result(style, results, body=body)

    @staticmethod
    def _save_to_library(style: dict[str, Any]) -> str:
        """One save per style per session — the endpoint is idempotent anyway."""
        ref = style.get("ref") or ""
        if style.get("source") == "library":
            return "Already your own style — nothing to save."
        key = _cache_key(ref)
        if key in _SAVED:
            return "Saved to your library ✔"
        try:
            payload = api.save_style(ref)
        except StyleRefError as err:
            # A failed save must not sink the run — the style itself loaded.
            return f"Could not save to your library: {err.message}"
        _SAVED.add(key)
        already = payload.get("alreadySaved")
        return "Already in your library ✔" if already else "Saved to your library ✔"

    @staticmethod
    def _result(style: dict[str, Any], results: str, body: str):
        # ui.text renders in the node body (P0-2): the load confirmation and any
        # search results are visible without wiring the STRING output anywhere.
        return {"ui": {"text": [body]}, "result": (style, results)}

    @classmethod
    def IS_CHANGED(
        cls,
        style_ref: str,
        search: str = "",
        category: str = "any",
        use_cache: bool = True,
        save_to_library: bool = False,
        **_kw,
    ):
        # With caching off, report a changing value so ComfyUI re-executes and
        # actually revalidates rather than serving its own cached output.
        if not use_cache:
            return float("nan")
        # The refresh generation makes the ↻ button work: bumping it changes
        # this key, which re-executes the node on the next queue.
        return (
            f"{style_ref}|{search}|{category}|{save_to_library}|"
            f"{refresh_generation(style_ref)}"
        )
