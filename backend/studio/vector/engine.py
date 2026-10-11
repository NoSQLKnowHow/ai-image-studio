"""The clean-up itself (DESIGN.md §31.5 steps 2 to 5, §31.12.3): from a picture and the options to the SVG, the layers' own SVGs and the check.

This file is the order of the steps; each step lives in a file of its own:

  pixels.py    read the picture, reduce it to white plus N-1 inks, remove soft fringes          (numpy)
  geometry.py  trace each ink to closed shapes (vtracer), simplify, remove what is too small   (vtracer, shapely)
  svgio.py     write the SVG in millimetres, one group per layer
  raster.py    draw the PNG from the SVG

Imported lazily (`studio/vector/__init__.py` calls it inside `build_vector`), because it needs numpy, Pillow, vtracer and shapely and the
server must be able to import the package without them.

The promises of §31.12.3 and where this file keeps them:
  1 white is the bare material       inks are N-1 at most, white is never a layer          (`reduce_colours`, `_layers_by_colour`)
  2 exact, distinct colours          `_assign_colours` (nudged by the smallest step), `SHAPE_COLOURS` by shape
  3 names and order                  `_name`, largest ink area first
  4 arrangement                      `_arrange`: side by side (each ink cut out of the ones before it), or stacked with bleed
  5 real size, closed shapes only    `svgio.build_svg`, every number in millimetres
  6 smallest feature                 `geometry.clean_layer`, `geometry.narrowest`, the notes
  7 kerf                             `_arrange`: every layer grown by half of it
  8 deterministic                    no random start, no clock, sorted everywhere an order could vary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from . import LayerFile, VectorError, VectorOptions, VectorResult, check_options
from .colours import colour_name, hex_to_rgb, rgb_to_hex
from . import geometry as geo
from . import pixels, svgio

# In *by shape* mode a layer's colour is a label, not the picture's colour (§31.10 item 7): laser programs tell layers apart by colour, and
# a drawing of 30 leaves in one green would be 30 layers in one colour. A fixed list of 30 colours, each at least 17 apart from every other in
# CIE Lab and none of them white (a test measures both; the first ten are the familiar layer colours and the rest were picked one at a time as the colour
# farthest from all those before it).
SHAPE_COLOURS = (
    "#000000", "#0000FF", "#FF0000", "#00A000", "#FF8000", "#00C8C8", "#FF00FF", "#8000FF", "#A52A2A", "#808080",
    "#0080FF", "#800080", "#FFC0FF", "#C0C000", "#004000", "#FF0080", "#00FF00", "#FFC080", "#000040", "#80C0FF",
    "#FF80FF", "#80C080", "#400000", "#000080", "#FFC0C0", "#808040", "#FF00C0", "#FF8080", "#C04080", "#004040",
)
WHITE = "#FFFFFF"


@dataclass
class _Layer:
    """One layer while it is being made: the ink it came from, its shapes in millimetres, and what the clean-up found."""

    ink: pixels.Ink
    geometry: BaseGeometry
    area: float = 0.0
    colour: str = ""


@dataclass
class _Tally:
    """What the clean-up removed or noticed, for the check's numbers and notes."""

    specks: int = 0
    thin_parts: int = 0
    narrow_gaps: int = 0
    missing: list[str] = field(default_factory=list)  # palette colours that are not in the picture at all
    vanished: list[str] = field(default_factory=list)  # colours that were in the picture, but everything of them was smaller than the smallest feature


def _plural(count: int, one: str, many: Optional[str] = None) -> str:
    """`1 speck`, `3 specks`: the noun in the form that goes with the number."""
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _nudge(colour: str, taken: set[str]) -> str:
    """The nearest colour to `colour` that is not in `taken`: one step in blue, then green, then red, up and down, then two steps, and so on
    (§31.12.3 item 2: "nudged by the smallest step that separates them"). The order is fixed, so the same clash always gives the same colour."""
    red, green, blue = hex_to_rgb(colour)
    for step in range(1, 256):
        for channel in (2, 1, 0):
            for sign in (1, -1):
                candidate = [red, green, blue]
                candidate[channel] = candidate[channel] + sign * step
                if all(0 <= v <= 255 for v in candidate) and rgb_to_hex(tuple(candidate)) not in taken:
                    return rgb_to_hex(tuple(candidate))
    raise VectorError("No distinct colour was left for a layer.")  # 16 million colours and at most 30 layers: cannot happen


def _assign_colours(layers: list[_Layer]) -> None:
    """Give each layer its exact colour (§31.12.3 item 2): the palette's own where the user named one, else the picture's colour, and
    never the same as another layer's or white's. The palette colours go first, so that when a picture-chosen colour clashes with one the
    picture-chosen one is the one that moves (a named colour is a promise; a found one is a measurement)."""
    taken = {WHITE}
    for layer in layers:
        if layer.ink.palette:
            layer.colour = layer.ink.palette
            taken.add(layer.colour)
    for layer in layers:
        if not layer.ink.palette:
            layer.colour = layer.ink.picture if layer.ink.picture not in taken else _nudge(layer.ink.picture, taken)
            taken.add(layer.colour)


def _order(layers: list[_Layer]) -> list[_Layer]:
    """Largest ink area first, ties by darkness (§31.12.3 item 3). The area is rounded to a millionth of a square millimetre so that two
    shapes of the same size, which can differ in the last digit of a float, count as a tie. The ink's index ends the sort so that it is total."""
    return sorted(layers, key=lambda layer: (-round(layer.area, 6), layer.ink.lightness, layer.ink.index))


def _name(number: int, label: str) -> tuple[str, str]:
    """(id, name) of a layer: `layer-2-red`, `Layer 2 (red)` (§31.12.3 item 3, the test sheet's convention)."""
    return f"layer-{number}-{label}", f"Layer {number} ({label})"


def _shapes(geometry: BaseGeometry) -> list[svgio.PathShape]:
    """The shapes of a layer, largest first, each as a path; the ones that rounding to the file's precision made nothing of are left out."""
    polygons = sorted(geo.polygons_of(geometry), key=lambda p: (-round(p.area, 6), round(p.bounds[1], 3), round(p.bounds[0], 3)))
    shapes = [svgio.path_shape(geo.oriented(polygon)) for polygon in polygons]
    return [shape for shape in shapes if shape is not None]


def build_vector(source: Union[Path, str, bytes], opts: VectorOptions) -> VectorResult:
    """See `studio.vector.build_vector`: this is its body. Library failures that are not our own `VectorError` are turned into one, with
    the original kept as the cause (the job prints it to the log), so that no stack trace reaches the card (§31.12.3 item 9)."""
    problems = check_options(opts)
    if problems:
        raise VectorError("The Vector options cannot be used: " + "; ".join(f"{name}: {message}" for name, message in problems))
    try:
        return _build(source, opts)
    except (VectorError, MemoryError):
        raise
    except Exception as exc:  # noqa: BLE001 - see the docstring
        raise VectorError("The clean-up stopped on this picture.", "Try again, or use another picture or a larger smallest feature.") from exc


def _build(source: Union[Path, str, bytes], opts: VectorOptions) -> VectorResult:
    """The steps in order."""
    # ---- the picture, and its scale (the sheet is `width_mm` x `height_mm`; the picture is stretched to fill it, which for a drawing made at
    # `draw_size` is a difference of a few thousandths) ----
    rgb = pixels.load_picture(source)
    pic_h, pic_w = rgb.shape[:2]
    width, height = float(opts.width_mm), float(opts.height_mm)
    scale_x, scale_y = width / pic_w, height / pic_h
    mm_per_pixel = max(scale_x, scale_y)  # the coarser way, so that "how small can this drawing show" is not flattered
    min_feature = float(opts.min_feature_mm)
    r = min_feature / 2
    eps = 0.25 * mm_per_pixel * mm_per_pixel  # a quarter of a pixel, in square millimetres: below it is rounding, not drawing
    tolerance = max(0.002, min(0.25 * mm_per_pixel, min_feature / 5, 0.1))  # how far simplified shapes may stray from the traced ones

    # ---- colours: white plus at most N-1 inks ----
    reduction = pixels.reduce_colours(rgb, opts.colours - 1, opts.palette, pixels.fringe_window(min_feature / mm_per_pixel))
    if not reduction.inks:
        raise VectorError("There is nothing to cut: the picture is all white.",
                          "White is the bare material. Draw the shapes in a darker or stronger colour on the white background.")

    # ---- trace each ink, largest first, cutting each out of the ones before it so that no two layers overlap (§31.12.3 item 4) ----
    tally = _Tally(missing=list(reduction.missing))
    covered: BaseGeometry = Polygon()
    layers: list[_Layer] = []
    for ink in sorted(reduction.inks, key=lambda i: (-i.pixels, i.lightness, i.index)):
        traced = geo.trace_ink(reduction.labels == ink.index, scale_x, scale_y, tolerance / 2)
        shape = geo.clip_to_sheet(traced.simplify(tolerance, preserve_topology=True), width, height)
        if not covered.is_empty:
            shape = shape.difference(covered)
        cleaned = geo.clean_layer(shape, r, eps)
        tally.specks += cleaned.specks
        tally.thin_parts += cleaned.thin_parts
        tally.narrow_gaps += cleaned.narrow_gaps
        final = geo.drop_rubbish(cleaned.geometry, eps)
        if final.is_empty:
            tally.vanished.append(ink.palette or ink.picture)
            continue
        covered = covered.union(final)
        layers.append(_Layer(ink=ink, geometry=final, area=final.area))
    if not layers:
        raise VectorError(f"Nothing is left after removing everything narrower than {min_feature:g} mm.",
                          "Lower the smallest feature, or make a bolder drawing with larger shapes.")
    for layer, geometry in zip(layers, geo.fill_narrow_holes([layer.geometry for layer in layers], r)):
        layer.geometry, layer.area = geometry, geometry.area  # the layers together leave no hairline gap where inks touch
    inks_found = len(layers)
    layers = _order(layers)

    # ---- layers: by colour, or one for each separate piece ----
    capped = 0
    pieces_total = 0
    if opts.layers_by == "shape":
        arrangement = "side_by_side"  # pieces are separate by construction (§31.12.3 item 4)
        pieces = [(layer, polygon) for layer in layers for polygon in geo.polygons_of(layer.geometry)]
        pieces.sort(key=lambda item: (-round(item[1].area, 6), item[0].ink.lightness, round(item[1].bounds[1], 3), round(item[1].bounds[0], 3)))
        pieces_total = len(pieces)
        cap = min(opts.shape_layer_cap, len(SHAPE_COLOURS))
        capped = max(0, pieces_total - cap)
        pieces = pieces[:cap]
        candidates = [(f"piece-{n}", SHAPE_COLOURS[n - 1], item[0].ink.picture, item[1]) for n, item in enumerate(pieces, start=1)]
    else:
        arrangement = opts.arrangement
        _assign_colours(layers)
        candidates = [(colour_name(layer.colour), layer.colour, layer.ink.picture, layer.geometry) for layer in layers]
    # each candidate is (name word, layer colour, the picture's colour, shapes)
    geometries = _arrange([candidate[3] for candidate in candidates], arrangement, float(opts.bleed_mm), float(opts.kerf_mm), width, height, eps)

    svg_layers: list[svgio.SvgLayer] = []
    kept_geometries: list[BaseGeometry] = []
    check_layers = []
    for (label, colour, picture, _), geometry in zip(candidates, geometries):
        shapes = _shapes(geometry)
        if not shapes:  # rounding left nothing (cannot happen after the smallest feature, but a file with an empty group is worse)
            tally.vanished.append(colour)
            continue
        number = len(svg_layers) + 1
        layer_id, name = _name(number, label)
        svg_layers.append(svgio.SvgLayer(number=number, id=layer_id, name=name, colour=colour, shapes=tuple(shapes)))
        kept_geometries.append(geometry)
        check_layers.append({"index": number, "id": layer_id, "name": name, "colour": colour, "picture_colour": picture, "paths": len(shapes),
                             "length_mm": round(sum(shape.length_mm for shape in shapes), 1)})
    if not svg_layers:
        raise VectorError("Nothing is left to cut.", "Lower the smallest feature, or make a bolder drawing with larger shapes.")

    # ---- the SVGs ----
    description = (f"{svgio.fmt(width)} x {svgio.fmt(height)} mm, one unit is one millimetre. {len(svg_layers)} layer"
                   f"{'' if len(svg_layers) == 1 else 's'}, each in its own exact colour. White is the bare material and has no layer.")
    title = "AI Image Studio Vector"
    svg = svgio.build_svg(width, height, svg_layers, opts.fill_mode, title, description)
    files = tuple(LayerFile(id=layer.id, name=layer.name, colour=layer.colour,
                            svg=svgio.build_svg(width, height, [layer], opts.fill_mode, title, description)) for layer in svg_layers)

    # ---- the check ----
    smallest = geo.narrowest(kept_geometries, 0.25 * mm_per_pixel, min(width, height) / 2, eps)
    hanging = sum(shape.holes for layer in svg_layers for shape in layer.shapes)
    white = reduction.white_pixels > 0
    found = inks_found + (1 if white else 0)
    notes = _notes(opts, found, white, tally, hanging, smallest, mm_per_pixel, capped, pieces_total, len(svg_layers))
    check = {
        "colours_asked": opts.colours,
        "colours_found": found,
        "white": white,
        "size_mm": [width, height],
        "layers_by": opts.layers_by,
        "arrangement": arrangement,
        "svg_bytes": len(svg.encode("utf-8")),
        "layers": check_layers,
        "smallest_feature_mm": round(smallest, 2),
        "removed": {"specks": tally.specks, "thin_parts": tally.thin_parts},
        "mm_per_pixel": round(mm_per_pixel, 4),
        "notes": notes,
    }
    return VectorResult(svg=svg, layers=files, check=check)


def _arrange(geometries: list[BaseGeometry], arrangement: str, bleed: float, kerf: float, width: float, height: float,
             eps: float) -> list[BaseGeometry]:
    """Side by side keeps each layer as it is (each ink was already cut out of the ones before it, so nothing overlaps); stacked makes
    layer k everything from k to the top, grown by the bleed (§31.12.3 item 4). Then the kerf (item 7): every layer grown by half of it,
    which moves a cut-out piece's outline out and a hole's in, as the laser's own width will take it back. Then everything is cut at the
    sheet's edge, so that growing a shape that touches the edge does not put anything off the sheet."""
    out = list(geometries)
    if arrangement == "stacked":
        import shapely

        out = [shapely.union_all(out[k:]) for k in range(len(out))]
        if bleed > 0:
            out = [geo.offset(g, bleed, "round") for g in out]
    if kerf > 0:
        out = [geo.offset(g, kerf / 2) for g in out]
    return [geo.drop_rubbish(geo.clip_to_sheet(g, width, height), eps) for g in out]


def _notes(opts: VectorOptions, found: int, white: bool, tally: _Tally, hanging: int, smallest: float, mm_per_pixel: float,
           capped: int, pieces_total: int, layer_count: int) -> list[dict]:
    """The check's notes, in the order of the codes in §31.12.3: each says in plain English what happened and why it matters."""
    notes: list[dict] = []
    minimum = f"{opts.min_feature_mm:g} mm"
    if found < opts.colours:
        counted = " (white counts as one)" if white else " and no white"  # a picture that fills the sheet has no bare material to count
        notes.append({"code": "fewer_colours", "message": f"The picture has {found} colour{'' if found == 1 else 's'}{counted}, "
                      f"not the {opts.colours} you asked for, so it makes {_plural(layer_count, 'layer')} and not more."})
    if tally.missing or tally.vanished:
        sentences = []
        if tally.missing:
            sentences.append(f"{', '.join(tally.missing)} {'is' if len(tally.missing) == 1 else 'are'} not in the picture, so "
                             f"{'it has' if len(tally.missing) == 1 else 'they have'} no layer.")
        if tally.vanished:
            sentences.append(f"{', '.join(tally.vanished)} had nothing left to cut after the specks and thin parts were removed, so "
                             f"{'it has' if len(tally.vanished) == 1 else 'they have'} no layer.")
        notes.append({"code": "empty_layer", "message": " ".join(sentences)})
    if tally.specks:
        notes.append({"code": "specks_removed", "message": f"{_plural(tally.specks, 'speck')} smaller than {minimum} "
                      f"{'was' if tally.specks == 1 else 'were'} removed."})
    if tally.thin_parts or tally.narrow_gaps:
        sentences = []
        if tally.thin_parts:
            sentences.append(f"{_plural(tally.thin_parts, 'thin part')} narrower than {minimum} (a neck or a whisker) "
                             f"{'was' if tally.thin_parts == 1 else 'were'} removed.")
        if tally.narrow_gaps:
            sentences.append(f"{_plural(tally.narrow_gaps, 'gap')} narrower than {minimum} between parts of a layer "
                             f"{'was' if tally.narrow_gaps == 1 else 'were'} left as drawn and may not cut cleanly.")
        notes.append({"code": "small_feature", "message": " ".join(sentences)})
    if hanging:
        notes.append({"code": "hanging_island", "message": f"{_plural(hanging, 'piece')} of material enclosed by a cut (like the middle of an O) "
                      "would fall out if this layer were cut as a stencil; leave a bridge there if you need it."})
    if opts.min_feature_mm < 2 * mm_per_pixel:
        notes.append({"code": "coarse_drawing", "message": f"The drawing is too coarse to show a feature as small as {minimum}: one pixel is "
                      f"{mm_per_pixel:.3f} mm, so about {2 * mm_per_pixel:.2f} mm is the smallest it can show. Make the sheet smaller "
                      "or the smallest feature larger."})
    if capped:
        notes.append({"code": "layers_capped", "message": f"The drawing has {_plural(pieces_total, 'separate piece')}; only the {pieces_total - capped} "
                      f"largest have a layer ({capped} smaller {'one was' if capped == 1 else 'ones were'} left out)."})
    return notes
