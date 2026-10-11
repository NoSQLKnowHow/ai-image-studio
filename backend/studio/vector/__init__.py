"""Vector (DESIGN.md §31, version 1.17): an SVG a laser can use, made from a picture.

THIS FILE IS THE CONTRACT BETWEEN THE ENGINE, THE SERVER AND THE PAGE (§31.12). The constants, the records and the signatures below are what the
server (`studio/runspec.py`, `jobs.py`, `api.py`) imports and what the page's limits come from. The engine owns everything in this package:
until it is built, the functions raise `NotImplementedError`, and the server's tests must not depend on them (they use a fake job, §31.12.4).

Why one file of constants: the limits (how many colours, how big a sheet) are checked by the server, shown by the page through
`GET /api/capabilities`, and used by the engine, so they live in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional, Union

# ---- the limits (DESIGN.md §31.3, §31.12.1) -------------------------------------------------------------------------------------------
COLOURS_MIN, COLOURS_MAX, COLOURS_DEFAULT = 2, 8, 2  # includes white, the bare material (§31.7 answer 2b)
SIZE_MM_MIN, SIZE_MM_MAX, SIZE_MM_DEFAULT = 5.0, 1200.0, 100.0  # a side of the sheet
VERSIONS_MIN, VERSIONS_MAX = 1, 4  # pictures from one prompt (the run's `num_images`)
MIN_FEATURE_MM_MIN, MIN_FEATURE_MM_MAX, MIN_FEATURE_MM_DEFAULT = 0.05, 10.0, 0.5
KERF_MM_MIN, KERF_MM_MAX, KERF_MM_DEFAULT = 0.0, 1.0, 0.0  # 0 = no compensation
BLEED_MM_MIN, BLEED_MM_MAX, BLEED_MM_DEFAULT = 0.0, 5.0, 0.5  # only used when stacked
SHAPE_LAYER_CAP = 30  # layers by shape stop here, and say so (§31.10 item 7)
RECIPE_MAX_CHARS = 2000  # the longest edited recipe the server accepts
DRAW_LONG_SIDE = 1536  # the picture Qwen draws has this long side in pixels; the other follows the sheet's shape (a multiple of 32)
# Qwen-Image only weighs the negative prompt when `true_cfg_scale` is above 1 (DESIGN.md §18 item 4: the pipeline's default is 1.0, "no
# guidance"), and the recipe's negative prompt is what keeps out gradients and shading, so a Vector run always sends guidance. 4.0 is the
# value Qwen-Image's own examples use; whether it is the best one for 2.1 is not confirmed and is to be tuned on the Spark.
RECIPE_CFG_SCALE = 4.0

KINDS = ("silhouette", "flat", "line")  # Silhouette or stencil, Flat colour art, Line art
LAYERS_BY = ("colour", "shape")
ARRANGEMENTS = ("side_by_side", "stacked")
KIND_DEFAULT, LAYERS_BY_DEFAULT, ARRANGEMENT_DEFAULT = "silhouette", "colour", "side_by_side"

# One machine preset for now (§31.7: the Gweike's working area is not confirmed, so it has none yet).
PRESETS = ({"id": "xtool-p2", "name": "xTool P2 bed", "width_mm": 600, "height_mm": 307},)

Kind = Literal["silhouette", "flat", "line"]
LayersBy = Literal["colour", "shape"]
Arrangement = Literal["side_by_side", "stacked"]
# The one switch the laser test sheet (§31.9) may flip: whether a shape is written as an area or as an outline (§31.10 item 8).
FillMode = Literal["filled", "outline"]


class VectorError(Exception):
    """The clean-up could not make a usable SVG (an unreadable picture, nothing left after the smallest feature, a missing library).
    `message` and `hint` are shown to the user as they are, like a pipeline error's."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class VectorOptions:
    """Everything the clean-up needs besides the picture. The server builds it from the request (after its own validation) and the job
    subprocess rebuilds it from JSON (`to_json` / `from_json`), so the two always agree."""

    colours: int = COLOURS_DEFAULT
    palette: tuple[str, ...] = ()  # ink colours only, uppercase #RRGGBB, never white, at most colours-1; fewer means the picture chooses
    kind: Kind = KIND_DEFAULT  # type: ignore[assignment]
    width_mm: float = SIZE_MM_DEFAULT
    height_mm: float = SIZE_MM_DEFAULT
    layers_by: LayersBy = LAYERS_BY_DEFAULT  # type: ignore[assignment]
    arrangement: Arrangement = ARRANGEMENT_DEFAULT  # type: ignore[assignment]
    bleed_mm: float = BLEED_MM_DEFAULT
    min_feature_mm: float = MIN_FEATURE_MM_DEFAULT
    kerf_mm: float = KERF_MM_DEFAULT
    shape_layer_cap: int = SHAPE_LAYER_CAP
    fill_mode: FillMode = "filled"  # type: ignore[assignment]

    def to_json(self) -> dict:
        raise NotImplementedError("engine")

    @staticmethod
    def from_json(data: dict) -> "VectorOptions":
        raise NotImplementedError("engine")


def check_options(opts: VectorOptions) -> list[tuple[str, str]]:
    """The problems with `opts`, as (field name, message) pairs: the server turns them into the 422 field errors of a run request, so the
    wording is the engine's and is the same wherever it is shown. An empty list means the options can be used."""
    raise NotImplementedError("engine")


def draw_size(width_mm: float, height_mm: float) -> tuple[int, int]:
    """The pixel size of the picture Qwen should draw for a sheet of this shape: long side `DRAW_LONG_SIDE`, the other side in proportion,
    both a multiple of 32 and at least 256 (a very long thin sheet is drawn at 256 on its short side and cropped nowhere)."""
    raise NotImplementedError("engine")


@dataclass(frozen=True)
class Recipe:
    """The words sent to the image model: what the user wrote plus what the studio adds (§31.3). `negative` is fixed, not editable."""

    positive: str
    negative: str


def build_recipe(idea: str, kind: str, colours: int, palette: tuple[str, ...]) -> Recipe:
    """The recipe for an idea: pure text, no model. The page shows `positive` (editable) before a run; the server builds it again when the
    request has no edited `recipe`."""
    raise NotImplementedError("engine")


@dataclass(frozen=True)
class LayerFile:
    """One layer's own SVG, at the same size and origin as the combined one so the layers register when stacked."""

    id: str  # layer-2-red
    name: str  # Layer 2 (red)
    colour: str  # #RRGGBB, exact and distinct from every other layer's
    svg: str


@dataclass(frozen=True)
class VectorResult:
    """What the clean-up makes from one picture. `check` has exactly the keys of `check` in tests/vector_contract.json."""

    svg: str  # the combined SVG, in millimetres, one <g> per layer
    layers: tuple[LayerFile, ...] = field(default_factory=tuple)
    check: dict = field(default_factory=dict)


def available() -> tuple[bool, Optional[str], Optional[str]]:
    """(usable, reason, hint): whether the libraries the clean-up needs import here. The server turns it into `capabilities.vector`."""
    raise NotImplementedError("engine")


def build_vector(source: Union[Path, bytes], opts: VectorOptions) -> VectorResult:
    """The clean-up (§31.5 steps 2 to 5): reduce the picture to `opts.colours`, trace each colour, make the shapes laser-ready, write the
    SVGs and the check. Deterministic: the same picture and options give the same bytes. Pure CPU, no model, no network."""
    raise NotImplementedError("engine")


def pack_zip(result: VectorResult) -> bytes:
    """A ZIP of one SVG per layer (`layer-1-black.svg`, ...), for the Download layers button."""
    raise NotImplementedError("engine")


def render_png(svg: str, long_side_px: int) -> bytes:
    """A PNG drawn from the SVG's own shapes (never from the model's picture), so it is as sharp as you ask. `long_side_px` is 16 to 8192."""
    raise NotImplementedError("engine")
