"""The SQL of readings and the order of the queue (DESIGN.md §38.6, §38.8), tested against a real database without a server.

The queue is the delicate part. A reading is one entry that goes back to the queue after every part, and "back" must mean behind everything that was queued
while that part was made and in front of everything queued afterwards — without a clock, which could jump, and without renumbering other runs.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from test_db import run_row

from studio.db import Database
from studio.speech.store import SPEED_WEIGHT, SpeechStore

NOW = "2026-11-08T10:00:00.000Z"
OLD = "2020-01-01T00:00:00.000Z"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


@pytest.fixture
def store(db) -> SpeechStore:
    return SpeechStore(db)


def reading_rows(n: int, parts: list[list[int]], **run_fields: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """The rows `insert_reading` takes: a queued speech run, and for each part the lengths of its pieces."""
    run = {**run_row(n), "mode": "speech", "options_json": json.dumps({"engine": "fake", "variant": "default"}), **run_fields}
    part_rows = [{"run_id": run["id"], "idx": i, "title": f"Part {i + 1}", "chars": sum(lengths)} for i, lengths in enumerate(parts)]
    piece_rows = [{"run_id": run["id"], "part_idx": i, "idx": j, "kind": "paragraph", "text": "x" * chars, "chars": chars, "continues": 0, "seed": j}
                  for i, lengths in enumerate(parts) for j, chars in enumerate(lengths)]
    return run, part_rows, piece_rows


def add(store: SpeechStore, n: int, parts: Optional[list[list[int]]] = None, cap: int = 100, **run_fields: Any) -> str:
    run, part_rows, piece_rows = reading_rows(n, parts or [[50, 60], [70]], **run_fields)
    assert store.insert_reading(run, part_rows, piece_rows, cap) == "ok"
    return run["id"]


def picture(db: Database, n: int, **fields: Any) -> str:
    assert db.insert_run_if_capacity({**run_row(n), **fields}, cap=100)
    return run_row(n)["id"]


def make(store: SpeechStore, run_id: str, part: int, idx: int, frames: int = 24000) -> None:
    store.mark_piece_done(run_id, part, idx, frames, 24000, frames * 2, f"speech/readings/{run_id}/{part:04d}/{idx:05d}.wav")


# ---------------------------------------------------------------- making and reading a reading
def test_a_reading_is_inserted_with_its_parts_and_pieces_in_one_go(store):
    run_id = add(store, 1)
    assert [(p["idx"], p["title"], p["chars"]) for p in store.parts(run_id)] == [(0, "Part 1", 110), (1, "Part 2", 70)]
    assert [(p["idx"], p["chars"], p["status"]) for p in store.pieces(run_id, 0)] == [(0, 50, "queued"), (1, 60, "queued")]
    assert store.piece(run_id, 1, 0)["chars"] == 70 and store.piece(run_id, 1, 5) is None
    assert store.reading(run_id)["mode"] == "speech" and store.reading("0" * 32) is None


def test_a_full_queue_refuses_the_reading_and_leaves_nothing_behind(db, store):
    picture(db, 1)
    run, part_rows, piece_rows = reading_rows(2, [[50]])
    assert store.insert_reading(run, part_rows, piece_rows, cap=1) == "full"
    assert db.get_run(run["id"]) is None and db.read_one("SELECT COUNT(*) AS n FROM speech_parts")["n"] == 0  # not even the parts


def test_a_reading_whose_pieces_cannot_all_be_stored_is_not_stored_at_all(db, store):
    run, part_rows, piece_rows = reading_rows(1, [[50, 60]])
    piece_rows[1]["kind"] = "poem"  # the table refuses it, half way through
    with pytest.raises(Exception):
        store.insert_reading(run, part_rows, piece_rows, cap=10)
    assert db.get_run(run["id"]) is None and db.read_one("SELECT COUNT(*) AS n FROM speech_pieces")["n"] == 0  # all or nothing


def test_only_a_visible_reading_is_a_reading(db, store):
    picture(db, 1)
    add(store, 2, hidden=1)
    visible = add(store, 3)
    assert store.reading(run_row(1)["id"]) is None  # a picture
    assert store.reading(run_row(2)["id"]) is None  # a voice sample's job
    assert store.reading(visible) is not None


# ---------------------------------------------------------------- what is left to make
def test_the_next_pieces_are_the_unmade_ones_of_the_first_part_that_has_any(store):
    run_id = add(store, 1, [[10, 20, 30], [40], [50]])
    assert [p["idx"] for p in store.next_pieces(run_id)] == [0, 1, 2] and store.remaining(run_id) == (150, 5)
    make(store, run_id, 0, 0)
    make(store, run_id, 0, 1)
    assert [p["idx"] for p in store.next_pieces(run_id)] == [2] and store.remaining(run_id) == (120, 3)  # resumes inside the part
    make(store, run_id, 0, 2)
    assert [(p["part_idx"], p["idx"]) for p in store.next_pieces(run_id)] == [(1, 0)]  # then the next part, and only that part
    make(store, run_id, 1, 0)
    make(store, run_id, 2, 0)
    assert store.next_pieces(run_id) == [] and store.remaining(run_id) == (0, 0)


def test_a_piece_made_out_of_order_does_not_hide_the_ones_before_it(store):
    """After a failure the first piece not made is where Continue starts, even if a later one was somehow made."""
    run_id = add(store, 1, [[10, 20, 30]])
    make(store, run_id, 0, 1)
    assert [p["idx"] for p in store.next_pieces(run_id)] == [0, 2]


def test_a_made_piece_records_what_the_worker_said_and_clears_an_old_error(store):
    run_id = add(store, 1, [[50]])
    store.set_piece_error(run_id, 0, 0, "the engine ran out of memory")
    assert store.piece(run_id, 0, 0)["error"] == "the engine ran out of memory"
    store.mark_piece_done(run_id, 0, 0, 12000, 22050, 24000, "speech/readings/x/0000/00000.wav")
    piece = store.piece(run_id, 0, 0)
    assert (piece["status"], piece["frames"], piece["sample_rate"], piece["bytes"], piece["path"], piece["error"]) == (
        "done", 12000, 22050, 24000, "speech/readings/x/0000/00000.wav", None)


def test_the_totals_of_several_readings_come_from_two_queries_and_are_kept_apart(store):
    first, second = add(store, 1, [[10, 20], [30]]), add(store, 2, [[40]])
    make(store, first, 0, 0, 1000)
    make(store, first, 0, 1, 2000)
    totals = store.totals([first, second, "9" * 32])
    assert totals[first]["pieces"] == 3 and totals[first]["made"] == 2 and totals[first]["frames"] == 3000 and totals[first]["bytes"] == 6000
    assert [(p["idx"], p["pieces"], p["made"]) for p in totals[first]["parts"]] == [(0, 2, 2), (1, 1, 0)]
    assert totals[first]["sample_rate"] == 24000 and totals[second]["made"] == 0 and totals[second]["sample_rate"] is None
    assert totals["9" * 32] == {"parts": [], "pieces": 0, "made": 0, "frames": 0, "bytes": 0, "sample_rate": None} and store.totals([]) == {}


# ---------------------------------------------------------------- changing the words of a piece
def test_a_piece_not_yet_made_can_be_changed_and_the_size_of_its_part_follows(store):
    run_id = add(store, 1, [[10, 20]])
    store.set_piece_error(run_id, 0, 1, "failed")
    assert store.edit_piece(run_id, 0, 1, "twelve chars") == "ok"
    piece = store.piece(run_id, 0, 1)
    assert (piece["text"], piece["chars"], piece["error"]) == ("twelve chars", 12, None) and store.parts(run_id)[0]["chars"] == 22


def test_a_made_piece_and_a_missing_one_cannot_be_changed(store):
    run_id = add(store, 1, [[10]])
    make(store, run_id, 0, 0)
    assert store.edit_piece(run_id, 0, 0, "other") == "made" and store.piece(run_id, 0, 0)["text"] == "x" * 10
    assert store.edit_piece(run_id, 0, 5, "other") == "not_found" and store.edit_piece("0" * 32, 0, 0, "other") == "not_found"


# ---------------------------------------------------------------- where the listener stopped; how fast an engine is
def test_a_position_is_one_row_that_is_replaced(store):
    run_id = add(store, 1)
    assert store.position(run_id) is None
    store.set_position(run_id, 0, 1, 250, NOW)
    store.set_position(run_id, 1, 0, 9000, "2026-11-08T11:00:00.000Z")
    row = store.position(run_id)
    assert (row["part_idx"], row["piece_idx"], row["offset_ms"], row["updated_at"]) == (1, 0, 9000, "2026-11-08T11:00:00.000Z")


def test_the_speed_of_an_engine_is_a_moving_average_that_one_slow_piece_does_not_swing(store):
    assert store.speed("fake", "default") is None
    store.record_speed("fake", "default", 100.0, NOW)
    assert store.speed("fake", "default") == pytest.approx(100.0)
    store.record_speed("fake", "default", 10.0, NOW)  # a very slow piece
    assert store.speed("fake", "default") == pytest.approx((1 - SPEED_WEIGHT) * 100.0 + SPEED_WEIGHT * 10.0) and store.speed("fake", "default") > 60
    assert store.speed("fake", "clone") is None and store.speed("other", "default") is None  # each engine and variant has its own
    assert store.db.read_one("SELECT samples FROM speech_speeds WHERE engine='fake' AND variant='default'")["samples"] == 2


# ---------------------------------------------------------------- the order of the queue
def test_runs_are_taken_in_the_order_they_arrived(db, store):
    a, b = picture(db, 1), add(store, 2)
    c = picture(db, 3)
    assert db.queued_ids() == [a, b, c] and db.next_queued()["id"] == a


def test_a_draft_still_goes_first_and_a_reading_never_does(db, store):
    a = add(store, 1)
    draft = picture(db, 2, options_json=json.dumps({"draft": True}))
    assert db.queued_ids() == [draft, a]


def test_a_reading_that_goes_back_to_the_queue_stands_behind_what_was_queued_meanwhile(db, store):
    reading = add(store, 1)
    assert db.mark_running(reading, NOW)
    first, second = picture(db, 2), picture(db, 3)  # queued while the part was being made
    assert db.requeue_run(reading) is True
    later = picture(db, 4)  # queued after the reading went back
    assert db.queued_ids() == [first, second, reading, later]  # not first (it was first), not last (it was not queued after "later")
    row = db.get_run(reading)
    assert row["status"] == "queued" and 3 < row["queue_key"] < 4  # between the last run made before and the next one


def test_two_readings_going_back_in_turn_keep_taking_turns(db, store):
    a, b = add(store, 1), add(store, 2)
    order = []
    for _ in range(4):  # the worker takes the head of the queue, makes a part, and the reading goes back
        head = db.next_queued()["id"]
        order.append(head)
        assert db.mark_running(head, NOW) and db.requeue_run(head)
    assert order == [a, b, a, b]


def test_readings_going_back_in_the_same_instant_are_told_apart(db, store):
    a, b = add(store, 1), add(store, 2)
    for run_id in (a, b):
        assert db.mark_running(run_id, NOW)
    assert db.requeue_run(a) and db.requeue_run(b)
    keys = [db.get_run(r)["queue_key"] for r in (a, b)]
    assert keys[0] < keys[1] and keys[1] - keys[0] == pytest.approx(1e-6) and db.queued_ids() == [a, b]  # in the order they went back


def test_the_place_in_the_queue_uses_no_clock_and_survives_the_newest_run_being_deleted(db, store):
    """The key comes from the sequence counter, which SQLite keeps even when the newest row is gone, so deleting the last run cannot let the next one slip
    in behind a reading it arrived before."""
    reading = add(store, 1)
    picture(db, 2)
    newest = picture(db, 3)
    db.mark_running(reading, NOW)
    with db.tx() as c:
        c.execute("DELETE FROM runs WHERE id=?", (newest,))
    assert db.requeue_run(reading)
    later = picture(db, 4)
    assert db.get_run(later)["seq"] == 4 and 3 < db.get_run(reading)["queue_key"] < 4  # key is 3 + a millionth, though run 3 no longer exists
    assert db.queued_ids()[-2:] == [reading, later]


def test_only_a_running_run_goes_back_to_the_queue(db, store):
    queued, done = add(store, 1), add(store, 2)
    db.mark_running(done, NOW)
    db.finish_run(done, "done", NOW)
    assert db.requeue_run(queued) is False and db.requeue_run(done) is False and db.requeue_run("0" * 32) is False
    assert db.get_run(queued)["queue_key"] is None and db.get_run(done)["status"] == "done"


# ---------------------------------------------------------------- Continue and Retry
@pytest.mark.parametrize("status", ["canceled", "failed"])
def test_a_canceled_or_failed_reading_is_queued_again_at_the_back_with_its_error_cleared(db, store, status):
    reading = add(store, 1)
    db.mark_running(reading, NOW)
    db.finish_run(reading, status, NOW, "it broke", "try again")
    other = picture(db, 2)
    assert db.restart_run(reading, cap=10) == "ok"
    row = db.get_run(reading)
    assert (row["status"], row["error_message"], row["error_hint"], row["finished_at"]) == ("queued", None, None, None) and row["queue_key"] > 2
    assert db.queued_ids() == [other, reading]


@pytest.mark.parametrize("setup", ["queued", "running", "done"])
def test_a_reading_that_is_not_stopped_cannot_be_restarted(db, store, setup):
    reading = add(store, 1)
    if setup != "queued":
        db.mark_running(reading, NOW)
    if setup == "done":
        db.finish_run(reading, "done", NOW)
    assert db.restart_run(reading, cap=10) == "not_restartable" and db.get_run(reading)["status"] == setup


def test_only_a_reading_can_be_restarted_not_a_picture_and_not_a_stranger(db, store):
    failed = picture(db, 1)
    db.mark_running(failed, NOW)
    db.finish_run(failed, "failed", NOW, "broke")
    assert db.restart_run(failed, cap=10) == "not_restartable" and db.get_run(failed)["status"] == "failed"
    assert db.restart_run("0" * 32, cap=10) == "not_found"


def test_a_restart_waits_when_the_queue_is_full(db, store):
    reading = add(store, 1)
    db.mark_running(reading, NOW)
    db.finish_run(reading, "failed", NOW, "broke")
    picture(db, 2)
    assert db.restart_run(reading, cap=1) == "full" and db.get_run(reading)["status"] == "failed"


# ---------------------------------------------------------------- a reading is never expired; the short hidden jobs are not history
def test_a_reading_never_expires_though_an_old_picture_does(db, store):
    reading = add(store, 1, created_at=OLD)
    db.mark_running(reading, OLD)
    db.finish_run(reading, "done", OLD)
    old_picture = picture(db, 2, created_at=OLD, status="done")
    assert db.expire_to_bin("2026-01-01T00:00:00.000Z", NOW, 10) == [old_picture]
    assert db.delete_expired("2026-01-01T00:00:00.000Z", 10) == []  # (the picture is in the bin now; and the reading is still not taken)
    assert db.get_run(reading)["deleted_at"] is None
    pic2 = picture(db, 3, created_at=OLD, status="done")
    assert db.delete_expired("2026-01-01T00:00:00.000Z", 10) == [pic2] and db.get_run(reading) is not None


def test_hidden_jobs_are_in_no_list_and_no_count(db, store):
    shown = add(store, 1)
    add(store, 2, hidden=1)
    assert [r["id"] for r in db.list_runs(10, kind="speech")[0]] == [shown] and [r["id"] for r in db.list_runs(10)[0]] == [shown]
    from studio.runfilter import RunFilter

    assert db.run_counts({"all": RunFilter()})["speech"]["all"] == 1
    assert db.run_counts({"bin": RunFilter(deleted=True)})["speech"]["bin"] == 0
