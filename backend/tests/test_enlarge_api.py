"""Enlarge through the API (DESIGN.md §28; criteria 94, 97-102): the route, the payload fields, what is kept and what replaces what,
and what goes wrong. Pictures come from the fake pipeline at real sizes; the upscaler is the fake one, or a stub whose behaviour a test
chooses. The rules are in test_enlarge.py, the process handling in test_upscaler.py."""

from __future__ import annotations

import asyncio
import io
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pytest
from conftest import create_edit, create_run, image_bytes, stage, wait_for
from PIL import Image

from studio.config import Settings
from studio.upscaler import Availability, FakeUpscaler, ModelUpscaler, UpscaleFailed, UpscaleTimeout, UpscalerBusy, UpscalerUnavailable

WIDE = {"width": 2560, "height": 1440, "steps": 2}  # 1.5x to the frame: one pass


def wide_run(client, prompt: str = "a harbour at dawn", **options) -> dict:
    return wait_for(client, create_run(client, prompt, **{**WIDE, **options})["id"])


def enlarge(client, image: dict, expect: Optional[int] = None):
    response = client.post(f"/api/images/{image['id']}/enlarge")
    if expect is not None:
        assert response.status_code == expect, response.text
    return response


def make_4k(client, image: dict, expect: Optional[int] = None):
    response = client.post(f"/api/images/{image['id']}/4k")
    if expect is not None:
        assert response.status_code == expect, response.text
    return response


def edit_with_source(client, size=(1280, 720), prompt="make it moody") -> dict:
    staged = stage(client, image_bytes(size=size))
    return wait_for(client, create_edit(client, prompt, [{"upload_id": staged["upload_id"]}], width=2560, height=1440)["id"])


class Stub:
    """An upscaler whose behaviour the test chooses; by default it enlarges with the fake one, so there is a file to look at."""

    def __init__(self, available: bool = True, error: Optional[BaseException] = None, delay: float = 0.0):
        self.available, self.error, self.delay = available, error, delay
        self.calls: list[str] = []
        self.running = self.peak = 0

    def availability(self) -> Availability:
        return Availability(True, model="stub") if self.available else Availability(False, "stub", "No upscaler model here.", "Download it, once.")

    async def enlarge(self, src, dst) -> None:
        self.calls.append(src.name)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            await FakeUpscaler().enlarge(src, dst)
        finally:
            self.running -= 1


def use(client, stub: Stub) -> Stub:
    client.app.state.jobs.upscaler = stub
    return stub


# ------------------------------------------------------------------ what a picture says about itself
def test_a_16_9_result_says_it_can_be_enlarged_and_what_it_would_make(client):
    image = wide_run(client)["images"][0]
    assert image["can_enlarge"] is True
    assert image["enlarge_size"] == {"width": 3840, "height": 2160, "trimmed": False, "passes": 1}
    assert image["four_k"] is None


def test_the_enlarge_fields_cover_every_kind_of_picture(client):
    seen = {}
    for width, height in ((2752, 1536), (1376, 768), (960, 544), (2048, 2048), (1536, 2752), (512, 512)):
        image = wait_for(client, create_run(client, f"{width}x{height}", width=width, height=height, steps=2)["id"])["images"][0]
        seen[(width, height)] = (image["can_4k"], image["can_enlarge"], image["enlarge_size"])
    assert seen[(2752, 1536)] == (True, True, {"width": 3840, "height": 2160, "trimmed": True, "passes": 1})
    assert seen[(1376, 768)] == (False, True, {"width": 3840, "height": 2160, "trimmed": True, "passes": 1})  # 2.8x: Enlarge, not Make 4K
    assert seen[(960, 544)] == (False, True, {"width": 3840, "height": 2160, "trimmed": True, "passes": 2})
    assert seen[(2048, 2048)] == (True, True, {"width": 3840, "height": 3840, "trimmed": False, "passes": 1})
    assert seen[(1536, 2752)] == (True, True, {"width": 2160, "height": 3840, "trimmed": True, "passes": 1})
    assert seen[(512, 512)] == (False, False, None)  # 7.5x: neither


def test_an_edits_source_says_the_same_things(client):
    done = edit_with_source(client, size=(1280, 720))
    source = done["inputs"][0]
    assert source["can_4k"] is False and source["can_enlarge"] is True
    assert source["enlarge_size"] == {"width": 3840, "height": 2160, "trimmed": False, "passes": 1}


def test_capabilities_say_whether_the_upscaler_is_available(client, tmp_path):
    assert client.get("/api/capabilities").json()["upscaler"] == {
        "available": True, "model": "fake", "reason": None, "hint": None, "max_enlargement": 4}
    use(client, ModelUpscaler(Settings(data_dir=tmp_path, pipeline="real", upscaler_model=tmp_path / "nowhere.pth")))
    status = client.get("/api/capabilities").json()["upscaler"]
    assert status["available"] is False and "nowhere.pth" in status["reason"] and "curl" in status["hint"] and status["max_enlargement"] == 4


# ------------------------------------------------------------------ making the copy
def test_post_makes_the_enlarged_copy_and_returns_the_updated_run(client):
    run = wide_run(client)
    response = enlarge(client, run["images"][0], 201)
    image = response.json()["images"][0]
    copy = image["four_k"]
    assert (copy["width"], copy["height"], copy["method"]) == (3840, 2160, "model")
    assert copy["url"] == f"/api/images/{image['id']}/4k?method=model"
    assert copy["download_url"] == f"/api/images/{image['id']}/4k?method=model&download=1"
    assert copy["bytes"] > 0
    served = client.get(copy["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/png" and "immutable" in served.headers["cache-control"]
    with Image.open(io.BytesIO(served.content)) as made:
        assert made.size == (3840, 2160) and "upscaler model the fake upscaler" in made.text["make4k"]


def test_the_copy_sits_beside_the_original_and_is_named_for_the_method(client):
    run = wide_run(client)
    enlarge(client, run["images"][0], 201)
    folder = client.app.state.storage.images / run["id"]
    assert {p.name for p in folder.iterdir() if p.suffix == ".png"} == {"0.png", "0-4k-enlarged.png"}


def test_a_two_pass_picture_is_enlarged_too(client):
    run = wait_for(client, create_run(client, "small", width=960, height=544, steps=2)["id"])
    image = run["images"][0]
    assert image["enlarge_size"]["passes"] == 2
    updated = enlarge(client, image, 201).json()["images"][0]
    assert (updated["four_k"]["width"], updated["four_k"]["height"]) == (3840, 2160)


def test_asking_again_finds_it_and_does_not_make_it_again(client):
    stub = use(client, Stub())
    image = wide_run(client)["images"][0]
    first = enlarge(client, image, 201).json()["images"][0]["four_k"]
    second = enlarge(client, image, 200).json()["images"][0]["four_k"]
    assert first == second and len(stub.calls) == 1


def test_an_existing_copy_is_found_even_if_the_upscaler_has_gone_away(client):
    stub = use(client, Stub())
    image = wide_run(client)["images"][0]
    enlarge(client, image, 201)
    stub.available = False  # the model file was removed afterwards
    assert enlarge(client, image, 200).json()["images"][0]["four_k"]["method"] == "model"


def test_the_copy_is_still_there_after_a_reload_and_after_a_restart(client, client_factory):
    run = wide_run(client)
    enlarge(client, run["images"][0], 201)
    assert client.get(f"/api/runs/{run['id']}").json()["images"][0]["four_k"]["method"] == "model"
    again = client_factory()
    assert again.get(f"/api/runs/{run['id']}").json()["images"][0]["four_k"]["method"] == "model"


def test_the_download_is_named_like_the_image_with_the_copys_size(client):
    run = wide_run(client)
    copy = enlarge(client, run["images"][0], 201).json()["images"][0]["four_k"]
    header = client.get(copy["download_url"]).headers["content-disposition"]
    assert "3840x2160" in header and "2560x1440" not in header and header.startswith("attachment")


def test_a_transparent_result_stays_transparent(client):
    run = wait_for(client, create_run(client, "a cut-out", transparent=True, **WIDE)["id"])
    copy = enlarge(client, run["images"][0], 201).json()["images"][0]["four_k"]
    with Image.open(io.BytesIO(client.get(copy["url"]).content)) as made:
        assert made.mode == "RGBA" and made.size == (3840, 2160)


def test_the_png_text_of_the_original_comes_with_it(client):
    run = wide_run(client, "a harbour at dawn")
    original = Image.open(io.BytesIO(client.get(run["images"][0]["url"]).content))
    copy = enlarge(client, run["images"][0], 201).json()["images"][0]["four_k"]
    with Image.open(io.BytesIO(client.get(copy["url"]).content)) as made:
        for key, value in original.text.items():
            assert made.text[key] == value


# ------------------------------------------------------------------ one copy per picture
def test_an_enlarged_copy_replaces_a_make_4k_copy_and_the_old_address_serves_the_new_file(client):
    run = wide_run(client)
    folder = client.app.state.storage.images / run["id"]
    plain = make_4k(client, run["images"][0], 201).json()["images"][0]["four_k"]
    assert plain["method"] == "resize" and plain["url"].endswith("/4k") and "?" not in plain["url"]
    assert (folder / "0-4k.png").is_file()
    better = enlarge(client, run["images"][0], 201).json()["images"][0]["four_k"]
    assert better["method"] == "model" and better["url"] != plain["url"]  # a new address: a browser's immutable copy of the old one is not reused
    names = {p.name for p in folder.iterdir()}
    assert "0-4k-enlarged.png" in names and "0-4k.png" not in names
    with Image.open(io.BytesIO(client.get(plain["url"]).content)) as made:  # the old address answers with the better file
        assert made.text["make4k"].startswith("Enlarged with the upscaler model") and "Lanczos resize" not in made.text["make4k"]


def test_make_4k_after_an_enlarge_finds_the_better_copy_and_makes_no_plain_one(client):
    run = wide_run(client)
    enlarge(client, run["images"][0], 201)
    assert make_4k(client, run["images"][0], 200).json()["images"][0]["four_k"]["method"] == "model"
    assert not list(client.app.state.storage.images.rglob("*-4k.png"))


def test_a_plain_copy_shows_as_a_resize_one(client):
    run = wide_run(client)
    assert make_4k(client, run["images"][0], 201).json()["images"][0]["four_k"]["method"] == "resize"


def test_when_both_files_exist_the_enlarged_one_is_the_copy(client):
    """A Make 4K that finished just after an Enlarge leaves both files; the better one wins, in the payload and on the wire."""
    run = wide_run(client)
    enlarge(client, run["images"][0], 201)
    folder = client.app.state.storage.images / run["id"]
    Image.new("RGB", (3840, 2160)).save(folder / "0-4k.png")
    image = client.get(f"/api/runs/{run['id']}").json()["images"][0]
    assert image["four_k"]["method"] == "model"
    with Image.open(io.BytesIO(client.get(image["four_k"]["url"]).content)) as made:
        assert "upscaler model" in made.text["make4k"]


def test_an_edits_source_can_be_enlarged_beside_the_copy_the_run_owns(client):
    done = edit_with_source(client, size=(1280, 720))
    source = done["inputs"][0]
    updated = enlarge(client, source, 201).json()
    assert updated["inputs"][0]["four_k"]["method"] == "model" and updated["images"][0]["four_k"] is None
    folder = client.app.state.storage.inputs / done["id"]
    assert sorted(p.name for p in folder.iterdir()) == ["1-4k-enlarged.png", "1.png"]
    header = client.get(updated["inputs"][0]["four_k"]["download_url"]).headers["content-disposition"]
    assert "source-1_" in header and "3840x2160" in header


# ------------------------------------------------------------------ refusals
def test_a_picture_that_needs_too_big_an_enlargement_is_refused_with_the_reason(client):
    image = wait_for(client, create_run(client, "tiny", width=512, height=512, steps=2)["id"])["images"][0]
    response = enlarge(client, image, 422)
    assert response.json()["code"] == "not_enlarge_eligible"
    assert "7.5× enlargement" in response.json()["detail"] and "Enlarge goes up to 4×" in response.json()["detail"]


def test_an_ineligible_picture_is_refused_before_the_upscaler_is_asked(client):
    stub = use(client, Stub(available=False))  # would answer 503 if it were consulted
    image = wait_for(client, create_run(client, "tiny", width=512, height=512, steps=2)["id"])["images"][0]
    assert enlarge(client, image).status_code == 422 and stub.calls == []


def test_an_unknown_image_and_a_staged_upload_are_404_and_leave_no_file(client):
    assert client.post("/api/images/" + "0" * 32 + "/enlarge").status_code == 404
    assert client.post("/api/images/not-an-id/enlarge").status_code == 404
    staged = stage(client, image_bytes(size=(1280, 720)))
    assert client.post(f"/api/images/{staged['upload_id']}/enlarge").status_code == 404
    assert not list(client.app.state.storage.root.rglob("*-4k-enlarged.png"))


def test_a_post_without_the_client_header_is_refused_like_every_mutation(client):
    image = wide_run(client)["images"][0]
    assert client.post(f"/api/images/{image['id']}/enlarge", headers={"X-Studio-Client": ""}).status_code == 403
    assert not list(client.app.state.storage.images.rglob("*-4k-enlarged.png"))


def test_without_the_model_it_is_a_503_with_what_to_do_and_nothing_changes(client):
    use(client, Stub(available=False))
    run = wide_run(client)
    response = enlarge(client, run["images"][0], 503)
    assert response.json() == {"detail": "No upscaler model here.", "code": "upscaler_unavailable", "hint": "Download it, once."}
    assert client.get(f"/api/runs/{run['id']}").json()["images"][0]["four_k"] is None
    assert not list(client.app.state.storage.images.rglob("*-4k*.png"))


@pytest.mark.parametrize("error,status,code,hint", [
    (UpscaleFailed("The model could not be loaded: boom", "Check the model file."), 500, "upscale_failed", "Check the model file."),
    (UpscalerUnavailable("PyTorch cannot see a GPU", "Check the GPU."), 503, "upscaler_unavailable", "Check the GPU."),
    (UpscaleTimeout("Enlarging took longer than 30 minutes and was stopped.", "Use the GPU."), 504, "upscale_timeout", "Use the GPU."),
    (UpscaleFailed("Out of memory while enlarging."), 500, "upscale_failed", None),
    (UpscalerBusy("Not enough memory for the upscaler right now.", "Try again in a moment."), 503, "upscaler_busy", "Try again in a moment."),
])
def test_a_failure_of_the_upscaler_is_answered_with_its_status_reason_and_hint(client, error, status, code, hint):
    use(client, Stub(error=error))
    run = wide_run(client)
    body = enlarge(client, run["images"][0], status).json()
    assert body["code"] == code and body["detail"] == str(error) and body.get("hint") == hint
    assert client.get(f"/api/runs/{run['id']}").json()["images"][0]["four_k"] is None  # nothing was made
    assert not list(client.app.state.storage.images.rglob("*-4k-enlarged.png"))


def test_after_a_failed_enlargement_asking_again_tries_again(client):
    """A failed attempt must not be remembered: the person fixes the model file and presses Enlarge again."""
    stub = use(client, Stub(error=UpscaleFailed("The model could not be loaded: boom")))
    image = wide_run(client)["images"][0]
    assert enlarge(client, image, 500).json()["code"] == "upscale_failed"
    stub.error = None  # the file was replaced
    assert enlarge(client, image, 201).json()["images"][0]["four_k"]["method"] == "model"
    assert len(stub.calls) == 2 and client.app.state.jobs._enlarging == {}  # nothing is left in the list of enlargements under way


def test_a_full_disk_is_a_507(client):
    use(client, Stub(error=OSError(28, "No space left on device")))
    response = enlarge(client, wide_run(client)["images"][0], 507)
    assert response.json()["code"] == "storage_full" and "No space left on device" in response.json()["detail"]


def test_an_original_that_has_gone_missing_is_a_404(client):
    run = wide_run(client)
    (client.app.state.storage.images / run["id"] / "0.png").unlink()
    assert enlarge(client, run["images"][0], 404).json()["code"] == "not_found"


def test_a_damaged_original_is_a_422_unreadable(client):
    run = wide_run(client)
    (client.app.state.storage.images / run["id"] / "0.png").write_bytes(b"not a picture")
    response = enlarge(client, run["images"][0], 422)
    assert response.json()["code"] == "unreadable"


# ------------------------------------------------------------------ concurrency, events, housekeeping
def test_overlapping_requests_for_one_picture_make_one_copy_and_one_of_them_made_it(client):
    stub = use(client, Stub(delay=0.4))
    image = wide_run(client)["images"][0]
    with ThreadPoolExecutor(3) as pool:
        responses = list(pool.map(lambda _: enlarge(client, image), range(3)))
    assert sorted(r.status_code for r in responses) == [200, 200, 201]
    assert len(stub.calls) == 1
    assert all(r.json()["images"][0]["four_k"]["method"] == "model" for r in responses)
    assert len({r.json()["images"][0]["four_k"]["bytes"] for r in responses}) == 1


def test_only_one_enlargement_runs_at_a_time_across_pictures(client):
    stub = use(client, Stub(delay=0.3))
    first, second = wide_run(client, "one")["images"][0], wide_run(client, "two")["images"][0]
    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(lambda image: enlarge(client, image).status_code, (first, second)))
    assert codes == [201, 201] and len(stub.calls) == 2 and stub.peak == 1


def test_other_open_pages_are_told_once_with_a_run_updated_event(client):
    run = wide_run(client)
    sub = client.app.state.bus.subscribe()
    enlarge(client, run["images"][0], 201)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    updated = [data for name, data in events if name == "run.updated" and data["id"] == run["id"]]
    assert len(updated) == 1 and updated[0]["images"][0]["four_k"]["method"] == "model"
    client.app.state.bus.unsubscribe(sub)


def test_finding_a_copy_that_exists_sends_no_event(client):
    run = wide_run(client)
    enlarge(client, run["images"][0], 201)
    sub = client.app.state.bus.subscribe()
    enlarge(client, run["images"][0], 200)
    assert sub.queue.empty()
    client.app.state.bus.unsubscribe(sub)


def test_the_copy_goes_with_its_run_and_keep_does_not_disturb_it(client):
    run = wide_run(client)
    enlarge(client, run["images"][0], 201)
    client.patch(f"/api/runs/{run['id']}", json={"pinned": True})
    assert client.get(f"/api/runs/{run['id']}").json()["images"][0]["four_k"]["method"] == "model"
    folder = client.app.state.storage.images / run["id"]
    client.patch(f"/api/runs/{run['id']}", json={"pinned": False})
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not folder.exists() and client.get(f"/api/images/{run['images'][0]['id']}/4k").status_code == 404


def test_a_run_deleted_while_its_picture_is_being_enlarged_ends_in_a_404_and_leaves_nothing_behind(client):
    stub = use(client, Stub(delay=0.8))  # the stub reads the picture only after this wait, by which time the run is gone
    run = wide_run(client)
    folder = client.app.state.storage.images / run["id"]
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: enlarge(client, run["images"][0]))
        time.sleep(0.25)
        assert client.delete(f"/api/runs/{run['id']}").status_code == 204
        response = future.result()
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert not folder.exists() and len(stub.calls) == 1


def test_the_database_schema_is_unchanged(client):
    from studio.db import SCHEMA_VERSION

    assert SCHEMA_VERSION == 3  # an enlarged copy is a file beside the image; there is no row for it (DESIGN.md §28.2)
