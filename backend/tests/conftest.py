"""Shared fixtures: a TestClient around a fresh app with the fake pipeline and a temp data dir."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api import create_app
from studio.config import Settings

TERMINAL = frozenset({"done", "failed", "canceled"})


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(data_dir=tmp_path / "data", pipeline="fake", fake_step_delay_ms=1, queue_cap=10,
                                  static_dir=tmp_path / "no-ui")  # placeholder page unless a test builds a UI
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def client_factory(tmp_path: Path) -> Callable[..., TestClient]:
    """Start an app (lifespan included). Extra keyword arguments override Settings fields."""
    open_clients: list[TestClient] = []

    def factory(**overrides: Any) -> TestClient:
        client = TestClient(create_app(make_settings(tmp_path, **overrides)))
        client.__enter__()
        client.headers.update({"X-Studio-Client": "1"})
        open_clients.append(client)
        return client

    yield factory
    for client in reversed(open_clients):
        close(client)


def close(client: TestClient) -> None:
    if getattr(client, "_closed_by_test", False):
        return
    client._closed_by_test = True  # type: ignore[attr-defined]
    client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory: Callable[..., TestClient]) -> TestClient:
    return client_factory()


def create_run(client: TestClient, prompt: str = "a lighthouse at dusk", expect: int = 201,
               mode: str = "generate", **options: Any) -> dict[str, Any]:
    body = {"mode": mode, "prompt": prompt, "options": {"width": 256, "height": 256, "steps": 3, **options}}
    response = client.post("/api/runs", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def wait_for(client: TestClient, run_id: str, statuses: frozenset[str] = TERMINAL, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} still {run['status']!r} after {timeout}s")
        time.sleep(0.02)


def wait_for_worker_state(client: TestClient, state: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        status = client.get("/api/status").json()
        if status["worker"]["state"] == state:
            return status
        if time.monotonic() > deadline:
            raise AssertionError(f"worker state still {status['worker']['state']!r} after {timeout}s")
        time.sleep(0.02)


# ---------------------------------------------------------------- images for upload and edit tests
def image_bytes(color=(255, 0, 0), size=(120, 80), mode: str = "RGB", fmt: str = "PNG", **save) -> bytes:
    """A solid-colour image file, so a test can tell images apart by one pixel."""
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=fmt, **save)
    return out.getvalue()


def stage(client: TestClient, data: bytes, expect: int = 201, **headers: str) -> dict[str, Any]:
    response = client.post("/api/uploads", content=data, headers=headers or None)
    assert response.status_code == expect, response.text
    return response.json()


def edit_body(prompt: str, refs: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
    return {"mode": "edit", "prompt": prompt, "input_images": refs, "options": {"steps": 3, "seed": 7, **options}}


def create_edit(client: TestClient, prompt: str, refs: list[dict[str, Any]], expect: int = 201, **options: Any) -> dict[str, Any]:
    response = client.post("/api/runs", json=edit_body(prompt, refs, **options))
    assert response.status_code == expect, response.text
    return response.json()


def open_result(client: TestClient, run: dict[str, Any], index: int = 0) -> Image.Image:
    response = client.get(run["images"][index]["url"])
    assert response.status_code == 200
    return Image.open(io.BytesIO(response.content))
