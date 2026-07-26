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

from scripts.build_workflows import FACET_SHOWCASE, INPUTS, MAX_TITLE, OUTPUTS, WORKFLOWS
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


def test_zimage_set_ships_its_five_workflows():
    """The Z-Image folder carries the root's stories plus its own two demos."""
    zimage = {_relpath(p) for p in FILES if _relpath(p).startswith("z-image/")}
    assert zimage == {
        "z-image/01-quickstart.json",
        "z-image/02-consistency-grid.json",
        "z-image/03-your-own-style.json",
        "z-image/04-reference-images.json",
        "z-image/05-facets.json",
    }


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
    """Overlapping nodes read as a broken template — layout is math,
    not guesses, so this must hold for every template."""
    nodes = _load(path)["nodes"]
    boxes = [(n["id"], *n["pos"], *n["size"]) for n in nodes]
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
    """A missing model should be a guided download, not a red error —
    for every loader type (checkpoints and the Z-Image UNET/CLIP/VAE stack)."""
    graph = _load(path)
    loader_files = {
        n["widgets_values"][0] for n in graph["nodes"] if n["type"] in _MODEL_LOADERS
    }
    if not loader_files:
        return
    declared = {m["name"] for m in graph.get("models", [])}
    assert loader_files <= declared, f"models metadata missing: {loader_files - declared}"
    for model in graph.get("models", []):
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


FACETS_FILES = ["04-facets.json", "z-image/05-facets.json"]


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


def test_prompt_previews_exist_where_promised():
    """Templates 01 and 04 make the compiled prompts visible."""
    for name in ("01-quickstart.json", "04-facets.json"):
        graph = _load(os.path.join(WORKFLOW_DIR, name))
        assert any(n["type"] == "PreviewAny" for n in graph["nodes"]), name


def test_consistency_template_makes_no_cross_tool_claim():
    """
    Brand rule: consistency claims are per generation within one tool. A template
    note promising matching output across tools, or a before/after, is a claim we
    cannot stand behind.
    """
    notes = _note_text(_load(os.path.join(WORKFLOW_DIR, "02-consistency-grid.json"))).lower()
    for forbidden in ("before/after", "before and after", "same across", "identical across"):
        assert forbidden not in notes
