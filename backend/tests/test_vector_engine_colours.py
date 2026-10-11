"""The colour side of the Vector clean-up (DESIGN.md §31.12.3 items 1, 2 and 3; §31.8 criterion 136): the number of colours is enforced by
the studio and not requested of the model, white is the bare material and never a layer, every layer has an exact colour that no other
layer has, the palette is honoured, and what a picture's soft edges would add (a thin third colour between two inks) is removed.

The pictures are constructed (shapes with anti-aliased edges, like a real drawing), so every expectation is known in advance."""

from __future__ import annotations

import collections
import hashlib
import itertools

import numpy as np
import pytest
from PIL import Image

from studio.vector import VectorError, VectorOptions, build_vector, colour_name
from studio.vector import engine, pixels
from studio.vector.pixels import _give_to_neighbours
from studio.vector.colours import COLOUR_NAMES, COLOUR_REFERENCES, hex_to_rgb, rgb_to_lab

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


def test_a_pale_ink_is_still_an_ink_and_a_light_grey_is_still_bare_material():
    """Near-white is a narrow band: a pale yellow fill (255, 255, 200) is a colour the user drew and gets its layer (3 colours: black and the yellow);
    a light grey (215, 215, 215) a little further from white than the cream above is nearer to white than to black, so with 2 colours it
    stays the bare material and only the disc is cut."""
    pale = make([("ellipse", (mm(3), mm(3), mm(23), mm(23)), BLACK), ("rect", (mm(28), mm(3), mm(46), mm(21)), (255, 255, 200))], colours=3)
    assert sorted(layer["colour"] for layer in pale.check["layers"]) == ["#000000", "#FFFFC8"]
    grey = make([("ellipse", (mm(3), mm(3), mm(23), mm(23)), BLACK), ("rect", (mm(28), mm(3), mm(46), mm(21)), (215, 215, 215))], colours=2)
    assert [layer["paths"] for layer in grey.check["layers"]] == [1] and grey.check["white"] is True


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
    assert layer["id"] == "layer-1-red"  # named for the colour the layer is, not for the colour the picture had


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


def test_two_picture_colours_that_come_out_the_same_are_told_apart_too():
    """If two inks' colours round to the same #RRGGBB the second is nudged, and a nudged colour counts as taken for the ones after it."""
    first, second, third = layer_of(ink(1, "#101010")), layer_of(ink(2, "#101010")), layer_of(ink(3, "#101011"))
    engine._assign_colours([first, second, third])
    assert (first.colour, second.colour, third.colour) == ("#101010", "#101011", "#101012")  # each nudged off the ones before it


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


def grid_colours():
    """A grid of 18 x 18 x 18 colours spread over the whole of RGB (every 15th level, and 255): enough that every entry of the table of names
    decides the name of some of them."""
    levels = list(range(0, 256, 15)) + [255]
    return [("#%02X%02X%02X" % rgb) for rgb in itertools.product(levels, repeat=3)]


def test_the_names_of_the_whole_colour_cube_are_a_fixed_list():
    """§31.12.9 item 8: the names are fixed and not configurable, and the same colour must have the same name in every release (a layer's name is in
    the file the owner keeps). A fingerprint of the name of each of 6859 colours over the whole cube, so that any change to the table of
    references or to how the nearest one is chosen shows up here, and a change that is meant is made in two places, on purpose. To renew it,
    run the same two lines and paste the new digest."""
    names = [colour_name(colour) for colour in grid_colours()]
    assert len(names) == 6859
    assert hashlib.sha256(",".join(names).encode()).hexdigest() == "20cc01072a54c0835ef4fbb8f929a979c4f38823d92acd41f39b54a19220963a"
    assert collections.Counter(names) == {"green": 1570, "purple": 1074, "blue": 969, "pink": 577, "cyan": 531, "yellow": 475, "brown": 473,
                                          "grey": 409, "red": 351, "orange": 174, "white": 131, "black": 125}


def test_no_entry_in_the_table_of_names_is_idle():
    """Each reference colour earns its place: without it, some colour on the grid would be called something else. An entry that changes
    nothing would be tuning nobody can see or test."""
    colours = grid_colours()
    lab = pixels.srgb_to_lab(np.array([hex_to_rgb(c) for c in colours], dtype=np.uint8))
    references = list(COLOUR_REFERENCES)
    ref_lab = pixels.srgb_to_lab(np.array([hex_to_rgb(h) for _, h in references], dtype=np.uint8))

    def names(skip=None):
        keep = [i for i in range(len(references)) if i != skip]
        distance = ((lab[:, None, :] - ref_lab[keep][None, :, :]) ** 2).sum(axis=2)
        return [references[keep[i]][0] for i in distance.argmin(axis=1)]

    full = names()
    assert full == [colour_name(c) for c in colours]  # the quick way and colour_name agree
    assert [i for i in range(len(references)) if names(skip=i) == full] == []


def test_names_come_from_the_twelve_and_are_stable():
    assert COLOUR_NAMES == ("black", "grey", "white", "red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "brown")
    rng = np.random.default_rng(3)
    for r, g, b in rng.integers(0, 256, size=(200, 3)):
        colour = "#%02X%02X%02X" % (r, g, b)
        assert colour_name(colour) in COLOUR_NAMES and colour_name(colour) == colour_name(colour.lower())  # either case of hex digit


def test_the_numpy_colour_space_is_the_plain_python_one():
    """There are two implementations of sRGB to Lab (plain Python for names and the recipe, numpy for the picture); they must agree, or the
    two halves of the studio would disagree about which colours look alike."""
    rng = np.random.default_rng(5)
    sample = rng.integers(0, 256, size=(300, 3)).astype(np.uint8)
    sample[:4] = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 0, 255)]
    fast = pixels.srgb_to_lab(sample)
    for row, lab in zip(sample, fast):
        assert np.allclose(lab, rgb_to_lab(tuple(int(v) for v in row)), atol=1e-6)
    assert np.allclose(fast[1], (100, 0, 0), atol=1e-3)  # white is L 100, no colour
    assert pixels.WHITE_LAB == pytest.approx(tuple(fast[1]), abs=1e-3)  # and the fixed centre of white's cluster is where white really is


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


def test_a_fringe_pixel_goes_to_the_neighbour_whose_colour_it_is_nearest_to():
    """A dark grey pixel between white and black belongs to the black (its colour is nearer), not to white by the luck of a tie of votes: the
    edge of a black shape sits where the grey stops being dark. Here a one pixel ribbon of (17, 17, 17) is between white and black."""
    labels = np.zeros((6, 12), dtype=np.uint8)
    labels[:, 6:] = 1
    labels[:, 5] = 2  # the ribbon, a label of its own as k-means would have made it
    colours = np.zeros((6, 12, 3))
    colours[:, :6] = rgb_to_lab((255, 255, 255))
    colours[:, 6:] = rgb_to_lab((0, 0, 0))
    colours[:, 5] = rgb_to_lab((17, 17, 17))
    centres = np.array([rgb_to_lab((255, 255, 255)), rgb_to_lab((0, 0, 0)), rgb_to_lab((17, 17, 17))])
    cleaned = pixels.remove_fringes(labels, 3, 3, lambda ys, xs: colours[ys, xs], centres)
    assert (cleaned[:, :5] == 0).all() and (cleaned[:, 5:] == 1).all()
    assert (pixels.remove_fringes(labels, 3, 3)[:, 5] == 0).all()  # by votes alone the tie goes to white, which is why the colours are used


def test_a_ribbon_between_two_regions_is_split_between_them():
    """The ribbon where white meets label 2 is given half to each side, so the two meet cleanly with no thin colour between them."""
    labels = np.zeros((10, 20), dtype=np.uint8)
    labels[:, 10:] = 2
    labels[:, 9:11] = 3
    cleaned = pixels.remove_fringes(labels, 3, 4)
    assert (cleaned[:, :10] == 0).all() and (cleaned[:, 10:] == 2).all()


def test_removing_fringes_goes_on_until_what_the_first_pass_uncovered_is_gone_too():
    """Giving a thin part to its neighbours can leave a new thin part (a label that was wide enough only with the pixels just given away).
    A worked example, a blocky 8 x 10 map of three labels, pinned as it comes out after the passes (one pass alone gives a different map
    in rows 4 and 5, where the label 1 is left a column short); the clean-up stops when a pass finds nothing, and never after more than three."""
    labels = np.array([[0, 0, 0, 0, 2, 2, 1, 1, 0, 0], [0, 0, 0, 0, 2, 2, 1, 1, 0, 0], [2, 2, 2, 2, 0, 0, 2, 2, 2, 2],
                       [2, 2, 2, 2, 0, 0, 2, 2, 2, 2], [2, 2, 1, 1, 2, 2, 0, 0, 0, 0], [2, 2, 1, 1, 2, 2, 0, 0, 0, 0],
                       [2, 2, 1, 1, 1, 1, 2, 2, 1, 0], [2, 2, 1, 1, 1, 1, 2, 2, 0, 0]], dtype=np.uint8)
    expected = np.array([[0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [2, 2, 0, 0, 0, 0, 0, 0, 0, 0],
                         [2, 2, 2, 0, 0, 0, 0, 0, 0, 0], [2, 2, 2, 1, 1, 1, 1, 1, 0, 0], [2, 2, 2, 1, 1, 1, 1, 1, 1, 1],
                         [2, 2, 1, 1, 1, 1, 1, 1, 1, 1], [2, 2, 1, 1, 1, 1, 1, 1, 1, 1]])
    assert np.array_equal(pixels.remove_fringes(labels, 3, 3), expected)


def test_pixels_far_from_any_assigned_pixel_get_their_label_from_the_nearest_ones_over_several_rounds():
    """A block of unassigned pixels more than two wide is filled from its edge inwards, a round at a time, with the label of the edge it is
    next to: a stripe of label 1 on the left and five unassigned columns gives label 1 all the way, and a single assigned pixel spreads in
    all eight directions, through the diagonals as well."""
    labels = np.zeros((4, 7), dtype=np.int16)
    labels[:, :2] = 1
    unassigned = np.zeros((4, 7), dtype=bool)
    unassigned[:, 2:] = True
    assert (_give_to_neighbours(labels, unassigned, 3) == 1).all()
    labels = np.zeros((3, 3), dtype=np.int16)
    labels[0, 1] = 2
    unassigned = np.ones((3, 3), dtype=bool)
    unassigned[0, 1] = False
    assert (_give_to_neighbours(labels, unassigned, 3) == 2).all()


def test_white_and_the_palette_are_fixed_centres_that_never_move():
    """White is the bare material and a named colour is a promise: neither centre moves to fit the picture, however the picture is made. On many
    little random pictures of five colours, a pixel is white only if it is nearer to white than to the named colour (or within the band around
    white), and the named colour's only if it is nearer to that (or far from white); a centre that wandered off to the mean of what it held
    would break this for some pixel."""
    rng = np.random.default_rng(0)
    white_lab = np.array(pixels.WHITE_LAB)
    checked = 0
    for _ in range(400):
        colours = rng.integers(0, 256, size=(5, 3)).astype(np.uint8)
        colours[0] = (255, 255, 255)
        image = colours[rng.integers(0, 5, size=(10, 10))]
        palette = "#%02X%02X%02X" % tuple(rng.integers(0, 256, 3))
        labels = pixels.reduce_colours(image, 3, (palette,), 1).labels
        lab = pixels.srgb_to_lab(image.reshape(-1, 3)).reshape(10, 10, 3)
        to_white = ((lab - white_lab) ** 2).sum(axis=2)
        to_named = ((lab - np.array(rgb_to_lab(hex_to_rgb(palette)))) ** 2).sum(axis=2)
        near, far = to_white <= pixels.NEAR_WHITE ** 2, to_white > pixels.BARE_REACH ** 2
        assert (~(labels == 0) | (to_white <= to_named) | near).all()
        assert (~(labels == 1) | (to_named <= to_white) | far).all()
        checked += 1
    assert checked == 400


def test_white_does_not_move_to_the_mean_of_an_off_white_background():
    """White's centre stays where white is even when the paper is (240, 240, 240). Were it to move to the paper's grey, the line between white and a
    black ink would move with it, from half way (L 50) toward the paper (L 47.5): a small grey square of (117, 117, 117) (L 49.2) is
    nearer the ink than white, and is the ink's, as it is on white paper. Two paths: the black square and the grey one."""
    shapes = [("rect", (mm(5), mm(5), mm(25), mm(25)), (0, 0, 0)), ("rect", (mm(30), mm(5), mm(34), mm(9)), (117, 117, 117))]
    for paper in ((240, 240, 240), (255, 255, 255)):
        result = make(shapes, colours=2, smooth=1, background=paper)
        assert [layer["paths"] for layer in result.check["layers"]] == [2], paper


def test_a_colour_that_was_asked_for_near_white_is_still_white_and_is_reported_missing():
    """The band around white is white whatever else is near: a cream (250, 247, 238) background, a black disc and the palette colour #FAF7EE
    (which is the cream) with 3 colours: the cream is the bare material, the palette colour gets no layer and is named as missing."""
    result = make([("ellipse", (mm(10), mm(10), mm(40), mm(40)), BLACK)], colours=3, background=(250, 247, 238), palette=("#FAF7EE",))
    assert [layer["colour"] for layer in result.check["layers"]] == ["#000000"] and result.check["white"] is True
    assert "#FAF7EE" in {n["code"]: n["message"] for n in result.check["notes"]}["empty_layer"]


def test_a_picture_in_none_of_the_named_colours_goes_to_the_nearest_named_one():
    """Palette red and blue, a black disc: black is nearer to red than to blue in Lab, so the red ink holds it, and the blue is missing."""
    result = make([("ellipse", (mm(10), mm(10), mm(40), mm(40)), BLACK)], colours=3, palette=("#FF0000", "#0000FF"))
    assert [layer["colour"] for layer in result.check["layers"]] == ["#FF0000"]
    assert "#0000FF" in {n["code"]: n["message"] for n in result.check["notes"]}["empty_layer"]


def test_a_line_too_thin_to_have_an_inside_still_has_the_colour_it_was_drawn_in():
    """The picture's colour for an ink is the mean of the pixels well inside it; a line two pixels wide has no inside, so its own pixels are used
    (and not, say, everything else's). With a smallest feature of 0.05 mm the line is kept, and its layer is the colour of the line."""
    result = make([("rect", (mm(5), mm(20), mm(45), mm(20) + 2), (200, 30, 30))], colours=2, smooth=1, min_feature_mm=0.05)
    assert [layer["colour"] for layer in result.check["layers"]] == ["#C81E1E"]


def test_two_shades_too_alike_to_tell_apart_are_one_colour():
    """Shades closer than 2 apart in Lab are the same colour to anyone's eye: a red square of two parts, (200, 30, 30) on the small left side and
    (200, 31, 30) on the large right side, asked for 3 colours, is one ink, not two (and the note says the picture has fewer colours).
    The smaller shade has the lower colour value, which is the one a careless start would reach for after the real colours run out."""
    shapes = [("rect", (mm(5), mm(5), mm(11), mm(45)), (200, 30, 30)), ("rect", (mm(11), mm(5), mm(23), mm(45)), (200, 31, 30))]
    result = make(shapes, colours=3, smooth=1)  # hard edges: no blurred in-between colours to be chosen instead
    assert len(result.check["layers"]) == 1 and "fewer_colours" in codes(result)


def test_an_inks_colour_moves_to_the_mean_of_what_it_holds_and_so_pulls_in_what_is_near_it():
    """k-means, not just a starting guess: asked for 2 colours, black (60% of the ink), a dark grey (90) and a mid grey (128) are ONE ink,
    because once the dark grey has joined the black the ink's centre is lighter, and nearer to the mid grey than white is. Started and left at
    pure black, the mid grey would stay bare material. One layer, three shapes."""
    shapes = [("rect", (mm(2), mm(2), mm(32), mm(22)), (0, 0, 0)), ("rect", (mm(2), mm(26), mm(22), mm(46)), (90, 90, 90)),
              ("rect", (mm(30), mm(30), mm(40), mm(40)), (128, 128, 128))]
    result = make(shapes, colours=2, smooth=1)
    assert [layer["paths"] for layer in result.check["layers"]] == [3]


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
