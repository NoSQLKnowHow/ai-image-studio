"""API-side handle on the worker subprocess (DESIGN.md §9)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Settings

log = logging.getLogger("studio.worker_client")
BACKEND_ROOT = Path(__file__).resolve().parent.parent


class WorkerGone(Exception):
    pass


def worker_env(settings: Optional[Settings] = None, kind: str = "image") -> dict[str, str]:
    """The environment of a worker process. The music worker gets `STUDIO_MUSIC_LIBS` (the image's own copy of the
    released diffusers) first on its Python path, so it never sees the pinned commit the image worker uses. Every worker
    gets `HF_HUB_DISABLE_TELEMETRY`, and with STUDIO_LOCAL_FILES_ONLY=true the hub libraries are told to stay offline."""
    env = dict(os.environ)
    paths = [str(BACKEND_ROOT), env.get("PYTHONPATH", "")]
    if kind == "music" and settings is not None and settings.music_libs is not None:
        paths.insert(0, str(settings.music_libs))
    env["PYTHONPATH"] = os.pathsep.join(p for p in paths if p)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if settings is not None and settings.hub_mode == "offline":
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    return env


class WorkerClient:
    def __init__(self, settings: Settings, on_event: Callable[[dict[str, Any]], None], kind: str = "image"):
        self.kind = kind  # "image" or "music": which model this worker process holds
        self._settings = settings
        self._on_event = on_event
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.Task] = None
        self._stopping = False

    def command(self, probe: bool = False) -> list[str]:
        s = self._settings
        cmd = [
            sys.executable, "-m", "studio.worker",
            "--kind", self.kind,
            "--pipeline", s.pipeline,
            "--data-dir", str(s.data_dir),
            "--model", s.music_model if self.kind == "music" else s.model,
            "--hub-mode", s.hub_mode,
            "--fake-step-delay-ms", str(s.fake_step_delay_ms),
        ]
        if s.cpu_offload:
            cmd.append("--cpu-offload")
        if probe:
            cmd.append("--probe")
        return cmd

    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self.alive() else None

    async def start(self) -> None:
        if self.alive():
            return
        env = worker_env(self._settings, self.kind)
        self._stopping = False
        self._proc = await asyncio.create_subprocess_exec(
            *self.command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            env=env,
            limit=1 << 20,
        )
        log.info("started %s worker pid %s (%s pipeline)", self.kind, self._proc.pid, self._settings.pipeline)
        self._reader = asyncio.create_task(self._read(self._proc), name="worker-reader")

    async def send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            raise WorkerGone(f"The {self.kind} worker is not running.")
        try:
            proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise WorkerGone(f"The {self.kind} worker went away ({exc}).") from exc

    async def stop(self, timeout: float = 10.0, busy: bool = False) -> None:
        """Stop the worker. An idle worker is asked to exit; a busy one only reads commands
        between jobs, so it is terminated instead (its run is being abandoned anyway)."""
        proc = self._proc
        if proc is None:
            return
        self._stopping = True
        if proc.returncode is None:
            if busy:
                with suppress(ProcessLookupError):
                    proc.terminate()
                timeout = min(timeout, 3.0)
            else:
                with suppress(WorkerGone):
                    await self.send({"cmd": "shutdown"})
            try:
                await asyncio.wait_for(proc.wait(), timeout)
            except asyncio.TimeoutError:
                log.warning("%s worker did not exit within %.0fs; killing it", self.kind, timeout)
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        if self._reader is not None:
            with suppress(Exception):
                await asyncio.wait_for(self._reader, 5)
        self._proc = None
        self._reader = None

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while True:
                try:
                    line = await proc.stdout.readline()
                except ValueError:  # a line longer than the buffer limit; it has been discarded
                    log.warning("discarded an over-long line from the %s worker", self.kind)
                    continue
                if not line:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    log.warning("ignoring non-protocol output from the %s worker: %r", self.kind, line[:200])
                    continue
                if not isinstance(event, dict) or "event" not in event:
                    log.warning("ignoring malformed event from the %s worker: %r", self.kind, line[:200])
                    continue
                self._dispatch(event)
        finally:
            returncode = await proc.wait()
            level = logging.INFO if self._stopping else logging.WARNING
            log.log(level, "%s worker pid %s exited with code %s", self.kind, proc.pid, returncode)
            self._dispatch({"event": "worker_exited", "returncode": returncode, "expected": self._stopping, "kind": self.kind})

    def _dispatch(self, event: dict[str, Any]) -> None:
        try:
            self._on_event(event)
        except Exception:
            log.exception("error while handling worker event %r", event.get("event"))
