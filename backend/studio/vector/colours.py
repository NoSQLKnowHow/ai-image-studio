"""Colour names and the colour arithmetic the Vector package needs without numpy (DESIGN.md §31.12.3 items 2 and 3).

Why this is its own file: the recipe (`build_recipe`) names the user's ink colours in words, the page and the server call it, and the
server imports `studio.vector` without numpy, Pillow, vtracer or shapely installed. So everything here is plain Python. The engine has a
numpy version of the same sRGB to CIE Lab conversion (`pixels.py`) that works on a whole picture at once, and a test checks that the two
agree, so there is one definition of "how far apart two colours look".
"""

from __future__ import annotations

import math
import re

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# The D65 white point and the sRGB matrix (IEC 61966-2-1): the standard constants, written out so that nothing depends on a library.
WHITE_XYZ = (0.95047, 1.0, 1.08883)
SRGB_TO_XYZ = ((0.4124564, 0.3575761, 0.1804375),
               (0.2126729, 0.7151522, 0.0721750),
               (0.0193339, 0.1191920, 0.9503041))
LAB_EPSILON, LAB_KAPPA = 216 / 24389, 24389 / 27  # the CIE's own constants for the cube-root cut-over

# The short fixed list the layer names come from (§31.12.3 item 3): black, grey, white, red, orange, yellow, green, cyan, blue, purple, pink,
# brown. A colour is called by the NEAREST reference colour in CIE Lab, and the table has several references for the names that cover a lot of
# ground (a saturated blue, a sky blue and a navy are all "blue"; with one reference each, a light blue would be nearer to grey than to the
# one blue). The order of the table decides a tie, so black comes before grey. Nothing here is configurable (§31.12.9 item 8).
COLOUR_REFERENCES: tuple[tuple[str, str], ...] = (
    ("black", "#000000"), ("black", "#202020"),
    ("grey", "#808080"), ("grey", "#A8A8A8"), ("grey", "#C8C8C8"), ("grey", "#505050"),
    ("white", "#FFFFFF"), ("white", "#F0F0F0"),
    ("red", "#FF0000"), ("red", "#CC2020"), ("red", "#E04040"), ("red", "#800000"),
    ("orange", "#FF8000"), ("orange", "#FFA500"), ("orange", "#E07000"),
    ("yellow", "#FFE000"), ("yellow", "#FFFF00"), ("yellow", "#D4C000"), ("yellow", "#F0E080"),
    ("green", "#00A000"), ("green", "#00FF00"), ("green", "#006400"), ("green", "#80C000"), ("green", "#20B060"), ("green", "#A0E0A0"),
    ("cyan", "#00D0D0"), ("cyan", "#00FFFF"), ("cyan", "#008B8B"),
    ("blue", "#0000FF"), ("blue", "#1E78DC"), ("blue", "#000080"), ("blue", "#4060FF"), ("blue", "#80B0E8"),
    ("purple", "#8000C0"), ("purple", "#800080"), ("purple", "#FF00FF"), ("purple", "#4B0082"), ("purple", "#B080E0"),
    ("pink", "#FF80C0"), ("pink", "#FFC0CB"), ("pink", "#FF69B4"), ("pink", "#E08090"),
    ("brown", "#8B4513"), ("brown", "#A0522D"), ("brown", "#6B3A1B"), ("brown", "#C08040"), ("brown", "#A52A2A"),
)
COLOUR_NAMES: tuple[str, ...] = tuple(dict.fromkeys(name for name, _ in COLOUR_REFERENCES))  # the twelve names, in order


def hex_to_rgb(colour: str) -> tuple[int, int, int]:
    """`#RRGGBB` (either case) as three 0 to 255 numbers. Anything else is a ValueError: a colour that is not one is a mistake to see."""
    if not isinstance(colour, str) or not HEX.match(colour):
        raise ValueError(f"{colour!r} is not a colour written as #RRGGBB")
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Three 0 to 255 numbers as uppercase `#RRGGBB`, the form every colour in a file or a check is written in."""
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb)


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """One sRGB colour (0 to 255 each) in CIE Lab (D65). Lab is where equal distances look like equal differences, which is what
    grouping colours (and choosing the nearest name) needs; plain RGB distances do not."""
    linear = [((v / 255 + 0.055) / 1.055) ** 2.4 if v / 255 > 0.04045 else v / 255 / 12.92 for v in rgb]
    xyz = [sum(row[i] * linear[i] for i in range(3)) / white for row, white in zip(SRGB_TO_XYZ, WHITE_XYZ)]
    f = [math.cbrt(t) if t > LAB_EPSILON else (LAB_KAPPA * t + 16) / 116 for t in xyz]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def colour_name(colour: str) -> str:
    """The plain name for a `#RRGGBB` colour: the name of the nearest of `COLOUR_REFERENCES` in Lab. Deterministic: a tie goes to the
    reference that comes first in the table."""
    lab = rgb_to_lab(hex_to_rgb(colour))
    best, best_distance = COLOUR_REFERENCES[0][0], math.inf
    for name, reference in COLOUR_REFERENCES:
        distance = math.dist(lab, rgb_to_lab(hex_to_rgb(reference)))
        if distance < best_distance:  # strictly less: the first of two equally near references stays
            best, best_distance = name, distance
    return best
