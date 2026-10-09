"""Make 4K through the API (DESIGN.md §27.3; criteria 78-86): the routes, the payload fields, what is kept, and what
goes wrong. Pictures come from the fake pipeline at real 16:9 sizes."""

from __future__ import annotations

import asyncio
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import close, create_edit, create_run, image_bytes, stage, wait_for
from PIL import Image

from studio import fourk

WIDE = {"width": 2560, "height": 1440, "steps": 2}  # exactly 16:9, 3.7 MP: inside the studio's limit


def wide_run(client, prompt: str = "a harbour at dawn", **options) -> dict:
    run = create_run(client, prompt, **{**WIDE, **options})
    return wait_for(client, run["id"])


def make(client, image: dict, expect: int | None = None):
    response = client.post(f"/api/images/{image['id']}/4k")
    if expect is not None:
        assert response.status_code == expect, response.text
    return response


def image_of(client, run_id: str, index: int = 0) -> dict:
    return client.get(f"/api/runs/{run_id}").json()["images"][index]


# ------------------------------------------------------------------ what a picture says about itself
def test_a_16_9_result_says_it_can_be_made_4k_and_has_no_4k_copy_yet(client):
    image = wide_run(client)["images"][0]
    assert image["can_4k"] is True and image["four_k"] is None


def test_other_shapes_and_small_pictures_say_they_cannot(client):
    square = wait_for(client, create_run(client, "square")["id"])["images"][0]
    small_wide = wait_for(client, create_run(client, "draft", width=1536, height=864, steps=2)["id"])["images"][0]
    assert square["can_4k"] is False and square["four_k"] is None
    assert small_wide["can_4k"] is False  # 1536x864 is exactly 16:9, but under 1920 wide


def test_every_run_in_the_listing_carries_the_fields(client):
    wide_run(client)
    for run in client.get("/api/runs").json()["runs"]:
        assert all("can_4k" in image and "four_k" in image for image in run["images"])


def test_an_edits_result_can_be_made_4k_but_its_sources_are_not_results(client):
    staged = stage(client, image_bytes(size=(64, 64)))
    done = wait_for(client, create_edit(client, "make it moody", [{"upload_id": staged["upload_id"]}], width=2560, height=1440)["id"])
    assert done["images"][0]["can_4k"] is True
    assert "can_4k" not in done["inputs"][0]
    assert make(client, {"id": done["inputs"][0]["id"]}).status_code == 404  # a source is not a result


# ------------------------------------------------------------------ making it
def test_post_makes_the_4k_copy_and_returns_the_updated_run(client):
    run = wide_run(client)
    original = client.get(run["images"][0]["url"]).content
    response = make(client, run["images"][0], 201)
    updated = response.json()
    assert updated["id"] == run["id"] and updated["status"] == "done"
    four_k = updated["images"][0]["four_k"]
    assert (four_k["width"], four_k["height"]) == (3840, 2160) and four_k["bytes"] > 100_000
    assert four_k["url"] == f"/api/images/{run['images'][0]['id']}/4k"
    assert four_k["download_url"] == four_k["url"] + "?download=1"
    served = client.get(four_k["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert len(served.content) == four_k["bytes"]
    assert Image.open(io.BytesIO(served.content)).size == (3840, 2160)
    assert client.get(run["images"][0]["url"]).content == original  # the original is untouched


def test_asking_again_finds_it_and_does_not_make_it_again(client):
    run = wide_run(client)
    make(client, run["images"][0], 201)
    path = next(client.app.state.storage.images.rglob("*-4k.png"))
    before = (path.stat().st_mtime_ns, path.stat().st_size)
    again = make(client, run["images"][0], 200)
    assert again.json()["images"][0]["four_k"]["bytes"] == before[1]
    assert (path.stat().st_mtime_ns, path.stat().st_size) == before


def test_the_copy_sits_beside_the_original_in_the_runs_folder(client):
    run = wide_run(client)
    make(client, run["images"][0], 201)
    folder = client.app.state.storage.images / run["id"]
    assert sorted(p.name for p in folder.iterdir()) == ["0-4k.png", "0.png"]


def test_a_run_with_several_images_has_a_copy_for_each_one_asked_for(client):
    run = wide_run(client, num_images=2)
    make(client, run["images"][1], 201)
    shown = client.get(f"/api/runs/{run['id']}").json()["images"]
    assert shown[0]["four_k"] is None and shown[1]["four_k"] is not None


def test_the_copy_is_still_there_after_a_reload_and_after_a_restart(client, client_factory):
    run = wide_run(client)
    made = make(client, run["images"][0], 201).json()["images"][0]["four_k"]
    assert client.get("/api/runs").json()["runs"][0]["images"][0]["four_k"] == made  # a reload
    close(client)
    restarted = client_factory()
    assert restarted.get(f"/api/runs/{run['id']}").json()["images"][0]["four_k"] == made
    assert restarted.get(made["url"]).status_code == 200


def test_the_download_is_named_like_the_image_with_the_4k_size(client):
    run = wide_run(client, prompt="a harbour at dawn")
    image = run["images"][0]
    make(client, image, 201)
    plain = client.get(f"/api/images/{image['id']}/4k")
    assert "content-disposition" not in plain.headers
    named = client.get(f"/api/images/{image['id']}/4k?download=1")
    header = named.headers["content-disposition"]
    assert header.startswith("attachment;") and "3840x2160" in header and ".png" in header
    assert "harbour" in header and "2560x1440" not in header
    original = client.get(f"/api/images/{image['id']}?download=1").headers["content-disposition"]
    assert original.replace("2560x1440", "3840x2160") == header  # the same scheme, only the size differs


def test_the_png_text_of_the_original_comes_with_it(client):
    run = wide_run(client, prompt="a harbour at dawn")
    image = run["images"][0]
    make(client, image, 201)
    text = Image.open(io.BytesIO(client.get(f"/api/images/{image['id']}/4k").content)).text
    assert "make4k" in text and "no detail" in text["make4k"]


def test_a_transparent_run_gives_a_transparent_4k_picture(client):
    run = wide_run(client, transparent=True)
    make(client, run["images"][0], 201)
    assert Image.open(io.BytesIO(client.get(f"/api/images/{run['images'][0]['id']}/4k").content)).mode == "RGBA"


# ------------------------------------------------------------------ not made yet / not allowed
def test_getting_a_copy_that_has_not_been_made_is_a_404(client):
    image = wide_run(client)["images"][0]
    response = client.get(f"/api/images/{image['id']}/4k")
    assert response.status_code == 404 and response.json()["code"] == "not_found"


def test_an_unknown_image_is_a_404_for_both_verbs(client):
    unknown = "0" * 32
    assert client.post(f"/api/images/{unknown}/4k").status_code == 404
    assert client.get(f"/api/images/{unknown}/4k").status_code == 404
    assert client.post("/api/images/not-an-id/4k").status_code == 404


def test_a_picture_that_is_not_16_9_is_refused_with_the_reason(client):
    square = wait_for(client, create_run(client, "square")["id"])["images"][0]
    response = make(client, square)
    assert response.status_code == 422
    assert response.json()["code"] == "not_4k_eligible" and "16:9" in response.json()["detail"]
    assert not list(client.app.state.storage.images.rglob("*-4k.png"))


def test_a_16_9_picture_that_is_too_small_says_so(client):
    small = wait_for(client, create_run(client, "draft", width=1536, height=864, steps=2)["id"])["images"][0]
    response = make(client, small)
    assert response.status_code == 422 and "too small" in response.json()["detail"]
    assert response.json()["code"] == "not_4k_eligible"


def test_a_source_image_has_no_4k_copy_to_get_either(client):
    staged = stage(client, image_bytes(size=(64, 64)))
    done = wait_for(client, create_edit(client, "x", [{"upload_id": staged["upload_id"]}], width=2560, height=1440)["id"])
    assert client.get(f"/api/images/{done['inputs'][0]['id']}/4k").status_code == 404


def test_a_post_without_the_client_header_is_refused_like_every_mutation(client):
    image = wide_run(client)["images"][0]
    response = client.post(f"/api/images/{image['id']}/4k", headers={"X-Studio-Client": ""})
    assert response.status_code == 403
    assert not list(client.app.state.storage.images.rglob("*-4k.png"))


# ------------------------------------------------------------------ things that go wrong
def test_a_full_disk_is_a_507_and_leaves_no_copy(client, monkeypatch):
    run = wide_run(client)

    def full_disk(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fourk, "make_4k", full_disk)
    response = make(client, run["images"][0])
    assert response.status_code == 507 and response.json()["code"] == "storage_full"
    assert "No space left" in response.json()["detail"]
    assert client.get(f"/api/runs/{run['id']}").json()["images"][0]["four_k"] is None


def test_a_damaged_original_is_a_422_unreadable(client):
    run = wide_run(client)
    path = client.app.state.storage.images / run["id"] / "0.png"
    path.write_bytes(b"this is not a png")
    response = make(client, run["images"][0])
    assert response.status_code == 422 and response.json()["code"] == "unreadable"


def test_an_original_that_has_gone_missing_is_a_404(client):
    run = wide_run(client)
    (client.app.state.storage.images / run["id"] / "0.png").unlink()
    assert make(client, run["images"][0]).status_code == 404


def test_the_copy_is_removed_with_its_run(client):
    run = wide_run(client)
    image = run["images"][0]
    make(client, image, 201)
    folder = client.app.state.storage.images / run["id"]
    assert (folder / "0-4k.png").is_file()
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not folder.exists()
    assert client.get(f"/api/images/{image['id']}/4k").status_code == 404


def test_keep_does_not_disturb_the_copy(client):
    run = wide_run(client)
    made = make(client, run["images"][0], 201).json()["images"][0]["four_k"]
    kept = client.patch(f"/api/runs/{run['id']}", json={"pinned": True}).json()
    assert kept["pinned"] is True and kept["images"][0]["four_k"] == made


# ------------------------------------------------------------------ overlapping requests, other pages, the worker
def test_overlapping_requests_make_one_file_and_only_one_of_them_made_it(client, monkeypatch):
    run = wide_run(client)
    image_id = run["images"][0]["id"]
    calls: list[str] = []
    real = fourk.make_4k

    def slow(src, dst):
        calls.append(threading.current_thread().name)
        time.sleep(0.4)  # long enough for the second request to arrive while the first is being made
        return real(src, dst)

    monkeypatch.setattr(fourk, "make_4k", slow)
    jobs = client.app.state.jobs

    async def both():
        return await asyncio.gather(jobs.make_4k(image_id), jobs.make_4k(image_id), jobs.make_4k(image_id))

    results = client.portal.call(both)
    assert len(calls) == 1
    assert sorted(made for _, made in results) == [False, False, True]
    assert len({r["images"][0]["four_k"]["bytes"] for r, _ in results}) == 1
    assert not list(client.app.state.storage.images.rglob("*.part"))


def test_overlapping_http_requests_answer_201_once_and_200_for_the_rest(client):
    image = wide_run(client)["images"][0]
    with ThreadPoolExecutor(3) as pool:
        codes = sorted(pool.map(lambda _: make(client, image).status_code, range(3)))
    assert codes.count(201) == 1 and codes.count(200) == 2
    assert len(list(client.app.state.storage.images.rglob("*-4k.png"))) == 1


def test_a_request_after_the_first_has_finished_is_a_plain_200(client):
    image = wide_run(client)["images"][0]
    make(client, image, 201)
    assert make(client, image, 200).status_code == 200


def test_other_open_pages_are_told_with_a_run_updated_event(client):
    run = wide_run(client)
    sub = client.app.state.bus.subscribe()
    make(client, run["images"][0], 201)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    updated = [data for name, data in events if name == "run.updated" and data["id"] == run["id"]]
    assert len(updated) == 1 and updated[0]["images"][0]["four_k"]["height"] == 2160
    client.app.state.bus.unsubscribe(sub)


def test_finding_a_copy_that_exists_sends_no_event(client):
    run = wide_run(client)
    make(client, run["images"][0], 201)
    sub = client.app.state.bus.subscribe()
    make(client, run["images"][0], 200)
    assert sub.queue.empty()
    client.app.state.bus.unsubscribe(sub)


def test_it_works_while_another_run_is_generating(client_factory):
    client = client_factory(fake_step_delay_ms=120)
    done = wide_run(client)
    busy = create_run(client, "a long one", steps=40)  # 40 steps at 120 ms: several seconds
    wait_for(client, busy["id"], frozenset({"running"}))
    started = time.monotonic()
    make(client, done["images"][0], 201)
    assert client.get(f"/api/runs/{busy['id']}").json()["status"] == "running"  # still going: 4K did not wait for it
    assert time.monotonic() - started < 4
    client.post(f"/api/runs/{busy['id']}/cancel")
    wait_for(client, busy["id"])


def test_the_database_schema_is_unchanged(client):
    from studio.db import SCHEMA_VERSION

    assert SCHEMA_VERSION == 3  # a 4K copy is a file beside the image; there is no row for it (DESIGN.md §27.3)
