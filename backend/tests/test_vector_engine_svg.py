"""The file the Vector clean-up writes (DESIGN.md §31.4, §31.12.3 items 5 and 8; §31.8 criteria 137 to 140): real millimetres, one group per
layer with an id, a name and an Inkscape label, closed paths of M, L and Z only, holes that are holes under either fill rule, nothing a
laser program cannot use, no edge drawn twice, and the check's numbers true of the file.

Every test reads the SVG with the helpers in vector_engine_support.py, which parse it from the text (as test_laser_test_sheet.py does for
the test sheet): none of them trusts the engine's own geometry objects."""

from __future__ import annotations

import collections
import re
import time

import pytest
from PIL import Image, ImageDraw

from studio.vector import VectorOptions, build_vector

from vector_engine_support import INK, SHEET_MM, SVG, as_png, bbox, inside, layers_of, length, make, mm, parse, segments

BLACK, RED, GREEN = (0, 0, 0), (255, 0, 0), (0, 160, 0)


def drawing():
    """Three inks, one with a hole: a red square with a square window, a black disc and a green triangle."""
    return [("rect", (mm(3), mm(3), mm(24), mm(24)), RED), ("rect", (mm(9), mm(9), mm(18), mm(18)), (255, 255, 255)),
            ("ellipse", (mm(28), mm(3), mm(46), mm(21)), BLACK), ("poly", [(mm(25), mm(46)), (mm(45), mm(46)), (mm(35), mm(28))], GREEN)]


# ---- real size (item 5; criterion 138) --------------------------------------------------------------------------------------------------
def test_the_file_is_in_millimetres_with_a_view_box_that_starts_at_zero():
    """A file with no units is read as 96 or 72 dots to the inch by different programs, which would cut the wrong size (§31.4): the size is
    in `mm`, the view box is in millimetres and starts at the origin, and nothing in the file is in pixels."""
    result = make([("rect", (mm(10), mm(10), mm(50), mm(30)), BLACK)], size=(600, 300), width_mm=120, height_mm=60)
    root = parse(result.svg)[0]
    assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("120mm", "60mm", "0 0 120 60")
    assert root.get("version") == "1.1" and "px" not in result.svg
    assert result.check["size_mm"] == [120, 60]


def test_a_square_drawn_25_mm_wide_is_25_mm_wide_in_the_file_at_the_real_drawing_size():
    """The real case: a 1536 pixel drawing (the size the model draws) on a 100 mm sheet. A 25 mm square comes out 25 mm, to a pixel (0.065
    mm); a clean-up that mixed up pixels and millimetres, or the width and the height, would not. It also must take seconds (§31.12)."""
    start = time.perf_counter()
    image = Image.new("RGB", (1536, 1536), "white")
    ImageDraw.Draw(image).rectangle((384, 192, 767, 575), fill=BLACK)  # 384 x 384 pixels, 25 mm square, at x 25 mm, y 12.5 mm
    result = build_vector(as_png(image), VectorOptions(colours=2, width_mm=100, height_mm=100))
    x0, y0, x1, y1 = bbox(layers_of(result.svg)[0])
    assert abs((x1 - x0) - 25) < 0.1 and abs((y1 - y0) - 25) < 0.1 and abs(x0 - 25) < 0.1 and abs(y0 - 12.5) < 0.1
    assert result.check["mm_per_pixel"] == pytest.approx(0.0651, abs=0.0001)
    assert time.perf_counter() - start < 30  # the real figure is about a second; this only catches an algorithm that has gone wrong


def test_width_and_height_are_not_mixed_up_on_a_sheet_that_is_not_square():
    """A 100 x 40 mm bar on a 120 x 60 mm sheet: 100 wide and 40 high, at the position it has in the picture. The picture is 600 x 300 pixels,
    so five pixels to the millimetre here (not the ten the other tests use)."""
    result = make([("rect", (25, 50, 525, 250), BLACK)], size=(600, 300), width_mm=120, height_mm=60, smooth=2)
    x0, y0, x1, y1 = bbox(layers_of(result.svg)[0])
    assert (round(x0), round(y0), round(x1), round(y1)) == (5, 10, 105, 50)


def test_a_picture_a_little_off_the_sheets_shape_is_stretched_to_fill_the_sheet():
    """The picture is drawn at a size that follows the sheet's shape to within a few percent (`draw_size`). The engine fills the sheet with it
    and does not leave a margin: a full-height black bar spans the whole sheet, 100 x 60 mm, though the picture is 512 x 256."""
    result = make([("rect", (0, 0, 512, 256), BLACK)], size=(512, 256), width_mm=100, height_mm=60)
    assert bbox(layers_of(result.svg)[0]) == (0, 0, 100, 60)
    assert result.check["mm_per_pixel"] == pytest.approx(60 / 256, abs=0.001)  # the coarser way: the check does not flatter the drawing


# ---- one group per layer (item 5, item 3) ----------------------------------------------------------------------------------------------
def test_each_layer_is_a_group_with_an_id_a_name_and_the_same_name_as_the_inkscape_label():
    """The test sheet's convention (DESIGN.md §31.9): `layer-2-black`, label `Layer 2 (black)`. The engine writes the label that the check
    calls the name, and the id differs from it, so that a program that reads one or the other shows the same name."""
    result = make(drawing(), colours=4)
    layers = layers_of(result.svg)
    assert [(layer.id, layer.label) for layer in layers] == [("layer-1-red", "Layer 1 (red)"), ("layer-2-black", "Layer 2 (black)"),
                                                            ("layer-3-green", "Layer 3 (green)")]
    for layer, row in zip(layers, result.check["layers"]):
        assert layer.groupmode == "layer" and layer.id != layer.label
        assert (row["id"], row["name"], row["index"]) == (layer.id, layer.label, int(layer.id.split("-")[1]))
    ids = [path.id for layer in layers for path in layer.paths]
    assert len(ids) == len(set(ids)) and all(path.id.startswith(f"layer{n}-") for n, layer in enumerate(layers, 1) for path in layer.paths)
    assert [path.id for path in layers[0].paths] == [f"layer1-path{m}" for m in range(1, len(layers[0].paths) + 1)]  # numbered from 1


def test_every_layer_has_one_exact_colour_and_no_two_layers_share_one():
    result = make(drawing(), colours=4)
    layers = layers_of(result.svg)
    colours = [layer.colours() for layer in layers]
    assert all(len(c) == 1 for c in colours) and len({next(iter(c)) for c in colours}) == len(layers)
    assert [next(iter(c)) for c in colours] == [row["colour"] for row in result.check["layers"]]
    assert all(re.fullmatch(r"#[0-9A-F]{6}", next(iter(c))) for c in colours)  # capitals, six digits, as laser programs and the contract want


# ---- only what a laser can use (item 5; criterion 139) ---------------------------------------------------------------------------------
def test_nothing_but_what_a_laser_program_takes_is_in_the_file():
    """Criterion 139: closed paths and groups. No gradient, opacity, filter, text, image, script, event handler, style, transform or link; the
    file is plain ASCII; the only addresses in it are the two namespace names."""
    result = make(drawing(), colours=4)
    root, layers = parse(result.svg)
    tags = {element.tag.replace(SVG, "") for element in root.iter()}
    assert tags == {"svg", "title", "desc", "g", "path"}
    assert set(root.attrib) == {"width", "height", "viewBox", "version"}
    allowed = {"id", "d", "fill", "fill-rule", "stroke", "stroke-width"}
    for element in root.iter(SVG + "path"):
        assert set(element.attrib) <= allowed
    for element in root.iter(SVG + "g"):
        assert set(element.attrib) == {"id", INK + "groupmode", INK + "label"}
    result.svg.encode("ascii")
    lowered = result.svg.lower()
    for word in ("gradient", "opacity", "filter", "<text", "<image", "<script", "onload", "onclick", "href", "style", "transform", "javascript", "url("):
        assert word not in lowered
    assert re.findall(r"https?://[^\"' ]+", result.svg) == ["http://www.w3.org/2000/svg", "http://www.inkscape.org/namespaces/inkscape"]


def test_every_path_is_closed_and_made_of_moves_lines_and_closes_only_inside_the_sheet():
    """`parse_d` fails on any other command, on an open subpath, or on fewer than three points; and nothing may be off the sheet."""
    result = make(drawing(), colours=4)
    for layer in layers_of(result.svg):
        x0, y0, x1, y1 = bbox(layer)
        assert layer.paths and x0 >= 0 and y0 >= 0 and x1 <= SHEET_MM and y1 <= SHEET_MM


def test_a_ring_is_written_with_its_hole_as_a_subpath_that_is_a_hole_under_either_fill_rule():
    """§31.12.3 item 5: the hole is wound the other way round, so it is empty whether a program uses the even-odd or the non-zero rule; the
    path says even-odd as well, as the test sheet's ring does. A black ring: disc 40 mm wide with a 16 mm hole."""
    result = make([("ellipse", (mm(5), mm(5), mm(45), mm(45)), BLACK), ("ellipse", (mm(17), mm(17), mm(33), mm(33)), (255, 255, 255))], colours=2)
    layer = layers_of(result.svg)[0]
    assert len(layer.paths) == 1 and len(layer.paths[0].rings) == 2 and layer.paths[0].fill_rule == "evenodd"
    outline, hole = layer.paths[0].rings

    def turn(ring):
        return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))

    assert turn(outline) * turn(hole) < 0  # opposite ways round
    for rule in ("evenodd", "nonzero"):
        assert not inside(layer.paths[0], 25, 25, rule)  # the middle of the hole is empty
        assert inside(layer.paths[0], 25, 9, rule) and inside(layer.paths[0], 41, 25, rule)  # the ring itself is filled


def test_a_piece_inside_a_hole_is_a_path_of_its_own_and_not_a_hole():
    """A black dot in the middle of a black ring is a separate piece (a second path), not a subpath, so that it is filled under both rules."""
    result = make([("ellipse", (mm(5), mm(5), mm(45), mm(45)), BLACK), ("ellipse", (mm(12), mm(12), mm(38), mm(38)), (255, 255, 255)),
                   ("ellipse", (mm(21), mm(21), mm(29), mm(29)), BLACK)], colours=2)
    layer = layers_of(result.svg)[0]
    assert len(layer.paths) == 2 and sorted(len(path.rings) for path in layer.paths) == [1, 2]
    dot = next(path for path in layer.paths if len(path.rings) == 1)
    assert inside(dot, 25, 25, "evenodd") and inside(dot, 25, 25, "nonzero")


def test_a_shape_without_a_hole_does_not_carry_a_fill_rule():
    """The file says even-odd only where there is a hole, as the test sheet does (its plain squares say nothing)."""
    result = make([("rect", (mm(10), mm(10), mm(40), mm(40)), BLACK)], colours=2)
    path = layers_of(result.svg)[0].paths[0]
    assert path.fill_rule is None and len(path.rings) == 1


# ---- filled or outline: the one switch (item 5; §31.10 item 8) ----------------------------------------------------------------------
def test_filled_mode_is_an_area_without_a_line_and_outline_mode_a_line_without_an_area():
    """`fill_mode` is the one setting that the laser test sheet will decide: filled is `fill` and `stroke="none"`, outline is `fill="none"`
    and a stroke 0.1 mm wide (the test sheet's own width) in the layer's colour; the shapes themselves are the same."""
    filled = make(drawing(), colours=4)
    outline = make(drawing(), colours=4, fill_mode="outline")
    for a, b in zip(layers_of(filled.svg), layers_of(outline.svg)):
        for fa, fb in zip(a.paths, b.paths):
            assert (fa.fill, fa.stroke, fa.stroke_width) == (a.colours().copy().pop(), "none", None)
            assert (fb.fill, fb.stroke, fb.stroke_width, fb.fill_rule) == ("none", fa.fill, "0.1", None)
            assert fa.d == fb.d
    assert filled.check["layers"] == outline.check["layers"]  # the check does not depend on how the shapes are painted


# ---- the layers' own files (criterion 141) -----------------------------------------------------------------------------------------------
def test_each_layers_own_svg_has_the_same_size_and_origin_and_holds_exactly_that_layer():
    """The per-layer files register when stacked because they have the same width, height and view box as the combined file, and each holds
    its own group, unchanged."""
    result = make(drawing(), colours=4, arrangement="stacked")
    combined_root, combined_layers = parse(result.svg)
    assert len(result.layers) == len(combined_layers) == 3
    for own, combined in zip(result.layers, combined_layers):
        root, layers = parse(own.svg)
        assert (root.get("width"), root.get("height"), root.get("viewBox")) == (combined_root.get("width"), combined_root.get("height"),
                                                                                 combined_root.get("viewBox"))
        assert len(layers) == 1 and layers[0].id == combined.id == own.id and layers[0].label == combined.label == own.name
        assert [(p.id, p.d, p.fill) for p in layers[0].paths] == [(p.id, p.d, p.fill) for p in combined.paths]
        assert own.colour == next(iter(combined.colours()))


def test_the_file_has_a_title_and_a_description_that_says_what_it_is():
    """A title and a description are the only text in the file (laser programs show them as the file's name and note): the studio's name, and the
    size and number of layers, worded for one layer and for several."""
    one = make([("rect", (mm(10), mm(10), mm(40), mm(40)), BLACK)], colours=2)
    many = make(drawing(), colours=4)
    root_one, root_many = parse(one.svg)[0], parse(many.svg)[0]
    assert root_one.find(SVG + "title").text == "AI Image Studio Vector"
    assert root_one.find(SVG + "desc").text.startswith("50 x 50 mm, one unit is one millimetre. 1 layer, each in")
    assert root_many.find(SVG + "desc").text.startswith("50 x 50 mm, one unit is one millimetre. 3 layers, each in")


def test_the_shapes_of_a_layer_are_written_largest_first_and_then_from_the_top_left():
    """Within a layer the biggest piece comes first (a laser program cuts in file order unless told otherwise; a big piece first is the steadiest
    cut), and equal pieces go top to bottom, then left to right. Drawn smallest first and scrambled, so that the order is not the drawing's."""
    shapes = [("rect", (mm(30), mm(30), mm(35), mm(35)), BLACK), ("rect", (mm(5), mm(30), mm(15), mm(40)), BLACK),
              ("rect", (mm(5), mm(5), mm(25), mm(25)), BLACK), ("rect", (mm(30), mm(5), mm(40), mm(15)), BLACK)]
    layer = layers_of(make(shapes, colours=2, smooth=1).svg)[0]
    corners = [min(p for p in path.rings[0]) for path in layer.paths]
    assert corners == [(5, 5), (30, 5), (5, 30), (30, 30)]  # the 20 mm square, then the two 10 mm ones top first, then the 5 mm one


def test_outline_mode_is_in_the_layers_own_files_as_well():
    """The per-layer files are written in the same mode as the combined file."""
    result = make(drawing(), colours=4, fill_mode="outline")
    for own in result.layers:
        paths = layers_of(own.svg)[0].paths
        assert paths and all((p.fill, p.stroke, p.stroke_width) == ("none", own.colour, "0.1") for p in paths)


# ---- the check tells the truth about the file (criterion 140) -----------------------------------------------------------------------------
def test_the_checks_numbers_are_what_is_in_the_file():
    """Paths per layer, cut length per layer, the size, the file's size and the layers' names and colours are measured from the file, not from
    what the engine meant to write."""
    result = make(drawing(), colours=4)
    layers = layers_of(result.svg)
    for row, layer in zip(result.check["layers"], layers):
        assert row["paths"] == len(layer.paths)
        assert row["length_mm"] == pytest.approx(length(layer), abs=0.15)
        assert row["colour"] == next(iter(layer.colours()))
    assert result.check["svg_bytes"] == len(result.svg.encode("utf-8"))
    assert result.check["size_mm"] == [SHEET_MM, SHEET_MM] and (result.check["layers_by"], result.check["arrangement"]) == ("colour", "side_by_side")
    # a 21 mm square with a 9 mm square window: cut length is both outlines, 84 + 36 = 120 mm (to a pixel on each side)
    red = result.check["layers"][0]
    assert red["paths"] == 1 and red["length_mm"] == pytest.approx(120, abs=1.5)


# ---- no edge drawn twice (item 4: "no edge is drawn twice"; item 5) -----------------------------------------------------------------
def test_pieces_of_one_colour_that_touch_are_one_path_and_share_no_edge():
    """Two black squares that touch along a side are one shape (a laser would cut the shared side twice and burn it): one path, four corners.
    Two that touch only at a corner are two paths and share no edge."""
    joined = layers_of(make([("rect", (mm(5), mm(10), mm(20), mm(25)), BLACK), ("rect", (mm(20), mm(10), mm(35), mm(25)), BLACK)], colours=2).svg)[0]
    assert len(joined.paths) == 1 and len(joined.paths[0].rings[0]) == 4
    corner = layers_of(make([("rect", (mm(5), mm(5), mm(20), mm(20)), BLACK), ("rect", (mm(20), mm(20), mm(35), mm(35)), BLACK)], colours=2).svg)[0]
    assert len(corner.paths) == 2


@pytest.mark.parametrize("arrangement", ["side_by_side", "stacked"])
def test_no_edge_is_drawn_twice_in_any_layer(arrangement):
    """Within a layer no edge appears twice (the same two points, in either direction): not as two touching shapes, not as a hole that
    coincides with an outline."""
    result = make(drawing() + [("rect", (mm(30), mm(30), mm(40), mm(40)), RED)], colours=4, arrangement=arrangement)
    for layer in layers_of(result.svg):
        counts = collections.Counter(segments(layer))
        assert [edge for edge, count in counts.items() if count > 1] == []


# ---- the same picture and options give the same bytes (item 8) ---------------------------------------------------------------------------
def test_the_same_picture_and_options_give_the_same_bytes():
    """§31.10 item 5: the seed makes the picture, not the clean-up. Run twice, everything is identical: the SVG, every layer's file, the check."""
    a, b = make(drawing(), colours=4), make(drawing(), colours=4)
    assert a.svg == b.svg and a.check == b.check and [x.svg for x in a.layers] == [x.svg for x in b.layers]
