"""
StyleRef Apply — turn a style + a subject into positive/negative prompts.

The compilation happens **server-side**. StyleRef already compiles a style per
target (FLUX phrasing differs from SDXL phrasing differs from natural language),
and reimplementing any of that here would fork the prompt logic into a client we
cannot update as fast as the server. So this node fetches the compiled spec and
composes it with the subject; it never writes style prose itself.
"""

from __future__ import annotations

import re
from typing import Any

import styleref_api as api
from styleref_api import StyleRefError
from styleref_style import attribution, is_style, negative_terms

CATEGORY = "StyleRef"

# Node-facing target → the server's `format` parameter.
#
# The list is EXACTLY the web app's copy-box format list, in the same order:
# AI Tools, STYLE.md, Flux, Midjourney, Diffusion, JSON. The style means the
# same thing here as on styleref.io, format for format.
#
# Only `flux` and `diffusion` (DALL·E, Stable Diffusion/SDXL, Imagen) are
# sampler text. The rest are for copying OUT of ComfyUI — ai_tools is prose
# for ChatGPT/Gemini/text LLMs, style_md is the portable STYLE.md document,
# midjourney is Midjourney-phrased (Midjourney cannot be sampled here), json
# is the structured spec. The JS warning badge fires when a copy-out target
# feeds a CLIP encoder.
TARGET_FORMATS = {
    "ai_tools": "default",
    "style_md": "stylemd",
    "flux": "flux",
    "midjourney": "midjourney",
    "diffusion": "diffusion",
    "json": "json",
}
TARGETS = list(TARGET_FORMATS)

# Targets that are not sampler text — the JS warning badge fires when one of
# these feeds a CLIP encoder.
COPY_OUT_TARGETS = ("ai_tools", "style_md", "midjourney", "json")

# Targets that are whole documents: shipped verbatim, no subject composition
# (unless the document itself carries the subject slot).
DOCUMENT_TARGETS = ("style_md", "json")

# The slot StyleRef's generator leaves for the subject in every prose format.
SUBJECT_PLACEHOLDER = "[YOUR SUBJECT/SCENE PROMPT HERE]"

# Rough chars-per-token for the node-body estimate. Precision is not the point —
# the estimate exists to make `compact` discoverable before a prompt truncates.
CHARS_PER_TOKEN = 4

# CLIP encoders effectively attend to ~77 tokens per chunk; past ~150 tokens a
# diffusion (SDXL/SD1.5) prompt is mostly truncation. FLUX's T5 tolerates more.
COMPACT_HINT_THRESHOLD = 150

# How much of the composed positive to show in the node body.
PREVIEW_CHARS = 200


def compile_style(
    style: dict[str, Any], target: str, compact: bool, sections: str = ""
) -> str:
    """
    Compiled prose for one target, cached on the style payload.

    The cache is what makes a re-queue free: ComfyUI re-runs this node whenever
    the subject changes, and each of those runs would otherwise be an API call
    for a style that has not moved.
    """
    fmt = TARGET_FORMATS.get(target, "default")
    sections = ",".join(p.strip() for p in sections.split(",") if p.strip())
    cache_key = f"{fmt}:{'compact' if compact else 'full'}:{sections}"

    compiled = style.setdefault("compiled", {})
    if cache_key in compiled:
        return compiled[cache_key]

    ref = style.get("ref") or ""
    if not ref:
        raise StyleRefError("This style has no reference to compile — reload it in StyleRef Load.")

    # raw=True: no markdown metadata header, no appended attribution line —
    # both would reach the text encoder as literal tokens. Attribution is
    # returned on this node's own `attribution` output instead. The exceptions
    # are the document formats, where the header/structure is the point.
    raw = fmt not in ("stylemd", "json")
    text = api.get_style_text(
        ref, fmt=fmt, compact=compact, raw=raw, sections=sections or None
    ).strip()
    compiled[cache_key] = text
    return text


def compose_prompt(subject: str, style_text: str, subject_first: bool = True) -> str:
    """
    Join subject and style.

    StyleRef's compiled specs carry an explicit slot — `[YOUR SUBJECT/SCENE
    PROMPT HERE]` — marking where the subject belongs in that target's phrasing.
    When it's there, the subject is substituted into it: the slot's position is
    the server's considered answer to where the subject reads best for FLUX vs
    SDXL vs natural language, and it beats any ordering guess made here.
    Leaving the placeholder in place would be worse than either — the encoder
    would take that bracketed string as literal prompt text.

    Only when there's no slot does ordering matter, and then the subject leads:
    early tokens carry the most weight, so the subject is what must survive
    truncation. The style is the modifier.
    """
    subject = subject.strip()
    style_text = style_text.strip()
    if not subject:
        # Strip the slot rather than shipping it — an empty subject is a style
        # preview, not an instruction to render the words "YOUR SUBJECT".
        return strip_subject_placeholder(style_text)
    if not style_text:
        return subject

    if SUBJECT_PLACEHOLDER in style_text:
        return style_text.replace(SUBJECT_PLACEHOLDER, subject)

    return f"{subject}\n\n{style_text}" if subject_first else f"{style_text}\n\n{subject}"


def strip_subject_placeholder(text: str) -> str:
    """Remove the slot and any punctuation left stranded by its removal."""
    return re.sub(r"\s*\[YOUR SUBJECT/SCENE PROMPT HERE\]\.?", "", text).strip()


def compose_negative(style: dict[str, Any], extra: str) -> str:
    """
    The style's own guardrails plus whatever the user typed — nothing else.

    Deliberately no injected "quality negatives" boilerplate: the style is the
    authority on what to avoid, and anything generic the user wants belongs in
    `extra_negative` where it stays visible.
    """
    parts: list[str] = []
    guardrails = negative_terms(style)
    if guardrails:
        parts.append(guardrails)
    if extra.strip():
        parts.append(extra.strip())
    return ", ".join(p.rstrip(" ,;.") for p in parts if p)


def estimate_tokens(text: str) -> int:
    """Chars/4 heuristic — deliberately rough, see CHARS_PER_TOKEN."""
    return max(0, round(len(text) / CHARS_PER_TOKEN))


def preview_text(positive: str, target: str, compact: bool) -> str:
    """The node-body summary: prompt preview + token estimate."""
    shown = positive if len(positive) <= PREVIEW_CHARS else positive[:PREVIEW_CHARS] + "…"
    tokens = estimate_tokens(positive)
    line = f"≈{tokens} tokens ({target})"
    if tokens > COMPACT_HINT_THRESHOLD and target == "diffusion" and not compact:
        line += " — consider `compact`"
    return f"{shown}\n\n{line}"


class StyleRefApply:
    """Compose a subject with a StyleRef style into positive/negative prompts."""

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "style": ("STYLEREF_STYLE", {"tooltip": "From StyleRef Load."}),
                "subject": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "placeholder": "a lighthouse on a rocky shore at dawn",
                        "tooltip": "What to depict — the style describes how it looks. "
                        "Leave empty to preview the style text alone.",
                    },
                ),
                "target": (
                    TARGETS,
                    {
                        "default": "diffusion",
                        "tooltip": "Which format to compile — exactly the web app's "
                        "copy list. For the sampler in this graph use flux, or "
                        "diffusion for SDXL/SD1.5/DALL·E. ai_tools (ChatGPT & text "
                        "LLMs), style_md (STYLE.md document), midjourney, and json "
                        "are for copying out of ComfyUI.",
                    },
                ),
            },
            "optional": {
                "extra_negative": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "placeholder": "extra negative terms",
                        "tooltip": "Your own additional negative terms, appended after "
                        "the style's guardrails.",
                    },
                ),
                "compact": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Shorter spec — use when the style overwhelms the "
                        "subject or you are near the token limit.",
                    },
                ),
                "sections": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "e.g. colors,light_shadow",
                        "tooltip": "Compile only these style sections, comma-separated "
                        "(e.g. colors,light_shadow,mood_personality) — empty = all. "
                        "More precise than `compact` when the style overwhelms the "
                        "subject.",
                    },
                ),
                "subject_first": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Only applies when the style has no built-in subject "
                        "slot: put the subject before the style text. Most styles carry "
                        "a slot, in which case this toggle does nothing.",
                    },
                ),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive", "negative", "attribution")
    OUTPUT_TOOLTIPS = (
        "Wire into your positive CLIP Text Encode.",
        "Wire into your negative CLIP Text Encode.",
        "Style name, author, and link — for captions or metadata, never the encoder.",
    )
    FUNCTION = "apply"
    CATEGORY = CATEGORY
    DESCRIPTION = "Compose a subject with a StyleRef style into positive and negative prompts."

    def apply(
        self,
        style: dict[str, Any],
        subject: str,
        target: str,
        extra_negative: str = "",
        compact: bool = False,
        sections: str = "",
        subject_first: bool = True,
    ):
        if not is_style(style):
            raise StyleRefError(
                "The `style` input is not a StyleRef style — connect StyleRef Load."
            )
        if not style.get("ref"):
            raise StyleRefError(
                "No style is loaded — set a `style_ref` on the StyleRef Load node."
            )

        style_text = compile_style(style, target, compact, sections)
        if target in DOCUMENT_TARGETS and SUBJECT_PLACEHOLDER not in style_text:
            # STYLE.md/JSON are documents, not prompts: without a subject slot,
            # prepending the subject would corrupt them. Ship verbatim.
            positive = style_text
        else:
            positive = compose_prompt(subject, style_text, subject_first)
        negative = compose_negative(style, extra_negative)

        # ui.text: show what was actually composed, in the node body —
        # otherwise the first visible evidence of the prompt is the final image.
        return {
            "ui": {"text": [preview_text(positive, target, compact)]},
            "result": (positive, negative, attribution(style)),
        }
