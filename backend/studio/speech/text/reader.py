"""The reader process: reads one source and prepares it, with limits (DESIGN.md §38.5 item 1, criterion 273).

Run as `python -m studio.speech.text.reader --kind K --path P ...` by `isolated.py`. It sets its own memory and CPU limits first, so that a hostile or merely
enormous file ends in a refusal and a dead child, and never in a hung or exhausted server; reads the file; prepares it (`prepare.build_review`); and
prints one line of JSON: `{"ok": true, "review": {...}}` or `{"ok": false, "error": {...}}`. Nothing else is written to standard output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .errors import PrepareError
from .model import Chapter, Extracted
from .prepare import Limits, build_review, decode_html

KINDS = ("txt", "md", "html", "epub", "docx", "pdf")


def apply_limits(memory_bytes: int, cpu_seconds: int) -> None:
    """Limit this process: address space, CPU time, no core files, no new files bigger than a few MB. Called first, before any file is opened."""
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))


def read(kind: str, path: str, charset: Optional[str], speakers: bool = True) -> Extracted:
    """The chapters of a source of the given kind. With `speakers` on, the readers that join lines (plain text, Markdown, PDF) start a paragraph at each speaker's line."""
    from .extract_text import decode_text, extract_markdown, extract_plain

    if kind == "txt":
        return extract_plain(decode_text(Path(path).read_bytes()), speakers)
    if kind == "md":
        return extract_markdown(decode_text(Path(path).read_bytes()), speakers)
    if kind == "html":
        from .extract_html import extract_html

        try:
            blocks, title = extract_html(decode_html(Path(path).read_bytes(), charset), article=True)
        except ValueError as exc:
            raise PrepareError("extract_failed", f"The page is too large or too tangled to read ({exc}).") from exc
        return Extracted(title=title, chapters=[Chapter(None, blocks)])
    if kind == "epub":
        from .extract_epub import extract_epub

        return extract_epub(path)
    if kind == "docx":
        from .extract_docx import extract_docx

        return extract_docx(path)
    if kind == "pdf":
        from .extract_pdf import extract_pdf

        return extract_pdf(path, speakers)
    raise PrepareError("unsupported", f"A {kind} file cannot be read.")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m studio.speech.text.reader")
    parser.add_argument("--kind", choices=KINDS, required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--charset", default=None)
    parser.add_argument("--source", default="{}", help="JSON describing where the text came from, passed through to the review")
    parser.add_argument("--max-chars", type=int, required=True)
    parser.add_argument("--part-max-chars", type=int, required=True)
    parser.add_argument("--chars-per-second", type=float, required=True)
    parser.add_argument("--no-clean", action="store_true")
    parser.add_argument("--read-extras", action="store_true")
    parser.add_argument("--no-speakers", action="store_true", help="do not look for speakers (DESIGN.md section 44.5)")
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--cpu-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    apply_limits(args.memory_mb * 1024 * 1024, args.cpu_seconds)
    try:
        extracted = read(args.kind, args.path, args.charset, speakers=not args.no_speakers)
        review = build_review(extracted, Limits(args.max_chars, args.part_max_chars, args.chars_per_second), json.loads(args.source),
                              clean=not args.no_clean, read_extras=args.read_extras, speakers=not args.no_speakers)
        answer = {"ok": True, "review": review}
    except PrepareError as exc:
        answer = {"ok": False, "error": exc.as_dict()}
    except MemoryError:
        answer = {"ok": False, "error": PrepareError("too_large", "The file needed more memory to read than is allowed.").as_dict()}
    sys.stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
