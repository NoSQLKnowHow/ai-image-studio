"""Dividing a text into parts (DESIGN.md §38.5 item 5, criterion 269).

A reading is made and played one part at a time, and a reading goes back to the queue after every part (§38.6), so the size of a part is the size of
the wait that a job from another tab may have to put up with. The rules:
  * chapters come from the source where it has them (an EPUB's table of contents, a Word file's Heading 1, Markdown's `#`, a PDF's bookmarks);
  * no part is longer than `part_max_chars`: a longer chapter is divided at paragraph boundaries into balanced parts titled `Chapter 7 (1)`, `Chapter 7 (2)`...;
  * a chapter shorter than `MERGE_BELOW` characters is merged into the next included one, unless that would make a part that is too long or it is the last;
  * a text with no chapters is divided by size alone, into `Part 1`, `Part 2`... (or kept whole, with a title from its first words, when it is short).
"""

from __future__ import annotations

import math
import re
from typing import Optional

from .model import Chapter, Paragraph, PartDraft

MERGE_BELOW = 1500  # a chapter with fewer characters than this is joined to the one after it
TITLE_WORDS = 8  # how many words of the first paragraph give an untitled text its title
TITLE_CHARS = 60


def _chars(paragraphs: list[Paragraph]) -> int:
    return sum(len(p.text) for p in paragraphs if p.skipped is None)


def title_from(paragraphs: list[Paragraph], fallback: str = "Reading") -> str:
    """A title for a text that has none: its first heading, else the first few words of its first paragraph."""
    for p in paragraphs:
        if p.skipped is None and p.text.strip():
            words = re.sub(r"\s+", " ", p.text).strip().split(" ")
            title = " ".join(words[:TITLE_WORDS])
            title = title[:TITLE_CHARS].rstrip(" ,;:-") if len(title) > TITLE_CHARS else title.rstrip(" ,;:-")
            return title + ("…" if len(words) > TITLE_WORDS or len(" ".join(words[:TITLE_WORDS])) > TITLE_CHARS else "") if title else fallback
    return fallback


def split_by_size(paragraphs: list[Paragraph], part_max_chars: int) -> list[list[Paragraph]]:
    """Paragraphs divided into the fewest groups of at most `part_max_chars`, as even as paragraph boundaries allow. A heading is never left as the last
    paragraph of a group (it goes with the paragraph after it). A paragraph longer than the limit is a group of its own (it is cut into pieces later)."""
    total = _chars(paragraphs)
    if total <= part_max_chars:
        return [paragraphs]
    count = math.ceil(total / part_max_chars)
    target = total / count
    groups: list[list[Paragraph]] = []
    current: list[Paragraph] = []
    size = 0
    for p in paragraphs:
        n = len(p.text) if p.skipped is None else 0
        over = size + n > part_max_chars
        # start a new group when this paragraph would overflow it, or when the group has reached its share and a boundary is here
        if current and (over or (size >= target and len(groups) < count - 1)):
            if current[-1].kind == "heading":  # a heading belongs to what it heads
                carried = current.pop()
                if current:
                    groups.append(current)
                current, size = [carried], len(carried.text)
            else:
                groups.append(current)
                current, size = [], 0
        current.append(p)
        size += n
    if current:
        groups.append(current)
    return groups


def draft_parts(chapters: list[Chapter], paragraphs_of, part_max_chars: int) -> list[PartDraft]:
    """The parts of a reading from its chapters, in the source's order. `paragraphs_of(chapter)` gives a chapter's cleaned paragraphs (the caller does the
    cleaning, so this stays about size). Chapters left out stay in the list, not included, so the person can put them back; a tiny chapter is only ever
    merged into the chapter immediately after it, and only if that one is included."""
    drafts: list[PartDraft] = []
    pending: Optional[tuple[str, list[Paragraph]]] = None  # a tiny chapter waiting to be joined to the next one
    single = len(chapters) == 1 and chapters[0].title is None  # a text with no divisions: titled from its words, numbered if it is cut
    for index, chapter in enumerate(chapters):
        paragraphs = paragraphs_of(chapter)
        title = chapter.title or ("" if single else "Part")
        if not chapter.include:
            if pending is not None:  # what waited for this chapter cannot be joined to it: it stands alone
                drafts.append(PartDraft(*pending))
                pending = None
            drafts.append(PartDraft(title or "Part", paragraphs, include=False, note=chapter.note))
            continue
        if pending is not None:
            joined = pending[1] + paragraphs
            if _chars(joined) <= part_max_chars:
                paragraphs = joined  # the tiny chapter's words (its heading included) are read first, and the part keeps the later title
            else:
                drafts.append(PartDraft(*pending))
            pending = None
        following = chapters[index + 1] if index + 1 < len(chapters) else None
        if _chars(paragraphs) < MERGE_BELOW and following is not None and following.include:
            pending = (title or "Part", paragraphs)
            continue
        groups = split_by_size(paragraphs, part_max_chars)
        if single:
            base = title_from(paragraphs)
            for i, group in enumerate(groups):
                drafts.append(PartDraft(base if len(groups) == 1 else f"Part {i + 1}", group))
        else:
            for i, group in enumerate(groups):
                drafts.append(PartDraft(title if len(groups) == 1 else f"{title} ({i + 1})", group))
    if pending is not None:  # unreachable while a pending chapter needs an included follower, kept so that no text can ever be lost
        drafts.append(PartDraft(*pending))
    return drafts
