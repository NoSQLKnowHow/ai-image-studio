"""The Prompt builder's routes under `/api/builder` (DESIGN.md §37.4). This file has the connection routes; the chat, the prompts and the
preferences are added to it as they are built.

Every call to Ollama blocks, so each runs in a thread. A failure is answered in the studio's usual shape (`detail`, `code`, and a `hint` where there is
one) and never contains the Ollama server's address (§37.3 rule 12).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .ollama import OllamaError
from .service import NOT_SET_UP, BuilderService

# the HTTP status for each kind of failure: the server in between did not answer (502, 504), or answered with a conflict the person can fix (409)
STATUS_FOR = {"unreachable": 502, "timeout": 504, "too_long": 504, "model_missing": 409, "out_of_memory": 409, "refused": 502, "bad_answer": 502, "stopped": 409}


def failure(exc: OllamaError) -> JSONResponse:
    content: dict[str, Any] = {"detail": exc.message, "code": f"ollama_{exc.kind}"}
    if exc.hint:
        content["hint"] = exc.hint
    return JSONResponse(status_code=STATUS_FOR.get(exc.kind, 502), content=content)


def not_set_up() -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": NOT_SET_UP, "code": "builder_not_configured"})


def add_routes(app: FastAPI) -> None:
    def service_of(request: Request) -> BuilderService:
        return request.app.state.builder

    @app.get("/api/builder/status")
    async def builder_status(request: Request) -> Any:
        """Whether the builder is set up, can reach Ollama and has the model (cached for a few seconds), and whether it can look at pictures."""
        return await asyncio.to_thread(service_of(request).status)

    @app.post("/api/builder/test")
    async def builder_test(request: Request) -> Any:
        """Test connection: a fresh look at the Ollama server, shown in full."""
        service = service_of(request)
        if not service.configured:
            return not_set_up()
        return await asyncio.to_thread(service.test)

    @app.post("/api/builder/unload")
    async def builder_unload(request: Request) -> Any:
        """Unload from memory: ask Ollama to take its model out of memory. Only a person's press calls this."""
        service = service_of(request)
        if not service.configured:
            return not_set_up()
        try:
            return await asyncio.to_thread(service.unload)
        except OllamaError as exc:
            return failure(exc)
