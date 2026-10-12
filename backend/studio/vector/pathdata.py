"""Reading the path data of an SVG: M, L, C and Z, in capitals, curves flattened to straight pieces (DESIGN.md §31.12.3 item 5).

It is plain Python on purpose: it reads what vtracer writes (`geometry.py`) and the engine's own SVG back for the PNG (`raster.py`), and the PNG
must not need shapely. Anything but those four commands is an error and not something to skip: a tracer that wrote something new, or a file
that is not ours, must be noticed rather than half read.
"""

from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"([A-Za-z])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def _flatten_cubic(p0, p1, p2, p3, tolerance: float) -> list[tuple[float, float]]:
    """The points of a cubic Bezier curve after p0 (not p0 itself), spaced so that the polyline is within `tolerance` of the curve. The number
    of pieces comes from the curve's own bend: a cubic's error with n equal steps is at most 0.75 d / n squared, d being the larger second
    difference of its control points."""
    d = max(math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]), math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]))
    steps = max(1, min(64, math.ceil(math.sqrt(0.75 * d / tolerance)))) if d > 0 else 1
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        a, b, c, e = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]))
    return out


def parse_path(data: str, tolerance: float) -> list[list[tuple[float, float]]]:
    """The closed rings of one SVG path (M, L, C and Z, in capitals, which is all vtracer writes), curves flattened to `tolerance`. Anything
    else is an error, not something to skip: a tracer that wrote something new must be noticed, not half-read. A ring with fewer than
    three points (a single pixel the tracer could not draw) is not a shape and is dropped."""
    tokens = [(match.group(1), match.group(2)) for match in _TOKEN.finditer(data)]
    rings: list[list[tuple[float, float]]] = []
    ring: list[tuple[float, float]] = []
    command = None
    i = 0

    def take(count: int) -> list[float]:
        """The next `count` numbers; a letter or the end of the data before then is a path that is not well formed."""
        nonlocal i
        values = []
        for _ in range(count):
            if i >= len(tokens) or tokens[i][0]:
                raise ValueError("a path command had the wrong number of numbers")
            values.append(float(tokens[i][1]))
            i += 1
        return values

    while i < len(tokens):
        letter, _ = tokens[i]
        if letter:
            if letter not in "MLCZ":
                raise ValueError(f"the tracer wrote a path command this engine does not read: {letter}")
            command = letter
            i += 1
            if letter in "MZ":  # a moveto starts a ring (closing one left open), a Z ends one
                if len(ring) >= 3:
                    rings.append(ring)
                ring = []
            continue
        if command in ("M", "L"):
            x, y = take(2)
            ring.append((x, y))
            command = "L"  # more pairs after a moveto are linetos, as in SVG
        elif command == "C":
            v = take(6)
            ring.extend(_flatten_cubic(ring[-1], (v[0], v[1]), (v[2], v[3]), (v[4], v[5]), tolerance))
        else:
            raise ValueError("numbers in a path with no command to use them")
    if len(ring) >= 3:
        rings.append(ring)
    return rings
