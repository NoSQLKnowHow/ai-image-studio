"""A Vector run through the server (DESIGN.md §31.12.5, §31.12.6): the image worker draws, the clean-up job turns each drawing into an SVG, a `vectors` row is
stored for each, progress says `draw` and then `trace`, Cancel works in both stages, and a version whose clean-up fails is missing and named while the run
keeps the rest. All of it against the fake pipeline (which draws a flat picture for a vector run) and the fake job (`fake_vector_job.py`), so none of it needs
the engine, a GPU or the Vector libraries.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest
from conftest import close, create_run, wait_for
from PIL import Image
from vector_server_support import (
    CONTRACT, NO_SUCH, create_vector, drain, job_calls, libraries_import, make_vector, process_is_gone, stored_files, subscribe, vclient,  # noqa: F401
    vclient_factory, wait_for_job_calls, walk_types,
)

from studio import vector as V
from studio import vectorrun
from studio.pipelines.fake import FLAT_INKS

RUNNING = frozenset({"running"})


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def wait_for_stage(client, run_id: str, stage: str, image: int = 1, timeout: float = 20.0) -> dict:
    """Wait until the run's progress says it is in this stage on this version (1-based), and return the run."""
    deadline = time.monotonic() + timeout
    while True:
        run = client.get(f"/api/runs/{run_id}").json()
        progress = run["progress"] or {}
        if progress.get("stage") == stage and progress.get("image") == image:
            return run
        assert run["status"] == "running" or run["progress"], f"the run ended without being in stage {stage}: {run['status']} {run['error']}"
        assert time.monotonic() < deadline, f"never in stage {stage} on version {image}: {run['progress']}"
        time.sleep(0.02)


# ------------------------------------------------------------------ a whole run
def test_a_vector_run_draws_then_traces_and_stores_each_version(vclient):
    run = make_vector(vclient, "a fox head", seed=100, num_images=3, colours=4, palette=["#FF0000"], width_mm=120, height_mm=60)
    assert run["status"] == "done" and run["error"] is None and run["mode"] == "vector" and run["model_id"] == "fake-pipeline"
    # no pictures and no tracks: the result is the SVG
    assert (run["images"], run["tracks"], run["binned_images"], run["binned_tracks"], run["inputs"]) == ([], [], [], [], [])
    assert [v["idx"] for v in run["vectors"]] == [0, 1, 2] and [v["seed"] for v in run["vectors"]] == [100, 101, 102]  # seed, seed + 1, seed + 2
    storage = vclient.app.state.storage
    for vector in run["vectors"]:
        # the numbers a card shows come from what the clean-up reported and from the file itself
        assert (vector["width_mm"], vector["height_mm"], vector["colours"], vector["layers"]) == (120.0, 60.0, 4, 3)
        assert vector["check"]["size_mm"] == [120.0, 60.0] and vector["check"]["colours_asked"] == 4
        svg = vclient.get(vector["url"]).content
        assert vector["bytes"] == len(svg) == (storage.vector_dir(run["id"]) / f"{vector['idx']}.svg").stat().st_size
    # the files: the drawing the model made, and what was made of it, and nothing of the job's working folder
    assert stored_files(vclient, run["id"]) == sorted(f"{n}{suffix}" for n in range(3) for suffix in ("-layers.zip", "-source.png", ".png", ".svg"))
    # it is not stored as a picture: no row, no thumbnail, no folder left under images
    assert vclient.app.state.db._one("SELECT COUNT(*) AS n FROM images")["n"] == 0
    assert not (storage.images / run["id"]).exists() and not (storage.thumbs / run["id"]).exists()
    assert vclient.app.state.jobs._tracing is None  # no process is remembered once its job is over
    # each file is the kind of file its name says (the job's three outputs go to three different names)
    folder = storage.vector_dir(run["id"])
    assert (folder / "0.svg").read_bytes().startswith(b"<svg") and zipfile.is_zipfile(folder / "0-layers.zip")
    assert (folder / "0.png").read_bytes().startswith(b"\x89PNG") and (folder / "0-source.png").read_bytes().startswith(b"\x89PNG")
    assert (folder / "0.png").read_bytes() != (folder / "0-source.png").read_bytes()  # the preview is not the drawing


def test_how_long_each_clean_up_took_is_logged(vclient, caplog):
    # The Spark checklist asks for the tracing time, and the card only shows the whole run, so the log says it, once for each version.
    with caplog.at_level("INFO", logger="studio.jobs"):
        run = make_vector(vclient, "a fox head", seed=5, num_images=2, colours=4, palette=["#FF0000"])
    lines = [r.getMessage() for r in caplog.records if " traced in " in r.getMessage()]
    assert len(lines) == 2 and all(line.startswith(f"run {run['id']} version ") for line in lines)
    assert lines[0].startswith(f"run {run['id']} version 1 traced in ") and lines[1].startswith(f"run {run['id']} version 2 traced in ")
    assert lines[0].endswith(f" ({run['vectors'][0]['colours']} colours, {run['vectors'][0]['layers']} layers)")


def test_a_clean_up_may_take_ten_minutes_before_it_is_stopped(vclient):
    assert vclient.app.state.jobs.vector_job_timeout == vectorrun.JOB_TIMEOUT_SECONDS == 600


def test_the_model_draws_a_flat_picture_at_the_drawing_size_with_the_recipe_and_guidance(vclient):
    run = make_vector(vclient, "a fox head", seed=3, steps=5, num_images=2, width_mm=200, height_mm=100)
    for idx in (0, 1):
        with Image.open(vclient.app.state.storage.vector_dir(run["id"]) / f"{idx}-source.png") as drawing:
            assert drawing.size == V.draw_size(200, 100) == (1536, 768)
            text = drawing.text  # the worker writes the job it was given into the PNG
            assert text["mode"] == "vector" and text["size"] == "1536x768" and text["steps"] == "5" and text["seed"] == str(3 + idx)
            assert text["cfg_scale"] == "4.0" and text["prompt"] == run["effective_prompt"] and text["negative_prompt"] == run["options"]["negative"]
            colours = {colour for _, colour in drawing.convert("RGB").getcolors(maxcolors=64)}  # flat: only a few exact colours (a gradient has thousands)
            assert (255, 255, 255) in colours and colours <= {(255, 255, 255), *FLAT_INKS} and len(colours) >= 2  # white, and at least one ink


def test_the_job_is_given_the_drawing_the_options_and_a_folder_of_its_own(vclient):
    run = make_vector(vclient, "a fox head", seed=100, num_images=2, colours=4, palette=["#FF0000"], kind="flat", width_mm=120, height_mm=60,
                      layers_by="shape", arrangement="stacked", bleed_mm=1.5, min_feature_mm=0.2, kerf_mm=0.1)
    calls = job_calls(vclient)
    assert [call["version"] for call in calls] == ["0", "1"]  # one job for each version, in order
    source = vclient.app.state.storage.vector_dir(run["id"])
    for call in calls:
        assert call["options"] == {"colours": 4, "palette": ["#FF0000"], "kind": "flat", "width_mm": 120.0, "height_mm": 60.0, "layers_by": "shape",
                                   "arrangement": "stacked", "bleed_mm": 1.5, "min_feature_mm": 0.2, "kerf_mm": 0.1, "shape_layer_cap": 30,
                                   "fill_mode": "filled"}
        assert call["sha1"] == sha1(source / f"{call['version']}-source.png")  # it was given the drawing, where the run keeps it
        assert [arg for arg in call["argv"] if arg.startswith("--")] == ["--source", "--options", "--out"]  # the command line of §31.12.4
        assert call["argv"][1] == str(source / f"{call['version']}-source.png") and call["out_exists"]
        assert call["argv"][3].endswith("options.json") and call["argv"][5] == str(Path(call["argv"][3]).parent)  # its own folder, made before it runs
    assert calls[0]["sha1"] != calls[1]["sha1"]  # the two versions are different drawings (different seeds)
    # the working folder is gone, and the options file with it
    assert not any(name.startswith("work") for name in stored_files(vclient, run["id"]))


def test_the_options_the_job_gets_are_the_ones_stored_with_the_run(vclient):
    run = make_vector(vclient, "a fox head", **{**CONTRACT["create_request"]["options"], "steps": 3, "recipe": None})
    [call] = job_calls(vclient)
    stored = run["options"]
    assert {key: call["options"][key] for key in ("colours", "palette", "kind", "width_mm", "height_mm", "layers_by", "arrangement", "bleed_mm",
                                                   "min_feature_mm", "kerf_mm")} == {key: stored[key] for key in
                                                                                       ("colours", "palette", "kind", "width_mm", "height_mm", "layers_by",
                                                                                        "arrangement", "bleed_mm", "min_feature_mm", "kerf_mm")}


def test_a_vector_run_uses_the_image_worker_and_loads_no_other_model(vclient):
    first = create_run(vclient, "a picture", steps=3)
    wait_for(vclient, first["id"])
    pid = vclient.get("/api/status").json()["worker"]["pid"]
    make_vector(vclient)
    status = vclient.get("/api/status").json()
    assert status["worker"]["pid"] == pid and status["worker"]["model"] == "image"  # the worker the picture run started served the vector run
    assert not vclient.app.state.jobs._clients["music"].alive()  # and the music model was never started


def test_vector_runs_one_after_another_each_make_their_versions(vclient):
    first = vclient.post("/api/runs", json={"mode": "vector", "prompt": "one", "options": {"steps": 3, "num_images": 2, "seed": 1}}).json()
    second = vclient.post("/api/runs", json={"mode": "vector", "prompt": "two", "options": {"steps": 3, "num_images": 2, "seed": 50}}).json()
    done = [wait_for(vclient, run["id"]) for run in (first, second)]
    assert [[v["seed"] for v in run["vectors"]] for run in done] == [[1, 2], [50, 51]]
    assert [call["version"] for call in job_calls(vclient)] == ["0", "1", "0", "1"]


def test_a_run_behind_a_vector_run_waits_for_its_clean_up(vclient, monkeypatch):
    """The clean-up is part of the run: it holds its place in the queue until the last version is stored."""
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "0.5")
    vector = create_vector(vclient, num_images=1)
    picture = create_run(vclient, "waiting", steps=3)
    done_vector, done_picture = wait_for(vclient, vector["id"]), wait_for(vclient, picture["id"])
    assert done_vector["finished_at"] <= done_picture["started_at"]  # the timestamps are text in one format, so they compare as text


# ------------------------------------------------------------------ the payload, against the contract
def test_the_run_payload_has_the_keys_and_types_of_the_contract(vclient):
    body = copy.deepcopy(CONTRACT["create_request"])
    body["options"]["steps"] = 3
    created = vclient.post("/api/runs", json=body).json()
    assert created["vectors"] == [] and created["status"] in ("queued", "running", "done")  # always present, empty until a version is made
    run = wait_for(vclient, created["id"])
    assert walk_types(run) == walk_types(CONTRACT["run"])  # the same paths, and the same kind of value at each
    assert set(run) == set(CONTRACT["run"]) and set(run["vectors"][0]) == set(CONTRACT["run"]["vectors"][0])
    assert list(run["options"]) == list(CONTRACT["run"]["options"])


def test_a_vectors_addresses_are_its_own_and_follow_the_contract(vclient):
    run = make_vector(vclient)
    vector, fixture = run["vectors"][0], CONTRACT["run"]["vectors"][0]
    stand_in = fixture["id"]
    for key in ("url", "download_url", "zip_url", "png_url", "png_download_url"):
        assert vector[key] == fixture[key].replace(stand_in, vector["id"]), key
    # the preview is the stored 1024 px one (the contract's example says 640, which would be drawn on request every time a card is shown)
    assert vector["preview_url"] == f"/api/vectors/{vector['id']}/png?size=1024"
    assert vector["check"] == {**fixture["check"], "size_mm": [100.0, 100.0], "colours_asked": 2, "svg_bytes": vector["bytes"]}


def test_a_picture_run_and_a_music_run_have_no_vectors(vclient):
    picture = wait_for(vclient, create_run(vclient)["id"])
    music = wait_for(vclient, vclient.post("/api/runs", json={"mode": "music", "prompt": "Genre: ambient.", "options": {"duration": 30}}).json()["id"])
    assert picture["vectors"] == [] and music["vectors"] == []
    listed = {run["id"]: run for run in vclient.get("/api/runs").json()["runs"]}
    assert listed[picture["id"]]["vectors"] == [] and listed[music["id"]]["vectors"] == []


def test_the_run_is_told_to_every_page_as_each_version_is_made(vclient):
    sub = subscribe(vclient)
    run = make_vector(vclient, num_images=3)
    events = drain(vclient, sub)
    assert [data["vectors"] for name, data in events if name == "run.created"] == [[]]
    growing = [len(data["vectors"]) for name, data in events if name == "run.updated" and data["id"] == run["id"]]
    assert {1, 2, 3} <= set(growing) and growing == sorted(growing)  # each version arrives as it is made, and the list only grows


# ------------------------------------------------------------------ progress, by stage
def test_progress_says_draw_while_the_model_works_and_then_trace_for_each_version(vclient):
    sub = subscribe(vclient)
    run = make_vector(vclient, num_images=3, steps=4)
    shown = [data["progress"] for name, data in drain(vclient, sub) if name == "run.progress" and data["id"] == run["id"]]
    stages = [progress["stage"] for progress in shown]
    assert set(stages) == {"draw", "trace"}
    assert stages == sorted(stages, key=["draw", "trace"].index)  # every draw before any trace: the stages come in order
    drawing = [progress for progress in shown if progress["stage"] == "draw"]
    assert {progress["image"] for progress in drawing} == {1, 2, 3} and all(progress["of"] == 3 and progress["steps"] == 4 for progress in drawing)
    assert {progress["step"] for progress in drawing} >= {1, 4}
    tracing = [progress for progress in shown if progress["stage"] == "trace"]
    assert tracing == [{"image": n, "of": 3, "step": 0, "steps": 1, "stage": "trace"} for n in (1, 2, 3)]  # one for each version


def test_the_payload_of_a_running_run_carries_its_stage_and_the_versions_made_so_far(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "1.5")
    monkeypatch.setenv("FAKE_VECTOR_SLEEP_ON", "1")  # the first version is quick, the second takes a while
    created = create_vector(vclient, num_images=2)
    running = wait_for_stage(vclient, created["id"], "trace", image=2)
    assert running["status"] == "running" and running["progress"] == {"image": 2, "of": 2, "step": 0, "steps": 1, "stage": "trace"}
    status = vclient.get("/api/status").json()
    assert status["worker"]["state"] == "ready" and status["queue"]["running"] == created["id"]  # the model is idle while the clean-up works; the run is not over
    assert [v["idx"] for v in running["vectors"]] == [0]  # the first version is already there for the card to show
    assert wait_for(vclient, created["id"])["status"] == "done"


def test_a_picture_run_has_no_stage_in_its_progress(vclient):
    sub = subscribe(vclient)
    run = wait_for(vclient, create_run(vclient, steps=3)["id"])
    shown = [data["progress"] for name, data in drain(vclient, sub) if name == "run.progress" and data["id"] == run["id"]]
    assert shown and all("stage" not in progress for progress in shown)


# ------------------------------------------------------------------ a version that cannot be made
def test_a_version_whose_clean_up_fails_is_missing_and_named_and_the_run_keeps_the_rest(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_FAIL", "1")
    run = make_vector(vclient, num_images=3, seed=10)
    assert run["status"] == "done"  # a run that keeps at least one version is done
    assert [v["idx"] for v in run["vectors"]] == [0, 2] and [v["seed"] for v in run["vectors"]] == [10, 12]  # version 2 (idx 1) is the one missing
    assert run["error"] == {"message": "Version 2 could not be made: Nothing was left of drawing 1 after the smallest feature.",
                            "hint": "Make the smallest feature smaller."}  # named, in the job's own words, with its hint
    assert stored_files(vclient, run["id"]) == sorted(["0-layers.zip", "0-source.png", "0.png", "0.svg", "1-source.png", "2-layers.zip", "2-source.png",
                                                       "2.png", "2.svg"])  # nothing of the missing version but its drawing


def test_every_missing_version_is_named_in_one_text(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_FAIL", "0,2")
    run = make_vector(vclient, num_images=3)
    assert run["status"] == "done" and [v["idx"] for v in run["vectors"]] == [1]
    assert run["error"]["message"] == ("Version 1 could not be made: Nothing was left of drawing 0 after the smallest feature. "
                                       "Version 3 could not be made: Nothing was left of drawing 2 after the smallest feature.")


def test_a_run_with_no_version_made_has_failed(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_FAIL", "0,1")
    run = make_vector(vclient, num_images=2)
    assert run["status"] == "failed" and run["vectors"] == [] and run["finished_at"]
    assert "Version 1 could not be made" in run["error"]["message"] and "Version 2 could not be made" in run["error"]["message"]
    assert run["error"]["hint"] == "Make the smallest feature smaller."
    single = make_vector(vclient, num_images=1)  # one version, and it fails: the run has nothing to show
    assert single["status"] == "failed" and single["error"]["message"].startswith("Version 1 could not be made: ")


def test_a_job_that_crashes_is_reported_as_stopping_unexpectedly(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_CRASH", "0")
    run = make_vector(vclient, num_images=2)
    assert run["status"] == "done" and [v["idx"] for v in run["vectors"]] == [1]
    assert run["error"] == {"message": "Version 1 could not be made: The clean-up stopped unexpectedly (exit code 3).", "hint": "See the server log for details."}


def failing_replace(monkeypatch, ends_with: str):
    """Make moving a file whose name ends like this fail with 'disk full' (in the job manager only: its `os` is replaced by one that has just `replace`)."""
    import errno
    from types import SimpleNamespace

    real = os.replace

    def replace(source, target):
        if str(target).endswith(ends_with):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(source, target)

    monkeypatch.setattr("studio.jobs.os", SimpleNamespace(replace=replace))


def test_a_version_that_cannot_be_moved_into_place_leaves_no_part_of_itself_behind(vclient, monkeypatch):
    """The SVG is moved first and the ZIP second: when the ZIP cannot be, the SVG already in place is taken away again."""
    failing_replace(monkeypatch, "-layers.zip")
    run = make_vector(vclient, num_images=1)
    assert run["status"] == "failed" and run["vectors"] == []
    assert run["error"]["message"] == "Version 1 could not be made: The clean-up's files could not be stored: No space left on device"
    assert stored_files(vclient, run["id"]) == ["0-source.png"]


def test_a_drawing_that_cannot_be_stored_fails_the_run_and_starts_no_clean_up(vclient, monkeypatch):
    failing_replace(monkeypatch, "-source.png")
    run = make_vector(vclient, num_images=2)
    assert run["status"] == "failed" and run["vectors"] == []
    assert run["error"] == {"message": "A drawing could not be stored: [Errno 28] No space left on device", "hint": "Check free disk space and the server log."}
    assert job_calls(vclient) == []


@pytest.mark.parametrize("answer, message", [
    ("no-line", vectorrun.UNREADABLE), ("not-json", vectorrun.UNREADABLE), ("no-check", vectorrun.UNREADABLE),
    ("bad-check", "The clean-up gave a check the studio could not read (the check has no colours_found)."),
])
def test_a_job_that_says_something_the_protocol_does_not_allow_makes_no_version(vclient, monkeypatch, answer, message):
    monkeypatch.setenv("FAKE_VECTOR_ANSWER", answer)
    run = make_vector(vclient, num_images=1)
    assert run["status"] == "failed" and run["vectors"] == [] and run["error"]["message"] == f"Version 1 could not be made: {message}"
    assert run["error"]["hint"] == "See the server log for details."
    assert stored_files(vclient, run["id"]) == ["0-source.png"]  # no half-stored version, and no working folder left behind


@pytest.mark.parametrize("missing", ["combined.svg", "layers.zip", "preview.png"])
def test_a_version_is_stored_whole_or_not_at_all(vclient, monkeypatch, missing):
    """A job that says it is done but did not write a file: nothing of that version is kept, so there is no SVG without its ZIP or preview."""
    monkeypatch.setenv("FAKE_VECTOR_SKIP", missing)
    run = make_vector(vclient, num_images=1)
    assert run["status"] == "failed" and run["vectors"] == []
    assert run["error"]["message"] == f"Version 1 could not be made: The clean-up's files could not be stored: the clean-up did not write {missing}"
    assert stored_files(vclient, run["id"]) == ["0-source.png"]


def test_a_clean_up_that_cannot_be_started_fails_its_versions(vclient_factory):
    client = vclient_factory(vector_job_command=("/no/such/program",))
    run = make_vector(client, num_images=1)
    assert run["status"] == "failed" and run["vectors"] == []
    assert run["error"]["message"].startswith("Version 1 could not be made: The clean-up could not be started: ")
    assert run["error"]["hint"] == "See the server log for details."


def test_a_clean_up_that_takes_too_long_is_stopped_and_its_version_is_missing(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "30")
    vclient.app.state.jobs.vector_job_timeout = 0.5
    started = time.monotonic()
    run = make_vector(vclient, num_images=1)
    assert time.monotonic() - started < 15  # not the 30 seconds the job wanted: it was killed
    assert run["status"] == "failed" and run["error"]["message"].startswith("Version 1 could not be made: The clean-up took longer than ")
    assert process_is_gone(job_calls(vclient)[0]["pid"])


# ------------------------------------------------------------------ the model failing
def test_a_run_fails_as_a_whole_when_the_model_does_and_no_clean_up_is_started(vclient):
    run = make_vector(vclient, recipe="a fox [fake:error]")  # the recipe is sent to the model as the page edited it, so the fake can be told to fail
    assert run["status"] == "failed" and run["vectors"] == [] and "Simulated pipeline failure" in run["error"]["message"]
    assert job_calls(vclient) == []


def test_a_drawing_made_before_the_model_failed_is_not_cleaned_up(vclient):
    """The run failed as a whole (DESIGN.md §31.12.5): the first drawing is kept on disk with the run, and no version is made of it."""
    run = make_vector(vclient, recipe="a fox [fake:error@1]", num_images=2)
    assert run["status"] == "failed" and run["vectors"] == [] and run["error"]["message"] == "Simulated pipeline failure ([fake:error])."
    assert job_calls(vclient) == [] and stored_files(vclient, run["id"]) == ["0-source.png"]


def test_a_worker_that_dies_fails_the_run_and_the_next_vector_run_works(vclient):
    run = make_vector(vclient, recipe="a fox [fake:crash]")
    assert run["status"] == "failed" and "worker stopped unexpectedly" in run["error"]["message"]
    again = make_vector(vclient)
    assert again["status"] == "done" and len(again["vectors"]) == 1


# ------------------------------------------------------------------ cancel
def test_cancel_in_the_draw_stage_stops_the_model_and_starts_no_clean_up(vclient_factory):
    client = vclient_factory(fake_step_delay_ms=40)
    run = create_vector(client, steps=60, num_images=2)  # a few seconds of drawing
    wait_for_stage(client, run["id"], "draw")
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    final = wait_for(client, run["id"], timeout=10)
    assert final["status"] == "canceled" and final["vectors"] == [] and final["progress"] is None
    assert job_calls(client) == []
    assert client.app.state.jobs._cancel_requested == set()  # nothing left behind


def test_cancel_in_the_trace_stage_kills_the_clean_up_at_once(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "30")
    run = create_vector(vclient, num_images=2)
    wait_for_stage(vclient, run["id"], "trace")
    pid = wait_for_job_calls(vclient, 1)[0]["pid"]  # the job logs as it starts: it is running now
    sent = time.monotonic()
    assert vclient.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    final = wait_for(vclient, run["id"], timeout=10)
    assert time.monotonic() - sent < 5  # not the 30 seconds the job wanted
    assert final["status"] == "canceled" and final["vectors"] == [] and final["error"] is None
    assert len(job_calls(vclient)) == 1  # the second version was never started
    assert process_is_gone(pid)
    assert stored_files(vclient, run["id"]) == ["0-source.png", "1-source.png"]  # the job's folder went with it
    jobs = vclient.app.state.jobs
    assert jobs._cancel_requested == set() and jobs._tracing is None


def test_cancel_in_the_trace_stage_keeps_the_versions_already_made(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "30")
    monkeypatch.setenv("FAKE_VECTOR_SLEEP_ON", "1")  # version 1 is quick, version 2 takes long
    run = create_vector(vclient, num_images=3)
    wait_for_stage(vclient, run["id"], "trace", image=2)
    wait_for_job_calls(vclient, 2)  # version 2 is running
    assert vclient.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    final = wait_for(vclient, run["id"], timeout=10)
    assert final["status"] == "canceled" and [v["idx"] for v in final["vectors"]] == [0]  # the finished one stays, as a finished picture does
    assert [call["version"] for call in job_calls(vclient)] == ["0", "1"]  # and the third was never started


def test_a_cancel_that_lands_before_the_clean_up_process_exists_still_stops_it(vclient, monkeypatch):
    """The window between "the trace stage began" and "the process is registered" is short but real: the folder is made in a thread first. A cancel in
    it finds nothing to kill, so the process is killed the moment it is registered, and the job (which would sleep 30 seconds) never gets on."""
    from studio.jobs import JobManager

    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "30")
    real = JobManager._prepare_work

    def slow_prepare(work, options):
        time.sleep(0.6)  # long enough for the cancel below to land while the folder is being made
        return real(work, options)

    monkeypatch.setattr(JobManager, "_prepare_work", staticmethod(slow_prepare))
    run = create_vector(vclient, num_images=1)
    wait_for_stage(vclient, run["id"], "trace")
    sent = time.monotonic()
    assert vclient.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    final = wait_for(vclient, run["id"], timeout=10)
    assert final["status"] == "canceled" and final["vectors"] == [] and time.monotonic() - sent < 5
    calls = job_calls(vclient)
    assert all(process_is_gone(call["pid"]) for call in calls)  # if it started at all, it was killed at once


def test_a_cancel_that_lands_between_the_drawing_and_the_first_clean_up_starts_none(vclient, monkeypatch):
    """After the last drawing and before the first job there is a moment (the image folder is tidied in a thread). A Cancel in it ends the run as
    canceled without starting a clean-up at all."""
    from studio import vectorrun as vectorrun_module
    from studio.jobs import JobManager

    started = []
    real_run_job = vectorrun_module.run_job

    async def watching(*args, **kwargs):
        started.append(True)
        return await real_run_job(*args, **kwargs)

    monkeypatch.setattr("studio.jobs.vectorrun.run_job", watching)
    real = JobManager._remove_empty_folder
    inside = threading.Event()

    def slow(folder):
        inside.set()  # the worker is done and the tidy-up has begun
        time.sleep(0.6)
        real(folder)

    monkeypatch.setattr(JobManager, "_remove_empty_folder", staticmethod(slow))
    run = create_vector(vclient, num_images=2)
    assert inside.wait(20)
    assert vclient.post(f"/api/runs/{run['id']}/cancel").status_code == 202
    final = wait_for(vclient, run["id"], timeout=10)
    assert final["status"] == "canceled" and final["vectors"] == [] and started == []


def test_the_queue_carries_on_after_a_cancel_in_the_trace_stage(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "30")
    run = create_vector(vclient, num_images=1)
    wait_for_stage(vclient, run["id"], "trace")
    wait_for_job_calls(vclient, 1)
    vclient.post(f"/api/runs/{run['id']}/cancel")
    assert wait_for(vclient, run["id"], timeout=10)["status"] == "canceled"
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "0")
    assert wait_for(vclient, create_vector(vclient)["id"])["status"] == "done"  # the same worker, the same server: nothing was left stuck


def test_a_cancel_after_the_run_has_finished_is_refused_as_for_any_run(vclient):
    run = make_vector(vclient)
    assert vclient.post(f"/api/runs/{run['id']}/cancel").status_code == 409


def test_stopping_the_server_kills_a_clean_up_that_is_running(vclient, monkeypatch):
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "30")
    run = create_vector(vclient, num_images=1)
    wait_for_stage(vclient, run["id"], "trace")
    pid = wait_for_job_calls(vclient, 1)[0]["pid"]
    close(vclient)  # the server shuts down in the middle of a clean-up
    assert process_is_gone(pid)


# ------------------------------------------------------------------ Reuse, Retry, Keep and projects
def test_reuse_and_retry_send_the_stored_options_back_and_get_the_same_drawings(vclient):
    first = make_vector(vclient, "a fox head", seed=42, num_images=2, colours=4, palette=["#FF0000"], kind="flat", width_mm=200, height_mm=100,
                        layers_by="shape", arrangement="stacked", bleed_mm=1.5, min_feature_mm=0.2, kerf_mm=0.1)
    request_keys = CONTRACT["create_request"]["options"]
    sent_back = {key: value for key, value in first["options"].items() if key in request_keys}  # what the page restores into the form and sends
    again = make_vector(vclient, first["prompt"], **{**sent_back, "steps": first["options"]["steps"]})
    assert again["options"] == first["options"] and again["effective_prompt"] == first["effective_prompt"]
    calls = job_calls(vclient)
    assert [call["sha1"] for call in calls[:2]] == [call["sha1"] for call in calls[2:]]  # the same seeds draw the same pictures


def test_keep_and_projects_work_for_a_vector_run(vclient):
    run = make_vector(vclient)
    kept = vclient.patch(f"/api/runs/{run['id']}", json={"pinned": True}).json()
    assert kept["pinned"] is True and kept["expires_at"] is None and len(kept["vectors"]) == 1
    project = vclient.post("/api/projects", json={"name": "Laser jobs"}).json()
    assert project["counts"] == {"image": 0, "music": 0, "vector": 0}
    filed = vclient.put(f"/api/runs/{run['id']}/project", json={"project_id": project["id"]})
    assert filed.status_code == 200 and filed.json()["project_id"] == project["id"]
    assert vclient.get("/api/projects").json()["projects"][0]["counts"] == {"image": 0, "music": 0, "vector": 1}
    assert vclient.patch(f"/api/runs/{run['id']}", json={"pinned": False}).status_code == 409  # a filed run stays kept
    assert [r["id"] for r in vclient.get("/api/runs", params={"project": project["id"], "kind": "vector"}).json()["runs"]] == [run["id"]]
    assert vclient.get("/api/runs", params={"project": project["id"], "kind": "image"}).json()["runs"] == []
    assert vclient.delete(f"/api/projects/{project['id']}").json() == {"unfiled": 1}
    after = vclient.get(f"/api/runs/{run['id']}").json()
    assert after["project_id"] is None and after["pinned"] is True


# ------------------------------------------------------------------ the tab: its list and its counts
def test_the_list_can_be_narrowed_to_one_tab_and_the_counts_have_three(vclient):
    picture = wait_for(vclient, create_run(vclient)["id"])
    music = wait_for(vclient, vclient.post("/api/runs", json={"mode": "music", "prompt": "Genre: ambient.", "options": {"duration": 30}}).json()["id"])
    first, second = make_vector(vclient, "one"), make_vector(vclient, "two")
    ids = lambda **params: [r["id"] for r in vclient.get("/api/runs", params=params).json()["runs"]]  # noqa: E731
    assert ids(kind="vector") == [second["id"], first["id"]] and ids(kind="image") == [picture["id"]] and ids(kind="music") == [music["id"]]
    assert len(ids()) == 4  # no kind: every tab, as before
    assert vclient.get("/api/runs", params={"kind": "sound"}).status_code == 422
    page = vclient.get("/api/runs", params={"kind": "vector", "limit": 1}).json()  # and it pages within the tab
    assert [r["id"] for r in page["runs"]] == [second["id"]] and page["next_before"] == second["id"]
    assert ids(kind="vector", before=page["next_before"]) == [first["id"]]
    vclient.patch(f"/api/runs/{first['id']}", json={"pinned": True})
    assert ids(kind="vector", kept="true") == [first["id"]] and ids(kind="vector", kept="false") == [second["id"]]
    counts = vclient.get("/api/runs/counts").json()
    assert counts["vector"] == {"all": 2, "kept": 1, "deleted": 0, "deleted_items": 0}
    assert counts["image"]["all"] == 1 and counts["music"]["all"] == 1
    vclient.post(f"/api/runs/{second['id']}/bin")
    counts = vclient.get("/api/runs/counts").json()
    assert counts["vector"] == {"all": 1, "kept": 1, "deleted": 1, "deleted_items": 0}  # a run in the bin is one thing in it, as for the other tabs
    assert ids(kind="vector", deleted="true") == [second["id"]] and ids(kind="vector") == [first["id"]]


# ------------------------------------------------------------------ the bin, delete and the clean-up of old runs
def test_a_vector_run_goes_to_the_bin_whole_with_its_files_and_comes_back(vclient):
    run = make_vector(vclient, num_images=2)
    files = stored_files(vclient, run["id"])
    binned = vclient.post(f"/api/runs/{run['id']}/bin").json()
    assert binned["deleted_at"] is not None and binned["purge_at"] is not None and len(binned["vectors"]) == 2  # the bin keeps what the run had
    assert stored_files(vclient, run["id"]) == files  # the bin moves nothing
    assert vclient.get(binned["vectors"][0]["url"]).status_code == 200  # and its files are still served
    restored = vclient.post(f"/api/runs/{run['id']}/restore").json()
    assert restored["deleted_at"] is None and [v["id"] for v in restored["vectors"]] == [v["id"] for v in run["vectors"]]
    assert stored_files(vclient, run["id"]) == files


def test_there_is_no_bin_for_a_single_version(vclient):
    """A vector run is deleted and restored as a whole (§31.12.9 item 3): the picture routes do not reach a version."""
    run = make_vector(vclient, num_images=2)
    vid = run["vectors"][0]["id"]
    assert vclient.post(f"/api/images/{vid}/bin").status_code == 404 and vclient.delete(f"/api/vectors/{vid}").status_code in (404, 405)
    assert len(vclient.get(f"/api/runs/{run['id']}").json()["vectors"]) == 2


def test_delete_for_good_removes_the_rows_and_the_whole_folder(vclient):
    run = make_vector(vclient, num_images=2)
    vector_ids = [v["id"] for v in run["vectors"]]
    folder = vclient.app.state.storage.vector_dir(run["id"])
    assert folder.is_dir()
    sub = subscribe(vclient)
    assert vclient.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not folder.exists() and vclient.get(f"/api/runs/{run['id']}").status_code == 404
    assert vclient.app.state.db._one("SELECT COUNT(*) AS n FROM vectors")["n"] == 0
    for vid in vector_ids:
        assert vclient.get(f"/api/vectors/{vid}/svg").status_code == 404
    assert [data for name, data in drain(vclient, sub) if name == "run.deleted"] == [{"id": run["id"]}]


def test_delete_for_good_works_from_the_bin_too(vclient):
    run = make_vector(vclient)
    vclient.post(f"/api/runs/{run['id']}/bin")
    assert vclient.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not vclient.app.state.storage.vector_dir(run["id"]).exists()


def test_emptying_the_bin_removes_the_folders_of_vector_runs_and_only_theirs(vclient):
    gone, binned_picture, kept = make_vector(vclient, "one"), wait_for(vclient, create_run(vclient)["id"]), make_vector(vclient, "two")
    other = make_vector(vclient, "three")
    for run in (gone, binned_picture):
        vclient.post(f"/api/runs/{run['id']}/bin")
    assert vclient.delete("/api/bin").json() == {"deleted": 2, "pictures": 0}
    storage = vclient.app.state.storage
    assert not storage.vector_dir(gone["id"]).exists()
    assert storage.vector_dir(kept["id"]).is_dir() and storage.vector_dir(other["id"]).is_dir()  # runs that were not in the bin are untouched
    assert vclient.get(kept["vectors"][0]["url"]).status_code == 200


def seed_vector_run(client, number: int, *, age_days: float, pinned: bool = False, binned_days_ago: float | None = None) -> str:
    """A finished vector run of a given age with one version, its rows and its files, as a real one has (made straight in the database and on disk)."""
    from datetime import datetime, timedelta, timezone

    from studio.serialize import format_ts

    def ago(days: float) -> str:
        return format_ts(datetime.now(timezone.utc) - timedelta(days=days))

    db, storage = client.app.state.db, client.app.state.storage
    run_id = f"{number:032x}"
    assert db.insert_run_if_capacity({
        "id": run_id, "created_at": ago(age_days), "status": "queued", "mode": "vector", "prompt": f"seed {number}", "effective_prompt": "x",
        "steps": 3, "seed": number, "num_images": 1, "model_id": "fake-pipeline", "options_json": json.dumps({}), "width": 64, "height": 64,
        "pinned": int(pinned)}, cap=10_000)
    with db.tx() as c:
        c.execute("UPDATE runs SET status='done', started_at=?, finished_at=?, deleted_at=? WHERE id=?",
                  (ago(age_days), ago(age_days), None if binned_days_ago is None else ago(binned_days_ago), run_id))
    folder = storage.vector_dir(run_id)
    folder.mkdir(parents=True)
    for name in ("0-source.png", "0.svg", "0-layers.zip", "0.png"):
        (folder / name).write_bytes(b"x")
    db.add_vector({"id": f"{number + 0x1000:032x}", "run_id": run_id, "idx": 0, "seed": number, "width_mm": 100.0, "height_mm": 100.0, "colours": 2,
                   "layers": 1, "bytes": 1, "check_json": "{}", "svg_path": storage.rel(folder / "0.svg"), "zip_path": storage.rel(folder / "0-layers.zip"),
                   "png_path": storage.rel(folder / "0.png"), "created_at": ago(age_days)})
    return run_id


def sweep(client) -> int:
    return client.portal.call(client.app.state.jobs.sweep_expired)


def test_the_daily_clean_up_bins_an_expired_vector_run_keeping_its_files_and_later_deletes_them_with_it(vclient_factory):
    client = vclient_factory(quiet=True)
    storage = client.app.state.storage
    old, kept, recent = (seed_vector_run(client, 1, age_days=40), seed_vector_run(client, 2, age_days=400, pinned=True), seed_vector_run(client, 3, age_days=2))
    assert sweep(client) == 0  # nothing is deleted for good yet: the expired run only moves to the bin
    assert client.get(f"/api/runs/{old}").json()["deleted_at"] is not None and storage.vector_dir(old).is_dir()  # and its files stay
    assert client.get(f"/api/runs/{kept}").json()["deleted_at"] is None and client.get(f"/api/runs/{recent}").json()["deleted_at"] is None
    # a month later the bin lets go of it: the run, its rows and its folder, and nothing of the others
    db = client.app.state.db
    with db.tx() as c:
        c.execute("UPDATE runs SET deleted_at='2000-01-01T00:00:00.000Z' WHERE id=?", (old,))
    assert sweep(client) == 1
    assert client.get(f"/api/runs/{old}").status_code == 404 and not storage.vector_dir(old).exists()
    assert db._one("SELECT COUNT(*) AS n FROM vectors WHERE run_id=?", (old,))["n"] == 0
    assert storage.vector_dir(kept).is_dir() and storage.vector_dir(recent).is_dir()


def test_with_no_bin_the_daily_clean_up_deletes_an_expired_vector_run_with_its_folder(vclient_factory):
    client = vclient_factory(quiet=True, bin_days=0)
    storage = client.app.state.storage
    expired, recent = seed_vector_run(client, 1, age_days=40), seed_vector_run(client, 2, age_days=2)
    assert sweep(client) == 1
    assert client.get(f"/api/runs/{expired}").status_code == 404 and not storage.vector_dir(expired).exists() and storage.vector_dir(recent).is_dir()


def test_a_vector_run_in_the_bin_past_its_days_is_deleted_by_the_clean_up_even_with_expiry_off(vclient_factory):
    client = vclient_factory(quiet=True, retention_days=0)
    storage = client.app.state.storage
    over, fine = seed_vector_run(client, 1, age_days=1, binned_days_ago=31), seed_vector_run(client, 2, age_days=1, binned_days_ago=1)
    assert sweep(client) == 1
    assert not storage.vector_dir(over).exists() and storage.vector_dir(fine).is_dir()


# ------------------------------------------------------------------ what a vector id is not
def test_the_picture_and_track_routes_refuse_a_vector_id_as_they_refuse_any_unknown_id(vclient):
    run = make_vector(vclient)
    vid = run["vectors"][0]["id"]
    for method, url in (("get", f"/api/images/{vid}"), ("get", f"/api/images/{vid}/thumb"), ("get", f"/api/images/{vid}/4k"),
                        ("get", f"/api/audio/{vid}"), ("post", f"/api/images/{vid}/bin"), ("post", f"/api/images/{vid}/restore"),
                        ("delete", f"/api/images/{vid}"), ("post", f"/api/images/{vid}/4k"), ("post", f"/api/images/{vid}/enlarge"),
                        ("post", f"/api/tracks/{vid}/bin"), ("post", f"/api/tracks/{vid}/restore"), ("delete", f"/api/tracks/{vid}")):
        response = getattr(vclient, method)(url)
        assert response.status_code == 404 and response.json()["code"] == "not_found", (method, url, response.text)
    assert vclient.get(f"/api/images/{NO_SUCH}").json() == vclient.get(f"/api/images/{vid}").json()  # the very answer an id that is not there gets
    assert vclient.get(f"/api/images/{run['id']}").status_code == 404  # nor does the run's own id reach a picture


# ------------------------------------------------------------------ the files on the data volume
def test_a_vector_run_has_a_folder_of_its_own_and_only_a_real_id_makes_one(tmp_path):
    import pytest as _pytest

    from studio.storage import Storage, StorageError

    storage = Storage(tmp_path / "data")
    storage.ensure_layout()
    assert (storage.root / "vector").is_dir()  # made with the rest of the layout, so a run never has to make the top folder
    assert storage.vector_dir("a" * 32) == storage.root / "vector" / ("a" * 32)
    for bad in ("../x", "short", "A" * 32, ""):
        with _pytest.raises(StorageError):
            storage.vector_dir(bad)


def test_deleting_a_runs_files_takes_its_vector_folder_and_leaves_the_others(tmp_path):
    from studio.storage import Storage

    storage = Storage(tmp_path / "data")
    storage.ensure_layout()
    for run_id in ("a" * 32, "b" * 32):
        (storage.vector_dir(run_id)).mkdir(parents=True)
        (storage.vector_dir(run_id) / "0.svg").write_text("x")
    storage.delete_run_files("a" * 32)
    assert not storage.vector_dir("a" * 32).exists() and (storage.vector_dir("b" * 32) / "0.svg").is_file()
