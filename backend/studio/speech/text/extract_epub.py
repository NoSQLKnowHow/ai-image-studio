"""Reading an EPUB (DESIGN.md §38.5 item 1, criteria 268 and 269).

An EPUB is a zip file of XHTML chapters plus a package file (OPF) that lists them in reading order (the "spine") and names the book. The table of contents
(an EPUB 3 navigation document, or an EPUB 2 NCX file) gives the chapters their titles. Each chapter in the spine becomes a `Chapter`. The cover, the
contents page, the copyright page and the like start out left out (the person can put them back on the review page), as does any chapter with almost no
text. Nothing in the archive is run: only its text is read, with the limits of `safe_zip`.
"""

from __future__ import annotations

import posixpath
import re
from typing import Optional
from urllib.parse import unquote

from .errors import PrepareError
from .extract_html import extract_html, parse
from .extract_html import Node
from .model import Block, Chapter, Extracted
from .safe_zip import open_zip, parse_xml, read_member

MAX_SPINE = 2000
MIN_CHAPTER_CHARS = 120  # a chapter with less text than this (a cover page, a divider) is left out
_FRONT_BACK = re.compile(
    r"^(?:the\s+)?(cover|title\s*page|half[\s-]?title|copyright|contents|table of contents|toc|dedication|acknowledg\w*|about the authors?|also by|"
    r"other books by|colophon|index|bibliography|further reading|notes|endnotes|footnotes|credits|permissions|imprint|praise for|newsletter|a note on)\b",
    re.IGNORECASE)
_XHTML = ("application/xhtml+xml", "text/html")


def _decode(data: bytes) -> str:
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _resolve(base_dir: str, href: str) -> str:
    """A path inside the archive from a link in the package file or the contents (relative to the file that holds it, URL-decoded, no fragment)."""
    return posixpath.normpath(posixpath.join(base_dir, unquote(href.split("#")[0])))


def _toc_titles(zf, opf_dir: str, manifest: dict) -> dict[str, str]:
    """Chapter titles by path, from the EPUB 3 navigation document if there is one, else the EPUB 2 NCX file."""
    titles: dict[str, str] = {}
    for item in manifest.values():
        if "nav" in item["properties"].split():
            path = _resolve(opf_dir, item["href"])
            data = read_member(zf, path)
            if data:
                root, _title = parse(_decode(data))
                navs = _nav_elements(root)
                folder = posixpath.dirname(path)
                stack = list(navs)
                while stack:
                    node = stack.pop(0)
                    for child in node.children:
                        if isinstance(child, Node):
                            if child.tag == "a" and child.attrs.get("href"):
                                titles.setdefault(_resolve(folder, child.attrs["href"]), child.text())
                            else:
                                stack.insert(0, child)
    if not titles:
        for item in manifest.values():
            if item["type"] == "application/x-dtbncx+xml":
                path = _resolve(opf_dir, item["href"])
                data = read_member(zf, path)
                if data:
                    folder = posixpath.dirname(path)
                    for point in parse_xml(data).iter():
                        if point.tag.endswith("navPoint"):
                            label = point.find(".//{*}navLabel/{*}text")
                            content = point.find("{*}content")
                            if label is not None and content is not None and content.get("src"):
                                titles.setdefault(_resolve(folder, content.get("src")), " ".join((label.text or "").split()))
    return titles


def _nav_elements(root: Node) -> list[Node]:
    """The `<nav>` elements that are the table of contents (epub:type="toc"), or all of them if none says so."""
    found, stack = [], [root]
    while stack:
        node = stack.pop()
        for child in node.children:
            if isinstance(child, Node):
                if child.tag == "nav":
                    found.append(child)
                stack.append(child)
    toc = [n for n in found if "toc" in n.attrs.get("epub:type", "").split()]
    return toc or found


def extract_epub(path: str) -> Extracted:
    """The chapters of an EPUB, in reading order. Raises PrepareError for a file that is not an EPUB or is damaged."""
    zf = open_zip(path)
    container = read_member(zf, "META-INF/container.xml")
    if container is None:
        raise PrepareError("extract_failed", "The file is not an EPUB (it has no container description).")
    rootfile = parse_xml(container).find(".//{*}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise PrepareError("extract_failed", "The EPUB does not say where its package file is.")
    opf_path = rootfile.get("full-path")
    opf_data = read_member(zf, opf_path)
    if opf_data is None:
        raise PrepareError("extract_failed", "The EPUB's package file is missing.")
    opf = parse_xml(opf_data)
    opf_dir = posixpath.dirname(opf_path)
    title_element = opf.find(".//{*}metadata/{*}title")
    book_title: Optional[str] = " ".join((title_element.text or "").split()) if title_element is not None and title_element.text else None

    manifest = {item.get("id"): {"href": item.get("href", ""), "type": item.get("media-type", ""), "properties": item.get("properties", "")}
                for item in opf.findall(".//{*}manifest/{*}item") if item.get("id")}
    spine = [ref for ref in opf.findall(".//{*}spine/{*}itemref") if ref.get("idref") in manifest]
    if not spine:
        raise PrepareError("no_text", "The EPUB has no chapters in it.")
    if len(spine) > MAX_SPINE:
        raise PrepareError("too_large", f"The EPUB has {len(spine)} sections, more than the {MAX_SPINE} that are allowed.")
    toc = _toc_titles(zf, opf_dir, manifest)

    chapters: list[Chapter] = []
    warnings: list[str] = []
    for number, ref in enumerate(spine, 1):
        item = manifest[ref.get("idref")]
        if item["type"] not in _XHTML:
            continue
        member = _resolve(opf_dir, item["href"])
        data = read_member(zf, member)
        if data is None:
            warnings.append(f"A chapter listed in the EPUB is missing from it ({posixpath.basename(member)}).")
            continue
        try:
            blocks, _page_title = extract_html(_decode(data), article=False)
        except ValueError:
            warnings.append(f"A chapter was too tangled to read ({posixpath.basename(member)}).")
            continue
        heading = next((b.text for b in blocks if b.kind == "heading"), None)
        title = toc.get(member) or heading or f"Section {number}"
        chars = sum(len(b.text) for b in blocks if b.kind in ("heading", "paragraph"))
        include, note = True, None
        if _FRONT_BACK.match(title.strip()) or (heading and _FRONT_BACK.match(heading.strip())) or ref.get("linear") == "no":
            include, note = False, "Front or back matter"
        elif chars < MIN_CHAPTER_CHARS:
            include, note = False, "Almost no text"
        if blocks and not (blocks[0].kind == "heading" and blocks[0].text.strip().lower() == title.strip().lower()):
            blocks = [Block("heading", title, 1), *blocks]  # the chapter's title is spoken, whether or not its text begins with it
        chapters.append(Chapter(title, blocks, include=include, note=note))
    if not any(c.include for c in chapters):
        raise PrepareError("no_text", "The EPUB has no text to read.")
    return Extracted(title=book_title, chapters=chapters, warnings=warnings)
