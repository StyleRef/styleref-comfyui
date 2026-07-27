"""Node behaviour tests. HTTP is mocked throughout — no network in CI."""

from __future__ import annotations

import importlib.util
import math
import os

import pytest

import styleref_api as api
from styleref_api import StyleRefError
from styleref_nodes import apply as apply_node
from styleref_nodes import load as load_node
from styleref_nodes.login import StyleRefLogin
from styleref_style import make_style

STYLE = make_style(
    ref="abc-123",
    name="Real Moments",
    author="@styleref",
    url="https://styleref.io/share/abc-123",
    sections={
        "guardrails": {"values": {"avoided_visuals": "lens flares; film grain"}},
        "colors": {"values": {"color_palette": {"solidColors": [{"hex": "#FFFFFF"}]}}},
    },
)


@pytest.fixture(autouse=True)
def clear_cache():
    load_node._CACHE.clear()
    load_node._GENERATION.clear()
    yield
    load_node._CACHE.clear()
    load_node._GENERATION.clear()


# ── pack registration ────────────────────────────────────────────────────────


def test_pack_registers_exactly_the_five_nodes():
    """
    Extraction moved to the web app: the pack registers
    Load/Apply/Facets/ReferenceImages/Login and nothing else — an Extract node
    here means the removal regressed.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("styleref_pack", os.path.join(root, "__init__.py"))
    pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pack)

    assert set(pack.NODE_CLASS_MAPPINGS) == {
        "StyleRefLoad",
        "StyleRefApply",
        "StyleRefFacets",
        "StyleRefReferenceImages",
        "StyleRefLogin",
    }
    assert set(pack.NODE_DISPLAY_NAME_MAPPINGS) == set(pack.NODE_CLASS_MAPPINGS)


# ── Apply: target mapping ────────────────────────────────────────────────────


def test_targets_are_exactly_the_web_apps_copy_list_in_order():
    """
    The target list mirrors the web app's copy-box format list exactly — same
    entries, same order — so a style means the same thing here as it does on
    styleref.io. The web app's list is the source of truth; this test is what
    catches the two drifting apart.
    """
    assert list(apply_node.TARGETS) == [
        "ai_tools",
        "style_md",
        "flux",
        "midjourney",
        "diffusion",
        "json",
    ]
    assert apply_node.TARGET_FORMATS == {
        "ai_tools": "default",
        "style_md": "stylemd",
        "flux": "flux",
        "midjourney": "midjourney",
        "diffusion": "diffusion",
        "json": "json",
    }


def test_only_sampler_formats_escape_the_copy_out_badge():
    """flux and diffusion are the only targets that belong in a CLIP encoder."""
    assert set(apply_node.COPY_OUT_TARGETS) == {"ai_tools", "style_md", "midjourney", "json"}
    assert set(apply_node.TARGETS) - set(apply_node.COPY_OUT_TARGETS) == {"flux", "diffusion"}
    assert set(apply_node.DOCUMENT_TARGETS) == {"style_md", "json"}


def test_every_advertised_target_has_a_format():
    assert set(apply_node.TARGETS) == set(apply_node.TARGET_FORMATS)


# ── Apply: composition ───────────────────────────────────────────────────────


def test_subject_is_substituted_into_the_servers_slot():
    """
    The compiled spec marks where the subject belongs for that target. Using the
    slot beats any client-side ordering guess.
    """
    style_text = f"Style: photography. {apply_node.SUBJECT_PLACEHOLDER}. Muted palette."
    out = apply_node.compose_prompt("a lighthouse", style_text)

    assert "a lighthouse" in out
    assert apply_node.SUBJECT_PLACEHOLDER not in out


def test_placeholder_never_survives_into_a_prompt():
    """
    The regression that matters: a leaked placeholder is encoded as literal text,
    so the model is told to draw the words "YOUR SUBJECT".
    """
    style_text = f"A photo. {apply_node.SUBJECT_PLACEHOLDER}. Natural light."
    for subject in ("a lighthouse", "", "   "):
        assert "YOUR SUBJECT" not in apply_node.compose_prompt(subject, style_text)


def test_stripping_the_placeholder_leaves_clean_punctuation():
    stripped = apply_node.strip_subject_placeholder(
        f"A photo. {apply_node.SUBJECT_PLACEHOLDER}. Natural light."
    )
    assert stripped == "A photo. Natural light."


def test_subject_comes_first_when_there_is_no_slot():
    """Fallback ordering: early tokens carry the most weight."""
    out = apply_node.compose_prompt("a lighthouse", "muted palette")
    assert out.startswith("a lighthouse")


def test_subject_first_can_be_flipped():
    out = apply_node.compose_prompt("a lighthouse", "muted palette", subject_first=False)
    assert out.startswith("muted palette")


def test_composition_handles_an_empty_side():
    assert apply_node.compose_prompt("", "style only") == "style only"
    assert apply_node.compose_prompt("subject only", "") == "subject only"


def test_negative_merges_guardrails_and_extra_only():
    """
    No injected boilerplate: the style's guardrails are the authority, and
    anything generic belongs in extra_negative, where the user can see it.
    """
    negative = apply_node.compose_negative(STYLE, "extra thing")
    assert "lens flares" in negative
    assert "extra thing" in negative
    assert "watermark" not in negative, "no hidden base-negative boilerplate"


def test_negative_is_the_normalized_guardrail_terms_without_extra():
    assert apply_node.compose_negative(STYLE, "") == "lens flares, film grain"


def test_negative_is_empty_for_a_style_without_guardrails():
    assert apply_node.compose_negative(make_style(ref="x", name="Bare"), "") == ""


# ── Apply: caching ───────────────────────────────────────────────────────────


def test_compile_requests_the_raw_form(monkeypatch):
    """
    The metadata header and attribution line are markdown for humans; sent to a
    text encoder they are just wasted, misleading tokens.
    """
    seen = {}

    def fake_get(ref, fmt="default", compact=False, raw=False, sections=None):
        seen["raw"] = raw
        return "spec"

    monkeypatch.setattr(api, "get_style_text", fake_get)
    apply_node.compile_style(dict(STYLE, compiled={}), "flux", False)
    assert seen["raw"] is True


def test_compile_keeps_the_header_for_style_md(monkeypatch):
    """STYLE.md is a document — its metadata header is part of it, not noise."""
    seen = {}

    def fake_get(ref, fmt="default", compact=False, raw=False, sections=None):
        seen["fmt"], seen["raw"] = fmt, raw
        return "# My Style — STYLE.md"

    monkeypatch.setattr(api, "get_style_text", fake_get)
    apply_node.compile_style(dict(STYLE, compiled={}), "style_md", False)
    assert seen["fmt"] == "stylemd"
    assert seen["raw"] is False


def test_style_md_target_ships_the_document_untouched(monkeypatch):
    """A subject must not be prepended to a STYLE.md document."""
    doc = "# My Style\n\n## Colors\n- warm"
    monkeypatch.setattr(api, "get_style_text", lambda *a, **k: doc)
    node = apply_node.StyleRefApply()
    out = node.apply(dict(STYLE, compiled={}), "a lighthouse", "style_md")
    positive, _negative, _attr = out["result"]
    assert positive == doc


def test_compiled_output_is_cached_per_target(monkeypatch):
    """Re-queueing with a new subject must not re-fetch an unchanged style."""
    calls = {"n": 0}

    def fake_get(ref, fmt="default", compact=False, raw=False, sections=None):
        calls["n"] += 1
        return f"compiled-{fmt}"

    monkeypatch.setattr(api, "get_style_text", fake_get)
    style = dict(STYLE, compiled={})

    assert apply_node.compile_style(style, "flux", False) == "compiled-flux"
    assert apply_node.compile_style(style, "flux", False) == "compiled-flux"
    assert calls["n"] == 1, "second call should hit the cache"

    # A different target is a genuinely different compilation.
    apply_node.compile_style(style, "diffusion", False)
    assert calls["n"] == 2

    # …and so is compact, which is why it is part of the key.
    apply_node.compile_style(style, "flux", True)
    assert calls["n"] == 3


def test_apply_rejects_a_non_style_input():
    node = apply_node.StyleRefApply()
    with pytest.raises(StyleRefError, match="not a StyleRef style"):
        node.apply({"nonsense": True}, "subject", "flux")


def test_apply_rejects_an_unloaded_style():
    node = apply_node.StyleRefApply()
    with pytest.raises(StyleRefError, match="No style is loaded"):
        node.apply(make_style(ref="", name="(none)"), "subject", "flux")


# ── Apply: node-body feedback ────────────────────────────────────────────────


def test_apply_returns_ui_text_and_unchanged_result_tuple(monkeypatch):
    """The ui.text preview must not change the outputs' order or arity."""
    monkeypatch.setattr(api, "get_style_text", lambda *a, **k: "muted palette, soft light")
    node = apply_node.StyleRefApply()
    out = node.apply(dict(STYLE, compiled={}), "a lighthouse", "diffusion")

    assert set(out) == {"ui", "result"}
    assert isinstance(out["ui"]["text"][0], str)
    positive, negative, attribution_line = out["result"]
    assert positive.startswith("a lighthouse")
    assert negative == "lens flares, film grain"
    assert "Real Moments" in attribution_line


def test_token_estimate_is_chars_over_four():
    assert apply_node.estimate_tokens("x" * 400) == 100


def test_preview_shows_estimate_and_compact_hint_for_diffusion():
    long_prompt = "word " * 200  # ~250 tokens
    preview = apply_node.preview_text(long_prompt, "diffusion", compact=False)
    assert "tokens (diffusion)" in preview
    assert "consider `compact`" in preview


def test_preview_hint_is_absent_for_flux_and_when_already_compact():
    long_prompt = "word " * 200
    assert "consider" not in apply_node.preview_text(long_prompt, "flux", compact=False)
    assert "consider" not in apply_node.preview_text(long_prompt, "diffusion", compact=True)


def test_preview_truncates_the_prompt_body():
    preview = apply_node.preview_text("x" * 1000, "flux", compact=False)
    first_line = preview.split("\n")[0]
    assert len(first_line) <= apply_node.PREVIEW_CHARS + 1  # + ellipsis


# ── Load ─────────────────────────────────────────────────────────────────────


def test_load_requires_a_ref_or_a_search():
    node = load_node.StyleRefLoad()
    with pytest.raises(StyleRefError, match="No style reference"):
        node.load("", "")


def test_fetch_style_caches_by_ref(monkeypatch):
    calls = {"n": 0}

    def fake_spec(ref, etag=None):
        calls["n"] += 1
        return {"name": "Cached", "sections": {}}, "https://styleref.io/share/abc", 'W/"e1"'

    monkeypatch.setattr(api, "get_style_spec", fake_spec)
    load_node.fetch_style("abc")
    load_node.fetch_style("abc")
    assert calls["n"] == 1

    load_node.fetch_style("abc", use_cache=False)
    assert calls["n"] == 2


def test_bump_refresh_drops_the_cache_and_dirties_is_changed(monkeypatch):
    """
    The ↻ Refresh style button POSTs /styleref/refresh → bump_refresh(). That
    must both forget the cached style AND change IS_CHANGED's key, or ComfyUI
    would serve its own cached outputs without ever calling the node.
    """
    calls = {"n": 0}

    def fake_spec(ref, etag=None):
        calls["n"] += 1
        return {"name": "Fresh", "sections": {}}, None, 'W/"e1"'

    monkeypatch.setattr(api, "get_style_spec", fake_spec)
    load_node.fetch_style("abc")
    load_node.fetch_style("abc")
    assert calls["n"] == 1

    key_before = load_node.StyleRefLoad.IS_CHANGED("abc")
    load_node.bump_refresh("abc")
    key_after = load_node.StyleRefLoad.IS_CHANGED("abc")
    assert key_before != key_after

    load_node.fetch_style("abc")
    assert calls["n"] == 2

    # No further bump → cached again.
    load_node.fetch_style("abc")
    assert calls["n"] == 2


def test_attribution_url_comes_from_the_servers_canonical_header(monkeypatch):
    """
    The share URL is the server's, not a client guess: `{SITE_URL}/share/{ref}`
    is only right for a bare share slug and breaks for an id or a URL ref.
    """
    monkeypatch.setattr(
        api,
        "get_style_spec",
        lambda ref, etag=None: ({"name": "Warm", "sections": {}}, "https://styleref.io/share/real-slug", None),
    )
    style = load_node.fetch_style("@ada/Warm Editorial")
    assert style["url"] == "https://styleref.io/share/real-slug"


def test_private_refs_are_tagged_as_library(monkeypatch):
    # Even when the server hands back a canonical URL, a private style is not
    # attributed to a public link.
    monkeypatch.setattr(
        api,
        "get_style_spec",
        lambda ref, etag=None: ({"name": "Mine", "sections": {}}, "https://styleref.io/styles/abc", None),
    )
    style = load_node.fetch_style("Mine")
    assert style["source"] == "library"
    assert style["url"] is None


def test_ungenerated_style_error_reaches_the_user_verbatim(monkeypatch):
    """
    When the server refuses a never-generated own-style ref (409),
    its message — which tells the user to Generate on styleref.io — must pass
    through unreworded.
    """
    message = (
        "This StyleRef hasn't been generated yet — open it on styleref.io and "
        "Generate it first."
    )

    def raise_409(ref, etag=None):
        raise StyleRefError(message, status=409, code="style_not_generated")

    monkeypatch.setattr(api, "get_style_spec", raise_409)
    node = load_node.StyleRefLoad()
    with pytest.raises(StyleRefError) as caught:
        node.load("Never Generated")
    assert caught.value.message == message


def test_load_returns_ui_text_and_result_tuple(monkeypatch):
    """The load confirmation renders in the node body."""
    monkeypatch.setattr(
        api, "get_style_spec", lambda ref, etag=None: ({"name": "Warm", "sections": {}}, None, None)
    )
    node = load_node.StyleRefLoad()
    out = node.load("abc")

    assert set(out) == {"ui", "result"}
    assert "Loaded" in out["ui"]["text"][0]
    style, results = out["result"]
    assert style["name"] == "Warm"
    assert results == ""


def test_load_never_fetches_style_text(monkeypatch):
    """
    STYLE.md is a target on Apply, not an output on Load — so Load has no
    style_doc output and must never spend the extra text-fetch call.
    """
    monkeypatch.setattr(
        api, "get_style_spec", lambda ref, etag=None: ({"name": "Warm", "sections": {}}, None, None)
    )
    monkeypatch.setattr(
        api,
        "get_style_text",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Load must not fetch text")),
    )
    load_node.StyleRefLoad().load("abc")
    assert len(load_node.StyleRefLoad.RETURN_NAMES) == 2


def test_load_is_an_output_node():
    """A search-only Load must be queueable alone and still display."""
    assert load_node.StyleRefLoad.OUTPUT_NODE is True


def test_search_results_render_slugs_to_copy():
    text = load_node.format_search_results(
        {"styles": [{"slug": "abc-1", "name": "Warm", "author": "@ada", "tags": ["warm", "film"]}]}
    )
    assert "abc-1" in text and "Warm" in text and "@ada" in text
    assert "style_ref" in text, "instructions must name the field as it is named now"


def test_empty_search_suggests_categories():
    text = load_node.format_search_results({"styles": [], "availableCategories": ["Photography"]})
    assert "Photography" in text
    assert "gallery" in text


def test_search_failure_does_not_sink_the_run(monkeypatch):
    """A bad search must not fail a graph whose ref is perfectly valid."""
    monkeypatch.setattr(
        api, "search_styles", lambda *a, **k: (_ for _ in ()).throw(StyleRefError("boom"))
    )
    monkeypatch.setattr(api, "get_style_spec", lambda ref, etag=None: ({"name": "Fine", "sections": {}}, None, None))

    node = load_node.StyleRefLoad()
    out = node.load("abc", "query that fails")
    style, results = out["result"]

    assert style["name"] == "Fine"
    assert "Search failed" in results


def test_search_category_is_forwarded(monkeypatch):
    """The vanilla category combo must reach the API; `any` must not."""
    seen = {}

    def fake_search(query, category=None, sort=None, limit=12):
        seen["category"] = category
        return {"styles": []}

    monkeypatch.setattr(api, "search_styles", fake_search)
    monkeypatch.setattr(api, "get_style_spec", lambda ref, etag=None: ({"name": "X", "sections": {}}, None, None))

    node = load_node.StyleRefLoad()
    node.load("abc", "warm", category="Photography")
    assert seen["category"] == "Photography"

    node.load("abc", "warm", category="any")
    assert seen["category"] is None


# ── IS_CHANGED contracts ─────────────────────────────────────────────────────


def test_load_is_changed_is_stable_when_cached():
    key1 = load_node.StyleRefLoad.IS_CHANGED("abc", "q", "any", True)
    key2 = load_node.StyleRefLoad.IS_CHANGED("abc", "q", "any", True)
    assert key1 == key2


def test_load_is_changed_reports_dirty_without_cache():
    value = load_node.StyleRefLoad.IS_CHANGED("abc", use_cache=False)
    assert isinstance(value, float) and math.isnan(value)


def test_login_is_always_dirty():
    """A cached "signed out" status would be wrong the moment a sign-in succeeded."""
    value = StyleRefLogin.IS_CHANGED()
    assert isinstance(value, float) and math.isnan(value)


def test_apply_has_no_is_changed_override():
    """
    Apply's dirtiness must come from its inputs alone. An IS_CHANGED override
    returning NaN here is the credit-burn/api-hammer class of bug —
    the node would re-run on every queue.
    """
    assert "IS_CHANGED" not in vars(apply_node.StyleRefApply)


# ── Apply: sections filter ───────────────────────────────────────────────────


def test_sections_filter_reaches_the_api_and_the_cache_key(monkeypatch):
    calls = []

    def fake_get(ref, fmt="default", compact=False, raw=False, sections=None):
        calls.append(sections)
        return f"compiled:{sections}"

    monkeypatch.setattr(api, "get_style_text", fake_get)
    style = dict(STYLE, compiled={})

    apply_node.compile_style(style, "diffusion", False, "colors, light_shadow")
    assert calls[-1] == "colors,light_shadow"

    # Same trimmed sections → cache hit; different sections → new compile.
    apply_node.compile_style(style, "diffusion", False, "colors,light_shadow")
    assert len(calls) == 1
    apply_node.compile_style(style, "diffusion", False, "")
    assert len(calls) == 2
    assert calls[-1] is None


# ── Facets: full schema coverage ─────────────────────────────────────────────


def test_facets_outputs_are_exactly_the_schema_sections_plus_custom_items():
    """
    Facets mirrors the style board one-to-one — the schema's section list in
    schema order, plus the six custom style items, and NOTHING derived (no
    palette/primary_color/width/height/negative).
    """
    from styleref_nodes import facets as facets_node

    assert facets_node.SCHEMA_SECTIONS[0] == "output_format"
    assert facets_node.SCHEMA_SECTIONS[-1] == "guardrails"
    assert "inspiration_images" not in facets_node.SCHEMA_SECTIONS, (
        "images are the Reference Images node's job"
    )
    names = facets_node.StyleRefFacets.RETURN_NAMES
    assert names == tuple(facets_node.SCHEMA_SECTIONS) + tuple(facets_node.CUSTOM_ITEM_NAMES)
    for derived in ("palette", "primary_color", "width", "height", "negative", "color_count"):
        assert derived not in names
    assert len(names) == len(facets_node.StyleRefFacets.RETURN_TYPES)
    assert len(names) == len(facets_node.StyleRefFacets.OUTPUT_TOOLTIPS)


def test_facets_split_returns_one_value_per_output(monkeypatch):
    from styleref_nodes import facets as facets_node

    node = facets_node.StyleRefFacets()
    out = node.split(STYLE)
    assert len(out) == len(facets_node.StyleRefFacets.RETURN_NAMES)
    by_name = dict(zip(facets_node.StyleRefFacets.RETURN_NAMES, out))
    assert "avoided visuals: lens flares; film grain" in by_name["guardrails"]
    assert "color palette" in by_name["colors"]
    assert by_name["custom_style_item_3"] == ""


# ── Reference Images node ────────────────────────────────────────────────────


def _style_with_images(urls):
    return make_style(
        ref="img-style",
        name="Imgs",
        sections={
            "inspiration_images": {
                "values": {"inspiration_images": [{"url": u} for u in urls]}
            }
        },
    )


def _png_bytes(width, height, color=(255, 0, 0)):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_reference_images_batches_and_counts(monkeypatch):
    from styleref_nodes import reference_images as ri

    ri._IMAGE_CACHE.clear()
    blobs = {"https://a/1.png": _png_bytes(64, 32), "https://a/2.png": _png_bytes(100, 80)}
    monkeypatch.setattr(api, "request_raw", lambda url, **k: (blobs[url], {}))

    node = ri.StyleRefReferenceImages()
    batch, count = node.fetch(_style_with_images(list(blobs)))

    assert count == 2
    # One batch = one resolution: the first image sets it, the rest resize.
    assert batch.shape == (2, 32, 64, 3)
    # IMAGE must be a torch tensor — PreviewImage/SaveImage call .cpu() on it.
    # torch is not in the pack's dev extras, so this asserts only where it is
    # installed; the node falls back to the ndarray otherwise.
    torch = pytest.importorskip("torch")
    assert isinstance(batch, torch.Tensor)
    assert batch.dtype is torch.float32
    assert float(batch.min()) >= 0.0 and float(batch.max()) <= 1.0


def test_reference_images_downloads_anonymously_and_caches(monkeypatch):
    """A bearer token must never go to storage hosts; repeats hit the cache."""
    from styleref_nodes import reference_images as ri

    ri._IMAGE_CACHE.clear()
    calls = []

    def fake_raw(url, anonymous=False, **_k):
        calls.append(anonymous)
        return _png_bytes(8, 8), {}

    monkeypatch.setattr(api, "request_raw", fake_raw)
    node = ri.StyleRefReferenceImages()
    node.fetch(_style_with_images(["https://a/1.png"]))
    node.fetch(_style_with_images(["https://a/1.png"]))

    assert calls == [True], "one anonymous download, then the cache"


def test_reference_images_errors_clearly_without_images():
    from styleref_nodes import reference_images as ri

    with pytest.raises(StyleRefError, match="no inspiration images"):
        ri.StyleRefReferenceImages().fetch(make_style(ref="x", name="Bare"))


# ── Load: save_to_library ────────────────────────────────────────────────────


def test_save_to_library_saves_once_per_session(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        api, "get_style_spec", lambda ref, etag=None: ({"name": "Warm", "sections": {}}, None, None)
    )

    def fake_save(ref):
        calls["n"] += 1
        return {"saved": True, "alreadySaved": False}

    monkeypatch.setattr(api, "save_style", fake_save)
    load_node._SAVED.clear()

    node = load_node.StyleRefLoad()
    out = node.load("abc", save_to_library=True)
    assert "Saved to your library" in out["ui"]["text"][0]
    node.load("abc", save_to_library=True)
    assert calls["n"] == 1, "idempotent server-side, but don't spam it either"
    load_node._SAVED.clear()


def test_save_failure_does_not_sink_the_run(monkeypatch):
    monkeypatch.setattr(
        api, "get_style_spec", lambda ref, etag=None: ({"name": "Warm", "sections": {}}, None, None)
    )
    monkeypatch.setattr(
        api, "save_style", lambda ref: (_ for _ in ()).throw(StyleRefError("Sign in first"))
    )
    load_node._SAVED.clear()

    out = load_node.StyleRefLoad().load("abc", save_to_library=True)
    style, _results = out["result"]
    assert style["name"] == "Warm"
    assert "Could not save" in out["ui"]["text"][0]


def test_own_styles_are_not_re_saved(monkeypatch):
    # A private style canonicalises to the builder — that, not the ref, is what
    # marks it as the caller's own.
    monkeypatch.setattr(
        api,
        "get_style_spec",
        lambda ref, etag=None: ({"name": "Mine", "sections": {}}, "https://styleref.io/styles/abc", None),
    )
    monkeypatch.setattr(
        api, "save_style", lambda ref: (_ for _ in ()).throw(AssertionError("must not be called"))
    )
    out = load_node.StyleRefLoad().load("Mine", save_to_library=True)
    assert "Already your own style" in out["ui"]["text"][0]


# ── Load: ETag revalidation ──────────────────────────────────────────────────


def test_cache_off_revalidates_with_the_stored_etag(monkeypatch):
    """use_cache=False means "re-check", not "re-download": a 304 keeps the
    cached style — including its compiled-prompt cache."""
    seen = []

    def fake_spec(ref, etag=None):
        seen.append(etag)
        if etag == 'W/"e1"':
            return None, None, etag  # 304
        return {"name": "Warm", "sections": {}}, None, 'W/"e1"'

    monkeypatch.setattr(api, "get_style_spec", fake_spec)

    first = load_node.fetch_style("abc")
    first["compiled"]["diffusion:full:"] = "cached-compilation"

    again = load_node.fetch_style("abc", use_cache=False)
    assert seen == [None, 'W/"e1"']
    assert again is first, "304 must keep the exact cached payload"
    assert again["compiled"]["diffusion:full:"] == "cached-compilation"


def test_changed_style_replaces_the_cache_on_revalidation(monkeypatch):
    def fake_spec(ref, etag=None):
        if etag is None:
            return {"name": "V1", "sections": {}}, None, 'W/"e1"'
        return {"name": "V2", "sections": {}}, None, 'W/"e2"'

    monkeypatch.setattr(api, "get_style_spec", fake_spec)
    load_node.fetch_style("abc")
    fresh = load_node.fetch_style("abc", use_cache=False)
    assert fresh["name"] == "V2"
