"""Helpers for the Prompt builder's chat tests: reading the server-sent events of a reply, and watching what the model is sent."""

from __future__ import annotations

import io
import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

import httpx2 as httpx
import pytest
from PIL import Image
from test_sse import CLIENT, LiveServer


def iter_events(lines) -> Iterator[tuple[str, dict]]:
    """The events of a stream, one at a time as their lines arrive (so that a test can act in the middle of a reply)."""
    name = None
    for line in lines:
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name is not None:
            yield name, json.loads(line[6:])
            name = None


def parse_events(lines) -> list[tuple[str, dict]]:
    return list(iter_events(lines))


def send(client, chat_id: str, **body: Any) -> list[tuple[str, dict]]:
    """Send a message and read the whole streamed reply."""
    with client.stream("POST", f"/api/builder/chats/{chat_id}/messages", json=body) as response:
        assert response.status_code == 200, response.read()
        return parse_events(response.iter_lines())


def names(events) -> list[str]:
    return [name for name, _ in events]


def result_of(events) -> dict:
    found = [data for name, data in events if name == "result"]
    assert len(found) == 1, names(events)
    return found[0]["message"]["content"]


def new_chat(client, **body: Any) -> str:
    response = client.post("/api/builder/chats", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def png_bytes(width: int = 64, height: int = 48, colour=(200, 30, 30), mode: str = "RGB", fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), colour).save(buffer, fmt)
    return buffer.getvalue()


class Spy:
    """Records every call the engine makes to the model, and passes it on."""

    def __init__(self, service, monkeypatch, fail_first: Optional[Callable[[], Exception]] = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.real = service.chat
        self.fail_first = fail_first
        monkeypatch.setattr(service, "chat", self.chat)

    def chat(self, model, messages, *, think, schema):
        self.calls.append({"model": model, "messages": messages, "think": think, "schema": schema})
        if self.fail_first is not None:
            error, self.fail_first = self.fail_first(), None
            raise error
        return self.real(model, messages, think=think, schema=schema)

    @property
    def system(self) -> str:
        return self.calls[-1]["messages"][0]["content"]

    @property
    def last_user(self) -> str:
        return self.calls[-1]["messages"][-1]["content"]


def wait_until(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("it did not happen in time")


def in_thread(function: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []
    thread = threading.Thread(target=lambda: out.append(function()), daemon=True)
    thread.start()
    return thread, out


@pytest.fixture
def builder(client_factory):
    return client_factory(builder_llm="fake")


class Live:
    """A real server on a real port, for what TestClient cannot do: it delivers a streamed reply only when it is over, so Stop and a closed page cannot be tried with it."""

    def __init__(self, server: LiveServer) -> None:
        self.server = server
        self.http = httpx.Client(base_url=server.url, headers=CLIENT, timeout=30)

    @property
    def jobs(self) -> dict:
        return self.server.server.config.app.state.builder_jobs

    @property
    def store(self):
        return self.server.server.config.app.state.builder_store

    def chat(self, **body: Any) -> str:
        return self.http.post("/api/builder/chats", json=body).json()["id"]

    @contextmanager
    def stream(self, chat_id: str, **body: Any) -> Iterator[Iterator[tuple[str, dict]]]:
        with httpx.stream("POST", f"{self.server.url}/api/builder/chats/{chat_id}/messages", json=body, headers=CLIENT, timeout=30) as response:
            assert response.status_code == 200, response.read()
            yield iter_events(response.iter_lines())

    def send(self, chat_id: str, **body: Any) -> list[tuple[str, dict]]:
        with self.stream(chat_id, **body) as events:
            return list(events)


@pytest.fixture
def live(tmp_path):
    from conftest import make_settings  # noqa: F401  (LiveServer builds its settings the same way)

    servers: list[LiveServer] = []

    def factory(**overrides: Any) -> Live:
        server = LiveServer(tmp_path, builder_llm="fake", **overrides).start()
        servers.append(server)
        return Live(server)

    yield factory
    for server in servers:
        if server.thread.is_alive():
            server.stop()
