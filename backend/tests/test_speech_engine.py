"""The speech engines and their registry (DESIGN.md §38.3, criteria 259 to 261 and 291).

What these protect: that an engine is described, looked up and made in one place; that the fake engines are deterministic and honest about what
they can do (the rest of the tests build on them); that a new engine needs one file and nothing else; and that no code outside the registry
and the engine files names an engine.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from speech_support import RUN_ID, ToyEngine, in_process_worker, piece, speech_job

from studio import wavfile
from studio.pipelines.base import Canceled, OutOfMemory, PipelineError, PipelineUnavailable
from studio.speech import registry
from studio.speech.engine import EngineSettings, PieceRequest, VoiceRef
from studio.speech.engines.fake import FakeBasicSpeech, FakeSpeech

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent


# ---------------------------------------------------------------- the registry
def test_the_fake_setting_offers_the_fakes_and_the_real_setting_the_real_engines():
    assert registry.engine_ids("fake") == ["fake", "fake-basic"]
    # the order the page lists them in: the two the owner asked for first
    assert registry.engine_ids("real") == ["qwen3-tts", "voxtral", "kokoro", "piper"]


def test_the_engines_setting_narrows_the_list_and_keeps_the_registry_order():
    assert registry.engine_ids("real", ("piper", "qwen3-tts")) == ["qwen3-tts", "piper"]
    assert registry.engine_ids("fake", ("fake-basic",)) == ["fake-basic"]
    assert registry.engine_ids("fake", ("kokoro",)) == []  # an engine the setting does not offer stays out even if asked for by name


def test_an_unknown_engine_or_variant_is_refused_by_name():
    with pytest.raises(registry.UnknownEngine):
        registry.info("no-such-engine")
    with pytest.raises(registry.UnknownEngine):
        registry.make_engine("fake", EngineSettings(variant="no-such-variant"))


def test_an_engine_that_cannot_be_imported_is_unavailable_with_a_reason_not_a_crash(monkeypatch):
    """Importing a real engine's module must never stop the others: the API asks every engine about itself at start-up (criterion 259)."""
    monkeypatch.setitem(registry.REAL_ENGINES, "ghost", ("studio.speech.engines.does_not_exist", "Ghost"))
    with pytest.raises(PipelineUnavailable) as caught:
        registry.info("ghost")
    assert "ghost" in caught.value.message and caught.value.hint


def test_every_engine_describes_itself_consistently():
    """What `EngineInfo` says must hang together, because the queue and the page act on it without knowing the engine."""
    for cls in (FakeSpeech, FakeBasicSpeech):
        info = cls.INFO
        assert info.preset_variant in info.variants
        # an engine that can clone (or design) says which variant does it, and one that cannot says none
        assert (info.clone_variant is not None) == info.can_clone and (info.design_variant is not None) == info.can_design
        for variant in (info.clone_variant, info.design_variant):
            assert variant is None or variant in info.variants
        assert 0 < info.min_chars < info.max_chars and info.sample_rate > 0
        json.dumps(info.as_dict())  # the page is sent this
        assert all(v.id and v.label for v in cls.PRESETS) and len({v.id for v in cls.PRESETS}) == len(cls.PRESETS)


def test_variant_for_chooses_the_checkpoint_that_does_the_work():
    full, plain = FakeSpeech.INFO, FakeBasicSpeech.INFO
    assert registry.variant_for(full, "reading", saved_voice=False) == "default"
    assert registry.variant_for(full, "reading", saved_voice=True) == "clone"
    assert registry.variant_for(full, "sample", saved_voice=True) == "clone"
    assert registry.variant_for(full, "design", saved_voice=False) == "design"
    assert registry.variant_for(plain, "reading", saved_voice=True) is None  # cannot clone
    assert registry.variant_for(plain, "design", saved_voice=False) is None  # cannot design


# ---------------------------------------------------------------- the fake engines
def speak(engine, text="Hello there, this is a test.", **request):
    return engine.synthesize(PieceRequest(text=text, **request), lambda: False)


def test_the_same_request_gives_the_same_audio_and_each_difference_changes_it():
    engine = FakeSpeech(EngineSettings())
    base = speak(engine, voice="anna", style="calm", seed=1)
    assert speak(engine, voice="anna", style="calm", seed=1).pcm == base.pcm
    for different in (dict(voice="carl", style="calm", seed=1), dict(voice="anna", style="loud", seed=1), dict(voice="anna", style="calm", seed=2),
                      dict(voice="anna", style="calm", seed=1, language="German")):
        assert speak(engine, **different).pcm != base.pcm, different
    assert speak(engine, "A different sentence entirely.", voice="anna", style="calm", seed=1).pcm != base.pcm


def test_a_saved_voice_is_told_apart_by_its_words_and_a_design_by_its_description():
    engine = FakeSpeech(EngineSettings(variant="clone"))
    one = speak(engine, ref=VoiceRef("/x/a.wav", "words one", "cloned"))
    two = speak(engine, ref=VoiceRef("/y/b.wav", "words two", "cloned"))
    moved = speak(engine, ref=VoiceRef("/elsewhere/a.wav", "words one", "cloned"))
    assert one.pcm != two.pcm and one.pcm == moved.pcm  # the clip's place on the disk is not what makes a voice
    designer = FakeSpeech(EngineSettings(variant="design"))
    assert speak(designer, design="warm and low").pcm != speak(designer, design="bright and fast").pcm


def test_the_two_fakes_speak_at_their_own_rates_and_longer_text_is_longer_audio():
    for cls in (FakeSpeech, FakeBasicSpeech):
        engine = cls(EngineSettings())
        short, long = speak(engine, "Short."), speak(engine, "A much longer piece of text. " * 6)
        assert short.sample_rate == long.sample_rate == cls.INFO.sample_rate
        assert len(long.pcm) > len(short.pcm) and len(short.pcm) % 2 == 0
    assert FakeSpeech.INFO.sample_rate != FakeBasicSpeech.INFO.sample_rate  # a reading's rate is never assumed to be one number


def test_the_directives_fail_a_piece_in_the_ways_the_tests_need():
    engine = FakeSpeech(EngineSettings())
    with pytest.raises(PipelineError) as plain:
        speak(engine, "This breaks [fake:error] here.")
    assert plain.value.kind == "error"
    with pytest.raises(OutOfMemory):
        speak(engine, "This runs out [fake:oom] of memory.")


def test_a_slow_piece_notices_cancel_within_a_slice():
    engine = FakeSpeech(EngineSettings())
    started = time.monotonic()
    answers = iter([False, False, True])  # Cancel arrives while it waits
    with pytest.raises(Canceled):
        engine.synthesize(PieceRequest(text="Take your time [fake:slow] please."), lambda: next(answers, True))
    assert time.monotonic() - started < 1.0  # it did not wait the full two seconds
    with pytest.raises(Canceled):  # and a Cancel that was already pressed stops it before it starts
        engine.synthesize(PieceRequest(text="Hello"), lambda: True)


def test_the_fake_probe_and_load_work_without_any_library():
    assert FakeSpeech.probe() == {"device": "fake"}
    assert FakeSpeech(EngineSettings()).load() == {"device": "fake"}


# ---------------------------------------------------------------- a new engine needs one file and a line (criterion 260)
def test_an_engine_nobody_has_heard_of_runs_a_whole_job_through_the_worker(tmp_path):
    """Register a toy engine at run time and drive the worker with it: the worker, the job checks and the piece files need nothing else."""
    from studio.speech.registry import register, unregister
    from studio.worker import Worker
    from speech_support import Capture

    register(ToyEngine)
    try:
        assert "toy" in registry.engine_ids("fake") and registry.info("toy").title == "Toy engine"
        emit = Capture()
        worker = Worker(registry.make_engine("toy", EngineSettings()), tmp_path, emit, kind="speech")  # type: ignore[arg-type]
        job = speech_job([piece(0, "x" * 50, pause_ms=0), piece(1, "y" * 20, pause_ms=100)], engine="toy", voice="toy_voice")
        worker.run(job)
        done = emit.of("piece_done")
        assert [d["key"] for d in done] == ["0-0", "0-1"] and emit.kinds()[-1] == "run_finished"
        # 50 characters at 10 ms each is 0.5 s at 8 kHz; the second piece is 20 x 10 ms plus a 100 ms pause
        assert done[0]["frames"] == 4000 and done[1]["frames"] == 1600 + 800 and {d["sample_rate"] for d in done} == {8000}
        assert wavfile.read_wav(tmp_path / done[0]["path"]).frames == 4000
    finally:
        unregister("toy")
    assert "toy" not in registry.engine_ids("fake")


def test_no_code_names_an_engine_outside_the_registry_and_the_engine_files():
    """Criterion 260 is a promise about the whole code base, so it is checked against the source: an engine's id written as a string anywhere else
    (the queue, the worker, the database, the API, the page) is a place that would need editing for the next model. Prose and comments are fine."""
    names = re.compile(r"""["'`](qwen3-tts|voxtral|kokoro|piper)["'`]""")
    allowed = {BACKEND / "studio" / "speech" / "registry.py"}
    offenders = []
    roots = [BACKEND / "studio", REPO / "frontend" / "src"]
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "engines" in path.parts and "speech" in path.parts or path in allowed:
                continue
            if "test" in path.name.lower():  # a test may name an engine to test it; the promise is about the code that ships
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if names.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()[:80]}")
    assert not offenders, "\n".join(offenders)


def test_the_jobs_a_worker_is_given_name_no_engine_either():
    """The job builder in the tests is generic over the engine, which is what the API's job builder has to be too."""
    assert speech_job(engine="anything")["engine"] == "anything" and RUN_ID in speech_job()["pieces"][0]["out"]
    worker, _ = in_process_worker(Path("/nonexistent"), engine="fake-basic")
    assert worker.pipeline.name == "fake-basic" and worker.kind == "speech"
