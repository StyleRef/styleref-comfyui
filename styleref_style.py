"""
The STYLEREF_STYLE payload and the facet readers that dig values out of it.

STYLEREF_STYLE is a plain dict so it survives ComfyUI's graph serialization and
stays inspectable, but it is built and read only through this module — the node
files never index into the raw API JSON.

Shape:
    {
      "ref":        str   the ref used to fetch it (share slug, id, or URL)
      "name":       str
      "slug":       str | None
      "author":     str | None
      "url":        str | None   canonical share URL, for attribution
      "sections":   dict         the server's format=json body, verbatim
      "compiled":   dict         {target: prose spec} — cached per target
      "source":     str          "gallery" | "library" | "extraction"
    }

`sections` mirrors the server's structure exactly:
    sections[<section>] = {"weight": <authority>, "values": {<type>: <value>}}
so a new section added server-side flows through without a client change.
"""

from __future__ import annotations

import re
from typing import Any

# Section/type coordinates for the facets we surface. Kept in one table so the
# Facets node stays declarative and a server-side rename is a one-line fix.
PALETTE = ("colors", "color_palette")
MOOD = ("mood_personality", "mood_personality")

# Every guardrail field that belongs in an image model's NEGATIVE channel, in
# render order. avoided_words is deliberately absent: it constrains text output
# (buzzwords, brand voice) and stays in the text formats — the same split as
# voice_language.
GUARDRAIL_ARTIFACTS = ("guardrails", "common_artifacts_to_avoid")
GUARDRAILS = ("guardrails", "avoided_visuals")
GUARDRAIL_COLORS = ("guardrails", "avoided_colors")
GUARDRAIL_LAYOUTS = ("guardrails", "avoided_layouts")
NEGATIVE_FIELDS = (GUARDRAIL_ARTIFACTS, GUARDRAILS, GUARDRAIL_COLORS, GUARDRAIL_LAYOUTS)

# Leading directive words stripped from guardrail terms: a negative channel
# negates the literal tokens, so "avoid X" would negate the word "avoid".
# Mirrors splitGuardrailTerms in src/lib/services/styleref-generator.ts —
# keep the two in sync.
_DIRECTIVE_PREFIX = re.compile(
    r"^(?:please\s+)?(?:avoid\s+using|avoid\s+any|avoid|do\s+not\s+use|don'?t\s+use"
    r"|do\s+not|don'?t|never\s+use|never|exclude|without|no|not)\b[\s:,-]*",
    re.IGNORECASE,
)

# Facets that read as "everything in this section" rather than one field.
LIGHTING_SECTION = "light_shadow"
COMPOSITION_SECTION = "spatial_hierarchy"


def make_style(
    ref: str,
    name: str,
    sections: dict[str, Any] | None = None,
    slug: str | None = None,
    author: str | None = None,
    url: str | None = None,
    source: str = "gallery",
    compiled: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "ref": ref,
        "name": name,
        "slug": slug,
        "author": author,
        "url": url,
        "sections": sections or {},
        "compiled": compiled or {},
        "source": source,
    }


def is_style(value: Any) -> bool:
    return isinstance(value, dict) and "sections" in value and "ref" in value


def section_values(style: dict[str, Any], section: str) -> dict[str, Any]:
    node = (style.get("sections") or {}).get(section) or {}
    values = node.get("values")
    return values if isinstance(values, dict) else {}


def field(style: dict[str, Any], coord: tuple[str, str]) -> Any:
    return section_values(style, coord[0]).get(coord[1])


def palette_hexes(style: dict[str, Any]) -> list[str]:
    """
    Hex colors in declared order.

    The palette value carries `mode`, `solidColors` (hex + amount), and
    `gradients`. Gradient stops are included because a gradient-mode palette
    would otherwise read as having no colors at all.
    """
    value = field(style, PALETTE)
    if not isinstance(value, dict):
        return []

    hexes: list[str] = []
    for entry in value.get("solidColors") or []:
        if isinstance(entry, dict) and entry.get("hex"):
            hexes.append(str(entry["hex"]))

    for gradient in value.get("gradients") or []:
        if not isinstance(gradient, dict):
            continue
        for stop in gradient.get("stops") or []:
            if isinstance(stop, dict) and stop.get("hex"):
                hexes.append(str(stop["hex"]))
            elif isinstance(stop, str):
                hexes.append(stop)

    # Dedupe case-insensitively, preserving order — the first occurrence carries
    # the highest `amount`, so it is the one worth keeping.
    seen: set[str] = set()
    ordered: list[str] = []
    for hx in hexes:
        key = hx.strip().lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(hx.strip())
    return ordered


def _flatten(value: Any) -> str:
    """Render any block value as one readable line."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(p for p in (_flatten(v) for v in value) if p)
    if isinstance(value, dict):
        # Prefer a human-facing field when the block has one.
        for key in ("value", "label", "name", "description"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        return ", ".join(f"{k}: {p}" for k, v in value.items() if (p := _flatten(v)))
    return str(value)


def section_summary(style: dict[str, Any], section: str) -> str:
    """`type: value` lines for a whole section — wire straight into a text input."""
    values = section_values(style, section)
    lines = []
    for key, raw in values.items():
        rendered = _flatten(raw)
        if rendered:
            lines.append(f"{key.replace('_', ' ')}: {rendered}")
    return "\n".join(lines)


def mood_terms(style: dict[str, Any]) -> str:
    return _flatten(field(style, MOOD))


def split_guardrail_terms(value: Any) -> list[str]:
    """
    Normalize a free-text guardrail value into terse negative-prompt terms.

    Guardrail textareas are user/AI-authored and arrive as anything from a
    clean "lens flares; heavy bokeh" to a sentence like "Avoid harsh drop
    shadows". Negative channels consume *terms*: split on ;/,/newlines (never
    on "and" — "black and white" is one concept), strip leading directive
    words, drop trailing periods, dedupe case-insensitively.
    """
    chunks: list[str] = []
    for entry in value if isinstance(value, list) else [value]:
        rendered = _flatten(entry)
        if rendered:
            chunks.extend(re.split(r"[;,\n]+", rendered))

    seen: set[str] = set()
    terms: list[str] = []
    for chunk in chunks:
        term = _DIRECTIVE_PREFIX.sub("", chunk.strip()).rstrip(".").strip()
        key = term.lower()
        if term and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def negative_terms(style: dict[str, Any]) -> str:
    """
    The style's guardrails as a ready-to-use negative prompt.

    Comma-separated terse terms — the idiom every negative-consuming surface
    expects: an SDXL/SD1.5 negative encoder (the diffusion target), FLUX's
    negative conditioning when run at real CFG, and a human pasting into a
    negative box. Reads every visual guardrail field (NEGATIVE_FIELDS),
    normalized via split_guardrail_terms and deduped across fields.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for coord in NEGATIVE_FIELDS:
        for term in split_guardrail_terms(field(style, coord)):
            key = term.lower()
            if key not in seen:
                seen.add(key)
                terms.append(term)
    return ", ".join(terms)


# The style's stored images (uploaded inspiration images; for extracted styles
# this includes the source image, attached server-side at extraction time).
INSPIRATION = ("inspiration_images", "inspiration_images")


def inspiration_image_urls(style: dict[str, Any]) -> list[str]:
    """URLs of the style's inspiration images, in stored order."""
    value = field(style, INSPIRATION)
    if not isinstance(value, list):
        return []
    urls: list[str] = []
    for entry in value:
        if isinstance(entry, dict) and isinstance(entry.get("url"), str) and entry["url"].strip():
            urls.append(entry["url"].strip())
        elif isinstance(entry, str) and entry.strip():
            urls.append(entry.strip())
    return urls


# output_format coordinates feeding the width/height derivation.
ASPECT_RATIO = ("output_format", "aspect_ratio")
RESOLUTION_TARGET = ("output_format", "resolution_target")

# Long-edge pixels per resolution_target option. Non-pixel targets (dpi,
# vector, variable) fall back to the sampler-friendly default.
_RESOLUTION_LONG_EDGE = {"1k": 1024, "2k": 2048, "4k": 4096, "8k": 8192}
_DEFAULT_LONG_EDGE = 1024

# Named ratios that don't parse as W:H.
_NAMED_RATIOS = {"imax": (1.43, 1.0)}


def _parse_ratio(value: str) -> tuple[float, float] | None:
    """'16:9 (Landscape)' → (16, 9). Returns None for Custom/unknown."""
    text = value.strip().lower()
    named = _NAMED_RATIOS.get(text.split("(")[0].strip())
    if named:
        return named
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    w, h = float(match.group(1)), float(match.group(2))
    return (w, h) if w > 0 and h > 0 else None


def _round8(value: float) -> int:
    """Samplers want multiples of 8."""
    return max(8, int(round(value / 8)) * 8)


def dimensions(style: dict[str, Any]) -> tuple[int, int]:
    """
    (width, height) derived from the style's aspect_ratio + resolution_target —
    so an Empty Latent Image can be driven by the style itself. Defaults to
    1024×1024 when the style doesn't say.
    """
    ratio = _parse_ratio(_flatten(field(style, ASPECT_RATIO))) or (1.0, 1.0)
    res = _flatten(field(style, RESOLUTION_TARGET)).strip().lower()
    long_edge = _RESOLUTION_LONG_EDGE.get(res, _DEFAULT_LONG_EDGE)

    rw, rh = ratio
    if rw >= rh:
        return _round8(long_edge), _round8(long_edge * rh / rw)
    return _round8(long_edge * rw / rh), _round8(long_edge)


def custom_item(style: dict[str, Any], index: int) -> str:
    """custom_style_item_{index} rendered as text; empty string when unset."""
    return _flatten(field(style, ("custom_style_items", f"custom_style_item_{index}")))


def attribution(style: dict[str, Any]) -> str:
    name = style.get("name") or "Untitled style"
    author = style.get("author")
    url = style.get("url")
    line = f"{name}" + (f" by {author}" if author else "")
    return f"{line} — {url}" if url else line


def summary_line(style: dict[str, Any]) -> str:
    """One-line status for a node's text output."""
    sections = style.get("sections") or {}
    count = sum(len(section_values(style, s)) for s in sections)
    return f"{attribution(style)} · {len(sections)} sections, {count} fields"
