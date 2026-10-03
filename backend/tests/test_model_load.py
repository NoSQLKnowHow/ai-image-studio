"""Loading and unloading the model on request (DESIGN.md §25, acceptance 51-59): the two endpoints, the idle clock, the
memory check, a run that arrives meanwhile, and the lock that keeps a click from meeting a half-stopped worker."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from unittest import mock

import studio.sysinfo
from conftest import create_run, wait_for, wait_for_worker_state

from studio.worker_client import WorkerClient
from test_m2 import meminfo, wait_status


def load(client, expect: int = 202):
    response = client.post("/api/model/load")
    assert response.status_code == expect, response.text
    return response


def unload(client, expect: int = 200):
    response = client.post("/api/model/unload")
    assert response.status_code == expect, response.text
    return response


def worker(client) -> dict:
    return client.get("/api/status").json()["worker"]


def seconds(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------- loading without a run (criterion 52)
def test_load_starts_the_model_without_a_run(client):
    body = load(client).json()
    assert body["worker"]["state"] in ("loading", "ready") and body["worker"]["pid"]  # the answer is the status
    ready = wait_for_worker_state(client, "ready")
    assert ready["worker"]["pid"] == body["worker"]["pid"]
    assert client.get("/api/runs").json()["runs"] == []  # nothing was generated, nothing was queued


def test_the_status_says_what_the_idle_timeout_is(client_factory):
    assert worker(client_factory(idle_timeout_min=12.5))["idle_timeout_min"] == 12.5


def test_the_idle_clock_starts_when_the_load_finishes_not_when_it_was_asked_for(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "3000")
    client = client_factory(idle_timeout_min=0.5)  # 30 s
    asked = time.time()
    load(client)
    loading = wait_for_worker_state(client, "loading")
    assert loading["worker"]["unload_at"] is None  # no countdown while it loads
    ready = wait_for_worker_state(client, "ready")
    unload_at = seconds(ready["worker"]["unload_at"])
    assert unload_at >= asked + 3 + 30 - 1  # counted from when it was ready: 3 s later than from the click


def test_a_model_loaded_with_the_button_is_still_unloaded_when_idle(client_factory):
    client = client_factory(idle_timeout_min=0.03)  # about 2 s
    load(client)
    wait_for_worker_state(client, "ready")
    gone = wait_status(client, lambda s: s["worker"]["state"] == "unloaded", timeout=15)
    assert gone["worker"]["pid"] is None and gone["worker"]["unload_at"] is None


# ---------------------------------------------------------------- the memory check is the run's (criterion 53)
def test_too_little_memory_refuses_to_load_and_starts_nothing(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=12.5))
    client = client_factory(min_free_gb=40)
    response = load(client, expect=409)
    body = response.json()
    assert body["code"] == "not_enough_memory"
    assert body["detail"] == ("Not enough free memory to load the model: 12.5 GB available, "
                              "40 GB required (STUDIO_MIN_FREE_GB).")
    assert "--gpu-memory-utilization" in body["hint"]
    state = worker(client)
    assert state["pid"] is None and state["state"] == "error" and state["detail"] == body["detail"]
    assert client.get("/api/runs").json()["runs"] == []
    # once memory is free the same button works
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=80))
    load(client)
    assert wait_for_worker_state(client, "ready")["worker"]["detail"] is None


# ---------------------------------------------------------------- a run during the load (criterion 54)
def test_a_run_sent_while_the_model_loads_waits_for_it_and_uses_that_worker(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "1500")
    client = client_factory()
    pid = load(client).json()["worker"]["pid"]
    wait_for_worker_state(client, "loading")
    run = wait_for(client, create_run(client)["id"])
    assert run["status"] == "done" and len(run["images"]) == 1
    assert worker(client)["pid"] == pid  # one worker, one load


# ---------------------------------------------------------------- unloading (criterion 55)
def test_unload_frees_the_model_at_once_and_the_next_run_loads_it_again(client):
    load(client)
    first = wait_for_worker_state(client, "ready")["worker"]["pid"]
    body = unload(client).json()
    assert body["worker"]["state"] == "unloaded" and body["worker"]["pid"] is None and body["worker"]["unload_at"] is None
    assert worker(client)["state"] == "unloaded"
    assert wait_for(client, create_run(client)["id"])["status"] == "done"
    assert worker(client)["pid"] not in (None, first)  # a new worker


def test_unload_is_refused_while_a_run_is_running_and_touches_nothing(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    run = create_run(client, steps=20)
    wait_for(client, run["id"], frozenset({"running"}))
    pid = worker(client)["pid"]
    refused = unload(client, expect=409).json()
    assert refused["code"] == "busy" and "working on a run" in refused["detail"]
    assert worker(client)["pid"] == pid
    assert wait_for(client, run["id"])["status"] == "done"  # the run was left alone


def test_unload_with_nothing_loaded_does_nothing(client):
    assert unload(client).json()["worker"]["state"] == "unloaded"


def test_unload_while_it_is_still_loading_stops_it_without_waiting_for_the_load(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "20000")  # far longer than the test is willing to wait
    client = client_factory()
    load(client)
    wait_for_worker_state(client, "loading")
    started = time.monotonic()
    unload(client)
    assert time.monotonic() - started < 8  # a polite shutdown request would wait for the 20 s load
    assert worker(client)["state"] == "unloaded" and worker(client)["pid"] is None
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "10")  # and it can be loaded again afterwards
    load(client)
    wait_for_worker_state(client, "ready")


def test_a_run_that_arrives_during_an_unload_waits_for_it_and_gets_a_fresh_worker(client_factory):
    """Stopping the worker is slow, and a run that arrived meanwhile must not be handed to the worker that is on its way
    out: it waits for the stop, then starts a new worker (the lock of DESIGN.md §25.3)."""
    client = client_factory()
    load(client)
    old = wait_for_worker_state(client, "ready")["worker"]["pid"]
    real_stop = WorkerClient.stop
    stopping = threading.Event()

    async def slow_stop(self, *args, **kwargs):
        stopping.set()
        import asyncio

        await asyncio.sleep(0.6)  # the worker is still alive and listening here
        return await real_stop(self, *args, **kwargs)

    with mock.patch.object(WorkerClient, "stop", slow_stop):
        outcome: dict = {}
        thread = threading.Thread(target=lambda: outcome.update(status=client.post("/api/model/unload").status_code))
        thread.start()
        assert stopping.wait(5)
        run = wait_for(client, create_run(client)["id"])
        thread.join(10)
    assert outcome["status"] == 200 and run["status"] == "done"
    now = worker(client)
    assert now["pid"] not in (None, old)  # it ran on a new worker, which is still loaded


# ---------------------------------------------------------------- pressing twice, or at the wrong moment (criterion 56)
def test_loading_twice_or_when_loaded_or_in_use_starts_nothing_extra(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "1000")
    client = client_factory(fake_step_delay_ms=40)
    first = load(client).json()["worker"]["pid"]
    assert load(client, expect=200).json()["worker"]["pid"] == first  # already loading
    wait_for_worker_state(client, "ready")
    assert load(client, expect=200).json()["worker"]["pid"] == first  # already loaded
    run = create_run(client, steps=20)
    wait_for(client, run["id"], frozenset({"running"}))
    assert load(client, expect=200).json()["worker"]["pid"] == first  # in use
    assert wait_for(client, run["id"])["status"] == "done"
    assert worker(client)["pid"] == first


def test_unloading_twice_is_harmless(client):
    load(client)
    wait_for_worker_state(client, "ready")
    unload(client)
    assert unload(client).json()["worker"]["state"] == "unloaded"


# ---------------------------------------------------------------- an idle timeout of zero (criterion 57)
def test_loading_ahead_is_refused_when_the_model_unloads_the_moment_it_is_idle(client_factory):
    client = client_factory(idle_timeout_min=0)
    refused = load(client, expect=409).json()
    assert refused["code"] == "no_idle_time" and "STUDIO_IDLE_TIMEOUT_MIN" in refused["hint"]
    assert worker(client)["pid"] is None  # nothing was started


# ---------------------------------------------------------------- after a failed load (criterion 58)
def test_after_a_failed_load_pressing_load_again_retries_on_the_same_worker(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_FAIL", "once")
    client = client_factory()
    load(client)
    failed = wait_for_worker_state(client, "error")["worker"]
    assert "Simulated model load failure" in failed["detail"] and failed["pid"]  # the worker stays up
    load(client)  # the cause is gone (the fake only fails the first time)
    ready = wait_for_worker_state(client, "ready")["worker"]
    assert ready["pid"] == failed["pid"] and ready["detail"] is None


def test_a_retry_that_takes_longer_than_the_idle_timeout_is_not_cut_short(client_factory, monkeypatch):
    """The idle countdown that was running for the worker that had failed must not run on during the retry's load."""
    monkeypatch.setenv("STUDIO_FAKE_LOAD_FAIL", "once")
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "4000")
    client = client_factory(idle_timeout_min=0.05)  # 3 s, shorter than the load
    load(client)
    wait_for_worker_state(client, "error")  # the first load fails at once, and the countdown starts
    pid = worker(client)["pid"]
    load(client)  # the retry takes 4 s
    ready = wait_for_worker_state(client, "ready", timeout=20)
    time.sleep(1.0)  # a countdown that had run on would have stopped the worker by now
    assert worker(client)["state"] == "ready" and worker(client)["pid"] == pid == ready["worker"]["pid"]


def test_a_failed_load_does_not_keep_the_worker_for_ever(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_FAIL", "1")
    client = client_factory(idle_timeout_min=0.03)  # about 2 s
    load(client)
    wait_for_worker_state(client, "error")
    gone = wait_status(client, lambda s: s["worker"]["state"] == "unloaded", timeout=15)
    assert gone["worker"]["pid"] is None


def test_unloading_after_a_failed_load_clears_the_problem(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_FAIL", "1")
    client = client_factory()
    load(client)
    wait_for_worker_state(client, "error")
    assert unload(client).json()["worker"]["state"] == "unloaded"


def test_a_server_that_cannot_run_the_model_is_asked_and_says_so_again(client_factory):
    client = client_factory(pipeline="real")  # this test environment has no PyTorch
    wait_status(client, lambda s: s["worker"]["state"] == "unavailable")
    load(client)
    answered = wait_status(client, lambda s: s["worker"]["state"] == "unavailable" and s["worker"]["pid"] is not None)
    assert "PyTorch is not installed" in answered["worker"]["detail"]
