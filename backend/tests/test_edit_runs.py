"""Edit runs through the API with the fake pipeline (DESIGN.md §21; criteria 20, 21, 24, 25, 26, 27, 32)."""

from __future__ import annotations

import errno
import io
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import create_edit, create_run, edit_body, image_bytes, open_result, stage, wait_for
from PIL import Image

from studio import inputs as inputs_module
from studio.pipelines.fake import strip_cells
from studio.serialize import format_ts

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


def ref(upload: dict) -> dict:
    return {"upload_id": upload["upload_id"]}


def strip_colours(image: Image.Image, count: int) -> list[tuple]:
    """The colour at the middle of each thumbnail the fake pipeline drew, in the order drawn."""
    boxes = strip_cells(image.width, image.height, count)
    return [image.convert("RGB").getpixel(((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)) for b in boxes]


def inputs_on_disk(client, run_id: str) -> list[str]:
    folder = client.app.state.storage.inputs / run_id
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def days_ago(days: float) -> str:
    return format_ts(datetime.now(timezone.utc) - timedelta(days=days))


# ------------------------------------------------------------------ the order is the order, and the last image sets the shape
def test_an_edit_gets_its_images_in_the_order_given_and_follows_the_last_ones_shape(client):
    ups = [stage(client, image_bytes(c, size)) for c, size in ((RED, (300, 200)), (GREEN, (200, 300)), (BLUE, (240, 240)))]
    run = wait_for(client, create_edit(client, "put them together", [ref(u) for u in ups])["id"])
    assert run["status"] == "done" and run["mode"] == "edit"
    result = open_result(client, run)
    assert result.size == (1024, 1024)  # the last image is square: about 1 MP, not Generate's 2048
    assert strip_colours(result, 3) == [RED, GREEN, BLUE]  # image 1, 2, 3, exactly as submitted
    assert [(i["position"], i["role"], i["width"], i["height"]) for i in run["inputs"]] == [
        (1, "reference", 300, 200), (2, "reference", 200, 300), (3, "reference", 240, 240)]
    assert run["options"]["roles"] == ["reference"] * 3 and run["options"]["resolution"] == 1024
    assert result.text["inputs"] == "3" and result.text["resolution"] == "1024"  # recorded in the PNG itself


def test_swapping_the_order_swaps_what_the_model_sees(client):
    a, b = stage(client, image_bytes(RED)), stage(client, image_bytes(BLUE))
    run = wait_for(client, create_edit(client, "x", [ref(b), ref(a)])["id"])
    assert strip_colours(open_result(client, run), 2) == [BLUE, RED]


def test_the_run_owns_copies_stored_in_order_and_the_staged_uploads_are_consumed(client):
    ups = [stage(client, image_bytes(c)) for c in (RED, GREEN)]
    staged_bytes = [client.get(u["url"]).content for u in ups]
    run = create_edit(client, "x", [ref(u) for u in ups])
    assert inputs_on_disk(client, run["id"]) == ["1.png", "2.png"]
    for item, up, original in zip(run["inputs"], ups, staged_bytes):
        assert item["id"] != up["upload_id"]  # a copy with its own id, not the staged file
        assert client.get(item["url"]).content == original  # byte for byte the same picture
        assert client.get(item["thumb_url"]).status_code == 200
        assert client.get(up["url"]).status_code == 404  # the staged upload is gone ...
        assert client.delete(f"/api/uploads/{up['upload_id']}").status_code == 404  # ... and cannot be used again
    storage = client.app.state.storage
    assert list(storage.staged.iterdir()) == [] and list(storage.staged_thumbs.iterdir()) == []
    wait_for(client, run["id"])


def test_the_run_listing_and_events_carry_the_inputs(client):
    bus = client.app.state.bus
    sub = bus.subscribe()
    try:
        run = create_edit(client, "x", [ref(stage(client, image_bytes(RED)))])
        event, data = sub.queue.get_nowait()
    finally:
        bus.unsubscribe(sub)
    assert event == "run.created" and [i["position"] for i in data["inputs"]] == [1]
    wait_for(client, run["id"])
    listed = client.get("/api/runs").json()["runs"][0]
    assert listed["inputs"] == client.get(f"/api/runs/{run['id']}").json()["inputs"] and len(listed["inputs"]) == 1


def test_a_batch_of_images_per_click_uses_the_same_inputs_for_each(client):
    ups = [stage(client, image_bytes(c)) for c in (RED, GREEN)]
    run = wait_for(client, create_edit(client, "x", [ref(u) for u in ups], num_images=2)["id"])
    assert len(run["images"]) == 2
    for i in (0, 1):
        assert strip_colours(open_result(client, run, i), 2) == [RED, GREEN]


# ------------------------------------------------------------------ size: Auto, "follows image N", an explicit size, resolution
def landscape_then_portrait(client):
    return [stage(client, image_bytes(RED, (400, 300))), stage(client, image_bytes(GREEN, (300, 400)))]


@pytest.mark.parametrize("options,expected", [
    ({}, (896, 1184)),                                  # Auto: the last image (portrait), at about 1 MP
    ({"shape_from": 2}, (896, 1184)),                   # naming the last one gives what Auto gives
    ({"shape_from": 1}, (1184, 896)),                   # following image 1 (landscape) reorders nothing
    ({"resolution": 2048}, (1760, 2368)),               # 2K: about 4 MP
    ({"resolution": 2048, "shape_from": 1}, (2368, 1760)),
    ({"width": 512, "height": 512}, (512, 512)),        # an explicit size overrides Auto, and only the result
    ({"width": 1024, "height": 768, "resolution": 2048}, (1024, 768)),
])
def test_the_result_shape(client, options, expected):
    run = wait_for(client, create_edit(client, "x", [ref(u) for u in landscape_then_portrait(client)], **options)["id"])
    result = open_result(client, run)
    assert result.size == expected
    # the inputs are drawn in the same order whatever the shape
    assert strip_colours(result, 2) == [RED, GREEN]


def test_follows_image_n_is_recorded_for_reuse_and_the_computed_size_is_not(client):
    run = create_edit(client, "x", [ref(u) for u in landscape_then_portrait(client)], shape_from=1)
    assert run["options"]["shape_from"] == 1
    assert run["options"]["width"] is None and run["options"]["height"] is None  # Reuse restores "Auto + follow 1"
    wait_for(client, run["id"])


def test_shape_from_with_an_explicit_size_is_refused(client):
    ups = landscape_then_portrait(client)
    refused = client.post("/api/runs", json=edit_body("x", [ref(u) for u in ups], shape_from=1, width=512, height=512))
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["body", "options", "shape_from"]
    assert client.get(ups[0]["url"]).status_code == 200  # a refused request consumes nothing


def test_shape_from_must_name_one_of_the_images(client):
    ups = landscape_then_portrait(client)
    for bad in (0, 3, -1):
        response = client.post("/api/runs", json=edit_body("x", [ref(u) for u in ups], shape_from=bad))
        assert response.status_code == 422 and response.json()["detail"][0]["loc"] == ["body", "options", "shape_from"], bad


def test_a_mask_never_sets_the_shape(client):
    picture, mask = stage(client, image_bytes(RED, (300, 400))), stage(client, image_bytes(GREEN, (400, 300)))
    refs = [ref(picture), {**ref(mask), "role": "mask"}]  # the mask is last, which is what sets the shape on Auto
    run = wait_for(client, create_edit(client, "x", refs)["id"])
    assert open_result(client, run).size == (896, 1184)  # the picture's shape, not the mask's
    assert [i["role"] for i in run["inputs"]] == ["reference", "mask"] and run["options"]["roles"] == ["reference", "mask"]
    refused = client.post("/api/runs", json=edit_body("x", [ref(stage(client, image_bytes(RED))), {**ref(stage(client, image_bytes(GREEN))), "role": "mask"}], shape_from=2))
    assert refused.status_code == 422 and "mask" in refused.json()["detail"][0]["msg"]


def test_masks_alone_are_not_an_edit(client):
    only = client.post("/api/runs", json=edit_body("x", [{**ref(stage(client, image_bytes())), "role": "mask"}]))
    assert only.status_code == 422 and only.json()["detail"][0]["loc"] == ["body", "input_images"]


def test_resolution_is_1024_or_2048_and_only_for_edits(client):
    up = stage(client, image_bytes())
    assert client.post("/api/runs", json=edit_body("x", [ref(up)], resolution=1500)).status_code == 422
    assert client.post("/api/runs", json={"prompt": "x", "options": {"resolution": 1024}}).status_code == 422
    assert client.post("/api/runs", json={"prompt": "x", "options": {"shape_from": 1}}).status_code == 422
    assert client.get(up["url"]).status_code == 200


# ------------------------------------------------------------------ transparency
def test_transparent_is_allowed_in_an_edit_and_wraps_the_prompt(client):
    run = wait_for(client, create_edit(client, "a cat on a sofa", [ref(stage(client, image_bytes()))], transparent=True)["id"])
    assert run["effective_prompt"].startswith("This is an RGBA image with transparency.") and run["prompt"] == "a cat on a sofa"
    assert run["images"][0]["has_alpha"] is True


def test_an_alpha_input_arrives_with_its_alpha(client):
    rgba = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    run = create_edit(client, "x", [ref(stage(client, buffer.getvalue()))])
    stored = Image.open(io.BytesIO(client.get(run["inputs"][0]["url"]).content))
    assert run["inputs"][0]["has_alpha"] is True and stored.mode == "RGBA" and stored.getpixel((1, 1)) == (255, 0, 0, 0)
    wait_for(client, run["id"])


# ------------------------------------------------------------------ requests that are wrong, with the position named
def test_each_bad_image_is_named_by_its_position_and_nothing_is_consumed(client):
    good = stage(client, image_bytes(RED))
    refs = [ref(good), {"upload_id": "a" * 32}, {"image_id": "b" * 32}]
    response = client.post("/api/runs", json=edit_body("x", refs))
    assert response.status_code == 422
    errors = {tuple(e["loc"]): e["msg"] for e in response.json()["detail"]}
    assert ("body", "input_images", 0) not in errors
    assert errors[("body", "input_images", 1)].startswith("Image 2: that upload was already used, or has expired")
    assert errors[("body", "input_images", 2)].startswith("Image 3: that image no longer exists")
    assert client.get(good["url"]).status_code == 200  # the good one is still waiting
    assert client.app.state.db.known_ids()[0] == set()  # and no run was made


@pytest.mark.parametrize("item,fragment", [
    ({}, "give either an upload_id or an image_id"),
    ({"upload_id": "a" * 32, "image_id": "b" * 32}, "give either an upload_id or an image_id"),
    ({"upload_id": "../../etc/passwd"}, "isn't a valid id"),
    ({"image_id": "XYZ"}, "isn't a valid id"),
])
def test_an_input_names_one_image_by_a_proper_id(client, item, fragment):
    response = client.post("/api/runs", json=edit_body("x", [item]))
    assert response.status_code == 422 and fragment in response.json()["detail"][0]["msg"]
    assert response.json()["detail"][0]["loc"] == ["body", "input_images", 0]


def test_the_number_of_images_is_limited_by_the_server(client_factory):
    client = client_factory(max_input_images=2)
    ups = [stage(client, image_bytes(c)) for c in (RED, GREEN, BLUE)]
    response = client.post("/api/runs", json=edit_body("x", [ref(u) for u in ups]))
    assert response.status_code == 422 and "at most 2 images" in response.json()["detail"][0]["msg"]
    assert client.post("/api/runs", json=edit_body("x", [])).status_code == 422
    assert client.post("/api/runs", json={"mode": "edit", "prompt": "x"}).status_code == 422
    run = create_edit(client, "x", [ref(u) for u in ups[:2]])  # at the cap is fine
    assert len(run["inputs"]) == 2
    wait_for(client, run["id"])


def test_images_in_generate_mode_are_refused(client):
    up = stage(client, image_bytes())
    response = client.post("/api/runs", json={"prompt": "x", "input_images": [ref(up)]})
    assert response.status_code == 422 and response.json()["detail"][0]["loc"] == ["body", "input_images"]


def test_an_upload_can_only_be_used_once(client):
    up = stage(client, image_bytes())
    wait_for(client, create_edit(client, "first", [ref(up)])["id"])
    again = client.post("/api/runs", json=edit_body("second", [ref(up)]))
    assert again.status_code == 422 and "already used" in again.json()["detail"][0]["msg"]


def test_the_same_upload_twice_in_one_edit_is_two_inputs_and_one_claim(client):
    up = stage(client, image_bytes(RED))
    run = create_edit(client, "x", [ref(up), ref(up)])
    assert [i["position"] for i in run["inputs"]] == [1, 2] and run["inputs"][0]["id"] != run["inputs"][1]["id"]
    assert inputs_on_disk(client, run["id"]) == ["1.png", "2.png"]
    assert strip_colours(open_result(client, wait_for(client, run["id"])), 2) == [RED, RED]


# ------------------------------------------------------------------ nothing is left behind when a submit fails
def test_a_full_queue_refuses_the_edit_and_keeps_the_uploads_for_another_try(client_factory):
    client = client_factory(queue_cap=1, fake_step_delay_ms=30)
    first = create_run(client, steps=40)
    wait_for(client, first["id"], frozenset({"running"}))
    second = create_run(client, steps=40)  # the one place in the queue
    up = stage(client, image_bytes(RED))
    full = client.post("/api/runs", json=edit_body("x", [ref(up)]))
    assert full.status_code == 429 and full.json()["code"] == "queue_full"
    storage = client.app.state.storage
    assert [p.name for p in storage.inputs.iterdir()] == ["staged"]  # no half-made run folder
    assert client.get(up["url"]).status_code == 200  # the upload waits
    wait_for(client, first["id"])
    wait_for(client, second["id"])
    run = create_edit(client, "x", [ref(up)])  # and the retry works with the same upload
    assert wait_for(client, run["id"])["status"] == "done"


def test_a_missing_image_file_is_named_and_leaves_the_upload_row_alone(client):
    up = stage(client, image_bytes())
    (client.app.state.storage.staged / f"{up['upload_id']}.png").unlink()
    response = client.post("/api/runs", json=edit_body("x", [ref(up)]))
    assert response.status_code == 422 and "file is missing" in response.json()["detail"][0]["msg"]
    assert [p.name for p in client.app.state.storage.inputs.iterdir()] == ["staged"]


def test_a_full_disk_while_copying_is_a_507_with_nothing_left_and_the_upload_kept(client, monkeypatch):
    ups = [stage(client, image_bytes(c)) for c in (RED, GREEN)]
    real_copy = shutil.copyfile
    calls = []

    def flaky(src, dst, *args, **kwargs):
        calls.append(dst)
        if len(calls) == 3:  # image 1 and its thumbnail were copied; image 2 is the one that runs out of space
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(inputs_module.shutil, "copyfile", flaky)
    response = client.post("/api/runs", json=edit_body("x", [ref(u) for u in ups]))
    assert response.status_code == 507 and response.json()["code"] == "storage_full"
    assert "No space left" in response.json()["detail"]
    assert [p.name for p in client.app.state.storage.inputs.iterdir()] == ["staged"]  # the first copy was cleaned up
    assert all(client.get(u["url"]).status_code == 200 for u in ups)


def test_a_database_failure_after_copying_cleans_up_the_copies(client, monkeypatch):
    up = stage(client, image_bytes())

    def broken(*args, **kwargs):
        raise RuntimeError("database trouble")

    monkeypatch.setattr(client.app.state.db, "insert_run", broken)
    with pytest.raises(RuntimeError):
        client.post("/api/runs", json=edit_body("x", [ref(up)]))
    assert [p.name for p in client.app.state.storage.inputs.iterdir()] == ["staged"]
    assert client.get(up["url"]).status_code == 200


# ------------------------------------------------------------------ a run owns its inputs (criterion 24)
def test_editing_a_past_result_copies_it_and_deleting_the_original_breaks_nothing(client):
    original = wait_for(client, create_run(client, "a lighthouse")["id"])
    result_id = original["images"][0]["id"]
    edit = wait_for(client, create_edit(client, "make it dusk", [{"image_id": result_id}])["id"])
    assert edit["status"] == "done"
    owned = edit["inputs"][0]
    assert owned["id"] != result_id and (owned["width"], owned["height"]) == (256, 256)
    assert inputs_on_disk(client, edit["id"]) == ["1.png"]

    assert client.delete(f"/api/runs/{original['id']}").status_code == 204  # the source goes away ...
    assert client.get(owned["url"]).status_code == 200 and client.get(owned["thumb_url"]).status_code == 200  # ... the copy stays

    again = wait_for(client, create_edit(client, "and again", [{"image_id": owned["id"]}])["id"])  # a past *input* works too
    assert again["status"] == "done" and again["inputs"][0]["id"] not in (owned["id"], result_id)
    assert client.delete(f"/api/runs/{edit['id']}").status_code == 204
    assert client.get(again["inputs"][0]["url"]).status_code == 200


def test_an_image_id_must_belong_to_a_run(client):
    up = stage(client, image_bytes())  # still staged: it has no run, so it can only be named as an upload
    response = client.post("/api/runs", json=edit_body("x", [{"image_id": up["upload_id"]}]))
    assert response.status_code == 422 and "no longer exists" in response.json()["detail"][0]["msg"]


# ------------------------------------------------------------------ delete, expiry, cancel (criteria 31 and 32)
def test_deleting_an_edit_removes_its_inputs_from_disk_and_database(client):
    run = wait_for(client, create_edit(client, "x", [ref(stage(client, image_bytes(c))) for c in (RED, GREEN)])["id"])
    ids = [i["id"] for i in run["inputs"]]
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    storage, db = client.app.state.storage, client.app.state.db
    assert inputs_on_disk(client, run["id"]) == [] and not (storage.thumbs / run["id"]).exists()
    assert db.inputs_for_runs([run["id"]])[run["id"]] == [] and all(db.get_image(i) is None for i in ids)
    assert all(client.get(f"/api/images/{i}").status_code == 404 for i in ids)


def age(client, *run_ids: str, days: float = 40) -> None:
    with client.app.state.db.tx() as c:
        for run_id in run_ids:
            c.execute("UPDATE runs SET created_at=? WHERE id=?", (days_ago(days), run_id))


def sweep(client) -> int:
    return client.portal.call(client.app.state.jobs.sweep_expired)


def test_expiry_takes_the_inputs_with_the_run_and_a_kept_run_keeps_them(quiet_client):
    client = quiet_client
    doomed = wait_for(client, create_edit(client, "x", [ref(stage(client, image_bytes(RED)))])["id"])
    kept = wait_for(client, create_edit(client, "y", [ref(stage(client, image_bytes(GREEN)))])["id"])
    client.patch(f"/api/runs/{kept['id']}", json={"pinned": True})
    age(client, doomed["id"], kept["id"])
    assert sweep(client) == 1
    assert client.get(f"/api/runs/{doomed['id']}").status_code == 404
    assert inputs_on_disk(client, doomed["id"]) == [] and client.get(doomed["inputs"][0]["url"]).status_code == 404
    assert inputs_on_disk(client, kept["id"]) == ["1.png"] and client.get(kept["inputs"][0]["url"]).status_code == 200


def test_an_input_copied_into_a_newer_run_survives_its_sources_expiry(quiet_client):
    client = quiet_client
    source = wait_for(client, create_edit(client, "x", [ref(stage(client, image_bytes(RED)))])["id"])
    newer = wait_for(client, create_edit(client, "y", [{"image_id": source["inputs"][0]["id"]}])["id"])
    age(client, source["id"])
    assert sweep(client) == 1
    assert client.get(f"/api/runs/{source['id']}").status_code == 404
    assert inputs_on_disk(client, newer["id"]) == ["1.png"] and client.get(newer["inputs"][0]["url"]).status_code == 200


def test_cancelling_an_edit_stops_it_and_keeps_its_inputs_until_it_is_deleted(client_factory):
    client = client_factory(fake_step_delay_ms=20)
    run = create_edit(client, "x", [ref(stage(client, image_bytes(RED)))], steps=40, num_images=3)
    wait_for(client, run["id"], frozenset({"running"}))
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code in (200, 202)
    final = wait_for(client, run["id"])
    assert final["status"] == "canceled" and len(final["images"]) < 3
    assert inputs_on_disk(client, run["id"]) == ["1.png"] and client.get(final["inputs"][0]["url"]).status_code == 200
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204 and inputs_on_disk(client, run["id"]) == []


def test_retry_and_reuse_can_name_a_past_runs_inputs(client):
    first = wait_for(client, create_edit(client, "x", [ref(stage(client, image_bytes(c))) for c in (RED, GREEN)])["id"])
    again = wait_for(client, create_edit(client, "x", [{"image_id": i["id"]} for i in first["inputs"]])["id"])
    assert strip_colours(open_result(client, again), 2) == [RED, GREEN]
    assert [i["id"] for i in again["inputs"]] != [i["id"] for i in first["inputs"]]  # copies, in the same order


# ------------------------------------------------------------------ what the page is told
def test_capabilities_describe_edits_but_do_not_offer_the_mode_yet(client_factory):
    caps = client_factory(max_input_images=6).get("/api/capabilities").json()
    assert caps["limits"]["input_images"] == {"min": 1, "max": 6}
    assert caps["limits"]["resolutions"] == [1024, 2048] and caps["limits"]["upload_mb"] == 20
    assert caps["supports"]["edit"] is True and caps["supports"]["multi_image"] is True
    # The API accepts edits, but the page cannot make one until M5b, so it must not offer the mode yet.
    assert caps["modes"] == ["generate"]


# ------------------------------------------------------------------ gaps the mutation checks found, and the paths changed after review
def test_the_clean_up_never_touches_the_inputs_of_a_run_that_exists_however_old_they_are(quiet_client):
    client = quiet_client
    """Every edit's input folder is older than the sweep's one-hour grace period before long: it must stay because
    the database owns it, not because it is young."""
    run = wait_for(client, create_edit(client, "x", [ref(stage(client, image_bytes(c))) for c in (RED, GREEN)])["id"])
    storage = client.app.state.storage
    long_ago = time.time() - 3 * 3600
    for path in [storage.inputs / run["id"], *(storage.inputs / run["id"]).iterdir(), *(storage.thumbs / run["id"]).iterdir()]:
        os.utime(path, (long_ago, long_ago))
    assert client.portal.call(client.app.state.jobs.sweep_uploads) == 0
    assert inputs_on_disk(client, run["id"]) == ["1.png", "2.png"]
    assert all(client.get(i["url"]).status_code == 200 for i in run["inputs"])


def test_an_upload_id_cannot_name_a_runs_own_image(client):
    first = wait_for(client, create_edit(client, "x", [ref(stage(client, image_bytes(RED)))])["id"])
    owned = first["inputs"][0]["id"]
    response = client.post("/api/runs", json=edit_body("y", [{"upload_id": owned}]))
    assert response.status_code == 422 and "already used" in response.json()["detail"][0]["msg"]
    assert client.get(first["inputs"][0]["url"]).status_code == 200 and inputs_on_disk(client, first["id"]) == ["1.png"]
    assert sorted(p.name for p in client.app.state.storage.inputs.iterdir()) == sorted(["staged", first["id"]])  # nothing new


def test_a_file_that_vanishes_while_copying_names_its_position_and_keeps_every_upload(client, monkeypatch):
    ups = [stage(client, image_bytes(c)) for c in (RED, GREEN, BLUE)]
    real_copy = shutil.copyfile
    calls = []

    def vanishing(src, dst, *args, **kwargs):
        calls.append(dst)
        if len(calls) == 5:  # images 1 and 2 and their thumbnails went through; image 3's file is gone
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(src))
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(inputs_module.shutil, "copyfile", vanishing)
    response = client.post("/api/runs", json=edit_body("x", [ref(u) for u in ups]))
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "input_images", 2]
    assert response.json()["detail"][0]["msg"].startswith("Image 3: the image file is missing")
    assert [p.name for p in client.app.state.storage.inputs.iterdir()] == ["staged"]  # the copies of 1 and 2 are gone
    assert all(client.get(u["url"]).status_code == 200 for u in ups)


def test_an_operating_system_file_error_while_copying_is_not_mistaken_for_a_missing_image(client, monkeypatch):
    up = stage(client, image_bytes(RED))

    def broken(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(inputs_module.shutil, "copyfile", broken)
    response = client.post("/api/runs", json=edit_body("x", [ref(up)]))
    assert response.status_code == 507 and "Permission denied" in response.json()["detail"]
    assert client.get(up["url"]).status_code == 200


def test_a_staged_file_that_cannot_be_removed_after_the_run_exists_does_not_fail_the_submit(client, monkeypatch):
    up = stage(client, image_bytes(RED))

    def stuck(upload_id):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(client.app.state.storage, "delete_staged_files", stuck)
    run = create_edit(client, "x", [ref(up)])  # still a 201: the run exists and is queued
    assert wait_for(client, run["id"])["status"] == "done"
    assert client.get(up["url"]).status_code == 404  # the database row is gone, whatever the file is doing
