"""Cancelling runs (DESIGN.md §21.11, acceptance 9 and 31): in the worker, over its protocol, and
through the API."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import create_run, wait_for, wait_for_worker_state

from studio.pipelines.base import Canceled
from studio.pipelines.fake import FakePipeline
from studio.worker import Emitter, Worker, read_commands

BACKEND = Path(__file__).resolve().parent.parent
RUN_ID = "0123456789abcdef0123456789abcdef"
OTHER_ID = "f" * 32


def job_dict(run_id: str = RUN_ID, seeds=(1, 2, 3), steps: int = 4, **extra) -> dict:
    job = dict(run_id=run_id, mode="generate", prompt="a red barn", negative_prompt=None, width=64, height=64,
               steps=steps, cfg_scale=None, seeds=list(seeds), transparent=False, model_id="fake-pipeline",
               input_path=None)
    job.update(extra)
    return job


class Collect:
    """An emitter that keeps the events instead of writing them."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **fields) -> None:
        self.events.append({"event": event, **fields})

    def kinds(self) -> list[str]:
        return [e["event"] for e in self.events]


class CancelAt(FakePipeline):
    """Asks the worker to cancel when image `image`, step `step` is reached (as the reader thread would)."""

    def __init__(self, image: int, step: int):
        super().__init__(step_delay_ms=0, load_delay_ms=0)
        self.image, self.step, self.worker = image, step, None
        self.steps_run: list[tuple[int, int]] = []  # (image, step) for every step the pipeline got to run

    def generate(self, job, index, seed, on_step):
        def hooked(step: int, total: int) -> None:
            self.steps_run.append((index, step))
            if index == self.image and step == self.step:
                self.worker.request_cancel(job.run_id)
            on_step(step, total)

        return super().generate(job, index, seed, hooked)


# ------------------------------------------------------------------ the worker, in process
def test_cancel_during_an_image_stops_at_that_step_and_keeps_finished_images(tmp_path):
    emit = Collect()
    pipeline = CancelAt(image=1, step=2)
    worker = pipeline.worker = Worker(pipeline, tmp_path, emit)
    worker.run(job_dict(seeds=(1, 2, 3)))
    assert emit.kinds().count("image_done") == 1
    assert emit.events[-1] == {"event": "run_canceled", "run_id": RUN_ID, "completed": 1}
    assert "run_finished" not in emit.kinds() and "run_failed" not in emit.kinds()
    assert (tmp_path / "images" / RUN_ID / "0.png").exists()
    assert not (tmp_path / "images" / RUN_ID / "1.png").exists()
    assert not list(tmp_path.rglob("*.part"))
    assert pipeline.steps_run[-1] == (1, 2) and (1, 3) not in pipeline.steps_run  # no step after the request


def test_a_cancel_that_arrives_after_an_images_last_step_stops_before_the_next_image_starts(tmp_path):
    """The check between images matters on the Spark: starting the next image would first spend the
    time to encode the prompt (and, for an edit, the input images) before the first step could stop it."""

    class CancelAfterImage(FakePipeline):
        def __init__(self):
            super().__init__(step_delay_ms=0, load_delay_ms=0)
            self.generate_calls = 0
            self.worker = None

        def generate(self, job, index, seed, on_step):
            self.generate_calls += 1
            image = super().generate(job, index, seed, on_step)
            self.worker.request_cancel(job.run_id)  # lands while the image is being decoded
            return image

    emit = Collect()
    pipeline = CancelAfterImage()
    worker = pipeline.worker = Worker(pipeline, tmp_path, emit)
    worker.run(job_dict(seeds=(1, 2, 3)))
    assert pipeline.generate_calls == 1
    assert emit.kinds().count("image_done") == 1
    assert emit.events[-1] == {"event": "run_canceled", "run_id": RUN_ID, "completed": 1}


def test_cancel_noticed_between_images_when_no_step_callback_fires(tmp_path):
    emit = Collect()
    worker = Worker(FakePipeline(step_delay_ms=0, load_delay_ms=0), tmp_path, emit)
    worker.request_cancel(RUN_ID)  # arrives before the run starts: the model is loaded first
    worker.run(job_dict())
    assert emit.kinds() == ["state", "state", "run_canceled"]  # loading, ready, canceled: never "started"
    assert emit.events[-1]["completed"] == 0
    assert not (tmp_path / "images").exists()


def test_the_worker_keeps_serving_after_a_cancel_and_forgets_it(tmp_path):
    emit = Collect()
    pipeline = CancelAt(image=0, step=1)
    worker = pipeline.worker = Worker(pipeline, tmp_path, emit)
    worker.run(job_dict(seeds=(1, 2)))
    assert emit.events[-1]["event"] == "run_canceled" and worker.loaded
    emit.events.clear()
    pipeline.image = 99  # no more cancelling
    worker.run(job_dict(seeds=(5,)))
    assert emit.kinds()[-1] == "run_finished" and emit.events[-1]["completed"] == 1


def test_a_cancel_for_another_run_does_not_stop_this_one_and_is_forgotten(tmp_path):
    emit = Collect()
    worker = Worker(FakePipeline(step_delay_ms=0, load_delay_ms=0), tmp_path, emit)
    worker.request_cancel(OTHER_ID)
    worker.run(job_dict(seeds=(1,)))
    assert emit.kinds()[-1] == "run_finished"
    worker.run(job_dict(run_id=OTHER_ID, seeds=(1,)))  # the stale cancel must not hit this later run
    assert emit.kinds()[-1] == "run_finished"


def test_canceled_is_not_an_exception_so_nothing_can_swallow_it():
    assert issubclass(Canceled, BaseException) and not issubclass(Canceled, Exception)


def test_the_command_reader_acts_on_cancel_at_once_and_queues_the_rest(tmp_path):
    emit = Collect()
    worker = Worker(FakePipeline(step_delay_ms=0, load_delay_ms=0), tmp_path, emit)
    commands: queue.Queue = queue.Queue()
    lines = [
        json.dumps({"cmd": "run", "job": {}}), "",
        json.dumps({"cmd": "cancel", "run_id": RUN_ID}),
        json.dumps({"cmd": "cancel", "run_id": "nope"}),
        json.dumps({"cmd": "cancel"}),
        "not json",
        json.dumps({"cmd": "shutdown"}),
    ]
    read_commands(lines, commands, worker, emit)
    assert RUN_ID in worker._canceled
    assert [m["cmd"] if m else None for m in iter(commands.get_nowait, None)] == ["run", "shutdown"]
    assert emit.kinds() == ["protocol_error"] * 3


# ------------------------------------------------------------------ the worker, as a process
class Proc:
    """A worker process talked to interactively: commands go in when we choose, events are collected."""

    def __init__(self, tmp_path: Path, step_delay_ms: int = 0, stderr=None):
        env = dict(os.environ, PYTHONPATH=str(BACKEND))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "studio.worker", "--pipeline", "fake", "--data-dir", str(tmp_path),
             "--model", "m", "--fake-step-delay-ms", str(step_delay_ms)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, env=env)
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.seen: list[dict] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            if line.strip():
                self.events.put(json.loads(line))

    def send(self, **message) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def until(self, kind: str, timeout: float = 20.0, **match) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            event = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            self.seen.append(event)
            if event["event"] == kind and all(event.get(k) == v for k, v in match.items()):
                return event

    def close(self) -> int:
        self.proc.stdin.close()
        try:
            return self.proc.wait(10)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()


def test_a_cancel_sent_while_the_worker_is_generating_is_seen_at_once(tmp_path):
    worker = Proc(tmp_path, step_delay_ms=100)
    try:
        worker.send(cmd="run", job=job_dict(seeds=(1, 2, 3), steps=50))  # 5 s per image if left alone
        worker.until("progress", image=1, step=1)
        time.sleep(0.3)  # a few steps in
        sent = time.monotonic()
        worker.send(cmd="cancel", run_id=RUN_ID)
        canceled = worker.until("run_canceled")
        assert time.monotonic() - sent < 2.0, "the cancel should land within a step or two, not at the end of the image"
        assert canceled["run_id"] == RUN_ID and canceled["completed"] == 0
        assert not any(e["event"] == "image_done" for e in worker.seen)
        # still alive and loaded: the next run starts at once, and nothing is left of the cancelled one
        worker.send(cmd="run", job=job_dict(run_id=OTHER_ID, seeds=(9,), steps=2))
        assert worker.until("run_finished", run_id=OTHER_ID)["completed"] == 1
        assert not (tmp_path / "images" / RUN_ID).exists()
    finally:
        assert worker.close() == 0


def test_cancel_in_the_same_batch_as_run_is_not_lost(tmp_path):
    worker = Proc(tmp_path, step_delay_ms=50)
    try:
        worker.send(cmd="run", job=job_dict(seeds=(1, 2), steps=20))
        worker.send(cmd="cancel", run_id=RUN_ID)  # the reader thread sees both before the run has begun
        assert worker.until("run_canceled")["completed"] == 0
        assert not any(e["event"] in ("image_done", "run_finished") for e in worker.seen)
    finally:
        assert worker.close() == 0


def test_the_worker_exits_cleanly_with_its_reader_thread_still_waiting(tmp_path):
    """The reader blocks in a read for the life of the process; exiting must not trip over it."""
    for _ in range(5):
        worker = Proc(tmp_path, stderr=subprocess.PIPE)
        worker.send(cmd="shutdown")  # stdin stays open, as it does when the API stops an idle worker
        assert worker.until("bye") and worker.proc.wait(10) == 0
        log = worker.proc.stderr.read()
        assert "Fatal" not in log and "Traceback" not in log, log


# ------------------------------------------------------------------ through the API
def slow(client_factory, delay_ms: int = 40, **overrides):
    return client_factory(fake_step_delay_ms=delay_ms, **overrides)


def start_running(client, prompt: str = "a lighthouse at dusk", **options) -> dict:
    run = create_run(client, prompt, **options)
    wait_for(client, run["id"], frozenset({"running"}))
    return run


def wait_progress(client, run_id: str, image: int, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running" or (run["progress"] and run["progress"]["image"] >= image):
            return run
        assert time.monotonic() < deadline, "no progress"
        time.sleep(0.02)


def test_a_queued_run_is_canceled_at_once_and_never_runs(client_factory):
    client = slow(client_factory, delay_ms=40)
    first = start_running(client, steps=25, num_images=1)
    second = create_run(client, "second", steps=3)
    third = create_run(client, "third", steps=3)
    assert client.get(f"/api/runs/{third['id']}").json()["queue_position"] == 2

    response = client.post(f"/api/runs/{second['id']}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "canceled" and body["finished_at"] and body["images"] == [] and body["queue_position"] is None
    assert body["error"] is None and body["canceling"] is False
    assert client.get(f"/api/runs/{third['id']}").json()["queue_position"] == 1  # closed up the queue

    assert wait_for(client, first["id"])["status"] == "done"
    assert wait_for(client, third["id"])["status"] == "done"
    final = client.get(f"/api/runs/{second['id']}").json()
    assert final["status"] == "canceled" and final["images"] == [] and final["started_at"] is None


def test_canceling_a_running_run_stops_it_between_steps_and_keeps_finished_images(client_factory):
    client = slow(client_factory, delay_ms=20)
    run = start_running(client, steps=40, num_images=3)  # 0.8 s per image
    wait_progress(client, run["id"], image=2)
    response = client.post(f"/api/runs/{run['id']}/cancel")
    assert response.status_code == 202
    assert response.json()["status"] == "running" and response.json()["canceling"] is True

    final = wait_for(client, run["id"])
    assert final["status"] == "canceled" and final["canceling"] is False and final["finished_at"]
    assert final["error"] is None and final["progress"] is None
    assert [img["idx"] for img in final["images"]] == [0]  # image 1 was under way and is discarded
    assert client.get(final["images"][0]["url"]).status_code == 200
    data_dir = client.app.state.settings.data_dir
    assert sorted(p.name for p in (data_dir / "images" / run["id"]).iterdir()) == ["0.png"]


def test_a_cancel_takes_effect_within_a_step_not_at_the_end_of_the_image(client_factory):
    client = slow(client_factory, delay_ms=100)
    run = start_running(client, steps=100, num_images=1)  # 10 s if left alone
    wait_progress(client, run["id"], image=1)
    time.sleep(0.3)
    sent = time.monotonic()
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    assert wait_for(client, run["id"], timeout=5)["status"] == "canceled"
    assert time.monotonic() - sent < 2.0


def test_the_worker_stays_loaded_and_the_queue_carries_on_after_a_cancel(client_factory):
    client = slow(client_factory, delay_ms=20)
    run = start_running(client, steps=40, num_images=3)
    follow_up = create_run(client, "after the cancel", steps=3)
    pid = wait_for_worker_state(client, "busy")["worker"]["pid"]
    wait_progress(client, run["id"], image=1)
    client.post(f"/api/runs/{run['id']}/cancel")
    assert wait_for(client, run["id"])["status"] == "canceled"
    assert wait_for(client, follow_up["id"])["status"] == "done"
    status = wait_for_worker_state(client, "ready")
    assert status["worker"]["pid"] == pid, "cancelling must not restart the worker (that would reload the model)"
    assert status["queue"]["running"] is None


def test_the_worker_reads_ready_as_soon_as_a_run_is_canceled(client_factory):
    client = slow(client_factory, delay_ms=20)
    run = start_running(client, steps=40, num_images=2)
    wait_for_worker_state(client, "busy")
    wait_progress(client, run["id"], image=1)
    client.post(f"/api/runs/{run['id']}/cancel")
    assert wait_for(client, run["id"])["status"] == "canceled"
    status = client.get("/api/status").json()  # nothing else is queued to make it ready again
    assert status["worker"]["state"] == "ready" and status["queue"]["running"] is None


def test_a_cancel_during_start_up_and_model_load_still_cancels(client_factory):
    client = slow(client_factory, delay_ms=20)
    run = create_run(client, steps=40, num_images=3)
    wait_for(client, run["id"], frozenset({"running"}))  # the worker is only just starting, the model not loaded
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    final = wait_for(client, run["id"], timeout=30)
    assert final["status"] == "canceled" and len(final["images"]) < 3
    follow_up = create_run(client, "after", steps=3)
    assert wait_for(client, follow_up["id"])["status"] == "done"


def test_a_cancel_that_lands_while_the_worker_is_starting_is_not_lost(client_factory, monkeypatch):
    from studio.worker_client import WorkerClient

    real_start = WorkerClient.start

    async def slow_start(self):  # widen the window between "running" and "job sent"
        await asyncio.sleep(0.6)
        await real_start(self)

    monkeypatch.setattr(WorkerClient, "start", slow_start)
    client = client_factory()
    run = create_run(client, steps=3, num_images=2)
    wait_for(client, run["id"], frozenset({"running"}))
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    final = wait_for(client, run["id"])
    assert final["status"] == "canceled" and final["images"] == []
    assert client.app.state.jobs._cancel_requested == set()  # nothing left behind
    monkeypatch.setattr(WorkerClient, "start", real_start)
    follow_up = create_run(client, "after", steps=3)
    assert wait_for(client, follow_up["id"])["status"] == "done"  # the worker that was started anyway still serves


def test_cancel_twice_is_harmless(client_factory):
    client = slow(client_factory, delay_ms=40)
    run = start_running(client, steps=60, num_images=1)
    wait_progress(client, run["id"], image=1)
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code in (202, 409)  # 409 once it has stopped
    assert wait_for(client, run["id"])["status"] == "canceled"


def test_cancel_refuses_runs_that_have_finished_and_unknown_ids(client_factory):
    client = client_factory()
    done = create_run(client)
    wait_for(client, done["id"])
    refused = client.post(f"/api/runs/{done['id']}/cancel")
    assert refused.status_code == 409 and refused.json()["code"] == "run_finished"
    failed = create_run(client, "x [fake:error]")
    wait_for(client, failed["id"])
    assert client.post(f"/api/runs/{failed['id']}/cancel").status_code == 409
    assert client.post(f"/api/runs/{'a' * 32}/cancel").status_code == 404
    assert client.post("/api/runs/not-an-id/cancel").status_code == 404
    assert client.get(f"/api/runs/{done['id']}").json()["status"] == "done"  # a refused cancel changes nothing


def test_canceling_an_already_canceled_run_is_refused(client_factory):
    client = slow(client_factory, delay_ms=40)
    first = start_running(client, steps=40, num_images=1)
    queued = create_run(client, "second", steps=3)
    assert client.post(f"/api/runs/{queued['id']}/cancel").status_code == 200
    again = client.post(f"/api/runs/{queued['id']}/cancel")
    assert again.status_code == 409 and again.json()["code"] == "run_finished"
    client.post(f"/api/runs/{first['id']}/cancel")
    wait_for(client, first["id"])


def test_cancel_needs_the_client_header_like_every_other_change(client_factory):
    client = client_factory()
    run = create_run(client)
    bare = client.post(f"/api/runs/{run['id']}/cancel", headers={"X-Studio-Client": ""})
    assert bare.status_code == 403 and bare.json()["code"] == "missing_client_header"
    wait_for(client, run["id"])


def test_a_canceled_run_can_be_deleted_with_its_files(client_factory):
    client = slow(client_factory, delay_ms=20)
    run = start_running(client, steps=40, num_images=2)
    wait_progress(client, run["id"], image=2)
    client.post(f"/api/runs/{run['id']}/cancel")
    final = wait_for(client, run["id"])
    assert final["status"] == "canceled" and len(final["images"]) == 1
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    data_dir = client.app.state.settings.data_dir
    assert not (data_dir / "images" / run["id"]).exists() and not (data_dir / "thumbs" / run["id"]).exists()


def test_a_running_run_cannot_be_deleted_even_while_it_is_canceling(client_factory):
    client = slow(client_factory, delay_ms=100)
    run = start_running(client, steps=100, num_images=1)
    wait_progress(client, run["id"], image=1)
    client.post(f"/api/runs/{run['id']}/cancel")
    assert client.delete(f"/api/runs/{run['id']}").status_code in (409, 204)  # 204 only if it already stopped
    wait_for(client, run["id"], timeout=10)


def test_cancel_is_pushed_to_every_open_page(client_factory):
    client = slow(client_factory, delay_ms=20)
    run = start_running(client, steps=40, num_images=2)
    bus = client.app.state.bus
    sub = bus.subscribe()
    try:
        client.post(f"/api/runs/{run['id']}/cancel")
        wait_for(client, run["id"])
        seen = []
        while not sub.queue.empty():
            event, data = sub.queue.get_nowait()
            if event == "run.updated" and data["id"] == run["id"]:
                seen.append((data["status"], data["canceling"]))
    finally:
        bus.unsubscribe(sub)
    assert ("running", True) in seen and seen[-1] == ("canceled", False)
