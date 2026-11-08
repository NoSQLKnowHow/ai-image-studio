"""The list of speech engines, and the one place that names them (DESIGN.md §38.3).

**Adding a model is one file in `engines/` and one line in `REAL_ENGINES` below.** Nothing else in the studio names an engine, and a test scans
the source to keep it that way. An engine is imported only when it is asked for, so a missing library of one engine never stops another.

`STUDIO_PIPELINE=fake` offers the two fake engines and nothing else; the real setting offers the real ones, narrowed by `STUDIO_SPEECH_ENGINES`.
"""

from __future__ import annotations

import importlib
from typing import Any, Optional, Type

from ..pipelines.base import PipelineUnavailable
from .engine import EngineInfo, EngineSettings, SpeechEngine, Voice

# id -> (module, class). The order is the order the page lists them in: the two the owner asked for first, then the two added with them (§38 answers 1 and 8).
REAL_ENGINES: dict[str, tuple[str, str]] = {
    "qwen3-tts": ("studio.speech.engines.qwen3", "Qwen3TTS"),
    "voxtral": ("studio.speech.engines.voxtral", "Voxtral"),
    "kokoro": ("studio.speech.engines.kokoro", "Kokoro"),
    "piper": ("studio.speech.engines.piper", "Piper"),
}
FAKE_ENGINES: dict[str, tuple[str, str]] = {
    "fake": ("studio.speech.engines.fake", "FakeSpeech"),
    "fake-basic": ("studio.speech.engines.fake", "FakeBasicSpeech"),
}

# Engines added while the program runs. Only a test does this (to prove that a new engine needs nothing else); the key is the engine's id.
_ADDED: dict[str, Type[Any]] = {}


class UnknownEngine(KeyError):
    """No engine has this id (or it is not offered by the current pipeline setting)."""


def register(engine_class: Type[Any]) -> None:
    """Add an engine class for the life of the process (tests)."""
    _ADDED[engine_class.INFO.id] = engine_class


def unregister(engine_id: str) -> None:
    _ADDED.pop(engine_id, None)


def engine_ids(pipeline: str, enabled: tuple[str, ...] = ()) -> list[str]:
    """The ids of the engines this setting offers, in display order. `enabled` is `STUDIO_SPEECH_ENGINES`; empty means all of them."""
    known = list((FAKE_ENGINES if pipeline == "fake" else REAL_ENGINES))
    known += [engine_id for engine_id in _ADDED if engine_id not in known]
    if enabled:
        known = [engine_id for engine_id in known if engine_id in enabled]
    return known


def engine_class(engine_id: str) -> Type[SpeechEngine]:
    """The class of an engine, importing its module now. Raises `UnknownEngine`, or `PipelineUnavailable` when the module cannot be imported
    (it needs a library the image does not have): the reason is then the engine's, and shown on its pill."""
    if engine_id in _ADDED:
        return _ADDED[engine_id]
    entry = FAKE_ENGINES.get(engine_id) or REAL_ENGINES.get(engine_id)
    if entry is None:
        raise UnknownEngine(engine_id)
    module_name, class_name = entry
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise PipelineUnavailable(f"The {engine_id} engine is not installed ({exc}).", hint="See the image's build log for this engine.") from exc
    return getattr(module, class_name)


def info(engine_id: str) -> EngineInfo:
    return engine_class(engine_id).INFO


def presets(engine_id: str) -> tuple[Voice, ...]:
    return tuple(engine_class(engine_id).PRESETS)


def make_engine(engine_id: str, settings: EngineSettings) -> SpeechEngine:
    """An engine ready to `load`. `settings.variant` must be one the engine has."""
    cls = engine_class(engine_id)
    if settings.variant not in cls.INFO.variants:
        raise UnknownEngine(f"{engine_id}/{settings.variant}")
    return cls(settings)  # type: ignore[call-arg]


def probe_engine(engine_id: str) -> dict[str, Any]:
    """The engine's own check that it can run here (no weights loaded). Raises `PipelineError` (`PipelineUnavailable` for a missing library)."""
    return engine_class(engine_id).probe()


def variant_for(engine_info: EngineInfo, task: str, saved_voice: bool) -> Optional[str]:
    """Which variant of an engine does a piece of work. `task` is "reading" or "sample" (speak with a preset, or with a saved voice when
    `saved_voice`), or "design" (invent candidate voices). None when the engine cannot do it."""
    if task == "design":
        return engine_info.design_variant
    return engine_info.clone_variant if saved_voice else engine_info.preset_variant
