"""Unit tests: configuration, presets, naming, request resolution, events, storage."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from studio import presets as P
from studio.config import ConfigError, Settings
from studio.events import OVERFLOW, EventBus, format_sse
from studio.naming import content_disposition, download_filename, slugify
from studio.runspec import RunCreate, RunRequestError, resolve_run
from studio.security import hostname
from studio.storage import Storage, StorageError, check_id

SETTINGS = Settings(data_dir=Path("/tmp/unused"), pipeline="fake")


# ---------------------------------------------------------------- config
def test_config_defaults_match_the_spec():
    s = Settings.from_env({})
    assert (s.model, s.pipeline, s.port, s.queue_cap, s.idle_timeout_min, s.retention_days) == (
        "Qwen/Qwen-Image-2.1", "real", 8080, 10, 30, 30)
    assert (s.max_images_per_run, s.max_prompt_chars, s.max_upload_mb) == (8, 8000, 20)
    assert s.min_free_gb is None and s.allowed_hosts == () and s.db_path == Path("/data/studio.sqlite")


def test_config_parses_every_variable():
    s = Settings.from_env({
        "STUDIO_MODEL": "/models/qwen", "STUDIO_DATA_DIR": "/srv/studio", "STUDIO_PIPELINE": "FAKE",
        "STUDIO_HOST": "127.0.0.1", "STUDIO_PORT": "9000", "STUDIO_ALLOWED_HOSTS": " Spark.lan , 10.0.0.5 ",
        "STUDIO_IDLE_TIMEOUT_MIN": "0", "STUDIO_QUEUE_CAP": "3", "STUDIO_RETENTION_DAYS": "0",
        "STUDIO_MAX_IMAGES_PER_RUN": "4", "STUDIO_MAX_PROMPT_CHARS": "500", "STUDIO_MAX_UPLOAD_MB": "5",
        "STUDIO_MIN_FREE_GB": "40.5", "STUDIO_CPU_OFFLOAD": "yes", "STUDIO_LOCAL_FILES_ONLY": "true",
        "STUDIO_FAKE_STEP_DELAY_MS": "0",
    })
    assert s.pipeline == "fake" and s.port == 9000 and s.allowed_hosts == ("spark.lan", "10.0.0.5")
    assert s.min_free_gb == 40.5 and s.cpu_offload and s.local_files_only and s.queue_cap == 3


def test_config_reports_every_problem_at_once():
    with pytest.raises(ConfigError) as info:
        Settings.from_env({"STUDIO_PORT": "eighty", "STUDIO_QUEUE_CAP": "0", "STUDIO_CPU_OFFLOAD": "maybe",
                           "STUDIO_PIPELINE": "gpu", "STUDIO_MIN_FREE_GB": "-1", "STUDIO_MODEL": "  "})
    message = str(info.value)
    for fragment in ("STUDIO_PORT", "STUDIO_QUEUE_CAP", "STUDIO_CPU_OFFLOAD", "STUDIO_PIPELINE",
                     "STUDIO_MIN_FREE_GB", "STUDIO_MODEL"):
        assert fragment in message


def test_config_refuses_the_reserved_token_rather_than_pretending_to_protect():
    with pytest.raises(ConfigError, match="would NOT protect"):
        Settings.from_env({"STUDIO_TOKEN": "hunter2"})


# ---------------------------------------------------------------- presets / naming
def test_transparent_wrapper_matches_model_card_and_is_idempotent():
    assert P.apply_transparent_format("A cute cartoon dragon sticker") == (
        "This is an RGBA image with transparency. A cute cartoon dragon sticker. "
        "The image has alpha channel and the background is transparent.")
    already = "an RGBA dragon"
    assert P.apply_transparent_format(already) == already


def test_presets_are_valid_sizes():
    for w, h in P.ASPECT_RATIOS.values():
        assert w % P.SIZE_MULTIPLE == 0 and h % P.SIZE_MULTIPLE == 0 and w * h <= P.MAX_PIXELS


@pytest.mark.parametrize("text,expected", [
    ("Halloween town at dawn, spooky!", "halloween-town-dawn-spooky"),
    ("A cute dragon in the forest of the moon", "cute-dragon-forest-moon"),
    ("a the of", "a-the-of"),
    ("!!! ???", "untitled"),
    ("one two three four five six seven", "one-two-three-four-five-six"),
])
def test_slugify(text, expected):
    assert slugify(text) == expected


def test_slugify_keeps_non_latin_and_respects_byte_budget():
    assert slugify("夜晚的万圣节小镇") != "untitled"
    assert len(slugify("骷" * 200).encode()) <= 60


def test_download_filename_and_content_disposition():
    when = datetime(2026, 10, 1, 14, 15, 2, tzinfo=timezone.utc)
    name = download_filename(mode="generate", prompt="A cute dragon sticker", width=2048, height=2048,
                             seed=42, created_at=when, transparent=True)
    assert name == "generate_cute-dragon-sticker_2048x2048_s42_rgba_20261001-141502.png"
    header = content_disposition("generate_café-☕_256x256_s1_20261001-141502.png")
    assert header.startswith('attachment; filename="generate_cafe_256x256_s1_20261001-141502.png"')
    assert "filename*=UTF-8''generate_caf%C3%A9-%E2%98%95" in header


# ---------------------------------------------------------------- request resolution
def resolve(body: dict, settings: Settings = SETTINGS):
    return resolve_run(RunCreate.model_validate(body), settings)


def errors_of(body: dict, settings: Settings = SETTINGS) -> dict[tuple, str]:
    with pytest.raises(RunRequestError) as info:
        resolve(body, settings)
    return {tuple(loc): msg for loc, msg in info.value.errors}


def test_resolve_defaults():
    run = resolve({"prompt": "  a fox  "})
    assert run.prompt == "a fox" and (run.width, run.height, run.steps, run.num_images) == (2048, 2048, 40, 1)
    assert run.seed_was_random and 0 <= run.seed <= P.SEED_MAX - SETTINGS.max_images_per_run + 1
    assert run.options_snapshot()["seed"] == run.seed


def test_resolve_explicit_seed_and_batch_seeds():
    run = resolve({"prompt": "x", "options": {"seed": 100, "num_images": 3}})
    assert run.seeds == [100, 101, 102] and not run.seed_was_random


def test_resolve_transparent_wraps_prompt_but_keeps_user_prompt():
    run = resolve({"prompt": "dragon", "options": {"transparent": True}})
    assert run.prompt == "dragon" and run.effective_prompt.startswith(P.TRANSPARENT_PREFIX)


def test_blank_negative_prompt_becomes_none():
    assert resolve({"prompt": "x", "options": {"negative_prompt": "   "}}).negative_prompt is None


@pytest.mark.parametrize("options,field,fragment", [
    ({"width": 1024}, "height", "together"),
    ({"width": 1040, "height": 1024}, "width", "multiple of 32"),  # fine for 16, not for the pipeline's 32
    ({"width": 128, "height": 1024}, "width", "between 256"),
    ({"width": 4096, "height": 2048}, "width", "MP"),
    ({"steps": 0}, "steps", "between 1"),
    ({"steps": 101}, "steps", "between 1"),
    ({"num_images": 9}, "num_images", "between 1 and 8"),
    ({"seed": 2**32}, "seed", "between 0"),
    ({"cfg_scale": 0.0}, "cfg_scale", "between"),
])
def test_resolve_rejects_out_of_range_options(options, field, fragment):
    errors = errors_of({"prompt": "x", "options": options})
    assert fragment in errors[("options", field)]


def test_resolve_collects_several_errors_with_locations():
    errors = errors_of({"prompt": "   ", "options": {"steps": 0, "num_images": 0}})
    assert set(errors) == {("prompt",), ("options", "steps"), ("options", "num_images")}


def test_prompt_length_limit_comes_from_settings():
    small = Settings(data_dir=Path("/tmp/unused"), max_prompt_chars=100)
    assert "limit is 100" in errors_of({"prompt": "x" * 101}, small)[("prompt",)]


def test_an_edit_needs_images_and_generate_takes_none():
    assert errors_of({"mode": "edit", "prompt": "make it blue"})[("input_images",)] == "Add at least one image to edit."
    ref = {"upload_id": "a" * 32}
    errors = errors_of({"prompt": "x", "input_images": [ref], "options": {"resolution": 2048, "shape_from": 1}})
    assert errors[("input_images",)] == "Images are only used in Edit mode."
    assert ("options", "resolution") in errors and ("options", "shape_from") in errors


# ---------------------------------------------------------------- events / security / storage
def test_event_bus_drops_a_subscriber_that_falls_behind():
    async def scenario():
        bus = EventBus(max_queue=2)
        slow, fast = bus.subscribe(), bus.subscribe()
        for i in range(3):
            bus.publish("tick", {"i": i})
            if fast.queue.qsize():
                fast.queue.get_nowait()
        assert slow.dropped and bus.subscriber_count == 1
        items = [slow.queue.get_nowait() for _ in range(slow.queue.qsize())]
        assert items[-1] == (OVERFLOW, None)
    asyncio.run(scenario())


def test_format_sse():
    assert format_sse("run.deleted", {"id": "x"}) == 'event: run.deleted\ndata: {"id":"x"}\n\n'


@pytest.mark.parametrize("header,expected", [
    ("Spark.lan:8080", "spark.lan"), ("10.0.0.5", "10.0.0.5"), ("[::1]:8080", "::1"), ("[fe80::1]", "fe80::1"),
])
def test_hostname_parsing(header, expected):
    assert hostname(header) == expected


def test_storage_rejects_paths_outside_the_run_folder(tmp_path):
    storage = Storage(tmp_path)
    storage.ensure_layout()
    run_id = "a" * 32
    other = storage.images / ("b" * 32)
    other.mkdir()
    Image.new("RGB", (8, 8)).save(other / "0.png")
    for bad in (f"images/{'b' * 32}/0.png", "../etc/passwd", f"images/{run_id}/../../studio.sqlite"):
        with pytest.raises(StorageError):
            storage.accept_worker_image(bad, run_id)
    with pytest.raises(StorageError):
        check_id("../x")


def test_storage_layout_fails_clearly_when_not_writable(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StorageError, match="not writable") as caught:
        Storage(blocker / "data").ensure_layout()
    assert f"sudo chown -R {os.getuid()}:{os.getgid()}" in str(caught.value)  # says how to fix a mount


def test_thumbnail_keeps_alpha_and_bounds_size(tmp_path):
    storage = Storage(tmp_path)
    storage.ensure_layout()
    src = tmp_path / "src.png"
    Image.new("RGBA", (2048, 1024), (255, 0, 0, 0)).save(src)
    thumb = storage.make_thumbnail(src, "c" * 32, 0)
    with Image.open(thumb) as im:
        assert im.format == "WEBP" and im.size == (512, 256) and im.mode == "RGBA"
    assert not list(tmp_path.rglob("*.part"))


# ---------------------------------------------------------------- version
def test_the_version_is_one_number_everywhere(client_factory):
    import json
    import re

    from studio import __version__

    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", __version__), __version__
    client = client_factory()
    assert client.get("/api/health").json()["version"] == __version__
    assert client.get("/api/status").json()["version"] == __version__
    assert client.get("/api/openapi.json").json()["info"]["version"] == __version__


def test_the_front_end_package_carries_the_same_version():
    """frontend/package.json must agree with studio.__version__ (1.1 and 1.1.0): bump them together."""
    import json

    from studio import __version__

    package = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "package.json").read_text())["version"]
    assert package == __version__ or package.startswith(__version__ + "."), (
        f"frontend/package.json says {package} but studio/__init__.py says {__version__}: bump both")
