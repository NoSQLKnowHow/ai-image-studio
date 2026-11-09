"""The readers for each kind of source (DESIGN.md §38.5 item 1, criteria 268, 269 and 273).

Each format is tested with a file made for the purpose (`speech_files.py`), so what the file holds is written next to what is expected of it. The hostile
cases (an archive that unpacks to gigabytes, an XML entity bomb, a page nested a thousand deep) are here too: the reader must refuse them with a reason.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from speech_files import make_docx, make_epub, make_pdf, many_entries, zip_bomb

from studio.speech.text import extract_html as H
from studio.speech.text.errors import PrepareError
from studio.speech.text.extract_docx import extract_docx
from studio.speech.text.extract_epub import extract_epub
from studio.speech.text.extract_pdf import extract_pdf
from studio.speech.text.extract_text import decode_text, extract_markdown, extract_plain
from studio.speech.text.prepare import decode_html
from studio.speech.text.safe_zip import MAX_ENTRIES, open_zip, parse_xml, read_member

LONG = "A paragraph with enough words in it to count as real content for the article finder, and then some more words after that."


def kinds(blocks):
    return [(b.kind, b.text) for b in blocks]


def write(tmp_path, name: str, data: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


# ---------------------------------------------------------------- plain text and Markdown
def test_bytes_become_text_by_the_encoding_they_are_in():
    assert decode_text("café".encode("utf-8")) == "café"
    assert decode_text(b"\xef\xbb\xbfcaf\xc3\xa9") == "café"  # a UTF-8 byte order mark
    assert decode_text("café".encode("cp1252")) == "café"  # an old Windows text file
    assert decode_text("hi".encode("utf-16")) == "hi"
    with pytest.raises(PrepareError) as binary:
        decode_text(b"PK\x03\x04\x00\x00\x00binary")
    assert binary.value.code == "bad_encoding"


def test_a_plain_text_book_is_cut_at_the_lines_that_name_a_chapter():
    text = ("CHAPTER I.\n\nIn the beginning there was a long sentence\nwrapped over two lines.\n\nCHAPTER II.\n\nThen came another paragraph.\n\n"
            "Part of the reason we left was the weather.\n")
    extracted = extract_plain(text)
    assert [c.title for c in extracted.chapters] == ["CHAPTER I.", "CHAPTER II."]
    assert kinds(extracted.chapters[0].blocks) == [("heading", "CHAPTER I."), ("paragraph", "In the beginning there was a long sentence wrapped over two lines.")]
    assert kinds(extracted.chapters[1].blocks)[-1] == ("paragraph", "Part of the reason we left was the weather.")  # a sentence is not a heading


@pytest.mark.parametrize("line, heading", [("Chapter 3: The Return", True), ("PROLOGUE", True), ("Part Two", True), ("Act I. Scene II.", True),
                                           ("Part of the reason we left was the weather.", False), ("Chapter and verse, as they say, is what you need.", False),
                                           ("A chapter of accidents", False)])
def test_what_counts_as_the_name_of_a_division(line, heading):
    assert (extract_plain(line + "\n\nSome text.").chapters[0].blocks[0].kind == "heading") is heading


def test_one_or_no_chapter_heading_leaves_the_text_whole():
    assert len(extract_plain("Chapter 1\n\nOnly one heading and then text.").chapters) == 1
    assert len(extract_plain("Just text.\n\nMore text.").chapters) == 1


def test_markdown_structure_is_used():
    text = ("# Book Title\n\nIntro line one\ncontinues here.\n\n## First\n\n- item one\n- item two\n  continued\n1. numbered\n\n> a quotation\n> over two lines\n\n"
            "```python\nprint('hi')\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n---\n\n## Second\n\nText of the second.\n\nSetext sub\n---\n\nAfter it.\n")
    extracted = extract_markdown(text)
    # one level-1 heading (the title) and several level-2 ones: the chapters are cut at the level-2 headings, and what comes before the first is its own
    assert [c.title for c in extracted.chapters] == [None, "First", "Second", "Setext sub"]
    first = next(c for c in extracted.chapters if c.title == "First")
    assert kinds(first.blocks) == [("heading", "First"), ("paragraph", "item one"), ("paragraph", "item two continued"), ("paragraph", "numbered"),
                                   ("paragraph", "a quotation over two lines"), ("code", "print('hi')"), ("table", "a, b\n1, 2")]
    assert extracted.title == "Book Title"  # the one level-1 heading is the document's title
    everything = [b for c in extracted.chapters for b in c.blocks]
    assert extracted.chapters[3].blocks[0].level == 2 and kinds(extracted.chapters[3].blocks) == [("heading", "Setext sub"), ("paragraph", "After it.")]
    assert ("paragraph", "Intro line one continues here.") in kinds(everything)


def test_markdown_with_two_level_ones_is_cut_there_and_an_unfinished_fence_runs_to_the_end():
    extracted = extract_markdown("# One\n\ntext\n\n# Two\n\n```\nnever closed\nstill code")
    assert [c.title for c in extracted.chapters] == ["One", "Two"] and extracted.title is None
    assert extracted.chapters[1].blocks[-1] == type(extracted.chapters[1].blocks[-1])("code", "never closed\nstill code")


# ---------------------------------------------------------------- web pages (criterion 268)
PAGE = f"""<html><head><title>Great Story | The Daily Site</title><script>var x = 1;</script><style>p {{ color: red }}</style></head><body>
<nav><a href=/>Home</a><a href=/news>News</a></nav><div class="header">Site header</div>
<article><h1>Great Story</h1><p>{LONG}
<p>Second paragraph, with a <a href="/x">link text</a> and a footnote<sup>1</sup> mark &amp; an entity &eacute;.
<ul><li>Item one</li><li>Item two</li></ul><pre>code here</pre><table><tr><td>a</td><td>b</td></tr></table>
<div class="share-buttons">Share on Twitter</div><div id="comments">Comment by someone</div>
<img src=x.png alt="a picture"><br>Loose text after a break.</article><footer>Copyright notice</footer></body></html>"""


def test_the_article_of_a_web_page_is_read_and_the_furniture_is_left_behind():
    blocks, title = H.extract_html(PAGE, article=True)
    assert title == "Great Story"  # the part of "Headline | Site name" that matches the page's first heading
    assert kinds(blocks) == [("heading", "Great Story"), ("paragraph", LONG), ("paragraph", "Second paragraph, with a link text and a footnote mark & an entity é."),
                             ("paragraph", "Item one"), ("paragraph", "Item two"), ("code", "code here"), ("table", "a, b"),
                             ("paragraph", "Loose text after a break.")]
    text = " ".join(b.text for b in blocks)
    for left_out in ("Home", "Site header", "Share on Twitter", "Comment by someone", "Copyright notice", "var x", "color: red"):
        assert left_out not in text


def test_without_an_article_element_main_and_then_the_densest_container_are_used():
    main = f"<body><div>menu words</div><main><p>{LONG}</p><p>{LONG}</p></main><div>other</div></body>"
    assert [b.text for b in H.extract_html(main, article=True)[0]] == [LONG, LONG]
    soup = (f"<body><div id=top><div class=menu><a>one</a><a>two</a></div><div class=content><p>{LONG}</p><p>{LONG}</p><p>{LONG}</p></div>"
            f"<div class=sidebar><p>short aside</p></div></div></body>")
    blocks, _ = H.extract_html(soup, article=True)
    assert [b.text for b in blocks] == [LONG, LONG, LONG]  # the container that holds most of the paragraph text
    assert [b.text for b in H.extract_html("<body><p>Tiny.</p></body>", article=True)[0]] == ["Tiny."]  # too little to find an article in: all of it


def test_untidy_html_is_read_anyway():
    blocks, _ = H.extract_html("<p>One<p>Two<ul><li>three<li>four</ul><b><i>bold</b> text</i><div>loose</div></body></html>", article=False)
    assert [b.text for b in blocks] == ["One", "Two", "three", "four", "bold text", "loose"]


def test_an_epub_chapter_is_read_whole_with_nothing_treated_as_furniture():
    blocks, _ = H.extract_html("<body><nav>Contents</nav><p>Real text.</p><aside>An aside</aside></body>", article=False)
    assert [b.text for b in blocks] == ["Contents", "Real text.", "An aside"]


def test_page_titles():
    assert H.page_title("Headline of the day | Site", []) == "Headline of the day"
    assert H.page_title("Site | Real headline here", [H.Block("heading", "Real headline here", 1)]) == "Real headline here"
    assert H.page_title("", [H.Block("heading", "From the heading", 1)]) == "From the heading"
    assert H.page_title("A — B", []) == "A — B" or H.page_title("A — B", []) in ("A", "B")  # two short parts: either is fine, it must not be empty
    assert H.page_title("", []) is None


def test_a_page_that_is_too_tangled_is_refused_not_parsed(monkeypatch):
    with pytest.raises(ValueError):
        H.extract_html("<div>" * (H.MAX_DEPTH + 5), article=True)
    monkeypatch.setattr(H, "MAX_NODES", 50)
    with pytest.raises(ValueError):
        H.extract_html("<p>x</p>" * 60, article=True)


def test_html_bytes_are_decoded_by_the_charset_the_server_or_the_page_names():
    latin = "<html><body><p>café</p></body></html>".encode("latin-1")
    assert "café" in decode_html(latin, "iso-8859-1")
    assert "café" in decode_html(b'<meta charset="windows-1252"><p>caf\xe9</p>', None)
    assert "café" in decode_html("<p>café</p>".encode("utf-8"), "no-such-charset")  # an unknown name falls through to UTF-8
    assert "�" not in decode_html(b"<p>caf\xe9</p>", None)  # not UTF-8 at all: Windows-1252, never a replacement character


# ---------------------------------------------------------------- EPUB (criteria 268, 269)
BODY = f"<h2>Heading in the text</h2><p>{LONG}</p><p>{LONG}</p>"


def test_an_epub_gives_a_chapter_for_each_file_in_reading_order_with_the_titles_of_its_contents(tmp_path):
    data = make_epub([("ch1.xhtml", "The Start", BODY), ("ch2.xhtml", "The Middle", BODY)], title="My Book")
    extracted = extract_epub(write(tmp_path, "a.epub", data))
    assert extracted.title == "My Book" and [c.title for c in extracted.chapters] == ["The Start", "The Middle"] and all(c.include for c in extracted.chapters)
    assert extracted.chapters[0].blocks[0] == type(extracted.chapters[0].blocks[0])("heading", "The Start", 1)  # the title is spoken though the text does not begin with it
    assert ("heading", "Heading in the text") in kinds(extracted.chapters[0].blocks)


def test_an_epub_2_contents_file_gives_the_titles_too(tmp_path):
    extracted = extract_epub(write(tmp_path, "b.epub", make_epub([("a.xhtml", "From The NCX", BODY)], toc="ncx")))
    assert [c.title for c in extracted.chapters] == ["From The NCX"]


def test_without_any_contents_the_first_heading_names_the_chapter(tmp_path):
    extracted = extract_epub(write(tmp_path, "c.epub", make_epub([("a.xhtml", None, "<h1>Its Own Heading</h1><p>" + LONG + "</p>")], toc="none")))
    assert [c.title for c in extracted.chapters] == ["Its Own Heading"]
    plain = extract_epub(write(tmp_path, "d.epub", make_epub([("a.xhtml", None, f"<p>{LONG}</p>"), ("b.xhtml", None, f"<p>{LONG}</p>")], toc="none")))
    assert [c.title for c in plain.chapters] == ["Section 1", "Section 2"]


def test_front_and_back_matter_and_nearly_empty_chapters_start_out_left_out(tmp_path):
    data = make_epub([("cover.xhtml", "Cover", "<p>Cover</p>"), ("copy.xhtml", "Copyright", f"<p>{LONG}</p>"), ("one.xhtml", "Chapter 1", BODY),
                      ("blank.xhtml", "A Divider", "<p>* * *</p>"), ("notes.xhtml", "Notes", f"<p>{LONG}</p>")])
    chapters = extract_epub(write(tmp_path, "e.epub", data)).chapters
    assert [(c.title, c.include) for c in chapters] == [("Cover", False), ("Copyright", False), ("Chapter 1", True), ("A Divider", False), ("Notes", False)]
    assert chapters[0].note == "Front or back matter" and chapters[3].note == "Almost no text"


def test_a_chapter_missing_from_the_archive_is_a_warning_not_a_failure(tmp_path):
    data = make_epub([("a.xhtml", "One", BODY), ("gone.xhtml", "Two", BODY)], missing=["gone.xhtml"])
    extracted = extract_epub(write(tmp_path, "f.epub", data))
    assert [c.title for c in extracted.chapters] == ["One"] and any("missing" in w for w in extracted.warnings)


def test_a_file_that_is_not_an_epub_or_has_nothing_to_read_is_refused_in_words(tmp_path):
    with pytest.raises(PrepareError) as not_zip:
        extract_epub(write(tmp_path, "g.epub", b"this is not a zip file"))
    assert not_zip.value.code == "extract_failed"
    plain_zip = io.BytesIO()
    with zipfile.ZipFile(plain_zip, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(PrepareError) as no_container:
        extract_epub(write(tmp_path, "h.epub", plain_zip.getvalue()))
    assert "not an EPUB" in no_container.value.message
    with pytest.raises(PrepareError) as nothing:
        extract_epub(write(tmp_path, "i.epub", make_epub([("c.xhtml", "Cover", "<p>Cover</p>")])))
    assert nothing.value.code == "no_text"


# ---------------------------------------------------------------- Word (criteria 268, 269)
def test_a_word_file_gives_paragraphs_headings_and_a_table_and_cuts_at_heading_1(tmp_path):
    data = make_docx([("Title", "The Report"), ("Heading1", "Introduction"), "First paragraph.", ("Heading2", "A detail"), "Second paragraph.", ("Heading1", "Results"),
                      "Third paragraph."], table=[["a", "b"], ["c", "d"]])
    extracted = extract_docx(write(tmp_path, "a.docx", data))
    assert [c.title for c in extracted.chapters] == ["The Report", "Introduction", "Results"] or [c.title for c in extracted.chapters][-2:] == ["Introduction", "Results"]
    everything = [b for c in extracted.chapters for b in c.blocks]
    assert ("heading", "A detail") in kinds(everything) and ("table", "a, b\nc, d") in kinds(everything)
    assert next(b for b in everything if b.text == "A detail").level == 2


def test_word_text_in_a_content_control_is_read_and_deleted_text_is_not(tmp_path):
    data = make_docx(["Visible words.", "More words."], wrap_in_control=True, with_deleted=True, title="From the properties")
    extracted = extract_docx(write(tmp_path, "b.docx", data))
    text = " ".join(b.text for c in extracted.chapters for b in c.blocks)
    assert "Visible words." in text and "More words." in text and "DELETED" not in text and extracted.title == "From the properties"


def test_word_headings_are_found_without_a_styles_part_by_their_id(tmp_path):
    data = make_docx([("Heading1", "One"), "a", ("Heading1", "Two"), "b"], styles=False)
    assert [c.title for c in extract_docx(write(tmp_path, "c.docx", data)).chapters] == ["One", "Two"]


def test_a_word_file_with_no_body_or_no_text_is_refused(tmp_path):
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("other.xml", "<x/>")
    with pytest.raises(PrepareError):
        extract_docx(write(tmp_path, "d.docx", empty.getvalue()))
    with pytest.raises(PrepareError) as none:
        extract_docx(write(tmp_path, "e.docx", make_docx([])))
    assert none.value.code == "no_text"


# ---------------------------------------------------------------- PDF (criteria 268, 273)
PARAGRAPHS = ["The first paragraph begins here and runs on across a full line of", "text before it ends. It continues for another line of text to be",
              "wrapped. The last line is short.", "A second paragraph starts here and also wraps around to the next", "line and finishes."]


def test_a_pdf_gives_its_paragraphs_with_wrapped_lines_joined(tmp_path):
    extracted = extract_pdf(write(tmp_path, "a.pdf", make_pdf([PARAGRAPHS], title="A PDF")))
    assert extracted.title == "A PDF" and len(extracted.chapters) == 1
    assert [b.text for b in extracted.chapters[0].blocks] == [
        "The first paragraph begins here and runs on across a full line of text before it ends. It continues for another line of text to be wrapped. "
        "The last line is short.", "A second paragraph starts here and also wraps around to the next line and finishes."]


def test_running_titles_and_page_numbers_are_dropped_from_a_pdf_of_several_pages(tmp_path):
    # each page has words of its own (a real page does), a running title at the top and a page number at the bottom
    pages = [["Running Title", *[f"{line} {word}" for line in PARAGRAPHS], str(n + 1)] for n, word in enumerate(["alpha", "bravo", "charlie", "delta", "echo"])]
    blocks = extract_pdf(write(tmp_path, "b.pdf", make_pdf(pages))).chapters[0].blocks
    text = " ".join(b.text for b in blocks)
    assert "Running Title" not in text and not any(b.text.strip().isdigit() for b in blocks) and text.count("The first paragraph begins") == 5
    assert all(word in text for word in ("alpha", "bravo", "charlie", "delta", "echo"))  # nothing but the furniture went


def test_the_bookmarks_of_a_pdf_are_its_chapters(tmp_path):
    pages = [[f"The text of page {word} goes here and is long enough to be a line", "and a second line that finishes the sentence."] for word in ("one", "two", "three", "four")]
    extracted = extract_pdf(write(tmp_path, "c.pdf", make_pdf(pages, outline=[("One", 0), ("Two", 2)])))
    assert [c.title for c in extracted.chapters] == ["One", "Two"]
    first, second = " ".join(b.text for b in extracted.chapters[0].blocks), " ".join(b.text for b in extracted.chapters[1].blocks)
    assert "page one" in first and "page two" in first and "page three" in second and "page four" in second and "page three" not in first
    assert extracted.chapters[0].blocks[0].kind == "heading" and extracted.chapters[1].blocks[0].text == "Two"


def test_a_pdf_with_no_text_layer_is_refused_with_that_reason(tmp_path):
    with pytest.raises(PrepareError) as scan:
        extract_pdf(write(tmp_path, "d.pdf", make_pdf([[], []])))
    assert scan.value.code == "no_text_layer" and "scan" in scan.value.message and "OCR" in scan.value.hint


def test_a_file_that_is_not_a_pdf_is_refused(tmp_path):
    with pytest.raises(PrepareError) as junk:
        extract_pdf(write(tmp_path, "e.pdf", b"%PDF-1.4 this is not a real document at all"))
    assert junk.value.code == "extract_failed"


def test_blank_pages_in_a_pdf_are_reported(tmp_path):
    pages = [PARAGRAPHS] + [[] for _ in range(3)]
    assert any("have no text" in w for w in extract_pdf(write(tmp_path, "f.pdf", make_pdf(pages))).warnings)


# ---------------------------------------------------------------- hostile archives and XML (criterion 273)
def test_an_archive_that_would_unpack_to_gigabytes_is_refused_before_anything_is_read(tmp_path):
    with pytest.raises(PrepareError) as bomb:
        open_zip(write(tmp_path, "bomb.zip", zip_bomb(70)))
    assert bomb.value.code == "too_large"
    with pytest.raises(PrepareError) as crowd:
        open_zip(write(tmp_path, "crowd.zip", many_entries(MAX_ENTRIES + 1)))
    assert crowd.value.code == "too_large" and "parts" in crowd.value.message


def test_a_member_is_read_up_to_its_limit_whatever_the_archive_claims(tmp_path):
    zf = open_zip(write(tmp_path, "ok.zip", make_docx(["x"])))
    assert read_member(zf, "word/document.xml") is not None and read_member(zf, "not/there") is None
    with pytest.raises(PrepareError):
        read_member(zf, "word/document.xml", limit=10)


def test_xml_that_defines_entities_is_refused_and_an_ordinary_doctype_is_not(tmp_path):
    with pytest.raises(PrepareError):
        parse_xml(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xxxx">]><a>&x;&x;</a>')
    assert parse_xml(b'<?xml version="1.0"?><!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://example.invalid/x.dtd"><ncx/>').tag == "ncx"
    with pytest.raises(PrepareError):
        parse_xml(b"<a><b></a>")
    with pytest.raises(PrepareError):
        extract_epub(write(tmp_path, "evil.epub", make_epub([("a.xhtml", "One", BODY)], entity_bomb=True)))


def test_a_bomb_inside_a_word_file_or_an_epub_is_refused(tmp_path):
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", bytes(70 * 1024 * 1024))
    with pytest.raises(PrepareError) as caught:
        extract_docx(write(tmp_path, "bomb.docx", bomb.getvalue()))
    assert caught.value.code == "too_large"


def test_a_document_whose_pages_all_look_alike_does_not_lose_its_text_to_the_furniture_rule(tmp_path):
    """Lines that repeat on most pages are furniture only if they are a small part of the document; if nearly all of it repeats it is not furniture."""
    pages = [[f"The same line on every page number {n} of a document that repeats", "and another line that is also the same on all of them."] for n in range(4)]
    text = " ".join(b.text for c in extract_pdf(write(tmp_path, "same.pdf", make_pdf(pages))).chapters for b in c.blocks)
    assert "The same line on every page" in text


def test_text_that_is_really_markdown_is_read_as_markdown_so_that_nothing_says_hash():
    """People paste Markdown from chat programs and editors. Read as plain text, "# A Title" would be spoken as "hash A Title"."""
    extracted = extract_plain("# A Title\n\nSome words here.\n\n## Part\n\n- one\n- two\n")
    everything = [b for c in extracted.chapters for b in c.blocks]
    assert ("heading", "A Title") in kinds(everything) and ("paragraph", "one") in kinds(everything) and not any("#" in b.text for b in everything)
    fenced = extract_plain("Intro line.\n\n```\ncode\n```\n")
    assert ("code", "code") in kinds([b for c in fenced.chapters for b in c.blocks])
    table = extract_plain("Before.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert ("table", "a, b\n1, 2") in kinds([b for c in table.chapters for b in c.blocks])
    plain = extract_plain("A # in the middle of a line, and\n\n- a dash that starts a plain line\n")
    assert kinds([b for c in plain.chapters for b in c.blocks]) == [("paragraph", "A # in the middle of a line, and"), ("paragraph", "- a dash that starts a plain line")]
