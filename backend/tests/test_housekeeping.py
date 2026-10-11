"""Keep and auto-expiry (DESIGN.md §5.6, §21.11, acceptance 12 and 32)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from conftest import create_run, make_settings, wait_for
from PIL import Image

from studio import jobs as jobs_module
from studio.db import Database
from studio.serialize import format_ts, parse_ts, utcnow
from studio.storage import Storage

DAY = timedelta(days=1)


def days_ago(days: float) -> str:
    return format_ts(datetime.now(timezone.utc) - timedelta(days=days))


def seed_run(db: Database, storage: Storage, n: int, *, age_days: float, status: str = "done",
             pinned: bool = False) -> str:
    """A finished run of the given age, with an output image and thumbnail on disk, as a real one has."""
    run_id = f"{n:032x}"
    created = days_ago(age_days)
    assert db.insert_run_if_capacity({
        "id": run_id, "created_at": created, "status": "queued", "mode": "generate", "prompt": f"seed {n}",
        "effective_prompt": f"seed {n}", "steps": 3, "seed": n, "num_images": 1, "model_id": "fake-pipeline",
        "options_json": json.dumps({"num_images": 1}), "width": 64, "height": 64, "pinned": int(pinned),
    }, cap=10_000)
    with db.tx() as c:
        c.execute("UPDATE runs SET status=?, started_at=?, finished_at=? WHERE id=?",
                  (status, created if status != "queued" else None,
                   created if status in ("done", "failed", "canceled") else None, run_id))
    if status in ("done", "failed", "canceled"):
        image = storage.run_dir(run_id) / "0.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), "red").save(image)
        thumb = storage.make_thumbnail(image, run_id, 0)
        db.add_image({"id": f"{n + 0x1000:032x}", "run_id": run_id, "kind": "output", "idx": 0, "seed": n,
                      "width": 64, "height": 64, "has_alpha": 0, "bytes": image.stat().st_size,
                      "path": storage.rel(image), "thumb_path": storage.rel(thumb), "created_at": created})
    return run_id


def on_disk(storage: Storage, run_id: str) -> bool:
    return (storage.images / run_id).exists() or (storage.thumbs / run_id).exists()


@pytest.fixture
def seeded(tmp_path):
    """Open the database and file layout the app will use, before the app starts (as after a restart)."""
    settings = make_settings(tmp_path)
    storage = Storage(settings.data_dir)
    storage.ensure_layout()
    db = Database(settings.db_path)
    yield db, storage
    db.close()


def wait_gone(client, run_id: str, timeout: float = 15.0) -> None:
    """Wait until the run is expired: its row first, then (a moment later, in a thread) its files."""
    storage = client.app.state.storage
    deadline = time.monotonic() + timeout
    while client.get(f"/api/runs/{run_id}").status_code != 404 or on_disk(storage, run_id):
        assert time.monotonic() < deadline, f"{run_id} was never expired"
        time.sleep(0.05)


def sweep(client) -> int:
    return client.portal.call(client.app.state.jobs.sweep_expired)


# ------------------------------------------------------------------ the database
def test_set_pinned_toggles_and_reports_a_missing_run(seeded):
    db, storage = seeded
    run_id = seed_run(db, storage, 1, age_days=1)
    assert db.set_pinned(run_id, True) == "ok" and db.get_run(run_id)["pinned"] == 1
    assert db.set_pinned(run_id, True) == "ok"  # asking for what is already so is not an error
    assert db.set_pinned(run_id, False) == "ok" and db.get_run(run_id)["pinned"] == 0
    assert db.set_pinned("0" * 32, True) == "not_found"


def test_delete_expired_takes_only_old_finished_runs_that_are_not_kept(seeded):
    db, storage = seeded
    old = {s: seed_run(db, storage, n, age_days=40, status=s) for n, s in enumerate(("done", "failed", "canceled"), 1)}
    kept = seed_run(db, storage, 4, age_days=40, pinned=True)
    recent = seed_run(db, storage, 5, age_days=29)
    queued = seed_run(db, storage, 6, age_days=40, status="queued")
    running = seed_run(db, storage, 7, age_days=40, status="running")
    removed = db.delete_expired(days_ago(30), limit=100)
    assert sorted(removed) == sorted(old.values())
    for run_id in (kept, recent, queued, running):
        assert db.get_run(run_id) is not None
    assert db.images_for_runs(list(old.values()))[old["done"]] == []  # image rows went with their runs
    assert len(db.images_for_runs([kept])[kept]) == 1


def test_delete_expired_honours_its_limit_and_oldest_first(seeded):
    db, storage = seeded
    ids = [seed_run(db, storage, n, age_days=40) for n in range(1, 6)]
    assert db.delete_expired(days_ago(30), limit=2) == ids[:2]
    assert db.delete_expired(days_ago(30), limit=100) == ids[2:]
    assert db.delete_expired(days_ago(30), limit=100) == []


def test_timestamps_compare_correctly_as_text():
    """The sweep compares created_at with a cutoff as strings; both come from one formatter."""
    a, b = utcnow(), format_ts(datetime.now(timezone.utc) + timedelta(milliseconds=5))
    assert a < b and parse_ts(a) < parse_ts(b) and len(a) == len(b)
    assert format_ts(parse_ts(a)) == a


# ------------------------------------------------------------------ Keep, through the API
def test_keep_and_unkeep_a_run(client):
    run = create_run(client)
    wait_for(client, run["id"])
    assert client.get(f"/api/runs/{run['id']}").json()["pinned"] is False
    kept = client.patch(f"/api/runs/{run['id']}", json={"pinned": True})
    assert kept.status_code == 200 and kept.json()["pinned"] is True and kept.json()["expires_at"] is None
    assert client.get(f"/api/runs/{run['id']}").json()["pinned"] is True  # stored, not just echoed
    assert client.get("/api/runs").json()["runs"][0]["pinned"] is True
    freed = client.patch(f"/api/runs/{run['id']}", json={"pinned": False}).json()
    assert freed["pinned"] is False and freed["expires_at"]


def test_a_run_can_be_kept_while_it_is_still_waiting_or_running(client_factory):
    client = client_factory(fake_step_delay_ms=30)
    running = create_run(client, steps=30)
    waiting = create_run(client, steps=3)
    assert client.patch(f"/api/runs/{waiting['id']}", json={"pinned": True}).json()["pinned"] is True
    assert client.patch(f"/api/runs/{running['id']}", json={"pinned": True}).json()["pinned"] is True
    wait_for(client, waiting["id"])
    assert client.get(f"/api/runs/{waiting['id']}").json()["pinned"] is True  # still kept once it finished


def test_keeping_is_pushed_to_every_open_page(client):
    run = create_run(client)
    wait_for(client, run["id"])
    bus = client.app.state.bus
    sub = bus.subscribe()
    try:
        client.patch(f"/api/runs/{run['id']}", json={"pinned": True})
        event, data = sub.queue.get_nowait()
    finally:
        bus.unsubscribe(sub)
    assert event == "run.updated" and data["id"] == run["id"] and data["pinned"] is True


def test_patch_rejects_anything_but_a_real_boolean_pinned(client):
    run = create_run(client)
    wait_for(client, run["id"])
    url = f"/api/runs/{run['id']}"
    for body in ({}, {"pinned": "yes"}, {"pinned": 1}, {"pinned": None}, {"pinned": True, "prompt": "x"}, {"prompt": "x"}):
        assert client.patch(url, json=body).status_code == 422, body
    assert client.get(url).json()["pinned"] is False and client.get(url).json()["prompt"] == "a lighthouse at dusk"


def test_patch_unknown_and_malformed_ids_and_missing_header(client):
    assert client.patch(f"/api/runs/{'a' * 32}", json={"pinned": True}).status_code == 404
    assert client.patch("/api/runs/not-an-id", json={"pinned": True}).status_code == 404
    run = create_run(client)
    refused = client.patch(f"/api/runs/{run['id']}", json={"pinned": True}, headers={"X-Studio-Client": ""})
    assert refused.status_code == 403 and refused.json()["code"] == "missing_client_header"
    wait_for(client, run["id"])
    assert client.get(f"/api/runs/{run['id']}").json()["pinned"] is False


# ------------------------------------------------------------------ the expiry date on a card
def test_expires_at_is_creation_plus_the_retention_period_for_finished_runs(client_factory):
    client = client_factory(retention_days=10)
    run = create_run(client)
    done = wait_for(client, run["id"])
    assert parse_ts(done["expires_at"]) - parse_ts(done["created_at"]) == 10 * DAY
    assert done["expires_at"] == client.get("/api/runs").json()["runs"][0]["expires_at"]


def test_expires_at_is_absent_while_pending_when_kept_and_when_expiry_is_off(client_factory):
    client = client_factory(fake_step_delay_ms=30)
    running = create_run(client, steps=30)
    waiting = create_run(client, steps=3)
    assert client.get(f"/api/runs/{running['id']}").json()["expires_at"] is None
    assert client.get(f"/api/runs/{waiting['id']}").json()["expires_at"] is None
    client.post(f"/api/runs/{running['id']}/cancel")
    assert wait_for(client, running["id"])["expires_at"] is not None  # finished: now it has a date
    wait_for(client, waiting["id"])
    kept = client.patch(f"/api/runs/{waiting['id']}", json={"pinned": True}).json()
    assert kept["expires_at"] is None

    off = client_factory(retention_days=0)
    run = create_run(off)
    assert wait_for(off, run["id"])["expires_at"] is None


# ------------------------------------------------------------------ the sweep
def test_old_runs_are_removed_at_start_up_with_every_file_and_the_rest_stay(seeded, client_factory):
    db, storage = seeded
    doomed = [seed_run(db, storage, n, age_days=40, status=s) for n, s in enumerate(("done", "failed", "canceled"), 1)]
    kept = seed_run(db, storage, 4, age_days=400, pinned=True)
    recent = seed_run(db, storage, 5, age_days=2)
    edge = seed_run(db, storage, 6, age_days=29.5)
    db.close()  # the app opens its own connection, as after a restart
    client = client_factory(bin_days=0)  # default retention: 30 days; no bin, so expiry deletes for good (the bin has tests/test_bin.py)
    for run_id in doomed:
        wait_gone(client, run_id)
        assert not on_disk(storage, run_id)
    for run_id in (kept, recent, edge):
        assert client.get(f"/api/runs/{run_id}").status_code == 200 and on_disk(storage, run_id)
        assert client.get(client.get(f"/api/runs/{run_id}").json()["images"][0]["url"]).status_code == 200


def test_nothing_is_removed_when_expiry_is_off(seeded, client_factory):
    db, storage = seeded
    old = seed_run(db, storage, 1, age_days=4000)
    db.close()
    client = client_factory(retention_days=0)
    assert client.app.state.jobs._janitor_task is None
    assert sweep(client) == 0
    assert client.get(f"/api/runs/{old}").status_code == 200 and on_disk(storage, old)


def test_pending_runs_are_never_expired_however_old(client_factory):
    client = client_factory(quiet=True, fake_step_delay_ms=30)
    running = create_run(client, steps=40)
    waiting = create_run(client, steps=3)
    wait_for(client, running["id"], frozenset({"running"}))
    db = client.app.state.db
    with db.tx() as c:
        c.execute("UPDATE runs SET created_at=? WHERE id IN (?, ?)", (days_ago(90), running["id"], waiting["id"]))
    assert sweep(client) == 0
    assert client.get(f"/api/runs/{running['id']}").json()["status"] in ("running", "done")
    assert client.get(f"/api/runs/{waiting['id']}").status_code == 200
    assert wait_for(client, waiting["id"])["status"] == "done"  # and it still ran


def test_a_kept_run_survives_and_a_run_stops_being_kept_when_unkept(client_factory):
    client = client_factory(quiet=True, bin_days=0)   # no bin: expiry deletes for good, as before 1.13 (the bin's own tests are in test_bin.py)
    run = create_run(client)
    wait_for(client, run["id"])
    with client.app.state.db.tx() as c:
        c.execute("UPDATE runs SET created_at=? WHERE id=?", (days_ago(90), run["id"]))
    client.patch(f"/api/runs/{run['id']}", json={"pinned": True})
    assert sweep(client) == 0 and client.get(f"/api/runs/{run['id']}").status_code == 200
    client.patch(f"/api/runs/{run['id']}", json={"pinned": False})
    assert sweep(client) == 1 and client.get(f"/api/runs/{run['id']}").status_code == 404


def test_a_sweep_that_is_bigger_than_one_batch_removes_everything(seeded, client_factory, monkeypatch):
    db, storage = seeded
    monkeypatch.setattr(jobs_module, "SWEEP_BATCH", 3)
    ids = [seed_run(db, storage, n, age_days=60) for n in range(1, 11)]
    db.close()
    client = client_factory(bin_days=0)   # no bin: expiry deletes for good, as before 1.13
    for run_id in ids:
        wait_gone(client, run_id)
        assert not on_disk(storage, run_id)


def test_the_sweep_repeats_on_its_interval_and_tells_open_pages(client_factory, monkeypatch):
    monkeypatch.setattr(jobs_module, "SWEEP_INTERVAL_SECONDS", 0.2)
    client = client_factory(bin_days=0)   # no bin: expiry deletes for good, as before 1.13
    run = create_run(client)
    wait_for(client, run["id"])
    bus = client.app.state.bus
    sub = bus.subscribe()
    try:
        with client.app.state.db.tx() as c:  # it grows old while the server is running
            c.execute("UPDATE runs SET created_at=? WHERE id=?", (days_ago(45), run["id"]))
        wait_gone(client, run["id"], timeout=10)
        events = []
        while not sub.queue.empty():
            events.append(sub.queue.get_nowait())
    finally:
        bus.unsubscribe(sub)
    assert ("run.deleted", {"id": run["id"]}) in events
    assert not on_disk(client.app.state.storage, run["id"])


def test_a_failed_sweep_does_not_stop_the_next_one(client_factory, monkeypatch):
    monkeypatch.setattr(jobs_module, "SWEEP_INTERVAL_SECONDS", 0.2)
    real = jobs_module.JobManager.sweep_expired
    calls = {"n": 0}

    async def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk hiccup")
        return await real(self)

    monkeypatch.setattr(jobs_module.JobManager, "sweep_expired", flaky)
    client = client_factory(bin_days=0)   # no bin: expiry deletes for good, as before 1.13
    run = create_run(client)
    wait_for(client, run["id"])
    with client.app.state.db.tx() as c:
        c.execute("UPDATE runs SET created_at=? WHERE id=?", (days_ago(45), run["id"]))
    wait_gone(client, run["id"], timeout=10)
    assert calls["n"] >= 2


def test_deleting_by_hand_still_works_for_kept_and_expiring_runs(client):
    run = create_run(client)
    wait_for(client, run["id"])
    client.patch(f"/api/runs/{run['id']}", json={"pinned": True})
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204  # Keep protects from expiry, not from you
    assert client.get(f"/api/runs/{run['id']}").status_code == 404
