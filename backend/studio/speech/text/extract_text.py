"""Readers for plain text and Markdown files, and for pasted text (DESIGN.md §38.5 item 1).

A plain text file has no structure of its own, so the paragraphs are found from its blank lines and its wrapped lines (`clean.split_paragraphs`), and
chapters from lines like "CHAPTER IV." that stand alone, which is how books are usually set in a text file. Markdown has structure, which is used:
headings, lists (each item is a paragraph, so each gets its pause), quotations, fenced code and tables.
"""

from __future__ import annotations

import re

from .clean import Report, normalize, split_paragraphs
from .errors import PrepareError
from .model import Block, Chapter, Extracted
from .speakers import speaker_keys, starts_speaker_line

# A line that stands alone and names a division of a book. Case does not matter; the rest of the line (a number, a title) is limited so that a sentence
# that happens to start with "Part of the reason" is not taken for a heading: the whole line must be short and must not end like a sentence.
_DIVISION = re.compile(r"^(chapter|part|book|section|prologue|epilogue|preface|introduction|foreword|afterword|appendix|act|scene)\b[^\n]{0,70}$", re.IGNORECASE)
_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^[\s|:\-]+$")


def decode_text(data: bytes) -> str:
    """Bytes of a text file as text: UTF-8 (with or without a byte order mark), UTF-16 where a mark says so, else Windows-1252, which is what an old
    text file from a Windows machine is. Raises PrepareError for a file that is binary (it holds NUL bytes and is not UTF-16)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data[:4096]:
        raise PrepareError("bad_encoding", "The file looks like a binary file, not text.", "Choose a .txt, .md, .pdf, .epub or .docx file.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# Text that is Markdown although it was pasted or saved as plain text: a heading line, a code fence, or a table. Read as plain text, "# A Title" would be
# spoken as "hash A Title". Lists are not a sign: a bulleted line in plain text is rare and in prose is just a line.
_LOOKS_LIKE_MARKDOWN = re.compile(r"^(?:#{1,6}[ \t]+\S|\s{0,3}(?:```|~~~)|\s*\|.+\|[ \t]*\n[ \t]*\|[ \t:|-]+\|)", re.MULTILINE)


def extract_plain(text: str, speakers: bool = True) -> Extracted:
    """Pasted text or a .txt file: paragraphs from the blank lines, chapters from lines that name one (two or more of them, else the text is one piece).
    Text that is really Markdown (see `_LOOKS_LIKE_MARKDOWN`) is read as Markdown, because people paste it from chat programs and editors. With `speakers` on (the
    default), a line that begins with a speaker's name starts a paragraph of its own (DESIGN.md §44.5); the speakers are found from the whole text first."""
    if _LOOKS_LIKE_MARKDOWN.search(text):
        return extract_markdown(text, speakers)
    report = Report()
    blocks: list[Block] = []
    keys = speaker_keys(normalize(text).split("\n")) if speakers else None
    for paragraph in split_paragraphs(text, report, keys):
        blocks.append(Block("heading", paragraph, 1) if _names_a_division(paragraph) else Block("paragraph", paragraph))
    return Extracted(title=None, chapters=_chapters(blocks, split_level=1, minimum=2))


def _names_a_division(line: str) -> bool:
    """Whether a paragraph is only the name of a division of a book ("CHAPTER IV.", "Part Two", "Chapter 3: The Return", "PROLOGUE"). It must be short and begin
    with such a word; and if it ends like a sentence it must be four words or fewer, so that "Part of the reason we left was the weather." is a sentence."""
    line = line.strip()
    if len(line) > 80 or not _DIVISION.match(line):
        return False
    return not re.search(r"[.!?]$", line) or len(line.split()) <= 4


def extract_markdown(text: str, speakers: bool = True) -> Extracted:
    """A Markdown file. Chapters are cut at the level-1 headings if there are two or more of them, else at the level-2 headings if there are two or more
    of those; a single level-1 heading is the document's title. With `speakers` on, a line that begins with a speaker's name starts a new paragraph (DESIGN.md §44.5)."""
    lines = normalize(text).split("\n")
    keys = speaker_keys(line.strip() for line in lines) if speakers else set()
    blocks: list[Block] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block("paragraph", " ".join(paragraph)))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        atx = _ATX.match(line)
        if fence:  # a fenced code block runs to the matching fence (or to the end of the file)
            flush()
            marker = fence.group(1)[0]
            code: list[str] = []
            i += 1
            while i < len(lines) and not (lines[i].lstrip().startswith(marker * 3)):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(code)))
        elif atx:
            flush()
            blocks.append(Block("heading", atx.group(2), len(atx.group(1))))
        elif i + 1 < len(lines) and line.strip() and not paragraph and _SETEXT.match(lines[i + 1]) and not _ITEM.match(line) and not _RULE.match(line):
            flush()  # "Title" over a line of === or --- is a heading too
            blocks.append(Block("heading", line.strip(), 1 if lines[i + 1].lstrip().startswith("=") else 2))
            i += 1
        elif _RULE.match(line):
            flush()
        elif _TABLE_ROW.match(line):
            flush()
            rows = []
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                if not _TABLE_RULE.match(lines[i]):
                    rows.append(", ".join(cell.strip() for cell in lines[i].strip().strip("|").split("|")))
                i += 1
            i -= 1
            blocks.append(Block("table", "\n".join(rows)))
        elif _ITEM.match(line):  # each list item is a paragraph of its own; its indented continuation lines belong to it
            flush()
            item = [_ITEM.match(line).group(2).strip()]  # type: ignore[union-attr]
            while i + 1 < len(lines) and lines[i + 1].startswith(("  ", "\t")) and lines[i + 1].strip() and not _ITEM.match(lines[i + 1]):
                item.append(lines[i + 1].strip())
                i += 1
            blocks.append(Block("paragraph", " ".join(item)))
        elif not line.strip():
            flush()
        else:
            quote = _QUOTE.match(line)
            words = (quote.group(1) if quote else line).strip()
            if keys and paragraph and starts_speaker_line(words, keys):
                flush()  # a speaker's line begins here, with no blank line before it
            paragraph.append(words)
        i += 1
    flush()
    levels = [b.level for b in blocks if b.kind == "heading"]
    split_level = 1 if levels.count(1) >= 2 else 2 if levels.count(2) >= 2 else 0
    title = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), None) if levels.count(1) == 1 else None
    return Extracted(title=title, chapters=_chapters(blocks, split_level=split_level, minimum=2))


def _chapters(blocks: list[Block], split_level: int, minimum: int) -> list[Chapter]:
    """Cut `blocks` into chapters at the headings of level `split_level` (0: no cutting). Words before the first heading are a chapter with no title."""
    if split_level == 0 or sum(1 for b in blocks if b.kind == "heading" and b.level == split_level) < minimum:
        return [Chapter(None, blocks)]
    chapters: list[Chapter] = []
    current = Chapter(None, [])
    for block in blocks:
        if block.kind == "heading" and block.level == split_level:
            if current.blocks:
                chapters.append(current)
            current = Chapter(block.text, [block])
        else:
            current.blocks.append(block)
    if current.blocks:
        chapters.append(current)
    return chapters
