"""Request models for creating a run, and their resolution into a fully specified run.

Pydantic checks the *shape* of the request (types, no unknown fields). `resolve_run`
then applies the limits from DESIGN.md §6 and the server configuration, and reports
every problem with its field location so the UI can show it next to the control.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import presets as P
from .config import Settings

# Edit mode is enabled in milestone M5.
AVAILABLE_MODES: tuple[str, ...] = ("generate",)


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    width: Optional[int] = None
    height: Optional[int] = None
    steps: int = P.DEFAULT_STEPS
    seed: Optional[int] = None  # None = pick a random seed on the server
    num_images: int = 1
    negative_prompt: Optional[str] = None
    cfg_scale: Optional[float] = None
    transparent: bool = False


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["generate", "edit"] = "generate"
    prompt: str
    options: RunOptions = Field(default_factory=RunOptions)
    input_image: Optional[dict[str, Any]] = None  # used by Edit mode (M5)


class RunRequestError(Exception):
    """One or more fields failed validation. Rendered like FastAPI's own 422 errors."""

    def __init__(self, errors: list[tuple[tuple[str, ...], str]]):
        super().__init__("; ".join(msg for _, msg in errors))
        self.errors = errors

    def detail(self) -> list[dict[str, Any]]:
        return [{"loc": ["body", *loc], "msg": msg, "type": "value_error"} for loc, msg in self.errors]


@dataclass(frozen=True)
class ResolvedRun:
    mode: str
    prompt: str  # as typed by the user (used for filenames and display)
    effective_prompt: str  # what the model receives (e.g. with the RGBA wrapper)
    negative_prompt: Optional[str]
    width: Optional[int]  # None = let the pipeline size the result (Edit mode)
    height: Optional[int]
    steps: int
    seed: int
    seed_was_random: bool
    num_images: int
    cfg_scale: Optional[float]
    transparent: bool
    input_image_id: Optional[str] = None

    @property
    def seeds(self) -> list[int]:
        return [self.seed + i for i in range(self.num_images)]

    def options_snapshot(self) -> dict[str, Any]:
        """Exactly what the run used; Reuse and Retry restore from this."""
        return {
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "seed": self.seed,
            "seed_was_random": self.seed_was_random,
            "num_images": self.num_images,
            "negative_prompt": self.negative_prompt,
            "cfg_scale": self.cfg_scale,
            "transparent": self.transparent,
        }


def resolve_run(req: RunCreate, settings: Settings) -> ResolvedRun:
    errors: list[tuple[tuple[str, ...], str]] = []
    opts = req.options

    if req.mode not in AVAILABLE_MODES:
        errors.append((("mode",), "Edit mode is not available yet; it arrives in milestone M5."))

    prompt = req.prompt.strip()
    if not prompt:
        errors.append((("prompt",), "Enter a prompt."))
    elif len(prompt) > settings.max_prompt_chars:
        errors.append((("prompt",), f"The prompt is {len(prompt)} characters; the limit is {settings.max_prompt_chars}."))

    negative = (opts.negative_prompt or "").strip() or None
    if negative and len(negative) > settings.max_prompt_chars:
        errors.append((("options", "negative_prompt"), f"The negative prompt is longer than {settings.max_prompt_chars} characters."))

    width, height = opts.width, opts.height
    if (width is None) != (height is None):
        missing = "width" if width is None else "height"
        errors.append((("options", missing), "Width and height must be given together."))
    elif width is None:
        if req.mode == "generate":
            width, height = P.DEFAULT_SIZE
    else:
        size_ok = True
        for name, value in (("width", width), ("height", height)):
            if not P.SIZE_MIN <= value <= P.SIZE_MAX:
                errors.append((("options", name), f"Must be between {P.SIZE_MIN} and {P.SIZE_MAX} pixels."))
                size_ok = False
            elif value % P.SIZE_MULTIPLE:
                errors.append((("options", name), f"Must be a multiple of {P.SIZE_MULTIPLE}."))
                size_ok = False
        if size_ok and width * height > P.MAX_PIXELS:
            errors.append((
                ("options", "width"),
                f"{width}x{height} is {width * height / 1e6:.2f} MP; the limit is {P.MAX_PIXELS / 1e6:.1f} MP.",
            ))

    if not P.STEPS_MIN <= opts.steps <= P.STEPS_MAX:
        errors.append((("options", "steps"), f"Must be between {P.STEPS_MIN} and {P.STEPS_MAX}."))
    if not 1 <= opts.num_images <= settings.max_images_per_run:
        errors.append((("options", "num_images"), f"Must be between 1 and {settings.max_images_per_run}."))
    if opts.seed is not None and not 0 <= opts.seed <= P.SEED_MAX:
        errors.append((("options", "seed"), f"Must be between 0 and {P.SEED_MAX}."))
    if opts.cfg_scale is not None and not P.CFG_MIN <= opts.cfg_scale <= P.CFG_MAX:
        errors.append((("options", "cfg_scale"), f"Must be between {P.CFG_MIN} and {P.CFG_MAX}."))
    if opts.transparent and req.mode != "generate":
        errors.append((("options", "transparent"), "Transparent output is only available in Generate mode."))
    if req.input_image is not None and req.mode == "generate":
        errors.append((("input_image",), "A reference image is only used in Edit mode."))

    if errors:
        raise RunRequestError(errors)

    seed_was_random = opts.seed is None
    seed = secrets.randbelow(P.SEED_MAX - settings.max_images_per_run + 2) if seed_was_random else opts.seed

    return ResolvedRun(
        mode=req.mode,
        prompt=prompt,
        effective_prompt=P.apply_transparent_format(prompt) if opts.transparent else prompt,
        negative_prompt=negative,
        width=width,
        height=height,
        steps=opts.steps,
        seed=seed,
        seed_was_random=seed_was_random,
        num_images=opts.num_images,
        cfg_scale=opts.cfg_scale,
        transparent=opts.transparent,
    )
