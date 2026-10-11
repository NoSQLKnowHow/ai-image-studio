"""The colour side of the Vector clean-up (DESIGN.md §31.12.3 items 1, 2 and 3; §31.8 criterion 136): the number of colours is enforced by
the studio and not requested of the model, white is the bare material and never a layer, every layer has an exact colour that no other
layer has, the palette is honoured, and what a picture's soft edges would add (a thin third colour between two inks) is removed.

The pictures are constructed (shapes with anti-aliased edges, like a real drawing), so every expectation is known in advance."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from studio.vector import VectorError, VectorOptions, build_vector, colour_name
from studio.vector import engine, pixels
from studio.vector.colours import COLOUR_NAMES, hex_to_rgb, rgb_to_lab

from vector_engine_support import SIZE, SHEET_MM, as_png, bbox, layers_of, make, mm, picture

BLACK, RED, GREEN, BLUE = (0, 0, 0), (255, 0, 0), (0, 160, 0), (0, 0, 255)


def three_inks():
    """A black disc, a red square and a green triangle on white: three inks, so four colours with white. By area: red 324 mm2, black 314,
    green 180, which is also the order the layers come in."""
    return [("ellipse", (mm(3), mm(3), mm(23), mm(23)), BLACK), ("rect", (mm(28), mm(3), mm(46), mm(21)), RED),
            ("poly", [(mm(25), mm(46)), (mm(45), mm(46)), (mm(35), mm(28))], GREEN)]


def codes(result):
    return [note["code"] for note in result.check["notes"]]


# ---- exactly N colours, white included (item 1; criterion 136) -------------------------------------------------------------------------
def test_four_colours_make_three_layers_in_the_exact_colours_of_the_picture():
    """White counts toward the number (§31.7 answer 2b): 4 colours is three inks. The colours are the picture's own, to the digit, though
    every edge of the picture is blurred with in-between colours, and the layers come largest first."""
    result = make(three_inks(), colours=4)
    check = result.check
    assert [layer["colour"] for layer in check["layers"]] == ["#FF0000", "#000000", "#00A000"]
    assert [layer["picture_colour"] for layer in check["layers"]] == ["#FF0000", "#000000", "#00A000"]
    assert (check["colours_asked"], check["colours_found"], check["white"]) == (4, 4, True)
    assert "fewer_colours" not in codes(result) and "empty_layer" not in codes(result)


@pytest.mark.parametrize("asked", [2, 3, 4])
def test_a_picture_with_more_colours_than_asked_is_reduced_to_exactly_that_many(asked):
    """The number is a limit the clean-up enforces: a drawing of three inks asked for 2 colours (one ink) or 3 (two) is merged down, never
    left with more layers than asked, and the check says it found exactly what was asked."""
    result = make(three_inks(), colours=asked)
    assert len(result.check["layers"]) == asked - 1
    assert result.check["colours_found"] == asked and "fewer_colours" not in codes(result)
    assert len(layers_of(result.svg)) == asked - 1  # and the file agrees with the check


def test_a_picture_with_fewer_colours_than_asked_makes_fewer_layers_and_says_so():
    """§31.10 item 1: at most N, not exactly N. Asking for 8 colours of a three-ink drawing gives three layers, `colours_found` 4 (white
    included) and a `fewer_colours` note in plain words, never an empty fourth layer."""
    result = make(three_inks(), colours=8)
    assert len(result.check["layers"]) == 3 and result.check["colours_found"] == 4 and result.check["colours_asked"] == 8
    message = {note["code"]: note["message"] for note in result.check["notes"]}["fewer_colours"]
    assert "4 colours" in message and "8" in message and "3 layers" in message


def test_the_note_about_fewer_colours_does_not_claim_white_when_the_picture_has_none():
    """A picture that fills the whole sheet (a red background, a black disc) has no bare material: the check says so, and the note does not
    say white counts as one of its colours."""
    result = make([("rect", (0, 0, SIZE, SIZE), RED), ("ellipse", (mm(10), mm(10), mm(40), mm(40)), BLACK)], colours=4)
    assert result.check["white"] is False and result.check["colours_found"] == 2 and len(result.check["layers"]) == 2
    message = {note["code"]: note["message"] for note in result.check["notes"]}["fewer_colours"]
    assert "no white" in message and "white counts" not in message


def test_soft_edges_never_make_a_layer_of_their_own():
    """The classic failure of a colour reduction: the in-between colours along an edge (dark red between black and red, grey between black
    and white) get a cluster of their own, and the file has a thin outline layer round everything. A black disc beside a red square, asked
    for 6 colours, must give exactly those two layers."""
    result = make(three_inks()[:2], colours=6)
    assert sorted(layer["colour"] for layer in result.check["layers"]) == ["#000000", "#FF0000"]
    assert result.check["colours_found"] == 3 and "fewer_colours" in codes(result)
    assert "empty_layer" not in codes(result)  # the phantom clusters were never a colour the user asked for


def test_ink_touching_ink_has_no_third_colour_between_them():
    """Two inks side by side, with the blurred seam of a real picture between them (dark red where black meets red): the seam is shared
    between the two, no layer is made of it, and nothing is left between the two layers as a gap."""
    result = make([("rect", (mm(5), mm(5), mm(25), mm(45)), BLACK), ("rect", (mm(25), mm(5), mm(45), mm(45)), RED)], colours=5)
    layers = layers_of(result.svg)
    assert len(layers) == 2
    union = layers[0].polygon().union(layers[1].polygon())
    assert abs(union.area - 40 * 40) < 0.5 and union.geom_type == "Polygon"  # one unbroken 40 x 40 mm area, no seam between


def test_a_region_that_touches_the_edge_of_the_picture_is_not_eaten_by_it():
    """A black bar across the full width is one path whose box is the whole width: the edge of the picture is not an edge of the drawing."""
    result = make([("rect", (0, mm(20), SIZE, mm(30)), BLACK)], colours=2)
    layer = layers_of(result.svg)[0]
    x0, y0, x1, y1 = bbox(layer)
    assert len(layer.paths) == 1 and (x0, x1) == (0, SHEET_MM) and abs(y0 - 20) < 0.25 and abs(y1 - 30) < 0.25


def test_a_small_ink_is_found_before_the_noise_in_a_large_one():
    """The colours the picture chooses start from the colour that is far from the others AND common, not merely far: a small red dot on a big
    black disc, and a few dozen stray violet pixels (an outlier a plain farthest-point start would grab), asked for 3 colours, finds red."""
    shapes = [("ellipse", (mm(5), mm(5), mm(45), mm(45)), BLACK), ("ellipse", (mm(23), mm(23), mm(27), mm(27)), RED)]
    data = np.array(picture(shapes))
    rng = np.random.default_rng(7)
    for y, x in rng.integers(0, SIZE, size=(30, 2)):
        if data[y, x].sum() == 765:  # only on the white, where it is an isolated pixel
            data[y, x] = (128, 0, 255)
    result = build_vector(as_png(Image.fromarray(data)), VectorOptions(colours=3, width_mm=SHEET_MM, height_mm=SHEET_MM))
    assert sorted(layer["colour"] for layer in result.check["layers"]) == ["#000000", "#FF0000"]


# ---- white is the bare material (item 1) ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("asked", [2, 4, 8])
def test_white_never_gets_a_layer_whatever_the_number_of_colours(asked):
    """§31.7 answer 2: nothing is cut or engraved for white. No layer is white, none is called white, and the paths together cover only the
    three shapes (a third of the sheet), never the white."""
    result = make(three_inks(), colours=asked)
    for layer in result.check["layers"]:
        assert layer["colour"] != "#FFFFFF" and "white" not in layer["id"] and "white" not in layer["name"]
    covered = sum(layer.polygon().area for layer in layers_of(result.svg))
    assert covered < 0.45 * SHEET_MM * SHEET_MM


def test_an_off_white_background_is_the_bare_material_too():
    """Paper-white is not always #FFFFFF: a cream background (250, 247, 238) is as much bare material as pure white. It does not become a layer."""
    result = make([("ellipse", (mm(10), mm(10), mm(40), mm(40)), BLACK)], colours=3, background=(250, 247, 238))
    assert [layer["colour"] for layer in result.check["layers"]] == ["#000000"] and result.check["white"] is True


def test_a_picture_that_is_all_white_has_nothing_to_cut():
    """The engine refuses it with words for the card (a VectorError with a hint), whether the picture is white or transparent."""
    for image in (Image.new("RGB", (64, 64), "white"), Image.new("RGBA", (64, 64), (0, 0, 0, 0))):
        with pytest.raises(VectorError) as caught:
            build_vector(as_png(image), VectorOptions(width_mm=20, height_mm=20))
        assert "nothing to cut" in caught.value.message and caught.value.hint


def test_transparent_parts_of_a_picture_are_white():
    """Transparency is the bare material: a black disc on a transparent background is the same drawing as on white."""
    solid = picture([("ellipse", (mm(10), mm(10), mm(40), mm(40)), BLACK)], smooth=1)
    data = np.array(solid.convert("RGBA"))
    data[(data[..., :3] == 255).all(axis=-1)] = (0, 0, 0, 0)
    options = VectorOptions(colours=2, width_mm=SHEET_MM, height_mm=SHEET_MM)
    assert build_vector(as_png(Image.fromarray(data)), options).svg == build_vector(as_png(solid), options).svg


# ---- the palette (item 1: `palette` lists the inks the user named) ---------------------------------------------------------------------
def test_a_named_colour_is_the_layers_exact_colour_whatever_the_picture_has():
    """The layer's colour is the palette's own: a square drawn in (230, 10, 10) with the palette #FF0000 is a #FF0000 layer, and the check
    says what the picture really had. The other ink is the picture's own."""
    shapes = [("ellipse", (mm(3), mm(3), mm(23), mm(23)), BLACK), ("rect", (mm(28), mm(3), mm(46), mm(21)), (230, 10, 10))]
    result = make(shapes, colours=3, palette=("#FF0000",))
    by_colour = {layer["colour"]: layer for layer in result.check["layers"]}
    assert set(by_colour) == {"#FF0000", "#000000"}
    assert by_colour["#FF0000"]["picture_colour"] == "#E60A0A" and by_colour["#000000"]["picture_colour"] == "#000000"
    red = next(layer for layer in layers_of(result.svg) if layer.colours() == {"#FF0000"})
    x0, _, x1, _ = bbox(red)
    assert abs(x0 - 28) < 0.25 and abs(x1 - 46) < 0.25  # it is the square that is red, and the disc that is black


def test_a_named_colour_the_picture_does_not_have_gets_no_layer_and_a_note():
    """Palette red and blue, a picture with only red: one layer, `empty_layer` naming the blue that is missing, and `fewer_colours`."""
    result = make([("rect", (mm(10), mm(10), mm(40), mm(40)), RED)], colours=3, palette=("#FF0000", "#0000FF"))
    assert [layer["colour"] for layer in result.check["layers"]] == ["#FF0000"]
    notes = {note["code"]: note["message"] for note in result.check["notes"]}
    assert "#0000FF" in notes["empty_layer"] and "not in the picture" in notes["empty_layer"] and "fewer_colours" in notes


def test_a_picture_in_other_colours_than_the_palette_is_not_called_empty():
    """A model that ignored the palette and drew in black: with palette red the black art is the red ink's (the nearest ink), because a colour
    as far from white as black is never the bare material. The alternative, "the picture is all white", would be false."""
    result = make([("ellipse", (mm(10), mm(10), mm(40), mm(40)), BLACK)], colours=2, palette=("#FF0000",))
    layer = result.check["layers"][0]
    assert (layer["colour"], layer["picture_colour"]) == ("#FF0000", "#000000")


def test_the_inks_the_palette_does_not_name_come_from_the_picture():
    """Colours 4 with one named colour: that ink is the palette's, the other two are the picture's, and the three are all there."""
    result = make(three_inks(), colours=4, palette=("#00FF00",))
    assert sorted(layer["colour"] for layer in result.check["layers"]) == ["#000000", "#00FF00", "#FF0000"]


# ---- every layer's colour is distinct (item 2) -----------------------------------------------------------------------------------------
def test_two_inks_of_nearly_the_same_colour_get_different_exact_colours():
    """Laser programs tell layers apart by colour, so two layers may never share one: two reds a few steps apart stay two layers, in two
    different colours."""
    shapes = [("rect", (mm(5), mm(5), mm(23), mm(45)), (200, 30, 30)), ("rect", (mm(27), mm(5), mm(45), mm(45)), (200, 42, 30))]
    result = make(shapes, colours=3)
    colours = [layer["colour"] for layer in result.check["layers"]]
    assert len(colours) == 2 and len(set(colours)) == 2


def ink(index, picture_colour, palette=None, count=100):
    """An `Ink` as the colour reduction would hand it to the engine, for the tests of how the engine gives out colours."""
    return pixels.Ink(index=index, palette=palette, picture=picture_colour, pixels=count, lightness=rgb_to_lab(hex_to_rgb(picture_colour))[0])


def layer_of(found):
    return engine._Layer(ink=found, geometry=None)  # the colour step looks at the ink only


def test_a_clash_is_nudged_by_the_smallest_step_in_a_fixed_order():
    """`_nudge` moves a colour to the nearest one that is free: one step up in blue, one down in blue, then green, then red, then two
    steps. The order is fixed so that the same clash always gives the same file."""
    assert engine._nudge("#112233", {"#112233"}) == "#112234"
    assert engine._nudge("#112233", {"#112233", "#112234"}) == "#112232"
    assert engine._nudge("#112233", {"#112233", "#112234", "#112232"}) == "#112333"
    assert engine._nudge("#112233", {"#112233", "#112234", "#112232", "#112333", "#112133"}) == "#122233"
    assert engine._nudge("#112233", {"#112233", "#112234", "#112232", "#112333", "#112133", "#122233", "#102233"}) == "#112235"
    assert engine._nudge("#0000FF", {"#0000FF"}) == "#0000FE"  # blue cannot go up from 255, so it goes down
    assert engine._nudge("#FFFFFF", {"#FFFFFF"}) == "#FFFFFE"


def test_a_picture_colour_that_clashes_with_a_named_one_or_with_white_is_the_one_that_moves():
    """The palette's colour is a promise and the picture's a measurement, so a clash moves the picture's colour, whichever layer is first.
    White is taken too (it is the bare material): a layer whose colour rounds to white is nudged off it."""
    first, second, third = layer_of(ink(1, "#FF0000")), layer_of(ink(2, "#FF0000", palette="#FF0000")), layer_of(ink(3, "#FFFFFF"))
    engine._assign_colours([first, second, third])
    assert second.colour == "#FF0000" and first.colour == "#FF0001" and third.colour == "#FFFFFE"
    assert len({first.colour, second.colour, third.colour}) == 3


# ---- the names (item 3) ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("colour,name", [
    ("#000000", "black"), ("#101214", "black"), ("#333333", "black"), ("#808080", "grey"), ("#C0C0C0", "grey"), ("#FFFFFF", "white"),
    ("#F4F4F4", "white"), ("#FF0000", "red"), ("#DC1E1E", "red"), ("#800000", "red"), ("#FF8000", "orange"), ("#FFA500", "orange"),
    ("#FFFF00", "yellow"), ("#FFD700", "yellow"), ("#00FF00", "green"), ("#00AA00", "green"), ("#006400", "green"), ("#00FFFF", "cyan"),
    ("#0000FF", "blue"), ("#1478DC", "blue"), ("#000080", "blue"), ("#87CEEB", "blue"), ("#800080", "purple"), ("#FF00FF", "purple"),
    ("#4B0082", "purple"), ("#FFC0CB", "pink"), ("#FF69B4", "pink"), ("#8B4513", "brown"),
])
def test_a_colour_is_called_by_the_nearest_name_of_the_fixed_list(colour, name):
    """The names a person would give these colours, from the fixed list of twelve; the nearest-reference rule must not call a light blue grey."""
    assert colour_name(colour) == name


def test_names_come_from_the_twelve_and_are_stable():
    assert COLOUR_NAMES == ("black", "grey", "white", "red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "brown")
    rng = np.random.default_rng(3)
    for r, g, b in rng.integers(0, 256, size=(200, 3)):
        colour = "#%02X%02X%02X" % (r, g, b)
        assert colour_name(colour) in COLOUR_NAMES and colour_name(colour) == colour_name(colour.lower().replace("#", "#"))


def test_the_numpy_colour_space_is_the_plain_python_one():
    """There are two implementations of sRGB to Lab (plain Python for names and the recipe, numpy for the picture); they must agree, or the
    two halves of the studio would disagree about which colours look alike."""
    rng = np.random.default_rng(5)
    sample = rng.integers(0, 256, size=(300, 3)).astype(np.uint8)
    sample[:4] = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 0, 255)]
    fast = pixels.srgb_to_lab(sample)
    for row, lab in zip(sample, fast):
        assert np.allclose(lab, rgb_to_lab(tuple(int(v) for v in row)), atol=1e-6)
    assert np.allclose(fast[1], (100, 0, 0), atol=1e-3)  # white is L 100, no colour: the fixed centre of its cluster is where white really is


# ---- reading the picture ----------------------------------------------------------------------------------------------------------------
def test_a_file_that_is_not_a_picture_is_a_message_for_the_card_and_not_a_traceback():
    with pytest.raises(VectorError) as caught:
        build_vector(b"this is not a picture", VectorOptions())
    assert caught.value.message and caught.value.hint


def test_a_picture_far_larger_than_the_studio_makes_is_refused(monkeypatch):
    """A wrong file (someone's 100 megapixel photograph) must not use all the memory of a machine that is holding a model."""
    monkeypatch.setattr(pixels, "MAX_PIXELS", 1000)
    with pytest.raises(VectorError) as caught:
        build_vector(as_png(Image.new("RGB", (64, 64), "black")), VectorOptions(width_mm=20, height_mm=20))
    assert "too large" in caught.value.message and caught.value.hint


def test_a_picture_can_be_given_as_a_path(tmp_path):
    """The job passes a path, the tests pass bytes: both work and give the same file."""
    image = picture(three_inks())
    path = tmp_path / "drawing.png"
    image.save(path)
    options = VectorOptions(colours=4, width_mm=SHEET_MM, height_mm=SHEET_MM)
    assert build_vector(path, options).svg == build_vector(as_png(image), options).svg


# ---- soft edges and the pixel clean-up (pixels.py) ------------------------------------------------------------------------------------
def naive(mask, size, mode):
    """Erosion or dilation by looking at every window, the slow way, with the picture's edge repeated outward."""
    low = -(size // 2)
    padded = np.pad(mask, size, mode="edge")
    out = np.zeros_like(mask)
    height, width = mask.shape
    for y in range(height):
        for x in range(width):
            offsets = [(dy, dx) for dy in range(low, low + size) for dx in range(low, low + size)]
            if mode == "dilate":
                offsets = [(-dy, -dx) for dy, dx in offsets]
            values = [padded[y + size + dy, x + size + dx] for dy, dx in offsets]
            out[y, x] = all(values) if mode == "erode" else any(values)
    return out


@pytest.mark.parametrize("size", [1, 2, 3])
def test_erode_and_dilate_match_a_window_looked_at_pixel_by_pixel(size):
    """The fast version (two straight passes) must be the same as the definition, at the picture's edge too."""
    mask = np.random.default_rng(11).random((17, 23)) > 0.4
    assert np.array_equal(pixels.erode(mask, size), naive(mask, size, "erode"))
    assert np.array_equal(pixels.dilate(mask, size), naive(mask, size, "dilate"))
    assert np.array_equal(pixels.opening(mask, size), pixels.dilate(pixels.erode(mask, size), size))


def test_a_ribbon_between_regions_disappears_a_wide_stripe_stays_and_a_stray_pixel_is_absorbed():
    """A two-pixel ribbon of a third label disappears; a stripe four pixels wide is a real feature and stays; a single stray pixel is given to
    what surrounds it."""
    labels = np.zeros((20, 40), dtype=np.uint8)
    labels[:, 12:] = 2
    labels[:, 5:7] = 3  # a ribbon in the white
    labels[:, 20:24] = 3  # a stripe four wide inside label 2
    labels[10, 30] = 1  # a stray pixel
    cleaned = pixels.remove_fringes(labels, 3, 4)
    assert (cleaned[:, 5:7] == 0).all()
    assert (cleaned[:, 20:24] == 3).all()
    assert cleaned[10, 30] == 2 and not (cleaned == 1).any()


def test_a_ribbon_between_two_regions_is_split_between_them():
    """The ribbon where white meets label 2 is given half to each side, so the two meet cleanly with no thin colour between them."""
    labels = np.zeros((10, 20), dtype=np.uint8)
    labels[:, 10:] = 2
    labels[:, 9:11] = 3
    cleaned = pixels.remove_fringes(labels, 3, 4)
    assert (cleaned[:, :10] == 0).all() and (cleaned[:, 10:] == 2).all()


def test_the_fringe_window_follows_the_smallest_feature_but_never_exceeds_three_pixels():
    """A line the user asked to keep (a smallest feature of two pixels or less) must survive, and a fringe is never wider than three."""
    assert [pixels.fringe_window(v) for v in (0.2, 1.0, 1.99, 2.0, 2.99, 3.0, 7.7, 100)] == [1, 1, 1, 2, 2, 3, 3, 3]


def test_the_same_drawing_turned_upside_down_has_the_same_colours():
    """The clustering starts from fixed points, not random ones, and does not depend on where in the picture things are."""
    image = picture(three_inks())
    options = VectorOptions(colours=4, width_mm=SHEET_MM, height_mm=SHEET_MM)
    a = build_vector(as_png(image), options).check["layers"]
    b = build_vector(as_png(image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)), options).check["layers"]
    assert [(x["colour"], x["paths"]) for x in a] == [(x["colour"], x["paths"]) for x in b]
