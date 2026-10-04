"""The description builder (DESIGN.md §26.2). The same golden cases are in frontend/src/music.test.ts."""

from __future__ import annotations

import pytest

from studio.musicprompt import GOLDEN, INSTRUMENTAL_LYRICS, build_description, key_sentence
from studio import presets as P


@pytest.mark.parametrize("fields,instrumental,expected", GOLDEN)
def test_the_description_is_built_the_same_way_on_both_sides(fields, instrumental, expected):
    assert build_description(fields, instrumental) == expected


@pytest.mark.parametrize("key,expected", [
    ("C major", "key is C, and scale is major."), ("F# minor", "key is F#, and scale is minor."),
    ("Bb Dorian", "key is Bb, and scale is Dorian."), ("A", "key is A."), ("  e   natural minor ", "key is e, and scale is natural minor."),
    ("", ""), ("   ", ""),
])
def test_a_key_becomes_a_key_and_a_scale(key, expected):
    assert key_sentence(key) == expected


def test_the_instrumental_wording_is_the_one_the_server_and_the_spec_use():
    assert INSTRUMENTAL_LYRICS == P.INSTRUMENTAL_LYRICS == "[Instrumental]"
    from studio.musicprompt import INSTRUMENTAL_PHRASE
    assert INSTRUMENTAL_PHRASE == P.INSTRUMENTAL_PHRASE == "Instrumental, no vocals."
