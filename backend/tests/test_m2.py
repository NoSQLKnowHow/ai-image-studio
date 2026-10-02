"""M2 behaviour through the API: start-up capability check, idle unload, memory check,
memory status, and serving the built web UI."""

from __future__ import annotations

import time

import pytest

import studio.sysinfo
from conftest import create_run, wait_for


def meminfo(tmp_path, available_gb: float, total_gb: float = 119.0):
    path = tmp_path / "meminfo"
    kib = lambda gb: int(gb * 1024 * 1024)  # noqa: E731
    path.write_text(f"MemTotal:       {kib(total_gb)} kB\nMemFree:        1000 kB\nMemAvailable:   {kib(available_gb)} kB\n")
    return path


def wait_status(client, predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while True:
        status = client.get("/api/status").json()
        if predicate(status):
            return status
        assert time.monotonic() < deadline, status
        time.sleep(0.05)


# ---------------------------------------------------------------- start-up capability check
def test_capability_check_runs_at_startup(client):
    status = wait_status(client, lambda s: s["worker"]["probe"] == "done")
    assert status["worker"]["device"] == {"name": "fake (no GPU used)"}
    assert status["worker"]["state"] == "unloaded" and status["worker"]["pid"] is None  # nothing loaded yet
    assert client.get("/api/capabilities").json()["supports"]["edit"] is False


def test_unavailable_pipeline_shows_before_any_job(client_factory):
    client = client_factory(pipeline="real")  # this test environment has no PyTorch
    status = wait_status(client, lambda s: s["worker"]["probe"] == "failed")
    assert status["worker"]["state"] == "unavailable"
    assert "PyTorch is not installed" in status["worker"]["detail"] and status["worker"]["hint"]
    assert client.get("/api/runs").json()["runs"] == []  # no job was needed to find out


# ---------------------------------------------------------------- idle unload
def test_model_unloads_after_the_idle_timeout_and_reloads_on_demand(client_factory):
    client = client_factory(idle_timeout_min=0.03)  # ~2 s
    wait_for(client, create_run(client)["id"])
    status = client.get("/api/status").json()
    assert status["worker"]["state"] == "ready" and status["worker"]["unload_at"] and status["worker"]["pid"]
    unloaded = wait_status(client, lambda s: s["worker"]["state"] == "unloaded", timeout=15)
    assert unloaded["worker"]["pid"] is None and unloaded["worker"]["unload_at"] is None
    assert wait_for(client, create_run(client, "again")["id"])["status"] == "done"  # loads again on demand


def test_zero_idle_timeout_unloads_as_soon_as_the_queue_empties(client_factory):
    client = client_factory(idle_timeout_min=0)
    wait_for(client, create_run(client)["id"])
    wait_status(client, lambda s: s["worker"]["state"] == "unloaded", timeout=10)


def test_new_work_cancels_the_pending_unload(client_factory):
    client = client_factory(idle_timeout_min=0.05)  # 3 s
    wait_for(client, create_run(client)["id"])
    pid = client.get("/api/status").json()["worker"]["pid"]
    time.sleep(1.5)
    wait_for(client, create_run(client, "second")["id"])
    assert client.get("/api/status").json()["worker"]["pid"] == pid  # same worker: no reload


# ---------------------------------------------------------------- memory check (decision #19: fail fast)
def test_too_little_memory_fails_fast_without_loading(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=12.5))
    client = client_factory(min_free_gb=40)
    run = wait_for(client, create_run(client)["id"])
    assert run["status"] == "failed" and run["images"] == []
    assert run["error"]["message"] == ("Not enough free memory to load the model: 12.5 GB available, "
                                       "40 GB required (STUDIO_MIN_FREE_GB).")
    assert "--gpu-memory-utilization" in run["error"]["hint"] and "drop_caches" in run["error"]["hint"]
    status = client.get("/api/status").json()
    assert status["worker"]["pid"] is None and status["worker"]["state"] == "error"


def test_enough_memory_runs_normally(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=80))
    client = client_factory(min_free_gb=40)
    assert wait_for(client, create_run(client)["id"])["status"] == "done"


def test_unreadable_meminfo_skips_the_check(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", tmp_path / "missing")
    client = client_factory(min_free_gb=40)
    assert wait_for(client, create_run(client)["id"])["status"] == "done"


def test_status_reports_memory(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=61.2, total_gb=119.6))
    client = client_factory(min_free_gb=40)
    memory = client.get("/api/status").json()["memory"]
    assert (memory["total_gb"], memory["available_gb"], memory["min_free_gb"]) == (119.6, 61.2, 40)


# ---------------------------------------------------------------- serving the web UI
@pytest.fixture
def ui_dir(tmp_path):
    root = tmp_path / "ui"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>AI Image Studio</title><div id=root></div>")
    (root / "assets" / "app-123.js").write_text("console.log('hi')")
    (root / "theme-init.js").write_text("// theme")
    return root


def test_built_ui_is_served_alongside_the_api(client_factory, ui_dir):
    client = client_factory(static_dir=ui_dir)
    page = client.get("/")
    assert page.status_code == 200 and "<div id=root>" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/assets/app-123.js").text == "console.log('hi')"
    assert client.get("/theme-init.js").status_code == 200
    assert client.get("/api/health").json()["ok"] is True  # API routes win over the UI mount
    assert client.get("/nope.js").status_code == 404


def test_the_page_is_always_revalidated_and_hashed_assets_are_cached_for_good(client_factory, ui_dir):
    """After a rebuild the browser must pick up the new index.html at once; the hashed files it names never change."""
    client = client_factory(static_dir=ui_dir)
    page = client.get("/")
    assert page.headers["cache-control"] == "no-cache"
    assert client.get("/theme-init.js").headers["cache-control"] == "no-cache"  # not hashed, so not cached blindly
    asset = client.get("/assets/app-123.js")
    assert "immutable" in asset.headers["cache-control"] and "max-age=31536000" in asset.headers["cache-control"]
    # revalidating costs a 304, which must keep saying so (a 304 without the header lets the browser guess)
    again = client.get("/", headers={"If-None-Match": page.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == "no-cache"
    assert "immutable" not in client.get("/api/health").headers.get("cache-control", "")  # the API is never cached for good
