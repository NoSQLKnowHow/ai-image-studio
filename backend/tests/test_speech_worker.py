"""The speech kind of the GPU worker (DESIGN.md §38.3, §38.6): the job's checks, the pieces it writes, Cancel, failures, and the process itself.

The worker is driven in this process where that is enough (fast, and the events can be inspected) and as a real subprocess where the protocol itself
is the thing being checked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from speech_support import OTHER_RUN_ID, RUN_ID, in_process_worker, piece, speech_job

from studio import wavfile
from studio.speech.job import SpeechJob

BACKEND = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- the job's checks
def test_a_good_job_is_read_back_as_it_was_sent(tmp_path):
    job = SpeechJob.from_dict(speech_job(style="calm", language="English"), tmp_path)
    assert job.run_id == RUN_ID and job.engine == "fake" and job.variant == "default" and job.voice == "anna"
    assert job.style == "calm" and job.language == "English" and [p.key for p in job.pieces] == ["0-0", "0-1"]


@pytest.mark.parametrize("change, why", [
    (dict(run_id="nope"), "bad run_id"),
    (dict(task="dance"), "unknown task"),
    (dict(engine=""), "engine and variant"),
    (dict(variant=None), "engine and variant"),
    (dict(pieces=[]), "between 1 and"),
    (dict(pieces="text"), "between 1 and"),
    (dict(pieces=[piece(0), piece(0)]), "own key"),
    (dict(pieces=[{**piece(0), "text": "   "}]), "some text"),
    (dict(pieces=[{**piece(0), "text": "x" * 20_001}]), "some text"),
    (dict(pieces=[{**piece(0), "kind": "poem"}]), "unknown kind"),
    (dict(pieces=[{**piece(0), "pause_ms": -1}]), "pause"),
    (dict(pieces=[{**piece(0), "pause_ms": 10_001}]), "pause"),
    (dict(pieces=[{**piece(0), "seed": "1"}]), "seed"),
    (dict(pieces=[{**piece(0), "seed": True}]), "seed"),
    (dict(pieces=[{**piece(0), "out": "speech/readings/x/0.mp3"}]), ".wav"),
    (dict(pieces=[{**piece(0), "out": "../escape.wav"}]), "not inside the speech folder"),
    (dict(pieces=[{**piece(0), "out": "speech/../images/escape.wav"}]), "not inside the speech folder"),
    (dict(pieces=[{**piece(0), "out": "/etc/escape.wav"}]), "not inside the speech folder"),
    (dict(pieces=[{**piece(0), "out": "audio/track.wav"}]), "not inside the speech folder"),
    (dict(voice=""), "needs a voice"),
    (dict(voice=7), "voice"),
    (dict(style=5), "style"),
    (dict(language=["English"]), "language"),
    (dict(task="design"), "description"),
])
def test_a_bad_job_is_refused_with_a_sentence_that_says_what_is_wrong(tmp_path, change, why):
    with pytest.raises(ValueError) as caught:
        SpeechJob.from_dict(speech_job(**change), tmp_path)
    assert why in str(caught.value)


def test_a_saved_voice_must_be_a_file_inside_the_voices_folder(tmp_path):
    (tmp_path / "speech" / "voices" / "v1").mkdir(parents=True)
    clip = tmp_path / "speech" / "voices" / "v1" / "reference.wav"
    wavfile.write_wav(clip, bytes(1000), 24000, 1)
    ok = SpeechJob.from_dict(speech_job(voice="", ref={"path": "speech/voices/v1/reference.wav", "text": "words", "kind": "cloned"}), tmp_path)
    assert ok.ref is not None and ok.ref.text == "words" and Path(ok.ref.path) == clip.resolve()
    other = tmp_path / "speech" / "readings" / "x.wav"
    wavfile.write_wav(other, bytes(1000), 24000, 1)
    for ref in ({"path": "speech/readings/x.wav", "text": "w"}, {"path": "speech/voices/v1/missing.wav", "text": "w"},
                {"path": "../../etc/passwd", "text": "w"}, {"path": "speech/voices/v1/reference.wav"}, "not an object"):
        with pytest.raises(ValueError):
            SpeechJob.from_dict(speech_job(voice="", ref=ref), tmp_path)


def test_a_design_job_needs_a_description_and_no_voice(tmp_path):
    job = SpeechJob.from_dict(speech_job(task="design", voice="", design="warm and low", variant="design"), tmp_path)
    assert job.design == "warm and low" and job.voice == "" and job.ref is None


# ---------------------------------------------------------------- the pieces it writes
def test_every_piece_is_written_as_a_mono_wav_with_its_pause_in_it_and_reported(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    worker.run(speech_job([piece(0, "First paragraph of the reading.", pause_ms=450), piece(1, "Chapter Two", kind="heading", pause_ms=900)],
                          style="calm"))
    # loading and ready, then the job: each piece is announced and then reported
    assert emit.kinds() == ["state", "state", "run_started", "progress", "piece_done", "progress", "piece_done", "run_finished"]
    done = emit.of("piece_done")
    assert [d["key"] for d in done] == ["0-0", "0-1"] and emit.of("run_finished")[0]["completed"] == 2
    for reported in done:
        info = wavfile.read_wav(tmp_path / reported["path"])
        assert info.channels == 1 and info.sample_rate == 24000 and info.frames == reported["frames"]
        assert reported["seconds"] == pytest.approx(info.frames / 24000, abs=0.001)
    # the pause is part of the audio: speaking the same text with no pause is exactly 450 ms (10,800 frames) shorter
    bare_worker, bare_emit = in_process_worker(tmp_path / "bare")
    bare_worker.run(speech_job([piece(0, "First paragraph of the reading.", pause_ms=0)], style="calm"))
    assert done[0]["frames"] - bare_emit.of("piece_done")[0]["frames"] == 450 * 24 and done[0]["spoken_chars"] == len("First paragraph of the reading.")


def test_progress_says_which_piece_of_how_many_before_each_one(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    worker.run(speech_job([piece(n) for n in range(3)]))
    assert [(p["image"], p["of"], p["stage"]) for p in emit.of("progress")] == [(1, 3, "speak"), (2, 3, "speak"), (3, 3, "speak")]
    order = [e["event"] for e in emit.events if e["event"] in ("progress", "piece_done")]
    assert order == ["progress", "piece_done"] * 3  # "piece 2 of 3" is announced after piece 1 is reported


def test_the_second_fake_writes_its_own_sample_rate(tmp_path):
    worker, emit = in_process_worker(tmp_path, engine="fake-basic")
    worker.run(speech_job(engine="fake-basic", voice="basic_one"))
    assert {d["sample_rate"] for d in emit.of("piece_done")} == {22050}
    assert wavfile.read_wav(tmp_path / emit.of("piece_done")[0]["path"]).sample_rate == 22050


def test_no_half_written_file_is_left_behind(tmp_path):
    worker, _ = in_process_worker(tmp_path)
    worker.run(speech_job([piece(n) for n in range(3)]))
    assert not list(tmp_path.rglob("*.part")) and len(list(tmp_path.rglob("*.wav"))) == 3


def test_a_sample_and_a_design_job_write_where_the_api_said(tmp_path):
    worker, emit = in_process_worker(tmp_path, variant="design")
    out = f"speech/candidates/{RUN_ID}/0.wav"
    worker.run(speech_job([{**piece(0), "out": out}], task="design", variant="design", voice="", design="warm and low"))
    assert emit.of("piece_done")[0]["path"] == out and (tmp_path / out).is_file()


# ---------------------------------------------------------------- Cancel and failure
def test_cancel_during_a_slow_piece_keeps_the_pieces_already_made(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    pieces = [piece(0), piece(1, "This one is slow [fake:slow] on purpose."), piece(2)]
    threading.Timer(0.4, worker.request_cancel, args=(RUN_ID,)).start()  # the person presses Cancel while piece 1 is being made
    worker.run(speech_job(pieces))
    assert emit.kinds()[-1] == "run_canceled" and emit.of("run_canceled")[0]["completed"] == 1
    assert [d["key"] for d in emit.of("piece_done")] == ["0-0"]
    assert (tmp_path / pieces[0]["out"]).is_file() and not (tmp_path / pieces[2]["out"]).exists()


def test_a_cancel_that_arrived_before_the_job_stops_it_at_once(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    worker.request_cancel(RUN_ID)
    worker.run(speech_job())
    assert emit.kinds()[-1] == "run_canceled" and emit.of("run_canceled")[0]["completed"] == 0 and not emit.of("piece_done")


def test_a_cancel_only_ever_concerns_the_run_that_was_current(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    worker.request_cancel(OTHER_RUN_ID)  # for some other run: this one is not affected
    worker.run(speech_job())
    assert emit.kinds()[-1] == "run_finished"
    worker.request_cancel(RUN_ID)
    worker.run(speech_job())  # the cancel is forgotten when a run ends, so the next one starts with a clean slate
    assert worker._canceled == set()


def test_a_failing_piece_fails_the_job_and_keeps_what_was_made(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    worker.run(speech_job([piece(0), piece(1, "Boom [fake:error] here."), piece(2)]))
    failed = emit.of("run_failed")[0]
    assert failed["completed"] == 1 and failed["error"]["kind"] == "error" and failed["error"]["hint"]
    assert [d["key"] for d in emit.of("piece_done")] == ["0-0"] and "run_finished" not in emit.kinds()


def test_an_out_of_memory_piece_says_so(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    worker.run(speech_job([piece(0, "Too big [fake:oom] for memory.")]))
    assert emit.of("run_failed")[0]["error"]["kind"] == "out_of_memory"


def test_a_disk_that_cannot_be_written_to_fails_the_job_in_plain_words(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    blocker = tmp_path / "speech"
    blocker.write_text("a file where the folder should be")  # so the piece's folder cannot be made
    worker.run(speech_job())
    failed = emit.of("run_failed")[0]
    assert failed["completed"] == 0 and ("could not be saved" in failed["error"]["message"] or "disk is full" in failed["error"]["message"])


def test_a_malformed_job_is_refused_without_writing_anything(tmp_path):
    worker, emit = in_process_worker(tmp_path)
    worker.run(speech_job(pieces=[{**piece(0), "out": "../escape.wav"}]))
    failed = emit.of("run_failed")[0]
    assert failed["error"]["message"].startswith("Malformed job") and failed["run_id"] == RUN_ID
    worker.run("not even an object")
    assert emit.of("run_failed")[-1]["run_id"] is None and not list(tmp_path.rglob("*.wav"))


def test_each_job_loads_the_engine_once_and_a_failed_load_fails_the_job(tmp_path, monkeypatch):
    worker, emit = in_process_worker(tmp_path)
    worker.run(speech_job())
    worker.run(speech_job(run_id=OTHER_RUN_ID, pieces=[piece(0, run_id=OTHER_RUN_ID)]))
    assert emit.kinds().count("state") == 2  # loading, ready: once for both jobs
    broken, broken_emit = in_process_worker(tmp_path / "b")
    monkeypatch.setattr(type(broken.pipeline), "load", lambda self: (_ for _ in ()).throw(RuntimeError("no weights")))
    broken.run(speech_job())
    assert broken_emit.of("run_failed")[0]["error"]["kind"] == "load_failed" and "no weights" in broken_emit.of("run_failed")[0]["error"]["message"]


# ---------------------------------------------------------------- the process itself
def talk(data_dir: Path, *commands: dict, engine: str = "fake", extra: tuple[str, ...] = ()):
    """Send commands (plus a shutdown) to a fresh speech worker process; return its events and exit code."""
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    proc = subprocess.run(
        [sys.executable, "-m", "studio.worker", "--kind", "speech", "--engine", engine, "--pipeline", "fake", "--data-dir", str(data_dir),
         "--model", "", "--fake-step-delay-ms", "0", *extra],
        input="".join(json.dumps(c) + "\n" for c in (*commands, {"cmd": "shutdown"})), capture_output=True, text=True, timeout=60, env=env)
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()], proc.returncode, proc.stderr


def test_the_process_says_hello_with_its_engine_runs_a_job_and_says_goodbye(tmp_path):
    events, code, _ = talk(tmp_path, {"cmd": "run", "job": speech_job()})
    kinds = [e["event"] for e in events]
    assert code == 0 and kinds[0] == "hello" and kinds[-1] == "bye"
    assert events[0]["kind"] == "speech" and events[0]["engine"] == "fake"
    assert kinds.index("run_started") < kinds.index("piece_done") < kinds.index("run_finished")
    assert (tmp_path / "speech" / "readings" / RUN_ID / "0000" / "00001.wav").is_file()


def test_the_process_answers_a_probe_for_its_engine_and_exits(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    proc = subprocess.run([sys.executable, "-m", "studio.worker", "--kind", "speech", "--engine", "fake-basic", "--pipeline", "fake",
                           "--data-dir", str(tmp_path), "--model", "", "--probe"], capture_output=True, text=True, timeout=60, env=env)
    answer = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()][-1]
    assert proc.returncode == 0 and answer["event"] == "probe" and answer["ok"] is True and answer["device"] == "fake"


def test_the_probe_of_an_engine_the_pipeline_does_not_offer_fails_clearly(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    proc = subprocess.run([sys.executable, "-m", "studio.worker", "--kind", "speech", "--engine", "kokoro", "--pipeline", "fake",
                           "--data-dir", str(tmp_path), "--model", "", "--probe"], capture_output=True, text=True, timeout=60, env=env)
    answer = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()][-1]
    assert answer["event"] == "probe" and answer["ok"] is False  # the fake setting never offers a real engine, so it cannot be probed by mistake


def test_cancel_reaches_a_running_job_in_the_process(tmp_path):
    """The command reader is a thread of its own, so a cancel is heard while a slow piece is being made (the same promise as for pictures)."""
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    proc = subprocess.Popen([sys.executable, "-m", "studio.worker", "--kind", "speech", "--engine", "fake", "--pipeline", "fake",
                             "--data-dir", str(tmp_path), "--model", "", "--fake-step-delay-ms", "0"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    assert proc.stdin is not None and proc.stdout is not None
    slow = speech_job([piece(0, "Please wait [fake:slow] a moment."), piece(1)])
    proc.stdin.write(json.dumps({"cmd": "run", "job": slow}) + "\n")
    proc.stdin.flush()
    seen = []
    while True:
        event = json.loads(proc.stdout.readline())
        seen.append(event["event"])
        if event["event"] == "run_started":
            proc.stdin.write(json.dumps({"cmd": "cancel", "run_id": RUN_ID}) + "\n")
            proc.stdin.flush()
        if event["event"] in ("run_canceled", "run_finished", "run_failed"):
            break
    proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
    proc.stdin.flush()
    proc.wait(timeout=30)
    assert seen[-1] == "run_canceled" and "piece_done" not in seen
