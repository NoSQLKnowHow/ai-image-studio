"""One model in memory at a time (DESIGN.md §26.4, decision #41): switching between the image and the music model, the
memory check for each, Load and Unload by model, and what a worker is started with (§26.11)."""

from __future__ import annotations

import itertools
import time

import pytest
from conftest import create_run, wait_for, wait_for_worker_state

import studio.sysinfo
from studio.config import Settings
from studio.worker_client import WorkerClient, worker_env
from test_m2 import meminfo
from test_music_runs import create_music


def worker(client) -> dict:
    return client.get("/api/status").json()["worker"]


def load(client, model=None, expect: int = 202):
    response = client.post("/api/model/load", **({"json": {"model": model}} if model else {}))
    assert response.status_code == expect, response.text
    return response


def unload(client, model=None, expect: int = 200):
    response = client.post("/api/model/unload", **({"json": {"model": model}} if model else {}))
    assert response.status_code == expect, response.text
    return response


def alive(client) -> dict[str, bool]:
    return {kind: c.alive() for kind, c in client.app.state.jobs._clients.items()}


# ------------------------------------------------------------------ switching
def test_a_music_run_after_a_picture_run_replaces_the_image_model(client):
    wait_for(client, create_run(client)["id"])
    image_pid = worker(client)["pid"]
    assert worker(client)["model"] == "image" and alive(client) == {"image": True, "music": False}
    assert wait_for(client, create_music(client)["id"])["status"] == "done"
    now = worker(client)
    assert now["model"] == "music" and now["pid"] not in (None, image_pid) and now["state"] == "ready"
    assert alive(client) == {"image": False, "music": True}  # never both


def test_and_back_again(client):
    wait_for(client, create_music(client)["id"])
    music_pid = worker(client)["pid"]
    assert wait_for(client, create_run(client)["id"])["status"] == "done"
    assert worker(client)["model"] == "image" and worker(client)["pid"] != music_pid
    assert alive(client) == {"image": True, "music": False}


def test_the_same_model_twice_in_a_row_reuses_the_worker(client):
    first = wait_for(client, create_music(client)["id"])
    pid = worker(client)["pid"]
    assert wait_for(client, create_music(client)["id"])["status"] == "done" and worker(client)["pid"] == pid
    assert first["status"] == "done"


def test_a_mixed_queue_runs_each_on_its_own_model_in_order(client_factory):
    client = client_factory(fake_step_delay_ms=15)
    sub = client.portal.call(lambda: client.app.state.bus.subscribe())
    ids = [create_music(client)["id"], create_run(client, steps=3)["id"], create_music(client)["id"], create_run(client, steps=3)["id"]]
    for run_id in ids:
        assert wait_for(client, run_id)["status"] == "done"

    async def take():
        items = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait())
        return items

    loads = [d["model"] for name, d in client.portal.call(take) if name == "worker.state" and d["state"] == "loading"]
    loads = [model for model, _ in itertools.groupby(loads)]  # the same load is reported again once the process has a pid
    assert loads == ["music", "image", "music", "image"]  # one load each, each model started for its own run, in the queue's order
    assert alive(client)["image"] and not alive(client)["music"]


def test_the_pill_is_told_about_the_switch(client):
    wait_for(client, create_run(client)["id"])
    sub = client.portal.call(lambda: client.app.state.bus.subscribe())
    wait_for(client, create_music(client)["id"])

    async def take():
        items = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait())
        return items

    states = [(d["state"], d["model"]) for name, d in client.portal.call(take) if name == "worker.state"]
    assert ("unloaded", None) in states and ("loading", "music") in states and ("ready", "music") in states
    assert states.index(("unloaded", None)) < states.index(("loading", "music"))  # the image model went first


# ------------------------------------------------------------------ Load and Unload, by model
def test_loading_the_music_model_unloads_the_image_model_first(client):
    load(client, "image")
    wait_for_worker_state(client, "ready")
    assert worker(client)["model"] == "image"
    body = load(client, "music").json()
    assert body["worker"]["model"] == "music"
    ready = wait_for_worker_state(client, "ready")["worker"]
    assert ready["model"] == "music" and alive(client) == {"image": False, "music": True}


def test_loading_the_image_model_unloads_the_music_model(client):
    load(client, "music")
    wait_for_worker_state(client, "ready")
    load(client, "image")
    assert wait_for_worker_state(client, "ready")["worker"]["model"] == "image"
    assert alive(client) == {"image": True, "music": False}


def test_without_a_body_load_means_the_image_model_as_before(client):
    assert load(client).json()["worker"]["model"] == "image"
    wait_for_worker_state(client, "ready")


def test_loading_the_model_that_is_loaded_does_nothing(client):
    load(client, "music")
    pid = wait_for_worker_state(client, "ready")["worker"]["pid"]
    assert load(client, "music", expect=200).json()["worker"]["pid"] == pid
    assert load(client, "image")  # but the other one is a switch
    assert wait_for_worker_state(client, "ready")["worker"]["pid"] != pid


def test_a_load_pressed_while_a_run_uses_a_model_changes_nothing(client_factory):
    client = client_factory(fake_step_delay_ms=40)
    run = create_music(client, tracks=3)
    wait_for(client, run["id"], frozenset({"running"}))
    pid = worker(client)["pid"]
    assert load(client, "image", expect=200).json()["worker"]["model"] == "music"  # not switched under a running track
    assert worker(client)["pid"] == pid
    assert wait_for(client, run["id"])["status"] == "done"


def test_unload_can_name_the_model_and_only_unloads_that_one(client):
    load(client, "music")
    wait_for_worker_state(client, "ready")
    assert unload(client, "image").json()["worker"]["model"] == "music"  # the image model was not loaded: nothing to do
    assert alive(client) == {"image": False, "music": True}
    assert unload(client, "music").json()["worker"]["state"] == "unloaded"
    assert alive(client) == {"image": False, "music": False}


def test_unload_without_a_body_unloads_whichever_is_loaded(client):
    load(client, "music")
    wait_for_worker_state(client, "ready")
    assert unload(client).json()["worker"]["state"] == "unloaded" and alive(client) == {"image": False, "music": False}


def test_unload_is_refused_while_a_track_is_being_made(client_factory):
    client = client_factory(fake_step_delay_ms=40)
    run = create_music(client, tracks=3)
    wait_for(client, run["id"], frozenset({"running"}))
    assert unload(client, expect=409).json()["code"] == "busy"
    assert wait_for(client, run["id"])["status"] == "done"


def test_a_model_that_does_not_exist_is_refused(client):
    for model in ("video", "", "Music", None):
        assert client.post("/api/model/load", json={"model": model}).status_code == 422, model
    assert client.post("/api/model/unload", json={"model": "video"}).status_code == 422
    assert client.post("/api/model/load", json={"model": "music", "extra": 1}).status_code == 422
    assert not any(alive(client).values())


def test_an_idle_music_model_is_unloaded_after_the_timeout_like_the_image_model(client_factory):
    client = client_factory(idle_timeout_min=0.03)  # about 2 s
    load(client, "music")
    ready = wait_for_worker_state(client, "ready")["worker"]
    assert ready["unload_at"] is not None
    gone = wait_for_worker_state(client, "unloaded", timeout=15)["worker"]
    assert gone["pid"] is None and gone["model"] is None


def test_the_model_pill_never_claims_a_model_that_is_not_there(client):
    assert worker(client)["model"] is None
    load(client, "music")
    assert wait_for_worker_state(client, "ready")["worker"]["model"] == "music"
    unload(client)
    assert worker(client)["model"] is None


# ------------------------------------------------------------------ memory, one number for each model
def test_the_music_model_has_its_own_memory_check_and_message(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=30))
    client = client_factory(min_free_gb=20, music_min_free_gb=45)
    body = load(client, "music", expect=409).json()
    assert body["code"] == "not_enough_memory"
    assert body["detail"] == "Not enough free memory to load the music model: 30.0 GB available, 45 GB required (STUDIO_MUSIC_MIN_FREE_GB)."
    assert "drop_caches" in body["hint"]
    assert worker(client)["pid"] is None and worker(client)["state"] == "error" and worker(client)["model"] == "music"
    load(client, "image")  # 30 GB is enough for the image model's 20
    assert wait_for_worker_state(client, "ready")["worker"]["model"] == "image"


def test_a_music_run_that_does_not_fit_fails_with_the_same_message_and_nothing_is_left_loading(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=30))
    client = client_factory(music_min_free_gb=45)
    run = wait_for(client, create_music(client)["id"])
    assert run["status"] == "failed" and run["tracks"] == []
    assert run["error"]["message"].startswith("Not enough free memory to load the music model: 30.0 GB available, 45 GB required")
    assert "drop_caches" in run["error"]["hint"] and not any(alive(client).values())


def test_a_failed_music_load_leaves_nothing_loaded_because_the_image_model_went_first(client_factory, tmp_path, monkeypatch):
    client = client_factory(music_min_free_gb=45)
    wait_for(client, create_run(client)["id"])  # no memory file patched yet: the image model loads
    assert alive(client)["image"]
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=30))
    run = wait_for(client, create_music(client)["id"])
    assert run["status"] == "failed" and alive(client) == {"image": False, "music": False}
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=80))  # memory is free again
    assert wait_for(client, create_music(client)["id"])["status"] == "done"


def test_the_image_check_is_unchanged_and_music_checks_are_off_by_default(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=12.5))
    client = client_factory(min_free_gb=40)
    run = wait_for(client, create_run(client)["id"])
    assert run["error"]["message"] == "Not enough free memory to load the model: 12.5 GB available, 40 GB required (STUDIO_MIN_FREE_GB)."
    assert wait_for(client, create_music(client)["id"])["status"] == "done"  # no music limit set: not checked
    memory = client.get("/api/status").json()["memory"]
    assert memory["min_free_gb"] == 40 and memory["music_min_free_gb"] is None


# ------------------------------------------------------------------ what a worker is started with
def settings(**extra) -> Settings:
    from pathlib import Path

    return Settings(data_dir=Path("/tmp/x"), pipeline="fake", **extra)


def test_each_worker_is_started_for_its_own_model_and_cache_mode():
    image = WorkerClient(settings(model="Qwen/Qwen-Image-2.1", music_model="MiniMaxAI/MiniMax-Music3", hub_mode="offline"), lambda e: None, "image")
    music = WorkerClient(settings(model="Qwen/Qwen-Image-2.1", music_model="MiniMaxAI/MiniMax-Music3", hub_mode="offline"), lambda e: None, "music")
    image_cmd, music_cmd = image.command(), music.command()
    assert image_cmd[image_cmd.index("--kind") + 1] == "image" and music_cmd[music_cmd.index("--kind") + 1] == "music"
    assert image_cmd[image_cmd.index("--model") + 1] == "Qwen/Qwen-Image-2.1"
    assert music_cmd[music_cmd.index("--model") + 1] == "MiniMaxAI/MiniMax-Music3"
    assert image_cmd[image_cmd.index("--hub-mode") + 1] == "offline" and "--local-files-only" not in image_cmd
    assert "--probe" in music.command(probe=True) and "--probe" not in music_cmd


def test_the_music_worker_alone_gets_the_second_copy_of_diffusers_first(tmp_path):
    s = settings(music_libs=tmp_path / "music-libs")
    music_path = worker_env(s, "music")["PYTHONPATH"].split(":")
    image_path = worker_env(s, "image")["PYTHONPATH"].split(":")
    assert music_path[0] == str(tmp_path / "music-libs") and str(tmp_path / "music-libs") not in image_path
    assert music_path[1:] == image_path  # otherwise the same
    assert "music-libs" not in worker_env(settings(), "music")["PYTHONPATH"]  # none configured: nothing added


def test_workers_do_not_report_usage_and_offline_mode_is_enforced_for_every_library(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)
    for kind in ("image", "music"):
        assert worker_env(settings(), kind)["HF_HUB_DISABLE_TELEMETRY"] == "1"
        assert "HF_HUB_OFFLINE" not in worker_env(settings(hub_mode="auto"), kind)
        assert "HF_HUB_OFFLINE" not in worker_env(settings(hub_mode="online"), kind)
        env = worker_env(settings(hub_mode="offline"), kind)
        assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1"
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "0")  # someone who set it on purpose keeps their choice
    assert worker_env(settings(), "image")["HF_HUB_DISABLE_TELEMETRY"] == "0"
