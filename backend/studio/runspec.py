"""Request models for creating a run, and their resolution into a fully specified run.

Pydantic checks the *shape* of the request (types, no unknown fields). `resolve_run`
then applies the limits from DESIGN.md §6 and the server configuration, and reports
every problem with its field location so the UI can show it next to the control.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import presets as P
from .config import Settings

_ID = re.compile(r"^[0-9a-f]{32}$")

# What the page is told it can offer (GET /api/capabilities). The API already accepts Edit runs (M5a), but the
# page has no way to make one until M5b, so Edit is not offered yet: the mode switch stays disabled.
OFFERED_MODES: tuple[str, ...] = ("generate",)


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
    resolution: Optional[int] = None  # Edit only: 1024 or 2048, sizes every input and the result (DESIGN.md §21.4)
    shape_from: Optional[int] = None  # Edit only: 1-based number of the image the result's shape follows (Size on Auto)
    draft: bool = False  # a small, quick try of the prompt (DESIGN.md §22.2): limited by the server, runs ahead of the queue


class InputRef(BaseModel):
    """One image of an edit: an upload that is waiting (`upload_id`), or an image from an earlier run
    (`image_id`, a result or an input). `role` is display-only for now (DESIGN.md §21.5)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    upload_id: Optional[str] = None
    image_id: Optional[str] = None
    role: Literal["reference", "marked", "mask"] = "reference"


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["generate", "edit"] = "generate"
    prompt: str
    options: RunOptions = Field(default_factory=RunOptions)
    input_images: Optional[list[InputRef]] = None  # Edit only, in the order the model sees them


class RunRequestError(Exception):
    """One or more fields failed validation. Rendered like FastAPI's own 422 errors."""

    def __init__(self, errors: list[tuple[tuple[Any, ...], str]]):
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
    inputs: tuple[InputRef, ...] = ()
    resolution: Optional[int] = None  # Edit: 1024 or 2048 (default 1024); None in Generate
    shape_from: Optional[int] = None  # Edit with Size on Auto: the image the result follows, if one was chosen
    draft: bool = False

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
            "resolution": self.resolution,
            "shape_from": self.shape_from,
            "roles": [ref.role for ref in self.inputs],
            "draft": self.draft,
        }


def resolve_run(req: RunCreate, settings: Settings) -> ResolvedRun:
    errors: list[tuple[tuple[Any, ...], str]] = []
    opts = req.options

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

    if opts.draft:  # small by rule, because it is allowed to jump the queue (DESIGN.md §22.2)
        if req.mode != "generate":
            errors.append((("options", "draft"), "Drafts are only for Generate."))
        elif opts.width is None or opts.height is None:
            errors.append((("options", "draft"), "A draft needs an explicit size."))
        else:
            if max(opts.width, opts.height) > settings.draft_size:
                errors.append((("options", "width"), f"A draft is at most {settings.draft_size} pixels on its long side."))
            if opts.steps > settings.draft_steps:
                errors.append((("options", "steps"), f"A draft uses at most {settings.draft_steps} steps."))
            if opts.num_images != 1:
                errors.append((("options", "num_images"), "A draft makes one image."))

    refs = tuple(req.input_images or ())
    resolution: Optional[int] = None
    if req.mode == "generate":
        if refs:
            errors.append((("input_images",), "Images are only used in Edit mode."))
        if opts.resolution is not None:
            errors.append((("options", "resolution"), "Resolution is only used in Edit mode."))
        if opts.shape_from is not None:
            errors.append((("options", "shape_from"), "Following an image's shape is only used in Edit mode."))
    else:
        cap = settings.max_input_images
        if not refs:
            errors.append((("input_images",), "Add at least one image to edit."))
        elif len(refs) > cap:
            errors.append((("input_images",), f"An edit takes at most {cap} images; this one has {len(refs)}."))
        for i, ref in enumerate(refs):
            given = [value for value in (ref.upload_id, ref.image_id) if value is not None]
            if len(given) != 1:
                errors.append((("input_images", i), f"Image {i + 1}: give either an upload_id or an image_id."))
            elif not _ID.match(given[0]):
                errors.append((("input_images", i), f"Image {i + 1}: that isn't a valid id."))
        if refs and all(ref.role == "mask" for ref in refs):
            errors.append((("input_images",), "At least one image must be a picture to edit, not only masks."))
        resolution = opts.resolution if opts.resolution is not None else P.DEFAULT_RESOLUTION
        if resolution not in P.RESOLUTIONS:
            errors.append((("options", "resolution"), f"Must be one of {', '.join(str(r) for r in P.RESOLUTIONS)}."))
        if opts.shape_from is not None:
            if width is not None:
                errors.append((("options", "shape_from"), "Following an image's shape only applies when Size is Auto."))
            elif not 1 <= opts.shape_from <= max(len(refs), 1):
                errors.append((("options", "shape_from"), f"Must be between 1 and the number of images ({len(refs)})."))
            elif refs and refs[opts.shape_from - 1].role == "mask":
                errors.append((("options", "shape_from"), "A mask can't set the result's shape; pick one of the pictures."))

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
        inputs=refs,
        resolution=resolution,
        shape_from=opts.shape_from,
        draft=opts.draft,
    )
