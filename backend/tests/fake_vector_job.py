"""A stand-in for the Vector clean-up job (DESIGN.md §31.12.4), used by the server's tests so that none of them needs the engine or its libraries.

It is run exactly as the real job is, `python fake_vector_job.py --source <png> --options <json file> --out <dir>`, by the server's own code
(`Settings.vector_job_command` puts this script where `python -m studio.vector.job` would be). It writes `combined.svg`, `layers.zip`, `preview.png` and
`check.json` into `--out` and prints the one last line of JSON the protocol asks for. The check is the one in `tests/vector_contract.json`, with the
sheet's size and the asked-for colours taken from the options file, so what the server stores is what the contract says it should be.

What it does can be changed, per version and per run, with environment variables (the server's job inherits the test process's environment). A version is
named by its number as the drawing's file name gives it: `<idx>-source.png`, idx counting from 0.

    FAKE_VECTOR_FAIL=1,2       these versions fail the way the engine reports a failure: exit 0 and {"ok": false, "error": {...}}
    FAKE_VECTOR_CRASH=0        these versions crash: exit code 3 and a traceback-like line on stderr
    FAKE_VECTOR_SLEEP=2.5      wait this many seconds first (so that a test can cancel, or time the job out)
    FAKE_VECTOR_SLEEP_ON=1     only these versions wait (all of them when this is not set)
    FAKE_VECTOR_SKIP=layers.zip   do not write these files (a job that said it was done and was not)
    FAKE_VECTOR_ANSWER=bad-check | no-line | not-json | no-check    say something the protocol does not allow
    FAKE_VECTOR_LOG=<file>     append one JSON line about each call: the version, the options file's contents, the sha1 of the drawing, the command line
                               and the process id (so that a test can check that a job that was cancelled or timed out is really gone)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

CONTRACT = Path(__file__).with_name("vector_contract.json")


def listed(name: str) -> set[str]:
    """The comma-separated list an environment variable holds, as a set of strings."""
    return {part.strip() for part in os.environ.get(name, "").split(",") if part.strip()}


def png_bytes(width: int, height: int) -> bytes:
    """A small solid PNG. Pillow is the studio's own dependency, so the fake needs nothing extra."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(out, format="PNG")
    return out.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--options", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    source, out = Path(args.source), Path(args.out)
    # the version's number, from the drawing's name (`2-source.png` is version 2, counting from 0)
    idx = re.match(r"^(\d+)-source\.png$", source.name)
    version = idx.group(1) if idx else "?"
    options = json.loads(Path(args.options).read_text(encoding="utf-8"))
    drawing = source.read_bytes()  # raises if the server did not leave the drawing where it said: the test then sees a crash

    log = os.environ.get("FAKE_VECTOR_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"version": version, "options": options, "sha1": hashlib.sha1(drawing).hexdigest(), "argv": sys.argv[1:],
                                     "out_exists": out.is_dir(), "pid": os.getpid()}) + "\n")

    slow = listed("FAKE_VECTOR_SLEEP_ON")
    if not slow or version in slow:
        time.sleep(float(os.environ.get("FAKE_VECTOR_SLEEP", "0") or 0))

    if version in listed("FAKE_VECTOR_CRASH"):
        print("Traceback (most recent call last): the fake job crashed on purpose", file=sys.stderr)
        return 3
    if version in listed("FAKE_VECTOR_FAIL"):
        print(json.dumps({"ok": False, "error": {"message": f"Nothing was left of drawing {version} after the smallest feature.",
                                                 "hint": "Make the smallest feature smaller."}}))
        return 0

    # the contract's check, with this run's size and colours in it
    check = json.loads(CONTRACT.read_text(encoding="utf-8"))["run"]["vectors"][0]["check"]
    check["size_mm"] = [options["width_mm"], options["height_mm"]]
    check["colours_asked"] = options["colours"]
    check["layers_by"], check["arrangement"] = options["layers_by"], options["arrangement"]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{options["width_mm"]}mm" height="{options["height_mm"]}mm" '
           f'viewBox="0 0 {options["width_mm"]} {options["height_mm"]}"><!-- version {version}, drawing {hashlib.sha1(drawing).hexdigest()} -->'
           '<g id="layer-1-black" fill="#000000"><path d="M0 0L10 0L10 10Z"/></g></svg>')
    check["svg_bytes"] = len(svg.encode("utf-8"))

    skipped = listed("FAKE_VECTOR_SKIP")
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "combined.svg": svg.encode("utf-8"),
        "preview.png": png_bytes(32, 32),
        "check.json": json.dumps(check).encode("utf-8"),
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("layer-1-black.svg", svg)
    files["layers.zip"] = archive.getvalue()
    for name, data in files.items():
        if name not in skipped:
            (out / name).write_bytes(data)

    answer = os.environ.get("FAKE_VECTOR_ANSWER", "")
    if answer == "no-line":
        return 0
    if answer == "not-json":
        print("done, and thank you")
        return 0
    if answer == "no-check":
        print(json.dumps({"ok": True}))
        return 0
    if answer == "bad-check":
        print(json.dumps({"ok": True, "check": {"colours_found": "four", "layers": [], "size_mm": [1, 2]}}))
        return 0
    print("some chatter before the answer is allowed")
    print(json.dumps({"ok": True, "check": check}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
