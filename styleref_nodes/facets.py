"""
StyleRef Facets — break a style into its sections for power-user graphs.

Apply is the paved road. This is the escape hatch: the style's sections come
out as individual STRING outputs so any field can drive things a prompt string
cannot — IPAdapter weights, conditioning regions, per-section text encoders.

The output list is EXACTLY the style schema's section list, in schema order,
plus the six custom style items individually —
nothing derived, nothing invented, so the node reads one-to-one against the
style board on styleref.io. Output names are the schema's own section ids.
The section list is data-driven from one constant: a future schema section is
a one-line addition here.

(The assembled negative lives on Apply's `negative` output; the inspiration
images live on StyleRef Reference Images.)
"""

from __future__ import annotations

from typing import Any

from styleref_api import StyleRefError
from styleref_style import custom_item, is_style, section_summary

CATEGORY = "StyleRef"

# Every text-bearing section of src/lib/storage/style-schema.json, in schema
# order (inspiration_images is images, not text — see StyleRef Reference
# Images for those).
SCHEMA_SECTIONS = [
    "output_format",
    "container_boundary",
    "mood_personality",
    "colors",
    "typography",
    "light_shadow",
    "spatial_hierarchy",
    "surface_material",
    "motion",
    "shape_language",
    "stroke_system",
    "voice_language",
    "post_processing",
    "background_environment",
    "artistic_mediums",
    "ui_web",
    "references",
    "guardrails",
]

CUSTOM_ITEM_COUNT = 6
CUSTOM_ITEM_NAMES = [f"custom_style_item_{i}" for i in range(1, CUSTOM_ITEM_COUNT + 1)]

RETURN_NAMES = tuple(SCHEMA_SECTIONS) + tuple(CUSTOM_ITEM_NAMES)
RETURN_TYPES = ("STRING",) * len(RETURN_NAMES)


class StyleRefFacets:
    """Expose every section of a style as its own output — the schema's list, exactly."""

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "style": ("STYLEREF_STYLE", {"tooltip": "From StyleRef Load."}),
            },
        }

    RETURN_TYPES = RETURN_TYPES
    RETURN_NAMES = RETURN_NAMES
    OUTPUT_TOOLTIPS = (
        tuple(
            f"The style's {name.replace('_', ' ')} fields as readable lines."
            for name in SCHEMA_SECTIONS
        )
        + tuple(f"Custom style item {i} — empty when unset." for i in range(1, CUSTOM_ITEM_COUNT + 1))
    )
    FUNCTION = "split"
    CATEGORY = CATEGORY
    DESCRIPTION = "Expose every section of a style as its own output — the schema's section list, exactly."

    def split(self, style: dict[str, Any]):
        if not is_style(style):
            raise StyleRefError(
                "The `style` input is not a StyleRef style — connect StyleRef Load."
            )

        return tuple(section_summary(style, section) for section in SCHEMA_SECTIONS) + tuple(
            custom_item(style, i) for i in range(1, CUSTOM_ITEM_COUNT + 1)
        )
