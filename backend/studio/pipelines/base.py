"""Interface shared by the real and fake pipelines. Runs inside the worker process only."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from PIL import Image

StepCallback = Callable[[int, int], None]  # (step, total_steps)


@dataclass(frozen=True)
class ImageJob:
    run_id: str
    mode: str
    prompt: str  # effective prompt (already wrapped for RGBA if requested)
    negative_prompt: Optional[str]
    width: Optional[int]
    height: Optional[int]
    steps: int
    cfg_scale: Optional[float]
    seeds: list[int] = field(default_factory=list)
    transparent: bool = False
    model_id: str = ""
    input_path: Optional[str] = None


class PipelineError(Exception):
    """A failure reported to the API with a kind, a message and an optional hint."""

    kind = "error"

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "hint": self.hint}


class PipelineUnavailable(PipelineError):
    kind = "unavailable"


class PipelineLoadError(PipelineError):
    kind = "load_failed"


class OutOfMemory(PipelineError):
    kind = "out_of_memory"


class Pipeline(Protocol):
    name: str

    def load(self) -> dict[str, Any]:
        """Load weights. Returns info such as {"supports": {...}}. Raises PipelineError."""

    def generate(self, job: ImageJob, index: int, seed: int, on_step: StepCallback) -> Image.Image:
        """Produce image `index` of the job. Raises PipelineError."""
