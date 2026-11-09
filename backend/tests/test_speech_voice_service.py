"""The voice service without a server (DESIGN.md §38.4, §38.11): the names voices are compared by, the jobs sent to the worker for a sample and a design, and
what the worker reports back for them, which is the one input here that comes from outside and is therefore tested with reports that are wrong and hostile.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from test_speech_service import READY, Env

from studio import wavfile
from studio.speech import registry
from studio.speech.passage import DESIGN_CANDIDATES, PASSAGE, SAMPLE_SENTENCE
from studio.speech.refused import ReadingRefused
from studio.speech.voices import NAME_MAX, file_stem, name_key
from studio.storage import StorageError


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


def hidden_sample(env: Env, **target: Any) -> sqlite3.Row:
    """A queued sample job for a preset voice, inserted as the job manager would."""
    voices = env.service.voices
    run = voices.sample_run(voices.sample_target(READY, "fake", **({"voice": "anna"} | target)))
    assert env.db.insert_run(run, 100) == "ok"
    return env.db.get_run(run["id"])


def hidden_design(env: Env) -> sqlite3.Row:
    voices = env.service.voices
    run = voices.design_run(READY, "a warm voice", "fake")
    assert env.db.insert_run(run, 100) == "ok"
    return env.db.get_run(run["id"])


def write_mono(env: Env, rel: str, frames: int = 4800, channels: int = 1) -> None:
    wavfile.write_wav(env.storage.abs(rel), bytes(2 * channels * frames), 24000, channels=channels)


# ---------------------------------------------------------------- names
@pytest.mark.parametrize("a,b", [("Kirk", "kirk"), ("  Kirk  ", "KIRK"), ("Zoë", "ZOË"), ("Zoë", "Zoë"), ("A  B", "a b"), ("Ｋirk", "kirk")])
def test_names_that_differ_only_in_case_spacing_or_how_an_accent_is_written_are_one_name(a, b):
    assert name_key(a) == name_key(b)


@pytest.mark.parametrize("a,b", [("Kirk", "Kirks"), ("Zoë", "Zoe"), ("A B", "AB")])
def test_names_that_differ_in_letters_are_two(a, b):
    assert name_key(a) != name_key(b)


def test_a_name_is_tidied_and_must_be_between_one_and_sixty_characters(env):
    voices = env.service.voices
    assert voices.clean_name("  Warm \n narrator ") == ("Warm narrator", "warm narrator")
    assert voices.clean_name("n" * NAME_MAX)[0] == "n" * NAME_MAX
    for bad, code in (("", "name_empty"), (" \n\t ", "name_empty"), ("n" * (NAME_MAX + 1), "name_too_long")):
        with pytest.raises(ReadingRefused) as caught:
            voices.clean_name(bad)
        assert caught.value.code == code and caught.value.field == "name" and caught.value.status == 422


@pytest.mark.parametrize("identifier,same", [("af_bella", True), ("Jenny", True), ("en_GB-jenny.dioritic", True), ("a/b", False), ("../x", False), (".hidden", False),
                                              ("", False), ("x" * 65, False), ("voz com espaço", False)])
def test_a_preset_id_becomes_a_safe_file_name_itself_where_it_can_and_a_digest_where_not(identifier, same):
    stem = file_stem(identifier)
    assert (stem == identifier) is same and stem and "/" not in stem and not stem.startswith(".") and len(stem) <= 64
    assert file_stem(identifier) == stem  # and it is always the same name for the same id


# ---------------------------------------------------------------- what a sample is of
def test_a_sample_of_a_preset_has_a_place_a_key_and_the_presets_variant(env):
    target = env.service.voices.sample_target(READY, "fake", voice="anna")
    assert (target.key, target.group, target.stem, target.variant, target.voice, target.ref, target.label) == ("preset:fake:anna", "fake", "anna", "default", "anna", None, "Anna")
    assert target.rel == "speech/samples/fake/anna.wav" and target.url == "/api/speech/samples/fake/anna.wav"


def test_a_sample_of_a_saved_voice_is_made_through_the_clone_variant_from_its_recording(env):
    voice = env.add_voice(kind="designed")
    target = env.service.voices.sample_target(READY, "fake", saved_voice=voice)
    assert (target.key, target.group, target.variant, target.voice) == (f"voice:{voice}", "voice", "clone", "")
    assert target.ref == {"path": f"speech/voices/{voice}/reference.wav", "text": "the words said", "kind": "designed"}


# ---------------------------------------------------------------- the job sent to the worker
def test_the_job_for_a_sample_is_one_piece_of_the_fixed_sentence_with_no_pause_and_the_voice(env):
    row = hidden_sample(env)
    job = env.service.job_for(row)
    assert job["task"] == "sample" and (job["engine"], job["variant"], job["voice"], job["ref"], job["style"], job["language"]) == ("fake", "default", "anna", None, None, None)
    assert job["pieces"] == [{"key": "sample", "text": SAMPLE_SENTENCE, "kind": "paragraph", "seed": row["seed"], "out": "speech/samples/fake/anna.wav", "pause_ms": 0}]
    assert env.service.has_work(row) is True and env.service.is_reading(row) is False


def test_the_job_for_a_design_is_the_passage_three_times_with_three_seeds_and_the_description(env):
    row = hidden_design(env)
    job = env.service.job_for(row)
    assert job["task"] == "design" and job["design"] == "a warm voice" and job["voice"] == "" and job["ref"] is None and job["variant"] == "design"
    assert [p["key"] for p in job["pieces"]] == ["c0", "c1", "c2"] and [p["text"] for p in job["pieces"]] == [PASSAGE] * DESIGN_CANDIDATES
    assert [p["seed"] for p in job["pieces"]] == [row["seed"], row["seed"] + 1, row["seed"] + 2]
    assert [p["out"] for p in job["pieces"]] == [f"speech/candidates/{row['id']}/{k}.wav" for k in range(3)] and {p["pause_ms"] for p in job["pieces"]} == {0}


def test_the_job_the_service_makes_is_one_the_worker_accepts(env):
    """The worker checks every job before believing it (job.py); a job the API makes must pass, whatever else is true of it."""
    from studio.speech.job import SpeechJob

    voice = env.add_voice()
    write_mono(env, f"speech/voices/{voice}/reference.wav")
    for row in (hidden_sample(env), hidden_sample(env, voice="", saved_voice=voice), hidden_design(env)):
        SpeechJob.from_dict(env.service.job_for(row), env.settings.data_dir)


def test_the_job_for_a_sample_made_from_a_recording_that_has_gone_fails_with_words(env):
    voice = env.add_voice()
    row = hidden_sample(env, voice="", saved_voice=voice)
    with pytest.raises(ValueError, match="no longer here"):
        env.service.job_for(row)


def test_the_design_prefers_the_first_engine_that_can_design_and_the_passage_fits_every_such_engine(env):
    run = env.service.voices.design_run(READY, "a voice", None)
    assert json.loads(run["options_json"])["engine"] == "fake"
    for engine_id in registry.engine_ids("fake"):
        info = registry.info(engine_id)
        if info.can_design:
            assert len(PASSAGE) <= info.max_chars, f"{engine_id} could not be given the passage as one piece"


def test_a_design_by_an_engine_that_can_design_but_not_clone_is_refused_because_the_voice_could_not_be_used(env):
    from dataclasses import replace

    import studio.speech.engines.fake as fake

    original = fake.FakeSpeech.INFO
    fake.FakeSpeech.INFO = replace(original, clone_variant=None)
    try:
        with pytest.raises(ReadingRefused) as caught:
            env.service.voices.design_run(READY, "a voice", "fake")
    finally:
        fake.FakeSpeech.INFO = original
    assert caught.value.code == "design_unusable" and caught.value.status == 422


# ---------------------------------------------------------------- what the worker reports back
def test_a_file_made_for_a_sample_is_accepted_when_it_is_the_one_named(env):
    row = hidden_sample(env)
    write_mono(env, "speech/samples/fake/anna.wav", 12000)
    assert env.service.voices.accept_file(row, {"key": "sample", "path": "speech/samples/fake/anna.wav"}) == {"key": "sample", "seconds": 0.5}


def test_a_report_of_any_other_path_is_refused_even_a_real_file_inside_the_speech_folder(env):
    """Criterion: the worker is trusted to write where it was told, never believed about it. A file that exists, is a mono WAV and lies inside speech/ is still
    not the file this job was to make; accepting it would let a confused or compromised worker point the studio at another job's audio."""
    row = hidden_sample(env)
    write_mono(env, "speech/samples/fake/anna.wav")
    write_mono(env, "speech/samples/fake/bea.wav")
    write_mono(env, f"speech/readings/{'e' * 32}/0000/00000.wav")
    for bad in ("speech/samples/fake/bea.wav", f"speech/readings/{'e' * 32}/0000/00000.wav", "speech/samples/fake/../fake/anna.wav", "/etc/passwd", "", "speech/samples/fake/anna.wav "):
        with pytest.raises(StorageError, match="was to be written to"):
            env.service.voices.accept_file(row, {"key": "sample", "path": bad})


def test_a_report_for_a_piece_the_job_does_not_have_is_refused(env):
    row = hidden_sample(env)
    write_mono(env, "speech/samples/fake/anna.wav")
    for key in ("c0", "", "None", "sample "):
        with pytest.raises(ValueError, match="does not have"):
            env.service.voices.accept_file(row, {"key": key, "path": "speech/samples/fake/anna.wav"})
    with pytest.raises(ValueError):
        env.service.voices.accept_file(row, {"path": "speech/samples/fake/anna.wav"})  # no key at all


def test_a_file_that_is_not_there_or_not_mono_or_not_audio_is_refused(env):
    row = hidden_sample(env)
    with pytest.raises(StorageError, match="missing file"):
        env.service.voices.accept_file(row, {"key": "sample", "path": "speech/samples/fake/anna.wav"})
    write_mono(env, "speech/samples/fake/anna.wav", channels=2)
    with pytest.raises(ValueError, match="must be mono"):
        env.service.voices.accept_file(row, {"key": "sample", "path": "speech/samples/fake/anna.wav"})
    env.storage.abs("speech/samples/fake/anna.wav").write_bytes(b"this is not a wav")
    with pytest.raises(wavfile.WavError):
        env.service.voices.accept_file(row, {"key": "sample", "path": "speech/samples/fake/anna.wav"})


def test_each_candidate_of_a_design_is_accepted_for_its_own_key_only(env):
    row = hidden_design(env)
    for k in range(3):
        write_mono(env, f"speech/candidates/{row['id']}/{k}.wav")
    assert env.service.voices.accept_file(row, {"key": "c1", "path": f"speech/candidates/{row['id']}/1.wav"})["key"] == "c1"
    with pytest.raises(StorageError):
        env.service.voices.accept_file(row, {"key": "c1", "path": f"speech/candidates/{row['id']}/2.wav"})  # candidate 2's file reported as candidate 1


# ---------------------------------------------------------------- how a job stands
def test_the_status_of_a_sample_lists_its_file_once_it_exists_and_its_error_when_it_failed(env):
    row = hidden_sample(env)
    voices = env.service.voices
    assert voices.job_status(row) == {"id": row["id"], "task": "sample", "status": "queued", "target": "preset:fake:anna", "label": "Anna", "error": None, "files": []}
    write_mono(env, "speech/samples/fake/anna.wav")
    assert voices.job_status(row)["files"] == [{"key": "sample", "url": "/api/speech/samples/fake/anna.wav"}]
    env.db.mark_running(row["id"], "2026-11-08T10:00:00.000Z")
    env.db.finish_run(row["id"], "failed", "2026-11-08T10:00:01.000Z", "it broke", "try again")
    failed = voices.job_status(env.db.get_run(row["id"]))
    assert failed["status"] == "failed" and failed["error"] == {"message": "it broke", "hint": "try again"}


def test_the_status_of_a_design_lists_the_candidates_made_so_far_in_order(env):
    row = hidden_design(env)
    write_mono(env, f"speech/candidates/{row['id']}/0.wav")
    write_mono(env, f"speech/candidates/{row['id']}/2.wav")  # (1 is not made yet)
    status = env.service.voices.job_status(row)
    assert [f["index"] for f in status["files"]] == [0, 2] and status["files"][1]["url"] == f"/api/speech/candidates/{row['id']}/2.wav" and status["description"] == "a warm voice"


def test_a_hidden_job_is_not_described_as_a_reading(env):
    assert env.service.readings([hidden_sample(env)]) == {}
