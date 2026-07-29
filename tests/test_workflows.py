"""
Workflow template tests.

A broken template is only discovered when a user drags it onto a canvas and it
fails silently, so the structural invariants are checked here instead: every
link points at a node that exists, at a slot that exists, carrying a type both
ends agree on — plus the layout invariants: no overlapping nodes, instructions
in visible Note nodes, titles short enough to render.
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from scripts.build_workflows import (
    COLLAPSED,
    FACET_SHOWCASE,
    INPUTS,
    MAX_NOTE,
    MAX_TITLE,
    MIN_H,
    OUTPUTS,
    WORKFLOWS,
    effective_size,
)
from styleref_nodes.facets import StyleRefFacets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(ROOT, "workflows")

FILES = sorted(glob.glob(os.path.join(WORKFLOW_DIR, "**", "*.json"), recursive=True))


def _relpath(path: str) -> str:
    """Key into WORKFLOWS — a POSIX-style path relative to workflows/."""
    return os.path.relpath(path, WORKFLOW_DIR).replace(os.sep, "/")


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _note_text(graph: dict) -> str:
    """All visible Note-node text — the instruction channel."""
    return "\n".join(
        str(n["widgets_values"][0])
        for n in graph["nodes"]
        if n["type"] == "Note" and n.get("widgets_values")
    )


def test_every_declared_workflow_is_committed():
    on_disk = {_relpath(p) for p in FILES}
    assert set(WORKFLOWS) == on_disk


def test_extraction_template_is_gone():
    """Extraction lives on the web; template 03 tells that story."""
    assert "03-extract-and-reuse.json" not in {os.path.basename(p) for p in FILES}
    assert "03-your-own-style.json" in {os.path.basename(p) for p in FILES}


STORIES = [
    "01-quickstart.json",
    "02-consistency-grid.json",
    "03-your-own-style.json",
    "04-reference-images.json",
    "05-facets.json",
]


def test_sdxl_set_ships_every_story():
    """
    The root (SDXL) set must tell all five stories. It shipped without the
    reference-images template once, which nothing caught: every other test
    iterates whatever happens to be on disk, so a missing file is simply a
    story never checked. Pinning the set is what makes an omission fail.
    """
    root = {_relpath(p) for p in FILES if "/" not in _relpath(p)}
    assert root == set(STORIES)


def test_zimage_set_ships_every_story():
    """The Z-Image folder tells the same five stories on its own loader stack."""
    zimage = {_relpath(p) for p in FILES if _relpath(p).startswith("z-image/")}
    assert zimage == {f"z-image/{name}" for name in STORIES}


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_committed_file_matches_the_generator(path):
    """The generator is the source of truth — a hand-edit here would be lost."""
    expected = WORKFLOWS[_relpath(path)]()
    assert _load(path) == expected, (
        f"{_relpath(path)} is stale. Run: python scripts/build_workflows.py"
    )


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_only_the_shipped_styleref_nodes_appear(path):
    """A template referencing a removed node fails on the canvas."""
    allowed = {
        "StyleRefLoad",
        "StyleRefApply",
        "StyleRefFacets",
        "StyleRefReferenceImages",
        "StyleRefLogin",
    }
    used = {n["type"] for n in _load(path)["nodes"] if n["type"].startswith("StyleRef")}
    assert used <= allowed, f"unknown StyleRef node(s): {used - allowed}"


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_links_are_structurally_valid(path):
    graph = _load(path)
    nodes = {n["id"]: n for n in graph["nodes"]}

    for link_id, src, src_slot, dst, dst_slot, kind in graph["links"]:
        assert src in nodes, f"link {link_id} originates at missing node {src}"
        assert dst in nodes, f"link {link_id} targets missing node {dst}"

        source = nodes[src]
        target = nodes[dst]
        assert src_slot < len(source["outputs"]), f"link {link_id}: bad output slot"
        assert dst_slot < len(target["inputs"]), f"link {link_id}: bad input slot"

        assert source["outputs"][src_slot]["type"] == kind
        target_type = target["inputs"][dst_slot]["type"]
        # "*" is ComfyUI's wildcard input (Preview Any) — it accepts any type.
        assert target_type in (kind, "*"), (
            f"link {link_id}: {source['type']} emits {kind} into "
            f"{target['type']}.{target['inputs'][dst_slot]['name']}"
        )


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_link_ids_are_unique_and_registered_on_both_ends(path):
    graph = _load(path)
    nodes = {n["id"]: n for n in graph["nodes"]}

    ids = [link[0] for link in graph["links"]]
    assert len(ids) == len(set(ids)), "duplicate link ids"

    for link_id, src, src_slot, dst, dst_slot, _kind in graph["links"]:
        assert link_id in nodes[src]["outputs"][src_slot]["links"]
        assert nodes[dst]["inputs"][dst_slot]["link"] == link_id


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_no_nodes_overlap_at_default_sizes(path):
    """
    Overlapping nodes read as a broken template — layout is math, not guesses, so
    this must hold for every template.

    Boxes come from `effective_size`, the generator's own answer, so a collapsed
    node is measured as the title bar it actually draws rather than the size it
    carries for when someone expands it.
    """
    nodes = _load(path)["nodes"]
    boxes = [(n["id"], *n["pos"], *effective_size(n)) for n in nodes]
    for i, (id_a, ax, ay, aw, ah) in enumerate(boxes):
        for id_b, bx, by, bw, bh in boxes[i + 1 :]:
            separated = ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay
            assert separated, f"nodes {id_a} and {id_b} overlap in {os.path.basename(path)}"


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_every_workflow_explains_itself_in_a_visible_note(path):
    """
    Templates are documentation — and ComfyUI never renders extra.styleref, so
    the instructions must live in a visible core Note node.
    """
    graph = _load(path)
    assert len(_note_text(graph)) > 60, "no substantial Note node found"
    # extra.styleref.notes carries provenance only.
    assert "build_workflows" in graph["extra"]["styleref"]["notes"]


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_titles_fit_without_truncating(path):
    for node in _load(path)["nodes"]:
        title = node.get("title")
        if title:
            assert len(title) <= MAX_TITLE, f"title truncates: {title!r}"


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_auth_requirements_are_stated(path):
    """A template that needs sign-in must say so, or it reads as broken."""
    graph = _load(path)
    notes = _note_text(graph).lower()
    # Own-style templates are the ones carrying a Login node — a ref doesn't
    # announce privacy (a private style is addressed like any other).
    needs_auth = any(n["type"] == "StyleRefLogin" for n in graph["nodes"])
    if needs_auth:
        assert "sign-in" in notes or "sign in" in notes
        # The sign-in path ships in the template, not just in prose.
        assert any(n["type"] == "StyleRefLogin" for n in graph["nodes"])


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_seeds_randomize(path):
    """
    A fixed seed makes the second queue a silent cache no-op —
    deadly in a demo.
    """
    for node in _load(path)["nodes"]:
        if node["type"] == "KSampler":
            assert node["widgets_values"][1] == "randomize"


# Every node type that names a downloadable model file in its first widget.
_MODEL_LOADERS = {"CheckpointLoaderSimple", "UNETLoader", "CLIPLoader", "VAELoader"}
_MODEL_DIRECTORIES = {"checkpoints", "diffusion_models", "text_encoders", "vae"}


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_model_loaders_carry_download_metadata(path):
    """
    A missing model should be a guided download, not a silently-substituted
    combo value — for every loader type (checkpoints and the Z-Image
    UNET/CLIP/VAE stack).

    The metadata must be on the *node*, under `properties.models`: that is
    where ComfyUI looks when deciding whether to offer the download. This
    assertion used to read the workflow-level `models` key instead, which is
    why every template shipped that key and none of them prompted.
    """
    graph = _load(path)
    loaders = [n for n in graph["nodes"] if n["type"] in _MODEL_LOADERS]
    if not loaders:
        return

    for node in loaders:
        declared = node["properties"].get("models")
        assert declared, f"{node['type']} carries no properties.models"
        # The name must match the widget, or the frontend cannot tell which
        # missing file this download would satisfy.
        assert {m["name"] for m in declared} == {node["widgets_values"][0]}
        for model in declared:
            assert model["url"].startswith("https://")
            assert model["directory"] in _MODEL_DIRECTORIES


def test_node_slot_tables_cover_every_linked_node_type():
    """Guards against a node type being linked using guessed slot layouts."""
    for path in FILES:
        for node in _load(path)["nodes"]:
            node_type = node["type"]
            if node["outputs"]:
                assert node_type in OUTPUTS, f"{node_type} outputs undeclared"
            if node["inputs"]:
                assert node_type in INPUTS, f"{node_type} inputs undeclared"


FACETS_FILES = ["05-facets.json", "z-image/05-facets.json"]


@pytest.mark.parametrize("name", FACETS_FILES)
def test_facets_template_previews_real_section_outputs(name):
    """
    The Facets templates exist to make the node's outputs legible, so each
    showcased section must reach a Preview Any — and at the slot the node
    actually emits it from, not a guessed index.
    """
    graph = _load(os.path.join(WORKFLOW_DIR, *name.split("/")))
    nodes = {n["id"]: n for n in graph["nodes"]}

    facets = [n for n in graph["nodes"] if n["type"] == "StyleRefFacets"]
    assert len(facets) == 1, "the Facets templates centre on exactly one Facets node"
    facet_id = facets[0]["id"]

    previewed = {
        StyleRefFacets.RETURN_NAMES[src_slot]
        for _lid, src, src_slot, dst, _ds, _k in graph["links"]
        if src == facet_id and nodes[dst]["type"] == "PreviewAny"
    }
    assert previewed == set(FACET_SHOWCASE)


@pytest.mark.parametrize("name", FACETS_FILES)
def test_facets_template_still_renders_an_image(name):
    """Inspecting the parts is the story, but a template that produces no image
    reads as broken — Apply and a sampler stay on the canvas."""
    types = {n["type"] for n in _load(os.path.join(WORKFLOW_DIR, *name.split("/")))["nodes"]}
    assert {"StyleRefApply", "KSampler", "SaveImage"} <= types


ZIMAGE_FILES = [f for f in (_relpath(p) for p in FILES) if f.startswith("z-image/")]


@pytest.mark.parametrize("name", ZIMAGE_FILES)
def test_zimage_templates_have_no_negative_encoder(name):
    """
    Z-Image runs without classifier-free guidance — the model takes no negative
    prompt. A second CLIPTextEncode wired into the sampler's negative would be
    dead weight that reads as working, so the negative is a zeroed conditioning
    and every text encoder on the canvas is driven by Apply's *positive*.
    """
    graph = _load(os.path.join(WORKFLOW_DIR, *name.split("/")))
    nodes = {n["id"]: n for n in graph["nodes"]}
    types = [n["type"] for n in graph["nodes"]]

    samplers = [n for n in graph["nodes"] if n["type"] == "KSampler"]
    assert samplers, name
    assert types.count("ConditioningZeroOut") == len(samplers)

    encoder_ids = {n["id"] for n in graph["nodes"] if n["type"] == "CLIPTextEncode"}
    apply_ids = {n["id"] for n in graph["nodes"] if n["type"] == "StyleRefApply"}
    for _lid, src, src_slot, dst, _ds, _k in graph["links"]:
        if src in apply_ids and dst in encoder_ids:
            assert src_slot == 0, (
                "an encoder is fed by Apply's negative output — Z-Image has no negative channel"
            )

    # Every sampler's negative input is fed by a zero-out, never an encoder.
    zero_ids = {n["id"] for n in graph["nodes"] if n["type"] == "ConditioningZeroOut"}
    negatives = [
        src
        for _lid, src, _ss, dst, dst_slot, _k in graph["links"]
        if nodes[dst]["type"] == "KSampler" and dst_slot == 2
    ]
    assert negatives, name
    assert set(negatives) <= zero_ids


def test_consistency_template_shows_one_style_many_subjects():
    """Three Apply nodes with three different subjects, one Load."""
    graph = _load(os.path.join(WORKFLOW_DIR, "02-consistency-grid.json"))
    applies = [n for n in graph["nodes"] if n["type"] == "StyleRefApply"]
    loads = [n for n in graph["nodes"] if n["type"] == "StyleRefLoad"]
    assert len(loads) == 1
    assert len(applies) == 3
    subjects = {n["widgets_values"][0] for n in applies}
    assert len(subjects) == 3, "the subjects must actually differ"


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_no_template_previews_the_compiled_prompts(path):
    """
    Apply prints the composed prompt and its token estimate in its own node body,
    so a Preview Any on `positive`/`negative` is a second copy of something
    already on screen — and in the consistency grid it was six of them. The only
    Preview Any nodes left are the Facets sections, whose strings have nowhere
    else to be read.
    """
    graph = _load(path)
    apply_ids = {n["id"] for n in graph["nodes"] if n["type"] == "StyleRefApply"}
    nodes = {n["id"]: n for n in graph["nodes"]}
    for _lid, src, _ss, dst, _ds, _k in graph["links"]:
        assert not (src in apply_ids and nodes[dst]["type"] == "PreviewAny"), (
            "Apply's compiled prompt is already visible in the node body"
        )


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_notes_stay_short(path):
    """
    A note is a signpost, not a manual: what this template produces, and how to
    get the model. Longer guidance belongs in the README, where it does not
    dominate the canvas.
    """
    for node in _load(path)["nodes"]:
        if node["type"] == "Note":
            text = str(node["widgets_values"][0])
            assert len(text) <= MAX_NOTE, f"note is {len(text)} chars: {text[:80]}…"


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_plumbing_nodes_ship_collapsed(path):
    """
    The text encoder, the zero-out and VAE Decode carry nothing a user edits.
    Shipped expanded they are tall empty boxes between Apply and the sampler.
    """
    for node in _load(path)["nodes"]:
        if node["type"] in COLLAPSED:
            assert node["flags"].get("collapsed") is True, node["type"]


@pytest.mark.parametrize("path", FILES, ids=[_relpath(p) for p in FILES])
def test_declared_heights_are_never_below_the_measured_minimum(path):
    """
    The frontend clamps a node up to its own minimum on load. A template that
    declares less does not get a smaller node — it gets a node that grows on open
    and covers whatever sits below it, which is exactly how the loaders came to
    overlap the Load node.
    """
    for node in _load(path)["nodes"]:
        minimum = MIN_H.get(node["type"])
        assert minimum, f"{node['type']} has no measured height in MIN_H"
        assert node["size"][1] >= minimum, (
            f"{node['type']} declares {node['size'][1]}px, under its {minimum}px minimum"
        )


def test_consistency_template_makes_no_cross_tool_claim():
    """
    Brand rule: consistency claims are per generation within one tool. A template
    note promising matching output across tools, or a before/after, is a claim we
    cannot stand behind.
    """
    notes = _note_text(_load(os.path.join(WORKFLOW_DIR, "02-consistency-grid.json"))).lower()
    for forbidden in ("before/after", "before and after", "same across", "identical across"):
        assert forbidden not in notes
