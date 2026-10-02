"""The full size a smaller run stands in for, which Regenerate larger sends back (DESIGN.md §23; criteria 40, 42)."""

from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import create_edit, create_run, image_bytes, stage, wait_for

FULL = {"width": 512, "height": 512, "steps": 30}


def small_run(client, prompt: str = "a lighthouse", full=FULL, **options):
    """A 256x256 run that says it stands in for a bigger one."""
    return create_run(client, prompt, full=full, **options)


def post(client, options: dict, **body):
    return client.post("/api/runs", json={"prompt": "x", **body, "options": options})


def errors_of(response) -> dict[tuple, str]:
    assert response.status_code == 422, response.text
    return {tuple(e["loc"]): e["msg"] for e in response.json()["detail"]}


# ------------------------------------------------------------------ recorded with the run
def test_the_full_size_is_stored_and_returned_with_the_run(client):
    run = small_run(client)
    assert run["options"]["full"] == FULL
    done = wait_for(client, run["id"])
    assert done["status"] == "done" and done["options"]["full"] == FULL
    assert client.get(f"/api/runs/{run['id']}").json()["options"]["full"] == FULL
    assert (done["images"][0]["width"], done["images"][0]["height"]) == (256, 256)  # the run itself stays as asked


def test_a_run_without_one_records_none(client):
    assert create_run(client)["options"]["full"] is None


def test_it_is_in_the_history_listing_too(client):
    run = small_run(client)
    wait_for(client, run["id"])
    listed = next(r for r in client.get("/api/runs").json()["runs"] if r["id"] == run["id"])
    assert listed["options"]["full"] == FULL


@pytest.mark.parametrize("full", [
    {"width": 512, "height": 512, "steps": 30},  # larger in both
    {"width": 512, "height": 256, "steps": 30},  # larger in one, equal in the other
    {"width": 256, "height": 256 + 32, "steps": 3},  # the smallest step up, with the same steps
    {"width": 256, "height": 4096, "steps": 4},  # the tallest allowed
])
def test_a_size_larger_in_one_side_and_smaller_in_none_is_accepted(client, full):
    assert small_run(client, full=full)["options"]["full"] == full


# ------------------------------------------------------------------ refused
def test_the_same_size_is_not_larger(client):
    response = post(client, {"width": 256, "height": 256, "steps": 3, "full": {"width": 256, "height": 256, "steps": 30}})
    assert "must be larger than the run (256x256)" in errors_of(response)[("body", "options", "full")]


@pytest.mark.parametrize("run,full", [
    ((512, 256), (288, 1024)),  # the width shrinks
    ((256, 512), (1024, 288)),  # the height shrinks
    ((1024, 1024), (512, 512)),  # smaller in both: a "full" size below the run is nonsense
])
def test_a_size_smaller_in_either_side_is_refused(client, run, full):
    response = post(client, {"width": run[0], "height": run[1], "steps": 3, "full": {"width": full[0], "height": full[1], "steps": 30}})
    assert "smaller in neither side" in errors_of(response)[("body", "options", "full")]


@pytest.mark.parametrize("full,field,fragment", [
    ({"width": 500, "height": 512, "steps": 30}, "width", "multiple of 32"),
    ({"width": 512, "height": 520, "steps": 30}, "height", "multiple of 32"),
    ({"width": 4128, "height": 512, "steps": 30}, "width", "between 256 and 4096"),
    ({"width": 512, "height": 224, "steps": 30}, "height", "between 256 and 4096"),
    ({"width": 4096, "height": 4096, "steps": 30}, "width", "the limit is"),
    ({"width": 512, "height": 512, "steps": 0}, "steps", "Must be between"),
    ({"width": 512, "height": 512, "steps": 1000}, "steps", "Must be between"),
])
def test_a_full_size_is_checked_like_any_size(client, full, field, fragment):
    errors = errors_of(post(client, {"width": 256, "height": 256, "steps": 3, "full": full}))
    assert fragment in errors[("body", "options", "full", field)]
    assert set(errors) == {("body", "options", "full", field)}  # one problem, said once


def test_only_generate_records_one(client):
    response = post(client, {"full": FULL}, mode="edit")
    assert response.status_code == 422
    assert any("only recorded for Generate" in e["msg"] for e in response.json()["detail"])


@pytest.mark.parametrize("bad", [
    {"width": 512, "height": 512},  # no steps
    {"width": 512, "height": 512, "steps": 30, "extra": 1},
    {"width": "512", "height": 512, "steps": 30},
    {"width": 512.0, "height": 512, "steps": 30},
    512,
    "big",
])
def test_the_shape_is_strict(client, bad):
    assert post(client, {"width": 256, "height": 256, "steps": 3, "full": bad}).status_code == 422


def test_a_refused_full_size_queues_nothing(client):
    post(client, {"width": 256, "height": 256, "steps": 3, "full": {"width": 256, "height": 256, "steps": 3}})
    assert client.get("/api/runs").json()["runs"] == []


# ------------------------------------------------------------------ together with a draft
def test_a_draft_may_record_the_full_size_it_stands_in_for(client):
    response = post(client, {"width": 512, "height": 288, "steps": 12, "num_images": 1, "draft": True,
                             "full": {"width": 1792, "height": 1024, "steps": 40}})
    assert response.status_code == 201, response.text
    assert response.json()["options"]["draft"] is True and response.json()["options"]["full"]["steps"] == 40


def test_recording_one_does_not_loosen_the_draft_limits(client):
    response = post(client, {"width": 1024, "height": 576, "steps": 40, "draft": True,
                             "full": {"width": 2048, "height": 1152, "steps": 40}})
    errors = errors_of(response)
    assert ("body", "options", "width") in errors and ("body", "options", "steps") in errors


# ------------------------------------------------------------------ the larger run is an ordinary run
def test_the_run_it_leads_to_is_ordinary_and_repeatable(client):
    """What the page's button sends: the full size and steps, the same seed and image count, no `full` of its own."""
    small = small_run(client, seed=1234, num_images=2)
    done = wait_for(client, small["id"])
    assert done["options"]["seed"] == 1234 and len(done["images"]) == 2
    big = create_run(client, "a lighthouse", width=512, height=512, steps=30, seed=1234, num_images=2)
    assert big["options"]["full"] is None and big["options"]["draft"] is False
    finished = wait_for(client, big["id"])
    assert finished["status"] == "done" and (finished["images"][0]["width"], finished["images"][0]["height"]) == (512, 512)
    assert [i["seed"] for i in finished["images"]] == [i["seed"] for i in done["images"]]


# ------------------------------------------------------------------ older runs and edits
def test_a_run_made_before_1_4_has_no_such_key_and_still_loads(client_factory, tmp_path):
    """Older rows have no `full` in their stored options; the page treats a missing key as none."""
    client = client_factory(quiet=True)
    run = create_run(client)
    wait_for(client, run["id"])
    with sqlite3.connect(tmp_path / "data" / "studio.sqlite") as db:
        stored = json.loads(db.execute("SELECT options_json FROM runs WHERE id=?", (run["id"],)).fetchone()[0])
        assert stored.pop("full") is None
        db.execute("UPDATE runs SET options_json=? WHERE id=?", (json.dumps(stored), run["id"]))
    options = client.get(f"/api/runs/{run['id']}").json()["options"]
    assert "full" not in options and options["steps"] == 3


def test_an_edit_run_records_none(client):
    staged = stage(client, image_bytes())
    run = create_edit(client, "make it blue", [{"upload_id": staged["upload_id"]}])
    assert run["options"]["full"] is None
