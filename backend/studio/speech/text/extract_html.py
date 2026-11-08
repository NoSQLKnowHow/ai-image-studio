"""Reading the text of a web page, or of an EPUB's XHTML (DESIGN.md §38.5 item 1).

A page is parsed into a small tree with the standard library's lenient HTML parser (no network, no scripts are ever run), and the blocks of text are
read out of it in document order. For a web page (`article=True`) the reader first finds where the article is and leaves the rest of the page behind:
  1. the `<article>` with the most paragraph text, if there is one with a real amount;
  2. else `<main>` (or `role="main"`);
  3. else the deepest element that holds most (70%) of all the page's paragraph text;
  4. else the whole `<body>`.
Inside it, navigation, asides, footers, comment sections, share buttons, cookie banners and the like are left out by tag and by what their class or id says.
This is a plain heuristic, not a promise: a page that builds its text with JavaScript, hides it behind a paywall or lists it as a table will not be read
well, and the page says to paste the text instead (`errors.PASTE_HINT`).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

from .model import Block

MIN_ARTICLE_CHARS = 80  # an <article> or <main> with less paragraph text than this is not taken for the page's article
MAX_NODES = 300_000  # a page with more elements than this is refused rather than parsed
MAX_DEPTH = 500

VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
# Elements whose content is never text to read.
SKIP = frozenset({"script", "style", "noscript", "template", "svg", "math", "iframe", "object", "canvas", "select", "option", "button", "textarea",
                  "input", "head", "audio", "video", "dialog", "figcaption"})
# Left out of a web page's article (not an EPUB): page furniture.
FURNITURE = frozenset({"nav", "aside", "footer"})
BLOCKS = frozenset({"p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dd", "dt", "blockquote", "pre", "table", "tr", "td", "th",
                    "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "aside", "nav", "figure", "form", "fieldset", "address", "hr", "body", "details",
                    "summary", "tbody", "thead", "tfoot", "caption"})
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# A start tag closes an open element of these kinds first (HTML's own rule for tags whose end is optional).
CLOSES = {"p": {"p"}, "li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "tr": {"tr", "td", "th"}, "td": {"td", "th"}, "th": {"td", "th"},
          "option": {"option"}, "h1": {"p"}, "h2": {"p"}, "h3": {"p"}, "h4": {"p"}, "h5": {"p"}, "h6": {"p"}, "ul": {"p"}, "ol": {"p"}, "div": {"p"},
          "table": {"p"}, "blockquote": {"p"}, "pre": {"p"}}
_BOILERPLATE = re.compile(r"(?:^|[\s_-])(comments?|sharing|share|shares|social|related|sidebar|cookies?|banner|newsletter|subscribe|advert|adverts|ads?|promo|"
                          r"breadcrumbs?|menu|navbar|nav|footer|popup|modal|toc|skip-link|paywall|signup|sign-up|login|recommended|trending)(?:$|[\s_-])", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_FOOTNOTE_MARK = re.compile(r"\[?\d{1,3}\]?")


class Node:
    """An element of the page: its tag, its attributes, and its children (elements, and text as plain strings)."""

    __slots__ = ("tag", "attrs", "children", "parent", "depth")

    def __init__(self, tag: str, attrs: dict[str, str], parent: Optional["Node"]):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent
        self.depth = parent.depth + 1 if parent is not None else 0

    def text(self) -> str:
        """All the text under this element, white space collapsed. Elements that are never read (scripts and so on) contribute nothing."""
        parts: list[str] = []

        def walk(node: "Node") -> None:
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag == "sup" and _FOOTNOTE_MARK.fullmatch(child.text()):
                    continue  # a footnote number: for the eye, not the ear
                elif child.tag not in SKIP:
                    if child.tag in BLOCKS or child.tag == "br":
                        parts.append(" ")
                    walk(child)
                    if child.tag in BLOCKS:
                        parts.append(" ")

        walk(self)
        return _SPACE.sub(" ", "".join(parts)).strip()


class _TreeBuilder(HTMLParser):
    """Builds a `Node` tree from HTML as it comes, tolerating the unclosed and mis-nested tags that real pages have."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.current = self.root
        self.count = 0
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.count += 1
        if self.count > MAX_NODES:
            raise ValueError("too many elements")
        if tag == "title":
            self._in_title = True
        closes = CLOSES.get(tag)
        if closes and self.current.tag in closes:
            self._close(self.current.tag)
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            if node.depth > MAX_DEPTH:
                raise ValueError("elements nested too deeply")
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self._close(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        # close the nearest open element with this tag; an end tag with no open element of its kind is ignored
        node: Optional[Node] = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def _close(self, tag: str) -> None:
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        self.current.children.append(data)


def parse(html: str) -> tuple[Node, str]:
    """The page's tree and its `<title>`. Raises ValueError for a page that is too big or too tangled to be a real document."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root, _SPACE.sub(" ", "".join(builder.title_parts)).strip()


def _find_all(node: Node, tags: frozenset) -> list[Node]:
    found: list[Node] = []
    stack = [node]
    while stack:
        current = stack.pop()
        for child in current.children:
            if isinstance(child, Node):
                if child.tag in tags:
                    found.append(child)
                stack.append(child)
    return found


def _paragraph_text(node: Node) -> int:
    """The characters in the paragraphs under `node` (the measure of where the article is)."""
    return sum(len(p.text()) for p in _find_all(node, frozenset({"p"})))


def _deepest_holding(node: Node, total: int, threshold: float = 0.7) -> Node:
    """From `node`, go down into the one child that still holds at least `threshold` of `total` paragraph text, as far as that goes."""
    while True:
        best = None
        for child in node.children:
            if isinstance(child, Node) and child.tag not in SKIP and _paragraph_text(child) >= threshold * total:
                best = child
                break
        if best is None:
            return node
        node = best


def find_article(root: Node) -> Node:
    """Where the article is on a page (see the module's description)."""
    articles = [a for a in _find_all(root, frozenset({"article"})) if _paragraph_text(a) >= MIN_ARTICLE_CHARS]
    if articles:
        return max(articles, key=_paragraph_text)
    mains = [m for m in _find_all(root, frozenset({"main"})) if _paragraph_text(m) >= MIN_ARTICLE_CHARS]
    mains += [n for n in _find_all(root, frozenset({"div", "section"})) if n.attrs.get("role") == "main" and _paragraph_text(n) >= MIN_ARTICLE_CHARS]
    if mains:
        return max(mains, key=_paragraph_text)
    total = _paragraph_text(root)
    if total < MIN_ARTICLE_CHARS:
        return root
    return _deepest_holding(root, total)


def _is_furniture(node: Node) -> bool:
    if node.tag in FURNITURE:
        return True
    if "hidden" in node.attrs or node.attrs.get("aria-hidden", "").lower() == "true":
        return True
    label = f"{node.attrs.get('class', '')} {node.attrs.get('id', '')}"
    return bool(label.strip()) and bool(_BOILERPLATE.search(label))


def blocks_of(container: Node, article: bool) -> list[Block]:
    """The blocks of text under `container`, in document order. Headings, paragraphs, list items and quotations become text blocks, `<pre>` becomes a
    code block, a `<table>` a table block, and loose text between blocks is gathered into paragraphs."""
    blocks: list[Block] = []
    buffer: list[str] = []

    def flush() -> None:
        text = _SPACE.sub(" ", "".join(buffer)).strip()
        buffer.clear()
        if text:
            blocks.append(Block("paragraph", text))

    def walk(node: Node) -> None:
        for child in node.children:
            if isinstance(child, str):
                buffer.append(child)
                continue
            tag = child.tag
            if tag in SKIP or (article and _is_furniture(child)):
                continue
            if tag == "br":
                buffer.append(" ")
            elif tag == "sup" and _FOOTNOTE_MARK.fullmatch(child.text()):
                continue  # a footnote number between inline words (the same rule is in Node.text for the text of a paragraph)
            elif tag in HEADINGS:
                flush()
                text = child.text()
                if text:
                    blocks.append(Block("heading", text, HEADINGS[tag]))
            elif tag == "pre":
                flush()
                text = "".join(c if isinstance(c, str) else c.text() for c in child.children).strip()
                if text:
                    blocks.append(Block("code", text))
            elif tag == "table":
                flush()
                rows = []
                for row in _find_all(child, frozenset({"tr"})):
                    cells = [c.text() for c in row.children if isinstance(c, Node) and c.tag in ("td", "th")]
                    if any(cells):
                        rows.append(", ".join(cells))
                if rows:
                    blocks.append(Block("table", "\n".join(rows)))
            elif tag in ("p", "li", "dd", "dt", "blockquote") and not any(isinstance(c, Node) and c.tag in ("p", "ul", "ol", "div", "blockquote") for c in child.children):
                flush()
                text = child.text()
                if text:
                    blocks.append(Block("paragraph", text))
            elif tag in BLOCKS:
                flush()
                walk(child)
                flush()
            else:  # an inline element (a link, emphasis, a span): its text joins the paragraph being gathered
                walk(child)

    walk(container)
    flush()
    return blocks


_SITE_SEPARATOR = re.compile(r"\s+[|–—·:-]\s+")


def page_title(title: str, blocks: list[Block]) -> Optional[str]:
    """A title for the page. A page's `<title>` is usually "Headline | Site name", so the part of it that matches the page's first heading is the headline;
    failing that, the first part (headlines come before the site's name), unless it is very short; failing a `<title>`, the first heading itself."""
    heading = next((b.text for b in blocks if b.kind == "heading"), None)
    if not title:
        return heading
    parts = [part.strip() for part in _SITE_SEPARATOR.split(title) if part.strip()]
    if not parts:
        return heading
    if heading:
        for part in parts:
            if part.lower() == heading.lower():
                return part
    return parts[0] if len(parts[0]) >= 10 or len(parts) == 1 else max(parts, key=len)


def extract_html(html: str, article: bool) -> tuple[list[Block], Optional[str]]:
    """The blocks and the title of an HTML document. `article` selects a web page's treatment (find the article, leave the furniture); an EPUB's chapter is
    read whole. Raises ValueError for a document that is too big or too tangled."""
    root, title = parse(html)
    if article:
        container = find_article(root)
    else:
        bodies = _find_all(root, frozenset({"body"}))
        container = bodies[0] if bodies else root
    blocks = blocks_of(container, article)
    return blocks, page_title(title, blocks)
