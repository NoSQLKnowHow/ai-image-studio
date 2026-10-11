"""The server's side of a Vector run's clean-up (DESIGN.md §31.12.4): starting the short-lived process that turns one drawing into an SVG, and reading
what it says.

The engine (`studio/vector/`) is not imported here. A Vector run draws its pictures with the image worker, and then, for each picture, the job manager
runs `python -m studio.vector.job --source <png> --options <json file> --out <dir>` as a process of its own, like Enlarge's (§28.3): a crash or a
runaway in the clean-up cannot take down the loaded model, or the server. The job writes `combined.svg`, `layers.zip`, `preview.png` and `check.json`
into `<dir>` and prints one last line of JSON, `{"ok": true, "check": {...}}` or `{"ok": false, "error": {"message": ..., "hint": ...}}`. It exits 0
for both of those (a handled outcome); any other exit is a crash.

This module has the pure parts (the command, the reading of the answer) and the one `async` function that runs the process, so each can be tested
alone. The command is the seam for the tests of the server: `Settings.vector_job_command` replaces the engine's own with a fake job that writes
canned files, so no test of the server needs the engine or its libraries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Settings
from .worker_client import BACKEND_ROOT

log = logging.getLogger("studio.vectorrun")

# A clean-up that has not finished after this long is stopped (and the version is missing, with a reason), so that one stuck job cannot hold the queue
# for ever. The clean-up is pure CPU on one drawing: seconds, not minutes, so ten minutes is far more than it should ever need. The value is a guess
# (DESIGN.md §31.12.9 does not give one), and the job manager keeps it as an attribute so that a test can make it short.
JOB_TIMEOUT_SECONDS = 10 * 60

# The files a job leaves in its output folder that the server keeps, and the names they are kept under (`<idx>` is the version's number). `check.json` is
# not kept as a file: the same check is in the job's last line, and it is stored in the database.
OUTPUT_FILES = ("combined.svg", "layers.zip", "preview.png")

CRASHED = "The clean-up stopped unexpectedly (exit code {code})."
UNREADABLE = "The clean-up stopped unexpectedly: it gave an answer the studio could not read."
NOT_MADE = "The clean-up could not make an SVG from this drawing."
TIMED_OUT = "The clean-up took longer than {minutes:g} minutes and was stopped."
SEE_THE_LOG = "See the server log for details."


@dataclass(frozen=True)
class JobResult:
    """What one run of the clean-up came to: the check it reported when it made the files, or why it did not (`message`, and a `hint` where the job
    gave one). Exactly one of `check` and `message` is set."""

    check: Optional[dict[str, Any]] = None
    message: Optional[str] = None
    hint: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.check is not None


def job_command(settings: Settings, source: Path, options_file: Path, out_dir: Path) -> list[str]:
    """The command that cleans up one drawing (§31.12.4). `settings.vector_job_command` replaces the engine's own program, and the three arguments
    are added in both cases, so a fake job has the same command line as the real one."""
    program = list(settings.vector_job_command) or [sys.executable, "-m", "studio.vector.job"]
    return [*program, "--source", str(source), "--options", str(options_file), "--out", str(out_dir)]


def job_environment() -> dict[str, str]:
    """The environment of the job: the studio's own folder first on the Python path (so `-m studio.vector.job` is found wherever the server was
    started from), and output that is not held back in a buffer."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(BACKEND_ROOT), env.get("PYTHONPATH", "")) if p)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def read_answer(returncode: Optional[int], stdout: str, stderr: str = "") -> JobResult:
    """Turn what the job did into a `JobResult`. A handled outcome is exit 0 and a last line of JSON (`ok` true with a `check`, or `ok` false with an
    `error`); a failure the job knew about is shown to the user in its own words. Anything else is a crash, or a job that said nothing readable, and
    is reported as *the clean-up stopped unexpectedly* with the last lines of what it wrote to its error output kept in the log, not shown."""
    if returncode != 0:
        log.warning("the vector clean-up exited with code %s: %s", returncode, _tail(stderr))
        return JobResult(message=CRASHED.format(code=returncode), hint=SEE_THE_LOG)
    lines = [line for line in stdout.splitlines() if line.strip()]
    try:
        answer = json.loads(lines[-1]) if lines else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or not isinstance(answer.get("ok"), bool):
        log.warning("the vector clean-up gave no readable answer: %s", _tail(stdout))
        return JobResult(message=UNREADABLE, hint=SEE_THE_LOG)
    if answer["ok"]:
        check = answer.get("check")
        if not isinstance(check, dict):
            log.warning("the vector clean-up said it was done but gave no check: %s", _tail(stdout))
            return JobResult(message=UNREADABLE, hint=SEE_THE_LOG)
        return JobResult(check=check)
    error = answer.get("error")
    error = error if isinstance(error, dict) else {}
    message, hint = error.get("message"), error.get("hint")
    return JobResult(message=message if isinstance(message, str) and message.strip() else NOT_MADE, hint=hint if isinstance(hint, str) and hint else None)


def _tail(text: str, lines: int = 8) -> str:
    """The last few lines of a process's output, for the log."""
    return " | ".join(line.strip() for line in text.strip().splitlines()[-lines:])


@dataclass(frozen=True)
class Summary:
    """The few numbers of a check that the database keeps as columns of the `vectors` row, so that a list of versions needs no JSON parsing."""

    colours: int
    layers: int
    width_mm: float
    height_mm: float


def summarize(check: dict[str, Any]) -> Summary:
    """The few numbers of a check the database keeps as columns. Raises `ValueError` if the check does not have them as the contract says
    (`colours_found` a whole number, `layers` a list, `size_mm` two numbers), so that a version with an unreadable check is reported as one that failed
    and nothing half-formed reaches the database or the page."""
    colours, layers, size = check.get("colours_found"), check.get("layers"), check.get("size_mm")
    if isinstance(colours, bool) or not isinstance(colours, int):
        raise ValueError("the check has no colours_found")
    if not isinstance(layers, list):
        raise ValueError("the check has no layers list")
    numbers = isinstance(size, list) and len(size) == 2 and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in size)
    if not numbers:
        raise ValueError("the check has no size_mm")
    return Summary(colours=colours, layers=len(layers), width_mm=float(size[0]), height_mm=float(size[1]))


async def run_job(
    settings: Settings, source: Path, options_file: Path, out_dir: Path, *, timeout: float = JOB_TIMEOUT_SECONDS,
    on_start: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
) -> JobResult:
    """Run the clean-up on one drawing and wait for it. `on_start` is called with the process as soon as it exists, so that Cancel can kill it. A job that
    takes longer than `timeout` seconds is killed and reported as failed. If this task is cancelled (the server is stopping) the process is killed too,
    and the cancellation goes on. Raises `OSError` when the process cannot be started at all (the caller reports it as a failed version)."""
    proc = await asyncio.create_subprocess_exec(
        *job_command(settings, source, options_file, out_dir), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=job_environment())
    if on_start is not None:
        on_start(proc)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        await _kill(proc)
        return JobResult(message=TIMED_OUT.format(minutes=timeout / 60), hint=SEE_THE_LOG)
    except asyncio.CancelledError:  # the server is shutting down: do not leave the process behind
        await _kill(proc)
        raise
    return read_answer(proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Stop a process and wait for it so that it is not left as a zombie. One that has ended already makes `kill` raise, which is no problem."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    await proc.wait()
