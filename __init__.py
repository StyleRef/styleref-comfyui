"""
StyleRef nodes for ComfyUI — https://styleref.io

Define a creative style once on StyleRef, then load it as a node and reuse it
across your workflows.

MIT licensed. Issues: https://github.com/StyleRef/styleref-comfyui
"""

from __future__ import annotations

import os
import re
import sys

# The node modules import `styleref_api` etc. as top-level names. ComfyUI adds
# custom_nodes/<pack>/ to sys.path only in some layouts, so make it explicit
# rather than depending on how this pack was installed.
_PACK_DIR = os.path.dirname(os.path.abspath(__file__))
if _PACK_DIR not in sys.path:
    sys.path.insert(0, _PACK_DIR)

from styleref_nodes.apply import StyleRefApply  # noqa: E402
from styleref_nodes.facets import StyleRefFacets  # noqa: E402
from styleref_nodes.load import StyleRefLoad  # noqa: E402
from styleref_nodes.login import StyleRefLogin  # noqa: E402
from styleref_nodes.reference_images import StyleRefReferenceImages  # noqa: E402

NODE_CLASS_MAPPINGS = {
    "StyleRefLoad": StyleRefLoad,
    "StyleRefApply": StyleRefApply,
    "StyleRefFacets": StyleRefFacets,
    "StyleRefReferenceImages": StyleRefReferenceImages,
    "StyleRefLogin": StyleRefLogin,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StyleRefLoad": "StyleRef Load",
    "StyleRefApply": "StyleRef Apply",
    "StyleRefFacets": "StyleRef Facets",
    "StyleRefReferenceImages": "StyleRef Reference Images",
    "StyleRefLogin": "StyleRef Login",
}

# Served by ComfyUI at /extensions/styleref-comfyui/ — the search widget's
# progressive enhancement. Nothing here is required for the nodes to work.
WEB_DIRECTORY = "./web"

_FALLBACK_VERSION = "1.0.4"


def _pack_version() -> str:
    """Single-source the version from pyproject.toml, with a constant fallback."""
    try:
        with open(os.path.join(_PACK_DIR, "pyproject.toml"), encoding="utf-8") as fh:
            match = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.MULTILINE)
        return match.group(1) if match else _FALLBACK_VERSION
    except OSError:
        return _FALLBACK_VERSION


# The install docs tell users to look for a `[StyleRef]` line at startup, so a
# healthy import must print one — silence would read as a failed install.
print(f"[StyleRef] v{_pack_version()} — {len(NODE_CLASS_MAPPINGS)} nodes registered")

# Registering the search route is optional: without it the enhanced widget
# simply doesn't populate, and the vanilla `search` field still works.
try:
    import server_routes  # noqa: F401
except Exception as err:  # noqa: BLE001 - never block node registration
    print(f"[StyleRef] Search route unavailable ({err}); using the plain search field.")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
