"""Reading the text layer of a PDF (DESIGN.md §38.5 item 1, criteria 268 and 273).

A PDF keeps lines of text at positions on a page, not paragraphs, so the paragraphs are rebuilt here from the lines, and only a PDF that has a text layer
can be read at all: a scan is a picture, and reading a picture needs OCR, which is not in 1.20 (§38.16). What is done, in order:
  1. pypdf pulls the text of each page (a page it cannot read is skipped, with a warning);
  2. lines that repeat at the top or bottom of most pages (a running title, a page number) are dropped;
  3. lines are gathered into paragraphs: a blank line ends one, and so does a line that ends like a sentence and is clearly shorter than the usual line
     (the last line of a paragraph); a word cut by a hyphen at the end of a line is made whole;
  4. chapters come from the PDF's bookmarks (its outline), if it has two or more at the top level; otherwise the text is one piece and is divided by size.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Any, Optional

from .clean import is_page_number, join_lines, normalize
from .errors import PASTE_HINT, PrepareError
from .model import Block, Chapter, Extracted
from .speakers import speaker_keys, starts_speaker_line

MAX_PAGES = 3000
EDGE_LINES = 3  # how many lines at the top and at the bottom of a page count as its header and footer
_SENTENCE_END = re.compile(r"[.!?…:\"”’')\]]$")


def _fingerprint(line: str) -> str:
    """A line as it repeats from page to page: digits all alike (so "Chapter 3 | 14" and "Chapter 3 | 15" are one line), case and spacing ignored."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", line.lower())).strip()


FURNITURE_LIMIT = 0.4  # lines judged to be furniture are only removed if they are less than this share of all the lines: if most of a document "repeats", it is not furniture


def _strip_furniture(pages: list[list[str]]) -> int:
    """Remove page numbers and the lines that repeat at the edges of at least half the pages, from every page; returns how many lines went. If the
    repeating lines would be more than `FURNITURE_LIMIT` of all the lines (a short document whose pages look alike), only the page numbers go."""
    removed = 0
    frequent: set[str] = set()
    if len(pages) >= 4:
        counts: Counter = Counter()
        for lines in pages:
            edges = {_fingerprint(line) for line in (*lines[:EDGE_LINES], *lines[-EDGE_LINES:]) if line.strip() and len(line) <= 80}
            counts.update(edges)
        frequent = {key for key, n in counts.items() if n >= max(3, len(pages) / 2) and key}
        total = sum(1 for lines in pages for line in lines if line.strip())
        repeating = sum(1 for lines in pages for index, line in enumerate(lines)
                        if (index < EDGE_LINES or index >= len(lines) - EDGE_LINES) and len(line) <= 80 and _fingerprint(line) in frequent)
        if total and repeating > FURNITURE_LIMIT * total:
            frequent = set()
    for lines in pages:
        keep = []
        for index, line in enumerate(lines):
            at_edge = index < EDGE_LINES or index >= len(lines) - EDGE_LINES
            if at_edge and (is_page_number(line) or (_fingerprint(line) in frequent and len(line) <= 80)):
                removed += 1
            else:
                keep.append(line)
        lines[:] = keep
    return removed


def _paragraphs(lines: list[str], keys: Optional[set[str]] = None) -> list[str]:
    """Paragraphs from the lines of one or more pages (see the module's description, step 3). A line that begins with a speaker's label (`keys`, DESIGN.md §44.5) starts a
    paragraph of its own, as a script's lines have no blank lines between them."""
    lengths = [len(line.strip()) for line in lines if len(line.strip()) > 20]
    usual = statistics.median(lengths) if lengths else 0
    paragraphs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            paragraphs.append(join_lines(current))
            current.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if keys and current and starts_speaker_line(stripped, keys):
            flush()
        current.append(stripped)
        if usual and _SENTENCE_END.search(stripped) and len(stripped) < 0.75 * usual:
            flush()
    flush()
    return [p for p in paragraphs if p]


def _outline_starts(reader: Any, page_count: int) -> list[tuple[str, int]]:
    """The top-level bookmarks of the PDF as (title, page index), in page order, one for each page."""
    starts: dict[int, str] = {}
    try:
        for item in reader.outline:
            if isinstance(item, list):  # the children of the bookmark before it: chapters are the top level only
                continue
            title = " ".join(str(getattr(item, "title", "") or "").split())
            page = reader.get_destination_page_number(item)
            if title and isinstance(page, int) and 0 <= page < page_count:
                starts.setdefault(page, title)
    except Exception:  # a damaged outline is not a reason to refuse the text
        return []
    return sorted(((title, page) for page, title in starts.items()), key=lambda pair: pair[1])


def extract_pdf(path: str, speakers: bool = True) -> Extracted:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PrepareError("extract_failed", "Reading PDF files is not installed on this server.", "Paste the text instead.") from exc
    warnings: list[str] = []
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise PrepareError("encrypted", "The PDF is locked with a password.", "Save an unlocked copy and add that, or paste the text.")
        page_count = len(reader.pages)
        if page_count > MAX_PAGES:
            raise PrepareError("too_large", f"The PDF has {page_count} pages, more than the {MAX_PAGES} that are allowed.")
        pages: list[list[str]] = []
        for number in range(page_count):
            try:
                text = reader.pages[number].extract_text() or ""
            except Exception:  # one page that cannot be read does not spoil the others
                warnings.append(f"Page {number + 1} could not be read and was skipped.")
                text = ""
            pages.append(normalize(text).split("\n"))
        outline = _outline_starts(reader, page_count)
        metadata = reader.metadata
        title: Optional[str] = " ".join(str(metadata.title).split()) if metadata is not None and metadata.title else None
    except PrepareError:
        raise
    except Exception as exc:  # pypdf's own errors, a truncated file, a recursion limit: any of them means "not a PDF we can read"
        raise PrepareError("extract_failed", "The PDF could not be read (it may be damaged).", PASTE_HINT) from exc

    if sum(len(line.strip()) for lines in pages for line in lines) < 30:
        raise PrepareError("no_text_layer", "This PDF has no text in it that can be read: it is probably a scan, which is a picture of the pages.",
                           "Reading a scan needs OCR, which the studio does not do. Paste the text instead.")
    blank = sum(1 for lines in pages if not "".join(lines).strip())
    if blank and blank >= max(2, page_count // 10):
        warnings.append(f"{blank} of the {page_count} pages have no text (pictures, or scanned pages) and were left out.")
    _strip_furniture(pages)
    keys = speaker_keys(line.strip() for lines in pages for line in lines) if speakers else None  # the speakers of the whole document, found before it is cut into chapters

    def blocks_for(first: int, last: int) -> list[Block]:
        return [Block("paragraph", p) for p in _paragraphs([line for lines in pages[first:last] for line in lines], keys)]

    if len(outline) >= 2:
        chapters: list[Chapter] = []
        if outline[0][1] > 0:  # pages before the first bookmark (a cover, a title page) are read as they stand
            front = blocks_for(0, outline[0][1])
            if front:
                chapters.append(Chapter(None, front))
        for index, (name, start) in enumerate(outline):
            end = outline[index + 1][1] if index + 1 < len(outline) else page_count
            blocks = blocks_for(start, end)
            if blocks:
                chapters.append(Chapter(name, [Block("heading", name, 1), *blocks]))
    else:
        chapters = [Chapter(None, blocks_for(0, page_count))]
    return Extracted(title=title, chapters=chapters, warnings=warnings)
