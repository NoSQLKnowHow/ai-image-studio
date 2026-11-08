"""Image pipelines. Imported only by the worker process (the real one pulls in PyTorch)."""

from __future__ import annotations

from typing import Any

from .base import (
    Canceled, ImageJob, MusicJob, MusicResult, OutOfMemory, Pipeline, PipelineError, PipelineLoadError, PipelineUnavailable,
)

__all__ = [
    "Canceled", "ImageJob", "MusicJob", "MusicResult", "OutOfMemory", "Pipeline", "PipelineError", "PipelineLoadError",
    "PipelineUnavailable", "make_pipeline", "probe_pipeline",
]

KINDS = ("image", "music", "speech")  # which model a worker holds (DESIGN.md §26.4, §38.3)


def probe_pipeline(name: str, kind: str = "image", engine: str = "") -> dict[str, Any]:
    """Cheap capability check without loading weights. Raises PipelineError. A speech worker is asked about one engine (DESIGN.md §38.3)."""
    if kind == "speech":
        from ..speech.registry import probe_engine

        return probe_engine(engine)
    if kind == "music":
        if name == "fake":
            from .fake_music import FakeMusicPipeline

            return FakeMusicPipeline.probe()
        from .real_music import probe as probe_music

        return probe_music()
    if name == "fake":
        from .fake import FakePipeline

        return FakePipeline.probe()
    from .real import probe

    return probe()


def make_pipeline(name: str, *, model: str, cpu_offload: bool, hub_mode: str, fake_step_delay_ms: int, kind: str = "image",
                  engine: str = "", variant: str = "") -> Any:
    if kind == "speech":  # one speech engine in one of its variants (DESIGN.md §38.3); `name` only says whether the fakes may be used
        from ..speech.engine import EngineSettings
        from ..speech.registry import info, make_engine

        if (name == "fake") != engine.startswith("fake"):
            raise ValueError(f"The {engine!r} speech engine does not go with the {name!r} pipeline.")
        return make_engine(engine, EngineSettings(variant=variant or info(engine).preset_variant, model=model, hub_mode=hub_mode,
                                                  step_delay_ms=fake_step_delay_ms if name == "fake" else 0))
    if kind == "music":
        if name == "fake":
            from .fake_music import FakeMusicPipeline

            return FakeMusicPipeline(step_delay_ms=fake_step_delay_ms)
        if name == "real":
            from .real_music import RealMusicPipeline

            return RealMusicPipeline(model, hub_mode=hub_mode)
        raise ValueError(f"Unknown pipeline {name!r}")
    if name == "fake":
        from .fake import FakePipeline

        return FakePipeline(step_delay_ms=fake_step_delay_ms)
    if name == "real":
        from .real import RealPipeline

        return RealPipeline(model, cpu_offload=cpu_offload, hub_mode=hub_mode)
    raise ValueError(f"Unknown pipeline {name!r}")
