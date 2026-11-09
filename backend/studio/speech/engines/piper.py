"""Piper, with the Jenny voice (DESIGN.md §38.1): a small speech model that runs on the CPU, and the one documented Irish woman's voice among the engines.

The calls are those of `piper-tts` 1.8.0, as read from its source: `PiperVoice.load(model_path)` reads an ONNX voice and its `.onnx.json`; `voice.synthesize(text)`
yields one `AudioChunk` for each sentence, each with `.audio_int16_bytes` and `.sample_rate`. The wheel carries its own copy of espeak-ng's data, so nothing needs
installing for it from the system. There is a wheel for aarch64.

**Licences.** `piper-tts` is GPL-3.0-or-later, so this engine imports it only in the speech worker, a process of its own, and never in the API process; and the
Jenny voice's own licence ("See URL" on its card, pointing at the dataset) has not been read. The engine's notice says both, and goes into every WAV it makes.

The voice is read from the Hugging Face cache like any model: `rhasspy/piper-voices`, `en/en_GB/jenny_dioco/medium`. STUDIO_SPEECH_PIPER_MODEL can name a local `.onnx`
file instead (with its `.onnx.json` beside it), which is how another Piper voice is used.

Piper has no style note, no cloning, no design and no seed (its noise is drawn inside the ONNX model), and it takes no language: the voice decides.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...pipelines.base import Canceled, PipelineError, PipelineLoadError, PipelineUnavailable
from ...pipelines.hub import load_with_hub_mode
from ...pipelines.real import REBUILD_HINT, translate_load_error
from ..engine import CancelCheck, EngineBase, EngineInfo, Licence, PieceRequest, Speech, Voice
from .common import speaking_failed

log = logging.getLogger("studio.speech.piper")

REPO = "rhasspy/piper-voices"
VOICE_DIR = "en/en_GB/jenny_dioco/medium"
VOICE_FILE = "en_GB-jenny_dioco-medium.onnx"
SAMPLE_RATE = 22050  # the voice's own config says so; what it really gives is read from the voice when it loads

NOTICE = ("Piper (piper-tts) is GPL-3.0-or-later. The Jenny voice's own licence is given at its source (the Jenny dataset) and has not been checked here: "
          "use this voice for yourself until it has.")


def _libs() -> SimpleNamespace:
    """The libraries, imported when first needed and from here only (a test puts others in their place). Raises ImportError."""
    import onnxruntime  # noqa: F401  (Piper cannot speak without it; its absence is found here and not in the middle of a reading)
    from huggingface_hub import hf_hub_download
    from piper import PiperVoice

    return SimpleNamespace(PiperVoice=PiperVoice, hf_hub_download=hf_hub_download)


class Piper(EngineBase):
    INFO = EngineInfo(
        id="piper", title="Piper (Jenny)", licence=Licence("GPL-3.0-or-later (the package); the voice's licence not yet checked", False, NOTICE),
        languages=(), can_style=False, can_clone=False, can_design=False,
        max_chars=300, min_chars=50,  # it speaks a sentence at a time; starting values to be tuned on the Spark (DESIGN.md §38.14)
        sample_rate=SAMPLE_RATE, interruptible=True,  # between its sentences
        memory_gb=0.3, model_id=f"{REPO}/{VOICE_DIR}",
        notes="Runs on the CPU, so it can speak while a GPU model is busy. One voice: Jenny, an Irish woman.",
    )
    PRESETS = (Voice("jenny_dioco", "Jenny", gender="female", language="English", accent="Irish",
                     description="A woman's voice; the Jenny dataset's own README says she is Irish."),)

    def __init__(self, settings: Any):
        super().__init__(settings)
        self._voice: Any = None

    # ------------------------------------------------------------------ can it run here
    @classmethod
    def probe(cls) -> dict[str, Any]:
        try:
            _libs()
        except ImportError as exc:
            raise PipelineUnavailable(f"Piper's libraries are not installed ({exc}).", hint=REBUILD_HINT) from exc
        return {"device": "cpu"}

    # ------------------------------------------------------------------ loading
    def _fetch(self, libs: SimpleNamespace, local_only: bool) -> str:
        """The path of the voice's .onnx file, with its .onnx.json fetched beside it (Piper reads both)."""
        named = self.settings.model
        if named and Path(named).expanduser().suffix == ".onnx":
            path = Path(named).expanduser()
            if not path.is_file() or not path.with_name(path.name + ".json").is_file():
                raise FileNotFoundError(f"{path} and {path.name}.json must both be there.")
            return str(path)
        model = libs.hf_hub_download(repo_id=REPO, filename=f"{VOICE_DIR}/{VOICE_FILE}", local_files_only=local_only)
        libs.hf_hub_download(repo_id=REPO, filename=f"{VOICE_DIR}/{VOICE_FILE}.json", local_files_only=local_only)
        return model

    def load(self) -> dict[str, Any]:
        try:
            libs = _libs()
        except ImportError as exc:
            raise PipelineLoadError(f"A required Python package is missing: {exc}", hint=REBUILD_HINT) from exc
        try:
            path = load_with_hub_mode(lambda local_only: self._fetch(libs, local_only), self.settings.hub_mode, "the Piper voice")
            self._voice = libs.PiperVoice.load(path, use_cuda=False)
        except Exception as exc:
            raise translate_load_error(exc, None, self.settings.hub_mode == "offline") from exc
        return {"device": "cpu", "sample_rate": self._voice.config.sample_rate}

    # ------------------------------------------------------------------ speaking
    def synthesize(self, request: PieceRequest, cancelled: CancelCheck) -> Speech:
        if self._voice is None:
            raise PipelineError("Piper is not loaded.")
        if cancelled():
            raise Canceled()
        try:
            chunks, rate = [], int(self._voice.config.sample_rate)
            for chunk in self._voice.synthesize(request.text):
                if cancelled():
                    raise Canceled()
                if chunk.sample_channels != 1 or chunk.sample_width != 2:
                    raise PipelineError("Piper gave audio that is not 16-bit mono.")
                rate = int(chunk.sample_rate)
                chunks.append(chunk.audio_int16_bytes)
            if not chunks:
                raise PipelineError("The engine made no audio for this text.", hint="A paragraph of nothing but punctuation or symbols cannot be spoken. Edit it, or take it out.")
            return Speech(pcm=b"".join(chunks), sample_rate=rate)
        except Canceled:
            raise
        except Exception as exc:
            raise speaking_failed(exc, "Piper") from exc
