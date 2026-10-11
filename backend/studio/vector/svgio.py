"""Writing and reading the SVG (DESIGN.md §31.4, §31.12.3 item 5).

The writer produces the file in the style of `scripts/laser_test_sheet.py`, which is the file the owner is testing in xTool Creative
Space and LightBurn: real millimetres, one `<g>` per layer with an id, a name and the same name as an Inkscape label, closed paths made of
M, L and Z only, each hole a sub-path wound the other way round. The reader understands exactly that subset (and the cubic curves the
test sheet has), so that the PNG is drawn from the file itself and not from the engine's objects (§31.4: "sharp at 4K from the vector").

Plain Python with no third-party import: shapely polygons are only read through `.exterior` and `.interiors`, so the PNG route does not
need shapely installed.
"""

from __future__ import annotations

import html
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from . import VectorError
from .pathdata import parse_path

INKSCAPE = "http://www.inkscape.org/namespaces/inkscape"
OUTLINE_WIDTH_MM = 0.1  # the test sheet's line width for outlines; `fill_mode="outline"` draws every shape as a line this wide
COORDINATE_DIGITS = 3  # a thousandth of a millimetre: far finer than any laser, and it keeps the file small and the numbers short


def fmt(value: float) -> str:
    """A number as short as it can be written: at most three decimals, no trailing zeros, no "-0"."""
    text = f"{value:.{COORDINATE_DIGITS}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True)
class PathShape:
    """One shape ready to be written: its path data, and the numbers the check reports about it."""

    d: str
    length_mm: float  # the cut length: the outline and every hole
    holes: int


@dataclass(frozen=True)
class SvgLayer:
    """One layer as the writer needs it."""

    number: int
    id: str
    name: str
    colour: str
    shapes: tuple[PathShape, ...]


def _ring_points(ring) -> list[tuple[float, float]]:
    """A ring's points rounded to the file's precision, without the repeated closing point and without points that rounding made equal."""
    points: list[tuple[float, float]] = []
    for x, y in list(ring.coords)[:-1]:
        point = (round(x, COORDINATE_DIGITS), round(y, COORDINATE_DIGITS))
        if not points or point != points[-1]:
            points.append(point)
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _area(points: list[tuple[float, float]]) -> float:
    """The shoelace area: zero for a ring that rounding flattened into a line."""
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])) / 2


def path_shape(polygon) -> Optional[PathShape]:
    """A shapely polygon as path data (the outline first, then each hole), or None when rounding left nothing of it. A ring too small to
    survive rounding is dropped; if that is the outline, the shape is."""
    outline = _ring_points(polygon.exterior)
    if len(outline) < 3 or _area(outline) == 0:
        return None
    rings = [outline]
    for hole in polygon.interiors:
        points = _ring_points(hole)
        if len(points) >= 3 and _area(points) != 0:
            rings.append(points)
    data = " ".join("M" + " L".join(f"{fmt(x)} {fmt(y)}" for x, y in points) + " Z" for points in rings)
    length = sum(math.dist(a, b) for points in rings for a, b in zip(points, points[1:] + points[:1]))
    return PathShape(d=data, length_mm=length, holes=len(rings) - 1)


def build_svg(width_mm: float, height_mm: float, layers: list[SvgLayer], fill_mode: str, title: str, description: str) -> str:
    """The SVG text for `layers`, drawn in order (the first is the bottom). One `<g>` per layer, one `<path>` per shape."""
    w, h = fmt(width_mm), fmt(height_mm)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="{INKSCAPE}" version="1.1" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">',
        f"  <title>{html.escape(title, quote=False)}</title>",
        f"  <desc>{html.escape(description, quote=False)}</desc>",
    ]
    for layer in layers:
        parts.append(f'  <g id="{html.escape(layer.id)}" inkscape:groupmode="layer" inkscape:label="{html.escape(layer.name)}">')
        for number, shape in enumerate(layer.shapes, start=1):
            ident = f"layer{layer.number}-path{number}"
            if fill_mode == "outline":  # a line and no area: the other way a laser program may want a shape (§31.10 item 8)
                style = f'fill="none" stroke="{layer.colour}" stroke-width="{fmt(OUTLINE_WIDTH_MM)}"'
            else:  # the default: an area and no line; a shape with holes also says even-odd, as the test sheet's ring does
                style = f'fill="{layer.colour}"' + (' fill-rule="evenodd"' if shape.holes else "") + ' stroke="none"'
            parts.append(f'    <path id="{ident}" d="{shape.d}" {style}/>')
        parts.append("  </g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# ---- reading it back (for the PNG) ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ParsedPath:
    """One path read back: its rings (outline and holes), how it is painted."""

    rings: tuple[tuple[tuple[float, float], ...], ...]
    fill: Optional[str]  # #RRGGBB, or None for "none"
    stroke: Optional[str]
    stroke_width: float


@dataclass(frozen=True)
class ParsedSvg:
    """An SVG read back: the size of its drawing area (the viewBox, which is in millimetres) and its paths in painting order."""

    width: float
    height: float
    paths: tuple[ParsedPath, ...]


def _paint(value: Optional[str]) -> Optional[str]:
    """A fill or stroke attribute as a colour or None. Only `#RRGGBB` and `none` are part of what the engine writes."""
    if value is None or value == "none":
        return None
    if len(value) != 7 or value[0] != "#":
        raise VectorError("The SVG has a colour this renderer does not draw.", "Only #RRGGBB colours are used.")
    return value


def parse_svg(svg: str) -> ParsedSvg:
    """Read the engine's own SVG (and the test sheet's): the viewBox and every path in document order."""
    try:
        root = ET.fromstring(svg.encode("utf-8"))
        box = [float(v) for v in root.get("viewBox", "").replace(",", " ").split()]
        if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
            raise ValueError("no usable viewBox")
        paths = []
        for element in root.iter():
            if element.tag.rpartition("}")[2] != "path":
                continue
            rings = tuple(tuple(ring) for ring in parse_path(element.get("d", ""), 0.01))
            width = element.get("stroke-width")
            paths.append(ParsedPath(rings=rings, fill=_paint(element.get("fill", "#000000")), stroke=_paint(element.get("stroke")),
                                    stroke_width=float(width) if width else 1.0))
    except VectorError:
        raise
    except (ET.ParseError, ValueError, UnicodeError) as exc:
        raise VectorError("The SVG could not be drawn.", "It is not a file this studio wrote.") from exc
    return ParsedSvg(width=box[2], height=box[3], paths=tuple(paths))
