"""Music through the API (DESIGN.md §26): the request and its limits, instrumental by default, tracks and the audio
route, progress by stage, cancel, failures, housekeeping, and what stays the same for pictures. All against the fake
music pipeline, which proves the plumbing and not the music."""

from __future__ import annotations

import io
import json
import re
import time
import wave
from pathlib import Path

import pytest
from conftest import close, create_run, wait_for, wait_for_worker_state

from studio import wavfile
from studio.pipelines.fake_music import FRAMES, MAX_SECONDS, RENDER_STEPS, SAMPLE_RATE

RUNNING = frozenset({"running"})


def create_music(client, description: str = "Genre: ambient. A slow piano.", lyrics=None, expect: int = 201, **options):
    body = {"mode": "music", "prompt": description, "options": {"duration": 30, **options}}
    if lyrics is not None:
        body["lyrics"] = lyrics
    response = client.post("/api/runs", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def run_and_wait(client, *args, **kwargs):
    return wait_for(client, create_music(client, *args, **kwargs)["id"])


def audio_bytes(client, track: dict) -> bytes:
    response = client.get(track["url"])
    assert response.status_code == 200, response.text
    return response.content


def frames_of(data: bytes) -> bytes:
    """Just the samples of a WAV (the information chunk has a date in it)."""
    with wave.open(io.BytesIO(data), "rb") as reader:
        return reader.readframes(reader.getnframes())


def problems(response) -> dict[str, str]:
    assert response.status_code == 422, response.text
    return {".".join(str(p) for p in item["loc"] if p != "body"): item["msg"] for item in response.json()["detail"]}


def subscribe(client):
    return client.portal.call(lambda: client.app.state.bus.subscribe())


def drain(client, sub) -> list[tuple[str, dict]]:
    async def take():
        items = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait())
        return items

    return client.portal.call(take)


# ------------------------------------------------------------------ capabilities
def test_the_music_tab_is_offered_and_its_limits_are_published(client):
    caps = client.get("/api/capabilities").json()
    deadline = time.monotonic() + 15
    while "music" not in caps["modes"]:  # the start-up check of the music pipeline runs in the background
        assert time.monotonic() < deadline, caps
        time.sleep(0.05)
        caps = client.get("/api/capabilities").json()
    assert caps["modes"] == ["generate", "edit", "music"]
    assert caps["music"] == {"available": True, "state": "done", "reason": None, "hint": None, "model": "fake-music"}
    assert caps["limits"]["music"] == {
        "duration": {"min": 10, "max": 300, "default": 60}, "tracks": {"min": 1, "max": 4},
        "steps": {"min": 10, "max": 60, "default": 30}, "description_chars": 2000, "lyrics_chars": 6000, "field_chars": 400}


def test_limits_follow_the_settings(client_factory):
    client = client_factory(music_max_seconds=120, music_max_tracks=2)
    limits = client.get("/api/capabilities").json()["limits"]["music"]
    assert limits["duration"]["max"] == 120 and limits["tracks"]["max"] == 2


def test_music_is_not_offered_where_the_music_pipeline_cannot_run(client_factory):
    """With the real pipelines and no PyTorch (as in this test environment) the check fails, and the page is told why."""
    client = client_factory(pipeline="real")
    deadline = time.monotonic() + 15
    while True:
        caps = client.get("/api/capabilities").json()
        if caps["music"]["state"] == "failed":
            break
        assert time.monotonic() < deadline, caps
        time.sleep(0.05)
    assert "music" not in caps["modes"] and caps["music"]["available"] is False
    assert "PyTorch is not installed" in caps["music"]["reason"] and caps["music"]["hint"]
    assert caps["music"]["model"] == "MiniMaxAI/MiniMax-Music3"


# ------------------------------------------------------------------ a music run
def test_a_music_run_makes_a_track_you_can_play(client):
    created = create_music(client, steps=20, seed=5, tracks=1)
    assert created["mode"] == "music" and created["status"] in ("queued", "running", "done")
    run = wait_for(client, created["id"])
    assert run["status"] == "done" and run["images"] == [] and run["inputs"] == [] and run["lyrics"] is None
    assert run["prompt"] == "Genre: ambient. A slow piano." and run["model_id"] == "fake-music"
    assert run["options"] == {"duration": 30, "steps": 20, "seed": 5, "seed_was_random": False, "tracks": 1,
                              "instrumental": True, "fields": {}}
    [track] = run["tracks"]
    assert track["seed"] == 5 and track["idx"] == 0 and track["sample_rate"] == SAMPLE_RATE and track["channels"] == 2
    assert track["seconds"] == MAX_SECONDS and track["url"] == f"/api/audio/{track['id']}"
    assert track["download_url"] == track["url"] + "?download=1"
    response = client.get(track["url"])
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    assert len(response.content) == track["bytes"] and "immutable" in response.headers["cache-control"]
    assert response.headers["accept-ranges"] == "bytes"
    path = Path(client.app.state.storage.root) / "audio" / run["id"] / "0.wav"
    assert path.is_file() and wavfile.read_wav(path).seconds == pytest.approx(track["seconds"], abs=0.001)


def test_no_lyrics_means_the_instrumental_tag_and_lyrics_are_sent_as_written(client):
    jobs, db = client.app.state.jobs, client.app.state.db
    for lyrics, expected, instrumental in ((None, "[Instrumental]", True), ("", "[Instrumental]", True),
                                           ("  \n ", "[Instrumental]", True),
                                           ("  [Verse]\nla la\n[Chorus]\nsing  ", "[Verse]\nla la\n[Chorus]\nsing", False)):
        run = run_and_wait(client, lyrics=lyrics)
        assert run["status"] == "done"
        job = jobs._job_for(db.get_run(run["id"]))
        assert job["lyrics"] == expected and job["prompt"] == "Genre: ambient. A slow piano."
        assert run["options"]["instrumental"] is instrumental and run["lyrics"] == (None if instrumental else expected)


def test_the_job_a_worker_gets_has_everything_it_needs(client):
    run = run_and_wait(client, steps=25, duration=45, seed=11, tracks=2)
    job = client.app.state.jobs._job_for(client.app.state.db.get_run(run["id"]))
    assert job == {"run_id": run["id"], "mode": "music", "prompt": "Genre: ambient. A slow piano.", "lyrics": "[Instrumental]",
                   "duration": 45, "steps": 25, "seeds": [11, 12], "model_id": "fake-music"}


def test_versions_have_their_own_seeds_and_sound_different(client):
    run = run_and_wait(client, seed=100, tracks=3)
    assert run["options"]["tracks"] == 3  # what was asked for is what Reuse, Retry and the card's "3 versions" read back
    assert [t["seed"] for t in run["tracks"]] == [100, 101, 102] and [t["idx"] for t in run["tracks"]] == [0, 1, 2]
    sounds = {frames_of(audio_bytes(client, t)) for t in run["tracks"]}
    assert len(sounds) == 3


def test_the_same_request_gives_the_same_track_and_another_seed_another(client):
    a = run_and_wait(client, seed=9)["tracks"][0]
    b = run_and_wait(client, seed=9)["tracks"][0]
    c = run_and_wait(client, seed=10)["tracks"][0]
    assert frames_of(audio_bytes(client, a)) == frames_of(audio_bytes(client, b)) != frames_of(audio_bytes(client, c))


def test_a_random_seed_is_chosen_and_recorded(client):
    run = run_and_wait(client)
    assert run["options"]["seed_was_random"] is True and run["tracks"][0]["seed"] == run["options"]["seed"]


def test_the_page_fields_are_kept_for_reuse_and_nothing_else_is_done_with_them(client):
    fields = {"genre": "acoustic pop", "bpm": "96", "key": "C major", "mood": "warm"}
    run = run_and_wait(client, fields=fields)
    assert run["options"]["fields"] == fields
    assert client.get(f"/api/runs/{run['id']}").json()["options"]["fields"] == fields


def test_the_wav_says_it_is_machine_generated(client):
    run = run_and_wait(client, "Genre: ambient. A slow piano.", seed=3)
    path = Path(client.app.state.storage.root) / "audio" / run["id"] / "0.wav"
    note = wavfile.read_wav(path).info["comment"]
    assert "machine-generated" in note and "fake-music" in note and "Seed 3" in note and "A slow piano." in note


# ------------------------------------------------------------------ the audio route
def test_a_player_can_seek_because_ranges_are_answered(client):
    track = run_and_wait(client)["tracks"][0]
    whole = audio_bytes(client, track)
    part = client.get(track["url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == whole[:100]
    assert part.headers["content-range"] == f"bytes 0-99/{len(whole)}"
    tail = client.get(track["url"], headers={"Range": "bytes=-50"})
    assert tail.status_code == 206 and tail.content == whole[-50:]
    middle = client.get(track["url"], headers={"Range": f"bytes=1000-{len(whole) + 500}"})  # an end past the file is fine
    assert middle.status_code == 206 and middle.content == whole[1000:]
    assert client.get(track["url"], headers={"Range": f"bytes={len(whole) + 5}-"}).status_code == 416


def test_a_download_has_a_meaningful_filename(client):
    run = run_and_wait(client, "Genre: ambient. A slow piano.", seed=7, fields={"genre": "Ambient / Chill"})
    track = run["tracks"][0]
    plain = client.get(track["url"])
    assert "content-disposition" not in plain.headers  # the player plays it inline
    named = client.get(track["download_url"])
    assert named.status_code == 200 and named.content == plain.content
    match = re.search(r'attachment; filename="(music_ambient-chill_6s_s7_\d{8}-\d{6}\.wav)"', named.headers["content-disposition"])
    assert match, named.headers["content-disposition"]


def test_without_a_genre_the_name_comes_from_the_description(client):
    track = run_and_wait(client, "A slow lullaby for a rainy evening", seed=2)["tracks"][0]
    name = client.get(track["download_url"]).headers["content-disposition"]
    assert re.search(r'filename="music_slow-lullaby-rainy-evening_6s_s2_\d{8}-\d{6}\.wav"', name)


@pytest.mark.parametrize("track_id", ["0" * 32, "not-an-id", "A" * 32, "0" * 31 + "g", "0" * 33])
def test_an_unknown_or_malformed_track_is_a_404(client, track_id):
    for suffix in ("", "?download=1"):
        response = client.get(f"/api/audio/{track_id}{suffix}")
        assert response.status_code == 404 and response.json()["code"] == "not_found"


def test_a_track_whose_file_is_gone_is_a_404_not_an_error(client):
    run = run_and_wait(client)
    (Path(client.app.state.storage.root) / "audio" / run["id"] / "0.wav").unlink()
    assert client.get(run["tracks"][0]["url"]).status_code == 404


def test_audio_is_allowed_by_the_page_security_policy(client):
    policy = client.get("/api/health").headers["content-security-policy"]
    assert "media-src 'self'" in policy


# ------------------------------------------------------------------ what a request may say
def test_the_description_is_required_and_limited(client):
    assert "prompt" in problems(client.post("/api/runs", json={"mode": "music", "prompt": "   "}))
    long = problems(client.post("/api/runs", json={"mode": "music", "prompt": "x" * 2001}))
    assert "2000" in long["prompt"]
    assert client.post("/api/runs", json={"mode": "music", "prompt": "x" * 2000}).status_code == 201


def test_the_lyrics_are_limited(client):
    assert "6000" in problems(client.post("/api/runs", json={"mode": "music", "prompt": "p", "lyrics": "x" * 6001}))["lyrics"]
    assert client.post("/api/runs", json={"mode": "music", "prompt": "p", "lyrics": "x" * 6000}).status_code == 201


@pytest.mark.parametrize("options,field,fragment", [
    ({"duration": 9}, "options.duration", "between 10 and 300"),
    ({"duration": 301}, "options.duration", "between 10 and 300"),
    ({"duration": 0}, "options.duration", "between 10 and 300"),
    ({"tracks": 0}, "options.tracks", "between 1 and 4"),
    ({"tracks": 5}, "options.tracks", "between 1 and 4"),
    ({"steps": 9}, "options.steps", "between 10 and 60"),
    ({"steps": 61}, "options.steps", "between 10 and 60"),
    ({"seed": -1}, "options.seed", "between 0"),
    ({"seed": 2 ** 32}, "options.seed", "between 0"),
])
def test_numbers_outside_their_limits_are_refused_naming_the_field(client, options, field, fragment):
    found = problems(client.post("/api/runs", json={"mode": "music", "prompt": "p", "options": options}))
    assert fragment in found[field]


def test_the_limits_of_the_range_themselves_are_accepted(client):
    for options in ({"duration": 10}, {"duration": 300}, {"tracks": 4}, {"steps": 10}, {"steps": 60}, {"seed": 0}):
        assert client.post("/api/runs", json={"mode": "music", "prompt": "p", "options": options}).status_code == 201, options


def test_the_servers_own_maximum_applies(client_factory):
    client = client_factory(music_max_seconds=90, music_max_tracks=2)
    assert "between 10 and 90" in problems(client.post("/api/runs", json={"mode": "music", "prompt": "p", "options": {"duration": 91}}))["options.duration"]
    assert "between 1 and 2" in problems(client.post("/api/runs", json={"mode": "music", "prompt": "p", "options": {"tracks": 3}}))["options.tracks"]


def test_steps_default_to_thirty_when_not_given_whatever_pictures_default_to(client):
    assert run_and_wait(client)["options"]["steps"] == 30
    assert run_and_wait(client, steps=12)["options"]["steps"] == 12
    assert wait_for(client, create_run(client)["id"])["options"]["steps"] == 3  # a picture run is unchanged


@pytest.mark.parametrize("name,value", [
    ("width", 512), ("height", 512), ("num_images", 2), ("negative_prompt", "x"), ("cfg_scale", 4.0), ("transparent", True),
    ("resolution", 1024), ("shape_from", 1), ("draft", True), ("full", {"width": 2048, "height": 2048, "steps": 40}),
])
def test_options_that_are_for_pictures_are_refused_for_music(client, name, value):
    found = problems(client.post("/api/runs", json={"mode": "music", "prompt": "p", "options": {name: value}}))
    assert found[f"options.{name}"] == "Not used for music."


def test_pictures_are_refused_for_music_and_music_options_for_pictures(client):
    ref = {"upload_id": "a" * 32}
    assert "Images are only used" in problems(client.post("/api/runs", json={"mode": "music", "prompt": "p", "input_images": [ref]}))["input_images"]
    for body, field in (({"options": {"duration": 30}}, "options.duration"), ({"options": {"tracks": 2}}, "options.tracks"),
                        ({"options": {"fields": {"genre": "x"}}}, "options.fields"), ({"lyrics": "la"}, "lyrics")):
        found = problems(client.post("/api/runs", json={"mode": "generate", "prompt": "a cat", **body}))
        assert "only used for music" in found[field].lower(), field


@pytest.mark.parametrize("fields,fragment", [
    ({"Genre": "x"}, "lowercase"), ({"1st": "x"}, "lowercase"), ({"a b": "x"}, "lowercase"), ({"": "x"}, "lowercase"),
    ({"g" * 41: "x"}, "lowercase"), ({"genre": "x" * 401}, "400"), ({f"f{i}": "x" for i in range(17)}, "At most 16"),
])
def test_the_pages_fields_are_checked_before_they_are_stored(client, fields, fragment):
    found = problems(client.post("/api/runs", json={"mode": "music", "prompt": "p", "options": {"fields": fields}}))
    assert any(fragment in message for message in found.values()), found


def test_the_page_must_send_the_right_types(client):
    for options in ({"duration": "30"}, {"duration": 30.5}, {"tracks": "2"}, {"steps": 12.5}, {"fields": ["genre"]}, {"fields": {"genre": 5}}):
        assert client.post("/api/runs", json={"mode": "music", "prompt": "p", "options": options}).status_code == 422, options
    assert client.post("/api/runs", json={"mode": "music", "prompt": "p", "lyrics": 5}).status_code == 422
    assert client.post("/api/runs", json={"mode": "opera", "prompt": "p"}).status_code == 422


# ------------------------------------------------------------------ progress, cancel and failures
def test_progress_is_reported_by_stage(client):
    sub = subscribe(client)
    run = run_and_wait(client, tracks=2)
    events = [data for name, data in drain(client, sub) if name == "run.progress" and data["id"] == run["id"]]
    stages = [(e["progress"]["image"], e["progress"]["stage"]) for e in events]
    assert {s for _, s in stages} == {"compose", "render", "finish"}
    for track in (1, 2):
        order = [stage for t, stage in stages if t == track]
        assert order[0] == "compose" and order[-1] == "finish" and order == sorted(order, key=["compose", "render", "finish"].index)
    last_compose = [e["progress"] for e in events if e["progress"].get("stage") == "compose"][-1]
    assert last_compose["of"] == 2 and last_compose["step"] == last_compose["steps"] == FRAMES
    assert max(e["progress"]["steps"] for e in events if e["progress"]["stage"] == "render") == RENDER_STEPS


def test_a_picture_runs_progress_is_unchanged(client):
    sub = subscribe(client)
    run = wait_for(client, create_run(client)["id"])
    progress = [d["progress"] for n, d in drain(client, sub) if n == "run.progress" and d["id"] == run["id"]]
    assert progress and all("stage" not in p for p in progress)


def test_a_queued_music_run_is_canceled_at_once(client_factory):
    client = client_factory(fake_step_delay_ms=40)
    first = create_music(client, tracks=3)
    second = create_music(client)
    wait_for(client, first["id"], RUNNING)
    response = client.post(f"/api/runs/{second['id']}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "canceled"
    client.post(f"/api/runs/{first['id']}/cancel")
    wait_for(client, first["id"])


def test_a_running_music_run_stops_within_a_step_and_the_model_stays_loaded(client_factory):
    client = client_factory(fake_step_delay_ms=40)  # a track takes about 1.2 s
    created = create_music(client, tracks=3)
    deadline = time.monotonic() + 20
    while not client.get(f"/api/runs/{created['id']}").json()["tracks"]:  # wait until the first track is finished
        assert time.monotonic() < deadline
        time.sleep(0.02)
    pid = client.get("/api/status").json()["worker"]["pid"]
    response = client.post(f"/api/runs/{created['id']}/cancel")
    assert response.status_code == 202 and response.json()["canceling"] is True
    run = wait_for(client, created["id"])
    assert run["status"] == "canceled" and 1 <= len(run["tracks"]) < 3  # what was finished is kept
    assert audio_bytes(client, run["tracks"][0])  # and it still plays
    status = wait_for_worker_state(client, "ready")["worker"]
    assert status["pid"] == pid and status["model"] == "music"  # the worker was not killed
    assert wait_for(client, create_music(client)["id"])["status"] == "done"


def test_a_failure_says_what_happened_and_how_many_tracks_were_kept(client):
    run = run_and_wait(client, "warm piano [fake:oom@1]", tracks=3)
    assert run["status"] == "failed" and len(run["tracks"]) == 1
    assert run["error"]["hint"] and "1 of 3 track(s) were finished and kept." in run["error"]["message"]
    assert audio_bytes(client, run["tracks"][0])


def test_an_ordinary_error_fails_the_run_and_the_next_one_works(client):
    run = run_and_wait(client, "warm piano [fake:error]")
    assert run["status"] == "failed" and "Simulated music generation failure" in run["error"]["message"] and run["tracks"] == []
    assert run_and_wait(client)["status"] == "done"


def test_a_crash_of_the_music_worker_fails_the_run_and_the_next_one_starts_a_new_worker(client):
    run = run_and_wait(client, "warm piano [fake:crash]")
    assert run["status"] == "failed" and "The music worker stopped unexpectedly (exit code 3)" in run["error"]["message"]  # not "image"
    assert client.get("/api/status").json()["worker"]["detail"].startswith("The music worker stopped unexpectedly")
    assert run_and_wait(client)["status"] == "done"


def test_retrying_means_sending_the_same_request_again(client):
    first = run_and_wait(client, "warm piano [fake:error]", lyrics="[Verse]\nla", seed=4, fields={"genre": "folk"})
    again = run_and_wait(client, first["prompt"], lyrics=first["lyrics"], seed=first["options"]["seed"], tracks=1,
                         steps=first["options"]["steps"], duration=first["options"]["duration"], fields=first["options"]["fields"])
    assert again["status"] == "failed" and again["error"] == first["error"] and again["lyrics"] == "[Verse]\nla"


# ------------------------------------------------------------------ housekeeping
def audio_dir(client, run_id: str) -> Path:
    return Path(client.app.state.storage.root) / "audio" / run_id


def test_deleting_a_run_deletes_its_audio(client):
    run = run_and_wait(client, tracks=2)
    assert len(list(audio_dir(client, run["id"]).glob("*.wav"))) == 2
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not audio_dir(client, run["id"]).exists()
    assert all(client.get(t["url"]).status_code == 404 for t in run["tracks"])
    assert client.app.state.db.get_track(run["tracks"][0]["id"]) is None


def test_keep_and_expiry_work_for_music(client_factory):
    client = client_factory(retention_days=10, bin_days=0)
    kept, old = run_and_wait(client), run_and_wait(client)
    assert client.patch(f"/api/runs/{kept['id']}", json={"pinned": True}).json()["pinned"] is True
    db = client.app.state.db
    with db.tx() as c:
        c.execute("UPDATE runs SET created_at='2026-01-01T00:00:00.000Z'")  # both are long past their 10 days
    removed = client.portal.call(client.app.state.jobs.sweep_expired)
    assert removed == 1
    assert client.get(f"/api/runs/{old['id']}").status_code == 404 and not audio_dir(client, old["id"]).exists()
    assert client.get(f"/api/runs/{kept['id']}").status_code == 200 and audio_dir(client, kept["id"]).exists()
    assert audio_bytes(client, kept["tracks"][0])
    assert wait_for(client, kept["id"])["expires_at"] is None


def test_a_music_run_survives_a_restart(client_factory):
    first = client_factory()
    run = run_and_wait(first, tracks=2, fields={"genre": "folk"}, lyrics="[Verse]\nla")
    sound = frames_of(audio_bytes(first, run["tracks"][1]))
    close(first)
    second = client_factory()
    again = second.get(f"/api/runs/{run['id']}").json()
    assert again["status"] == "done" and again["lyrics"] == "[Verse]\nla" and again["options"]["fields"] == {"genre": "folk"}
    assert [t["seed"] for t in again["tracks"]] == [t["seed"] for t in run["tracks"]]
    assert frames_of(audio_bytes(second, again["tracks"][1])) == sound


def test_a_run_interrupted_by_a_restart_keeps_the_tracks_it_finished(client_factory, tmp_path):
    """recover_interrupted marks a run left 'running' as failed; its tracks stay, as a picture run's images do."""
    first = client_factory(fake_step_delay_ms=30)
    created = create_music(first, tracks=3)
    deadline = time.monotonic() + 20
    while not first.get(f"/api/runs/{created['id']}").json()["tracks"]:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    close(first)  # the server stops while the run is still going
    second = client_factory()
    run = second.get(f"/api/runs/{created['id']}").json()
    assert run["status"] == "failed" and run["tracks"] and audio_bytes(second, run["tracks"][0])


# ------------------------------------------------------------------ pictures and music side by side
def test_the_history_holds_both_kinds_and_a_picture_run_has_no_tracks(client):
    picture = wait_for(client, create_run(client)["id"])
    music = run_and_wait(client)
    listed = {r["id"]: r for r in client.get("/api/runs").json()["runs"]}
    assert listed[picture["id"]]["mode"] == "generate" and listed[picture["id"]]["tracks"] == [] and listed[picture["id"]]["lyrics"] is None
    assert listed[music["id"]]["mode"] == "music" and listed[music["id"]]["images"] == [] and len(listed[music["id"]]["tracks"]) == 1
    assert listed[picture["id"]]["images"]


def test_the_queue_is_shared_and_in_order(client_factory):
    client = client_factory(fake_step_delay_ms=20)
    ids = [create_music(client)["id"], create_run(client, steps=3)["id"], create_music(client)["id"]]
    for run_id in ids:
        assert wait_for(client, run_id)["status"] == "done"
    started = [client.get(f"/api/runs/{run_id}").json()["started_at"] for run_id in ids]
    assert started == sorted(started)


def test_a_music_run_does_not_count_against_a_pictures_limits(client):
    run = run_and_wait(client, duration=300, steps=60, tracks=4, seed=1)  # far outside any picture limit
    assert run["status"] == "done" and len(run["tracks"]) == 4


# ------------------------------------------------------------------ two things a music worker must not disturb
def test_the_image_capabilities_do_not_change_when_the_music_worker_has_been_loaded(client):
    before = client.get("/api/capabilities").json()
    deadline = time.monotonic() + 15
    while "music" not in before["modes"]:  # the start-up checks run in the background: wait for both to finish
        assert time.monotonic() < deadline
        time.sleep(0.05)
        before = client.get("/api/capabilities").json()
    assert wait_for(client, create_music(client)["id"])["status"] == "done"  # the music worker reports its own supports
    after = client.get("/api/capabilities").json()
    assert after["supports"] == before["supports"] and after["supports"]["edit"] is True and after["modes"] == before["modes"]


def test_a_path_a_worker_reports_for_a_track_must_be_inside_that_runs_audio_folder(tmp_path):
    from studio.storage import Storage, StorageError

    storage = Storage(tmp_path)
    storage.ensure_layout()
    run, other = "a" * 32, "b" * 32
    (storage.audio / run).mkdir()
    good = storage.audio / run / "0.wav"
    good.write_bytes(b"x")
    assert storage.accept_worker_audio(f"audio/{run}/0.wav", run) == good.resolve()
    for rel, fragment in ((f"audio/{other}/0.wav", "outside the run folder"), ("images/x/0.png", "outside the run folder"),
                          ("../../etc/passwd", "escapes"), (f"audio/{run}/missing.wav", "missing track file"),
                          (f"audio/{run}/sub/../../{other}/0.wav", "outside the run folder")):
        with pytest.raises(StorageError, match=fragment):
            storage.accept_worker_audio(rel, run)
    (storage.audio / other).mkdir()
    (storage.audio / other / "0.wav").write_bytes(b"y")
    with pytest.raises(StorageError):  # another run's real file is still not this run's
        storage.accept_worker_audio(f"audio/{other}/0.wav", run)


# ------------------------------------------------------------------ each model's start-up check decides for that model
@pytest.mark.parametrize("image_ok,music_ok", [(False, True), (True, False)])
def test_each_models_start_up_check_decides_its_own_availability(client_factory, image_ok, music_ok):
    """The Music tab is offered because the *music* check passed, and the image model's state comes from the image check:
    one failing must not make the other look failed, and one passing must not make the other look fine."""
    from unittest import mock

    from studio.jobs import JobManager

    def outcome(kind: str, ok: bool) -> dict:
        if ok:
            return {"event": "probe", "ok": True, "supports": {"edit": True}, "device": {"name": f"{kind} device"}}
        return {"event": "probe", "ok": False, "error": {"kind": "error", "message": f"the {kind} check failed", "hint": None}}

    async def probe(self, kind):
        return outcome(kind, image_ok if kind == "image" else music_ok)

    with mock.patch.object(JobManager, "_probe_subprocess", probe):
        client = client_factory()
        deadline = time.monotonic() + 15
        while client.get("/api/capabilities").json()["music"]["state"] in ("pending", "running"):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        caps = client.get("/api/capabilities").json()
        worker = client.get("/api/status").json()["worker"]
    assert caps["music"]["available"] is music_ok and ("music" in caps["modes"]) is music_ok
    assert caps["music"]["reason"] == (None if music_ok else "the music check failed")
    assert worker["probe"] == ("done" if image_ok else "failed")
    assert (worker["detail"] is None) is image_ok  # and only a failed image check puts a problem on the worker


def test_with_lyrics_the_fake_melody_has_a_second_voice_so_the_two_kinds_of_track_sound_different():
    import random

    from studio.pipelines.base import MusicJob
    from studio.pipelines.fake_music import FakeMusicPipeline, melody_seed, render_melody

    plain, sung = render_melody(random.Random(5), 1.0, False), render_melody(random.Random(5), 1.0, True)
    assert len(plain) == len(sung) and plain != sung
    pipeline = FakeMusicPipeline(step_delay_ms=0)
    for lyrics, voiced in (("[Verse]\nla", True), ("[Instrumental]", False), ("  [INSTRUMENTAL] ", False)):  # the tag, however it is written
        job = MusicJob(run_id="0" * 32, mode="music", prompt="p", lyrics=lyrics, duration=2, steps=10, seeds=[3], model_id="x")
        made = pipeline.generate(job, 0, 3, lambda *_: None)
        assert made.pcm == render_melody(random.Random(melody_seed(job, 3)), made.seconds, voiced), lyrics


def test_the_progress_bar_stand_in_reports_each_step_whether_it_is_iterated_or_updated():
    from studio.pipelines.real_music import _ReportingBar

    seen: list = []
    assert list(_ReportingBar(lambda n, t: seen.append((n, t)), [10, 20, 30], total=3)) == [10, 20, 30]
    assert seen == [(1, 3), (2, 3), (3, 3)]
    seen.clear()
    with _ReportingBar(lambda n, t: seen.append((n, t)), total=2) as bar:
        bar.update()
        bar.update()
        bar.set_description("anything else a progress bar does is accepted and ignored")
    assert seen == [(1, 2), (2, 2)]


def test_the_fake_tune_depends_on_everything_that_was_asked_for():
    from studio.pipelines.base import MusicJob
    from studio.pipelines.fake_music import FakeMusicPipeline

    pipeline = FakeMusicPipeline(step_delay_ms=0)

    def tune(seed: int = 3, **change) -> bytes:
        asked = {"prompt": "p", "lyrics": "[Verse]\nla", "duration": 2, "steps": 10, **change}
        job = MusicJob(run_id="0" * 32, mode="music", seeds=[seed], model_id="x", **asked)
        return pipeline.generate(job, 0, seed, lambda *_: None).pcm

    base = tune()
    assert tune() == base  # the same request, the same tune
    for change in ({"seed": 4}, {"prompt": "q"}, {"lyrics": "[Verse]\nlo"}, {"duration": 3}, {"steps": 11}):
        assert tune(**change) != base, change
