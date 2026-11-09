"""Voices over HTTP (DESIGN.md §38.4, §38.9; criteria 261 to 267): presets and their samples, designing a voice from a description, uploading a recording to
clone, saving and renaming and deleting a voice, and speaking a reading in a voice that was made.

Whole paths through the real job manager and the speech worker (a subprocess) with the fake engines; nothing needs a model, a GPU, the network or ffmpeg
(criterion 291). The reading of a recording itself is tested in test_speech_recording.py, with the formats that need ffmpeg skipped where there is none.
"""

from __future__ import annotations

import io
import time
import wave
from typing import Any

import pytest
from conftest import wait_for
from test_speech_readings import SLOW, make_reading, part, reading_body, wait_until
from test_speech_recording import silence, tone, wav
from test_speech_server import wait_for_engines

from studio.speech.passage import CONSENT_STATEMENT, PASSAGE, SAMPLE_SENTENCE


def wait_job(client, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
    """Poll a sample or a design until it has finished (whichever way)."""
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/speech/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "canceled"):
            return job
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} is still {job['status']!r} after {timeout}s")
        time.sleep(0.02)


def sample(client, expect: int = 200, **body: Any) -> dict[str, Any]:
    wait_for_engines(client)
    response = client.post("/api/speech/samples", json={"engine": "fake", **body})
    assert response.status_code == expect, response.text
    return response.json()


def upload(client, data: bytes, expect: int = 201) -> dict[str, Any]:
    wait_for_engines(client)
    response = client.post("/api/speech/recordings", content=data, headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == expect, response.text
    return response.json()


def design(client, description: str = "a warm Irish woman in her forties, unhurried and clear", expect: int = 202, **body: Any) -> dict[str, Any]:
    wait_for_engines(client)
    response = client.post("/api/speech/voices/design", json={"description": description, **body})
    assert response.status_code == expect, response.text
    return response.json()


def save(client, expect: int = 201, **body: Any) -> dict[str, Any]:
    response = client.post("/api/speech/voices", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def wav_info(data: bytes) -> tuple[int, int, int, int]:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


def saved_designed_voice(client, name: str = "Warm narrator") -> dict[str, Any]:
    job = wait_job(client, design(client)["id"])
    return save(client, kind="designed", name=name, candidate={"job": job["id"], "index": 1})


def saved_cloned_voice(client, name: str = "My own voice") -> dict[str, Any]:
    recording = upload(client, wav(tone(10)))
    return save(client, kind="cloned", name=name, recording=recording["id"], engine="fake", consent=True)


# ---------------------------------------------------------------- what there is to choose from
def test_an_engines_voices_are_its_presets_as_the_source_states_them_and_the_voices_made_for_it(client):
    wait_for_engines(client)
    listing = client.get("/api/speech/engines/fake/voices").json()
    assert listing["engine"] == "fake" and listing["saved"] == [] and [v["id"] for v in listing["presets"]] == ["anna", "bea", "carl", "dan"]
    anna, bea = listing["presets"][0], listing["presets"][1]
    assert (anna["label"], anna["gender"], anna["language"], anna["accent"], anna["styleable"]) == ("Anna", "female", "English", "American", True)
    assert bea["accent"] is None  # "accent not stated" is the page's to say; the server never guesses one (criterion 261)
    assert anna["sample"] == {"ready": False, "url": None}


def test_an_engine_that_cannot_clone_lists_no_saved_voices_and_an_unknown_one_is_not_found(client):
    wait_for_engines(client)
    saved_designed_voice(client)
    assert client.get("/api/speech/engines/fake/voices").json()["saved"][0]["name"] == "Warm narrator"
    assert client.get("/api/speech/engines/fake-basic/voices").json()["saved"] == []  # (a voice belongs to its engine; this one cannot speak in it)
    assert client.get("/api/speech/engines/no-such/voices").status_code == 404


def test_the_passage_the_sample_sentence_and_the_statement_come_from_the_server(client):
    body = client.get("/api/speech/passage").json()
    assert body["text"] == PASSAGE and body["sample_sentence"] == SAMPLE_SENTENCE and body["consent"] == CONSENT_STATEMENT
    assert body["voice_seconds"] == {"min": 8, "max": 60} and body["name_max"] == 60


# ---------------------------------------------------------------- samples
def test_play_sample_makes_it_once_keeps_it_and_the_second_press_is_instant(client):
    jobs = client.app.state.jobs
    first = sample(client, expect=202, voice="anna")
    assert first["ready"] is False and first["job"]["task"] == "sample" and first["job"]["status"] in ("queued", "running")
    done = wait_job(client, first["job"]["id"])
    assert done["status"] == "done" and [f["key"] for f in done["files"]] == ["sample"]
    audio = client.get(done["files"][0]["url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav" and wav_info(audio.content)[:3] == (1, 2, 24000)
    assert 0.9 < wav_info(audio.content)[3] / 24000 < 1.1  # the fixed sentence (the fake engine speaks 0.3 s and 12 ms a character): a sentence, not a click
    again = sample(client, voice="anna")  # 200, not 202: nothing was queued
    assert again == {"ready": True, "url": done["files"][0]["url"], "job": None}
    assert len(jobs.speech.store.hidden_jobs(["queued", "running", "done"])) == 1  # (the second press made no job)
    listing = client.get("/api/speech/engines/fake/voices").json()["presets"][0]
    assert listing["sample"] == {"ready": True, "url": done["files"][0]["url"]}


def test_each_voice_has_a_sample_of_its_own(client):
    urls = {}
    for voice in ("anna", "carl"):
        job = wait_job(client, sample(client, expect=202, voice=voice)["job"]["id"])
        urls[voice] = job["files"][0]["url"]
    assert urls["anna"] != urls["carl"] and client.get(urls["anna"]).content != client.get(urls["carl"]).content


def test_pressing_play_sample_again_while_it_waits_finds_the_job_already_queued(client):
    make_reading(client, [part("One", 1, 1, {0: SLOW})])  # the worker is busy for two seconds, so the sample waits
    first = sample(client, expect=202, voice="bea")
    second = sample(client, expect=202, voice="bea")
    assert first["job"]["id"] == second["job"]["id"]
    assert len(client.app.state.jobs.speech.store.hidden_jobs(["queued", "running"])) == 1


def test_a_sample_goes_ahead_of_waiting_readings_but_never_ahead_of_the_part_being_made(client):
    """Criterion 263: like a Draft. The reading that is running is not interrupted; the one waiting is passed."""
    running = make_reading(client, [part("One", 1, 1, {0: SLOW})], title="running")
    wait_until(client, running["id"], lambda r: r["status"] == "running", "running")
    waiting = make_reading(client, [part("Two", 5, 1)], title="waiting")
    job = sample(client, expect=202, voice="anna")["job"]
    queued = client.app.state.jobs.db.queued_ids()
    assert queued[0] == job["id"] and waiting["id"] in queued[1:] and client.get(f"/api/runs/{running['id']}").json()["status"] == "running"
    assert wait_job(client, job["id"])["status"] == "done"
    assert wait_for(client, waiting["id"])["status"] == "done"


def test_a_sample_is_not_history_no_list_no_count_and_no_run_event(client):
    jobs = client.app.state.jobs
    events: list[str] = []
    original = jobs.bus.publish
    jobs.bus.publish = lambda kind, payload=None: (events.append(kind), original(kind, payload))[1]  # type: ignore[method-assign]
    job = sample(client, expect=202, voice="anna")["job"]
    wait_job(client, job["id"])
    assert client.get("/api/runs").json()["runs"] == [] and client.get("/api/runs", params={"kind": "speech"}).json()["runs"] == []
    assert client.get("/api/runs/counts").json()["speech"]["all"] == 0
    assert "speech.job" in events and "run.created" not in events and "run.updated" not in events  # (the page hears of it as a speech.job)


def test_the_follow_up_event_of_a_sample_carries_its_files(client):
    jobs = client.app.state.jobs
    seen: list[dict] = []
    original = jobs.bus.publish
    jobs.bus.publish = lambda kind, payload=None: (seen.append(payload) if kind == "speech.job" else None, original(kind, payload))[1]  # type: ignore[method-assign]
    job = wait_job(client, sample(client, expect=202, voice="anna")["job"]["id"])
    assert seen and seen[-1]["id"] == job["id"] and seen[-1]["status"] == "done" and seen[-1]["files"] == job["files"]


@pytest.mark.parametrize("body,status,code,field", [
    ({"engine": "nope", "voice": "anna"}, 422, "unknown_engine", "engine"),
    ({"voice": "nobody"}, 422, "unknown_voice", "voice"),
    ({"voice": ""}, 422, "one_voice", "voice"),
    ({"voice": "anna", "saved_voice": "a" * 32}, 422, "one_voice", "voice"),
    ({"saved_voice": "a" * 32}, 422, "unknown_voice", "voice"),
    ({"recording": "b" * 32}, 404, "unknown_recording", "recording"),
    ({"engine": "fake-basic", "saved_voice": "a" * 32}, 422, "cannot_clone", "voice"),
])
def test_a_request_for_a_sample_that_cannot_be_made_says_why(client, body, status, code, field):
    wait_for_engines(client)
    response = client.post("/api/speech/samples", json={"engine": "fake", **body})
    assert response.status_code == status and response.json()["code"] == code and response.json()["field"] == field


def test_a_sample_file_is_found_only_inside_the_samples_folder(client):
    wait_job(client, sample(client, expect=202, voice="anna")["job"]["id"])
    assert client.get("/api/speech/samples/fake/anna.wav").status_code == 200
    for path in ("fake/missing.wav", "nogroup/anna.wav", ".hidden/anna.wav", "fake/.anna.wav", "fake/a%2Fb.wav", "fa%2Fke/anna.wav"):
        assert client.get(f"/api/speech/samples/{path}").status_code == 404, path


# ---------------------------------------------------------------- designing a voice
def test_a_design_makes_three_different_clips_of_the_passage(client):
    started = design(client)
    assert started["task"] == "design" and started["status"] in ("queued", "running") and started["description"].startswith("a warm Irish woman")
    job = wait_job(client, started["id"])
    assert job["status"] == "done" and [f["index"] for f in job["files"]] == [0, 1, 2]
    clips = [client.get(f["url"]).content for f in job["files"]]
    assert all(wav_info(c)[:3] == (1, 2, 24000) for c in clips) and len(set(clips)) == 3  # three seeds, three different tries (criterion 265)
    assert wav_info(clips[0])[3] / 24000 > 3  # the whole passage, not a word of it


def test_a_design_needs_a_description_that_is_neither_empty_nor_enormous(client):
    wait_for_engines(client)
    for description, code in (("", "description_empty"), ("   ", "description_empty"), ("x" * 301, "description_too_long")):
        response = client.post("/api/speech/voices/design", json={"description": description})
        assert response.status_code == 422 and response.json()["code"] == code and response.json()["field"] == "description", description


def test_an_engine_that_cannot_design_is_refused_and_so_is_a_server_with_none(client_factory):
    client = client_factory()
    wait_for_engines(client)
    assert client.post("/api/speech/voices/design", json={"description": "a voice", "engine": "fake-basic"}).json()["code"] == "cannot_design"
    only_basic = client_factory(speech_engines=("fake-basic",))
    wait_for_engines(only_basic)
    refused = only_basic.post("/api/speech/voices/design", json={"description": "a voice"})
    assert refused.status_code == 409 and refused.json()["code"] == "no_design_engine" and refused.json()["hint"]


def test_a_chosen_clip_is_saved_as_a_voice_with_its_description_and_the_clip(client):
    job = wait_job(client, design(client)["id"])
    voice = save(client, kind="designed", name="  Warm   narrator ", candidate={"job": job["id"], "index": 2})
    assert (voice["name"], voice["kind"], voice["engine"], voice["consent_at"]) == ("Warm narrator", "designed", "fake", None)
    assert voice["description"].startswith("a warm Irish woman") and voice["seconds"] > 3 and voice["sample"] == {"ready": False, "url": None}
    storage = client.app.state.jobs.storage
    stored = (storage.speech / "voices" / voice["id"] / "reference.wav").read_bytes()
    assert stored == client.get(job["files"][2]["url"]).content  # exactly the clip that was chosen
    row = client.app.state.jobs.speech.store.voice(voice["id"])
    assert row["ref_text"] == PASSAGE and row["ref_path"] == f"speech/voices/{voice['id']}/reference.wav"  # the words said in it travel with it
    assert [v["id"] for v in client.get("/api/speech/engines/fake/voices").json()["saved"]] == [voice["id"]]


def test_two_clips_of_one_design_can_both_be_kept_as_voices(client):
    job = wait_job(client, design(client)["id"])
    one = save(client, kind="designed", name="First", candidate={"job": job["id"], "index": 0})
    two = save(client, kind="designed", name="Second", candidate={"job": job["id"], "index": 1})
    assert one["id"] != two["id"] and len(client.get("/api/speech/engines/fake/voices").json()["saved"]) == 2


@pytest.mark.parametrize("body,status,code,field", [
    ({"candidate": {"job": "f" * 32, "index": 0}}, 404, "unknown_design", "candidate"),
    ({"candidate": {"job": "x", "index": 0}}, 404, "unknown_design", "candidate"),
    ({"name": ""}, 422, "name_empty", "name"),
    ({"name": "n" * 61}, 422, "name_too_long", "name"),
    ({"recording": "a" * 32}, 422, "one_source", "candidate"),
    ({"candidate": None}, 422, "one_source", "candidate"),
])
def test_a_designed_voice_that_cannot_be_saved_says_why(client, body, status, code, field):
    job = wait_job(client, design(client)["id"])
    request = {"kind": "designed", "name": "A name", "candidate": {"job": job["id"], "index": 0}, **body}
    request = {k: v for k, v in request.items() if v is not None}
    response = client.post("/api/speech/voices", json=request)
    assert response.status_code == status and response.json()["code"] == code and response.json()["field"] == field


def test_a_clip_that_is_not_made_yet_cannot_be_saved(client):
    make_reading(client, [part("One", 1, 1, {0: SLOW})])  # holds the worker, so the design waits
    started = design(client)
    refused = client.post("/api/speech/voices", json={"kind": "designed", "name": "Too soon", "candidate": {"job": started["id"], "index": 0}})
    assert refused.status_code == 404 and refused.json()["code"] == "unknown_candidate"
    wait_job(client, started["id"])


def test_a_picture_run_or_a_reading_is_not_a_design(client):
    reading = make_reading(client)
    refused = client.post("/api/speech/voices", json={"kind": "designed", "name": "Not a design", "candidate": {"job": reading["id"], "index": 0}})
    assert refused.status_code == 404 and refused.json()["code"] == "unknown_design"
    assert client.get(f"/api/speech/jobs/{reading['id']}").status_code == 404  # and a reading is not one of the short jobs either


# ---------------------------------------------------------------- a recording to clone
def test_a_good_recording_is_checked_and_kept_as_the_studios_own_wav_only(client):
    staged = upload(client, wav(silence(1), tone(10), silence(1)))
    assert staged["seconds"] == pytest.approx(10.24, abs=0.1) and 0 < staged["peak"] < 1 and staged["clipped"] == 0 and staged["url"].endswith(".wav")
    assert wav_info(client.get(staged["url"]).content)[:3] == (1, 2, 24000)
    folder = client.app.state.jobs.storage.speech / "voices" / "_staged"
    assert [p.name for p in folder.iterdir()] == [f"{staged['id']}.wav"]  # one file: the upload itself was not kept


@pytest.mark.parametrize("data,status,code", [
    (b"", 422, "empty"), (wav(silence(10)), 422, "silent"), (wav(tone(4)), 422, "too_short"), (wav(tone(70)), 422, "too_long"),
    (b"not audio, and not a wav either", 415, "unreadable"),  # (not any format the page names: refused without ffmpeg being asked)
    (b"OggS" + bytes(300), 415, "no_ffmpeg"),  # (looks like one, and there is no ffmpeg here to say it is not)
])
def test_a_recording_that_cannot_be_used_is_refused_naming_which_check_failed(client_factory, data, status, code):
    client = client_factory(speech_ffmpeg="/no/such/ffmpeg")
    response = None
    wait_for_engines(client)
    response = client.post("/api/speech/recordings", content=data, headers={"Content-Type": "application/octet-stream"})
    body = response.json()
    assert response.status_code == status and body["code"] == code and body["field"] == "recording" and body["detail"]
    assert not list((client.app.state.jobs.storage.speech / "voices" / "_staged").glob("*.wav"))  # nothing was kept


def test_a_recording_larger_than_the_limit_is_refused_while_it_streams_in(client_factory):
    client = client_factory(speech_recording_max_mb=1)
    wait_for_engines(client)
    refused = client.post("/api/speech/recordings", content=bytes(2 * 1024 * 1024), headers={"Content-Type": "application/octet-stream"})
    assert refused.status_code == 413 and refused.json()["code"] == "too_large"


def test_a_recording_can_be_previewed_in_the_new_voice_before_it_is_saved(client):
    staged = upload(client, wav(tone(10)))
    job = wait_job(client, sample(client, expect=202, recording=staged["id"])["job"]["id"])
    assert job["status"] == "done" and job["files"][0]["url"] == f"/api/speech/samples/staged/{staged['id']}.wav"
    assert client.get(job["files"][0]["url"]).status_code == 200


def test_a_recording_is_saved_as_a_voice_only_with_the_statement_ticked_and_the_statement_is_recorded(client):
    staged = upload(client, wav(tone(10)))
    unticked = client.post("/api/speech/voices", json={"kind": "cloned", "name": "Mine", "recording": staged["id"], "engine": "fake"})
    assert unticked.status_code == 422 and unticked.json()["code"] == "consent_required" and unticked.json()["field"] == "consent"
    assert client.get(staged["url"]).status_code == 200  # refused, and the recording is still there to try again
    voice = save(client, kind="cloned", name="Mine", recording=staged["id"], engine="fake", consent=True)
    assert voice["kind"] == "cloned" and voice["consent_at"].endswith("Z") and voice["seconds"] == pytest.approx(10, abs=0.1) and voice["description"] is None
    row = client.app.state.jobs.speech.store.voice(voice["id"])
    assert row["consent_text"] == CONSENT_STATEMENT and row["ref_text"] == PASSAGE  # exactly the words shown, and the words said
    assert client.get(staged["url"]).status_code == 404  # moved into the voice: nothing is left staged
    assert (client.app.state.jobs.storage.speech / "voices" / voice["id"] / "reference.wav").is_file()


def test_a_refusal_to_save_does_not_throw_the_recording_away(client):
    saved_cloned_voice(client, "Taken")
    staged = upload(client, wav(tone(10)))
    refused = client.post("/api/speech/voices", json={"kind": "cloned", "name": "TAKEN", "recording": staged["id"], "engine": "fake", "consent": True})
    assert refused.status_code == 409 and refused.json()["code"] == "name_taken" and refused.json()["field"] == "name"
    assert client.get(staged["url"]).status_code == 200  # (the person gives it another name; they do not record the passage again)
    assert save(client, kind="cloned", name="Another", recording=staged["id"], engine="fake", consent=True)["name"] == "Another"


@pytest.mark.parametrize("body,status,code", [
    ({"engine": "fake-basic"}, 422, "cannot_clone"), ({"engine": "nope"}, 422, "unknown_engine"), ({"recording": "c" * 32}, 404, "unknown_recording"),
    ({"recording": None}, 422, "one_source"), ({"engine": None}, 422, "one_source"), ({"name": "  "}, 422, "name_empty"),
])
def test_a_recording_that_cannot_be_saved_says_why(client, body, status, code):
    staged = upload(client, wav(tone(10)))
    request = {"kind": "cloned", "name": "Mine", "recording": staged["id"], "engine": "fake", "consent": True, **body}
    response = client.post("/api/speech/voices", json={k: v for k, v in request.items() if v is not None})
    assert response.status_code == status and response.json()["code"] == code


# ---------------------------------------------------------------- the voices you made
def test_a_voice_can_be_renamed_to_another_name_but_not_to_one_you_already_have(client):
    one, two = saved_designed_voice(client, "One"), saved_cloned_voice(client, "Two")
    assert client.patch(f"/api/speech/voices/{one['id']}", json={"name": "  First  voice "}).json()["name"] == "First voice"
    assert client.patch(f"/api/speech/voices/{one['id']}", json={"name": "FIRST VOICE"}).status_code == 200  # its own name in other letters
    taken = client.patch(f"/api/speech/voices/{one['id']}", json={"name": "two"})
    assert taken.status_code == 409 and taken.json()["code"] == "name_taken"
    assert client.patch(f"/api/speech/voices/{two['id']}", json={"name": ""}).json()["code"] == "name_empty"
    for voice_id in ("f" * 32, "short", "G" * 32):
        assert client.patch(f"/api/speech/voices/{voice_id}", json={"name": "x"}).status_code == 404


def test_a_voices_name_is_compared_with_accents_and_case_folded_away(client):
    saved_cloned_voice(client, "Zoë")
    taken = client.post("/api/speech/voices", json={"kind": "cloned", "name": "ZOË", "recording": upload(client, wav(tone(10)))["id"], "engine": "fake", "consent": True})
    assert taken.status_code == 409


def test_deleting_a_voice_removes_it_its_files_and_its_sample(client):
    voice = saved_designed_voice(client)
    wait_job(client, sample(client, expect=202, saved_voice=voice["id"])["job"]["id"])
    storage = client.app.state.jobs.storage
    assert (storage.speech / "samples" / "voice" / f"{voice['id']}.wav").is_file()
    assert client.delete(f"/api/speech/voices/{voice['id']}").status_code == 204
    assert not (storage.speech / "voices" / voice["id"]).exists() and not (storage.speech / "samples" / "voice" / f"{voice['id']}.wav").exists()
    assert client.get("/api/speech/engines/fake/voices").json()["saved"] == [] and client.delete(f"/api/speech/voices/{voice['id']}").status_code == 404


def test_a_voice_in_use_by_a_reading_that_is_waiting_or_running_cannot_be_deleted(client):
    voice = saved_designed_voice(client)
    reading = make_reading(client, [part("One", 1, 1, {0: SLOW})], voice="", saved_voice=voice["id"])
    refused = client.delete(f"/api/speech/voices/{voice['id']}")
    assert refused.status_code == 409 and refused.json()["code"] == "voice_in_use" and "1 reading is" in refused.json()["detail"] and refused.json()["hint"]
    client.post(f"/api/runs/{reading['id']}/cancel")
    wait_for(client, reading["id"])
    assert client.delete(f"/api/speech/voices/{voice['id']}").status_code == 204


def test_a_reading_keeps_its_audio_and_the_voices_name_when_the_voice_is_deleted_but_cannot_be_continued(client):
    voice = saved_designed_voice(client, "Gone soon")
    reading = make_reading(client, [part("One", 1, 3, {2: SLOW})], voice="", saved_voice=voice["id"])
    wait_until(client, reading["id"], lambda r: r["reading"]["pieces_done"] >= 2, "two pieces made")
    client.post(f"/api/runs/{reading['id']}/cancel")
    canceled = wait_for(client, reading["id"])
    assert client.delete(f"/api/speech/voices/{voice['id']}").status_code == 204
    after = client.get(f"/api/runs/{reading['id']}").json()
    assert after["reading"]["voice"]["label"] == "Gone soon" and after["reading"]["pieces_done"] == canceled["reading"]["pieces_done"] == 2  # as it was made
    client.post(f"/api/readings/{reading['id']}/continue")
    failed = wait_for(client, reading["id"])
    assert failed["status"] == "failed" and "Gone soon that this reading uses has been deleted" in failed["error"]["message"]


def test_a_reading_is_made_in_a_designed_voice_and_in_a_cloned_one(client):
    designed, cloned = saved_designed_voice(client, "Designed"), saved_cloned_voice(client, "Cloned")
    runs = {}
    for voice in (designed, cloned):
        run = wait_for(client, make_reading(client, [part("One", 1, 2)], voice="", saved_voice=voice["id"], title=voice["name"])["id"])
        assert run["status"] == "done" and run["reading"]["variant"] == "clone", run
        assert run["reading"]["voice"] == {"kind": "saved", "id": voice["id"], "label": voice["name"], "saved_kind": voice["kind"]}
        runs[voice["name"]] = client.get(f"/api/speech/{run['id']}/parts/0/pieces/0.wav").content
    assert runs["Designed"] != runs["Cloned"]  # different voices, different audio (the fake engine keys its tone on the reference clip's words and kind)


def test_a_saved_voice_takes_no_style_note_and_belongs_to_one_engine(client):
    voice = saved_designed_voice(client)
    wait_for_engines(client)
    style = client.post("/api/readings", json=reading_body(voice="", saved_voice=voice["id"], style="warm"))
    assert style.status_code == 422 and style.json()["code"] == "no_style"
    other = client.post("/api/readings", json=reading_body(engine="fake-basic", voice="", saved_voice=voice["id"]))
    assert other.status_code == 422 and other.json()["code"] in ("voice_engine", "unknown_voice")


def test_a_recording_that_is_gone_when_its_preview_comes_to_be_made_fails_with_words(client):
    make_reading(client, [part("One", 1, 1, {0: SLOW})])  # the worker is busy, so the preview waits
    staged = upload(client, wav(tone(10)))
    job = sample(client, expect=202, recording=staged["id"])["job"]
    (client.app.state.jobs.storage.speech / "voices" / "_staged" / f"{staged['id']}.wav").unlink()
    failed = wait_job(client, job["id"])
    assert failed["status"] == "failed" and "no longer here" in failed["error"]["message"] and failed["files"] == []


# ---------------------------------------------------------------- what is never saved is cleared away
def test_what_was_never_saved_is_swept_after_the_hours_set_and_not_before(client):
    voices = client.app.state.jobs.speech.voices
    staged = upload(client, wav(tone(10)))
    job = wait_job(client, design(client)["id"])
    wait_job(client, sample(client, expect=202, voice="anna")["job"]["id"])
    assert voices.sweep() == {"recordings": 0, "candidates": 0, "jobs": 0}  # fresh: nothing is old enough
    removed = voices.sweep(now=time.time() + 25 * 3600)
    assert removed == {"recordings": 1, "candidates": 1, "jobs": 2}
    assert client.get(staged["url"]).status_code == 404 and client.get(job["files"][0]["url"]).status_code == 404
    assert client.get(f"/api/speech/jobs/{job['id']}").status_code == 404
    assert client.get("/api/speech/samples/fake/anna.wav").status_code == 200  # a sample is kept: it is the point of pressing the button once


def test_a_saved_voice_is_not_swept(client):
    voice = saved_cloned_voice(client)
    client.app.state.jobs.speech.voices.sweep(now=time.time() + 1000 * 3600)
    assert (client.app.state.jobs.storage.speech / "voices" / voice["id"] / "reference.wav").is_file()
    assert client.get("/api/speech/engines/fake/voices").json()["saved"][0]["id"] == voice["id"]


def test_the_new_routes_all_need_the_client_header(client):
    wait_for_engines(client)
    voice = saved_cloned_voice(client)
    for method, url, body in [
        ("post", "/api/speech/samples", {"engine": "fake", "voice": "anna"}), ("post", "/api/speech/voices/design", {"description": "a voice"}),
        ("post", "/api/speech/recordings", None), ("post", "/api/speech/voices", {}), ("patch", f"/api/speech/voices/{voice['id']}", {"name": "x"}),
        ("delete", f"/api/speech/voices/{voice['id']}", None),
    ]:
        kwargs: dict[str, Any] = {"json": body} if body is not None else {}
        refused = getattr(client, method)(url, headers={"X-Studio-Client": ""}, **kwargs)
        assert refused.status_code == 403 and refused.json()["code"] == "missing_client_header", (method, url)
    assert client.app.state.jobs.speech.store.voice(voice["id"]) is not None


def test_a_full_queue_refuses_a_sample_and_says_so(client_factory):
    client = client_factory(queue_cap=1)
    busy = make_reading(client, [part("Busy", 1, 1, {0: SLOW})])
    wait_until(client, busy["id"], lambda r: r["status"] == "running", "running")
    sample(client, expect=202, voice="anna")  # the one place
    full = sample(client, expect=429, voice="bea")
    assert full["code"] == "queue_full"
