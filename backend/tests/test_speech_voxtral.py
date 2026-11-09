"""Voxtral TTS (DESIGN.md §38.1, §38.3): the engine that runs a server of its own, tested against a stand-in for that server.

Nothing here has run Voxtral, and nothing can: that is a Spark check (DESIGN.md §38.14). What can be tested is everything the studio does around it, with a REAL
process: finding the `vllm` program, starting it with the command line the model card gives (and the GPU share capped), waiting until it answers, sending a request
shaped as the card shows, reading the WAV that comes back, giving up on a request when Cancel comes, noticing that the server died (while starting, and while
speaking), and stopping it. The stand-in is a small Python program that takes the same command line, serves `/health` and `/v1/audio/speech` on the port it is
given, and writes down how it was called.
"""

from __future__ import annotations

import json
import os
import stat
import struct
import sys
import time
from pathlib import Path

import pytest

from studio.pipelines.base import Canceled, PipelineError, PipelineLoadError, PipelineUnavailable
from studio.speech.engine import EngineSettings, PieceRequest
from studio.speech.engines import voxtral

NEVER = lambda: False  # noqa: E731

STAND_IN = r'''#!{python}
"""Stands in for `vllm serve <model> --omni --host H --port P --gpu-memory-utilization F`."""
import http.server, json, os, struct, sys, time

record = os.environ["FAKE_VLLM_RECORD"]
argv = sys.argv[1:]
with open(os.path.join(record, "argv.json"), "w") as handle:
    json.dump(argv, handle)
with open(os.path.join(record, "env.json"), "w") as handle:
    json.dump({{k: os.environ.get(k) for k in ("PYTHONPATH", "HF_HUB_OFFLINE", "VIRTUAL_ENV", "HF_TOKEN")}}, handle)
mode = os.environ.get("FAKE_VLLM_MODE", "ok")
if mode == "die":
    print("CUDA error: the stand-in server was asked to fall over")
    sys.exit(3)
if mode == "never":
    time.sleep(60)
port = int(argv[argv.index("--port") + 1])


def wav():
    samples = [0.5] * 2400
    data = struct.pack("<%df" % len(samples), *samples)
    fmt = struct.pack("<HHIIHH", 3, 1, 24000, 24000 * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health" or (self.path == "/v1/models" and os.environ.get("FAKE_VLLM_HEALTH") != "no"):
            self.reply(200, b"{{}}")
        else:
            self.reply(404, b"{{}}")

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with open(os.path.join(record, "requests.jsonl"), "a") as handle:
            handle.write(json.dumps({{"path": self.path, "payload": payload}}) + "\n")
        if "[500]" in payload["input"]:
            self.reply(500, json.dumps({{"error": {{"message": "the stand-in was told to fail"}}}}).encode())
        elif "[slow]" in payload["input"]:
            time.sleep(4)
            self.reply(200, wav(), "audio/wav")
        elif "[html]" in payload["input"]:
            self.reply(502, b"<html>Bad gateway</html>", "text/html")
        elif "[notwav]" in payload["input"]:
            self.reply(200, b"this is not audio", "audio/wav")
        else:
            self.reply(200, wav(), "audio/wav")


http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """A `vllm` program in the place the engine looks, the folder it records into, and the environment that points the engine at them. Returns the record folder."""
    libs = tmp_path / "libs"
    program = libs / "voxtral" / "bin" / "vllm"
    program.parent.mkdir(parents=True)
    program.write_text(STAND_IN.format(python=sys.executable))
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    record = tmp_path / "record"
    record.mkdir()
    monkeypatch.setenv("STUDIO_SPEECH_LIBS", str(libs))
    monkeypatch.setenv("FAKE_VLLM_RECORD", str(record))
    monkeypatch.delenv("FAKE_VLLM_MODE", raising=False)
    monkeypatch.setattr(voxtral, "START_POLL_S", 0.05)
    monkeypatch.setattr(voxtral, "_gpu_total_gb", lambda: 96.0)
    return record


def settings(**kw) -> EngineSettings:
    return EngineSettings(variant="default", model=kw.get("model", ""), hub_mode=kw.get("hub_mode", "auto"))


@pytest.fixture
def engine(stand_in):
    engines = []

    def make(**kw) -> voxtral.Voxtral:
        e = voxtral.Voxtral(settings(**kw))
        engines.append(e)
        return e

    yield make
    for e in engines:  # whatever a test leaves running is stopped: a server must never outlive its test
        e.unload()


def recorded(record: Path, name: str):
    return json.loads((record / name).read_text())


def requests_made(record: Path) -> list[dict]:
    path = record / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def piece(text: str = "Paris is a beautiful city!", voice: str = "neutral_female") -> PieceRequest:
    return PieceRequest(text=text, voice=voice)


# ---------------------------------------------------------------- can it run here
def test_without_the_server_installed_the_engine_says_so_and_how_to_fix_it(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_SPEECH_LIBS", str(tmp_path / "nothing"))
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(PipelineUnavailable, match="vLLM-Omni") as caught:
        voxtral.Voxtral.probe()
    assert "optional" in caught.value.hint and "Dockerfile" in caught.value.hint


def test_the_server_is_found_in_its_own_environment_first_and_then_on_the_path(stand_in, monkeypatch, tmp_path):
    own = voxtral._find_server()
    assert own is not None and own.endswith("libs/voxtral/bin/vllm")
    monkeypatch.setenv("STUDIO_SPEECH_LIBS", str(tmp_path / "elsewhere"))
    elsewhere = tmp_path / "bin"
    elsewhere.mkdir()
    program = elsewhere / "vllm"
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(elsewhere))
    assert voxtral._find_server() == str(program)


def test_a_file_that_is_not_executable_is_not_a_server(stand_in, monkeypatch):
    own = Path(voxtral._find_server())
    own.chmod(0o644)
    monkeypatch.setenv("PATH", "")
    assert voxtral._find_server() is None


def test_the_probe_passes_with_a_server_and_a_gpu_big_enough(stand_in):
    result = voxtral.Voxtral.probe()
    assert result["device"] == "cuda" and result["server"].endswith("vllm")


def test_the_probe_refuses_a_gpu_smaller_than_the_card_asks_for(stand_in, monkeypatch):
    monkeypatch.setattr(voxtral, "_gpu_total_gb", lambda: 8.0)
    with pytest.raises(PipelineUnavailable, match="at least 16 GB.*8 GB"):
        voxtral.Voxtral.probe()
    monkeypatch.setattr(voxtral, "_gpu_total_gb", lambda: None)  # a GPU that cannot be measured is not a reason to refuse
    assert voxtral.Voxtral.probe()["device"] == "unknown"


# ---------------------------------------------------------------- starting the server
def test_the_server_is_started_as_the_model_card_says_with_the_gpu_share_capped(engine, stand_in, monkeypatch):
    monkeypatch.setenv("STUDIO_SPEECH_GPU_FRACTION", "0.35")
    facts = engine().load()
    argv = recorded(stand_in, "argv.json")
    assert argv[:3] == ["serve", "mistralai/Voxtral-4B-TTS-2603", "--omni"]
    assert argv[argv.index("--host") + 1] == "127.0.0.1" and argv[argv.index("--gpu-memory-utilization") + 1] == "0.35"
    assert int(argv[argv.index("--port") + 1]) == facts["port"] > 1024 and facts["gpu_fraction"] == 0.35


@pytest.mark.parametrize("given,used", [("0.99", "0.9"), ("0.001", "0.05"), ("lots", "0.2"), ("", "0.2")])
def test_the_gpu_share_is_kept_within_sense_and_defaults_to_a_fifth(engine, stand_in, monkeypatch, given, used):
    monkeypatch.setenv("STUDIO_SPEECH_GPU_FRACTION", given)
    engine().load()
    argv = recorded(stand_in, "argv.json")
    assert argv[argv.index("--gpu-memory-utilization") + 1] == used


def test_a_model_set_by_the_person_replaces_the_default_in_the_command_and_in_each_request(engine, stand_in):
    e = engine(model="my-org/voxtral-copy")
    e.load()
    assert recorded(stand_in, "argv.json")[1] == "my-org/voxtral-copy"
    e.synthesize(piece(), NEVER)
    assert requests_made(stand_in)[0]["payload"]["model"] == "my-org/voxtral-copy"


def test_the_server_runs_in_its_own_environment_not_the_workers(engine, stand_in, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/opt/speech-libs/voxtral")
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/else")
    monkeypatch.setenv("HF_TOKEN", "a-token-the-hub-needs")
    engine().load()
    seen = recorded(stand_in, "env.json")
    assert seen["PYTHONPATH"] is None and seen["VIRTUAL_ENV"] is None  # they would point it at the wrong libraries
    assert seen["HF_TOKEN"] == "a-token-the-hub-needs" and seen["HF_HUB_OFFLINE"] is None  # but the cache and the token are its to use


def test_when_told_to_stay_offline_the_server_is_too(engine, stand_in):
    engine(hub_mode="offline").load()
    assert recorded(stand_in, "env.json")["HF_HUB_OFFLINE"] == "1"


def test_a_server_that_falls_over_while_starting_is_reported_with_its_last_words(engine, stand_in, monkeypatch):
    monkeypatch.setenv("FAKE_VLLM_MODE", "die")
    e = engine()
    with pytest.raises(PipelineLoadError, match=r"stopped while starting \(exit code 3\)") as caught:
        e.load()
    assert "CUDA error: the stand-in server was asked to fall over" in caught.value.hint
    assert e._process is None and e._log is None  # nothing left behind


def test_a_server_that_never_answers_is_given_up_on_and_killed(engine, stand_in, monkeypatch):
    monkeypatch.setenv("FAKE_VLLM_MODE", "never")
    monkeypatch.setattr(voxtral, "START_TIMEOUT_S", 1.0)
    e = engine()
    started = time.monotonic()
    with pytest.raises(PipelineLoadError, match="did not answer within 1 seconds") as caught:
        e.load()
    assert time.monotonic() - started < 15 and caught.value.hint and e._process is None


def test_a_missing_server_at_load_time_is_a_load_error_not_a_crash(engine, monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_SPEECH_LIBS", str(tmp_path / "nothing"))
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(PipelineLoadError, match="not installed"):
        engine().load()


def test_a_server_that_answers_models_but_not_health_is_still_ready(engine, stand_in, monkeypatch):
    """Either address means ready (an OpenAI-compatible server has /v1/models; vLLM also has /health); only the first that answers is needed."""
    monkeypatch.setattr(voxtral.Voxtral, "_answers", lambda self, path: path == "/v1/models")
    engine().load()


# ---------------------------------------------------------------- speaking
def test_a_piece_is_sent_as_the_card_shows_and_the_wav_that_comes_back_is_read(engine, stand_in):
    e = engine()
    e.load()
    speech = e.synthesize(piece("Paris is a beautiful city!", "casual_male"), NEVER)
    assert requests_made(stand_in) == [{"path": "/v1/audio/speech", "payload": {
        "input": "Paris is a beautiful city!", "model": "mistralai/Voxtral-4B-TTS-2603", "response_format": "wav", "voice": "casual_male"}}]
    assert speech.sample_rate == 24000 and len(speech.pcm) == 2 * 2400
    assert struct.unpack("<2h", speech.pcm[:4]) == (16384, 16384)  # the stand-in's 32-bit float 0.5, as the studio's 16-bit samples


def test_every_piece_is_a_request_to_the_same_server(engine, stand_in):
    e = engine()
    facts = e.load()
    for text in ("one", "two", "three"):
        e.synthesize(piece(text), NEVER)
    assert [r["payload"]["input"] for r in requests_made(stand_in)] == ["one", "two", "three"]
    assert e._port == facts["port"]


def test_a_voice_the_model_does_not_have_is_refused_before_the_server_is_asked(engine, stand_in):
    e = engine()
    e.load()
    for voice in ("", "nobody", "NEUTRAL_FEMALE", "../neutral_female"):
        with pytest.raises(PipelineError, match="no voice called"):
            e.synthesize(piece(voice=voice), NEVER)
    assert requests_made(stand_in) == []


def test_a_refusal_from_the_server_is_passed_on_in_its_own_words(engine, stand_in):
    e = engine()
    e.load()
    with pytest.raises(PipelineError, match=r"refused this paragraph \(HTTP 500\): the stand-in was told to fail"):
        e.synthesize(piece("fail [500]"), NEVER)
    with pytest.raises(PipelineError, match=r"HTTP 502"):
        e.synthesize(piece("fail [html]"), NEVER)  # a gateway's page is shown as text, not parsed


def test_something_that_is_not_audio_is_reported_as_what_it_is(engine, stand_in):
    e = engine()
    e.load()
    with pytest.raises(PipelineError, match="did not send a WAV"):
        e.synthesize(piece("[notwav]"), NEVER)


def test_cancel_gives_up_on_a_request_in_flight_without_waiting_for_the_server(engine, stand_in):
    e = engine()
    e.load()
    started = time.monotonic()
    with pytest.raises(Canceled):
        e.synthesize(piece("a long one [slow]"), lambda: time.monotonic() - started > 0.5)
    assert time.monotonic() - started < 3.0  # the stand-in takes 4 seconds to answer


def test_cancel_before_a_request_sends_nothing(engine, stand_in):
    e = engine()
    e.load()
    with pytest.raises(Canceled):
        e.synthesize(piece(), lambda: True)
    assert requests_made(stand_in) == []


def test_before_it_is_loaded_the_engine_says_so(engine, stand_in):
    with pytest.raises(PipelineError, match="not loaded"):
        engine().synthesize(piece(), NEVER)


def test_a_server_that_dies_while_speaking_is_reported_not_left_as_a_timeout(engine, stand_in):
    e = engine()
    e.load()
    e._process.kill()
    e._process.wait()
    with pytest.raises(PipelineError, match="did not answer|stopped while speaking"):
        e.synthesize(piece(), NEVER)


# ---------------------------------------------------------------- stopping the server
def test_unloading_stops_the_server_and_its_port_stops_answering(engine, stand_in):
    e = engine()
    e.load()
    process, port, log = e._process, e._port, e._log
    assert e._answers("/health") and log.is_file()
    e.unload()
    assert process.poll() is not None and e._process is None and not log.exists()
    e._port = port
    assert not e._answers("/health")


def test_unloading_twice_or_before_loading_is_harmless(engine, stand_in):
    e = engine()
    e.unload()
    e.load()
    e.unload()
    e.unload()


def test_a_server_that_ignores_the_polite_request_is_killed(engine, stand_in, monkeypatch):
    e = engine()
    e.load()
    real_wait = e._process.wait
    calls = []

    def stubborn(timeout=None):  # the first wait (after SIGTERM) never ends, as if the server were stuck; the kill that follows is what ends it
        calls.append(timeout)
        if len(calls) == 1:
            raise voxtral.subprocess.TimeoutExpired("vllm", timeout)
        return real_wait(timeout)

    e._process.wait = stubborn  # type: ignore[method-assign]
    process = e._process
    e.unload()
    assert len(calls) == 2 and process.poll() is not None


def test_the_server_is_a_process_of_its_own_so_that_stopping_it_stops_what_it_started(engine, stand_in):
    e = engine()
    e.load()
    assert os.getpgid(e._process.pid) == e._process.pid != os.getpgid(0)


def test_the_voices_of_voxtral_are_named_as_its_files_are(engine):
    assert [v.id for v in voxtral.Voxtral.PRESETS][:3] == ["neutral_female", "casual_female", "cheerful_female"]  # the women first
    labels = {v.id: v.label for v in voxtral.Voxtral.PRESETS}
    assert labels["de_female"] == "German (female)" and labels["ar_male"] == "Arabic (male)" and labels["cheerful_female"] == "Cheerful (female)"
    by_id = {v.id: v for v in voxtral.Voxtral.PRESETS}
    assert by_id["de_female"].language == "German" and by_id["casual_male"].language is None  # a language only where the file's name gives one
    assert all(v.accent is None for v in voxtral.Voxtral.PRESETS)
