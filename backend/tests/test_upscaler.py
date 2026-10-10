"""studio/upscaler.py and the settings behind it (DESIGN.md §28.3; criteria 99, 101, 102): whether Enlarge can run, the fake stand-in,
and the real one's process handling, with the process replaced by small scripts that exit the way studio/upscale_job.py does. The
job's own side of the same exit-code table is in test_upscale_job.py."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError

from studio import fourk
from studio.config import ConfigError, Settings, UPSCALER_DEVICES
from studio.upscaler import (Availability, FakeUpscaler, ModelUpscaler, TIMEOUT_SECONDS, UpscaleFailed, UpscaleTimeout, UpscalerBusy,
                             UpscalerError, UpscalerUnavailable, make_upscaler)
from studio.worker_client import BACKEND_ROOT


def settings_for(tmp_path, **overrides) -> Settings:
    return Settings(data_dir=tmp_path / "data", pipeline=overrides.pop("pipeline", "real"), **overrides)


def scripted(tmp_path, code: int = 0, stderr: str = "", stdout: str = "", sleep: float = 0.0, pidfile: Path | None = None, **kwargs) -> ModelUpscaler:
    """A ModelUpscaler whose process is a Python one-liner that behaves as asked."""
    model = tmp_path / "model.pth"
    model.write_bytes(b"x")
    body = ["import sys, time, os"]
    if pidfile is not None:
        body.append(f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))")
    if stdout:
        body.append(f"print({stdout!r})")
    if stderr:
        body.append(f"print({stderr!r}, file=sys.stderr)")
    if sleep:
        body.append(f"time.sleep({sleep})")
    body.append(f"sys.exit({code})")
    script = "; ".join(body)

    class Scripted(ModelUpscaler):
        def command(self, src, dst):
            return [sys.executable, "-c", script]

    return Scripted(settings_for(tmp_path, upscaler_model=model, **kwargs))


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ settings
def test_the_model_file_defaults_to_the_upscalers_folder_of_the_hugging_face_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "models"))
    assert settings_for(tmp_path).upscaler_model_path == tmp_path / "models" / "upscalers" / "RealESRGAN_x2plus.pth"
    monkeypatch.delenv("HF_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert settings_for(tmp_path).upscaler_model_path == tmp_path / ".cache" / "huggingface" / "upscalers" / "RealESRGAN_x2plus.pth"


def test_a_named_model_file_wins_over_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "models"))
    named = tmp_path / "mine" / "other_x2.pth"
    assert settings_for(tmp_path, upscaler_model=named).upscaler_model_path == named


def test_the_defaults_are_the_gpu_if_there_is_one_and_the_cache_folder(tmp_path):
    direct = Settings(data_dir=tmp_path)
    from_env = Settings.from_env({"STUDIO_DATA_DIR": str(tmp_path)})
    for settings in (direct, from_env):
        assert settings.upscaler_device == "auto" and settings.upscaler_model is None


def test_the_environment_names_the_file_and_the_device(tmp_path):
    env = {"STUDIO_UPSCALER_MODEL": "~/mine/x2.pth", "STUDIO_UPSCALER_DEVICE": " CPU ", "STUDIO_DATA_DIR": str(tmp_path)}
    settings = Settings.from_env(env)
    assert settings.upscaler_model == Path("~/mine/x2.pth").expanduser() and settings.upscaler_device == "cpu"


def test_empty_settings_mean_the_defaults_as_compose_passes_them(tmp_path):
    settings = Settings.from_env({"STUDIO_UPSCALER_MODEL": "", "STUDIO_UPSCALER_DEVICE": "", "STUDIO_DATA_DIR": str(tmp_path)})
    assert settings.upscaler_model is None and settings.upscaler_device == "auto"


def test_a_device_that_is_not_one_of_the_three_stops_the_server_with_a_readable_message(tmp_path):
    with pytest.raises(ConfigError, match=r"STUDIO_UPSCALER_DEVICE='tpu' must be one of: auto, cuda, cpu"):
        Settings.from_env({"STUDIO_UPSCALER_DEVICE": "tpu", "STUDIO_DATA_DIR": str(tmp_path)})
    assert UPSCALER_DEVICES == ("auto", "cuda", "cpu")


# ------------------------------------------------------------------ which upscaler
def test_the_fake_pipeline_gets_the_fake_upscaler_and_the_real_one_the_real(tmp_path):
    assert isinstance(make_upscaler(settings_for(tmp_path, pipeline="fake")), FakeUpscaler)
    real = make_upscaler(settings_for(tmp_path, upscaler_model=tmp_path / "m.pth", upscaler_device="cuda"))
    assert isinstance(real, ModelUpscaler) and real.model_path == tmp_path / "m.pth" and real.device == "cuda"


# ------------------------------------------------------------------ availability
def test_the_fake_upscaler_is_always_available():
    assert FakeUpscaler().availability() == Availability(True, model="fake")


def test_availability_is_reported_with_the_limit(tmp_path):
    assert Availability(True, model="m").as_dict() == {"available": True, "model": "m", "reason": None, "hint": None, "max_enlargement": 4}
    assert Availability(False, "m", "why", "how").as_dict() == {"available": False, "model": "m", "reason": "why", "hint": "how", "max_enlargement": 4}


def test_a_missing_model_file_says_where_it_looked_and_how_to_get_it(tmp_path):
    model = tmp_path / "upscalers" / "RealESRGAN_x2plus.pth"
    status = ModelUpscaler(settings_for(tmp_path, upscaler_model=model)).availability()
    assert status.available is False and status.model == "RealESRGAN_x2plus.pth"
    assert str(model) in status.reason
    assert "curl -L -o ~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth" in status.hint and "github.com/xinntao/Real-ESRGAN" in status.hint
    assert "STUDIO_UPSCALER_MODEL" in status.hint and "terms" in status.hint


def test_a_model_file_that_is_a_folder_is_not_a_model_file(tmp_path):
    folder = tmp_path / "RealESRGAN_x2plus.pth"
    folder.mkdir()
    assert ModelUpscaler(settings_for(tmp_path, upscaler_model=folder)).availability().available is False


@pytest.mark.parametrize("missing,what", [("spandrel", "spandrel"), ("torch", "PyTorch")])
def test_a_missing_package_says_so_and_says_to_rebuild(tmp_path, monkeypatch, missing, what):
    import importlib.util

    model = tmp_path / "m.pth"
    model.write_bytes(b"x")
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == missing else real_find_spec("os"))
    status = ModelUpscaler(settings_for(tmp_path, upscaler_model=model)).availability()
    assert status.available is False and what in status.reason and "docker compose up -d --build" in status.hint


def test_with_the_file_and_both_packages_it_is_available(tmp_path, monkeypatch):
    import importlib.util

    model = tmp_path / "m.pth"
    model.write_bytes(b"x")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object())
    assert ModelUpscaler(settings_for(tmp_path, upscaler_model=model)).availability() == Availability(True, model="m.pth")


# ------------------------------------------------------------------ the fake
def test_the_fake_upscaler_enlarges_the_picture_and_says_so_in_the_file(tmp_path):
    source, target = tmp_path / "0.png", tmp_path / "0-4k-enlarged.png"
    Image.new("RGB", (2752, 1536), (9, 9, 9)).save(source)
    run(FakeUpscaler().enlarge(source, target))
    with Image.open(target) as made:
        assert made.size == (3840, 2160) and "the fake upscaler" in made.text["make4k"]


def test_the_fake_upscaler_runs_the_same_rules_as_the_real_one(tmp_path):
    source = tmp_path / "0.png"
    Image.new("RGB", (512, 512)).save(source)
    with pytest.raises(fourk.NotEligible):
        run(FakeUpscaler().enlarge(source, tmp_path / "out.png"))
    with pytest.raises(FileNotFoundError):
        run(FakeUpscaler().enlarge(tmp_path / "nowhere.png", tmp_path / "out.png"))


# ------------------------------------------------------------------ the process
def test_the_command_runs_the_job_module_with_the_model_the_device_and_the_two_files(tmp_path):
    upscaler = ModelUpscaler(settings_for(tmp_path, upscaler_model=tmp_path / "m.pth", upscaler_device="cuda"))
    assert upscaler.command(Path("/a/0.png"), Path("/a/0-4k-enlarged.png")) == [
        sys.executable, "-m", "studio.upscale_job", "--model", str(tmp_path / "m.pth"), "--device", "cuda",
        "--src", "/a/0.png", "--dst", "/a/0-4k-enlarged.png"]


def test_the_process_environment_finds_the_studio_package_and_is_unbuffered(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    env = ModelUpscaler._environment()
    assert env["PYTHONPATH"].split(os.pathsep) == [str(BACKEND_ROOT), "/somewhere/else"] and env["PYTHONUNBUFFERED"] == "1"
    monkeypatch.delenv("PYTHONPATH")
    assert ModelUpscaler._environment()["PYTHONPATH"] == str(BACKEND_ROOT)


def test_success_returns_quietly(tmp_path):
    assert run(scripted(tmp_path, 0, stdout="ENLARGED 3840x2160 in 1.0 s on cpu with m").enlarge(tmp_path / "s.png", tmp_path / "d.png")) is None


# What the server does with each exit code of the upscaling process: the error it answers with. Code 9 (out of memory) is "busy":
# try again later, not "broken".
@pytest.mark.parametrize("code,error", [
    (2, fourk.NotEligible), (7, FileNotFoundError), (8, UnidentifiedImageError), (6, OSError),
    (3, UpscalerUnavailable), (4, UpscaleFailed), (5, UpscaleFailed), (9, UpscalerBusy), (1, UpscaleFailed), (137, UpscaleFailed),
])
def test_each_exit_code_becomes_the_error_the_server_answers_with(tmp_path, code, error):
    with pytest.raises(error) as info:
        run(scripted(tmp_path, code, stderr="ENLARGE FAILED: the reason").enlarge(tmp_path / "s.png", tmp_path / "d.png"))
    assert type(info.value) is error and "the reason" in str(info.value)


def test_the_answers_status_code_and_hint_come_with_the_error(tmp_path):
    expected = {3: (503, "upscaler_unavailable", True), 4: (500, "upscale_failed", True), 5: (500, "upscale_failed", False),
                9: (503, "upscaler_busy", True)}
    for code, (status, name, has_hint) in expected.items():
        with pytest.raises(UpscalerError) as info:
            run(scripted(tmp_path, code, stderr="ENLARGE FAILED: x").enlarge(tmp_path / "s.png", tmp_path / "d.png"))
        assert (info.value.status, info.value.code, bool(info.value.hint)) == (status, name, has_hint), code
        assert info.value.detail == "x"


def test_an_unknown_exit_says_the_upscaler_stopped_and_how(tmp_path):
    with pytest.raises(UpscaleFailed) as info:
        run(scripted(tmp_path, 11).enlarge(tmp_path / "s.png", tmp_path / "d.png"))
    assert str(info.value) == "The upscaler stopped unexpectedly (exit code 11)."


def test_the_reason_is_the_last_failed_line_else_the_last_line_else_nothing():
    reason = ModelUpscaler._failure_message
    assert reason("warning\nENLARGE FAILED: first\nnoise\nENLARGE FAILED: second\ntrailing noise\n") == "second"
    assert reason("just a traceback\nValueError: boom\n") == "ValueError: boom"
    assert reason("") == "" and reason("\n  \n") == ""


def test_a_stuck_enlargement_is_stopped_after_the_limit_and_its_half_written_file_removed(tmp_path):
    destination = tmp_path / "0-4k-enlarged.png"
    leftover = tmp_path / (destination.name + ".abc123.part")
    leftover.write_bytes(b"half")
    other = tmp_path / "keep.part"
    other.write_bytes(b"not ours")
    pidfile = tmp_path / "pid"
    upscaler = scripted(tmp_path, sleep=60, pidfile=pidfile)
    upscaler.timeout = 0.8
    started = time.monotonic()
    with pytest.raises(UpscaleTimeout) as info:
        run(upscaler.enlarge(tmp_path / "s.png", destination))
    assert time.monotonic() - started < 10
    assert info.value.status == 504 and info.value.code == "upscale_timeout" and "longer than" in str(info.value) and info.value.hint
    assert not leftover.exists() and other.exists()
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the process is gone, not left running


def test_the_default_limit_is_thirty_minutes():
    assert TIMEOUT_SECONDS == 30 * 60


def test_cancelling_the_request_stops_the_process(tmp_path):
    pidfile = tmp_path / "pid"
    upscaler = scripted(tmp_path, sleep=60, pidfile=pidfile)

    async def scenario():
        task = asyncio.ensure_future(upscaler.enlarge(tmp_path / "s.png", tmp_path / "d.png"))
        for _ in range(100):  # wait until the process is really running
            if pidfile.exists() and pidfile.read_text():
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_the_api_process_never_imports_pytorch_or_spandrel():
    """DESIGN.md §28.3, criterion 101: the model is loaded only in the job's own process, so the server stays light."""
    code = ("import studio.api, studio.jobs, studio.upscaler, studio.fourk, sys; "
            "sys.exit(1 if {'torch', 'spandrel', 'numpy'} & set(sys.modules) else 0)")
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(BACKEND_ROOT)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or "a heavy library was imported by the API process"
