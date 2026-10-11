"""The contract's small pure functions and limits (studio/vector/__init__.py, DESIGN.md §31.12.2): the checks the server turns into 422s, the
size of the drawing the model is asked for, the options' trip through JSON, and that the constants say what the contract fixture says (the page's
limits come from the one, the server's tests from the other, and they must not drift apart).

The engine uses `check_options` before it starts, so these are part of what stops a bad request from reaching the clean-up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import studio.vector as vector
from studio.vector import VectorOptions, check_options, draw_size

LIMITS = json.loads((Path(__file__).parent / "vector_contract.json").read_text())["capabilities"]["limits_vector"]


def fields(**changes):
    """The names of the fields `check_options` complains about for the default options with `changes`."""
    return [name for name, _ in check_options(VectorOptions(**changes))]


def test_the_defaults_are_valid():
    assert check_options(VectorOptions()) == []


@pytest.mark.parametrize("field,below,low,high,above", [
    ("colours", 1, 2, 8, 9), ("width_mm", 4.99, 5, 1200, 1200.01), ("height_mm", 4.99, 5, 1200, 1200.01),
    ("min_feature_mm", 0.04, 0.05, 10, 10.01), ("kerf_mm", -0.01, 0, 1, 1.01), ("bleed_mm", -0.01, 0, 5, 5.01), ("shape_layer_cap", 0, 1, 30, 31)])
def test_each_number_is_accepted_at_its_limits_and_refused_just_outside(field, below, low, high, above):
    """The edges are in: the lowest and the highest allowed are fine, anything beyond is a message naming the field, in one wording."""
    assert fields(**{field: low}) == [] and fields(**{field: high}) == []
    assert fields(**{field: below}) == [field] and fields(**{field: above}) == [field]
    assert dict(check_options(VectorOptions(**{field: above})))[field] == f"Must be between {low:g} and {high:g}."


def test_a_boolean_or_a_string_is_not_a_number():
    """True would pass as 1 and "5" is text: neither is a size."""
    assert fields(colours=True) == ["colours"] and fields(width_mm="50") == ["width_mm"] and fields(kerf_mm=None) == ["kerf_mm"]
    assert fields(shape_layer_cap=True) == ["shape_layer_cap"]  # True is 1, inside the range, and still not a number


@pytest.mark.parametrize("field,bad", [("kind", "blob"), ("layers_by", "size"), ("arrangement", "pile"), ("fill_mode", "dotted")])
def test_a_choice_must_be_one_of_the_allowed_ones(field, bad):
    assert fields(**{field: bad}) == [field]
    assert dict(check_options(VectorOptions(**{field: bad})))[field].startswith("Must be one of: ")


def test_the_palette_lists_inks_only_in_capitals_without_repeats_and_at_most_colours_minus_one():
    """White is the bare material, not a palette entry (§31.12.3 item 1); there are at most colours-1 inks; each is #RRGGBB in capitals."""
    assert fields(colours=3, palette=("#FF0000", "#00AA00")) == []
    assert fields(colours=2, palette=("#FF0000", "#00AA00")) == ["palette"]
    assert "At most 1 colour besides white" in dict(check_options(VectorOptions(colours=2, palette=("#FF0000", "#00AA00"))))["palette"]
    assert "At most 2 colours besides white" in dict(check_options(VectorOptions(colours=3, palette=("#FF0000", "#00AA00", "#0000FF"))))["palette"]
    assert dict(check_options(VectorOptions(colours=3, palette=("#FFFFFF",))))["palette"].startswith("White is always included")
    assert "listed twice" in dict(check_options(VectorOptions(colours=3, palette=("#FF0000", "#FF0000"))))["palette"]
    for bad in ("#ff0000", "red", "#FF00", "FF0000", 5):
        assert "is not a colour" in dict(check_options(VectorOptions(colours=3, palette=(bad,))))["palette"]


def test_the_options_go_through_json_and_back_unchanged_and_an_unknown_key_is_refused():
    """The server writes `to_json` to the job's options file and the job reads it with `from_json`: the palette becomes a list and a tuple
    again. A key this version does not know is a mistake between the two programs and is never ignored."""
    options = VectorOptions(colours=5, palette=("#FF0000", "#00AA00"), kind="flat", width_mm=120.5, layers_by="shape", arrangement="stacked",
                            bleed_mm=1.5, min_feature_mm=0.8, kerf_mm=0.2, shape_layer_cap=12, fill_mode="outline")
    data = json.loads(json.dumps(options.to_json()))
    assert options.to_json()["palette"] == ["#FF0000", "#00AA00"] and isinstance(options.to_json()["palette"], list)  # a list, not a tuple, before JSON
    assert data["palette"] == ["#FF0000", "#00AA00"] and VectorOptions.from_json(data) == options
    assert VectorOptions.from_json({}) == VectorOptions()
    with pytest.raises(ValueError, match="sparkle"):
        VectorOptions.from_json({"colours": 3, "sparkle": 1})


@pytest.mark.parametrize("w,h,expected", [(100, 100, (1536, 1536)), (200, 100, (1536, 768)), (100, 200, (768, 1536)), (600, 307, (1536, 800)),
                                          (1200, 5, (1536, 256)), (5, 1200, (256, 1536)), (100, 99, (1536, 1536))])
def test_the_drawing_is_1536_on_its_long_side_and_a_multiple_of_32_on_the_other(w, h, expected):
    """DESIGN.md §31.12.2: the long side is 1536, the short side follows the sheet's shape in a multiple of 32, at least 256. (600 x 307 is the
    xTool P2's bed: 1536 x 785.9 rounds to 800.)"""
    assert draw_size(w, h) == expected


def test_the_constants_are_the_ones_the_contract_fixture_publishes():
    """`capabilities.limits.vector` is built from these constants, and the page shows them: a change to one without the other is caught here."""
    assert LIMITS["colours"] == {"min": vector.COLOURS_MIN, "max": vector.COLOURS_MAX, "default": vector.COLOURS_DEFAULT}
    assert LIMITS["size_mm"] == {"min": vector.SIZE_MM_MIN, "max": vector.SIZE_MM_MAX, "default": [vector.SIZE_MM_DEFAULT] * 2}
    assert LIMITS["versions"] == {"min": vector.VERSIONS_MIN, "max": vector.VERSIONS_MAX}
    assert LIMITS["min_feature_mm"] == {"min": vector.MIN_FEATURE_MM_MIN, "max": vector.MIN_FEATURE_MM_MAX, "default": vector.MIN_FEATURE_MM_DEFAULT}
    assert LIMITS["kerf_mm"] == {"min": vector.KERF_MM_MIN, "max": vector.KERF_MM_MAX, "default": vector.KERF_MM_DEFAULT}
    assert LIMITS["bleed_mm"] == {"min": vector.BLEED_MM_MIN, "max": vector.BLEED_MM_MAX, "default": vector.BLEED_MM_DEFAULT}
    assert LIMITS["shape_layer_cap"] == vector.SHAPE_LAYER_CAP and LIMITS["recipe_max_chars"] == vector.RECIPE_MAX_CHARS
    assert (LIMITS["kinds"], LIMITS["layers_by"], LIMITS["arrangements"]) == (list(vector.KINDS), list(vector.LAYERS_BY), list(vector.ARRANGEMENTS))
    assert LIMITS["presets"] == list(vector.PRESETS)
    assert LIMITS["defaults"] == {"kind": vector.KIND_DEFAULT, "layers_by": vector.LAYERS_BY_DEFAULT, "arrangement": vector.ARRANGEMENT_DEFAULT}


def test_the_options_defaults_are_the_limits_defaults():
    options = VectorOptions()
    assert (options.colours, options.width_mm, options.height_mm, options.min_feature_mm, options.kerf_mm, options.bleed_mm) == (
        vector.COLOURS_DEFAULT, vector.SIZE_MM_DEFAULT, vector.SIZE_MM_DEFAULT, vector.MIN_FEATURE_MM_DEFAULT, vector.KERF_MM_DEFAULT,
        vector.BLEED_MM_DEFAULT)
    assert options.shape_layer_cap == vector.SHAPE_LAYER_CAP and options.fill_mode == "filled"
