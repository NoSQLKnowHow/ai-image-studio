"""Finding who speaks (DESIGN.md §44.5): lines that start with a name and a colon, `Alice: Hello there.`

Plain functions over strings, so every clause of the rule is tested without a server. The rule is strict on purpose, because a wrong guess silently takes a word out of the
text (the label is not read) and puts a line in another voice, and texts are full of lines that look like `Name: words` and are not speech.

A LINE is a speaker line when all of these hold:
  * it begins with a NAME, then a colon, then white space, then some words (so `10:30`, `https://example.com` and `Note:Something` are not);
  * the name is one to three words, each beginning with a capital letter and made of letters (and an apostrophe, a hyphen or a full stop), with no digit anywhere (so
    `Chapter 1:`, `Agent 47:` and `Note to self:` are not), 2 to 30 characters in all and not a single letter (so `Q:` and `A:` are not);
  * no word of the name is one of the labels that texts are full of (`Note`, `Warning`, `Chapter`, `Example`, ... : `LABELS` below).
A NAME is a speaker when at least two lines of the text begin with it (`MIN_LINES`), names being the same ignoring case; and a TEXT has speakers only when at least two names
are (`MIN_NAMES`): a lone `Remember: lock the door` is a line, and one name alone is not a conversation. At most `MAX_SPEAKERS` are offered, the ones that begin the most lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

MAX_SPEAKERS = 12  # the most names offered for one text
MIN_LINES = 2  # lines that must begin with a name for it to be a speaker
MIN_NAMES = 2  # names that must pass for a text to have speakers at all
NAME_MIN_CHARS, NAME_MAX_CHARS, NAME_MAX_WORDS = 2, 30, 3
SAMPLE_CHARS = 80  # how much of a speaker's first line the review shows

# The labels that texts are full of: a line that starts with one is a label, not a person (matched ignoring case, against each word of a name, without a closing full stop).
LABELS = frozenset("""
note notes warning warnings caution danger tip tips hint hints important example examples exercise exercises answer answers question questions summary abstract introduction
conclusion chapter part section step steps figure fig table page see source sources reference references update edit editor translator author caption credit credits
copyright contents title subtitle by re subject from to cc bcc date sent time duration location address phone tel fax email url link website ingredients directions method
instructions serves yield prep cook cooking difficulty rating status version license licence total price cost score result results problem solution goal aim rule rules
remember however finally first second third next then also but and or so thus therefore meanwhile ps nb todo draft abstract keywords definition theorem proof lemma
""".split())

_WORD = re.compile(r"^[^\W\d_][^\W\d_'’.\-]*(?:[’'.\-][^\W\d_]+)*\.?$")  # letters, with an apostrophe, hyphen or full stop between them (Mary-Jane, O'Neil, Dr.)


@dataclass(frozen=True)
class Label:
    """A line that looks like a speaker line: the name as written, the label as written (name and colon), and the words after it."""

    name: str
    label: str
    rest: str

    @property
    def key(self) -> str:
        return name_key(self.name)


def name_key(name: str) -> str:
    """What makes two names the same speaker: the name with its case folded away and its spaces made one (`ALICE` and `Alice` are one speaker)."""
    return " ".join(name.split()).casefold()


def _name_ok(name: str) -> bool:
    """Whether `name` has the shape of a name (the rules above), not yet whether the text has enough of them."""
    if not NAME_MIN_CHARS <= len(name) <= NAME_MAX_CHARS or name != name.strip() or "  " in name:
        return False
    words = name.split(" ")
    if len(words) > NAME_MAX_WORDS:
        return False
    for word in words:
        if not word[:1].isupper() or not _WORD.match(word) or word.rstrip(".").casefold() in LABELS:
            return False
    return sum(ch.isalpha() for ch in name) >= 2


def candidate(text: str) -> Optional[Label]:
    """The label at the start of `text` (a line or a paragraph), or None when it does not start like a speaker line. Looks only at the first colon."""
    colon = text.find(":")
    if colon < 1:
        return None
    name, rest = text[:colon], text[colon + 1:]
    if not rest[:1].isspace() or not rest.strip() or not _name_ok(name):
        return None
    return Label(name=name, label=text[:colon + 1], rest=rest.strip())


@dataclass
class Found:
    """What a text holds of one name: how it was first written and how many lines begin with it."""

    name: str
    lines: int = 0
    order: int = 0  # where among the names it was first seen
    sample: str = field(default="")


def tally(texts: Iterable[str]) -> dict[str, Found]:
    """Count, for each name (by `name_key`), the texts that begin with it. The name is kept as it was first written."""
    found: dict[str, Found] = {}
    for text in texts:
        label = candidate(text)
        if label is None:
            continue
        entry = found.get(label.key)
        if entry is None:
            entry = found[label.key] = Found(label.name, 0, len(found), label.rest[:SAMPLE_CHARS])
        entry.lines += 1
    return found


def accepted(found: dict[str, Found]) -> dict[str, Found]:
    """The names of a tally that are speakers: at least `MIN_LINES` lines each, at least `MIN_NAMES` such names, and the `MAX_SPEAKERS` with the most lines if there are more.
    In the order the names first appear. Nothing at all when fewer than two names qualify."""
    qualify = [(key, f) for key, f in found.items() if f.lines >= MIN_LINES]
    if len(qualify) < MIN_NAMES:
        return {}
    kept = sorted(qualify, key=lambda pair: (-pair[1].lines, pair[1].order))[:MAX_SPEAKERS]  # most lines first; a tie goes to the name seen first
    return {key: f for key, f in sorted(kept, key=lambda pair: pair[1].order)}


def speaker_keys(texts: Iterable[str]) -> set[str]:
    """The keys of the speakers a text has, from its lines or paragraphs: what a reader needs to know where a new paragraph must begin."""
    return set(accepted(tally(texts)))


def starts_speaker_line(line: str, keys: set[str]) -> bool:
    """Whether `line` begins with the label of one of the speakers `keys` names: a reader then starts a new paragraph here, even with no blank line before it."""
    label = candidate(line.strip())
    return label is not None and label.key in keys
