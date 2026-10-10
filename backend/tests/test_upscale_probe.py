"""scripts/upscale_probe.py (DESIGN.md §27.5): the arithmetic that cuts a picture into tiles and pads them for a model, and its
small helpers. These run everywhere; the tiling itself, which needs PyTorch, is in test_upscale_probe_torch.py. The real model
cannot run here: that is what the probe is for, on the Spark."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "upscale_probe.py"
spec = importlib.util.spec_from_file_location("upscale_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
sys.modules["upscale_probe"] = probe  # a dataclass in the script looks its own module up by name
spec.loader.exec_module(probe)  # importing it must not run anything or need torch


# ------------------------------------------------------------------ tiles
def coverage(width: int, height: int, tile: int, overlap: int) -> list[list[int]]:
    grid = [[0] * width for _ in range(height)]
    for piece in probe.tile_boxes(width, height, tile, overlap):
        x0, y0, x1, y1 = piece.core
        for y in range(y0, y1):
            for x in range(x0, x1):
                grid[y][x] += 1
    return grid


@pytest.mark.parametrize("width,height,tile,overlap", [(100, 60, 64, 8), (64, 64, 64, 0), (65, 65, 64, 4), (10, 200, 32, 3), (200, 10, 32, 3), (1, 1, 64, 8)])
def test_the_cores_cover_every_pixel_exactly_once(width, height, tile, overlap):
    assert all(count == 1 for row in coverage(width, height, tile, overlap) for count in row)


def test_a_picture_smaller_than_a_tile_is_one_tile_with_no_padding_beyond_the_picture():
    (only,) = probe.tile_boxes(40, 30, 64, 8)
    assert only.core == (0, 0, 40, 30) and only.fed == (0, 0, 40, 30)


def test_the_fed_box_has_context_where_there_is_a_neighbour_and_stops_at_the_pictures_edge():
    tiles = {t.core: t.fed for t in probe.tile_boxes(200, 200, 64, 10)}
    assert tiles[(0, 0, 64, 64)] == (0, 0, 74, 74)  # the corner: nothing beyond the edge
    assert tiles[(64, 64, 128, 128)] == (54, 54, 138, 138)  # the middle: the overlap on all four sides
    assert tiles[(192, 192, 200, 200)] == (182, 182, 200, 200)  # the last, small one


def test_every_fed_box_contains_its_core_and_stays_inside_the_picture():
    for piece in probe.tile_boxes(333, 177, 50, 7):
        (cx0, cy0, cx1, cy1), (fx0, fy0, fx1, fy1) = piece.core, piece.fed
        assert 0 <= fx0 <= cx0 < cx1 <= fx1 <= 333 and 0 <= fy0 <= cy0 < cy1 <= fy1 <= 177


@pytest.mark.parametrize("tile,overlap", [(0, 0), (-5, 0), (32, -1)])
def test_nonsense_tiles_are_refused(tile, overlap):
    with pytest.raises(ValueError):
        probe.tile_boxes(100, 100, tile, overlap)


# ------------------------------------------------------------------ padding for a model's size requirements
@pytest.mark.parametrize("size,requirements,expected", [
    ((13, 10), dict(multiple_of=4), (3, 2)),       # the ESRGAN x2 model needs multiples of 4
    ((16, 8), dict(multiple_of=4), (0, 0)),         # already fine
    ((3, 3), dict(minimum=4, multiple_of=4), (1, 1)),
    ((2, 9), dict(minimum=8), (6, 0)),
    ((10, 6), dict(square=True), (0, 4)),           # square: the short side grows to the long one
    ((10, 6), dict(square=True, multiple_of=8), (6, 10)),  # and then to a multiple of 8, both sides
    ((5, 5), {}, (0, 0)),
])
def test_padding_brings_a_size_up_to_what_the_model_needs(size, requirements, expected):
    assert probe.padding_needed(*size, **requirements) == expected


def test_a_padded_size_always_meets_the_requirements():
    for width in range(1, 40):
        for height in range(1, 40):
            for requirements in (dict(multiple_of=4), dict(minimum=16, multiple_of=4), dict(square=True, multiple_of=2)):
                right, bottom = probe.padding_needed(width, height, **requirements)
                w, h = width + right, height + bottom
                assert right >= 0 and bottom >= 0
                assert w % requirements.get("multiple_of", 1) == 0 and h % requirements.get("multiple_of", 1) == 0
                assert w >= requirements.get("minimum", 0) and h >= requirements.get("minimum", 0)
                assert not requirements.get("square") or w == h


# ------------------------------------------------------------------ small helpers
def test_sizes_are_parsed_from_the_command_line():
    assert probe.parse_size("512x288") == (512, 288) and probe.parse_size("512X288") == (512, 288)
    for bad in ("512", "axb", "512x", "4x4", "0x100", "512x288x2"):
        with pytest.raises(argparse.ArgumentTypeError):
            probe.parse_size(bad)


def test_the_synthetic_picture_is_deterministic_and_the_size_asked_for():
    a, b = probe.synthetic_picture(320, 180), probe.synthetic_picture(320, 180)
    assert a.size == (320, 180) and a.mode == "RGB" and a.tobytes() == b.tobytes()
    assert len({a.getpixel((x, 90)) for x in range(320)}) > 8  # not flat: there is detail to upscale
    assert probe.synthetic_picture().size == (2752, 1536)  # the default is the model's own 16:9 size


def test_the_memory_figure_is_a_number_or_nothing():
    value = probe.meminfo_available_gb()
    assert value is None or value > 0


def test_a_missing_model_file_is_refused_with_exit_code_2_or_3_before_anything_runs(tmp_path, capsys):
    code = probe.main(["--model", str(tmp_path / "nope.pth"), "--out", str(tmp_path / "out")])
    assert code in (2, 3)  # 2: no such file; 3: this environment has no torch or spandrel to begin with
    assert "PROBE FAILED" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_bad_arguments_exit_with_2(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        probe.main(["--model", str(tmp_path / "x.pth"), "--crop", "banana"])
    assert stopped.value.code == 2
