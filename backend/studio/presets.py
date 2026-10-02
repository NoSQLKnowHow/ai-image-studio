"""Model-card presets and per-run limits (DESIGN.md §6)."""

from __future__ import annotations

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
SIZE_MULTIPLE = 16
MAX_PIXELS = 4_500_000  # to be confirmed on the Spark (DESIGN.md §18 item 7)

STEPS_MIN, STEPS_MAX, DEFAULT_STEPS = 1, 100, 40
SEED_MAX = 2**32 - 1
CFG_MIN, CFG_MAX = 0.1, 20.0

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
