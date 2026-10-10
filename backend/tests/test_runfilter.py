"""History filters (DESIGN.md §29.5, §29.6; criteria 117, 120): the server's side. The cases are the table the page's own tests read too
(`filter_cases.json`), so the two cannot drift apart. Runs are inserted straight into the database with the states the table names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import create_run, wait_for

from studio.db import Database
from studio.runfilter import COUNTED, KINDS, RunFilter

TABLE = json.loads((Path(__file__).parent / "filter_cases.json").read_text())


def row(letter: str, seq: int, mode: str, status: str, pinned: bool, deleted: bool = False) -> dict:
    return {
        "id": f"{ord(letter):032x}", "created_at": f"2026-10-01T00:00:{seq:02d}Z", "status": status, "mode": mode,
        "prompt": f"run {letter}", "effective_prompt": f"run {letter}", "steps": 3, "seed": seq, "num_images": 1,
        "model_id": "fake-pipeline", "options_json": json.dumps({}), "width": 256, "height": 256, "pinned": int(pinned),
        "deleted_at": "2026-10-02T00:00:00.000Z" if deleted else None,
    }


def letter(run_id: str) -> str:
    return chr(int(run_id, 16))


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    # the table lists the runs newest first; they are made oldest first
    for seq, entry in enumerate(reversed(TABLE["runs"]), start=1):
        assert database.insert_run_if_capacity(row(entry["id"], seq, entry["mode"], entry["status"], entry["pinned"], entry["deleted"]), cap=100)
    yield database
    database.close()


def as_filter(spec: dict) -> RunFilter:
    return RunFilter(**spec)


def keep(client, run_id: str, kept: bool) -> None:
    assert client.patch(f"/api/runs/{run_id}", json={"pinned": kept}).status_code == 200


@pytest.mark.parametrize("case", TABLE["cases"], ids=[case["name"] for case in TABLE["cases"]])
def test_every_case_of_the_table_gives_the_listed_runs_in_order(db, case):
    rows, more = db.list_runs(100, None, as_filter(case["filter"]))
    assert [letter(r["id"]) for r in rows] == case["expect"] and not more


def test_the_counts_are_the_tables(db):
    assert db.run_counts(COUNTED) == TABLE["counts"]


def test_the_kinds_are_the_tabs():
    assert KINDS == {"image": ("generate", "edit"), "music": ("music",)}


def test_a_filter_with_nothing_set_adds_no_condition():
    assert RunFilter(deleted=None).conditions() == ([], [])
    assert RunFilter(deleted=None, kept=True).conditions() == (["pinned = ?"], [1])
    assert RunFilter(deleted=None, kept=False).conditions() == (["pinned = ?"], [0])
    assert RunFilter().conditions() == (["deleted_at IS NULL"], [])  # the history is what a filter means unless it says otherwise
    assert RunFilter(deleted=True).conditions() == (["deleted_at IS NOT NULL"], [])


def test_pages_continue_within_the_filter(db):
    first, more = db.list_runs(2, None, RunFilter(kept=True))
    assert [letter(r["id"]) for r in first] == ["f", "c"] and more
    second, more = db.list_runs(2, first[-1]["id"], RunFilter(kept=True))
    assert [letter(r["id"]) for r in second] == ["a"] and not more


def test_a_page_that_is_exactly_full_is_not_followed_by_an_empty_one(db):
    """Three runs are kept: a page of three has no next page, a page of two has."""
    rows, more = db.list_runs(3, None, RunFilter(kept=True))
    assert [letter(r["id"]) for r in rows] == ["f", "c", "a"] and not more
    rows, more = db.list_runs(2, f"{ord('f'):032x}", RunFilter(kept=True))  # after f: c and a, exactly a page
    assert [letter(r["id"]) for r in rows] == ["c", "a"] and not more


def test_the_cursor_may_be_a_run_the_filter_does_not_show(db):
    """A run un-kept since the page was loaded can still be the place the next page starts from."""
    rows, more = db.list_runs(100, f"{ord('d'):032x}", RunFilter(kept=True))  # d is not kept
    assert [letter(r["id"]) for r in rows] == ["c", "a"] and not more


def test_an_unknown_cursor_is_still_an_error(db):
    with pytest.raises(KeyError):
        db.list_runs(10, "f" * 32, RunFilter(kept=True))


def test_no_filter_is_what_it_was(db):
    rows, _ = db.list_runs(100)
    assert [letter(r["id"]) for r in rows] == ["f", "e", "d", "c", "b", "a"]
    rows, _ = db.list_runs(100, None, None)
    assert [letter(r["id"]) for r in rows] == ["f", "e", "d", "c", "b", "a"]


# ------------------------------------------------------------------ the API
def test_the_api_lists_kept_runs_only_and_pages_within_them(client):
    made = [create_run(client, f"keep me {n}" if n % 2 == 0 else f"let go {n}")["id"] for n in range(6)]
    for run_id in made:
        wait_for(client, run_id)
    for n in (0, 2, 4):
        keep(client, made[n], True)
    page = client.get("/api/runs", params={"kept": "true", "limit": 2}).json()
    assert [r["prompt"] for r in page["runs"]] == ["keep me 4", "keep me 2"] and page["next_before"] == made[2]
    rest = client.get("/api/runs", params={"kept": "true", "limit": 2, "before": page["next_before"]}).json()
    assert [r["prompt"] for r in rest["runs"]] == ["keep me 0"] and rest["next_before"] is None
    others = client.get("/api/runs", params={"kept": "false"}).json()["runs"]
    assert sorted(r["prompt"] for r in others) == ["let go 1", "let go 3", "let go 5"]
    everything = client.get("/api/runs").json()["runs"]
    assert len(everything) == 6


@pytest.mark.parametrize("value", ["maybe", "2", ""])
def test_an_unknown_value_for_kept_is_refused(client, value):
    assert client.get("/api/runs", params={"kept": value}).status_code == 422


def test_an_unknown_cursor_in_a_filtered_list_is_a_400(client):
    response = client.get("/api/runs", params={"kept": "true", "before": "f" * 32})
    assert response.status_code == 400 and response.json()["code"] == "bad_cursor"


def test_the_counts_say_how_many_runs_each_tab_holds_and_how_many_are_kept(client):
    first = create_run(client, "a picture")["id"]
    second = create_run(client, "another picture")["id"]
    music = client.post("/api/runs", json={"mode": "music", "prompt": "Genre: ambient. A slow piano.", "options": {"duration": 30}}).json()["id"]
    for run_id in (first, second, music):
        wait_for(client, run_id)
    assert client.get("/api/runs/counts").json() == {"image": {"all": 2, "kept": 0, "deleted": 0}, "music": {"all": 1, "kept": 0, "deleted": 0}}
    keep(client, first, True)
    keep(client, music, True)
    assert client.get("/api/runs/counts").json() == {"image": {"all": 2, "kept": 1, "deleted": 0}, "music": {"all": 1, "kept": 1, "deleted": 0}}
    keep(client, first, False)
    assert client.get("/api/runs/counts").json()["image"] == {"all": 2, "kept": 0, "deleted": 0}


def test_counts_is_not_mistaken_for_a_run_id(client):
    assert client.get("/api/runs/counts").status_code == 200
    assert client.get("/api/runs/" + "f" * 32).status_code == 404


def test_without_the_parameter_the_list_still_has_every_run_kept_or_not(client):
    made = [create_run(client, f"run {n}")["id"] for n in range(3)]
    for run_id in made:
        wait_for(client, run_id)
    keep(client, made[0], True)
    assert len(client.get("/api/runs").json()["runs"]) == 3
