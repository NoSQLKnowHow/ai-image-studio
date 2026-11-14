"""Making text speakable (DESIGN.md §38.5 item 3).

The rules are deliberately few and each one is reported (`Report`), so the page can list what was done and the person can switch cleaning off and
read the raw text. Nothing here rewrites numbers, dates, money or abbreviations: how those are said is the engine's business (§38.12 choice 6).

The rules, in the order they are applied to a paragraph:
  1. Unicode is normalised; soft hyphens, zero-width characters and control characters go; non-breaking spaces become spaces.
  2. Markdown and HTML markup goes: emphasis marks, inline code marks, `![images](...)`, `<tags>`; a `[link](address)` is read as its text.
  3. A bare web address is replaced by the word "link" (an address read letter by letter is unbearable).
  4. Footnote and citation marks (`[12]`, `[^3]`, a superscript digit after a word) go.
  5. Runs of white space become one space.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

# Invisible characters that only get in a speech engine's way. The zero-width joiner and non-joiner are NOT here: in some scripts they change a word.
_INVISIBLE = dict.fromkeys(map(ord, "­​⁠﻿‎‏‪‫‬‭‮"), None)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t  -   　]+")

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^()\s]|\([^)]*\))*(?:\s+[\"'][^)]*[\"'])?\)")
_REF_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_AUTOLINK = re.compile(r"<(?:https?://|www\.)[^>\s]+>")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_EMPHASIS_STAR = re.compile(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])")
_EMPHASIS_UNDERSCORE = re.compile(r"(?<![\w_])_(?!\s)([^_]+?)(?<!\s)_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}\"'’”"
_CITATION = re.compile(r"\s?\[\d{1,3}(?:\s*[,–—-]\s*\d{1,3})*\]")
_SUPERSCRIPT = re.compile(r"(?<=\w)[¹²³⁰⁴-⁹]+")
_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$|^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$", re.IGNORECASE)


@dataclass
class Report:
    """What cleaning did, counted, so the review can say so (criterion 271)."""

    markup: int = 0
    links: int = 0
    citations: int = 0
    invisible: int = 0
    page_numbers: int = 0
    code_blocks: int = 0
    tables: int = 0

    def lines(self) -> list[str]:
        """The plain sentences the page shows under the text, one for each rule that did something."""
        said = []
        if self.markup:
            said.append(f"Removed {self.markup} piece{'s' if self.markup != 1 else ''} of markup (bold, italics, link wrappers).")
        if self.links:
            said.append(f"Replaced {self.links} web address{'es' if self.links != 1 else ''} with the word \"link\".")
        if self.citations:
            said.append(f"Removed {self.citations} footnote or citation mark{'s' if self.citations != 1 else ''}.")
        if self.invisible:
            said.append(f"Removed {self.invisible} invisible character{'s' if self.invisible != 1 else ''} (soft hyphens and the like).")
        if self.page_numbers:
            said.append(f"Removed {self.page_numbers} page number{'s' if self.page_numbers != 1 else ''}.")
        if self.code_blocks:
            said.append(f"Left out {self.code_blocks} code block{'s' if self.code_blocks != 1 else ''}.")
        if self.tables:
            said.append(f"Left out {self.tables} table{'s' if self.tables != 1 else ''}.")
        return said


def normalize(text: str, report: Optional[Report] = None) -> str:
    """Unicode normalisation and the removal of characters nobody can hear; line breaks become `\\n`. Does not touch white space inside a line."""
    report = report or Report()
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    before = len(text)
    text = text.translate(_INVISIBLE)
    text = _CONTROL.sub("", text)
    report.invisible += before - len(text)
    return text


def strip_markup(text: str, report: Optional[Report] = None) -> str:
    """Markdown and HTML markup out of one paragraph's text. A link keeps its words; an image goes."""
    report = report or Report()

    def count(pattern: "re.Pattern[str]", replacement, value: str) -> str:
        new, n = pattern.subn(replacement, value)
        report.markup += n
        return new

    text = count(_HTML_COMMENT, "", text)
    text = count(_IMAGE, "", text)
    text = count(_LINK, r"\1", text)
    text = count(_REF_LINK, r"\1", text)
    text = count(_FOOTNOTE_REF, "", text)
    # an autolink is a web address in angle brackets: it becomes "link" like any other (below), so it is counted there, not here
    text, n = _AUTOLINK.subn(" link ", text)
    report.links += n
    text = count(_HTML_TAG, " ", text)
    text = count(_STRONG, r"\2", text)
    text = count(_EMPHASIS_STAR, r"\1", text)
    text = count(_EMPHASIS_UNDERSCORE, r"\1", text)
    text = count(_STRIKE, r"\1", text)
    text = count(_INLINE_CODE, r"\1", text)
    return text


def replace_addresses(text: str, report: Optional[Report] = None) -> str:
    """A web address in running text becomes the word "link". Punctuation that ended the sentence after the address stays."""
    report = report or Report()

    def swap(match: "re.Match[str]") -> str:
        address = match.group(0)
        stripped = address.rstrip(_TRAILING)
        report.links += 1
        return "link" + address[len(stripped):]

    return _URL.sub(swap, text)


def drop_citation_marks(text: str, report: Optional[Report] = None) -> str:
    """`[12]` and `[3, 4]` after a claim, and a superscript digit after a word, are references for the eye."""
    report = report or Report()
    text, n = _CITATION.subn("", text)
    text, m = _SUPERSCRIPT.subn("", text)
    report.citations += n + m
    return text


def collapse_spaces(text: str) -> str:
    """Every run of white space, newlines included, becomes one space; the ends are trimmed."""
    return " ".join(_SPACES.sub(" ", text).split())


def clean_text(text: str, report: Optional[Report] = None) -> str:
    """All the rules above, in order, on the text of one paragraph."""
    report = report or Report()
    text = normalize(text, report)
    text = strip_markup(text, report)
    text = replace_addresses(text, report)
    text = drop_citation_marks(text, report)
    return collapse_spaces(text)


def is_page_number(line: str) -> bool:
    """A line that is only a page number: `12`, `Page 12`, `12 / 40`, `- 12 -`."""
    return bool(_PAGE_NUMBER.match(line))


_SENTENCE_END = re.compile(r"[.!?…:;\"”’')\]]$")


def join_wrapped_lines(lines: list[str]) -> list[str]:
    """The lines of one block of a plain text file or a PDF page, as one or more paragraphs.

    A hard-wrapped paragraph has lines that stop in the middle of a sentence; they are joined with a space, and a word cut by a hyphen at the end of a
    line is made whole again when the next line starts with a lower-case letter ("exam-" then "ple" becomes "example"). A word that is hyphenated for real
    and happens to break at its hyphen ("well-" then "known") loses the hyphen too, because nothing here can tell the two apart; typeset text breaks words
    far more often than it breaks compounds, so that is the lesser harm. A block whose lines nearly all end like sentences is one paragraph per line (a
    text that was never wrapped), and is left that way."""
    lines = [line.strip() for line in lines if line.strip()]
    if len(lines) <= 1:
        return lines
    ending = sum(1 for line in lines if _SENTENCE_END.search(line))
    if ending >= 0.7 * len(lines):
        return lines
    return [join_lines(lines)]


def join_lines(lines: list[str]) -> str:
    """Lines of one paragraph as one line of text: joined with a space, except that a word cut by a hyphen at the end of a line is made whole."""
    joined = lines[0].strip()
    for line in lines[1:]:
        line = line.strip()
        if re.search(r"[A-Za-z]{2}-$", joined) and line[:1].islower():
            joined = joined[:-1] + line
        else:
            joined = joined + " " + line
    return joined


def split_paragraphs(text: str, report: Optional[Report] = None, speaker_keys: Optional[set[str]] = None) -> list[str]:
    """The paragraphs of plain text: blocks separated by blank lines, each block's hard-wrapped lines joined (`join_wrapped_lines`), page-number lines
    dropped. The result is raw, not yet cleaned: `clean_text` is applied to each by the caller.

    `speaker_keys` (DESIGN.md §44.5) are the speakers the text has (`speakers.speaker_keys`): a line that begins with one of their labels starts a new paragraph even
    with no blank line before it, which is how a script is written (`Alice: Hello` and `Bob: Hi` on consecutive lines). Without them, as in 1.24, such lines would be joined
    into one paragraph. Wrapped lines after a label still belong to its paragraph."""
    from .speakers import starts_speaker_line

    report = report or Report()
    paragraphs: list[str] = []
    for block in re.split(r"\n[ \t]*\n+", normalize(text, report).strip()):
        runs: list[list[str]] = [[]]
        for line in block.split("\n"):
            if is_page_number(line):
                report.page_numbers += 1
                continue
            if speaker_keys and runs[-1] and starts_speaker_line(line, speaker_keys):
                runs.append([])  # a speaker's line begins here
            runs[-1].append(line)
        for lines in runs:
            paragraphs.extend(join_wrapped_lines(lines))
    return paragraphs
