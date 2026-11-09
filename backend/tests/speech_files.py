"""Builders for the files the speech readers are tested on: Word, EPUB and PDF files made from scratch, and hostile archives (DESIGN.md §38.5).

None of the three formats has a writer in the standard library, and the tests must not depend on a document program, so each file is written here by hand,
in the smallest form a real reader accepts. That has a second use: the tests say exactly what is in the file.
"""

from __future__ import annotations

import io
import zipfile
from typing import Optional, Sequence, Union
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------- Word
def make_docx(items: Sequence[Union[str, tuple[str, str]]], *, title: Optional[str] = None, table: Optional[list[list[str]]] = None,
              wrap_in_control: bool = False, with_deleted: bool = False, styles: bool = True) -> bytes:
    """A .docx. Each item is a paragraph's text, or (style id, text): style ids "Heading1", "Heading2" and "Title" are what Word writes. `table` is rows of
    cells, added at the end; `wrap_in_control` puts the paragraphs in a content control (an `sdt`); `with_deleted` adds a tracked deletion to the first."""
    body = []
    for index, item in enumerate(items):
        style, text = item if isinstance(item, tuple) else (None, item)
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        deleted = '<w:del><w:r><w:delText>DELETED WORDS</w:delText></w:r></w:del>' if with_deleted and index == 0 else ""
        body.append(f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>{deleted}</w:p>')
    xml_body = "".join(body)
    if wrap_in_control:
        xml_body = f"<w:sdt><w:sdtContent>{xml_body}</w:sdtContent></w:sdt>"
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>" for row in table)
        xml_body += f"<w:tbl>{rows}</w:tbl>"
    document = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W_NS}"><w:body>{xml_body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("word/document.xml", document)
        if styles:
            names = {"Heading1": "heading 1", "Heading2": "heading 2", "Heading3": "heading 3", "Title": "Title"}
            zf.writestr("word/styles.xml", f'<?xml version="1.0"?><w:styles xmlns:w="{W_NS}">' + "".join(
                f'<w:style w:styleId="{i}"><w:name w:val="{n}"/></w:style>' for i, n in names.items()) + "</w:styles>")
        if title:
            zf.writestr("docProps/core.xml", '<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                        f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{escape(title)}</dc:title></cp:coreProperties>')
    return out.getvalue()


# ---------------------------------------------------------------- EPUB
def make_epub(chapters: Sequence[tuple[str, Optional[str], str]], *, title: str = "A Book", toc: str = "nav", missing: Sequence[str] = (),
              entity_bomb: bool = False) -> bytes:
    """An EPUB. `chapters` are (file name, title in the contents or None, the XHTML body). `toc` is "nav" (EPUB 3), "ncx" (EPUB 2) or "none". Files named in
    `missing` are listed in the package but left out of the archive. `entity_bomb` puts an XML entity definition in the package file."""
    manifest, spine, nav_links, ncx_points = [], [], [], []
    for number, (name, chapter_title, _body) in enumerate(chapters, 1):
        manifest.append(f'<item id="c{number}" href="text/{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{number}"/>')
        if chapter_title is not None:
            nav_links.append(f'<li><a href="text/{name}">{escape(chapter_title)}</a></li>')
            ncx_points.append(f'<navPoint id="n{number}"><navLabel><text>{escape(chapter_title)}</text></navLabel><content src="text/{name}"/></navPoint>')
    if toc == "nav":
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    elif toc == "ncx":
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    bomb = '<!DOCTYPE package [<!ENTITY a "aaaaaaaaaa">]>' if entity_bomb else ""
    opf = (f'<?xml version="1.0"?>{bomb}<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:title>{escape(title)}</dc:title></metadata><manifest>{"".join(manifest)}</manifest><spine>{"".join(spine)}</spine></package>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr("content.opf", opf)
        if toc == "nav":
            zf.writestr("nav.xhtml", '<html xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>' + "".join(nav_links) + "</ol></nav></body></html>")
        elif toc == "ncx":
            zf.writestr("toc.ncx", '<?xml version="1.0"?><!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">'
                        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>' + "".join(ncx_points) + "</navMap></ncx>")
        for name, _t, body in chapters:
            if name not in missing:
                zf.writestr(f"text/{name}", f'<?xml version="1.0"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title></head><body>{body}</body></html>')
    return out.getvalue()


# ---------------------------------------------------------------- PDF
def _pdf_text(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: Sequence[Sequence[str]], *, outline: Sequence[tuple[str, int]] = (), title: Optional[str] = None) -> bytes:
    """A PDF with Helvetica text: one list of lines for each page. `outline` is (title, page index) bookmarks. Written by hand with a correct cross-reference
    table, in the simplest form a real reader accepts."""
    objects: dict[int, bytes] = {}
    page_ids = [4 + 2 * i for i in range(len(pages))]  # page object, then its content stream
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R " + (f"/Outlines {4 + 2 * len(pages) + 1} 0 R ".encode() if outline else b"") + b">>"
    objects[2] = b"<< /Type /Pages /Kids [" + b" ".join(f"{p} 0 R".encode() for p in page_ids) + f"] /Count {len(pages)} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for i, lines in enumerate(pages):
        page, content = page_ids[i], page_ids[i] + 1
        objects[page] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R /Resources << /Font << /F1 3 0 R >> >> >>").encode()
        stream = "BT /F1 11 Tf 14 TL 50 750 Td " + " ".join(f"({_pdf_text(line)}) Tj T*" for line in lines) + " ET"
        objects[content] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode("latin-1")
    next_id = 4 + 2 * len(pages)
    if outline:
        root, first = next_id + 1, next_id + 2
        for n, (name, page_index) in enumerate(outline):
            parts = [f"/Title ({_pdf_text(name)})", f"/Parent {root} 0 R", f"/Dest [{page_ids[page_index]} 0 R /Fit]"]
            if n > 0:
                parts.append(f"/Prev {first + n - 1} 0 R")
            if n < len(outline) - 1:
                parts.append(f"/Next {first + n + 1} 0 R")
            objects[first + n] = ("<< " + " ".join(parts) + " >>").encode("latin-1")
        objects[root] = f"<< /Type /Outlines /First {first} 0 R /Last {first + len(outline) - 1} 0 R /Count {len(outline)} >>".encode()
        next_id = root
    if title:
        objects[next_id + 1] = f"<< /Title ({_pdf_text(title)}) >>".encode("latin-1")
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    size = max(objects) + 1
    xref = len(out)
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode() if number in offsets else b"0000000000 65535 f \n"
    info = f" /Info {next_id + 1} 0 R" if title else ""
    out += f"trailer\n<< /Size {size} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---------------------------------------------------------------- hostile archives
def zip_bomb(member_mb: int = 70) -> bytes:
    """A small zip whose one member unpacks to `member_mb` megabytes of zeros (it deflates to a few tens of kilobytes)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", bytes(member_mb * 1024 * 1024))
    return out.getvalue()


def many_entries(count: int) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for n in range(count):
            zf.writestr(f"f{n}", b"x")
    return out.getvalue()
