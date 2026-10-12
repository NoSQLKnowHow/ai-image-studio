"""The PNG, drawn from the SVG's own shapes (DESIGN.md §31.4, §31.12.3, criterion 141): sharp at any size because it is drawn from the
vector, never scaled up from the model's picture. Pillow only: no Cairo, no cairosvg (a native library is exactly what the Spark's image
may not have).

How: every path is drawn into a mask several times larger than the result (supersampling) and averaged down, which gives smooth edges.
A filled path is drawn ring by ring with exclusive-or, so that a ring inside a ring is a hole whatever the winding (the even-odd rule: the
engine writes holes wound the other way round, so the non-zero rule gives the same picture). Paths are painted in file order, the first
at the bottom, as an SVG program would.
"""

from __future__ import annotations

import io
import math

from PIL import Image, ImageChops, ImageDraw

from . import VectorError
from .colours import hex_to_rgb
from .svgio import ParsedPath, parse_svg

LONG_SIDE_MIN, LONG_SIDE_MAX = 16, 8192  # the range the API accepts (DESIGN.md §31.12.7)
SUPERSAMPLE = 4  # 4 x 4 samples per pixel: 16 grey levels along an edge, which is smooth to the eye
# A mask is drawn at SUPERSAMPLE times the size, so the biggest ones are limited (a whole 8192 px sheet would otherwise need a 32768 px mask).
# 40 million samples is about 5 MB as a yes/no image; beyond it the supersampling is reduced, down to none, which at that size nobody can see.
MAX_SAMPLES = 40_000_000
PAPER = (255, 255, 255)  # white is the bare material, so the picture is drawn on it


def _samples(width: int, height: int) -> int:
    """The supersampling that fits a mask of `width` x `height` pixels into `MAX_SAMPLES`."""
    return max(1, min(SUPERSAMPLE, math.isqrt(MAX_SAMPLES // max(1, width * height))))


def _bounds(points: list[tuple[float, float]], margin: float, size: tuple[int, int]):
    """The pixel box (x0, y0, x1, y1) that holds the points plus a margin, clipped to the picture, or None when it is outside."""
    x0, y0 = max(0, math.floor(min(p[0] for p in points) - margin)), max(0, math.floor(min(p[1] for p in points) - margin))
    x1, y1 = min(size[0], math.ceil(max(p[0] for p in points) + margin)), min(size[1], math.ceil(max(p[1] for p in points) + margin))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


# Pillow rounds every vertex to a whole sample and fills both ends of a span, so a filled shape comes out up to one sample (a quarter of a pixel)
# wider than it is, about half a percent of a 10 mm square at 1000 px and less for bigger shapes. That is the price of using Pillow's polygon
# fill (a correction would need an inward offset of the shape, which needs a geometry library the PNG route does not otherwise have); the test
# pins the size of the error.
def _fill_mask(path: ParsedPath, scale: float, size: tuple[int, int]):
    """The coverage mask (0 to 255) of a filled path and where it goes, or None. Even-odd: each ring flips the inside and the outside."""
    rings = [[(x * scale, y * scale) for x, y in ring] for ring in path.rings]  # (parse_path gives rings of three points or more)
    if not rings:
        return None
    box = _bounds([p for ring in rings for p in ring], 0, size)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    ss = _samples(x1 - x0, y1 - y0)
    mask = None
    for ring in rings:
        layer = Image.new("1", ((x1 - x0) * ss, (y1 - y0) * ss), 0)
        ImageDraw.Draw(layer).polygon([((x - x0) * ss, (y - y0) * ss) for x, y in ring], fill=1)
        mask = layer if mask is None else ImageChops.logical_xor(mask, layer)  # a ring inside a ring is a hole
    coverage = mask.convert("L")
    return ((coverage.reduce(ss) if ss > 1 else coverage), (x0, y0))


def _stroke_mask(path: ParsedPath, scale: float, size: tuple[int, int]):
    """The coverage mask of a path drawn as closed lines `stroke_width` wide (the `outline` fill mode), and where it goes, or None."""
    width = max(1.0, path.stroke_width * scale)
    rings = [[(x * scale, y * scale) for x, y in ring] for ring in path.rings]
    if not rings:
        return None
    box = _bounds([p for ring in rings for p in ring], width, size)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    ss = _samples(x1 - x0, y1 - y0)
    mask = Image.new("L", ((x1 - x0) * ss, (y1 - y0) * ss), 0)
    draw = ImageDraw.Draw(mask)
    for ring in rings:
        points = [((x - x0) * ss, (y - y0) * ss) for x, y in ring]
        draw.line(points + [points[0]], fill=255, width=max(1, round(width * ss)), joint="curve")
    return ((mask.reduce(ss) if ss > 1 else mask), (x0, y0))


def render_svg(svg: str, long_side_px: int) -> bytes:
    """The PNG of `svg` with its long side `long_side_px` pixels (16 to 8192), white where nothing is drawn."""
    if not isinstance(long_side_px, int) or not LONG_SIDE_MIN <= long_side_px <= LONG_SIDE_MAX:  # (True is 1, outside the range too)
        raise VectorError(f"The PNG size must be a whole number of pixels from {LONG_SIDE_MIN} to {LONG_SIDE_MAX}.")
    parsed = parse_svg(svg)
    scale = long_side_px / max(parsed.width, parsed.height)
    size = (max(1, round(parsed.width * scale)), max(1, round(parsed.height * scale)))
    canvas = Image.new("RGB", size, PAPER)
    for path in parsed.paths:
        for colour, make in ((path.fill, _fill_mask), (path.stroke, _stroke_mask)):
            if colour is None:
                continue
            made = make(path, scale, size)
            if made is not None:
                mask, corner = made
                canvas.paste(hex_to_rgb(colour), corner, mask)
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()
