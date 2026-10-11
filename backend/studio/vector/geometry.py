"""The shape side of the clean-up (DESIGN.md §31.5 steps 3 and 4): trace an ink's pixels into closed shapes with vtracer, and make the
shapes laser-ready with shapely: simplified, nothing narrower than the smallest feature, holes as holes, one shape per piece.

Everything here is in millimetres once `trace_ink` has returned (the tracer works in pixels; the scale is applied at the door, so no
other function needs to know about pixels). Imported lazily by `engine.py`, never at the top of `studio/vector/__init__.py`.
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import shapely
from PIL import Image
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from .pathdata import parse_path

# Offsets (the smallest-feature opening, bleed, kerf) use mitre joins, so that a square stays a square and a corner stays sharp. A round
# join would shave every convex corner by 0.2 r squared, which would be counted as lost detail all over a clean drawing. Mitres of very
# acute corners are cut off (limit 3.0, which is about 39 degrees), which is right: nothing sharper than that can be cut anyway.
MITRE_LIMIT = 3.0
QUAD_SEGS = 4  # only matters for round joins (the bleed), where 4 per quarter turn is an error of 0.5% of the radius
# How elongated a removed part has to be, in smallest features, to be reported as a thin part or a narrow gap: a short stub or the
# sharp tip of a star is trimmed without a word, a neck or a slit that runs for twice the smallest feature or more is a real finding.
ELONGATED = 4.0  # in units of r (half the smallest feature), so 2 smallest features long
_TRANSLATE = re.compile(r"translate\(\s*([-+0-9.eE]+)[\s,]+([-+0-9.eE]+)\s*\)")


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    """The polygons inside any geometry (a polygon, a multipolygon, a collection that an operation returned), with no empty or flat ones.
    Lines and points that an operation can leave behind are not shapes a laser can cut, so they are dropped here."""
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry] if geometry.area > 0 else []
    if hasattr(geometry, "geoms"):
        return [p for part in geometry.geoms for p in _polygons(part)]
    return []


polygons_of = _polygons


def _even_odd(rings: Iterable[list[tuple[float, float]]]) -> BaseGeometry:
    """The area a path's rings enclose under the even-odd rule: a ring inside a ring is a hole. That is how every SVG reader treats the
    tracer's compound paths, and the engine does the same so that holes come out as holes."""
    result: BaseGeometry = Polygon()
    for ring in rings:
        polygon = Polygon(ring)
        if not polygon.is_valid:
            polygon = shapely.make_valid(polygon)  # a spline can cross itself by a hair; the repaired shape is what was meant
        result = result.symmetric_difference(polygon)
    return result


def trace_ink(mask: np.ndarray, scale_x: float, scale_y: float, tolerance_mm: float) -> BaseGeometry:
    """Trace one ink's pixels (a yes/no mask) into shapes in millimetres, with vtracer in binary mode (§31.5 step 3).

    Only the part of the picture that has ink is given to the tracer (the mask's bounding box), which is much faster for a small ink and
    makes no difference to the shapes. `tolerance_mm` is how far a flattened curve may stray from the tracer's curve (polygon mode writes
    none, but the reader flattens them if a later tracer does)."""
    import vtracer

    rows, cols = np.nonzero(mask.any(axis=1))[0], np.nonzero(mask.any(axis=0))[0]
    if not len(rows):
        return Polygon()
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    crop = mask[y0:y1, x0:x1]
    # vtracer's binary mode traces the dark pixels of a picture, so the ink is drawn black on white.
    picture = Image.fromarray(np.where(crop, 0, 255).astype(np.uint8), "L")
    buffer = io.BytesIO()
    picture.save(buffer, "PNG", compress_level=1)
    # Polygon mode, not spline mode: measured with vtracer 0.6.15, spline mode can drift 40 pixels from the edge of a large circle (its
    # curves are fitted across long arcs), while polygon mode stays within a pixel and a half of the true edge on every shape tried, keeps
    # straight edges straight (a square is five points) and is the faster of the two. A laser file has no need of the curves.
    svg = vtracer.convert_raw_image_to_svg(buffer.getvalue(), img_format="png", colormode="binary", mode="polygon", filter_speckle=0,
                                           path_precision=3)
    tolerance_px = tolerance_mm / max(scale_x, scale_y)
    pieces = []
    for element in ET.fromstring(svg.encode("utf-8")).iter():
        if not element.tag.endswith("path"):
            continue
        moved = _TRANSLATE.search(element.get("transform", ""))
        dx, dy = (float(moved.group(1)), float(moved.group(2))) if moved else (0.0, 0.0)
        rings = [[((x + dx + x0) * scale_x, (y + dy + y0) * scale_y) for x, y in ring] for ring in parse_path(element.get("d", ""), tolerance_px)]
        if rings:
            pieces.append(_even_odd(rings))
    return shapely.union_all(pieces) if pieces else Polygon()


# ---- cleaning a layer's shapes ---------------------------------------------------------------------------------------------------------
def offset(geometry: BaseGeometry, distance: float, join: str = "mitre") -> BaseGeometry:
    """The geometry grown by `distance` (shrunk when negative): the one place buffering is done, so that the joins are chosen once."""
    return geometry.buffer(distance, quad_segs=QUAD_SEGS, join_style=join, mitre_limit=MITRE_LIMIT)


def opened(geometry: BaseGeometry, r: float) -> BaseGeometry:
    """The geometry without anything narrower than 2r: shrink by r, grow back. A part too thin to survive the shrinking does not come
    back (a speck, a neck, a whisker); everything thick enough returns exactly (mitre joins)."""
    return offset(offset(geometry, -r), r) if r > 0 else geometry


def closed(geometry: BaseGeometry, r: float) -> BaseGeometry:
    """The geometry with every gap narrower than 2r filled: grow by r, shrink back."""
    return offset(offset(geometry, r), -r) if r > 0 else geometry


def elongated(piece: Polygon, r: float) -> bool:
    """Is this piece at least `ELONGATED` r long (twice the smallest feature)? Measured on the smallest rectangle that holds it."""
    rectangle = piece.minimum_rotated_rectangle
    if not isinstance(rectangle, Polygon):
        return False
    corners = list(rectangle.exterior.coords)
    return max(math.dist(corners[i], corners[i + 1]) for i in range(2)) >= ELONGATED * r


@dataclass
class Cleaned:
    """A layer's shapes after the smallest-feature clean-up, and what it found (the numbers in the check's `removed`)."""

    geometry: BaseGeometry
    specks: int = 0  # whole pieces smaller than the smallest feature, and holes too small to cut, that were removed
    thin_parts: int = 0  # thin necks and whiskers that were cut off a piece that stays
    narrow_gaps: int = 0  # gaps between parts of the layer that are narrower than the smallest feature (kept, and flagged)


def lost_parts(before: BaseGeometry, after: BaseGeometry, r: float, eps_area: float) -> tuple[list[Polygon], list[Polygon]]:
    """What an opening by r took away from `before`, as (specks, thin parts). A speck is a lost piece that touches nothing that stayed (a
    whole piece of the layer smaller than the smallest feature); a thin part is a lost piece attached to something that stayed (a neck or a
    whisker). Pieces smaller than a quarter of a pixel are the tracer's rounding and are not counted, and a thin part must also be bigger
    than 0.04 r squared, which is more than the sliver that rounding a polygon's corners leaves, and less than any whisker worth a word."""
    kept = _polygons(after)
    tree = shapely.STRtree(kept) if kept else None
    specks: list[Polygon] = []
    thin: list[Polygon] = []
    for lost in _polygons(before.difference(after)):
        if lost.area < eps_area:
            continue
        if tree is None or not len(tree.query(lost, predicate="intersects")):
            specks.append(lost)
        elif lost.area >= 0.04 * r * r:
            thin.append(lost)
    return specks, thin


def narrow_gaps(geometry: BaseGeometry, r: float, eps_area: float) -> list[Polygon]:
    """The gaps in `geometry` narrower than 2r: what closing it by r would fill, as long as it is long enough to be a gap and not the tip
    of a notch (`elongated`) and big enough not to be rounding (as for a thin part)."""
    return [gap for gap in _polygons(closed(geometry, r).difference(geometry))
            if gap.area >= max(eps_area, 0.04 * r * r) and elongated(gap, r)]


def clean_layer(geometry: BaseGeometry, r: float, eps_area: float) -> Cleaned:
    """Apply the smallest feature (§31.12.3 item 6) to one layer's shapes. `r` is half the smallest feature, in millimetres.

    Parts narrower than 2r are removed (specks, thin necks); a hole too small to hold a disc of radius r is filled (a speck of bare
    material); a gap narrower than 2r between parts is kept, because closing it would change the drawing, and is counted so that it
    is flagged. `eps_area` is a quarter of a pixel: what is smaller than that is the tracer's rounding, not part of the picture."""
    keep = opened(geometry, r)
    specks, thin = lost_parts(geometry, keep, r, eps_area)
    found = Cleaned(geometry=keep, specks=len(specks), thin_parts=len(thin))
    # Holes with no room for a disc of radius r are filled in.
    whole = []
    for polygon in _polygons(keep):
        holes = [ring for ring in polygon.interiors if not offset(Polygon(ring), -r).is_empty]
        found.specks += len(polygon.interiors) - len(holes)
        whole.append(Polygon(polygon.exterior, holes) if len(holes) != len(polygon.interiors) else polygon)
    found.geometry = shapely.union_all(whole) if whole else Polygon()
    if not found.geometry.is_empty:
        found.narrow_gaps = len(narrow_gaps(found.geometry, r, eps_area))
    return found


def removes_something(geometry: BaseGeometry, r: float, eps_area: float, gaps: bool) -> bool:
    """Is there a part of this geometry narrower than 2r (or, with `gaps`, a gap narrower than 2r)? Used to measure the smallest feature
    that remains. A speck counts when it is at least a pixel (`4 * eps_area`): the smallest thing the picture can show."""
    if gaps:
        return bool(narrow_gaps(geometry, r, eps_area))
    specks, thin = lost_parts(geometry, opened(geometry, r), r, eps_area)
    return bool(thin) or any(speck.area >= 4 * eps_area for speck in specks)


def narrowest(geometries: list[BaseGeometry], r_floor: float, r_ceiling: float, eps_area: float) -> float:
    """The width, in millimetres, of the narrowest part of any of `geometries` or the narrowest gap in any of them: the smallest
    feature that remains. Found by trying wider and wider openings (and closings) until one changes something, then narrowing down on it.
    The answer is at most `2 * r_ceiling` (the sheet's own size: a part cannot be wider), and at least a pixel or so."""
    best = 2 * r_ceiling
    for geometry in geometries:
        if geometry.is_empty:
            continue
        for gaps in (False, True):
            # Cheap test first: does this geometry have anything narrower than the best so far? Most layers do not, and are done in one try.
            r_test = best / 2 * 0.98
            if r_test <= r_floor or not removes_something(geometry, r_test, eps_area, gaps):
                continue
            low, high = r_floor, r_test
            r = r_floor
            while r < high and not removes_something(geometry, r, eps_area, gaps):  # doubling up to the first width that changes something
                low, r = r, min(r * 2, high)
            high = r
            for _ in range(6):  # then narrowing down on it (a part's width to about 1.5%)
                middle = (low + high) / 2
                if removes_something(geometry, middle, eps_area, gaps):
                    high = middle
                else:
                    low = middle
            best = min(best, 2 * high)
    return best


# ---- layout ---------------------------------------------------------------------------------------------------------------------------
def clip_to_sheet(geometry: BaseGeometry, width: float, height: float) -> BaseGeometry:
    """Nothing may be off the sheet (a laser program would place it outside the work area): offsets and the tracer's curves can reach
    a hair over the edge, so everything is cut at the sheet's edge."""
    return geometry.intersection(box(0, 0, width, height))


def drop_rubbish(geometry: BaseGeometry, eps_area: float) -> BaseGeometry:
    """Remove polygons smaller than a quarter of a pixel (what boolean operations leave along a shared edge) and anything that is not
    a polygon."""
    kept = [p for p in _polygons(geometry) if p.area >= eps_area]
    return shapely.union_all(kept) if kept else Polygon()


def oriented(polygon: Polygon) -> Polygon:
    """The outline runs one way round and every hole the other way, so that a hole is a hole under the even-odd and the non-zero fill rules
    alike (§31.12.3 item 5): the rule a program uses is not ours to choose."""
    return orient(polygon, 1.0)
