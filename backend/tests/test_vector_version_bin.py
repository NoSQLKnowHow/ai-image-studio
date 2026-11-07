"""One version of a Vector run in the bin, and the two files the viewer shows beside it: the server's side of DESIGN.md §35 (criteria 188 to 192,
203 and 208).

Until 1.18 a Vector run was deleted whole: Delete took every version, and a run with several was one tall card. Now a version is binned, restored,
deleted for good and purged by the same code that does it for a picture or a track (the database rules are also run for versions in
test_picture_bin.py); this file tests what is particular to a version over HTTP: the three routes, the payload's `binned_vectors`, the four files
a version owns (its SVG, its ZIP, its PNG preview and the model's drawing), and the two routes the viewer needs (`/drawing` and `/layers/{n}.svg`).
The migration from schema 7 is here too, because the new column is the version's.

The clean-up that makes the versions is the fake job (tests/fake_vector_job.py), so none of this needs the engine or its libraries."""

from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from vector_server_support import CONTRACT, NO_SUCH, drain, make_vector, subscribe, vclient, vclient_factory, libraries_import  # noqa: F401 (fixtures)
from test_bin import bin_it, captured, columns, sweep
from test_housekeeping import days_ago

from studio.db import SCHEMA_VERSION, Database
from studio.serialize import parse_ts

SUFFIXES = (".svg", "-layers.zip", ".png", "-source.png")  # the four files of a version (§35.3 item 2)


# ------------------------------------------------------------------ helpers
def three(client, **options) -> dict:
    """A finished Vector run with three versions, seeds 100, 101 and 102."""
    run = make_vector(client, num_images=3, seed=100, **options)
    assert run["status"] == "done" and [v["seed"] for v in run["vectors"]] == [100, 101, 102]
    return run


def bin_version(client, vector_id: str):
    return client.post(f"/api/vectors/{vector_id}/bin")


def restore_version(client, vector_id: str):
    return client.post(f"/api/vectors/{vector_id}/restore")


def delete_version(client, vector_id: str):
    return client.delete(f"/api/vectors/{vector_id}")


def get_run(client, run_id: str) -> dict:
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def files_of(client, run: dict, vector: dict) -> dict:
    """The four files of a version, by suffix, as paths on the disk."""
    folder = client.app.state.storage.vector_dir(run["id"])
    return {suffix: folder / f"{vector['idx']}{suffix}" for suffix in SUFFIXES}


def exists(paths: dict) -> dict:
    return {suffix: path.exists() for suffix, path in paths.items()}


def set_status(client, run_id: str, status: str) -> None:
    """Put a run into a state the API would not let us make (a run still working, say)."""
    with client.app.state.db.tx() as c:
        c.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))


def set_deleted_at(client, vector_id: str, when: str) -> None:
    with client.app.state.db.tx() as c:
        c.execute("UPDATE vectors SET deleted_at=? WHERE id=?", (when, vector_id))


def code(response) -> str:
    return response.json()["code"]


# ------------------------------------------------------------------ sending one version to the bin
def test_a_version_leaves_the_card_for_the_bin_and_everything_else_stays(vclient):
    """The life of one version of three (criteria 188 and 189): it is out of `vectors` and into `binned_vectors` with its dates, the others keep their
    seeds and their places (the page numbers them again by position, the server never renumbers), nothing on the disk moves, the run is still in the
    history, the version's files can still be opened (the read-only viewer of the Deleted view needs them), and a `run.updated` goes to every page."""
    run = three(vclient)
    victim = run["vectors"][1]
    before = {suffix: path.read_bytes() for suffix, path in files_of(vclient, run, victim).items()}
    with captured(vclient) as events:
        response = bin_version(vclient, victim["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["moved"] == "version" and body["run"]["id"] == run["id"]
    after = body["run"]
    assert [v["seed"] for v in after["vectors"]] == [100, 102] and [v["idx"] for v in after["vectors"]] == [0, 2]
    assert [v["id"] for v in after["binned_vectors"]] == [victim["id"]]
    gone = after["binned_vectors"][0]
    assert gone["seed"] == 101 and gone["deleted_at"] and gone["layer_urls"] == victim["layer_urls"]  # a version as ever, with when it went...
    assert parse_ts(gone["purge_at"]) - parse_ts(gone["deleted_at"]) == timedelta(days=30)  # ...and when it will be gone for good (STUDIO_BIN_DAYS)
    assert after["deleted_at"] is None and after["status"] == "done"  # the run itself is in the history as ever
    assert {suffix: path.read_bytes() for suffix, path in files_of(vclient, run, victim).items()} == before  # nothing was touched on the disk
    for url in (victim["url"], victim["zip_url"], victim["png_url"], victim["drawing_url"], victim["layer_urls"][0]):
        assert vclient.get(url).status_code == 200, url
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    assert get_run(vclient, run["id"]) == after  # and a reload of the run says the same


def test_a_version_in_the_bin_has_the_contracts_keys(vclient):
    """The page's test data is built from `binned_vector_example` in the contract file: a version in the bin has exactly a version's keys and two more."""
    run = three(vclient)
    assert bin_version(vclient, run["vectors"][0]["id"]).status_code == 200
    binned = get_run(vclient, run["id"])["binned_vectors"][0]
    assert set(binned) == set(CONTRACT["binned_vector_example"]) == set(CONTRACT["run"]["vectors"][0]) | {"deleted_at", "purge_at"}
    assert get_run(vclient, run["id"])["vectors"][0]["id"] != binned["id"] and "deleted_at" not in get_run(vclient, run["id"])["vectors"][0]


def test_a_second_press_changes_nothing_not_even_the_date(vclient):
    run = three(vclient)
    victim = run["vectors"][0]["id"]
    assert bin_version(vclient, victim).status_code == 200
    first = get_run(vclient, run["id"])["binned_vectors"][0]["deleted_at"]
    set_deleted_at(vclient, victim, "2026-10-01T00:00:00.000Z")
    assert bin_version(vclient, victim).status_code == 200  # it is in the bin already: the answer is the same and the date is kept
    assert get_run(vclient, run["id"])["binned_vectors"][0]["deleted_at"] == "2026-10-01T00:00:00.000Z" != first


def test_restoring_a_version_puts_it_back_at_its_own_place(vclient):
    """Versions keep their order: the middle one deleted and restored is the middle one again (criterion 189), and Undo of the toast is this call."""
    run = three(vclient)
    middle = run["vectors"][1]
    assert bin_version(vclient, middle["id"]).status_code == 200
    with captured(vclient) as events:
        response = restore_version(vclient, middle["id"])
    assert response.status_code == 200
    after = response.json()
    assert [v["seed"] for v in after["vectors"]] == [100, 101, 102] and after["binned_vectors"] == []
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    assert restore_version(vclient, middle["id"]).status_code == 409 and code(restore_version(vclient, middle["id"])) == "not_in_bin"


def test_the_last_version_sends_the_run_to_the_bin_instead(vclient):
    """A run is never left with no version in the history (criterion 190): deleting the last one is Delete on the run, with its version still in it,
    and restoring the run brings the earlier ones back as deleted versions, which Restore then puts back."""
    run = three(vclient)
    a, b, c = (v["id"] for v in run["vectors"])
    assert bin_version(vclient, a).json()["moved"] == "version" and bin_version(vclient, b).json()["moved"] == "version"
    response = bin_version(vclient, c)
    assert response.status_code == 200 and response.json()["moved"] == "run"
    in_bin = response.json()["run"]
    assert in_bin["deleted_at"] and [v["id"] for v in in_bin["vectors"]] == [c] and in_bin["binned_vectors"] == []  # a run in the bin lists no binned versions
    assert restore_version(vclient, a).status_code == 409 and code(restore_version(vclient, a)) == "run_in_bin"  # the run first
    assert vclient.post(f"/api/runs/{run['id']}/restore").status_code == 200
    back = get_run(vclient, run["id"])
    assert [v["id"] for v in back["vectors"]] == [c] and [v["id"] for v in back["binned_vectors"]] == [a, b]
    assert restore_version(vclient, a).status_code == 200 and restore_version(vclient, b).status_code == 200
    assert [v["id"] for v in get_run(vclient, run["id"])["vectors"]] == [a, b, c]


def test_a_run_with_one_version_goes_to_the_bin_with_it(vclient):
    run = make_vector(vclient, num_images=1)
    response = bin_version(vclient, run["vectors"][0]["id"])
    assert response.status_code == 200 and response.json()["moved"] == "run"
    assert bin_it(vclient, run["id"]).status_code == 200  # (and the run can be put there again: it is there already)


def test_a_version_that_cannot_go_to_the_bin_is_refused_with_its_own_word(vclient):
    run = three(vclient)
    victim = run["vectors"][0]["id"]
    assert bin_version(vclient, NO_SUCH).status_code == 404 and restore_version(vclient, NO_SUCH).status_code == 404 and delete_version(vclient, NO_SUCH).status_code == 404
    assert bin_version(vclient, "not-an-id").status_code == 404
    set_status(vclient, run["id"], "running")
    refused = bin_version(vclient, victim)
    assert refused.status_code == 409 and code(refused) == "run_not_finished"
    assert refused.json()["detail"] == "This run has not finished. Wait for it to finish before deleting one of its versions."
    set_status(vclient, run["id"], "done")
    assert bin_it(vclient, run["id"]).status_code == 200
    refused = bin_version(vclient, victim)
    assert refused.status_code == 409 and code(refused) == "run_in_bin" and refused.json()["detail"] == "This run is in the bin. Restore the run first."
    assert vclient.post(f"/api/runs/{run['id']}/restore").status_code == 200
    refused = restore_version(vclient, victim)
    assert refused.status_code == 409 and code(refused) == "not_in_bin" and refused.json()["detail"] == "This version is not in the bin."
    assert len(get_run(vclient, run["id"])["vectors"]) == 3  # none of the refusals changed anything


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_a_failed_or_canceled_run_can_have_a_version_deleted(vclient, status):
    """A run that stopped may hold versions made before it stopped (§35.3 item 1): they are as deletable as a finished run's."""
    run = three(vclient)
    set_status(vclient, run["id"], status)
    assert bin_version(vclient, run["vectors"][0]["id"]).json()["moved"] == "version"


def test_with_no_bin_a_version_is_refused_and_deleted_for_good(vclient_factory):
    """STUDIO_BIN_DAYS=0 turns the bin off: the bin route says so, as for a run, and the page then asks to delete for good."""
    client = vclient_factory(bin_days=0)
    run = three(client)
    victim = run["vectors"][0]
    refused = bin_version(client, victim["id"])
    assert refused.status_code == 409 and code(refused) == "bin_off" and "STUDIO_BIN_DAYS=0" in refused.json()["detail"]
    assert delete_version(client, victim["id"]).status_code == 204
    assert [v["seed"] for v in get_run(client, run["id"])["vectors"]] == [101, 102]


def test_a_version_of_a_kept_and_filed_run_can_be_deleted_and_the_run_stays_so(vclient):
    """Deleting a version is not deleting the run: its Keep and its project stay (the question says the run stays in its project)."""
    run = three(vclient)
    project = vclient.post("/api/projects", json={"name": "Logo"}).json()
    assert vclient.put(f"/api/runs/{run['id']}/project", json={"project_id": project["id"]}).status_code == 200
    body = bin_version(vclient, run["vectors"][0]["id"]).json()["run"]
    assert body["pinned"] is True and body["project_id"] == project["id"] and body["deleted_at"] is None


# ------------------------------------------------------------------ deleting for good
def test_delete_forever_removes_the_four_files_of_the_version_and_nothing_else(vclient):
    run = three(vclient)
    victim, other = run["vectors"][1], run["vectors"][2]
    assert bin_version(vclient, victim["id"]).status_code == 200
    mine, theirs = files_of(vclient, run, victim), files_of(vclient, run, other)
    assert all(exists(mine).values())
    with captured(vclient) as events:
        assert delete_version(vclient, victim["id"]).status_code == 204
    assert not any(exists(mine).values())  # the SVG, the ZIP, the preview and the model's drawing, all four (§35.3 item 2)
    assert all(exists(theirs).values())  # the others are untouched
    assert vclient.app.state.storage.vector_dir(run["id"]).is_dir()  # and so is the run's folder
    body = get_run(vclient, run["id"])
    assert [v["seed"] for v in body["vectors"]] == [100, 102] and body["binned_vectors"] == []
    assert [data["id"] for name, data in events if name == "run.updated"] == [run["id"]]
    assert vclient.get(victim["url"]).status_code == 404 and vclient.get(victim["drawing_url"]).status_code == 404


def test_a_present_version_can_be_deleted_for_good_directly(vclient):
    run = three(vclient)
    victim = run["vectors"][0]
    assert delete_version(vclient, victim["id"]).status_code == 204
    assert not any(exists(files_of(vclient, run, victim)).values())
    assert [v["seed"] for v in get_run(vclient, run["id"])["vectors"]] == [101, 102]


def test_deleting_the_last_version_for_good_deletes_the_run(vclient):
    run = three(vclient)
    folder = vclient.app.state.storage.vector_dir(run["id"])
    assert delete_version(vclient, run["vectors"][0]["id"]).status_code == 204
    assert delete_version(vclient, run["vectors"][1]["id"]).status_code == 204
    with captured(vclient) as events:
        assert delete_version(vclient, run["vectors"][2]["id"]).status_code == 204
    assert vclient.get(f"/api/runs/{run['id']}").status_code == 404 and not folder.exists()
    assert [data["id"] for name, data in events if name == "run.deleted"] == [run["id"]]


def test_a_present_version_of_an_unfinished_run_or_a_run_in_the_bin_is_not_deleted_for_good(vclient):
    run = three(vclient)
    victim = run["vectors"][0]["id"]
    set_status(vclient, run["id"], "running")
    assert delete_version(vclient, victim).status_code == 409 and code(delete_version(vclient, victim)) == "run_not_finished"
    set_status(vclient, run["id"], "done")
    assert bin_it(vclient, run["id"]).status_code == 200
    assert delete_version(vclient, victim).status_code == 409 and code(delete_version(vclient, victim)) == "run_in_bin"
    assert len(get_run(vclient, run["id"])["vectors"]) == 3


def test_a_version_in_the_bin_can_be_deleted_for_good_whatever_its_run_is_doing(vclient):
    run = three(vclient)
    victim = run["vectors"][0]["id"]
    assert bin_version(vclient, victim).status_code == 200
    assert bin_it(vclient, run["id"]).status_code == 200  # the run is in the bin now too
    assert delete_version(vclient, victim).status_code == 204


def test_deleting_a_run_for_good_takes_its_versions_in_the_bin_with_it(vclient):
    run = three(vclient)
    victim = run["vectors"][0]
    assert bin_version(vclient, victim["id"]).status_code == 200
    folder = vclient.app.state.storage.vector_dir(run["id"])
    assert vclient.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not folder.exists() and vclient.app.state.db._one("SELECT COUNT(*) AS n FROM vectors")["n"] == 0


# ------------------------------------------------------------------ Empty bin, the clean-up, the counts and the Deleted list
def test_empty_bin_deletes_versions_and_counts_them_with_the_other_items(vclient):
    staying = three(vclient)
    leaving = make_vector(vclient, num_images=2, seed=200)
    assert bin_version(vclient, staying["vectors"][0]["id"]).status_code == 200 and bin_version(vclient, staying["vectors"][1]["id"]).status_code == 200
    assert bin_version(vclient, leaving["vectors"][0]["id"]).status_code == 200
    assert bin_it(vclient, leaving["id"]).status_code == 200  # `leaving` is in the bin and has a version of its own in it
    mine = files_of(vclient, staying, staying["vectors"][0])
    with captured(vclient) as events:
        response = vclient.delete("/api/bin")
    assert response.status_code == 200 and response.json() == {"deleted": 1, "pictures": 2}  # the key keeps its name; it counts every kind of item
    assert not any(exists(mine).values())
    body = get_run(vclient, staying["id"])
    assert [v["seed"] for v in body["vectors"]] == [102] and body["binned_vectors"] == []
    assert vclient.get(f"/api/runs/{leaving['id']}").status_code == 404
    assert [d["id"] for n, d in events if n == "run.deleted"] == [leaving["id"]] and [d["id"] for n, d in events if n == "run.updated"] == [staying["id"]]
    assert vclient.delete("/api/bin").json() == {"deleted": 0, "pictures": 0}


def test_the_clean_up_deletes_versions_whose_days_are_over_and_no_others(vclient_factory):
    """A version 11 days in a 10-day bin goes with its four files; one 9 days in stays (criterion 192). The pages that show the run are told once."""
    client = vclient_factory(quiet=True, bin_days=10)
    run = make_vector(client, num_images=4, seed=300)
    ids = [v["id"] for v in run["vectors"]]
    for victim in ids[:3]:
        assert bin_version(client, victim).status_code == 200
    set_deleted_at(client, ids[0], days_ago(11))
    set_deleted_at(client, ids[1], days_ago(9))
    set_deleted_at(client, ids[2], days_ago(12))
    paths = {v["id"]: files_of(client, run, v) for v in run["vectors"]}
    with captured(client) as events:
        sweep(client)
    assert not any(any(exists(paths[i]).values()) for i in (ids[0], ids[2]))
    assert all(all(exists(paths[i]).values()) for i in (ids[1], ids[3]))
    body = get_run(client, run["id"])
    assert [v["id"] for v in body["binned_vectors"]] == [ids[1]] and [v["id"] for v in body["vectors"]] == [ids[3]]
    assert [d["id"] for n, d in events if n == "run.updated"] == [run["id"]]


def test_the_clean_up_with_no_bin_deletes_every_version_that_is_in_it(vclient_factory):
    """With no bin the clean-up deletes what is in it at once, as it deletes every run; a version can only be there if the bin was on earlier."""
    client = vclient_factory(quiet=True, bin_days=0)
    run = three(client)
    victim = run["vectors"][0]
    set_deleted_at(client, victim["id"], days_ago(0))  # left over from when there was a bin
    paths = files_of(client, run, victim)
    sweep(client)
    assert not any(exists(paths).values()) and [v["id"] for v in get_run(client, run["id"])["vectors"]] == [v["id"] for v in run["vectors"][1:]]


def test_the_counts_follow_runs_and_versions_in_the_bin(vclient):
    """The number beside Deleted counts things: each run in the bin once and each version in it of a run still in the history once; the versions alone
    are `deleted_items` (criterion 191). Here: two versions of one run, and another run in the bin whole."""
    run = three(vclient)
    other = make_vector(vclient, num_images=2, seed=400)
    assert vclient.get("/api/runs/counts").json()["vector"] == {"all": 2, "kept": 0, "deleted": 0, "deleted_items": 0}
    assert bin_version(vclient, run["vectors"][0]["id"]).status_code == 200 and bin_version(vclient, run["vectors"][1]["id"]).status_code == 200
    assert bin_it(vclient, other["id"]).status_code == 200
    # the history is the run that is in it; the bin has the other run once and the two versions once each
    assert vclient.get("/api/runs/counts").json()["vector"] == {"all": 1, "kept": 0, "deleted": 3, "deleted_items": 2}
    assert vclient.get("/api/runs/counts").json()["image"]["deleted"] == 0  # the other tabs do not count a Vector version


def test_the_deleted_list_shows_a_run_with_a_version_in_the_bin_once(vclient):
    run = three(vclient)
    plain = three(vclient)
    assert bin_version(vclient, run["vectors"][0]["id"]).status_code == 200
    listed = vclient.get("/api/runs?kind=vector&deleted=true").json()["runs"]
    assert [r["id"] for r in listed] == [run["id"]] and plain["id"] not in [r["id"] for r in listed]
    history = vclient.get("/api/runs?kind=vector").json()["runs"]
    assert {run["id"], plain["id"]} <= {r["id"] for r in history}  # the run is in the history as well: it was not deleted
    assert bin_it(vclient, run["id"]).status_code == 200
    assert [r["id"] for r in vclient.get("/api/runs?kind=vector&deleted=true").json()["runs"]] == [run["id"]]  # once, not twice


# ------------------------------------------------------------------ the drawing and the layers, which the viewer shows
def test_the_drawing_is_served_as_a_png_and_downloads_under_its_own_name(vclient):
    run = three(vclient)
    vector = run["vectors"][0]
    response = vclient.get(vector["drawing_url"])
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.content == files_of(vclient, run, vector)["-source.png"].read_bytes()  # the very file the model drew
    assert response.headers["x-content-type-options"] == "nosniff" and "immutable" in response.headers["cache-control"]
    assert "content-disposition" not in response.headers  # shown in the viewer, not saved, unless asked
    download = vclient.get(vector["drawing_download_url"])
    assert download.headers["content-disposition"].startswith("attachment; filename=\"vector_fox-head_100x100mm_")
    assert "_s100_" in download.headers["content-disposition"] and "-drawing.png" in download.headers["content-disposition"]


def test_a_drawing_that_is_gone_says_so_and_nothing_else_about_the_version_changes(vclient):
    run = three(vclient)
    vector = run["vectors"][0]
    files_of(vclient, run, vector)["-source.png"].unlink()
    response = vclient.get(vector["drawing_url"])
    assert response.status_code == 404 and code(response) == "drawing_missing" and response.json()["detail"] == "The drawing is no longer on the disk."
    assert vclient.get(vector["url"]).status_code == 200 and vclient.get(vector["layer_urls"][0]).status_code == 200  # the vector is fine
    assert vclient.get(f"/api/vectors/{NO_SUCH}/drawing").status_code == 404 and code(vclient.get(f"/api/vectors/{NO_SUCH}/drawing")) == "not_found"


def test_each_layer_is_served_on_its_own_from_the_zip_with_the_svg_headers(vclient):
    """The stack view shows the sheets one at a time (criterion 208): layer n is the n-th file of the ZIP, with the headers that keep a script in an SVG
    from running, exactly as the combined SVG has them."""
    run = three(vclient)
    vector = run["vectors"][0]
    with zipfile.ZipFile(io.BytesIO(files_of(vclient, run, vector)["-layers.zip"].read_bytes())) as bundle:
        wanted = {name: bundle.read(name) for name in bundle.namelist()}
    combined = vclient.get(vector["url"])
    assert vector["layers"] == 3 and len(vector["layer_urls"]) == 3
    for n, (layer, name) in enumerate(zip(vector["check"]["layers"], sorted(wanted)), start=1):
        response = vclient.get(vector["layer_urls"][n - 1])
        assert response.status_code == 200 and response.content == wanted[f"{layer['id']}.svg"] and f'id="{layer["id"]}"' in response.text
        assert response.headers["content-type"] == "image/svg+xml" and response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == combined.headers["content-security-policy"] and "immutable" in response.headers["cache-control"]
    assert wanted["layer-2-red.svg"] != wanted["layer-3-green.svg"]  # they are different files, so a mix-up of numbers would show


@pytest.mark.parametrize("number", ["0", "4", "-1", "99"])
def test_a_layer_outside_the_ones_that_exist_is_a_404(vclient, number):
    vector = three(vclient)["vectors"][0]
    response = vclient.get(f"/api/vectors/{vector['id']}/layers/{number}.svg")
    assert response.status_code == 404 and code(response) == "not_found"


def test_the_layer_route_does_not_answer_for_what_is_not_a_number_or_a_version(vclient):
    vector = three(vclient)["vectors"][0]
    assert vclient.get(f"/api/vectors/{vector['id']}/layers/one.svg").status_code in (404, 422)  # never a 500, and never a file
    assert vclient.get(f"/api/vectors/{NO_SUCH}/layers/1.svg").status_code == 404


def test_the_layers_of_a_stacked_run_say_how_high_they_stand(vclient):
    """The check the fake job writes mirrors the engine's for a stacked run (§35.4): the choice made, and each layer as high as its number."""
    vector = make_vector(vclient, num_images=1, seed=500, colours=4, arrangement="stacked", stack_order="area_asc")["vectors"][0]
    assert vector["check"]["stack_order"] == "area_asc" and [layer["height"] for layer in vector["check"]["layers"]] == [1, 2, 3]
    side = make_vector(vclient, num_images=1, seed=501, colours=4)["vectors"][0]
    assert side["check"]["stack_order"] is None and [layer["height"] for layer in side["check"]["layers"]] == [1, 1, 1]


# ------------------------------------------------------------------ the migration from schema 7 (1.17)
def as_schema_7(path) -> None:
    """Make a database that this code made look as one from 1.17 does: `vectors` without its `deleted_at` column and its index, and the stored version 7.
    The table is made again without the column (not altered), the way the old code would have made it."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP INDEX IF EXISTS idx_vectors_deleted")
        kept = [row[1] for row in conn.execute("PRAGMA table_info(vectors)") if row[1] != "deleted_at"]
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='vectors'").fetchone()[0]
        older = sql.replace("    deleted_at  TEXT\n", "").replace("created_at  TEXT NOT NULL,\n)", "created_at  TEXT NOT NULL\n)")
        assert "deleted_at" not in older, older
        conn.execute("ALTER TABLE vectors RENAME TO vectors_new")
        conn.execute(older)
        conn.execute(f"INSERT INTO vectors ({', '.join(kept)}) SELECT {', '.join(kept)} FROM vectors_new")
        conn.execute("DROP TABLE vectors_new")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_run ON vectors(run_id, idx)")
        conn.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
        conn.commit()
    finally:
        conn.close()


def test_a_1_17_database_gets_the_column_keeps_its_versions_and_is_the_same_as_a_fresh_one(tmp_path):
    """Schema 8 adds `vectors.deleted_at` (criterion 251). A database as 1.17 left it opens, keeps every version (none of them in the bin), makes a copy
    of itself first, and then has exactly the columns, in the same order, that a new one has: the column is the last one so that ALTER TABLE gives the
    same table as CREATE TABLE does."""
    assert SCHEMA_VERSION == 8
    path = tmp_path / "studio.sqlite"
    fresh = tmp_path / "fresh.sqlite"
    Database(fresh).close()
    db = Database(path)
    run_id = "a" * 32
    assert db.insert_run_if_capacity({"id": run_id, "created_at": days_ago(1), "status": "done", "mode": "vector", "prompt": "a fox", "effective_prompt": "a fox",
                                      "steps": 3, "seed": 1, "num_images": 1, "model_id": "fake", "options_json": "{}", "width": 64, "height": 64, "pinned": 0}, cap=100)
    db.add_vector({"id": "b" * 32, "run_id": run_id, "idx": 0, "seed": 1, "width_mm": 100.0, "height_mm": 100.0, "colours": 2, "layers": 1, "bytes": 1, "check_json": "{}",
                   "svg_path": "vector/x/0.svg", "zip_path": "vector/x/0-layers.zip", "png_path": "vector/x/0.png", "created_at": days_ago(1)})
    db.close()
    as_schema_7(path)
    assert "deleted_at" not in dict(columns(path, "vectors"))
    migrated = Database(path)
    try:
        assert columns(path, "vectors") == columns(fresh, "vectors")  # the same columns, the same types, the same order
        row = migrated.get_vector("b" * 32)
        assert row is not None and row["deleted_at"] is None and migrated.vectors_for_runs([run_id])[run_id][0]["id"] == "b" * 32
        indexes = {r[1] for r in sqlite3.connect(path).execute("PRAGMA index_list(vectors)")}
        assert "idx_vectors_deleted" in indexes  # what lets the daily clean-up find what is due without reading every version
        assert migrated.bin_picture("vector", "b" * 32, "2026-11-01T00:00:00.000Z") == ("run", run_id)  # it works: a run's only version sends the run
    finally:
        migrated.close()
    copy = tmp_path / f"studio.sqlite.before-schema-{SCHEMA_VERSION}"
    assert copy.exists() and "deleted_at" not in dict(columns(copy, "vectors"))  # the copy is the database as 1.17 left it, a way back
    assert sqlite3.connect(path).execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "8"
