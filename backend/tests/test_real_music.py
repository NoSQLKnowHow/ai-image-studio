"""The real music pipeline code against the real `diffusers` implementation (DESIGN.md §26.4), on a tiny random-weight
copy of the model (see music_tiny.py) so that it runs on a CPU in seconds. Skipped where `torch` and a `diffusers` that
has the MiniMax-Music3 pipeline (0.40.0) are not installed, which is the plain test environment: the container's music
worker has both. What this proves is the plumbing around the model (the hooks that report progress and carry out Cancel,
the shape and rate of the output, the files the worker writes). What the real model sounds like it cannot say."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("diffusers.modular_pipelines.minimax_music3")
pytest.importorskip("tokenizers")

import diffusers.modular_pipelines.modular_pipeline as modular  # noqa: E402

from music_tiny import build_tiny_music_repo  # noqa: E402
from studio import wavfile  # noqa: E402
from studio.pipelines import real_music  # noqa: E402
from studio.pipelines.base import Canceled, MusicJob, PipelineError, PipelineUnavailable  # noqa: E402
from studio.pipelines.real_music import RealMusicPipeline  # noqa: E402
from studio.worker import Worker  # noqa: E402

RUN_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build_tiny_music_repo(tmp_path_factory.mktemp("tiny-music"))


@pytest.fixture(scope="module")
def pipe(repo):
    pipeline = RealMusicPipeline(str(repo), hub_mode="offline", device="cpu", dtype="float32")
    pipeline.load()
    return pipeline


def job(duration: int = 2, steps: int = 4, lyrics: str = "[Instrumental]", prompt: str = "Genre: ambient. A slow piano.", seeds=(7,)) -> MusicJob:
    return MusicJob(run_id=RUN_ID, mode="music", prompt=prompt, lyrics=lyrics, duration=duration, steps=steps, seeds=list(seeds),
                    model_id="tiny-music")


def make(pipe: RealMusicPipeline, music_job: MusicJob, seed: int = 7):
    seen: list[tuple[str, int, int]] = []
    result = pipe.generate(music_job, 0, seed, lambda *a: seen.append(a))
    return result, seen


# ------------------------------------------------------------------ what comes out
def test_a_track_has_the_shape_rate_and_length_the_pipeline_reports(pipe):
    result, _ = make(pipe, job(duration=2))
    assert (result.sample_rate, result.channels) == (44100, 2)
    assert len(result.pcm) == int(result.seconds * 44100 + 0.5) * 2 * 2  # 16-bit, interleaved, two channels
    assert result.seconds == pytest.approx(2.0, abs=0.05) and any(result.pcm)  # about what was asked for, and not silence


def test_the_same_seed_gives_the_same_samples_and_another_seed_others(pipe):
    a, _ = make(pipe, job(), seed=7)
    b, _ = make(pipe, job(), seed=7)
    c, _ = make(pipe, job(), seed=8)
    assert a.pcm == b.pcm != c.pcm


def test_a_different_description_or_lyrics_change_the_result(pipe):
    base, _ = make(pipe, job())
    assert make(pipe, job(prompt="Genre: techno. A fast kick drum."))[0].pcm != base.pcm
    assert make(pipe, job(lyrics="[verse]\nla la la"))[0].pcm != base.pcm


# ------------------------------------------------------------------ progress
def test_progress_counts_every_frame_then_every_render_step_then_finishes(pipe):
    _, seen = make(pipe, job(duration=2, steps=4))
    compose = [s for s in seen if s[0] == "compose"]
    assert compose[0] == ("compose", 1, 50) and compose[-1] == ("compose", 50, 50)  # 2 s at 25 frames a second
    assert [c[1] for c in compose] == sorted(c[1] for c in compose) and all(c[2] == 50 for c in compose)
    render = [s for s in seen if s[0] == "render"]
    assert render == [("render", i, 4) for i in range(1, 5)]  # one window of 4 steps
    assert seen.index(compose[-1]) < seen.index(render[0]) and seen[-1] == ("finish", 0, 1)
    assert seen.count(("finish", 0, 1)) == 1


def test_a_long_track_is_rendered_in_several_windows_and_the_total_says_so(pipe):
    _, seen = make(pipe, job(duration=10, steps=4))  # 250 frames: two 200-frame windows with a 100-frame hop
    render = [s for s in seen if s[0] == "render"]
    assert [r[2] for r in render] == [8] * 8 and [r[1] for r in render] == list(range(1, 9))  # 2 windows x 4 steps


def test_the_progress_bar_the_pipeline_uses_is_put_back_afterwards(pipe):
    original = modular.tqdm
    make(pipe, job())
    assert modular.tqdm is original
    assert len(pipe._pipe.language_model.lm_head._forward_pre_hooks) == 0  # and the frame counter is gone


# ------------------------------------------------------------------ cancel
class StopAt:
    """A progress callback that cancels at a given point, as the worker's does when the command reader has heard Cancel."""

    def __init__(self, stage: str, step: int):
        self.stage, self.step, self.seen = stage, step, []

    def __call__(self, stage: str, step: int, total: int) -> None:
        self.seen.append((stage, step))
        if (stage, step) == (self.stage, self.step):
            raise Canceled()


def test_cancel_while_composing_stops_at_that_frame_and_cleans_up(pipe):
    original = modular.tqdm
    stop = StopAt("compose", 5)
    with pytest.raises(Canceled):
        pipe.generate(job(duration=4), 0, 7, stop)
    assert stop.seen[-1] == ("compose", 5)  # not one frame further
    assert not any(s == "render" for s, _ in stop.seen)
    assert modular.tqdm is original and len(pipe._pipe.language_model.lm_head._forward_pre_hooks) == 0


def test_cancel_while_rendering_stops_at_that_step_and_cleans_up(pipe):
    original = modular.tqdm
    stop = StopAt("render", 2)
    with pytest.raises(Canceled):
        pipe.generate(job(duration=2, steps=4), 0, 7, stop)
    assert stop.seen[-1] == ("render", 2) and ("finish", 0) not in stop.seen
    assert modular.tqdm is original and len(pipe._pipe.language_model.lm_head._forward_pre_hooks) == 0


def test_the_pipeline_works_again_after_a_cancel(pipe):
    with pytest.raises(Canceled):
        pipe.generate(job(), 0, 7, StopAt("compose", 3))
    result, _ = make(pipe, job())
    assert result.seconds > 1 and make(pipe, job())[0].pcm == result.pcm  # nothing was left over from the canceled one


# ------------------------------------------------------------------ failures
def test_the_models_own_refusals_are_reported_plainly(pipe):
    with pytest.raises(PipelineError) as info:
        pipe.generate(job(lyrics=""), 0, 7, lambda *a: None)  # the pipeline refuses empty lyrics
    assert "rejected the request" in info.value.message and "lyrics" in info.value.message and "5,000-token" in info.value.hint


def test_a_pipeline_that_was_never_loaded_says_so(repo):
    with pytest.raises(PipelineError, match="not loaded"):
        RealMusicPipeline(str(repo), device="cpu").generate(job(), 0, 7, lambda *a: None)


# ------------------------------------------------------------------ loading
@pytest.mark.parametrize("mode", ["auto", "offline", "online"])
def test_the_model_loads_from_a_local_folder_in_every_cache_mode(repo, mode):
    loaded = RealMusicPipeline(str(repo), hub_mode=mode, device="cpu", dtype="float32").load()
    assert loaded["supports"] == {"music": True, "instrumental": True, "step_progress": True, "cancel": True}


def test_a_folder_that_is_not_there_fails_the_load_with_an_explanation(tmp_path):
    with pytest.raises(PipelineError) as info:
        RealMusicPipeline(str(tmp_path / "nowhere"), hub_mode="offline", device="cpu", dtype="float32").load()
    assert info.value.kind == "load_failed" and info.value.message


def test_the_start_up_check_gets_as_far_as_asking_for_a_gpu(monkeypatch):
    """Importing the music pipeline works with this diffusers; what is missing on a CPU-only machine is the GPU."""
    assert real_music.import_music_runtime()[1].__name__ == "ModularPipeline"
    with pytest.raises(PipelineUnavailable) as info:
        real_music.probe()
    assert "No CUDA GPU is visible" in info.value.message
    monkeypatch.setattr(real_music, "require_gpu", lambda torch: None)
    result = real_music.probe()
    assert result["supports"]["music"] is True and result["diffusers"] == "0.40.0"


def test_an_old_diffusers_without_the_music_pipeline_is_called_unavailable_with_the_fix(monkeypatch):
    import diffusers.modular_pipelines as pipelines

    monkeypatch.delattr(pipelines, "MiniMaxMusic3ModularPipeline", raising=False)
    monkeypatch.setattr(pipelines.__class__, "__getattr__", lambda self, name: (_ for _ in ()).throw(AttributeError(name)), raising=False)
    with pytest.raises(PipelineUnavailable) as info:
        real_music.import_music_runtime()
    assert "does not provide the MiniMax-Music3 pipeline" in info.value.message and "STUDIO_MUSIC_LIBS" in info.value.hint


# ------------------------------------------------------------------ the worker around it
class Collect:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **fields) -> None:
        self.events.append({"event": event, **fields})

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["event"] == kind]


def job_dict(**extra) -> dict:
    j = job(**extra)
    return {k: getattr(j, k) for k in ("run_id", "mode", "prompt", "lyrics", "duration", "steps", "seeds", "model_id")}


def test_the_worker_makes_a_playable_wav_with_the_real_pipeline_code(repo, tmp_path):
    emit = Collect()
    worker = Worker(RealMusicPipeline(str(repo), hub_mode="offline", device="cpu", dtype="float32"), tmp_path, emit, kind="music")
    worker.run(job_dict(seeds=(7, 8)))
    done = emit.of("track_done")
    assert [(e["idx"], e["seed"], e["sample_rate"], e["channels"]) for e in done] == [(0, 7, 44100, 2), (1, 8, 44100, 2)]
    assert emit.events[-1] == {"event": "run_finished", "run_id": RUN_ID, "completed": 2}
    info = wavfile.read_wav(tmp_path / done[0]["path"])
    assert (info.sample_rate, info.channels) == (44100, 2) and info.seconds == pytest.approx(2.0, abs=0.05)
    assert info.seconds == pytest.approx(done[0]["seconds"], abs=0.001) and "machine-generated" in info.info["comment"]
    stages = [e["stage"] for e in emit.of("progress") if e["image"] == 1]
    assert stages[0] == "compose" and "render" in stages and stages[-1] == "finish"


def test_cancel_reaches_the_real_pipeline_through_the_worker(repo, tmp_path):
    emit = Collect()
    worker = Worker(RealMusicPipeline(str(repo), hub_mode="offline", device="cpu", dtype="float32"), tmp_path, emit, kind="music")
    original = worker.pipeline.generate

    def generate(music_job, index, seed, on_progress):
        def hooked(stage, step, total):
            if (stage, step) == ("compose", 6):
                worker.request_cancel(music_job.run_id)  # what the command-reader thread does on hearing Cancel
            on_progress(stage, step, total)

        return original(music_job, index, seed, hooked)

    worker.pipeline.generate = generate
    worker.run(job_dict(duration=4, seeds=(1, 2)))
    assert emit.events[-1] == {"event": "run_canceled", "run_id": RUN_ID, "completed": 0}
    assert [e["step"] for e in emit.of("progress") if e["stage"] == "compose"][-1] <= 6  # it stopped there
    assert not list(tmp_path.rglob("*.wav")) and not list(tmp_path.rglob("*.part"))


def test_the_command_line_example_makes_a_wav_on_the_tiny_model(repo, tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "minimax_music.py"
    out = tmp_path / "example.wav"
    done = subprocess.run([sys.executable, str(script), "--genre", "ambient", "--mood", "slow", "--duration", "10", "--steps", "10", "--seed", "3",
                           "--model", str(repo), "--hub", "offline", "--device", "cpu", "--dtype", "float32", "--out", str(out)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    info = wavfile.read_wav(out)
    assert (info.sample_rate, info.channels) == (44100, 2) and info.seconds == pytest.approx(10.0, abs=0.05)
    assert "machine-generated" in info.info["comment"] and "composing" in done.stderr and "rendering" in done.stderr
    assert "say so" in done.stderr
