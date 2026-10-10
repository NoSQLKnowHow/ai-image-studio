"""Make 4K (DESIGN.md §27): a picture enlarged to cover the 4K frame with a standard resize.

The frame is 3840x2160 (2160x3840 for a portrait picture). A picture is scaled, keeping its shape, until it covers that frame;
one within 2% of 16:9 (or 9:16) is first trimmed to exactly that shape, so its copy is exactly the frame. The model cannot make
3840x2160 itself (2160 is not a multiple of 32), so this is done to a picture it has made, or to one the person brings. The rules
of when it is offered are here, in one place; the page asks the server (`can_4k`) rather than repeating them.

    plan_4k(width, height)     what is kept of a picture and the size of its copy, or why it is not eligible (pure arithmetic)
    render_4k(picture)         the copy of a decoded picture: one resampling pass, plus the PNG text it carries
    make_4k(src, dst)          the same for a file, written atomically
    encode_png(...)            a PNG as bytes, for a picture that is not kept anywhere
    file_size(path)            the size of a PNG, from its header
"""

from __future__ import annotations

import io
import math
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .storage import has_alpha

FRAME_LONG, FRAME_SHORT = 3840, 2160
TOLERANCE_PERCENT = 2  # a picture up to 2% off 16:9 (inclusive) is trimmed to it; the model's own 2752x1536 is 0.8% off
MAX_ENLARGEMENT = 2  # a bigger enlargement by resizing is a blurry picture, not a 4K one
MAX_OUTPUT_PIXELS = 20_000_000  # a guard for odd shapes and for pictures from outside; a square copy is 14.7 MP
PNG_LEVEL = 1  # 0.6 s against 2.6 s at the default level, for a file about 12% larger (measured, DESIGN.md §27.3)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class NotEligible(Exception):
    """The picture cannot be made 4K. The message says why, in words for the person using the page."""


@dataclass(frozen=True)
class Plan:
    """What Make 4K does to a picture of a given size. The box is fractional, so the picture is resampled once."""

    box: tuple[float, float, float, float]  # the part of the picture that is kept: left, top, right, bottom
    width: float  # of the kept part
    height: float
    out_width: int  # the size of the copy
    out_height: int
    trimmed: bool  # a 16:9 (or 9:16) picture was cut to exactly that shape, so the copy is exactly the frame


def plan_4k(width: int, height: int) -> Plan:
    """What Make 4K would do to a width x height picture, or NotEligible."""
    if width <= 0 or height <= 0:
        raise NotEligible("This picture has no size.")
    portrait = height > width
    long, short = (height, width) if portrait else (width, height)  # the plan is made for a landscape picture, then turned back

    if abs(long * 9 - short * 16) * 100 <= TOLERANCE_PERCENT * short * 16:  # integers: exact, and 2.000% is allowed
        # Trim to exactly 16:9. Integer comparisons and exact arithmetic, so an exactly-16:9 picture keeps all of itself and
        # the box never sticks out of the picture (Pillow refuses a box larger than the picture): when the short side is
        # cut every figure is a multiple of 1/16, which floats hold exactly; when the long side is cut the box ends at
        # least 1/9 of a pixel inside, far more than a rounding error.
        if long * 9 > short * 16:  # too long: cut the long side equally from both ends
            kept_long, kept_short = short * 16 / 9, float(short)
            cut_long, cut_short = (long - kept_long) / 2, 0.0
        else:  # too short (or exact): cut the short side equally from both ends
            kept_long, kept_short = float(long), long * 9 / 16
            cut_long, cut_short = 0.0, (short - kept_short) / 2
        if kept_long >= FRAME_LONG:
            raise NotEligible("This picture is already 4K or bigger.")
        _check_enlargement(width, height, FRAME_LONG / kept_long)
        out_long, out_short, trimmed = FRAME_LONG, FRAME_SHORT, (kept_long != long or kept_short != short)
    else:
        # Any other shape: scale to cover the frame, trimming nothing. One side comes out exactly the frame's.
        if long >= FRAME_LONG and short >= FRAME_SHORT:
            raise NotEligible("This picture is already 4K or bigger.")
        scale = max(FRAME_LONG / long, FRAME_SHORT / short)
        _check_enlargement(width, height, scale)
        kept_long, kept_short, cut_long, cut_short = float(long), float(short), 0.0, 0.0
        out_long, out_short, trimmed = round(long * scale), round(short * scale), False

    if out_long * out_short > MAX_OUTPUT_PIXELS:
        raise NotEligible(f"The 4K copy of this picture ({width}×{height}) would be {out_long * out_short / 1e6:.0f} megapixels; "
                          f"the limit is {MAX_OUTPUT_PIXELS // 1_000_000}.")
    if portrait:
        box = (cut_short, cut_long, cut_short + kept_short, cut_long + kept_long)
        return Plan(box, kept_short, kept_long, out_short, out_long, trimmed)
    box = (cut_long, cut_short, cut_long + kept_long, cut_short + kept_short)
    return Plan(box, kept_long, kept_short, out_long, out_short, trimmed)


def _check_enlargement(width: int, height: int, scale: float) -> None:
    if scale > MAX_ENLARGEMENT:
        needed = math.ceil(scale * 10) / 10  # rounded up, so a refusal never reads "2.0× needed, up to 2× allowed"
        raise NotEligible(
            f"This picture ({width}×{height}) is too small to enlarge to 4K without it looking blurry: it would need a "
            f"{needed:.1f}× enlargement, and Make 4K goes up to {MAX_ENLARGEMENT}×.")


def plan_or_none(width: int, height: int) -> Optional[Plan]:
    try:
        return plan_4k(width, height)
    except NotEligible:
        return None


def can_4k(width: int, height: int) -> bool:
    return plan_or_none(width, height) is not None


def render_4k(opened: Image.Image) -> tuple[Image.Image, dict[str, str], Plan]:
    """The 4K copy of a decoded picture: the resized picture (one Lanczos pass, transparency kept), the PNG text it carries (the
    original's, if it has any, with one line added saying how the copy was made), and the plan. Raises NotEligible."""
    plan = plan_4k(opened.width, opened.height)
    text = {str(key): str(value) for key, value in getattr(opened, "text", {}).items()}
    picture = opened.convert("RGBA" if has_alpha(opened) else "RGB")
    result = picture.resize((plan.out_width, plan.out_height), Image.Resampling.LANCZOS, box=plan.box)
    text["make4k"] = (f"{'Trimmed to 16:9 and enlarged' if plan.trimmed else 'Enlarged'} with a Lanczos resize from "
                      f"{opened.width}x{opened.height} to {plan.out_width}x{plan.out_height}; no detail was added.")
    return result, text, plan


def _png_info(text: dict[str, str]) -> PngInfo:
    info = PngInfo()
    for key, value in text.items():
        info.add_text(key, value)
    return info


def encode_png(picture: Image.Image, text: dict[str, str]) -> bytes:
    """The PNG as bytes (at the fast encoder level), for a copy that is sent and not kept."""
    out = io.BytesIO()
    picture.save(out, format="PNG", pnginfo=_png_info(text), compress_level=PNG_LEVEL)
    return out.getvalue()


def make_4k(src: Path, dst: Path) -> Path:
    """Write the 4K copy of the picture at `src` to `dst`. Written to a temporary file in the same folder and renamed, so a
    half-written file is never seen. Raises NotEligible (judged on the file itself), FileNotFoundError, or OSError."""
    with Image.open(src) as opened:
        opened.load()
        result, text, _ = render_4k(opened)
    dst.parent.mkdir(parents=True, exist_ok=True)
    handle, partial_name = tempfile.mkstemp(dir=dst.parent, prefix=dst.name + ".", suffix=".part")
    partial = Path(partial_name)
    try:
        with os.fdopen(handle, "wb") as out:
            result.save(out, format="PNG", pnginfo=_png_info(text), compress_level=PNG_LEVEL)
        os.replace(partial, dst)
    finally:
        partial.unlink(missing_ok=True)
    return dst


def file_size(path: Path) -> Optional[tuple[int, int]]:
    """The width and height of a PNG file, read from its header (the first 24 bytes), or None if it is not one."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height
