"""scripts/minimax_music.py (DESIGN.md §26.6): the command-line example. Everything here needs nothing installed: the
real run on a tiny model is in test_real_music.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from studio import presets as P
from studio.musicprompt import GOLDEN, build_description

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "minimax_music.py"


def run(*args: str, stdin: str | None = None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, input=stdin, timeout=60)


def sections(stderr: str) -> tuple[str, str]:
    """(description, lyrics) as the dry run prints them."""
    body = stderr.split("--- description ---\n", 1)[1]
    description, rest = body.split("\n--- lyrics ---\n", 1)
    return description, rest.split("\n--- ", 1)[0]


def test_a_dry_run_shows_exactly_what_would_be_sent_and_loads_nothing():
    done = run("--genre", "acoustic pop", "--bpm", "96", "--key", "C major", "--mood", "warm and intimate, building gently",
               "--instruments", "fingerpicked guitar and soft piano; brushed drums enter in the second half", "--dry-run")
    assert done.returncode == 0
    description, lyrics = sections(done.stderr)
    fields, instrumental, expected = GOLDEN[0]
    assert (description, lyrics) == (expected, "[Instrumental]") and instrumental
    assert "instrumental, up to 60 s, 30 steps, seed 0" in done.stderr and done.stdout == ""


def test_lyrics_make_it_a_song_with_a_voice_and_are_sent_as_written(tmp_path):
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("[Verse]\nla la la\n[Chorus]\nsing it again\n")
    done = run("--genre", "indie folk", "--voice", "soft female lead", "--lyrics", f"@{lyrics}", "--duration", "30", "--seed", "5", "--dry-run")
    assert done.returncode == 0
    description, sent = sections(done.stderr)
    assert sent == "[Verse]\nla la la\n[Chorus]\nsing it again"
    assert "Vocal Details\nVocal Gender & Timbre: soft female lead." in description and "Instrumental, no vocals." not in description
    assert "with lyrics, up to 30 s, 30 steps, seed 5" in done.stderr


def test_your_own_description_text_is_sent_untouched(tmp_path):
    done = run("--prompt", "Genre: jazz. A walking bass.", "--dry-run")
    assert sections(done.stderr) == ("Genre: jazz. A walking bass.", "[Instrumental]")
    file = tmp_path / "d.txt"
    file.write_text("  A long, careful description\nover two lines.  \n")
    assert sections(run("--prompt-file", str(file), "--dry-run").stderr)[0] == "A long, careful description\nover two lines."
    assert sections(run("--prompt-file", "-", "--dry-run", stdin="From standard input.").stderr)[0] == "From standard input."


@pytest.mark.parametrize("args,fragment", [
    ([], "Describe the music"),
    (["--genre", "x", "--duration", "9"], "--duration must be between 10 and 360"),
    (["--genre", "x", "--duration", "361"], "--duration must be between 10 and 360"),
    (["--genre", "x", "--steps", "9"], "--steps must be between 10 and 60"),
    (["--genre", "x", "--steps", "61"], "--steps must be between 10 and 60"),
    (["--prompt", "x" * (P.MUSIC_DESCRIPTION_MAX + 1)], "description is 2001 characters"),
    (["--genre", "x", "--lyrics", "y" * (P.MUSIC_LYRICS_MAX + 1)], "lyrics are 6001 characters"),
    (["--genre", "x", "--lyrics", "@/no/such/file.txt"], "No such file"),
    (["--prompt-file", "/no/such/file.txt"], "No such file"),
])
def test_mistakes_are_explained_and_exit_with_the_argument_error_code(args, fragment):
    done = run(*args, "--dry-run")
    assert done.returncode == 2 and fragment in done.stderr and "Traceback" not in done.stderr


def test_without_the_music_libraries_it_says_what_is_missing_and_how_to_fix_it():
    """In the plain test environment there is no PyTorch: exit code 3, not a traceback."""
    if importlib.util.find_spec("torch") is not None:
        pytest.skip("PyTorch is installed here, so there is nothing missing to report")
    done = run("--genre", "ambient")
    assert done.returncode == 3 and "cannot run the music model" in done.stderr and "PyTorch is not installed" in done.stderr
    assert "Traceback" not in done.stderr


def test_the_help_has_the_examples_and_the_licence_note():
    done = run("--help")
    assert done.returncode == 0 and "--dry-run" in done.stdout and "machine-generated" in done.stdout and "--hub" in done.stdout


def test_the_script_and_the_page_agree_on_the_description_for_every_golden_case():
    for fields, instrumental, expected in GOLDEN:
        assert build_description(fields, instrumental) == expected
