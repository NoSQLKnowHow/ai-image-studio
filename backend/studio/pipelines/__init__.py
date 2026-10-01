"""Image pipelines. Imported only by the worker process (the real one pulls in PyTorch)."""

from __future__ import annotations

from .base import ImageJob, OutOfMemory, Pipeline, PipelineError, PipelineLoadError, PipelineUnavailable

__all__ = [
    "ImageJob", "OutOfMemory", "Pipeline", "PipelineError", "PipelineLoadError", "PipelineUnavailable",
    "make_pipeline",
]


def make_pipeline(name: str, *, model: str, cpu_offload: bool, local_files_only: bool, fake_step_delay_ms: int) -> Pipeline:
    if name == "fake":
        from .fake import FakePipeline

        return FakePipeline(step_delay_ms=fake_step_delay_ms)
    if name == "real":
        from .real import RealPipeline

        return RealPipeline(model, cpu_offload=cpu_offload, local_files_only=local_files_only)
    raise ValueError(f"Unknown pipeline {name!r}")
