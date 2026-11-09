"""The server side of the Speech tab's foundation (DESIGN.md §38.3, §38.9, §38.10): what `/api/capabilities` says about the engines, Load and Unload
for a speech engine, the rule that only one model is in memory, the memory check for each engine, and the settings.
"""

from __future__ import annotations

import time

import pytest
from conftest import create_run, wait_for, wait_for_worker_state
from test_m2 import meminfo

import studio.sysinfo
from studio.config import ConfigError, Settings


def capabilities(client) -> dict:
    return client.get("/api/capabilities").json()


def engines_by_id(client) -> dict:
    return {e["id"]: e for e in capabilities(client)["speech"]["engines"]}


def wait_for_engines(client, timeout: float = 20.0) -> dict:
    """The engines are checked in the background after start-up; wait until none is still being checked."""
    deadline = time.monotonic() + timeout
    while True:
        caps = capabilities(client)
        if all(e["state"] != "checking" for e in caps["speech"]["engines"]):
            return caps
        if time.monotonic() > deadline:
            raise AssertionError("the speech engines are still being checked")
        time.sleep(0.05)


def worker(client) -> dict:
    return client.get("/api/status").json()["worker"]


def load(client, **body):
    return client.post("/api/model/load", json={"model": "speech", **body})


def alive(client) -> list[str]:
    return sorted(key for key, c in client.app.state.jobs._clients.items() if c.alive())


# ---------------------------------------------------------------- capabilities (criterion 259)
def test_the_speech_tab_is_offered_when_an_engine_can_run_and_each_engine_says_what_it_is(client):
    caps = wait_for_engines(client)
    assert "speech" in caps["modes"] and caps["speech"]["available"] is True and caps["speech"]["reason"] is None
    engines = engines_by_id(client)
    assert list(engines) == ["fake", "fake-basic"]  # the registry's order
    full, plain = engines["fake"], engines["fake-basic"]
    assert full["state"] == plain["state"] == "ready" and full["reason"] is None
    assert (full["can_style"], full["can_clone"], full["can_design"]) == (True, True, True)
    assert (plain["can_style"], plain["can_clone"], plain["can_design"]) == (False, False, False)
    assert full["variants"] == ["default", "clone", "design"] and plain["licence"]["noncommercial"] is True


def test_the_limits_the_text_pipeline_obeys_are_published_so_the_page_never_repeats_a_number(client):
    limits = capabilities(client)["limits"]["speech"]
    assert limits["max_chars"] == 1_500_000 and limits["part_max_chars"] == 30_000 and limits["upload_mb"] == 50
    assert limits["fetch_mb"] == 5 and limits["pause_ms"] == 450 and limits["voice_seconds"] == {"min": 8, "max": 60}
    assert limits["chars_per_second"] == pytest.approx(14.0)


def test_the_engines_setting_narrows_what_is_offered(client_factory):
    client = client_factory(speech_engines=("fake-basic",))
    wait_for_engines(client)
    assert list(engines_by_id(client)) == ["fake-basic"]
    assert client.post("/api/model/load", json={"model": "speech", "engine": "fake"}).status_code == 422  # not offered, so not loadable either


def test_with_no_engine_switched_on_the_tab_says_so(client_factory):
    client = client_factory(speech_engines=("kokoro",))  # a real engine does not exist under the fake pipeline
    caps = capabilities(client)
    assert caps["speech"]["available"] is False and "speech" not in caps["modes"] and "switched on" in caps["speech"]["reason"]


def test_an_engine_that_failed_its_check_is_unavailable_with_its_own_reason(client_factory, monkeypatch):
    # the check runs in a worker process, which inherits this environment; the fake engine fails it the way an engine with a missing library does
    monkeypatch.setenv("STUDIO_FAKE_SPEECH_UNAVAILABLE", "fake-basic")
    client = client_factory()
    wait_for_engines(client)
    broken = engines_by_id(client)["fake-basic"]
    assert broken["state"] == "not_installed" and "library is missing" in broken["reason"] and broken["hint"]
    assert engines_by_id(client)["fake"]["state"] == "ready" and "speech" in capabilities(client)["modes"]  # one engine failing does not hide the tab


def test_the_status_never_reports_a_probe_client_as_a_worker(client):
    wait_for_engines(client)
    assert alive(client) == [] and sorted(client.app.state.jobs._clients) == ["image", "music"]  # checking an engine leaves no client behind


# ---------------------------------------------------------------- Load and Unload (criterion 292)
def test_loading_a_speech_engine_starts_its_worker_and_the_status_names_it(client):
    response = load(client, engine="fake")
    assert response.status_code == 202, response.text
    status = wait_for_worker_state(client, "ready")["worker"]
    assert (status["model"], status["engine"], status["variant"]) == ("speech", "fake", "default")
    assert alive(client) == ["speech:fake:default"]


def test_a_variant_is_a_different_model_and_replaces_the_loaded_one(client):
    load(client, engine="fake")
    wait_for_worker_state(client, "ready")
    first_pid = worker(client)["pid"]
    assert load(client, engine="fake", variant="clone").status_code == 202
    wait_for_worker_state(client, "ready")
    now = worker(client)
    assert now["variant"] == "clone" and now["pid"] not in (None, first_pid)
    assert alive(client) == ["speech:fake:clone"]  # never both checkpoints


def test_asking_for_what_is_already_loaded_does_nothing(client):
    load(client, engine="fake")
    wait_for_worker_state(client, "ready")
    pid = worker(client)["pid"]
    assert load(client, engine="fake").status_code == 200 and worker(client)["pid"] == pid


def test_speech_replaces_the_image_model_and_the_image_model_replaces_it(client):
    wait_for(client, create_run(client)["id"])
    assert worker(client)["model"] == "image"
    load(client, engine="fake-basic")
    wait_for_worker_state(client, "ready")
    assert worker(client)["model"] == "speech" and alive(client) == ["speech:fake-basic:default"]  # the image model went first
    assert wait_for(client, create_run(client)["id"])["status"] == "done"
    assert worker(client)["model"] == "image" and alive(client) == ["image"]


def test_unload_by_kind_stops_a_speech_worker_and_leaves_other_models_alone(client):
    load(client, engine="fake")
    wait_for_worker_state(client, "ready")
    assert client.post("/api/model/unload", json={"model": "image"}).status_code == 200 and alive(client) == ["speech:fake:default"]
    assert client.post("/api/model/unload", json={"model": "speech"}).status_code == 200
    wait_for_worker_state(client, "unloaded")
    assert alive(client) == []


@pytest.mark.parametrize("body, code", [
    ({"model": "speech"}, "unknown_engine"),  # a speech model is an engine: none named
    ({"model": "speech", "engine": "no-such"}, "unknown_engine"),
    ({"model": "speech", "engine": "fake", "variant": "no-such"}, "unknown_variant"),
])
def test_a_load_that_names_no_real_engine_or_variant_is_refused_in_words(client, body, code):
    response = client.post("/api/model/load", json=body)
    assert response.status_code == 422 and response.json()["code"] == code


def test_a_body_with_a_field_the_server_does_not_know_is_still_refused(client):
    assert client.post("/api/model/load", json={"model": "speech", "engine": "fake", "bogus": 1}).status_code == 422
    assert client.post("/api/model/load", json={"model": "video"}).status_code == 422


# ---------------------------------------------------------------- memory (criterion 278)
def test_a_speech_engine_has_its_own_memory_check_and_message(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=10))
    client = client_factory(speech_min_free_gb=25)
    response = load(client, engine="fake")
    assert response.status_code == 409 and response.json()["code"] == "not_enough_memory"
    detail = response.json()["detail"]
    assert "speech model" in detail and "10.0 GB available" in detail and "25 GB required" in detail and "STUDIO_SPEECH_MIN_FREE_GB" in detail
    assert response.json()["hint"] and alive(client) == []  # nothing is left loading


def test_the_page_is_told_when_an_engine_would_not_fit_right_now(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=10))
    client = client_factory(speech_min_free_gb=25)
    wait_for_engines(client)
    engine = engines_by_id(client)["fake"]
    assert engine["state"] == "needs_memory" and "25 GB required" in engine["reason"] and engine["hint"]
    assert capabilities(client)["speech"]["available"] is True  # it may fit later, so the tab stays
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=60))
    assert engines_by_id(client)["fake"]["state"] == "ready"  # judged afresh on every look


def test_the_fake_engines_have_no_memory_check_unless_one_is_asked_for(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=0.5))  # almost nothing free
    client = client_factory()
    assert load(client, engine="fake").status_code == 202  # a test never depends on how much memory the machine has
    assert client.get("/api/status").json()["memory"]["speech_min_free_gb"] is None


# ---------------------------------------------------------------- settings (criterion 288's neighbours)
def test_the_defaults_are_the_specified_ones():
    s = Settings.from_env({})
    assert (s.speech_max_chars, s.speech_part_max_chars, s.speech_max_upload_mb, s.speech_fetch_max_mb) == (1_500_000, 30_000, 50, 5)
    assert (s.speech_fetch_timeout_s, s.speech_pause_ms, s.speech_voice_min_seconds, s.speech_voice_max_seconds) == (20, 450, 8, 60)
    assert s.speech_gpu_fraction == 0.2 and s.speech_engines == () and s.speech_libs is None and s.speech_min_free_gb is None


def test_model_paths_are_set_per_engine_by_a_generic_name():
    s = Settings.from_env({"STUDIO_SPEECH_QWEN3_TTS_MODEL": " /models/q ", "STUDIO_SPEECH_PIPER_MODEL": "/m/p.onnx", "STUDIO_SPEECH_KOKORO_MODEL": ""})
    assert s.speech_model_for("qwen3-tts") == "/models/q" and s.speech_model_for("piper") == "/m/p.onnx"
    assert s.speech_model_for("kokoro") == "" and s.speech_model_for("anything") == ""  # empty or unset: the engine's own default


def test_a_worker_is_told_its_engines_model_and_gets_its_own_libraries(client_factory, tmp_path):
    from studio.worker_client import WorkerClient, worker_env

    settings = Settings(data_dir=tmp_path, pipeline="fake", speech_libs=tmp_path / "libs", speech_models=(("fake", "/m/fake"),))
    command = WorkerClient(settings, lambda event: None, "speech", "fake", "clone").command()
    assert command[command.index("--engine") + 1] == "fake" and command[command.index("--variant") + 1] == "clone"
    assert command[command.index("--model") + 1] == "/m/fake" and command[command.index("--kind") + 1] == "speech"
    assert str(tmp_path / "libs" / "fake") in worker_env(settings, "speech", "fake")["PYTHONPATH"].split(":")[0]
    assert str(tmp_path / "libs") not in worker_env(settings, "image")["PYTHONPATH"]  # only the speech worker of that engine sees them
    assert str(tmp_path / "libs") not in worker_env(settings, "speech", "other")["PYTHONPATH"].replace(str(tmp_path / "libs" / "other"), "")


@pytest.mark.parametrize("env, fragment", [
    ({"STUDIO_SPEECH_ENGINES": "kokoro,nonsense"}, "does not exist: nonsense"),
    ({"STUDIO_SPEECH_MAX_CHARS": "abc"}, "STUDIO_SPEECH_MAX_CHARS"),
    ({"STUDIO_SPEECH_PAUSE_MS": "9000"}, "STUDIO_SPEECH_PAUSE_MS"),
    ({"STUDIO_SPEECH_GPU_FRACTION": "2"}, "STUDIO_SPEECH_GPU_FRACTION"),
    ({"STUDIO_SPEECH_VOICE_MIN_SECONDS": "60", "STUDIO_SPEECH_VOICE_MAX_SECONDS": "30"}, "smaller than STUDIO_SPEECH_VOICE_MAX_SECONDS"),
    ({"STUDIO_SPEECH_PART_MAX_CHARS": "200000", "STUDIO_SPEECH_MAX_CHARS": "100000"}, "must not be larger than STUDIO_SPEECH_MAX_CHARS"),
])
def test_a_bad_speech_setting_is_reported_with_its_name(env, fragment):
    with pytest.raises(ConfigError) as caught:
        Settings.from_env(env)
    assert fragment in str(caught.value)
