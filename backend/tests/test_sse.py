"""Server-sent events against a real uvicorn server (TestClient cannot stream endless responses)."""

from __future__ import annotations

import json
import socket
import threading
import time

import httpx2 as httpx
import pytest
import uvicorn

from conftest import make_settings
from studio.api import create_app


@pytest.fixture
def live_server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(make_settings(tmp_path, fake_step_delay_ms=5)),
                                           host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=20)


def read_events(lines):
    event = None
    for line in lines:
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            yield event, json.loads(line[6:])
            event = None


def test_event_stream_follows_a_run_to_completion(live_server):
    seen: list[str] = []
    with httpx.Client(base_url=live_server, timeout=30) as http:
        with http.stream("GET", "/api/events") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")
            events = read_events(stream.iter_lines())
            name, hello = next(events)
            assert name == "hello" and hello["queue"]["cap"] == 10
            created = http.post("/api/runs", headers={"X-Studio-Client": "1"},
                                json={"prompt": "stream me", "options": {"width": 256, "height": 256, "steps": 4}}).json()
            for name, data in events:
                seen.append(name)
                if name == "run.updated" and data["id"] == created["id"] and data["status"] == "done":
                    assert len(data["images"]) == 1
                    break
    for expected in ("run.created", "queue.updated", "worker.state", "run.progress", "run.updated"):
        assert expected in seen, (expected, seen)
