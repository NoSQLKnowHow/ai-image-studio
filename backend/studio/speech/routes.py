"""The Speech tab's routes (DESIGN.md §38.9). This file has the preparation of a source; readings, audio and voices are added to it as they are built.

Every call here blocks (a fetch, a child process), so each runs in a thread; at most two at a time, because each is a process that can use a processor for
a minute.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import prepare_service
from .text.errors import PrepareError

PREPARE_AT_ONCE = 2


class PrepareBody(BaseModel):
    """Pasted text or a web address to prepare. Which one is `source`; the other field is ignored."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(pattern="^(text|url)$")
    text: Optional[str] = None
    url: Optional[str] = None
    clean: bool = True  # switch the cleaning rules off to read the raw text
    read_extras: bool = False  # read code blocks and tables as well


def failure(exc: PrepareError) -> JSONResponse:
    """A refusal in the studio's usual shape: `detail`, `code` and, where there is one, a `hint`."""
    content: dict[str, Any] = {"detail": exc.message, "code": exc.code}
    if exc.hint:
        content["hint"] = exc.hint
    return JSONResponse(status_code=exc.status, content=content)


def add_routes(app: FastAPI, read_limited_body) -> None:
    """Register the routes. `read_limited_body(request, megabytes)` is the API's helper that reads a request body up to a size limit."""
    slots = asyncio.Semaphore(PREPARE_AT_ONCE)

    @app.post("/api/speech/prepare")
    async def prepare(body: PrepareBody, request: Request) -> Any:
        """Pasted text or a web address, prepared for review (nothing is stored). The answer is the parts and paragraphs the page shows."""
        settings = request.app.state.settings
        try:
            async with slots:
                if body.source == "text":
                    if body.text is None or len(body.text) > settings.speech_max_chars * 2:
                        raise PrepareError("too_large" if body.text else "empty", "Paste the text to read (up to a million words or so)." if not body.text
                                           else f"That is more than {settings.speech_max_chars * 2:,} characters; make two readings.")
                    return await asyncio.to_thread(prepare_service.prepare_text, settings, body.text, clean=body.clean, read_extras=body.read_extras)
                if not body.url:
                    raise PrepareError("bad_address", "Give the web address of the page to read.")
                return await asyncio.to_thread(prepare_service.prepare_url, settings, body.url, clean=body.clean, read_extras=body.read_extras)
        except PrepareError as exc:
            return failure(exc)

    @app.post("/api/speech/prepare/file")
    async def prepare_file(request: Request, name: str, clean: bool = True, read_extras: bool = False) -> Any:
        """A file, prepared for review. The file is the request body (as for pictures, §21.6); its name, in the query, tells what kind it is."""
        settings = request.app.state.settings
        data = await read_limited_body(request, settings.speech_max_upload_mb)
        if isinstance(data, JSONResponse):
            return data
        try:
            async with slots:
                return await asyncio.to_thread(prepare_service.prepare_file, settings, name, data, clean=clean, read_extras=read_extras)
        except PrepareError as exc:
            return failure(exc)
