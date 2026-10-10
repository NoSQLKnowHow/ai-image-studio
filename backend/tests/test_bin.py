"""The bin (DESIGN.md §30; criteria 121, 125-131): runs that are deleted stay for 30 days, can be restored, and can be emptied."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from conftest import create_run, wait_for
from test_housekeeping import days_ago, on_disk, seed_run, seeded  # noqa: F401  (seeded is a fixture)

from studio.db import SCHEMA_VERSION, Database
from studio.runfilter import RunFilter
from studio.serialize import format_ts, parse_ts

MS = timedelta(milliseconds=1)


def run_ids(rows) -> list[str]:
    return [r["id"] for r in rows]


def set_column(db: Database, run_id: str, column: str, value) -> None:
    with db.tx() as c:
        c.execute(f"UPDATE runs SET {column}=? WHERE id=?", (value, run_id))


# ------------------------------------------------------------------ the database
def test_a_finished_run_goes_to_the_bin_and_comes_back(seeded):
    db, storage = seeded
    run = seed_run(db, storage, 1, age_days=2)
    assert db.get_run(run)["deleted_at"] is None
    assert db.bin_run(run, "2026-10-05T10:00:00.000Z") == "binned"
    assert db.get_run(run)["deleted_at"] == "2026-10-05T10:00:00.000Z"
    assert db.bin_run(run, "2026-10-09T10:00:00.000Z") == "binned"  # again: it is there already, and keeps the first time
    assert db.get_run(run)["deleted_at"] == "2026-10-05T10:00:00.000Z"
    assert db.restore_run(run, "2026-10-10T10:00:00.000Z") == "restored"
    row = db.get_run(run)
    assert (row["deleted_at"], row["restored_at"]) == (None, "2026-10-10T10:00:00.000Z")
    assert db.restore_run(run, "2026-10-11T10:00:00.000Z") == "not_in_bin"
    assert db.get_run(run)["restored_at"] == "2026-10-10T10:00:00.000Z"  # unchanged by the refusal


@pytest.mark.parametrize("status", ["done", "failed", "canceled"])
def test_every_finished_state_can_go_to_the_bin(seeded, status):
    db, storage = seeded
    assert db.bin_run(seed_run(db, storage, 1, age_days=1, status=status), "2026-10-05T10:00:00.000Z") == "binned"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_run_that_has_not_finished_cannot_go_to_the_bin(seeded, status):
    db, storage = seeded
    run = seed_run(db, storage, 1, age_days=1, status=status)
    assert db.bin_run(run, "2026-10-05T10:00:00.000Z") == "not_finished"
    assert db.get_run(run)["deleted_at"] is None


def test_an_unknown_run_is_not_found(seeded):
    db, _ = seeded
    assert db.bin_run("f" * 32, "2026-10-05T10:00:00.000Z") == "not_found"
    assert db.restore_run("f" * 32, "2026-10-05T10:00:00.000Z") == "not_found"


def test_the_history_leaves_the_bin_out_and_the_bin_holds_only_the_bin(seeded):
    db, storage = seeded
    here, gone = seed_run(db, storage, 1, age_days=1), seed_run(db, storage, 2, age_days=1)
    db.bin_run(gone, "2026-10-05T10:00:00.000Z")
    assert run_ids(db.list_runs(10)[0]) == [here]
    assert run_ids(db.list_runs(10, None, RunFilter(deleted=True))[0]) == [gone]
    assert sorted(run_ids(db.list_runs(10, None, RunFilter(deleted=None))[0])) == sorted([here, gone])


def test_deleting_for_good_works_on_a_run_in_the_bin(seeded):
    db, storage = seeded
    run = seed_run(db, storage, 1, age_days=1)
    db.bin_run(run, "2026-10-05T10:00:00.000Z")
    assert db.delete_run(run) == "deleted"
    assert db.get_run(run) is None and db.images_for_runs([run])[run] == []


def test_expiry_moves_old_unkept_finished_runs_to_the_bin_and_nothing_else(seeded):
    db, storage = seeded
    old = [seed_run(db, storage, n, age_days=40, status=s) for n, s in enumerate(("done", "failed", "canceled"), 1)]
    kept = seed_run(db, storage, 4, age_days=400, pinned=True)
    recent = seed_run(db, storage, 5, age_days=2)
    waiting = seed_run(db, storage, 6, age_days=90, status="queued")
    running = seed_run(db, storage, 7, age_days=90, status="running")
    already = seed_run(db, storage, 8, age_days=90)
    db.bin_run(already, "2026-10-01T00:00:00.000Z")
    moved = db.expire_to_bin(days_ago(30), "2026-10-10T00:00:00.000Z", 100)
    assert sorted(moved) == sorted(old)
    assert all(db.get_run(run_id)["deleted_at"] == "2026-10-10T00:00:00.000Z" for run_id in old)
    assert db.get_run(already)["deleted_at"] == "2026-10-01T00:00:00.000Z"  # not stamped again
    for run_id in (kept, recent, waiting, running):
        assert db.get_run(run_id)["deleted_at"] is None


def test_expiry_counts_from_the_later_of_when_a_run_was_made_and_when_it_was_restored(seeded):
    db, storage = seeded
    fresh, stale = seed_run(db, storage, 1, age_days=90), seed_run(db, storage, 2, age_days=90)
    set_column(db, fresh, "restored_at", days_ago(3))
    set_column(db, stale, "restored_at", days_ago(45))
    assert db.expire_to_bin(days_ago(30), "2026-10-10T00:00:00.000Z", 100) == [stale]


def test_expiry_honours_its_limit_oldest_first(seeded):
    db, storage = seeded
    runs = [seed_run(db, storage, n, age_days=40 + n) for n in range(1, 5)]  # run 4 is the oldest, but 1 was made first
    assert db.expire_to_bin(days_ago(30), "2026-10-10T00:00:00.000Z", 2) == runs[:2]


def test_delete_expired_leaves_the_bin_alone_and_honours_a_restore(seeded):
    db, storage = seeded
    binned, restored, plain = (seed_run(db, storage, n, age_days=90) for n in (1, 2, 3))
    db.bin_run(binned, "2026-10-01T00:00:00.000Z")
    set_column(db, restored, "restored_at", days_ago(1))
    assert db.delete_expired(days_ago(30), 100) == [plain]
    assert db.get_run(binned) is not None and db.get_run(restored) is not None


def test_the_bin_is_purged_by_age_or_all_at_once_or_a_batch_at_a_time(seeded):
    db, storage = seeded
    runs = [seed_run(db, storage, n, age_days=1) for n in range(1, 5)]
    db.bin_run(runs[0], days_ago(45))
    db.bin_run(runs[1], days_ago(31))
    db.bin_run(runs[2], days_ago(2))
    assert db.purge_bin(days_ago(30), 100) == runs[:2]  # the two whose time is over, not the third, not the one that was never deleted
    assert db.get_run(runs[2]) is not None and db.get_run(runs[3]) is not None
    assert db.purge_bin(None, 100) == [runs[2]]  # everything in the bin, and only that
    assert db.get_run(runs[3]) is not None
    db.bin_run(runs[3], days_ago(1))
    extra = [seed_run(db, storage, n, age_days=1) for n in (5, 6, 7)]
    for run_id in extra:
        db.bin_run(run_id, days_ago(1))
    assert len(db.purge_bin(None, 2)) == 2 and len(db.purge_bin(None, 2)) == 2


def test_the_counts_know_the_bin(seeded):
    db, storage = seeded
    here, gone, kept_gone = seed_run(db, storage, 1, age_days=1), seed_run(db, storage, 2, age_days=1), seed_run(db, storage, 3, age_days=1, pinned=True)
    db.bin_run(gone, "2026-10-05T10:00:00.000Z")
    db.bin_run(kept_gone, "2026-10-05T10:00:00.000Z")
    from studio.runfilter import COUNTED
    assert db.run_counts(COUNTED)["image"] == {"all": 1, "kept": 0, "deleted": 2}
    assert here


# ------------------------------------------------------------------ schema 4
def v3_database(path):
    """A schema 3 database as 1.12 leaves it: the current tables without the bin's columns, and a run."""
    Database(path).close()
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX idx_runs_deleted")
    conn.execute("ALTER TABLE runs DROP COLUMN deleted_at")
    conn.execute("ALTER TABLE runs DROP COLUMN restored_at")
    conn.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    conn.execute(
        "INSERT INTO runs (id, created_at, status, mode, prompt, effective_prompt, steps, seed, num_images, model_id, pinned, options_json) "
        "VALUES (?, '2026-10-01T10:00:00.000Z', 'done', 'generate', 'a lighthouse', 'a lighthouse', 20, 5, 1, 'm', 1, '{}')", ("a" * 32,))
    conn.commit()
    conn.close()


def columns(path, table="runs") -> list[tuple[str, str]]:
    conn = sqlite3.connect(path)
    try:
        return [(r[1], r[2]) for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_a_1_12_database_gets_the_bin_and_loses_nothing(tmp_path):
    path = tmp_path / "studio.sqlite"
    v3_database(path)
    assert "deleted_at" not in dict(columns(path))
    db = Database(path)
    try:
        assert SCHEMA_VERSION == 4
        row = db.get_run("a" * 32)
        assert (row["prompt"], row["pinned"], row["deleted_at"], row["restored_at"]) == ("a lighthouse", 1, None, None)
        assert db.bin_run("a" * 32, "2026-10-05T10:00:00.000Z") == "binned"  # the new columns work
    finally:
        db.close()


def test_the_1_12_database_is_copied_first_as_a_way_back(tmp_path):
    path = tmp_path / "studio.sqlite"
    v3_database(path)
    Database(path).close()
    copy = tmp_path / "studio.sqlite.before-schema-4"
    assert copy.is_file()
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"  # as it was
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    finally:
        conn.close()
    assert "deleted_at" not in dict(columns(copy))


def test_a_second_start_changes_nothing_and_a_migrated_database_is_the_same_as_a_fresh_one(tmp_path):
    migrated, fresh = tmp_path / "migrated.sqlite", tmp_path / "fresh.sqlite"
    v3_database(migrated)
    Database(migrated).close()
    copy_bytes = (tmp_path / "migrated.sqlite.before-schema-4").read_bytes()
    Database(migrated).close()
    assert (tmp_path / "migrated.sqlite.before-schema-4").read_bytes() == copy_bytes
    Database(fresh).close()
    for table in ("runs", "images", "tracks", "run_inputs"):
        assert sorted(columns(migrated, table)) == sorted(columns(fresh, table)), table
    conn = sqlite3.connect(migrated)
    try:
        assert "idx_runs_deleted" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    finally:
        conn.close()


# ------------------------------------------------------------------ the API
def finished(client, prompt: str = "a harbour at dawn") -> dict:
    return wait_for(client, create_run(client, prompt)["id"])


def bin_it(client, run_id: str):
    return client.post(f"/api/runs/{run_id}/bin")


@contextmanager
def captured(client):
    bus = client.app.state.bus
    sub = bus.subscribe()
    events: list = []
    try:
        yield events
    finally:
        while not sub.queue.empty():
            events.append(sub.queue.get_nowait())
        bus.unsubscribe(sub)


def test_moving_a_run_to_the_bin_keeps_its_files_and_takes_it_out_of_the_history(client):
    run = finished(client)
    storage = client.app.state.storage
    with captured(client) as events:
        response = bin_it(client, run["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run["id"] and body["deleted_at"] is not None and body["pinned"] is False
    assert on_disk(storage, run["id"]) and client.get(body["images"][0]["url"]).status_code == 200
    assert client.get("/api/runs").json()["runs"] == []
    assert [r["id"] for r in client.get("/api/runs", params={"deleted": "true"}).json()["runs"]] == [run["id"]]
    assert client.get(f"/api/runs/{run['id']}").json()["deleted_at"] == body["deleted_at"]  # still one run you can ask for
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    assert [data for name, data in events if name == "run.deleted"] == []


def test_a_run_in_the_bin_says_when_it_will_be_deleted_for_good_and_does_not_expire(client_factory):
    client = client_factory(bin_days=10)
    run = finished(client)
    body = bin_it(client, run["id"]).json()
    assert parse_ts(body["purge_at"]) - parse_ts(body["deleted_at"]) == timedelta(days=10)
    assert body["expires_at"] is None
    plain = finished(client, "another")
    assert plain["deleted_at"] is None and plain["purge_at"] is None and plain["expires_at"] is not None


def test_a_kept_run_in_the_bin_is_still_kept(client):
    run = finished(client)
    assert client.patch(f"/api/runs/{run['id']}", json={"pinned": True}).status_code == 200
    assert bin_it(client, run["id"]).json()["pinned"] is True
    assert client.post(f"/api/runs/{run['id']}/restore").json()["pinned"] is True


def test_binning_a_run_that_is_not_finished_is_refused(client_factory):
    client = client_factory(fake_step_delay_ms=80)
    running = create_run(client, "going now", steps=20)
    wait_for(client, running["id"], frozenset({"running"}))
    waiting = create_run(client, "waiting its turn", steps=3)
    for run_id in (running["id"], waiting["id"]):
        response = bin_it(client, run_id)
        assert response.status_code == 409 and response.json()["code"] == "run_not_finished"
        assert client.get(f"/api/runs/{run_id}").json()["deleted_at"] is None
    wait_for(client, running["id"])
    wait_for(client, waiting["id"])


def test_unknown_and_malformed_ids_are_404_for_every_bin_route(client):
    for bad in ("f" * 32, "nope", "../x"):
        assert client.post(f"/api/runs/{bad}/bin").status_code == 404
        assert client.post(f"/api/runs/{bad}/restore").status_code == 404


def test_the_bin_routes_need_the_client_header_like_every_change(client):
    run = finished(client)
    plain = client.__class__(client.app)  # no X-Studio-Client
    try:
        assert plain.post(f"/api/runs/{run['id']}/bin").status_code in (400, 403)
        assert plain.delete("/api/bin").status_code in (400, 403)
    finally:
        plain.close()


def test_with_no_bin_the_bin_route_says_so(client_factory):
    client = client_factory(bin_days=0)
    run = finished(client)
    response = bin_it(client, run["id"])
    assert response.status_code == 409 and response.json()["code"] == "bin_off"
    assert client.get(f"/api/runs/{run['id']}").json()["deleted_at"] is None


def test_restoring_puts_the_run_back_with_a_fresh_expiry_clock(client_factory):
    client = client_factory(quiet=True, retention_days=30)
    run = finished(client)
    with client.app.state.db.tx() as c:  # it was made 50 days ago, so it is due to expire: that is why it is in the bin
        c.execute("UPDATE runs SET created_at=? WHERE id=?", (days_ago(50), run["id"]))
    bin_it(client, run["id"])
    with captured(client) as events:
        restored = client.post(f"/api/runs/{run['id']}/restore")
    body = restored.json()
    assert restored.status_code == 200 and body["deleted_at"] is None and body["purge_at"] is None
    assert parse_ts(body["expires_at"]) > datetime.now(timezone.utc) + timedelta(days=29)  # 30 days from now, not from 50 days ago
    assert [r["id"] for r in client.get("/api/runs").json()["runs"]] == [run["id"]]
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]


def test_restoring_a_run_that_is_not_in_the_bin_is_refused(client):
    run = finished(client)
    response = client.post(f"/api/runs/{run['id']}/restore")
    assert response.status_code == 409 and response.json()["code"] == "not_in_bin"


def test_deleting_a_run_in_the_bin_deletes_it_for_good(client):
    run = finished(client)
    bin_it(client, run["id"])
    storage = client.app.state.storage
    with captured(client) as events:
        assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert client.get(f"/api/runs/{run['id']}").status_code == 404 and not on_disk(storage, run["id"])
    assert [data for name, data in events if name == "run.deleted"] == [{"id": run["id"]}]


def test_deleting_a_run_outside_the_bin_still_deletes_it_for_good(client):
    run = finished(client)
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert client.get(f"/api/runs/{run['id']}").status_code == 404 and not on_disk(client.app.state.storage, run["id"])


def test_emptying_the_bin_deletes_what_is_in_it_and_only_that(client):
    one, two, kept = finished(client, "one"), finished(client, "two"), finished(client, "three")
    for run in (one, two):
        bin_it(client, run["id"])
    storage = client.app.state.storage
    with captured(client) as events:
        response = client.delete("/api/bin")
    assert response.status_code == 200 and response.json() == {"deleted": 2}
    for run in (one, two):
        assert client.get(f"/api/runs/{run['id']}").status_code == 404 and not on_disk(storage, run["id"])
    assert client.get(f"/api/runs/{kept['id']}").status_code == 200 and on_disk(storage, kept["id"])
    assert sorted(data["id"] for name, data in events if name == "run.deleted") == sorted([one["id"], two["id"]])
    assert client.delete("/api/bin").json() == {"deleted": 0}


def test_the_counts_follow_the_bin(client):
    run, other = finished(client, "a picture"), finished(client, "another")
    assert client.get("/api/runs/counts").json()["image"] == {"all": 2, "kept": 0, "deleted": 0}
    bin_it(client, run["id"])
    assert client.get("/api/runs/counts").json()["image"] == {"all": 1, "kept": 0, "deleted": 1}
    client.post(f"/api/runs/{run['id']}/restore")
    assert client.get("/api/runs/counts").json()["image"] == {"all": 2, "kept": 0, "deleted": 0}
    assert other


def test_the_list_pages_within_the_bin_and_the_history_separately(client):
    ids = [finished(client, f"run {n}")["id"] for n in range(5)]
    for run_id in ids[:3]:
        bin_it(client, run_id)
    page = client.get("/api/runs", params={"deleted": "true", "limit": 2}).json()
    assert [r["id"] for r in page["runs"]] == [ids[2], ids[1]] and page["next_before"] == ids[1]
    rest = client.get("/api/runs", params={"deleted": "true", "limit": 2, "before": page["next_before"]}).json()
    assert [r["id"] for r in rest["runs"]] == [ids[0]] and rest["next_before"] is None
    assert [r["id"] for r in client.get("/api/runs").json()["runs"]] == [ids[4], ids[3]]
    assert client.get("/api/runs", params={"deleted": "maybe"}).status_code == 422


def test_the_capabilities_say_how_long_the_bin_keeps_a_run(client_factory):
    assert client_factory(bin_days=7).get("/api/capabilities").json()["limits"]["bin_days"] == 7
    assert client_factory(bin_days=0).get("/api/capabilities").json()["limits"]["bin_days"] == 0


# ------------------------------------------------------------------ the daily clean-up
def sweep(client) -> int:
    return client.portal.call(client.app.state.jobs.sweep_expired)


def test_the_clean_up_moves_an_expired_run_to_the_bin_instead_of_deleting_it(seeded, client_factory):
    db, storage = seeded
    old = seed_run(db, storage, 1, age_days=40)
    kept = seed_run(db, storage, 2, age_days=400, pinned=True)
    recent = seed_run(db, storage, 3, age_days=2)
    db.close()
    client = client_factory(quiet=True)
    with captured(client) as events:
        assert sweep(client) == 0  # nothing was deleted for good
    row = client.get(f"/api/runs/{old}").json()
    assert row["deleted_at"] is not None and on_disk(storage, old)
    assert client.get(f"/api/runs/{kept}").json()["deleted_at"] is None and client.get(f"/api/runs/{recent}").json()["deleted_at"] is None
    assert [data["id"] for name, data in events if name == "run.updated"] == [old]
    assert [data for name, data in events if name == "run.deleted"] == []
    assert sweep(client) == 0 and client.get(f"/api/runs/{old}").json()["deleted_at"] == row["deleted_at"]  # not moved again


def test_the_clean_up_deletes_for_good_a_run_whose_time_in_the_bin_is_over(seeded, client_factory):
    db, storage = seeded
    over, still, never = (seed_run(db, storage, n, age_days=1) for n in (1, 2, 3))
    db.bin_run(over, days_ago(31))
    db.bin_run(still, days_ago(29))
    db.close()
    client = client_factory(quiet=True)
    with captured(client) as events:
        assert sweep(client) == 1
    assert client.get(f"/api/runs/{over}").status_code == 404 and not on_disk(storage, over)
    assert client.get(f"/api/runs/{still}").status_code == 200 and on_disk(storage, still)
    assert client.get(f"/api/runs/{never}").status_code == 200
    assert [data for name, data in events if name == "run.deleted"] == [{"id": over}]


def test_a_run_moved_to_the_bin_by_the_clean_up_can_be_restored_and_is_not_moved_again(seeded, client_factory):
    db, storage = seeded
    old = seed_run(db, storage, 1, age_days=60)
    db.close()
    client = client_factory(quiet=True)
    sweep(client)
    assert client.get(f"/api/runs/{old}").json()["deleted_at"] is not None
    assert client.post(f"/api/runs/{old}/restore").status_code == 200
    sweep(client)
    assert client.get(f"/api/runs/{old}").json()["deleted_at"] is None  # a fresh 30 days: it is 60 days old and stays


def test_with_no_bin_the_clean_up_deletes_as_it_did_before_and_empties_what_is_there(seeded, client_factory):
    db, storage = seeded
    expired, binned, recent = seed_run(db, storage, 1, age_days=40), seed_run(db, storage, 2, age_days=2), seed_run(db, storage, 3, age_days=2)
    db.bin_run(binned, days_ago(1))
    db.close()
    client = client_factory(quiet=True, bin_days=0)
    assert sweep(client) == 2
    for run_id in (expired, binned):
        assert client.get(f"/api/runs/{run_id}").status_code == 404 and not on_disk(storage, run_id)
    assert client.get(f"/api/runs/{recent}").status_code == 200


def test_with_expiry_off_the_bin_still_empties_by_age(seeded, client_factory):
    db, storage = seeded
    over, old = seed_run(db, storage, 1, age_days=1), seed_run(db, storage, 2, age_days=400)
    db.bin_run(over, days_ago(31))
    db.close()
    client = client_factory(quiet=True, retention_days=0)
    assert sweep(client) == 1
    assert client.get(f"/api/runs/{over}").status_code == 404 and client.get(f"/api/runs/{old}").json()["deleted_at"] is None
