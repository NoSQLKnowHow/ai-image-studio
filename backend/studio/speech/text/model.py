"""The records that move through the text pipeline (DESIGN.md §38.5).

A reader (one for each kind of source) produces an `Extracted`: chapters of blocks. `prepare.py` turns that into the parts and paragraphs the page
shows for review; `pieces.py` turns the paragraphs the person settled on into the pieces an engine speaks. Nothing here depends on anything else
in the studio, so a reader can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

BLOCK_KINDS = ("heading", "paragraph", "code", "table")
# What a block that will not be read is called on the page: the reason it is shown greyed (DESIGN.md §38.2).
SKIP_CODE = "code"
SKIP_TABLE = "table"


@dataclass(frozen=True)
class Block:
    """One block of a source, as a reader found it, before cleaning. `text` of a heading or paragraph is plain text; of a code block or a table it is
    the text that reading it aloud would give (a table's cells joined row by row)."""

    kind: str  # "heading", "paragraph", "code" or "table"
    text: str
    level: int = 0  # a heading's level, 1 to 6; 0 otherwise


@dataclass
class Chapter:
    """A run of blocks with a title, as the source divides itself: an EPUB's chapter, a Word file's Heading 1, a PDF bookmark. A source with no
    divisions is one chapter with no title."""

    title: Optional[str]
    blocks: list[Block] = field(default_factory=list)
    include: bool = True  # an EPUB's cover, contents and legal pages start left out (the person can put them back)
    note: Optional[str] = None  # why it is left out, in a few words


@dataclass
class Extracted:
    """What a reader found in a source."""

    title: Optional[str]
    chapters: list[Chapter]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "warnings": list(self.warnings),
            "chapters": [{"title": c.title, "include": c.include, "note": c.note,
                          "blocks": [{"kind": b.kind, "text": b.text, "level": b.level} for b in c.blocks]} for c in self.chapters],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Extracted":
        """The inverse of `as_dict`, for what a reader process sends back (`isolated.py`)."""
        chapters = [Chapter(title=c.get("title"), include=bool(c.get("include", True)), note=c.get("note"),
                            blocks=[Block(kind=b["kind"], text=b["text"], level=int(b.get("level", 0))) for b in c["blocks"]])
                    for c in raw["chapters"]]
        return cls(title=raw.get("title"), chapters=chapters, warnings=list(raw.get("warnings", [])))


@dataclass(frozen=True)
class Paragraph:
    """A cleaned paragraph of a part, as the page shows it and as a reading is made from it. `skipped` is None for text that will be read, or why it
    will not be (`SKIP_CODE`, `SKIP_TABLE`): the page greys it and a switch reads it anyway."""

    kind: str  # "heading" or "paragraph"
    text: str
    skipped: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "skipped": self.skipped}


@dataclass
class PartDraft:
    """A part of a reading before it is made: a title, the paragraphs, and whether it is included (an EPUB's front matter starts out not)."""

    title: str
    paragraphs: list[Paragraph]
    include: bool = True
    note: Optional[str] = None

    @property
    def chars(self) -> int:
        """The characters that will be read: every paragraph that is not skipped."""
        return sum(len(p.text) for p in self.paragraphs if p.skipped is None)

    def as_dict(self, chars_per_second: float) -> dict[str, Any]:
        chars = self.chars
        return {"title": self.title, "chars": chars, "minutes": round(chars / chars_per_second / 60, 1), "include": self.include, "note": self.note,
                "paragraphs": [p.as_dict() for p in self.paragraphs]}


@dataclass(frozen=True)
class PieceSpec:
    """One piece: what one call to an engine speaks and what the player highlights (DESIGN.md §38.5 item 4)."""

    kind: str  # "heading" or "paragraph"
    text: str
    continues: bool = False  # true for a piece that carries on a paragraph begun in the piece before it
