"""A reading is kept from the start (DESIGN.md §38.8, §38.18): it never expires, so it is made with the Keep flag on and nothing can turn it off. That is what lets the
Kept filter, the counts and the projects treat a reading as they treat any kept run, with no case for it. The sweep that removes old runs leaving every reading is tested
in test_speech_readings.py; this file is about the flag, and about the routes that could change it."""

from __future__ import annotations

from conftest import create_run, wait_for
from test_speech_readings import finished_reading, make_reading


def test_a_reading_is_kept_from_the_moment_it_is_made_and_has_no_expiry(client):
    queued = make_reading(client)
    assert queued["pinned"] is True and queued["expires_at"] is None  # not "kept" by anyone's choice: it simply never expires
    done = finished_reading(client)
    assert done["pinned"] is True and done["expires_at"] is None


def test_the_kept_filter_lists_a_reading_and_the_other_filter_does_not(client):
    reading = finished_reading(client)
    picture = wait_for(client, create_run(client)["id"])
    kept = {run["id"] for run in client.get("/api/runs?kept=true").json()["runs"]}
    unkept = {run["id"] for run in client.get("/api/runs?kept=false").json()["runs"]}
    assert reading["id"] in kept and picture["id"] not in kept
    assert reading["id"] not in unkept and picture["id"] in unkept


def test_the_counts_count_a_reading_as_kept(client):
    finished_reading(client)
    counts = client.get("/api/runs/counts").json()["speech"]
    assert counts["all"] == 1 and counts["kept"] == 1 and counts["deleted"] == 0


def test_a_reading_cannot_be_made_not_kept_and_the_answer_says_why(client):
    reading = finished_reading(client)
    refused = client.patch(f"/api/runs/{reading['id']}", json={"pinned": False})
    assert refused.status_code == 409 and refused.json()["code"] == "always_kept"
    assert "never expires" in refused.json()["detail"]
    assert client.get(f"/api/runs/{reading['id']}").json()["pinned"] is True  # nothing changed
    assert client.patch(f"/api/runs/{reading['id']}", json={"pinned": True}).status_code == 200  # keeping it, which it is, is not refused


def test_a_reading_in_a_project_asked_not_to_be_kept_gets_the_readings_answer_and_not_advice_that_would_not_help(client):
    """For a picture in a project the answer is "take it out of the project first", because that would let it stop being kept. For a reading that advice would be
    wrong: taking it out changes nothing about being kept, so the answer has to be the reading's own, whatever else is true of it."""
    reading = finished_reading(client)
    project = client.post("/api/projects", json={"name": "Books"}).json()
    assert client.put(f"/api/runs/{reading['id']}/project", json={"project_id": project["id"]}).status_code == 200
    refused = client.patch(f"/api/runs/{reading['id']}", json={"pinned": False})
    assert refused.status_code == 409 and refused.json()["code"] == "always_kept"
    assert "project" not in refused.json()["detail"]


def test_a_picture_can_still_be_made_not_kept(client):
    """The refusal is for readings only: every other kind of run keeps the Keep it always had."""
    picture = wait_for(client, create_run(client)["id"])
    assert client.patch(f"/api/runs/{picture['id']}", json={"pinned": True}).json()["pinned"] is True
    assert client.patch(f"/api/runs/{picture['id']}", json={"pinned": False}).json()["pinned"] is False


def test_a_reading_taken_out_of_a_project_stays_kept_even_when_asked_to_go_back_to_what_it_was(client):
    reading = finished_reading(client)
    project = client.post("/api/projects", json={"name": "Books"}).json()
    assert client.put(f"/api/runs/{reading['id']}/project", json={"project_id": project["id"]}).json()["pinned"] is True
    taken = client.delete(f"/api/runs/{reading['id']}/project?keep=false")  # what Undo of a filing sends: for a picture, back to not kept
    assert taken.status_code == 200 and taken.json()["project_id"] is None
    assert taken.json()["pinned"] is True  # a reading was kept before it was filed: back to what it was is kept
    again = client.get(f"/api/runs/{reading['id']}").json()
    assert again["pinned"] is True


def test_a_picture_taken_out_with_keep_false_still_goes_back_to_not_kept(client):
    picture = wait_for(client, create_run(client)["id"])
    project = client.post("/api/projects", json={"name": "Pictures"}).json()
    client.put(f"/api/runs/{picture['id']}/project", json={"project_id": project["id"]})
    taken = client.delete(f"/api/runs/{picture['id']}/project?keep=false").json()
    assert taken["pinned"] is False  # unchanged for every other kind


def test_a_reading_in_the_bin_is_still_kept_and_comes_back_kept(client):
    reading = finished_reading(client)
    assert client.post(f"/api/runs/{reading['id']}/bin").status_code == 200  # Delete: to the bin for 30 days (criterion 287)
    binned = client.get(f"/api/runs/{reading['id']}").json()
    assert binned["deleted_at"] is not None and binned["pinned"] is True
    restored = client.post(f"/api/runs/{reading['id']}/restore")
    assert restored.status_code == 200 and restored.json()["pinned"] is True and restored.json()["deleted_at"] is None
