"""One clean-up, in its own process (DESIGN.md §31.12.4, the pattern of Enlarge's `upscale_job.py`, §28.3).

    python -m studio.vector.job --source <png> --options <json file> --out <dir>

The server starts this for each picture the image model has drawn: it reads the picture and the options (`VectorOptions.to_json`), runs the
clean-up, writes into `<dir>` the combined SVG (`combined.svg`), the ZIP of one SVG per layer (`layers.zip`), the preview (`preview.png`, the
combined SVG drawn at a long side of 1024 px) and the check (`check.json`), and prints ONE final line of JSON on standard output:

    {"ok": true, "check": {...}}                                   or
    {"ok": false, "error": {"message": "...", "hint": "..." | null}}

Both are handled outcomes and the process exits 0 for both, so the server can tell "this picture cannot be made into a file" (a message for
the card) from "the process died" (any other exit code, which the server reports as *the clean-up stopped unexpectedly*). It is a separate
process so that a crash or an out-of-memory in a library cannot take down the loaded image model, and so that the API process never
imports numpy, vtracer or shapely.

Nothing else is ever written to standard output (anything a library prints is sent to standard error), so the last line is always the answer.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import traceback
from pathlib import Path
from typing import Optional

from . import VectorError, VectorOptions, build_vector, pack_zip, render_png

PREVIEW_PX = 1024  # the stored preview's long side (DESIGN.md §31.12.4; the API's `png?size=1024` is this file)


def _answer(payload: dict) -> int:
    """Print the one line the server reads, and report a handled outcome (exit code 0)."""
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


def _failed(message: str, hint: Optional[str]) -> int:
    return _answer({"ok": False, "error": {"message": message, "hint": hint}})


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Make a laser-ready SVG from a picture (DESIGN.md section 31.12.4).")
    parser.add_argument("--source", required=True, type=Path, help="the picture the image model drew (PNG)")
    parser.add_argument("--options", required=True, type=Path, help="a JSON file with the VectorOptions")
    parser.add_argument("--out", required=True, type=Path, help="the folder to write combined.svg, layers.zip, preview.png and check.json into")
    args = parser.parse_args(argv)

    # The options file is the server's own: one it cannot read, or with a key this version does not know, is a mistake between the two
    # programs, and the card should say so in words rather than the server seeing a crash.
    try:
        options = VectorOptions.from_json(json.loads(args.options.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        return _failed(f"The clean-up was given options it cannot use: {exc}", None)

    try:
        # A library that prints would corrupt the answer line, so while the work runs standard output is standard error.
        with contextlib.redirect_stdout(sys.stderr):
            result = build_vector(args.source, options)
            archive = pack_zip(result)
            preview = render_png(result.svg, PREVIEW_PX)
    except VectorError as exc:
        if exc.__cause__ is not None:  # the library's own failure goes to the log, never to the card
            traceback.print_exception(exc.__cause__, file=sys.stderr)
        return _failed(exc.message, exc.hint)

    try:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "combined.svg").write_text(result.svg, encoding="utf-8")
        (args.out / "layers.zip").write_bytes(archive)
        (args.out / "preview.png").write_bytes(preview)
        (args.out / "check.json").write_text(json.dumps(result.check, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:  # a full disk is not the picture's fault, but it is a handled outcome with a message
        return _failed(f"The files could not be written: {exc}", "Check that there is room on the disk.")
    return _answer({"ok": True, "check": result.check})


if __name__ == "__main__":
    sys.exit(main())
