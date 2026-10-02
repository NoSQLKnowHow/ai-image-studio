"""Drafts, the queue order they bring, and downloadable thumbnails (DESIGN.md §22; criteria 36, 37, 38)."""

from __future__ import annotations

import io
import sqlite3

import pytest
from conftest import create_run, wait_for
from PIL import Image

from studio.config import ConfigError, Settings
from studio.db import Database


def draft(client, prompt: str = "a quick try", expect: int = 201, **options):
    body = {"prompt": prompt, "options": {"width": 512, "height": 288, "steps": 12, "num_images": 1, "draft": True, **options}}
    response = client.post("/api/runs", json=body)
    assert response.status_code == expect, response.text
    return response.json()


# ------------------------------------------------------------------ the server keeps drafts small
def test_a_draft_is_recorded_and_runs_like_any_other(client):
    run = draft(client)
    assert run["options"]["draft"] is True
    done = wait_for(client, run["id"])
    assert done["status"] == "done" and (done["images"][0]["width"], done["images"][0]["height"]) == (512, 288)
    assert create_run(client)["options"]["draft"] is False  # an ordinary run is not one


@pytest.mark.parametrize("options,field,fragment", [
    ({"width": 1024, "height": 576}, "width", "at most 512 pixels on its long side"),
    ({"width": 288, "height": 1024}, "width", "at most 512 pixels on its long side"),
    ({"steps": 13}, "steps", "at most 12 steps"),
    ({"num_images": 2}, "num_images", "makes one image"),
])
def test_a_draft_that_is_big_many_stepped_or_many_imaged_is_refused(client, options, field, fragment):
    response = client.post("/api/runs", json={"prompt": "x", "options": {"width": 512, "height": 288, "steps": 12, "draft": True, **options}})
    assert response.status_code == 422
    errors = {tuple(e["loc"]): e["msg"] for e in response.json()["detail"]}
    assert fragment in errors[("body", "options", field)]


def test_a_draft_needs_an_explicit_size_and_is_only_for_generate(client):
    no_size = client.post("/api/runs", json={"prompt": "x", "options": {"draft": True, "steps": 5}})
    assert no_size.status_code == 422 and "needs an explicit size" in no_size.json()["detail"][0]["msg"]
    edit = client.post("/api/runs", json={"mode": "edit", "prompt": "x", "options": {"draft": True}})
    assert any("only for Generate" in e["msg"] for e in edit.json()["detail"])


def test_the_draft_limits_are_settings_and_are_told_to_the_page(client_factory):
    client = client_factory(draft_size=256, draft_steps=4)
    assert client.get("/api/capabilities").json()["limits"]["draft"] == {"long_side": 256, "steps": 4}
    assert client.post("/api/runs", json={"prompt": "x", "options": {"draft": True, "width": 512, "height": 288, "steps": 3}}).status_code == 422
    ok = draft(client, width=256, height=256, steps=4)  # at 256 only a square fits: no side may be under 256
    assert wait_for(client, ok["id"])["status"] == "done"


def test_the_default_draft_limits_and_their_validation():
    s = Settings.from_env({})
    assert (s.draft_size, s.draft_steps) == (512, 12)
    assert Settings.from_env({"STUDIO_DRAFT_SIZE": "768", "STUDIO_DRAFT_STEPS": "20"}).draft_size == 768
    for env in ({"STUDIO_DRAFT_SIZE": "500"}, {"STUDIO_DRAFT_SIZE": "128"}, {"STUDIO_DRAFT_SIZE": "2048"}, {"STUDIO_DRAFT_STEPS": "0"}):
        with pytest.raises(ConfigError):
            Settings.from_env(env)


# ------------------------------------------------------------------ a draft jumps waiting full-size runs, never the running one
def started_order(client, ids):
    runs = {i: client.get(f"/api/runs/{i}").json() for i in ids}
    return [i for i in sorted(ids, key=lambda i: runs[i]["started_at"] or "9")]


def test_a_draft_runs_before_queued_full_size_runs_but_after_the_running_one(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    running = create_run(client, "running", steps=40)
    wait_for(client, running["id"], frozenset({"running"}))
    first, second = create_run(client, "full 1", steps=4), create_run(client, "full 2", steps=4)
    quick = draft(client, "the draft", steps=3)
    positions = {r["id"]: r["queue_position"] for r in client.get("/api/runs").json()["runs"] if r["status"] == "queued"}
    assert positions == {quick["id"]: 1, first["id"]: 2, second["id"]: 3}  # what the cards will show
    for run in (running, first, second, quick):
        wait_for(client, run["id"])
    assert started_order(client, [running["id"], first["id"], second["id"], quick["id"]]) == [
        running["id"], quick["id"], first["id"], second["id"]]


def test_drafts_keep_their_own_order_and_a_canceled_draft_leaves_the_queue(client_factory):
    client = client_factory(fake_step_delay_ms=25)
    running = create_run(client, "running", steps=40)
    wait_for(client, running["id"], frozenset({"running"}))
    full = create_run(client, "full", steps=3)
    d1, d2, d3 = (draft(client, f"draft {i}", steps=3) for i in (1, 2, 3))
    assert [r["queue_position"] for r in (client.get(f"/api/runs/{x['id']}").json() for x in (d1, d2, d3, full))] == [1, 2, 3, 4]
    assert client.post(f"/api/runs/{d2['id']}/cancel").status_code == 200
    positions = {x["id"]: client.get(f"/api/runs/{x['id']}").json()["queue_position"] for x in (d1, d3, full)}
    assert positions == {d1["id"]: 1, d3["id"]: 2, full["id"]: 3}
    for run in (running, d1, d3, full):
        wait_for(client, run["id"])
    assert started_order(client, [d1["id"], d3["id"], full["id"]]) == [d1["id"], d3["id"], full["id"]]


def test_runs_made_before_drafts_existed_still_queue_in_order(tmp_path):
    """Old runs have no draft key at all: they must sort as ordinary runs, in arrival order."""
    db = Database(tmp_path / "studio.sqlite")
    try:
        for n, options in enumerate(("{}", '{"draft": false}', '{"draft": true}', "{}"), 1):
            db.insert_run({"id": f"{n:032x}", "created_at": "2026-10-02T10:00:00.000Z", "status": "queued", "mode": "generate",
                           "prompt": "p", "effective_prompt": "p", "steps": 3, "seed": 1, "num_images": 1, "model_id": "m",
                           "options_json": options}, 10)
        assert [i[-1] for i in db.queued_ids()] == ["3", "1", "2", "4"]
        assert db.next_queued()["id"].endswith("3")
    finally:
        db.close()


def test_without_json_functions_the_queue_falls_back_to_arrival_order(tmp_path, monkeypatch):
    real_connect = sqlite3.connect

    class NoJson(sqlite3.Connection):
        def execute(self, sql, *args):
            if "json_extract" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return super().execute(sql, *args)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real_connect(*a, **{**k, "factory": NoJson}))
    db = Database(tmp_path / "studio.sqlite")
    try:
        assert db._queue_order == "seq"
    finally:
        db.close()


# ------------------------------------------------------------------ the thumbnail download
def finished_run(client, prompt="a lighthouse at dusk", **options):
    return wait_for(client, create_run(client, prompt, **options)["id"])


def test_a_results_thumbnail_can_be_downloaded_as_webp_with_a_matching_name(client):
    run = finished_run(client, num_images=1)
    image = run["images"][0]
    full = client.get(image["download_url"])
    thumb = client.get(f"{image['thumb_url']}?download=1")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    stem = full.headers["content-disposition"].split('filename="')[1].split('"')[0].removesuffix(".png")
    assert f'filename="{stem}_thumb.webp"' in thumb.headers["content-disposition"] and thumb.headers["content-disposition"].startswith("attachment")
    assert Image.open(io.BytesIO(thumb.content)).format == "WEBP" and "immutable" in thumb.headers["cache-control"]


def test_without_the_download_flag_a_thumbnail_is_shown_inline_as_before(client):
    image = finished_run(client)["images"][0]
    plain = client.get(image["thumb_url"])
    assert plain.status_code == 200 and "content-disposition" not in plain.headers


def test_a_transparent_results_thumbnail_keeps_its_transparency(client):
    run = finished_run(client, "a glass", transparent=True)
    thumb = client.get(f"{run['images'][0]['thumb_url']}?download=1")
    assert Image.open(io.BytesIO(thumb.content)).mode == "RGBA" and "rgba" in thumb.headers["content-disposition"]


def test_only_results_have_a_downloadable_thumbnail(client):
    from conftest import create_edit, image_bytes, stage

    up = stage(client, image_bytes())
    run = wait_for(client, create_edit(client, "x", [{"upload_id": up["upload_id"]}])["id"])
    for target in (up["thumb_url"], run["inputs"][0]["thumb_url"]):  # a waiting upload, and a run's own input
        response = client.get(f"{target}?download=1") if target is up["thumb_url"] else client.get(f"{target}?download=1")
        assert "content-disposition" not in response.headers
    assert client.get(f"/api/images/{'a' * 32}/thumb?download=1").status_code == 404


def test_thumbnail_filename_replaces_the_extension_and_never_doubles_it():
    from studio.naming import content_disposition, thumbnail_filename

    assert thumbnail_filename("generate_cat_512x288_s1_20261002-101500.png") == "generate_cat_512x288_s1_20261002-101500_thumb.webp"
    assert thumbnail_filename("odd") == "odd_thumb.webp"
    assert 'filename="thumbnail.webp"' in content_disposition("猫.webp", default="thumbnail.webp")
