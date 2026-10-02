"""Model-card presets and per-run limits (DESIGN.md §6)."""

from __future__ import annotations

import math

# From the Qwen-Image-2.1 model card ("Supported Aspect Ratios"): (width, height)
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "1:1": (2048, 2048),
    "4:3": (2400, 1792),
    "3:4": (1792, 2400),
    "3:2": (2528, 1696),
    "2:3": (1696, 2528),
    "16:9": (2752, 1536),
    "9:16": (1536, 2752),
}
DEFAULT_ASPECT = "1:1"
DEFAULT_SIZE = ASPECT_RATIOS[DEFAULT_ASPECT]

SIZE_MIN = 256
SIZE_MAX = 4096
# QwenImage21Pipeline requires height and width divisible by 2 x vae_scale_factor (16) = 32
# (diffusers main @ 578c9b2c, pipeline_qwenimage21.py check_inputs). All model-card presets comply.
SIZE_MULTIPLE = 32
MAX_PIXELS = 4_500_000  # to be confirmed on the Spark (DESIGN.md §18 item 7)

STEPS_MIN, STEPS_MAX, DEFAULT_STEPS = 1, 100, 40
SEED_MAX = 2**32 - 1
CFG_MIN, CFG_MAX = 0.1, 20.0

# Edit mode (DESIGN.md §21). Every input is resized to about RESOLUTION x RESOLUTION pixels by the pipeline,
# and so is the result unless a size is given. 1K is about 1 megapixel, 2K about 4.
RESOLUTIONS = (1024, 2048)
DEFAULT_RESOLUTION = 1024
ROLES = ("reference", "marked", "mask")  # "marked" and "mask" arrive with local edits (M5d)
UPLOAD_MAX_PIXELS = 16_000_000  # per uploaded image (DESIGN.md §11)
UPLOAD_FORMATS = ("PNG", "JPEG", "WEBP")


def calculate_dimensions(target_area: float, ratio: float) -> tuple[int, int]:
    """The size the pipeline gives an image of aspect ratio `ratio` (width / height) at `target_area` pixels:
    a copy of `calculate_dimensions` in diffusers @ 578c9b2c (pipeline_qwenimage21.py), sides rounded to a multiple
    of 32 with Python's own round(). The studio uses it to name the size itself when the result should follow
    an image other than the last one; a test pins its answers to the pipeline's."""
    width = math.sqrt(target_area * ratio)
    height = width / ratio
    return round(width / 32) * 32, round(height / 32) * 32


# From the model card ("Transparent Image Generation (RGBA)")
TRANSPARENT_PREFIX = "This is an RGBA image with transparency."
TRANSPARENT_SUFFIX = "The image has alpha channel and the background is transparent."


def apply_transparent_format(prompt: str) -> str:
    """Wrap a prompt in the model card's recommended RGBA format (idempotent)."""
    if "rgba" in prompt.lower():
        return prompt
    if prompt[-1] not in ".!?":
        prompt += "."
    return f"{TRANSPARENT_PREFIX} {prompt} {TRANSPARENT_SUFFIX}"
