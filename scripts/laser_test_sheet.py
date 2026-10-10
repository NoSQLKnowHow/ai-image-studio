#!/usr/bin/env python3
"""A test file for your laser software (DESIGN.md §31.9, step 1). Run it, or just download docs/laser_test_sheet.svg.

The Vector tab (DESIGN.md §31, proposed, not built) would write SVG files in a particular way: a real size in millimetres, one
group per layer, every layer in its own exact colour, closed shapes only. Whether xTool Creative Space (XCS) on the P2 and
LightBurn on the Gweike G3 Ultra read such a file as intended is something nobody can know from here. This writes a small
file written exactly that way, so that you can open it in each program and tell me what you see.

  python3 scripts/laser_test_sheet.py                      # writes laser-test-sheet.svg here
  python3 scripts/laser_test_sheet.py --out /some/folder/sheet.svg

It uses only the Python standard library, so any computer with Python 3.9 or newer will do. Nothing in the studio uses it.

The file is 100 x 100 mm with four layers, each in an exact colour (black, red, green, blue). Each layer holds the same five
things, so that the layers can be compared with each other:

  a filled square (15 mm)       an outline square (15 mm)      a ring: a filled circle (15 mm) with a hole (7 mm)
  an outline circle (15 mm)     a scale bar, filled, 10 x 2 mm

and layer 1 also has an outline frame round the whole 100 x 100 mm. The shapes have an id such as `layer2-ring`, so you can name
the one that misbehaves. The layers carry both an id (`layer-2-red`) and an Inkscape label (`Layer 2 (red)`) that differ, so that
what a program shows tells us which of the two it reads.

What to look for, in XCS and then in LightBurn (write down what you see; a screenshot of each is better than a description):

  1. Size: does the whole design measure 100 x 100 mm (100.1 is fine: that is the frame's line width)? Does a scale bar measure 10 mm?
  2. Layers: what appear, and what are they called ("layer-2-red", "Layer 2 (red)", a colour, or nothing)?
  3. Can each layer (each colour, in LightBurn) be given its own cut or engrave setting?
  4. Do the ring's hole and the circles come in as holes and circles, and does an engraving preview of the ring leave the hole empty?
  5. Are the outline shapes taken as lines and the filled shapes as areas, and which of the two looks right for cutting?
  6. Is anything missing, moved, joined to another shape, or the wrong colour?

Exit codes: 0 ok, 2 bad arguments, 6 the file could not be written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

SIZE_MM = 100
ROW_MM = 25  # each layer has a row this tall
SHAPE_MM = 15
MARGIN_MM = 5
KAPPA = 0.5522847498  # the control-point distance that makes four cubic curves a circle
OUTLINE_WIDTH_MM = 0.1

# (name, colour): exact, distinct colours; a laser program tells layers apart by colour
LAYERS = [("black", "#000000"), ("red", "#FF0000"), ("green", "#00FF00"), ("blue", "#0000FF")]

INKSCAPE = "http://www.inkscape.org/namespaces/inkscape"


def fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def rect_path(x: float, y: float, w: float, h: float) -> str:
    return f"M{fmt(x)} {fmt(y)} L{fmt(x + w)} {fmt(y)} L{fmt(x + w)} {fmt(y + h)} L{fmt(x)} {fmt(y + h)} Z"


def circle_path(cx: float, cy: float, r: float, reverse: bool = False) -> str:
    """A circle as four cubic curves, the way a tracer writes curves. Clockwise on the page, or counter-clockwise when `reverse`
    (a hole is drawn the other way round, so that it is a hole under either fill rule)."""
    k = KAPPA * r
    if reverse:
        pts = [(cx + r, cy), (cx + r, cy - k), (cx + k, cy - r), (cx, cy - r),
               (cx - k, cy - r), (cx - r, cy - k), (cx - r, cy),
               (cx - r, cy + k), (cx - k, cy + r), (cx, cy + r),
               (cx + k, cy + r), (cx + r, cy + k), (cx + r, cy)]
    else:
        pts = [(cx + r, cy), (cx + r, cy + k), (cx + k, cy + r), (cx, cy + r),
               (cx - k, cy + r), (cx - r, cy + k), (cx - r, cy),
               (cx - r, cy - k), (cx - k, cy - r), (cx, cy - r),
               (cx + k, cy - r), (cx + r, cy - k), (cx + r, cy)]
    p = [f"{fmt(x)} {fmt(y)}" for x, y in pts]
    return f"M{p[0]} C{p[1]} {p[2]} {p[3]} C{p[4]} {p[5]} {p[6]} C{p[7]} {p[8]} {p[9]} C{p[10]} {p[11]} {p[12]} Z"


def filled(shape_id: str, d: str, colour: str, rule: Optional[str] = None) -> str:
    extra = f' fill-rule="{rule}"' if rule else ""
    return f'    <path id="{shape_id}" d="{d}" fill="{colour}"{extra} stroke="none"/>'


def outline(shape_id: str, d: str, colour: str) -> str:
    return f'    <path id="{shape_id}" d="{d}" fill="none" stroke="{colour}" stroke-width="{fmt(OUTLINE_WIDTH_MM)}"/>'


def layer_group(index: int) -> str:
    """Layer `index` (1 to 4): its five shapes in a row, and for layer 1 the frame."""
    name, colour = LAYERS[index - 1]
    top = ROW_MM * (index - 1) + MARGIN_MM
    mid = top + SHAPE_MM / 2
    n = f"layer{index}"
    lines = [f'  <g id="layer-{index}-{name}" inkscape:groupmode="layer" inkscape:label="Layer {index} ({name})">']
    if index == 1:
        lines.append(outline("frame", rect_path(0, 0, SIZE_MM, SIZE_MM), colour))
    lines.append(filled(f"{n}-filled-square", rect_path(5, top, SHAPE_MM, SHAPE_MM), colour))
    lines.append(outline(f"{n}-outline-square", rect_path(25, top, SHAPE_MM, SHAPE_MM), colour))
    ring = circle_path(52.5, mid, 7.5) + " " + circle_path(52.5, mid, 3.5, reverse=True)
    lines.append(filled(f"{n}-ring", ring, colour, rule="evenodd"))
    lines.append(outline(f"{n}-outline-circle", circle_path(72.5, mid, 7.5), colour))
    lines.append(filled(f"{n}-scale-bar", rect_path(85, mid - 1, 10, 2), colour))
    lines.append("  </g>")
    return "\n".join(lines)


def build_svg() -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="{INKSCAPE}" version="1.1" '
        f'width="{SIZE_MM}mm" height="{SIZE_MM}mm" viewBox="0 0 {SIZE_MM} {SIZE_MM}">',
        "  <title>AI Image Studio laser test sheet</title>",
        f"  <desc>DESIGN.md section 31.9. {SIZE_MM} x {SIZE_MM} mm, one unit is one millimetre. Four layers in four exact colours; "
        "each holds a filled square, an outline square, a ring (a filled circle with a hole), an outline circle and a 10 x 2 mm "
        "scale bar. Layer 1 also has an outline frame of the whole sheet.</desc>",
    ]
    parts += [layer_group(i) for i in range(1, len(LAYERS) + 1)]
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Write the laser test sheet (DESIGN.md section 31.9).")
    parser.add_argument("--out", default="laser-test-sheet.svg", help="where to write the SVG (default: laser-test-sheet.svg here)")
    args = parser.parse_args(argv)
    path = Path(args.out)
    try:
        path.write_text(build_svg(), encoding="utf-8")
    except OSError as err:
        print(f"Could not write {path}: {err}", file=sys.stderr)
        return 6
    print(f"Wrote {path}: {SIZE_MM} x {SIZE_MM} mm, {len(LAYERS)} layers. Open it in XCS and in LightBurn; the questions are at the top of this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
