"""The GPU worker process (DESIGN.md §4, §9). Started by the API as `python -m studio.worker`.

Protocol: JSON lines. Commands arrive on stdin; events leave on the *original* stdout.
Before anything else runs, file descriptor 1 is redirected to stderr, so print(),
progress bars and C libraries can never corrupt the protocol channel.

Commands: {"cmd": "load"} | {"cmd": "run", "job": {...}} | {"cmd": "shutdown"}
Events:   hello, state, load_failed, run_started, progress, image_done,
          run_finished, run_failed, protocol_error, bye
"""

from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

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
    def __init__(self, stream: TextIO):
        self._stream = stream

    def __call__(self, event: str, **fields: Any) -> None:
        self._stream.write(json.dumps({"event": event, **fields}, separators=(",", ":"), ensure_ascii=False) + "\n")
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
        last = 0.0

        def on_step(step: int, total: int) -> None:
            nonlocal last
            now = time.monotonic()
            if step in (1, total) or now - last >= PROGRESS_INTERVAL:
                last = now
                self.emit("progress", run_id=run_id, image=index + 1, of=count, step=step, steps=total)

        return on_step

    def run(self, job_dict: Any) -> None:
        from .pipelines import ImageJob, PipelineError

        run_id = job_dict.get("run_id") if isinstance(job_dict, dict) else None
        try:
            job = ImageJob(**job_dict)
            if not _RUN_ID.match(job.run_id) or not job.seeds:
                raise ValueError("bad run_id or empty seeds")
        except (TypeError, ValueError) as exc:
            self.emit("run_failed", run_id=run_id, completed=0,
                      error={"kind": "error", "message": f"Malformed job: {exc}", "hint": None})
            return

        if not self.loaded and not self.load():
            self.emit("run_failed", run_id=job.run_id, completed=0, error=self.last_load_error)
            return

        self.emit("run_started", run_id=job.run_id)
        completed = 0
        for index, seed in enumerate(job.seeds):
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
                    "size": f"{image.width}x{image.height}",
                })
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
        emit("probe", ok=False, error=exc.as_dict())
    except Exception as exc:
        log.exception("capability probe crashed")
        emit("probe", ok=False, error={"kind": "error", "message": f"{type(exc).__name__}: {exc}", "hint": None})
    else:
        emit("probe", ok=True, **result)
    return 0


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

    for raw in sys.stdin:
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
    return 0  # stdin closed: the API process has gone away


if __name__ == "__main__":
    sys.exit(main())
