"""Database behaviour: schema/version, queue capacity, pagination, state transitions."""

from __future__ import annotations

import json

import pytest

from studio.db import INTERRUPTED_MESSAGE, Database, DatabaseError


def run_row(n: int, status: str = "queued") -> dict:
    return {
        "id": f"{n:032x}", "created_at": f"2026-10-01T00:00:{n:02d}Z", "status": status, "mode": "generate",
        "prompt": f"p{n}", "effective_prompt": f"p{n}", "steps": 3, "seed": n, "num_images": 1,
        "model_id": "fake-pipeline", "options_json": json.dumps({}), "width": 256, "height": 256,
    }


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


def test_schema_version_is_recorded_and_newer_databases_are_refused(tmp_path):
    path = tmp_path / "s.sqlite"
    Database(path).close()
    Database(path).close()  # reopening an existing database is fine
    import sqlite3
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(DatabaseError, match="newer version"):
        Database(path)


def test_queue_capacity_counts_only_waiting_runs(db):
    assert db.insert_run_if_capacity(run_row(1, "running"), cap=1)  # running doesn't count
    assert db.insert_run_if_capacity(run_row(2), cap=1)
    assert not db.insert_run_if_capacity(run_row(3), cap=1)
    assert db.count_queued() == 1


def test_list_is_newest_first_with_cursor_pagination(db):
    for n in range(1, 6):
        db.insert_run_if_capacity(run_row(n), cap=100)
    first, more = db.list_runs(limit=2)
    assert [r["prompt"] for r in first] == ["p5", "p4"] and more
    second, more = db.list_runs(limit=2, before_id=first[-1]["id"])
    assert [r["prompt"] for r in second] == ["p3", "p2"] and more
    last, more = db.list_runs(limit=2, before_id=second[-1]["id"])
    assert [r["prompt"] for r in last] == ["p1"] and not more
    with pytest.raises(KeyError):
        db.list_runs(limit=2, before_id="f" * 32)


def test_state_transitions(db):
    db.insert_run_if_capacity(run_row(1), cap=10)
    run_id = run_row(1)["id"]
    assert db.next_queued()["id"] == run_id
    assert db.mark_running(run_id, "now") and not db.mark_running(run_id, "again")
    assert db.delete_run(run_id) == "running"
    db.finish_run(run_id, "done", "later")
    assert db.get_run(run_id)["status"] == "done"
    db.finish_run(run_id, "failed", "even later", "nope")  # only running runs can be finished
    assert db.get_run(run_id)["status"] == "done"
    assert db.delete_run(run_id) == "deleted" and db.delete_run(run_id) == "not_found"


def test_recover_interrupted_marks_running_runs_failed(db):
    db.insert_run_if_capacity(run_row(1, "running"), cap=10)
    db.insert_run_if_capacity(run_row(2), cap=10)
    assert db.recover_interrupted("now") == [run_row(1)["id"]]
    assert db.get_run(run_row(1)["id"])["error_message"] == INTERRUPTED_MESSAGE
    assert db.get_run(run_row(2)["id"])["status"] == "queued"


def test_images_cascade_with_their_run(db):
    db.insert_run_if_capacity(run_row(1), cap=10)
    run_id = run_row(1)["id"]
    db.add_image({"id": "e" * 32, "run_id": run_id, "kind": "output", "idx": 0, "seed": 1, "width": 8,
                  "height": 8, "has_alpha": 0, "bytes": 10, "path": "images/x/0.png", "created_at": "now"})
    assert len(db.images_for_runs([run_id])[run_id]) == 1
    db.delete_run(run_id)
    assert db.get_image("e" * 32) is None
