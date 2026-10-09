"""Make 4K (DESIGN.md §27; criteria 78-82, 88-93): which pictures are offered it, what is kept of them and the size of the copy,
and the file made.

The geometry is pure arithmetic and is pinned by examples, a sweep and the boundaries; the file work runs on real PNGs
built so that a trim, a stretch and a missing trim give different pixels."""

from __future__ import annotations

import io
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from datetime import datetime, timezone

from studio import fourk
from studio.fourk import NotEligible, can_4k, encode_png, file_size, make_4k, plan_4k, render_4k
from studio.naming import source_filename, upscale_filename
from studio.storage import Storage, StorageError


# ------------------------------------------------------------------ 16:9 (and 9:16): trimmed to exactly the frame
@pytest.mark.parametrize("width,height", [(1920, 1080), (2048, 1152), (2560, 1440), (3072, 1728), (3584, 2016), (3824, 2151)])
def test_an_exactly_16_9_picture_keeps_all_of_itself_and_is_not_called_trimmed(width, height):
    plan = plan_4k(width, height)
    assert plan.box == (0.0, 0.0, float(width), float(height))
    assert (plan.width, plan.height) == (width, height)
    assert (plan.out_width, plan.out_height, plan.trimmed) == (3840, 2160, False)


def test_every_exactly_16_9_size_in_range_keeps_all_of_itself_with_no_rounding_spill():
    """The box must never stick out of the picture by a rounding error: Pillow refuses a box larger than the picture."""
    for k in range(120, 240):  # 1920x1080 up to 3824x2151
        width, height = 16 * k, 9 * k
        assert plan_4k(width, height).box == (0.0, 0.0, float(width), float(height)), (width, height)
        assert plan_4k(height, width).box == (0.0, 0.0, float(height), float(width)), (height, width)  # and upright


def test_the_models_own_16_9_size_is_trimmed_in_width_equally_from_both_sides_and_gives_exactly_the_frame():
    plan = plan_4k(2752, 1536)
    left, top, right, bottom = plan.box
    assert (top, bottom) == (0.0, 1536.0)
    assert plan.width == pytest.approx(1536 * 16 / 9)  # 2730.67
    assert left == pytest.approx(10.6667, abs=1e-3)
    assert 2752 - right == pytest.approx(left)  # the same amount from each side
    assert right - left == pytest.approx(plan.width)
    assert (plan.out_width, plan.out_height, plan.trimmed) == (3840, 2160, True)


def test_a_picture_that_is_a_little_too_tall_is_trimmed_in_height_equally_from_the_top_and_bottom():
    plan = plan_4k(2560, 1450)  # 0.7% too tall
    left, top, right, bottom = plan.box
    assert (left, right) == (0.0, 2560.0)
    assert plan.height == pytest.approx(1440.0)
    assert top == pytest.approx(5.0) and 1450 - bottom == pytest.approx(5.0)
    assert (plan.out_width, plan.out_height, plan.trimmed) == (3840, 2160, True)


def test_an_upright_9_16_picture_is_trimmed_the_same_way_turned_and_gives_2160_by_3840():
    plan = plan_4k(1536, 2752)  # the model's own 9:16
    left, top, right, bottom = plan.box
    assert (left, right) == (0.0, 1536.0)  # the trim is in the long side, which is the height now
    assert top == pytest.approx(10.6667, abs=1e-3) and 2752 - bottom == pytest.approx(top)
    assert (plan.out_width, plan.out_height, plan.trimmed) == (2160, 3840, True)
    exact = plan_4k(1152, 2048)
    assert (exact.box, exact.out_width, exact.out_height, exact.trimmed) == ((0.0, 0.0, 1152.0, 2048.0), 2160, 3840, False)


def test_whatever_is_kept_of_a_16_9_picture_is_exactly_16_9_and_inside_it_over_a_sweep_of_sizes():
    for height in range(1090, 2160, 13):
        for width in range(int(height * 1.73), int(height * 1.83), 11):
            try:
                plan = plan_4k(width, height)
            except NotEligible:
                continue
            left, top, right, bottom = plan.box
            assert 0 <= left < right <= width and 0 <= top < bottom <= height, (width, height, plan.box)
            if plan.trimmed or (plan.out_width, plan.out_height) == (3840, 2160):  # within 2% of 16:9
                assert (right - left) * 9 == pytest.approx((bottom - top) * 16, rel=1e-9), (width, height)
                assert left == pytest.approx(width - right, abs=1e-9) and top == pytest.approx(height - bottom, abs=1e-9)


def test_the_box_is_inside_the_picture_for_every_eligible_size_in_range():
    """There is no clamp on the box, because none is needed (the arithmetic is exact when the short side is cut and the long
    side's cut ends at least 1/9 pixel inside). This is that argument checked on every size, not a sample: Pillow refuses a
    box that sticks out, so one failure here would be an error on the page."""
    checked = 0
    for height in range(1000, 2200):
        for width in range(int(height * 16 / 9 * 0.979), int(height * 16 / 9 * 1.021) + 2):  # the 2% band, with a margin
            try:
                plan = plan_4k(width, height)
            except NotEligible:
                continue
            for w, h in ((width, height), (height, width)):  # landscape, and the same picture upright
                left, top, right, bottom = plan_4k(w, h).box
                assert 0 <= left < right <= w and 0 <= top < bottom <= h, (w, h)
            checked += 1
    assert checked > 50_000  # it really did look at a great many sizes (and the band's edges are refused, not skipped)


# ------------------------------------------------------------------ the 2% boundary (inclusive, exact)
@pytest.mark.parametrize("width", [2091, 2133, 2175, 2176])
def test_within_two_percent_of_16_9_is_trimmed_to_exactly_the_frame(width):
    plan = plan_4k(width, 1200)
    assert (plan.out_width, plan.out_height) == (3840, 2160)


@pytest.mark.parametrize("width", [2090, 2177, 2300])
def test_further_than_two_percent_from_16_9_is_scaled_to_cover_the_frame_and_not_trimmed(width):
    plan = plan_4k(width, 1200)
    assert not plan.trimmed and plan.box == (0.0, 0.0, float(width), 1200.0)
    assert (plan.out_width, plan.out_height) != (3840, 2160)  # one side is the frame's, the other follows the shape
    assert plan.out_width >= 3840 and plan.out_height >= 2160


# ------------------------------------------------------------------ any other shape: cover the frame, trim nothing
@pytest.mark.parametrize("width,height,out", [
    (2048, 2048, (3840, 3840)),   # the default size: 1.875x
    (2400, 1792, (3840, 2867)),   # 4:3: 1.6x
    (2528, 1696, (3840, 2576)),   # 3:2
    (1792, 2400, (2867, 3840)),   # 3:4 upright
    (1696, 2528, (2576, 3840)),   # 2:3 upright
    (1920, 1920, (3840, 3840)),   # exactly 2x
    (3000, 2200, (3840, 2816)),   # the short side is already past 2160 but the long one is not 3840
    (3840, 2000, (4147, 2160)),   # the long side is 4K already; the short one is not, so it grows to 2160
])
def test_other_shapes_are_scaled_to_cover_the_frame_with_nothing_cut_off(width, height, out):
    plan = plan_4k(width, height)
    assert (plan.out_width, plan.out_height) == out
    assert plan.box == (0.0, 0.0, float(width), float(height)) and not plan.trimmed


def test_a_cover_copy_has_one_side_exactly_the_frames_covers_it_and_keeps_the_shape_over_many_sizes():
    checked = 0
    for width in range(1100, 3900, 37):
        for height in range(1100, 3900, 41):
            try:
                plan = plan_4k(width, height)
            except NotEligible:
                continue
            checked += 1
            out_long, out_short = max(plan.out_width, plan.out_height), min(plan.out_width, plan.out_height)
            assert out_long >= 3840 and out_short >= 2160, (width, height)  # it covers the frame, in either way up
            assert out_long == 3840 or out_short == 2160, (width, height)  # and one side is exactly the frame's
            if width > height:  # the way up is kept (ties are possible only where rounding meets a half)
                assert plan.out_width >= plan.out_height, (width, height)
            if width < height:
                assert plan.out_width <= plan.out_height, (width, height)
            if not plan.trimmed:  # a trimmed (16:9 or 9:16) picture is cut to the frame's shape on purpose
                # the shape is kept to the nearest pixel: cross-multiplied sides differ by at most the rounding of one side
                assert abs(plan.out_width * height - plan.out_height * width) <= width + height, (width, height)
    assert checked > 500


# ------------------------------------------------------------------ the size rules
def test_up_to_a_doubling_is_offered_and_more_is_a_blurry_picture():
    assert can_4k(1920, 1080) and can_4k(1920, 1920) and can_4k(2000, 1100)
    for width, height, needed in ((1919, 1080, "2.1"), (1024, 1024, "3.8"), (1900, 1900, "2.1"), (2000, 1000, "2.2")):
        with pytest.raises(NotEligible, match=rf"{width}×{height}\) is too small.*{needed}× enlargement.*up to 2×"):
            plan_4k(width, height)


def test_it_is_the_enlargement_of_the_picture_as_it_will_be_trimmed_that_counts():
    # 1930 wide and 1070 tall is 1.4% wider than 16:9, so its width is trimmed to 1902.2 first, and that needs more than 2x,
    # although 1930 itself would have passed
    with pytest.raises(NotEligible, match="too small.*2.1× enlargement"):
        plan_4k(1930, 1070)


@pytest.mark.parametrize("width,height", [(512, 288), (1280, 720), (1024, 576), (960, 540), (256, 256), (1536, 864)])
def test_small_pictures_such_as_drafts_are_not_offered(width, height):
    assert not can_4k(width, height)


@pytest.mark.parametrize("width,height", [(3840, 2160), (4096, 2304), (5000, 2812), (3840, 3840), (4096, 2160), (2160, 3840), (6000, 4000)])
def test_a_picture_that_is_already_4k_is_not_offered(width, height):
    with pytest.raises(NotEligible, match="already 4K"):
        plan_4k(width, height)


def test_just_under_4k_in_one_side_is_offered():
    assert can_4k(3839, 2160) and can_4k(3840, 2159) and can_4k(3839, 3839)


def test_a_copy_over_20_megapixels_is_refused_even_when_the_enlargement_is_small():
    # 7000x1100 is 7.7 MP and needs only 1.96x, but its copy would be 13745x2160: 29.7 MP
    with pytest.raises(NotEligible, match=r"\(7000×1100\) would be 30 megapixels; the limit is 20"):
        plan_4k(7000, 1100)
    assert can_4k(2048, 2048) and not can_4k(8000, 1100)  # a square copy (14.7 MP) is fine


def test_a_nonsense_size_is_not_offered():
    assert not can_4k(0, 0) and not can_4k(1920, 0) and not can_4k(-1920, 1080)


# ------------------------------------------------------------------ the file
def stripes(path: Path, width=2752, height=1536, **save) -> Path:
    """A picture whose colour depends on x, so a trim, a stretch and no trim put features in different places:
    red for x < 100, blue in the middle, green for x >= width - 100, and a white 6-pixel line at x = 200."""
    img = Image.new("RGB", (width, height), (0, 0, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 99, height], fill=(255, 0, 0))
    draw.rectangle([width - 100, 0, width, height], fill=(0, 255, 0))
    draw.rectangle([200, 0, 205, height], fill=(255, 255, 255))
    img.save(path, **save)
    return path


def line_centre(img: Image.Image, y: int = 500) -> float:
    """Where the white line is, in pixel-edge coordinates: the middle of the columns that are white (min channel high).
    The stripes' other colours have a zero channel, so only the line counts."""
    rgb = img.convert("RGB")  # once, not once per column
    weights = [min(rgb.getpixel((x, y))) for x in range(rgb.width)]
    return sum((x + 0.5) * w for x, w in enumerate(weights)) / sum(weights)


def test_it_makes_exactly_3840_by_2160(tmp_path):
    src = stripes(tmp_path / "0.png")
    dst = make_4k(src, tmp_path / "0-4k.png")
    with Image.open(dst) as out:
        assert out.size == (3840, 2160) and out.format == "PNG" and out.mode == "RGB"


def test_the_picture_is_trimmed_not_stretched(tmp_path):
    out = Image.open(make_4k(stripes(tmp_path / "0.png"), tmp_path / "0-4k.png"))
    # the white line's middle is at source x = 203; with the trim it lands at (203 - 10.667) / 0.7111 = 270.5; stretched it
    # would land at 203 x 3840 / 2752 = 283.2, and enlarged with no trim at all at 203 x 3840 / 2730.7 = 285.5
    assert line_centre(out) == pytest.approx(270.5, abs=1.5)


def test_the_trim_is_the_same_on_both_sides(tmp_path):
    out = Image.open(make_4k(stripes(tmp_path / "0.png"), tmp_path / "0-4k.png")).convert("RGB")
    # red ends at source x = 100 and green starts at 2652: 89.3 and 100.0 pixels of source from the trimmed edges
    # (10.667 + ... ), so in the output the red band is (100 - 10.667) / 0.7111 = 125.6 wide and the green one
    # (2741.333 - 2652) / 0.7111 = 125.6 wide: equal
    red = sum(1 for x in range(out.width) if out.getpixel((x, 500))[0] > 200 and out.getpixel((x, 500))[2] < 60)
    green = sum(1 for x in range(out.width) if out.getpixel((x, 500))[1] > 200 and out.getpixel((x, 500))[2] < 60)
    assert red == pytest.approx(125.6, abs=3) and green == pytest.approx(125.6, abs=3)
    assert abs(red - green) <= 2


def test_the_resampling_is_one_lanczos_pass_over_the_trim_box_and_nothing_else(tmp_path):
    """Exactly what Pillow gives for a single resize of that box: not a trim followed by a resize (two passes), and not a
    cheaper filter. Fine detail is what tells a Lanczos pass from a bilinear one."""
    from PIL import ImageChops

    src = stripes(tmp_path / "0.png")
    with Image.open(src) as picture:
        expected = picture.convert("RGB").resize((3840, 2160), Image.Resampling.LANCZOS, box=plan_4k(2752, 1536).box)
    made = Image.open(make_4k(src, tmp_path / "0-4k.png")).convert("RGB")
    assert ImageChops.difference(expected, made).getbbox() is None
    bilinear = Image.open(src).convert("RGB").resize((3840, 2160), Image.Resampling.BILINEAR, box=plan_4k(2752, 1536).box)
    assert ImageChops.difference(bilinear, made).getbbox() is not None  # and the check can tell the filters apart


def test_a_picture_that_needs_no_trim_is_only_enlarged(tmp_path):
    out = Image.open(make_4k(stripes(tmp_path / "0.png", 2560, 1440), tmp_path / "0-4k.png"))
    assert out.size == (3840, 2160)
    assert line_centre(out, 700) == pytest.approx(203 * 1.5, abs=1.5)  # a factor of exactly 1.5 and nothing cut


def test_the_original_is_not_touched(tmp_path):
    src = stripes(tmp_path / "0.png")
    before = src.read_bytes()
    make_4k(src, tmp_path / "0-4k.png")
    assert src.read_bytes() == before


def test_transparency_is_kept(tmp_path):
    img = Image.new("RGBA", (2560, 1440), (255, 0, 0, 0))  # transparent red
    ImageDraw.Draw(img).rectangle([800, 400, 1700, 1000], fill=(0, 255, 0, 255))
    img.save(tmp_path / "0.png")
    out = Image.open(make_4k(tmp_path / "0.png", tmp_path / "0-4k.png"))
    assert out.mode == "RGBA" and out.size == (3840, 2160)
    assert out.getpixel((10, 10))[3] == 0  # still transparent outside
    assert out.getpixel((1920, 1080)) == (0, 255, 0, 255)  # opaque inside
    # no red fringe where the green meets the transparent area (resampling is done on premultiplied colour)
    edge = [out.getpixel((x, 1080)) for x in range(1190, 1215)]
    assert all(r < 40 for r, g, b, a in edge if a > 0)


def test_a_palette_picture_with_transparency_becomes_rgba_and_a_grey_one_rgb(tmp_path):
    base = Image.new("RGB", (2560, 1440), (10, 200, 30))
    paletted = base.convert("P")
    paletted.save(tmp_path / "p.png", transparency=0)
    assert Image.open(make_4k(tmp_path / "p.png", tmp_path / "p4.png")).mode == "RGBA"
    base.convert("L").save(tmp_path / "g.png")
    assert Image.open(make_4k(tmp_path / "g.png", tmp_path / "g4.png")).mode == "RGB"


def test_the_pngs_text_is_kept_and_one_line_says_how_the_file_was_made(tmp_path):
    info = PngInfo()
    info.add_text("prompt", "a harbour at dawn")
    info.add_text("seed", "42")
    stripes(tmp_path / "0.png", pnginfo=info)
    out = Image.open(make_4k(tmp_path / "0.png", tmp_path / "0-4k.png"))
    assert out.text["prompt"] == "a harbour at dawn" and out.text["seed"] == "42"
    assert "2752x1536" in out.text["make4k"] and "16:9" in out.text["make4k"] and "no detail" in out.text["make4k"]


def test_a_picture_that_cannot_be_made_4k_raises_and_leaves_nothing_behind(tmp_path):
    Image.new("RGB", (1024, 1024)).save(tmp_path / "0.png")
    with pytest.raises(NotEligible):
        make_4k(tmp_path / "0.png", tmp_path / "0-4k.png")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["0.png"]


def test_it_is_judged_on_the_file_not_on_what_anyone_said_about_it(tmp_path):
    stripes(tmp_path / "0.png", 1280, 720)  # a small file, whatever the database might claim
    with pytest.raises(NotEligible, match="too small"):
        make_4k(tmp_path / "0.png", tmp_path / "0-4k.png")


def test_a_missing_original_is_a_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_4k(tmp_path / "gone.png", tmp_path / "gone-4k.png")


def test_a_failure_while_writing_leaves_no_partial_file_and_no_result(tmp_path):
    src = stripes(tmp_path / "0.png")
    with mock.patch.object(Image.Image, "save", side_effect=OSError(28, "No space left on device")):
        with pytest.raises(OSError, match="No space"):
            make_4k(src, tmp_path / "0-4k.png")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["0.png"]  # not even a .part


def test_a_result_is_written_whole_or_not_at_all(tmp_path):
    """The destination appears only by a rename of a finished file: while the encoder is running there is no file at it."""
    src = stripes(tmp_path / "0.png")
    seen: list[bool] = []
    levels: list[int] = []
    real_save = Image.Image.save

    def watching(self, fp, *args, **kwargs):
        seen.append((tmp_path / "0-4k.png").exists())
        levels.append(kwargs.get("compress_level"))
        return real_save(self, fp, *args, **kwargs)

    with mock.patch.object(Image.Image, "save", watching):
        make_4k(src, tmp_path / "0-4k.png")
    assert seen == [False] and (tmp_path / "0-4k.png").is_file()
    assert levels == [fourk.PNG_LEVEL]  # the fast encoder level is really the one used
    assert [p.name for p in tmp_path.glob("*.part")] == []


def test_an_existing_result_is_replaced_whole(tmp_path):
    src = stripes(tmp_path / "0.png")
    (tmp_path / "0-4k.png").write_bytes(b"old and broken")
    make_4k(src, tmp_path / "0-4k.png")
    assert Image.open(tmp_path / "0-4k.png").size == (3840, 2160)


def test_it_makes_the_destination_folder_if_needed(tmp_path):
    src = stripes(tmp_path / "0.png")
    assert make_4k(src, tmp_path / "deeper" / "still" / "0-4k.png").is_file()


# ------------------------------------------------------------------ where it lives
def test_the_4k_copy_lives_beside_the_original(tmp_path):
    storage = Storage(tmp_path)
    assert storage.four_k_path("images/" + "ab" * 16 + "/3.png") == tmp_path.resolve() / "images" / ("ab" * 16) / "3-4k.png"


def test_a_path_that_leaves_the_data_folder_is_refused(tmp_path):
    with pytest.raises(StorageError):
        Storage(tmp_path).four_k_path("../outside/0.png")


def test_the_png_encoder_level_is_the_fast_one():
    """Measured (DESIGN.md §27.3): level 1 is four times faster than the default for a file about 12% larger."""
    assert fourk.PNG_LEVEL == 1
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buffer, format="PNG", compress_level=fourk.PNG_LEVEL)
    assert buffer.getvalue().startswith(b"\x89PNG")


# ------------------------------------------------------------------ any shape (DESIGN.md §27.9)
def test_a_square_picture_is_enlarged_to_3840_by_3840_with_nothing_cut_off(tmp_path):
    out = Image.open(make_4k(stripes(tmp_path / "0.png", 2048, 2048), tmp_path / "0-4k.png")).convert("RGB")
    assert out.size == (3840, 3840)
    # nothing is trimmed: the red edge (source x 0..99) and the green edge (the last 100) are both in the copy, and the white
    # line, at source x 203, lands at 203 x 1.875 = 380.6 (a trimmed or stretched copy would put it elsewhere)
    assert out.getpixel((3, 900))[0] > 200 and out.getpixel((3, 900))[2] < 60
    assert out.getpixel((3836, 900))[1] > 200 and out.getpixel((3836, 900))[2] < 60
    assert line_centre(out, 900) == pytest.approx(203 * 1.875, abs=1.5)


def test_a_4_3_picture_gets_the_size_the_rule_gives(tmp_path):
    assert Image.open(make_4k(stripes(tmp_path / "0.png", 2400, 1792), tmp_path / "0-4k.png")).size == (3840, 2867)


def test_an_upright_picture_is_trimmed_top_and_bottom_the_way_a_landscape_one_is_trimmed_left_and_right(tmp_path):
    landscape = Image.open(stripes(tmp_path / "land.png"))  # 2752x1536, with its features along x
    landscape.transpose(Image.Transpose.TRANSPOSE).save(tmp_path / "0.png")  # 1536x2752, the same features along y
    out = Image.open(make_4k(tmp_path / "0.png", tmp_path / "0-4k.png"))
    assert out.size == (2160, 3840)
    assert line_centre(out.transpose(Image.Transpose.TRANSPOSE), 500) == pytest.approx(270.5, abs=1.5)  # as for the landscape one


def test_the_note_says_whether_the_picture_was_trimmed(tmp_path):
    with Image.open(stripes(tmp_path / "a.png")) as wide:
        text = render_4k(wide)[1]["make4k"]
    assert text == "Trimmed to 16:9 and enlarged with a Lanczos resize from 2752x1536 to 3840x2160; no detail was added."
    with Image.open(stripes(tmp_path / "b.png", 2048, 2048)) as square:
        text = render_4k(square)[1]["make4k"]
    assert text == "Enlarged with a Lanczos resize from 2048x2048 to 3840x3840; no detail was added."


def test_a_picture_that_is_not_offered_raises_in_render_too(tmp_path):
    with Image.open(stripes(tmp_path / "a.png", 1024, 1024)) as small, pytest.raises(NotEligible, match="too small"):
        render_4k(small)


def test_encode_png_gives_the_png_bytes_with_the_text_in_them(tmp_path):
    result, text, plan = render_4k(Image.open(stripes(tmp_path / "a.png", 2048, 2048)))
    data = encode_png(result, {**text, "prompt": "a harbour"})
    assert data[:8] == fourk.PNG_SIGNATURE
    again = Image.open(io.BytesIO(data))
    assert again.size == (plan.out_width, plan.out_height) and again.text["prompt"] == "a harbour" and "make4k" in again.text


def test_the_size_of_a_png_is_read_from_its_header(tmp_path):
    made = make_4k(stripes(tmp_path / "0.png", 2048, 2048), tmp_path / "0-4k.png")
    assert file_size(made) == (3840, 3840)


def test_a_file_that_is_not_a_png_has_no_size(tmp_path):
    assert file_size(tmp_path / "missing.png") is None
    (tmp_path / "text.png").write_bytes(b"this is not a png at all, not even close")
    assert file_size(tmp_path / "text.png") is None
    (tmp_path / "short.png").write_bytes(fourk.PNG_SIGNATURE + b"\x00\x00")
    assert file_size(tmp_path / "short.png") is None
    good = (tmp_path / "good.png"); Image.new("RGB", (5, 3)).save(good)
    assert file_size(good) == (5, 3)
    broken = bytearray(good.read_bytes()); broken[12:16] = b"IDAT"  # the first chunk must be the header
    (tmp_path / "broken.png").write_bytes(bytes(broken))
    assert file_size(tmp_path / "broken.png") is None


# ------------------------------------------------------------------ download names
WHEN = datetime(2026, 10, 9, 18, 15, 0, tzinfo=timezone.utc)


def test_a_sources_4k_copy_is_named_for_its_place_prompt_and_size():
    assert source_filename(prompt="Put the dog from image 1 on a beach", position=2, width=3840, height=3840, created_at=WHEN) \
        == "source-2_put-dog-image-1-beach_3840x3840_20261009-181500.png"


@pytest.mark.parametrize("name,stem", [
    ("My Holiday Photo.JPG", "my-holiday-photo"),
    ("C:\\Users\\me\\pics\\dog.png", "dog"),          # a Windows path: only the file's own name
    ("../../etc/passwd.png", "passwd"),                 # nothing to climb out of
    ("a.very.long.name.with.dots.webp", "long-name-dots"),    # the same slug rule as every other download name drops filler words
    ("写真.jpg", "写真"),                               # other scripts are kept
    ("", "untitled"),
    ("!!!.png", "untitled"),
])
def test_a_picture_from_the_computer_is_named_for_its_file(name, stem):
    assert upscale_filename(name=name, width=3840, height=2160, created_at=WHEN) == f"upscale_{stem}_3840x2160_20261009-181500.png"

