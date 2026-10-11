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
from . import vector as V
from .config import Settings

_ID = re.compile(r"^[0-9a-f]{32}$")


class FullSize(BaseModel):
    """What a run that was made smaller than intended should be at full size (DESIGN.md §23.1). The page records it
    so Regenerate larger works from the history; the server checks it and keeps it, and uses it for nothing else."""

    model_config = ConfigDict(extra="forbid", strict=True)

    width: int
    height: int
    steps: int


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
    full: Optional[FullSize] = None  # Generate only: the size and steps this run stands in for (DESIGN.md §23.1)
    duration: Optional[int] = None  # Music only: the most seconds of music to make; the model may stop sooner (DESIGN.md §26.3)
    tracks: Optional[int] = None  # Music only: how many versions of the same description
    fields: Optional[dict[str, str]] = None  # Music only: what the page's fields said, kept for Reuse and never interpreted
    # Vector only (DESIGN.md §31.12.5). Each is None when not sent, and the server then uses the default of §31.12.2 (`steps`, `seed` and `num_images`
    # are the ones every mode has: the steps and the seed of the drawing, and the number of versions). The text ones are plain strings and not Literals
    # so that `vector.check_options` can refuse a wrong one in its own words, the same ones the page shows.
    colours: Optional[int] = None
    palette: Optional[list[str]] = None
    kind: Optional[str] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    layers_by: Optional[str] = None
    arrangement: Optional[str] = None
    bleed_mm: Optional[float] = None
    min_feature_mm: Optional[float] = None
    kerf_mm: Optional[float] = None
    recipe: Optional[str] = None  # the page's edited text for what the studio sends; None means the server builds it (`vector.build_recipe`)


class InputRef(BaseModel):
    """One image of an edit: an upload that is waiting (`upload_id`), or an image from an earlier run
    (`image_id`, a result or an input). `role` is display-only for now (DESIGN.md §21.5)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    upload_id: Optional[str] = None
    image_id: Optional[str] = None
    role: Literal["reference", "marked", "mask"] = "reference"


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["generate", "edit", "music", "vector"] = "generate"
    prompt: str
    options: RunOptions = Field(default_factory=RunOptions)
    input_images: Optional[list[InputRef]] = None  # Edit only, in the order the model sees them
    lyrics: Optional[str] = None  # Music only: absent, empty or blank means instrumental (DESIGN.md §26.1)


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
    full: Optional[FullSize] = None
    lyrics: Optional[str] = None  # Music: what the user wrote, None for an instrumental track
    duration: Optional[int] = None  # Music: seconds, an upper bound
    fields: Optional[dict[str, str]] = None  # Music: the page's fields, for Reuse
    # Vector: what the clean-up is asked for. For a vector run `prompt` is the user's idea, `effective_prompt` the recipe sent to the model,
    # `negative_prompt` the fixed negative, `width` and `height` the drawing's pixel size and `cfg_scale` the guidance (§31.12.6).
    vector: Optional[V.VectorOptions] = None

    @property
    def instrumental(self) -> bool:
        return self.mode == "music" and self.lyrics is None

    @property
    def seeds(self) -> list[int]:
        return [self.seed + i for i in range(self.num_images)]

    def options_snapshot(self) -> dict[str, Any]:
        """Exactly what the run used; Reuse and Retry restore from this."""
        if self.mode == "vector":
            # the fixture's `run.options` (tests/vector_contract.json): the clean-up's options, the drawing's, and the words that were sent
            options = self.vector
            assert options is not None  # a vector run always has them
            return {
                "colours": options.colours,
                "palette": list(options.palette),
                "kind": options.kind,
                "width_mm": options.width_mm,
                "height_mm": options.height_mm,
                "layers_by": options.layers_by,
                "arrangement": options.arrangement,
                "bleed_mm": options.bleed_mm,
                "min_feature_mm": options.min_feature_mm,
                "kerf_mm": options.kerf_mm,
                "steps": self.steps,
                "seed": self.seed,
                "num_images": self.num_images,
                "recipe": self.effective_prompt,  # what was sent, whether the page edited it or the server built it
                "seed_was_random": self.seed_was_random,
                "negative": self.negative_prompt,
                "draw_width": self.width,
                "draw_height": self.height,
            }
        if self.mode == "music":
            return {
                "duration": self.duration,
                "steps": self.steps,
                "seed": self.seed,
                "seed_was_random": self.seed_was_random,
                "tracks": self.num_images,
                "instrumental": self.instrumental,
                "fields": self.fields or {},
            }
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
            "full": self.full.model_dump() if self.full else None,
        }


# The options that mean something only for a Vector run: sending one with another kind of run is a mistake the page should hear about, as it is for
# the music options in `resolve_run`.
_ONLY_FOR_VECTOR = ("colours", "palette", "kind", "width_mm", "height_mm", "layers_by", "arrangement", "bleed_mm", "min_feature_mm", "kerf_mm", "recipe")


def _vector_only_errors(sent: set[str]) -> list[tuple[tuple[Any, ...], str]]:
    """The 422 field errors for any Vector-only option that was sent with a run that is not a Vector run."""
    return [(("options", name), "Only used for Vector.") for name in _ONLY_FOR_VECTOR if name in sent]


_MUSIC_FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# Options that mean something for a picture and nothing for a track: sending one is a mistake the page should hear about.
_NOT_FOR_MUSIC = ("width", "height", "num_images", "negative_prompt", "cfg_scale", "transparent", "resolution", "shape_from",
                  "draft", "full")


def _resolve_music(req: RunCreate, settings: Settings) -> ResolvedRun:
    """A music run (DESIGN.md §26.3): the description, optional lyrics, and a few limits. Instrumental when the lyrics
    are absent or blank."""
    errors: list[tuple[tuple[Any, ...], str]] = []
    opts = req.options
    sent = opts.model_fields_set

    description = req.prompt.strip()
    if not description:
        errors.append((("prompt",), "Describe the music."))
    elif len(description) > P.MUSIC_DESCRIPTION_MAX:
        errors.append((("prompt",), f"The description is {len(description)} characters; the limit is {P.MUSIC_DESCRIPTION_MAX}."))

    lyrics = (req.lyrics or "").strip() or None
    if lyrics is not None and len(lyrics) > P.MUSIC_LYRICS_MAX:
        errors.append((("lyrics",), f"The lyrics are {len(lyrics)} characters; the limit is {P.MUSIC_LYRICS_MAX}."))

    duration = opts.duration if opts.duration is not None else P.MUSIC_DEFAULT_SECONDS
    if not P.MUSIC_DURATION_MIN <= duration <= settings.music_max_seconds:
        errors.append((("options", "duration"), f"Must be between {P.MUSIC_DURATION_MIN} and {settings.music_max_seconds} seconds."))

    tracks = opts.tracks if opts.tracks is not None else 1
    if not 1 <= tracks <= settings.music_max_tracks:
        errors.append((("options", "tracks"), f"Must be between 1 and {settings.music_max_tracks}."))

    steps = opts.steps if "steps" in sent else P.MUSIC_DEFAULT_STEPS
    if not P.MUSIC_STEPS_MIN <= steps <= P.MUSIC_STEPS_MAX:
        errors.append((("options", "steps"), f"Must be between {P.MUSIC_STEPS_MIN} and {P.MUSIC_STEPS_MAX}."))

    if opts.seed is not None and not 0 <= opts.seed <= P.SEED_MAX:
        errors.append((("options", "seed"), f"Must be between 0 and {P.SEED_MAX}."))

    fields = opts.fields
    if fields is not None:
        if len(fields) > P.MUSIC_FIELDS_MAX:
            errors.append((("options", "fields"), f"At most {P.MUSIC_FIELDS_MAX} fields."))
        for key, value in fields.items():
            if not _MUSIC_FIELD_KEY.match(key):
                errors.append((("options", "fields", key), "A field name is lowercase letters, digits and underscores."))
            elif len(value) > P.MUSIC_FIELD_MAX:
                errors.append((("options", "fields", key), f"At most {P.MUSIC_FIELD_MAX} characters."))

    for name in _NOT_FOR_MUSIC:
        if name in sent:
            errors.append((("options", name), "Not used for music."))
    errors.extend(_vector_only_errors(sent))
    if req.input_images:
        errors.append((("input_images",), "Images are only used in Edit mode."))
    if errors:
        raise RunRequestError(errors)

    seed_was_random = opts.seed is None
    seed = secrets.randbelow(P.SEED_MAX - settings.music_max_tracks + 2) if seed_was_random else opts.seed
    return ResolvedRun(
        mode="music", prompt=description, effective_prompt=description, negative_prompt=None, width=None, height=None,
        steps=steps, seed=seed, seed_was_random=seed_was_random, num_images=tracks, cfg_scale=None, transparent=False,
        lyrics=lyrics, duration=duration, fields=dict(fields) if fields else None,
    )


# Options that mean something for a picture or a track and nothing for a Vector run (DESIGN.md §31.12.5): its size is in millimetres, its guidance and
# negative text are the studio's own, and the rest belong to Generate, Edit and Music.
_NOT_FOR_VECTOR = ("width", "height", "negative_prompt", "cfg_scale", "transparent", "resolution", "shape_from", "draft", "full", "duration", "tracks",
                   "fields")


def _built_recipe(idea: str, options: V.VectorOptions, *, check_length: bool) -> V.Recipe:
    """The recipe the studio builds for an idea (`vector.build_recipe`). One that is longer than an edit of it may be (`RECIPE_MAX_CHARS`) is refused,
    when `check_length` is true: it would be stored as the run's recipe, and Reuse would then send it back as an edit that is too long. (When the page
    sent its own edit, the built text is not used and its length is no concern.)"""
    built = V.build_recipe(idea, options.kind, options.colours, options.palette)
    if check_length and len(built.positive) > V.RECIPE_MAX_CHARS:
        raise RunRequestError([(("prompt",), f"The prompt is too long: with the words the studio adds it makes {len(built.positive)} characters, "
                                             f"and the limit is {V.RECIPE_MAX_CHARS}.")])
    return built


class RecipeRequest(BaseModel):
    """The body of `POST /api/vector/recipe` (DESIGN.md §31.12.7): the few things the words the studio adds depend on. Each but the idea is optional,
    with the same defaults as a run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    prompt: str
    kind: Optional[str] = None
    colours: Optional[int] = None
    palette: Optional[list[str]] = None


def resolve_recipe(req: RecipeRequest, settings: Settings) -> V.Recipe:
    """The recipe for the form to show: what a run with these choices would send. Pure text, no model and no database. The fields are checked as a
    run checks them (so the page hears the same words), and so is the length. Raises `RunRequestError` (a `422`)."""
    errors: list[tuple[tuple[Any, ...], str]] = []
    idea = req.prompt.strip()
    if not idea:
        errors.append((("prompt",), "Describe what to draw."))
    elif len(idea) > settings.max_prompt_chars:
        errors.append((("prompt",), f"The prompt is {len(idea)} characters; the limit is {settings.max_prompt_chars}."))
    chosen = V.VectorOptions(
        colours=req.colours if req.colours is not None else V.COLOURS_DEFAULT, palette=tuple(req.palette or ()),
        kind=req.kind if req.kind is not None else V.KIND_DEFAULT)  # type: ignore[arg-type]
    # the options this body does not carry are the defaults, which are always fine, so any problem found here is with one of the three it does
    errors.extend(((name,), message) for name, message in V.check_options(chosen))
    if errors:
        raise RunRequestError(errors)
    return _built_recipe(idea, chosen, check_length=True)


def _resolve_vector(req: RunCreate, settings: Settings) -> ResolvedRun:
    """A Vector run (DESIGN.md §31.12.5): an idea, the clean-up's options (checked by the engine's own `vector.check_options`, so the wording is the
    same wherever it is shown), and the words the studio adds to the idea (`vector.build_recipe`, unless the page sent its edited text)."""
    errors: list[tuple[tuple[Any, ...], str]] = []
    opts = req.options
    sent = opts.model_fields_set

    idea = req.prompt.strip()
    if not idea:
        errors.append((("prompt",), "Describe what to draw."))
    elif len(idea) > settings.max_prompt_chars:
        errors.append((("prompt",), f"The prompt is {len(idea)} characters; the limit is {settings.max_prompt_chars}."))

    # the clean-up's options: what was sent, else the default. `shape_layer_cap` and `fill_mode` are not on the form (§31.12.9 item 7), so they stay
    # at the engine's defaults.
    chosen = V.VectorOptions(
        colours=opts.colours if opts.colours is not None else V.COLOURS_DEFAULT,
        palette=tuple(opts.palette or ()),
        kind=opts.kind if opts.kind is not None else V.KIND_DEFAULT,  # type: ignore[arg-type]
        width_mm=opts.width_mm if opts.width_mm is not None else V.SIZE_MM_DEFAULT,
        height_mm=opts.height_mm if opts.height_mm is not None else V.SIZE_MM_DEFAULT,
        layers_by=opts.layers_by if opts.layers_by is not None else V.LAYERS_BY_DEFAULT,  # type: ignore[arg-type]
        arrangement=opts.arrangement if opts.arrangement is not None else V.ARRANGEMENT_DEFAULT,  # type: ignore[arg-type]
        bleed_mm=opts.bleed_mm if opts.bleed_mm is not None else V.BLEED_MM_DEFAULT,
        min_feature_mm=opts.min_feature_mm if opts.min_feature_mm is not None else V.MIN_FEATURE_MM_DEFAULT,
        kerf_mm=opts.kerf_mm if opts.kerf_mm is not None else V.KERF_MM_DEFAULT,
    )
    errors.extend((("options", name), message) for name, message in V.check_options(chosen))

    if not P.STEPS_MIN <= opts.steps <= P.STEPS_MAX:
        errors.append((("options", "steps"), f"Must be between {P.STEPS_MIN} and {P.STEPS_MAX}."))
    # the number of versions has its own limit (1 to 4), not the picture limit of STUDIO_MAX_IMAGES_PER_RUN
    if not V.VERSIONS_MIN <= opts.num_images <= V.VERSIONS_MAX:
        errors.append((("options", "num_images"), f"Must be between {V.VERSIONS_MIN} and {V.VERSIONS_MAX}."))
    if opts.seed is not None and not 0 <= opts.seed <= P.SEED_MAX:
        errors.append((("options", "seed"), f"Must be between 0 and {P.SEED_MAX}."))

    # a blank edited recipe is the same as none, as a blank negative prompt or blank lyrics are for the other modes: the server builds it
    edited = (opts.recipe or "").strip() or None
    if edited is not None and len(edited) > V.RECIPE_MAX_CHARS:
        errors.append((("options", "recipe"), f"The recipe is {len(edited)} characters; the limit is {V.RECIPE_MAX_CHARS}."))

    for name in _NOT_FOR_VECTOR:
        if name in sent:
            errors.append((("options", name), "Not used for Vector."))
    if req.lyrics is not None:
        errors.append((("lyrics",), "Lyrics are only used for music."))
    if req.input_images:
        errors.append((("input_images",), "Images are only used in Edit mode."))
    if errors:
        raise RunRequestError(errors)

    # The words sent to the model. The negative text is always the studio's; the positive one is the page's edit, or built from the idea (and then
    # checked, as `_built_recipe` says)
    built = _built_recipe(idea, chosen, check_length=edited is None)
    positive = edited if edited is not None else built.positive

    draw_width, draw_height = V.draw_size(chosen.width_mm, chosen.height_mm)
    seed_was_random = opts.seed is None
    seed = secrets.randbelow(P.SEED_MAX - opts.num_images + 2) if seed_was_random else opts.seed  # room for seed + n - 1 under the limit
    return ResolvedRun(
        mode="vector", prompt=idea, effective_prompt=positive, negative_prompt=built.negative, width=draw_width, height=draw_height,
        steps=opts.steps, seed=seed, seed_was_random=seed_was_random, num_images=opts.num_images, cfg_scale=V.RECIPE_CFG_SCALE, transparent=False,
        vector=chosen,
    )


def resolve_run(req: RunCreate, settings: Settings) -> ResolvedRun:
    if req.mode == "music":
        return _resolve_music(req, settings)
    if req.mode == "vector":
        return _resolve_vector(req, settings)
    errors: list[tuple[tuple[Any, ...], str]] = []
    opts = req.options
    sent = opts.model_fields_set
    errors.extend(_vector_only_errors(sent))
    for name in ("duration", "tracks", "fields"):
        if name in sent:
            errors.append((("options", name), "Only used for music."))
    if req.lyrics is not None:
        errors.append((("lyrics",), "Lyrics are only used for music."))

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

    if opts.full is not None:  # the bigger version this run stands in for (DESIGN.md §23.1)
        full = opts.full
        if req.mode != "generate":
            errors.append((("options", "full"), "A full size is only recorded for Generate."))
        else:
            full_ok = True
            for name, value in (("width", full.width), ("height", full.height)):
                if not P.SIZE_MIN <= value <= P.SIZE_MAX:
                    errors.append((("options", "full", name), f"Must be between {P.SIZE_MIN} and {P.SIZE_MAX} pixels."))
                    full_ok = False
                elif value % P.SIZE_MULTIPLE:
                    errors.append((("options", "full", name), f"Must be a multiple of {P.SIZE_MULTIPLE}."))
                    full_ok = False
            if full_ok and full.width * full.height > P.MAX_PIXELS:
                errors.append((
                    ("options", "full", "width"),
                    f"{full.width}x{full.height} is {full.width * full.height / 1e6:.2f} MP; the limit is {P.MAX_PIXELS / 1e6:.1f} MP.",
                ))
            if not P.STEPS_MIN <= full.steps <= P.STEPS_MAX:
                errors.append((("options", "full", "steps"), f"Must be between {P.STEPS_MIN} and {P.STEPS_MAX}."))
            if full_ok and width is not None and height is not None and not (
                full.width >= width and full.height >= height and (full.width, full.height) != (width, height)
            ):
                errors.append((("options", "full"), f"The full size must be larger than the run ({width}x{height}) and smaller in neither side."))

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
        full=opts.full,
    )
