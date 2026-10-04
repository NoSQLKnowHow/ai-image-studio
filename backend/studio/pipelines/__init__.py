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

KINDS = ("image", "music")  # which model a worker holds (DESIGN.md §26.4)


def probe_pipeline(name: str, kind: str = "image") -> dict[str, Any]:
    """Cheap capability check without loading weights. Raises PipelineError."""
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


def make_pipeline(name: str, *, model: str, cpu_offload: bool, hub_mode: str, fake_step_delay_ms: int, kind: str = "image") -> Any:
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
