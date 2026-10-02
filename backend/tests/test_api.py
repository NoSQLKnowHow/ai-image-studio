"""API behaviour end to end, with the fake pipeline running in a real worker subprocess."""

from __future__ import annotations

import json
import sqlite3

from PIL import Image

from conftest import close, create_run, wait_for, wait_for_worker_state


def test_health_index_and_capabilities(client):
    assert client.get("/api/health").json()["ok"] is True
    assert "web interface arrives in milestone M3" in client.get("/").text
    caps = client.get("/api/capabilities").json()
    assert caps["pipeline"] == "fake" and caps["modes"] == ["generate"] and caps["model"] == "fake-pipeline"
    assert caps["defaults"]["width"] == 2048 and caps["defaults"]["steps"] == 40 and caps["queue_cap"] == 10
    assert caps["aspect_ratios"]["16:9"] == [2752, 1536] and caps["supports"]["negative_prompt"] is True
    assert caps["limits"]["num_images"]["max"] == 8


def test_generate_end_to_end(client, tmp_path):
    created = create_run(client, "Halloween town at dawn", seed=42, num_images=2, negative_prompt="blurry")
    assert created["status"] == "queued" and created["queue_position"] == 1
    run = wait_for(client, created["id"])
    assert run["status"] == "done" and run["error"] is None and run["finished_at"]
    assert [im["seed"] for im in run["images"]] == [42, 43]
    assert run["options"] == {"width": 256, "height": 256, "steps": 3, "seed": 42, "seed_was_random": False,
                              "num_images": 2, "negative_prompt": "blurry", "cfg_scale": None, "transparent": False}

    image = run["images"][1]
    full = client.get(image["url"])
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"
    assert "immutable" in full.headers["cache-control"]
    png = tmp_path / "out.png"
    png.write_bytes(full.content)
    with Image.open(png) as im:
        assert im.size == (256, 256) and im.text["seed"] == "43" and im.text["negative_prompt"] == "blurry"

    thumb = client.get(image["thumb_url"])
    assert thumb.headers["content-type"] == "image/webp"
    download = client.get(image["download_url"])
    disposition = download.headers["content-disposition"]
    assert 'filename="generate_halloween-town-dawn_256x256_s43_' in disposition


def test_seed_is_random_when_omitted(client):
    body = {"prompt": "x", "options": {"width": 256, "height": 256, "steps": 1}}
    first = client.post("/api/runs", json=body).json()["options"]
    second = client.post("/api/runs", json=body).json()["options"]
    assert first["seed_was_random"] and second["seed_was_random"] and first["seed"] != second["seed"]


def test_transparent_run_produces_alpha(client):
    run = wait_for(client, create_run(client, "dragon sticker", transparent=True)["id"])
    assert run["effective_prompt"].startswith("This is an RGBA image") and run["images"][0]["has_alpha"]
    assert "_rgba_" in client.get(run["images"][0]["download_url"]).headers["content-disposition"]


def test_validation_errors_use_fastapi_shape(client):
    bad_size = client.post("/api/runs", json={"prompt": "x", "options": {"width": 1000, "height": 1024}})
    assert bad_size.status_code == 422
    assert bad_size.json()["detail"][0]["loc"] == ["body", "options", "width"]
    for body in ({"prompt": "x", "options": {"steps": "40"}}, {"prompt": "x", "colour": "red"}, {}):
        assert client.post("/api/runs", json=body).status_code == 422
    edit = client.post("/api/runs", json={"mode": "edit", "prompt": "x"})
    assert edit.status_code == 422 and "M5" in edit.json()["detail"][0]["msg"]


def test_listing_and_pagination(client):
    ids = [create_run(client, f"prompt {i}", steps=1)["id"] for i in range(3)]
    page = client.get("/api/runs", params={"limit": 2}).json()
    assert [r["id"] for r in page["runs"]] == ids[:0:-1] and page["next_before"] == ids[1]
    rest = client.get("/api/runs", params={"limit": 2, "before": page["next_before"]}).json()
    assert [r["id"] for r in rest["runs"]] == [ids[0]] and rest["next_before"] is None
    assert client.get("/api/runs", params={"before": "f" * 32}).status_code == 400
    assert client.get("/api/runs", params={"limit": 0}).status_code == 422


def test_not_found_paths(client):
    assert client.get("/api/runs/" + "f" * 32).status_code == 404
    assert client.delete("/api/runs/" + "f" * 32).status_code == 404
    assert client.delete("/api/runs/../../etc").status_code in (404, 405)
    assert client.get("/api/images/" + "f" * 32).status_code == 404
    assert client.get("/api/images/" + "f" * 32 + "/thumb").status_code == 404


def test_delete_removes_run_and_files(client, tmp_path):
    run = wait_for(client, create_run(client)["id"])
    image_dir = tmp_path / "data" / "images" / run["id"]
    assert image_dir.exists()
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not image_dir.exists() and not (tmp_path / "data" / "thumbs" / run["id"]).exists()
    assert client.get(f"/api/runs/{run['id']}").status_code == 404
    assert client.get(run["images"][0]["url"]).status_code == 404


def test_queue_order_positions_cap_and_conflicts(client_factory):
    client = client_factory(queue_cap=2, fake_step_delay_ms=40)
    slow = create_run(client, "slow", steps=25)  # ~1 s
    wait_for(client, slow["id"], frozenset({"running"}))
    assert client.delete(f"/api/runs/{slow['id']}").status_code == 409

    second, third = create_run(client, "second", steps=1), create_run(client, "third", steps=1)
    assert (second["queue_position"], third["queue_position"]) == (1, 2)
    full = client.post("/api/runs", json={"prompt": "fourth", "options": {"width": 256, "height": 256}})
    assert full.status_code == 429 and full.json()["code"] == "queue_full"

    assert client.delete(f"/api/runs/{second['id']}").status_code == 204  # a waiting run can be deleted
    assert client.get(f"/api/runs/{third['id']}").json()["queue_position"] == 1
    done = [wait_for(client, r["id"]) for r in (slow, third)]
    assert [r["status"] for r in done] == ["done", "done"]
    assert done[0]["finished_at"] <= done[1]["started_at"]  # strictly one at a time, in order
    assert client.get(f"/api/runs/{second['id']}").status_code == 404


def test_pipeline_error_and_out_of_memory_fail_the_run_with_hints(client):
    error = wait_for(client, create_run(client, "x [fake:error]")["id"])
    assert error["status"] == "failed" and "Simulated pipeline failure" in error["error"]["message"]
    oom = wait_for(client, create_run(client, "x [fake:oom@1]", num_images=3)["id"])
    assert oom["status"] == "failed" and oom["error"]["hint"]
    assert "1 of 3 image(s) were finished and kept" in oom["error"]["message"] and len(oom["images"]) == 1
    assert wait_for(client, create_run(client, "fine")["id"])["status"] == "done"


def test_worker_crash_fails_the_run_and_the_worker_restarts(client):
    crashed = wait_for(client, create_run(client, "x [fake:crash@1]", num_images=3)["id"])
    assert crashed["status"] == "failed" and "stopped unexpectedly (exit code 3)" in crashed["error"]["message"]
    assert "1 of 3" in crashed["error"]["message"] and "restarted automatically" in crashed["error"]["hint"]
    assert client.get("/api/status").json()["worker"]["state"] == "error"
    assert wait_for(client, create_run(client, "after the crash")["id"])["status"] == "done"
    assert client.get("/api/status").json()["worker"]["state"] == "ready"


def test_model_load_failure_is_reported(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_FAIL", "1")
    client = client_factory()
    run = wait_for(client, create_run(client)["id"])
    assert run["status"] == "failed" and "Simulated model load failure" in run["error"]["message"]
    worker = wait_for_worker_state(client, "error")["worker"]
    assert "Simulated model load failure" in worker["detail"] and worker["hint"]


def test_real_pipeline_reports_unavailable_until_m2(client_factory):
    client = client_factory(pipeline="real")
    assert client.get("/api/capabilities").json()["model"] == "Qwen/Qwen-Image-2.1"
    run = wait_for(client, create_run(client)["id"])
    assert run["status"] == "failed" and "M2" in run["error"]["message"]
    assert "STUDIO_PIPELINE=fake" in run["error"]["hint"]
    assert wait_for_worker_state(client, "unavailable")["worker"]["detail"]


def test_shutdown_and_restart_recovery(client_factory, tmp_path):
    first = client_factory(fake_step_delay_ms=40)
    running = create_run(first, "long one", steps=50)
    waiting = create_run(first, "waits for the restart", steps=1)
    wait_for(first, running["id"], frozenset({"running"}))
    (tmp_path / "data" / "images" / "leftover.png.part").write_bytes(b"half")
    close(first)  # graceful shutdown in the middle of a run

    # simulate a hard crash too: a run left 'running' in the database
    with sqlite3.connect(tmp_path / "data" / "studio.sqlite") as conn:
        conn.execute("INSERT INTO runs (id, created_at, started_at, status, mode, prompt, effective_prompt, steps, "
                     "seed, num_images, model_id, options_json) VALUES (?, '2026-01-01T00:00:00Z', "
                     "'2026-01-01T00:00:01Z', 'running', 'generate', 'p', 'p', 1, 1, 1, 'fake-pipeline', ?)",
                     ("d" * 32, json.dumps({})))

    second = client_factory()
    interrupted = second.get(f"/api/runs/{running['id']}").json()
    assert interrupted["status"] == "failed" and "server was stopped" in interrupted["error"]["message"]
    crashed = second.get(f"/api/runs/{'d' * 32}").json()
    assert crashed["status"] == "failed" and crashed["error"]["message"] == "Interrupted by a server restart."
    assert wait_for(second, waiting["id"])["status"] == "done"  # the queue survives a restart
    assert not list((tmp_path / "data").rglob("*.part"))
