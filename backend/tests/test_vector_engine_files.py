"""What the Vector engine hands to the server (DESIGN.md §31.12.3 item 8, §31.12.4, §31.12.7): the check's exact keys, the ZIP, the PNG drawn
from the SVG, the job process and its one-line answer, the recipe's wording, and the rule that importing `studio.vector` needs no library.

The SVG-to-PNG tests read the PNG's pixels and compare them with where the shapes are, so a picture that is merely the right size fails."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from studio.vector import (KINDS, RECIPE_MAX_CHARS, Recipe, VectorError, VectorOptions, available, build_recipe, build_vector, pack_zip, render_png)
from studio.vector import job
from studio.vector import engine, svgio
from studio.vector.colours import hex_to_rgb
from studio.vector.geometry import clean_layer, elongated, fill_narrow_holes, narrowest, polygons_of, trace_ink, _even_odd
from studio.vector.pathdata import parse_path
from studio.vector.pixels import _give_to_neighbours

import numpy as np
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box

from vector_engine_support import SHEET_MM, SIZE, as_png, layers_of, make, mm, parse, picture

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
BLACK, RED, GREEN, BLUE = (0, 0, 0), (255, 0, 0), (0, 160, 0), (0, 0, 255)
CONTRACT = json.loads((Path(__file__).parent / "vector_contract.json").read_text())
CODES = {"fewer_colours", "empty_layer", "specks_removed", "small_feature", "hanging_island", "coarse_drawing", "layers_capped"}


def drawing():
    """A black disc with a window, a red square and a green triangle: three layers, one with a hole."""
    return [("ellipse", (mm(3), mm(3), mm(23), mm(23)), BLACK), ("ellipse", (mm(9), mm(9), mm(17), mm(17)), (255, 255, 255)),
            ("rect", (mm(28), mm(3), mm(46), mm(21)), RED), ("poly", [(mm(25), mm(46)), (mm(45), mm(46)), (mm(35), mm(28))], GREEN)]


# ---- the check has exactly the contract's keys (§31.12.3; vector_contract.json) ------------------------------------------------------
def kinds(value):
    """The shape of a value as the contract fixes it: its type name, and for a list the type of its first item."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return ["list", sorted({kinds(item) if not isinstance(item, dict) else "dict" for item in value})]
    return type(value).__name__


def test_the_check_has_exactly_the_keys_and_types_of_the_contracts_example():
    """The server stores the check and the page reads it, both written against `run.vectors[0].check` in the contract fixture: same keys, no
    extra, the same types, at every level (the layers, `removed`, the notes)."""
    fixture = CONTRACT["run"]["vectors"][0]["check"]
    check = make(drawing(), colours=4, kerf_mm=0).check  # three layers and a hole, so there is a note
    assert check["notes"], "the picture has a hole, so there is a hanging_island note to compare"
    assert set(check) == set(fixture)
    for key in fixture:
        assert kinds(check[key]) == kinds(fixture[key]), key
    assert set(check["layers"][0]) == set(fixture["layers"][0]) and set(check["removed"]) == set(fixture["removed"])
    for key, value in fixture["layers"][0].items():
        assert kinds(check["layers"][0][key]) == kinds(value), key
    assert set(check["notes"][0]) == set(fixture["notes"][0]) == {"code", "message"}
    assert json.loads(json.dumps(check)) == check  # plain JSON, nothing a server cannot store


def test_every_note_has_one_of_the_contracts_codes_and_words():
    """Notes use the codes of §31.12.3 and say something in plain English: no code is made up, and no message is empty or a bare code."""
    result = make(drawing(), colours=8, min_feature_mm=0.15)  # fewer colours, a hole, and a coarse drawing
    assert {note["code"] for note in result.check["notes"]} <= CODES and len(result.check["notes"]) >= 3
    for note in result.check["notes"]:
        assert note["message"].endswith(".") and " " in note["message"] and note["message"] != note["code"]


# ---- the ZIP of the layers (criterion 141) -------------------------------------------------------------------------------------------------
def test_the_zip_has_one_svg_per_layer_named_by_the_layers_id():
    """`layer-1-red.svg` and so on, each holding exactly that layer's SVG, in the order of the layers; and no other file."""
    result = make(drawing(), colours=4)
    with zipfile.ZipFile(io.BytesIO(pack_zip(result))) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [f"{layer.id}.svg" for layer in result.layers] == ["layer-1-red.svg", "layer-2-black.svg", "layer-3-green.svg"]
        for layer in result.layers:
            assert archive.read(f"{layer.id}.svg").decode("utf-8") == layer.svg


def test_the_zip_is_the_same_bytes_every_time_and_does_not_carry_the_date():
    """§31.12.3 item 8: the same layers give the same bytes, so every entry is dated 1 January 1980, the earliest a ZIP can say."""
    result = make(drawing(), colours=4)
    first, second = pack_zip(result), pack_zip(result)
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in archive.infolist())


def test_a_zip_of_a_by_shape_run_names_the_pieces():
    shapes = [("ellipse", (mm(5 + i * 12), mm(10), mm(13 + i * 12), mm(18)), GREEN) for i in range(3)]
    result = make(shapes, colours=2, layers_by="shape")
    with zipfile.ZipFile(io.BytesIO(pack_zip(result))) as archive:
        assert archive.namelist() == ["layer-1-piece-1.svg", "layer-2-piece-2.svg", "layer-3-piece-3.svg"]


# ---- the PNG, drawn from the SVG's own shapes (criterion 141) ------------------------------------------------------------------------
def png_of(svg, size):
    return Image.open(io.BytesIO(render_png(svg, size)))


def svg_of(*paths, width=100, height=100):
    """A small SVG in the engine's style with the given path elements (strings)."""
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" '
            f'viewBox="0 0 {width} {height}"><g id="layer-1-black">{"".join(paths)}</g></svg>\n')


def path(d, fill="#000000", extra=""):
    return f'<path id="p" d="{d}" fill="{fill}" stroke="none"{extra}/>'


def test_the_png_has_the_asked_long_side_and_the_sheets_shape():
    """`long_side_px` is the long side exactly, and the short side follows the sheet: 120 x 60 mm at 600 is 600 x 300; a tall sheet turns it round."""
    wide = svg_of(path("M10 10 L50 10 L50 30 Z"), width=120, height=60)
    assert png_of(wide, 600).size == (600, 300) and png_of(wide, 16).size == (16, 8)
    tall = svg_of(path("M10 10 L50 10 L50 30 Z"), width=60, height=120)
    assert png_of(tall, 600).size == (300, 600)
    assert png_of(wide, 600).mode == "RGB"


@pytest.mark.parametrize("bad", [15, 8193, 0, -5, 1.5, "100", True, None])
def test_a_size_outside_16_to_8192_pixels_is_refused_with_words(bad):
    """The API takes 16 to 8192 (§31.12.7); the engine says so itself rather than drawing nothing or everything."""
    with pytest.raises(VectorError) as caught:
        render_png(svg_of(path("M0 0 L10 0 L10 10 Z")), bad)
    assert "16" in caught.value.message and "8192" in caught.value.message


def test_the_largest_size_is_drawn_at_full_length():
    """8192 pixels is allowed. A sheet 8192 wide and 64 tall is drawn at 8192 x 64 (a square would need 200 MB: the limit is the long side)."""
    image = png_of(svg_of(path("M0 0 L8192 0 L8192 64 L0 64 Z"), width=8192, height=64), 8192)
    assert image.size == (8192, 64) and image.getpixel((4000, 30)) == (0, 0, 0)


def test_the_png_shows_the_shapes_where_they_are_in_the_colours_they_have():
    """Drawn from the SVG the engine wrote: at 500 pixels (ten to the millimetre), the red square's middle is red, the black disc's black, the
    disc's window white (the hole is a hole), the background white, and the green triangle's middle green."""
    result = make(drawing(), colours=4)
    image = png_of(result.svg, 500).convert("RGB")
    assert image.getpixel((370, 120)) == (255, 0, 0) and image.getpixel((60, 130)) == (0, 0, 0) and image.getpixel((130, 130)) == (255, 255, 255)
    assert image.getpixel((480, 30)) == (255, 255, 255) and image.getpixel((350, 400)) == (0, 160, 0)
    assert image.getpixel((130, 35)) == (0, 0, 0)  # the disc above its window


def test_a_filled_shape_has_the_area_it_should_with_smooth_edges():
    """A 10 mm square in a 100 mm sheet at 1000 pixels is 100 x 100 pixels: the PNG's coverage (summing how dark each pixel is) is 10,000 pixels
    to within 1%, so supersampling neither fattens nor thins it, and an edge between two pixels is a grey, not a staircase."""
    image = np.array(png_of(svg_of(path("M20.05 20.05 L30.05 20.05 L30.05 30.05 L20.05 30.05 Z")), 1000).convert("L")).astype(float)
    assert (255 - image).sum() / 255 == pytest.approx(10000, rel=0.01)
    assert ((image > 0) & (image < 255)).sum() > 100  # the edge is anti-aliased


def test_the_even_odd_rule_is_honoured_whichever_way_the_hole_is_wound():
    """Two rings wound the SAME way with `fill-rule="evenodd"` still make a hole (a program that took the non-zero rule would fill it), and
    the engine's own holes, wound the other way, give the same picture."""
    same_way = svg_of(path("M10 10 L90 10 L90 90 L10 90 Z M30 30 L70 30 L70 70 L30 70 Z", extra=' fill-rule="evenodd"'))
    other_way = svg_of(path("M10 10 L90 10 L90 90 L10 90 Z M30 30 L30 70 L70 70 L70 30 Z", extra=' fill-rule="evenodd"'))
    for svg in (same_way, other_way):
        image = png_of(svg, 100)
        assert image.getpixel((50, 50)) == (255, 255, 255) and image.getpixel((20, 20)) == (0, 0, 0) and image.getpixel((5, 5)) == (255, 255, 255)


def test_later_layers_are_painted_over_earlier_ones():
    """File order is painting order, the first layer at the bottom (that is what stacked layers rely on)."""
    svg = svg_of(path("M10 10 L60 10 L60 60 L10 60 Z"), path("M40 40 L90 40 L90 90 L40 90 Z", fill="#FF0000"))
    image = png_of(svg, 100)
    assert image.getpixel((20, 20)) == (0, 0, 0) and image.getpixel((50, 50)) == (255, 0, 0) and image.getpixel((80, 80)) == (255, 0, 0)


def test_outline_mode_is_drawn_as_lines_with_an_empty_inside():
    """`fill_mode="outline"` writes strokes with no fill: the PNG shows the outline and leaves the middle of the square white."""
    result = make([("rect", (mm(10), mm(10), mm(40), mm(40)), BLACK)], colours=2, fill_mode="outline")
    image = png_of(result.svg, 500).convert("L")
    assert image.getpixel((250, 250)) == 255
    # the line is 0.1 mm wide, so one pixel at ten to the millimetre, which falls half on each of two pixels: its darkness over the pixels
    # round the edge adds up to one black pixel (255), so the width is right as well as the place
    assert sum(255 - image.getpixel((x, 250)) for x in range(96, 105)) == pytest.approx(255, abs=30)


def test_the_laser_test_sheet_is_drawn_with_its_curves_rings_and_outlines():
    """The renderer reads the cubic curves of the test sheet (the engine writes none, but the contract allows them) and its rings and strokes:
    at 1000 pixels (10 to the millimetre) the filled square is black, the ring's hole is white and its body black, the outline square is
    empty inside and has a line on its edge, and the red, green and blue layers are those colours."""
    image = png_of((ROOT / "docs" / "laser_test_sheet.svg").read_text(), 1000).convert("RGB")
    assert image.getpixel((125, 125)) == (0, 0, 0)  # layer 1's filled square
    assert image.getpixel((525, 125)) == (255, 255, 255) and image.getpixel((580, 125)) == (0, 0, 0)  # the ring's hole and its body
    assert image.getpixel((325, 125)) == (255, 255, 255)  # an outline square is empty inside, and has a line one pixel wide on its edge
    assert sum(255 - image.getpixel((x, 125))[0] for x in range(246, 255)) == pytest.approx(255, abs=30)
    assert image.getpixel((800, 125))[0] < 100 and image.getpixel((725, 125)) == (255, 255, 255)  # the outline circle (curves): edge, and inside
    assert image.getpixel((125, 375)) == (255, 0, 0) and image.getpixel((125, 625)) == (0, 255, 0) and image.getpixel((125, 875)) == (0, 0, 255)


def test_the_png_is_the_same_bytes_every_time_and_needs_no_cairo():
    result = make(drawing(), colours=4)
    assert render_png(result.svg, 400) == render_png(result.svg, 400)
    assert "cairosvg" not in sys.modules and "cairo" not in sys.modules


@pytest.mark.parametrize("svg", ["<svg", "<svg xmlns='http://www.w3.org/2000/svg'/>", svg_of(path("M0 0 Q 5 5 10 0 Z")),
                                  svg_of(path("M0 0 L10 0 L10 10 Z", fill="red")), svg_of(path("m0 0 l10 0 l0 10 z"))])
def test_an_svg_that_is_not_ours_is_a_message_for_the_card(svg):
    """Not XML, no view box, a command or colour the engine does not write: a VectorError, not a crash."""
    with pytest.raises(VectorError):
        render_png(svg, 100)


# ---- reading path data (pathdata.py) ----------------------------------------------------------------------------------------------------
def test_path_data_is_read_as_rings_with_curves_flattened():
    """M, L, Z, commas or spaces between numbers, further pairs after a move as lines, and a ring that is not closed at the end of the data."""
    assert parse_path("M0 0 L10 0 L10 10 Z M2 2 L4 2 L4 4 Z", 0.1) == [[(0, 0), (10, 0), (10, 10)], [(2, 2), (4, 2), (4, 4)]]
    assert parse_path("M0,0 L1,0 L1,1 Z", 0.1) == [[(0, 0), (1, 0), (1, 1)]]
    assert parse_path("M0 0 10 0 10 10 Z", 0.1) == [[(0, 0), (10, 0), (10, 10)]]
    assert parse_path("M0 0 L5 0 L5 5", 0.1) == [[(0, 0), (5, 0), (5, 5)]]
    assert parse_path("M0 0 L5 0 Z", 0.1) == []  # two points are not a shape


def test_a_curve_is_flattened_to_within_the_tolerance_of_the_curve():
    """A quarter circle of radius 100 as the cubic the test sheet uses: every point of the flattened version is on the circle to 0.05, and the
    last point is exactly the end of the curve."""
    k = 0.5522847498 * 100
    ring = parse_path(f"M100 0 C100 {k} {k} 100 0 100 L0 0 Z", 0.05)[0]
    radii = [(x * x + y * y) ** 0.5 for x, y in ring[:-1]]
    assert max(abs(r - 100) for r in radii) < 0.05 and ring[len(ring) - 2] == (0, 100)
    assert len(ring) > 8  # and it is more than a chord


@pytest.mark.parametrize("data", ["M0 0 H 5 Z", "m0 0 l1 0 l0 1 z", "M0 0 L1", "0 0 L1 1", "M0 0 C1 1 2 2 L3 3 Z", "M0 0 Q1 1 2 2 Z"])
def test_path_commands_this_engine_does_not_write_are_errors_and_not_skipped(data):
    with pytest.raises(ValueError):
        parse_path(data, 0.1)


# ---- the shape clean-up, on shapes (geometry.py) ----------------------------------------------------------------------------------------
def dumbbell(neck):
    """Two 10 mm blocks joined by a neck `neck` mm wide and 4 mm long, in millimetres."""
    return box(0, 0, 10, 10).union(box(10, 5 - neck / 2, 14, 5 + neck / 2)).union(box(14, 0, 24, 10))


def test_a_thin_neck_is_cut_and_counted_and_a_wide_one_is_not():
    """With a smallest feature of 1 mm (r = 0.5): a 0.4 mm neck goes (two pieces, one thin part), a 1.2 mm neck stays (one piece)."""
    thin = clean_layer(dumbbell(0.4), 0.5, 0.001)
    assert len(polygons_of(thin.geometry)) == 2 and (thin.specks, thin.thin_parts, thin.narrow_gaps) == (0, 1, 0)
    wide = clean_layer(dumbbell(1.2), 0.5, 0.001)
    assert len(polygons_of(wide.geometry)) == 1 and (wide.specks, wide.thin_parts) == (0, 0)
    assert wide.geometry.area == pytest.approx(dumbbell(1.2).area, abs=0.01)  # nothing else was shaved: corners stay sharp


def test_a_piece_smaller_than_the_smallest_feature_is_a_speck_and_a_gap_is_kept():
    two = box(0, 0, 10, 10).union(box(10.3, 0, 20, 10))  # a 0.3 mm gap between two blocks
    kept = clean_layer(two, 0.5, 0.001)
    assert len(polygons_of(kept.geometry)) == 2 and kept.narrow_gaps == 1 and kept.specks == 0
    specks = clean_layer(box(0, 0, 10, 10).union(box(20, 20, 20.3, 20.3)), 0.5, 0.001)
    assert len(polygons_of(specks.geometry)) == 1 and specks.specks == 1


def test_a_spot_of_bare_material_enclosed_by_two_layers_is_filled_by_the_one_with_more_border():
    """Two inks that meet with a hairline hole between them (a gap in both layers together, a hole in neither of them): filled, and given to the
    layer with the longer shared border. A hole wide enough to cut stays a hole."""
    left, right = box(0, 0, 10, 10), box(10, 0, 20, 10)
    hairline = box(9.9, 3, 10.1, 4)  # a 0.2 mm slit, enclosed
    a, b = left.difference(hairline.intersection(left)), right.difference(hairline.intersection(right))
    filled = fill_narrow_holes([a, b], 0.5)
    assert filled[0].union(filled[1]).area == pytest.approx(200, abs=0.001) and len(filled[0].union(filled[1]).interiors) == 0
    window = box(8, 3, 12, 7)
    kept = fill_narrow_holes([left.difference(window), right.difference(window)], 0.5)
    assert len(kept[0].union(kept[1]).interiors) == 1


def test_tracing_a_mask_gives_shapes_in_millimetres_with_holes_as_holes():
    """A 20 x 20 pixel square with a 6 x 6 hole at half a millimetre a pixel: 10 mm wide with a 3 mm hole, in the right place, as one polygon."""
    mask = np.zeros((40, 50), dtype=bool)
    mask[10:30, 15:35] = True
    mask[17:23, 22:28] = False
    shape = trace_ink(mask, 0.5, 0.5, 0.01)
    assert shape.geom_type == "Polygon" and len(shape.interiors) == 1
    assert shape.bounds == pytest.approx((7.5, 5.0, 17.5, 15.0)) and shape.area == pytest.approx(100 - 9)
    assert trace_ink(np.zeros((5, 5), dtype=bool), 1, 1, 0.01).is_empty


# ---- the recipe (build_recipe) -------------------------------------------------------------------------------------------------------------
def test_the_recipe_asks_for_flat_artwork_in_the_number_of_colours_and_the_named_ones():
    """The wording is the point of the recipe: a plain white background, solid regions, thick clean outlines, no gradients, shading or text,
    the number of colours (white included), and the inks the user named with their names and codes."""
    recipe = build_recipe("a fox head", "flat", 4, ("#FF0000", "#00AA00"))
    text = recipe.positive
    assert isinstance(recipe, Recipe) and text.startswith("A fox head, flat colour artwork")
    for phrase in ("plain pure white background", "no gradients", "no shading", "no text", "thick", "Exactly 4 colours in total", "3 other solid colours",
                   "red (#FF0000)", "green (#00AA00)", "one more solid colour"):
        assert phrase in text, phrase
    assert len(text) < RECIPE_MAX_CHARS


@pytest.mark.parametrize("kind,phrase", [("silhouette", "bold flat silhouette"), ("flat", "flat colour artwork"), ("line", "clean bold line art")])
def test_each_kind_of_artwork_has_its_own_wording(kind, phrase):
    text = build_recipe("a rose", kind, 2, ()).positive
    assert phrase in text and text.startswith("A rose, ") and "Exactly 2 colours in total: the white background and 1 other solid colour" in text
    assert len({build_recipe("a rose", k, 2, ()).positive for k in KINDS}) == 3


def test_the_recipe_follows_the_number_of_colours_and_the_palette():
    assert "5 different solid colours" in build_recipe("x", "flat", 6, ()).positive and "Exactly 6 colours" in build_recipe("x", "flat", 6, ()).positive
    two = build_recipe("x", "flat", 3, ("#FF0000", "#0000FF")).positive
    assert "red (#FF0000) and blue (#0000FF)" in two and "more solid colour" not in two
    assert "one solid colour (a strong dark one)" in build_recipe("x", "silhouette", 2, ()).positive


def test_the_negative_text_is_fixed_and_the_recipe_is_the_same_every_time():
    """The negative cannot be edited, so it is the same for every kind, number and palette, and lists what a laser cannot use. The same
    arguments always give the same text (the server builds it again when the request has none)."""
    negatives = {build_recipe("a rose", kind, n, palette).negative for kind in KINDS for n in (2, 8) for palette in ((), ("#FF0000",))}
    assert len(negatives) == 1
    (negative,) = negatives
    for word in ("gradient", "shading", "shadow", "photograph", "text", "thin lines", "blur", "texture"):
        assert word in negative
    assert build_recipe("a rose", "flat", 4, ("#FF0000",)) == build_recipe("a rose", "flat", 4, ("#FF0000",))


def test_the_idea_is_tidied_and_an_empty_idea_still_gives_a_recipe():
    """A trailing full stop is dropped (a comma follows) and the first letter is a capital; nothing else of the idea is changed."""
    assert build_recipe("  a red fox with a hat.  ", "flat", 2, ()).positive.startswith("A red fox with a hat, flat")
    assert build_recipe("", "flat", 2, ()).positive.startswith("Flat colour artwork made of")
    assert build_recipe("a rose", "nonsense", 2, ()).positive == build_recipe("a rose", "flat", 2, ()).positive  # the server has checked the kind


# ---- importing the package needs no library (the server imports it without them) ---------------------------------------------------------
def test_importing_the_package_does_not_import_numpy_pillow_shapely_or_vtracer():
    """The server imports `studio.vector` for its limits on a machine that may not have the libraries: they are imported inside the functions
    that need them. Checked in a fresh interpreter, because the test process has imported them all."""
    code = ("import sys; import studio.vector as v; v.build_recipe('x', 'flat', 2, ()); v.colour_name('#FF0000'); "
            "bad = [m for m in ('numpy', 'PIL', 'shapely', 'vtracer', 'cairosvg') if m in sys.modules]; assert not bad, bad")
    done = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_available_names_what_is_missing_without_raising(monkeypatch):
    import importlib

    assert available() == (True, None, None)
    real = importlib.import_module

    def without(missing):
        def fake(name, *args, **kwargs):
            if name in missing:
                raise ImportError(name)
            return real(name, *args, **kwargs)
        return fake

    monkeypatch.setattr(importlib, "import_module", without({"vtracer", "shapely", "PIL"}))
    usable, reason, hint = available()
    assert usable is False and "vtracer" in reason and "shapely" in reason and "Pillow" in reason and "numpy" not in reason and "requirements" in hint


# ---- the job process (§31.12.4) -----------------------------------------------------------------------------------------------------------
def run_job(tmp_path, image_bytes=None, options=None, extra_env=None, out=None, options_text=None):
    """Run `python -m studio.vector.job` as the server does and return (exit code, the lines on stdout, stderr, the out folder)."""
    source = tmp_path / "source.png"
    source.write_bytes(image_bytes if image_bytes is not None else as_png(picture(drawing())))
    options_file = tmp_path / "options.json"
    options_file.write_text(options_text if options_text is not None else json.dumps(
        (options or VectorOptions(colours=4, width_mm=SHEET_MM, height_mm=SHEET_MM)).to_json()))
    out = out or tmp_path / "out"
    env = {**__import__("os").environ, **(extra_env or {})}
    done = subprocess.run([sys.executable, "-m", "studio.vector.job", "--source", str(source), "--options", str(options_file), "--out", str(out)],
                          cwd=BACKEND, capture_output=True, text=True, env=env)
    return done.returncode, done.stdout.splitlines(), done.stderr, out


def test_the_job_writes_the_four_files_and_prints_one_json_line(tmp_path):
    """Exit 0, one line on stdout (`{"ok": true, "check": ...}`), and in the folder `combined.svg`, `layers.zip`, `preview.png` (the SVG drawn at a
    long side of 1024) and `check.json`, all the same as the engine makes in process."""
    code, lines, stderr, out = run_job(tmp_path)
    assert code == 0 and len(lines) == 1, stderr
    answer = json.loads(lines[0])
    expected = build_vector(as_png(picture(drawing())), VectorOptions(colours=4, width_mm=SHEET_MM, height_mm=SHEET_MM))
    assert answer == {"ok": True, "check": expected.check}
    assert sorted(p.name for p in out.iterdir()) == ["check.json", "combined.svg", "layers.zip", "preview.png"]
    assert (out / "combined.svg").read_text(encoding="utf-8") == expected.svg
    assert json.loads((out / "check.json").read_text()) == expected.check
    assert (out / "layers.zip").read_bytes() == pack_zip(expected)
    assert (out / "preview.png").read_bytes() == render_png(expected.svg, 1024) and Image.open(out / "preview.png").size == (1024, 1024)


def test_a_picture_that_cannot_be_made_into_a_file_is_a_handled_outcome_with_words(tmp_path):
    """Exit 0 and `{"ok": false, "error": {"message", "hint"}}`, so the server can tell this from a crash; nothing is written. An all-white picture,
    a file that is not a picture, and options the engine does not know are all of this kind."""
    cases = {"white": dict(image_bytes=as_png(Image.new("RGB", (64, 64), "white"))), "not a picture": dict(image_bytes=b"nope"),
             "unknown option": dict(options_text=json.dumps({"colours": 4, "sparkle": True})), "no options file content": dict(options_text="not json")}
    for name, kwargs in cases.items():
        case = tmp_path / name.replace(" ", "_")
        case.mkdir()
        code, lines, _, out = run_job(case, **kwargs)
        assert code == 0 and len(lines) == 1, name
        answer = json.loads(lines[0])
        assert answer["ok"] is False and set(answer["error"]) == {"message", "hint"} and answer["error"]["message"], name
        assert not out.exists(), name


def test_a_folder_that_cannot_be_written_is_a_handled_outcome(tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the folder should go")
    code, lines, _, _ = run_job(tmp_path, out=blocker / "out")
    assert code == 0 and json.loads(lines[0])["ok"] is False and "written" in json.loads(lines[0])["error"]["message"]


def test_a_missing_argument_is_a_crash_not_an_answer(tmp_path):
    """Any exit other than 0 is a crash to the server (it reports "the clean-up stopped unexpectedly"): argparse's own exit 2 is one."""
    done = subprocess.run([sys.executable, "-m", "studio.vector.job", "--source", "x"], cwd=BACKEND, capture_output=True, text=True)
    assert done.returncode == 2 and done.stdout == ""


def test_an_unexpected_failure_is_not_dressed_up_as_an_answer(monkeypatch, tmp_path, capsys):
    """A bug that is not a VectorError must exit non-zero (here: raise out of `main`), so that it is seen as the crash it is, and nothing is
    printed on stdout that the server could mistake for an answer."""
    monkeypatch.setattr(job, "build_vector", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("a bug")))
    (tmp_path / "o.json").write_text(json.dumps(VectorOptions().to_json()))
    with pytest.raises(RuntimeError):
        job.main(["--source", str(tmp_path / "s.png"), "--options", str(tmp_path / "o.json"), "--out", str(tmp_path / "out")])
    assert capsys.readouterr().out == ""


def test_whatever_a_library_prints_goes_to_stderr_and_the_last_line_is_the_answer(monkeypatch, tmp_path, capsys):
    """A library that prints (a progress line, a warning) would corrupt the answer: during the work stdout is stderr."""
    real = job.build_vector

    def noisy(*args, **kwargs):
        print("a library says hello")
        return real(*args, **kwargs)

    monkeypatch.setattr(job, "build_vector", noisy)
    (tmp_path / "s.png").write_bytes(as_png(picture(drawing())))
    (tmp_path / "o.json").write_text(json.dumps(VectorOptions(colours=4, width_mm=SHEET_MM, height_mm=SHEET_MM).to_json()))
    assert job.main(["--source", str(tmp_path / "s.png"), "--options", str(tmp_path / "o.json"), "--out", str(tmp_path / "out")]) == 0
    captured = capsys.readouterr()
    assert "hello" in captured.err and "hello" not in captured.out and json.loads(captured.out)["ok"] is True and captured.out.count("\n") == 1


def test_the_job_gives_the_same_bytes_whatever_the_hash_seed(tmp_path):
    """§31.12.3 item 8: nothing depends on the machine or the run. Two processes with different hash seeds write the same files."""
    results = []
    for seed in ("1", "2"):
        case = tmp_path / seed
        case.mkdir()
        code, _, stderr, out = run_job(case, extra_env={"PYTHONHASHSEED": seed})
        assert code == 0, stderr
        results.append({p.name: p.read_bytes() for p in out.iterdir()})
    assert results[0] == results[1]


def test_the_palette_survives_the_trip_through_the_options_file(tmp_path):
    """`to_json` makes the palette a list and `from_json` makes it a tuple again: a named colour reaches the job and is the layer's colour."""
    options = VectorOptions(colours=3, palette=("#00FF00",), width_mm=SHEET_MM, height_mm=SHEET_MM)
    code, lines, stderr, _ = run_job(tmp_path, options=options)
    assert code == 0, stderr
    assert "#00FF00" in {layer["colour"] for layer in json.loads(lines[0])["check"]["layers"]}


# ---- the defensive corners (each is a thing that cannot happen with a picture the studio makes, and must still end in words) --------------
def test_only_polygons_are_taken_from_what_an_operation_returns():
    """A boolean operation can hand back a collection with lines and points in it; a laser cannot cut those, so only the polygons are kept."""
    mixed = GeometryCollection([box(0, 0, 1, 1), LineString([(0, 0), (5, 5)]), Point(3, 3), box(5, 5, 6, 6)])
    assert [p.area for p in polygons_of(mixed)] == [1.0, 1.0] and polygons_of(LineString([(0, 0), (1, 1)])) == []
    assert polygons_of(Polygon()) == [] and polygons_of(Polygon([(0, 0), (1, 0), (2, 0)])) == []  # empty and flat ones are not shapes


def test_a_ring_that_crosses_itself_is_repaired_and_a_flat_piece_is_not_long_and_thin():
    """A bow-tie ring (a spline can cross itself by a hair) becomes two triangles, whose area is what was meant; and a piece that has no width
    at all is not 'elongated' (and does not crash the measure)."""
    bow = _even_odd([[(0, 0), (2, 2), (2, 0), (0, 2)]])
    assert bow.area == pytest.approx(2.0)
    assert elongated(Polygon([(0, 0), (1, 0), (2, 0)])) is False and elongated(box(0, 0, 10, 1)) is True and elongated(box(0, 0, 4, 1.1)) is False


def test_the_smallest_feature_measure_skips_empty_shapes_and_says_the_sheet_when_nothing_is_narrower():
    """An empty layer is skipped; a lone block is as wide as it is (here 10 mm), and a block as wide as the whole ceiling reports the ceiling."""
    assert narrowest([Polygon(), box(0, 0, 10, 40)], 0.05, 20, 0.001) == pytest.approx(10, abs=0.3)
    assert narrowest([box(0, 0, 40, 40)], 0.05, 20, 0.001) == 40


def test_pixels_with_no_assigned_neighbour_anywhere_go_to_white_rather_than_stay_unassigned():
    """If nothing in the picture is assigned at all (it cannot be, in a picture with any colour), the clean-up still ends, with white."""
    labels = np.full((4, 4), 2, dtype=np.int16)
    assert (_give_to_neighbours(labels, np.ones((4, 4), dtype=bool), 3) == 0).all()


def test_a_shape_that_rounding_to_a_micrometre_flattens_is_not_written():
    """`path_shape` drops a ring the file's precision makes into a point or a line, and a point that rounding made equal to the first closes
    the ring; a hole that rounds away is dropped and the shape stays."""
    assert svgio.path_shape(Polygon([(0, 0), (0.0004, 0), (0.0004, 0.0004)])) is None
    closing = svgio.path_shape(Polygon([(0, 0), (10, 0), (10, 10), (0.0001, 0.0001)]))
    assert closing.d == "M0 0 L10 0 L10 10 Z"
    holed = svgio.path_shape(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(5, 5), (5.0001, 5), (5.0001, 5.0001)]]))
    assert holed.holes == 0 and holed.d.count("M") == 1
    real = svgio.path_shape(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (4, 2), (4, 4), (2, 4)]]))
    assert real.holes == 1 and real.length_mm == pytest.approx(40 + 8)


def test_a_layer_that_rounds_to_nothing_is_dropped_with_a_note_and_if_all_do_the_run_stops(monkeypatch):
    """A layer with no shape the file can hold is left out and named in `empty_layer`; if every layer is, the card gets words, not an empty file."""
    real = engine._shapes
    calls = {"n": 0}

    def first_is_empty(geometry):
        calls["n"] += 1
        return [] if calls["n"] == 1 else real(geometry)

    monkeypatch.setattr(engine, "_shapes", first_is_empty)
    result = make(drawing(), colours=4)
    assert len(result.check["layers"]) == 2 and "#FF0000" in {n["code"]: n["message"] for n in result.check["notes"]}["empty_layer"]
    monkeypatch.setattr(engine, "_shapes", lambda geometry: [])
    with pytest.raises(VectorError) as caught:
        make(drawing(), colours=4)
    assert "Nothing is left" in caught.value.message and caught.value.hint


def test_the_renderer_draws_nothing_for_a_path_that_is_empty_or_off_the_picture():
    """A path with no rings, one entirely outside the view box, and a stroked one with nothing to draw leave the page white, without error."""
    for d in ("M0 0 L1 1 Z", "M200 200 L300 200 L300 300 Z", "M-50 -50 L-10 -50 L-10 -10 Z"):
        assert png_of(svg_of(path(d)), 50).convert("L").getextrema() == (255, 255)
        outline = f'<path id="p" d="{d}" fill="none" stroke="#000000" stroke-width="1"/>'
        assert png_of(svg_of(outline), 50).convert("L").getextrema() == (255, 255)


def test_a_colour_that_is_not_a_hex_colour_is_an_error():
    for bad in ("red", "#12345", "#GG0000", 5, None):
        with pytest.raises(ValueError):
            hex_to_rgb(bad)
    assert hex_to_rgb("#0a0B0c") == (10, 11, 12)


def test_a_curve_from_a_tracer_is_flattened_to_the_tolerance_and_put_where_the_picture_has_it(monkeypatch):
    """The engine asks vtracer for straight-edged polygons, but its reader also takes the cubic curves and the translation that vtracer's spline
    mode writes (and the test sheet has curves too). A canned tracer answer, an ellipse of four cubics about its own centre and moved by a
    translation: the shape ends up where the picture has it (the mask's own offset, the translation and each axis's scale all counted),
    flattened finely when asked finely and coarsely when asked coarsely."""
    import vtracer

    k = 0.5522847498 * 10
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="50" height="40"><path d="M10 0 '
           f'C10 {k} {k} 10 0 10 C-{k} 10 -10 {k} -10 0 C-10 -{k} -{k} -10 0 -10 C{k} -10 10 -{k} 10 0 Z" transform="translate(20,15)"/></svg>')
    monkeypatch.setattr(vtracer, "convert_raw_image_to_svg", lambda *args, **kwargs: svg)
    mask = np.zeros((40, 50), dtype=bool)
    mask[5:35, 8:38] = True  # the tracer was given this part of the picture, which starts at column 8, row 5
    fine = trace_ink(mask, 0.5, 0.25, 0.01)  # half a millimetre a pixel across, a quarter down
    assert fine.bounds == pytest.approx((9.0, 2.5, 19.0, 7.5), abs=0.01)  # centre (20 + 8, 15 + 5) pixels = (14, 5) mm; radii 5 mm and 2.5 mm
    assert fine.area == pytest.approx(np.pi * 5 * 2.5, rel=0.003)
    coarse = trace_ink(mask, 0.5, 0.25, 1.0)
    assert len(coarse.exterior.coords) < len(fine.exterior.coords) / 4 and len(coarse.exterior.coords) >= 9


def test_a_big_circle_is_traced_to_within_a_few_tenths_of_a_millimetre_of_the_true_edge():
    """The reason the tracer runs in polygon mode: vtracer's spline mode, measured, can drift 40 pixels from the edge of a large circle. A 40 mm
    circle at 500 pixels (r = 200 pixels): every vertex of the file is within 0.25 mm of a true circle (2.5 pixels)."""
    result = make([("ellipse", (mm(5), mm(5), mm(45), mm(45)), (0, 0, 0))], colours=2)
    points = [p for ring in layers_of(result.svg)[0].paths[0].rings for p in ring]
    radii = [((x - 25) ** 2 + (y - 25) ** 2) ** 0.5 for x, y in points]
    assert len(points) > 40 and all(abs(r - 20) < 0.25 for r in radii)


def test_the_colour_of_a_layer_is_the_mean_of_the_picture_rounded_not_cut():
    """A region whose columns alternate between (10, 10, 10) and (11, 11, 11) has the mean 10.5, which rounds to 11 (#0B0B0B): the colour is
    rounded to the nearest, as the contract says ("rounded to #RRGGBB")."""
    data = np.full((SIZE, SIZE, 3), 255, dtype=np.uint8)
    data[100:400, 100:400] = 10
    data[100:400, 101:400:2] = 11
    result = build_vector(as_png(Image.fromarray(data)), VectorOptions(colours=2, width_mm=SHEET_MM, height_mm=SHEET_MM))
    assert result.check["layers"][0]["colour"] == "#0B0B0B"


def test_a_picture_exactly_at_the_size_limit_is_accepted(monkeypatch):
    from studio.vector import pixels

    monkeypatch.setattr(pixels, "MAX_PIXELS", 64 * 64)
    assert build_vector(as_png(Image.new("RGB", (64, 64), "black")), VectorOptions(width_mm=20, height_mm=20)).check["layers"]


def test_every_shape_runs_the_same_way_round_and_every_hole_the_other():
    """Not only 'a hole is wound against its outline' but every outline the same way: a file that mixed the two would behave differently
    under the non-zero rule when shapes overlap (stacked layers do). A ring, a dot in it, a bar and a disc, side by side and stacked."""
    shapes = [("ellipse", (mm(5), mm(5), mm(30), mm(30)), BLACK), ("ellipse", (mm(10), mm(10), mm(25), mm(25)), (255, 255, 255)),
              ("ellipse", (mm(15), mm(15), mm(20), mm(20)), BLACK), ("rect", (mm(35), mm(5), mm(45), mm(40)), RED)]

    def turn(ring):
        return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))

    for arrangement in ("side_by_side", "stacked"):
        rings = [(i, turn(ring) > 0) for layer in layers_of(make(shapes, colours=3, arrangement=arrangement).svg) for p in layer.paths
                 for i, ring in enumerate(p.rings)]
        assert {positive for i, positive in rings if i == 0} == {True} and {positive for i, positive in rings if i > 0} == {False}


def test_numbers_in_the_file_have_at_most_three_decimals_and_no_trailing_zeros():
    """A thousandth of a millimetre is far finer than any laser; the numbers are written as short as they can be."""
    import re

    result = make(drawing(), colours=4)
    numbers = re.findall(r"-?\d[\d.]*", " ".join(p.d for layer in layers_of(result.svg) for p in layer.paths))
    assert numbers and all(re.fullmatch(r"-?\d+(\.\d*[1-9])?", n) and len(n.partition(".")[2]) <= 3 and n != "-0" for n in numbers)
    assert any("." in n for n in numbers)


def test_the_supersampling_is_reduced_to_fit_big_masks_and_a_reduced_one_still_draws(monkeypatch):
    """`_samples` picks 4 for a small shape and fewer as the mask grows toward the 40 million sample limit; a render with that limit made tiny
    (so that there is no supersampling at all) still draws the shape in the right place."""
    from studio.vector import raster

    assert [raster._samples(w, h) for w, h in ((1000, 1000), (2000, 2000), (3000, 3000), (4000, 4000), (10000, 10000))] == [4, 3, 2, 1, 1]
    monkeypatch.setattr(raster, "MAX_SAMPLES", 5000)
    image = np.array(png_of(svg_of(path("M20 20 L30 20 L30 30 L20 30 Z")), 100).convert("L"))
    assert image[25, 25] == 0 and image[5, 5] == 255 and (image == 0).sum() == pytest.approx(100, abs=25)
