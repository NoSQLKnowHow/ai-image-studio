"""Running the reader in a child process with limits (DESIGN.md §38.5 item 1, criterion 273).

A file or page from outside is parsed by libraries that were not written with a hostile file in mind, and the text cleaning is regular expressions applied
to whatever was pasted. Both run in `reader.py` in a child process that sets its own memory and CPU limits and is killed if it takes too long, so the worst
a hostile input can do is cost a minute and be refused. The child gets a bare environment (no secrets), runs from the backend folder (never the folder the
upload is in, where a planted module could be imported), and its answer is read as JSON, never executed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .errors import PASTE_HINT, PrepareError

BACKEND_ROOT = Path(__file__).resolve().parents[3]
MAX_OUTPUT_BYTES = 128 * 1024 * 1024  # the review of the longest allowed text is a few times its size; more than this is not a review


def run_reader(args: Sequence[str], *, timeout: float = 60.0, command: Optional[Sequence[str]] = None) -> dict[str, Any]:
    """Run the reader with `args` (see `reader.main`) and return its review, or raise the PrepareError it reported. `command` replaces the program (tests
    use it to stand in for a child that hangs, or eats memory, or prints rubbish); the real one is `python -m studio.speech.text.reader`."""
    argv = list(command) if command is not None else [sys.executable, "-m", "studio.speech.text.reader", *args]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(BACKEND_ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
           "LANG": "C.UTF-8"}
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout, env=env, cwd=BACKEND_ROOT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise PrepareError("timeout", "Reading the file took too long, so it was stopped.", "A very large or very tangled file can do that. " + PASTE_HINT) from exc
    except OSError as exc:
        raise PrepareError("extract_failed", f"The reader could not be started: {exc.strerror or exc}.") from exc
    if len(done.stdout) > MAX_OUTPUT_BYTES:
        raise PrepareError("too_large", "The file holds far more text than one reading can.")
    answer: Optional[dict[str, Any]] = None
    for line in reversed(done.stdout.decode("utf-8", "replace").splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and "ok" in candidate:
            answer = candidate
            break
    if answer is None:  # the child died (memory or CPU limit, a crash) before it could say anything
        raise PrepareError("extract_failed", "The file could not be read: it needed more time or memory than is allowed, or it is damaged.", PASTE_HINT)
    if answer["ok"]:
        return answer["review"]
    error = answer.get("error") or {}
    raise PrepareError(str(error.get("code") or "extract_failed"), str(error.get("message") or "The file could not be read."), error.get("hint"))
