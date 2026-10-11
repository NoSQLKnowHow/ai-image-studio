"""The server's side of the clean-up job (`studio/vectorrun.py`, DESIGN.md §31.12.4) and the fake flat picture a vector run draws with the fake pipeline.

The job itself is the engine's. Here: the command that starts it (and the setting that replaces it, which is how every other test of the server runs a fake),
the reading of its one line of answer, the numbers the server keeps from its check, and the running of it as a process that can be timed out and killed. And the
fake model's flat drawing, which the real clean-up is meant to be able to work on in the end-to-end run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from vector_server_support import FAKE_JOB, fake_job_command, process_is_gone

from studio import vector as V
from studio import vectorrun
from studio.config import ConfigError, Settings
from studio.pipelines.base import ImageJob
from studio.pipelines.fake import FLAT_INKS, FakePipeline, render_flat_image
from studio.vectorrun import CRASHED, NOT_MADE, UNREADABLE, JobResult, job_command, read_answer, run_job, summarize

SOURCE, OPTIONS, OUT = Path("/data/vector/r/0-source.png"), Path("/data/vector/r/work-0/options.json"), Path("/data/vector/r/work-0")
ARGUMENTS = ["--source", str(SOURCE), "--options", str(OPTIONS), "--out", str(OUT)]


# ------------------------------------------------------------------ the command
def test_the_command_is_the_engines_own_job_unless_it_is_replaced():
    assert job_command(Settings(), SOURCE, OPTIONS, OUT) == [sys.executable, "-m", "studio.vector.job", *ARGUMENTS]  # §31.12.4, to the letter


def test_a_replaced_command_gets_the_same_three_arguments():
    settings = Settings(vector_job_command=("python3", "/tests/fake_job.py", "--flag"))
    assert job_command(settings, SOURCE, OPTIONS, OUT) == ["python3", "/tests/fake_job.py", "--flag", *ARGUMENTS]


def test_the_setting_is_read_from_the_environment_like_a_command_line():
    assert Settings.from_env({}).vector_job_command == ()
    assert Settings.from_env({"STUDIO_VECTOR_JOB_COMMAND": ""}).vector_job_command == ()
    assert Settings.from_env({"STUDIO_VECTOR_JOB_COMMAND": "  "}).vector_job_command == ()
    assert Settings.from_env({"STUDIO_VECTOR_JOB_COMMAND": "python3 '/my tests/job.py' -v"}).vector_job_command == ("python3", "/my tests/job.py", "-v")
    with pytest.raises(ConfigError, match="STUDIO_VECTOR_JOB_COMMAND is not a command line"):
        Settings.from_env({"STUDIO_VECTOR_JOB_COMMAND": "python3 'unclosed"})


def test_the_job_runs_with_the_studios_folder_on_its_python_path(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    env = vectorrun.job_environment()
    folders = env["PYTHONPATH"].split(":")
    assert folders[0].endswith("backend") and (Path(folders[0]) / "studio" / "vectorrun.py").is_file() and folders[1:] == ["/somewhere/else"]
    assert env["PYTHONUNBUFFERED"] == "1"
    monkeypatch.delenv("PYTHONPATH")
    assert vectorrun.job_environment()["PYTHONPATH"] == folders[0]  # and no empty entry when there was none


# ------------------------------------------------------------------ reading its answer
CHECK = {"colours_found": 3, "layers": [{}, {}], "size_mm": [100, 50]}


@pytest.mark.parametrize("out", [
    json.dumps({"ok": True, "check": CHECK}),
    "chatter first\nmore chatter\n" + json.dumps({"ok": True, "check": CHECK}),  # only the last line is the answer
    json.dumps({"ok": True, "check": CHECK}) + "\n\n  \n",  # and blank lines after it are not
])
def test_a_job_that_made_the_files_says_ok_with_its_check(out):
    result = read_answer(0, out)
    assert result == JobResult(check=CHECK) and result.ok and result.message is None


@pytest.mark.parametrize("answer, message, hint", [
    ({"ok": False, "error": {"message": "Nothing was left.", "hint": "Smaller features."}}, "Nothing was left.", "Smaller features."),
    ({"ok": False, "error": {"message": "Nothing was left.", "hint": None}}, "Nothing was left.", None),
    ({"ok": False, "error": {"message": "Nothing was left."}}, "Nothing was left.", None),
    ({"ok": False, "error": {"message": "Nothing was left.", "hint": ""}}, "Nothing was left.", None),
    ({"ok": False, "error": {"message": "Nothing was left.", "hint": 5}}, "Nothing was left.", None),
    ({"ok": False, "error": {"message": "   ", "hint": "h"}}, NOT_MADE, "h"),  # a failure with no words gets ours
    ({"ok": False, "error": {"message": 7}}, NOT_MADE, None),
    ({"ok": False, "error": "text"}, NOT_MADE, None),
    ({"ok": False}, NOT_MADE, None),
])
def test_a_job_that_knows_it_failed_is_shown_in_its_own_words(answer, message, hint):
    result = read_answer(0, json.dumps(answer))
    assert result == JobResult(message=message, hint=hint) and not result.ok


@pytest.mark.parametrize("out", ["", "\n\n", "done, thank you", "[1, 2]", '"ok"', "{}", '{"ok": "yes"}', '{"ok": 1}', '{"ok": null}',
                                 '{"ok": true}', '{"ok": true, "check": []}', '{"ok": true, "check": "fine"}', '{"ok": true, "check": null}'])
def test_an_answer_the_protocol_does_not_allow_is_unreadable_not_a_success(out):
    assert read_answer(0, out) == JobResult(message=UNREADABLE, hint=vectorrun.SEE_THE_LOG)


@pytest.mark.parametrize("code", [1, 2, 3, 139, -9, -11, None])
def test_any_exit_but_zero_is_a_crash_whatever_it_printed(code):
    """Even when it printed a perfectly good answer first: a process that did not exit cleanly is not to be believed."""
    for out in ("", json.dumps({"ok": True, "check": CHECK}), json.dumps({"ok": False, "error": {"message": "m"}})):
        assert read_answer(code, out) == JobResult(message=CRASHED.format(code=code), hint=vectorrun.SEE_THE_LOG)
    assert CRASHED.format(code=3) == "The clean-up stopped unexpectedly (exit code 3)."


def test_what_a_crashed_job_wrote_is_in_the_log_and_not_shown(caplog):
    with caplog.at_level(logging.WARNING, logger="studio.vectorrun"):
        result = read_answer(1, "", "line 1\nline 2\nTraceback ...\nValueError: the real reason")
    assert "the real reason" not in (result.message or "") and "the real reason" in caplog.text and "exit" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="studio.vectorrun"):
        read_answer(0, "gibberish", "")
    assert "gibberish" in caplog.text


def test_only_the_last_lines_of_a_long_output_are_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="studio.vectorrun"):
        read_answer(1, "", "\n".join(f"line {n}" for n in range(100)))
    assert "line 99" in caplog.text and "line 92" in caplog.text and "line 91" not in caplog.text  # the last eight


# ------------------------------------------------------------------ the numbers kept from a check
def test_the_numbers_of_a_check_are_the_columns_of_the_row():
    summary = summarize({"colours_found": 4, "layers": [{}, {}, {}], "size_mm": [152.4, 100]})
    assert (summary.colours, summary.layers, summary.width_mm, summary.height_mm) == (4, 3, 152.4, 100.0)
    assert type(summary.height_mm) is float and type(summary.width_mm) is float  # a whole number of millimetres is still a real number
    assert summarize({"colours_found": 2, "layers": [], "size_mm": [5, 5]}).layers == 0  # a picture with only white has no layers: still a check


@pytest.mark.parametrize("check, reason", [
    ({"layers": [], "size_mm": [1, 2]}, "colours_found"), ({"colours_found": "4", "layers": [], "size_mm": [1, 2]}, "colours_found"),
    ({"colours_found": 4.0, "layers": [], "size_mm": [1, 2]}, "colours_found"), ({"colours_found": True, "layers": [], "size_mm": [1, 2]}, "colours_found"),
    ({"colours_found": 4, "size_mm": [1, 2]}, "layers"), ({"colours_found": 4, "layers": "3", "size_mm": [1, 2]}, "layers"),
    ({"colours_found": 4, "layers": {}, "size_mm": [1, 2]}, "layers"),
    ({"colours_found": 4, "layers": []}, "size_mm"), ({"colours_found": 4, "layers": [], "size_mm": [1]}, "size_mm"),
    ({"colours_found": 4, "layers": [], "size_mm": [1, 2, 3]}, "size_mm"), ({"colours_found": 4, "layers": [], "size_mm": ["1", 2]}, "size_mm"),
    ({"colours_found": 4, "layers": [], "size_mm": [True, 2]}, "size_mm"), ({"colours_found": 4, "layers": [], "size_mm": 7}, "size_mm"),
    ({"colours_found": 4, "layers": [], "size_mm": {"w": 1}}, "size_mm"),
])
def test_a_check_without_what_the_contract_says_is_refused_before_anything_is_stored(check, reason):
    with pytest.raises(ValueError, match=reason):
        summarize(check)


# ------------------------------------------------------------------ running it as a process
@pytest.fixture
def files(tmp_path, monkeypatch):
    """A drawing, an options file and an output folder for the fake job, and the fake job as the command. Returns (settings, source, options, out)."""
    source, options, out = tmp_path / "0-source.png", tmp_path / "options.json", tmp_path / "out"
    Image.new("RGB", (8, 8), (255, 255, 255)).save(source)
    options.write_text(json.dumps(V.VectorOptions(width_mm=80.0, height_mm=40.0, colours=3).to_json()))
    return Settings(vector_job_command=fake_job_command()), source, options, out


def test_a_job_is_run_and_its_answer_read(files):
    settings, source, options, out = files
    result = asyncio.run(run_job(settings, source, options, out))
    assert result.ok and result.check["size_mm"] == [80.0, 40.0] and result.check["colours_asked"] == 3
    assert sorted(path.name for path in out.iterdir()) == ["check.json", "combined.svg", "layers.zip", "preview.png"]  # the files of §31.12.4
    assert json.loads((out / "check.json").read_text()) == result.check  # the check in the file is the one on the line


def test_the_process_is_handed_over_as_it_starts_so_that_cancel_can_reach_it(files):
    settings, source, options, out = files
    seen = []
    asyncio.run(run_job(settings, source, options, out, on_start=lambda proc: seen.append((proc.pid, proc.returncode))))
    assert len(seen) == 1 and seen[0][0] > 0 and seen[0][1] is None  # a real process, and still running when it was handed over


def test_a_job_that_fails_cleanly_or_crashes_is_read_as_such(files, monkeypatch):
    settings, source, options, out = files
    monkeypatch.setenv("FAKE_VECTOR_FAIL", "0")
    assert asyncio.run(run_job(settings, source, options, out)) == JobResult(
        message="Nothing was left of drawing 0 after the smallest feature.", hint="Make the smallest feature smaller.")
    monkeypatch.delenv("FAKE_VECTOR_FAIL")
    monkeypatch.setenv("FAKE_VECTOR_CRASH", "0")
    assert asyncio.run(run_job(settings, source, options, out)) == JobResult(message=CRASHED.format(code=3), hint=vectorrun.SEE_THE_LOG)


def test_a_job_that_takes_too_long_is_killed(files, monkeypatch):
    settings, source, options, out = files
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "30")
    monkeypatch.setenv("FAKE_VECTOR_LOG", str(out.parent / "log"))
    started = time.monotonic()
    result = asyncio.run(run_job(settings, source, options, out, timeout=0.6))
    assert time.monotonic() - started < 15
    assert result == JobResult(message="The clean-up took longer than 0.01 minutes and was stopped.", hint=vectorrun.SEE_THE_LOG)
    assert process_is_gone(json.loads((out.parent / "log").read_text().splitlines()[0])["pid"])


def test_the_default_timeout_is_ten_minutes_and_shown_in_minutes():
    assert vectorrun.JOB_TIMEOUT_SECONDS == 600
    assert vectorrun.TIMED_OUT.format(minutes=600 / 60) == "The clean-up took longer than 10 minutes and was stopped."


def test_a_job_that_is_cancelled_with_its_task_is_killed_and_the_cancellation_goes_on(files, monkeypatch):
    settings, source, options, out = files
    monkeypatch.setenv("FAKE_VECTOR_SLEEP", "30")
    monkeypatch.setenv("FAKE_VECTOR_LOG", str(out.parent / "log"))

    async def scenario():
        task = asyncio.ensure_future(run_job(settings, source, options, out))
        for _ in range(500):  # until the job has started and logged
            if (out.parent / "log").exists():
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert process_is_gone(json.loads((out.parent / "log").read_text().splitlines()[0])["pid"])


def test_a_command_that_is_not_there_is_an_oserror_for_the_caller_to_report(files):
    settings, source, options, out = files
    with pytest.raises(OSError):
        asyncio.run(run_job(Settings(vector_job_command=("/no/such/program",)), source, options, out))


def test_the_fake_job_is_where_the_tests_say_it_is():
    assert FAKE_JOB.is_file() and fake_job_command() == (sys.executable, str(FAKE_JOB))


# ------------------------------------------------------------------ the fake model's flat drawing
def test_a_flat_drawing_is_white_with_a_few_solid_inks_and_nothing_else():
    for seed in range(40):
        picture = render_flat_image(width=512, height=384, seed=seed)
        assert picture.size == (512, 384) and picture.mode == "RGB"
        colours = {colour for _, colour in picture.getcolors(maxcolors=64)}  # more than 64 would mean smoothing or a gradient
        assert (255, 255, 255) in colours and 2 <= len(colours) <= 4 and colours <= {(255, 255, 255), *FLAT_INKS}, seed


def test_a_flat_drawing_leaves_the_edge_bare_and_is_mostly_white():
    picture = render_flat_image(width=600, height=400, seed=3)
    margin = 400 // 10
    for x in range(600):
        for y in (0, 399):
            assert picture.getpixel((x, y)) == (255, 255, 255)
    for y in range(400):
        for x in (0, 599):
            assert picture.getpixel((x, y)) == (255, 255, 255)
    white = dict((colour, n) for n, colour in picture.getcolors(maxcolors=64))[(255, 255, 255)]
    assert margin == 40 and 0.3 < white / (600 * 400) < 1.0  # the shapes are a part of the sheet, not all of it


def test_the_same_seed_draws_the_same_picture_and_another_seed_another():
    first = render_flat_image(width=300, height=300, seed=11)
    assert first.tobytes() == render_flat_image(width=300, height=300, seed=11).tobytes()
    assert len({render_flat_image(width=300, height=300, seed=seed).tobytes() for seed in range(10)}) == 10


@pytest.mark.parametrize("size", [(16, 16), (64, 64), (256, 1536), (1536, 256), (1536, 1536), (1, 1)])
def test_a_flat_drawing_is_made_at_any_size_the_run_may_ask_for(size):
    assert render_flat_image(width=size[0], height=size[1], seed=1).size == size


def job(mode: str, **changes) -> ImageJob:
    fields = dict(run_id="0" * 32, mode=mode, prompt="a fox", negative_prompt=None, width=256, height=128, steps=2, cfg_scale=4.0, seeds=[5, 6])
    fields.update(changes)
    return ImageJob(**fields)


def test_the_fake_pipeline_draws_flat_for_a_vector_job_and_as_before_for_the_others():
    pipeline = FakePipeline(step_delay_ms=0, load_delay_ms=0)
    steps = []
    vector = pipeline.generate(job("vector"), 0, 5, lambda step, total: steps.append((step, total)))
    assert vector.size == (256, 128) and vector.tobytes() == render_flat_image(width=256, height=128, seed=5).tobytes() and steps == [(1, 2), (2, 2)]
    picture = pipeline.generate(job("generate"), 0, 5, lambda step, total: None)
    assert picture.getcolors(maxcolors=100_000) is None or len(picture.getcolors(maxcolors=100_000)) > 64  # the test picture has a gradient: not flat


def test_a_vector_job_still_honours_the_fakes_directives_and_cancel():
    from studio.pipelines.base import Canceled, PipelineError

    pipeline = FakePipeline(step_delay_ms=0, load_delay_ms=0)
    with pytest.raises(PipelineError, match="Simulated pipeline failure"):
        pipeline.generate(job("vector", prompt="a fox [fake:error]"), 0, 5, lambda step, total: None)

    def cancel(step, total):
        raise Canceled()

    with pytest.raises(Canceled):
        pipeline.generate(job("vector"), 0, 5, cancel)
