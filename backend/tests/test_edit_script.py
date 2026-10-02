"""scripts/edit_via_api.sh against a real server running the fake pipeline."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import image_bytes
from PIL import Image
from test_sse import LiveServer

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "edit_via_api.sh"
pytestmark = pytest.mark.skipif(not (shutil.which("curl") and shutil.which("bash")), reason="needs curl and bash")


@pytest.fixture
def server(tmp_path):
    started = LiveServer(tmp_path, max_input_images=3).start()
    yield started
    started.stop()


def images(tmp_path: Path, count: int = 2) -> list[str]:
    paths = []
    for i, colour in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (9, 9, 9)][:count], 1):
        path = tmp_path / f"in{i}.png"
        path.write_bytes(image_bytes(colour, (160, 120)))
        paths.append(str(path))
    return paths


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def test_it_uploads_runs_waits_and_saves_the_result(server, tmp_path):
    out = tmp_path / "results"
    done = run("--url", server.url, "--out", str(out), "put image 1 into image 2", *images(tmp_path), cwd=tmp_path)
    assert done.returncode == 0, done.stderr + done.stdout
    assert "image 1:" in done.stdout and "image 2:" in done.stdout and "Queued run" in done.stdout
    saved = list(out.glob("edit-*.png"))
    assert len(saved) == 1
    with Image.open(saved[0]) as result:
        assert result.size == (1184, 896) and result.text["inputs"] == "2"  # the 4:3 last image, at 1K, from 2 inputs


def test_the_options_are_passed_on(server, tmp_path):
    out = tmp_path / "results"
    done = run("--url", server.url, "--out", str(out), "--resolution", "2048", "--shape-from", "1", "--steps", "2", "--seed", "5",
               "--images", "2", "--transparent", "x", *images(tmp_path), cwd=tmp_path)
    assert done.returncode == 0, done.stderr + done.stdout
    files = sorted(out.glob("edit-*.png"))
    assert len(files) == 2
    with Image.open(files[0]) as result:
        assert result.size == (2368, 1760) and result.mode == "RGBA" and result.text["resolution"] == "2048"
        assert result.text["seed"] == "5"
    explicit = run("--url", server.url, "--out", str(out), "--size", "512x384", "x", *images(tmp_path), cwd=tmp_path)
    assert explicit.returncode == 0, explicit.stderr
    assert any(Image.open(p).size == (512, 384) for p in out.glob("edit-*.png"))


def test_a_failed_run_exits_1_and_says_why(server, tmp_path):
    failed = run("--url", server.url, "--out", str(tmp_path), "break it [fake:error]", *images(tmp_path), cwd=tmp_path)
    assert failed.returncode == 1 and "Simulated pipeline failure" in failed.stderr and "failed" in failed.stderr


def test_the_servers_own_refusal_is_shown_and_exits_2(server, tmp_path):
    too_many = run("--url", server.url, "x", *images(tmp_path, 4), cwd=tmp_path)
    assert too_many.returncode == 2 and "at most 3 images" in too_many.stderr
    bad_file = tmp_path / "notes.txt"
    bad_file.write_text("not an image")
    refused = run("--url", server.url, "x", str(bad_file), cwd=tmp_path)
    assert refused.returncode == 2 and "isn't a PNG, JPEG or WebP" in refused.stderr


def test_problems_with_the_arguments_or_the_connection_exit_2_with_a_clear_message(tmp_path):
    assert run("--help", cwd=tmp_path).returncode == 0
    assert "Give a prompt and at least one image" in run(cwd=tmp_path).stderr
    assert "Give a prompt and at least one image" in run("only a prompt", cwd=tmp_path).stderr
    missing = run("x", str(tmp_path / "nope.png"), cwd=tmp_path)
    assert missing.returncode == 2 and "No such file" in missing.stderr
    unknown = run("--frobnicate", "x", *images(tmp_path), cwd=tmp_path)
    assert unknown.returncode == 2 and "Unknown option" in unknown.stderr
    unreachable = run("--url", "http://127.0.0.1:9", "x", *images(tmp_path), cwd=tmp_path)
    assert unreachable.returncode == 2 and "Can't reach the studio" in unreachable.stderr


@pytest.mark.parametrize("args,message", [
    (["--size", "big"], "--size needs the form 1024x768"),
    (["--size", "1024x"], "--size needs the form 1024x768"),
    (["--resolution", "high"], "--resolution needs a whole number"),
    (["--steps", "-3"], "--steps needs a whole number"),
    (["--seed", "1.5"], "--seed needs a whole number"),
])
def test_bad_numbers_are_caught_before_anything_is_uploaded(server, tmp_path, args, message):
    bad = run("--url", server.url, *args, "x", *images(tmp_path), cwd=tmp_path)
    assert bad.returncode == 2 and message in bad.stderr
    assert "Uploading" not in bad.stdout  # nothing was sent
