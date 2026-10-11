"""The default shape of a picture is 16:9, 2752x1536 (DESIGN.md §34, version 1.16; acceptance criteria 177 and 181).

Up to 1.15 the default was 1:1, 2048x2048 (decision #20). These tests pin the new default at every place the server uses it (the presets,
GET /api/capabilities, a Generate request that names no size) and pin what must NOT move with it: a request that names a size, an Edit
request with no size, and the other presets. Most of the 1.16 work is elsewhere: the tests that used to lean on the old default now say
`1:1` themselves, so a later change of default touches none of them. This file is the one that is meant to change when the default does.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import create_edit, image_bytes, stage, wait_for
from PIL import Image

from studio import presets as P
from studio.config import Settings
from studio.runspec import RunCreate, RunRequestError, resolve_run

SETTINGS = Settings(data_dir=Path("/tmp/unused"), pipeline="fake")


def resolve(body: dict):
    """The server's reading of a request, without running it: the size, steps and seed it would use."""
    return resolve_run(RunCreate.model_validate(body), SETTINGS)


# ---------------------------------------------------------------- the value itself
def test_the_default_is_the_model_cards_16_9_preset():
    """Both numbers come from the table of presets, so the name and the size cannot drift apart, and the size is the model card's
    2752x1536, not 1920x1080 (1080 is not on the pipeline's grid of 32 pixels, so the studio would refuse it)."""
    assert P.DEFAULT_ASPECT == "16:9"
    assert P.DEFAULT_SIZE == (2752, 1536) == P.ASPECT_RATIOS["16:9"]
    width, height = P.DEFAULT_SIZE
    assert width % P.SIZE_MULTIPLE == 0 and height % P.SIZE_MULTIPLE == 0 and width * height <= P.MAX_PIXELS


def test_the_default_costs_what_the_old_square_cost():
    """The reason 16:9 is safe to make the default (§34.1): about 4.23 MP against the square's 4.19, so a picture takes the same time
    and memory as before. If a later default broke this, the Spark check of criterion 184 would have to be done again."""
    width, height = P.DEFAULT_SIZE
    assert abs(width * height / (2048 * 2048) - 1) < 0.01


def test_the_other_presets_are_untouched():
    """Only the default moved: the table the page offers still has the same seven model-card sizes, 1:1 among them."""
    assert P.ASPECT_RATIOS == {
        "1:1": (2048, 2048), "4:3": (2400, 1792), "3:4": (1792, 2400), "3:2": (2528, 1696),
        "2:3": (1696, 2528), "16:9": (2752, 1536), "9:16": (1536, 2752),
    }


# ---------------------------------------------------------------- what the page is told (it has no number of its own)
def test_capabilities_say_16_9_and_the_size_that_goes_with_it(client):
    defaults = client.get("/api/capabilities").json()["defaults"]
    assert (defaults["aspect_ratio"], defaults["width"], defaults["height"]) == ("16:9", 2752, 1536)


def test_the_capabilities_size_is_the_size_of_the_capabilities_name(client):
    """The page starts Custom at defaults.width x defaults.height and selects defaults.aspect_ratio; they must describe one shape."""
    caps = client.get("/api/capabilities").json()
    assert caps["aspect_ratios"][caps["defaults"]["aspect_ratio"]] == [caps["defaults"]["width"], caps["defaults"]["height"]]


# ---------------------------------------------------------------- a Generate request that names no size
def test_a_request_with_no_size_resolves_to_the_default_size():
    for body in ({"prompt": "a fox"}, {"prompt": "a fox", "options": {}}, {"prompt": "a fox", "options": {"steps": 5}}):
        run = resolve(body)
        assert (run.width, run.height) == (2752, 1536)


def test_a_request_that_names_a_size_gets_that_size_not_the_default():
    run = resolve({"prompt": "a fox", "options": {"width": 512, "height": 384}})
    assert (run.width, run.height) == (512, 384)


def test_the_old_square_is_still_there_for_whoever_asks_for_it():
    run = resolve({"prompt": "a fox", "options": {"width": 2048, "height": 2048}})
    assert (run.width, run.height) == (2048, 2048)


def test_a_request_with_no_size_makes_a_2752_by_1536_picture(client):
    """The whole path (criterion 181): the run, the file and its download name all say 2752x1536."""
    created = client.post("/api/runs", json={"prompt": "a harbour at dusk", "options": {"steps": 2, "seed": 3}})
    assert created.status_code == 201, created.text
    assert (created.json()["options"]["width"], created.json()["options"]["height"]) == (2752, 1536)  # the run says so at once

    run = wait_for(client, created.json()["id"])
    assert run["status"] == "done"
    image = run["images"][0]
    assert (image["width"], image["height"]) == (2752, 1536)  # the stored picture's record
    with Image.open(io.BytesIO(client.get(image["url"]).content)) as picture:
        assert picture.size == (2752, 1536)  # and the file itself
    assert "_2752x1536_" in client.get(image["download_url"]).headers["content-disposition"]  # and the name it downloads under


def test_a_request_that_names_a_size_is_made_at_that_size(client):
    created = client.post("/api/runs", json={"prompt": "a harbour", "options": {"width": 384, "height": 256, "steps": 1}})
    run = wait_for(client, created.json()["id"])
    assert (run["options"]["width"], run["options"]["height"]) == (384, 256)
    assert (run["images"][0]["width"], run["images"][0]["height"]) == (384, 256)


# ---------------------------------------------------------------- an Edit request with no size stays with the pipeline
def test_an_edit_with_no_size_is_still_sized_by_the_pipeline_not_by_the_default(client):
    """§34.3, your answer 2: Edit stays on Auto. The run records no size, and the picture follows the source (a square source at
    1K is 1024x1024), so it is neither the default's shape nor its size."""
    source = stage(client, image_bytes((10, 200, 30), (240, 240)))
    created = create_edit(client, "make it greener", [{"upload_id": source["upload_id"]}])
    assert created["options"]["width"] is None and created["options"]["height"] is None
    run = wait_for(client, created["id"])
    assert run["status"] == "done" and (run["images"][0]["width"], run["images"][0]["height"]) == (1024, 1024)


def test_an_edit_that_names_a_size_gets_it(client):
    source = stage(client, image_bytes((10, 200, 30), (240, 240)))
    created = create_edit(client, "make it greener", [{"upload_id": source["upload_id"]}], width=512, height=288)
    run = wait_for(client, created["id"])
    assert (run["images"][0]["width"], run["images"][0]["height"]) == (512, 288)


@pytest.mark.parametrize("mode", ["generate", "edit"])
def test_the_default_does_not_make_a_size_half_given_acceptable(mode):
    """Width and height still come together; the default fills in only when both are missing."""
    body = {"mode": mode, "prompt": "x", "options": {"width": 512}}
    if mode == "edit":
        body["input_images"] = [{"upload_id": "a" * 32}]
    with pytest.raises(RunRequestError) as info:
        resolve(body)
    assert [message for _, message in info.value.errors] == ["Width and height must be given together."]
