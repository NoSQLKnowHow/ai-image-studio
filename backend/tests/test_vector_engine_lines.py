"""Keeping thin lines (DESIGN.md §41; criteria 319 to 321, 326 to 328): a stroke narrower than the smallest feature is widened to it, instead of
deleted, so a drawing keeps its face and a stroke that wobbles across the limit stays one line instead of breaking into dashes.

Three groups. The pieces of arithmetic in `studio/vector/lines.py` are checked against plain, slow versions written here (a flood fill, a brute-force
distance), because a mistake in them would not show as a failure, only as a quietly wrong picture. Then the keeping of lines on a label map. Then the
whole clean-up on constructed pictures (see `vector_engine_support.pencil_shapes`), including that `thin_lines = "drop"` still writes, byte for byte, what
the engine wrote before 1.23: the golden hashes below were made by the engine as it was, and a change to them means the old behaviour changed."""

from __future__ import annotations

import hashlib
import math
import random
from collections import deque

import numpy as np
import pytest

from shapely.geometry import Point
from shapely.ops import unary_union

from studio.vector import VectorError, VectorOptions, build_vector, check_options, lines, pixels

from vector_engine_support import SHEET_MM, as_png, layers_of, make, picture, polygon_of, pencil_shapes

BLACK = (0, 0, 0)


# ---- helpers: slow and obvious versions of what lines.py does fast ---------------------------------------------------------------------
def flood_pieces(mask: np.ndarray) -> list[tuple[int, int, int]]:
    """(area, bounding-box height, bounding-box width) of each 8-connected piece, found by a plain flood fill, sorted."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    found = []
    for y in range(height):
        for x in range(width):
            if mask[y, x] and not seen[y, x]:
                queue, cells = deque([(y, x)]), []
                seen[y, x] = True
                while queue:
                    cy, cx = queue.popleft()
                    cells.append((cy, cx))
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                                seen[ny, nx] = True
                                queue.append((ny, nx))
                ys, xs = [c[0] for c in cells], [c[1] for c in cells]
                found.append((len(cells), max(ys) - min(ys) + 1, max(xs) - min(xs) + 1))
    return sorted(found)


def strip_width(geometry) -> float:
    """The width, in the geometry's own units, of a long thin strip: twice its area over its perimeter (exact for a rectangle much longer than wide)."""
    return 2 * geometry.area / geometry.length


def layer_geometry(svg: str):
    """The first layer's shapes as one geometry, in millimetres, read from the file with the test's own reader."""
    return unary_union([polygon_of(path.rings) for path in layers_of(svg)[0].paths])


def codes(result) -> list[str]:
    return [note["code"] for note in result.check["notes"]]


# ---- pieces ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(12))
def test_pieces_agree_with_a_plain_flood_fill(seed):
    """The run-based labelling finds the same pieces, with the same areas and boxes, as a flood fill on random masks of every density: a mistake in how runs on
    neighbouring rows are joined (a corner touch, the first or last run of a row) would merge or split pieces and be wrong everywhere that uses them."""
    rng = np.random.default_rng(seed)
    density = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5][seed % 6]
    mask = rng.random((24 + seed, 31 - seed)) < density
    found = lines.pieces(mask)
    expected = flood_pieces(mask)
    got = sorted((int(found.area[i]), int(found.height[i]), int(found.width[i])) for i in range(1, found.count + 1))
    assert got == expected
    assert found.labels.max() == found.count
    assert ((found.labels > 0) == mask).all()  # every ink pixel is in a piece, and nothing else is


def test_pieces_touching_at_a_corner_are_one_piece_and_a_gap_of_one_pixel_is_two():
    """A pencil stroke drawn on a slant is a staircase of pixels that touch at corners: it is one stroke. Two pieces one pixel apart are two."""
    diagonal = np.eye(6, dtype=bool)
    assert lines.pieces(diagonal).count == 1
    apart = np.zeros((3, 7), dtype=bool)
    apart[1, 0:3] = apart[1, 4:7] = True
    assert lines.pieces(apart).count == 2


def test_pieces_of_nothing_and_of_everything():
    assert lines.pieces(np.zeros((5, 5), dtype=bool)).count == 0
    everything = lines.pieces(np.ones((4, 6), dtype=bool))
    assert everything.count == 1 and int(everything.area[1]) == 24 and (int(everything.height[1]), int(everything.width[1])) == (4, 6)


# ---- specks ---------------------------------------------------------------------------------------------------------------------------
def test_a_dot_is_a_speck_but_a_line_and_a_round_spot_are_not():
    """With a smallest feature of 5 pixels: one pixel is noise; a stroke 1 pixel wide and as long as 2 features is the shortest thing that is a line; a
    round spot as wide as the feature or wider is a wanted dot (a pupil, a full stop): nothing is a speck that the old removal would have kept. The rule that tells them apart is what keeps noise from becoming blots."""
    mask = np.zeros((60, 60), dtype=bool)
    mask[5, 5] = True                       # a dot
    mask[20, 10:20] = True                  # a line 10 long: twice the feature
    yy, xx = np.mgrid[0:60, 0:60]
    mask |= (yy - 40) ** 2 + (xx - 40) ** 2 <= 3 ** 2     # a round spot a little wider than the feature
    pixels_of_specks, count = lines.specks_in(mask, 5.0)
    assert count == 1 and pixels_of_specks[5, 5] and not pixels_of_specks[20, 15] and not pixels_of_specks[40, 40]
    # a line one pixel short of twice the feature, with nothing round about it, is a speck
    short = np.zeros((20, 30), dtype=bool)
    short[10, 5:14] = True
    assert lines.specks_in(short, 5.0)[1] == 1


def test_specks_of_an_empty_mask():
    found, count = lines.specks_in(np.zeros((8, 8), dtype=bool), 5.0)
    assert count == 0 and not found.any()


# ---- the distance ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(6))
def test_inside_distance_is_the_true_distance_up_to_the_reach(seed):
    """For every ink pixel, the squared distance to the nearest paper pixel, against a brute-force search: exact wherever the answer is within the reach."""
    rng = np.random.default_rng(100 + seed)
    mask = rng.random((20, 22)) < 0.7
    mask = np.pad(mask, 4, constant_values=False)  # paper all round, so that the picture's edge does not decide anything here
    reach = 5
    got = lines.inside_distance_squared(mask, reach)
    paper = np.argwhere(~mask)
    for y, x in np.argwhere(mask):
        true = int(((paper - [y, x]) ** 2).sum(axis=1).min())
        if true <= reach * reach:
            assert got[y, x] == true, (y, x)
        else:
            assert got[y, x] > reach * reach
    assert not got[~mask].any()


def test_the_edge_of_the_picture_is_not_paper():
    """Ink that runs off the sheet is not thinned there: the distance from a pixel on the edge is measured to real paper, not to the edge."""
    mask = np.ones((10, 10), dtype=bool)
    assert (lines.inside_distance_squared(mask, 3) > 9).all()


# ---- widening ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("half", [1.5, 2.5, 4.0])
def test_a_one_pixel_line_is_widened_to_the_target(half):
    """A line one pixel wide ends up the width asked for, to the pixel: one pixel plus a row for every whole step within reach of it on each side."""
    mask = np.zeros((40, 100), dtype=bool)
    mask[20, 10:90] = True
    out = lines.thicken(mask, half)
    column = out[:, 50]
    steps = math.floor(half - 0.5)
    assert column.sum() == 1 + 2 * steps
    assert out[:, 50].nonzero()[0].tolist() == list(range(20 - steps, 21 + steps))
    assert (out | mask).sum() == out.sum()   # nothing that was ink is lost


def test_a_wide_shape_does_not_move_and_a_corner_moves_by_less_than_a_pixel():
    """Only what is narrower than the target grows. A square much wider than the target gains nothing along its edges and at most a pixel at each corner."""
    mask = np.zeros((60, 60), dtype=bool)
    mask[15:45, 15:45] = True
    out = lines.thicken(mask, 3.0)
    assert (out[15:45, 15:45]).all()
    gained = out & ~mask
    assert gained.sum() <= 8
    assert not gained[:, 20:40].any() or gained[:, 20:40].sum() == 0  # no growth along the straight edges


def test_a_stroke_that_wobbles_across_the_limit_stays_one_piece_and_is_wide_everywhere():
    """The reason for all this (§41.1 cause 2): a stroke whose width wanders between 3 and 9 pixels is widened where it is under 8 and left alone where it is
    over, so it is one piece, and every cross-section is at least as wide as the target."""
    height, width = 120, 400
    yy, xx = np.mgrid[0:height, 0:width]
    centre = 60 + 25 * np.sin(xx / 60.0)
    thickness = 6.0 + 3.0 * np.sin(xx / 23.0)                  # 3 to 9 pixels
    mask = np.abs(yy - centre) <= thickness / 2
    mask[:, :20] = mask[:, -20:] = False
    assert lines.pieces(mask).count == 1
    out = lines.thicken(mask, 4.5)                             # target 9 pixels
    assert lines.pieces(out).count == 1
    runs = out[:, 40:360].sum(axis=0)                          # the vertical run in each column (the stroke is never steep here)
    assert runs.min() >= 8                                      # nowhere under the target (a steep stretch of the wave measures a little wider, never narrower)
    assert (out | mask).sum() == out.sum()


def test_nothing_to_widen_gives_the_mask_back_and_the_answer_is_always_the_same():
    empty = np.zeros((10, 10), dtype=bool)
    assert not lines.thicken(empty, 3.0).any()
    mask = np.zeros((30, 30), dtype=bool)
    mask[10, 5:25] = True
    assert (lines.thicken(mask, 3.0) == lines.thicken(mask, 3.0)).all()


# ---- keeping lines on a label map ---------------------------------------------------------------------------------------------------
def test_specks_go_to_white_and_are_counted_and_a_line_is_widened_and_counted():
    labels = np.zeros((80, 120), dtype=np.uint8)
    labels[10, 10] = labels[30, 70] = labels[50, 90] = 1       # three specks
    labels[40, 10:100] = 1                                      # a thin line
    out, kept = lines.keep_thin_lines(labels, 6.0)
    assert kept.specks == 3 and kept.widened == 1 and not kept.too_wide
    assert out[10, 10] == 0 and out[30, 70] == 0 and out[50, 90] == 0   # the specks are not widened into blots
    assert (out[40 - 3:40 + 4, 50] == 1).all()                  # the line is wider than before
    assert out[:, 50].sum() == (out[:, 50] == 1).sum()


def test_a_widened_ink_takes_from_white_and_from_larger_inks_but_never_from_a_smaller_one():
    """Inks are done smallest first, and a widened ink may take pixels from white and from the inks not yet done, never from one that is. Here the smallest ink
    (3) has a thin line at row 17 and ink 1 a longer one at row 23; both are widened by three rows each way, so row 20 is wanted by both, and ink 3, done first,
    keeps it. Ink 2, the largest, starts at row 25 and gives ink 1 the rows its widening needs."""
    labels = np.zeros((60, 100), dtype=np.uint8)
    labels[25:60, 0:100] = 2                                    # the largest ink fills the bottom
    labels[23, 10:90] = 1                                       # a thin line of ink 1: 80 pixels
    labels[17, 10:60] = 3                                       # a shorter line of ink 3: 50 pixels, the smallest ink, so it is done first
    sizes = {k: int((labels == k).sum()) for k in (1, 2, 3)}
    assert sizes[3] < sizes[1] < sizes[2]
    out, _ = lines.keep_thin_lines(labels, 6.0)
    assert (out[20, 20:50] == 3).all()                          # the row both wanted goes to the one done first
    assert (out[14:21, 20:50] == 3).all()                       # and ink 3's whole widened band is whole
    assert (out[23, 20:50] == 1).all()                          # ink 1 keeps its own line
    assert (out == 2).sum() < sizes[2]                          # the largest ink gave some up to ink 1
    assert (out == 1).sum() > sizes[1]                          # ink 1 grew


def test_a_smallest_feature_that_is_too_wide_for_widening_is_said_and_nothing_is_widened():
    labels = np.zeros((100, 200), dtype=np.uint8)
    labels[50, 20:180] = 1
    out, kept = lines.keep_thin_lines(labels, 2 * lines.MAX_HALF_WIDTH_PX + 10)
    assert kept.too_wide and kept.widened == 0
    assert (out == labels).all() or (out == 0).all()            # a line this long is no speck for such a feature; either way nothing grew
    assert (out == 1).sum() <= (labels == 1).sum()


def test_a_label_map_with_no_ink_is_returned_as_it_is():
    labels = np.zeros((10, 10), dtype=np.uint8)
    out, kept = lines.keep_thin_lines(labels, 5.0)
    assert (out == labels).all() and (kept.specks, kept.widened) == (0, 0)


def test_a_bump_on_a_wide_shape_is_not_counted_as_a_line():
    """The count says how many lines were widened. A rounded corner of a wide shape grows by a fraction of a pixel and is not one."""
    labels = np.zeros((120, 120), dtype=np.uint8)
    labels[30:90, 30:90] = 1
    _, kept = lines.keep_thin_lines(labels, 6.0)
    assert kept.widened == 0


# ---- the colour step's bias towards ink ------------------------------------------------------------------------------------------------
def grey_of_lightness(target: float) -> int:
    """The sRGB grey (red = green = blue) whose CIE lightness is nearest `target`: these tests name a pixel by how light it is, which is what the colour step decides on."""
    greys = np.stack([np.arange(256)] * 3, axis=1).astype(np.uint8)
    return int(np.abs(pixels.srgb_to_lab(greys)[:, 0] - target).argmin())


def test_the_bias_moves_the_cut_between_white_and_ink_to_about_l_58():
    """The amount of the bias (§41.3: about L 58 for a black ink), not only that there is one. Black, then greys at L 54, 59.5 and 64, then white,
    the greys small beside the black so that they do not pull the ink's own colour: with no bias the cut is half way (about L 50) and the grey at 54 is
    paper; with the bias it is ink; the grey at 64 is paper both ways. A bias applied to the distance and not its square, or left out, moves the cut and fails this."""
    darker, near_cut, paler = grey_of_lightness(54), grey_of_lightness(59.5), grey_of_lightness(64)
    rgb = np.full((20, 200, 3), 255, dtype=np.uint8)
    rgb[:, :160] = 0
    rgb[:, 160:170] = darker
    rgb[:, 170:180] = near_cut
    rgb[:, 180:190] = paler
    for bias, darker_is_ink in ((1.0, False), (pixels.THIN_LINES_INK_BIAS, True)):
        labels = pixels.reduce_colours(rgb, 1, (), 1, bias).labels
        assert (labels[:, :160] == 1).all(), bias
        assert (labels[:, 160:170] == (1 if darker_is_ink else 0)).all(), bias
        # just past the biased cut (L 59.5 is lighter than about 58): paper both ways. The rule that a colour more than 50 from white is never paper must
        # use the real distance from white and not the biased one, or this one flips to ink (the biased distance is over 50 from L 60.6 down to here).
        assert (labels[:, 170:] == 0).all(), bias


# ---- the whole clean-up -------------------------------------------------------------------------------------------------------------
def test_the_options_accept_thicken_and_drop_and_nothing_else():
    assert check_options(VectorOptions(thin_lines="thicken")) == [] and check_options(VectorOptions(thin_lines="drop")) == []
    problems = dict(check_options(VectorOptions(thin_lines="widen")))
    assert "thin_lines" in problems and "thicken" in problems["thin_lines"]
    assert VectorOptions().thin_lines == "drop"                 # an engine call that does not say behaves as it did before 1.23
    assert VectorOptions.from_json(VectorOptions(thin_lines="thicken").to_json()).thin_lines == "thicken"


def test_the_engine_writes_what_it_wrote_before_1_23_when_asked_to_drop():
    """The old behaviour is still there, byte for byte (criterion 326): the hashes are of the SVG the engine as it was before 1.23 wrote for this picture."""
    shapes, _ = pencil_shapes()
    golden = {0.5: "2be31058318c431160017d866049ddb17551ce2bac8738ca8c9b514ee1f66be8", 0.15: "2269283a404790ce2c38e031d920c7c41866f425d2a62680487053ec9b8d75ad"}
    for feature, digest in golden.items():
        result = make(shapes, min_feature_mm=feature, thin_lines="drop")
        assert hashlib.sha256(result.svg.encode("utf-8")).hexdigest() == digest, feature
        assert result.check["widened"] == 0 and result.check["thin_lines"] == "drop"


def test_a_face_made_of_thin_lines_is_kept_when_thickened_and_lost_when_dropped():
    """The bug, and its fix (criteria 319 and 320): at the default smallest feature the eyes, brows, nose and mouth, 3 and 4 pixels wide, are all gone when
    thin lines are dropped, and all there, as lines, when they are thickened. The heavy outline and the hair are there either way."""
    shapes, where = pencil_shapes()
    dropped = layer_geometry(make(shapes, thin_lines="drop").svg)
    kept_result = make(shapes, thin_lines="thicken")
    kept = layer_geometry(kept_result.svg)
    for part in ("eye", "brow", "nose", "mouth"):
        spot = Point(*where[part])
        assert not dropped.buffer(0.15).intersects(spot), f"{part} should be lost when thin lines are dropped"
        assert kept.buffer(0.15).intersects(spot), f"{part} should be kept when thin lines are thickened"
    assert dropped.intersects(Point(*where["hair"])) and kept.intersects(Point(*where["hair"]))
    assert "thin_widened" in codes(kept_result) and kept_result.check["widened"] >= 4


def test_pale_shading_is_not_turned_into_lines():
    """The bias towards ink keeps dark thin strokes, not pale hatching (the shading is for the dots of §41, or for nothing): grey hatching 2 pixels wide, lighter
    than the line cut, leaves no ink where it was."""
    shapes, where = pencil_shapes()
    kept = layer_geometry(make(shapes, thin_lines="thicken").svg)
    region = Point(*where["hatching"]).buffer(3.0)
    assert not kept.intersects(region)


def test_a_wobbling_stroke_is_one_path_when_thickened_and_dashes_when_dropped():
    """§41.1 cause 2 through the whole engine: one stroke 3 to 9 pixels wide (0.3 to 0.9 mm) at the default smallest feature of 0.5 mm."""
    steps = [(x, 250 + 60 * math.sin(x / 60.0), 6.0 + 3.0 * math.sin(x / 23.0)) for x in range(40, 460, 2)]
    shapes = [("ellipse", (x - w / 2, y - w / 2, x + w / 2, y + w / 2), BLACK) for x, y, w in steps]
    dashes = make(shapes, thin_lines="drop")
    whole = make(shapes, thin_lines="thicken")
    assert dashes.check["layers"][0]["paths"] > 1
    assert whole.check["layers"][0]["paths"] == 1


def test_a_widened_line_is_wider_than_the_smallest_feature_whatever_it_was_drawn_as():
    """Criterion 319: lines drawn 1 to 4 pixels wide, flat and slanting, all come out strictly wider than the smallest feature (a strip exactly that wide would
    vanish in the opening that follows: it was measured) and not more than about three pixels wider (a staircase of pixels on a slant grows a little
    more than a flat row does), in one path each."""
    feature_mm = 0.5
    for drawn in (1, 2, 3, 4):
        for name, shape in (("flat", ("rect", (100, 250, 400, 250 + drawn), BLACK)),
                            ("slanting", ("poly", [(100, 100), (400, 400), (400 + drawn * 1.4, 400), (100 + drawn * 1.4, 100)], BLACK))):
            result = make([shape], min_feature_mm=feature_mm, thin_lines="thicken")
            assert result.check["layers"][0]["paths"] == 1, (name, drawn)
            width = strip_width(layer_geometry(result.svg))
            assert feature_mm < width <= feature_mm + 0.31, (name, drawn, width)


def test_specks_are_removed_before_widening_so_noise_does_not_become_blots():
    """Scattered one-pixel noise beside a solid shape: none of it is widened, and it is counted as specks."""
    rng = random.Random(7)
    shapes = [("rect", (200, 200, 300, 300), BLACK)]
    shapes += [("rect", (x, y, x + 1, y + 1), BLACK) for x, y in ((rng.randrange(20, 150), rng.randrange(20, 480)) for _ in range(25))]
    result = make(shapes, smooth=1, thin_lines="thicken")
    assert result.check["layers"][0]["paths"] == 1
    assert result.check["removed"]["specks"] >= 20 and "thin_widened" not in codes(result)


def test_the_same_picture_and_options_give_the_same_bytes():
    shapes, _ = pencil_shapes()
    first, second = make(shapes, thin_lines="thicken"), make(shapes, thin_lines="thicken")
    assert first.svg == second.svg and [layer.svg for layer in first.layers] == [layer.svg for layer in second.layers]


def test_the_check_says_what_was_done():
    shapes, _ = pencil_shapes()
    result = make(shapes, thin_lines="thicken")
    assert result.check["thin_lines"] == "thicken" and result.check["widened"] > 0
    message = next(note["message"] for note in result.check["notes"] if note["code"] == "thin_widened")
    assert "widened to 0.5 mm" in message and "narrower than 0.5 mm" in message


def test_a_smallest_feature_too_wide_to_widen_to_is_said_in_a_note():
    """A 5 mm smallest feature on a 50 mm sheet drawn at 500 pixels is 50 pixels: far past what is widened to. Thin parts go, as before, and the note says why."""
    shapes = [("rect", (100, 100, 400, 400), BLACK), ("rect", (50, 50, 52, 450), BLACK)]
    result = make(shapes, min_feature_mm=5.0, thin_lines="thicken")
    assert "thin_not_widened" in codes(result) and result.check["widened"] == 0


def test_the_colour_step_is_told_to_favour_ink_only_when_thin_lines_are_kept(monkeypatch):
    """`thicken` switches on the bias towards ink, and, for one ink only, the fringe clean-up off; with several inks the clean-up stays; `drop` is unchanged."""
    seen = []
    real = pixels.reduce_colours

    def spy(rgb, inks, palette, window, ink_bias=1.0):
        seen.append((inks, window, ink_bias))
        return real(rgb, inks, palette, window, ink_bias)

    monkeypatch.setattr(pixels, "reduce_colours", spy)
    shapes = [("rect", (100, 100, 400, 400), BLACK), ("rect", (150, 150, 250, 250), (200, 0, 0))]
    make(shapes, colours=2, thin_lines="thicken")
    make(shapes, colours=3, thin_lines="thicken")
    make(shapes, colours=2, thin_lines="drop")
    one_ink, two_inks, dropped = seen
    assert one_ink == (1, 1, pixels.THIN_LINES_INK_BIAS)
    assert two_inks[0] == 2 and two_inks[1] > 1 and two_inks[2] == pixels.THIN_LINES_INK_BIAS   # the fringe window stays (it is 5 pixels capped at 3)
    assert dropped[2] == 1.0 and dropped[1] > 1


def test_an_all_white_picture_is_still_an_error_when_thickened():
    with pytest.raises(VectorError, match="all white"):
        build_vector(as_png(picture([])), VectorOptions(width_mm=SHEET_MM, height_mm=SHEET_MM, thin_lines="thicken"))
