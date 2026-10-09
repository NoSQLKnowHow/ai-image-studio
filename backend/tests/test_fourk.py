"""Make 4K (DESIGN.md §27; criteria 78-82): which pictures are offered it, what is kept of them, and the file made.

The geometry is pure arithmetic and is pinned by examples, a sweep and the boundaries; the file work runs on real PNGs
built so that a trim, a stretch and a missing trim give different pixels."""

from __future__ import annotations

import io
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from studio import fourk
from studio.fourk import NotEligible, can_4k, make_4k, plan_4k
from studio.storage import Storage, StorageError


# ------------------------------------------------------------------ which pictures
@pytest.mark.parametrize("width,height", [(1920, 1080), (2048, 1152), (2560, 1440), (3072, 1728), (3584, 2016), (3824, 2151)])
def test_an_exactly_16_9_picture_keeps_all_of_itself(width, height):
    plan = plan_4k(width, height)
    assert plan.box == (0.0, 0.0, float(width), float(height))
    assert (plan.width, plan.height) == (width, height)


def test_every_exactly_16_9_size_in_range_keeps_all_of_itself_with_no_rounding_spill():
    """The box must never stick out of the picture by a rounding error: Pillow refuses a box larger than the picture."""
    for k in range(120, 240):  # 1920x1080 up to 3824x2151
        width, height = 16 * k, 9 * k
        assert plan_4k(width, height).box == (0.0, 0.0, float(width), float(height)), (width, height)


def test_the_models_own_16_9_size_is_trimmed_in_width_equally_from_both_sides():
    plan = plan_4k(2752, 1536)
    left, top, right, bottom = plan.box
    assert (top, bottom) == (0.0, 1536.0)
    assert plan.width == pytest.approx(1536 * 16 / 9)  # 2730.67
    assert left == pytest.approx(10.6667, abs=1e-3)
    assert 2752 - right == pytest.approx(left)  # the same amount from each side
    assert right - left == pytest.approx(plan.width)


def test_a_picture_that_is_too_tall_is_trimmed_in_height_equally_from_the_top_and_bottom():
    plan = plan_4k(2560, 1450)  # 0.7% too tall
    left, top, right, bottom = plan.box
    assert (left, right) == (0.0, 2560.0)
    assert plan.height == pytest.approx(1440.0)
    assert top == pytest.approx(5.0) and 1450 - bottom == pytest.approx(5.0)


def test_whatever_is_kept_is_exactly_16_9_and_inside_the_picture_over_a_sweep_of_sizes():
    for height in range(1090, 2160, 13):
        for width in range(int(height * 1.73), int(height * 1.83), 11):
            try:
                plan = plan_4k(width, height)
            except NotEligible:
                continue
            left, top, right, bottom = plan.box
            assert 0 <= left < right <= width and 0 <= top < bottom <= height, (width, height, plan.box)
            assert (right - left) * 9 == pytest.approx((bottom - top) * 16, rel=1e-9), (width, height)
            assert left == pytest.approx(width - right, abs=1e-9) and top == pytest.approx(height - bottom, abs=1e-9)


# ------------------------------------------------------------------ the 2% boundary (inclusive, exact)
@pytest.mark.parametrize("width", [2091, 2133, 2175, 2176])
def test_within_two_percent_of_16_9_is_offered(width):
    assert can_4k(width, 1200)


@pytest.mark.parametrize("width", [2090, 2177, 2300, 1900 * 2])
def test_further_than_two_percent_from_16_9_is_not(width):
    assert not can_4k(width, 1200)
    with pytest.raises(NotEligible, match="16:9"):
        plan_4k(width, 1200)


@pytest.mark.parametrize("width,height", [(2048, 2048), (2400, 1792), (2528, 1696), (1536, 2752), (2000, 1000)])
def test_other_shapes_are_not_offered_and_the_reason_names_the_size(width, height):
    with pytest.raises(NotEligible, match=f"16:9 pictures; this one is {width}×{height}"):
        plan_4k(width, height)


# ------------------------------------------------------------------ the size rules
def test_the_smallest_offered_width_is_1920_after_the_trim():
    assert can_4k(1920, 1080)
    with pytest.raises(NotEligible, match="too small.*at least 1920.*1919"):
        plan_4k(1919, 1080)


def test_it_is_the_trimmed_width_that_counts_not_the_pictures():
    # 1930 wide and 1070 tall is 1.4% wider than 16:9, so its width is trimmed to 1902.2 and it is too small,
    # although 1930 itself would have passed
    assert 1930 >= fourk.MIN_TRIMMED_WIDTH
    with pytest.raises(NotEligible, match="too small.*1902"):
        plan_4k(1930, 1070)


@pytest.mark.parametrize("width,height", [(512, 288), (1280, 720), (1024, 576), (960, 540)])
def test_small_pictures_such_as_drafts_are_not_offered(width, height):
    assert not can_4k(width, height)


@pytest.mark.parametrize("width,height", [(3840, 2160), (4096, 2304), (5000, 2812)])
def test_a_picture_that_is_already_4k_is_not_offered(width, height):
    with pytest.raises(NotEligible, match="already 4K"):
        plan_4k(width, height)


def test_just_under_4k_is_offered():
    assert can_4k(3839, 2160)


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
    weights = [min(img.convert("RGB").getpixel((x, y))) for x in range(img.width)]
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
