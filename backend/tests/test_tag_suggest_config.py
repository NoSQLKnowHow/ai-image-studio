"""The one setting of Stage C, `STUDIO_TAG_SUGGESTIONS` (DESIGN.md §45.6, criterion 430): 5 unless set, 0 to 8, read as a whole number, and carried from the `.env` file a person edits to the
container by `compose.yaml`, which passes every setting BY NAME: a setting missing there would be set in `.env` and never arrive, silently (the same test as for 1.24's setting)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from studio.config import ConfigError, Settings

NAME = "STUDIO_TAG_SUGGESTIONS"


def _repo_file(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / name).read_text(encoding="utf-8")


def test_five_suggestions_unless_set_and_an_empty_value_means_the_default():
    assert Settings.from_env({}).tag_suggestions == 5
    assert Settings.from_env({NAME: ""}).tag_suggestions == 5  # `NAME=` in a .env file means "the default", like every other setting


@pytest.mark.parametrize("value", [0, 1, 5, 8])
def test_zero_to_eight_are_accepted(value):
    assert Settings.from_env({NAME: str(value)}).tag_suggestions == value


@pytest.mark.parametrize("bad", ["-1", "9", "many", "2.5"])
def test_anything_else_is_refused_by_name(bad):
    with pytest.raises(ConfigError, match=NAME):
        Settings.from_env({NAME: bad})


def test_compose_passes_the_setting_on_with_the_same_default():
    passed = re.findall(rf"^\s+{NAME}: \$\{{{NAME}:-([^}}]*)\}}$", _repo_file("compose.yaml"), flags=re.M)
    assert len(passed) == 1, f"compose.yaml must pass {NAME} on, once, as `{NAME}: ${{{NAME}:-<default>}}`"
    assert Settings.from_env({NAME: passed[0]}).tag_suggestions == Settings().tag_suggestions


def test_the_example_file_shows_the_setting_once_at_the_default():
    example = _repo_file(".env.example")
    assert len(re.findall(rf"^{NAME}=", example, flags=re.M)) == 1, f".env.example must show {NAME} once"
    line = re.search(rf"^{NAME}=(.*)$", example, flags=re.M)
    assert line is not None and Settings.from_env({NAME: line.group(1)}).tag_suggestions == 5


def test_the_configuration_reference_lists_it():
    assert f"| `{NAME}` | `5` |" in _repo_file("docs/DESIGN.md")
