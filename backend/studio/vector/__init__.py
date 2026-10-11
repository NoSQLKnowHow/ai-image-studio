"""Vector (DESIGN.md §31, version 1.17): an SVG a laser can use, made from a picture.

THIS FILE IS THE CONTRACT BETWEEN THE ENGINE, THE SERVER AND THE PAGE (§31.12). The constants, the records and the signatures below are what the
server (`studio/runspec.py`, `jobs.py`, `api.py`) imports and what the page's limits come from. The engine (built in 1.17) owns everything
else in this package: `build_vector`, `pack_zip` and `render_png` do the work in `engine.py`, `raster.py` and `svgio.py`; `build_recipe` has the
real wording (`recipe.py`); `colour_name` is in `colours.py`; the job the server starts is `python -m studio.vector.job` (`job.py`). Keep the
import of this package light: numpy, Pillow, vtracer and shapely are imported inside the functions that need them, never at the top, so that
the server can import the limits without the libraries (`colours.py` and `recipe.py` are plain Python, so they are imported here).

Why one file of constants: the limits (how many colours, how big a sheet) are checked by the server, shown by the page through
`GET /api/capabilities`, and used by the engine, so they live in exactly one place.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Optional, Union

from .colours import colour_name  # noqa: F401 - part of the package's face: the name a layer is called by (§31.12.3 item 3)

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
        """The options as plain JSON (the palette as a list), for the job's options file."""
        data = asdict(self)
        data["palette"] = list(self.palette)
        return data

    @staticmethod
    def from_json(data: dict) -> "VectorOptions":
        """The inverse of `to_json`. An unknown key is a mistake and raises `ValueError`, so the job never silently ignores an option."""
        known = {name for name in VectorOptions.__dataclass_fields__}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"Unknown Vector option(s): {', '.join(unknown)}.")
        values = dict(data)
        if "palette" in values:
            values["palette"] = tuple(values["palette"])
        return VectorOptions(**values)


_HEX = re.compile(r"^#[0-9A-F]{6}$")


def _out_of_range(name: str, value: object, low: float, high: float) -> Optional[tuple[str, str]]:
    """One range check, with the one wording every number here uses. A bool is not a number (True would pass as 1)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        return (name, f"Must be between {low:g} and {high:g}.")
    return None


def check_options(opts: VectorOptions) -> list[tuple[str, str]]:
    """The problems with `opts`, as (field name, message) pairs: the server turns them into the 422 field errors of a run request, so the
    wording is the engine's and is the same wherever it is shown. An empty list means the options can be used."""
    errors: list[tuple[str, str]] = []
    checks = (
        _out_of_range("colours", opts.colours, COLOURS_MIN, COLOURS_MAX),
        _out_of_range("width_mm", opts.width_mm, SIZE_MM_MIN, SIZE_MM_MAX),
        _out_of_range("height_mm", opts.height_mm, SIZE_MM_MIN, SIZE_MM_MAX),
        _out_of_range("min_feature_mm", opts.min_feature_mm, MIN_FEATURE_MM_MIN, MIN_FEATURE_MM_MAX),
        _out_of_range("kerf_mm", opts.kerf_mm, KERF_MM_MIN, KERF_MM_MAX),
        _out_of_range("bleed_mm", opts.bleed_mm, BLEED_MM_MIN, BLEED_MM_MAX),
        _out_of_range("shape_layer_cap", opts.shape_layer_cap, 1, SHAPE_LAYER_CAP),
    )
    errors.extend(error for error in checks if error is not None)
    if isinstance(opts.colours, int) and not isinstance(opts.colours, bool) and len(opts.palette) > max(opts.colours - 1, 0):
        inks = max(opts.colours - 1, 0)
        errors.append(("palette", f"At most {inks} colour{'s' if inks != 1 else ''} besides white (white is always included and counts)."))
    seen: set[str] = set()
    for colour in opts.palette:
        if not isinstance(colour, str) or not _HEX.match(colour):
            errors.append(("palette", f"{colour!r} is not a colour: write it as #RRGGBB in capitals."))
        elif colour == "#FFFFFF":
            errors.append(("palette", "White is always included and is the bare material: list only the other colours."))
        elif colour in seen:
            errors.append(("palette", f"{colour} is listed twice."))
        else:
            seen.add(colour)
    for name, value, allowed in (("kind", opts.kind, KINDS), ("layers_by", opts.layers_by, LAYERS_BY), ("arrangement", opts.arrangement, ARRANGEMENTS),
                                 ("fill_mode", opts.fill_mode, ("filled", "outline"))):
        if value not in allowed:
            errors.append((name, f"Must be one of: {', '.join(allowed)}."))
    return errors


def draw_size(width_mm: float, height_mm: float) -> tuple[int, int]:
    """The pixel size of the picture Qwen should draw for a sheet of this shape: long side `DRAW_LONG_SIDE`, the other side in proportion,
    both a multiple of 32 and at least 256 (a very long thin sheet is drawn at 256 on its short side and cropped nowhere)."""
    long_mm, short_mm = max(width_mm, height_mm), min(width_mm, height_mm)
    short_px = round(DRAW_LONG_SIDE * short_mm / long_mm / 32) * 32
    short_px = min(DRAW_LONG_SIDE, max(256, short_px))
    return (DRAW_LONG_SIDE, short_px) if width_mm >= height_mm else (short_px, DRAW_LONG_SIDE)


@dataclass(frozen=True)
class Recipe:
    """The words sent to the image model: what the user wrote plus what the studio adds (§31.3). `negative` is fixed, not editable."""

    positive: str
    negative: str


def build_recipe(idea: str, kind: str, colours: int, palette: tuple[str, ...]) -> Recipe:
    """The recipe for an idea: pure text, no model. The page shows `positive` (editable) before a run; the server builds it again when the
    request has no edited `recipe`. The same arguments always give the same text. The wording, and why it is what it is, is `recipe.py`;
    the negative text is fixed."""
    from .recipe import NEGATIVE, positive_text

    return Recipe(positive=positive_text(idea, kind, colours, tuple(palette)), negative=NEGATIVE)


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
    import importlib

    missing = []
    for name in ("numpy", "PIL", "vtracer", "shapely"):
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append({"PIL": "Pillow"}.get(name, name))
    if missing:
        return (False, f"The Vector libraries are not installed here ({', '.join(missing)}).",
                "They are in backend/requirements.txt: rebuild the image, or pip install them.")
    return (True, None, None)


def _need_libraries() -> None:
    """Stop with the card's own wording, and not an ImportError, when a library the clean-up needs is not installed."""
    usable, reason, hint = available()
    if not usable:
        raise VectorError(reason or "The Vector libraries are not installed.", hint)


def build_vector(source: Union[Path, bytes], opts: VectorOptions) -> VectorResult:
    """The clean-up (§31.5 steps 2 to 5): reduce the picture to `opts.colours`, trace each colour, make the shapes laser-ready, write the
    SVGs and the check. Deterministic: the same picture and options give the same bytes. Pure CPU, no model, no network. Raises
    `VectorError` (with words for the card) for anything that goes wrong, including a library failing; never a library's own exception."""
    _need_libraries()
    from . import engine

    return engine.build_vector(source, opts)


def pack_zip(result: VectorResult) -> bytes:
    """A ZIP of one SVG per layer (`layer-1-black.svg`, ...), for the Download layers button. The same layers always give the same bytes:
    every entry is dated 1 January 1980, the earliest a ZIP can say, so the archive does not depend on the clock (§31.12.3 item 8)."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for layer in result.layers:
            entry = zipfile.ZipInfo(f"{layer.id}.svg", date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16  # a plain file that its owner can write, whatever the machine that unpacks it
            archive.writestr(entry, layer.svg.encode("utf-8"))
    return buffer.getvalue()


def render_png(svg: str, long_side_px: int) -> bytes:
    """A PNG drawn from the SVG's own shapes (never from the model's picture), so it is as sharp as you ask. `long_side_px` is 16 to 8192."""
    from .raster import render_svg  # Pillow only: it is in every install, so no library check

    return render_svg(svg, long_side_px)
