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

from . import __version__, fourk, sysinfo
from . import inputs as inputs_mod
from . import presets as P
from . import wavfile
from .config import Settings
from .db import Database
from .events import EventBus
from .projects import normalize_name
from .runspec import ResolvedRun, RunRequestError
from .serialize import format_ts, parse_ts, run_payload, utcnow
from .storage import Storage, StorageError
from .upscaler import UpscalerUnavailable, make_upscaler
from .worker_client import WorkerClient, WorkerGone, worker_env

log = logging.getLogger("studio.jobs")

RUN_EVENTS = frozenset({"run_started", "progress", "image_done", "track_done", "run_finished", "run_failed", "run_canceled"})
FAKE_MODEL_ID = "fake-pipeline"
FAKE_MUSIC_MODEL_ID = "fake-music"
KINDS = ("image", "music")  # the two models; only one is ever loaded (DESIGN.md §26.4)
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


class ImageNotFound(Exception):
    """No such result image, or its file is gone."""


class ProjectNotFound(Exception):
    """No such project (DESIGN.md §32)."""


class ProjectNameTaken(Exception):
    """Another project already has this name, ignoring case (DESIGN.md §32.4)."""


class InputStorageError(Exception):
    """The inputs of a new run could not be copied (usually: the disk is full)."""


class _MemoryShortfall(Exception):
    """There is not enough free memory to load a model (the message says how much is needed)."""


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
        # One worker process per model, never both alive: `_worker` is the one in use (or the last one used).
        self._clients = {kind: WorkerClient(settings, self._on_worker_event, kind) for kind in KINDS}
        self._worker = self._clients["image"]
        self._model: Optional[str] = None  # the model the worker state is about ("image" or "music"); None when nothing is loaded
        self._wakeup = asyncio.Event()
        self._run_events: asyncio.Queue = asyncio.Queue()
        self._current: Optional[str] = None
        self._progress: dict[str, dict[str, Any]] = {}
        self._cancel_requested: set[str] = set()  # running runs the user has asked to stop
        self._making_4k: dict[str, asyncio.Future] = {}  # image id -> the build in progress (DESIGN.md §27.3)
        self._enlarging: dict[str, asyncio.Future] = {}  # image id -> the enlargement in progress (DESIGN.md §28.3)
        # One heavy thing at a time on the GPU: a run (from picking it up to its last event) or an enlargement (DESIGN.md §28.3). A
        # lock hands itself on in the order it was asked, so an enlargement asked for during a run goes next, ahead of the runs
        # still queued, and a run asked for during an enlargement waits the few seconds it takes.
        self._gpu_gate = asyncio.Lock()
        self._enlarge_waiting: list[str] = []  # image ids whose enlargement is waiting for its turn, oldest first
        self.upscaler = make_upscaler(settings)
        self._worker_state: dict[str, Optional[str]] = {"state": "unloaded", "detail": None, "hint": None}
        self._worker_info: Optional[dict[str, Any]] = None
        self._task: Optional[asyncio.Task] = None
        self._probe_task: Optional[asyncio.Task] = None
        self._janitor_task: Optional[asyncio.Task] = None
        self._uploads_task: Optional[asyncio.Task] = None
        self._probe: dict[str, Any] = {"state": "pending"}
        self._music_probe: dict[str, Any] = {"state": "pending"}
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
        # First, before the loop is cancelled: the loop gives up the GPU gate on its way out, and an enlargement that is waiting for
        # it would take its turn and start a process of its own in the middle of a shutdown.
        for flight in list(self._enlarging.values()):  # an enlargement waiting its turn, or its process
            flight.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await flight
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
        return self.model_id_for("image")

    def model_id_for(self, kind: str) -> str:
        if kind == "music":
            return FAKE_MUSIC_MODEL_ID if self.settings.pipeline == "fake" else self.settings.music_model
        return FAKE_MODEL_ID if self.settings.pipeline == "fake" else self.settings.model

    def worker_status(self) -> dict[str, Any]:
        unload_at = None
        if self._unload_at is not None:
            unload_at = datetime.fromtimestamp(self._unload_at, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return {
            **self._worker_state,
            "pipeline": self.settings.pipeline,
            "model": self._model,
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
            # enlarge_waiting: the pictures whose Enlarge is queued behind the run that is going; the page shows "Waiting…" on them
            "queue": {"running": self._current, "queued": self.db.count_queued(), "cap": self.settings.queue_cap,
                      "enlarge_waiting": list(self._enlarge_waiting)},
            "memory": self.memory_status(),
        }

    def memory_status(self) -> dict[str, Any]:
        mem = sysinfo.memory() or {}
        return {**mem, "min_free_gb": self.settings.min_free_gb, "music_min_free_gb": self.settings.music_min_free_gb,
                "worker_rss_gb": sysinfo.process_rss_gb(self._worker.pid)}

    def music_status(self) -> dict[str, Any]:
        """Whether the music model can run here (DESIGN.md §26.3): the result of the start-up check of its libraries."""
        probe = self._music_probe
        error = probe.get("error") or {}
        return {
            "available": probe.get("state") == "done",
            "state": probe.get("state"),
            "reason": error.get("message"),
            "hint": error.get("hint"),
        }

    def modes(self) -> list[str]:
        return ["generate", *(["edit"] if self.supports().get("edit") else []), *(["music"] if self.music_status()["available"] else [])]

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
        images, inputs, tracks = self.db.images_for_runs(ids), self.db.inputs_for_runs(ids), self.db.tracks_for_runs(ids)
        positions = self.queue_positions() if any(r["status"] == "queued" for r in rows) else {}
        four_k = self._four_k_files([row for run_id in ids for row in (*images[run_id], *inputs[run_id])])
        return [
            run_payload(r, images[r["id"]], self._progress.get(r["id"]), positions.get(r["id"]),
                        r["id"] in self._cancel_requested, self.expires_at(r), inputs[r["id"]], tracks[r["id"]], four_k, self.purge_at(r))
            for r in rows
        ]

    def _four_k_files(self, rows: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
        """The 4K copy of each picture that has one (DESIGN.md §27.3, §28.2): its file's size in bytes, its width and height read
        from the file's header, and how it was made (`model` for an Enlarge copy, which is the better one and wins, else `resize`).
        The file's existence is the whole record, there is no database row. Every picture is looked for, not only the ones the
        rule offers now, so a copy made under an earlier rule is still shown."""
        found: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                for method, path in (("model", self.storage.enlarged_path(row["path"])), ("resize", self.storage.four_k_path(row["path"]))):
                    size = fourk.file_size(path)
                    if size is not None:
                        found[row["id"]] = {"bytes": path.stat().st_size, "width": size[0], "height": size[1], "method": method}
                        break
            except (OSError, StorageError):
                pass
        return found

    def expires_at(self, row: sqlite3.Row) -> Optional[str]:
        """When the run will be deleted, or None if it never will be (kept, still pending, or expiry off)."""
        days = self.settings.retention_days
        # no expiry for: expiry off, kept runs, runs that have not finished, and runs in the bin (the bin has its own clock, `purge_at`)
        if days <= 0 or row["pinned"] or row["status"] not in FINISHED or row["deleted_at"]:
            return None
        return format_ts(parse_ts(row["restored_at"] or row["created_at"]) + timedelta(days=days))  # from the later of the two (§30.4)

    def purge_at(self, row: sqlite3.Row) -> Optional[str]:
        """When a run in the bin will be deleted for good, or None if it is not in the bin (DESIGN.md §30.2)."""
        days = self.settings.bin_days
        if days <= 0 or not row["deleted_at"]:
            return None
        return format_ts(parse_ts(row["deleted_at"]) + timedelta(days=days))

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
            "model_id": self.model_id_for("music" if run.mode == "music" else "image"),
            "options_json": json.dumps(run.options_snapshot()),
            "lyrics": run.lyrics,
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
        """Keep a run, or stop keeping it. A run that is in a project stays kept, so stopping is refused with `RunConflict` (DESIGN.md §32.3)."""
        result = self.db.set_pinned(run_id, pinned)
        if result == "not_found":
            raise RunNotFound(run_id)
        if result == "locked":
            raise RunConflict("This run is in a project, so it stays kept. Take it out of the project first.")
        self._publish_run(run_id)

    # ------------------------------------------------------------ projects (DESIGN.md §32)
    def list_projects(self) -> list[dict[str, Any]]:
        """Every project with its counts, A to Z."""
        return self.db.list_projects()

    async def create_project(self, name: str) -> dict[str, Any]:
        """Make a project. Raises `projects.BadProjectName` for a name that cannot be used and `ProjectNameTaken` for one that is taken."""
        name = normalize_name(name)
        project_id = secrets.token_hex(16)
        if self.db.create_project(project_id, name, utcnow()) == "taken":
            raise ProjectNameTaken(name)
        self._publish_projects()
        return self.db.list_projects(project_id)[0]

    async def rename_project(self, project_id: str, name: str) -> dict[str, Any]:
        """Rename a project (`BadProjectName`, `ProjectNameTaken` and `ProjectNotFound` as the API needs them)."""
        name = normalize_name(name)
        result = self.db.rename_project(project_id, name)
        if result == "not_found":
            raise ProjectNotFound(project_id)
        if result == "taken":
            raise ProjectNameTaken(name)
        self._publish_projects()
        return self.db.list_projects(project_id)[0]

    async def delete_project(self, project_id: str) -> int:
        """Delete a project; its runs stay, kept and in no project. Returns how many runs were taken out of it. Every open page is told that
        each of those runs changed, and that the projects changed."""
        ids = self.db.delete_project(project_id)
        if ids is None:
            raise ProjectNotFound(project_id)
        for run_id in ids:
            self._publish_run(run_id)
        self._publish_projects()
        return len(ids)

    async def file_run(self, run_id: str, project_id: str) -> None:
        """File a run in a project and keep it, or move it from the project it is in (DESIGN.md §32.3). `RunNotFound` for a run that is not
        there, `ProjectNotFound` for a project that is not, `RunConflict` for a run in the bin."""
        result = self.db.file_run(run_id, project_id)
        if result == "not_found":
            raise RunNotFound(run_id)
        if result == "no_project":
            raise ProjectNotFound(project_id)
        if result == "in_bin":
            raise RunConflict("This run is in the bin. Restore it before filing it in a project.")
        self._publish_run(run_id)
        self._publish_projects()  # the counts of two projects may have moved

    async def unfile_run(self, run_id: str, keep: bool = True) -> None:
        """Take a run out of its project; it stays kept unless `keep` is False. `RunNotFound`, or `RunConflict` if it is in no project."""
        result = self.db.unfile_run(run_id, keep)
        if result == "not_found":
            raise RunNotFound(run_id)
        if result == "not_in_project":
            raise RunConflict("This run is not in a project.")
        self._publish_run(run_id)
        self._publish_projects()

    async def make_4k(self, image_id: str) -> tuple[dict[str, Any], bool]:
        """Make the 4K copy of a result image if it has none (DESIGN.md §27). Returns the updated run and whether this
        call made the file. Raises ImageNotFound, fourk.NotEligible (before any file work, from the stored size), or
        the OSError of a full disk. Requests for one image that overlap share one build: the file is made once."""
        image = self.db.get_image(image_id)
        if image is None or image["run_id"] is None:  # an image no run owns (a staged upload) is not part of the history
            raise ImageNotFound(image_id)
        fourk.plan_4k(image["width"], image["height"])  # NotEligible, with the reason, before touching any file
        build = self._making_4k.get(image_id)
        first = build is None
        if build is None:
            build = asyncio.ensure_future(asyncio.to_thread(self._build_4k, image))
            self._making_4k[image_id] = build
            # Whoever asked first may go away (a closed tab) while the thread still runs; its outcome is still collected.
            build.add_done_callback(lambda done: (self._making_4k.pop(image_id, None), done.cancelled() or done.exception()))
        made = await asyncio.shield(build)
        if first and made:
            self._publish_run(image["run_id"])  # other open pages show Download 4K
        payload = self.payload(image["run_id"])
        if payload is None:  # the run was deleted while the file was being made
            raise ImageNotFound(image_id)
        return payload, first and made

    def _build_4k(self, image: sqlite3.Row) -> bool:
        """In a thread: make the file unless it is already there. True if it was made now."""
        try:
            source, target = self.storage.abs(image["path"]), self.storage.four_k_path(image["path"])
        except StorageError as exc:
            raise ImageNotFound(image["id"]) from exc
        if fourk.file_size(target) is not None:  # there already, and a real PNG (a damaged leftover is made again)
            return False
        if fourk.file_size(self.storage.enlarged_path(image["path"])) is not None:  # the better copy is there: no plain one beside it
            return False
        try:
            fourk.make_4k(source, target)
        except FileNotFoundError as exc:  # the original is gone: its run was deleted under us
            raise ImageNotFound(image["id"]) from exc
        return True

    def upscaler_status(self) -> dict[str, Any]:
        """Whether Enlarge can run, and if not why and what to do (DESIGN.md §28.3): `capabilities.upscaler`."""
        return self.upscaler.availability().as_dict()

    async def enlarge(self, image_id: str) -> tuple[dict[str, Any], bool]:
        """Enlarge a picture to the 4K frame with the upscaler model, if it has no Enlarge copy yet (DESIGN.md §28). Returns the
        updated run and whether this call made the file. Raises ImageNotFound, fourk.NotEligible (before any file work, from the
        stored size), UpscalerError (no model, or it failed), or the OSError of a full disk. Requests for one picture that overlap
        share one enlargement, and one enlargement runs at a time."""
        image = self.db.get_image(image_id)
        if image is None or image["run_id"] is None:  # an image no run owns (a staged upload) is not part of the history
            raise ImageNotFound(image_id)
        fourk.plan_enlarge(image["width"], image["height"])  # NotEligible, with the reason, before touching any file
        try:
            already = fourk.file_size(self.storage.enlarged_path(image["path"])) is not None
        except StorageError as exc:
            raise ImageNotFound(image_id) from exc
        flight = self._enlarging.get(image_id)
        first = flight is None
        if not already and flight is None:
            availability = self.upscaler.availability()
            if not availability.available:
                raise UpscalerUnavailable(availability.reason or "Enlarge is not available.", availability.hint)
            flight = asyncio.ensure_future(self._build_enlarged(image))
            self._enlarging[image_id] = flight
            # Whoever asked first may go away (a closed tab) while the process still runs; its outcome is still collected.
            flight.add_done_callback(lambda done: (self._enlarging.pop(image_id, None), done.cancelled() or done.exception()))
        made = False if flight is None else await asyncio.shield(flight)
        if first and made:
            self._publish_run(image["run_id"])  # other open pages show Download 4K
        payload = self.payload(image["run_id"])
        if payload is None:  # the run was deleted while the file was being made
            raise ImageNotFound(image_id)
        return payload, first and made

    async def _build_enlarged(self, image: sqlite3.Row) -> bool:
        """Make the Enlarge copy unless it is there. True if it was made now. It waits for its turn on the GPU first (a picture
        being generated holds nearly all of it: DESIGN.md §28.3). The plain copy of the same picture, if there is one, is removed:
        the better copy replaces it."""
        try:
            source, target = self.storage.abs(image["path"]), self.storage.enlarged_path(image["path"])
        except StorageError as exc:
            raise ImageNotFound(image["id"]) from exc
        # Wait for the GPU. From here to the `finally`, this enlargement holds the gate, so no run can start while it works.
        await self._take_gpu_turn(image["id"])
        try:
            # Another request for the same picture may have finished while this one waited: then there is nothing left to do
            if fourk.file_size(target) is not None:
                return False
            try:
                await self.upscaler.enlarge(source, target)
            except FileNotFoundError as exc:  # the original is gone: its run was deleted under us
                raise ImageNotFound(image["id"]) from exc
        finally:
            # always give the GPU back, even when the upscaler failed
            self._gpu_gate.release()
        with suppress(OSError, StorageError):
            self.storage.four_k_path(image["path"]).unlink(missing_ok=True)
        return True

    async def _take_gpu_turn(self, image_id: str) -> None:
        """Wait until nothing else is using the GPU, and return holding `_gpu_gate` (the caller releases it). Something is using it
        while a run is going (the loop holds the gate) and while a model is being loaded by the Load button (a worker that is
        `loading`). Whoever has to wait is announced in `enlarge_waiting`, so the page can say so; one that does not is not."""
        # True once this request has been announced as waiting: it has to be taken out of `enlarge_waiting` however this ends
        waiting = False
        try:
            while True:
                # If something is using the GPU, say once that this enlargement is waiting (the page then shows "Waiting…"). If the GPU is free
                # it starts at once, without a word.
                if (self._gpu_gate.locked() or self._worker_state["state"] == "loading") and not waiting:
                    waiting = True
                    self._enlarge_waiting.append(image_id)
                    self._publish_queue()
                # Queue for the gate. A lock hands itself on in the order it was asked, so an enlargement asked for during a run goes next, ahead
                # of the runs still queued.
                await self._gpu_gate.acquire()
                # Got the gate. But a model that is still loading (the Load button) is using the GPU without holding it: if so, give the gate
                # back and look again in a moment.
                if self._worker_state["state"] != "loading":
                    return
                self._gpu_gate.release()  # a model is loading: let the loop go on, and look again in a moment
                await asyncio.sleep(0.5)
        finally:
            # however this ended (done, or cancelled by a shutdown), the request is no longer waiting
            if waiting:
                self._enlarge_waiting.remove(image_id)
                self._publish_queue()

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

    # ------------------------------------------------------------ the bin (DESIGN.md §30)
    async def bin_run(self, run_id: str) -> None:
        """Move a finished run to the bin: its files stay, it leaves the history, every open page is told (`run.updated`)."""
        result = self.db.bin_run(run_id, utcnow())
        if result == "not_found":
            raise RunNotFound(run_id)
        if result == "not_finished":
            raise RunConflict("This run has not finished. Cancel it or wait for it to finish; a run that is only waiting can be deleted for good instead.")
        self._publish_run(run_id)

    async def restore_run(self, run_id: str) -> None:
        """Take a run out of the bin: it goes back as it was, with a fresh expiry clock (§30.4)."""
        result = self.db.restore_run(run_id, utcnow())
        if result == "not_found":
            raise RunNotFound(run_id)
        if result == "not_in_bin":
            raise RunConflict("This run is not in the bin.")
        self._publish_run(run_id)

    async def empty_bin(self) -> int:
        """Delete for good every run in the bin, with its files. Returns how many."""
        return await self._purge_bin(None)

    # Delete runs from the bin for good, `SWEEP_BATCH` at a time so that a big bin never holds the database for long: the rows go first, in one
    # transaction; then their files, in a worker thread (the server stays responsive); then every open page is told.
    async def _purge_bin(self, cutoff: Optional[str]) -> int:
        removed = 0
        while True:
            ids = self.db.purge_bin(cutoff, SWEEP_BATCH)
            if not ids:
                break
            await asyncio.to_thread(lambda: [self.storage.delete_run_files(run_id) for run_id in ids])
            for run_id in ids:
                self._progress.pop(run_id, None)
                self.bus.publish("run.deleted", {"id": run_id})
            removed += len(ids)
        return removed

    # ------------------------------------------------------------ expiry
    async def sweep_expired(self) -> int:
        """The daily clean-up. First the runs whose time in the bin is over are deleted for good (everything in the bin when there is
        no bin); then finished runs older than the retention period are moved to the bin, or deleted if there is none. Kept runs, and
        runs that are queued or running, are never touched. Returns how many runs were deleted for good."""
        bin_days = self.settings.bin_days
        now = datetime.now(timezone.utc)
        # 1. Delete for good what has been in the bin long enough (all of it when there is no bin)
        removed = await self._purge_bin(format_ts(now - timedelta(days=bin_days)) if bin_days > 0 else None)
        if removed:
            log.info("deleted %d run(s) that had been in the bin for %d day(s) (STUDIO_BIN_DAYS)", removed, bin_days)
        days = self.settings.retention_days
        if days <= 0:
            return removed
        # 2. Expire. Finished runs older than the retention move to the bin, or are deleted outright when there is no bin.
        cutoff = format_ts(now - timedelta(days=days))
        moved = 0
        expired = 0
        while True:
            # with a bin: only mark the runs (their files stay, and every page is told that the run changed)
            if bin_days > 0:
                ids = self.db.expire_to_bin(cutoff, utcnow(), SWEEP_BATCH)
                if not ids:
                    break
                for run_id in ids:
                    self._publish_run(run_id)
                moved += len(ids)
            else:
                # without a bin: delete the rows and the files, as it was before the bin existed
                ids = self.db.delete_expired(cutoff, SWEEP_BATCH)
                if not ids:
                    break
                await asyncio.to_thread(lambda: [self.storage.delete_run_files(run_id) for run_id in ids])
                for run_id in ids:
                    self._progress.pop(run_id, None)
                    self.bus.publish("run.deleted", {"id": run_id})
                expired += len(ids)
        if moved:
            log.info("moved %d run(s) older than %d day(s) to the bin (STUDIO_RETENTION_DAYS)", moved, days)
        if expired:
            log.info("expired %d run(s) older than %d day(s) (STUDIO_RETENTION_DAYS)", expired, days)
        return removed + expired

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
                async with self._gpu_gate:  # not while an enlargement is using the GPU
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
                    try:
                        started_worker = await self._prepare_worker(self._kind_of(row))
                    except _MemoryShortfall as short:
                        self._finish(run_id, "failed", str(short), sysinfo.MEMORY_HINT)
                        return
                    # Anything stale goes, and only now: a worker that was being stopped (by an Unload this run waited
                    # for, or by `_prepare_worker` making room for the other model) reports its exit as the run's
                    # current event, and it must not be taken for a crash. Nothing of this run's own can be here yet.
                    while not self._run_events.empty():
                        self._run_events.get_nowait()
                if run_id in self._cancel_requested:  # cancelled while the worker was starting
                    if started_worker:
                        self._set_worker_state("unloaded")
                    self._finish(run_id, "canceled")
                    return
                await self._worker.send({"cmd": "run", "job": self._job_for(row)})
            except (WorkerGone, OSError) as exc:
                self._set_worker_state("error", f"Could not start the {self._worker.kind} worker: {exc}", "See the server log for details.")
                self._finish(run_id, "failed", f"Could not start the {self._worker.kind} worker: {exc}")
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
                if event.get("stage"):  # a music run says which stage it is in (compose, render, finish)
                    progress["stage"] = event["stage"]
                self._progress[run_id] = progress
                self.bus.publish("run.progress", {"id": run_id, "progress": progress})
            elif kind == "image_done":
                try:
                    await self._accept_image(row, event)
                except Exception as exc:
                    log.exception("could not store image %s of run %s", event.get("idx"), run_id)
                    storage_error = storage_error or f"An image could not be stored: {exc}"
            elif kind == "track_done":
                try:
                    await self._accept_track(row, event)
                except Exception as exc:
                    log.exception("could not store track %s of run %s", event.get("idx"), run_id)
                    storage_error = storage_error or f"A track could not be stored: {exc}"
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
                message = f"The {event.get('kind', 'image')} worker stopped unexpectedly (exit code {event.get('returncode')})."
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
    @staticmethod
    def _kind_of(row: sqlite3.Row) -> str:
        return "music" if row["mode"] == "music" else "image"

    async def _prepare_worker(self, kind: str) -> bool:
        """Make `kind`'s worker the one that is running, starting it (and stopping the other model's, which is idle:
        nothing calls this while a run is using it) if need be. The caller holds the lifecycle lock. True if a process
        was started. Raises _MemoryShortfall, with the state already set to an error, if the model would not fit."""
        if self._worker.alive() and self._model != kind:
            log.info("unloading the %s model to make room for the %s model", self._model, kind)
            self._unload_at = None
            await self._worker.stop()  # its exit report sets the state to unloaded before this returns
        self._worker = self._clients[kind]
        if self._worker.alive():
            return False
        self._model = kind
        shortfall = self._memory_shortfall(kind)
        if shortfall:
            self._set_worker_state("error", shortfall, sysinfo.MEMORY_HINT)
            raise _MemoryShortfall(shortfall)
        self._set_worker_state("loading")
        await self._worker.start()
        return True

    async def load_model(self, kind: str = "image") -> bool:
        """Start loading a model now, without a run. True if a load was started, False if there was nothing to do
        (it is loading or loaded already, or a run is using a model). Loading one model unloads the other first.
        Raises ModelRefused."""
        if kind not in KINDS:
            raise ModelRefused(422, "unknown_model", f"There is no {kind!r} model; use one of: {', '.join(KINDS)}.")
        if self.settings.idle_timeout_min <= 0:
            raise ModelRefused(409, "no_idle_time",
                               "The model unloads as soon as it is idle (STUDIO_IDLE_TIMEOUT_MIN=0), so loading it ahead of "
                               "time would unload it again at once.", "Set STUDIO_IDLE_TIMEOUT_MIN above 0 to use Load model.")
        async with self._lifecycle:
            if self._current is not None:
                return False
            if self._worker.alive() and self._model == kind and self._worker_state["state"] in ("loading", "ready", "busy"):
                return False
            # else nothing is loaded, or the other model is (it is unloaded first), or this worker is running but has
            # no model (a load that failed, or a run canceled while it started): it is asked to load below
            try:
                await self._prepare_worker(kind)
            except _MemoryShortfall as short:
                raise ModelRefused(409, "not_enough_memory", str(short), sysinfo.MEMORY_HINT) from short
            except (WorkerGone, OSError) as exc:
                message = f"Could not start the {kind} worker: {exc}"
                self._set_worker_state("error", message, "See the server log for details.")
                raise ModelRefused(503, "worker_failed", message, "See the server log for details.") from exc
            self._set_worker_state("loading")
            try:
                await self._worker.send({"cmd": "load"})
            except (WorkerGone, OSError) as exc:
                message = f"Could not start the {kind} worker: {exc}"
                self._set_worker_state("error", message, "See the server log for details.")
                raise ModelRefused(503, "worker_failed", message, "See the server log for details.") from exc
        self._wakeup.set()  # the job loop is waiting without a worker (or with a timer): let it look again
        return True

    async def unload_model(self, kind: Optional[str] = None) -> bool:
        """Unload the model now (only `kind`'s, if one is named). True if a worker was stopped. Raises ModelRefused
        while a run is running."""
        if kind is not None and kind not in KINDS:
            raise ModelRefused(422, "unknown_model", f"There is no {kind!r} model; use one of: {', '.join(KINDS)}.")
        async with self._lifecycle:
            if self._current is not None:
                raise ModelRefused(409, "busy", "The model is working on a run. Cancel it or wait for it to finish.")
            if not self._worker.alive() or (kind is not None and self._model != kind):
                return False
            loading = self._worker_state["state"] == "loading"  # a worker that is loading hears no polite request
            self._unload_at = None
            await self._worker.stop(busy=loading)
        self._wakeup.set()  # drop the idle timer that was running for the worker that is gone
        return True

    def _memory_shortfall(self, kind: str = "image") -> Optional[str]:
        """Fail fast (decision #19) instead of starting a load that can't fit next to Hermes."""
        needed = self.settings.music_min_free_gb if kind == "music" else self.settings.min_free_gb
        if needed is None:
            return None
        mem = sysinfo.memory()
        if mem is None:
            log.warning("could not read free memory; skipping the pre-load memory check")
            return None
        if mem["available_gb"] < needed:
            setting = "STUDIO_MUSIC_MIN_FREE_GB" if kind == "music" else "STUDIO_MIN_FREE_GB"
            return (f"Not enough free memory to load the {'music ' if kind == 'music' else ''}model: "
                    f"{mem['available_gb']:.1f} GB available, {needed:g} GB required ({setting}).")
        return None

    async def _probe_subprocess(self, kind: str) -> dict[str, Any]:
        """Run one model's start-up check in a worker process (`--probe`) and return its result."""
        result: dict[str, Any] = {}
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._clients[kind].command(probe=True), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, env=worker_env(self.settings, kind), limit=1 << 20)
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
        return result

    async def _run_probe(self) -> None:
        """Check each pipeline and the GPU without loading weights, so problems show before the first job. The image
        model's result sets the worker state when it failed; the music model's only says whether the Music tab can work."""
        self._probe = {"state": "running"}
        self._music_probe = {"state": "running"}
        result, music = await asyncio.gather(self._probe_subprocess("image"), self._probe_subprocess("music"))
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
        if music.get("ok"):
            self._music_probe = {"state": "done", "supports": music.get("supports") or {}, "device": music.get("device")}
            log.info("music capability check passed: %s", music.get("device"))
        else:
            error = music.get("error") or {}
            self._music_probe = {"state": "failed", "error": error}
            log.warning("music capability check failed: %s", error.get("message"))
        self.bus.publish("capabilities.updated", {"supports": self.supports(), "modes": self.modes(), "music": self.music_status()})
        self.bus.publish("worker.state", self.worker_status())

    def _with_partial(self, run_id: str, row: sqlite3.Row, message: str) -> str:
        music = row["mode"] == "music"
        done = len((self.db.tracks_for_runs if music else self.db.images_for_runs)([run_id])[run_id])
        if done:
            message += f" {done} of {row['num_images']} {'track(s)' if music else 'image(s)'} were finished and kept."
        return message

    async def _accept_track(self, row: sqlite3.Row, event: dict[str, Any]) -> None:
        run_id = row["id"]
        idx = int(event["idx"])
        path = self.storage.accept_worker_audio(str(event["path"]), run_id)
        info = await asyncio.to_thread(wavfile.read_wav, path)  # what the file itself says: its length, rate and size
        self.db.add_track({
            "id": secrets.token_hex(16),
            "run_id": run_id,
            "idx": idx,
            "seed": int(event["seed"]),
            "seconds": round(info.seconds, 3),
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "bytes": info.bytes,
            "path": self.storage.rel(path),
            "created_at": utcnow(),
        })
        self._publish_run(run_id)

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
        if row["mode"] == "music":
            return {
                "run_id": row["id"],
                "mode": "music",
                "prompt": row["effective_prompt"],
                "lyrics": row["lyrics"] or P.INSTRUMENTAL_LYRICS,  # instrumental when the user wrote none
                "duration": json.loads(row["options_json"]).get("duration") or P.MUSIC_DEFAULT_SECONDS,
                "steps": row["steps"],
                "seeds": [row["seed"] + i for i in range(row["num_images"])],
                "model_id": row["model_id"],
            }
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

    def _publish_projects(self) -> None:
        """Tell every open page that the projects, or how many runs each holds, changed: each page then asks for the list again (DESIGN.md §32.4).
        The event carries nothing; the list is the page's to read, because its counts depend on nothing but the database."""
        self.bus.publish("projects.changed", {})

    def _publish_queue(self) -> None:
        self.bus.publish("queue.updated", {
            "running": self._current,
            "positions": self.queue_positions(),
            "cap": self.settings.queue_cap,
            "enlarge_waiting": list(self._enlarge_waiting),  # enlargements waiting for the picture that is being made
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
                if self._worker.kind == "image":  # the music worker's info describes music, not what pictures can do
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
                self._model = None
                self._set_worker_state("unloaded")
            else:
                self._set_worker_state(
                    "error", f"The {event.get('kind', 'image')} worker stopped unexpectedly (exit code {event.get('returncode')}).",
                    WORKER_RESTART_HINT,
                )
            if self._current is not None:
                self._run_events.put_nowait(event)
        elif kind == "hello":
            log.info("%s worker pid %s says hello (%s pipeline)", event.get("kind", "image"), event.get("pid"), event.get("pipeline"))
        elif kind == "protocol_error":
            log.error("%s worker reported a protocol error: %s", self._worker.kind, event.get("message"))
