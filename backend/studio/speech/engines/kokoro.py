"""Kokoro-82M (DESIGN.md §38.1): a small, fast English voice model (82 million parameters, Apache-2.0).

The calls are those of the `kokoro` package, version 0.9.4, as read from its source: a `KModel` loads from `config.json` and `kokoro-v1_0.pth`; one `KPipeline`
for each language ('a' American, 'b' British) shares that model; calling the pipeline with the text and a voice yields results whose `.audio` is a float tensor at
24 kHz. A voice is a small file, `voices/<id>.pt`, which the library downloads on first use; here the studio fetches it, so that the same cache-first rule
(`STUDIO_LOCAL_FILES_ONLY`, DESIGN.md §26.11) applies to it as to the model.

English words that Kokoro's own dictionary lacks are passed to espeak-ng; without it they are skipped, with a warning, so the probe says so as a note and does not
refuse the engine. The voices are the ones its card lists for English, with the grade the card gives each; the card states each voice's accent (American or
British) and its gender, so this engine does too.

Kokoro has no style note, no cloning and no design, and takes no language: the voice decides.
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
from .common import pcm16, speaking_failed

log = logging.getLogger("studio.speech.kokoro")

REPO = "hexgrad/Kokoro-82M"
CONFIG_FILE = "config.json"
WEIGHTS_FILE = "kokoro-v1_0.pth"
SAMPLE_RATE = 24000
SPEED = 1.0  # Kokoro's one control; the studio reads at the voice's own pace

# (id, label, gender, accent, the card's overall grade): the English voices of the model card's VOICES.md, American then British, women first, best grade first
_CARD: tuple[tuple[str, str, str, str, str], ...] = (
    ("af_heart", "Heart", "female", "American", "A"), ("af_bella", "Bella", "female", "American", "A-"), ("af_nicole", "Nicole", "female", "American", "B-"),
    ("af_aoede", "Aoede", "female", "American", "C+"), ("af_kore", "Kore", "female", "American", "C+"), ("af_sarah", "Sarah", "female", "American", "C+"),
    ("af_alloy", "Alloy", "female", "American", "C"), ("af_nova", "Nova", "female", "American", "C"), ("af_sky", "Sky", "female", "American", "C-"),
    ("af_jessica", "Jessica", "female", "American", "D"), ("af_river", "River", "female", "American", "D"),
    ("am_fenrir", "Fenrir", "male", "American", "C+"), ("am_michael", "Michael", "male", "American", "C+"), ("am_puck", "Puck", "male", "American", "C+"),
    ("am_echo", "Echo", "male", "American", "D"), ("am_eric", "Eric", "male", "American", "D"), ("am_liam", "Liam", "male", "American", "D"),
    ("am_onyx", "Onyx", "male", "American", "D"), ("am_santa", "Santa", "male", "American", "D-"), ("am_adam", "Adam", "male", "American", "F+"),
    ("bf_emma", "Emma", "female", "British", "B-"), ("bf_isabella", "Isabella", "female", "British", "C"), ("bf_alice", "Alice", "female", "British", "D"),
    ("bf_lily", "Lily", "female", "British", "D"),
    ("bm_fable", "Fable", "male", "British", "C"), ("bm_george", "George", "male", "British", "C"), ("bm_lewis", "Lewis", "male", "British", "D+"),
    ("bm_daniel", "Daniel", "male", "British", "D"),
)


def _libs() -> SimpleNamespace:
    """The libraries, imported when first needed and from here only, so that a test can put others in their place. Raises ImportError."""
    import torch
    from huggingface_hub import hf_hub_download
    from kokoro import KModel, KPipeline

    return SimpleNamespace(torch=torch, KModel=KModel, KPipeline=KPipeline, hf_hub_download=hf_hub_download)


def _espeak_fallback_works() -> bool:
    """Whether words outside Kokoro's dictionary can be spoken (espeak-ng reachable through misaki). A note for the probe, not a condition."""
    try:
        from misaki import espeak

        espeak.EspeakFallback(british=False)
        return True
    except Exception:
        return False


class Kokoro(EngineBase):
    INFO = EngineInfo(
        id="kokoro", title="Kokoro-82M", licence=Licence("Apache-2.0", False, "Kokoro-82M (hexgrad) is Apache-2.0; some of its training data is CC BY."),
        languages=(), can_style=False, can_clone=False, can_design=False,
        max_chars=350, min_chars=60,  # its card: best at 100 to 200 tokens, "rushing" past about 400; a starting value to be tuned on the Spark (DESIGN.md §38.14)
        sample_rate=SAMPLE_RATE, interruptible=False, memory_gb=1.5, model_id=REPO,
        notes="Small and fast; English only, American and British voices, each graded on its card.",
    )
    PRESETS = tuple(Voice(vid, label, gender=gender, language="English", accent=accent, description=f"Overall grade {grade} on the model card.")
                    for vid, label, gender, accent, grade in _CARD)

    def __init__(self, settings: Any):
        super().__init__(settings)
        self._libs: SimpleNamespace | None = None
        self._model: Any = None
        self._pipelines: dict[str, Any] = {}
        self._device = "cpu"

    # ------------------------------------------------------------------ can it run here
    @classmethod
    def probe(cls) -> dict[str, Any]:
        try:
            libs = _libs()
        except ImportError as exc:
            raise PipelineUnavailable(f"Kokoro's libraries are not installed ({exc}).", hint=REBUILD_HINT) from exc
        notes = []
        if not _espeak_fallback_works():
            notes.append("espeak-ng was not found, so a word missing from Kokoro's dictionary will be skipped instead of spoken.")
        return {"device": "cuda" if libs.torch.cuda.is_available() else "cpu", "notes": notes}

    # ------------------------------------------------------------------ loading
    def _file(self, name: str, local_only: bool) -> str:
        """A file of the model: from the folder the person named (STUDIO_SPEECH_KOKORO_MODEL, a local copy) or from the Hugging Face cache."""
        assert self._libs is not None
        base = self.settings.model
        if base and Path(base).expanduser().is_dir():
            path = Path(base).expanduser() / name
            if not path.is_file():
                raise FileNotFoundError(f"{path} is not there; the Kokoro folder must hold {CONFIG_FILE}, {WEIGHTS_FILE} and a voices folder.")
            return str(path)
        return self._libs.hf_hub_download(repo_id=base or REPO, filename=name, local_files_only=local_only)

    def download_plan(self) -> Any:
        """A few files of the repository (the weights, the config and the voices used), so the Hub's size for all of it is not offered (DESIGN.md section 39)."""
        from ...pipelines.progress import plan_for

        return plan_for(self.settings.model or REPO, self.settings.hub_mode, ask_hub=False)

    def load(self) -> dict[str, Any]:
        try:
            self._libs = libs = _libs()
        except ImportError as exc:
            raise PipelineLoadError(f"A required Python package is missing: {exc}", hint=REBUILD_HINT) from exc
        self._device = "cuda" if libs.torch.cuda.is_available() else "cpu"

        def fetch(local_only: bool) -> Any:
            return libs.KModel(repo_id=REPO, config=self._file(CONFIG_FILE, local_only), model=self._file(WEIGHTS_FILE, local_only))

        try:
            model = load_with_hub_mode(fetch, self.settings.hub_mode, "Kokoro-82M")
            self._model = model.to(self._device).eval()
        except Exception as exc:
            raise translate_load_error(exc, libs.torch, self.settings.hub_mode == "offline") from exc
        return {"device": self._device}

    def _pipeline(self, lang_code: str) -> Any:
        """The pipeline for one language, made once; all of them share the one model (the library's own advice)."""
        if lang_code not in self._pipelines:
            assert self._libs is not None
            self._pipelines[lang_code] = self._libs.KPipeline(lang_code=lang_code, repo_id=REPO, model=self._model)
        return self._pipelines[lang_code]

    # ------------------------------------------------------------------ speaking
    def synthesize(self, request: PieceRequest, cancelled: CancelCheck) -> Speech:
        if self._model is None or self._libs is None:
            raise PipelineError("Kokoro is not loaded.")
        if cancelled():
            raise Canceled()
        voice = request.voice
        if not voice or voice not in {v.id for v in self.PRESETS}:
            raise PipelineError(f"Kokoro has no voice called {voice!r}.")
        try:
            # a voice's first letter is its language ('a' American, 'b' British); the file is fetched under the same cache rule as the model
            path = self.fetch_voice(voice)
            chunks = []
            for result in self._pipeline(voice[0])(request.text, voice=path, speed=SPEED, split_pattern=None):
                if cancelled():
                    raise Canceled()
                if result.audio is not None:
                    chunks.append(pcm16(result.audio))
            return Speech(pcm=b"".join(chunks) if chunks else pcm16([]), sample_rate=SAMPLE_RATE)
        except Canceled:
            raise
        except Exception as exc:
            raise speaking_failed(exc, "Kokoro", self._libs.torch) from exc

    def fetch_voice(self, voice: str) -> str:
        return load_with_hub_mode(lambda local_only: self._file(f"voices/{voice}.pt", local_only), self.settings.hub_mode, f"the Kokoro voice {voice}")
