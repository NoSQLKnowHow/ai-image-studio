"""How the Vector clean-up lays the layers out and what it does with what is too small (DESIGN.md §31.12.3 items 3, 4, 6 and 7; §31.8
criteria 137 and 139): largest layer first, side by side without overlap or gaps, stacked with a bleed, the kerf, one layer per separate piece
(capped), the smallest feature (removed, flagged, measured), and the notes that say so.

All pictures are constructed at ten pixels to the millimetre (see vector_engine_support.py); the ones that need exact sizes are drawn without
smoothing, so that "a 0.4 mm dot" is exactly four pixels."""

from __future__ import annotations

import itertools
import math

import pytest

from shapely.geometry import box

from studio.vector import VectorError, VectorOptions, build_vector, engine, pixels
from studio.vector.colours import hex_to_rgb, rgb_to_lab

from vector_engine_support import SHEET_MM, as_png, bbox, layers_of, make, mm, picture

BLACK, RED, GREEN, BLUE, WHITE = (0, 0, 0), (255, 0, 0), (0, 120, 0), (0, 0, 255), (255, 255, 255)


def codes(result):
    return [note["code"] for note in result.check["notes"]]


def notes(result):
    return {note["code"]: note["message"] for note in result.check["notes"]}


def square(x, y, side, colour):
    """A square at (x, y) mm, `side` mm, as a picture shape."""
    return ("rect", (mm(x), mm(y), mm(x + side), mm(y + side)), colour)


def nested():
    """Three inks in contact: a red square 40 mm, a black disc in it, a blue square in the disc. By area: red, black, blue."""
    return [("rect", (mm(5), mm(5), mm(45), mm(45)), RED), ("ellipse", (mm(12), mm(12), mm(38), mm(38)), BLACK),
            ("rect", (mm(20), mm(20), mm(30), mm(30)), BLUE)]


# ---- the order of the layers (item 3) -------------------------------------------------------------------------------------------------
def test_layers_come_largest_ink_area_first_and_are_numbered_in_that_order():
    """Layer 1 is the one with the most ink: it is the bottom when stacked. Three squares of 30, 20 and 10 mm, drawn smallest first, so
    that the order cannot be the order of drawing, of position or of colour."""
    result = make([square(35, 35, 10, GREEN), square(20, 5, 20, BLUE), square(3, 3, 30, RED)], colours=4)
    assert [layer["id"] for layer in result.check["layers"]] == ["layer-1-red", "layer-2-blue", "layer-3-green"]
    areas = [layer.polygon().area for layer in layers_of(result.svg)]
    assert areas == sorted(areas, reverse=True)


def test_two_layers_of_the_same_area_are_ordered_darkest_first():
    """A tie goes to the darker colour (§31.12.3 item 3). Two squares of exactly the same size, hard edges so that the areas are equal to
    the pixel: black before red, and (red is lighter than blue is not true: blue, L 32, is darker than red, L 53) blue before red."""
    same = lambda c1, c2: make([square(5, 5, 15, c1), square(30, 5, 15, c2)], colours=3, smooth=1)
    assert [layer["colour"] for layer in same(RED, BLACK).check["layers"]] == ["#000000", "#FF0000"]
    assert [layer["colour"] for layer in same(RED, BLUE).check["layers"]] == ["#0000FF", "#FF0000"]
    assert [layer["colour"] for layer in same(BLACK, RED).check["layers"]] == ["#000000", "#FF0000"]  # not the order of drawing


def test_a_tie_is_a_tie_even_when_the_last_digit_of_a_float_says_otherwise():
    """Two squares 15 mm by 15 mm are equal, though the arithmetic of their corners can make one's area bigger by 6e-14 (here the red one's). The
    area is compared to a millionth of a square millimetre, so the darker colour still comes first: as layers and as pieces."""
    shapes = [("rect", (mm(32) + 3, mm(3), mm(32) + 153, mm(18)), BLACK), ("rect", (mm(18) - 2, mm(30), mm(18) + 148, mm(45)), RED)]
    assert [layer["colour"] for layer in make(shapes, colours=3, smooth=1).check["layers"]] == ["#000000", "#FF0000"]
    pieces = make(shapes, colours=3, smooth=1, layers_by="shape").check["layers"]
    assert [layer["picture_colour"] for layer in pieces] == ["#000000", "#FF0000"]


def test_areas_that_differ_by_a_few_hundredths_are_not_a_tie():
    """The order is by area first: a lighter layer that is bigger by 0.04 mm2 (a thousandth of its size) comes before a darker one."""
    dark, light = pixels.Ink(1, None, "#000000", 10, 0.0), pixels.Ink(2, None, "#FF0000", 10, 53.0)
    layers = [engine._Layer(ink=dark, geometry=box(0, 0, 10, 10)), engine._Layer(ink=light, geometry=box(0, 0, 10, 10.004))]
    assert [layer.ink.picture for layer in engine._order(layers)] == ["#FF0000", "#000000"]
    layers = [engine._Layer(ink=light, geometry=box(0, 0, 10, 10 + 1e-13)), engine._Layer(ink=dark, geometry=box(0, 0, 10, 10))]
    assert [layer.ink.picture for layer in engine._order(layers)] == ["#000000", "#FF0000"]


# ---- side by side (item 4) ----------------------------------------------------------------------------------------------------------------
def test_side_by_side_layers_hold_only_their_own_regions_and_do_not_overlap():
    """Criterion 137: each layer holds only its own colour's regions. Red has the hole where the black disc is, black the hole where the
    blue square is, and no two layers share any area (to a few hundredths of a square millimetre: the file is rounded to a micrometre)."""
    layers = layers_of(make(nested(), colours=4).svg)
    red, black, blue = (layer.polygon() for layer in layers)
    for a, b in itertools.combinations([red, black, blue], 2):
        assert a.intersection(b).area < 0.05
    assert not red.contains(black.representative_point()) and not black.contains(blue.representative_point())  # each is a ring round the next
    assert abs(red.area - (40 * 40 - math.pi * 13 * 13)) < 8 and abs(blue.area - 100) < 3


def test_side_by_side_leaves_no_gap_between_touching_inks():
    """Where two inks meet there is no hairline between their layers: together they cover the whole picture's ink (within 0.5%)."""
    layers = layers_of(make(nested(), colours=4).svg)
    union = layers[0].polygon().union(layers[1].polygon()).union(layers[2].polygon())
    assert abs(union.area - 1600) < 8 and union.geom_type == "Polygon" and len(union.interiors) == 0


def test_the_bleed_is_ignored_when_side_by_side():
    """The bleed is for stacked layers only: the same file with any bleed."""
    assert make(nested(), colours=4, bleed_mm=0).svg == make(nested(), colours=4, bleed_mm=5).svg


# ---- stacked with a bleed (item 4) ----------------------------------------------------------------------------------------------------
def test_stacked_layer_k_holds_its_own_regions_and_everything_above_it():
    """§31.12.3 item 4: with no bleed, layer 1 is the union of all three inks (the whole 40 mm square), layer 2 the black disc with the blue
    square (the disc) and layer 3 only the blue square; each one holds the ones after it."""
    layers = [layer.polygon() for layer in layers_of(make(nested(), colours=4, arrangement="stacked", bleed_mm=0).svg)]
    side = [layer.polygon() for layer in layers_of(make(nested(), colours=4).svg)]
    assert abs(layers[0].area - 1600) < 8 and abs(layers[1].area - (side[1].area + side[2].area)) < 0.2 and abs(layers[2].area - side[2].area) < 0.2
    for lower, upper in itertools.combinations(layers, 2):
        assert upper.difference(lower).area < 0.05  # everything above is inside everything below
    assert len(layers[0].interiors) == 0 or layers[0].geom_type == "Polygon"


def test_the_bleed_grows_every_stacked_layer_outward_by_exactly_that_much():
    """A 20 mm black square with a 6 mm red square on it, stacked with a bleed of 0.5 mm: layer 1 is 21 mm wide, the red layer 7 mm, centred
    where the unbled ones were; round corners, so the area is 440.8 and 36 + 2*6*... (checked on layer 1)."""
    shapes = [square(15, 15, 20, BLACK), square(22, 22, 6, RED)]
    plain = [bbox(layer) for layer in layers_of(make(shapes, colours=3, arrangement="stacked", bleed_mm=0, smooth=1).svg)]
    grown = layers_of(make(shapes, colours=3, arrangement="stacked", bleed_mm=0.5, smooth=1).svg)
    for before, layer in zip(plain, grown):
        after = bbox(layer)
        assert after == pytest.approx((before[0] - 0.5, before[1] - 0.5, before[2] + 0.5, before[3] + 0.5), abs=0.005)
    side = 20 + 1.0
    assert grown[0].polygon().area == pytest.approx(side * side - (4 - math.pi) * 0.25, abs=0.05)  # round corners of radius 0.5, not mitred


def test_the_bleed_is_cut_off_at_the_edge_of_the_sheet():
    """A shape that touches the sheet's edge is not grown beyond it: nothing in the file is off the sheet."""
    result = make([("rect", (0, 0, mm(20), mm(20)), BLACK), square(5, 5, 8, RED)], colours=3, arrangement="stacked", bleed_mm=2)
    for layer in layers_of(result.svg):
        x0, y0, x1, y1 = bbox(layer)
        assert x0 >= 0 and y0 >= 0 and x1 <= SHEET_MM and y1 <= SHEET_MM
    assert bbox(layers_of(result.svg)[0])[:2] == (0, 0)


def test_stacked_checks_the_paths_and_lengths_of_the_layers_as_written():
    """The check describes the stacked layers (the union with everything above), not the single colours: layer 1 is one path round the lot."""
    result = make(nested(), colours=4, arrangement="stacked", bleed_mm=0)
    assert result.check["arrangement"] == "stacked" and result.check["layers"][0]["paths"] == 1
    assert result.check["layers"][0]["length_mm"] == pytest.approx(160, abs=1.5)


# ---- kerf (item 7) -----------------------------------------------------------------------------------------------------------------------
def test_the_kerf_grows_every_layer_by_half_of_it():
    """Kerf compensation: a piece that is cut out is drawn half a kerf bigger on each side, and a hole half a kerf smaller, because the
    laser's own width takes it back. A 20 mm black ring with a 8 mm hole, kerf 0.3 mm: outline 20.3 mm, hole 7.7 mm. Off, nothing changes."""
    shapes = [("rect", (mm(15), mm(15), mm(35), mm(35)), BLACK), ("rect", (mm(21), mm(21), mm(29), mm(29)), WHITE)]
    plain = make(shapes, colours=2, smooth=1)
    kerf = make(shapes, colours=2, smooth=1, kerf_mm=0.3)
    outline_before, hole_before = layers_of(plain.svg)[0].paths[0].rings
    outline_after, hole_after = layers_of(kerf.svg)[0].paths[0].rings

    def width(ring):
        xs = [p[0] for p in ring]
        return max(xs) - min(xs)

    assert width(outline_after) - width(outline_before) == pytest.approx(0.3, abs=0.005)
    assert width(hole_before) - width(hole_after) == pytest.approx(0.3, abs=0.005)
    assert make(shapes, colours=2, smooth=1, kerf_mm=0).svg == plain.svg
    fine = layers_of(make(shapes, colours=2, smooth=1, kerf_mm=0.01).svg)[0].paths[0].rings[0]  # a hundredth of a millimetre: half is 5 micrometres
    assert width(fine) - width(outline_before) == pytest.approx(0.01, abs=0.0006)


def test_with_a_kerf_neighbouring_side_by_side_layers_overlap_by_the_kerf():
    """Each layer is cut as its own piece out of its own sheet, so each is grown; where two touch they overlap by a kerf. Without a kerf they
    do not overlap at all (tested above). This is the one place 'no two layers overlap' does not hold, by design."""
    shapes = [square(5, 10, 20, BLACK), square(25, 10, 20, RED)]
    a, b = (layer.polygon() for layer in layers_of(make(shapes, colours=3, smooth=1, kerf_mm=0.4).svg))
    assert a.intersection(b).area == pytest.approx(0.4 * 20, rel=0.1)


def test_a_kerf_never_puts_anything_off_the_sheet():
    """Growing a shape that touches the edge must not leave the sheet: on a 100 x 40 mm sheet a full-bleed bar with a 1 mm kerf is still exactly
    the sheet (the height counts, not only the width), and a bleed likewise."""
    for options in ({"kerf_mm": 1.0}, {"arrangement": "stacked", "bleed_mm": 2.0}):
        result = make([("rect", (0, 0, 500, 200), BLACK)], size=(500, 200), width_mm=100, height_mm=40, colours=2, **options)
        assert bbox(layers_of(result.svg)[0]) == (0, 0, 100, 40)


# ---- by shape: one layer for each separate piece (§31.7 answer 1b; item 2) ----------------------------------------------------------
def leaves(count, small=0):
    """`count` separate ellipses in a grid, all the same green: the first `small` are 2 mm across, the rest 4 mm, each a little different
    in height so that no two have the same area (the order is then the order of size, not of position)."""
    shapes = []
    for i in range(count):
        column, row = i % 7, i // 7
        w, h = (2.0, 2.0 + 0.01 * i) if i < small else (4.0, 3.0 + 0.02 * i)
        shapes.append(("ellipse", (mm(1 + column * 7), mm(1 + row * 7), mm(1 + column * 7 + w), mm(1 + row * 7 + h)), GREEN))
    return shapes


def test_by_shape_each_separate_piece_is_a_layer_with_a_label_colour():
    """Seven leaves are seven layers, `piece-1` to `piece-7`, largest first. A layer's colour is a label from the fixed list (the picture's
    own colour would be the same green seven times) and `picture_colour` says what the picture had."""
    result = make(leaves(7), colours=2, layers_by="shape")
    check = result.check
    assert len(check["layers"]) == 7 and check["layers_by"] == "shape"
    assert [layer["id"] for layer in check["layers"]] == [f"layer-{n}-piece-{n}" for n in range(1, 8)]
    assert [layer["name"] for layer in check["layers"]] == [f"Layer {n} (piece-{n})" for n in range(1, 8)]
    assert [layer["colour"] for layer in check["layers"]] == list(engine.SHAPE_COLOURS[:7])
    assert {layer["picture_colour"] for layer in check["layers"]} == {"#007800"} and all(layer["paths"] == 1 for layer in check["layers"])
    areas = [layer.polygon().area for layer in layers_of(result.svg)]
    assert [round(a, 3) for a in areas] == sorted((round(a, 3) for a in areas), reverse=True) and check["colours_found"] == 2  # ties are within a hair


def test_by_shape_is_always_side_by_side():
    """Pieces are separate by construction, so the arrangement is not offered by shape: asking for stacked with a bleed gives the very same file
    as side by side, and the check says what was done."""
    stacked = make(leaves(5), colours=2, layers_by="shape", arrangement="stacked", bleed_mm=2)
    assert stacked.svg == make(leaves(5), colours=2, layers_by="shape").svg and stacked.check["arrangement"] == "side_by_side"


def test_by_shape_stops_at_the_cap_and_says_so():
    """35 pieces with the default cap of 30: the 30 largest have a layer, the five smallest (2 mm) are left out, and a `layers_capped` note says
    how many of each. A smaller cap is honoured, and exactly 30 pieces are not capped."""
    result = make(leaves(35, small=5), colours=2, layers_by="shape")
    assert len(result.check["layers"]) == 30
    message = notes(result)["layers_capped"]
    assert "35" in message and "30" in message and "5" in message
    assert min(bbox(layer)[2] - bbox(layer)[0] for layer in layers_of(result.svg)) > 3.8  # none of the 2 mm ones is there
    three = make(leaves(35, small=5), colours=2, layers_by="shape", shape_layer_cap=3)
    assert len(three.check["layers"]) == 3 and "layers_capped" in codes(three)
    assert "layers_capped" not in codes(make(leaves(30), colours=2, layers_by="shape"))


def test_the_thirty_label_colours_are_distinct_and_none_is_white():
    """A laser program tells layers apart by colour, so the fixed list must be 30 colours that really differ: none closer than 17 in Lab,
    none white, all written as uppercase #RRGGBB."""
    colours = engine.SHAPE_COLOURS
    assert len(colours) == 30 == len(set(colours))
    labs = [rgb_to_lab(hex_to_rgb(c)) for c in colours]
    assert min(math.dist(a, b) for a, b in itertools.combinations(labs, 2)) >= 17
    assert min(math.dist(lab, (100, 0, 0)) for lab in labs) > 20
    assert all(c == c.upper() and len(c) == 7 for c in colours)


def test_a_ring_is_one_piece_and_a_dot_inside_it_is_another():
    """A piece is a shape with its holes: the ring is one layer (holes are not pieces), the dot in its middle another; and pieces of two
    different inks are pieces too, each keeping the picture colour it came from."""
    shapes = [("ellipse", (mm(5), mm(5), mm(35), mm(35)), BLACK), ("ellipse", (mm(12), mm(12), mm(28), mm(28)), WHITE),
              ("ellipse", (mm(18), mm(18), mm(22), mm(22)), BLACK), square(38, 38, 8, RED)]
    result = make(shapes, colours=3, layers_by="shape")
    assert len(result.check["layers"]) == 3
    assert [layer["picture_colour"] for layer in result.check["layers"]] == ["#000000", "#FF0000", "#000000"]
    assert [len(layer.paths[0].rings) for layer in layers_of(result.svg)] == [2, 1, 1]


# ---- the smallest feature (item 6; criterion 139) -----------------------------------------------------------------------------------------
def dots(*sizes_px):
    """A big black square and, in a row below it, hard-edged black dots of the given sizes in pixels."""
    shapes = [square(5, 5, 20, BLACK)]
    for i, size in enumerate(sizes_px):
        shapes.append(("rect", (mm(5) + i * 100, mm(35), mm(5) + i * 100 + size, mm(35) + size), BLACK))
    return shapes


def test_a_speck_smaller_than_the_smallest_feature_is_removed_and_counted():
    """A 0.4 mm dot (4 pixels) cannot be cut with a 0.5 mm smallest feature: it is gone from the file, counted in `removed.specks`, and a
    `specks_removed` note says how many. A 1.2 mm dot stays."""
    result = make(dots(4, 12), colours=2, smooth=1)
    assert len(layers_of(result.svg)[0].paths) == 2  # the big square and the 1.2 mm dot
    assert result.check["removed"] == {"specks": 1, "thin_parts": 0}
    assert "1 speck" in notes(result)["specks_removed"] and "0.5 mm" in notes(result)["specks_removed"]
    assert "specks_removed" not in codes(make(dots(12), colours=2, smooth=1))


def test_noise_of_a_pixel_or_two_is_cleaned_without_being_counted():
    """The soft-edge clean-up (a window of up to three pixels) takes away one and two pixel specks before they are ever traced; they are not
    counted as specks, which are the pieces the picture really had that are too small."""
    result = make(dots(1, 2, 2), colours=2, smooth=1)
    assert len(layers_of(result.svg)[0].paths) == 1 and result.check["removed"]["specks"] == 0 and "specks_removed" not in codes(result)


def bridge(neck_px):
    """Two 15 mm blocks joined by a neck `neck_px` pixels wide and 4 mm long."""
    return [square(5, 15, 15, BLACK), ("rect", (mm(20), mm(22), mm(24), mm(22) + neck_px), BLACK), square(24, 15, 15, BLACK)]


def test_a_neck_narrower_than_the_smallest_feature_is_removed_and_the_blocks_are_two_pieces():
    """A 0.3 mm neck between two blocks would not survive being cut: it is cut away (the file has two paths), counted as a thin part, and a
    `small_feature` note says so. The smallest feature that remains is then the blocks' own size."""
    result = make(bridge(3), colours=2, smooth=1)
    assert len(layers_of(result.svg)[0].paths) == 2
    assert result.check["removed"] == {"specks": 0, "thin_parts": 1}
    assert "1 thin part" in notes(result)["small_feature"] and result.check["smallest_feature_mm"] > 10


def test_a_neck_wide_enough_stays_and_is_the_smallest_feature_reported():
    """A 1 mm neck stays: the two blocks are one path, no note, and `smallest_feature_mm` is the neck's 1.0 mm (to a tenth of a millimetre)."""
    result = make(bridge(10), colours=2, smooth=1)
    assert len(layers_of(result.svg)[0].paths) == 1 and "small_feature" not in codes(result)
    assert result.check["smallest_feature_mm"] == pytest.approx(1.0, abs=0.1)


@pytest.mark.parametrize("width_mm_", [0.8, 2.0, 6.0])
def test_the_smallest_feature_is_measured_from_the_shapes(width_mm_):
    """A bar `width` wide, 30 mm long: the check reports its width, whatever the width, because the measure is of the shapes. The search
    narrows down on the width from above, so the answer is never below it and within 2.5% over it."""
    px = round(width_mm_ * 10)
    result = make([("rect", (mm(5), mm(20), mm(35), mm(20) + px), BLACK)], colours=2, smooth=1)
    assert width_mm_ <= result.check["smallest_feature_mm"] <= width_mm_ * 1.025


def test_the_smallest_feature_option_decides_what_is_removed():
    """The same 2 mm bar: kept at 1 mm, and with a 3 mm smallest feature there is nothing left, which is an error with words for the card."""
    bar = [("rect", (mm(5), mm(20), mm(35), mm(22)), BLACK)]
    assert len(make(bar, colours=2, smooth=1, min_feature_mm=1.0).check["layers"]) == 1
    with pytest.raises(VectorError) as caught:
        make(bar, colours=2, smooth=1, min_feature_mm=3.0)
    assert "3 mm" in caught.value.message and caught.value.hint


def test_a_gap_narrower_than_the_smallest_feature_is_flagged_and_kept():
    """Closing a 0.3 mm gap would change the drawing, so it is left as drawn and flagged: two blocks stay two paths, a `small_feature` note
    says a gap may not cut cleanly, and the smallest feature reported is the gap's 0.3 mm, below the 0.5 that was asked."""
    shapes = [square(5, 15, 18, BLACK), ("rect", (mm(23) + 3, mm(15), mm(41) + 3, mm(33)), BLACK)]
    result = make(shapes, colours=2, smooth=1)
    assert len(layers_of(result.svg)[0].paths) == 2 and "gap" in notes(result)["small_feature"]
    assert result.check["smallest_feature_mm"] == pytest.approx(0.3, abs=0.1)


def test_a_hole_too_small_to_cut_is_filled_and_a_bigger_one_stays():
    """A 0.4 mm hole in a block is a speck of bare material: it is filled (counted as a speck); a 0.8 mm hole stays a hole."""
    def block(hole_px):
        return [square(10, 10, 25, BLACK), ("rect", (mm(20), mm(20), mm(20) + hole_px, mm(20) + hole_px), WHITE)]

    small, big = make(block(4), colours=2, smooth=1), make(block(8), colours=2, smooth=1)
    assert len(layers_of(small.svg)[0].paths[0].rings) == 1 and small.check["removed"]["specks"] == 1
    assert len(layers_of(big.svg)[0].paths[0].rings) == 2 and big.check["removed"]["specks"] == 0


def test_a_slit_that_is_a_hole_is_filled_and_is_not_also_reported_as_a_gap():
    """A hole 0.3 mm wide and 5 mm long inside a block is a speck of bare material: filled and counted once, as a speck. It is not also a
    'gap narrower than the smallest feature' (the gaps are the ones between parts, and are kept), so there is no `small_feature` note."""
    block = [square(10, 10, 25, BLACK), ("rect", (mm(20), mm(20), mm(20) + 3, mm(25)), WHITE)]
    result = make(block, colours=2, smooth=1)
    assert len(layers_of(result.svg)[0].paths[0].rings) == 1 and result.check["removed"]["specks"] == 1
    assert "small_feature" not in codes(result)


def test_a_drawing_too_coarse_for_the_smallest_feature_says_so():
    """`coarse_drawing` when the smallest feature is under two pixels of the drawing (here a pixel is 0.1 mm, so under 0.2 mm), and not at
    0.2 mm or more. A drawing at the real size, 1536 pixels for 100 mm, has a pixel of 0.065 mm, so it is under 0.13 mm."""
    bar = [("rect", (mm(5), mm(20), mm(35), mm(24)), BLACK)]
    assert "coarse_drawing" in codes(make(bar, colours=2, smooth=1, min_feature_mm=0.15))
    assert "coarse_drawing" not in codes(make(bar, colours=2, smooth=1, min_feature_mm=0.2))
    assert "coarse_drawing" not in codes(make(bar, colours=2, smooth=1))
    assert make(bar, colours=2, smooth=1, min_feature_mm=0.15).check["mm_per_pixel"] == 0.1


def test_the_middle_of_an_o_is_flagged_as_a_hanging_island():
    """§31.5 step 4: a ring cut as a stencil drops its middle out. A ring has one such piece, a disc none, a disc with two windows two."""
    ring = [("ellipse", (mm(10), mm(10), mm(40), mm(40)), BLACK), ("ellipse", (mm(18), mm(18), mm(32), mm(32)), WHITE)]
    assert "1 piece of material" in notes(make(ring, colours=2))["hanging_island"]
    assert "hanging_island" not in codes(make([ring[0]], colours=2))
    two = [ring[0], ("ellipse", (mm(15), mm(15), mm(21), mm(21)), WHITE), ("ellipse", (mm(29), mm(29), mm(35), mm(35)), WHITE)]
    assert "2 pieces of material" in notes(make(two, colours=2))["hanging_island"]


def test_the_smallest_feature_looks_at_every_layer_and_never_beyond_the_sheets_own_height():
    """The narrowest part may be in any layer, not only the first: a 30 mm black square with a 0.8 mm red bar gives 0.8. And nothing is wider
    than the sheet is tall: a bar across a 100 x 40 mm sheet is 40 mm wide, which is exactly what is reported (not 100, and not a search's
    near miss)."""
    result = make([square(5, 5, 30, BLACK), ("rect", (mm(5), mm(40), mm(35), mm(40) + 8), RED)], colours=3, smooth=1)
    assert 0.8 <= result.check["smallest_feature_mm"] <= 0.82
    across = make([("rect", (0, 0, 500, 200), BLACK)], size=(500, 200), width_mm=100, height_mm=39.7, colours=2, smooth=1)
    assert across.check["smallest_feature_mm"] == 39.7  # exactly: the search stops at the sheet, it does not creep up on it


def test_the_clean_up_is_given_half_the_smallest_feature_and_a_quarter_of_a_pixel(monkeypatch):
    """The two numbers every smallest-feature decision rests on, read off the call: r is half the smallest feature (0.25 mm for 0.5) and
    `eps_area` a quarter of a pixel in square millimetres (0.1 mm a pixel here: 0.0025 mm2)."""
    seen = []
    real = engine.geo.clean_layer
    monkeypatch.setattr(engine.geo, "clean_layer", lambda geometry, r, eps: (seen.append((r, eps)), real(geometry, r, eps))[1])
    make([square(5, 5, 20, BLACK)], colours=2)
    assert seen == [(pytest.approx(0.25), pytest.approx(0.0025))]


def test_a_colour_whose_every_part_is_too_small_has_no_layer_and_is_named():
    """A red line 0.3 mm wide (three pixels: it passes the soft-edge clean-up, which takes only up to two) beside a big black square: all of the
    red is narrower than the smallest feature, so the red has no layer, and `empty_layer` names it (by the colour the user asked for if it was
    named, else by the picture's own)."""
    shapes = [square(5, 5, 20, BLACK), ("rect", (mm(30), mm(5), mm(30) + 3, mm(45)), RED)]
    result = make(shapes, colours=3, smooth=1)
    assert [layer["colour"] for layer in result.check["layers"]] == ["#000000"]
    message = notes(result)["empty_layer"]
    assert message.startswith("#FF0000 had nothing left to cut") and message.endswith("so it has no layer.")
    named = make(shapes, colours=3, smooth=1, palette=("#FF1111",))
    assert notes(named)["empty_layer"].startswith("#FF1111 had nothing left")


def test_the_notes_are_worded_for_one_and_for_several():
    """One speck 'was' removed, two 'were'; one missing colour 'is' not in the picture, two 'are' and 'they' have no layer; one piece left out
    by the cap is 'one', and the coarse note names the pixel and what it can show."""
    two_dots = make(dots(4, 4), colours=2, smooth=1)
    assert notes(two_dots)["specks_removed"] == "2 specks smaller than 0.5 mm were removed."
    assert notes(make(dots(4), colours=2, smooth=1))["specks_removed"] == "1 speck smaller than 0.5 mm was removed."
    missing = make([square(10, 10, 30, RED)], colours=4, palette=("#FF0000", "#0000FF", "#00FF00"))
    assert notes(missing)["empty_layer"] == "#0000FF, #00FF00 are not in the picture, so they have no layer."
    one = make([square(10, 10, 30, RED)], colours=3, palette=("#FF0000", "#0000FF"))
    assert notes(one)["empty_layer"] == "#0000FF is not in the picture, so it has no layer."
    capped = make(leaves(31, small=1), colours=2, layers_by="shape")
    assert "(1 smaller one was left out)" in notes(capped)["layers_capped"] and "only the 30 largest" in notes(capped)["layers_capped"]
    assert "(5 smaller ones were left out)" in notes(make(leaves(35, small=5), colours=2, layers_by="shape"))["layers_capped"]
    coarse = make([("rect", (mm(5), mm(20), mm(35), mm(24)), BLACK)], colours=2, smooth=1, min_feature_mm=0.15)
    assert notes(coarse)["coarse_drawing"].startswith("The drawing is too coarse to show a feature as small as 0.15 mm: one pixel is 0.100 mm, "
                                                      "so about 0.20 mm is the smallest it can show.")


def test_a_dot_a_pixel_or_two_across_is_a_feature_when_the_smallest_feature_is_under_a_pixel():
    """With a smallest feature of 0.05 mm (half a pixel here) nothing a pixel can show is too small: dots two and three pixels square, beside
    the big square, are kept (the quarter-of-a-pixel rule is about rounding, not about pixels). The tracer itself cannot draw anything one
    pixel wide, which is why the smallest dot is two."""
    result = make(dots(2, 3), colours=2, smooth=1, min_feature_mm=0.05)
    assert len(layers_of(result.svg)[0].paths) == 3 and result.check["removed"]["specks"] == 0


def test_shapes_are_simplified_to_a_tolerance_of_a_quarter_pixel():
    """A tracer's shape with a redundant point in the middle of each long side, 0.02 mm off the straight line (under a quarter of the 0.1 mm
    pixel) comes out with four corners; one 0.03 mm off (over it) keeps its points. This is the one place the engine's own tolerance shows."""
    from shapely.geometry import Polygon

    def corners_of(offset, monkeypatch):
        monkeypatch.setattr(engine.geo, "trace_ink", lambda *args, **kwargs: Polygon([(5, 5), (15, 5 + offset), (25, 5), (25, 25), (15, 25 + offset), (5, 25)]))
        result = make([square(5, 5, 20, BLACK)], colours=2)
        return len(layers_of(result.svg)[0].paths[0].rings[0])

    with pytest.MonkeyPatch.context() as patch:
        assert corners_of(0.02, patch) == 4
    with pytest.MonkeyPatch.context() as patch:
        assert corners_of(0.03, patch) == 6


# ---- options and failures (item 9) ---------------------------------------------------------------------------------------------------------
def test_options_that_cannot_be_used_are_a_message_for_the_card():
    """The engine checks its options with the contract's `check_options` and names the field, as the server's 422 does."""
    with pytest.raises(VectorError) as caught:
        build_vector(as_png(picture([square(5, 5, 20, BLACK)])), VectorOptions(colours=1))
    assert "colours" in caught.value.message


def test_a_library_failing_is_a_message_for_the_card_not_a_traceback(monkeypatch):
    """§31.12.3 item 9: whatever a library raises, the card gets a VectorError with words; the library's own error is kept as the cause."""
    import vtracer

    def boom(*args, **kwargs):
        raise RuntimeError("the tracer fell over")

    monkeypatch.setattr(vtracer, "convert_raw_image_to_svg", boom)
    with pytest.raises(VectorError) as caught:
        make([square(5, 5, 20, BLACK)], colours=2)
    assert caught.value.message and caught.value.hint and isinstance(caught.value.__cause__, RuntimeError)


def test_running_out_of_memory_is_not_turned_into_a_message(monkeypatch):
    """A MemoryError is the process's problem and the server reports it as a crash: it must not be dressed up as a bad picture."""
    import vtracer

    def boom(*args, **kwargs):
        raise MemoryError

    monkeypatch.setattr(vtracer, "convert_raw_image_to_svg", boom)
    with pytest.raises(MemoryError):
        make([square(5, 5, 20, BLACK)], colours=2)


def test_missing_libraries_are_a_message_for_the_card(monkeypatch):
    """When a library is not installed, build_vector says which, with the hint of `available()`, and does not raise ImportError."""
    import studio.vector as vector

    monkeypatch.setattr(vector, "available", lambda: (False, "The Vector libraries are not installed here (shapely).", "Rebuild."))
    with pytest.raises(VectorError) as caught:
        vector.build_vector(b"x", VectorOptions())
    assert "shapely" in caught.value.message and caught.value.hint == "Rebuild."
