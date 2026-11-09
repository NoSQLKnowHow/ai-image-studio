"""Preparing a source for the API (DESIGN.md §38.5, §38.9): get the bytes, hand them to the reader process, return its review.

The three ways in (pasted text, a web address, a file) end the same way: the bytes are written to a private temporary folder (never inside the data
folder and never somewhere the reader could import code from) and `reader.py` is run on them in a child process with limits. This module runs blocking
work (a fetch, a child process), so the API calls it in a thread.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from . import SPOKEN_CHARS_PER_SECOND
from .text.errors import PrepareError
from .text.fetch import Connector, Resolver, fetch_page
from .text.isolated import run_reader

# the extension of a file the person gives, and the kind of reader that reads it
FILE_KINDS = {".txt": "txt", ".text": "txt", ".md": "md", ".markdown": "md", ".pdf": "pdf", ".epub": "epub", ".docx": "docx"}
# the type of a fetched page, and the reader
PAGE_KINDS = {"text/html": "html", "application/xhtml+xml": "html", "text/plain": "txt", "text/markdown": "md", "application/pdf": "pdf"}
READER_TIMEOUT_SECONDS = 75.0  # a little over the reader's own CPU limit (60), so its own refusal is the one that is heard


def safe_name(name: str) -> str:
    """A file name for display: the last part of whatever was given, control characters gone, at most 120 characters."""
    base = os.path.basename(name.replace("\\", "/")).strip()
    return "".join(c for c in base if c.isprintable())[:120] or "file"


def _run(settings: Settings, kind: str, data: bytes, source: dict[str, Any], *, charset: Optional[str], clean: bool, read_extras: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="studio-speech-") as folder:
        path = Path(folder) / f"source.{kind}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        args = ["--kind", kind, "--path", str(path), "--source", json.dumps(source), "--max-chars", str(settings.speech_max_chars),
                "--part-max-chars", str(settings.speech_part_max_chars), "--chars-per-second", str(SPOKEN_CHARS_PER_SECOND)]
        if charset:
            args += ["--charset", charset]
        if not clean:
            args.append("--no-clean")
        if read_extras:
            args.append("--read-extras")
        return run_reader(args, timeout=READER_TIMEOUT_SECONDS)


def prepare_text(settings: Settings, text: str, *, clean: bool = True, read_extras: bool = False) -> dict[str, Any]:
    """Pasted text."""
    if not text.strip():
        raise PrepareError("empty", "There is no text to read yet.", "Paste some text, give a web address, or choose a file.")
    return _run(settings, "txt", text.encode("utf-8"), {"kind": "text"}, charset=None, clean=clean, read_extras=read_extras)


def prepare_file(settings: Settings, name: str, data: bytes, *, clean: bool = True, read_extras: bool = False) -> dict[str, Any]:
    """A file the person chose. Its kind comes from its extension (the content is judged by the reader, which refuses what it cannot read)."""
    shown = safe_name(name)
    kind = FILE_KINDS.get(os.path.splitext(shown)[1].lower())
    if kind is None:
        raise PrepareError("unsupported", f"{shown} is not a kind of file that can be read.", "Choose a .txt, .md, .pdf, .epub or .docx file.")
    if not data:
        raise PrepareError("empty", f"{shown} is empty.")
    return _run(settings, kind, data, {"kind": "file", "name": shown}, charset=None, clean=clean, read_extras=read_extras)


def prepare_url(settings: Settings, url: str, *, clean: bool = True, read_extras: bool = False, resolve: Optional[Resolver] = None,
                connector: Optional[Connector] = None) -> dict[str, Any]:
    """A web page (or a text or PDF file at a web address), fetched with the guards of `text/fetch.py`."""
    fetched = fetch_page(url, max_bytes=settings.speech_fetch_max_mb * 1024 * 1024, step_timeout=settings.speech_fetch_timeout_s,
                         resolve=resolve, connector=connector)
    kind = PAGE_KINDS[fetched.content_type]  # fetch_page only returns the types in PAGE_KINDS
    from urllib.parse import urlsplit

    return _run(settings, kind, fetched.body, {"kind": "url", "url": fetched.url[:300], "host": urlsplit(fetched.url).hostname or ""},
                charset=fetched.charset, clean=clean, read_extras=read_extras)
