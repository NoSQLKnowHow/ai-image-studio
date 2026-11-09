"""Qwen3-TTS (DESIGN.md §38.1): three 1.7B-parameter checkpoints from Alibaba's Qwen team, Apache-2.0, of which one is in memory at a time.

  variant  checkpoint                          what it speaks
  custom   Qwen3-TTS-12Hz-1.7B-CustomVoice    the nine preset voices, with an optional style note ("very happy")
  design   Qwen3-TTS-12Hz-1.7B-VoiceDesign     a voice invented from a description; used only to make the candidate clips of a design
  clone    Qwen3-TTS-12Hz-1.7B-Base            a saved voice (designed or cloned), from its reference clip and the words said in it; no style note

The calls are those of `qwen-tts` 0.1.1, as read from its source: `Qwen3TTSModel.from_pretrained(path, device_map=…, dtype=…, attn_implementation=…)`; then
`generate_custom_voice(text, speaker, language, instruct)`, `generate_voice_design(text, instruct, language)`, or `create_voice_clone_prompt(ref_audio, ref_text,
x_vector_only_mode)` once and `generate_voice_clone(text, language, voice_clone_prompt)`; each returns `(a list of numpy arrays, the sample rate)`. A language of
None is "Auto". The library takes no seed, so the engine seeds torch before each piece.

`qwen-tts` pins `transformers==4.57.3` and the image has another, so it lives in a folder of its own (`STUDIO_SPEECH_LIBS/qwen3-tts`, DESIGN.md §38.3).

STUDIO_SPEECH_QWEN3_TTS_MODEL, when set, is a folder (or a Hugging Face namespace) that holds the three checkpoints under the names above, instead of `Qwen/`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from ...pipelines.base import Canceled, PipelineError, PipelineLoadError, PipelineUnavailable
from ...pipelines.hub import load_with_hub_mode
from ...pipelines.real import REBUILD_HINT, translate_load_error
from ..engine import CancelCheck, EngineBase, EngineInfo, Licence, PieceRequest, Speech, Voice
from .common import pcm16, seed_torch, speaking_failed

log = logging.getLogger("studio.speech.qwen3")

NAMESPACE = "Qwen"
CHECKPOINTS = {"custom": "Qwen3-TTS-12Hz-1.7B-CustomVoice", "design": "Qwen3-TTS-12Hz-1.7B-VoiceDesign", "clone": "Qwen3-TTS-12Hz-1.7B-Base"}
ATTENTION = "sdpa"  # the README uses flash_attention_2, which needs a build for the GPU; PyTorch's own attention runs everywhere
MAX_NEW_TOKENS = 1536  # the library's default is 2048; at 12.5 frames a second this is two minutes of audio, far past one paragraph
LANGUAGES = ("English", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian")  # what the card lists

# The nine preset voices as the README's table states them: gender and language as written there, the dialect as an accent where it names one
PRESETS = (
    Voice("Vivian", "Vivian", gender="female", language="Chinese", description="Bright, slightly edgy young female voice.", styleable=True),
    Voice("Serena", "Serena", gender="female", language="Chinese", description="Warm, gentle young female voice.", styleable=True),
    Voice("Ono_Anna", "Ono Anna", gender="female", language="Japanese", description="Playful Japanese female voice with a light, nimble timbre.", styleable=True),
    Voice("Sohee", "Sohee", gender="female", language="Korean", description="Warm Korean female voice with rich emotion.", styleable=True),
    Voice("Ryan", "Ryan", gender="male", language="English", description="Dynamic male voice with strong rhythmic drive.", styleable=True),
    Voice("Aiden", "Aiden", gender="male", language="English", accent="American", description="Sunny American male voice with a clear midrange.", styleable=True),
    Voice("Uncle_Fu", "Uncle Fu", gender="male", language="Chinese", description="Seasoned male voice with a low, mellow timbre.", styleable=True),
    Voice("Dylan", "Dylan", gender="male", language="Chinese", accent="Beijing dialect", description="Youthful Beijing male voice with a clear, natural timbre.", styleable=True),
    Voice("Eric", "Eric", gender="male", language="Chinese", accent="Sichuan dialect", description="Lively Chengdu male voice with a slightly husky brightness.", styleable=True),
)


def _libs() -> SimpleNamespace:
    """The libraries, imported when first needed and from here only (a test puts others in their place). Raises ImportError."""
    import torch
    from qwen_tts import Qwen3TTSModel

    return SimpleNamespace(torch=torch, Qwen3TTSModel=Qwen3TTSModel)


class Qwen3TTS(EngineBase):
    INFO = EngineInfo(
        id="qwen3-tts", title="Qwen3-TTS", licence=Licence("Apache-2.0", False, "Qwen3-TTS by the Qwen team at Alibaba Cloud, Apache-2.0."),
        languages=LANGUAGES, can_style=True, can_clone=True, can_design=True,
        max_chars=500, min_chars=80,  # starting values, to be tuned on the Spark (DESIGN.md §38.14); no length limit is stated on the card
        sample_rate=24000, interruptible=False, memory_gb=6.0,  # about 4.5 GB of files for a checkpoint (§38.18) and room to work: an estimate to be checked
        model_id=f"{NAMESPACE}/{CHECKPOINTS['custom']}", variants=("custom", "clone", "design"), preset_variant="custom", clone_variant="clone",
        design_variant="design", notes="Nine preset voices (none is documented as British or Irish), a style note, voices designed from a description, and cloning.",
    )
    PRESETS = PRESETS

    def __init__(self, settings: Any):
        super().__init__(settings)
        self._libs: Optional[SimpleNamespace] = None
        self._model: Any = None
        self._prompts: dict[tuple[str, str], Any] = {}  # (reference clip, its words) -> the clone prompt built from them, once for each saved voice

    # ------------------------------------------------------------------ can it run here
    @classmethod
    def probe(cls) -> dict[str, Any]:
        try:
            libs = _libs()
        except ImportError as exc:
            raise PipelineUnavailable(f"Qwen3-TTS's library (qwen-tts) is not installed ({exc}).", hint=REBUILD_HINT) from exc
        if not libs.torch.cuda.is_available():
            raise PipelineUnavailable("Qwen3-TTS needs a GPU and none can be used here.", hint="Check that the container was started with the GPU (docker compose, gpus: all).")
        return {"device": "cuda"}

    # ------------------------------------------------------------------ loading
    def _checkpoint(self) -> str:
        """The place of this variant's checkpoint: under the folder or namespace the person set, else the Qwen namespace on Hugging Face."""
        name = CHECKPOINTS[self.settings.variant]
        base = self.settings.model
        if not base:
            return f"{NAMESPACE}/{name}"
        if Path(base).expanduser().is_dir():
            return str(Path(base).expanduser() / name)
        return f"{base.rstrip('/')}/{name}"

    def load(self) -> dict[str, Any]:
        if self.settings.variant not in CHECKPOINTS:
            raise PipelineLoadError(f"Qwen3-TTS has no variant {self.settings.variant!r}.")
        try:
            self._libs = libs = _libs()
        except ImportError as exc:
            raise PipelineLoadError(f"A required Python package is missing: {exc}", hint=REBUILD_HINT) from exc
        where = self._checkpoint()

        def fetch(local_only: bool) -> Any:
            return libs.Qwen3TTSModel.from_pretrained(where, device_map="cuda:0", dtype=libs.torch.bfloat16, attn_implementation=ATTENTION, local_files_only=local_only)

        try:
            self._model = load_with_hub_mode(fetch, self.settings.hub_mode, where)
        except Exception as exc:
            raise translate_load_error(exc, libs.torch, self.settings.hub_mode == "offline") from exc
        self._prompts = {}
        return {"device": "cuda:0", "checkpoint": where}

    # ------------------------------------------------------------------ speaking
    def _clone_prompt(self, ref: Any) -> Any:
        """The reusable prompt for a saved voice, built from its reference clip and the words said in it, once (the README's advice for one voice across many
        lines). `x_vector_only_mode=False` is the mode that uses the words, which is why they are stored with the clip."""
        key = (ref.path, ref.text)
        if key not in self._prompts:
            self._prompts[key] = self._model.create_voice_clone_prompt(ref_audio=ref.path, ref_text=ref.text, x_vector_only_mode=False)
        return self._prompts[key]

    def synthesize(self, request: PieceRequest, cancelled: CancelCheck) -> Speech:
        if self._model is None or self._libs is None:
            raise PipelineError("Qwen3-TTS is not loaded.")
        if cancelled():
            raise Canceled()
        torch, variant = self._libs.torch, self.settings.variant
        try:
            seed_torch(torch, request.seed)
            common = {"language": request.language, "max_new_tokens": MAX_NEW_TOKENS}  # (a language of None is "Auto" to the library)
            if variant == "custom":
                if request.voice not in {v.id for v in self.PRESETS}:
                    raise PipelineError(f"Qwen3-TTS has no voice called {request.voice!r}.")
                wavs, rate = self._model.generate_custom_voice(text=request.text, speaker=request.voice, instruct=request.style or None, **common)
            elif variant == "design":
                if not request.design:
                    raise PipelineError("A voice design needs a description of the voice.")
                wavs, rate = self._model.generate_voice_design(text=request.text, instruct=request.design, **common)
            else:
                if request.ref is None:
                    raise PipelineError("A saved voice needs its reference clip.")
                wavs, rate = self._model.generate_voice_clone(text=request.text, voice_clone_prompt=self._clone_prompt(request.ref), **common)
            if not len(wavs):
                raise PipelineError("The engine made no audio for this text.")
            return Speech(pcm=pcm16(wavs[0]), sample_rate=int(rate))
        except Canceled:
            raise
        except Exception as exc:
            raise speaking_failed(exc, "Qwen3-TTS", torch) from exc

    def unload(self) -> None:
        self._model, self._prompts = None, {}
