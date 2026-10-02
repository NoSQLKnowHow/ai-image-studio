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
    input_paths: list[str] = field(default_factory=list)  # Edit: the images, in the order the model sees them
    resolution: Optional[int] = None  # Edit: 1024 or 2048; sizes every input and, on Auto, the result


class Canceled(BaseException):
    """Raised from the step callback to stop an image the user cancelled.

    Deliberately not an `Exception`: it must pass through every `except Exception` between the
    callback and the worker (the pipeline's own, translate-this-error handlers included) and
    never be mistaken for a failure. The worker catches it and reports `run_canceled`."""


class PipelineError(Exception):
    """A failure reported to the API with a kind, a message and an optional hint."""

    kind = "error"

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "hint": self.hint}


def load_inputs(paths: list[str]) -> list[Image.Image]:
    """Open an edit's input images exactly as stored: 8-bit RGB or RGBA, never flattened (the model reads the
    alpha channel). Raises PipelineError when one can't be read."""
    images: list[Image.Image] = []
    for path in paths:
        try:
            with Image.open(path) as im:
                im.load()
                images.append(im.copy())
        except (OSError, ValueError, SyntaxError) as exc:
            raise PipelineError(
                f"An input image could not be read ({getattr(exc, 'strerror', None) or exc}).",
                hint="The file may have been removed from the data folder; add the images again.",
            ) from exc
    return images


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
