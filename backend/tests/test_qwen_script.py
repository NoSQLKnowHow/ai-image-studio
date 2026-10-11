"""scripts/qwen_image.py: the command-line script's default size is 16:9, 2752x1536 (DESIGN.md §34.4, criterion 182).

Up to 1.15 the script, like the server and the page, made 2048x2048 when no size was given. Your answer 3 in §34 changed the default
everywhere, so these tests hold the script to the same value as the server, and check its help, its example and its out-of-memory hint
say so. Nothing here needs PyTorch or a GPU: `--dry-run` prints the plan without loading the model.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio import presets as P

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "qwen_image.py"

# The script's own functions, for the few checks that are about one function (its constants, its error text).
spec = importlib.util.spec_from_file_location("qwen_image", SCRIPT)
script = importlib.util.module_from_spec(spec)
sys.modules["qwen_image"] = script  # the script's dataclasses look themselves up by module name
spec.loader.exec_module(script)


def run(*args: str):
    """Run the script as a person does, in a child process."""
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)


def planned_size(*args: str) -> str:
    """The size a dry run says it would make, as '2752x1536', or the pipeline-default wording for an edit."""
    done = run(*args, "--dry-run")
    assert done.returncode == 0, done.stderr
    return next(line.split(":", 1)[1].strip() for line in done.stdout.splitlines() if line.strip().startswith("size"))


# ---------------------------------------------------------------- the default
def test_generate_with_no_size_makes_2752_by_1536():
    assert planned_size("generate", "a harbour at dusk") == "2752x1536"


def test_the_file_name_says_the_size_it_will_have():
    done = run("generate", "a harbour at dusk", "--dry-run")
    assert "_2752x1536_" in done.stdout


def test_a_preset_still_gets_its_own_size_and_1_1_is_still_a_square():
    assert planned_size("generate", "x", "--aspect-ratio", "1:1") == "2048x2048"
    assert planned_size("generate", "x", "--aspect-ratio", "4:3") == "2400x1792"
    assert planned_size("generate", "x", "--aspect-ratio", "16:9") == "2752x1536"


def test_an_explicit_width_and_height_are_honoured():
    assert planned_size("generate", "x", "--width", "1024", "--height", "1024") == "1024x1024"


def test_an_edit_with_no_size_still_leaves_the_sizing_to_the_pipeline(tmp_path):
    """§34.3: Edit stays on Auto, in the script as on the page."""
    source = tmp_path / "in.png"
    source.write_bytes(b"not read in a dry run")
    plan = planned_size("edit", "make it blue", "--image", str(source))
    assert plan == "pipeline default (from the input image)"


def test_the_script_and_the_server_agree_on_the_default_and_the_presets():
    """One value, so that every way of making a picture agrees (§34.4): the script keeps its own copy of the table because it runs
    without the studio's package, and this is what stops the two copies from drifting apart."""
    assert script.DEFAULT_ASPECT == P.DEFAULT_ASPECT
    assert script.ASPECT_RATIOS == P.ASPECT_RATIOS
    assert script.ASPECT_RATIOS[script.DEFAULT_ASPECT] == P.DEFAULT_SIZE == (2752, 1536)


# ---------------------------------------------------------------- what the script says about the default
def test_the_help_says_the_default_is_2752x1536_and_16_9():
    done = run("generate", "--help")
    assert done.returncode == 0
    text = " ".join(done.stdout.split())  # the help wraps its lines
    assert "generate defaults to 2752x1536 (16:9)" in text
    assert "2048x2048 (1:1)" not in text  # the old wording is gone
    assert "1:1=2048x2048" in text  # the preset list still offers the square


def test_the_top_of_the_help_example_makes_the_default_and_shows_how_to_ask_for_a_square():
    docstring = script.__doc__
    assert "Text to image (2752x1536, which is 16:9;" in docstring
    assert "--aspect-ratio 1:1" in docstring and "(2048x2048)" in docstring


def test_the_out_of_memory_hint_names_the_default_size_and_suggests_half_of_it():
    """Where the old hint said '--aspect-ratio 1:1 is 2048x2048', which would now be a mistake about the default."""
    runtime = SimpleNamespace(torch=SimpleNamespace(cuda=SimpleNamespace()))  # no torch.cuda.OutOfMemoryError: the text decides
    job = SimpleNamespace(cpu_offload=False, num_images=1)
    error = script.translate_generation_error(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB"), runtime, job)
    assert isinstance(error, script.GenerationError)
    assert "the default is 2752x1536; try --width 1376 --height 768" in error.hint
    assert "2048x2048" not in error.hint
    assert "--cpu-offload" in error.hint
