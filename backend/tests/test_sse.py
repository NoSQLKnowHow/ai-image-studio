"""Server-sent events and shutdown, against a real uvicorn server built exactly like production
(`studio.__main__.build_server`). TestClient cannot stream endless responses."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time

import httpx2 as httpx
import pytest

import studio.api
from conftest import make_settings
from studio.__main__ import GRACEFUL_SHUTDOWN_SECONDS, build_server

CLIENT = {"X-Studio-Client": "1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, tmp_path, **overrides):
        settings = make_settings(tmp_path, host="127.0.0.1", port=free_port(), **overrides)
        self.server = build_server(settings)
        self.url = f"http://127.0.0.1:{settings.port}"
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "LiveServer":
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.05)
        return self

    def stop(self, timeout: float = 30.0) -> float:
        started = time.monotonic()
        self.server.should_exit = True
        self.thread.join(timeout)
        return time.monotonic() - started


@pytest.fixture
def live(tmp_path):
    servers: list[LiveServer] = []

    def factory(**overrides) -> LiveServer:
        server = LiveServer(tmp_path, **overrides).start()
        servers.append(server)
        return server

    yield factory
    for server in servers:
        if server.thread.is_alive():
            server.stop()


def read_events(lines):
    event = None
    for line in lines:
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            yield event, json.loads(line[6:])
            event = None


def test_event_stream_follows_a_run_to_completion(live):
    server = live(fake_step_delay_ms=5)
    seen: list[str] = []
    with httpx.Client(base_url=server.url, timeout=30) as http:
        with http.stream("GET", "/api/events") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")
            events = read_events(stream.iter_lines())
            name, hello = next(events)
            assert name == "hello" and hello["queue"]["cap"] == 10
            created = http.post("/api/runs", headers=CLIENT,
                                json={"prompt": "stream me", "options": {"width": 256, "height": 256, "steps": 4}}).json()
            for name, data in events:
                seen.append(name)
                if name == "run.updated" and data["id"] == created["id"] and data["status"] == "done":
                    assert len(data["images"]) == 1
                    break
    for expected in ("run.created", "queue.updated", "worker.state", "run.progress", "run.updated"):
        assert expected in seen, (expected, seen)


def shutdown_during_a_run(server: "LiveServer") -> tuple[list[str], float, int, str]:
    """Start a slow run, open an event stream, stop the server. Returns (event names received,
    seconds the shutdown took, worker pid, run id). A stream that never closes fails, not hangs."""
    timeout = httpx.Timeout(30, read=GRACEFUL_SHUTDOWN_SECONDS + 15)
    with httpx.Client(base_url=server.url, timeout=timeout) as http:
        run = http.post("/api/runs", headers=CLIENT,
                        json={"prompt": "slow", "options": {"width": 256, "height": 256, "steps": 50}}).json()
        deadline = time.monotonic() + 15
        while True:
            status = http.get("/api/status").json()
            if status["queue"]["running"] == run["id"] and status["worker"]["pid"]:
                break
            assert time.monotonic() < deadline, status
            time.sleep(0.05)

        names: list[str] = []
        elapsed: dict[str, float] = {}
        with http.stream("GET", "/api/events") as stream:
            lines = stream.iter_lines()
            assert next(lines).startswith("retry:")
            stopper = threading.Thread(target=lambda: elapsed.setdefault("s", server.stop()))
            stopper.start()
            deadline = time.monotonic() + GRACEFUL_SHUTDOWN_SECONDS + 10
            try:
                for line in lines:  # must end: the server closes the stream when it shuts down
                    if line.startswith("event: "):
                        names.append(line[7:])
                    assert time.monotonic() < deadline, "shutdown did not close the open event stream"
            except httpx.RemoteProtocolError:
                pass  # connection cut mid-stream (the safety-net path)
            stopper.join(30)
    assert not server.thread.is_alive()
    return names, elapsed["s"], status["worker"]["pid"], run["id"]


def assert_worker_gone_and_run_interrupted(tmp_path, worker_pid: int, run_id: str) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)  # the worker is gone, so its GPU memory would be released
    with sqlite3.connect(tmp_path / "data" / "studio.sqlite") as conn:
        state, message = conn.execute("SELECT status, error_message FROM runs WHERE id=?", (run_id,)).fetchone()
    assert state == "failed" and "server was stopped" in message


def test_shutdown_closes_open_streams_cleanly_and_stops_a_busy_worker(live, tmp_path, monkeypatch):
    """Regression: an open browser tab (event stream) used to block shutdown forever."""
    monkeypatch.setattr(studio.api, "HEARTBEAT_SECONDS", 0.5)  # frequent lines, so the deadline is checked
    names, seconds, worker_pid, run_id = shutdown_during_a_run(live(fake_step_delay_ms=200))
    assert names and names[-1] == "shutdown", names  # the stream said goodbye instead of being cut
    assert seconds < 3.5, seconds  # well before the safety-net deadline, and the busy worker didn't stall it
    assert_worker_gone_and_run_interrupted(tmp_path, worker_pid, run_id)


def test_safety_net_closes_a_stream_that_ignores_shutdown(live, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.api, "HEARTBEAT_SECONDS", 0.5)
    monkeypatch.setattr(studio.api, "server_stopping", lambda app: False)  # a stream that never notices
    names, seconds, worker_pid, run_id = shutdown_during_a_run(live(fake_step_delay_ms=200))
    assert "shutdown" not in names
    assert GRACEFUL_SHUTDOWN_SECONDS - 0.5 <= seconds < GRACEFUL_SHUTDOWN_SECONDS + 4, seconds
    assert_worker_gone_and_run_interrupted(tmp_path, worker_pid, run_id)
