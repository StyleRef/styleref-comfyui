"""
StyleRef Reference Images — the style's inspiration images as an IMAGE batch.

Wire `style` from StyleRef Load, and every inspiration image stored with the
style comes out as one IMAGE output — straight into IPAdapter, ControlNet
reference, or a Preview Image node. This composes with ComfyUI's
image-conditioning ecosystem instead of competing with it: the prompt from
Apply says what the style *reads* like, the images here show what it *looks*
like.

The images are deliberately NOT in Apply's positive prompt — a sampler cannot
fetch a URL out of prompt text, so there they would only be junk tokens.

PIL, numpy and torch are imported lazily inside the function: every ComfyUI
install ships all three, but the pack's import (and the rest of the nodes)
must not depend on them.
"""

from __future__ import annotations

import io
from typing import Any

import styleref_api as api
from styleref_api import StyleRefError
from styleref_style import inspiration_image_urls, is_style

CATEGORY = "StyleRef"

# Downloaded image bytes for the session, keyed by URL — inspiration images are
# immutable uploads, so there is no staleness to manage.
_IMAGE_CACHE: dict[str, bytes] = {}

MAX_IMAGES = 12


def _download(url: str) -> bytes:
    if url in _IMAGE_CACHE:
        return _IMAGE_CACHE[url]
    # anonymous=True: these are public storage URLs, and a bearer token must
    # never be sent to a host we don't control.
    raw, _headers = api.request_raw(url, anonymous=True, timeout=60)
    _IMAGE_CACHE[url] = raw
    return raw


class StyleRefReferenceImages:
    """Load a style's inspiration images as a ComfyUI IMAGE batch."""

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "style": ("STYLEREF_STYLE", {"tooltip": "From StyleRef Load."}),
            },
            "optional": {
                "max_images": (
                    "INT",
                    {
                        "default": MAX_IMAGES,
                        "min": 1,
                        "max": MAX_IMAGES,
                        "step": 1,
                        "tooltip": "Load at most this many inspiration images.",
                    },
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "INT")
    RETURN_NAMES = ("images", "count")
    OUTPUT_TOOLTIPS = (
        "All inspiration images as one batch (resized to the first image's size) "
        "— wire into IPAdapter, a reference input, or Preview Image.",
        "How many images were loaded.",
    )
    FUNCTION = "fetch"
    CATEGORY = CATEGORY
    DESCRIPTION = "The style's inspiration images as an IMAGE batch, for image conditioning."

    def fetch(self, style: dict[str, Any], max_images: int = MAX_IMAGES):
        if not is_style(style):
            raise StyleRefError(
                "The `style` input is not a StyleRef style — connect StyleRef Load."
            )

        urls = inspiration_image_urls(style)[: max(1, max_images)]
        if not urls:
            raise StyleRefError(
                f"“{style.get('name') or style.get('ref')}” has no inspiration images. "
                "Add some on styleref.io, or disconnect this node."
            )

        # Lazy on purpose — see the module docstring.
        import numpy as np
        from PIL import Image

        frames = []
        size: tuple[int, int] | None = None
        for url in urls:
            try:
                image = Image.open(io.BytesIO(_download(url))).convert("RGB")
            except StyleRefError:
                raise
            except Exception as err:  # noqa: BLE001 - a bad file must name itself
                raise StyleRefError(f"Could not decode inspiration image {url}: {err}") from err

            if size is None:
                size = image.size
            elif image.size != size:
                # One batch = one resolution in ComfyUI. The first image sets
                # it; the rest are resized to match.
                image = image.resize(size, Image.LANCZOS)
            frames.append(np.asarray(image, dtype=np.float32) / 255.0)

        batch = np.stack(frames, axis=0)  # (B, H, W, 3) float32 in 0-1

        # ComfyUI's IMAGE type is a torch tensor — SaveImage/PreviewImage and
        # IPAdapter all call `.cpu()` on it, which a bare ndarray lacks. torch
        # ships with every ComfyUI install; the numpy fallback only matters to
        # the unit tests, whose dev extras are pillow + numpy only.
        try:
            import torch

            return (torch.from_numpy(batch), len(frames))
        except ImportError:
            return (batch, len(frames))
