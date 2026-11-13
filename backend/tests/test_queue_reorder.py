"""Reordering the queue (DESIGN.md section 40, criteria 306 to 313).

Two layers. The first is the database alone: the rules of where a run goes, tried directly and then against a plain list in a few hundred random steps, since an ordering
bug is a sequence of ordinary steps that ends in the wrong place. The second is the real app with the fake pipeline: the route, what it refuses and why, the listing the
Queue panel reads, and what every open page is told.
"""

from __future__ import annotations

import random
import sqlite3
from typing import Optional

import pytest

from conftest import create_run, wait_for
from studio.db import Database

NOW = "2026-11-13T10:00:00.000Z"


def rid(n: int) -> str:
    return f"{n:032x}"


def put(db: Database, n: int, options: str = "{}", mode: str = "generate", hidden: int = 0) -> str:
    """A run that has just been made and is waiting; `n` is both its id and, since they are made in order, its arrival."""
    run = {"id": rid(n), "created_at": NOW, "status": "queued", "mode": mode, "prompt": f"run {n}", "effective_prompt": f"run {n}", "steps": 3, "seed": 1,
           "num_images": 1, "model_id": "m", "options_json": options, "hidden": hidden}
    assert db.insert_run(run, 1000) == "ok"
    return rid(n)


DRAFT = '{"draft": true}'


def order(db: Database) -> list[int]:
    return [int(i, 16) for i in db.queued_ids()]


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


# ------------------------------------------------------------------ moving a waiting run (criterion 306)
def test_a_run_goes_before_or_after_another_and_the_next_to_be_worked_on_follows(db):
    for n in (1, 2, 3, 4):
        put(db, n)
    assert order(db) == [1, 2, 3, 4]
    assert db.move_queued(rid(4), before=rid(1)) == "ok" and order(db) == [4, 1, 2, 3]   # to the front
    assert db.move_queued(rid(4), after=rid(3)) == "ok" and order(db) == [1, 2, 3, 4]    # and to the back
    assert db.move_queued(rid(1), after=rid(2)) == "ok" and order(db) == [2, 1, 3, 4]    # one place down
    assert db.move_queued(rid(3), before=rid(1)) == "ok" and order(db) == [2, 3, 1, 4]   # one place up
    assert int(db.next_queued()["id"], 16) == 2
    assert db.move_queued(rid(1), before=rid(2)) == "ok" and int(db.next_queued()["id"], 16) == 1


def test_dropping_a_run_where_it_already_is_writes_nothing(db):
    for n in (1, 2, 3):
        put(db, n)
    db.move_queued(rid(3), before=rid(1))
    keys = [r["queue_key"] for r in db._all("SELECT queue_key FROM runs ORDER BY seq")]
    assert db.move_queued(rid(1), before=rid(2)) == "ok"      # 1 is already just before 2
    assert db.move_queued(rid(2), after=rid(1)) == "ok"       # and 2 just after 1
    assert [r["queue_key"] for r in db._all("SELECT queue_key FROM runs ORDER BY seq")] == keys and order(db) == [3, 1, 2]


# ------------------------------------------------------------------ what cannot be moved, and what it cannot be moved next to (criterion 307)
def test_a_run_that_is_going_or_finished_or_canceled_cannot_be_moved(db):
    for n in (1, 2, 3, 4):
        put(db, n)
    assert db.mark_running(rid(1), NOW)
    assert db.cancel_queued(rid(2), NOW)
    assert db.move_queued(rid(1), after=rid(3)) == "not_movable"     # going
    assert db.move_queued(rid(2), after=rid(3)) == "not_movable"     # canceled
    db._conn.execute("UPDATE runs SET status='done' WHERE id=?", (rid(3),))
    assert db.move_queued(rid(3), after=rid(4)) == "not_movable"     # done
    assert order(db) == [4]


def test_a_reading_that_has_made_a_part_is_started_and_stays_put_but_others_can_be_placed_around_it(db):
    """A reading goes back to the queue between parts, but it has started: the person's rule is that a run that is under way cannot be moved (DESIGN.md section 40.3)."""
    for n in (1, 2, 3):
        put(db, n, mode="speech")
    assert db.mark_running(rid(1), NOW) and db.requeue_run(rid(1))      # a part made, waiting for the next turn
    assert order(db) == [2, 3, 1]
    assert db.move_queued(rid(1), before=rid(2)) == "not_movable"
    assert order(db) == [2, 3, 1]
    assert db.move_queued(rid(3), after=rid(1)) == "ok" and order(db) == [2, 1, 3]   # others may go after it ...
    assert db.move_queued(rid(3), before=rid(1)) == "ok" and order(db) == [2, 3, 1]  # ... and before it


def test_it_is_put_next_to_a_waiting_run_and_nothing_else(db):
    for n in (1, 2, 3):
        put(db, n)
    assert db.mark_running(rid(1), NOW)
    assert db.move_queued(rid(3), before=rid(1)) == "target_gone"     # nothing goes before the run that is going
    assert db.move_queued(rid(3), after=rid(99)) == "target_gone"     # a run that is not there
    assert db.cancel_queued(rid(2), NOW)
    assert db.move_queued(rid(3), before=rid(2)) == "target_gone"     # a run that was canceled while the person was dragging
    assert db.move_queued(rid(3), before=rid(3)) == "invalid"         # itself
    assert db.move_queued(rid(3)) == "invalid"                        # nothing at all
    assert db.move_queued(rid(98), before=rid(3)) == "not_found"
    assert order(db) == [3]


# ------------------------------------------------------------------ where a run goes when it arrives (criterion 308)
def test_a_run_that_arrives_after_a_reorder_goes_to_the_back(db):
    for n in (1, 2, 3):
        put(db, n)
    db.move_queued(rid(3), before=rid(1))
    put(db, 4)
    assert order(db) == [3, 1, 2, 4]
    db.move_queued(rid(4), before=rid(3))
    put(db, 5)
    assert order(db) == [4, 3, 1, 2, 5]


def test_a_draft_goes_after_the_drafts_at_the_front_and_before_the_rest(db):
    """The rule of DESIGN.md section 22.2, as it was: a draft jumps the waiting full-size runs, and drafts keep their own order."""
    put(db, 1), put(db, 2)
    put(db, 3, DRAFT)
    assert order(db) == [3, 1, 2]
    put(db, 4, DRAFT)
    assert order(db) == [3, 4, 1, 2]
    put(db, 5)
    put(db, 6, DRAFT)
    assert order(db) == [3, 4, 6, 1, 2, 5]


def test_drafts_alone_queue_in_arrival_order(db):
    for n in (1, 2, 3):
        put(db, n, DRAFT)
    assert order(db) == [1, 2, 3]


def test_a_draft_a_person_put_further_down_stays_there_and_a_new_draft_still_goes_to_the_front(db):
    """Your order wins (DESIGN.md section 40.3): moved by hand, a draft is a run like any other; the next draft to arrive is what jumps."""
    put(db, 1), put(db, 2)
    put(db, 3, DRAFT)
    assert order(db) == [3, 1, 2]
    assert db.move_queued(rid(3), after=rid(2)) == "ok" and order(db) == [1, 2, 3]
    put(db, 4, DRAFT)
    assert order(db) == [4, 1, 2, 3]                                        # the new one jumps; the one put at the back is not at the front
    put(db, 5, DRAFT)
    assert order(db) == [4, 5, 1, 2, 3]
    assert db.move_queued(rid(1), before=rid(4)) == "ok" and order(db) == [1, 4, 5, 2, 3]   # a full-size run put in front of the drafts stays there
    put(db, 6, DRAFT)
    assert order(db) == [6, 1, 4, 5, 2, 3]


def test_a_voice_sample_is_a_draft_too_and_goes_ahead_of_waiting_readings(db):
    put(db, 1, mode="speech"), put(db, 2, mode="speech")
    put(db, 3, '{"draft": true, "task": "sample"}', mode="speech", hidden=1)
    assert order(db) == [3, 1, 2]


def test_a_reading_that_goes_back_after_a_part_goes_behind_the_order_a_person_made(db):
    for n in (1, 2, 3, 4):
        put(db, n, mode="speech" if n == 1 else "generate")
    assert db.mark_running(rid(1), NOW)
    assert order(db) == [2, 3, 4]
    db.move_queued(rid(4), before=rid(2))
    assert order(db) == [4, 2, 3]
    assert db.requeue_run(rid(1)) and order(db) == [4, 2, 3, 1]
    put(db, 5)
    assert order(db) == [4, 2, 3, 1, 5]


@pytest.mark.parametrize("options", [None, "", "not json", "[]", "3", '{"draft": false}', '{"draft": 0}', '{}'])
def test_options_that_do_not_say_draft_are_not_a_draft(options):
    assert Database._is_draft(options) is False


def test_options_that_say_draft_are_a_draft():
    assert Database._is_draft('{"draft": true}') is True and Database._is_draft('{"draft": 1}') is True


# ------------------------------------------------------------------ however many times (criterion 309)
def test_the_order_matches_a_plain_list_through_hundreds_of_random_steps(db):
    """Arrivals, drafts, moves, runs started, canceled and sent back by a reading, against a list that is what a person would draw on paper. The keys must also stay
    inside the room between the newest run and the next one, which is what keeps a run that arrives later at the back."""
    rng = random.Random(1122)
    model: list[int] = []        # the waiting runs, in order
    started: set[int] = set()    # readings that went back after a part: waiting, but started
    drafts: set[int] = set()
    going: Optional[int] = None
    n = 0
    for step in range(600):
        action = rng.choice(["arrive", "arrive", "draft", "move", "move", "move", "start", "cancel", "finish", "back"])
        if action in ("arrive", "draft") and len(model) < 40:
            n += 1
            put(db, n, DRAFT if action == "draft" else "{}")
            if action == "draft":
                drafts.add(n)
                front = 0
                while front < len(model) and model[front] in drafts and model[front] != n:
                    front += 1
                model.insert(front, n)
            else:
                model.append(n)
        elif action == "move" and len(model) >= 2:
            who, target = rng.sample(model, 2)
            way = rng.choice(["before", "after"])
            result = db.move_queued(rid(who), **{way: rid(target)})
            if who in started:
                assert result == "not_movable"
            else:
                assert result == "ok"
                model.remove(who)
                model.insert(model.index(target) + (0 if way == "before" else 1), who)
        elif action == "start" and model and going is None:
            going = model.pop(0)
            assert db.mark_running(rid(going), NOW)
        elif action == "back" and going is not None:
            assert db.requeue_run(rid(going))
            started.add(going)
            model.append(going)
            going = None
        elif action == "finish" and going is not None:
            db._conn.execute("UPDATE runs SET status='done' WHERE id=?", (rid(going),))
            going = None
        elif action == "cancel" and model:
            gone = rng.choice(model)
            assert db.cancel_queued(rid(gone), NOW)
            model.remove(gone)
            started.discard(gone)
        assert order(db) == model, f"after step {step} ({action})"
    last = db._conn.execute("SELECT seq FROM sqlite_sequence WHERE name='runs'").fetchone()[0]
    keys = [r["queue_key"] for r in db._all("SELECT queue_key FROM runs WHERE status='queued' AND queue_key IS NOT NULL")]
    assert keys and all(last - 1 <= key < last + 1 for key in keys) and len(set(keys)) == len(keys)


# ------------------------------------------------------------------ the queue no longer needs SQLite's JSON functions
def test_a_draft_still_jumps_the_queue_in_a_sqlite_without_json_functions(tmp_path, monkeypatch):
    """The order used to be a SQL expression that read the draft flag with json_extract, and fell back to arrival order where there is none. The flag is read in Python
    now, when the draft arrives, so the same rule holds everywhere."""
    real_connect = sqlite3.connect

    class NoJson(sqlite3.Connection):
        def execute(self, sql, *args):
            if "json_extract" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real_connect(*a, **{**k, "factory": NoJson}))
    database = Database(tmp_path / "studio.sqlite")
    try:
        put(database, 1), put(database, 2, DRAFT)
        assert order(database) == [2, 1]
    finally:
        database.close()


# ------------------------------------------------------------------ the route (criteria 310 to 313)
def hold(client, steps: int = 60) -> dict:
    """A run that keeps the worker busy, so that what is made after it waits."""
    running = create_run(client, "going now", steps=steps)
    wait_for(client, running["id"], frozenset({"running"}))
    return running


def move(client, run_id: str, **where):
    return client.post("/api/queue/move", json={"id": run_id, **where})


def waiting_order(client) -> list[str]:
    return [item["id"] for item in client.get("/api/queue").json()["queued"]]


def test_the_route_puts_a_waiting_run_where_it_is_told_and_the_worker_follows(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    running = hold(client)
    a, b, c = (create_run(client, f"waiting {n}") for n in "abc")
    assert waiting_order(client) == [a["id"], b["id"], c["id"]]
    answer = move(client, c["id"], before=a["id"])
    assert answer.status_code == 200 and [i["id"] for i in answer.json()["queued"]] == [c["id"], a["id"], b["id"]]
    assert [client.get(f"/api/runs/{r['id']}").json()["queue_position"] for r in (c, a, b)] == [1, 2, 3]   # what the cards say
    assert move(client, a["id"], after=b["id"]).status_code == 200 and waiting_order(client) == [c["id"], b["id"], a["id"]]
    runs = {r["id"]: wait_for(client, r["id"]) for r in (running, a, b, c)}
    started = sorted(runs, key=lambda i: runs[i]["started_at"])
    assert started == [running["id"], c["id"], b["id"], a["id"]]


def test_every_page_is_told_when_the_order_changes(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    hold(client)
    a, b = create_run(client, "a"), create_run(client, "b")
    sub = client.app.state.bus.subscribe()
    assert move(client, b["id"], before=a["id"]).status_code == 200
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    client.app.state.bus.unsubscribe(sub)
    told = [data["positions"] for name, data in events if name == "queue.updated"]
    assert told and told[-1] == {b["id"]: 1, a["id"]: 2}


def test_the_status_carries_the_order_so_a_page_can_tell_it_changed(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    hold(client)
    a, b = create_run(client, "a"), create_run(client, "b")
    assert client.get("/api/status").json()["queue"]["order"] == [a["id"], b["id"]]
    move(client, b["id"], before=a["id"])
    assert client.get("/api/status").json()["queue"]["order"] == [b["id"], a["id"]]


def test_a_run_that_is_going_or_done_is_refused_with_409(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    running = hold(client, steps=40)
    a, b = create_run(client, "a"), create_run(client, "b")
    refused = move(client, running["id"], after=b["id"])
    assert refused.status_code == 409 and refused.json()["code"] == "not_movable" and "already started" in refused.json()["detail"]
    for run in (running, a, b):
        wait_for(client, run["id"])
    again = move(client, a["id"], after=b["id"])
    assert again.status_code == 409 and again.json()["code"] == "not_movable"


def test_the_run_it_is_put_next_to_must_still_be_waiting(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    running = hold(client)
    a, b = create_run(client, "a"), create_run(client, "b")
    gone = move(client, b["id"], before=running["id"])          # the run that is going is not in the line
    assert gone.status_code == 409 and gone.json()["code"] == "queue_changed"
    assert client.post(f"/api/runs/{a['id']}/cancel").status_code == 200
    gone = move(client, b["id"], after=a["id"])                 # canceled while the person was dragging
    assert gone.status_code == 409 and gone.json()["code"] == "queue_changed"
    assert waiting_order(client) == [b["id"]]


def test_a_request_that_does_not_name_one_other_run_is_refused(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    hold(client)
    a, b = create_run(client, "a"), create_run(client, "b")
    for where in ({}, {"before": a["id"], "after": a["id"]}):
        refused = move(client, b["id"], **where)
        assert refused.status_code == 422 and refused.json()["code"] == "invalid"
    assert move(client, b["id"], before=b["id"]).json()["code"] == "invalid"
    assert move(client, b["id"], before="not-an-id").json()["code"] == "invalid"
    assert client.post("/api/queue/move", json={"id": b["id"], "before": a["id"], "extra": 1}).status_code == 422
    assert client.post("/api/queue/move", json={"before": a["id"]}).status_code == 422
    assert waiting_order(client) == [a["id"], b["id"]]


def test_a_run_that_is_not_there_is_404(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    hold(client)
    a = create_run(client, "a")
    assert move(client, "f" * 32, before=a["id"]).status_code == 404
    assert move(client, "not-an-id", before=a["id"]).status_code == 404


def test_the_listing_names_the_run_going_and_each_waiting_run(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    running = hold(client)
    a = create_run(client, "  a   lighthouse\nat dusk ")
    quick = create_run(client, "the draft", steps=3, draft=True)
    long_words = create_run(client, "word " * 40)
    listing = client.get("/api/queue").json()
    assert listing["cap"] == 10 and listing["running"]["id"] == running["id"] and listing["running"]["position"] is None and listing["running"]["movable"] is False
    queued = {item["id"]: item for item in listing["queued"]}
    assert [item["id"] for item in listing["queued"]] == [quick["id"], a["id"], long_words["id"]]          # the draft jumped, as before
    assert [item["position"] for item in listing["queued"]] == [1, 2, 3]
    assert queued[a["id"]] == {"id": a["id"], "kind": "image", "title": "a lighthouse at dusk", "draft": False, "movable": True, "position": 2}
    assert queued[quick["id"]]["draft"] is True and queued[quick["id"]]["movable"] is True
    assert len(queued[long_words["id"]]["title"]) == 80 and queued[long_words["id"]]["title"].endswith("…")


def test_nothing_is_running_and_nothing_waits_when_the_queue_is_empty(client):
    assert client.get("/api/queue").json() == {"running": None, "queued": [], "cap": 10}


@pytest.mark.parametrize("mode, hidden, options, expected_kind, expected_title", [
    ("generate", 0, "{}", "image", "words"),
    ("edit", 0, "{}", "image", "words"),
    ("music", 0, "{}", "music", "words"),
    ("vector", 0, "{}", "vector", "words"),
    ("speech", 0, "{}", "speech", "words"),
    ("speech", 1, '{"draft": true, "task": "sample", "label": "Anna"}', "voice", "Sample: Anna"),
    ("speech", 1, '{"draft": true, "task": "design", "label": "a warm voice, unhurried"}', "voice", "New voice: a warm voice, unhurried"),
    ("speech", 1, '{"draft": true, "task": "sample"}', "voice", "Voice sample"),
])
def test_each_kind_of_run_is_named_for_the_panel(client, mode, hidden, options, expected_kind, expected_title):
    row = {"id": rid(1), "mode": mode, "prompt": "words", "options_json": options, "hidden": hidden, "started_at": None}
    item = client.app.state.jobs._queue_item(row, 1)
    assert item["kind"] == expected_kind and item["title"] == expected_title and item["movable"] is True
    assert item["draft"] is (bool(not hidden and '"draft": true' in options))


def test_a_reading_that_has_started_is_listed_as_not_movable(client):
    row = {"id": rid(1), "mode": "speech", "prompt": "A long book", "options_json": "{}", "hidden": 0, "started_at": NOW}
    assert client.app.state.jobs._queue_item(row, 2)["movable"] is False
