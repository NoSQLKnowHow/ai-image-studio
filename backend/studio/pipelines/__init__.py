"""Image pipelines. Imported only by the worker process (the real one pulls in PyTorch)."""

from __future__ import annotations

from typing import Any

from .base import ImageJob, OutOfMemory, Pipeline, PipelineError, PipelineLoadError, PipelineUnavailable

__all__ = [
    "ImageJob", "OutOfMemory", "Pipeline", "PipelineError", "PipelineLoadError", "PipelineUnavailable",
    "make_pipeline", "probe_pipeline",
]


def probe_pipeline(name: str) -> dict[str, Any]:
    """Cheap capability check without loading weights. Raises PipelineError."""
    if name == "fake":
        from .fake import FakePipeline

        return FakePipeline.probe()
    from .real import probe

    return probe()


def make_pipeline(name: str, *, model: str, cpu_offload: bool, local_files_only: bool, fake_step_delay_ms: int) -> Pipeline:
    if name == "fake":
        from .fake import FakePipeline

        return FakePipeline(step_delay_ms=fake_step_delay_ms)
    if name == "real":
        from .real import RealPipeline

        return RealPipeline(model, cpu_offload=cpu_offload, local_files_only=local_files_only)
    raise ValueError(f"Unknown pipeline {name!r}")
