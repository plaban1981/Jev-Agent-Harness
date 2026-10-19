"""Multimodal decisions: validate/resize a listing photo, then ask Jev.

Jev image limits (typesafe/jev-1.13): each side 256-1568 px, total pixels
78400-2458624, png/jpeg only.  ``prepare_image`` enforces all of these with
PIL (the offline pipeline resizes for real — end to end).  The photo is then
base64-encoded into the decision state and Jev is asked one noul ("the photo
shows the item in the title") and one choice (color: red/green/blue).
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from .config import (
    IMG_FORMATS,
    IMG_MAX_PIXELS,
    IMG_MAX_SIDE,
    IMG_MIN_PIXELS,
    IMG_MIN_SIDE,
)
from .events import HOOK_MULTIMODAL, EventSink, GateEvent, get_sink
from .jev import FakeJevClient, JevClient, choice, noul
from .metrics import SavingsTracker

MIME = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg"}


class ImageValidationError(ValueError):
    pass


def validate_and_resize(path: str) -> Tuple[Image.Image, str]:
    """Load, format-check and resize an image to Jev's limits.

    Returns (image, format) where format is 'png' or 'jpeg'.
    Raises ImageValidationError for unsupported formats.
    """
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext not in IMG_FORMATS:
        raise ImageValidationError(
            f"unsupported image format '.{ext}' — Jev accepts png/jpeg only"
        )
    img = Image.open(path)
    fmt = "jpeg" if ext in ("jpeg", "jpg") else "png"
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    w, h = img.size
    scale = 1.0
    # Fit each side into [MIN_SIDE, MAX_SIDE].
    if max(w, h) > IMG_MAX_SIDE:
        scale = IMG_MAX_SIDE / max(w, h)
    if min(w, h) < IMG_MIN_SIDE and scale == 1.0:
        scale = IMG_MIN_SIDE / min(w, h)
    # Fit total pixels into [MIN_PIXELS, MAX_PIXELS].
    pixels = w * h
    if pixels * scale * scale > IMG_MAX_PIXELS:
        scale = (IMG_MAX_PIXELS / pixels) ** 0.5
    if pixels * scale * scale < IMG_MIN_PIXELS and scale >= 1.0:
        scale = (IMG_MIN_PIXELS / pixels) ** 0.5

    if scale != 1.0:
        new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
        img = img.resize(new_size, Image.LANCZOS)

    # Final clamp pass — guarantees contract even after rounding.
    w, h = img.size
    if not (IMG_MIN_SIDE <= w <= IMG_MAX_SIDE and IMG_MIN_SIDE <= h <= IMG_MAX_SIDE):
        raise ImageValidationError(
            f"could not resize image into Jev limits: {w}x{h}"
        )
    if not (IMG_MIN_PIXELS <= w * h <= IMG_MAX_PIXELS):
        raise ImageValidationError(
            f"pixel count {w * h} outside Jev limits "
            f"[{IMG_MIN_PIXELS}, {IMG_MAX_PIXELS}]"
        )
    return img, fmt


def encode_image(img: Image.Image, fmt: str) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG" if fmt == "png" else "JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:{MIME[fmt]};base64,{b64}"


@dataclass
class ListingCheck:
    shows_item: float                      # noul probability
    color: str                             # chosen color
    color_probabilities: Dict[str, float] = field(default_factory=dict)
    resized_to: Optional[Tuple[int, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shows_item": self.shows_item,
            "color": self.color,
            "color_probabilities": self.color_probabilities,
            "resized_to": list(self.resized_to) if self.resized_to else None,
        }


def check_listing_photo(
    image_path: str,
    listing_title: str,
    jev: JevClient | FakeJevClient,
    event_sink: Optional[EventSink] = None,
    tracker: Optional[SavingsTracker] = None,
) -> ListingCheck:
    """Validate/resize the photo, then ask Jev noul + choice questions."""
    sink = get_sink(event_sink)
    original = Image.open(image_path)
    original_size = original.size
    img, fmt = validate_and_resize(image_path)
    resized_to = img.size if img.size != original_size else None

    state = {
        "listing_title": listing_title,
        "image": encode_image(img, fmt),
    }
    questions = {
        "shows_item": noul(
            "The photo shows the item described in the listing title."
        ),
        "color": choice(
            "What is the dominant color of the item in the photo?",
            ["red", "green", "blue"],
        ),
    }
    result_call = jev.ask_with_usage(state, questions)
    rec = (
        tracker.record_jev_call(HOOK_MULTIMODAL, questions, result_call.usage,
                                result_call.latency_ms,
                                state_obj={"listing_title": listing_title})
        if tracker is not None else None
    )
    answers = result_call.answers

    result = ListingCheck(
        shows_item=answers["shows_item"].noul,
        color=answers["color"].choice_value,
        color_probabilities=answers["color"].probabilities,
        resized_to=resized_to,
    )
    sink.emit(
        GateEvent(
            hook=HOOK_MULTIMODAL,
            stage="listing_photo_check",
            decision="MATCH" if result.shows_item >= 0.8 else "MISMATCH",
            detail={
                "listing_title": listing_title,
                "shows_item": result.shows_item,
                "color": result.color,
                "color_probabilities": result.color_probabilities,
                "resized_to": list(resized_to) if resized_to else None,
            },
            **(rec.event_kwargs() if rec else {}),
        )
    )
    return result
