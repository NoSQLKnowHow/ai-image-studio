"""Make 4K (DESIGN.md §27): a 16:9 picture, trimmed to exactly 16:9 and enlarged to 3840x2160 with a standard resize.

The model cannot make 3840x2160 itself (2160 is not a multiple of 32), so this is done to a picture it has made. The
rules of when it is offered are here, in one place; the page asks the server (`can_4k`) rather than repeating them.

    plan_4k(width, height)     which part of the picture is kept, or why it is not eligible (pure arithmetic)
    make_4k(src, dst)          the file: one resampling pass from the kept part to 3840x2160, written atomically
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .storage import has_alpha

TARGET_WIDTH, TARGET_HEIGHT = 3840, 2160
TOLERANCE_PERCENT = 2  # a picture up to 2% off 16:9 (inclusive) is trimmed to it; the model's own 2752x1536 is 0.8% off
MIN_TRIMMED_WIDTH = 1920  # at most a doubling: a bigger enlargement by resizing is a blurry picture, not a 4K one
PNG_LEVEL = 1  # 0.6 s against 2.6 s at the default level, for a file about 12% larger (measured, DESIGN.md §27.3)


class NotEligible(Exception):
    """The picture cannot be made 4K. The message says why, in words for the person using the page."""


@dataclass(frozen=True)
class Plan:
    """The part of the source that is kept, in source pixels. The box is fractional, so the picture is resampled once."""

    box: tuple[float, float, float, float]  # left, top, right, bottom
    width: float  # of the kept part
    height: float


def plan_4k(width: int, height: int) -> Plan:
    """What Make 4K would keep of a width x height picture, or NotEligible."""
    if width <= 0 or height <= 0:
        raise NotEligible("This picture has no size.")
    if abs(width * 9 - height * 16) * 100 > TOLERANCE_PERCENT * height * 16:  # integers: exact, and 2.000% is allowed
        raise NotEligible(f"Make 4K is for 16:9 pictures; this one is {width}×{height}.")
    # Integer comparisons and exact arithmetic, so an exactly-16:9 picture keeps all of itself and the box never sticks out
    # of the picture (Pillow refuses a box larger than the picture): in the tall branch every figure is a multiple of 1/16,
    # which floats hold exactly; in the wide branch the box ends at least 1/9 of a pixel inside, far more than a rounding error.
    if width * 9 > height * 16:  # too wide: cut equally from the left and the right
        kept_w, kept_h = height * 16 / 9, float(height)
        left, top = (width - kept_w) / 2, 0.0
    else:  # too tall (or exact): cut equally from the top and the bottom
        kept_w, kept_h = float(width), width * 9 / 16
        left, top = 0.0, (height - kept_h) / 2
    if kept_w < MIN_TRIMMED_WIDTH:
        raise NotEligible(
            f"This picture is too small to enlarge to 4K without it looking blurry: once trimmed to 16:9 it must be "
            f"at least {MIN_TRIMMED_WIDTH} pixels wide, and this one is {int(kept_w)}.")
    if kept_w >= TARGET_WIDTH:
        raise NotEligible("This picture is already 4K or bigger.")
    return Plan((left, top, left + kept_w, top + kept_h), kept_w, kept_h)


def can_4k(width: int, height: int) -> bool:
    try:
        plan_4k(width, height)
    except NotEligible:
        return False
    return True


def make_4k(src: Path, dst: Path) -> Path:
    """Write the 4K copy of `src` at `dst`: 3840x2160, trimmed and resampled in one pass (Lanczos), transparency kept, the
    original's PNG text kept with one line added. Written to a temporary file in the same folder and renamed, so a
    half-written file is never seen. Raises NotEligible (judged on the file itself), FileNotFoundError, or OSError."""
    with Image.open(src) as opened:
        opened.load()
        plan = plan_4k(opened.width, opened.height)
        text = {str(key): str(value) for key, value in getattr(opened, "text", {}).items()}
        source_size = opened.size
        picture = opened.convert("RGBA" if has_alpha(opened) else "RGB")
    result = picture.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS, box=plan.box)
    info = PngInfo()
    for key, value in text.items():
        info.add_text(key, value)
    info.add_text("make4k", f"Trimmed to 16:9 and enlarged with a Lanczos resize from {source_size[0]}x{source_size[1]}; "
                            "no detail was added.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    handle, partial_name = tempfile.mkstemp(dir=dst.parent, prefix=dst.name + ".", suffix=".part")
    partial = Path(partial_name)
    try:
        with os.fdopen(handle, "wb") as out:
            result.save(out, format="PNG", pnginfo=info, compress_level=PNG_LEVEL)
        os.replace(partial, dst)
    finally:
        partial.unlink(missing_ok=True)
    return dst
