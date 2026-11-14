"""Tags and notes on runs, through the API (DESIGN.md §45.7; criteria 401 to 403, 407, 408, 421): what the payload carries on every kind of run, how a tag and a note are cleaned
and limited, what is refused, what the other open pages are told, and that the word index follows every change and every way a run can go.

All against the fake pipeline. The words rule and the filters are the shared table's (`test_runfilter.py`) and `test_search_server.py`'s; here the question is whether what a person
WRITES on a run reaches the index and the other pages, and whether what a person DELETES leaves nothing behind."""

from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import create_run, wait_for
from test_db import run_row
from test_music_runs import create_music
from vector_server_support import drain, subscribe

from studio.db import Database
from studio.runfilter import RunFilter

BIN_NOW = "2026-10-02T00:00:00.000Z"


def made(client, prompt: str = "a lighthouse at dusk", **options) -> dict:
    """A finished picture run."""
    return wait_for(client, create_run(client, prompt, **options)["id"])


def search(client, text: str, **params) -> list[str]:
    """The prompts a search finds, newest first (the history, not the bin unless `deleted=true` is passed)."""
    response = client.get("/api/runs", params={"q": text, **params})
    assert response.status_code == 200, response.text
    return [run["prompt"] for run in response.json()["runs"]]


def put_tags(client, run_id: str, tags):
    return client.put(f"/api/runs/{run_id}/tags", json={"tags": tags})


def put_notes(client, run_id: str, notes: str):
    return client.put(f"/api/runs/{run_id}/notes", json={"notes": notes})


# ------------------------------------------------------------------------------------------------- the payload
def test_every_run_carries_tags_and_notes_empty_to_begin_with(client):
    """A page draws a tag row and a note on every kind of card, so the keys are on every run (not behind `mode` as the Speech and picture-only keys are)."""
    picture = made(client)
    assert picture["tags"] == [] and picture["notes"] == ""
    track = wait_for(client, create_music(client)["id"])
    assert track["tags"] == [] and track["notes"] == ""
    # and what the list and a single run say is the same thing
    listed = client.get("/api/runs").json()["runs"]
    assert {run["id"]: (run["tags"], run["notes"]) for run in listed} == {picture["id"]: ([], ""), track["id"]: ([], "")}


# ------------------------------------------------------------------------------------------------- tags
def test_tags_are_cleaned_each_once_and_shown_a_to_z(client):
    run = made(client)
    answer = put_tags(client, run["id"], ["  Winter  ", "FOX", "fox", "red  fox", "a,b", "", "   "])
    assert answer.status_code == 200
    # lower case, white space made one space, a comma becomes a space (the page splits on commas), the empty ones dropped, each once, A to Z
    assert answer.json()["tags"] == ["a b", "fox", "red fox", "winter"]
    assert client.get(f"/api/runs/{run['id']}").json()["tags"] == ["a b", "fox", "red fox", "winter"]


def test_putting_tags_replaces_them_and_an_empty_list_clears_them(client):
    run = made(client)
    put_tags(client, run["id"], ["one", "two"])
    assert put_tags(client, run["id"], ["two", "three"]).json()["tags"] == ["three", "two"]
    assert put_tags(client, run["id"], []).json()["tags"] == []


def test_a_tag_is_cut_at_30_characters_and_a_run_has_at_most_12(client):
    run = made(client)
    long_tag = "x" * 45
    assert put_tags(client, run["id"], [long_tag]).json()["tags"] == ["x" * 30]  # the Prompt builder's rule (§37.4): one word is one tag on a run and on a prompt
    twelve = [f"tag{n:02d}" for n in range(12)]
    assert len(put_tags(client, run["id"], twelve).json()["tags"]) == 12  # exactly the limit is fine
    over = put_tags(client, run["id"], [*twelve, "one too many"])
    assert over.status_code == 422 and over.json()["code"] == "too_many_tags" and "12" in over.json()["detail"]
    assert len(client.get(f"/api/runs/{run['id']}").json()["tags"]) == 12  # a refusal changes nothing
    # tags that are the same once cleaned count once, so thirteen typed as twelve is fine
    assert put_tags(client, run["id"], [*twelve, "TAG00", "tag00 "]).status_code == 200


def test_tags_must_be_a_list_of_text(client):
    run = made(client)
    for bad in ({"tags": "fox"}, {"tags": [1, 2]}, {"tags": None}, {}, {"tags": ["a"], "extra": 1}):
        assert client.put(f"/api/runs/{run['id']}/tags", json=bad).status_code == 422, bad
    # a request that carries far more than a run can hold is refused before anything is cleaned
    assert client.put(f"/api/runs/{run['id']}/tags", json={"tags": ["t"] * 500}).status_code == 422


def test_tags_of_a_run_that_is_not_there_are_404_and_of_a_run_in_the_bin_409(client):
    assert put_tags(client, "f" * 32, ["a"]).status_code == 404
    assert put_tags(client, "not an id", ["a"]).status_code == 404  # the same answer as for one that is not there
    assert put_notes(client, "f" * 32, "x").status_code == 404
    run = made(client)
    assert put_tags(client, run["id"], ["keep me"]).status_code == 200
    assert client.post(f"/api/runs/{run['id']}/bin").status_code == 200
    refused = put_tags(client, run["id"], ["new"])
    assert refused.status_code == 409 and refused.json()["code"] == "run_in_bin"
    assert put_notes(client, run["id"], "new").status_code == 409
    # it only reads: what it had is still shown there, and nothing changed
    assert client.get(f"/api/runs/{run['id']}").json()["tags"] == ["keep me"]
    # restore it, and it can be changed again
    assert client.post(f"/api/runs/{run['id']}/restore").status_code == 200
    assert put_tags(client, run["id"], ["new"]).status_code == 200


def test_a_run_that_is_still_queued_can_be_tagged_and_noted(client_factory):
    """A run that is waiting or working is not finished, and a person may still want to say what it is for (the Kept and project buttons work then too)."""
    client = client_factory(fake_step_delay_ms=60)  # slow steps, so that the second run is still waiting or working when it is tagged
    first = create_run(client, "first", steps=10)
    second = create_run(client, "second", steps=10)  # waits behind the first
    assert second["status"] == "queued"
    assert put_tags(client, second["id"], ["soon"]).status_code == 200 and put_notes(client, second["id"], "for later").status_code == 200
    wait_for(client, first["id"])
    done = wait_for(client, second["id"])
    assert done["tags"] == ["soon"] and done["notes"] == "for later"  # they survive the run finishing


# ------------------------------------------------------------------------------------------------- notes
def test_a_note_is_cleaned_and_kept_as_text(client):
    run = made(client)
    answer = put_notes(client, run["id"], "\n  Chosen for the poster.\r\nSecond line\twith a tab\x00 and a \x07bell\n\n")
    assert answer.status_code == 200
    # line breaks kept (as \n), the tab kept, the control characters gone, the ends trimmed
    assert answer.json()["notes"] == "Chosen for the poster.\nSecond line\twith a tab and a bell"
    # markup is only text: it is stored and returned as typed
    assert put_notes(client, run["id"], "<b>bold</b> <script>alert(1)</script>").json()["notes"] == "<b>bold</b> <script>alert(1)</script>"


def test_a_note_is_at_most_2000_characters_and_an_empty_one_clears_it(client):
    run = made(client)
    assert put_notes(client, run["id"], "n" * 2000).status_code == 200  # exactly the limit
    over = put_notes(client, run["id"], "n" * 2001)
    assert over.status_code == 422 and over.json()["code"] == "notes_too_long" and "2001" in over.json()["detail"]
    assert client.get(f"/api/runs/{run['id']}").json()["notes"] == "n" * 2000  # a refusal changes nothing
    assert put_notes(client, run["id"], "   \n ").json()["notes"] == ""  # only white space is no note
    assert client.get(f"/api/runs/{run['id']}").json()["notes"] == ""
    assert client.put(f"/api/runs/{run['id']}/notes", json={"notes": 5}).status_code == 422
    assert client.put(f"/api/runs/{run['id']}/notes", json={"notes": "x" * 20000}).status_code == 422  # far more than a note can be: refused before it is cleaned


def test_notes_and_tags_do_not_touch_what_matters_to_the_queue_or_to_keeping(client):
    run = made(client)
    before = client.get(f"/api/runs/{run['id']}").json()
    after = put_notes(client, run["id"], "a note").json()
    after = put_tags(client, run["id"], ["a tag"]).json()
    for key in ("created_at", "status", "pinned", "expires_at", "project_id", "images", "finished_at", "queue_position"):
        assert after[key] == before[key], key  # a tagged run that is not kept still expires, and nothing else moves (§45.4 rule 11)


# ------------------------------------------------------------------------------------------------- what other pages are told
def test_a_change_is_published_as_run_updated_with_the_run(client):
    run = made(client)
    sub = subscribe(client)
    put_tags(client, run["id"], ["fox"])
    put_notes(client, run["id"], "why")
    events = [(name, data) for name, data in drain(client, sub) if name == "run.updated" and data["id"] == run["id"]]
    assert [(data["tags"], data["notes"]) for _name, data in events] == [(["fox"], ""), (["fox"], "why")]  # each change, with the whole run as it is then
    # a refusal is not announced
    client.post(f"/api/runs/{run['id']}/bin")
    drain(client, sub)
    put_tags(client, run["id"], ["no"])
    assert [name for name, _data in drain(client, sub)] == []


# ------------------------------------------------------------------------------------------------- the list of tags in use
def test_the_tags_in_use_are_listed_most_used_first_and_the_bin_is_left_out(client):
    one, two, three = made(client, "one"), made(client, "two"), made(client, "three")
    put_tags(client, one["id"], ["fox", "winter"])
    put_tags(client, two["id"], ["fox", "night"])
    put_tags(client, three["id"], ["fox", "night", "bin only"])
    assert client.get("/api/tags").json() == {"tags": [{"tag": "fox", "count": 3}, {"tag": "night", "count": 2}, {"tag": "bin only", "count": 1}, {"tag": "winter", "count": 1}]}
    client.post(f"/api/runs/{three['id']}/bin")  # the autocomplete offers what the history uses, not what the bin holds
    assert client.get("/api/tags").json() == {"tags": [{"tag": "fox", "count": 2}, {"tag": "night", "count": 1}, {"tag": "winter", "count": 1}]}


# ------------------------------------------------------------------------------------------------- the index follows every change
def test_a_tag_and_a_note_are_found_as_soon_as_they_are_written_and_not_after_they_are_removed(client):
    run = made(client, "a lighthouse at dusk")
    assert search(client, "poster") == [] and search(client, "winter") == []
    put_notes(client, run["id"], "chosen for the poster")
    put_tags(client, run["id"], ["winter"])
    assert search(client, "poster") == ["a lighthouse at dusk"] and search(client, "winter") == ["a lighthouse at dusk"]
    assert search(client, "lighthouse poster winter") == ["a lighthouse at dusk"]  # all three places, one search
    put_tags(client, run["id"], [])
    put_notes(client, run["id"], "")
    assert search(client, "poster") == [] and search(client, "winter") == []
    assert search(client, "lighthouse") == ["a lighthouse at dusk"]  # its own words are still there: editing an annotation leaves the index of the rest as it was


def test_a_changed_tag_replaces_the_old_one_in_the_index(client):
    run = made(client)
    put_tags(client, run["id"], ["alpha"])
    put_tags(client, run["id"], ["beta"])
    assert search(client, "alpha") == [] and search(client, "beta") == ["a lighthouse at dusk"]


def test_a_run_in_the_bin_is_still_found_in_the_deleted_view_and_after_a_restore(client):
    run = made(client, "a red fox")
    put_tags(client, run["id"], ["hero"])
    client.post(f"/api/runs/{run['id']}/bin")
    assert search(client, "fox") == [] and search(client, "hero") == []  # not in the history
    assert search(client, "fox", deleted="true") == ["a red fox"] and search(client, "hero", deleted="true") == ["a red fox"]  # in the bin, by its words and its tags
    client.post(f"/api/runs/{run['id']}/restore")  # the index needed nothing: it never forgot the run
    assert search(client, "fox") == ["a red fox"] and search(client, "hero") == ["a red fox"]


def index_rows(client) -> set[str]:
    """The ids the word index holds."""
    db = client.app.state.db
    return {row["run_id"] for row in db.read_all("SELECT run_id FROM run_search")}


def annotation_rows(client, run_id: str) -> int:
    """How many rows of tags, notes and index a run still has: what a delete must leave at none."""
    db = client.app.state.db
    return sum(db.read_one(f"SELECT COUNT(*) AS n FROM {table} WHERE run_id=?", (run_id,))["n"] for table in ("run_tags", "run_notes", "run_search"))


def test_deleting_a_run_for_good_takes_its_tags_note_and_index_row(client):
    keep, gone = made(client, "keeper"), made(client, "doomed words")
    for run in (keep, gone):
        put_tags(client, run["id"], ["tagged"])
        put_notes(client, run["id"], "annotated")
    assert annotation_rows(client, gone["id"]) == 3
    assert client.delete(f"/api/runs/{gone['id']}").status_code == 204
    assert annotation_rows(client, gone["id"]) == 0 and index_rows(client) == {keep["id"]}
    assert search(client, "doomed") == [] and search(client, "tagged") == ["keeper"] and search(client, "annotated") == ["keeper"]
    assert annotation_rows(client, keep["id"]) == 3  # the other run is as it was


def test_delete_forever_from_the_bin_and_empty_bin_leave_nothing_behind(client):
    first, second, third = made(client, "first"), made(client, "second"), made(client, "third")
    for run in (first, second, third):
        put_tags(client, run["id"], ["tagged"])
    client.post(f"/api/runs/{first['id']}/bin")
    client.post(f"/api/runs/{second['id']}/bin")
    assert client.delete(f"/api/runs/{first['id']}").status_code == 204  # Delete forever, one run
    assert annotation_rows(client, first["id"]) == 0 and annotation_rows(client, second["id"]) == 2  # its tag and its index row (no note was written)
    assert client.delete("/api/bin").status_code == 200  # Empty bin
    assert annotation_rows(client, second["id"]) == 0 and index_rows(client) == {third["id"]}


def test_the_clean_up_that_expires_and_purges_leaves_nothing_behind(tmp_path):
    """The daily clean-up goes through `delete_expired` and `purge_bin`, which use the same one function that removes a run (`_delete_rows`); a test that goes through them directly, since a
    real clean-up waits for the clock."""
    db = Database(tmp_path / "studio.sqlite")
    try:
        for n in (1, 2, 3):
            assert db.insert_run_if_capacity(run_row(n, "done"), cap=10)
            assert db.set_run_tags(run_row(n)["id"], [f"tag{n}"]) == "ok" and db.set_run_notes(run_row(n)["id"], f"note{n}", BIN_NOW) == "ok"
        assert [row["prompt"] for row in db.list_runs(10, run_filter=RunFilter(q="tag1"))[0]] == ["p1"]
        assert db.delete_expired("2026-12-01T00:00:00.000Z", 10) == [run_row(1)["id"], run_row(2)["id"], run_row(3)["id"]]  # all are older than the cutoff and not kept
        for table in ("run_tags", "run_notes", "run_search"):
            assert db.read_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table
        # and the bin's own purge
        assert db.insert_run_if_capacity(run_row(4, "done"), cap=10) and db.set_run_tags(run_row(4)["id"], ["tag4"]) == "ok"
        assert db.bin_run(run_row(4)["id"], BIN_NOW) == "binned"
        assert db.purge_bin("2026-12-01T00:00:00.000Z", 10) == [run_row(4)["id"]]
        assert db.read_one("SELECT COUNT(*) AS n FROM run_search")["n"] == 0 and db.read_one("SELECT COUNT(*) AS n FROM run_tags")["n"] == 0
    finally:
        db.close()


def test_the_last_picture_of_a_run_deleted_for_good_takes_the_run_and_its_index_row(client):
    run = made(client, "only picture")
    put_tags(client, run["id"], ["tagged"])
    image_id = run["images"][0]["id"]
    assert client.delete(f"/api/images/{image_id}").status_code == 204  # the last picture: the whole run goes (§33.3)
    assert client.get(f"/api/runs/{run['id']}").status_code == 404
    assert annotation_rows(client, run["id"]) == 0 and search(client, "tagged") == []


def test_a_new_run_is_found_at_once_and_a_reading_by_its_title(client):
    run = made(client, "a brand new idea")
    assert search(client, "brand") == ["a brand new idea"]
    track = wait_for(client, create_music(client, "slow piano", lyrics="the moon is high")["id"])
    assert search(client, "moon") == ["slow piano"]  # the lyrics, which are not in the description


def test_a_tag_is_in_the_database_cleaned_and_a_hidden_job_cannot_be_tagged(tmp_path):
    db = Database(tmp_path / "studio.sqlite")
    try:
        hidden = {**run_row(1, "done"), "mode": "speech", "hidden": 1, "options_json": json.dumps({"task": "sample"})}
        assert db.insert_run_if_capacity(hidden, cap=10)
        assert db.read_one("SELECT COUNT(*) AS n FROM run_search")["n"] == 0  # a hidden job is not history: it is not indexed, so no search finds it
        assert db.set_run_tags(hidden["id"], ["x"]) == "not_found" and db.set_run_notes(hidden["id"], "x", BIN_NOW) == "not_found"
        assert db.set_run_tags("0" * 32, ["x"]) == "not_found"
    finally:
        db.close()


@pytest.mark.parametrize("table", ["run_tags", "run_notes"])
def test_the_foreign_keys_cascade_if_a_run_is_removed_by_hand(tmp_path, table):
    """`_delete_rows` removes the rows by hand, as it does for every table that hangs off a run; the foreign key is the second line of defence."""
    db = Database(tmp_path / "studio.sqlite")
    try:
        assert db.insert_run_if_capacity(run_row(1, "done"), cap=10)
        db.set_run_tags(run_row(1)["id"], ["a"])
        db.set_run_notes(run_row(1)["id"], "b", BIN_NOW)
        with db.tx() as c:
            c.execute("DELETE FROM runs WHERE id=?", (run_row(1)["id"],))
        assert db.read_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0
    finally:
        db.close()


def test_a_reading_is_found_by_its_title_and_the_name_of_its_source_and_can_be_tagged(client):
    """A reading is inserted by the Speech tab's own code (`SpeechStore.insert_reading`), not by `insert_run`; it must be indexed there too (DESIGN.md §45.7), by its title (the prompt
    column) and by the name of the file its text came from (in its options), and it carries tags and notes like every run."""
    from test_speech_readings import make_reading

    reading = make_reading(client, title="Moby Dick chapter one", source={"kind": "file", "name": "whale-book.epub"})
    assert reading["tags"] == [] and reading["notes"] == ""
    assert search(client, "moby") == ["Moby Dick chapter one"]  # its title
    assert search(client, "whale") == ["Moby Dick chapter one"] and search(client, "epub") == ["Moby Dick chapter one"]  # its source's name
    assert put_tags(client, reading["id"], ["audiobook"]).json()["tags"] == ["audiobook"]
    assert search(client, "audiobook") == ["Moby Dick chapter one"]
    wait_for(client, reading["id"])  # a reading that is being made cannot be deleted
    assert client.delete(f"/api/runs/{reading['id']}").status_code == 204
    assert search(client, "whale") == [] and annotation_rows(client, reading["id"]) == 0
