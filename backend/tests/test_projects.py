"""Project folders, the server's side (DESIGN.md §32; criteria 147-151, 154-160, 162).

A project is a name that runs are filed under; filing a run also keeps it, and a filed run cannot stop being kept. Four layers are tested here:
the rules for a project's name (`projects.py`, pure), the database, the HTTP API with the events it sends, and the migration to schema 5.
The history filter's own cases (`?project=` and the counts within a project) are in `test_runfilter.py`, on the table the page reads too.
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import create_run, wait_for
from test_bin import bin_it, captured, columns, finished, sweep
from test_bin import v3_database as without_the_bin
from test_housekeeping import days_ago, on_disk, seed_run, seeded  # noqa: F401  (seeded is a fixture)
from test_music_db import RUN_A, RUN_B, make_v2

from studio.db import SCHEMA_VERSION, Database
from studio.presets import PROJECT_NAME_MAX
from studio.projects import BadProjectName, name_key, normalize_name

NOW = "2026-10-05T10:00:00.000Z"
NO_SUCH = "e" * 32  # a well-formed id that nothing has


# ------------------------------------------------------------------ the name of a project (projects.py)
# The name is trimmed and each run of white space becomes one space, so a name never differs from another only in how it was spaced.
@pytest.mark.parametrize("raw, shown", [
    ("Logo", "Logo"),
    ("  Logo  ", "Logo"),
    ("Logo   mark", "Logo mark"),
    ("Logo mark", "Logo mark"),  # a no-break space is white space too
    ("Ünïcode ✓ naïve", "Ünïcode ✓ naïve"),  # letters and symbols of any script are fine
])
def test_a_name_is_trimmed_and_its_white_space_made_single_spaces(raw, shown):
    assert normalize_name(raw) == shown


# Nothing left after trimming is not a name.
@pytest.mark.parametrize("raw", ["", "   ", "  "])
def test_a_name_with_nothing_in_it_is_refused(raw):
    with pytest.raises(BadProjectName, match="Give the project a name"):
        normalize_name(raw)


# The limit is on the name as it is stored (after trimming and collapsing), and it is inclusive: 60 characters pass, 61 do not.
def test_a_name_may_be_exactly_the_longest_and_no_longer():
    assert PROJECT_NAME_MAX == 60
    assert normalize_name("a" * 60) == "a" * 60
    assert normalize_name("  " + "a" * 60 + "  ") == "a" * 60  # the padding does not count
    with pytest.raises(BadProjectName, match="at most 60 characters; this one is 61"):
        normalize_name("a" * 61)


# A character that is not text could make two names look the same while being different (a zero-width space) or break the line the name is
# shown on (a newline), so it is refused, including a tab and a newline, which would otherwise be "collapsed" into a space without a word.
@pytest.mark.parametrize("raw", ["Lo\tgo", "Lo\ngo", "Lo\x00go", "Lo​go", "Lo‮go", "Logo"],
                         ids=["tab", "newline", "NUL", "zero-width space", "right-to-left override", "private use"])
def test_a_name_with_a_character_that_is_not_text_is_refused(raw):
    with pytest.raises(BadProjectName, match="letters, numbers, spaces and punctuation"):
        normalize_name(raw)


# What makes two names one project: the same words, ignoring case and spacing.
def test_two_names_are_the_same_project_when_only_case_or_spacing_differs():
    assert name_key("Logo") == name_key("  logo ") == name_key("LOGO") == name_key("Logo")
    assert name_key("Straße") == name_key("STRASSE")  # case folding, not just lower-casing
    assert name_key("Logo mark") == name_key("logo   MARK")


def test_names_that_differ_in_their_words_are_different_projects():
    assert name_key("Logo") != name_key("Logos")
    assert name_key("Logo mark") != name_key("Logomark")  # a space is a character


# ------------------------------------------------------------------ the database
# A music run, finished: `seed_run` makes picture runs. Enough of a row for the counts, which only look at the mode.
def seed_music(db: Database, n: int, age_days: float = 1) -> str:
    run_id = f"{n:032x}"
    assert db.insert_run_if_capacity({
        "id": run_id, "created_at": days_ago(age_days), "status": "done", "mode": "music", "prompt": f"track {n}",
        "effective_prompt": f"track {n}", "steps": 30, "seed": n, "num_images": 1, "model_id": "fake-music", "options_json": "{}",
    }, cap=10_000)
    return run_id


def make_project(db: Database, name: str, n: int) -> str:
    project_id = f"{n:032x}"
    assert db.create_project(project_id, name, NOW) == "created"
    return project_id


# Listed A to Z ignoring case (not in the order they were made), with the runs each holds on each tab, and a run in the bin is not counted
# (it is not on show). The counts are what the filter bar's drop-down shows.
def test_projects_are_listed_a_to_z_ignoring_case_with_their_runs_counted_by_tab(seeded):
    db, storage = seeded
    banana, apple, cherry = make_project(db, "banana", 1), make_project(db, "Apple", 2), make_project(db, "Cherry", 3)
    picture, other_picture, track, binned = seed_run(db, storage, 1, age_days=1), seed_run(db, storage, 2, age_days=1), seed_music(db, 3), seed_run(db, storage, 4, age_days=1)
    for run_id in (picture, other_picture, binned):
        assert db.file_run(run_id, apple) == "filed"
    assert db.file_run(track, apple) == "filed"
    assert db.file_run(seed_run(db, storage, 5, age_days=1), banana) == "filed"
    assert db.bin_run(binned, NOW) == "binned"
    listed = db.list_projects()
    assert [p["name"] for p in listed] == ["Apple", "banana", "Cherry"]
    assert {p["name"]: p["counts"] for p in listed} == {
        "Apple": {"image": 2, "music": 1},  # `binned` is in Apple but in the bin, so it is not counted
        "banana": {"image": 1, "music": 0},
        "Cherry": {"image": 0, "music": 0},  # empty, and still listed
    }
    assert [p["id"] for p in db.list_projects(apple)] == [apple] and db.list_projects(NO_SUCH) == []
    assert cherry  # made, never filed in


# The unique key is the name with its case and spacing folded away, so two requests at once cannot make "Logo" and "logo".
def test_a_name_taken_ignoring_case_or_spacing_makes_no_second_project(seeded):
    db, _ = seeded
    assert db.create_project("1" * 32, "Logo", NOW) == "created"
    for clash in ("logo", "LOGO", "  Logo  "):
        assert db.create_project("2" * 32, clash, NOW) == "taken"
    assert [p["name"] for p in db.list_projects()] == ["Logo"]
    assert db.create_project("2" * 32, "Logo mark", NOW) == "created"  # a different name is fine


# Renaming: to a name that is free; not to another project's name; and to a different spelling of its own, which is no clash with itself.
def test_a_project_can_be_renamed_but_not_to_a_taken_name(seeded):
    db, _ = seeded
    logo, pitch = make_project(db, "Logo", 1), make_project(db, "Pitch", 2)
    assert db.rename_project(logo, "Brand") == "renamed"
    assert db.rename_project(logo, "pitch") == "taken" and [p["name"] for p in db.list_projects()] == ["Brand", "Pitch"]  # untouched by the refusal
    assert db.rename_project(pitch, "PITCH") == "renamed"  # its own name, spelled differently
    assert [p["name"] for p in db.list_projects()] == ["Brand", "PITCH"]
    assert db.rename_project(NO_SUCH, "Anything") == "not_found"


# Filing keeps the run, in the same step; the same call moves a run that is filed; and the refusals say why (no run, no project, in the bin).
def test_filing_a_run_keeps_it_and_the_same_call_moves_it(seeded):
    db, storage = seeded
    logo, pitch = make_project(db, "Logo", 1), make_project(db, "Pitch", 2)
    run = seed_run(db, storage, 1, age_days=1)
    assert db.get_run(run)["pinned"] == 0 and db.get_run(run)["project_id"] is None
    assert db.file_run(run, logo) == "filed"
    assert (db.get_run(run)["pinned"], db.get_run(run)["project_id"]) == (1, logo)
    assert db.file_run(run, pitch) == "filed" and db.get_run(run)["project_id"] == pitch  # moved
    assert db.file_run(run, pitch) == "filed"  # already there is not an error
    assert db.file_run(NO_SUCH, logo) == "not_found"
    assert db.file_run(run, NO_SUCH) == "no_project"
    assert db.get_run(run)["project_id"] == pitch  # the refusals changed nothing


# A run that is queued or running can be filed too (it is kept when it finishes, as Keep works on a working run, §29.7 item 9).
@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_run_that_is_still_working_can_be_filed(seeded, status):
    db, storage = seeded
    logo = make_project(db, "Logo", 1)
    run = seed_run(db, storage, 1, age_days=0, status=status)
    assert db.file_run(run, logo) == "filed" and db.get_run(run)["pinned"] == 1


# A run in the bin cannot be filed (restore it first), and filing a run does not move it out of the bin or the other way.
def test_a_run_in_the_bin_cannot_be_filed(seeded):
    db, storage = seeded
    logo = make_project(db, "Logo", 1)
    run = seed_run(db, storage, 1, age_days=1)
    assert db.bin_run(run, NOW) == "binned"
    assert db.file_run(run, logo) == "in_bin"
    assert db.get_run(run)["project_id"] is None and db.get_run(run)["pinned"] == 0


# The one difference between taking a run out and Undo of a filing: `keep` (default True) leaves the run kept; keep=False also stops keeping it.
def test_taking_a_run_out_leaves_it_kept_unless_asked_not_to(seeded):
    db, storage = seeded
    logo = make_project(db, "Logo", 1)
    first, second = seed_run(db, storage, 1, age_days=1), seed_run(db, storage, 2, age_days=1)
    for run in (first, second):
        assert db.file_run(run, logo) == "filed"
    assert db.unfile_run(first) == "unfiled"
    assert (db.get_run(first)["project_id"], db.get_run(first)["pinned"]) == (None, 1)  # out, and still kept
    assert db.unfile_run(second, keep=False) == "unfiled"
    assert (db.get_run(second)["project_id"], db.get_run(second)["pinned"]) == (None, 0)  # out, and not kept: back as it was
    assert db.unfile_run(first) == "not_in_project"
    assert db.unfile_run(NO_SUCH) == "not_found"


# The lock: a filed run cannot stop being kept; being kept again is fine; once it is out of the project it can stop.
def test_a_filed_run_cannot_stop_being_kept(seeded):
    db, storage = seeded
    logo = make_project(db, "Logo", 1)
    run = seed_run(db, storage, 1, age_days=1)
    assert db.file_run(run, logo) == "filed"
    assert db.set_pinned(run, False) == "locked" and db.get_run(run)["pinned"] == 1
    assert db.set_pinned(run, True) == "ok"
    assert db.unfile_run(run) == "unfiled"
    assert db.set_pinned(run, False) == "ok" and db.get_run(run)["pinned"] == 0


# Deleting a project takes its runs out of it and nothing else: they stay kept, they are not deleted, and the runs that are in the bin lose the
# project too, so that a restore cannot put one back into a project that is gone. The ids come back oldest first, for the events.
def test_deleting_a_project_unfiles_its_runs_and_deletes_none(seeded):
    db, storage = seeded
    logo, pitch = make_project(db, "Logo", 1), make_project(db, "Pitch", 2)
    first, second, binned, elsewhere = (seed_run(db, storage, n, age_days=1) for n in (1, 2, 3, 4))
    for run in (first, second, binned):
        assert db.file_run(run, logo) == "filed"
    assert db.file_run(elsewhere, pitch) == "filed"
    assert db.bin_run(binned, NOW) == "binned"
    assert db.delete_project(logo) == [first, second, binned]
    assert [p["name"] for p in db.list_projects()] == ["Pitch"]
    for run in (first, second, binned):
        row = db.get_run(run)
        assert (row["project_id"], row["pinned"]) == (None, 1) and on_disk(storage, run)
    assert db.get_run(binned)["deleted_at"] == NOW  # still in the bin, now with no project
    assert db.get_run(elsewhere)["project_id"] == pitch  # another project's runs are not touched
    assert db.delete_project(logo) is None  # gone already


# A run keeps its project while it is in the bin, so Restore puts it back (§32.3 item 5); the filter then finds it there.
def test_a_run_keeps_its_project_through_the_bin_and_back(seeded):
    db, storage = seeded
    logo = make_project(db, "Logo", 1)
    run = seed_run(db, storage, 1, age_days=1)
    assert db.file_run(run, logo) == "filed"
    assert db.bin_run(run, NOW) == "binned"
    assert db.get_run(run)["project_id"] == logo and db.list_projects()[0]["counts"]["image"] == 0  # in the bin: not counted
    assert db.restore_run(run, NOW) == "restored"
    assert (db.get_run(run)["project_id"], db.get_run(run)["pinned"]) == (logo, 1)
    assert db.list_projects()[0]["counts"]["image"] == 1


# The clean-up never takes a filed run (criterion 151). It is kept, and the clean-up also says "not in a project", so the guarantee holds
# even for a run whose Keep flag has been cleared by hand in the database, which the API could never do.
@pytest.mark.parametrize("take", ["expire_to_bin", "delete_expired"])
def test_the_clean_up_never_takes_a_filed_run_even_when_its_keep_flag_was_cleared(seeded, take):
    db, storage = seeded
    logo = make_project(db, "Logo", 1)
    filed, plain = seed_run(db, storage, 1, age_days=400), seed_run(db, storage, 2, age_days=400)
    assert db.file_run(filed, logo) == "filed"
    with db.tx() as c:  # what the API refuses: a filed run that is not kept
        c.execute("UPDATE runs SET pinned=0 WHERE id=?", (filed,))
    cutoff = days_ago(30)
    taken = db.expire_to_bin(cutoff, NOW, 100) if take == "expire_to_bin" else db.delete_expired(cutoff, 100)
    assert taken == [plain]  # the unfiled, un-kept run of the same age is taken; the filed one is not
    assert db.get_run(filed) is not None and db.get_run(filed)["deleted_at"] is None


# ------------------------------------------------------------------ the API
def new_project(client, name: str = "Logo") -> dict:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def file_it(client, run_id: str, project_id: str):
    return client.put(f"/api/runs/{run_id}/project", json={"project_id": project_id})


def project_names(client) -> list[str]:
    return [p["name"] for p in client.get("/api/projects").json()["projects"]]


# What a project looks like to the page, and that making one tells every page (`projects.changed`) and costs nothing but a database row.
def test_making_a_project_answers_201_with_the_project_and_tells_every_page(client):
    with captured(client) as events:
        project = new_project(client, "  Logo   mark ")
    assert project["name"] == "Logo mark" and len(project["id"]) == 32 and project["counts"] == {"image": 0, "music": 0}
    assert set(project) == {"id", "name", "created_at", "counts"}
    assert client.get("/api/projects").json() == {"projects": [project]}
    assert [name for name, _ in events] == ["projects.changed"]


# A name that cannot be used is refused with the reason in words (422 `bad_name`), and nothing is made or announced.
@pytest.mark.parametrize("name", ["", "   ", "a" * 61, "Lo\tgo", "Lo​go"])
def test_a_bad_name_is_refused_with_a_reason_and_makes_nothing(client, name):
    with captured(client) as events:
        response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 422 and response.json()["code"] == "bad_name" and response.json()["detail"]
    assert project_names(client) == [] and events == []


def test_a_project_name_that_is_not_text_is_refused_by_validation(client):
    for body in ({"name": 5}, {"name": None}, {}, {"name": "x", "extra": 1}):
        assert client.post("/api/projects", json=body).status_code == 422


def test_a_name_taken_ignoring_case_is_409_name_taken(client):
    new_project(client, "Logo")
    for name in ("logo", "LOGO", " Logo "):
        response = client.post("/api/projects", json={"name": name})
        assert response.status_code == 409 and response.json()["code"] == "name_taken"
    assert project_names(client) == ["Logo"]


def test_projects_are_listed_a_to_z_ignoring_case(client):
    for name in ("banana", "Apple", "cherry"):
        new_project(client, name)
    assert project_names(client) == ["Apple", "banana", "cherry"]


def test_renaming_a_project_answers_with_it_and_tells_every_page(client):
    project = new_project(client, "Logo")
    with captured(client) as events:
        response = client.patch(f"/api/projects/{project['id']}", json={"name": "Brand"})
    assert response.status_code == 200 and response.json()["name"] == "Brand" and response.json()["id"] == project["id"]
    assert [name for name, _ in events] == ["projects.changed"]
    assert client.patch(f"/api/projects/{project['id']}", json={"name": "BRAND"}).status_code == 200  # its own name, another spelling
    other = new_project(client, "Pitch")
    clash = client.patch(f"/api/projects/{other['id']}", json={"name": "brand"})
    assert clash.status_code == 409 and clash.json()["code"] == "name_taken"
    assert client.patch(f"/api/projects/{other['id']}", json={"name": "x" * 61}).status_code == 422
    assert client.patch(f"/api/projects/{NO_SUCH}", json={"name": "Any"}).status_code == 404


# Filing: the run comes back kept and in the project; every page is told about the run (`run.updated`) and that the projects changed (the counts).
def test_filing_a_run_keeps_it_and_tells_every_page(client):
    run = finished(client)
    project = new_project(client)
    with captured(client) as events:
        response = file_it(client, run["id"], project["id"])
    assert response.status_code == 200
    body = response.json()
    assert (body["project_id"], body["pinned"], body["expires_at"]) == (project["id"], True, None)  # kept: it will never expire
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    assert [name for name, _ in events].count("projects.changed") == 1
    assert client.get("/api/projects").json()["projects"][0]["counts"] == {"image": 1, "music": 0}
    assert client.get(f"/api/runs/{run['id']}").json()["project_id"] == project["id"]


def test_the_same_call_moves_a_filed_run_to_another_project(client):
    run = finished(client)
    logo, pitch = new_project(client, "Logo"), new_project(client, "Pitch")
    assert file_it(client, run["id"], logo["id"]).status_code == 200
    moved = file_it(client, run["id"], pitch["id"])
    assert moved.status_code == 200 and moved.json()["project_id"] == pitch["id"]
    counts = {p["name"]: p["counts"]["image"] for p in client.get("/api/projects").json()["projects"]}
    assert counts == {"Logo": 0, "Pitch": 1}


# A music run is filed in the same projects: one list serves both tabs.
def test_a_music_run_is_filed_in_the_same_projects(client):
    track = wait_for(client, client.post("/api/runs", json={"mode": "music", "prompt": "Genre: ambient. A slow piano.", "options": {"duration": 30}}).json()["id"])
    project = new_project(client)
    assert file_it(client, track["id"], project["id"]).json()["pinned"] is True
    assert client.get("/api/projects").json()["projects"][0]["counts"] == {"image": 0, "music": 1}


def test_filing_names_what_is_missing(client):
    run = finished(client)
    project = new_project(client)
    assert file_it(client, NO_SUCH, project["id"]).status_code == 404
    assert file_it(client, run["id"], NO_SUCH).status_code == 404
    for bad in ("nope", "../x", "F" * 32):
        assert file_it(client, bad, project["id"]).status_code == 404  # a malformed run id is a run that is not there
        assert file_it(client, run["id"], bad).status_code == 404  # and so is a malformed project id
    assert client.put(f"/api/runs/{run['id']}/project", json={}).status_code == 422
    assert client.get(f"/api/runs/{run['id']}").json()["project_id"] is None  # nothing happened


# Both answers are 404, and the page tells them apart by the start of the message ("Run ..." or "Project ...", App.tsx fileInto) to say "That
# run no longer exists." or "That project no longer exists." So the words are part of the contract, and when the run AND the project are both
# unknown it is the run that is named (the run is looked for first, §32.4). Without this the wording could change and nothing would notice.
def test_filing_says_which_of_the_two_is_missing_in_words_the_page_relies_on(client):
    run = finished(client)
    project = new_project(client)
    assert file_it(client, NO_SUCH, project["id"]).json()["detail"].startswith("Run")
    assert file_it(client, run["id"], NO_SUCH).json()["detail"].startswith("Project")
    assert file_it(client, "nope", project["id"]).json()["detail"].startswith("Run")  # a malformed run id is a run that is not there
    assert file_it(client, run["id"], "nope").json()["detail"].startswith("Project")  # a malformed project id is a project that is not
    assert file_it(client, NO_SUCH, NO_SUCH).json()["detail"].startswith("Run")  # neither exists: the run is named
    assert file_it(client, "nope", NO_SUCH).json()["detail"].startswith("Run")  # the same when the run's id is malformed


# A run in the bin cannot be filed: 409 `run_in_bin`, and nothing changes.
def test_a_run_in_the_bin_cannot_be_filed(client):
    run = finished(client)
    project = new_project(client)
    assert bin_it(client, run["id"]).status_code == 200
    response = file_it(client, run["id"], project["id"])
    assert response.status_code == 409 and response.json()["code"] == "run_in_bin"
    assert client.get(f"/api/runs/{run['id']}").json()["project_id"] is None


# Taking a run out leaves it kept; `?keep=false` is Undo of a filing and puts the run back as it was. A run in no project is 409 `not_in_project`.
def test_taking_a_run_out_leaves_it_kept_and_keep_false_gives_the_old_state_back(client):
    run = finished(client)
    project = new_project(client)
    assert file_it(client, run["id"], project["id"]).status_code == 200
    with captured(client) as events:
        out = client.delete(f"/api/runs/{run['id']}/project")
    assert out.status_code == 200 and (out.json()["project_id"], out.json()["pinned"]) == (None, True)
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    assert [name for name, _ in events].count("projects.changed") == 1
    again = client.delete(f"/api/runs/{run['id']}/project")
    assert again.status_code == 409 and again.json()["code"] == "not_in_project"
    # Undo of a filing: filed, then taken out with keep=false: neither filed nor kept, which is what the run was before
    assert file_it(client, run["id"], project["id"]).status_code == 200
    undone = client.delete(f"/api/runs/{run['id']}/project", params={"keep": "false"})
    assert (undone.json()["project_id"], undone.json()["pinned"]) == (None, False)
    assert client.delete(f"/api/runs/{NO_SUCH}/project").status_code == 404
    assert client.delete("/api/runs/nope/project").status_code == 404


# The lock (criterion 150): Keep cannot be turned off on a filed run, with a code the page can act on; turning it on is fine; out of the project it can.
def test_a_filed_run_cannot_stop_being_kept(client):
    run = finished(client)
    project = new_project(client)
    assert file_it(client, run["id"], project["id"]).status_code == 200
    refused = client.patch(f"/api/runs/{run['id']}", json={"pinned": False})
    assert refused.status_code == 409 and refused.json()["code"] == "run_in_project" and "Take it out of the project" in refused.json()["detail"]
    assert client.get(f"/api/runs/{run['id']}").json()["pinned"] is True
    assert client.patch(f"/api/runs/{run['id']}", json={"pinned": True}).status_code == 200
    assert client.delete(f"/api/runs/{run['id']}/project").status_code == 200
    assert client.patch(f"/api/runs/{run['id']}", json={"pinned": False}).json()["pinned"] is False


# Deleting a project: the answer says how many runs it let go, each of them is announced as changed (so every page moves it), the projects are
# announced once, and the runs are neither deleted nor un-kept.
def test_deleting_a_project_unfiles_and_announces_its_runs_and_deletes_none(client):
    first, second = finished(client, "one"), finished(client, "two")
    project = new_project(client)
    for run in (first, second):
        assert file_it(client, run["id"], project["id"]).status_code == 200
    with captured(client) as events:
        response = client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 200 and response.json() == {"unfiled": 2}
    assert sorted(data["id"] for name, data in events if name == "run.updated") == sorted([first["id"], second["id"]])
    assert [name for name, _ in events].count("projects.changed") == 1 and [data for name, data in events if name == "run.deleted"] == []
    for run in (first, second):
        body = client.get(f"/api/runs/{run['id']}").json()
        assert (body["project_id"], body["pinned"], body["status"]) == (None, True, "done")
    assert client.get("/api/projects").json() == {"projects": []}
    assert client.delete(f"/api/projects/{project['id']}").status_code == 404
    assert client.delete("/api/projects/nope").status_code == 404


# A filed run deleted by hand goes to the bin as ever, keeps its project there, and Restore puts it back kept and in the project (§32.3 item 5).
def test_a_filed_run_goes_to_the_bin_with_its_project_and_comes_back_to_it(client):
    run = finished(client)
    project = new_project(client)
    assert file_it(client, run["id"], project["id"]).status_code == 200
    binned = bin_it(client, run["id"]).json()
    assert (binned["project_id"], binned["pinned"]) == (project["id"], True) and binned["deleted_at"] is not None
    assert client.get("/api/projects").json()["projects"][0]["counts"] == {"image": 0, "music": 0}  # not on show: not counted
    restored = client.post(f"/api/runs/{run['id']}/restore").json()
    assert (restored["project_id"], restored["pinned"], restored["deleted_at"]) == (project["id"], True, None)
    assert client.get("/api/projects").json()["projects"][0]["counts"] == {"image": 1, "music": 0}


# `?project=` on the list and the counts (criterion 153, 158): a project's id, `none`, or nothing; paging within the project; the counts follow it.
def test_the_list_and_the_counts_can_be_narrowed_to_a_project(client):
    filed, plain = finished(client, "filed run"), finished(client, "plain run")
    project = new_project(client)
    assert file_it(client, filed["id"], project["id"]).status_code == 200
    only = client.get("/api/runs", params={"project": project["id"]}).json()["runs"]
    assert [r["prompt"] for r in only] == ["filed run"]
    assert [r["prompt"] for r in client.get("/api/runs", params={"project": "none"}).json()["runs"]] == ["plain run"]
    assert len(client.get("/api/runs").json()["runs"]) == 2
    assert client.get("/api/runs/counts", params={"project": project["id"]}).json() == {
        "image": {"all": 1, "kept": 1, "deleted": 0, "deleted_items": 0}, "music": {"all": 0, "kept": 0, "deleted": 0, "deleted_items": 0}}
    assert client.get("/api/runs/counts", params={"project": "none"}).json()["image"] == {"all": 1, "kept": 0, "deleted": 0, "deleted_items": 0}
    assert client.get("/api/runs/counts").json()["image"] == {"all": 2, "kept": 1, "deleted": 0, "deleted_items": 0}  # no project: the whole tab, as ever
    assert plain  # (made, not filed)


# An id that is well formed but unknown is an empty list and zero counts, not an error: a page that remembers a project another page deleted
# just finds nothing (and knows the project is gone from its own list). A value that is not an id or `none` is refused before it reaches SQL.
def test_an_unknown_project_is_an_empty_list_and_a_malformed_one_is_a_422(client):
    finished(client)
    assert client.get("/api/runs", params={"project": NO_SUCH}).json() == {"runs": [], "next_before": None}
    assert client.get("/api/runs/counts", params={"project": NO_SUCH}).json()["image"] == {"all": 0, "kept": 0, "deleted": 0, "deleted_items": 0}
    for bad in ("", "x' OR 1=1 --", "NONE", "f" * 31, "F" * 32, "../x"):
        assert client.get("/api/runs", params={"project": bad}).status_code == 422, bad
        assert client.get("/api/runs/counts", params={"project": bad}).status_code == 422, bad


# The capabilities say how long a name may be, so the page can say it.
def test_the_capabilities_say_how_long_a_project_name_may_be(client):
    assert client.get("/api/capabilities").json()["limits"]["project_name_max"] == 60


# Like every route that changes something, the project routes refuse a request without the X-Studio-Client header.
def test_the_project_routes_need_the_client_header_like_every_change(client):
    run = finished(client)
    project = new_project(client)
    plain = client.__class__(client.app)  # no X-Studio-Client
    try:
        calls = [
            plain.post("/api/projects", json={"name": "Other"}),
            plain.patch(f"/api/projects/{project['id']}", json={"name": "Other"}),
            plain.delete(f"/api/projects/{project['id']}"),
            plain.put(f"/api/runs/{run['id']}/project", json={"project_id": project["id"]}),
            plain.delete(f"/api/runs/{run['id']}/project"),
        ]
        assert all(call.status_code in (400, 403) for call in calls), [call.status_code for call in calls]
    finally:
        plain.close()
    assert project_names(client) == ["Logo"]  # none of it happened


# ------------------------------------------------------------------ the daily clean-up
# The janitor, end to end (criterion 151): an old un-kept run is moved to the bin and an equally old filed run is not.
def test_the_daily_clean_up_leaves_a_filed_run_alone(seeded, client_factory):
    db, storage = seeded
    logo = make_project(db, "Logo", 1)
    old, filed = seed_run(db, storage, 1, age_days=40), seed_run(db, storage, 2, age_days=40)
    assert db.file_run(filed, logo) == "filed"
    db.close()
    client = client_factory(quiet=True)
    assert sweep(client) == 0
    assert client.get(f"/api/runs/{old}").json()["deleted_at"] is not None  # un-kept and unfiled: expired to the bin
    kept = client.get(f"/api/runs/{filed}").json()
    assert kept["deleted_at"] is None and kept["project_id"] == logo and kept["expires_at"] is None
    assert on_disk(storage, filed)


# ------------------------------------------------------------------ schema 5
# The current schema is 6 (1.15's images.deleted_at and tracks.deleted_at; 1.14 made it 5, the projects table and runs.project_id). When the next
# release changes it, this is the line to move.
def test_the_schema_is_6():
    assert SCHEMA_VERSION == 6


def tables_and_indexes(path) -> tuple[set[str], set[str]]:
    conn = sqlite3.connect(path)
    try:
        names = {kind: {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))} for kind in ("table", "index")}
        return names["table"], names["index"]
    finally:
        conn.close()


# Take what schema 5 added out of a database made by this code: the index and the column on runs, and the projects table. Used to make the
# databases that 1.12 and 1.13 left behind (a database "from 1.12" made by this code alone would still have a project column).
def without_projects(path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX idx_runs_project")
    conn.execute("ALTER TABLE runs DROP COLUMN project_id")
    conn.execute("DROP TABLE projects")
    conn.commit()
    conn.close()


# Take what schema 6 added out of a database made by this code: `deleted_at` on pictures and tracks, and the indexes on it. (A database "from 1.13" made
# by this code alone would still have them.)
def without_picture_bin(path) -> None:
    conn = sqlite3.connect(path)
    for table in ("images", "tracks"):
        conn.execute(f"DROP INDEX idx_{table}_deleted")
        conn.execute(f"ALTER TABLE {table} DROP COLUMN deleted_at")
    conn.commit()
    conn.close()


# A schema 3 database as 1.12 leaves it: no bin, no projects, and a run (the bin's own test helper makes the first two halves).
def v3_database(path) -> None:
    without_the_bin(path)  # makes the database with this code, then takes the bin's columns out, sets the version to 3 and adds a run
    without_projects(path)
    without_picture_bin(path)


# A schema 4 database as 1.13 leaves it: the bin, but no projects, and a run that is in the bin.
def v4_database(path) -> None:
    Database(path).close()
    without_projects(path)
    without_picture_bin(path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    conn.execute(
        "INSERT INTO runs (id, created_at, status, mode, prompt, effective_prompt, steps, seed, num_images, model_id, pinned, options_json, deleted_at) "
        "VALUES (?, '2026-10-01T10:00:00.000Z', 'done', 'generate', 'a lighthouse', 'a lighthouse', 20, 5, 1, 'm', 1, '{}', '2026-10-02T10:00:00.000Z')", ("a" * 32,))
    conn.commit()
    conn.close()


# Starting each old database (1.7 is schema 2, 1.12 is schema 3, 1.13 is schema 4) with this version: nothing is lost, a copy of the old one is
# made first and stays exactly as it was (the way back), and the new column and table work. (Criterion 159.)
@pytest.mark.parametrize("build, run_id, old_version", [(make_v2, RUN_A, "2"), (v3_database, "a" * 32, "3"), (v4_database, "a" * 32, "4")],
                         ids=["1.7 (schema 2)", "1.12 (schema 3)", "1.13 (schema 4)"])
def test_every_older_database_gets_projects_and_loses_nothing(tmp_path, build, run_id, old_version):
    path = tmp_path / "studio.sqlite"
    build(path)
    db = Database(path)
    try:
        run = db.get_run(run_id)
        assert run is not None and run["project_id"] is None and run["prompt"]
        assert db.create_project("1" * 32, "Logo", NOW) == "created"
        # the new column and table work: a run that was kept in the bin cannot be filed there; any other can
        assert db.file_run(run_id, "1" * 32) == ("in_bin" if run["deleted_at"] else "filed")
    finally:
        db.close()
    copy = tmp_path / f"studio.sqlite.before-schema-{SCHEMA_VERSION}"
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == old_version  # as it was
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= 1
    finally:
        conn.close()
    assert "project_id" not in dict(columns(copy))  # the way back has not been touched by the upgrade


# A migrated database ends up with the same tables, columns and indexes as a fresh one (criterion 159), and starting it again changes nothing
# (the way-back copy is not made again or overwritten).
@pytest.mark.parametrize("build", [make_v2, v3_database, v4_database], ids=["1.7", "1.12", "1.13"])
def test_a_migrated_database_is_the_same_as_a_fresh_one(tmp_path, build):
    migrated, fresh = tmp_path / "migrated.sqlite", tmp_path / "fresh.sqlite"
    build(migrated)
    Database(migrated).close()
    copy = tmp_path / f"migrated.sqlite.before-schema-{SCHEMA_VERSION}"
    first = copy.read_bytes()
    Database(migrated).close()
    assert copy.read_bytes() == first
    Database(fresh).close()
    assert tables_and_indexes(migrated) == tables_and_indexes(fresh)
    for table in ("runs", "images", "tracks", "run_inputs", "projects"):
        assert sorted(columns(migrated, table)) == sorted(columns(fresh, table)), table
    assert "idx_runs_project" in tables_and_indexes(migrated)[1]


def test_a_fresh_database_starts_with_no_projects_and_no_copy(tmp_path):
    path = tmp_path / "studio.sqlite"
    db = Database(path)
    try:
        assert db.list_projects() == []
    finally:
        db.close()
    assert not list(tmp_path.glob("*before-schema*"))
