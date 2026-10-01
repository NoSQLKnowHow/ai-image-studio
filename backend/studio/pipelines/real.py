"""The real Qwen-Image-2.1 pipeline. Implemented in milestone M2.

Until then it reports itself as unavailable, so a server started with the default
STUDIO_PIPELINE=real says clearly what is missing instead of failing obscurely.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from .base import ImageJob, PipelineUnavailable, StepCallback

_MESSAGE = "The real Qwen-Image-2.1 pipeline is added in milestone M2."
_HINT = "Set STUDIO_PIPELINE=fake to use the test pipeline until then."


class RealPipeline:
    name = "real"
    SUPPORTS: dict[str, bool] = {}

    def __init__(self, model: str, cpu_offload: bool = False, local_files_only: bool = False):
        self.model = model
        self.cpu_offload = cpu_offload
        self.local_files_only = local_files_only

    def load(self) -> dict[str, Any]:
        raise PipelineUnavailable(_MESSAGE, hint=_HINT)

    def generate(self, job: ImageJob, index: int, seed: int, on_step: StepCallback) -> Image.Image:
        raise PipelineUnavailable(_MESSAGE, hint=_HINT)
