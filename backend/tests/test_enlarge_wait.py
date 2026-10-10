"""Enlarge takes its turn on the GPU (DESIGN.md §28.3; criteria 106-109). A picture being generated holds nearly all of the GPU's
memory, so an enlargement started then ran out of it ("CUDA error: out of memory"). It now waits: it goes after the run that is
going and before the runs still queued, a run asked for during an enlargement waits for it, and the page is told who is waiting.
The upscaler is a stub that records what the job manager was doing when it started; pictures come from the fake pipeline."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from conftest import create_run, wait_for

from studio.serialize import parse_ts
from studio.upscaler import Availability, FakeUpscaler

WIDE = {"width": 2560, "height": 1440, "steps": 2}


def wide_run(client, prompt: str = "a harbour at dawn") -> dict:
    return wait_for(client, create_run(client, prompt, **WIDE)["id"])


def enlarge(client, image: dict):
    return client.post(f"/api/images/{image['id']}/enlarge")


def now() -> datetime:
    return datetime.now(timezone.utc)


MS = timedelta(milliseconds=1)  # the server stores a run's times to the millisecond, cut off, so one that started a moment after
                                # `ended` can read a few microseconds earlier


class Recording:
    """An upscaler that notes, the moment it starts, whether a run was going and what the worker was doing, and when it ended."""

    def __init__(self, jobs, delay: float = 0.0):
        self.jobs, self.delay = jobs, delay
        self.started: Optional[datetime] = None
        self.ended: Optional[datetime] = None
        self.run_going_at_start: Optional[str] = "never started"
        self.calls = 0

    def availability(self) -> Availability:
        return Availability(True, model="recording")

    async def enlarge(self, src, dst) -> None:
        self.calls += 1
        self.started, self.run_going_at_start = now(), self.jobs._current
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            await FakeUpscaler().enlarge(src, dst)
        finally:
            self.ended = now()


def waiting(client) -> list[str]:
    return client.get("/api/status").json()["queue"]["enlarge_waiting"]


def use(client, delay: float = 0.0) -> Recording:
    stub = Recording(client.app.state.jobs, delay)
    client.app.state.jobs.upscaler = stub
    return stub


def test_nobody_is_waiting_by_default_and_the_status_says_so(client):
    assert waiting(client) == []


def test_an_enlargement_during_a_run_waits_for_it_and_goes_before_the_runs_still_queued(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    older = wide_run(client)["images"][0]
    stub = use(client, delay=0.3)
    first = create_run(client, "going now", steps=25)  # about 1.5 s
    wait_for(client, first["id"], frozenset({"running"}))
    behind = create_run(client, "queued behind it", steps=3)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, older))
        time.sleep(0.4)  # asked for while the first run is still going
        assert waiting(client) == [older["id"]] and stub.calls == 0
        assert client.get(f"/api/runs/{first['id']}").json()["status"] == "running"
        response = future.result()
    assert response.status_code == 201
    first_done, behind_done = wait_for(client, first["id"]), wait_for(client, behind["id"])
    assert first_done["status"] == behind_done["status"] == "done"
    assert stub.run_going_at_start is None  # no run was going when it started
    assert parse_ts(first_done["finished_at"]) <= stub.started  # after the run it waited for ...
    assert stub.ended <= parse_ts(behind_done["started_at"]) + MS  # ... and before the one queued behind that
    assert waiting(client) == []


def test_the_queue_event_says_who_is_waiting_and_then_that_nobody_is(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    older = wide_run(client)["images"][0]
    use(client, delay=0.1)
    sub = client.app.state.bus.subscribe()
    run = create_run(client, "going now", steps=15)
    wait_for(client, run["id"], frozenset({"running"}))
    enlarge(client, older)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    client.app.state.bus.unsubscribe(sub)
    lists = [data["enlarge_waiting"] for name, data in events if name == "queue.updated"]
    assert [older["id"]] in lists and lists[-1] == []
    assert all(len(waiting_ids) <= 1 for waiting_ids in lists)


def test_an_enlargement_that_does_not_have_to_wait_is_not_announced(client):
    older = wide_run(client)["images"][0]
    use(client)
    sub = client.app.state.bus.subscribe()
    assert enlarge(client, older).status_code == 201
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    client.app.state.bus.unsubscribe(sub)
    assert [data for name, data in events if name == "queue.updated" and data["enlarge_waiting"]] == []


def test_a_run_asked_for_during_an_enlargement_waits_for_it(client):
    older = wide_run(client)["images"][0]
    stub = use(client, delay=0.8)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, older))
        time.sleep(0.25)  # the enlargement is going
        run = create_run(client, "asked for meanwhile", steps=3)
        time.sleep(0.2)
        assert client.get(f"/api/runs/{run['id']}").json()["status"] == "queued"  # waiting for the GPU, still in the queue
        assert future.result().status_code == 201
    done = wait_for(client, run["id"])
    assert done["status"] == "done" and stub.ended <= parse_ts(done["started_at"]) + MS


def test_two_enlargements_wait_for_each_other_in_the_order_they_asked(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    one, two = wide_run(client, "one")["images"][0], wide_run(client, "two")["images"][0]
    stub = use(client, delay=0.25)
    run = create_run(client, "going now", steps=15)
    wait_for(client, run["id"], frozenset({"running"}))
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(lambda: enlarge(client, one))
        time.sleep(0.15)
        second = pool.submit(lambda: enlarge(client, two))
        time.sleep(0.15)
        assert waiting(client) == [one["id"], two["id"]]  # oldest first
        assert first.result().status_code == second.result().status_code == 201
    assert stub.calls == 2 and waiting(client) == []


def test_a_model_that_is_loading_is_using_the_gpu_too(client):
    """The Load model button starts a load that goes on in the worker after the request has returned, so the gate is not held."""
    jobs = client.app.state.jobs
    older = wide_run(client)["images"][0]
    stub = use(client)

    async def set_state(state: str) -> None:
        jobs._set_worker_state(state)

    client.portal.call(set_state, "loading")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, older))
        time.sleep(0.8)  # long enough for several looks
        assert stub.calls == 0 and waiting(client) == [older["id"]]
        released = now()
        client.portal.call(set_state, "ready")
        assert future.result().status_code == 201
    assert stub.started >= released and waiting(client) == []


def test_an_enlargement_waiting_for_its_turn_that_is_asked_again_is_still_one_enlargement(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    older = wide_run(client)["images"][0]
    stub = use(client, delay=0.1)
    run = create_run(client, "going now", steps=12)
    wait_for(client, run["id"], frozenset({"running"}))
    with ThreadPoolExecutor(3) as pool:
        responses = [f.result() for f in [pool.submit(lambda: enlarge(client, older)) for _ in range(3)]]
    assert sorted(r.status_code for r in responses) == [200, 200, 201] and stub.calls == 1


def test_a_failed_enlargement_gives_the_turn_back(client):
    """The gate is released on every way out: a failure must not leave the next run waiting for ever."""
    from studio.upscaler import UpscaleFailed

    older = wide_run(client)["images"][0]
    stub = use(client)

    async def broken(src, dst):
        raise UpscaleFailed("boom")

    stub.enlarge = broken
    assert enlarge(client, older).status_code == 500
    run = create_run(client, "after the failure", steps=3)
    assert wait_for(client, run["id"], timeout=10)["status"] == "done"
