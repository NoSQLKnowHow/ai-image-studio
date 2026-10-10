"""POST /api/upscale (DESIGN.md §27.9; criteria 90-92): a picture from the person's computer, made 4K and sent back, with nothing
stored. The checks of an upload (type, size, pixels, rotation) are the upload's own, reused; the rule is Make 4K's."""

from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pytest
from conftest import create_run, image_bytes, wait_for
from PIL import Image

import studio.api as api
from studio import fourk
from studio import presets as P


def upscale(client, data: bytes, name: str | None = None, **kwargs):
    url = "/api/upscale" + (f"?name={quote(name)}" if name is not None else "")
    return client.post(url, content=data, **kwargs)


def result(response) -> Image.Image:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(response.content))


def stored(client) -> list[str]:
    """Every file under the data folder except the database's own (which change as the server runs)."""
    root = client.app.state.storage.root
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and "studio.sqlite" not in p.name)


# ------------------------------------------------------------------ it works, for every kind of file
def test_a_png_comes_back_as_a_3840_by_2160_png_with_its_headers(client):
    response = upscale(client, image_bytes((30, 90, 160), (1920, 1080)), name="harbour.png")
    assert result(response).size == (3840, 2160)
    assert response.headers["x-output-size"] == "3840x2160"
    assert response.headers["cache-control"] == "no-store"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "upscale_harbour_3840x2160_" in disposition and disposition.count(".png") >= 1


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_a_jpeg_and_a_webp_work_too_and_get_the_size_the_rule_gives(client, fmt):
    out = result(upscale(client, image_bytes((200, 120, 40), (2000, 1100), fmt=fmt), name=f"photo.{fmt.lower()}"))
    plan = fourk.plan_4k(2000, 1100)
    assert out.size == (plan.out_width, plan.out_height) == (3927, 2160)  # 2.3% from 16:9: scaled to cover, not trimmed


def test_a_square_picture_is_enlarged_to_cover_the_frame(client):
    assert result(upscale(client, image_bytes((10, 10, 10), (2048, 2048)))).size == (3840, 3840)


def test_a_phone_photos_rotation_is_applied_before_anything_else(client):
    """A 1920x1080 file whose EXIF orientation says 'turn it a quarter' is a 1080x1920 picture upright: exactly 9:16."""
    exif = Image.Exif()
    exif[274] = 6  # Orientation: rotate 90 degrees clockwise to view
    buffer = io.BytesIO()
    Image.new("RGB", (1920, 1080), (90, 160, 30)).save(buffer, format="JPEG", exif=exif.tobytes())
    assert result(upscale(client, buffer.getvalue(), name="IMG_0001.jpg")).size == (2160, 3840)


def test_transparency_is_kept(client):
    picture = Image.new("RGBA", (2048, 2048), (255, 0, 0, 0))  # transparent
    picture.paste((0, 255, 0, 255), (600, 600, 1400, 1400))  # an opaque square
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    out = result(upscale(client, buffer.getvalue()))
    assert out.mode == "RGBA" and out.getpixel((5, 5))[3] == 0 and out.getpixel((1920, 1920)) == (0, 255, 0, 255)


def test_it_gives_the_same_pixels_as_make_4k_on_a_picture_the_studio_holds(client):
    """One piece of code makes both: the picture a card's button makes and the one this route makes from the same file."""
    run = wait_for(client, create_run(client, "a harbour", width=2752, height=1536, steps=2)["id"])
    image = run["images"][0]
    original = client.get(image["url"]).content
    held = Image.open(io.BytesIO(client.get(client.post(f"/api/images/{image['id']}/4k").json()["images"][0]["four_k"]["url"]).content))
    brought = result(upscale(client, original, name="0.png"))
    assert brought.size == held.size == (3840, 2160) and brought.tobytes() == held.tobytes()


# ------------------------------------------------------------------ nothing is kept
def test_nothing_is_stored_and_no_run_appears(client):
    before = stored(client)
    for _ in range(2):
        result(upscale(client, image_bytes(size=(1920, 1080))))
    assert stored(client) == before
    assert client.get("/api/runs").json()["runs"] == []
    assert client.get("/api/status").json()["queue"]["queued"] == 0


# ------------------------------------------------------------------ the name
@pytest.mark.parametrize("name,expected", [
    ("My Holiday Photo.JPG", "upscale_my-holiday-photo_"),
    ("../../etc/passwd.png", "upscale_passwd_"),
    ("C:\\Users\\me\\dog.png", "upscale_dog_"),
    (None, "upscale_untitled_"),
    ("", "upscale_untitled_"),
])
def test_the_returned_file_is_named_for_the_file_that_was_sent(client, name, expected):
    disposition = upscale(client, image_bytes(size=(1920, 1080)), name=name).headers["content-disposition"]
    assert f"filename*=UTF-8''{expected}" in disposition, disposition
    assert "/" not in disposition.split("filename=")[1].split(";")[0]  # nothing that could be a path


def test_a_name_in_another_script_is_kept_in_the_utf8_form_with_an_ascii_fallback(client):
    disposition = upscale(client, image_bytes(size=(1920, 1080)), name="写真.jpg").headers["content-disposition"]
    assert "filename*=UTF-8''upscale_%E5%86%99%E7%9C%9F_3840x2160_" in disposition
    assert 'filename="upscale_' in disposition


def test_an_over_long_name_is_refused_not_trusted(client):
    assert upscale(client, image_bytes(size=(1920, 1080)), name="x" * 300).status_code == 422


# ------------------------------------------------------------------ what it refuses, and why
def test_a_picture_that_needs_too_big_an_enlargement_is_refused_with_the_reason(client):
    response = upscale(client, image_bytes(size=(1024, 1024)))
    assert response.status_code == 422 and response.json()["code"] == "not_4k_eligible"
    assert "too small" in response.json()["detail"] and "3.8× enlargement" in response.json()["detail"]


def test_a_picture_that_is_already_4k_is_refused(client):
    response = upscale(client, image_bytes(size=(3840, 2160)))
    assert response.status_code == 422 and response.json()["code"] == "not_4k_eligible" and "already 4K" in response.json()["detail"]


def test_a_copy_over_20_megapixels_is_refused(client):
    response = upscale(client, image_bytes(size=(7000, 1100)))
    assert response.status_code == 422 and "megapixels" in response.json()["detail"]


def test_a_file_that_is_not_an_image_is_415(client):
    response = upscale(client, b"this is a text file, not a picture")
    assert response.status_code == 415 and response.json()["code"] == "unsupported_type"


def test_an_image_in_a_format_that_is_not_accepted_is_415(client):
    response = upscale(client, image_bytes(size=(1920, 1080), fmt="GIF", mode="P", color=1))
    assert response.status_code == 415 and "PNG, JPEG and WebP" in response.json()["detail"]


def test_an_empty_body_is_422(client):
    response = upscale(client, b"")
    assert response.status_code == 422 and response.json()["code"] == "unreadable"


def test_a_damaged_picture_is_422(client):
    good = image_bytes(size=(1920, 1080))
    response = upscale(client, good[:60] + b"\x00" * 40)  # a real header, then rubbish
    assert response.status_code == 422 and response.json()["code"] == "unreadable"


def test_a_file_over_the_upload_size_limit_is_413(client_factory):
    client = client_factory(max_upload_mb=1)
    response = upscale(client, b"\x00" * (1024 * 1024 + 10))
    assert response.status_code == 413 and response.json()["code"] == "too_large"


def test_a_picture_over_the_pixel_limit_is_413(client, monkeypatch):
    monkeypatch.setattr(P, "UPLOAD_MAX_PIXELS", 10_000)
    response = upscale(client, image_bytes(size=(200, 200)))
    assert response.status_code == 413


def test_it_needs_the_client_header_like_every_mutation_and_is_not_a_get(client):
    assert upscale(client, image_bytes(size=(1920, 1080)), headers={"X-Studio-Client": ""}).status_code == 403
    assert client.get("/api/upscale").status_code == 405


# ------------------------------------------------------------------ at most two at once
def test_no_more_than_two_pictures_are_being_made_at_the_same_moment(client, monkeypatch):
    running = peak = 0
    lock = threading.Lock()
    real = api._upscale_bytes

    def slow(data: bytes):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            time.sleep(0.35)  # long enough for the others to arrive and wait
            return real(data)
        finally:
            with lock:
                running -= 1

    monkeypatch.setattr(api, "_upscale_bytes", slow)
    data = image_bytes(size=(1920, 1080))
    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(lambda _: upscale(client, data).status_code, range(6)))
    assert codes == [200] * 6  # the rest waited their turn, none was refused
    assert peak == 2
