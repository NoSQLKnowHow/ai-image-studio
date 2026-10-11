"""The picture side of the clean-up (DESIGN.md §31.5 steps 1 and 2): read the picture, reduce it to the colours asked for, and remove the
soft fringes. Everything here works on pixels with numpy; nothing here knows about millimetres or shapes.

The result is a *label map*: one small number per pixel. 0 is white (the bare material, §31.7 answer 2), 1, 2, ... are the inks. The
later steps (`geometry.py`) trace each ink's pixels into shapes.

Imported lazily by `engine.py`, never by `studio/vector/__init__.py`, which has to stay importable without numpy.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import numpy as np
from PIL import Image

from . import VectorError
from .colours import SRGB_TO_XYZ, WHITE_XYZ, LAB_EPSILON, LAB_KAPPA, hex_to_rgb, rgb_to_hex, rgb_to_lab

# A drawing is 1536 px on its long side (DRAW_LONG_SIDE); 40 million pixels is a 6000 x 6000 picture, far more than the studio ever
# makes, and the limit keeps a wrong file (someone's 100 MP photograph) from using all the memory of a machine that is also holding a model.
MAX_PIXELS = 40_000_000
# k-means stops when no pixel changes cluster; this is only the safety net for a picture that would keep flipping a few of them.
MAX_ITERATIONS = 60
# The pixel-level clean-up looks at windows of at most 3 x 3: anti-aliased edges and noise are one or two pixels wide at any size.
MAX_FRINGE_WINDOW = 3
WHITE_LAB = (100.0, 0.0, 0.0)  # white is the fixed centre of its cluster (§31.12.3 item 1): it is never moved to fit the picture
# Two colours closer than this (CIE Lab distance) are the same colour to anyone's eye, so a new ink is never started from a colour that
# near to a centre already there. Without it, the tiny arithmetic difference between the picture's white and the exact white of the
# centre would start an ink of its own: a picture that is all white would have "a white ink".
SAME_COLOUR = 2.0
# Colours this near white (CIE Lab distance) are white: a cream or grey-white background, a faint tint, the pale halo of an edge. They
# never start an ink, so a background that is a little off white is not asked to be "the second colour" when 3 colours are asked for.
NEAR_WHITE = 12.0
# How far from white a colour can be and still be the bare material. The nearest centre decides everything else, but a colour as far from
# white as this is never bare: if the picture does not use the colours that were asked for (a palette of red for a picture drawn in black),
# the black is the red ink's, rather than the whole picture being "all white" because black is nearer to white than to red.
BARE_REACH = 50.0


@dataclass(frozen=True)
class Ink:
    """One colour found in the picture that is not white."""

    index: int  # its value in the label map
    palette: Optional[str]  # the palette's own colour when the user named it, else None (the picture chose)
    picture: str  # the picture's own colour for it, #RRGGBB (the mean of the pixels well inside its regions)
    pixels: int
    lightness: float  # CIE L of `picture`, used to break ties by darkness (§31.12.3 item 3)


@dataclass
class Reduction:
    """What the colour reduction found."""

    labels: np.ndarray  # (height, width) uint8: 0 white, 1.. the inks
    inks: list[Ink]  # only the inks that still have pixels after the clean-up
    missing: list[str] = field(default_factory=list)  # palette colours that no pixel of the picture ended up with
    white_pixels: int = 0


def load_picture(source: Union[Path, str, bytes]) -> np.ndarray:
    """The picture as an (height, width, 3) uint8 array. Transparent areas become white: they are the bare material too."""
    try:
        with Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source) as image:
            if image.width * image.height > MAX_PIXELS:
                raise VectorError(f"The picture is too large to clean up ({image.width} x {image.height} pixels).",
                                  "The drawing the studio makes is 1536 pixels on its long side; use a picture of that size.")
            image.load()
            rgba = image.convert("RGBA")
    except VectorError:
        raise
    except Exception as exc:  # noqa: BLE001 - an unreadable file, however Pillow words it, is one message for the card
        raise VectorError("The picture could not be read.", "It is not a picture file the studio can open (PNG, JPEG, WebP).") from exc
    flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flat.alpha_composite(rgba)
    return np.asarray(flat.convert("RGB"), dtype=np.uint8)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(n, 3) sRGB (0 to 255) to (n, 3) CIE Lab, the numpy version of `colours.rgb_to_lab` (a test checks the two agree)."""
    c = rgb.astype(np.float64) / 255.0
    linear = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = (linear @ np.array(SRGB_TO_XYZ).T) / np.array(WHITE_XYZ)
    f = np.where(xyz > LAB_EPSILON, np.cbrt(xyz), (LAB_KAPPA * xyz + 16) / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


# ---- small morphology on yes/no masks (numpy only: scipy is not a dependency) -----------------------------------------------------------
def _offsets(size: int) -> range:
    """The shifts of a `size` window: 3 gives -1, 0, 1; 2 gives -1, 0; 1 gives 0."""
    return range(-(size // 2), size - size // 2)


def _window(mask: np.ndarray, offsets, combine) -> np.ndarray:
    """Combine `mask` with copies of itself shifted by each of `offsets`, first along the rows and then along the columns (a square window
    is two straight ones, which is much cheaper). The edge of the picture repeats itself, so that a region touching the edge is not eaten
    away by it: the edge of the picture is not an edge of the drawing."""
    low, high = min(offsets), max(offsets)
    out = mask
    for axis in (0, 1):
        padding = [(0, 0), (0, 0)]
        padding[axis] = (-low, high)
        padded = np.pad(out, padding, mode="edge")
        length = out.shape[axis]
        acc = None
        for shift in offsets:
            index = [slice(None), slice(None)]
            index[axis] = slice(shift - low, shift - low + length)
            part = padded[tuple(index)]
            acc = part if acc is None else combine(acc, part)
        out = acc
    return out


def erode(mask: np.ndarray, size: int) -> np.ndarray:
    """Pixels whose whole `size` x `size` window is inside the mask."""
    return _window(mask, _offsets(size), np.logical_and)


def dilate(mask: np.ndarray, size: int) -> np.ndarray:
    """Pixels within reach of the mask by a `size` x `size` window (the mirror image of `erode`, so that erode then dilate is an opening)."""
    return _window(mask, [-o for o in _offsets(size)], np.logical_or)


def opening(mask: np.ndarray, size: int) -> np.ndarray:
    """The mask without anything narrower than `size` pixels (a speck, a 1 or 2 pixel ribbon): erode, then grow back what survived."""
    return dilate(erode(mask, size), size)


def fringe_window(min_feature_px: float) -> int:
    """How wide a ribbon counts as a soft fringe, in pixels. Never more than 3 (a fringe is one or two pixels at any resolution), and never
    more than the smallest feature itself: a 2 pixel line the user asked to keep (a smallest feature of 2 pixels or less) must survive."""
    return int(min(MAX_FRINGE_WINDOW, max(1, np.floor(min_feature_px))))


def remove_fringes(labels: np.ndarray, size: int, label_count: int) -> np.ndarray:
    """Remove soft fringes and specks from the label map (§31.5 step 2): any part of a colour that is narrower than `size` pixels is given
    to the colour next to it. Anti-aliased edges between two inks are one or two pixels of in-between colours; once they are given to
    their neighbours the two inks meet cleanly, with no thin third colour between them to be traced and cut."""
    if size <= 1:
        return labels
    labels = labels.astype(np.int16)
    for _ in range(3):  # a second pass only finds what the first one's reassignment uncovered; three is more than any real picture needs
        thin = np.zeros(labels.shape, dtype=bool)
        for label in range(label_count):
            mask = labels == label
            if mask.any():
                thin |= mask & ~opening(mask, size)
        if not thin.any():
            break
        labels = _give_to_neighbours(labels, thin, label_count)
    return labels


def _give_to_neighbours(labels: np.ndarray, unassigned: np.ndarray, label_count: int) -> np.ndarray:
    """Each unassigned pixel takes the label most common among its eight assigned neighbours; pixels with none yet wait for the next round.
    Rounds are simultaneous (every pixel looks at the same snapshot), so the answer does not depend on the order pixels are visited in,
    and a tie goes to the lower label (white is label 0): all of which keeps the result deterministic."""
    labels = labels.copy()
    labels[unassigned] = -1
    ys, xs = np.nonzero(unassigned)
    while len(ys):
        padded = np.pad(labels, 1, constant_values=-1)
        votes = np.zeros((len(ys), label_count), dtype=np.int16)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy or dx:
                    neighbour = padded[ys + 1 + dy, xs + 1 + dx]
                    known = neighbour >= 0
                    votes[np.nonzero(known)[0], neighbour[known]] += 1
        decided = votes.max(axis=1) > 0
        if not decided.any():  # nothing assigned anywhere near: cannot happen in a picture that has any colour at all
            labels[ys, xs] = 0
            break
        labels[ys[decided], xs[decided]] = votes.argmax(axis=1)[decided]
        ys, xs = ys[~decided], xs[~decided]
    return labels


def reduce_colours(rgb: np.ndarray, inks: int, palette: tuple[str, ...], window: int) -> Reduction:
    """Reduce `rgb` to white plus at most `inks` inks (§31.5 step 2, §31.12.3 item 1).

    White is a fixed centre. The palette's colours are fixed centres. Any further inks are found by k-means in CIE Lab, started
    from fixed points rather than random ones, so the same picture always gives the same colours: each new centre is the colour that
    is both far from the centres so far and common (distance squared times the number of pixels that have it), which finds a small red
    eye on a large black shape before it finds a rare shade of grey. `window` is the width of fringe to remove (`fringe_window`)."""
    height, width, _ = rgb.shape
    flat = rgb.reshape(-1, 3)
    # Work on the distinct colours with a count each: a flat drawing has few, and even a noisy one has far fewer than pixels.
    key = (flat[:, 0].astype(np.uint32) << 16) | (flat[:, 1].astype(np.uint32) << 8) | flat[:, 2].astype(np.uint32)
    distinct, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
    distinct_rgb = np.stack([(distinct >> 16) & 255, (distinct >> 8) & 255, distinct & 255], axis=1).astype(np.uint8)
    lab = srgb_to_lab(distinct_rgb)
    weight = counts.astype(np.float64)

    centres = [np.array(WHITE_LAB)] + [np.array(rgb_to_lab(hex_to_rgb(colour))) for colour in palette]
    fixed = len(centres)
    to_white = ((lab - centres[0]) ** 2).sum(axis=1)
    nearest = np.min([((lab - centre) ** 2).sum(axis=1) for centre in centres], axis=0)
    for _ in range(inks - len(palette)):  # the inks the picture chooses: mass-weighted farthest-point seeding
        score = np.where((nearest >= SAME_COLOUR ** 2) & (to_white >= NEAR_WHITE ** 2), nearest * weight, 0.0)
        best = int(np.argmax(score))  # the first of equals: the distinct colours are sorted, so this is deterministic
        if score[best] <= 0:  # every colour in the picture is already a centre: the picture has fewer colours than asked
            break
        centres.append(lab[best].copy())
        nearest = np.minimum(nearest, ((lab - lab[best]) ** 2).sum(axis=1))

    assignment = None
    for _ in range(MAX_ITERATIONS):
        distance = np.stack([((lab - centre) ** 2).sum(axis=1) for centre in centres], axis=1)
        new_assignment = distance.argmin(axis=1)  # a tie goes to the lowest centre: white first
        if len(centres) > 1:  # a colour far from white is never the bare material: it belongs to the nearest ink
            far = (new_assignment == 0) & (distance[:, 0] > BARE_REACH ** 2)
            new_assignment = np.where(far, 1 + distance[:, 1:].argmin(axis=1), new_assignment)
        new_assignment = np.where(to_white <= NEAR_WHITE ** 2, 0, new_assignment)  # and a colour near white is white, whatever else is near it
        if assignment is not None and np.array_equal(new_assignment, assignment):
            break
        assignment = new_assignment
        for k in range(fixed, len(centres)):  # only the picture's own inks move; white and the palette stay where they were put
            member = assignment == k
            if member.any():
                centres[k] = (lab[member] * weight[member, None]).sum(axis=0) / weight[member].sum()

    label_count = len(centres)
    labels = assignment[inverse].reshape(height, width).astype(np.uint8)
    seeded = [None] + list(palette) + [None] * (label_count - fixed)  # the palette colour of each centre, None for white and picture-chosen ones
    labels = remove_fringes(labels, window, label_count).astype(np.uint8)

    found: list[Ink] = []
    missing: list[str] = []
    for label in range(1, label_count):
        mask = labels == label
        pixels = int(mask.sum())
        if not pixels:
            # A colour the user named that the picture does not have is worth a note. One the picture chose for itself and then lost (it
            # was only the soft edge between two other colours) is not: the user never asked for it, and `fewer_colours` says what matters.
            if seeded[label]:
                missing.append(seeded[label])
            continue
        # The picture's colour for this ink is the mean of the pixels well inside its regions: the edges are mixed with the neighbours'
        # colours, so a plain mean of everything would turn a pure black into near-black.
        core = erode(mask, MAX_FRINGE_WINDOW)
        sample = rgb[core] if core.any() else rgb[mask]
        mean = sample.mean(axis=0)
        picture = rgb_to_hex(tuple(int(np.floor(v + 0.5)) for v in mean))
        found.append(Ink(index=label, palette=seeded[label], picture=picture, pixels=pixels, lightness=rgb_to_lab(hex_to_rgb(picture))[0]))
    return Reduction(labels=labels, inks=found, missing=missing, white_pixels=int((labels == 0).sum()))
