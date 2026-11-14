"""History filters (DESIGN.md §29.5, §29.6, §32.4; criteria 117, 120, 162): the server's side. The cases are the table the page's own tests read
too (`filter_cases.json`), so the two cannot drift apart. Runs are inserted straight into the database with the states the table names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import create_run, wait_for

from studio.db import Database
from studio.runfilter import COUNTED, KINDS, NO_PROJECT, RunFilter, counted_within
from studio.runsearch import match_string, search_terms

TABLE = json.loads((Path(__file__).parent / "filter_cases.json").read_text())


def same_as_the_table(counts: dict, expected: dict) -> None:
    """The counts for every tab are the table's, exactly: the table lists a tab for each kind of run the server counts (Images, Music, Vector and, since the page has a Speech tab,
    Speech), so a kind the table forgot would be a count nothing checks."""
    assert counts == expected


# One run as the database wants it (a dict of columns). The letter names the run (a to f), `seq` fixes how old it is, `deleted` puts it in the bin,
# and `project` is the project it is filed in (the table's own short names, "x" and "y": the database takes any text there).
def row(letter: str, seq: int, mode: str, status: str, pinned: bool, deleted: bool = False, project: str | None = None, family: str | None = None, more: dict | None = None) -> dict:
    """One run as the database wants it. `more` is what the table says of the run besides its state (DESIGN.md §45): its words, lyrics, source, model, size and the moment it was made;
    each left out takes the default the table's `about` names (the words "run <letter>", a model called "fake-pipeline", 256 by 256, a moment from `seq`). Tags and notes are not
    columns: the fixture writes them through the database's own calls."""
    more = more or {}
    options = {"source": more["source"]} if "source" in more else {}  # a reading records where its text came from in its options (the index reads it there)
    return {
        # the letter of the run this one is a variation of, as that run's id (the first run of its family: DESIGN.md §43.4), or none
        "family_id": id_of(family) if family else None,
        "id": f"{ord(letter):032x}", "created_at": more.get("made", f"2026-10-01T00:00:{seq:02d}Z"), "status": status, "mode": mode,
        "prompt": more.get("prompt", f"run {letter}"), "effective_prompt": f"run {letter}", "steps": 3, "seed": seq, "num_images": 1,
        "model_id": more.get("model", "fake-pipeline"), "options_json": json.dumps(options), "width": more.get("width", 256), "height": more.get("height", 256), "pinned": int(pinned),
        "deleted_at": "2026-10-02T00:00:00.000Z" if deleted else None, "project_id": project, "lyrics": more.get("lyrics"),
    }


# the run's letter back from its id (an id is the letter's code point in hex, so ids sort and read easily)
def letter(run_id: str) -> str:
    return chr(int(run_id, 16))


# and the id of a letter, which is how a family in the table names its first run (a letter no run has is still an id: a family nobody is in)
def id_of(run_letter: str) -> str:
    return f"{ord(run_letter):032x}"


# the pictures (or tracks, for a music run; versions, for a vector run) of a run that are in the bin: how many the table says, each a row with its own `deleted_at`
def put_items_in_the_bin(database: Database, run: dict, count: int) -> None:
    for n in range(count):
        item_id = f"{(int(run['id'], 16) << 8) + n + 1:032x}"  # a 32-digit id of its own: the run's number, shifted, plus its place
        if run["mode"] == "vector":  # the items of a Vector run are its versions (DESIGN.md §35.4)
            database.add_vector({"id": item_id, "run_id": run["id"], "idx": n, "seed": n, "width_mm": 100.0, "height_mm": 100.0, "colours": 2, "layers": 1,
                                 "bytes": 10, "check_json": "{}", "svg_path": f"vector/{run['id']}/{n}.svg", "zip_path": f"vector/{run['id']}/{n}-layers.zip",
                                 "png_path": f"vector/{run['id']}/{n}.png", "created_at": run["created_at"], "deleted_at": "2026-10-03T00:00:00.000Z"})
        elif run["mode"] == "music":
            database.add_track({"id": item_id, "run_id": run["id"], "idx": n, "seed": n, "seconds": 5.0, "sample_rate": 24000, "channels": 1, "bytes": 10,
                                "path": f"audio/{run['id']}/{n}.wav", "created_at": run["created_at"], "deleted_at": "2026-10-03T00:00:00.000Z"})
        else:
            database.add_image({"id": item_id, "run_id": run["id"], "kind": "output", "idx": n, "seed": n, "width": 8, "height": 8, "has_alpha": 0,
                                "bytes": 10, "path": f"images/{run['id']}/{n}.png", "thumb_path": None, "created_at": run["created_at"],
                                "deleted_at": "2026-10-03T00:00:00.000Z"})


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    # the table lists the runs newest first; they are made oldest first
    for seq, entry in enumerate(reversed(TABLE["runs"]), start=1):
        made = row(entry["id"], seq, entry["mode"], entry["status"], entry["pinned"], entry["deleted"], entry["project"], entry.get("family"), entry)
        assert database.insert_run_if_capacity(made, cap=100)
        put_items_in_the_bin(database, made, entry["items_in_bin"])
        # the person's own words: tags and a note, written as the routes write them (a run in the bin cannot have them changed, and the table gives none to such a run)
        if entry.get("tags"):
            assert database.set_run_tags(made["id"], entry["tags"]) == "ok"
        if entry.get("notes"):
            assert database.set_run_notes(made["id"], entry["notes"], "2026-10-02T00:00:00.000Z") == "ok"
    yield database
    database.close()


# a case's filter, as written in the JSON table, becomes a RunFilter (a family is written there as the letter of its first run, and is that run's id here)
def as_filter(spec: dict) -> RunFilter:
    return RunFilter(**{**spec, **({"family": id_of(spec["family"])} if spec.get("family") else {})})


# Keep or un-keep a run through the API
def keep(client, run_id: str, kept: bool) -> None:
    assert client.patch(f"/api/runs/{run_id}", json={"pinned": kept}).status_code == 200


# Every case of the shared table: a filter, and the letters of the runs it must give, newest first. The page's own tests run the same
# table through its `matches()`, so the two sides cannot drift apart.
@pytest.mark.parametrize("case", TABLE["cases"], ids=[case["name"] for case in TABLE["cases"]])
def test_every_case_of_the_table_gives_the_listed_runs_in_order(db, case):
    rows, more = db.list_runs(100, None, as_filter(case["filter"]))
    assert [letter(r["id"]) for r in rows] == case["expect"] and not more


# the table also lists the counts the server must give for each tab
def test_the_counts_are_the_tables(db):
    same_as_the_table(db.run_counts(COUNTED), TABLE["counts"])


# ... and what they are within each project, and within "no project": the numbers on the filter bar follow the project chosen (§32.5)
@pytest.mark.parametrize("project", sorted(TABLE["counts_by_project"]))
def test_the_counts_within_a_project_are_the_tables(db, project):
    same_as_the_table(db.run_counts(counted_within(project)), TABLE["counts_by_project"][project])


# ... and within a search and the filters (criterion 414): the numbers on the filter bar describe what is on the screen, so each is the table's count for that scope
@pytest.mark.parametrize("entry", TABLE["counts_by_scope"], ids=[entry["name"] for entry in TABLE["counts_by_scope"]])
def test_the_counts_within_a_scope_are_the_tables(db, entry):
    same_as_the_table(db.run_counts(counted_within(**entry["scope"])), entry["counts"])


# a filter with no project is `COUNTED` itself, and a project is added to every counted filter without losing what each already says
def test_counted_within_narrows_every_counted_filter_to_the_project():
    assert counted_within(None) is COUNTED
    narrowed = counted_within("x")
    assert set(narrowed) == set(COUNTED)
    assert all(narrowed[name] == RunFilter(kept=COUNTED[name].kept, deleted=COUNTED[name].deleted, project="x") for name in COUNTED)


def test_the_kinds_are_the_tabs():
    assert KINDS == {"image": ("generate", "edit"), "music": ("music",), "vector": ("vector",), "speech": ("speech",)}  # Vector is the third tab (§31.12.7), Speech the fifth (§38.2: the fourth, Prompt builder, has no runs of its own)


# conditions() is the whole SQL contract: nothing set adds nothing, and kept=True or False binds 1 or 0
def test_a_filter_with_nothing_set_adds_no_condition():
    assert RunFilter(deleted=None).conditions() == ([], [])
    assert RunFilter(deleted=None, kept=True).conditions() == (["pinned = ?"], [1])
    assert RunFilter(deleted=None, kept=False).conditions() == (["pinned = ?"], [0])
    assert RunFilter().conditions() == (["deleted_at IS NULL"], [])  # the history is what a filter means unless it says otherwise
    # the bin is the runs in it, and the runs that have a picture or a track of their own in it (§33.3); it binds nothing
    clauses, values = RunFilter(deleted=True).conditions()
    assert values == [] and len(clauses) == 1
    assert "deleted_at IS NOT NULL" in clauses[0] and "FROM images" in clauses[0] and "FROM tracks" in clauses[0]


# the project is the one filter that holds a value: "none" is a fixed condition, an id is bound as a value and never pasted into the text
def test_a_project_is_one_fixed_condition_with_a_bound_value():
    assert RunFilter(deleted=None, project=NO_PROJECT).conditions() == (["project_id IS NULL"], [])
    assert RunFilter(deleted=None, project="x").conditions() == (["project_id = ?"], ["x"])
    # text that looks like SQL is still only a value: it matches no run and changes nothing else
    hostile = "x' OR 1=1 --"
    assert RunFilter(deleted=None, project=hostile).conditions() == (["project_id = ?"], [hostile])
    assert NO_PROJECT == "none"  # the word the API and the page use for "no project"


# paging inside a filter: the cursor continues among the matching runs only
def test_pages_continue_within_the_filter(db):
    first, more = db.list_runs(2, None, RunFilter(kept=True))
    assert [letter(r["id"]) for r in first] == ["r", "p"] and more  # (the two readings: they are kept from the start, §38.8)
    second, more = db.list_runs(2, first[-1]["id"], RunFilter(kept=True))
    assert [letter(r["id"]) for r in second] == ["j", "f"] and more
    third, more = db.list_runs(2, second[-1]["id"], RunFilter(kept=True))
    assert [letter(r["id"]) for r in third] == ["c", "a"] and more  # l, the oldest kept run, is on a page of its own
    fourth, more = db.list_runs(2, third[-1]["id"], RunFilter(kept=True))
    assert [letter(r["id"]) for r in fourth] == ["l"] and not more


def test_a_page_that_is_exactly_full_is_not_followed_by_an_empty_one(db):
    """Seven runs are kept (two of them readings): a page of seven has no next page, a page of six has."""
    rows, more = db.list_runs(7, None, RunFilter(kept=True))
    assert [letter(r["id"]) for r in rows] == ["r", "p", "j", "f", "c", "a", "l"] and not more
    rows, more = db.list_runs(6, None, RunFilter(kept=True))
    assert [letter(r["id"]) for r in rows] == ["r", "p", "j", "f", "c", "a"] and more
    rows, more = db.list_runs(3, f"{ord('f'):032x}", RunFilter(kept=True))  # after f: c, a and l, exactly a page
    assert [letter(r["id"]) for r in rows] == ["c", "a", "l"] and not more


def test_the_cursor_may_be_a_run_the_filter_does_not_show(db):
    """A run un-kept since the page was loaded can still be the place the next page starts from."""
    rows, more = db.list_runs(100, f"{ord('d'):032x}", RunFilter(kept=True))  # d is not kept
    assert [letter(r["id"]) for r in rows] == ["c", "a", "l"] and not more


def test_an_unknown_cursor_is_still_an_error(db):
    with pytest.raises(KeyError):
        db.list_runs(10, "f" * 32, RunFilter(kept=True))


def test_no_filter_is_what_it_was(db):
    # the history: the runs in the bin (k, m, q) are not in it; the runs with a picture, a track or a version in the bin (g, j, o) are
    rows, _ = db.list_runs(100)
    assert [letter(r["id"]) for r in rows] == ["r", "p", "o", "j", "g", "f", "e", "d", "c", "b", "a", "l", "n"]
    rows, _ = db.list_runs(100, None, None)
    assert [letter(r["id"]) for r in rows] == ["r", "p", "o", "j", "g", "f", "e", "d", "c", "b", "a", "l", "n"]


# ------------------------------------------------------------------ the API
def test_the_api_lists_kept_runs_only_and_pages_within_them(client):
    # make six runs whose prompts say which will be kept, and wait for them all
    made = [create_run(client, f"keep me {n}" if n % 2 == 0 else f"let go {n}")["id"] for n in range(6)]
    for run_id in made:
        wait_for(client, run_id)
    # keep every other one
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


# a value that is not true or false is refused by validation (422), never passed on to SQL
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
    none = {"all": 0, "kept": 0, "deleted": 0, "deleted_items": 0}  # nothing has been made on the Vector or Speech tab: they are counted, and count zero
    assert client.get("/api/runs/counts").json() == {"image": {"all": 2, "kept": 0, "deleted": 0, "deleted_items": 0},
                                                     "music": {"all": 1, "kept": 0, "deleted": 0, "deleted_items": 0}, "vector": none, "speech": none}
    keep(client, first, True)
    keep(client, music, True)
    assert client.get("/api/runs/counts").json() == {"image": {"all": 2, "kept": 1, "deleted": 0, "deleted_items": 0},
                                                     "music": {"all": 1, "kept": 1, "deleted": 0, "deleted_items": 0}, "vector": none, "speech": none}
    keep(client, first, False)
    assert client.get("/api/runs/counts").json()["image"] == {"all": 2, "kept": 0, "deleted": 0, "deleted_items": 0}


# /api/runs/counts must reach the counts handler, not be read as a run called "counts"; a real but unknown id is still a 404
def test_counts_is_not_mistaken_for_a_run_id(client):
    assert client.get("/api/runs/counts").status_code == 200
    assert client.get("/api/runs/" + "f" * 32).status_code == 404


def test_without_the_parameter_the_list_still_has_every_run_kept_or_not(client):
    made = [create_run(client, f"run {n}")["id"] for n in range(3)]
    for run_id in made:
        wait_for(client, run_id)
    keep(client, made[0], True)
    assert len(client.get("/api/runs").json()["runs"]) == 3
