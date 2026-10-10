"""scripts/laser_test_sheet.py (DESIGN.md §31.9, step 1): the test file for the owner's laser software. It has to be written the way
the Vector tab would write a file (real millimetres, one group per layer, every layer one exact colour, closed shapes only), or
what the owner sees in XCS and LightBurn would say nothing about the real thing. The checks read the SVG as XML and parse its paths
themselves; they do not use the script's own helpers."""

from __future__ import annotations

import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "laser_test_sheet.py"
COMMITTED = ROOT / "docs" / "laser_test_sheet.svg"
spec = importlib.util.spec_from_file_location("laser_test_sheet", SCRIPT)
sheet = importlib.util.module_from_spec(spec)
sys.modules["laser_test_sheet"] = sheet
spec.loader.exec_module(sheet)  # importing it must not write anything

SVG = "{http://www.w3.org/2000/svg}"
INK = "{http://www.inkscape.org/namespaces/inkscape}"
COLOURS = ["#000000", "#FF0000", "#00FF00", "#0000FF"]
FIVE = {"filled-square", "outline-square", "ring", "outline-circle", "scale-bar"}


def root():
    return ET.fromstring(sheet.build_svg().encode("utf-8"))


def layers():
    return [g for g in root() if g.tag == SVG + "g"]


def paths(group):
    return [p for p in group if p.tag == SVG + "path"]


def subpaths(d: str):
    """The subpaths of `d` as lists of (x, y) points; only M, L, C and Z in capitals are allowed, and every subpath must be closed."""
    assert re.fullmatch(r"[MLCZ0-9. \-]+", d), f"a command a laser program may not take: {d}"
    out = []
    for chunk in d.split("M")[1:]:
        chunk = chunk.strip()
        assert chunk.endswith("Z"), f"an open subpath: {d}"
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", chunk)]
        assert len(nums) % 2 == 0
        out.append(list(zip(nums[0::2], nums[1::2])))
    return out


def bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def turn(points) -> float:
    """The shoelace sum: its sign is the direction the outline runs round."""
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))


def colour_of(path) -> str:
    fill, stroke = path.get("fill"), path.get("stroke")
    return stroke if fill == "none" else fill


def short(path) -> str:
    return re.sub(r"^layer\d-", "", path.get("id"))


# ------------------------------------------------------------------ the sheet is what the Vector tab would write
def test_the_sheet_is_100_mm_square_with_one_unit_to_the_millimetre():
    r = root()
    assert (r.get("width"), r.get("height"), r.get("viewBox")) == ("100mm", "100mm", "0 0 100 100")


def test_there_are_four_layers_in_four_exact_distinct_colours():
    gs = layers()
    assert [g.get("id") for g in gs] == ["layer-1-black", "layer-2-red", "layer-3-green", "layer-4-blue"]
    used = []
    for g in gs:
        colours = {c for p in paths(g) for c in (p.get("fill"), p.get("stroke")) if c != "none"}
        assert len(colours) == 1, "a layer is one colour"
        used.append(colours.pop())
    assert used == COLOURS and len(set(used)) == 4


def test_each_layer_has_a_label_that_differs_from_its_id():
    """So that what a program shows tells us which of the two it reads."""
    for g in layers():
        label = g.get(INK + "label")
        assert g.get(INK + "groupmode") == "layer"
        assert label and label.startswith("Layer ") and label != g.get("id")


def test_every_layer_holds_the_same_five_shapes_and_layer_1_has_the_frame_too():
    for i, g in enumerate(layers(), start=1):
        names = [short(p) for p in paths(g)]
        assert set(names) == FIVE | ({"frame"} if i == 1 else set()) and len(names) == len(set(names))
        assert all(p.get("id").startswith(f"layer{i}-") for p in paths(g) if p.get("id") != "frame")


def test_filled_shapes_are_filled_without_a_stroke_and_outlines_are_lines_without_a_fill():
    for g in layers():
        for p in paths(g):
            colour = colour_of(p)
            if short(p) in ("filled-square", "ring", "scale-bar"):
                assert (p.get("fill"), p.get("stroke")) == (colour, "none") and p.get("stroke-width") is None
            else:
                assert short(p) in ("outline-square", "outline-circle", "frame")
                assert p.get("fill") == "none" and p.get("stroke") == colour and float(p.get("stroke-width")) > 0


def test_nothing_but_what_a_laser_program_takes_is_in_the_file():
    r = root()
    tags = {el.tag.replace(SVG, "") for el in r.iter()}
    assert tags == {"svg", "title", "desc", "g", "path"}  # no text, image, script, gradient, filter, style
    banned = {"style", "transform", "opacity", "fill-opacity", "stroke-opacity", "filter", "clip-path", "mask", "href", "onload"}
    for el in r.iter():
        assert not banned & {k.split("}")[-1] for k in el.attrib}, el.tag
    for p in r.iter(SVG + "path"):
        assert set(p.attrib) <= {"id", "d", "fill", "stroke", "stroke-width", "fill-rule"}
        subpaths(p.get("d"))  # only M L C Z, all closed


def test_nothing_leaves_the_sheet_and_the_frame_is_the_whole_sheet():
    for g in layers():
        for p in paths(g):
            x0, y0, x1, y1 = bbox([pt for sp in subpaths(p.get("d")) for pt in sp])
            assert 0 <= x0 and 0 <= y0 and x1 <= 100 and y1 <= 100, p.get("id")
    frame = [p for p in paths(layers()[0]) if p.get("id") == "frame"][0]
    assert bbox(subpaths(frame.get("d"))[0]) == (0, 0, 100, 100)


def test_the_shapes_have_the_stated_sizes_and_the_scale_bar_is_10_mm_in_every_layer():
    for g in layers():
        for p in paths(g):
            sub = subpaths(p.get("d"))
            x0, y0, x1, y1 = bbox(sub[0])
            size = (round(x1 - x0, 4), round(y1 - y0, 4))
            kind = short(p)
            if kind in ("filled-square", "outline-square", "outline-circle", "ring"):
                assert size == (15, 15), (p.get("id"), size)
            if kind == "scale-bar":
                assert size == (10, 2), (p.get("id"), size)
            if kind == "ring":
                hx0, hy0, hx1, hy1 = bbox(sub[1])
                assert (round(hx1 - hx0, 4), round(hy1 - hy0, 4)) == (7, 7)
                assert x0 < hx0 and y0 < hy0 and hx1 < x1 and hy1 < y1  # the hole is inside


def test_a_ring_is_a_hole_under_either_fill_rule():
    """Even-odd says so, and the hole runs the other way round, so a program that uses the non-zero rule also leaves it empty."""
    rings = [p for g in layers() for p in paths(g) if short(p) == "ring"]
    assert len(rings) == 4
    for p in rings:
        outer, hole = subpaths(p.get("d"))
        assert p.get("fill-rule") == "evenodd"
        assert turn(outer) * turn(hole) < 0
    assert all(p.get("fill-rule") is None for g in layers() for p in paths(g) if short(p) != "ring")


def test_circles_are_round():
    """Four cubic curves make a circle only with the right control-point distance (0.5523 of the radius); a wrong one gives a rounded
    square or a diamond with the same bounding box. The middle of each curve must lie on the circle."""
    seen = 0
    for g in layers():
        for p in paths(g):
            if short(p) not in ("ring", "outline-circle"):
                continue
            for sub in subpaths(p.get("d")):
                x0, y0, x1, y1 = bbox(sub)
                cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2
                assert len(sub) == 13  # a start and four curves of three points
                for i in range(0, 12, 3):
                    p0, p1, p2, p3 = sub[i:i + 4]
                    mx = (p0[0] + 3 * p1[0] + 3 * p2[0] + p3[0]) / 8
                    my = (p0[1] + 3 * p1[1] + 3 * p2[1] + p3[1]) / 8
                    assert abs(((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5 - r) < 0.002 * r
                    seen += 1
    assert seen == 4 * 3 * 4  # four layers; a ring (two circles) and a circle; four curves each


def test_no_two_shapes_touch():
    boxes = []
    for g in layers():
        for p in paths(g):
            if short(p) != "frame":
                boxes.append((p.get("id"), bbox(subpaths(p.get("d"))[0])))
    assert len(boxes) == 20
    for i, (a, (ax0, ay0, ax1, ay1)) in enumerate(boxes):
        for b, (bx0, by0, bx1, by1) in boxes[i + 1:]:
            assert ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0, (a, b)


# ------------------------------------------------------------------ the file and the script
def test_the_file_is_the_same_every_time():
    assert sheet.build_svg() == sheet.build_svg()


def test_the_committed_file_is_what_the_script_writes():
    """docs/laser_test_sheet.svg is what the owner downloads, so it must not drift from the script."""
    assert COMMITTED.read_text(encoding="utf-8") == sheet.build_svg()


def test_the_script_writes_the_file_where_it_is_told(tmp_path, capsys):
    out = tmp_path / "sheet.svg"
    assert sheet.main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == sheet.build_svg()
    assert str(out) in capsys.readouterr().out


def test_the_script_writes_laser_test_sheet_svg_here_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert sheet.main([]) == 0
    assert (tmp_path / "laser-test-sheet.svg").read_text(encoding="utf-8") == sheet.build_svg()


def test_a_file_that_cannot_be_written_is_exit_6_and_says_so(tmp_path, capsys):
    assert sheet.main(["--out", str(tmp_path / "no" / "such" / "folder" / "sheet.svg")]) == 6
    assert "Could not write" in capsys.readouterr().err


def test_a_bad_argument_is_exit_2():
    with pytest.raises(SystemExit) as stop:
        sheet.main(["--bogus"])
    assert stop.value.code == 2
