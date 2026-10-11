"""Shared helpers for the Vector engine's tests (test_vector_engine_*.py): constructed pictures, and a reader for the SVG the engine writes.

The reader is deliberately written here and not taken from the engine: the tests must parse the file the way a laser program would, with
their own code, so that a mistake in the engine's own geometry objects cannot hide a wrong file (the same rule as test_laser_test_sheet.py).
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from PIL import Image, ImageDraw
from shapely.geometry import Polygon

SVG = "{http://www.w3.org/2000/svg}"
INK = "{http://www.inkscape.org/namespaces/inkscape}"

# A scale that every constructed picture uses unless it says otherwise: 500 pixels for 50 mm is ten pixels to the millimetre, so the default
# smallest feature of 0.5 mm is five pixels, a size the clean-up can see clearly (and 1 mm is 10 pixels, which is easy to reason about).
PX_PER_MM = 10
SHEET_MM = 50
SIZE = SHEET_MM * PX_PER_MM


def picture(shapes, size=(SIZE, SIZE), background=(255, 255, 255), smooth=4) -> Image.Image:
    """A constructed picture: shapes are ("rect", (x0, y0, x1, y1), colour), ("ellipse", box, colour) or ("poly", [(x, y), ...], colour),
    in pixels, painted in order. They are drawn `smooth` times larger and averaged down, so that every edge has the one or two pixels of
    in-between colour a real picture has (`smooth=1` gives hard edges, for the tests that need pixel-exact areas)."""
    width, height = size
    image = Image.new("RGB", (width * smooth, height * smooth), background)
    draw = ImageDraw.Draw(image)
    for kind, geometry, colour in shapes:
        if kind == "rect":
            draw.rectangle([v * smooth for v in geometry], fill=colour)
        elif kind == "ellipse":
            draw.ellipse([v * smooth for v in geometry], fill=colour)
        else:
            draw.polygon([(x * smooth, y * smooth) for x, y in geometry], fill=colour)
    return image.resize(size, Image.Resampling.LANCZOS) if smooth > 1 else image


def as_png(image: Image.Image) -> bytes:
    """The picture as the bytes of a PNG file, which is what the server hands the job."""
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def mm(value: float) -> float:
    """Millimetres to pixels at the default scale."""
    return value * PX_PER_MM


# ---- reading the SVG -------------------------------------------------------------------------------------------------------------------
@dataclass
class ReadPath:
    """One <path> as a laser program sees it."""

    id: str
    d: str
    rings: list  # each a list of (x, y); the subpaths, outline first
    fill: str
    stroke: str
    stroke_width: str | None
    fill_rule: str | None


@dataclass
class ReadLayer:
    id: str
    label: str
    groupmode: str
    paths: list[ReadPath]

    def colours(self) -> set[str]:
        return {c for p in self.paths for c in (p.fill, p.stroke) if c != "none"}

    def polygon(self) -> Polygon:
        """The area the layer's paths enclose (even-odd): shapes with holes, all paths together."""
        total = Polygon()
        for path in self.paths:
            total = total.union(polygon_of(path.rings))
        return total


def polygon_of(rings) -> Polygon:
    """A path's area under the even-odd rule: each ring flips inside and outside."""
    area = Polygon()
    for ring in rings:
        area = area.symmetric_difference(Polygon(ring))
    return area


def parse_d(d: str):
    """The subpaths of a path's data. Only M, L and Z in capitals may be used by the engine (C is allowed by the contract but the engine does
    not write it), and every subpath must be closed: anything else fails here, as it would in a laser program."""
    assert re.fullmatch(r"[MLZ0-9. \-]+", d), f"a command the engine should not write: {d[:80]}"
    rings = []
    for chunk in d.split("M")[1:]:
        chunk = chunk.strip()
        assert chunk.endswith("Z"), f"an open subpath: {chunk[-40:]}"
        numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", chunk)]
        assert len(numbers) % 2 == 0 and len(numbers) >= 6, f"a subpath of fewer than three points: {chunk[:60]}"
        rings.append(list(zip(numbers[0::2], numbers[1::2])))
    return rings


def parse(svg: str):
    """(root element, layers) of an SVG written by the engine."""
    root = ET.fromstring(svg.encode("utf-8"))
    layers = []
    for group in root:
        if group.tag != SVG + "g":
            continue
        paths = []
        for element in group:
            assert element.tag == SVG + "path", f"only paths in a layer, found {element.tag}"
            paths.append(ReadPath(id=element.get("id"), d=element.get("d"), rings=parse_d(element.get("d")), fill=element.get("fill"),
                                  stroke=element.get("stroke"), stroke_width=element.get("stroke-width"), fill_rule=element.get("fill-rule")))
        layers.append(ReadLayer(id=group.get("id"), label=group.get(INK + "label"), groupmode=group.get(INK + "groupmode"), paths=paths))
    return root, layers


def layers_of(svg: str) -> list[ReadLayer]:
    return parse(svg)[1]


def bbox(layer: ReadLayer):
    """(min x, min y, max x, max y) of everything in a layer."""
    points = [p for path in layer.paths for ring in path.rings for p in ring]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def segments(layer: ReadLayer):
    """Every edge of every ring of the layer, each as an unordered pair of points (so that the same edge written the other way round is the
    same edge)."""
    out = []
    for path in layer.paths:
        for ring in path.rings:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                out.append(tuple(sorted([a, b])))
    return out


def winding(ring, x, y) -> int:
    """The winding number of a ring round the point (x, y): non-zero means inside under the non-zero rule."""
    total = 0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if y1 <= y < y2 and (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1) > 0:
            total += 1
        elif y2 <= y < y1 and (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1) < 0:
            total -= 1
    return total


def inside(path: ReadPath, x, y, rule: str) -> bool:
    """Is (x, y) filled by the path under `rule` ("evenodd" or "nonzero")? Used to prove a hole is a hole whichever rule a program uses."""
    if rule == "nonzero":
        return sum(winding(ring, x, y) for ring in path.rings) != 0
    return sum(winding(ring, x, y) != 0 for ring in path.rings) % 2 == 1


def length(layer: ReadLayer) -> float:
    return sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in segments(layer))


# ---- building ------------------------------------------------------------------------------------------------------------------------
def make(shapes, size=(SIZE, SIZE), smooth=4, background=(255, 255, 255), **options):
    """Draw `shapes` and run the real clean-up on the picture, on a sheet of the default size unless the options say otherwise."""
    from studio.vector import VectorOptions, build_vector

    values = {"width_mm": SHEET_MM, "height_mm": SHEET_MM}
    values.update(options)
    return build_vector(as_png(picture(shapes, size=size, background=background, smooth=smooth)), VectorOptions(**values))
