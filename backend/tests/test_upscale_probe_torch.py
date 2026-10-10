"""The tiling in scripts/upscale_probe.py against stand-in models, and its 4K comparison files (DESIGN.md §27.5). Needs PyTorch
and NumPy, so it is skipped where they are not installed (the container and the Spark have them), as the real-music tests are.
The real model cannot run here: that is what the probe is for, on the Spark."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "upscale_probe.py"
spec = importlib.util.spec_from_file_location("upscale_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
sys.modules["upscale_probe"] = probe  # a dataclass in the script looks its own module up by name
spec.loader.exec_module(probe)


class StandIn:
    """A stand-in for a spandrel model: a 3x3 convolution (which looks one pixel to each side) then a nearest-neighbour
    enlargement by `scale`. If the tiles lack the context the convolution needs, the tiled result differs from the whole."""

    def __init__(self, scale=2, multiple_of=1, minimum=0, square=False):
        torch.manual_seed(0)
        self.scale = scale
        self.conv = torch.nn.Conv2d(3, 3, 3, padding=1)
        self.size_requirements = type("Req", (), dict(minimum=minimum, multiple_of=multiple_of, square=square))()
        self.multiple_of, self.minimum = multiple_of, minimum
        self.calls: list[tuple[int, int]] = []

    def __call__(self, x):
        assert x.shape[-1] % self.multiple_of == 0 and x.shape[-2] % self.multiple_of == 0, f"{tuple(x.shape)} breaks the size requirement"
        assert min(x.shape[-2:]) >= self.minimum
        self.calls.append((x.shape[-2], x.shape[-1]))
        return torch.nn.functional.interpolate(self.conv(x), scale_factor=self.scale, mode="nearest")


def gradient_picture(width: int, height: int) -> Image.Image:
    rng = np.random.default_rng(3)
    base = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    return Image.fromarray(base, "RGB")


CPU = torch.device("cpu")


@pytest.mark.parametrize("scale", [2, 4])
def test_the_result_is_scale_times_bigger(scale):
    out = probe.upscale(StandIn(scale), gradient_picture(37, 21), 16, 3, CPU, torch.float32)
    assert out.size == (37 * scale, 21 * scale) and out.mode == "RGB"


@pytest.mark.parametrize("tile,overlap", [(16, 1), (16, 4), (20, 2), (7, 1), (64, 8)])
def test_tiling_gives_the_same_picture_as_one_pass_when_the_overlap_covers_the_models_reach(tile, overlap):
    picture = gradient_picture(53, 41)
    whole = probe.upscale(StandIn(), picture, 1000, 0, CPU, torch.float32)  # one tile
    tiled = probe.upscale(StandIn(), picture, tile, overlap, CPU, torch.float32)
    assert np.array_equal(np.array(whole), np.array(tiled))


def test_with_no_overlap_the_seams_show_which_proves_the_test_above_can_fail():
    picture = gradient_picture(53, 41)
    whole = probe.upscale(StandIn(), picture, 1000, 0, CPU, torch.float32)
    seamed = probe.upscale(StandIn(), picture, 16, 0, CPU, torch.float32)
    assert not np.array_equal(np.array(whole), np.array(seamed))


@pytest.mark.parametrize("multiple_of,minimum", [(4, 0), (8, 0), (4, 20)])
def test_every_tile_is_padded_to_the_models_size_requirements_and_the_result_is_cut_back(multiple_of, minimum):
    picture = gradient_picture(53, 41)
    model = StandIn(multiple_of=multiple_of, minimum=minimum)  # raises if a tile arrives that it cannot take
    out = probe.upscale(model, picture, 16, 2, CPU, torch.float32)
    assert out.size == (106, 82) and model.calls
    plain = probe.upscale(StandIn(), picture, 16, 2, CPU, torch.float32)
    assert np.array_equal(np.array(out)[:60, :60], np.array(plain)[:60, :60])  # padding changes nothing inside


def test_progress_is_reported_once_per_tile_in_order():
    seen = []
    probe.upscale(StandIn(), gradient_picture(40, 40), 16, 2, CPU, torch.float32, lambda number, total, seconds: seen.append((number, total)))
    assert seen == [(n, 9) for n in range(1, 10)]


def test_values_outside_the_pictures_range_are_clamped_not_wrapped():
    class Loud(StandIn):
        def __call__(self, x):
            return super().__call__(x) * 100 - 20  # far outside 0..1 on both sides

    out = np.array(probe.upscale(Loud(), gradient_picture(24, 24), 16, 2, CPU, torch.float32))
    assert out.min() == 0 and out.max() == 255  # clamped; wrapped values would also sit in between, but never reach both ends alone
    assert set(np.unique(out)) <= set(range(256))


# ------------------------------------------------------------------ the 4K comparison files
def test_the_4k_comparison_files_are_made_for_a_picture_make_4k_accepts(tmp_path):
    picture = Image.new("RGB", (1920, 1080), (30, 90, 160))
    picture.save(tmp_path / "input.png")
    result = picture.resize((3840, 2160))  # what a x2 model would give
    written: list[str] = []
    notes = probe.four_k_files(picture, result, 2, tmp_path, written)
    assert written == ["4k-lanczos.png", "4k-from-model.png"] and notes
    for name in written:
        assert Image.open(tmp_path / name).size == (3840, 2160)


def test_the_models_output_is_trimmed_by_the_same_box_scaled(tmp_path):
    """For 2752x1536 the trim is 10.67 px on each side of the source; a x2 model's output must lose 21.33 on each side."""
    picture = Image.new("RGB", (2752, 1536), (0, 0, 255))
    picture.save(tmp_path / "input.png")
    result = Image.new("RGB", (5504, 3072), (0, 0, 255))
    ImageDraw.Draw(result).rectangle([0, 0, 15, 3072], fill=(255, 0, 0))  # the outer 16 px on the left: inside the trim (21.3)
    ImageDraw.Draw(result).rectangle([60, 0, 75, 3072], fill=(0, 255, 0))  # just inside the kept part
    probe.four_k_files(picture, result, 2, tmp_path, [])
    out = Image.open(tmp_path / "4k-from-model.png").convert("RGB")
    assert all(out.getpixel((x, 1000))[0] < 40 for x in range(0, 200, 7))  # the red edge is gone
    assert any(out.getpixel((x, 1000))[1] > 200 for x in range(0, 200))   # the green line is there, near the left edge


def test_a_picture_make_4k_does_not_accept_gets_a_note_and_no_files(tmp_path):
    picture = Image.new("RGB", (512, 288))
    written: list[str] = []
    notes = probe.four_k_files(picture, picture.resize((1024, 576)), 2, tmp_path, written)
    assert written == [] and "too small" in notes[0]
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ a model file that is not one
def test_a_file_that_is_not_a_model_exits_with_4(tmp_path, capsys):
    pytest.importorskip("spandrel")
    bad = tmp_path / "bad.pth"
    bad.write_bytes(b"this is not a model")
    assert probe.main(["--model", str(bad), "--out", str(tmp_path / "out"), "--device", "cpu"]) == 4
    assert "could not be loaded" in capsys.readouterr().err
