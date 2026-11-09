"""Voxtral TTS (DESIGN.md §38.1, §38.3): Mistral's 4-billion-parameter speech model, CC BY-NC 4.0 (non-commercial; the reference voices too).

**It runs as a server of its own.** The model card's way to run it is `vllm serve mistralai/Voxtral-4B-TTS-2603 --omni` (vLLM-Omni 0.18 or later, one GPU with at
least 16 GB), and a request to `POST /v1/audio/speech` with `{"input", "model", "response_format": "wav", "voice"}`. So this engine starts that server as a child of
the speech worker, on a loopback port, with the share of the GPU's memory it may take capped (`STUDIO_SPEECH_GPU_FRACTION`; that vLLM takes most of a GPU unless
told otherwise is my knowledge of vLLM and not something the card says), waits until it answers, and sends it each piece. The child ends with the worker.

vLLM brings its own PyTorch, which must not replace NVIDIA's in the image, so its packages live in a virtual environment of their own,
`STUDIO_SPEECH_LIBS/voxtral` (an optional build step); its `bin/vllm` is what this engine runs. Where that is not installed the engine says so and why.

**Voxtral is the riskiest engine**: no run on a DGX Spark has been reported, and nothing here has run it. The pieces that can be tested without it (finding the server,
starting and stopping a process, the request, reading the WAV that comes back, Cancel) are tested against a stand-in server.

Voxtral has no style note, no cloning that the open weights are confirmed to offer, no design and no language setting (the voice decides), and no seed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from ...pipelines.base import Canceled, PipelineError, PipelineLoadError, PipelineUnavailable
from ...pipelines.real import REBUILD_HINT
from ..engine import CancelCheck, EngineBase, EngineInfo, Licence, PieceRequest, Speech, Voice
from .common import wav_to_pcm16

log = logging.getLogger("studio.speech.voxtral")

MODEL = "mistralai/Voxtral-4B-TTS-2603"
HOST = "127.0.0.1"
MIN_GPU_GB = 16  # "a single GPU with >= 16GB memory" (the card)
START_TIMEOUT_S = 900  # downloading 8 GB the first time, then loading it
START_POLL_S = 1.0  # how often a server that is starting is asked whether it is ready
REQUEST_TIMEOUT_S = 300
POLL_S = 0.2  # how often a request in flight looks for Cancel
DEFAULT_FRACTION = 0.2

# The 20 reference voices of the repository's voice_embedding folder. The card states no accent for any. A file's name states its gender; a two-letter prefix is a language
# code, taken as the language of the voice. "casual", "neutral" and "cheerful" name a manner, and no language is stated for them.
_LANGUAGE_CODES = {"ar": "Arabic", "de": "German", "es": "Spanish", "fr": "French", "hi": "Hindi", "it": "Italian", "nl": "Dutch", "pt": "Portuguese"}
_VOICE_IDS = (
    "neutral_female", "casual_female", "cheerful_female", "de_female", "es_female", "fr_female", "hi_female", "it_female", "nl_female", "pt_female",
    "neutral_male", "casual_male", "ar_male", "de_male", "es_male", "fr_male", "hi_male", "it_male", "nl_male", "pt_male",
)


def _voice(identifier: str) -> Voice:
    prefix, _, gender = identifier.rpartition("_")  # "de_female" -> ("de", "_", "female")
    language = _LANGUAGE_CODES.get(prefix)
    label = (f"{language} ({gender})" if language else f"{prefix.capitalize()} ({gender})")
    return Voice(identifier, label, gender=gender, language=language, accent=None,
                 description=f"Reference voice file {identifier}; the model card states no accent." + (f" Named for {language}." if language else ""))


def _find_server() -> Optional[str]:
    """The `vllm` program: the one in the engine's own virtual environment (STUDIO_SPEECH_LIBS/voxtral/bin/vllm), else one on the PATH. None if there is none."""
    libs = os.environ.get("STUDIO_SPEECH_LIBS", "").strip()
    if libs:
        candidate = Path(libs).expanduser() / "voxtral" / "bin" / "vllm"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("vllm")


def _gpu_total_gb() -> Optional[float]:
    """The memory of the first GPU in gigabytes, or None where that cannot be found out (no torch, no GPU)."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    except Exception:
        return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _die_with_parent() -> None:
    """Run in the child just before it starts: ask the kernel to send it SIGTERM when the speech worker dies, however it dies (Linux only; elsewhere nothing)."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG = 1
    except Exception:
        pass


class Voxtral(EngineBase):
    INFO = EngineInfo(
        id="voxtral", title="Voxtral TTS", licence=Licence("CC BY-NC 4.0", True, "Voxtral TTS and its reference voices are CC BY-NC 4.0: for non-commercial use only."),
        languages=(), can_style=False, can_clone=False, can_design=False,
        max_chars=1200, min_chars=120,  # "natively up to two minutes of audio" a request (the card): about 1,700 characters; starting values to be tuned on the Spark
        sample_rate=24000, interruptible=True,  # a request in flight is given up on when Cancel comes (the server finishes it and its audio is dropped)
        memory_gb=20.0,  # the card says 16 GB for the model; the rest is room to work, and the share vLLM is allowed (STUDIO_SPEECH_GPU_FRACTION) is checked against the GPU
        model_id=MODEL,
        notes="Speaks nine languages (the voice decides which). No accent is stated for any voice. Runs a vLLM-Omni server of its own; non-commercial licence.",
    )
    PRESETS = tuple(_voice(identifier) for identifier in _VOICE_IDS)

    def __init__(self, settings: Any):
        super().__init__(settings)
        self._process: Optional[subprocess.Popen] = None
        self._port = 0
        self._log: Optional[Path] = None

    # ------------------------------------------------------------------ can it run here
    @classmethod
    def probe(cls) -> dict[str, Any]:
        server = _find_server()
        if server is None:
            raise PipelineUnavailable(
                "Voxtral's server (vLLM-Omni) is not installed in this image.",
                hint="It is an optional install in its own environment (see docker/Dockerfile, the Voxtral step): build the image with it, then restart.")
        total = _gpu_total_gb()
        if total is not None and total < MIN_GPU_GB:
            raise PipelineUnavailable(f"Voxtral needs a GPU with at least {MIN_GPU_GB} GB of memory and this one has {total:.0f} GB.")
        return {"device": "cuda" if total is not None else "unknown", "server": server}

    # ------------------------------------------------------------------ the server
    def _fraction(self) -> float:
        try:
            return min(0.9, max(0.05, float(os.environ.get("STUDIO_SPEECH_GPU_FRACTION", DEFAULT_FRACTION))))
        except ValueError:
            return DEFAULT_FRACTION

    def _command(self, server: str) -> list[str]:
        return [server, "serve", self.settings.model or MODEL, "--omni", "--host", HOST, "--port", str(self._port), "--gpu-memory-utilization", f"{self._fraction():g}"]

    def _tail(self, lines: int = 12) -> str:
        """The end of the server's own output, for an error message."""
        if self._log is None or not self._log.is_file():
            return ""
        text = self._log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        return "\n".join(text[-lines:])

    def _answers(self, path: str) -> bool:
        try:
            with urllib.request.urlopen(f"http://{HOST}:{self._port}{path}", timeout=2) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def load(self) -> dict[str, Any]:
        server = _find_server()
        if server is None:
            raise PipelineLoadError("Voxtral's server (vLLM-Omni) is not installed in this image.", hint=REBUILD_HINT)
        self._port = _free_port()
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}  # the server runs in its own environment, not the worker's
        if self.settings.hub_mode == "offline":
            env["HF_HUB_OFFLINE"] = "1"
        handle, name = tempfile.mkstemp(prefix="voxtral-", suffix=".log")
        self._log = Path(name)
        command = self._command(server)
        log.info("starting %s", " ".join(command))
        try:
            with os.fdopen(handle, "wb") as sink:
                self._process = subprocess.Popen(command, stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, start_new_session=True,
                                                 preexec_fn=_die_with_parent)
        except OSError as exc:
            raise PipelineLoadError(f"Could not start Voxtral's server: {exc.strerror or exc}.", hint=REBUILD_HINT) from exc
        deadline = time.monotonic() + START_TIMEOUT_S
        while True:
            if self._process.poll() is not None:
                code, tail = self._process.returncode, self._tail()  # (read before unload() forgets both)
                self.unload()
                raise PipelineLoadError(f"Voxtral's server stopped while starting (exit code {code}).", hint=("Its last output: " + tail) if tail else "Check the container's log.")
            if self._answers("/health") or self._answers("/v1/models"):
                return {"device": "cuda", "port": self._port, "gpu_fraction": self._fraction()}
            if time.monotonic() > deadline:
                self.unload()
                raise PipelineLoadError(f"Voxtral's server did not answer within {START_TIMEOUT_S:g} seconds.",
                                        hint="The first start downloads 8 GB; check the container's log and the network, and try again.")
            time.sleep(START_POLL_S)

    def unload(self) -> None:
        """Stop the server (terminate, then kill if it will not go) and forget its log. Safe to call twice."""
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)  # the server and anything it started (it was made the leader of its own group)
            except (ProcessLookupError, PermissionError, OSError):
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    process.kill()
                process.wait(timeout=15)
        if self._log is not None:
            self._log.unlink(missing_ok=True)
            self._log = None

    # ------------------------------------------------------------------ speaking
    def _post(self, text: str, voice: str) -> bytes:
        body = json.dumps({"input": text, "model": self.settings.model or MODEL, "response_format": "wav", "voice": voice}).encode("utf-8")
        request = urllib.request.Request(f"http://{HOST}:{self._port}/v1/audio/speech", data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300].strip()
            try:
                parsed = json.loads(detail)
                detail = str((parsed.get("error") or {}).get("message") or parsed.get("detail") or parsed.get("message") or detail)
            except (ValueError, AttributeError):
                pass
            raise PipelineError(f"Voxtral's server refused this paragraph (HTTP {exc.code}): {detail or exc.reason}") from exc

    def synthesize(self, request: PieceRequest, cancelled: CancelCheck) -> Speech:
        if self._process is None:
            raise PipelineError("Voxtral is not loaded.")
        if cancelled():
            raise Canceled()
        if request.voice not in {v.id for v in self.PRESETS}:
            raise PipelineError(f"Voxtral has no voice called {request.voice!r}.")
        outcome: dict[str, Any] = {}

        def work() -> None:  # in a thread, so that Cancel can be looked for while the server is busy
            try:
                outcome["audio"] = self._post(request.text, request.voice)
            except BaseException as exc:  # handed to the caller below
                outcome["error"] = exc

        thread = threading.Thread(target=work, name="voxtral-request", daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(POLL_S)
            if cancelled():
                raise Canceled()  # the server finishes the paragraph and its audio is dropped
        error = outcome.get("error")
        if error is not None:
            if isinstance(error, PipelineError):
                raise error
            if self._process is not None and self._process.poll() is not None:
                raise PipelineError("Voxtral's server stopped while speaking.", hint=("Its last output: " + self._tail()) if self._tail() else "Check the container's log.") from error
            raise PipelineError(f"Voxtral's server did not answer: {type(error).__name__}: {error}", hint="If it keeps failing, unload the model and load it again.") from error
        pcm, rate = wav_to_pcm16(outcome["audio"])
        return Speech(pcm=pcm, sample_rate=rate)
