"""Reading a Word file (.docx) (DESIGN.md §38.5 item 1, criteria 268 and 269).

A .docx is a zip file whose body text is `word/document.xml`. Paragraphs come out in order; a paragraph in a Heading style is a heading; a table is a table
block (left out unless asked for). Headers, footers, footnotes, endnotes, comments and tracked deletions are in other parts or marked differently and are
not read. Chapters are cut at Heading 1 if there are two or more of them, else at Heading 2 if there are two or more of those.
"""

from __future__ import annotations

import re
from typing import Optional

from .errors import PrepareError
from .extract_text import _chapters
from .model import Block, Extracted
from .safe_zip import open_zip, parse_xml, read_member

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING = re.compile(r"^heading\s*([1-9])$", re.IGNORECASE)
MAX_BLOCKS = 200_000


def _style_names(zf) -> dict[str, str]:
    """Word's style ids and the names the person sees for them ("Heading1" and "heading 1"), from `word/styles.xml`."""
    data = read_member(zf, "word/styles.xml")
    if data is None:
        return {}
    names = {}
    for style in parse_xml(data).iter(W + "style"):
        name = style.find(W + "name")
        if style.get(W + "styleId") and name is not None and name.get(W + "val"):
            names[style.get(W + "styleId")] = name.get(W + "val")
    return names


def _text(paragraph) -> str:
    """The text of a paragraph, from its runs: text, tabs and line breaks (as spaces) and non-breaking hyphens. Deleted text and field codes are not text."""
    parts = []
    for node in paragraph.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag in (W + "tab", W + "br", W + "cr"):
            parts.append(" ")
        elif node.tag == W + "noBreakHyphen":
            parts.append("-")
    return " ".join("".join(parts).split())


def _heading_level(paragraph, names: dict[str, str]) -> int:
    """0 for an ordinary paragraph; 1 to 9 for a heading; 1 for the Title style as well."""
    style = paragraph.find(f"{W}pPr/{W}pStyle")
    if style is None or not style.get(W + "val"):
        return 0
    style_id = style.get(W + "val")
    for candidate in (names.get(style_id, ""), style_id):
        match = _HEADING.match(candidate.replace("_", " "))
        if match:
            return int(match.group(1))
        if candidate.lower() == "title":
            return 1
    return 0


def extract_docx(path: str) -> Extracted:
    zf = open_zip(path)
    data = read_member(zf, "word/document.xml")
    if data is None:
        raise PrepareError("extract_failed", "The file is not a Word document (it has no body).")
    body = parse_xml(data).find(W + "body")
    if body is None:
        raise PrepareError("extract_failed", "The Word document has no body.")
    names = _style_names(zf)
    blocks: list[Block] = []

    def walk(container) -> None:
        for element in container:
            if len(blocks) > MAX_BLOCKS:
                raise PrepareError("too_large", "The Word document has more paragraphs than a book can need.")
            if element.tag == W + "p":
                text = _text(element)
                if text:
                    level = _heading_level(element, names)
                    blocks.append(Block("heading", text, level) if level else Block("paragraph", text))
            elif element.tag == W + "tbl":
                rows = []
                for row in element.iter(W + "tr"):
                    cells = [" ".join(_text(p) for p in cell.iter(W + "p")).strip() for cell in row.findall(W + "tc")]
                    if any(cells):
                        rows.append(", ".join(cells))
                if rows:
                    blocks.append(Block("table", "\n".join(rows)))
            elif element.tag == W + "sdt":  # a content control wraps ordinary paragraphs
                content = element.find(W + "sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    if not any(b.kind in ("heading", "paragraph") for b in blocks):
        raise PrepareError("no_text", "The Word document has no text in it.")
    levels = [b.level for b in blocks if b.kind == "heading"]
    split_level = 1 if levels.count(1) >= 2 else 2 if levels.count(2) >= 2 else 0
    title: Optional[str] = None
    core = read_member(zf, "docProps/core.xml")
    if core is not None:
        node = parse_xml(core).find("{http://purl.org/dc/elements/1.1/}title")
        title = " ".join((node.text or "").split()) if node is not None and node.text else None
    if title is None and levels.count(1) == 1:
        title = next(b.text for b in blocks if b.kind == "heading" and b.level == 1)
    return Extracted(title=title, chapters=_chapters(blocks, split_level=split_level, minimum=2))
