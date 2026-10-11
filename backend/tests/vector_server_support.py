"""What the server's Vector tests share (DESIGN.md §31.12): a client whose clean-up is the fake job, requests made like the page's, and a few readers.

The fixtures here are imported by name into each test module (pytest finds a fixture in the module that imports it). None of them needs the engine or its
libraries: the image drawing is made by the fake pipeline, which draws a flat picture for a vector run, and the clean-up is `fake_vector_job.py`.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest
from conftest import wait_for
from fastapi.testclient import TestClient

HERE = Path(__file__).parent
FAKE_JOB = HERE / "fake_vector_job.py"
CONTRACT = json.loads((HERE / "vector_contract.json").read_text(encoding="utf-8"))

# the 32 hex digits an id is made of, for a run or a vector that does not exist
NO_SUCH = "f" * 32


def fake_job_command() -> tuple[str, ...]:
    """The clean-up command of the tests: this interpreter running the fake job (`Settings.vector_job_command`, DESIGN.md §31.12.4)."""
    return (sys.executable, str(FAKE_JOB))


@pytest.fixture
def libraries_import(monkeypatch):
    """Say that the Vector libraries import here (they do not in the test environment, and the server's tests must not need them)."""
    monkeypatch.setattr("studio.vector.available", lambda: (True, None, None))


@pytest.fixture
def vclient_factory(client_factory: Callable[..., TestClient], libraries_import, monkeypatch, tmp_path) -> Callable[..., TestClient]:
    """Start a client whose Vector clean-up is the fake job (extra keyword arguments override Settings fields, as `client_factory` does). `client.job_log`
    is the file the job writes one line to for each call (what it was given)."""
    log = tmp_path / "job.log"
    monkeypatch.setenv("FAKE_VECTOR_LOG", str(log))

    def factory(**overrides: Any) -> TestClient:
        client = client_factory(**{"vector_job_command": fake_job_command(), **overrides})
        client.job_log = log  # type: ignore[attr-defined]
        return client

    return factory


@pytest.fixture
def vclient(vclient_factory: Callable[..., TestClient]) -> TestClient:
    return vclient_factory()


def subscribe(client: TestClient):
    """Subscribe to the server's event bus, as an open page does: what is published from now on is queued for `drain`."""
    return client.portal.call(lambda: client.app.state.bus.subscribe())


def drain(client: TestClient, sub) -> list[tuple[str, dict]]:
    """Everything the event bus has queued for a subscriber so far, as (event name, data)."""

    async def take():
        items = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait())
        return items

    return client.portal.call(take)


def job_calls(client: TestClient) -> list[dict[str, Any]]:
    """The calls the fake job has had so far, in order (empty if it has not been run)."""
    log = client.job_log  # type: ignore[attr-defined]
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def process_is_gone(pid: int, timeout: float = 5.0) -> bool:
    """Whether a process has ended (and been collected), waiting a moment for it: what shows that a clean-up that was cancelled, timed out or
    interrupted by a shutdown is not still running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def wait_for_job_calls(client: TestClient, count: int, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Wait until the fake job has logged this many calls (it logs as it starts, so a test that acts on a running job can be sure it is running)."""
    deadline = time.monotonic() + timeout
    while True:
        calls = job_calls(client)
        if len(calls) >= count:
            return calls
        assert time.monotonic() < deadline, f"the job was called {len(calls)} time(s), not {count}"
        time.sleep(0.02)


def vector_body(prompt: str = "a fox head", **options: Any) -> dict[str, Any]:
    """A `POST /api/runs` body for a Vector run, as the contract's `create_request` has it, with few steps so that the fake model is quick. Options
    are the contract's own names; passing None for one sends it as null."""
    return {"mode": "vector", "prompt": prompt, "options": {"steps": 3, **options}}


def create_vector(client: TestClient, prompt: str = "a fox head", expect: int = 201, **options: Any) -> dict[str, Any]:
    response = client.post("/api/runs", json=vector_body(prompt, **options))
    assert response.status_code == expect, response.text
    return response.json()


def make_vector(client: TestClient, prompt: str = "a fox head", **options: Any) -> dict[str, Any]:
    """Create a Vector run and wait for it to finish; the finished run."""
    return wait_for(client, create_vector(client, prompt, **options)["id"])


def problems(response) -> dict[str, str]:
    """A 422's field errors as {"options.colours": "message"} (the last message for a field wins), without the `body` every location starts with."""
    assert response.status_code == 422, response.text
    return {".".join(str(p) for p in item["loc"] if p != "body"): item["msg"] for item in response.json()["detail"]}


def wait_for_capability(client: TestClient, mode: str, timeout: float = 15.0) -> dict[str, Any]:
    """Wait until `GET /api/capabilities` lists a mode (the start-up check of the image pipeline runs in the background), and return it."""
    deadline = time.monotonic() + timeout
    while True:
        caps = client.get("/api/capabilities").json()
        if mode in caps["modes"]:
            return caps
        assert time.monotonic() < deadline, caps
        time.sleep(0.05)


def stored_files(client: TestClient, run_id: str) -> list[str]:
    """The names of the files in a vector run's folder, sorted (empty if it has none)."""
    folder = client.app.state.storage.vector_dir(run_id)
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def walk_types(value: Any, where: str = "") -> dict[str, str]:
    """Every path in a JSON value with the kind of thing at it, for comparing the shape of two values: dict keys are paths (`a.b`), a list is `[]` and
    is looked at through its first element, and numbers are one kind (JSON has one number type: 5 and 5.0 are the same number)."""
    if isinstance(value, dict):
        found = {where or ".": "object"}
        for key, inner in value.items():
            found.update(walk_types(inner, f"{where}.{key}" if where else key))
        return found
    if isinstance(value, list):
        found = {where: "array"}
        if value:
            found.update(walk_types(value[0], f"{where}[]"))
        return found
    if isinstance(value, bool):
        return {where: "boolean"}
    if isinstance(value, (int, float)):
        return {where: "number"}
    if value is None:
        return {where: "null"}
    return {where: "string"}
