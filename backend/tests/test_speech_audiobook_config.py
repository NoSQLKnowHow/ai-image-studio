"""The one setting of 1.25, `STUDIO_SPEECH_AUDIOBOOK_KBPS` (DESIGN.md §44.4, §44.6): the bit rate of the M4B file, read as a whole number in a range, and carried from the `.env`
file a person edits to the container by `compose.yaml`, which lists every setting it passes on by name: a setting missing there would be set in `.env` and never arrive,
silently (the 1.24 setting for variations found that out, and `test_variations_config.py` is this file's model).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from studio.config import ConfigError, Settings

NAME = "STUDIO_SPEECH_AUDIOBOOK_KBPS"


def _repo_file(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / name).read_text(encoding="utf-8")


def test_the_bit_rate_is_64_unless_set_and_an_empty_value_means_the_default():
    assert Settings.from_env({}).speech_audiobook_kbps == 64
    assert Settings.from_env({NAME: ""}).speech_audiobook_kbps == 64  # `NAME=` in a .env file means "the default", like every other setting
    assert Settings.from_env({NAME: " 96 "}).speech_audiobook_kbps == 96


@pytest.mark.parametrize("value", ["16", "320"])
def test_the_ends_of_the_range_are_allowed(value):
    assert Settings.from_env({NAME: value}).speech_audiobook_kbps == int(value)


@pytest.mark.parametrize("value", ["15", "321", "0", "-64", "sixty-four", "64.5"])
def test_a_value_outside_the_range_or_not_a_whole_number_is_refused_by_name(value):
    with pytest.raises(ConfigError, match=NAME):
        Settings.from_env({NAME: value})


def test_compose_passes_the_setting_on_with_the_default_the_code_has():
    compose = _repo_file("compose.yaml")
    passed = re.findall(rf"^\s+{NAME}: \$\{{{NAME}:-([^}}]*)\}}$", compose, flags=re.M)
    assert len(passed) == 1, f"compose.yaml must pass {NAME} on, once, as `{NAME}: ${{{NAME}:-<default>}}`"
    assert Settings.from_env({NAME: passed[0]}).speech_audiobook_kbps == Settings.speech_audiobook_kbps  # a person who never set it gets the code's own default


def test_the_example_file_shows_the_setting_once_at_the_default():
    example = _repo_file(".env.example")
    assert len(re.findall(rf"^{NAME}=", example, flags=re.M)) == 1, f".env.example must show {NAME} once"
    line = re.search(rf"^{NAME}=(.*)$", example, flags=re.M)
    assert line is not None and Settings.from_env({NAME: line.group(1)}).speech_audiobook_kbps == Settings.speech_audiobook_kbps


def test_the_backup_script_leaves_the_audiobook_folder_out_of_the_data_archive():
    """The files in `data/speech/audiobooks` are made again whenever they are asked for and can be larger than the reading's own parts, so a backup does not carry them
    (DESIGN.md §44.4). The script's own tests (scripts/tests/backup_restore_test.sh) run the real archive; this keeps the rule from being dropped from the line."""
    script = _repo_file("scripts/backup.sh")
    line = next(line for line in script.splitlines() if "tar -czf" in line and "data.tar.gz" in line)
    assert "--exclude=data/speech/audiobooks" in line
