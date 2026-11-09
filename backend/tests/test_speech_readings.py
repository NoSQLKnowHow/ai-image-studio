"""Readings on the server (DESIGN.md §38.6): one queue entry that is made a part at a time, other jobs slipping in between the parts, Cancel and Continue,
a piece the engine fails on being corrected and retried, and what a restart does to a reading that was being made.

These run the real job manager, the real speech worker (a subprocess) and the fake engines, so a passing test means the whole path works: the request,
the pieces in the database, the job sent to the worker, each finished piece taken in as it arrives, and the reading going back to the queue after a part.
Validation of the request and the audio routes are in test_speech_reading_requests.py and test_speech_audio_routes.py.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable, Optional

import pytest
from conftest import close, create_run, wait_for
from test_speech_server import wait_for_engines

from studio.serialize import utcnow

# A paragraph long enough (over the fake engine's 40-character minimum) to be a piece of its own, so the number of pieces in a test is the number of
# paragraphs it wrote and nothing is merged into its neighbour.
SLOW = "[fake:slow]"  # makes a piece take two seconds, in slices that look for Cancel: how a test keeps a reading "being made" long enough to act on it
ERROR = "[fake:error]"


def words(n: int, directive: str = "") -> str:
    return f"Paragraph {n}: the quick brown fox jumps over the lazy dog, again and again. {directive}".strip()


def part(title: str, first: int, count: int, directives: Optional[dict[int, str]] = None) -> dict[str, Any]:
    """A part of `count` paragraphs numbered from `first`. `directives` puts a fake-engine directive in the paragraph at that position (0-based)."""
    directives = directives or {}
    return {"title": title, "paragraphs": [{"kind": "paragraph", "text": words(first + i, directives.get(i, ""))} for i in range(count)]}


def reading_body(parts: Optional[list[dict[str, Any]]] = None, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"title": "A test reading", "source": {"kind": "paste"}, "engine": "fake", "voice": "anna",
                            "parts": parts if parts is not None else [part("One", 1, 3), part("Two", 4, 2)]}
    body.update(fields)
    return body


def make_reading(client, parts: Optional[list[dict[str, Any]]] = None, expect: int = 201, **fields: Any) -> dict[str, Any]:
    wait_for_engines(client)  # as the page does: the start-up check of the engines must have finished before a reading is asked for
    response = client.post("/api/readings", json=reading_body(parts, **fields))
    assert response.status_code == expect, response.text
    return response.json()


def wait_until(client, run_id: str, check: Callable[[dict[str, Any]], bool], what: str, timeout: float = 30.0) -> dict[str, Any]:
    """Poll the run until `check(run)` is true. For the moments a test must act on a reading in a particular state, not just wait for it to end."""
    deadline = time.monotonic() + timeout
    while True:
        run = client.get(f"/api/runs/{run_id}").json()
        if check(run):
            return run
        if time.monotonic() > deadline:
            raise AssertionError(f"reading {run_id} never reached: {what} (it is {run['status']!r}, {run['reading']['pieces_done']} pieces made)")
        time.sleep(0.02)


def finished_reading(client, parts: Optional[list[dict[str, Any]]] = None, **fields: Any) -> dict[str, Any]:
    run = wait_for(client, make_reading(client, parts, **fields)["id"])
    assert run["status"] == "done", run
    return run


# ---------------------------------------------------------------- making a reading
def test_a_reading_is_queued_as_one_entry_that_says_what_it_will_be(client_factory):
    client = client_factory(fake_step_delay_ms=20)
    created = make_reading(client)
    assert created["mode"] == "speech" and created["hidden"] is False and created["prompt"] == "A test reading"
    reading = created["reading"]
    assert (reading["engine"], reading["variant"], reading["parts_total"], reading["pieces_total"]) == ("fake", "default", 2, 5)
    assert reading["voice"]["kind"] == "preset" and reading["voice"]["id"] == "anna" and reading["voice"]["label"] == "Anna"
    assert reading["source"] == {"kind": "paste"} and reading["licence"]["name"] == "Test only"
    assert [(p["title"], p["pieces"]) for p in reading["parts"]] == [("One", 3), ("Two", 2)]
    assert reading["seconds_total"] > 0 and created["created_at"].endswith("Z")
    # one entry in the queue however many parts it has (criterion 267): counted once, in the Speech tab only
    assert len(client.get("/api/runs", params={"kind": "speech"}).json()["runs"]) == 1
    assert client.get("/api/runs", params={"kind": "image"}).json()["runs"] == []
    counts = client.get("/api/runs/counts").json()
    assert counts["speech"]["all"] == 1 and counts["image"]["all"] == 0


def test_it_is_made_part_by_part_and_ends_done_with_every_piece_made(client):
    run = finished_reading(client)
    reading = run["reading"]
    assert reading["pieces_done"] == reading["pieces_total"] == 5 and reading["parts_done"] == reading["parts_total"] == 2
    assert [p["state"] for p in reading["parts"]] == ["done", "done"] and all(p["seconds"] > 0 for p in reading["parts"])
    assert reading["seconds_made"] == pytest.approx(sum(p["seconds"] for p in reading["parts"]), abs=0.05)
    assert run["error"] is None and run["queue_position"] is None and run["progress"] is None and reading["waiting"] is False
    # the server measured how fast the engine works and keeps it, to tell the next reading how long it will take
    assert client.app.state.jobs.speech.store.speed("fake", "default") > 0


def test_a_reading_reaches_the_page_as_events_as_it_goes(client):
    """The run is announced when it is queued and again as each piece is made, so the page's card fills in without asking."""
    events: list[tuple[str, dict[str, Any]]] = []
    jobs = client.app.state.jobs
    original = jobs.bus.publish

    def spy(kind: str, payload: Any = None) -> None:
        events.append((kind, payload))
        original(kind, payload)

    jobs.bus.publish = spy  # type: ignore[method-assign]
    run = finished_reading(client)
    kinds = [kind for kind, _ in events]
    assert kinds.count("run.created") == 1 and "reading.piece" in kinds
    pieces = [payload for kind, payload in events if kind == "reading.piece"]
    assert len(pieces) == 5 and {(p["run_id"], p["part"]) for p in pieces} == {(run["id"], 0), (run["id"], 1)}
    updates = [p["reading"]["pieces_done"] for kind, p in events if kind == "run.updated" and p["id"] == run["id"] and p.get("reading")]
    assert updates and updates[-1] == 5 and updates == sorted(updates)  # it only ever goes forward


def test_the_pieces_are_stored_where_the_worker_was_told_to_put_them(client):
    run = finished_reading(client)
    storage = client.app.state.jobs.storage
    files = sorted(p.relative_to(storage.speech).as_posix() for p in (storage.speech / "readings" / run["id"]).rglob("*.wav"))
    assert files == ["readings/%s/0000/%05d.wav" % (run["id"], n) for n in range(3)] + ["readings/%s/0001/%05d.wav" % (run["id"], n) for n in range(2)]


# ---------------------------------------------------------------- other jobs slip in between the parts
def test_another_job_slips_in_between_two_parts_of_a_reading(client):
    """DESIGN.md §38.6 item 3 (criterion 268): a reading goes back to the queue after each part, behind whatever was queued while that part was made. The
    first piece of the first part is slow, so the picture is queued while the reading is still on part one."""
    reading = make_reading(client, [part("One", 1, 2, {0: SLOW}), part("Two", 3, 2)])
    wait_until(client, reading["id"], lambda r: r["status"] == "running", "running")
    picture = create_run(client, "a lighthouse")
    assert picture["status"] == "queued"
    finished_picture = wait_for(client, picture["id"])
    finished_reading_ = wait_for(client, reading["id"])
    assert finished_picture["status"] == "done" and finished_reading_["status"] == "done"
    # had the reading been made straight through, the picture would have waited for all of it
    assert finished_picture["finished_at"] < finished_reading_["finished_at"]
    assert finished_reading_["reading"]["pieces_done"] == 4


def test_a_reading_between_parts_is_queued_with_something_made_and_says_it_is_waiting(client):
    """Two readings share the one worker: when the first goes back to the queue after part one, the second is ahead of it."""
    first = make_reading(client, [part("One", 1, 1, {0: SLOW}), part("Two", 2, 1)], title="first")
    wait_until(client, first["id"], lambda r: r["status"] == "running", "running")
    # the second is slow, so the first stays "queued between its parts" for two seconds and the poll below cannot miss it (a quick one is over in a few
    # milliseconds, which is the whole point of the design and the reason this test must not rely on catching it)
    second = make_reading(client, [part("Only", 10, 1, {0: SLOW})], title="second")
    mid = wait_until(client, first["id"], lambda r: r["status"] == "queued" and r["reading"]["pieces_done"] == 1, "queued between its parts")
    assert mid["reading"]["waiting"] is True and mid["queue_position"] >= 1  # waiting for its turn, not "not started"
    done_second, done_first = wait_for(client, second["id"]), wait_for(client, first["id"])
    assert done_second["finished_at"] < done_first["finished_at"]  # the one that arrived in the meantime was made first


# ---------------------------------------------------------------- Cancel and Continue
def test_cancel_keeps_what_was_made_and_continue_makes_only_the_rest(client):
    reading = make_reading(client, [part("One", 1, 3, {2: SLOW})])
    run = wait_until(client, reading["id"], lambda r: r["reading"]["pieces_done"] >= 2, "two pieces made")
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code in (200, 202)
    canceled = wait_for(client, run["id"])
    assert canceled["status"] == "canceled" and canceled["reading"]["pieces_done"] == 2  # the two before the slow one are kept
    storage = client.app.state.jobs.storage
    made = {p.name: p.stat().st_mtime_ns for p in (storage.speech / "readings" / run["id"] / "0000").glob("*.wav")}
    assert sorted(made) == ["00000.wav", "00001.wav"]

    resumed = client.post(f"/api/readings/{run['id']}/continue")
    assert resumed.status_code == 200 and resumed.json()["status"] in ("queued", "running") and resumed.json()["error"] is None
    final = wait_for(client, run["id"])
    assert final["status"] == "done" and final["reading"]["pieces_done"] == 3
    after = {p.name: p.stat().st_mtime_ns for p in (storage.speech / "readings" / run["id"] / "0000").glob("*.wav")}
    assert all(after[name] == stamp for name, stamp in made.items())  # the pieces made before were not made again


def test_a_reading_canceled_while_queued_between_parts_is_canceled_at_once(client):
    first = make_reading(client, [part("One", 1, 1, {0: SLOW}), part("Two", 2, 1)])
    wait_until(client, first["id"], lambda r: r["status"] == "running", "running")
    blocker = make_reading(client, [part("Only", 10, 1, {0: SLOW})], title="blocker")  # keeps the worker busy once the first is back in the queue
    wait_until(client, first["id"], lambda r: r["status"] == "queued" and r["reading"]["pieces_done"] == 1, "queued between its parts")
    response = client.post(f"/api/runs/{first['id']}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "canceled"
    assert response.json()["reading"]["pieces_done"] == 1
    wait_for(client, blocker["id"])
    assert client.post(f"/api/readings/{first['id']}/continue").status_code == 200
    assert wait_for(client, first["id"])["reading"]["pieces_done"] == 2


@pytest.mark.parametrize("state", ["queued", "running", "done"])
def test_only_a_canceled_or_failed_reading_can_be_continued(client, state):
    if state == "done":
        run = finished_reading(client)
    else:
        make_reading(client, [part("Busy", 1, 1, {0: SLOW})], title="busy")  # the worker is taken, so the next one stays queued
        # (the subject is slow too: "running" is a state the test has to catch, and a quick reading is running for a few milliseconds, which a busy machine
        # can make shorter than the gap between two polls)
        run = make_reading(client, [part("One", 5, 1, {0: SLOW})], title="subject")
        if state == "running":
            run = wait_until(client, run["id"], lambda r: r["status"] == "running", "running")
    response = client.post(f"/api/readings/{run['id']}/continue")
    assert response.status_code == 409 and response.json()["code"] == "not_continuable"


def test_continue_on_something_that_is_not_a_reading_is_not_found(client):
    picture = wait_for(client, create_run(client)["id"])
    # (an id with dots and slashes is not tried: an HTTP client resolves those out of the URL before it is sent, so the server never sees them)
    for run_id in (picture["id"], "f" * 32, "x", "not-an-id", "G" * 32):
        response = client.post(f"/api/readings/{run_id}/continue")
        assert response.status_code == 404 and response.json()["code"] == "not_found", run_id


def test_continue_is_refused_when_the_queue_is_full(client_factory):
    client = client_factory(queue_cap=1, fake_step_delay_ms=1)
    failed = make_reading(client, [part("One", 1, 1, {0: ERROR})], title="will fail")
    assert wait_for(client, failed["id"])["status"] == "failed"
    busy = make_reading(client, [part("Busy", 5, 1, {0: SLOW})], title="busy")
    wait_until(client, busy["id"], lambda r: r["status"] == "running", "running")  # the worker is taken, so the next one waits in the queue
    make_reading(client, [part("Waiting", 9, 1)], title="waiting")
    refused = client.post(f"/api/readings/{failed['id']}/continue")
    assert refused.status_code == 429 and refused.json()["code"] == "queue_full"


# ---------------------------------------------------------------- a piece the engine fails on
def test_a_failed_reading_says_where_it_stopped_and_what_was_kept(client):
    run = wait_for(client, make_reading(client, [part("One", 1, 2, {1: ERROR}), part("Two", 3, 2)])["id"])
    assert run["status"] == "failed"
    message = run["error"]["message"]
    assert "fake speech engine was asked to fail" in message and "part 1, paragraph 2" in message
    assert "1 of 4 paragraphs were made and kept" in message and "Continue makes the rest" in message
    assert run["error"]["hint"]  # the engine's own hint comes through
    detail = client.get(f"/api/readings/{run['id']}/parts/0").json()
    assert [p["status"] for p in detail["pieces"]] == ["done", "queued"] and detail["complete"] is False
    assert detail["pieces"][1]["error"] and detail["pieces"][0]["error"] is None  # the reason is on the piece it happened to


def test_a_piece_can_be_corrected_and_the_reading_retried(client):
    run = wait_for(client, make_reading(client, [part("One", 1, 2, {1: ERROR})])["id"])
    assert run["status"] == "failed"
    fixed = client.patch(f"/api/readings/{run['id']}/parts/0/pieces/1", json={"text": "  The   words,\nmended and long enough to say.  "})
    assert fixed.status_code == 200 and fixed.json() == {"idx": 1, "text": "The words, mended and long enough to say."}
    detail = client.get(f"/api/readings/{run['id']}/parts/0").json()
    assert detail["pieces"][1]["text"] == "The words, mended and long enough to say." and detail["pieces"][1]["error"] is None
    assert detail["chars"] == sum(len(p["text"]) for p in detail["pieces"])  # the part's size follows its words
    assert client.post(f"/api/readings/{run['id']}/continue").status_code == 200
    final = wait_for(client, run["id"])
    assert final["status"] == "done" and final["error"] is None and final["reading"]["pieces_done"] == 2


def test_a_piece_that_is_made_cannot_be_changed(client):
    run = finished_reading(client)
    refused = client.patch(f"/api/readings/{run['id']}/parts/0/pieces/0", json={"text": "different words entirely"})
    assert refused.status_code == 409 and refused.json()["code"] == "already_made"


def test_no_piece_can_be_changed_while_the_reading_is_being_made(client):
    run = make_reading(client, [part("One", 1, 2, {0: SLOW})])
    wait_until(client, run["id"], lambda r: r["status"] == "running", "running")
    refused = client.patch(f"/api/readings/{run['id']}/parts/0/pieces/1", json={"text": "words for a piece that is next in line"})
    assert refused.status_code == 409 and refused.json()["code"] == "busy"
    client.post(f"/api/runs/{run['id']}/cancel")


@pytest.mark.parametrize("path,body,status,code", [
    ("parts/0/pieces/9", {"text": "no such piece"}, 404, "not_found"),
    ("parts/9/pieces/0", {"text": "no such part"}, 404, "not_found"),
    ("parts/0/pieces/0", {"text": "   \n  "}, 422, "empty"),
])
def test_a_correction_must_name_a_piece_that_is_there_and_say_something(client, path, body, status, code):
    run = wait_for(client, make_reading(client, [part("One", 1, 1, {0: ERROR})])["id"])  # failed, so its piece can be changed
    response = client.patch(f"/api/readings/{run['id']}/{path}", json=body)
    assert response.status_code == status and (status == 404 or response.json()["code"] == code)


def test_a_correction_to_a_picture_or_a_stranger_is_not_found(client):
    picture = wait_for(client, create_run(client)["id"])
    for run_id in (picture["id"], "e" * 32, "no/such"):
        assert client.patch(f"/api/readings/{run_id}/parts/0/pieces/0", json={"text": "words"}).status_code == 404


# ---------------------------------------------------------------- a restart in the middle of a reading
def test_a_reading_being_made_when_the_server_stops_fails_and_can_be_continued(client_factory, tmp_path):
    first = client_factory(fake_step_delay_ms=1)
    reading = make_reading(first, [part("One", 1, 3, {2: SLOW})])
    wait_until(first, reading["id"], lambda r: r["reading"]["pieces_done"] >= 2, "two pieces made")
    close(first)  # a graceful stop in the middle of a piece

    second = client_factory()
    after = second.get(f"/api/runs/{reading['id']}").json()
    assert after["status"] == "failed" and after["reading"]["pieces_done"] == 2  # what was made is still there
    assert not list((tmp_path / "data" / "speech").rglob("*.part"))  # and no half-written file
    assert second.post(f"/api/readings/{reading['id']}/continue").status_code == 200
    assert wait_for(second, reading["id"])["status"] == "done"


def test_a_reading_left_running_by_a_crash_is_failed_at_the_next_start(client_factory, tmp_path):
    first = client_factory()
    run = finished_reading(first)
    close(first)
    with sqlite3.connect(tmp_path / "data" / "studio.sqlite") as conn:
        conn.execute("UPDATE runs SET status='running', finished_at=NULL WHERE id=?", (run["id"],))
        conn.execute("UPDATE speech_pieces SET status='queued', path=NULL, frames=NULL, bytes=NULL WHERE run_id=? AND part_idx=1", (run["id"],))
    second = client_factory()
    crashed = second.get(f"/api/runs/{run['id']}").json()
    assert crashed["status"] == "failed" and crashed["error"]["message"] == "Interrupted by a server restart."
    assert crashed["reading"]["parts"][0]["state"] == "done" and crashed["reading"]["parts"][1]["state"] == "waiting"


# ---------------------------------------------------------------- the other tabs are untouched
def test_a_picture_run_has_neither_of_the_speech_keys(client):
    """The payloads of the other tabs are the contracts of 1.17 to 1.19 byte for byte; only a Speech run has `hidden` and `reading`."""
    picture = wait_for(client, create_run(client)["id"])
    assert "reading" not in picture and "hidden" not in picture
    assert client.get(f"/api/readings/{picture['id']}").status_code == 404  # and it is not a reading to the reading routes


def test_the_run_list_with_readings_in_it_keeps_the_order_newest_first(client):
    picture = wait_for(client, create_run(client)["id"])
    reading = finished_reading(client)
    listed = client.get("/api/runs").json()["runs"]
    assert [r["id"] for r in listed] == [reading["id"], picture["id"]] and "reading" in listed[0] and "reading" not in listed[1]


def test_utcnow_stamps_used_for_order_are_comparable_strings():
    """The tests above compare `finished_at` strings; that is right only while the stamps are fixed-width ISO-8601 in UTC."""
    first = utcnow()
    time.sleep(0.002)
    second = utcnow()
    assert len(first) == len(second) and first < second and first.endswith("Z")
    json.dumps({"stamp": first})
