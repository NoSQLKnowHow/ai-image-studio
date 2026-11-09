"""The build's patch to qwen-tts (docker/patch_qwen_tts.py; DESIGN.md section 38.18, item 72).

What happened: on the DGX Spark the Qwen3-TTS engine said `No module named 'torchaudio'`. qwen-tts 0.1.1 imports torchaudio at the top of the module of its OLD 25 Hz
tokenizer, and imports that module whenever the package is imported, though only the old tokenizer uses it and the studio's checkpoints (the 12 Hz ones) do not. NVIDIA's image
has no torchaudio for the Spark. The build therefore makes that one import optional in the studio's own copy of the library.

Nothing here can run the real library (it needs PyTorch and a GPU), so what is tried is the patch itself, on a file that has the real file's import block, and the one thing
the patch must never get wrong in the Dockerfile: that it runs AFTER the folder it patches exists and the script is copied in, and BEFORE the image check that is meant to
see its result. The patch was also tried by hand on the real 0.1.1 file, which is not kept here (the package comes from PyPI): see the commit that added this file.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "docker" / "patch_qwen_tts.py"
RELATIVE = "qwen_tts/core/tokenizer_25hz/vq/speech_vq.py"

# The import block of the real file, as qwen-tts 0.1.1 has it (lines 16 to 31, read from the package on PyPI), so that the patch is tried on what it will meet. `kaldi` is
# used once, further down, as `kaldi.fbank(...)`; a function that does so is added below so that a test can call it.
REAL_HEAD = textwrap.dedent('''\
    # See the License for the specific language governing permissions and
    # limitations under the License.
    import sox
    import copy
    import torch
    import operator
    import onnxruntime

    import torch.nn as nn
    import torch.nn.functional as F
    import torchaudio.compliance.kaldi as kaldi

    from librosa.filters import mel as librosa_mel_fn
    from itertools import accumulate
    from typing import List
    from torch import Tensor


    def features(audio):
        return kaldi.fbank(audio)
    ''')


def _load_patch():
    """The script as a module (it is a script in docker/, not part of the package)."""
    spec = importlib.util.spec_from_file_location("patch_qwen_tts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patch_module = _load_patch()


def _library(tmp_path: Path, text: str = REAL_HEAD) -> Path:
    """A folder laid out as `pip install --target` leaves qwen-tts, with the given text as the file the patch is for."""
    folder = tmp_path / "qwen3-tts"
    file = folder / RELATIVE
    file.parent.mkdir(parents=True)
    file.write_text(text, encoding="utf-8")
    return folder


def _run(folder: Path | None = None) -> subprocess.CompletedProcess:
    """The script as the Dockerfile runs it: a process, with the folder as its argument, and its exit code the answer."""
    command = [sys.executable, str(SCRIPT)] + ([str(folder)] if folder else [])
    return subprocess.run(command, capture_output=True, text=True, timeout=60)


def _patched_block(text: str) -> str:
    """The part of the patched file that replaced the import: from the `try` to the line before the next import, ready to run on its own."""
    start = text.index("try:  # patched")
    return text[start:text.index("\nfrom librosa.filters", start)]


# ------------------------------------------------------------------ what the patch does

def test_the_import_becomes_optional_and_nothing_else_changes(tmp_path):
    folder = _library(tmp_path)
    assert patch_module.patch(folder) == "patched"
    after = (folder / RELATIVE).read_text(encoding="utf-8")
    # exactly one line was replaced: every other line of the file is as it was, in order
    before_lines, after_lines = REAL_HEAD.split("\n"), after.split("\n")
    assert [l for l in after_lines if l in before_lines] == [l for l in before_lines if l != "import torchaudio.compliance.kaldi as kaldi"]
    assert "try:" in after and "except ImportError:" in after
    compile(after, "speech_vq.py", "exec")  # and it is still a file Python can read


def test_without_torchaudio_the_stand_in_says_why_it_cannot_work(tmp_path):
    folder = _library(tmp_path)
    patch_module.patch(folder)
    namespace: dict = {}
    # torchaudio is not importable in this test run (nor on the Spark), so this is the branch that runs there
    assert importlib.util.find_spec("torchaudio") is None
    exec(_patched_block((folder / RELATIVE).read_text(encoding="utf-8")), namespace)  # noqa: S102 (a block of our own, made above)
    with pytest.raises(RuntimeError, match="torchaudio is not installed in this image.*25 Hz tokenizer.*12 Hz models the studio uses do not"):
        namespace["kaldi"].fbank(object(), sample_frequency=16000)  # whatever the 25 Hz code passes, it is refused with the reason


def test_with_torchaudio_the_real_one_is_used_untouched(tmp_path):
    """Where torchaudio IS there (a machine that has it), the patch must change nothing: the real `kaldi` is what the file ends up with."""
    folder = _library(tmp_path)
    patch_module.patch(folder)
    fake = tmp_path / "fake" / "torchaudio" / "compliance"
    fake.mkdir(parents=True)
    (fake.parent / "__init__.py").write_text("", encoding="utf-8")
    (fake / "__init__.py").write_text("", encoding="utf-8")
    (fake / "kaldi.py").write_text("def fbank(*args, **kwargs):\n    return 'the real fbank'\n", encoding="utf-8")
    block = _patched_block((folder / RELATIVE).read_text(encoding="utf-8"))
    code = f"{block}\nprint(kaldi.fbank())"
    ran = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"PYTHONPATH": str(tmp_path / "fake"), "PATH": ""}, timeout=60)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "the real fbank"


def test_the_patched_file_still_imports_in_a_process_of_its_own(tmp_path):
    """End to end, as far as it can go here: the patched head of the real file, with its other imports supplied as empty modules, imports with no torchaudio and its
    `features()` then refuses with the reason, instead of the import failing as it did on the Spark."""
    folder = _library(tmp_path)
    patch_module.patch(folder)
    stubs = tmp_path / "stubs"
    for name in ("sox", "onnxruntime", "librosa"):
        (stubs / name).mkdir(parents=True)
        (stubs / name / "__init__.py").write_text("", encoding="utf-8")
    (stubs / "librosa" / "filters.py").write_text("def mel(*a, **k):\n    return None\n", encoding="utf-8")
    (stubs / "torch").mkdir()
    (stubs / "torch" / "__init__.py").write_text("class Tensor: ...\n", encoding="utf-8")
    (stubs / "torch" / "nn").mkdir()
    (stubs / "torch" / "nn" / "__init__.py").write_text("", encoding="utf-8")
    (stubs / "torch" / "nn" / "functional.py").write_text("", encoding="utf-8")
    code = ("import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('speech_vq', {str(folder / RELATIVE)!r})\n"
            "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
            "try:\n    module.features(1)\nexcept RuntimeError as exc:\n    print('refused:', exc)\n")
    ran = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"PYTHONPATH": str(stubs), "PATH": ""}, timeout=60)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.startswith("refused: torchaudio is not installed in this image")


# ------------------------------------------------------------------ what it refuses, and doing it twice

def test_patching_twice_changes_nothing_the_second_time(tmp_path):
    folder = _library(tmp_path)
    assert patch_module.patch(folder) == "patched"
    once = (folder / RELATIVE).read_bytes()
    assert patch_module.patch(folder) == "already patched"
    assert (folder / RELATIVE).read_bytes() == once


@pytest.mark.parametrize("text, why", [
    ("import sox\nimport torchaudio as ta\n", "another form of the import"),
    ("import sox\n", "no import at all"),
    ("import torchaudio.compliance.kaldi as kaldi\nimport torchaudio.compliance.kaldi as kaldi\n", "the line twice"),
    ("    import torchaudio.compliance.kaldi as kaldi\n", "the line inside something else (not at the margin)"),
])
def test_a_file_that_is_not_the_one_it_was_written_for_is_left_alone(tmp_path, text, why):
    folder = _library(tmp_path, text)
    before = (folder / RELATIVE).read_bytes()
    with pytest.raises(ValueError, match="another version"):
        patch_module.patch(folder)
    assert (folder / RELATIVE).read_bytes() == before, why


def test_a_missing_library_is_said_so_not_crashed_on(tmp_path):
    with pytest.raises(ValueError, match="is not there; is qwen-tts installed"):
        patch_module.patch(tmp_path / "nothing-here")


def test_as_a_process_the_exit_code_is_the_answer(tmp_path):
    """The Dockerfile reads the exit code (`|| echo NOTE`): 0 when patched or already patched, 1 when it could not."""
    folder = _library(tmp_path)
    first, second = _run(folder), _run(folder)
    assert (first.returncode, second.returncode) == (0, 0)
    assert "patched" in first.stdout and "already patched" in second.stdout
    refused = _run(tmp_path / "empty")
    assert refused.returncode == 1 and "not done" in refused.stdout


# ------------------------------------------------------------------ where it sits in the Dockerfile

def test_the_dockerfile_patches_after_the_library_exists_and_before_the_check_that_must_see_it():
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()

    def first_line(needle: str) -> int:
        found = [i for i, line in enumerate(lines) if needle in line and not line.lstrip().startswith("#")]
        assert found, f"the Dockerfile has no line with {needle!r}"
        return found[0]

    installed = first_line("--target /opt/speech-libs/qwen3-tts")   # the folder exists from here
    copied = first_line("COPY docker/ /app/docker/")                  # the script is in the image from here
    patched = first_line("patch_qwen_tts.py /opt/speech-libs/qwen3-tts")
    checked = first_line("RUN python /app/docker/check_image.py")     # the check that probes each engine and must see the patched library
    assert installed < copied < patched < checked


def test_a_failed_patch_cannot_fail_the_build():
    """Every speech step is optional (the Dockerfile says so); this one must end in `|| echo NOTE...` like the others."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    start = text.index("RUN python /app/docker/patch_qwen_tts.py")
    step = text[start:text.index("\n\n", start)]
    assert "|| echo \"NOTE:" in step


def test_the_script_is_in_the_build_context():
    """`.dockerignore` must not leave it out (test_docker_context.py checks the folder as a whole; this names the file, so that a move of it is noticed)."""
    assert SCRIPT.is_file()
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert not [line for line in ignored if line.strip() in ("docker", "docker/", "docker/patch_qwen_tts.py", "*.py")]
