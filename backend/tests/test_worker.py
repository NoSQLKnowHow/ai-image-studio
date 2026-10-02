"""The fake pipeline, and the worker process driven directly over its JSON-lines protocol."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from studio.pipelines.base import ImageJob, PipelineLoadError
from studio.pipelines.fake import FakePipeline, parse_directives, render_fake_image

BACKEND = Path(__file__).resolve().parent.parent
RUN_ID = "0123456789abcdef0123456789abcdef"


# ---------------------------------------------------------------- fake pipeline
def job(prompt: str = "a red barn", seeds=(7,), **extra) -> ImageJob:
    base = dict(run_id=RUN_ID, mode="generate", prompt=prompt, negative_prompt=None, width=256, height=192,
                steps=4, cfg_scale=None, seeds=list(seeds), transparent=False, model_id="fake-pipeline")
    base.update(extra)
    return ImageJob(**base)


def test_fake_images_are_deterministic_and_seed_dependent():
    kwargs = dict(prompt="x", width=320, height=240, index=0, count=1, steps=3, transparent=False)
    a = render_fake_image(seed=1, **kwargs).tobytes()
    assert a == render_fake_image(seed=1, **kwargs).tobytes()
    assert a != render_fake_image(seed=2, **kwargs).tobytes()


def test_fake_transparent_output_has_real_alpha():
    img = render_fake_image(prompt="x", width=256, height=256, seed=3, index=0, count=1, steps=1, transparent=True)
    assert img.mode == "RGBA"
    alpha = img.getchannel("A")
    assert alpha.getextrema() == (0, 255)  # fully transparent background and opaque content


def test_fake_pipeline_reports_every_step():
    steps = []
    image = FakePipeline(step_delay_ms=0, load_delay_ms=0).generate(job(), 0, 7, lambda s, t: steps.append((s, t)))
    assert steps == [(1, 4), (2, 4), (3, 4), (4, 4)] and image.size == (256, 192)


def test_directive_parsing():
    assert parse_directives("cat [fake:OOM] dog [fake:crash@2]") == [("oom", None), ("crash", 2)]


def test_simulated_load_failure(monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_FAIL", "1")
    with pytest.raises(PipelineLoadError):
        FakePipeline(load_delay_ms=0).load()


# ---------------------------------------------------------------- worker process
def talk(data_dir: Path, *commands: dict, pipeline: str = "fake", env_extra: dict | None = None):
    """Send commands (plus a shutdown) to a fresh worker; return (events, stdout_lines, returncode)."""
    env = dict(os.environ, PYTHONPATH=str(BACKEND), **(env_extra or {}))
    proc = subprocess.run(
        [sys.executable, "-m", "studio.worker", "--pipeline", pipeline, "--data-dir", str(data_dir),
         "--model", "test-model", "--fake-step-delay-ms", "0"],
        input="".join(json.dumps(c) + "\n" for c in (*commands, {"cmd": "shutdown"})),
        capture_output=True, text=True, timeout=60, env=env,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return [json.loads(line) for line in lines], lines, proc.returncode


def run_cmd(prompt: str = "a red barn", seeds=(7, 8), run_id: str = RUN_ID, **extra) -> dict:
    payload = dict(run_id=run_id, mode="generate", prompt=prompt, negative_prompt=None, width=256, height=192,
                   steps=3, cfg_scale=None, seeds=list(seeds), transparent=False, model_id="fake-pipeline",
                   input_path=None)
    payload.update(extra)
    return {"cmd": "run", "job": payload}


def kinds(events):
    return [e["event"] for e in events]


def test_worker_happy_path_writes_images_with_metadata(tmp_path):
    events, _, code = talk(tmp_path, run_cmd())
    assert code == 0
    k = kinds(events)
    assert k[0] == "hello" and k[-1] == "bye"
    assert k.index("state") < k.index("run_started") < k.index("image_done") < k.index("run_finished")
    assert [e["seed"] for e in events if e["event"] == "image_done"] == [7, 8]
    progress = [e for e in events if e["event"] == "progress"]
    assert progress and progress[-1]["image"] == 2 and progress[-1]["step"] == 3
    with Image.open(tmp_path / "images" / RUN_ID / "1.png") as im:
        assert im.size == (256, 192) and im.text["seed"] == "8" and im.text["prompt"] == "a red barn"
        assert im.text["model"] == "fake-pipeline" and im.text["Software"] == "ai-image-studio"
    assert not list(tmp_path.rglob("*.part"))


def test_worker_reports_errors_and_keeps_serving(tmp_path):
    other = "f" * 32
    events, _, code = talk(tmp_path, run_cmd("boom [fake:error]"), run_cmd(run_id=other, seeds=(1,)))
    failed = next(e for e in events if e["event"] == "run_failed")
    assert failed["run_id"] == RUN_ID and failed["completed"] == 0 and failed["error"]["kind"] == "error"
    assert any(e["event"] == "run_finished" and e["run_id"] == other for e in events) and code == 0


def test_worker_partial_batch_on_out_of_memory(tmp_path):
    events, _, _ = talk(tmp_path, run_cmd("big [fake:oom@1]", seeds=(1, 2, 3)))
    failed = next(e for e in events if e["event"] == "run_failed")
    assert failed["completed"] == 1 and failed["error"]["kind"] == "out_of_memory" and failed["error"]["hint"]
    assert (tmp_path / "images" / RUN_ID / "0.png").exists() and not (tmp_path / "images" / RUN_ID / "1.png").exists()


def test_worker_crash_exits_without_finishing(tmp_path):
    events, _, code = talk(tmp_path, run_cmd("die [fake:crash]"))
    assert code == 3 and "run_finished" not in kinds(events) and "bye" not in kinds(events)


def test_stray_stdout_output_cannot_corrupt_the_protocol(tmp_path):
    events, lines, _ = talk(tmp_path, run_cmd("noisy [fake:noise]"))
    assert all(line.startswith("{") for line in lines)  # every stdout line is protocol JSON
    assert "run_finished" in kinds(events)


def test_worker_rejects_bad_input_but_survives(tmp_path):
    events, _, code = talk(tmp_path, {"cmd": "dance"}, {"cmd": "run", "job": {"run_id": "nope"}}, run_cmd(seeds=(1,)))
    assert kinds(events).count("protocol_error") == 1
    assert next(e for e in events if e["event"] == "run_failed")["error"]["message"].startswith("Malformed job")
    assert "run_finished" in kinds(events) and code == 0


def test_load_failure_is_reported_then_the_run_fails(tmp_path):
    events, _, code = talk(tmp_path, run_cmd(), env_extra={"STUDIO_FAKE_LOAD_FAIL": "1"})
    assert next(e for e in events if e["event"] == "load_failed")["error"]["kind"] == "load_failed"
    assert next(e for e in events if e["event"] == "run_failed")["error"]["kind"] == "load_failed" and code == 0


def test_real_pipeline_without_pytorch_is_unavailable(tmp_path):
    events, _, _ = talk(tmp_path, run_cmd(), pipeline="real")  # no PyTorch in the test environment
    error = next(e for e in events if e["event"] == "load_failed")["error"]
    assert error["kind"] == "unavailable" and "PyTorch is not installed" in error["message"] and error["hint"]


def probe(tmp_path, pipeline):
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    proc = subprocess.run([sys.executable, "-m", "studio.worker", "--pipeline", pipeline, "--data-dir", str(tmp_path),
                           "--model", "m", "--probe"], capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return lines, proc.returncode


def test_probe_mode_reports_capabilities_and_exits(tmp_path):
    lines, code = probe(tmp_path, "fake")
    assert code == 0 and lines == [{"event": "probe", "ok": True, "pipeline": "fake",
                                    "supports": FakePipeline.SUPPORTS, "device": {"name": "fake (no GPU used)"}}]
    lines, code = probe(tmp_path, "real")
    assert code == 0 and lines[0]["ok"] is False and lines[0]["error"]["kind"] == "unavailable"
