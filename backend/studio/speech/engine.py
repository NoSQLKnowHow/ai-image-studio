"""The interface every text-to-speech engine implements (DESIGN.md §38.3).

An engine is one model (Qwen3-TTS, Voxtral, Kokoro, Piper, ...) behind a small, fixed surface. Nothing outside `speech/engines/` and
`speech/registry.py` names an engine: the queue, the worker, the database, the API and the page all ask `EngineInfo` what an engine can do and
ask the engine to speak. That is what makes "a model joins by one file and one line" (criterion 260) true, and a test scans the source to keep it so.

Three things live here:
  * the descriptive records (`Licence`, `Voice`, `EngineInfo`) that the page is shown;
  * the request and result records of one call to an engine (`PieceRequest`, `Speech`);
  * the `SpeechEngine` protocol itself.

Everything in this module is plain Python: it imports no model library, so the API process can read an engine's description without loading
PyTorch. The engines import their heavy libraries only inside `probe()` and `load()`, which run in the speech worker process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

# A function an engine calls now and then while it works (between sentences, or every few steps, as far as it can): True means the person pressed
# Cancel, and the engine should stop and raise `Canceled` (pipelines.base) as soon as it can.
CancelCheck = Callable[[], bool]

GENDERS = ("female", "male")


@dataclass(frozen=True)
class Licence:
    """What an engine's model is licensed under, for the badge on the tab and the note in each file (DESIGN.md §38.4)."""

    name: str  # e.g. "Apache-2.0", "CC BY-NC 4.0"
    noncommercial: bool = False  # true shows the "non-commercial" badge
    notice: str = ""  # one sentence for the badge's tooltip and for the WAV's note; empty for nothing special to say

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "noncommercial": self.noncommercial, "notice": self.notice}


@dataclass(frozen=True)
class Voice:
    """One preset voice. Every field after `label` is what the SOURCE (the model's card, its file names, its README) states, and `None` where it
    states nothing: the page then says "accent not stated" and never guesses from a name (criterion 261)."""

    id: str  # what the engine is given to pick the voice (e.g. "af_bella", "vivian")
    label: str  # what the page shows
    gender: Optional[str] = None  # "female", "male" or None
    language: Optional[str] = None  # e.g. "English", or None when not stated
    accent: Optional[str] = None  # e.g. "American", or None when not stated
    description: str = ""  # the source's own words, if it has any
    styleable: bool = False  # whether a style note changes how this voice speaks (DESIGN.md §38.2)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "gender": self.gender, "language": self.language, "accent": self.accent,
                "description": self.description, "styleable": self.styleable}


@dataclass(frozen=True)
class EngineInfo:
    """Everything the queue, the memory check and the page need to know about an engine without naming it (DESIGN.md §38.3)."""

    id: str  # the engine's name in the registry and in the API: lower case, digits and hyphens
    title: str  # what the page shows
    licence: Licence
    languages: tuple[str, ...]  # the languages the engine can speak, for the Language list; empty = the engine takes none (the voice fixes it)
    can_style: bool  # takes a style note on its preset voices
    can_clone: bool  # can speak in a voice made from a recording of a person
    can_design: bool  # can make a voice from a written description
    max_chars: int  # the most characters one piece should have (a starting value, to be tuned on the Spark, §38.14)
    min_chars: int  # a paragraph shorter than this is joined to the next, because very short pieces are an engine's weak spot
    sample_rate: int  # the rate the engine is expected to give; the audio itself says what it really is, and the studio trusts that
    interruptible: bool  # whether Cancel can stop a piece that is being made, and not only between pieces
    memory_gb: float  # an estimate of what the loaded model takes, for the pre-flight check (§9a)
    model_id: str  # the model's Hugging Face id or file, for display and for the status
    # Qwen3-TTS has three checkpoints and only one is in memory at a time; an engine with one model has the single variant "default".
    variants: tuple[str, ...] = ("default",)
    preset_variant: str = "default"  # the variant that speaks the preset voices
    clone_variant: Optional[str] = None  # the variant that speaks a saved voice (designed or cloned), if the engine can
    design_variant: Optional[str] = None  # the variant that makes candidate voices from a description, if the engine can
    notes: str = ""  # anything the page should say about this engine, in one sentence

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "licence": self.licence.as_dict(), "languages": list(self.languages),
            "can_style": self.can_style, "can_clone": self.can_clone, "can_design": self.can_design,
            "max_chars": self.max_chars, "min_chars": self.min_chars, "sample_rate": self.sample_rate,
            "interruptible": self.interruptible, "memory_gb": self.memory_gb, "model_id": self.model_id,
            "variants": list(self.variants), "preset_variant": self.preset_variant, "clone_variant": self.clone_variant,
            "design_variant": self.design_variant, "notes": self.notes,
        }


@dataclass(frozen=True)
class VoiceRef:
    """A saved voice as the engine needs it: the reference clip (an absolute path the worker has already checked) and the words said in it."""

    path: str
    text: str
    kind: str  # "designed" or "cloned"


@dataclass(frozen=True)
class PieceRequest:
    """One call to an engine: speak `text` as a piece of a reading (DESIGN.md §38.5 item 4)."""

    text: str
    voice: str = ""  # a preset voice's id; empty when `ref` is set
    ref: Optional[VoiceRef] = None  # a saved voice, used through the engine's clone variant
    style: Optional[str] = None  # the style note, already dropped by the caller for a voice that takes none
    language: Optional[str] = None  # None = the engine decides ("Auto")
    seed: int = 0
    design: Optional[str] = None  # a description of a voice to invent (the design variant only)


@dataclass(frozen=True)
class Speech:
    """What an engine made: 16-bit mono little-endian PCM and the sample rate the engine really produced."""

    pcm: bytes
    sample_rate: int


class SpeechEngine(Protocol):
    """What an engine class provides. `INFO` and `PRESETS` are class attributes, so the API process can describe an engine without an instance."""

    INFO: EngineInfo
    PRESETS: tuple[Voice, ...]

    @classmethod
    def probe(cls) -> dict[str, Any]:
        """Can this engine run here? Cheap: imports the libraries and looks for the files, loads no weights. Raises `PipelineUnavailable`
        with a reason and a hint when it cannot; returns a dict (it may carry `device`) when it can."""

    def load(self) -> dict[str, Any]:
        """Load the model. Raises `PipelineError`. Returns a dict of facts about the loaded model (it may carry `device`)."""

    def synthesize(self, request: PieceRequest, cancelled: CancelCheck) -> Speech:
        """Speak one piece. Raises `PipelineError`, or `Canceled` when `cancelled()` became true."""

    def unload(self) -> None:
        """Let go of the model's memory. The worker process exits right after, so this is a courtesy for a server the engine started itself."""


@dataclass
class EngineSettings:
    """What an engine is told when it is made: which model, which variant, where model files may come from. The real engines read their
    own model id from the registry's `model_for`, so this record holds only what the worker received."""

    variant: str = "default"
    model: str = ""  # a model id or path that replaces the engine's own default; empty = the default
    hub_mode: str = "auto"  # as for the other models (DESIGN.md §26.11)
    step_delay_ms: int = 0  # the fake engines only: a pause per piece, so a test can see a reading wait
    extra: dict[str, Any] = field(default_factory=dict)  # per-engine settings the worker was given (e.g. Voxtral's GPU share)


class EngineBase:
    """What every engine class shares, so each engine file holds only what is its own: it keeps the settings it was made with, answers `name`
    (the worker logs it and the status shows it) and does nothing on `unload` unless it started something that must be stopped."""

    INFO: EngineInfo
    PRESETS: tuple[Voice, ...] = ()

    def __init__(self, settings: EngineSettings):
        self.settings = settings

    @property
    def name(self) -> str:
        return self.INFO.id

    def unload(self) -> None:
        return None

    def download_plan(self) -> Any:
        """What this engine will download into the cache while it loads, for the progress shown (DESIGN.md section 39): a `DownloadPlan`, or None when it does not say."""
        return None
