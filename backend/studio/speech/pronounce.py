"""The pronunciation list (DESIGN.md §44.3): words the engines say wrongly, and how to say them.

An entry is a word (or a short phrase) and a respelling: `Nguyen` -> `win`. The respelling replaces the word in the text that is GIVEN TO THE ENGINE, and nowhere
else: the review, the pieces in the database, the read-along, the part titles and the ZIP all keep the words as they were. That is why this module is plain
functions over strings and knows nothing about readings; `service.py` calls it at the moment a part's job is made.

The rule (§44.3), which the tests pin one clause at a time:
  * an entry matches WHOLE WORDS: `win` is not found in `winter`; `Nguyen` is found in `Nguyen's` and in `Nguyen-Smith` (an apostrophe or a hyphen ends a word);
  * case does not matter, unless the entry says `match_case` (so that `US` can be said "you ess" without changing `us`);
  * a phrase matches across any run of white space;
  * the LEFTMOST match wins and, where several entries match at the same place, the LONGEST;
  * everything is done in ONE pass over the original text: a respelling is never looked at again, so one entry cannot change what another has said.

Speed matters here, because a book of 1,500,000 characters is matched against up to 1,000 entries: the text is scanned for the START of each word only, and at
such a place only the entries that begin with the same letter are tried (a dictionary of small alternations), which is a few dozen tries a word and not a thousand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

WORD_MAX = 60  # the longest word or phrase an entry may name
WORD_WORDS_MAX = 6  # and the most words in it ("a short phrase")
SAY_MAX = 120  # the longest respelling
ENTRIES_MAX = 1000  # the most entries in the list
TRY_CHARS = 5000  # how much text "Try it" takes: a few paragraphs is plenty to see what the list does


class EntryProblem(ValueError):
    """An entry that cannot be kept. `field` is the part of the entry at fault (`word` or `say`), `code` a short name for the tests and the page."""

    def __init__(self, field: str, code: str, message: str):
        super().__init__(message)
        self.field, self.code, self.message = field, code, message


@dataclass(frozen=True)
class Entry:
    """One line of the list as the matcher needs it."""

    word: str
    say: str
    match_case: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"word": self.word, "say": self.say, "match_case": self.match_case}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Entry":
        return cls(word=str(raw["word"]), say=str(raw["say"]), match_case=bool(raw.get("match_case", False)))


# ---------------------------------------------------------------- checking what a person typed
def _tidy(raw: Any) -> str:
    """The text of a field with every run of white space made one space and the ends trimmed (a word typed with a trailing space is the same word)."""
    return " ".join(str(raw).split())


def word_key(word: str) -> str:
    """What makes two words the same word for the list: the word with its case folded away. The list holds ONE entry for each key, so that no two entries can
    ever fight over the same text (§44.8 choice 4), including one that matches capitals and one that does not."""
    return word.casefold()


def check_word(raw: Any) -> str:
    """The word of an entry, tidied, or an `EntryProblem` saying what is wrong with it."""
    word = _tidy(raw)
    if not word:
        raise EntryProblem("word", "empty", "Write the word, or the short phrase, that is said wrongly.")
    if len(word) > WORD_MAX or len(word.split(" ")) > WORD_WORDS_MAX:
        raise EntryProblem("word", "too_long", f"A word on the list is at most {WORD_MAX} characters and {WORD_WORDS_MAX} words; this is a short phrase at most.")
    if not word.isprintable():
        raise EntryProblem("word", "characters", "The word has a character that cannot be written out, such as a control character.")
    if not word[0].isalnum():
        # the matcher finds the START of each word and looks only at entries that begin with the letter there; an entry that began with a symbol could never be found
        raise EntryProblem("word", "start", "A word on the list has to begin with a letter or a digit.")
    return word


def check_say(raw: Any, word: str) -> str:
    """The respelling of an entry, tidied, or an `EntryProblem`. It cannot be empty (an entry changes how a word is said; it does not take the word out, §44.8
    choice 5) and cannot be the word again (that would change nothing and look as if it had)."""
    say = _tidy(raw)
    if not say:
        raise EntryProblem("say", "empty", "Write how the word is said, in plain letters (for example: win).")
    if len(say) > SAY_MAX:
        raise EntryProblem("say", "too_long", f"A respelling is at most {SAY_MAX} characters.")
    if not say.isprintable():
        raise EntryProblem("say", "characters", "The respelling has a character that cannot be written out, such as a control character.")
    if say == word:
        raise EntryProblem("say", "same", "That is the word itself, so it would change nothing. Write it the way it should be said.")
    return say


# ---------------------------------------------------------------- the matcher
_WORD_RUN = re.compile(r"\w+")  # a maximal run of letters, digits and underscores: its start is a place where a whole-word match may begin


def _first(char: str) -> str:
    """The key an entry is filed under, and a place in the text is looked up by: its first letter, in lower case."""
    return char.lower()[:1]


def _body(word: str) -> str:
    """The regular expression of one entry's words: each word literally, any run of white space between them, and a closing boundary after a letter, digit or
    underscore (so `win` stops before `ter` but `St.` needs none after its full stop). The opening boundary is the caller's: it only ever looks at a word's start."""
    body = r"\s+".join(re.escape(piece) for piece in word.split(" "))
    if re.match(r"\w", word[-1]):
        body += r"(?!\w)"
    return body


@dataclass
class Applied:
    """What `Matcher.apply` made of a text: the spoken words, and how many times each entry (by its place in the matcher's list) was used."""

    spoken: str
    counts: dict[int, int]

    @property
    def uses(self) -> int:
        return sum(self.counts.values())


class Matcher:
    """The list, compiled. Make one for a job (or one for a request) and `apply` it to each piece."""

    def __init__(self, entries: Iterable[Entry]):
        # Longest first, so that among entries that begin at the same place the longest is tried first and wins (a regular expression takes the first alternative
        # that matches, not the longest). The word itself breaks a tie, so the order and so the result never depend on the order the entries were made in.
        self.entries: list[Entry] = sorted(entries, key=lambda e: (-len(e.word), e.word.casefold(), e.word))
        buckets: dict[str, list[str]] = {}
        for index, entry in enumerate(self.entries):
            flags = "" if entry.match_case else "i"
            buckets.setdefault(_first(entry.word[0]), []).append(f"(?P<e{index}>(?{flags}:{_body(entry.word)}))")
        self._by_letter = {letter: re.compile("|".join(alternatives)) for letter, alternatives in buckets.items()}

    def __bool__(self) -> bool:
        return bool(self.entries)

    def apply(self, text: str) -> Applied:
        """`text` with every entry that matches replaced by its respelling, and a count of the uses. One pass over the original: a respelling is never matched again."""
        if not self.entries:
            return Applied(text, {})
        out: list[str] = []
        counts: dict[int, int] = {}
        done = 0  # everything before this has been copied (or replaced)
        for run in _WORD_RUN.finditer(text):
            start = run.start()
            if start < done:  # inside a phrase that has just been replaced
                continue
            pattern = self._by_letter.get(_first(text[start]))
            if pattern is None:
                continue
            found = pattern.match(text, start)
            if found is None or found.lastgroup is None:
                continue
            index = int(found.lastgroup[1:])
            out.append(text[done:start])
            out.append(self.entries[index].say)
            done = found.end()
            counts[index] = counts.get(index, 0) + 1
        if not counts:
            return Applied(text, {})
        out.append(text[done:])
        return Applied("".join(out), counts)


def used_entries(entries: Sequence[Entry], texts: Iterable[str]) -> tuple[list[Entry], int]:
    """The entries that a body of text uses (in the order of the list given) and how many times in all: what a reading copies from the list when it is made."""
    matcher = Matcher(entries)
    totals: dict[int, int] = {}
    for text in texts:
        for index, n in matcher.apply(text).counts.items():
            totals[index] = totals.get(index, 0) + n
    chosen = {matcher.entries[i] for i in totals}
    return [entry for entry in entries if entry in chosen], sum(totals.values())


def spoken_for(text: str, matcher: Optional[Matcher]) -> str:
    """The words to give the engine for a piece whose words are `text`."""
    return matcher.apply(text).spoken if matcher else text
