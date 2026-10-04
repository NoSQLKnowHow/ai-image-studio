"""The music worker (DESIGN.md §26.4): the fake pipeline, the worker driving it in process, and the worker process over
its JSON-lines protocol (`--kind music`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from studio import wavfile
from studio.pipelines.base import MusicJob, OutOfMemory, PipelineError
from studio.pipelines.fake_music import FRAMES, MAX_SECONDS, RENDER_STEPS, SAMPLE_RATE, FakeMusicPipeline
from studio.worker import Worker

BACKEND = Path(__file__).resolve().parent.parent
RUN_ID = "0123456789abcdef0123456789abcdef"
OTHER_ID = "f" * 32


def music_job(prompt: str = "Genre: ambient. A slow piano.", seeds=(7,), lyrics: str = "[Instrumental]", **extra) -> MusicJob:
    base = dict(run_id=RUN_ID, mode="music", prompt=prompt, lyrics=lyrics, duration=60, steps=30, seeds=list(seeds),
                model_id="fake-music")
    base.update(extra)
    return MusicJob(**base)


def job_dict(**extra) -> dict:
    job = music_job(**extra)
    return {k: getattr(job, k) for k in ("run_id", "mode", "prompt", "lyrics", "duration", "steps", "seeds", "model_id")}


class Collect:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **fields) -> None:
        self.events.append({"event": event, **fields})

    def kinds(self) -> list[str]:
        return [e["event"] for e in self.events]

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["event"] == kind]


def make_worker(tmp_path, pipeline=None) -> tuple[Worker, Collect]:
    emit = Collect()
    worker = Worker(pipeline or FakeMusicPipeline(step_delay_ms=0), tmp_path, emit, kind="music")
    return worker, emit


# ------------------------------------------------------------------ the fake pipeline
def generate(job: MusicJob, seed: int = 7, index: int = 0):
    return FakeMusicPipeline(step_delay_ms=0).generate(job, index, seed, lambda *_: None)


def test_the_same_inputs_give_the_same_track_and_any_change_gives_another():
    base = generate(music_job()).pcm
    assert generate(music_job()).pcm == base
    assert generate(music_job(), seed=8).pcm != base  # a different seed: a different tune
    assert generate(music_job(prompt="Genre: jazz.")).pcm != base
    assert generate(music_job(duration=30)).pcm != base
    assert generate(music_job(steps=20)).pcm != base
    assert generate(music_job(lyrics="[Verse]\nla la la")).pcm != base  # with vocals sounds different from without


def test_the_fake_track_is_short_and_in_the_shape_of_the_real_one():
    made = generate(music_job(duration=300))
    assert (made.sample_rate, made.channels, made.seconds) == (SAMPLE_RATE, 2, MAX_SECONDS)  # never longer than the cap
    assert len(made.pcm) == int(MAX_SECONDS * SAMPLE_RATE) * 2 * 2
    assert generate(music_job(duration=3)).seconds == 3  # but a shorter ask is respected


def test_the_fake_reports_both_stages_then_finishes():
    seen = []
    FakeMusicPipeline(step_delay_ms=0).generate(music_job(steps=30), 0, 7, lambda *a: seen.append(a))
    assert seen[:FRAMES] == [("compose", i, FRAMES) for i in range(1, FRAMES + 1)]
    assert seen[FRAMES:FRAMES + RENDER_STEPS] == [("render", i, RENDER_STEPS) for i in range(1, RENDER_STEPS + 1)]
    assert seen[-1] == ("finish", 0, 1) and len(seen) == FRAMES + RENDER_STEPS + 1


def test_fewer_steps_asked_means_fewer_render_steps():
    seen = []
    FakeMusicPipeline(step_delay_ms=0).generate(music_job(steps=3), 0, 7, lambda *a: seen.append(a))
    assert [s for s in seen if s[0] == "render"] == [("render", 1, 3), ("render", 2, 3), ("render", 3, 3)]


@pytest.mark.parametrize("directive,exc", [("[fake:error]", PipelineError), ("[fake:oom]", OutOfMemory)])
def test_the_fault_directives_work_for_music_too(directive, exc):
    with pytest.raises(exc):
        generate(music_job(prompt=f"warm piano {directive}"))


def test_a_directive_for_one_track_leaves_the_others_alone():
    job = music_job(prompt="warm piano [fake:error@1]")
    assert generate(job, index=0).seconds > 0
    with pytest.raises(PipelineError):
        generate(job, index=1)


def test_the_fake_load_can_fail_or_be_slow_like_the_image_one(monkeypatch):
    from studio.pipelines.base import PipelineLoadError

    monkeypatch.setenv("STUDIO_FAKE_LOAD_FAIL", "1")
    with pytest.raises(PipelineLoadError):
        FakeMusicPipeline(step_delay_ms=0).load()
    monkeypatch.setenv("STUDIO_FAKE_LOAD_FAIL", "")
    assert FakeMusicPipeline(step_delay_ms=0).load()["supports"]["music"] is True


def test_the_probe_says_what_the_music_pipeline_supports():
    result = FakeMusicPipeline.probe()
    assert result["supports"] == {"music": True, "instrumental": True, "step_progress": True, "cancel": True}


# ------------------------------------------------------------------ the worker, in process
def test_a_music_job_makes_tracks_and_says_so(tmp_path):
    worker, emit = make_worker(tmp_path)
    worker.run(job_dict(seeds=(7, 8)))
    kinds = emit.kinds()
    assert kinds.index("run_started") < kinds.index("track_done") < kinds.index("run_finished")
    done = emit.of("track_done")
    assert [(e["idx"], e["seed"], e["path"]) for e in done] == [(0, 7, f"audio/{RUN_ID}/0.wav"), (1, 8, f"audio/{RUN_ID}/1.wav")]
    assert all(e["sample_rate"] == SAMPLE_RATE and e["channels"] == 2 and e["seconds"] == MAX_SECONDS for e in done)
    assert emit.events[-1] == {"event": "run_finished", "run_id": RUN_ID, "completed": 2}
    info = wavfile.read_wav(tmp_path / done[1]["path"])
    assert (info.sample_rate, info.channels, info.seconds) == (SAMPLE_RATE, 2, MAX_SECONDS)
    assert not list(tmp_path.rglob("*.part"))


def test_progress_names_the_track_and_the_stage_and_is_in_order(tmp_path):
    worker, emit = make_worker(tmp_path)
    worker.run(job_dict(seeds=(7, 8)))
    progress = emit.of("progress")
    assert {e["of"] for e in progress} == {2} and {e["image"] for e in progress} == {1, 2}
    first_track = [e["stage"] for e in progress if e["image"] == 1]
    assert first_track == sorted(first_track, key=["compose", "render", "finish"].index)  # compose, then render, then finish
    assert first_track[0] == "compose" and first_track[-1] == "finish"
    compose = [e["step"] for e in progress if e["image"] == 1 and e["stage"] == "compose"]
    assert compose[0] == 1 and compose[-1] == FRAMES and compose == sorted(compose)  # first and last frame are always reported


def test_every_wav_carries_the_machine_generated_note(tmp_path):
    worker, emit = make_worker(tmp_path)
    worker.run(job_dict(prompt="Genre: ambient.\nGlobal Metadata\nA slow piano.", seeds=(41,)))
    info = wavfile.read_wav(tmp_path / emit.of("track_done")[0]["path"]).info
    assert info["software"] == "ai-image-studio" and info["title"].startswith("Genre: ambient.")
    assert "\n" not in info["title"] and len(info["title"]) <= 80
    assert "machine-generated" in info["comment"] and "fake-music" in info["comment"] and "Seed 41" in info["comment"]
    assert "A slow piano." in info["comment"] and "say so" in info["comment"]
    assert len(info["date"]) == 10


# ------------------------------------------------------------------ cancel
class CancelAt(FakeMusicPipeline):
    """Asks the worker to cancel when `track`'s `stage` reaches `step`, as the command-reader thread would."""

    def __init__(self, track: int, stage: str, step: int):
        super().__init__(step_delay_ms=0)
        self.track, self.stage, self.step, self.worker = track, stage, step, None
        self.reported: list[tuple[int, str, int]] = []  # every (track, stage, step) the pipeline got to report

    def generate(self, job, index, seed, on_progress):
        def hooked(stage: str, step: int, total: int) -> None:
            self.reported.append((index, stage, step))
            if index == self.track and stage == self.stage and step == self.step:
                self.worker.request_cancel(job.run_id)
            on_progress(stage, step, total)

        return super().generate(job, index, seed, hooked)


def test_cancel_while_composing_stops_within_a_frame(tmp_path):
    pipeline = CancelAt(track=0, stage="compose", step=5)
    worker, emit = make_worker(tmp_path, pipeline)
    pipeline.worker = worker
    worker.run(job_dict(seeds=(1, 2)))
    assert emit.events[-1] == {"event": "run_canceled", "run_id": RUN_ID, "completed": 0}
    assert (0, "compose", 6) not in pipeline.reported and not any(s == "render" for _, s, _ in pipeline.reported)
    assert "track_done" not in emit.kinds() and "run_finished" not in emit.kinds()
    assert not (tmp_path / "audio").exists() or not list((tmp_path / "audio").rglob("*"))


def test_cancel_while_rendering_stops_within_a_step(tmp_path):
    pipeline = CancelAt(track=0, stage="render", step=3)
    worker, emit = make_worker(tmp_path, pipeline)
    pipeline.worker = worker
    worker.run(job_dict(seeds=(1, 2)))
    assert emit.events[-1] == {"event": "run_canceled", "run_id": RUN_ID, "completed": 0}
    assert (0, "render", 4) not in pipeline.reported and (0, "finish", 0) not in pipeline.reported


def test_cancel_during_the_second_track_keeps_the_first(tmp_path):
    pipeline = CancelAt(track=1, stage="compose", step=2)
    worker, emit = make_worker(tmp_path, pipeline)
    pipeline.worker = worker
    worker.run(job_dict(seeds=(1, 2, 3)))
    assert emit.kinds().count("track_done") == 1
    assert emit.events[-1] == {"event": "run_canceled", "run_id": RUN_ID, "completed": 1}
    assert (tmp_path / "audio" / RUN_ID / "0.wav").exists() and not (tmp_path / "audio" / RUN_ID / "1.wav").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_a_cancel_that_arrives_before_the_run_starts_is_noticed_after_loading(tmp_path):
    worker, emit = make_worker(tmp_path)
    worker.request_cancel(RUN_ID)
    worker.run(job_dict())
    assert emit.kinds() == ["state", "state", "run_canceled"]  # loading, ready, canceled: never "started"


def test_the_worker_forgets_a_cancel_and_serves_the_next_job(tmp_path):
    pipeline = CancelAt(track=0, stage="compose", step=1)
    worker, emit = make_worker(tmp_path, pipeline)
    pipeline.worker = worker
    worker.run(job_dict())
    pipeline.track = -1  # never again
    worker.run(job_dict(run_id=OTHER_ID, seeds=(3,)))
    assert emit.events[-1] == {"event": "run_finished", "run_id": OTHER_ID, "completed": 1}


# ------------------------------------------------------------------ failures and bad jobs
def test_a_failing_track_fails_the_run_and_keeps_the_ones_before_it(tmp_path):
    worker, emit = make_worker(tmp_path)
    worker.run(job_dict(prompt="warm piano [fake:oom@1]", seeds=(1, 2, 3)))
    failed = emit.of("run_failed")[0]
    assert failed["completed"] == 1 and failed["error"]["kind"] == "out_of_memory" and failed["error"]["hint"]
    assert (tmp_path / "audio" / RUN_ID / "0.wav").exists() and not (tmp_path / "audio" / RUN_ID / "1.wav").exists()


@pytest.mark.parametrize("change", [
    {"lyrics": ""}, {"lyrics": "   "}, {"prompt": " "}, {"mode": "generate"}, {"seeds": []}, {"run_id": "nope"},
])
def test_a_bad_music_job_is_refused_without_a_crash(tmp_path, change):
    worker, emit = make_worker(tmp_path)
    worker.run(job_dict(**change))
    failed = emit.of("run_failed")[0]
    assert failed["error"]["message"].startswith("Malformed job") and "track_done" not in emit.kinds()


def test_an_unknown_field_is_refused_like_any_other_malformed_job(tmp_path):
    worker, emit = make_worker(tmp_path)
    worker.run({**job_dict(), "width": 512})
    assert emit.of("run_failed")[0]["error"]["message"].startswith("Malformed job")


def test_a_full_disk_is_a_clear_failure(tmp_path, monkeypatch):
    import errno

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(wavfile, "write_wav", full)
    worker, emit = make_worker(tmp_path)
    worker.run(job_dict())
    assert "disk is full" in emit.of("run_failed")[0]["error"]["message"]


# ------------------------------------------------------------------ the worker process
def talk(data_dir: Path, *commands: dict, kind: str = "music", env_extra: dict | None = None):
    env = dict(os.environ, PYTHONPATH=str(BACKEND), **(env_extra or {}))
    proc = subprocess.run(
        [sys.executable, "-m", "studio.worker", "--kind", kind, "--pipeline", "fake", "--data-dir", str(data_dir),
         "--model", "test-music", "--fake-step-delay-ms", "0"],
        input="".join(json.dumps(c) + "\n" for c in (*commands, {"cmd": "shutdown"})),
        capture_output=True, text=True, timeout=60, env=env,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return [json.loads(line) for line in lines], lines, proc.returncode


def test_the_music_worker_says_what_it_is_and_makes_a_track_over_the_protocol(tmp_path):
    events, _, code = talk(tmp_path, {"cmd": "run", "job": job_dict(seeds=(7,))})
    hello = events[0]
    assert hello["event"] == "hello" and hello["kind"] == "music" and hello["pipeline"] == "fake" and code == 0
    kinds = [e["event"] for e in events]
    assert kinds.index("state") < kinds.index("run_started") < kinds.index("track_done") < kinds.index("run_finished") < kinds.index("bye")
    assert wavfile.read_wav(tmp_path / "audio" / RUN_ID / "0.wav").seconds == MAX_SECONDS


def test_stray_output_cannot_corrupt_the_music_protocol(tmp_path):
    events, lines, _ = talk(tmp_path, {"cmd": "run", "job": job_dict(prompt="noisy [fake:noise]")})
    assert all(line.startswith("{") for line in lines)
    assert "run_finished" in [e["event"] for e in events]


def test_a_music_worker_refuses_a_picture_job_and_an_image_worker_a_music_job(tmp_path):
    image_job = {"run_id": RUN_ID, "mode": "generate", "prompt": "a barn", "negative_prompt": None, "width": 64, "height": 64,
                 "steps": 2, "cfg_scale": None, "seeds": [1], "transparent": False, "model_id": "m"}
    events, _, code = talk(tmp_path, {"cmd": "run", "job": image_job})
    assert next(e for e in events if e["event"] == "run_failed")["error"]["message"].startswith("Malformed job") and code == 0
    events, _, code = talk(tmp_path, {"cmd": "run", "job": job_dict()}, kind="image")
    assert next(e for e in events if e["event"] == "run_failed")["error"]["message"].startswith("Malformed job") and code == 0


def test_a_crash_in_the_music_pipeline_ends_the_process_like_the_image_one(tmp_path):
    events, _, code = talk(tmp_path, {"cmd": "run", "job": job_dict(prompt="die [fake:crash]")})
    assert code == 3 and "run_finished" not in [e["event"] for e in events]


def test_probe_mode_reports_music_capabilities_and_exits(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    proc = subprocess.run([sys.executable, "-m", "studio.worker", "--kind", "music", "--pipeline", "fake", "--data-dir",
                           str(tmp_path), "--model", "m", "--probe"], capture_output=True, text=True, timeout=60, env=env,
                          stdin=subprocess.DEVNULL)
    event = json.loads(proc.stdout.strip().splitlines()[-1])
    assert event["event"] == "probe" and event["ok"] is True and event["supports"]["music"] is True


def test_the_real_music_pipeline_without_pytorch_is_unavailable_and_says_how_to_fix_it(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    proc = subprocess.run([sys.executable, "-m", "studio.worker", "--kind", "music", "--pipeline", "real", "--data-dir",
                           str(tmp_path), "--model", "m", "--probe"], capture_output=True, text=True, timeout=60, env=env,
                          stdin=subprocess.DEVNULL)
    event = json.loads(proc.stdout.strip().splitlines()[-1])
    assert event["ok"] is False and event["error"]["kind"] == "unavailable"
    assert event["error"]["message"] and event["error"]["hint"]
