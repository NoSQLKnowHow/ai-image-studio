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
from conftest import close, create_run, wait_for

from studio.serialize import parse_ts
from studio.upscaler import Availability, FakeUpscaler

# A 16:9 size that Enlarge accepts, with few steps so that the fake pipeline is quick
WIDE = {"width": 2560, "height": 1440, "steps": 2}


# Make one finished picture of that size and return its run; the tests enlarge its image
def wide_run(client, prompt: str = "a harbour at dawn") -> dict:
    return wait_for(client, create_run(client, prompt, **WIDE)["id"])


# Press Enlarge on a picture (POST /api/images/{id}/enlarge). The request returns when the copy has been made.
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

    # Called when the job manager starts the enlargement itself: note the moment, and which run (if any) the manager thought was going,
    # then do the (fake) work after the delay
    async def enlarge(self, src, dst) -> None:
        self.calls += 1
        self.started, self.run_going_at_start = now(), self.jobs._current
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            await FakeUpscaler().enlarge(src, dst)
        finally:
            self.ended = now()


# The images the server says are waiting for their turn on the GPU (`enlarge_waiting` in the status's queue)
def waiting(client) -> list[str]:
    return client.get("/api/status").json()["queue"]["enlarge_waiting"]


# Swap the job manager's upscaler for the recording stub; `delay` is how long its work takes
def use(client, delay: float = 0.0) -> Recording:
    stub = Recording(client.app.state.jobs, delay)
    client.app.state.jobs.upscaler = stub
    return stub


# The baseline: with nothing going on, nobody is waiting
def test_nobody_is_waiting_by_default_and_the_status_says_so(client):
    assert waiting(client) == []


# The main rule (criterion 106). One run is going (about 1.5 s) and another is queued behind it. Enlarge is pressed meanwhile: it
# must wait, and say so, start only once the running run is over, and be done before the queued run starts: after the current run, before
# the queue. The timestamps at the end prove that order.
def test_an_enlargement_during_a_run_waits_for_it_and_goes_before_the_runs_still_queued(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    older = wide_run(client)["images"][0]
    stub = use(client, delay=0.3)
    first = create_run(client, "going now", steps=25)  # about 1.5 s
    wait_for(client, first["id"], frozenset({"running"}))
    behind = create_run(client, "queued behind it", steps=3)
    # press Enlarge from another thread: the request does not return until the copy is made
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, older))
        time.sleep(0.4)  # asked for while the first run is still going
        assert waiting(client) == [older["id"]] and stub.calls == 0
        assert client.get(f"/api/runs/{first['id']}").json()["status"] == "running"
        response = future.result()
    assert response.status_code == 201
    first_done, behind_done = wait_for(client, first["id"]), wait_for(client, behind["id"])
    assert first_done["status"] == behind_done["status"] == "done"
    # the order, proved by the timestamps: no run was going when the enlargement started, it started after the run it waited for had
    # finished, and it ended before the queued run began
    assert stub.run_going_at_start is None  # no run was going when it started
    assert parse_ts(first_done["finished_at"]) <= stub.started  # after the run it waited for ...
    assert stub.ended <= parse_ts(behind_done["started_at"]) + MS  # ... and before the one queued behind that
    assert waiting(client) == []


# The page learns who is waiting from the queue event: it names the waiting picture, and the list is empty again afterwards
def test_the_queue_event_says_who_is_waiting_and_then_that_nobody_is(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    older = wide_run(client)["images"][0]
    use(client, delay=0.1)
    # listen to the server's event stream, as the page does, and collect everything published meanwhile
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


# With the GPU free, Enlarge starts at once and nobody is told it is "waiting": no flash of Waiting… on the button
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


# The other direction: a run asked for during an enlargement stays queued until the enlargement is over (the few seconds it takes)
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


# The gate is fair: two enlargements wait in the order they were asked, and both are made in the end
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

    # pretend the worker is loading a model (the Load model button): it is using the GPU without holding the gate
    async def set_state(state: str) -> None:
        jobs._set_worker_state(state)

    client.portal.call(set_state, "loading")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, older))
        try:
            time.sleep(0.8)  # long enough for several looks
            assert stub.calls == 0 and waiting(client) == [older["id"]]
            released = now()
        finally:
            client.portal.call(set_state, "ready")  # even after a failed assertion: the request must not be left waiting for ever
        assert future.result().status_code == 201
    assert stub.started >= released and waiting(client) == []


# Pressing Enlarge again while it waits must not start a second enlargement: the later requests join the first one (200, "already
# made", for two of the three) and the upscaler is called once.
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

    # make the upscaler fail
    async def broken(src, dst):
        raise UpscaleFailed("boom")

    stub.enlarge = broken
    assert enlarge(client, older).status_code == 500
    run = create_run(client, "after the failure", steps=3)
    assert wait_for(client, run["id"], timeout=10)["status"] == "done"


# The announcement has to come when the wait starts, not when it is over, or a page would show Waiting… too late to matter: compare
# where the two events fall in the stream.
def test_it_is_announced_when_it_starts_to_wait_not_only_when_the_run_it_waits_for_is_over(client_factory):
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
    said = next(i for i, (name, data) in enumerate(events) if name == "queue.updated" and older["id"] in data["enlarge_waiting"])
    over = next(i for i, (name, data) in enumerate(events) if name == "run.updated" and data["id"] == run["id"] and data["status"] == "done")
    assert said < over  # a page learns it is waiting while it waits, not afterwards


# A lock that counts how often it was asked for: used to show that a loading model is looked at now and then, not in a tight loop
class CountingGate(asyncio.Lock):
    acquisitions = 0

    async def acquire(self) -> bool:
        type(self).acquisitions += 1
        return await super().acquire()


# While a model loads, the waiting enlargement must look again gently (about every half second), not spin: count how often it asked
# for the gate in 1.2 seconds.
def test_a_model_that_is_loading_is_looked_at_now_and_then_not_in_a_tight_loop(client):
    jobs = client.app.state.jobs
    older = wide_run(client)["images"][0]
    use(client)
    CountingGate.acquisitions = 0
    jobs._gpu_gate = CountingGate()

    # pretend the worker is loading a model, as above
    async def set_state(state: str) -> None:
        jobs._set_worker_state(state)

    client.portal.call(set_state, "loading")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, older))
        try:
            time.sleep(1.2)
            looks = CountingGate.acquisitions
        finally:
            client.portal.call(set_state, "ready")
        assert future.result().status_code == 201
    assert 1 <= looks <= 6  # about every half second


# Two requests for the same picture: if the copy is made by someone else while this one waits, its turn must find it there and do
# nothing (200, not 201, and the upscaler is never called).
def test_a_copy_that_appeared_while_it_waited_is_not_made_again(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    older = wide_run(client)["images"][0]
    stub = use(client)
    run = create_run(client, "going now", steps=25)
    wait_for(client, run["id"], frozenset({"running"}))
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, older))
        time.sleep(0.4)
        assert waiting(client) == [older["id"]]
        storage = client.app.state.storage
        # make the copy ourselves, as another request would have done while this one waited
        asyncio.run(FakeUpscaler().enlarge(storage.abs(older_path(client, older)), storage.enlarged_path(older_path(client, older))))
        response = future.result()
    assert response.status_code == 200 and stub.calls == 0  # there already; nothing was made when its turn came
    assert waiting(client) == []


# The stored path of the picture (the test builds the copy's path from it)
def older_path(client, image: dict) -> str:
    return client.app.state.db.get_image(image["id"])["path"]


# A shutdown while an enlargement still waits must cancel it, instead of letting it start a process in the middle of the shutdown (a
# real flaw that these tests found; DESIGN.md §28.9).
def test_stopping_the_server_cancels_an_enlargement_that_is_still_waiting(client_factory):
    client = client_factory(fake_step_delay_ms=60)
    older = wide_run(client)["images"][0]
    stub = use(client)
    run = create_run(client, "going now", steps=40)
    wait_for(client, run["id"], frozenset({"running"}))
    jobs = client.app.state.jobs
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, older))
        time.sleep(0.4)
        assert waiting(client) == [older["id"]]
        # the request that is in progress, remembered so that we can check afterwards that it was cancelled
        flight = jobs._enlarging[older["id"]]
        close(client)
        assert flight.cancelled() and stub.calls == 0 and jobs._enlarge_waiting == []
        future.exception(timeout=15)  # the request ends; how it ends is not the point
