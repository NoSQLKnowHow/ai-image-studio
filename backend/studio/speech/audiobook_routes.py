"""The routes of the audiobook file (DESIGN.md §44.4, §44.6): what can be made and where a build stands, making it, and taking it.

The file is made by `audiobook.py`; these routes only ask for it and hand it over. Making it is a build that can take minutes for a long book, so it is not part of a request:
`POST` starts it and answers at once, the page asks `GET` how far it has got, and the finished file is taken from `GET …/audiobook.m4b` with range requests, like any
download. Nothing in this file names an engine.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import naming
from ..serialize import parse_ts
from ..storage import StorageError, check_id
from .refused import ReadingRefused
from .routes import error, refusal


def add_routes(app: FastAPI) -> None:
    def jobs_of(request: Request):
        return request.app.state.jobs

    def reading_row(request: Request, run_id: str):
        """The reading's row, or None for an id that is not one (the id is checked first, so nothing odd reaches a path)."""
        try:
            check_id(run_id)
        except StorageError:
            return None
        return jobs_of(request).speech.store.reading(run_id)

    not_found = lambda: error(404, "Reading not found.", "not_found")  # noqa: E731

    @app.get("/api/speech/{run_id}/audiobook")
    async def audiobook_status(run_id: str, request: Request) -> Any:
        """Where the audiobook of a reading stands: whether this server can make one (and if not, why, in words), how many parts it would hold, and whether a file is ready,
        being made, or failed."""
        row = reading_row(request, run_id)
        return not_found() if row is None else await asyncio.to_thread(jobs_of(request).speech.audiobooks.status, row)

    @app.post("/api/speech/{run_id}/audiobook")
    async def make_audiobook(run_id: str, request: Request) -> Any:
        """Start making the audiobook, unless a file for the parts finished now is there or being made. Answers at once with the status (202 while it is being made)."""
        row = reading_row(request, run_id)
        if row is None:
            return not_found()
        try:
            status = await asyncio.to_thread(jobs_of(request).speech.audiobooks.start, row)
        except ReadingRefused as exc:
            return refusal(exc)
        return JSONResponse(status_code=202 if status["state"] in ("waiting", "building") else 200, content=status)

    @app.get("/api/speech/{run_id}/audiobook.m4b")
    async def take_audiobook(run_id: str, request: Request) -> Any:
        """The finished file, as a download, with range requests. 409 when there is no file for the parts finished now (it was never made, or more parts have been finished
        since, or it was kept for its day and cleaned away): the page makes it again."""
        row = reading_row(request, run_id)
        if row is None:
            return not_found()
        path = await asyncio.to_thread(jobs_of(request).speech.audiobooks.file_for, row)
        if path is None:
            return error(409, "There is no audiobook file for this reading as it stands now. Make it first.", "not_built")
        options = json.loads(row["options_json"])
        name = naming.speech_filename(title=options.get("title") or row["prompt"], part=0, voice="", created_at=parse_ts(row["created_at"]), ext=".m4b")
        return FileResponse(path, media_type="audio/mp4", headers={"Content-Disposition": naming.content_disposition(name, default="reading.m4b"),
                                                                  "Cache-Control": "private, max-age=3600"})
