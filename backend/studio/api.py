"""HTTP API (DESIGN.md §7). Create with `create_app(settings)`."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from . import __version__
from . import presets as P
from .config import Settings
from .db import Database
from .events import OVERFLOW, EventBus, format_sse
from . import inputs as inputs_mod
from .jobs import InputStorageError, JobManager, ModelRefused, QueueFull, RunConflict, RunNotFound
from .naming import content_disposition, download_filename, thumbnail_filename
from .runspec import RunCreate, RunRequestError, resolve_run
from .security import SecurityMiddleware
from .serialize import parse_ts
from .storage import Storage, StorageError, check_id

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
<title>AI Image Studio</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#222;background:#fafafa}
@media (prefers-color-scheme:dark){body{color:#eee;background:#16161a}}code{font-size:.9em}</style></head>
<body><h1>AI Image Studio</h1>
<p>The backend is running, but the web interface has not been built. Build it with <code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code> (the container image does this for you).</p>
<p>API: <code>/api/health</code>, <code>/api/status</code>, <code>/api/capabilities</code>, <code>/api/runs</code>,
<code>/api/events</code>.</p></body></html>"""


class RunPatch(BaseModel):
    """The one thing about a run that can be changed after it is made: whether it is kept."""

    model_config = ConfigDict(extra="forbid", strict=True)
    pinned: bool


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
        log.info("AI Image Studio %s ready (pipeline=%s, data=%s)", __version__, settings.pipeline, storage.root)
        try:
            yield
        finally:
            await jobs.stop()
            db.close()

    app = FastAPI(
        title="AI Image Studio",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(SecurityMiddleware, allowed_hosts=settings.allowed_hosts)

    def jobs_of(request: Request) -> JobManager:
        return request.app.state.jobs

    def runs_page(request: Request, limit: int, before: Optional[str] = None) -> dict[str, Any]:
        """One page of runs, newest first. Raises KeyError for an unknown `before` cursor."""
        rows, has_more = request.app.state.db.list_runs(limit, before)
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
        width, height = P.DEFAULT_SIZE
        supports = jobs.supports()
        return {
            "pipeline": settings.pipeline,
            "model": jobs.model_id,
            "modes": ["generate", *(["edit"] if supports.get("edit") else [])],  # Edit only where the pipeline can
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
                "resolutions": list(P.RESOLUTIONS),
                "upload_mb": settings.max_upload_mb,
                "steps": {"min": P.STEPS_MIN, "max": P.STEPS_MAX},
                "num_images": {"min": 1, "max": settings.max_images_per_run},
                "seed": {"min": 0, "max": P.SEED_MAX},
                "cfg_scale": {"min": P.CFG_MIN, "max": P.CFG_MAX},
                "size": {"min": P.SIZE_MIN, "max": P.SIZE_MAX, "multiple": P.SIZE_MULTIPLE, "max_pixels": P.MAX_PIXELS},
            },
            "queue_cap": settings.queue_cap,
            "device": jobs.worker_status().get("device"),
        }

    @app.post("/api/model/load")
    async def load_model(request: Request) -> Any:
        """Start loading the model now, without a run (DESIGN.md §25.2)."""
        jobs = jobs_of(request)
        try:
            started = await jobs.load_model()
        except ModelRefused as exc:
            return _model_refused(exc)
        return JSONResponse(status_code=202 if started else 200, content=jobs.status())

    @app.post("/api/model/unload")
    async def unload_model(request: Request) -> Any:
        """Unload the model now, unless a run is using it (DESIGN.md §25.2)."""
        jobs = jobs_of(request)
        try:
            await jobs.unload_model()
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

    @app.post("/api/uploads", status_code=201)
    async def upload_image(request: Request) -> Any:
        """Stage one image for an edit. The file itself is the request body (not multipart: one image per call, the
        size can be capped while it streams in, and `curl --data-binary @photo.jpg` is all it takes). The type is
        decided by decoding it, never by the Content-Type header."""
        limit = settings.max_upload_mb * 1024 * 1024
        too_big = _error(413, f"The file is larger than {settings.max_upload_mb} MB.", "too_large")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            return too_big
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                return too_big
        try:
            return await asyncio.to_thread(inputs_mod.store_upload, request.app.state.db, request.app.state.storage, bytes(data))
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
    ) -> Any:
        try:
            return runs_page(request, limit, before)
        except KeyError:
            return _error(400, "Unknown 'before' cursor.", "bad_cursor")

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
