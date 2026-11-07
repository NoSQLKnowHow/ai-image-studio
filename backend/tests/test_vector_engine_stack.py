"""Which colour is the back sheet of a stacked Vector run, and how many sheets high each colour stands (DESIGN.md §35.3 items 11 and 12,
§35.4, criteria 205, 206 and 209).

The stacked arrangement itself (layer k holds its own regions and everything in the layers above it, grown by the bleed) is tested in
test_vector_engine_layout.py. What is new in 1.18 is the choice of the order (`stack_order`: the most ink at the back, the least, or the first
ink of the form's list), the `height` that the check gives each layer, and the rule that the choice only counts for a run stacked by colour.

The pictures are constructed at ten pixels to the millimetre (see vector_engine_support.py) and drawn without smoothing, so that an area is exact
to the pixel and "the front sheet is the black ring" is something a test can measure rather than eyeball."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from studio.vector import STACK_ORDERS, STACK_ORDER_DEFAULT, VectorOptions, check_options

from vector_engine_support import SHEET_MM, layers_of, make, mm

BLACK, RED, GREEN, BLUE, ORANGE, PURPLE = (0, 0, 0), (255, 0, 0), (0, 120, 0), (0, 0, 255), (255, 165, 0), (128, 0, 128)


def disc_square_dot():
    """The picture the spec measured (§35.1): a black disc, a red square on it, a green dot on the square. By ink area: black (the ring), red (the
    square without the dot), green (the dot), so the default order is black, red, green and a stacked black layer is the whole disc."""
    return [("ellipse", (mm(7), mm(7), mm(43), mm(43)), BLACK), ("rect", (mm(18), mm(18), mm(32), mm(32)), RED),
            ("ellipse", (mm(22), mm(22), mm(28), mm(28)), GREEN)]


def stacked(shapes, **options):
    """The real clean-up, stacked by colour with no bleed (so that a sheet's area is the sum of the colours it holds), hard edges."""
    return make(shapes, smooth=1, arrangement="stacked", bleed_mm=0, colours=4, **options)


def ids(result):
    return [layer["id"] for layer in result.check["layers"]]


def areas(result):
    """The area each sheet encloses, read from the file: every path's outline minus the holes cut in it (a ring after the first is a hole, which
    holds for these pictures, where nothing is nested more than one deep). The support module's `polygon()` combines the rings with a geometry
    operation that fails on an outline touching a hole's corner, which a hard-edged picture can make; adding up areas has no such failure."""
    return [sum(Polygon(path.rings[0]).area - sum(Polygon(ring).area for ring in path.rings[1:]) for path in layer.paths)
            for layer in layers_of(result.svg)]


# ---- the default is today's rule -------------------------------------------------------------------------------------------------------
def test_the_default_order_is_the_most_ink_at_the_back_and_changes_nothing_from_1_17():
    """`area_desc` is what 1.17 did, and it is the default, so a run made without the option gives the very same file as one that asks for it.
    (A regression here would change every stacked file people already made.)"""
    assert STACK_ORDER_DEFAULT == "area_desc" and STACK_ORDERS == ("area_desc", "area_asc", "palette")
    plain = stacked(disc_square_dot())
    assert ids(plain) == ["layer-1-black", "layer-2-red", "layer-3-green"]
    assert plain.svg == stacked(disc_square_dot(), stack_order="area_desc").svg
    assert plain.check["stack_order"] == "area_desc"


def test_the_sheets_are_cumulative_so_each_colour_stands_as_high_as_its_number():
    """The point of the arrangement: layer k is its own regions plus everything in front of it, so with three colours the black ring is one sheet
    high, the red square two and the green dot three. Measured with no bleed: each sheet's area is the sum of the colours from k to the front."""
    result = stacked(disc_square_dot())
    assert [layer["height"] for layer in result.check["layers"]] == [1, 2, 3]
    back, middle, front = areas(result)
    assert back > middle > front
    # the front sheet is the dot alone; the middle one the dot and the red square without its dot; the back one the whole disc
    disc = back
    assert front == pytest.approx(mm(6) * mm(6) * 0.7854 / 100, rel=0.2)  # a 6 mm dot: about 28 square millimetres
    assert middle == pytest.approx(14 * 14, rel=0.02)  # the red square, which includes the dot on it
    assert disc == pytest.approx(3.14159 * 18 * 18, rel=0.02)  # the 36 mm disc


def test_six_colours_make_six_levels_of_depth():
    """What the owner asked for in words: "if you have six layers, they stack one on top of the other to create 6 layers of depth." Six nested
    squares in six colours: the heights are 1 to 6, each sheet is smaller than the one behind it, and the front sheet is the innermost square."""
    sizes = [(40, BLACK), (34, RED), (28, GREEN), (22, BLUE), (16, ORANGE), (10, PURPLE)]
    shapes = [("rect", (mm(25 - side / 2), mm(25 - side / 2), mm(25 + side / 2), mm(25 + side / 2)), colour) for side, colour in sizes]
    result = make(shapes, smooth=1, arrangement="stacked", bleed_mm=0, colours=7)
    assert [layer["height"] for layer in result.check["layers"]] == [1, 2, 3, 4, 5, 6]
    sheets = areas(result)
    assert sheets == sorted(sheets, reverse=True) and len(set(round(a) for a in sheets)) == 6
    assert sheets[-1] == pytest.approx(10 * 10, rel=0.02)  # the front sheet is the 10 mm square alone
    assert sheets[0] == pytest.approx(40 * 40, rel=0.02)  # the back sheet is the whole design


# ---- the other two orders --------------------------------------------------------------------------------------------------------------
def test_area_asc_puts_the_least_ink_at_the_back():
    """The reverse of the default: the green dot is the back sheet (and so holds the whole design), the black ring is the front sheet. The sheets
    are cumulative whichever way round, so the back one is still the whole disc and the sheets still get smaller towards the front."""
    default = stacked(disc_square_dot())
    reverse = stacked(disc_square_dot(), stack_order="area_asc")
    assert ids(reverse) == ["layer-1-green", "layer-2-red", "layer-3-black"]
    assert [layer["height"] for layer in reverse.check["layers"]] == [1, 2, 3]
    assert reverse.check["stack_order"] == "area_asc"
    back, middle, front = areas(reverse)
    assert back > middle > front
    assert back == pytest.approx(areas(default)[0], rel=0.001)  # the whole design is on the back sheet whatever the order
    assert front == pytest.approx(36 * 36 * 0.7854 - 14 * 14, rel=0.03)  # the front sheet is the black ring alone


def test_palette_order_puts_the_first_ink_of_the_form_at_the_back_then_the_rest_in_order_then_the_picture_s_inks():
    """`palette` follows the form's list: red first, then black, and green (which the picture chose, not the form) after them."""
    result = stacked(disc_square_dot(), stack_order="palette", palette=("#FF0000", "#000000"))
    assert ids(result) == ["layer-1-red", "layer-2-black", "layer-3-green"]
    assert result.check["stack_order"] == "palette"


def test_palette_order_with_one_ink_listed_puts_it_at_the_back_and_the_rest_by_area():
    """The inks the form does not list keep their default order (the biggest first), after the listed ones."""
    result = stacked(disc_square_dot(), stack_order="palette", palette=("#00A000",))
    assert ids(result)[0].startswith("layer-1-")
    # the unlisted inks follow in area order: black before red (green is the listed one, if the picture's green matched it)
    names = [i.split("-", 2)[2] for i in ids(result)]
    assert names.index("black") < names.index("red")


def test_changing_the_order_never_changes_a_layer_s_colour():
    """Each ink keeps its exact colour whatever is at the back; only the numbers and the nesting change. Compared by colour name."""
    by_order = {}
    for order in STACK_ORDERS:
        result = stacked(disc_square_dot(), stack_order=order)
        by_order[order] = {layer["id"].split("-", 2)[2]: layer["colour"] for layer in result.check["layers"]}
    assert by_order["area_desc"] == by_order["area_asc"] == by_order["palette"]


# ---- where the choice does not count (§35.3 item 12) -------------------------------------------------------------------------------------
@pytest.mark.parametrize("order", ["area_asc", "palette"])
def test_side_by_side_ignores_the_order_and_every_layer_stands_one_high(order):
    """Side by side, each layer holds only its own colour, so there is no back sheet to choose: the same file, `stack_order` null, height 1."""
    plain = make(disc_square_dot(), smooth=1, colours=4)
    chosen = make(disc_square_dot(), smooth=1, colours=4, stack_order=order)
    assert chosen.svg == plain.svg
    assert chosen.check["stack_order"] is None
    assert [layer["height"] for layer in chosen.check["layers"]] == [1, 1, 1]


def test_by_shape_ignores_the_order_too():
    """Layers by shape are always side by side (§31.12.3 item 4), so a stacked request with an order gives the file it always gave."""
    plain = make(disc_square_dot(), smooth=1, colours=4, layers_by="shape")
    chosen = make(disc_square_dot(), smooth=1, colours=4, layers_by="shape", arrangement="stacked", stack_order="area_asc", bleed_mm=2)
    assert chosen.svg == plain.svg and chosen.check["stack_order"] is None
    assert all(layer["height"] == 1 for layer in chosen.check["layers"])


# ---- the option itself -------------------------------------------------------------------------------------------------------------------
def test_an_unknown_order_is_refused_by_name():
    """The server turns the engine's check into a 422 that names the field (§35.4), so the wording and the name are the engine's."""
    errors = check_options(VectorOptions(stack_order="loudest"))  # type: ignore[arg-type]
    assert errors == [("stack_order", "Must be one of: area_desc, area_asc, palette.")]
    assert check_options(VectorOptions(stack_order="palette")) == []


def test_a_1_17_options_file_without_the_order_means_the_default():
    """The job is given its options as JSON. A run queued by 1.17 and cleaned up by 1.18 has no `stack_order` in it, and must still load, as the
    default; and what 1.18 writes round-trips."""
    data = VectorOptions().to_json()
    assert data["stack_order"] == "area_desc"
    del data["stack_order"]
    assert VectorOptions.from_json(data).stack_order == "area_desc"
    assert VectorOptions.from_json(VectorOptions(stack_order="area_asc").to_json()).stack_order == "area_asc"


def test_the_sheet_constant_this_file_relies_on_is_the_one_the_support_module_uses():
    """The expected areas above are written for a 50 mm sheet; if the support module's scale ever changes this file must be rewritten, so say so."""
    assert SHEET_MM == 50
