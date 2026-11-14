"""Keeping thin lines (DESIGN.md §41.3): what the clean-up does with a stroke that is narrower than the smallest feature, instead of deleting it.

Until 1.23 everything narrower than the smallest feature was removed (`geometry.clean_layer`). That is right for a cutter, where a part that
thin would burn away, and wrong for a drawing: a pencil face is made of strokes 3 to 5 pixels wide, and a stroke whose width wanders around
the limit came back as dashes (§41.1). With `thin_lines = "thicken"` the picture goes through this module on its way from the colour step to the tracer:

  1. `remove_specks`  a piece of ink that is too small to be a stroke or a spot is noise, and goes (it must go before anything is widened, or
                      noise would become blots);
  2. `thicken`        every part of an ink that is narrower than the smallest feature is widened to exactly that width, and nothing else moves.

It works on the label map (white and the inks, one number each) with numpy alone: the vector package has two libraries beyond numpy and
Pillow already (vtracer and shapely, `requirements-vector.txt`) and a third would be one more thing that might not install on the Spark.

Everything here is deterministic: no random numbers, no clock, and every order that could vary is sorted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# A piece is a speck when it is shorter than this many smallest features in both directions and is not a solid blob (below). Twice the
# feature is the shortest thing that can be called a line: a stroke 1 feature wide and 2 long is a dot, and a dot is not noise unless it is
# smaller than that.
SPECK_REACH = 2.0
# ... and a "solid blob" is a piece with at least the area of a disc this many features across: a pupil, a nostril, a full stop are small
# and round and wanted; a scatter of one-pixel dots is not. It is 1 because a disc exactly one feature across is the smallest spot that the
# opening in `geometry.clean_layer` keeps: keeping thin lines must never keep less than dropping them does, so nothing is called a speck here
# that the old removal would have left (an earlier 1.6 did, and a test of a spot one feature across found it).
SPECK_DISC = 1.0
# How much wider than the smallest feature a line is widened, in pixels (each side: the line ends up about twice this wider). The opening that
# follows (`geometry.clean_layer`) removes everything narrower than the smallest feature, and a strip that is exactly that wide shrinks to
# nothing in it: measured, a 1 pixel line widened to exactly the smallest feature came out empty and "nothing is left". Pixels are whole,
# so a line widened to a given radius is up to a pixel narrower than the circle it was made from; a full pixel of margin keeps the result
# strictly wider than the smallest feature (by between 1 and 2 pixels, about 0.1 mm on a 100 mm sheet drawn at 1536 pixels).
WIDEN_MARGIN_PX = 1.0
# The widest half-width (in pixels) that is widened. Widening costs about the square of it: 16 is a smallest feature of 32 pixels, over 2 mm
# on a 100 mm sheet drawn at 1536 pixels, where nobody is asking for a thin line. Beyond it thin parts are removed as before, and a note says so.
MAX_HALF_WIDTH_PX = 16.0
# "Far away" in the distance arithmetic below. Squared distances of at most a few thousand are added to it, so it must fit an int32 with room.
_FAR = 10 ** 6


@dataclass(frozen=True)
class Pieces:
    """The connected pieces of a yes/no mask. `labels` is (height, width) int32, 0 where there is none and 1.. for each piece; `area`,
    `height` and `width` are indexed by that number (so entry 0 is the background and means nothing)."""

    labels: np.ndarray
    area: np.ndarray
    height: np.ndarray
    width: np.ndarray

    @property
    def count(self) -> int:
        """How many pieces there are."""
        return len(self.area) - 1


def pieces(mask: np.ndarray) -> Pieces:
    """The 8-connected pieces of `mask` (pixels touching at a corner are one piece, as a pencil stroke drawn on a slant is).

    It works on runs, the unbroken stretches of ink along each row, not on pixels: a drawing has far fewer runs than ink pixels. Each run
    is joined to the runs of the row above that touch it, with a union-find, and the pieces are the sets that result."""
    height, width = mask.shape
    padded = np.zeros((height, width + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    edges = np.diff(padded, axis=1)
    run_rows, run_starts = np.nonzero(edges == 1)  # np.nonzero gives row-major order, so the runs come sorted by row, then by start
    _, run_ends = np.nonzero(edges == -1)  # one past the last pixel of each run, in the same order
    count = len(run_rows)
    if count == 0:
        zeros = np.zeros(1, dtype=np.int64)
        return Pieces(np.zeros((height, width), dtype=np.int32), zeros, zeros.copy(), zeros.copy())

    parent = np.arange(count)

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]  # path halving keeps the sets shallow
            a = parent[a]
        return int(a)

    # `first[y]` is the index of the first run of row y, so the runs of row y are first[y]:first[y + 1]
    first = np.searchsorted(run_rows, np.arange(height + 1))
    for y in range(1, height):
        above_from, above_to, here_from, here_to = first[y - 1], first[y], first[y], first[y + 1]
        if above_from == above_to or here_from == here_to:
            continue
        above_starts, above_ends = run_starts[above_from:above_to], run_ends[above_from:above_to]
        # A run above touches ours (corners count) when it ends at or after our start and starts at or before our last pixel + 1. The runs above are
        # in order and do not overlap, so the ones that touch are one stretch of them, found by two searches for all the runs of this row at once.
        low = np.searchsorted(above_ends, run_starts[here_from:here_to], side="left")
        high = np.searchsorted(above_starts, run_ends[here_from:here_to], side="right")
        for offset in np.nonzero(high > low)[0]:
            here = here_from + int(offset)
            for above in range(above_from + int(low[offset]), above_from + int(high[offset])):
                root_above, root_here = find(above), find(here)
                if root_above != root_here:
                    parent[max(root_above, root_here)] = min(root_above, root_here)  # the lower run is the root, so the result does not depend on the order

    roots = np.array([find(i) for i in range(count)])
    _, ids = np.unique(roots, return_inverse=True)  # 0.. for each piece, in order of its first run
    pieces_n = int(ids.max()) + 1
    lengths = run_ends - run_starts

    # paint the pieces: every pixel of every run gets its piece's number + 1
    labels = np.zeros((height, width), dtype=np.int32)
    offsets_in_run = np.arange(int(lengths.sum())) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    labels[np.repeat(run_rows, lengths), np.repeat(run_starts, lengths) + offsets_in_run] = np.repeat(ids + 1, lengths)

    # each piece's area and its bounding box, worked out from the runs
    area = np.zeros(pieces_n + 1, dtype=np.int64)
    area[1:] = np.bincount(ids, weights=lengths, minlength=pieces_n).astype(np.int64)
    top, bottom = np.full(pieces_n, height, dtype=np.int64), np.full(pieces_n, -1, dtype=np.int64)
    left, right = np.full(pieces_n, width, dtype=np.int64), np.full(pieces_n, -1, dtype=np.int64)
    np.minimum.at(top, ids, run_rows)
    np.maximum.at(bottom, ids, run_rows)
    np.minimum.at(left, ids, run_starts)
    np.maximum.at(right, ids, run_ends - 1)
    box_height, box_width = np.zeros(pieces_n + 1, dtype=np.int64), np.zeros(pieces_n + 1, dtype=np.int64)
    box_height[1:], box_width[1:] = bottom - top + 1, right - left + 1
    return Pieces(labels, area, box_height, box_width)


def specks_in(mask: np.ndarray, min_feature_px: float) -> tuple[np.ndarray, int]:
    """The pixels of `mask` that belong to a speck, and how many specks there are. A speck is a piece shorter than `SPECK_REACH` smallest
    features in both directions that is also smaller than a disc `SPECK_DISC` features across: too small to be a line and too small to be a spot."""
    found = pieces(mask)
    if found.count == 0:
        return np.zeros_like(mask, dtype=bool), 0
    extent = np.maximum(found.height, found.width)
    disc = math.pi * (SPECK_DISC * min_feature_px / 2) ** 2
    is_speck = (extent < SPECK_REACH * min_feature_px) & (found.area < disc)
    is_speck[0] = False  # the background is not a piece
    return is_speck[found.labels], int(is_speck.sum())


def inside_distance_squared(mask: np.ndarray, reach: int) -> np.ndarray:
    """For each pixel of `mask`, the squared distance to the nearest pixel that is not in it; 0 outside the mask. Exact for distances up to `reach`
    pixels (a larger one only means "more than reach"), which is all that is needed here and makes it cheap: two passes, rows then columns, each a
    minimum over 2 * reach + 1 shifted copies. The edge of the picture does not count as an edge of the drawing: ink that runs off the sheet is
    treated as continuing, so it is not thinned there."""
    height, width = mask.shape
    far = np.full((height + 2 * reach, width + 2 * reach), _FAR, dtype=np.int32)
    far[reach:reach + height, reach:reach + width] = np.where(mask, _FAR, 0)
    vertical = np.full((height, width + 2 * reach), _FAR, dtype=np.int32)
    for dy in range(-reach, reach + 1):
        np.minimum(vertical, far[reach + dy:reach + dy + height, :] + dy * dy, out=vertical)
    both = np.full((height, width), _FAR, dtype=np.int32)
    for dx in range(-reach, reach + 1):
        np.minimum(both, vertical[:, reach + dx:reach + dx + width] + dx * dx, out=both)
    return np.where(mask, both, 0)


def thicken(mask: np.ndarray, half_width_px: float) -> np.ndarray:
    """`mask` with every part narrower than `2 * half_width_px` widened to that width, and nothing else changed.

    The idea: a point of ink that lies in the middle of a stroke is the centre of the widest circle that fits in it, and that circle's radius
    is half the stroke's width. Give each point of ink the radius rho of the widest circle that covers it, and let it grow by
    `half_width_px - rho`: on the middle line of a stroke that makes the stroke exactly `2 * half_width_px` wide, a stroke that is already wide
    enough (rho at least `half_width_px` everywhere) does not grow at all, and a stroke whose width changes along its length is widened
    only where it is too narrow, so it stays one unbroken line instead of breaking where it dips below the limit."""
    reach = int(math.ceil(half_width_px)) + 1
    squared = inside_distance_squared(mask, reach)
    depth = np.sqrt(squared.astype(np.float32))  # distance to the nearest paper pixel; a stroke n pixels wide has depth (n + 1) / 2 in its middle
    height, width = mask.shape
    # Only a point close to the edge of its shape can be part of something thin; a point deeper than the target is in the middle of something wide enough.
    ys, xs = np.nonzero(mask & (depth < half_width_px + 0.5))
    if len(ys) == 0:
        return mask.copy()

    # rho for each of those points: the radius (depth - 0.5, the 0.5 because depth is measured between pixel centres) of the widest circle
    # that covers it, found by trying every centre within reach: a centre c covers the point when its depth is at least their distance
    rho = np.zeros(len(ys), dtype=np.float32)
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            distance_squared = dy * dy + dx * dx
            if distance_squared > reach * reach:
                continue
            cy, cx = ys + dy, xs + dx
            inside = (cy >= 0) & (cy < height) & (cx >= 0) & (cx < width)
            cy, cx = np.where(inside, cy, 0), np.where(inside, cx, 0)
            covers = inside & (squared[cy, cx] >= distance_squared)
            np.maximum(rho, np.where(covers, depth[cy, cx] - 0.5, 0.0), out=rho)

    # grow each point by (target - rho): mark every pixel within that distance of it
    out = mask.copy()
    growth = half_width_px - rho
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            step = math.hypot(dy, dx)
            if step > half_width_px:
                continue
            chosen = growth >= step
            if not chosen.any():
                continue
            ty, tx = ys[chosen] + dy, xs[chosen] + dx
            inside = (ty >= 0) & (ty < height) & (tx >= 0) & (tx < width)
            out[ty[inside], tx[inside]] = True
    return out


@dataclass
class Kept:
    """What `keep_thin_lines` did, for the check's numbers and notes."""

    specks: int = 0  # pieces removed as noise
    widened: int = 0  # lines (long, thin stretches) widened to the smallest feature
    too_wide: bool = False  # the smallest feature is too wide for widening (`MAX_HALF_WIDTH_PX`): thin parts are left for the old removal


def keep_thin_lines(labels: np.ndarray, min_feature_px: float) -> tuple[np.ndarray, Kept]:
    """The label map (0 white, 1.. the inks) with each ink's specks removed and its thin parts widened to the smallest feature, which is
    `min_feature_px` pixels. Returns the new map and what was done.

    Inks are done smallest first, and an ink that is widened takes the pixels it grows into from white and from the larger inks only: a thin red
    line drawn over a large blue shape stays a red line, while two thin lines do not eat each other, whichever is done first. (Widening never
    takes a pixel from an ink that was already done.) Specks go to white: the layers' own clean-up closes any hairline gap that leaves."""
    out = labels.copy()
    report = Kept()
    half = min_feature_px / 2 + WIDEN_MARGIN_PX
    report.too_wide = half > MAX_HALF_WIDTH_PX
    inks = sorted((int(k) for k in np.unique(labels) if k != 0), key=lambda k: (int((labels == k).sum()), k))
    done = np.zeros(labels.shape, dtype=bool)  # pixels of the inks that are finished: nothing may grow over them
    for ink in inks:
        mask = out == ink
        speck_pixels, specks = specks_in(mask, min_feature_px)
        if specks:
            out[speck_pixels] = 0
            mask &= ~speck_pixels
            report.specks += specks
        if not report.too_wide and mask.any():
            grown = thicken(mask, half)
            added = grown & ~mask & ~done
            if added.any():
                out[added] = ink
                # count lines, not bumps: a long thin stretch is a line, a rounded corner of a wide shape is not
                found = pieces(added)
                long_enough = np.maximum(found.height, found.width) >= SPECK_REACH * min_feature_px
                long_enough[0] = False
                report.widened += int(long_enough.sum())
        done |= out == ink
    return out, report
