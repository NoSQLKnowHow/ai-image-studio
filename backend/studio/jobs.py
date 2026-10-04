"""The job queue and its dispatch to the GPU worker (DESIGN.md §5.5a, §9).

The database is the source of truth for the queue: queued runs survive a restart and
are picked up again in order. One run executes at a time. Everything here runs on
the event loop; slow file work (thumbnails, deletes) is pushed to threads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from contextlib import suppress
from typing import Any, Optional

from . import __version__, sysinfo
from . import inputs as inputs_mod
from .config import Settings
from .db import Database
from .events import EventBus
from .runspec import ResolvedRun, RunRequestError
from .serialize import format_ts, parse_ts, run_payload, utcnow
from .storage import Storage
from .worker_client import WorkerClient, WorkerGone, worker_env

log = logging.getLogger("studio.jobs")

RUN_EVENTS = frozenset({"run_started", "progress", "image_done", "run_finished", "run_failed", "run_canceled"})
FAKE_MODEL_ID = "fake-pipeline"
SHUTDOWN_MESSAGE = "Interrupted because the server was stopped."
PROBE_TIMEOUT_SECONDS = 300  # importing torch + diffusers can be slow on a cold start
SWEEP_INTERVAL_SECONDS = 24 * 3600  # how often old runs are expired (once at start-up, then daily)
SWEEP_BATCH = 200  # runs removed per database transaction, so a first sweep of thousands never stalls the server
UPLOAD_SWEEP_SECONDS = 3600  # how often staged uploads nobody claimed are removed (and once at start-up)
FINISHED = frozenset({"done", "failed", "canceled"})
WORKER_RESTART_HINT = "The worker is restarted automatically for the next job; the server log has details."


class QueueFull(Exception):
    pass


class RunNotFound(Exception):
    pass


class RunConflict(Exception):
    pass


class InputStorageError(Exception):
    """The inputs of a new run could not be copied (usually: the disk is full)."""


class ModelRefused(Exception):
    """Loading or unloading the model on request was not possible (DESIGN.md §25.2). `status` is the HTTP answer."""

    def __init__(self, status: int, code: str, detail: str, hint: Optional[str] = None):
        super().__init__(detail)
        self.status, self.code, self.detail, self.hint = status, code, detail, hint


class JobManager:
    def __init__(self, settings: Settings, db: Database, storage: Storage, bus: EventBus):
        self.settings = settings
        self.db = db
        self.storage = storage
        self.bus = bus
        self._worker = WorkerClient(settings, self._on_worker_event)
        self._wakeup = asyncio.Event()
        self._run_events: asyncio.Queue = asyncio.Queue()
        self._current: Optional[str] = None
        self._progress: dict[str, dict[str, Any]] = {}
        self._cancel_requested: set[str] = set()  # running runs the user has asked to stop
        self._worker_state: dict[str, Optional[str]] = {"state": "unloaded", "detail": None, "hint": None}
        self._worker_info: Optional[dict[str, Any]] = None
        self._task: Optional[asyncio.Task] = None
        self._probe_task: Optional[asyncio.Task] = None
        self._janitor_task: Optional[asyncio.Task] = None
        self._uploads_task: Optional[asyncio.Task] = None
        self._probe: dict[str, Any] = {"state": "pending"}
        self._unload_at: Optional[float] = None
        # Starting and stopping the worker process happen one at a time: a run starting it, a Load, an Unload and the
        # idle timeout. Without this a click could meet a worker that is half stopped (DESIGN.md §25.3).
        self._lifecycle = asyncio.Lock()

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        recovered = self.db.recover_interrupted(utcnow())
        if recovered:
            log.warning("marked %d run(s) interrupted by a previous shutdown as failed", len(recovered))
        self._probe_task = asyncio.create_task(self._run_probe(), name="capability-probe")
        self._task = asyncio.create_task(self._loop(), name="job-loop")
        self._uploads_task = asyncio.create_task(self._upload_janitor(), name="upload-janitor")
        if self.settings.retention_days > 0:
            self._janitor_task = asyncio.create_task(self._janitor(), name="expiry-janitor")
        else:
            log.info("auto-expiry is off (STUDIO_RETENTION_DAYS=0): runs are kept until you delete them")

    async def stop(self) -> None:
        busy = self._current is not None  # read before cancelling: the loop clears it on the way out
        if self._probe_task is not None:
            self._probe_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._probe_task
        for name in ("_uploads_task", "_janitor_task"):
            task = getattr(self, name)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                setattr(self, name, None)
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._worker.stop(busy=busy)

    # ------------------------------------------------------------ queries
    @property
    def model_id(self) -> str:
        return FAKE_MODEL_ID if self.settings.pipeline == "fake" else self.settings.model

    def worker_status(self) -> dict[str, Any]:
        unload_at = None
        if self._unload_at is not None:
            unload_at = datetime.fromtimestamp(self._unload_at, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return {
            **self._worker_state,
            "pipeline": self.settings.pipeline,
            "pid": self._worker.pid,
            "unload_at": unload_at,
            "idle_timeout_min": self.settings.idle_timeout_min,
            "device": self._probe.get("device"),
            "probe": self._probe.get("state"),
        }

    def status(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "worker": self.worker_status(),
            "queue": {"running": self._current, "queued": self.db.count_queued(), "cap": self.settings.queue_cap},
            "memory": self.memory_status(),
        }

    def memory_status(self) -> dict[str, Any]:
        mem = sysinfo.memory() or {}
        return {**mem, "min_free_gb": self.settings.min_free_gb, "worker_rss_gb": sysinfo.process_rss_gb(self._worker.pid)}

    def supports(self) -> dict[str, bool]:
        found = self._supports_found()
        if "edit" in found:  # a list of images can go wherever one can; the Spark test confirms it (DESIGN.md §21.6)
            found["multi_image"] = bool(found["edit"])
        return found

    def _supports_found(self) -> dict[str, bool]:
        if self._worker_info and isinstance(self._worker_info.get("supports"), dict):
            return dict(self._worker_info["supports"])
        if isinstance(self._probe.get("supports"), dict):
            return dict(self._probe["supports"])
        if self.settings.pipeline == "fake":
            from .pipelines.fake import FakePipeline

            return dict(FakePipeline.SUPPORTS)
        return {}  # the real pipeline reports what it supports once loaded (M2)

    def queue_positions(self) -> dict[str, int]:
        return {run_id: i + 1 for i, run_id in enumerate(self.db.queued_ids())}

    def payloads(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        ids = [r["id"] for r in rows]
        images, inputs = self.db.images_for_runs(ids), self.db.inputs_for_runs(ids)
        positions = self.queue_positions() if any(r["status"] == "queued" for r in rows) else {}
        return [
            run_payload(r, images[r["id"]], self._progress.get(r["id"]), positions.get(r["id"]),
                        r["id"] in self._cancel_requested, self.expires_at(r), inputs[r["id"]])
            for r in rows
        ]

    def expires_at(self, row: sqlite3.Row) -> Optional[str]:
        """When the run will be deleted, or None if it never will be (kept, still pending, or expiry off)."""
        days = self.settings.retention_days
        if days <= 0 or row["pinned"] or row["status"] not in FINISHED:
            return None
        return format_ts(parse_ts(row["created_at"]) + timedelta(days=days))

    def payload(self, run_id: str) -> Optional[dict[str, Any]]:
        row = self.db.get_run(run_id)
        return None if row is None else self.payloads([row])[0]

    # ------------------------------------------------------------ commands
    async def submit(self, run: ResolvedRun) -> dict[str, Any]:
        """Queue a run. An edit first claims its inputs: each is *copied* into a folder the run owns (so the
        original can be deleted without breaking it), and staged uploads are consumed. All of it happens in one
        database transaction; if the queue is full or an upload was taken meanwhile, nothing is left behind."""
        run_id = secrets.token_hex(16)
        width, height = run.width, run.height
        owned: list[dict[str, Any]] = []
        claims: list[str] = []
        if run.mode == "edit":
            sources, problems = inputs_mod.resolve_refs(self.db, self.storage, list(run.inputs))
            if problems:
                raise RunRequestError(problems)
            width, height = self._shape_for(run, sources)
            try:
                owned = await asyncio.to_thread(inputs_mod.copy_inputs, self.storage, run_id, sources)
            except inputs_mod.InputFileMissing as gone:
                raise RunRequestError([(("input_images", gone.position - 1),
                                        f"Image {gone.position}: the image file is missing on the server. Add it again.")])
            except OSError as exc:
                raise InputStorageError(f"The images could not be copied for this run: {exc.strerror or exc}") from exc
            claims = [src.row["id"] for src in sources if src.staged]
        row = {
            "id": run_id,
            "created_at": utcnow(),
            "status": "queued",
            "mode": run.mode,
            "prompt": run.prompt,
            "effective_prompt": run.effective_prompt,
            "negative_prompt": run.negative_prompt,
            "transparent": int(run.transparent),
            "width": width,
            "height": height,
            "steps": run.steps,
            "cfg_scale": run.cfg_scale,
            "seed": run.seed,
            "num_images": run.num_images,
            "model_id": self.model_id,
            "options_json": json.dumps(run.options_snapshot()),
        }
        try:
            outcome = self.db.insert_run(row, self.settings.queue_cap, owned, claims)
        except BaseException:
            if owned:
                await asyncio.to_thread(self.storage.delete_run_files, run_id)
            raise
        if outcome != "ok":
            if owned:
                await asyncio.to_thread(self.storage.delete_run_files, run_id)  # the staged uploads stay, for another try
            if outcome == "full":
                raise QueueFull(f"The queue is full ({self.settings.queue_cap} jobs waiting). Try again when one finishes.")
            taken = outcome.split(":", 1)[1]
            raise RunRequestError([
                (("input_images", src.position - 1),
                 f"Image {src.position}: that upload was already used, or has expired. Add the image again.")
                for src in sources if src.row["id"] == taken
            ])
        for upload_id in dict.fromkeys(claims):  # the run has its own copies now
            try:
                await asyncio.to_thread(self.storage.delete_staged_files, upload_id)
            except OSError:  # the run exists and is queued; a file that can't be removed now is swept up later
                log.warning("could not remove the staged file of upload %s", upload_id, exc_info=True)
        payload = self.payload(run_id)
        assert payload is not None
        self.bus.publish("run.created", payload)
        self._publish_queue()
        self._wakeup.set()
        return payload

    @staticmethod
    def _shape_for(run: ResolvedRun, sources: list[inputs_mod.SourceImage]) -> tuple[Optional[int], Optional[int]]:
        """The explicit size for an edit whose Size is Auto but whose result should follow a particular image:
        the one chosen, or, when the last image is a mask (which must never set the shape), the last picture.
        Otherwise None: the pipeline sizes the result from the last image by itself."""
        if run.width is not None:
            return run.width, run.height
        follow = run.shape_from
        if follow is None and sources[-1].role == "mask":
            follow = max(src.position for src in sources if src.role != "mask")
        if follow is None:
            return None, None
        chosen = sources[follow - 1].row
        assert run.resolution is not None
        return inputs_mod.shape_dimensions(run.resolution, chosen["width"], chosen["height"])

    async def cancel(self, run_id: str) -> str:
        """Stop a run. Returns "canceled" (a queued run, stopped at once) or "canceling" (a running run,
        which stops at its next step; the worker confirms and the run becomes canceled then)."""
        row = self.db.get_run(run_id)
        if row is None:
            raise RunNotFound(run_id)
        if row["status"] == "queued" and self.db.cancel_queued(run_id, utcnow()):
            self._publish_run(run_id)
            self._publish_queue()
            return "canceled"
        row = self.db.get_run(run_id)  # re-read: it may have started in the meantime
        if row is None:
            raise RunNotFound(run_id)
        if row["status"] != "running" or self._current != run_id:
            raise RunConflict("This run has already finished.")
        newly = run_id not in self._cancel_requested
        self._cancel_requested.add(run_id)
        if newly:
            log.info("cancel requested for %s", run_id)
            self._publish_run(run_id)
        # A worker that has gone away is handled by its exit event, which fails the run.
        with suppress(WorkerGone, OSError):
            await self._worker.send({"cmd": "cancel", "run_id": run_id})
        return "canceling"

    async def set_pinned(self, run_id: str, pinned: bool) -> None:
        if not self.db.set_pinned(run_id, pinned):
            raise RunNotFound(run_id)
        self._publish_run(run_id)

    async def delete(self, run_id: str) -> None:
        result = self.db.delete_run(run_id)
        if result == "not_found":
            raise RunNotFound(run_id)
        if result == "running":
            raise RunConflict("This run is still generating. Wait for it to finish before deleting it.")
        await asyncio.to_thread(self.storage.delete_run_files, run_id)
        self._progress.pop(run_id, None)
        self.bus.publish("run.deleted", {"id": run_id})
        self._publish_queue()

    # ------------------------------------------------------------ expiry
    async def sweep_expired(self) -> int:
        """Delete finished runs older than the retention period, with their files. Kept runs, and runs
        that are queued or running, are never touched. Returns how many runs were removed."""
        days = self.settings.retention_days
        if days <= 0:
            return 0
        cutoff = format_ts(datetime.now(timezone.utc) - timedelta(days=days))
        removed = 0
        while True:
            ids = self.db.delete_expired(cutoff, SWEEP_BATCH)
            if not ids:
                break
            await asyncio.to_thread(lambda: [self.storage.delete_run_files(run_id) for run_id in ids])
            for run_id in ids:
                self._progress.pop(run_id, None)
                self.bus.publish("run.deleted", {"id": run_id})
            removed += len(ids)
        if removed:
            log.info("expired %d run(s) older than %d day(s) (STUDIO_RETENTION_DAYS)", removed, days)
        return removed

    async def _janitor(self) -> None:
        while True:
            try:
                await self.sweep_expired()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the expiry sweep failed; it will be tried again at the next interval")
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)

    async def sweep_uploads(self) -> int:
        """Remove staged uploads that no run claimed within STUDIO_UPLOAD_TTL_HOURS, and stray files."""
        return await asyncio.to_thread(inputs_mod.sweep_staged, self.db, self.storage, self.settings.upload_ttl_hours)

    async def _upload_janitor(self) -> None:
        while True:
            try:
                await self.sweep_uploads()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the upload clean-up failed; it will be tried again at the next interval")
            await asyncio.sleep(UPLOAD_SWEEP_SECONDS)

    # ------------------------------------------------------------ the loop
    async def _loop(self) -> None:
        while True:
            row = self.db.next_queued()
            if row is None:
                self._wakeup.clear()
                if self.db.next_queued() is None:  # re-check after clear: no lost wake-ups
                    await self._wait_while_idle()
                continue
            try:
                await self._execute(row)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("unexpected error while running %s", row["id"])
                self._finish(row["id"], "failed", "Internal error while running this job.", "See the server log for details.")
            finally:
                self._current = None

    async def _execute(self, row: sqlite3.Row) -> None:
        run_id = row["id"]
        if not self.db.mark_running(run_id, utcnow()):
            return  # deleted (or otherwise changed) since it was picked
        self._current = run_id
        self._publish_run(run_id)
        self._publish_queue()
        try:
            try:
                started_worker = False
                async with self._lifecycle:  # not while an Unload or the idle timeout is stopping it
                    # Anything stale goes, and only now: a worker that was being stopped while this run waited for the
                    # lock reports its exit as the run's current event, and it must not be taken for a crash.
                    while not self._run_events.empty():
                        self._run_events.get_nowait()
                    if not self._worker.alive():
                        shortfall = self._memory_shortfall()
                        if shortfall:
                            self._set_worker_state("error", shortfall, sysinfo.MEMORY_HINT)
                            self._finish(run_id, "failed", shortfall, sysinfo.MEMORY_HINT)
                            return
                        self._set_worker_state("loading")
                        await self._worker.start()
                        started_worker = True
                if run_id in self._cancel_requested:  # cancelled while the worker was starting
                    if started_worker:
                        self._set_worker_state("unloaded")
                    self._finish(run_id, "canceled")
                    return
                await self._worker.send({"cmd": "run", "job": self._job_for(row)})
            except (WorkerGone, OSError) as exc:
                self._set_worker_state("error", f"Could not start the image worker: {exc}", "See the server log for details.")
                self._finish(run_id, "failed", f"Could not start the image worker: {exc}")
                return
            await self._consume(row)
        except asyncio.CancelledError:
            self._finish(run_id, "failed", SHUTDOWN_MESSAGE)
            raise

    async def _consume(self, row: sqlite3.Row) -> None:
        run_id = row["id"]
        storage_error: Optional[str] = None
        while True:
            event = await self._run_events.get()
            kind = event.get("event")
            if kind == "run_started":
                self._set_worker_state("busy")
            elif kind == "progress":
                progress = {k: event.get(k) for k in ("image", "of", "step", "steps")}
                self._progress[run_id] = progress
                self.bus.publish("run.progress", {"id": run_id, "progress": progress})
            elif kind == "image_done":
                try:
                    await self._accept_image(row, event)
                except Exception as exc:
                    log.exception("could not store image %s of run %s", event.get("idx"), run_id)
                    storage_error = storage_error or f"An image could not be stored: {exc}"
            elif kind == "run_finished":
                if storage_error:
                    self._finish(run_id, "failed", storage_error, "Check free disk space and the server log.")
                else:
                    self._finish(run_id, "done")
                self._set_worker_state("ready")
                return
            elif kind == "run_canceled":
                if storage_error:
                    log.warning("run %s was canceled after a storage problem: %s", run_id, storage_error)
                self._finish(run_id, "canceled")
                self._set_worker_state("ready")
                return
            elif kind == "run_failed":
                error = event.get("error") or {}
                message = self._with_partial(run_id, row, error.get("message") or "Image generation failed.")
                self._finish(run_id, "failed", message, error.get("hint"))
                if self._worker_state["state"] == "busy":
                    self._set_worker_state("ready")
                return
            elif kind == "worker_exited":
                message = f"The image worker stopped unexpectedly (exit code {event.get('returncode')})."
                self._finish(run_id, "failed", self._with_partial(run_id, row, message), WORKER_RESTART_HINT)
                return

    async def _wait_while_idle(self) -> None:
        """Wait for work. A loaded model is unloaded (worker exits, memory freed) after the idle timeout. The clock is
        not running while the model is still loading (a Load without a run): the worker's report that it is ready, or
        that the load failed, wakes this up so the countdown starts then (DESIGN.md §25.3)."""
        if not self._worker.alive() or self._worker_state["state"] == "loading":
            await self._wakeup.wait()
            return
        timeout = self.settings.idle_timeout_min * 60
        self._unload_at = time.time() + timeout
        self.bus.publish("worker.state", self.worker_status())
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout)
        except asyncio.TimeoutError:
            async with self._lifecycle:
                if self._worker.alive():  # an Unload may have got there first
                    log.info("unloading the model after %.1f idle minute(s)", self.settings.idle_timeout_min)
                    self._unload_at = None
                    await self._worker.stop()
        finally:
            if self._unload_at is not None:
                self._unload_at = None
                self.bus.publish("worker.state", self.worker_status())

    # ------------------------------------------------------------ load and unload on request (DESIGN.md §25)
    async def load_model(self) -> bool:
        """Start loading the model now, without a run. True if a load was started, False if there was nothing to do
        (it is loading or loaded already, or a run is using it). Raises ModelRefused."""
        if self.settings.idle_timeout_min <= 0:
            raise ModelRefused(409, "no_idle_time",
                               "The model unloads as soon as it is idle (STUDIO_IDLE_TIMEOUT_MIN=0), so loading it ahead of "
                               "time would unload it again at once.", "Set STUDIO_IDLE_TIMEOUT_MIN above 0 to use Load model.")
        async with self._lifecycle:
            if self._current is not None:
                return False
            if self._worker.alive():
                if self._worker_state["state"] in ("loading", "ready", "busy"):
                    return False
                # else a worker that is running but has no model (a load that failed, or a run canceled while it started):
                # it is asked to load again below, and `start` leaves it be
            else:
                shortfall = self._memory_shortfall()
                if shortfall:
                    self._set_worker_state("error", shortfall, sysinfo.MEMORY_HINT)
                    raise ModelRefused(409, "not_enough_memory", shortfall, sysinfo.MEMORY_HINT)
            self._set_worker_state("loading")
            try:
                await self._worker.start()  # does nothing for a worker that is already running
                await self._worker.send({"cmd": "load"})
            except (WorkerGone, OSError) as exc:
                message = f"Could not start the image worker: {exc}"
                self._set_worker_state("error", message, "See the server log for details.")
                raise ModelRefused(503, "worker_failed", message, "See the server log for details.") from exc
        self._wakeup.set()  # the job loop is waiting without a worker (or with a timer): let it look again
        return True

    async def unload_model(self) -> bool:
        """Unload the model now. True if a worker was stopped. Raises ModelRefused while a run is running."""
        async with self._lifecycle:
            if self._current is not None:
                raise ModelRefused(409, "busy", "The model is working on a run. Cancel it or wait for it to finish.")
            if not self._worker.alive():
                return False
            loading = self._worker_state["state"] == "loading"  # a worker that is loading hears no polite request
            self._unload_at = None
            await self._worker.stop(busy=loading)
        self._wakeup.set()  # drop the idle timer that was running for the worker that is gone
        return True

    def _memory_shortfall(self) -> Optional[str]:
        """Fail fast (decision #19) instead of starting a load that can't fit next to Hermes."""
        needed = self.settings.min_free_gb
        if needed is None:
            return None
        mem = sysinfo.memory()
        if mem is None:
            log.warning("could not read free memory; skipping the pre-load memory check")
            return None
        if mem["available_gb"] < needed:
            return (f"Not enough free memory to load the model: {mem['available_gb']:.1f} GB available, "
                    f"{needed:g} GB required (STUDIO_MIN_FREE_GB).")
        return None

    async def _run_probe(self) -> None:
        """Check the pipeline and GPU without loading weights, so problems show before the first job."""
        self._probe = {"state": "running"}
        result: dict[str, Any] = {}
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._worker.command(probe=True), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, env=worker_env(), limit=1 << 20)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), PROBE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                out = b""
                result = {"ok": False, "error": {"kind": "error", "message": "The start-up capability check timed out.", "hint": None}}
            for line in reversed(out.decode("utf-8", "replace").splitlines()):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("event") == "probe":
                    result = event
                    break
        except OSError as exc:
            result = {"ok": False, "error": {"kind": "error", "message": f"Could not run the capability check: {exc}", "hint": None}}
        except asyncio.CancelledError:  # server stopping: don't leave the check running behind us
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        if not result:
            result = {"ok": False, "error": {"kind": "error", "message": "The capability check produced no result.", "hint": "See the server log."}}
        if result.get("ok"):
            self._probe = {"state": "done", "supports": result.get("supports") or {}, "device": result.get("device")}
            log.info("capability check passed: %s", result.get("device"))
        else:
            error = result.get("error") or {}
            self._probe = {"state": "failed", "error": error}
            log.warning("capability check failed: %s", error.get("message"))
            if self._current is None and not self._worker.alive():
                state = "unavailable" if error.get("kind") == "unavailable" else "error"
                self._set_worker_state(state, error.get("message"), error.get("hint"))
        self.bus.publish("capabilities.updated", {"supports": self.supports()})
        self.bus.publish("worker.state", self.worker_status())

    def _with_partial(self, run_id: str, row: sqlite3.Row, message: str) -> str:
        done = len(self.db.images_for_runs([run_id])[run_id])
        if done:
            message += f" {done} of {row['num_images']} image(s) were finished and kept."
        return message

    async def _accept_image(self, row: sqlite3.Row, event: dict[str, Any]) -> None:
        run_id = row["id"]
        idx = int(event["idx"])
        path = self.storage.accept_worker_image(str(event["path"]), run_id)
        info = await asyncio.to_thread(self.storage.image_info, path)
        thumb = await asyncio.to_thread(self.storage.make_thumbnail, path, run_id, idx)
        self.db.add_image({
            "id": secrets.token_hex(16),
            "run_id": run_id,
            "kind": "output",
            "idx": idx,
            "seed": int(event["seed"]),
            "width": info.width,
            "height": info.height,
            "has_alpha": int(info.has_alpha),
            "bytes": info.bytes,
            "path": self.storage.rel(path),
            "thumb_path": self.storage.rel(thumb),
            "created_at": utcnow(),
        })
        self._publish_run(run_id)

    def _finish(self, run_id: str, status: str, error: Optional[str] = None, hint: Optional[str] = None) -> None:
        self.db.finish_run(run_id, status, utcnow(), error, hint)
        if status == "failed":
            log.warning("run %s failed: %s", run_id, error)
        self._progress.pop(run_id, None)
        self._cancel_requested.discard(run_id)
        if self._current == run_id:
            self._current = None
        self._publish_run(run_id)
        self._publish_queue()

    def _job_for(self, row: sqlite3.Row) -> dict[str, Any]:
        inputs = self.db.inputs_for_runs([row["id"]])[row["id"]]
        return {
            "run_id": row["id"],
            "mode": row["mode"],
            "prompt": row["effective_prompt"],
            "negative_prompt": row["negative_prompt"],
            "width": row["width"],
            "height": row["height"],
            "steps": row["steps"],
            "cfg_scale": row["cfg_scale"],
            "seeds": [row["seed"] + i for i in range(row["num_images"])],
            "transparent": bool(row["transparent"]),
            "model_id": row["model_id"],
            "input_paths": [item["path"] for item in inputs],  # relative to the data folder, in the model's order
            "resolution": json.loads(row["options_json"]).get("resolution"),
        }

    # ------------------------------------------------------------ events
    def _publish_run(self, run_id: str) -> None:
        payload = self.payload(run_id)
        if payload is not None:
            self.bus.publish("run.updated", payload)

    def _publish_queue(self) -> None:
        self.bus.publish("queue.updated", {
            "running": self._current,
            "positions": self.queue_positions(),
            "cap": self.settings.queue_cap,
        })

    def _set_worker_state(self, state: str, detail: Optional[str] = None, hint: Optional[str] = None) -> None:
        new = {"state": state, "detail": detail, "hint": hint}
        if new != self._worker_state:
            self._worker_state = new
            self.bus.publish("worker.state", self.worker_status())

    def _on_worker_event(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind in RUN_EVENTS:
            if self._current is not None and event.get("run_id") == self._current:
                self._run_events.put_nowait(event)
            else:
                log.warning("ignoring %s for run %s (current run: %s)", kind, event.get("run_id"), self._current)
        elif kind == "state":
            if event.get("state") == "ready":
                self._worker_info = event.get("info") or {}
                self._set_worker_state("busy" if self._current else "ready")
                if self._current is None:
                    self._wakeup.set()  # a Load without a run: the idle countdown starts now
            elif event.get("state") == "loading":
                self._set_worker_state("loading")
        elif kind == "load_failed":
            error = event.get("error") or {}
            state = "unavailable" if error.get("kind") == "unavailable" else "error"
            self._set_worker_state(state, error.get("message"), error.get("hint"))
            if self._current is None:
                self._wakeup.set()
        elif kind == "worker_exited":
            if event.get("expected"):
                self._set_worker_state("unloaded")
            else:
                self._set_worker_state(
                    "error", f"The image worker stopped unexpectedly (exit code {event.get('returncode')}).",
                    WORKER_RESTART_HINT,
                )
            if self._current is not None:
                self._run_events.put_nowait(event)
        elif kind == "hello":
            log.info("image worker pid %s says hello (%s pipeline)", event.get("pid"), event.get("pipeline"))
        elif kind == "protocol_error":
            log.error("image worker reported a protocol error: %s", event.get("message"))
