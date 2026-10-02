"""HTTP API (DESIGN.md §7). Create with `create_app(settings)`."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from . import __version__
from . import presets as P
from .config import Settings
from .db import Database
from .events import OVERFLOW, EventBus, format_sse
from .jobs import JobManager, QueueFull, RunConflict, RunNotFound
from .naming import content_disposition, download_filename
from .runspec import AVAILABLE_MODES, RunCreate, RunRequestError, resolve_run
from .security import SecurityMiddleware
from .serialize import parse_ts
from .storage import Storage, StorageError, check_id

log = logging.getLogger("studio.api")

HEARTBEAT_SECONDS = 15.0  # keep-alive comment on idle event streams (stops proxies closing them)
POLL_SECONDS = 1.0  # how quickly an idle event stream notices that the server is shutting down
IMMUTABLE = "private, max-age=31536000, immutable"


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
<p>The backend is running. The web interface arrives in milestone M3.</p>
<p>API: <code>/api/health</code>, <code>/api/status</code>, <code>/api/capabilities</code>, <code>/api/runs</code>,
<code>/api/events</code>.</p></body></html>"""


def _error(status: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return PLACEHOLDER_HTML

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
        return {
            "pipeline": settings.pipeline,
            "model": jobs.model_id,
            "modes": list(AVAILABLE_MODES),
            "supports": jobs.supports(),
            "aspect_ratios": {name: list(size) for name, size in P.ASPECT_RATIOS.items()},
            "defaults": {
                "mode": "generate", "aspect_ratio": P.DEFAULT_ASPECT, "width": width, "height": height,
                "steps": P.DEFAULT_STEPS, "seed": None, "num_images": 1, "transparent": False,
            },
            "limits": {
                "prompt_chars": settings.max_prompt_chars,
                "steps": {"min": P.STEPS_MIN, "max": P.STEPS_MAX},
                "num_images": {"min": 1, "max": settings.max_images_per_run},
                "seed": {"min": 0, "max": P.SEED_MAX},
                "cfg_scale": {"min": P.CFG_MIN, "max": P.CFG_MAX},
                "size": {"min": P.SIZE_MIN, "max": P.SIZE_MAX, "multiple": P.SIZE_MULTIPLE, "max_pixels": P.MAX_PIXELS},
            },
            "queue_cap": settings.queue_cap,
        }

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunCreate, request: Request) -> Any:
        try:
            resolved = resolve_run(body, settings)
        except RunRequestError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.detail()})
        try:
            return await jobs_of(request).submit(resolved)
        except QueueFull as exc:
            return _error(429, str(exc), "queue_full")

    @app.get("/api/runs")
    async def list_runs(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        before: Optional[str] = Query(None),
    ) -> Any:
        try:
            rows, has_more = request.app.state.db.list_runs(limit, before)
        except KeyError:
            return _error(400, "Unknown 'before' cursor.", "bad_cursor")
        runs = jobs_of(request).payloads(rows)
        return {"runs": runs, "next_before": runs[-1]["id"] if has_more and runs else None}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> Any:
        payload = jobs_of(request).payload(run_id)
        return payload if payload is not None else _error(404, "Run not found.", "not_found")

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

    def _image_file(request: Request, image_id: str, thumb: bool) -> Any:
        db: Database = request.app.state.db
        storage: Storage = request.app.state.storage
        image = db.get_image(image_id)
        rel = None if image is None else (image["thumb_path"] if thumb else image["path"])
        if rel is None:
            return None, None
        try:
            path = storage.abs(rel)
        except StorageError:
            return None, None
        return (image, path) if path.is_file() else (None, None)

    @app.get("/api/images/{image_id}")
    async def get_image(image_id: str, request: Request, download: bool = False) -> Any:
        image, path = _image_file(request, image_id, thumb=False)
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
        return FileResponse(path, media_type="image/png", headers=headers)

    @app.get("/api/images/{image_id}/thumb")
    async def get_thumb(image_id: str, request: Request) -> Any:
        image, path = _image_file(request, image_id, thumb=True)
        if image is None:
            return _error(404, "Thumbnail not found.", "not_found")
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": IMMUTABLE})

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        bus: EventBus = request.app.state.bus
        jobs = jobs_of(request)
        sub = bus.subscribe()

        async def stream() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            try:
                yield "retry: 3000\n\n"
                yield format_sse("hello", jobs.status())
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

    return app
