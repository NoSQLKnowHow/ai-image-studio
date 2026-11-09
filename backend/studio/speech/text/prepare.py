"""From what a reader found to the review the page shows (DESIGN.md §38.5 items 3 and 5).

`build_review` takes an `Extracted` (chapters of blocks) and does the whole of the preparation: cleaning every block (`clean.py`), leaving out code and
tables unless asked, checking the size, dividing into parts (`parts.py`), and describing what was done. The result is a plain dict, which is what
`POST /api/speech/prepare` answers and what the page edits before **Make reading**.

It runs in the reader process (`reader.py`), not in the API process, because the cleaning rules are regular expressions applied to text from outside.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .clean import Report, clean_text, collapse_spaces, normalize
from .errors import PASTE_HINT, PrepareError
from .model import SKIP_CODE, SKIP_TABLE, Chapter, Extracted, Paragraph
from .parts import draft_parts, title_from

SKIPPED_PREVIEW_CHARS = 1000  # how much of a skipped block the review carries: it is shown greyed, not read


@dataclass(frozen=True)
class Limits:
    """What the preparation obeys (`capabilities.limits.speech`)."""

    max_chars: int
    part_max_chars: int
    chars_per_second: float


def decode_html(data: bytes, charset: Optional[str]) -> str:
    """HTML bytes as text: the charset the server named if it is one Python knows, else the one the page names in a `<meta>` near its top, else UTF-8,
    else Windows-1252."""
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    head = data[:4096].decode("ascii", errors="ignore")
    meta = re.search(r"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", head, re.IGNORECASE)
    if meta:
        candidates.append(meta.group(1))
    candidates.append("utf-8")
    for name in candidates:
        try:
            return data.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("cp1252", errors="replace")


def _paragraphs(chapter: Chapter, report: Report, clean: bool, read_extras: bool) -> list[Paragraph]:
    """A chapter's blocks as cleaned paragraphs. Code and tables are kept but marked skipped, so the review can show them greyed and a switch can read them."""
    out: list[Paragraph] = []
    for block in chapter.blocks:
        if block.kind in ("heading", "paragraph"):
            text = clean_text(block.text, report) if clean else collapse_spaces(normalize(block.text))
            if text:
                out.append(Paragraph(block.kind, text))
            continue
        skipped = SKIP_CODE if block.kind == "code" else SKIP_TABLE
        if read_extras:
            # a table read aloud is its rows one after the other; code is read as the words it has, on one line
            text = collapse_spaces(block.text.replace("\n", ". " if block.kind == "table" else " "))
            if text:
                out.append(Paragraph("paragraph", clean_text(text, report) if clean else text))
        else:
            shown = block.text.strip()
            if len(shown) > SKIPPED_PREVIEW_CHARS:
                shown = shown[:SKIPPED_PREVIEW_CHARS] + "…"
            if shown:
                out.append(Paragraph("paragraph", shown, skipped=skipped))
                if skipped == SKIP_CODE:
                    report.code_blocks += 1
                else:
                    report.tables += 1
    return out


def build_review(extracted: Extracted, limits: Limits, source: dict[str, Any], *, clean: bool = True, read_extras: bool = False) -> dict[str, Any]:
    """The review of a source: its parts and paragraphs, what was cleaned, any warnings. Raises PrepareError for a source with no text or too much."""
    report = Report()
    cache: dict[int, list[Paragraph]] = {}

    def paragraphs_of(chapter: Chapter) -> list[Paragraph]:
        if id(chapter) not in cache:
            cache[id(chapter)] = _paragraphs(chapter, report, clean, read_extras)
        return cache[id(chapter)]

    # clean everything once, in order, so the report is complete and the size can be judged before the parts are made
    included = [c for c in extracted.chapters if c.include]
    spoken = sum(len(p.text) for c in extracted.chapters for p in paragraphs_of(c) if p.skipped is None and c.include)
    if spoken == 0:
        raise PrepareError("no_text", "No text that can be read was found.", PASTE_HINT)
    if spoken > limits.max_chars:
        raise PrepareError("too_large", f"The text has {spoken:,} characters; one reading can hold {limits.max_chars:,}. Leave some parts out, or make two readings.")
    parts = draft_parts(extracted.chapters, paragraphs_of, limits.part_max_chars)
    first_included = next((p for p in parts if p.include), parts[0])
    title = (collapse_spaces(extracted.title) if extracted.title else None) or title_from(first_included.paragraphs)
    total = sum(p.chars for p in parts if p.include)
    return {
        "title": title[:200],
        "source": source,
        "parts": [p.as_dict(limits.chars_per_second) for p in parts],
        "warnings": list(extracted.warnings),
        "cleaning": report.lines() if clean else [],
        "chars": total,
        "minutes": round(total / limits.chars_per_second / 60, 1),
        "options": {"clean": clean, "read_extras": read_extras},
        "chapters_found": len(included),
    }
