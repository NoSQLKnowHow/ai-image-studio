"""HTTP API (DESIGN.md §7). Create with `create_app(settings)`."""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
import json
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import UnidentifiedImageError
from starlette.types import Scope

from . import __version__
from . import presets as P
from .builder import builder_limits
from .builder.chat_routes import add_chat_routes as add_builder_chat_routes
from .builder.engine import BuilderEngine
from .builder.library import Library
from .builder.prompt_routes import add_prompt_routes as add_builder_prompt_routes
from .builder.routes import add_routes as add_builder_routes
from .builder.service import BuilderService
from .builder.store import BuilderStore
from .speech import speech_limits
from .speech.routes import add_routes as add_speech_routes
from .config import Settings
from .db import Database
from .events import OVERFLOW, EventBus, format_sse
from . import fourk as fourk_mod
from . import vector as vector_mod
from .fourk import NotEligible
from . import inputs as inputs_mod
from .jobs import (
    ImageNotFound, InputStorageError, JobManager, ModelRefused, PictureConflict, ProjectNameTaken, ProjectNotFound, PromptRefRefused, QueueFull, QueueMoveRefused, RunAlwaysKept, RunConflict, RunNotFound,
    VariationRefused,
)
from .naming import (
    content_disposition, download_filename, music_filename, source_filename, thumbnail_filename, upscale_filename, vector_filename,
)
from .projects import BadProjectName
from .runfilter import RunFilter, counted_within
from .runspec import RecipeRequest, RunCreate, RunRequestError, resolve_recipe, resolve_run
from .security import SecurityMiddleware
from .serialize import PREVIEW_SIZE, parse_ts, utcnow, variations_status, vector_limits
from .storage import Storage, StorageError, check_id
from .upscaler import UpscalerError

log = logging.getLogger("studio.api")

HEARTBEAT_SECONDS = 15.0  # keep-alive comment on idle event streams (stops proxies closing them)
POLL_SECONDS = 1.0  # how quickly an idle event stream notices that the server is shutting down
HELLO_RUNS = 20  # newest runs in the event stream's opening snapshot (older pages: GET /api/runs)
IMMUTABLE = "private, max-age=31536000, immutable"
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"  # dev checkout


class UiFiles(StaticFiles):
    """The built web page. Vite names everything under assets/ by a hash of its contents, so those files
    can be cached for good; index.html (which names them) must be revalidated on every load, or a
    browser can keep showing an old page for a while after the studio has been rebuilt."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            response.headers["Cache-Control"] = IMMUTABLE if path.startswith("assets/") else "no-cache"
        return response


def server_stopping(app: FastAPI) -> bool:
    """True once uvicorn has been asked to exit. `build_server` puts the server on app.state."""
    server = getattr(app.state, "server", None)
    return bool(server is not None and server.should_exit)

PLACEHOLDER_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Studio</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#222;background:#fafafa}
@media (prefers-color-scheme:dark){body{color:#eee;background:#16161a}}code{font-size:.9em}</style></head>
<body><h1>AI Studio</h1>
<p>The backend is running, but the web interface has not been built. Build it with <code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code> (the container image does this for you).</p>
<p>API: <code>/api/health</code>, <code>/api/status</code>, <code>/api/capabilities</code>, <code>/api/runs</code>,
<code>/api/events</code>.</p></body></html>"""


class ModelChoice(BaseModel):
    """Which model a Load or Unload is about (DESIGN.md §26.3). Without a body, Load means the image model and Unload
    means whichever model is loaded."""

    model_config = ConfigDict(extra="forbid", strict=True)
    model: Literal["image", "music", "speech"]
    engine: Optional[str] = None  # a speech model is one engine in one variant (DESIGN.md §38.3); the variant defaults to the one that speaks the presets
    variant: Optional[str] = None


class RunPatch(BaseModel):
    """The one thing about a run that can be changed after it is made: whether it is kept."""

    model_config = ConfigDict(extra="forbid", strict=True)
    pinned: bool


class QueueMove(BaseModel):
    """Which waiting run to move, and the waiting run to put it next to: `before` or `after`, one of them (DESIGN.md section 40.2). Relative to another run and not to a
    position number, so that a queue that changed while the person was dragging cannot send the run somewhere they did not mean."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    before: Optional[str] = None
    after: Optional[str] = None


class ProjectBody(BaseModel):
    """The name of a project, to make one or to rename one (DESIGN.md §32.4). The name is checked by `projects.normalize_name`, not here, so the
    page is told in words why a name was refused."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str


class RunProject(BaseModel):
    """The project to file a run in (DESIGN.md §32.4)."""

    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str


UPSCALE_AT_ONCE = 2  # pictures being made through POST /api/upscale at the same moment
VECTOR_PNG_AT_ONCE = 2  # PNGs being drawn from an SVG at the same moment (DESIGN.md §31.12.7): at 8192 pixels each is a lot of memory
# What a vector's SVG is served with (DESIGN.md §31.12.7, §31.6): a policy that allows nothing at all (no script, no fetching, no framing, a sandbox) so
# that a file opened by itself can run nothing, and a ban on guessing another type. The studio's own clean-up wrote the file, but nothing here relies on that.
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
VECTOR_PNG_SIZE = {"min": 16, "max": 8192}  # `?size=` of the PNG route: the long side in pixels

# What `?project=` may be (DESIGN.md §32.4): the word `none` (runs in no project) or a project's id, which is 32 hex digits like every id. Anything
# else is a 422 from the framework; an id that is well formed but unknown is not an error, it matches no run.
PROJECT_PATTERN = r"^(none|[0-9a-f]{32})$"


def _png_size(data: bytes) -> Optional[tuple[int, int]]:
    """The width and height in a PNG's header, or None if `data` does not start like one."""
    if len(data) < 24 or data[:8] != fourk_mod.PNG_SIGNATURE or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _upscale_bytes(data: bytes) -> tuple[bytes, fourk_mod.Plan]:
    """A file from outside, checked as an upload is and made 4K, as PNG bytes. Synchronous: callers run it in a thread."""
    picture = inputs_mod.decode_upload(data)
    result, text, plan = fourk_mod.render_4k(picture)
    return fourk_mod.encode_png(result, text), plan


def _error(status: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


def _model_refused(exc: ModelRefused) -> JSONResponse:
    content = {"detail": exc.detail, "code": exc.code}
    if exc.hint:
        content["hint"] = exc.hint
    return JSONResponse(status_code=exc.status, content=content)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        storage = Storage(settings.data_dir)
        try:
            storage.ensure_layout()
        except StorageError as exc:
            log.error("%s", exc)
            raise
        removed = storage.cleanup_partials()
        if removed:
            log.info("removed %d half-written file(s) left by an earlier crash", removed)
        db = Database(settings.db_path)
        bus = EventBus()
        jobs = JobManager(settings, db, storage, bus)
        await jobs.start()
        app.state.settings, app.state.db, app.state.storage, app.state.bus, app.state.jobs = (
            settings, db, storage, bus, jobs)
        app.state.builder = BuilderService(settings)  # the Prompt builder's view of Ollama (DESIGN.md §37.4)
        app.state.builder_store = BuilderStore(db)  # its prompts, versions and chats (§37.4)
        app.state.builder_library = Library(app.state.builder_store)
        app.state.builder_engine = BuilderEngine(settings, app.state.builder, app.state.builder_store, storage)
        app.state.builder_slots = asyncio.Semaphore(settings.builder_at_once)  # one reply is written at a time (§37.3 rule 9)
        app.state.builder_jobs = {}  # chat id -> the reply being written in it
        if app.state.builder_store.ensure_search_in_step():
            log.info("the prompts' word index was not in step with the prompts and has been rebuilt")
        app.state.upscale_slots = asyncio.Semaphore(UPSCALE_AT_ONCE)
        app.state.vector_png_slots = asyncio.Semaphore(VECTOR_PNG_AT_ONCE)
        log.info("AI Studio %s ready (pipeline=%s, data=%s)", __version__, settings.pipeline, storage.root)
        try:
            yield
        finally:
            await jobs.stop()
            db.close()

    app = FastAPI(
        title="AI Studio",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(SecurityMiddleware, allowed_hosts=settings.allowed_hosts)

    def jobs_of(request: Request) -> JobManager:
        return request.app.state.jobs

    def runs_page(request: Request, limit: int, before: Optional[str] = None, run_filter: Optional[RunFilter] = None,
                  kind: Optional[str] = None) -> dict[str, Any]:
        """One page of runs, newest first, those `run_filter` lets through (and, with `kind`, only those of one tab). Raises KeyError for an unknown
        `before` cursor."""
        rows, has_more = request.app.state.db.list_runs(limit, before, run_filter, kind)
        runs = jobs_of(request).payloads(rows)
        return {"runs": runs, "next_before": runs[-1]["id"] if has_more and runs else None}

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        return jobs_of(request).status()

    @app.get("/api/capabilities")
    async def capabilities(request: Request) -> dict[str, Any]:
        jobs = jobs_of(request)
        width, height = P.DEFAULT_SIZE  # the page reads its default shape from "defaults" below and has no number of its own (DESIGN.md §34.4)
        supports = jobs.supports()
        return {
            "pipeline": settings.pipeline,
            "model": jobs.model_id,
            "modes": jobs.modes(),  # Edit only where the pipeline can edit, Music only where the music model can run, Vector where both halves can
            "supports": supports,
            "aspect_ratios": {name: list(size) for name, size in P.ASPECT_RATIOS.items()},
            "defaults": {
                "mode": "generate", "aspect_ratio": P.DEFAULT_ASPECT, "width": width, "height": height,
                "steps": P.DEFAULT_STEPS, "seed": None, "num_images": 1, "transparent": False,
            },
            "limits": {
                "prompt_chars": settings.max_prompt_chars,
                "input_images": {"min": 1, "max": settings.max_input_images},
                "draft": {"long_side": settings.draft_size, "steps": settings.draft_steps},
                "edit_warn_units": settings.edit_warn_units,
                "bin_days": settings.bin_days,
                "project_name_max": P.PROJECT_NAME_MAX,  # the longest a project's name may be (DESIGN.md §32.4), so the page can say it
                "resolutions": list(P.RESOLUTIONS),
                "upload_mb": settings.max_upload_mb,
                "steps": {"min": P.STEPS_MIN, "max": P.STEPS_MAX},
                "num_images": {"min": 1, "max": settings.max_images_per_run},
                "seed": {"min": 0, "max": P.SEED_MAX},
                "cfg_scale": {"min": P.CFG_MIN, "max": P.CFG_MAX},
                "size": {"min": P.SIZE_MIN, "max": P.SIZE_MAX, "multiple": P.SIZE_MULTIPLE, "max_pixels": P.MAX_PIXELS},
                "vector": vector_limits(),
                "builder": builder_limits(),
                "speech": speech_limits(settings),
                "music": {
                    "duration": {"min": P.MUSIC_DURATION_MIN, "max": settings.music_max_seconds, "default": P.MUSIC_DEFAULT_SECONDS},
                    "tracks": {"min": 1, "max": settings.music_max_tracks},
                    "steps": {"min": P.MUSIC_STEPS_MIN, "max": P.MUSIC_STEPS_MAX, "default": P.MUSIC_DEFAULT_STEPS},
                    "description_chars": P.MUSIC_DESCRIPTION_MAX,
                    "lyrics_chars": P.MUSIC_LYRICS_MAX,
                    "field_chars": P.MUSIC_FIELD_MAX,
                },
            },
            "music": {**jobs.music_status(), "model": jobs.model_id_for("music")},
            "vector": jobs.vector_status(),  # the Vector tab (DESIGN.md §31.12.7): {available, reason, hint}, like `music`
            "speech": jobs.speech_status(),  # the Speech tab (DESIGN.md §38.9): {available, reason, hint, engines: [...]}
            "upscaler": jobs.upscaler_status(),  # Enlarge (DESIGN.md §28.3): whether it can run here, and if not why
            # More like this (DESIGN.md §43.6): "More takes" always works; "Variations of this picture" needs the editing pipeline and the setting that says the Spark
            # experiment has been looked at. `reason` is what the dialog says beside the greyed choice.
            "variations": variations_status(settings, "edit" in jobs.modes()),
            "builder": request.app.state.builder.cached(),  # the Prompt builder (§37.4): set up? what is known of Ollama (null until something has looked)
            "queue_cap": settings.queue_cap,
            "device": jobs.worker_status().get("device"),
        }

    @app.post("/api/model/load")
    async def load_model(request: Request, body: Optional[ModelChoice] = None) -> Any:
        """Start loading a model now, without a run (DESIGN.md §25.2, §26.3). The image model unless one is named."""
        jobs = jobs_of(request)
        try:
            started = await jobs.load_model(body.model if body else "image", (body.engine or "") if body else "", (body.variant or "") if body else "")
        except ModelRefused as exc:
            return _model_refused(exc)
        return JSONResponse(status_code=202 if started else 200, content=jobs.status())

    @app.post("/api/model/unload")
    async def unload_model(request: Request, body: Optional[ModelChoice] = None) -> Any:
        """Unload the model now, unless a run is using it (DESIGN.md §25.2): the one named, or whichever is loaded."""
        jobs = jobs_of(request)
        try:
            await jobs.unload_model(body.model if body else None)
        except ModelRefused as exc:
            return _model_refused(exc)
        return jobs.status()

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunCreate, request: Request) -> Any:
        try:
            resolved = resolve_run(body, settings)
            return await jobs_of(request).submit(resolved)  # an edit's inputs are checked here, against what is stored
        except RunRequestError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.detail()})
        except QueueFull as exc:
            return _error(429, str(exc), "queue_full")
        except InputStorageError as exc:
            return _error(507, str(exc), "storage_full")
        except (PromptRefRefused, VariationRefused) as exc:  # the run it is made from (a saved prompt, or More like this of) is not there, or is in the bin
            return _error(exc.status, exc.message, exc.code)

    async def read_limited_body(request: Request, megabytes: Optional[int] = None) -> Any:
        """The request body as bytes, or the 413 response when it is larger than STUDIO_MAX_UPLOAD_MB (refused from the declared
        length when there is one, and while it streams in when there is not). Used by uploads and by Upscale a picture, and, with its own limit
        in `megabytes`, by the Speech tab's files (STUDIO_SPEECH_MAX_UPLOAD_MB)."""
        megabytes = megabytes or settings.max_upload_mb
        limit = megabytes * 1024 * 1024
        too_big = _error(413, f"The file is larger than {megabytes} MB.", "too_large")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            return too_big
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                return too_big
        return bytes(data)

    @app.post("/api/uploads", status_code=201)
    async def upload_image(request: Request) -> Any:
        """Stage one image for an edit. The file itself is the request body (not multipart: one image per call, the
        size can be capped while it streams in, and `curl --data-binary @photo.jpg` is all it takes). The type is
        decided by decoding it, never by the Content-Type header."""
        data = await read_limited_body(request)
        if isinstance(data, JSONResponse):
            return data
        try:
            return await asyncio.to_thread(inputs_mod.store_upload, request.app.state.db, request.app.state.storage, data)
        except inputs_mod.UploadError as exc:
            return _error(exc.status, exc.message, {413: "too_large", 415: "unsupported_type"}.get(exc.status, "unreadable"))
        except OSError as exc:
            return _error(507, f"The image could not be stored: {exc.strerror or exc}", "storage_full")

    @app.delete("/api/uploads/{upload_id}", status_code=204)
    async def delete_upload(upload_id: str, request: Request) -> Response:
        try:
            removed = await asyncio.to_thread(
                inputs_mod.delete_upload, request.app.state.db, request.app.state.storage, upload_id)
        except StorageError:
            removed = False
        if not removed:
            return _error(404, "No such upload (it may have been used by a run, or have expired).", "not_found")
        return Response(status_code=204)

    @app.get("/api/runs")
    async def list_runs(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        before: Optional[str] = Query(None),
        kept: Optional[bool] = Query(None, description="true: only kept runs; false: only runs that are not kept; absent: either"),
        deleted: Optional[bool] = Query(None, description="true: only the bin; false or absent: the history, without the bin"),
        project: Optional[str] = Query(None, pattern=PROJECT_PATTERN, description="a project's id: only its runs; 'none': only runs in no project; absent: any"),
        kind: Optional[Literal["image", "music", "vector", "speech"]] = Query(None, description="only the runs of one tab (DESIGN.md §31.12.7); absent: every tab"),
        prompt: Optional[str] = Query(None, pattern=PROJECT_PATTERN, description="a saved prompt's id: only the runs made from it (DESIGN.md §37.4)"),
        family: Optional[str] = Query(None, pattern=r"^[0-9a-f]{32}$", description="a run's id: that run and every variation made from it (DESIGN.md §43.4)"),
    ) -> Any:
        try:
            return runs_page(request, limit, before, RunFilter(kept=kept, deleted=bool(deleted), project=project, prompt=prompt, family=family), kind)
        except KeyError:
            return _error(400, "Unknown 'before' cursor.", "bad_cursor")

    @app.get("/api/runs/counts")
    async def run_counts(
        request: Request,
        project: Optional[str] = Query(None, pattern=PROJECT_PATTERN, description="count only this project's runs ('none': runs in no project)"),
    ) -> Any:
        """How many runs each tab holds and how many are kept or in the bin (DESIGN.md §29.6), within one project when `project` is given
        (§32.5). Declared before `/api/runs/{run_id}`, which would take it."""
        return request.app.state.db.run_counts(counted_within(project))

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> Any:
        payload = jobs_of(request).payload(run_id)
        return payload if payload is not None else _error(404, "Run not found.", "not_found")

    @app.patch("/api/runs/{run_id}")
    async def patch_run(run_id: str, body: RunPatch, request: Request) -> Any:
        try:
            check_id(run_id)
            await jobs_of(request).set_pinned(run_id, body.pinned)
        except (StorageError, RunNotFound):
            return _error(404, "Run not found.", "not_found")
        except RunAlwaysKept as exc:
            return _error(409, str(exc), "always_kept")  # a reading never expires, so it is kept from the start (DESIGN.md §38.8)
        except RunConflict as exc:
            # a run in a project stays kept (DESIGN.md §32.3): it has to be taken out of the project before it can stop being kept
            return _error(409, str(exc), "run_in_project")
        return jobs_of(request).payload(run_id)

    # ------------------------------------------------------------------ projects (DESIGN.md §32.4)
    @app.get("/api/projects")
    async def list_projects(request: Request) -> Any:
        """Every project, A to Z, with how many runs of each tab it holds (runs in the bin are not counted)."""
        return {"projects": jobs_of(request).list_projects()}

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectBody, request: Request) -> Any:
        try:
            return await jobs_of(request).create_project(body.name)
        except BadProjectName as exc:
            return _error(422, str(exc), "bad_name")
        except ProjectNameTaken:
            return _error(409, "There is a project with that name already.", "name_taken")

    @app.patch("/api/projects/{project_id}")
    async def rename_project(project_id: str, body: ProjectBody, request: Request) -> Any:
        try:
            check_id(project_id)
            return await jobs_of(request).rename_project(project_id, body.name)
        except (StorageError, ProjectNotFound):
            return _error(404, "Project not found.", "not_found")
        except BadProjectName as exc:
            return _error(422, str(exc), "bad_name")
        except ProjectNameTaken:
            return _error(409, "There is a project with that name already.", "name_taken")

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str, request: Request) -> Any:
        """Delete a project. Its runs are not deleted: they stay kept and are in no project. `unfiled` is how many that was."""
        try:
            check_id(project_id)
            return {"unfiled": await jobs_of(request).delete_project(project_id)}
        except (StorageError, ProjectNotFound):
            return _error(404, "Project not found.", "not_found")

    # File a run in a project, and keep it: one call (and one transaction) that also moves a run that is filed already
    @app.put("/api/runs/{run_id}/project")
    async def file_run(run_id: str, body: RunProject, request: Request) -> Any:
        # an id that is not even shaped like one cannot belong to anything: the same answer as for one that is not there. Run first, then project.
        try:
            check_id(run_id)
        except StorageError:
            return _error(404, "Run not found.", "not_found")
        try:
            check_id(body.project_id)
            await jobs_of(request).file_run(run_id, body.project_id)
        except RunNotFound:
            return _error(404, "Run not found.", "not_found")
        except (StorageError, ProjectNotFound):
            return _error(404, "Project not found.", "not_found")
        except RunConflict as exc:
            return _error(409, str(exc), "run_in_bin")
        return jobs_of(request).payload(run_id)

    # Take a run out of its project. It stays kept, unless `?keep=false` (what Undo of a filing sends: the run goes back to what it was)
    @app.delete("/api/runs/{run_id}/project")
    async def unfile_run(run_id: str, request: Request, keep: bool = Query(True)) -> Any:
        try:
            check_id(run_id)
            await jobs_of(request).unfile_run(run_id, keep)
        except (StorageError, RunNotFound):
            return _error(404, "Run not found.", "not_found")
        except RunConflict as exc:
            return _error(409, str(exc), "not_in_project")
        return jobs_of(request).payload(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request) -> Any:
        try:
            check_id(run_id)
            outcome = await jobs_of(request).cancel(run_id)
        except (StorageError, RunNotFound):
            return _error(404, "Run not found.", "not_found")
        except RunConflict as exc:
            return _error(409, str(exc), "run_finished")
        payload = jobs_of(request).payload(run_id)
        return JSONResponse(status_code=200 if outcome == "canceled" else 202, content=payload)

    # ------------------------------------------------------------------ the queue (DESIGN.md section 40)
    @app.get("/api/queue")
    async def get_queue(request: Request) -> Any:
        """The run that is going and the waiting runs, in order, with what the Queue panel says of each and whether it can still be moved."""
        return jobs_of(request).queue_listing()

    @app.post("/api/queue/move")
    async def move_in_queue(body: QueueMove, request: Request) -> Any:
        """Put a waiting run before or after another waiting run. 409 `not_movable` for a run that has started or finished, 409 `queue_changed` when the other run is no
        longer waiting, 404 for no such run, 422 for a request that does not name exactly one other run. Answers with the queue as it is now."""
        if (body.before is None) == (body.after is None):
            return _error(422, "Say which waiting job to put it before, or which to put it after: one, not both and not neither.", "invalid")
        try:
            check_id(body.id)
        except StorageError:
            return _error(404, "Run not found.", "not_found")
        try:
            for other in (body.before, body.after):
                if other is not None:
                    check_id(other)
        except StorageError:
            return _error(422, "That is not the id of a job.", "invalid")
        try:
            await jobs_of(request).move_queued(body.id, body.before, body.after)
        except QueueMoveRefused as exc:
            return _error(exc.status, str(exc), exc.code)
        return jobs_of(request).queue_listing()

    @app.delete("/api/runs/{run_id}", status_code=204)
    async def delete_run(run_id: str, request: Request) -> Response:
        try:
            check_id(run_id)
            await jobs_of(request).delete(run_id)
        except (StorageError, RunNotFound):
            return _error(404, "Run not found.", "not_found")
        except RunConflict as exc:
            return _error(409, str(exc), "run_active")
        return Response(status_code=204)

    @app.post("/api/runs/{run_id}/bin")
    async def bin_run(run_id: str, request: Request) -> Any:
        """Move a finished run to the bin (DESIGN.md §30.5). `DELETE` still means for good."""
        # With the bin turned off there is nothing to move a run to: refuse. The page knows (`capabilities.limits.bin_days`) and deletes for
        # good instead.
        if settings.bin_days <= 0:
            return _error(409, "The bin is turned off (STUDIO_BIN_DAYS=0): a deleted run is deleted for good.", "bin_off")
        try:
            check_id(run_id)
            await jobs_of(request).bin_run(run_id)
        except (StorageError, RunNotFound):
            return _error(404, "Run not found.", "not_found")
        except RunConflict as exc:
            return _error(409, str(exc), "run_not_finished")
        return jobs_of(request).payload(run_id)

    # Take a run out of the bin: 404 if there is no such run, 409 if it is not in the bin
    @app.post("/api/runs/{run_id}/restore")
    async def restore_run(run_id: str, request: Request) -> Any:
        try:
            check_id(run_id)
            await jobs_of(request).restore_run(run_id)
        except (StorageError, RunNotFound):
            return _error(404, "Run not found.", "not_found")
        except RunConflict as exc:
            return _error(409, str(exc), "not_in_bin")
        return jobs_of(request).payload(run_id)

    @app.delete("/api/bin")
    async def empty_bin(request: Request) -> Any:
        """Delete for good everything in the bin, with its files (DESIGN.md §30.5, §33.3): `deleted` is how many runs, `pictures` how many pictures
        and tracks of runs that stay (the ones in a run that was deleted went with it and are not counted)."""
        runs, pictures = await jobs_of(request).empty_bin()
        return {"deleted": runs, "pictures": pictures}

    # A single picture or track in the bin (DESIGN.md §33.3). The three routes are the same for both, so they are made once and registered for each:
    # send it to the bin (`bin_off` when there is no bin, as for a run), take it out, and delete it for good. All three answer 404 for an id that
    # is not there and 409, with a code, for a refusal (`PictureConflict`); the words of each are the job manager's.
    def picture_routes(kind: str, prefix: str, label: str) -> None:
        @app.post(f"/api/{prefix}/{{item_id}}/bin", name=f"bin_{kind}")
        async def bin_item(item_id: str, request: Request) -> Any:
            if settings.bin_days <= 0:
                return _error(409, f"The bin is turned off (STUDIO_BIN_DAYS=0): a deleted {label.lower()} is deleted for good.", "bin_off")
            try:
                check_id(item_id)
                moved, run = await jobs_of(request).bin_picture(kind, item_id)
            except (StorageError, ImageNotFound):
                return _error(404, f"{label} not found.", "not_found")
            except PictureConflict as exc:
                return _error(409, exc.detail, exc.code)
            return {"moved": moved, "run": run}

        @app.post(f"/api/{prefix}/{{item_id}}/restore", name=f"restore_{kind}")
        async def restore_item(item_id: str, request: Request) -> Any:
            try:
                check_id(item_id)
                return await jobs_of(request).restore_picture(kind, item_id)
            except (StorageError, ImageNotFound):
                return _error(404, f"{label} not found.", "not_found")
            except PictureConflict as exc:
                return _error(409, exc.detail, exc.code)

        @app.delete(f"/api/{prefix}/{{item_id}}", status_code=204, name=f"delete_{kind}")
        async def delete_item(item_id: str, request: Request) -> Response:
            try:
                check_id(item_id)
                await jobs_of(request).delete_picture(kind, item_id)
            except (StorageError, ImageNotFound):
                return _error(404, f"{label} not found.", "not_found")
            except PictureConflict as exc:
                return _error(409, exc.detail, exc.code)
            return Response(status_code=204)

    picture_routes("image", "images", "Picture")
    picture_routes("track", "tracks", "Track")
    picture_routes("vector", "vectors", "Version")  # DESIGN.md §35.4: a version of a Vector run is binned, restored and deleted as a picture is

    async def _image_file(request: Request, image_id: str, thumb: bool) -> tuple[Any, Optional[bytes]]:
        """(image row, file contents), or (None, None) when there's no such image.

        Read whole, up front: a run deleted while its images are being fetched (the UI loads
        thumbnails as runs appear, and deletes can come from another tab) then gives a clean 404
        instead of a 500 or a response cut short after its headers. Files are at most a few MB."""
        db: Database = request.app.state.db
        storage: Storage = request.app.state.storage
        image = db.get_image(image_id)
        rel = None if image is None else (image["thumb_path"] if thumb else image["path"])
        if rel is None:
            return None, None
        try:
            data = await asyncio.to_thread(storage.abs(rel).read_bytes)
        except (StorageError, FileNotFoundError, IsADirectoryError):
            return None, None
        return image, data

    @app.get("/api/images/{image_id}")
    async def get_image(image_id: str, request: Request, download: bool = False) -> Any:
        image, data = await _image_file(request, image_id, thumb=False)
        if image is None:
            return _error(404, "Image not found.", "not_found")
        headers = {"Cache-Control": IMMUTABLE}
        if download and image["kind"] == "output":
            run = request.app.state.db.get_run(image["run_id"])
            if run is not None:
                name = download_filename(
                    mode=run["mode"], prompt=run["prompt"], width=image["width"], height=image["height"],
                    seed=image["seed"], created_at=parse_ts(run["created_at"]), transparent=bool(run["transparent"]),
                )
                headers["Content-Disposition"] = content_disposition(name)
        return Response(data, media_type="image/png", headers=headers)

    @app.get("/api/images/{image_id}/thumb")
    async def get_thumb(image_id: str, request: Request, download: bool = False) -> Any:
        image, data = await _image_file(request, image_id, thumb=True)
        if image is None:
            return _error(404, "Thumbnail not found.", "not_found")
        headers = {"Cache-Control": IMMUTABLE}
        if download and image["kind"] == "output":  # a result's thumbnail is a download; anything else is only shown
            run = request.app.state.db.get_run(image["run_id"])
            if run is not None:
                name = download_filename(
                    mode=run["mode"], prompt=run["prompt"], width=image["width"], height=image["height"],
                    seed=image["seed"], created_at=parse_ts(run["created_at"]), transparent=bool(run["transparent"]),
                )
                headers["Content-Disposition"] = content_disposition(thumbnail_filename(name), default="thumbnail.webp")
        return Response(data, media_type="image/webp", headers=headers)

    @app.post("/api/images/{image_id}/4k")
    async def make_image_4k(image_id: str, request: Request) -> Any:
        """Make the 4K copy of a result image (DESIGN.md §27): `201` with the updated run when this call made it, `200`
        when it was there already. It is CPU work in this process, off the event loop; the GPU worker is not involved."""
        try:
            run, made = await jobs_of(request).make_4k(image_id)
        except (StorageError, ImageNotFound):
            return _error(404, "Image not found.", "not_found")
        except NotEligible as exc:
            return _error(422, str(exc), "not_4k_eligible")
        except UnidentifiedImageError:
            return _error(422, "The picture file could not be read.", "unreadable")
        except OSError as exc:
            return _error(507, f"The 4K picture could not be saved: {exc.strerror or exc}", "storage_full")
        return JSONResponse(status_code=201 if made else 200, content=run)

    @app.post("/api/images/{image_id}/enlarge")
    async def enlarge_image(image_id: str, request: Request) -> Any:
        """Enlarge a picture to the 4K frame with the upscaler model (DESIGN.md §28): `201` with the updated run when this call made
        the copy, `200` when it was there already. The work is done by a short-lived process (the image worker is not involved),
        so the request stays open for as long as it takes."""
        try:
            run, made = await jobs_of(request).enlarge(image_id)
        except (StorageError, ImageNotFound, FileNotFoundError):
            return _error(404, "Image not found.", "not_found")
        except NotEligible as exc:
            return _error(422, str(exc), "not_enlarge_eligible")
        except UnidentifiedImageError:
            return _error(422, "The picture file could not be read.", "unreadable")
        except UpscalerError as exc:
            content: dict[str, Any] = {"detail": exc.detail, "code": exc.code}
            if exc.hint:
                content["hint"] = exc.hint
            return JSONResponse(status_code=exc.status, content=content)
        except OSError as exc:
            return _error(507, f"The enlarged picture could not be saved: {exc.strerror or exc}", "storage_full")
        return JSONResponse(status_code=201 if made else 200, content=run)

    @app.get("/api/images/{image_id}/4k")
    async def get_image_4k(image_id: str, request: Request, download: bool = False, method: str = Query("", max_length=16)) -> Any:
        """The 4K copy of a picture the studio holds (a result, or an edit's source), once it has been made: the Enlarge copy if
        there is one, else the Make 4K one; `?download=1` names it like the picture, with the copy's own size. `method` is only
        part of the address the payload gives (an Enlarge copy replaces a Make 4K copy, and the address is cached as immutable);
        the answer does not depend on it."""
        db: Database = request.app.state.db
        storage: Storage = request.app.state.storage
        image = db.get_image(image_id)
        if image is None or image["run_id"] is None:
            return _error(404, "Image not found.", "not_found")
        data = None
        for find in (storage.enlarged_path, storage.four_k_path):
            try:
                data = await asyncio.to_thread(find(image["path"]).read_bytes)
                break
            except (FileNotFoundError, IsADirectoryError):
                continue
            except StorageError:
                break
        if data is None:
            return _error(404, "No 4K copy of this image has been made yet.", "not_found")
        headers = {"Cache-Control": IMMUTABLE}
        run = db.get_run(image["run_id"]) if download else None
        if run is not None:
            width, height = _png_size(data) or (image["width"], image["height"])
            if image["kind"] == "input":
                name = source_filename(prompt=run["prompt"], position=db.input_position(image_id) or 0, width=width, height=height,
                                       created_at=parse_ts(run["created_at"]))
            else:
                name = download_filename(
                    mode=run["mode"], prompt=run["prompt"], width=width, height=height,
                    seed=image["seed"], created_at=parse_ts(run["created_at"]), transparent=bool(run["transparent"]),
                )
            headers["Content-Disposition"] = content_disposition(name)
        return Response(data, media_type="image/png", headers=headers)

    @app.post("/api/upscale")
    async def upscale_picture(request: Request, name: str = Query("", max_length=255)) -> Any:
        """Make the 4K copy of a picture from the person's computer (DESIGN.md §27.9). The file is the request body, as for an
        upload; the answer is the PNG, to be saved by the page. Nothing is stored: there is no run, no file and no history."""
        data = await read_limited_body(request)
        if isinstance(data, JSONResponse):
            return data
        async with request.app.state.upscale_slots:  # at most two at a time: each is a few seconds of CPU and tens of MB
            try:
                png, plan = await asyncio.to_thread(_upscale_bytes, data)
            except inputs_mod.UploadError as exc:
                return _error(exc.status, exc.message, {413: "too_large", 415: "unsupported_type"}.get(exc.status, "unreadable"))
            except NotEligible as exc:
                return _error(422, str(exc), "not_4k_eligible")
            except OSError as exc:
                return _error(507, f"The picture could not be made: {exc.strerror or exc}", "storage_full")
        filename = upscale_filename(name=name, width=plan.out_width, height=plan.out_height, created_at=parse_ts(utcnow()))
        return Response(png, media_type="image/png", headers={
            "Content-Disposition": content_disposition(filename), "Cache-Control": "no-store",
            "X-Output-Size": f"{plan.out_width}x{plan.out_height}",
        })

    @app.get("/api/audio/{track_id}")
    async def get_audio(track_id: str, request: Request, download: bool = False) -> Any:
        """A track's WAV. FileResponse answers range requests, which an audio player needs to seek (DESIGN.md §26.3)."""
        try:
            check_id(track_id)
        except StorageError:
            return _error(404, "Track not found.", "not_found")
        db: Database = request.app.state.db
        storage: Storage = request.app.state.storage
        track = db.get_track(track_id)
        if track is None:
            return _error(404, "Track not found.", "not_found")
        try:
            path = storage.abs(track["path"])
        except StorageError:
            return _error(404, "Track not found.", "not_found")
        if not path.is_file():
            return _error(404, "Track not found.", "not_found")
        headers = {"Cache-Control": IMMUTABLE}
        if download:
            run = db.get_run(track["run_id"])
            if run is not None:
                try:
                    label = (json.loads(run["options_json"]).get("fields") or {}).get("genre") or run["prompt"]
                except ValueError:
                    label = run["prompt"]
                name = music_filename(label=label, seconds=track["seconds"], seed=track["seed"], created_at=parse_ts(run["created_at"]))
                headers["Content-Disposition"] = content_disposition(name, default="music.wav")
        return FileResponse(path, media_type="audio/wav", headers=headers)

    # ------------------------------------------------------------------ Vector (DESIGN.md §31.12.7)
    @app.post("/api/vector/recipe")
    async def vector_recipe(body: RecipeRequest) -> Any:
        """The words the studio will send for an idea, for the form to show and let the user edit. Pure text: no model, no database, no run."""
        try:
            recipe = resolve_recipe(body, settings)
        except RunRequestError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.detail()})
        return {"positive": recipe.positive, "negative": recipe.negative}

    async def _vector_file(request: Request, vector_id: str, column: str) -> tuple[Any, Any, Optional[bytes]]:
        """(vector row, its run, the file's contents) for one of a vector's three files (`column` is `svg_path`, `zip_path` or `png_path`), or
        (None, None, None) when there is no such vector or its file is gone. Read whole and up front, as `_image_file` does, so that a run deleted
        while it is being fetched gives a clean 404 and not a response cut short after its headers."""
        db: Database = request.app.state.db
        storage: Storage = request.app.state.storage
        vector = db.get_vector(vector_id)  # an id that is not even shaped like one matches no row, and is a 404 like any other
        if vector is None:
            return None, None, None
        run = db.get_run(vector["run_id"])
        try:
            data = await asyncio.to_thread(storage.abs(vector[column]).read_bytes)
        except (StorageError, FileNotFoundError, IsADirectoryError):
            return None, None, None
        return vector, run, data

    def vector_download_name(vector: Any, run: Any, suffix: str) -> str:
        """The name a vector is saved under (DESIGN.md §31.12.7), from the run's idea and the vector's own size, colours and seed."""
        return vector_filename(prompt=run["prompt"], width_mm=vector["width_mm"], height_mm=vector["height_mm"], colours=vector["colours"],
                               seed=vector["seed"], created_at=parse_ts(run["created_at"]), suffix=suffix)

    @app.get("/api/vectors/{vector_id}/svg")
    async def get_vector_svg(vector_id: str, request: Request, download: bool = False) -> Any:
        """The combined SVG. Served so that it can run nothing (`SVG_CSP`, `nosniff`); the page shows it only in an `<img>`, where scripts never run."""
        vector, run, data = await _vector_file(request, vector_id, "svg_path")
        if vector is None:
            return _error(404, "Vector not found.", "not_found")
        headers = {"Cache-Control": IMMUTABLE, "Content-Security-Policy": SVG_CSP, "X-Content-Type-Options": "nosniff"}
        if download and run is not None:
            headers["Content-Disposition"] = content_disposition(vector_download_name(vector, run, ".svg"), default="vector.svg")
        return Response(data, media_type="image/svg+xml", headers=headers)

    @app.get("/api/vectors/{vector_id}/layers.zip")
    async def get_vector_layers(vector_id: str, request: Request) -> Any:
        """The ZIP of one SVG for each layer: always an attachment, which is what it is for."""
        vector, run, data = await _vector_file(request, vector_id, "zip_path")
        if vector is None:
            return _error(404, "Vector not found.", "not_found")
        headers = {"Cache-Control": IMMUTABLE}
        if run is not None:
            headers["Content-Disposition"] = content_disposition(vector_download_name(vector, run, "-layers.zip"), default="vector-layers.zip")
        return Response(data, media_type="application/zip", headers=headers)

    @app.get("/api/vectors/{vector_id}/png")
    async def get_vector_png(vector_id: str, request: Request, download: bool = False,
                             size: int = Query(PREVIEW_SIZE, ge=VECTOR_PNG_SIZE["min"], le=VECTOR_PNG_SIZE["max"], description="the long side in pixels")) -> Any:
        """A PNG drawn from the SVG at `size` pixels on its long side. The stored preview is `PREVIEW_SIZE` (1024) wide and is sent as it is; any other
        size is drawn when asked for, from the SVG's own shapes and never from the model's picture, so it is as sharp as the size allows. The drawing
        is CPU work, so it is done off the event loop, and a few at a time."""
        # the SVG is only read for a size that is drawn; the stored preview is read as it is
        vector, run, data = await _vector_file(request, vector_id, "png_path" if size == PREVIEW_SIZE else "svg_path")
        if vector is None:
            return _error(404, "Vector not found.", "not_found")
        if size != PREVIEW_SIZE:
            async with request.app.state.vector_png_slots:
                try:
                    data = await asyncio.to_thread(vector_mod.render_png, data.decode("utf-8"), size)
                except vector_mod.VectorError as exc:
                    content: dict[str, Any] = {"detail": exc.message, "code": "png_failed"}
                    if exc.hint:
                        content["hint"] = exc.hint
                    return JSONResponse(status_code=500, content=content)
        headers = {"Cache-Control": IMMUTABLE}
        if download and run is not None:
            headers["Content-Disposition"] = content_disposition(vector_download_name(vector, run, ".png"), default="vector.png")
        return Response(data, media_type="image/png", headers=headers)

    @app.get("/api/vectors/{vector_id}/drawing")
    async def get_vector_drawing(vector_id: str, request: Request, download: bool = False) -> Any:
        """The picture the image model drew for this version, which the clean-up traced (DESIGN.md §35.2, §35.4): the stored `<idx>-source.png`, shown
        beside the vector in the viewer. It has no column of its own, so its file is found from the run and the version's place; one that is gone
        (the disk was cleaned by hand) is a 404 that says so, and nothing else about the version changes."""
        db: Database = request.app.state.db
        storage: Storage = request.app.state.storage
        vector = db.get_vector(vector_id)
        if vector is None:
            return _error(404, "Vector not found.", "not_found")
        run = db.get_run(vector["run_id"])
        try:
            data = await asyncio.to_thread(storage.drawing_path(vector["run_id"], vector["idx"]).read_bytes)
        except (StorageError, FileNotFoundError, IsADirectoryError):
            return _error(404, "The drawing is no longer on the disk.", "drawing_missing")
        headers = {"Cache-Control": IMMUTABLE, "X-Content-Type-Options": "nosniff"}
        if download and run is not None:
            headers["Content-Disposition"] = content_disposition(vector_download_name(vector, run, "-drawing.png"), default="vector-drawing.png")
        return Response(data, media_type="image/png", headers=headers)

    @app.get("/api/vectors/{vector_id}/layers/{number}.svg")
    async def get_vector_layer(vector_id: str, number: int, request: Request) -> Any:
        """One layer's own SVG, read out of the version's ZIP (DESIGN.md §35.4): the stack view shows the sheets one at a time. `number` counts from 1,
        as the layers do; one outside 1 to the number of layers is a 404. Served with the same script-forbidding headers as the combined SVG."""
        vector, _run, data = await _vector_file(request, vector_id, "zip_path")
        if vector is None:
            return _error(404, "Vector not found.", "not_found")
        layers = json.loads(vector["check_json"]).get("layers", [])
        if not 1 <= number <= len(layers):
            return _error(404, "That layer does not exist.", "not_found")
        try:
            # the entry is named from the layer's own id in the check (`layer-2-red.svg`), never from anything the request sent but the number
            svg = await asyncio.to_thread(lambda: zipfile.ZipFile(io.BytesIO(data)).read(f"{layers[number - 1]['id']}.svg"))
        except (KeyError, zipfile.BadZipFile):
            return _error(404, "That layer is not in the file.", "not_found")
        return Response(svg, media_type="image/svg+xml",
                        headers={"Cache-Control": IMMUTABLE, "Content-Security-Policy": SVG_CSP, "X-Content-Type-Options": "nosniff"})

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        bus: EventBus = request.app.state.bus
        jobs = jobs_of(request)
        sub = bus.subscribe()

        async def stream() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            try:
                yield "retry: 3000\n\n"
                # The snapshot is read after subscribing, so every later change arrives as an event
                # after it: the client can take it as the truth and apply what follows on top.
                yield format_sse("hello", {"status": jobs.status(), "runs": runs_page(request, HELLO_RUNS)})
                last_ping = loop.time()
                while True:
                    if server_stopping(request.app):
                        # End the stream ourselves so shutdown doesn't have to cancel it.
                        yield format_sse("shutdown", {"detail": "The server is shutting down."})
                        return
                    try:
                        event, data = await asyncio.wait_for(sub.queue.get(), POLL_SECONDS)
                    except asyncio.TimeoutError:
                        if loop.time() - last_ping >= HEARTBEAT_SECONDS:
                            last_ping = loop.time()
                            yield ": ping\n\n"
                        continue
                    if event is OVERFLOW:
                        yield format_sse("overflow", {"detail": "Too many events at once; reconnect and refetch."})
                        return
                    yield format_sse(event, data)
            finally:
                bus.unsubscribe(sub)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    add_builder_routes(app)  # /api/builder/... (registered before the page below, which takes every other path)
    add_builder_chat_routes(app)
    add_builder_prompt_routes(app)
    add_speech_routes(app, read_limited_body)  # /api/speech/... and /api/readings/...

    static_dir = settings.static_dir or DEFAULT_STATIC_DIR
    if (static_dir / "index.html").is_file():
        # Registered last, so every /api route above takes precedence.
        app.mount("/", UiFiles(directory=static_dir, html=True), name="ui")
        log.info("serving the web UI from %s", static_dir)
    else:
        if settings.static_dir is not None:
            log.warning("STUDIO_STATIC_DIR=%s has no index.html; serving a placeholder page", static_dir)

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def index() -> str:
            return PLACEHOLDER_HTML

    return app
