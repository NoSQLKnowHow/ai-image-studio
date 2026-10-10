"""Enlarge's rules (DESIGN.md §28.2; criteria 94-96, 105): when a picture qualifies, how many x2 passes of the model it takes, and
what the copy is, with the model replaced by a stand-in that only enlarges. The model itself needs PyTorch and is checked in
test_enlarge_torch.py; the job around it in test_upscale_job.py; the server in test_enlarge_api.py."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from studio import fourk
from studio.fourk import ENLARGE_MAX, NotEligible, SINGLE_PASS_MAX, plan_enlarge


def nearest(calls: list | None = None):
    """A stand-in for the model: enlarges by 2**passes with no detail added, and records what it was asked."""

    def upscale(image: Image.Image, passes: int) -> Image.Image:
        if calls is not None:
            calls.append((image.mode, image.size, passes))
        return image.resize((image.width * 2 ** passes, image.height * 2 ** passes), Image.Resampling.NEAREST)

    return upscale


# ------------------------------------------------------------------ the plan
@pytest.mark.parametrize("size,out,passes", [
    ((2752, 1536), (3840, 2160), 1),  # the model's own size: 1.4x
    ((1376, 768), (3840, 2160), 1),  # 50%: 2.8x
    ((1280, 720), (3840, 2160), 1),  # exactly 3.0x: one pass is still enough
    ((1272, 716), (3840, 2160), 2),  # just over 3x
    ((1264, 711), (3840, 2160), 2),
    ((960, 540), (3840, 2160), 2),  # exactly 4.0x: allowed
    ((2048, 2048), (3840, 3840), 1),  # 1.875x
    ((1024, 1024), (3840, 3840), 2),  # 3.75x
    ((960, 960), (3840, 3840), 2),  # exactly 4x
    ((1536, 2752), (2160, 3840), 1),  # upright
    ((768, 1376), (2160, 3840), 1),
    ((3000, 2000), (3840, 2560), 1),
])
def test_the_frame_and_the_number_of_passes(size, out, passes):
    plan = plan_enlarge(*size)
    assert (plan.plan.out_width, plan.plan.out_height) == out
    assert plan.passes == passes and plan.factor == 2 ** passes


def test_the_constants_are_the_ones_the_spec_names():
    assert (ENLARGE_MAX, SINGLE_PASS_MAX) == (4, 3)


@pytest.mark.parametrize("size,needed", [((956, 538), "4.1"), ((952, 536), "4.1"), ((688, 384), "5.7"), ((512, 512), "7.5")])
def test_a_picture_that_needs_more_than_4x_is_refused_with_the_reason_and_the_buttons_name(size, needed):
    with pytest.raises(NotEligible) as info:
        plan_enlarge(*size)
    message = str(info.value)
    assert f"{needed}× enlargement" in message and "Enlarge goes up to 4×" in message and "Make 4K" not in message


def test_make_4k_still_refuses_what_it_always_refused():
    with pytest.raises(NotEligible) as info:
        fourk.plan_4k(1376, 768)
    assert "Make 4K goes up to 2×" in str(info.value) and "Enlarge" not in str(info.value)
    assert fourk.can_4k(1376, 768) is False and fourk.enlarge_plan_or_none(1376, 768) is not None


def test_a_picture_that_is_already_4k_is_refused_by_enlarge_too():
    for size in ((3840, 2160), (4096, 2304), (2160, 3840)):
        with pytest.raises(NotEligible, match="already 4K"):
            plan_enlarge(*size)


def test_the_20_megapixel_guard_applies_to_enlarge_as_it_does_to_make_4k():
    with pytest.raises(NotEligible, match="23 megapixels"):
        plan_enlarge(5000, 1000)


def test_a_picture_with_no_size_is_refused():
    with pytest.raises(NotEligible):
        plan_enlarge(0, 100)


def test_enlarge_plan_or_none():
    assert fourk.enlarge_plan_or_none(2752, 1536) is not None
    assert fourk.enlarge_plan_or_none(512, 512) is None and fourk.enlarge_plan_or_none(3840, 2160) is None


def test_the_plan_carries_the_enlargement_and_the_same_box_as_make_4k_where_both_apply():
    both = plan_enlarge(2752, 1536).plan, fourk.plan_4k(2752, 1536)
    assert both[0].box == both[1].box and both[0].trimmed and both[1].trimmed
    assert both[0].scale == pytest.approx(3840 / (1536 * 16 / 9)) == pytest.approx(both[1].scale)
    assert plan_enlarge(2048, 2048).plan.scale == pytest.approx(3840 / 2048)


# ------------------------------------------------------------------ the copy
def line_picture(width: int, height: int, xs: tuple[int, ...]) -> Image.Image:
    picture = Image.new("RGB", (width, height), (0, 0, 0))
    for x in xs:
        for y in range(height):
            picture.putpixel((x, y), (255, 255, 255))
    return picture


def bright_columns(picture: Image.Image, threshold: int = 60) -> list[int]:
    grey = picture.convert("L")
    row = [grey.getpixel((x, grey.height // 2)) for x in range(grey.width)]
    return [x for x, value in enumerate(row) if value > threshold]


def test_the_copy_is_exactly_the_frame_and_the_model_is_asked_for_the_right_passes():
    for size, passes in (((2752, 1536), 1), ((1280, 720), 1), ((1272, 716), 2), ((960, 540), 2)):
        calls: list = []
        result, text, plan = fourk.render_enlarged(Image.new("RGB", size, (10, 20, 30)), nearest(calls), "a stand-in")
        assert result.size == (3840, 2160) and plan.passes == passes
        assert calls == [("RGB", size, passes)]  # called once, with the whole picture and the number of passes


def test_the_model_gets_the_colour_only_and_the_alpha_is_put_back_resized():
    calls: list = []
    source = Image.new("RGBA", (2048, 2048), (200, 100, 50, 255))
    for x in range(1024):
        for y in range(0, 2048, 64):
            source.putpixel((x, y), (200, 100, 50, 0))  # a transparent stripe on the left half, every 64th row
    result, _, _ = fourk.render_enlarged(source, nearest(calls), "a stand-in")
    assert calls[0][0] == "RGB"  # never RGBA: a network trained on three channels
    assert result.mode == "RGBA" and result.size == (3840, 3840)
    alphas = {result.getpixel((100, y))[3] for y in range(0, 3840, 7)}
    assert 0 in alphas and 255 in alphas  # the transparent rows survived and the opaque ones are opaque
    assert result.getpixel((3000, 1000))[3] == 255  # the right half has no transparent rows at all


def test_a_picture_without_alpha_stays_rgb():
    result, _, _ = fourk.render_enlarged(Image.new("RGB", (2048, 2048), (1, 2, 3)), nearest(), "a stand-in")
    assert result.mode == "RGB"


def test_a_palette_picture_with_transparency_keeps_it():
    palette = Image.new("P", (2048, 2048), 0)
    palette.putpalette([255, 0, 0] + [0] * 765)
    palette.info["transparency"] = 0
    result, _, _ = fourk.render_enlarged(palette, nearest(), "a stand-in")
    assert result.mode == "RGBA" and result.getpixel((5, 5))[3] == 0


def test_a_16_9_picture_is_trimmed_equally_from_both_ends_through_the_model_too():
    """2752x1536 loses 10.67 px each side. Lines inside the cut zones must be gone; a line at the centre must stay at the centre."""
    cut_left, cut_right, centre = 5, 2746, 1376
    result, _, plan = fourk.render_enlarged(line_picture(2752, 1536, (cut_left, centre, cut_right)), nearest(), "a stand-in")
    assert plan.plan.trimmed
    columns = bright_columns(result)
    assert columns and abs((columns[0] + columns[-1]) / 2 - 1919.5) < 3, columns[:3]  # the centre line is the centre of the frame
    assert all(abs(x - 1920) < 12 for x in columns), columns  # and it is the only line: the two in the cut zones are gone


def test_the_box_is_scaled_by_the_passes_so_a_two_pass_copy_is_cut_the_same_way():
    """960x540 is exactly 16:9: nothing is cut. Lines near both edges must both be in the copy, near its edges."""
    result, _, plan = fourk.render_enlarged(line_picture(960, 540, (3, 956)), nearest(), "a stand-in")
    assert plan.passes == 2 and not plan.plan.trimmed
    columns = bright_columns(result)
    assert columns[0] < 40 and columns[-1] > 3800, (columns[0], columns[-1])


def test_a_model_that_returns_the_wrong_size_is_an_error_not_a_misshapen_picture():
    def shrinking(image, passes):
        return image.resize((image.width, image.height))  # forgot to enlarge

    with pytest.raises(ValueError, match="should be 5504×3072"):
        fourk.render_enlarged(Image.new("RGB", (2752, 1536)), shrinking, "a stand-in")

    def one_pass_short(image, passes):  # a x2 model asked for two passes that only does one
        return image.resize((image.width * 2, image.height * 2))

    with pytest.raises(ValueError, match="2 passes"):
        fourk.render_enlarged(Image.new("RGB", (960, 540)), one_pass_short, "a stand-in")


def test_the_note_names_the_model_the_size_and_what_it_means_for_the_detail():
    original = Image.new("RGB", (2752, 1536))
    original.text = {"prompt": "a harbour"}  # what the studio's own pictures carry
    _, text, _ = fourk.render_enlarged(original, nearest(), "ESRGAN ×2 (RealESRGAN_x2plus.pth)")
    assert text["prompt"] == "a harbour"
    note = text["make4k"]
    assert note.startswith("Trimmed to 16:9 and enlarged with the upscaler model ESRGAN ×2 (RealESRGAN_x2plus.pth) (×2)")
    assert "from 2752x1536 to 3840x2160" in note and "the model's guess" in note
    _, text, _ = fourk.render_enlarged(Image.new("RGB", (2048, 2048)), nearest(), "m")
    assert text["make4k"].startswith("Enlarged with the upscaler model m (×2)")
    _, text, _ = fourk.render_enlarged(Image.new("RGB", (960, 540)), nearest(), "m")
    assert "(×4)" in text["make4k"]


def test_a_picture_that_cannot_be_enlarged_is_refused_before_the_model_is_called():
    calls: list = []
    with pytest.raises(NotEligible):
        fourk.render_enlarged(Image.new("RGB", (512, 512)), nearest(calls), "m")
    assert calls == []


# ------------------------------------------------------------------ the file
def write_png(path: Path, size: tuple[int, int], **text: str) -> Path:
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    for key, value in text.items():
        info.add_text(key, value)
    Image.new("RGB", size, (30, 60, 90)).save(path, pnginfo=info)
    return path


def test_the_file_is_written_whole_with_the_originals_text_and_no_leftovers(tmp_path):
    source = write_png(tmp_path / "0.png", (2752, 1536), prompt="a harbour")
    target = tmp_path / "0-4k-enlarged.png"
    assert fourk.make_enlarged(source, target, nearest(), "a stand-in") == target
    with Image.open(target) as made:
        assert made.size == (3840, 2160)
        assert made.text["prompt"] == "a harbour" and "upscaler model a stand-in" in made.text["make4k"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["0-4k-enlarged.png", "0.png"]  # no .part file


def test_a_failing_model_leaves_no_file_and_no_partial(tmp_path):
    source = write_png(tmp_path / "0.png", (2752, 1536))

    def broken(image, passes):
        raise RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError):
        fourk.make_enlarged(source, tmp_path / "0-4k-enlarged.png", broken, "m")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["0.png"]


def test_a_failure_while_writing_leaves_the_old_copy_and_no_partial(tmp_path, monkeypatch):
    source = write_png(tmp_path / "0.png", (2752, 1536))
    target = tmp_path / "0-4k-enlarged.png"
    target.write_bytes(b"the earlier copy")
    monkeypatch.setattr(fourk.os, "replace", lambda *args: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    with pytest.raises(OSError):
        fourk.make_enlarged(source, target, nearest(), "m")
    assert target.read_bytes() == b"the earlier copy"
    assert not list(tmp_path.glob("*.part"))


def test_a_missing_source_and_an_ineligible_one_raise_the_right_errors_and_write_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        fourk.make_enlarged(tmp_path / "nowhere.png", tmp_path / "out.png", nearest(), "m")
    small = write_png(tmp_path / "small.png", (512, 512))
    with pytest.raises(NotEligible):
        fourk.make_enlarged(small, tmp_path / "out.png", nearest(), "m")
    assert not (tmp_path / "out.png").exists()


def test_the_file_a_copy_is_read_back_as_a_png_of_the_frames_size(tmp_path):
    source = write_png(tmp_path / "0.png", (2048, 2048))
    target = fourk.make_enlarged(source, tmp_path / "0-4k-enlarged.png", nearest(), "m")
    assert fourk.file_size(target) == (3840, 3840)
    assert Image.open(io.BytesIO(target.read_bytes())).size == (3840, 3840)
