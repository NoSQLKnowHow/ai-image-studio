"""A single picture or track in the bin, the server's side (DESIGN.md §33; criteria 163-166, 168-174, 176).

The bin of §30 holds a whole run. Here it learns to hold ONE picture (or one track of a music run) as well: a deleted picture stays in the run's own
folders with its thumbnail and its 4K copies for `bin_days`, is not shown on the card, can be restored to its place, and is deleted for good by
the daily clean-up, by Delete forever, by Empty bin, or with its run. Its run's last picture is the exception: that sends the run. Layers tested here:
the database (rules, in one transaction each), the HTTP API (codes, payload, events), the files on disk, the clean-up's clock, and the migration to
schema 6. The history filter's cases (a run with something in the bin is in the Deleted view) are on the shared table in `test_runfilter.py`.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from conftest import create_edit, create_run, image_bytes, stage, wait_for
from PIL import Image
from test_bin import bin_it, captured, columns, sweep
from test_edit_runs import ref
from test_housekeeping import days_ago, seed_run, seeded  # noqa: F401  (seeded is a fixture)
from test_music_db import make_v2
from test_music_runs import create_music
from test_projects import v3_database, v4_database

from studio.db import SCHEMA_VERSION, Database
from studio.runfilter import COUNTED
from studio.serialize import parse_ts

NO_SUCH = "e" * 32  # a well-formed id that nothing has
NOW = "2026-10-05T10:00:00.000Z"


# ------------------------------------------------------------------ helpers
# A run with `count` pictures (or tracks) made the real way, through the fake pipeline, and waited for.
def made(client, count: int = 3, prompt: str = "a row of pictures") -> dict:
    run = wait_for(client, create_run(client, prompt, num_images=count)["id"])
    assert run["status"] == "done" and len(run["images"]) == count
    return run


def made_music(client, count: int = 3) -> dict:
    run = wait_for(client, create_music(client, tracks=count)["id"])
    assert run["status"] == "done" and len(run["tracks"]) == count
    return run


# the three routes for a picture, and the same three for a track
def bin_picture(client, picture_id: str):
    return client.post(f"/api/images/{picture_id}/bin")


def restore_picture(client, picture_id: str):
    return client.post(f"/api/images/{picture_id}/restore")


def delete_picture(client, picture_id: str):
    return client.delete(f"/api/images/{picture_id}")


def get_run(client, run_id: str) -> dict:
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


# What is on the disk for one picture: its file, its thumbnail, and its two kinds of 4K copy (the copies only if they have been made).
def files_of(client, picture_id: str) -> dict:
    row = client.app.state.db.get_image(picture_id)
    storage = client.app.state.storage
    return {"picture": storage.abs(row["path"]), "thumb": storage.abs(row["thumb_path"]),
            "4k": storage.four_k_path(row["path"]), "enlarged": storage.enlarged_path(row["path"])}


# Make the two copies of a picture beside it, as Make 4K and Enlarge would (a tiny real PNG each: the server reads their headers).
def add_copies(client, picture_id: str) -> None:
    for kind in ("4k", "enlarged"):
        Image.new("RGB", (16, 9), "blue").save(files_of(client, picture_id)[kind])


def exists(paths: dict) -> dict:
    return {name: path.exists() for name, path in paths.items()}


# Put a run into a state the API would not let us make (running, say), or a picture into the bin at a moment of our choosing.
def set_status(client, run_id: str, status: str) -> None:
    with client.app.state.db.tx() as c:
        c.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))


def set_deleted_at(client, table: str, item_id: str, when: str | None) -> None:
    with client.app.state.db.tx() as c:
        c.execute(f"UPDATE {table} SET deleted_at=? WHERE id=?", (when, item_id))


def code(response) -> str:
    return response.json()["code"]


# ------------------------------------------------------------------ the database, for pictures and for tracks
# A finished run with `count` pictures or tracks on disk, as a real one has (a picture, its thumbnail; a track is one file). Returns the run's id and
# the ids of its items in order. Items are made by hand so that the rules below can be tested without a worker.
def seed_items(db: Database, storage, n: int, count: int, *, kind: str = "image", status: str = "done") -> tuple[str, list[str]]:
    run_id = seed_run(db, storage, n, age_days=1, status=status)
    with db.tx() as c:
        c.execute("DELETE FROM images WHERE run_id=?", (run_id,))  # seed_run makes one picture; here the count is ours
        if kind == "track":
            c.execute("UPDATE runs SET mode='music' WHERE id=?", (run_id,))
    ids: list[str] = []
    for idx in range(count):
        item_id = f"{(n << 8) + idx + 1:032x}"
        if kind == "image":
            path = storage.run_dir(run_id) / f"{idx}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "red").save(path)
            thumb = storage.make_thumbnail(path, run_id, idx)
            db.add_image({"id": item_id, "run_id": run_id, "kind": "output", "idx": idx, "seed": idx, "width": 8, "height": 8, "has_alpha": 0,
                          "bytes": path.stat().st_size, "path": storage.rel(path), "thumb_path": storage.rel(thumb), "created_at": NOW})
        else:
            path = storage.audio_dir(run_id) / f"{idx}.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"RIFF")
            db.add_track({"id": item_id, "run_id": run_id, "idx": idx, "seed": idx, "seconds": 5.0, "sample_rate": 24000, "channels": 1,
                          "bytes": 4, "path": storage.rel(path), "created_at": NOW})
        ids.append(item_id)
    return run_id, ids


def present(db: Database, kind: str, run_id: str) -> list[int]:
    rows = (db.images_for_runs if kind == "image" else db.tracks_for_runs)([run_id])[run_id]
    return [row["idx"] for row in rows]


def binned(db: Database, kind: str, run_id: str) -> list[int]:
    rows = (db.binned_images_for_runs if kind == "image" else db.binned_tracks_for_runs)([run_id])[run_id]
    return [row["idx"] for row in rows]


# One item goes to the bin and the others stay, in their places; a second press changes nothing, not even the date; and a restore puts it back.
@pytest.mark.parametrize("kind", ["image", "track"])
def test_an_item_goes_to_the_bin_alone_and_comes_back_to_its_place(seeded, kind):
    db, storage = seeded
    run_id, ids = seed_items(db, storage, 1, 3, kind=kind)
    assert db.bin_picture(kind, ids[1], NOW) == ("picture", run_id)
    assert present(db, kind, run_id) == [0, 2] and binned(db, kind, run_id) == [1]  # the others keep their idx: nothing is renumbered in the database
    assert db.get_run(run_id)["deleted_at"] is None  # the run itself is in the history as ever
    assert db.bin_picture(kind, ids[1], "2026-10-09T10:00:00.000Z") == ("picture", run_id)  # again: it is there already
    row = (db.get_image if kind == "image" else db.get_track)(ids[1])
    assert row["deleted_at"] == NOW  # the first time counts
    assert db.restore_picture(kind, ids[1]) == ("restored", run_id)
    assert present(db, kind, run_id) == [0, 1, 2] and binned(db, kind, run_id) == []  # back between its neighbours
    assert db.restore_picture(kind, ids[1]) == ("not_in_bin", run_id)


# A run's last picture is not deleted by itself: the run goes to the bin, with the picture still in it (criterion 165). With one picture it is the first
# one; with three it is the third, after two were deleted alone, and those two stay in the bin on their own clocks.
@pytest.mark.parametrize("kind", ["image", "track"])
def test_the_last_item_sends_the_run_to_the_bin_instead(seeded, kind):
    db, storage = seeded
    only, [only_id] = seed_items(db, storage, 1, 1, kind=kind)
    assert db.bin_picture(kind, only_id, NOW) == ("run", only)
    assert db.get_run(only)["deleted_at"] == NOW and present(db, kind, only) == [0] and binned(db, kind, only) == []
    run_id, ids = seed_items(db, storage, 2, 3, kind=kind)
    assert db.bin_picture(kind, ids[0], NOW)[0] == "picture"
    assert db.bin_picture(kind, ids[1], NOW)[0] == "picture"
    assert db.bin_picture(kind, ids[2], NOW) == ("run", run_id)
    assert db.get_run(run_id)["deleted_at"] == NOW
    assert present(db, kind, run_id) == [2] and binned(db, kind, run_id) == [0, 1]
    assert db.restore_picture(kind, ids[0]) == ("run_in_bin", run_id)  # the run first (§33.2 item 6)
    assert db.restore_run(run_id, NOW) == "restored"
    assert db.restore_picture(kind, ids[0]) == ("restored", run_id)


# The refusals, each with its own word: nothing is changed by any of them.
@pytest.mark.parametrize("kind", ["image", "track"])
def test_an_item_that_cannot_go_to_the_bin_is_refused_and_nothing_changes(seeded, kind):
    db, storage = seeded
    assert db.bin_picture(kind, NO_SUCH, NOW) == ("not_found", None)
    assert db.restore_picture(kind, NO_SUCH) == ("not_found", None)
    for status in ("queued", "running"):
        run_id, ids = seed_items(db, storage, 3 if status == "queued" else 4, 2, kind=kind, status="done")
        with db.tx() as c:
            c.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        assert db.bin_picture(kind, ids[0], NOW) == ("not_finished", run_id)
        assert binned(db, kind, run_id) == []
    run_id, ids = seed_items(db, storage, 5, 2, kind=kind)
    assert db.bin_run(run_id, NOW) == "binned"
    assert db.bin_picture(kind, ids[0], NOW) == ("run_in_bin", run_id)  # a picture of a run in the bin is not deleted separately
    assert binned(db, kind, run_id) == []
    assert db.restore_picture(kind, ids[0]) == ("not_in_bin", run_id)  # a picture that was never in the bin is not in it, whatever its run is doing


# An edit's source images are rows of the same table but are not results: they cannot be deleted by themselves (§33.2, §33.4 item 4); and a staged
# upload, which no run owns, is not part of the history at all.
def test_an_edits_source_and_a_staged_upload_are_not_pictures_to_delete(seeded):
    db, storage = seeded
    run_id, ids = seed_items(db, storage, 1, 2)
    db.add_image({"id": "5" * 32, "run_id": run_id, "kind": "input", "idx": None, "seed": None, "width": 8, "height": 8, "has_alpha": 0, "bytes": 1,
                  "path": "inputs/x.png", "thumb_path": None, "created_at": NOW})
    db.add_image({"id": "6" * 32, "run_id": None, "kind": "input", "idx": None, "seed": None, "width": 8, "height": 8, "has_alpha": 0, "bytes": 1,
                  "path": "inputs/staged/y.png", "thumb_path": None, "created_at": NOW})
    for verb in (lambda i: db.bin_picture("image", i, NOW), lambda i: db.restore_picture("image", i), lambda i: db.delete_picture("image", i)):
        assert verb("5" * 32)[0] == "not_a_result"
        assert verb("6" * 32)[0] == "not_found"
    assert present(db, "image", run_id) == [0, 1]  # and the result pictures did not notice


# Deleting for good: a picture that is not the last goes alone; one in the bin goes whatever its run is doing; the last one takes the run with it
# (the caller removes the files). A picture that is present needs a finished run in the history, as sending it to the bin does.
@pytest.mark.parametrize("kind", ["image", "track"])
def test_deleting_an_item_for_good_takes_it_alone_unless_it_is_the_last(seeded, kind):
    db, storage = seeded
    run_id, ids = seed_items(db, storage, 1, 3, kind=kind)
    result, run, row = db.delete_picture(kind, ids[0])
    assert (result, run, row["id"]) == ("picture", run_id, ids[0]) and present(db, kind, run_id) == [1, 2]
    assert db.bin_picture(kind, ids[1], NOW)[0] == "picture"
    result, run, row = db.delete_picture(kind, ids[1])  # in the bin: just it
    assert (result, row["id"]) == ("picture", ids[1]) and binned(db, kind, run_id) == [] and present(db, kind, run_id) == [2]
    result, run, row = db.delete_picture(kind, ids[2])  # the last: the run goes
    assert (result, run) == ("run", run_id) and db.get_run(run_id) is None


@pytest.mark.parametrize("kind", ["image", "track"])
def test_deleting_for_good_is_refused_for_an_unfinished_run_and_for_a_present_item_of_a_run_in_the_bin(seeded, kind):
    db, storage = seeded
    run_id, ids = seed_items(db, storage, 1, 2, kind=kind)
    with db.tx() as c:
        c.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
    assert db.delete_picture(kind, ids[0]) == ("not_finished", run_id, None)
    with db.tx() as c:
        c.execute("UPDATE runs SET status='done' WHERE id=?", (run_id,))
    assert db.bin_run(run_id, NOW) == "binned"
    assert db.delete_picture(kind, ids[0]) == ("run_in_bin", run_id, None)
    assert present(db, kind, run_id) == [0, 1]
    assert db.delete_picture(kind, NO_SUCH) == ("not_found", None, None)


# The clean-up's side: everything whose time in the bin is over, wherever its run is, and nothing before the cutoff (the cutoff itself is not over
# yet); `limit` caps each of the two tables; no cutoff takes everything in the bin (Empty bin, or no bin at all).
def test_purging_takes_what_is_due_from_both_tables_and_wherever_the_run_is(seeded):
    db, storage = seeded
    run_id, ids = seed_items(db, storage, 1, 4)
    track_run, tracks = seed_items(db, storage, 2, 3, kind="track")
    for item, when in ((ids[0], "2026-09-01T00:00:00.000Z"), (ids[1], "2026-09-10T00:00:00.000Z"), (ids[2], "2026-10-01T00:00:00.000Z")):
        with db.tx() as c:
            c.execute("UPDATE images SET deleted_at=? WHERE id=?", (when, item))
    with db.tx() as c:
        c.execute("UPDATE tracks SET deleted_at='2026-09-02T00:00:00.000Z' WHERE id=?", (tracks[0],))
        c.execute("UPDATE tracks SET deleted_at='2026-10-02T00:00:00.000Z' WHERE id=?", (tracks[1],))
    assert db.bin_run(track_run, NOW) == "binned"  # a run in the bin: its tracks are still purged on their own clocks
    purged = db.purge_binned_items("2026-09-10T00:00:00.000Z", 100)  # strictly before this moment
    assert sorted((kind, row["id"]) for kind, row in purged) == sorted([("image", ids[0]), ("track", tracks[0])])
    assert binned(db, "image", run_id) == [1, 2] and binned(db, "track", track_run) == [1]
    assert len(db.purge_binned_items(None, 1)) == 2  # no cutoff: all of it, one of each table per call at limit 1
    assert len(db.purge_binned_items(None, 100)) == 1 and db.purge_binned_items(None, 100) == []


# The count beside Deleted counts things (§33.1): each run in the bin once, and each picture or track in it of a run that is in the history once.
# The pictures alone are `deleted_items`. One in a run that is itself in the bin is not counted (that run's card shows it).
def test_the_deleted_count_is_of_things_and_says_how_many_are_pictures(seeded):
    db, storage = seeded
    a, ids = seed_items(db, storage, 1, 3)
    b, _ = seed_items(db, storage, 2, 2)
    c_run, c_ids = seed_items(db, storage, 3, 2)
    assert db.bin_picture("image", ids[0], NOW)[0] == "picture" and db.bin_picture("image", ids[1], NOW)[0] == "picture"
    assert db.bin_run(b, NOW) == "binned"
    assert db.bin_picture("image", c_ids[0], NOW)[0] == "picture"
    assert db.bin_run(c_run, NOW) == "binned"  # c has a picture in the bin of its own AND is itself in the bin
    counts = db.run_counts(COUNTED)["image"]
    # a: 2 pictures; b: 1 run; c: 1 run (its own binned picture is not counted again)
    assert counts == {"all": 1, "kept": 0, "deleted": 4, "deleted_items": 2}


# ------------------------------------------------------------------ the API: sending a picture to the bin
# The whole life of one picture of four (criteria 163, 164, 166): it is out of `images` and into `binned_images` with its dates, the others keep their
# seeds and their place, nothing on the disk moves, the run is in the history and in the Deleted view, the file can still be opened (a read-only
# viewer needs it), and a `run.updated` goes to every page.
def test_a_picture_leaves_the_card_for_the_bin_and_everything_else_stays(client):
    run = made(client, 4)
    victim = run["images"][1]
    add_copies(client, victim["id"])
    before = {name: path.read_bytes() for name, path in files_of(client, victim["id"]).items()}
    with captured(client) as events:
        response = bin_picture(client, victim["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["moved"] == "picture" and body["run"]["id"] == run["id"]
    after = body["run"]
    assert [(p["idx"], p["seed"]) for p in after["images"]] == [(p["idx"], p["seed"]) for p in run["images"] if p["id"] != victim["id"]]  # same seeds
    [gone] = after["binned_images"]
    assert gone["id"] == victim["id"] and gone["seed"] == victim["seed"] and gone["thumb_url"] == victim["thumb_url"]
    days = client.app.state.jobs.settings.bin_days
    assert parse_ts(gone["purge_at"]) - parse_ts(gone["deleted_at"]) == timedelta(days=days)
    assert after["deleted_at"] is None and after["binned_tracks"] == []  # the run is in the history as ever
    assert get_run(client, run["id"])["binned_images"][0]["id"] == victim["id"]  # and any page that asks sees the same
    assert {name: path.read_bytes() for name, path in files_of(client, victim["id"]).items()} == before  # nothing was touched on the disk
    assert client.get(gone["url"]).status_code == 200 and client.get(gone["thumb_url"]).status_code == 200  # the viewer can still open it
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    assert [name for name, _ in events if name == "run.deleted"] == []
    history = client.get("/api/runs").json()["runs"]
    in_the_bin = client.get("/api/runs", params={"deleted": "true"}).json()["runs"]
    assert [r["id"] for r in history] == [r["id"] for r in in_the_bin] == [run["id"]]


# Criterion 165: deleting the last picture moves the whole run to the bin ("moved": "run"), the pictures of the run are all still there, and Undo (the
# run's own Restore) brings the run back: the pictures that were deleted alone before it come back as deleted pictures, on their own clocks.
def test_the_last_picture_sends_the_run_and_restoring_the_run_brings_the_earlier_ones_back_as_deleted(client):
    run = made(client, 3)
    first, second, last = (p["id"] for p in run["images"])
    assert bin_picture(client, first).json()["moved"] == "picture"
    assert bin_picture(client, second).json()["moved"] == "picture"
    response = bin_picture(client, last)
    assert response.status_code == 200 and response.json()["moved"] == "run"
    sent = response.json()["run"]
    assert sent["deleted_at"] is not None and [p["id"] for p in sent["images"]] == [last]
    assert sent["binned_images"] == []  # a run in the bin lists the pictures it has, and not the ones deleted before it (§33.2 item 5)
    assert client.post(f"/api/runs/{run['id']}/restore").status_code == 200
    back = get_run(client, run["id"])
    assert back["deleted_at"] is None and [p["id"] for p in back["images"]] == [last] and [p["id"] for p in back["binned_images"]] == [first, second]


def test_a_run_with_one_picture_goes_to_the_bin_with_it(client):
    run = made(client, 1)
    response = bin_picture(client, run["images"][0]["id"])
    assert response.json()["moved"] == "run" and response.json()["run"]["deleted_at"] is not None
    assert client.get("/api/runs").json()["runs"] == []


# A picture can be deleted from a kept run and from a run filed in a project: the run's own protections are not the picture's (§33.2 item 1); and the
# run stays kept and filed.
def test_a_picture_of_a_kept_and_filed_run_can_be_deleted_and_the_run_stays_so(client):
    run = made(client, 2)
    project = client.post("/api/projects", json={"name": "Logo"}).json()
    assert client.put(f"/api/runs/{run['id']}/project", json={"project_id": project["id"]}).status_code == 200
    assert bin_picture(client, run["images"][0]["id"]).status_code == 200
    kept = get_run(client, run["id"])
    assert kept["pinned"] is True and kept["project_id"] == project["id"] and len(kept["images"]) == 1


# ---- the refusals, each with its own code
def test_a_picture_of_a_run_that_has_not_finished_is_refused(client):
    run = made(client, 2)
    for status in ("queued", "running"):
        set_status(client, run["id"], status)
        response = bin_picture(client, run["images"][0]["id"])
        assert response.status_code == 409 and code(response) == "run_not_finished"
        assert delete_picture(client, run["images"][0]["id"]).status_code == 409
    set_status(client, run["id"], "done")
    assert get_run(client, run["id"])["binned_images"] == []  # nothing changed


def test_a_picture_of_a_run_in_the_bin_is_refused_and_so_is_restoring_it(client):
    run = made(client, 3)
    victim = run["images"][0]["id"]
    assert bin_picture(client, victim).status_code == 200
    assert bin_it(client, run["id"]).status_code == 200
    response = bin_picture(client, run["images"][1]["id"])
    assert response.status_code == 409 and code(response) == "run_in_bin"
    response = restore_picture(client, victim)
    assert response.status_code == 409 and code(response) == "run_in_bin"  # the run first
    assert delete_picture(client, run["images"][1]["id"]).status_code == 409  # a present picture of a run in the bin is not deleted alone
    assert client.post(f"/api/runs/{run['id']}/restore").status_code == 200
    assert restore_picture(client, victim).status_code == 200


def test_an_edits_source_picture_is_not_a_result(client):
    upload = stage(client, image_bytes((255, 0, 0)))
    run = wait_for(client, create_edit(client, "make it blue", [ref(upload)])["id"])
    source = run["inputs"][0]["id"]
    for response in (bin_picture(client, source), restore_picture(client, source), delete_picture(client, source)):
        assert response.status_code == 409 and code(response) == "not_a_result"
    assert client.get(f"/api/images/{source}").status_code == 200 and len(get_run(client, run["id"])["images"]) == 1  # all as it was
    assert bin_picture(client, upload["upload_id"]).status_code == 404  # a staged upload is no part of the history


# An edit's source images live in the same table as its results, but they are not pictures of the run: an edit's only result is its LAST picture,
# however many sources it was given, so deleting it sends the run (to the bin, or for good), and does not leave a run with nothing to show.
def test_the_only_result_of_an_edit_is_its_last_picture_whatever_its_sources(client):
    ups = [stage(client, image_bytes(color)) for color in ((255, 0, 0), (0, 255, 0))]
    run = wait_for(client, create_edit(client, "put them together", [ref(u) for u in ups])["id"])
    assert len(run["images"]) == 1 and len(run["inputs"]) == 2
    result = bin_picture(client, run["images"][0]["id"])
    assert result.json()["moved"] == "run" and result.json()["run"]["deleted_at"] is not None
    assert client.post(f"/api/runs/{run['id']}/restore").status_code == 200
    assert delete_picture(client, run["images"][0]["id"]).status_code == 204
    assert client.get(f"/api/runs/{run['id']}").status_code == 404  # for good, the run too


# A picture that Make 4K or Enlarge is working on, or has waiting, cannot be deleted: the copy being written would be left behind. The studio keeps
# the pictures in flight in two tables of its own (`_making_4k`, `_enlarging`); a picture in either is busy, one that is in neither is not.
@pytest.mark.parametrize("table", ["_making_4k", "_enlarging"])
def test_a_picture_that_is_being_made_bigger_cannot_be_deleted_until_it_is_done(client, table):
    run = made(client, 2)
    victim = run["images"][0]["id"]
    flight = getattr(client.app.state.jobs, table)
    flight[victim] = object()
    try:
        for response in (bin_picture(client, victim), delete_picture(client, victim)):
            assert response.status_code == 409 and code(response) == "image_busy"
        assert bin_picture(client, run["images"][1]["id"]).status_code == 200  # another picture of the same run is free
    finally:
        flight.pop(victim)
    assert bin_picture(client, victim).json()["moved"] == "run"  # (now the last one) free again


def test_unknown_and_malformed_ids_are_404(client):
    for verb in (bin_picture, restore_picture, delete_picture):
        assert verb(client, NO_SUCH).status_code == 404
        assert verb(client, "nope").status_code == 404
        assert verb(client, "../x").status_code in (404, 405)


# With the bin turned off there is nowhere to send a picture: bin_off (as for a run), and Delete forever is what the page asks for instead.
def test_with_no_bin_a_picture_is_deleted_for_good_at_once(client_factory):
    client = client_factory(bin_days=0)
    run = made(client, 2)
    victim = run["images"][0]["id"]
    response = bin_picture(client, victim)
    assert response.status_code == 409 and code(response) == "bin_off"
    paths = files_of(client, victim)
    assert delete_picture(client, victim).status_code == 204
    assert not any(exists(paths).values()) and [p["id"] for p in get_run(client, run["id"])["images"]] == [run["images"][1]["id"]]


# ------------------------------------------------------------------ restoring
def test_a_restored_picture_goes_back_to_its_own_place(client):
    run = made(client, 4)
    ids = [p["id"] for p in run["images"]]
    assert bin_picture(client, ids[1]).status_code == 200 and bin_picture(client, ids[2]).status_code == 200
    with captured(client) as events:
        response = restore_picture(client, ids[2])
    assert response.status_code == 200
    body = response.json()
    assert [p["id"] for p in body["images"]] == [ids[0], ids[2], ids[3]]  # between its neighbours, by its idx, with its seed
    assert [p["id"] for p in body["binned_images"]] == [ids[1]]
    assert [p["seed"] for p in body["images"]] == [run["images"][i]["seed"] for i in (0, 2, 3)]
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    again = restore_picture(client, ids[2])
    assert again.status_code == 409 and code(again) == "not_in_bin"


# ------------------------------------------------------------------ deleting for good
# Criterion 166: a picture's file, thumbnail and both 4K copies go together; the run's other pictures and the run stay; every page is told.
def test_delete_forever_removes_the_picture_with_its_thumbnail_and_copies_and_nothing_else(client):
    run = made(client, 3)
    ids = [p["id"] for p in run["images"]]
    for i in ids:
        add_copies(client, i)
    assert bin_picture(client, ids[1]).status_code == 200
    gone, kept = files_of(client, ids[1]), {i: files_of(client, i) for i in (ids[0], ids[2])}
    assert all(exists(gone).values())
    with captured(client) as events:
        assert delete_picture(client, ids[1]).status_code == 204
    assert not any(exists(gone).values())
    assert all(all(exists(paths).values()) for paths in kept.values())  # the neighbours are whole, copies included
    body = get_run(client, run["id"])
    assert [p["id"] for p in body["images"]] == [ids[0], ids[2]] and body["binned_images"] == []
    assert client.get(f"/api/images/{ids[1]}").status_code == 404
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    assert client.app.state.storage.run_dir(run["id"]).exists()  # the run's folder was not removed
    assert delete_picture(client, ids[1]).status_code == 404  # and it is gone for the second press


# A present picture that is not the last is deleted for good the same way (the bin is skipped: this is what the page asks for with no bin).
def test_a_present_picture_can_be_deleted_for_good_directly(client):
    run = made(client, 2)
    victim = run["images"][0]["id"]
    paths = files_of(client, victim)
    assert delete_picture(client, victim).status_code == 204
    assert not any(exists(paths).values()) and len(get_run(client, run["id"])["images"]) == 1


# The last picture deleted for good takes the run with it, files and all, and tells the pages the run is gone (`run.deleted`), as Delete forever on
# the run does.
def test_deleting_the_last_picture_for_good_deletes_the_run(client):
    run = made(client, 2)
    first, last = (p["id"] for p in run["images"])
    assert bin_picture(client, first).status_code == 200
    storage = client.app.state.storage
    with captured(client) as events:
        assert delete_picture(client, last).status_code == 204
    assert client.get(f"/api/runs/{run['id']}").status_code == 404
    assert not storage.run_dir(run["id"]).exists() and not (storage.thumbs / run["id"]).exists()  # the binned one went with it
    assert [data["id"] for name, data in events if name == "run.deleted"] == [run["id"]]
    assert client.get(f"/api/images/{first}").status_code == 404


# A run that is deleted for good takes its binned pictures' files too (§33.2 item 5): "everything of it goes".
def test_deleting_a_run_for_good_takes_its_pictures_in_the_bin_with_it(client):
    run = made(client, 3)
    victim = run["images"][0]["id"]
    assert bin_picture(client, victim).status_code == 200
    paths = files_of(client, victim)
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not any(exists(paths).values()) and client.get(f"/api/images/{victim}").status_code == 404


# ------------------------------------------------------------------ Empty bin and the clean-up
# Empty bin deletes the runs in it and the pictures in it of runs that stay, answers how many of each, and leaves the pictures that are not in the bin.
# A picture in the bin of a run that is itself in the bin goes with the run and is not counted on its own.
def test_empty_bin_deletes_runs_and_pictures_and_counts_each(client):
    staying = made(client, 3, "staying")
    leaving = made(client, 2, "leaving")
    s_ids, l_ids = [p["id"] for p in staying["images"]], [p["id"] for p in leaving["images"]]
    assert bin_picture(client, s_ids[0]).status_code == 200 and bin_picture(client, s_ids[1]).status_code == 200
    assert bin_picture(client, l_ids[0]).status_code == 200
    assert bin_it(client, leaving["id"]).status_code == 200  # `leaving` is in the bin and has a picture of its own in it
    for i in s_ids + l_ids:
        add_copies(client, i)  # every picture has its two copies, so "all there" and "all gone" mean the copies as well
    paths = {i: files_of(client, i) for i in s_ids + l_ids}
    with captured(client) as events:
        response = client.delete("/api/bin")
    assert response.status_code == 200 and response.json() == {"deleted": 1, "pictures": 2}
    assert all(exists(paths[s_ids[2]]).values())  # the one picture still on the card
    assert not any(any(exists(paths[i]).values()) for i in (s_ids[0], s_ids[1], *l_ids))
    body = get_run(client, staying["id"])
    assert [p["id"] for p in body["images"]] == [s_ids[2]] and body["binned_images"] == []
    assert client.get(f"/api/runs/{leaving['id']}").status_code == 404
    assert [d["id"] for n, d in events if n == "run.deleted"] == [leaving["id"]] and [d["id"] for n, d in events if n == "run.updated"] == [staying["id"]]
    assert client.delete("/api/bin").json() == {"deleted": 0, "pictures": 0}  # and nothing more to empty


# The daily clean-up deletes the pictures whose time is over (wherever their run is) and leaves the others: a picture 11 days in a 10-day bin goes,
# one 9 days in stays. With no bin it deletes every picture in it, as it deletes every run.
def test_the_clean_up_deletes_pictures_whose_days_are_over_and_no_others(client_factory):
    client = client_factory(quiet=True, bin_days=10)
    run = made(client, 4)
    ids = [p["id"] for p in run["images"]]
    for i in ids[:3]:
        assert bin_picture(client, i).status_code == 200
    set_deleted_at(client, "images", ids[0], days_ago(11))
    set_deleted_at(client, "images", ids[1], days_ago(9))
    set_deleted_at(client, "images", ids[2], days_ago(12))
    for i in ids:
        add_copies(client, i)  # so that "all there" and "all gone" mean each picture's copies as well
    paths = {i: files_of(client, i) for i in ids}
    with captured(client) as events:
        sweep(client)
    assert not any(any(exists(paths[i]).values()) for i in (ids[0], ids[2]))
    assert all(all(exists(paths[i]).values()) for i in (ids[1], ids[3]))
    body = get_run(client, run["id"])
    assert [p["id"] for p in body["binned_images"]] == [ids[1]] and [p["id"] for p in body["images"]] == [ids[3]]
    assert [d["id"] for n, d in events if n == "run.updated"] == [run["id"]]  # the pages that show the run are told once


def test_the_clean_up_deletes_the_pictures_in_the_bin_of_a_run_that_is_in_the_bin_too(client_factory):
    client = client_factory(quiet=True, bin_days=10)
    run = made(client, 3)
    ids = [p["id"] for p in run["images"]]
    assert bin_picture(client, ids[0]).status_code == 200
    assert bin_it(client, run["id"]).status_code == 200
    set_deleted_at(client, "images", ids[0], days_ago(11))  # its own days are over; the run's are not
    sweep(client)
    assert client.get(f"/api/images/{ids[0]}").status_code == 404
    assert get_run(client, run["id"])["deleted_at"] is not None and len(get_run(client, run["id"])["images"]) == 2  # the run and its others stay


def test_the_clean_up_with_no_bin_deletes_every_picture_that_is_in_it(client_factory):
    client = client_factory(quiet=True, bin_days=0)
    run = made(client, 3)
    ids = [p["id"] for p in run["images"]]
    set_deleted_at(client, "images", ids[0], days_ago(0))  # left over from when there was a bin
    paths = files_of(client, ids[0])
    sweep(client)
    assert not any(exists(paths).values()) and [p["id"] for p in get_run(client, run["id"])["images"]] == ids[1:]


# ------------------------------------------------------------------ the counts and the lists
def test_the_counts_follow_pictures_and_runs_in_the_bin(client):
    a, b = made(client, 3, "one"), made(client, 2, "two")
    assert client.get("/api/runs/counts").json()["image"] == {"all": 2, "kept": 0, "deleted": 0, "deleted_items": 0}
    assert bin_picture(client, a["images"][0]["id"]).status_code == 200
    assert bin_picture(client, a["images"][1]["id"]).status_code == 200
    assert client.get("/api/runs/counts").json()["image"] == {"all": 2, "kept": 0, "deleted": 2, "deleted_items": 2}  # a thing each
    assert bin_it(client, b["id"]).status_code == 200
    assert client.get("/api/runs/counts").json()["image"] == {"all": 1, "kept": 0, "deleted": 3, "deleted_items": 2}  # b is a thing too
    assert client.get("/api/runs/counts").json()["music"] == {"all": 0, "kept": 0, "deleted": 0, "deleted_items": 0}  # per tab
    # a project's own numbers: the same rule within it
    project = client.post("/api/projects", json={"name": "Logo"}).json()
    assert client.put(f"/api/runs/{a['id']}/project", json={"project_id": project["id"]}).status_code == 200
    assert client.get("/api/runs/counts", params={"project": project["id"]}).json()["image"] == {"all": 1, "kept": 1, "deleted": 2, "deleted_items": 2}
    assert client.get("/api/runs/counts", params={"project": "none"}).json()["image"] == {"all": 0, "kept": 0, "deleted": 1, "deleted_items": 0}


def test_the_deleted_list_shows_a_run_with_a_picture_in_the_bin_once_and_a_run_in_the_bin_once(client):
    a, b = made(client, 3, "has a deleted picture"), made(client, 2, "is deleted")
    assert bin_picture(client, a["images"][0]["id"]).status_code == 200
    assert bin_picture(client, b["images"][0]["id"]).status_code == 200  # b has one too...
    assert bin_it(client, b["id"]).status_code == 200  # ...and is in the bin itself: still one card
    deleted = client.get("/api/runs", params={"deleted": "true"}).json()["runs"]
    assert [r["id"] for r in deleted] == [b["id"], a["id"]]
    assert deleted[0]["binned_images"] == [] and len(deleted[1]["binned_images"]) == 1  # (§33.2 item 5)
    assert [r["id"] for r in client.get("/api/runs").json()["runs"]] == [a["id"]]


# ------------------------------------------------------------------ tracks
# The same three routes for a track, under /api/tracks, and a track's one file is removed when it is deleted for good.
def test_a_track_goes_to_the_bin_comes_back_and_is_deleted_for_good(client):
    run = made_music(client, 3)
    ids = [t["id"] for t in run["tracks"]]
    wav = client.app.state.storage.abs(client.app.state.db.get_track(ids[1])["path"])
    with captured(client) as events:
        response = client.post(f"/api/tracks/{ids[1]}/bin")
    assert response.status_code == 200 and response.json()["moved"] == "picture"
    body = response.json()["run"]
    assert [t["id"] for t in body["tracks"]] == [ids[0], ids[2]] and [t["id"] for t in body["binned_tracks"]] == [ids[1]]
    assert body["binned_tracks"][0]["purge_at"] is not None and body["binned_images"] == []
    assert wav.exists() and client.get(f"/api/audio/{ids[1]}").status_code == 200  # still playable, read-only
    assert [d["id"] for n, d in events if n == "run.updated"] == [run["id"]]
    restored = client.post(f"/api/tracks/{ids[1]}/restore")
    assert restored.status_code == 200 and [t["id"] for t in restored.json()["tracks"]] == ids
    assert client.post(f"/api/tracks/{ids[1]}/restore").status_code == 409
    assert client.post(f"/api/tracks/{ids[1]}/bin").status_code == 200
    assert client.delete(f"/api/tracks/{ids[1]}").status_code == 204
    assert not wav.exists() and client.get(f"/api/audio/{ids[1]}").status_code == 404
    assert [t["id"] for t in get_run(client, run["id"])["tracks"]] == [ids[0], ids[2]]


def test_the_last_track_sends_the_run_to_the_bin(client):
    run = made_music(client, 1)
    response = client.post(f"/api/tracks/{run['tracks'][0]['id']}/bin")
    assert response.json()["moved"] == "run" and response.json()["run"]["deleted_at"] is not None
    assert client.post(f"/api/tracks/{NO_SUCH}/bin").status_code == 404
    assert client.get("/api/runs/counts").json()["music"] == {"all": 0, "kept": 0, "deleted": 1, "deleted_items": 0}


def test_a_track_with_no_bin_is_refused_and_deleted_for_good(client_factory):
    client = client_factory(bin_days=0)
    run = made_music(client, 2)
    victim = run["tracks"][0]["id"]
    refused = client.post(f"/api/tracks/{victim}/bin")
    assert refused.status_code == 409 and code(refused) == "bin_off"
    assert client.delete(f"/api/tracks/{victim}").status_code == 204


def test_a_track_of_a_run_that_has_not_finished_is_refused(client):
    run = made_music(client, 2)
    set_status(client, run["id"], "running")
    refused = client.post(f"/api/tracks/{run['tracks'][0]['id']}/bin")
    assert refused.status_code == 409 and code(refused) == "run_not_finished"


# ------------------------------------------------------------------ schema 6
def test_the_schema_is_6():
    assert SCHEMA_VERSION == 6


def tables_and_indexes(path) -> tuple[set[str], set[str]]:
    conn = sqlite3.connect(path)
    try:
        names = {kind: {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))} for kind in ("table", "index")}
        return names["table"], names["index"]
    finally:
        conn.close()


# A schema 5 database as 1.14 leaves it: projects, but no `deleted_at` on pictures and tracks, with a run, two pictures and two tracks in it.
def v5_database(path) -> None:
    Database(path).close()
    conn = sqlite3.connect(path)
    for table in ("images", "tracks"):
        conn.execute(f"DROP INDEX idx_{table}_deleted")
        conn.execute(f"ALTER TABLE {table} DROP COLUMN deleted_at")
    conn.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
    conn.execute(
        "INSERT INTO runs (id, created_at, status, mode, prompt, effective_prompt, steps, seed, num_images, model_id, pinned, options_json) "
        "VALUES (?, '2026-10-01T10:00:00.000Z', 'done', 'generate', 'a lighthouse', 'a lighthouse', 20, 5, 2, 'm', 0, '{}')", ("a" * 32,))
    for n in (1, 2):
        conn.execute("INSERT INTO images (id, run_id, kind, idx, seed, width, height, has_alpha, bytes, path, created_at) "
                     "VALUES (?, ?, 'output', ?, ?, 8, 8, 0, 1, ?, '2026-10-01T10:00:00.000Z')", (f"{n:032x}", "a" * 32, n, n, f"images/a/{n}.png"))
        conn.execute("INSERT INTO tracks (id, run_id, idx, seed, seconds, sample_rate, channels, bytes, path, created_at) "
                     "VALUES (?, ?, ?, ?, 5.0, 24000, 1, 1, ?, '2026-10-01T10:00:00.000Z')", (f"{n + 16:032x}", "a" * 32, n, n, f"audio/a/{n}.wav"))
    conn.commit()
    conn.close()


# Starting each old database (1.7 is schema 2, 1.12 is 3, 1.13 is 4, 1.14 is 5) with this version: nothing is lost, a copy of the old one is made first
# and stays exactly as it was (the way back), and the new column works on what was already there. (Criterion 173.)
@pytest.mark.parametrize("build, old_version", [(make_v2, "2"), (v3_database, "3"), (v4_database, "4"), (v5_database, "5")],
                         ids=["1.7 (schema 2)", "1.12 (schema 3)", "1.13 (schema 4)", "1.14 (schema 5)"])
def test_every_older_database_gets_the_picture_bin_and_loses_nothing(tmp_path, build, old_version):
    path = tmp_path / "studio.sqlite"
    build(path)
    before = sqlite3.connect(path)
    pictures_before = before.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    before.close()
    db = Database(path)
    try:
        assert {"deleted_at"} <= {name for name, _ in columns(path, "images")} and {"deleted_at"} <= {name for name, _ in columns(path, "tracks")}
        assert len(db._all("SELECT id FROM images")) == pictures_before  # no picture was lost, and none is in the bin
        assert db._all("SELECT id FROM images WHERE deleted_at IS NOT NULL") == []
        if old_version == "5":  # 1.14's has a run with two pictures and two tracks: they can be sent to the bin and back, on what was already there
            assert db.bin_picture("image", f"{1:032x}", NOW) == ("picture", "a" * 32)
            assert [r["idx"] for r in db.images_for_runs(["a" * 32])["a" * 32]] == [2]
            assert db.restore_picture("image", f"{1:032x}") == ("restored", "a" * 32)
            assert db.bin_picture("track", f"{17:032x}", NOW) == ("picture", "a" * 32)
    finally:
        db.close()
    copy = tmp_path / f"studio.sqlite.before-schema-{SCHEMA_VERSION}"
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == old_version  # as it was
    finally:
        conn.close()
    assert "deleted_at" not in dict(columns(copy, "images")) and "deleted_at" not in dict(columns(copy, "tracks"))  # the way back is untouched


# A migrated database ends up with the same tables, columns and indexes as a fresh one (criterion 173), and starting it again changes nothing.
@pytest.mark.parametrize("build", [make_v2, v3_database, v4_database, v5_database], ids=["1.7", "1.12", "1.13", "1.14"])
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
    assert {"idx_images_deleted", "idx_tracks_deleted"} <= tables_and_indexes(migrated)[1]
