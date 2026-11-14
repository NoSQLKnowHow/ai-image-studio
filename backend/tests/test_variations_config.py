"""The one setting of 1.24, `STUDIO_VARIATIONS_FROM_PICTURE` (DESIGN.md §43.3, §43.9): off unless asked for, read as a true/false word, and carried from the `.env` file a person edits to
the container by `compose.yaml` (which lists every setting it passes on: a setting missing there would be switched on in `.env` and never arrive, silently)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from studio.config import ConfigError, Settings

NAME = "STUDIO_VARIATIONS_FROM_PICTURE"


def _repo_file(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / name).read_text(encoding="utf-8")


def test_variations_of_a_picture_are_off_unless_switched_on():
    assert Settings.from_env({}).variations_from_picture is False
    assert Settings.from_env({NAME: ""}).variations_from_picture is False  # `NAME=` in a .env file means "the default", like every other setting
    for word in ("1", "true", "TRUE", " yes "):
        assert Settings.from_env({NAME: word}).variations_from_picture is True, word
    for word in ("0", "false", "No"):
        assert Settings.from_env({NAME: word}).variations_from_picture is False, word


def test_a_word_that_is_neither_is_refused_by_name():
    with pytest.raises(ConfigError, match=NAME):
        Settings.from_env({NAME: "maybe"})


def test_compose_passes_the_setting_on_and_what_it_passes_when_nothing_is_set_is_off():
    compose = _repo_file("compose.yaml")
    passed = re.findall(rf"^\s+{NAME}: \$\{{{NAME}:-([^}}]*)\}}$", compose, flags=re.M)
    assert len(passed) == 1, f"compose.yaml must pass {NAME} on, once, as `{NAME}: ${{{NAME}:-<default>}}`"
    assert Settings.from_env({NAME: passed[0]}).variations_from_picture is False  # the container is not switched on by a person who never set it


def test_the_example_file_shows_the_setting_switched_off():
    example = _repo_file(".env.example")
    assert len(re.findall(rf"^{NAME}=", example, flags=re.M)) == 1, f".env.example must show {NAME} once"
    line = re.search(rf"^{NAME}=(.*)$", example, flags=re.M)
    assert line is not None and Settings.from_env({NAME: line.group(1)}).variations_from_picture is False
