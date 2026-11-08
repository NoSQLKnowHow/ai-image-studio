"""Dividing text into sentences, and long sentences into smaller pieces (DESIGN.md §38.5 item 4, criterion 270).

The rule for a sentence boundary is: a full stop, exclamation mark, question mark or ellipsis (with any closing quotes or brackets after it), then white
space, then something that starts a sentence (a capital letter, a digit, an opening quote or bracket followed by one of those, or a letter of a script with
no capitals). Three things make that not enough, and are handled here:
  * abbreviations: `Dr.` and `St.` never end a sentence, `etc.` and `p.m.` do only before a capital, an initial like `J.` never does;
  * numbers and addresses: a full stop with no white space after it (3.14, example.com) is not a boundary at all, because the pattern needs the space;
  * quoted speech: `"Stop!" she said.` is one sentence, because what follows the closing quote is lower case.
"""

from __future__ import annotations

import re

# Never the end of a sentence, whatever follows (titles, and abbreviations that are followed by what they introduce).
_NEVER = frozenset({
    "mr", "mrs", "ms", "mx", "dr", "prof", "sr", "jr", "st", "mt", "ft", "fig", "figs", "vs", "cf", "viz", "ca", "approx", "dept", "est", "gen", "col",
    "lt", "sgt", "capt", "cmdr", "rev", "hon", "gov", "sen", "rep", "pres", "vol", "vols", "ch", "pp", "ed", "eds", "al", "e.g", "i.e", "ph.d", "b.a", "m.a",
    "b.sc", "m.sc", "jan", "feb", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "mon", "tue", "thu", "fri",
})  # month and day abbreviations are here only when they are not also English words: "He sat. The dog" must still end a sentence, so "sat", "sun", "wed" and "mar" are not
# The end of a sentence only when a capital letter follows ("at 5 p.m. The meeting", but "at 5 p.m. tomorrow").
_ONLY_BEFORE_CAPITAL = frozenset({"etc", "a.m", "p.m", "u.s", "u.k", "u.n", "e.u", "d.c", "inc", "ltd", "co", "corp", "no", "p"})

_CANDIDATE = re.compile(r"([.!?…]+)([\"'”’»)\]}]*)(\s+|$)")
_CJK_END = re.compile(r"([。！？]+)([」』）]*)")
_OPENERS = "\"'“‘«([{"
_DELIMITERS = ("; ", ": ", ", ", " — ", " – ", " - ", " ")  # where a too-long sentence is cut, best place first


def _starts_sentence(rest: str) -> bool:
    """Whether `rest` (the text after the white space) begins like the start of a sentence."""
    i = 0
    while i < len(rest) and rest[i] in _OPENERS:
        i += 1
    if i >= len(rest):
        return False
    c = rest[i]
    if c.isdigit():
        return True
    if c.isalpha():
        return c.isupper() or c.lower() == c.upper()  # a capital, or a letter of a script that has no capitals (Arabic, Hindi, Chinese, ...)
    return False


_LOOKBEHIND = 24  # an abbreviation is never longer than this; looking further back would make a paragraph of dots take quadratic time
_LOOKAHEAD = 12  # enough to see past a few opening quotes to the first letter


def _previous_word(text: str, end: int) -> str:
    """The word before the full stop that begins at `end`, with dots inside it kept (`U.S`, `e.g`, `Ph.D`): lower case, no final dot."""
    match = re.search(r"((?:[A-Za-z]\.)*[A-Za-z]+)$", text[max(0, end - _LOOKBEHIND):end])
    return match.group(1) if match else ""


def split_sentences(text: str) -> list[str]:
    """The sentences of one paragraph, in order, trimmed. Joined with single spaces they give the paragraph back (white space aside)."""
    text = text.strip()
    boundaries: list[int] = []
    for match in _CANDIDATE.finditer(text):
        stops, _closers, space = match.groups()
        end = match.end(2)  # the sentence includes any closing quote or bracket
        if match.end() == len(text):
            continue  # the end of the text is the end of the last sentence; nothing to cut
        rest = text[match.end():match.end() + _LOOKAHEAD]  # a window, not the whole remainder: a copy of the rest for every stop would be quadratic
        if not _starts_sentence(rest):
            continue
        if stops == ".":  # only a lone full stop can belong to an abbreviation
            word = _previous_word(text, match.start(1))
            lower = word.lower()
            if len(word) == 1 and word.isupper():
                continue  # an initial: "J. R. R. Tolkien"
            if lower in _NEVER:
                continue
            if lower in _ONLY_BEFORE_CAPITAL:
                first = rest.lstrip(_OPENERS)[:1]
                if lower == "no" and first.isdigit():
                    continue  # "No. 5"
                if not first.isupper():
                    continue
        boundaries.append(end)
    # Chinese and Japanese stops need no white space after them
    for match in _CJK_END.finditer(text):
        if match.end() < len(text):
            boundaries.append(match.end())
    cuts = sorted(set(boundaries))
    sentences, start = [], 0
    for cut in cuts:
        piece = text[start:cut].strip()
        if piece:
            sentences.append(piece)
        start = cut
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def cut_long(text: str, max_chars: int) -> list[str]:
    """Cut `text` that is longer than `max_chars` into pieces no longer than that, at the best place in the last part of the allowance: after a semicolon or
    colon, then a comma, then a dash, then any space. A word longer than the allowance is cut where it stands."""
    out: list[str] = []
    text = text.strip()
    pos = 0  # an index and not a repeatedly shortened copy of the text: a very long text must not take quadratic time
    while len(text) - pos > max_chars:
        window = text[pos:pos + max_chars + 1]
        floor = max_chars // 2  # never cut so early that the piece is a fragment
        cut = -1
        for delimiter in _DELIMITERS:
            at = window.rfind(delimiter, floor)
            if at != -1:
                cut = at + (0 if delimiter == " " else len(delimiter.rstrip()))
                break
        if cut <= 0:
            cut = max_chars  # no place to cut: a very long word
        piece = text[pos:pos + cut].strip()
        if piece:
            out.append(piece)
        pos += cut
        while pos < len(text) and text[pos].isspace():
            pos += 1
    tail = text[pos:].strip()
    if tail:
        out.append(tail)
    return out


def pack(sentences: list[str], max_chars: int) -> list[str]:
    """Sentences gathered, in order, into chunks of at most `max_chars`: as many whole sentences as fit; a sentence that is too long on its own is cut by
    `cut_long`. Every chunk is non-empty, and joined with spaces they are the sentences again."""
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(cut_long(sentence, max_chars))
        elif current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        chunks.append(current)
    return chunks
