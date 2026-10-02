"""Uploading images to stage for an edit (DESIGN.md §11, §21.6, §21.7, criteria 25 and 26)."""

from __future__ import annotations

import io
import os
import time

import pytest
from conftest import image_bytes, stage
from PIL import Image

from studio import jobs as jobs_module
from studio import presets as P
from studio.serialize import format_ts

from datetime import datetime, timedelta, timezone


def staged_files(client):
    storage = client.app.state.storage
    return sorted(p.name for p in storage.staged.iterdir()), sorted(p.name for p in storage.staged_thumbs.iterdir())


def fetch(client, url: str) -> Image.Image:
    response = client.get(url)
    assert response.status_code == 200
    return Image.open(io.BytesIO(response.content))


# ------------------------------------------------------------------ what an upload becomes
def test_an_upload_is_stored_as_a_png_with_a_thumbnail(client):
    up = stage(client, image_bytes((10, 20, 30), (120, 80)))
    assert (up["width"], up["height"], up["has_alpha"]) == (120, 80, False) and len(up["upload_id"]) == 32
    full = client.get(up["url"])
    assert full.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(full.content))
    assert image.format == "PNG" and image.size == (120, 80) and image.getpixel((5, 5)) == (10, 20, 30)
    thumb = client.get(up["thumb_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    assert staged_files(client) == ([f"{up['upload_id']}.png"], [f"{up['upload_id']}.webp"])
    assert client.app.state.db.get_staged(up["upload_id"]) is not None


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP", "PNG"])
def test_png_jpeg_and_webp_are_all_accepted_and_stored_as_png(client, fmt):
    up = stage(client, image_bytes((200, 100, 50), (64, 48), fmt=fmt))
    assert fetch(client, up["url"]).format == "PNG"
    assert (up["width"], up["height"]) == (64, 48)


def test_the_file_type_is_decided_by_decoding_not_by_the_header(client):
    stage(client, image_bytes(), **{"Content-Type": "text/plain"})  # a real PNG with a wrong label is fine
    refused = client.post("/api/uploads", content=b"just some text", headers={"Content-Type": "image/png"})
    assert refused.status_code == 415 and refused.json()["code"] == "unsupported_type"


def test_transparency_is_kept_never_flattened(client):
    """§21.2 point 5: the model reads the alpha channel, so an upload must not be flattened onto a background."""
    rgba = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
    rgba.putpixel((0, 0), (0, 255, 0, 0))  # one fully transparent pixel
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    up = stage(client, buffer.getvalue())
    assert up["has_alpha"] is True
    stored = fetch(client, up["url"])
    assert stored.mode == "RGBA" and stored.getpixel((0, 0)) == (0, 255, 0, 0) and stored.getpixel((5, 5)) == (255, 0, 0, 255)

    palette = Image.new("P", (16, 16), 0)
    palette.putpalette([255, 0, 0] + [0] * 765)
    buffer = io.BytesIO()
    palette.save(buffer, format="PNG", transparency=0)  # a palette image whose colour 0 is transparent
    up = stage(client, buffer.getvalue())
    assert up["has_alpha"] is True and fetch(client, up["url"]).getpixel((3, 3))[3] == 0

    la = io.BytesIO()
    Image.new("LA", (8, 8), (100, 128)).save(la, format="PNG")
    assert stage(client, la.getvalue())["has_alpha"] is True

    assert stage(client, image_bytes((1, 2, 3), (8, 8), fmt="JPEG"))["has_alpha"] is False  # an ordinary photo has none


def test_a_phones_rotation_tag_is_applied_so_the_model_sees_the_picture_upright(client):
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees clockwise to display"
    up = stage(client, image_bytes((9, 9, 9), (100, 50), fmt="JPEG", exif=exif))
    assert (up["width"], up["height"]) == (50, 100)
    assert fetch(client, up["url"]).size == (50, 100)


def test_cmyk_and_greyscale_jpegs_become_rgb(client):
    cmyk = io.BytesIO()
    Image.new("CMYK", (16, 16), (0, 0, 0, 0)).save(cmyk, format="JPEG")
    assert fetch(client, stage(client, cmyk.getvalue())["url"]).mode == "RGB"
    grey = io.BytesIO()
    Image.new("L", (16, 16), 128).save(grey, format="JPEG")
    assert fetch(client, stage(client, grey.getvalue())["url"]).mode == "RGB"


def png_16_bit_rgb(r: int, g: int, b: int, size: int = 4) -> bytes:
    """A 16-bit-per-channel RGB PNG, written by hand (Pillow can read these but not make them)."""
    import struct
    import zlib

    raw = b"".join(b"\x00" + struct.pack(">HHH", r, g, b) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 16, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@pytest.mark.parametrize("value,expected", [(65535, 255), (30000, 117), (32768, 128), (256, 1), (255, 0), (0, 0)])
def test_a_16_bit_greyscale_png_is_scaled_not_clipped_to_white(client, value, expected):
    """Pillow's own conversion clips every 16-bit value above 255 to white, which silently ruins such an image."""
    buffer = io.BytesIO()
    Image.new("I;16", (8, 8), value).save(buffer, format="PNG")
    stored = fetch(client, stage(client, buffer.getvalue())["url"])
    assert stored.mode == "RGB" and stored.getpixel((2, 2)) == (expected,) * 3


def test_a_16_bit_colour_png_keeps_its_colours(client):
    stored = fetch(client, stage(client, png_16_bit_rgb(65535, 32768, 0))["url"])
    assert stored.getpixel((1, 1)) == (255, 128, 0)


def test_unusual_but_valid_files_load_as_ordinary_rgb(client):
    """1-bit and palette-free greyscale PNGs, an animated WebP (first frame) and an animated PNG (first frame)."""
    cases = {}
    one_bit = io.BytesIO()
    Image.new("1", (16, 16), 1).save(one_bit, format="PNG")
    cases["1-bit"] = (one_bit.getvalue(), (255, 255, 255))
    grey = io.BytesIO()
    Image.new("L", (16, 16), 7).save(grey, format="PNG")
    cases["greyscale"] = (grey.getvalue(), (7, 7, 7))
    frames = [Image.new("RGB", (40, 30), c) for c in ((255, 0, 0), (0, 0, 255))]
    for fmt in ("WEBP", "PNG"):
        animated = io.BytesIO()
        extra = {"lossless": True} if fmt == "WEBP" else {}  # WebP is lossy by default, which would blur the colour
        frames[0].save(animated, format=fmt, save_all=True, append_images=frames[1:], duration=100, **extra)
        cases[f"animated {fmt}"] = (animated.getvalue(), (255, 0, 0))
    for label, (data, colour) in cases.items():
        stored = fetch(client, stage(client, data)["url"])
        assert stored.mode == "RGB" and stored.getpixel((2, 2)) == colour, label


# ------------------------------------------------------------------ what is refused, and that nothing is left behind
@pytest.mark.parametrize("data,status,code", [
    (b"", 422, "unreadable"),
    (b"not an image at all", 415, "unsupported_type"),
    (image_bytes(fmt="GIF"), 415, "unsupported_type"),
    (image_bytes(fmt="BMP"), 415, "unsupported_type"),
    (image_bytes(fmt="PNG")[:60], 422, "unreadable"),  # a PNG cut off after its header
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, 415, "unsupported_type"),  # a PNG signature and then rubbish: not a PNG
])
def test_files_that_are_not_usable_are_refused_with_the_right_status_and_store_nothing(client, data, status, code):
    response = client.post("/api/uploads", content=data)
    assert response.status_code == status and response.json()["code"] == code and response.json()["detail"]
    assert staged_files(client) == ([], [])
    assert client.app.state.db.known_ids() == (set(), set())


def test_a_file_over_the_size_limit_is_refused_while_it_streams_in(client_factory):
    client = client_factory(max_upload_mb=1)
    big = os.urandom(1024 * 1024 + 5000)
    declared = client.post("/api/uploads", content=big)  # Content-Length says so up front
    assert declared.status_code == 413 and declared.json()["code"] == "too_large" and "1 MB" in declared.json()["detail"]

    def chunks():  # no Content-Length: chunked, so it can only be caught as it arrives
        for i in range(0, len(big), 65536):
            yield big[i:i + 65536]

    assert client.post("/api/uploads", content=chunks()).status_code == 413
    assert staged_files(client) == ([], [])
    assert stage(client, image_bytes())["width"] == 120  # and a normal upload still works


def test_an_image_with_too_many_pixels_is_refused(client, monkeypatch):
    monkeypatch.setattr(P, "UPLOAD_MAX_PIXELS", 10_000)
    response = client.post("/api/uploads", content=image_bytes(size=(200, 100)))
    assert response.status_code == 413 and "megapixels" in response.json()["detail"]
    assert staged_files(client) == ([], [])
    assert stage(client, image_bytes(size=(100, 100)))["width"] == 100  # exactly at the limit is fine


def test_uploads_need_the_client_header_like_every_other_change(client):
    refused = client.post("/api/uploads", content=image_bytes(), headers={"X-Studio-Client": ""})
    assert refused.status_code == 403 and refused.json()["code"] == "missing_client_header"
    assert staged_files(client) == ([], [])


# ------------------------------------------------------------------ taking one back
def test_deleting_an_upload_removes_its_files_and_row(client):
    up = stage(client, image_bytes())
    assert client.delete(f"/api/uploads/{up['upload_id']}").status_code == 204
    assert staged_files(client) == ([], []) and client.get(up["url"]).status_code == 404
    again = client.delete(f"/api/uploads/{up['upload_id']}")
    assert again.status_code == 404 and again.json()["code"] == "not_found"


def test_delete_upload_refuses_unknown_and_malformed_ids_and_needs_the_header(client):
    assert client.delete(f"/api/uploads/{'a' * 32}").status_code == 404
    assert client.delete("/api/uploads/not-an-id").status_code == 404
    up = stage(client, image_bytes())
    assert client.delete(f"/api/uploads/{up['upload_id']}", headers={"X-Studio-Client": ""}).status_code == 403
    assert client.get(up["url"]).status_code == 200


def test_the_upload_route_cannot_delete_a_run_that_owns_its_image(client):
    """DELETE /api/uploads only removes images that are still waiting; an image a run owns goes with its run."""
    up = stage(client, image_bytes())
    run = client.post("/api/runs", json={"mode": "edit", "prompt": "x", "input_images": [{"upload_id": up["upload_id"]}],
                                         "options": {"steps": 1}}).json()
    owned = run["inputs"][0]["id"]
    assert client.delete(f"/api/uploads/{owned}").status_code == 404
    assert client.get(f"/api/images/{owned}").status_code == 200


# ------------------------------------------------------------------ clean-up of uploads nobody used
def sweep(client) -> int:
    return client.portal.call(client.app.state.jobs.sweep_uploads)


def age_upload(client, upload_id: str, hours: float) -> None:
    created = format_ts(datetime.now(timezone.utc) - timedelta(hours=hours))
    with client.app.state.db.tx() as c:
        c.execute("UPDATE images SET created_at=? WHERE id=?", (created, upload_id))


def test_an_unclaimed_upload_is_removed_after_the_ttl_and_a_newer_one_is_kept(client_factory):
    client = client_factory(quiet=True, upload_ttl_hours=24)
    old, new = stage(client, image_bytes((1, 1, 1))), stage(client, image_bytes((2, 2, 2)))
    age_upload(client, old["upload_id"], 25)
    age_upload(client, new["upload_id"], 23)
    assert sweep(client) == 1
    assert client.get(old["url"]).status_code == 404 and client.get(old["thumb_url"]).status_code == 404
    assert client.get(new["url"]).status_code == 200
    assert staged_files(client) == ([f"{new['upload_id']}.png"], [f"{new['upload_id']}.webp"])


def test_the_ttl_is_configurable(client_factory):
    client = client_factory(quiet=True, upload_ttl_hours=2)
    up = stage(client, image_bytes())
    age_upload(client, up["upload_id"], 3)
    assert sweep(client) == 1


def test_uploads_are_swept_at_start_up_and_then_on_an_interval(client_factory, monkeypatch):
    monkeypatch.setattr(jobs_module, "UPLOAD_SWEEP_SECONDS", 0.2)
    client = client_factory()
    up = stage(client, image_bytes())
    age_upload(client, up["upload_id"], 30)  # it grows old while the server is running
    deadline = time.monotonic() + 10
    while client.get(up["url"]).status_code != 404:
        assert time.monotonic() < deadline, "the staged upload was never swept"
        time.sleep(0.05)


def test_files_nothing_owns_are_removed_once_they_are_old_enough_and_not_before(quiet_client):
    client = quiet_client
    storage = client.app.state.storage
    stray = storage.staged / ("c" * 32 + ".png")
    stray_thumb = storage.staged_thumbs / ("c" * 32 + ".webp")
    fresh = storage.staged / ("d" * 32 + ".png")
    for path in (stray, stray_thumb, fresh):
        path.write_bytes(b"x")
    long_ago = time.time() - 7200
    os.utime(stray, (long_ago, long_ago))
    os.utime(stray_thumb, (long_ago, long_ago))
    lost_run = storage.inputs / ("e" * 32)  # a run folder whose run is not in the database (a crash mid-submit)
    lost_run.mkdir()
    (lost_run / "1.png").write_bytes(b"x")
    os.utime(lost_run, (long_ago, long_ago))
    young_run = storage.inputs / ("f" * 32)  # one that might still be being filled by a submit that is under way
    young_run.mkdir()
    assert sweep(client) == 3
    assert not stray.exists() and not stray_thumb.exists() and not lost_run.exists()
    assert fresh.exists() and young_run.exists() and storage.staged.is_dir()


def test_a_real_staged_upload_is_never_mistaken_for_a_stray_file(quiet_client):
    client = quiet_client
    up = stage(client, image_bytes())
    storage = client.app.state.storage
    long_ago = time.time() - 7200
    for path in (storage.staged / f"{up['upload_id']}.png", storage.staged_thumbs / f"{up['upload_id']}.webp"):
        os.utime(path, (long_ago, long_ago))  # old on disk, but the database knows it
    assert sweep(client) == 0 and client.get(up["url"]).status_code == 200
