"""The rules that make text speakable and divide it (DESIGN.md §38.5, criteria 269 to 271).

What these protect: that markup, addresses and footnote marks never reach an engine; that the sentence splitter does not break at `Dr.` or `3.14` and
does break at the end of a sentence in a quotation; that pieces never exceed an engine's allowance and never lose or repeat a word; and that a book is
divided into parts a person would divide it into. All of it is plain text in, plain text out, so no server or model is needed.
"""

from __future__ import annotations

import re

import pytest

from studio.speech.text.clean import (Report, clean_text, collapse_spaces, drop_citation_marks, is_page_number, join_wrapped_lines, normalize,
                                      replace_addresses, split_paragraphs, strip_markup)
from studio.speech.text.model import Block, Chapter, Paragraph
from studio.speech.text.parts import MERGE_BELOW, draft_parts, split_by_size, title_from
from studio.speech.text.pieces import make_pieces
from studio.speech.text.sentences import cut_long, pack, split_sentences


def words(text: str) -> list[str]:
    return text.split()


# ---------------------------------------------------------------- cleaning (criterion 271)
def test_invisible_characters_and_odd_spaces_go_and_line_breaks_are_unified():
    report = Report()
    text = normalize("soft­hyphen zero​width bom﻿ line one\r\nline two\rline three\x07 end", report)
    assert text == "softhyphen zerowidth bom line one\nline two\nline three end"  # the NBSP is turned into a space by collapse_spaces
    assert report.invisible == 4  # soft hyphen, zero-width space, BOM, bell
    assert collapse_spaces("a  b\t\tc\n d") == "a b c d"


def test_a_joiner_that_matters_in_some_scripts_is_left_alone():
    assert normalize("क‍ष") == "क‍ष"  # zero-width joiner: it changes the letter, so it stays


@pytest.mark.parametrize("raw, expected", [
    ("This is **bold** and __also bold__.", "This is bold and also bold."),
    ("This is *italic* and _italic_ text.", "This is italic and italic text."),
    ("A [link to a page](https://example.com/page) here.", "A link to a page here."),
    ("A [link](https://en.wikipedia.org/wiki/Foo_(bar) \"title\") with parens.", "A link with parens."),
    ("An image ![alt text](pic.png) is gone.", "An image  is gone."),
    ("Use `print(x)` to show it.", "Use print(x) to show it."),
    ("Some <b>HTML</b> and <br/> tags <!-- and a comment --> go.", "Some  HTML  and   tags  go."),
    ("A reference [text][1] link and a note[^2] mark.", "A reference text link and a note mark."),
    ("Struck ~~out~~ words.", "Struck out words."),
])
def test_markup_is_removed_and_a_link_keeps_its_words(raw, expected):
    assert strip_markup(raw) == expected


@pytest.mark.parametrize("untouched", ["Variables like snake_case_name stay.", "2 * 3 * 4 is 24.", "A lone * star and a lone _ bar.", "5 < 6 and 7 > 3."])
def test_things_that_only_look_like_markup_are_left_alone(untouched):
    assert strip_markup(untouched) == untouched


def test_a_web_address_becomes_the_word_link_and_the_sentence_punctuation_stays():
    report = Report()
    assert replace_addresses("See https://example.com/a?b=1&c=2. Or (www.example.org), or <https://x.org>!", report) == \
        "See link. Or (link), or <link>!"  # the brackets stay (strip_markup turns an address in angle brackets into a link first, in clean_text)
    assert report.links == 3
    assert clean_text("Go to <https://x.org/a> now.") == "Go to link now."


def test_footnote_and_citation_marks_go():
    report = Report()
    assert drop_citation_marks("A claim.[12] Another[3, 4] one[5-7] and word¹² end.", report) == "A claim. Another one and word end."
    assert report.citations == 4
    assert drop_citation_marks("An array a[i] and the year [1999a] stay.") == "An array a[i] and the year [1999a] stay."


def test_page_numbers_are_recognised_but_numbers_in_text_are_not():
    for line in ("12", " 7 ", "Page 12", "page 3 of 40", "12 / 40", "- 12 -", "— 5 —"):
        assert is_page_number(line), line
    for line in ("In 1999 it happened", "Chapter 12", "3 apples", "12345", ""):
        assert not is_page_number(line), line


def test_clean_text_applies_every_rule_and_reports_them():
    report = Report()
    out = clean_text("A **very** [good](http://a.b/c) book[1], see https://d.e/f.­", report)
    assert out == "A very good book, see link."
    assert (report.markup, report.links, report.citations, report.invisible) == (2, 1, 1, 1)  # bold, a link's wrapper; one address; one mark; one soft hyphen
    assert any("web address" in line for line in report.lines()) and Report().lines() == []


def test_the_report_speaks_in_singular_and_plural():
    assert Report(links=1).lines() == ['Replaced 1 web address with the word "link".']
    assert Report(links=2).lines() == ['Replaced 2 web addresses with the word "link".']
    assert "1 code block." in Report(code_blocks=1).lines()[0] and "3 tables." in Report(tables=3).lines()[0]


def test_hard_wrapped_lines_are_joined_and_cut_words_are_made_whole():
    assert join_wrapped_lines(["This sentence was wrapped", "in the middle by an editor", "and ends here."]) == \
        ["This sentence was wrapped in the middle by an editor and ends here."]
    assert join_wrapped_lines(["An exam-", "ple of a word", "cut by a hyphen."]) == ["An example of a word cut by a hyphen."]
    assert join_wrapped_lines(["A line that is long enough.", "Another line, also complete.", "And a third one."]) == \
        ["A line that is long enough.", "Another line, also complete.", "And a third one."]  # never wrapped: one paragraph per line stays
    assert join_wrapped_lines(["Alone"]) == ["Alone"] and join_wrapped_lines([]) == [] and join_wrapped_lines(["", "  "]) == []


def test_paragraphs_are_split_on_blank_lines_and_page_numbers_dropped():
    report = Report()
    text = "First paragraph that\nwraps over two lines.\n\n\n\nSecond paragraph.\n\n12\n\nThird, with a   gap."
    assert split_paragraphs(text, report) == ["First paragraph that wraps over two lines.", "Second paragraph.", "Third, with a   gap."]
    assert report.page_numbers == 1
    assert split_paragraphs("") == [] and split_paragraphs("\n\n\n") == []


# ---------------------------------------------------------------- sentences (criterion 270)
@pytest.mark.parametrize("text, expected", [
    ("Hello world. This is a test.", ["Hello world.", "This is a test."]),
    ("Dr. Smith went to St. Louis. He arrived at 5 p.m. The meeting began.",
     ["Dr. Smith went to St. Louis.", "He arrived at 5 p.m.", "The meeting began."]),
    ('She said, "Stop!" he replied.', ['She said, "Stop!" he replied.']),
    ('He left. "Why?" she asked. "Because." Then silence.', ["He left.", '"Why?" she asked.', '"Because."', "Then silence."]),
    ("He said, “I agree.” Then left.", ["He said, “I agree.”", "Then left."]),
    ("The value is 3.14 and the site is example.com today. Next.", ["The value is 3.14 and the site is example.com today.", "Next."]),
    ("J. R. R. Tolkien wrote it. Yes.", ["J. R. R. Tolkien wrote it.", "Yes."]),
    ("Wait... What? Really?! Yes.", ["Wait...", "What?", "Really?!", "Yes."]),
    ("Wait… What.", ["Wait…", "What."]),
    ("See e.g. the first one, i.e. this. Done.", ["See e.g. the first one, i.e. this.", "Done."]),
    ("It costs No. 5 dollars. Fine.", ["It costs No. 5 dollars.", "Fine."]),
    ("He lives in the U.S. He likes it.", ["He lives in the U.S.", "He likes it."]),
    ("He lives in the U.S. capital today.", ["He lives in the U.S. capital today."]),
    ("He sat. The dog barked.", ["He sat.", "The dog barked."]),  # "sat" is not an abbreviation
    ("Meet on Jan. 5 at noon. Bring lunch.", ["Meet on Jan. 5 at noon.", "Bring lunch."]),
    ("Fruit, nuts, etc. Then dinner.", ["Fruit, nuts, etc.", "Then dinner."]),
    ("Fruit, nuts, etc. and more.", ["Fruit, nuts, etc. and more."]),
    ("你好。再见。", ["你好。", "再见。"]),
    ("no punctuation here", ["no punctuation here"]),
    ("Ends without a stop. And this one", ["Ends without a stop.", "And this one"]),
    ("", []),
    ("   ", []),
])
def test_the_sentence_splitter_on_the_cases_that_make_it_hard(text, expected):
    assert split_sentences(text) == expected
    assert "".join(split_sentences(text)).replace(" ", "") == text.replace(" ", "")  # nothing lost, nothing repeated (spaces aside: Chinese has none)


def test_a_long_sentence_is_cut_at_the_best_place_in_the_second_half_of_the_allowance():
    text = "first clause here; second clause follows, and then a third one, then a fourth clause to end it all"
    for max_chars in (30, 40, 55):
        pieces = cut_long(text, max_chars)
        assert all(0 < len(p) <= max_chars for p in pieces) and words(" ".join(pieces)) == words(text)
    # a semicolon in the second half of the allowance is preferred to a comma that comes earlier
    preferring = "alpha beta gamma delta, epsilon zeta eta; theta iota kappa lambda mu nu xi"
    assert cut_long(preferring, 45)[0] == "alpha beta gamma delta, epsilon zeta eta;"
    assert cut_long("x" * 100, 30) == ["x" * 30, "x" * 30, "x" * 30, "x" * 10]  # a word longer than the allowance is cut where it stands
    assert cut_long("short", 30) == ["short"]


def test_sentences_are_packed_as_full_as_the_allowance_lets_them_be_and_none_is_lost():
    sentences = ["One two three.", "Four five six seven.", "Eight.", "Nine ten eleven twelve thirteen fourteen fifteen."]
    chunks = pack(sentences, 40)
    assert all(len(c) <= 40 for c in chunks) and words(" ".join(chunks)) == words(" ".join(sentences))
    # the first two sentences fit together; the long one cannot be joined to anything and is cut where it stands, so there are four chunks
    assert chunks == ["One two three. Four five six seven.", "Eight.", "Nine ten eleven twelve thirteen fourteen", "fifteen."]
    big = "A very long sentence " + "with many many words " * 6 + "that cannot fit."
    assert all(len(c) <= 50 for c in pack([big, "Short."], 50)) and words(" ".join(pack([big, "Short."], 50))) == words(big + " Short.")


# ---------------------------------------------------------------- pieces (criterion 270)
def P(text: str, kind: str = "paragraph", skipped=None) -> Paragraph:
    return Paragraph(kind, text, skipped)


def test_a_paragraph_that_fits_is_one_piece_and_a_heading_is_always_alone():
    pieces = make_pieces([P("Chapter One", "heading"), P("A paragraph that is long enough to stand on its own here."),
                          P("Another paragraph that is also long enough to be alone.")], max_chars=200, min_chars=20)
    assert [(p.kind, p.text) for p in pieces] == [("heading", "Chapter One"), ("paragraph", "A paragraph that is long enough to stand on its own here."),
                                                  ("paragraph", "Another paragraph that is also long enough to be alone.")]
    assert not any(p.continues for p in pieces)


def test_a_long_paragraph_becomes_several_pieces_that_carry_on_from_each_other():
    text = " ".join(f"Sentence number {n} is here." for n in range(1, 13))
    pieces = make_pieces([P(text)], max_chars=100, min_chars=10)
    assert len(pieces) > 1 and all(len(p.text) <= 100 for p in pieces)
    assert [p.continues for p in pieces] == [False] + [True] * (len(pieces) - 1)
    assert words(" ".join(p.text for p in pieces)) == words(text)  # a split loses and repeats nothing


def test_a_short_paragraph_is_joined_to_the_next_but_never_across_a_heading():
    pieces = make_pieces([P("Short one."), P("Another short."), P("This is the longer paragraph that follows them both."),
                          P("Tiny."), P("Heading", "heading"), P("Body of the next section is long enough on its own.")], max_chars=300, min_chars=30)
    assert [p.text for p in pieces] == ["Short one. Another short. This is the longer paragraph that follows them both.", "Tiny.", "Heading",
                                        "Body of the next section is long enough on its own."]


def test_two_that_do_not_fit_together_are_spoken_apart_and_a_short_last_paragraph_stays_short():
    pieces = make_pieces([P("Short."), P("x" * 95), P("The end.")], max_chars=100, min_chars=30)
    assert [p.text for p in pieces] == ["Short.", "x" * 95, "The end."]


def test_skipped_and_empty_paragraphs_make_no_piece():
    pieces = make_pieces([P("def f(): pass", skipped="code"), P("   "), P("Real text that is long enough to be a piece by itself."),
                          P("| a | b |", skipped="table")], max_chars=100, min_chars=10)
    assert [p.text for p in pieces] == ["Real text that is long enough to be a piece by itself."]


def test_every_piece_respects_the_allowance_whatever_the_input():
    """A property over awkward inputs: no piece is longer than the allowance, none is empty, and no word is lost."""
    for max_chars in (25, 60, 120):
        paragraphs = [P("Heading text that is quite long indeed", "heading"), P("a" * 200), P("Dr. A. B. Smith, etc. said so. " * 9),
                      P("Ok."), P("Word " * 80), P("A sentence; with clauses, dashes — and more, until it is long enough to need cutting twice over.")]
        pieces = make_pieces(paragraphs, max_chars=max_chars, min_chars=10)
        assert pieces and all(0 < len(p.text) <= max_chars for p in pieces), (max_chars, [len(p.text) for p in pieces])
        assert words("".join(p.text.replace(" ", "") for p in pieces)) == words("".join(p.text.replace(" ", "") for p in paragraphs))


# ---------------------------------------------------------------- parts (criterion 269)
def chapter(title, *paragraph_texts, include=True, note=None) -> Chapter:
    return Chapter(title, [Block("heading", title, 1)] * bool(title) + [Block("paragraph", t) for t in paragraph_texts], include=include, note=note)


def paragraphs_of(ch: Chapter) -> list[Paragraph]:
    return [Paragraph(b.kind, b.text) for b in ch.blocks]


def sized(n: int, count: int = 1) -> list[str]:
    """`count` paragraphs of about `n` characters."""
    return [("Word " * (n // 5)).strip() + "." for _ in range(count)]


def test_a_text_with_chapters_gives_a_part_for_each_and_keeps_the_titles():
    parts = draft_parts([chapter("One", *sized(2000)), chapter("Two", *sized(2500)), chapter("Three", *sized(1800))], paragraphs_of, part_max_chars=30000)
    assert [p.title for p in parts] == ["One", "Two", "Three"] and all(p.include for p in parts)
    assert parts[0].paragraphs[0].kind == "heading" and parts[0].chars > 2000


def test_a_chapter_longer_than_the_limit_is_divided_evenly_at_paragraph_boundaries():
    parts = draft_parts([chapter("Seven", *sized(1000, 25))], paragraphs_of, part_max_chars=10000)  # about 25,000 characters
    assert [p.title for p in parts] == ["Seven (1)", "Seven (2)", "Seven (3)"]
    assert all(p.chars <= 10000 for p in parts) and max(p.chars for p in parts) - min(p.chars for p in parts) <= 2100  # even, to within a paragraph
    assert sum(p.chars for p in parts) == sum(len(p.text) for p in paragraphs_of(chapter("Seven", *sized(1000, 25))))


def test_a_heading_is_never_left_at_the_end_of_a_part():
    # three paragraphs fill the first half exactly, so the heading would be the last thing in it
    paragraphs = [Paragraph("paragraph", t) for t in sized(1400, 3)] + [Paragraph("heading", "Next section")] + [Paragraph("paragraph", t) for t in sized(1400, 3)]
    groups = split_by_size(paragraphs, 4500)
    assert len(groups) == 2 and groups[0][-1].kind != "heading" and groups[1][0].kind == "heading"


def test_a_tiny_chapter_is_read_with_the_one_after_it_and_a_tiny_last_one_stands_alone():
    parts = draft_parts([chapter("Dedication", "For my mother."), chapter("Chapter 1", *sized(3000)), chapter("Epilogue", "Short end.")],
                        paragraphs_of, part_max_chars=30000)
    assert [p.title for p in parts] == ["Chapter 1", "Epilogue"]
    assert parts[0].paragraphs[0].text == "Dedication" and parts[0].paragraphs[2].text.startswith("Chapter 1")  # its words are read first
    assert parts[1].chars < MERGE_BELOW


def test_a_tiny_chapter_is_not_merged_when_that_would_make_the_part_too_long():
    # "Big" is 10,003 characters with its heading, over the limit of 10,000 already, so "Tiny" cannot be put in front of it
    parts = draft_parts([chapter("Tiny", "Short."), chapter("Big", *sized(1000, 10))], paragraphs_of, part_max_chars=10000)
    assert [p.title for p in parts] == ["Tiny", "Big (1)", "Big (2)"] and parts[0].chars == len("Tiny") + len("Short.")


def test_a_tiny_chapter_is_not_merged_across_a_chapter_that_is_left_out():
    parts = draft_parts([chapter("Tiny", "Short."), chapter("Copyright", "All rights.", include=False, note="Front matter"), chapter("One", *sized(3000))],
                        paragraphs_of, part_max_chars=30000)
    # the order of the source is kept, and nothing the person included is lost
    assert [(p.title, p.include) for p in parts] == [("Tiny", True), ("Copyright", False), ("One", True)] and parts[1].note == "Front matter"
    assert parts[0].chars == len("Tiny") + len("Short.")


def test_a_text_with_no_chapters_is_titled_from_its_words_and_numbered_when_it_is_cut():
    whole = draft_parts([Chapter(None, [Block("paragraph", t) for t in ("The quick brown fox jumps over the lazy dog today.", *sized(500))])],
                        paragraphs_of, part_max_chars=30000)
    assert len(whole) == 1 and whole[0].title == "The quick brown fox jumps over the lazy…"
    cut = draft_parts([Chapter(None, [Block("paragraph", t) for t in sized(1000, 8)])], paragraphs_of, part_max_chars=3500)
    assert [p.title for p in cut] == ["Part 1", "Part 2", "Part 3"] and all(p.chars <= 3500 for p in cut)


def test_titles_from_words():
    assert title_from([Paragraph("paragraph", "Short title here.")]) == "Short title here."
    assert title_from([Paragraph("paragraph", "   "), Paragraph("paragraph", "code", skipped="code")]) == "Reading"
    assert title_from([Paragraph("paragraph", "x" * 200)]).endswith("…") and len(title_from([Paragraph("paragraph", "x" * 200)])) <= 61
