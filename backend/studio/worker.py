"""The GPU worker process (DESIGN.md §4, §9). Started by the API as `python -m studio.worker`.

Protocol: JSON lines. Commands arrive on stdin; events leave on the *original* stdout.
Before anything else runs, file descriptor 1 is redirected to stderr, so print(),
progress bars and C libraries can never corrupt the protocol channel.

Commands: {"cmd": "load"} | {"cmd": "run", "job": {...}} | {"cmd": "cancel", "run_id": "..."}
          | {"cmd": "shutdown"}
Events:   hello, state, load_failed, run_started, progress, image_done,
          run_finished, run_failed, run_canceled, protocol_error, bye

Commands are read by a thread of their own, so a cancel is seen while a run is under way. It
stops the run at the next step (or before the next image, or once a model load in progress has
finished); images already finished stay on disk. Everything else waits in a queue for the main
thread, which does the work.
"""

from __future__ import annotations

import argparse
import dataclasses
import errno
import json
import logging
import os
import queue
import re
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, TextIO

PROTOCOL_VERSION = 1
PROGRESS_INTERVAL = 0.25  # seconds between progress events (first and last step always sent)
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")

log = logging.getLogger("studio.worker")


def claim_stdout() -> TextIO:
    """Keep a private handle on the real stdout and point fd 1 (and sys.stdout) at stderr."""
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(protocol_fd, "w", buffering=1, encoding="utf-8")


class Emitter:
    """Writes one JSON line per event. Two threads emit (the worker and the command reader), so
    each line is written under a lock and can never be interleaved with another."""

    def __init__(self, stream: TextIO):
        self._stream = stream
        self._lock = threading.Lock()

    def __call__(self, event: str, **fields: Any) -> None:
        line = json.dumps({"event": event, **fields}, separators=(",", ":"), ensure_ascii=False) + "\n"
        with self._lock:
            self._stream.write(line)
            self._stream.flush()


def save_png(image: Any, path: Path, meta: dict[str, Any]) -> None:
    """Write atomically (temp file + rename) with the run's settings in PNG text chunks."""
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    for key, value in meta.items():
        if value is not None:
            info.add_text(key, str(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    try:
        image.save(partial, format="PNG", pnginfo=info)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


class Worker:
    def __init__(self, pipeline: Any, data_dir: Path, emit: Emitter):
        self.pipeline = pipeline
        self.data_dir = data_dir.resolve()
        self.emit = emit
        self.loaded = False
        self.last_load_error: Optional[dict[str, Any]] = None
        # Run ids the API asked to cancel. Filled by the command-reader thread, read by the thread
        # that is generating; adding to and testing a set are atomic, so no lock is needed.
        self._canceled: set[str] = set()

    def request_cancel(self, run_id: str) -> None:
        self._canceled.add(run_id)

    def load(self) -> bool:
        from .pipelines import PipelineError

        self.emit("state", state="loading")
        started = time.monotonic()
        try:
            info = self.pipeline.load()
        except PipelineError as exc:
            self.last_load_error = exc.as_dict()
        except Exception as exc:  # keep the worker alive and report it
            log.exception("model load crashed")
            self.last_load_error = {
                "kind": "load_failed",
                "message": f"Loading the model failed: {type(exc).__name__}: {exc}",
                "hint": None,
            }
        else:
            self.loaded = True
            self.last_load_error = None
            log.info("pipeline %s ready in %.1fs", self.pipeline.name, time.monotonic() - started)
            self.emit("state", state="ready", info=info)
            return True
        self.emit("load_failed", error=self.last_load_error)
        return False

    def _progress_callback(self, run_id: str, index: int, count: int) -> Callable[[int, int], None]:
        from .pipelines import Canceled

        last = 0.0

        def on_step(step: int, total: int) -> None:
            nonlocal last
            now = time.monotonic()
            if step in (1, total) or now - last >= PROGRESS_INTERVAL:
                last = now
                self.emit("progress", run_id=run_id, image=index + 1, of=count, step=step, steps=total)
            if run_id in self._canceled:  # checked on every step, however rarely progress is reported
                raise Canceled()

        return on_step

    def _check_inputs(self, job: Any) -> list[str]:
        """The absolute paths of an edit's inputs, each checked to be a file in this run's own input folder
        (the same care as for the images the worker writes). Raises ValueError."""
        if job.mode == "edit" and not job.input_paths:
            raise ValueError("an edit needs at least one input image")
        if job.mode != "edit" and job.input_paths:
            raise ValueError("only an edit takes input images")
        folder = (self.data_dir / "inputs" / job.run_id).resolve()
        paths = []
        for rel in job.input_paths:
            path = (self.data_dir / rel).resolve()
            if path.parent != folder or not path.is_file():
                raise ValueError(f"input image {rel!r} is not a file in this run's input folder")
            paths.append(str(path))
        return paths

    def run(self, job_dict: Any) -> None:
        try:
            self._run(job_dict)
        finally:
            self._canceled.clear()  # a cancel only ever concerns the run that was current

    def _run(self, job_dict: Any) -> None:
        from .pipelines import Canceled, ImageJob, PipelineError

        run_id = job_dict.get("run_id") if isinstance(job_dict, dict) else None
        try:
            job = ImageJob(**job_dict)
            if not _RUN_ID.match(job.run_id) or not job.seeds:
                raise ValueError("bad run_id or empty seeds")
            job = dataclasses.replace(job, input_paths=self._check_inputs(job))
        except (TypeError, ValueError) as exc:
            self.emit("run_failed", run_id=run_id, completed=0,
                      error={"kind": "error", "message": f"Malformed job: {exc}", "hint": None})
            return

        if not self.loaded and not self.load():
            self.emit("run_failed", run_id=job.run_id, completed=0, error=self.last_load_error)
            return

        if job.run_id in self._canceled:  # cancelled while the model was loading
            self.emit("run_canceled", run_id=job.run_id, completed=0)
            return
        self.emit("run_started", run_id=job.run_id)
        completed = 0
        for index, seed in enumerate(job.seeds):
            if job.run_id in self._canceled:
                self.emit("run_canceled", run_id=job.run_id, completed=completed)
                return
            started = time.monotonic()
            try:
                image = self.pipeline.generate(job, index, seed, self._progress_callback(job.run_id, index, len(job.seeds)))
                rel = f"images/{job.run_id}/{index}.png"
                save_png(image, self.data_dir / rel, {
                    "Software": "ai-image-studio",
                    "mode": job.mode,
                    "model": job.model_id,
                    "prompt": job.prompt,
                    "negative_prompt": job.negative_prompt,
                    "seed": seed,
                    "steps": job.steps,
                    "cfg_scale": job.cfg_scale,
                    "inputs": len(job.input_paths) or None,
                    "resolution": job.resolution,
                    "size": f"{image.width}x{image.height}",
                })
            except Canceled:
                log.info("run %s canceled during image %d/%d", job.run_id, index + 1, len(job.seeds))
                self.emit("run_canceled", run_id=job.run_id, completed=completed)
                return
            except PipelineError as exc:
                self.emit("run_failed", run_id=job.run_id, completed=completed, error=exc.as_dict())
                return
            except OSError as exc:
                message = ("The disk is full; the image could not be saved." if exc.errno == errno.ENOSPC
                           else f"The image could not be saved: {exc.strerror or exc}")
                self.emit("run_failed", run_id=job.run_id, completed=completed,
                          error={"kind": "error", "message": message, "hint": None})
                return
            except Exception as exc:
                log.exception("generation crashed")
                self.emit("run_failed", run_id=job.run_id, completed=completed,
                          error={"kind": "error", "message": f"{type(exc).__name__}: {exc}", "hint": None})
                return
            completed += 1
            log.info("run %s image %d/%d done in %.1fs", job.run_id, index + 1, len(job.seeds), time.monotonic() - started)
            self.emit("image_done", run_id=job.run_id, idx=index, seed=seed, path=rel,
                      width=image.width, height=image.height)
        self.emit("run_finished", run_id=job.run_id, completed=completed)


def run_probe(pipeline: str, emit: Emitter) -> int:
    from .pipelines import PipelineError, probe_pipeline

    try:
        result = probe_pipeline(pipeline)
    except PipelineError as exc:
        if exc.__cause__ is not None:  # the full story, for `docker compose logs`
            log.error("capability check failed: %s", exc.message, exc_info=exc.__cause__)
        emit("probe", ok=False, error=exc.as_dict())
    except Exception as exc:
        log.exception("capability probe crashed")
        emit("probe", ok=False, error={"kind": "error", "message": f"{type(exc).__name__}: {exc}", "hint": None})
    else:
        emit("probe", ok=True, **result)
    return 0


def stdin_lines(fd: int = 0) -> Iterator[str]:
    """Lines from a file descriptor, read with os.read rather than through sys.stdin.

    The command-reader thread sits in a blocking read for the life of the process. Blocked inside
    sys.stdin's buffered reader, it holds that object's lock, and Python can abort at exit ("could
    not acquire lock for <_io.BufferedReader name='<stdin>'> at interpreter shutdown"). A raw
    read holds no Python lock, so the process can exit while the thread is still waiting."""
    pending = b""
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            if pending:
                yield pending.decode("utf-8", "replace")
            return
        pending += chunk
        while (newline := pending.find(b"\n")) >= 0:
            line, pending = pending[:newline], pending[newline + 1:]
            yield line.decode("utf-8", "replace")


def read_commands(lines: Iterable[str], commands: "queue.Queue[Optional[dict[str, Any]]]", worker: Worker, emit: Emitter) -> None:
    """Runs in its own thread. Passes every command to `commands` for the main thread, except
    `cancel`, which is acted on at once (the main thread may be busy generating). None marks the
    end of input."""
    try:
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("not an object")
            except ValueError as exc:
                emit("protocol_error", message=f"Unreadable command ({exc}).")
                continue
            if message.get("cmd") == "cancel":
                run_id = message.get("run_id")
                if isinstance(run_id, str) and _RUN_ID.match(run_id):
                    worker.request_cancel(run_id)
                else:
                    emit("protocol_error", message="A cancel command needs the run_id of the run to cancel.")
                continue
            commands.put(message)
    finally:
        commands.put(None)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m studio.worker")
    parser.add_argument("--pipeline", choices=["fake", "real"], required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cpu-offload", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--fake-step-delay-ms", type=int, default=30)
    parser.add_argument("--probe", action="store_true", help="report capabilities without loading weights, then exit")
    args = parser.parse_args(argv)

    # The API process owns the lifecycle (it sends "shutdown" or closes stdin); Ctrl-C in a
    # terminal reaches the whole process group, so ignore it here to avoid noisy tracebacks.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    protocol = claim_stdout()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(levelname)s %(message)s", stream=sys.stderr)
    emit = Emitter(protocol)

    if args.probe:
        return run_probe(args.pipeline, emit)

    from .pipelines import make_pipeline

    pipeline = make_pipeline(args.pipeline, model=args.model, cpu_offload=args.cpu_offload,
                             local_files_only=args.local_files_only, fake_step_delay_ms=args.fake_step_delay_ms)
    worker = Worker(pipeline, Path(args.data_dir), emit)
    emit("hello", pid=os.getpid(), pipeline=args.pipeline, protocol=PROTOCOL_VERSION)

    commands: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue()
    threading.Thread(target=read_commands, args=(stdin_lines(), commands, worker, emit), name="commands", daemon=True).start()
    while True:
        message = commands.get()
        if message is None:  # stdin closed: the API process has gone away
            return 0
        command = message.get("cmd")
        if command == "shutdown":
            emit("bye")
            return 0
        if command == "load":
            if not worker.loaded:
                worker.load()
        elif command == "run":
            worker.run(message.get("job"))
        else:
            emit("protocol_error", message=f"Unknown command {command!r}.")


if __name__ == "__main__":
    sys.exit(main())
