"""
Facet-reading tests, against a fixture captured from the live API
(GET /api/v1/styles/{ref}?format=json) so the section and field names under test
are the real ones, not invented.
"""

from __future__ import annotations

import pytest

from styleref_style import (
    COMPOSITION_SECTION,
    LIGHTING_SECTION,
    attribution,
    is_style,
    make_style,
    mood_terms,
    negative_terms,
    palette_hexes,
    section_summary,
    summary_line,
)

SECTIONS = {
    "colors": {
        "weight": "Absolute",
        "values": {
            "color_palette": {
                "mode": "solid",
                "gradients": [],
                "solidColors": [
                    {"hex": "#F6F6F4", "amount": 35},
                    {"hex": "#D7DDE0", "amount": 15},
                    {"hex": "#1B2A44", "amount": 10},
                ],
            },
            "palette_size": "8 colors",
        },
    },
    "mood_personality": {
        "weight": "Primary",
        "values": {"mood_personality": ["Serious", "Calm", "Minimalist"], "energy_level": "Low"},
    },
    "light_shadow": {
        "weight": "secondary",
        "values": {"light_source_type": "Natural", "shadow_type": "Soft"},
    },
    "spatial_hierarchy": {
        "weight": "secondary",
        "values": {"perspective_type": "Eye level", "symmetry": "Asymmetric"},
    },
    "guardrails": {
        "weight": "absolute",
        "values": {"avoided_visuals": "Lens flares; bokeh; film grain"},
    },
}


@pytest.fixture
def style():
    return make_style(
        ref="9a2adtz6-cd8d77ee2f51",
        name="Real Moments",
        sections=SECTIONS,
        author="@styleref",
        url="https://styleref.io/share/9a2adtz6-cd8d77ee2f51",
    )


def test_palette_preserves_declared_order(style):
    """Order matters — it runs most-used first, so [0] is the dominant color."""
    assert palette_hexes(style) == ["#F6F6F4", "#D7DDE0", "#1B2A44"]


def test_palette_includes_gradient_stops():
    """A gradient-mode palette would otherwise read as having no colors at all."""
    style = make_style(
        ref="x",
        name="Gradient",
        sections={
            "colors": {
                "values": {
                    "color_palette": {
                        "mode": "gradient",
                        "solidColors": [],
                        "gradients": [{"stops": [{"hex": "#FF0000"}, {"hex": "#0000FF"}]}],
                    }
                }
            }
        },
    )
    assert palette_hexes(style) == ["#FF0000", "#0000FF"]


def test_palette_dedupes_case_insensitively():
    style = make_style(
        ref="x",
        name="Dupes",
        sections={
            "colors": {
                "values": {
                    "color_palette": {
                        "solidColors": [{"hex": "#AABBCC"}, {"hex": "#aabbcc"}, {"hex": "#123456"}]
                    }
                }
            }
        },
    )
    assert palette_hexes(style) == ["#AABBCC", "#123456"]


def test_palette_is_empty_for_a_style_without_colors():
    assert palette_hexes(make_style(ref="x", name="Bare")) == []


def test_mood_list_flattens_to_a_prompt_fragment(style):
    assert mood_terms(style) == "Serious, Calm, Minimalist"


def test_negatives_are_normalized_comma_terms(style):
    """
    The negative output is negative-channel idiom: terse comma-separated terms
    (an SD negative box negates literal tokens, so no directive words, and the
    convention there is commas, not semicolons).
    """
    assert negative_terms(style) == "Lens flares, bokeh, film grain"


def test_negatives_cover_artifacts_and_avoided_colors():
    """
    Plan P1-9: guardrails carry more than avoided_visuals — artifacts to avoid
    and avoided colors are user-authored constraints and belong in the negative.
    """
    style = make_style(
        ref="x",
        name="Guarded",
        sections={
            "guardrails": {
                "values": {
                    "avoided_visuals": "Lens flares",
                    "common_artifacts_to_avoid": ["AI Gloss / Plastic Smoothing", "Uncanny skin"],
                    "avoided_colors": "neon green",
                }
            }
        },
    )
    negative = negative_terms(style)
    assert "Lens flares" in negative
    assert "AI Gloss" in negative
    assert "neon green" in negative


def test_negatives_strip_directive_words_and_split_sentences():
    """
    "Avoid harsh shadows" in a negative channel would negate the word "avoid".
    Users author sentences; the channel needs terms.
    """
    from styleref_style import split_guardrail_terms

    style = make_style(
        ref="x",
        name="Messy",
        sections={
            "guardrails": {
                "values": {
                    "avoided_visuals": "Avoid harsh drop shadows; no neon glow",
                    "avoided_colors": "never use hot pink",
                    "avoided_layouts": "cluttered grids",
                }
            }
        },
    )
    assert negative_terms(style) == "harsh drop shadows, neon glow, hot pink, cluttered grids"
    # "black and white" is one concept — never split on "and".
    assert split_guardrail_terms("black and white photography") == ["black and white photography"]


def test_negatives_exclude_text_domain_words():
    """avoided_words constrains text output (buzzwords) — not image negatives."""
    style = make_style(
        ref="x",
        name="Wordy",
        sections={"guardrails": {"values": {"avoided_words": "synergy; disrupt"}}},
    )
    assert negative_terms(style) == ""


def test_negatives_dedupe_repeated_fields():
    style = make_style(
        ref="x",
        name="Dupes",
        sections={
            "guardrails": {
                "values": {
                    "avoided_visuals": "film grain",
                    "common_artifacts_to_avoid": "Film Grain",
                }
            }
        },
    )
    assert negative_terms(style).lower().count("film grain") == 1


def test_section_summary_is_readable_lines(style):
    summary = section_summary(style, LIGHTING_SECTION)
    assert "light source type: Natural" in summary
    assert "shadow type: Soft" in summary


def test_section_summary_of_missing_section_is_empty(style):
    assert section_summary(style, "does_not_exist") == ""


def test_composition_section_resolves(style):
    assert "perspective type: Eye level" in section_summary(style, COMPOSITION_SECTION)


def test_unknown_sections_do_not_break_reading():
    """A section added server-side must flow through, not raise."""
    style = make_style(ref="x", name="Future", sections={"brand_new": {"values": {"a": "b"}}})
    assert section_summary(style, "brand_new") == "a: b"
    assert palette_hexes(style) == []


def test_attribution_includes_author_and_url(style):
    assert attribution(style) == "Real Moments by @styleref — https://styleref.io/share/9a2adtz6-cd8d77ee2f51"


def test_attribution_degrades_without_author_or_url():
    assert attribution(make_style(ref="x", name="Solo")) == "Solo"


def test_summary_line_counts_fields(style):
    assert "5 sections" in summary_line(style)


def test_is_style_rejects_arbitrary_dicts():
    """Guards the node inputs against a mis-wired connection."""
    assert is_style({"foo": "bar"}) is False
    assert is_style("a string") is False
    assert is_style(make_style(ref="x", name="ok")) is True


# ── dimensions (P5-2: width/height from the style) ───────────────────────────


def _fmt_style(aspect: str | None = None, resolution: str | None = None):
    values = {}
    if aspect:
        values["aspect_ratio"] = aspect
    if resolution:
        values["resolution_target"] = resolution
    return make_style(ref="x", name="Fmt", sections={"output_format": {"values": values}})


def test_dimensions_16_9_at_1k():
    from styleref_style import dimensions

    assert dimensions(_fmt_style("16:9 (Landscape)", "1K")) == (1024, 576)


def test_dimensions_portrait_puts_the_long_edge_on_height():
    from styleref_style import dimensions

    width, height = dimensions(_fmt_style("9:16 (Portrait)", "2K"))
    assert height == 2048
    assert width == 1152


def test_dimensions_default_to_square_1024():
    from styleref_style import dimensions

    assert dimensions(make_style(ref="x", name="Bare")) == (1024, 1024)
    # dpi / vector targets aren't pixel sizes — fall back to the default edge.
    assert dimensions(_fmt_style("1:1 (Square)", "300dpi")) == (1024, 1024)


def test_dimensions_are_sampler_friendly_multiples_of_8():
    from styleref_style import dimensions

    width, height = dimensions(_fmt_style("2.39:1 (Cinematic Landscape)", "1K"))
    assert width % 8 == 0 and height % 8 == 0


# ── custom items & inspiration urls ──────────────────────────────────────────


def test_custom_item_reads_by_index_and_defaults_empty():
    from styleref_style import custom_item

    style = make_style(
        ref="x",
        name="Custom",
        sections={"custom_style_items": {"values": {"custom_style_item_2": "brand watermark"}}},
    )
    assert custom_item(style, 2) == "brand watermark"
    assert custom_item(style, 1) == ""


def test_inspiration_image_urls_in_stored_order():
    from styleref_style import inspiration_image_urls

    style = make_style(
        ref="x",
        name="Insp",
        sections={
            "inspiration_images": {
                "values": {
                    "inspiration_images": [
                        {"url": "https://cdn.styleref.io/a.webp"},
                        {"url": "https://cdn.styleref.io/b.webp"},
                        {"notaurl": True},
                    ]
                }
            }
        },
    )
    assert inspiration_image_urls(style) == [
        "https://cdn.styleref.io/a.webp",
        "https://cdn.styleref.io/b.webp",
    ]


def test_inspiration_image_urls_empty_without_section():
    from styleref_style import inspiration_image_urls

    assert inspiration_image_urls(make_style(ref="x", name="Bare")) == []
