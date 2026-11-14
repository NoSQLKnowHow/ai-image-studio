"""Search, tags and notes on runs (DESIGN.md §45): the words rule, the word index's tables and what goes into it, and the cleaning of what a person types.

Everything a person types here is user input, so none of it is ever part of SQL text or of FTS5's query syntax: a search is cut into WORDS (runs of letters and digits) and the
match string is made from those words alone, each in quotes with a `*`, so no quote, `*`, `AND`, `-` or `:` a person types can be syntax (§45.4 rule 2). The page has the same
rule in `frontend/src/history.ts` (`searchTokens`), and both are tested against one table of cases (`tests/filter_cases.json`, §29.5), so they cannot drift apart.

The tables are in this file and not in `db.py` for the reason the Prompt builder's are in `builder/schema.py`: the feature is one file's worth, and `db.py` adds them to the end of its schema.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Optional, Sequence

from .builder import TAG_CHARS, TAGS_MAX, normalize_tag

# What a person may type, and what is used of it (DESIGN.md §45.4 rules 2, 9, 10). The page is told them in `capabilities.limits.annotations`.
SEARCH_CHARS = 200  # the longest search text (and the longest model name) a request may carry; longer is a 422
SEARCH_WORDS = 10  # the most distinct words of a search that are used; the rest are ignored
NOTES_CHARS = 2000  # the longest note
SUGGEST_MAX = 8  # the most tags Suggest may be asked for (the setting `STUDIO_TAG_SUGGESTIONS`, 0 switching it off)
TAGS_LISTED = 500  # the most tags `GET /api/tags` lists
FACETS_LISTED = 50  # the most models, and the most sizes, a drop-down lists

# The tables of schema 13 (DESIGN.md §45.7). They only ADD: the `runs` table is untouched, so no run can be lost or reordered by the upgrade. A tag and a note go with their run
# (ON DELETE CASCADE; `Database._delete_rows` also deletes them by hand, as it does for every table that hangs off a run). `run_tags.tag` is already cleaned (`clean_tags`).
RUN_ANNOTATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_tags (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    tag    TEXT NOT NULL,
    PRIMARY KEY (run_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_run_tags_tag ON run_tags(tag);
CREATE TABLE IF NOT EXISTS run_notes (
    run_id     TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    notes      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# The word index over runs: one row for each run that is not a hidden job (the short jobs behind a voice sample), kept in step by the code, in the transaction that changes what it holds
# (`Database.index_run`). `prompt` is the run's words (a reading's title), `extra` what some kinds have besides (a music run's lyrics; a reading's source name), `notes` and `tags` are
# the person's own. A separate statement, because a SQLite built without FTS5 refuses it and the studio then searches with a plain match instead (DESIGN.md §45.7), as the prompts' is.
RUN_SEARCH_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS run_search USING fts5(run_id UNINDEXED, prompt, extra, notes, tags, tokenize='unicode61 remove_diacritics 2')
"""

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)  # a word: a run of letters and digits (not the underscore, which the index's tokenizer also splits at)


def fold(text: str) -> str:
    """Lower case with accents taken off (`Café` is `cafe`), the way the word index reads text (`unicode61 remove_diacritics 2`): decompose, drop the combining marks, lower-case. It
    does NOT use `casefold`, which turns `ß` into `ss` where the index leaves it alone. `ø` and `ß` have no decomposition, so they stay as they are on both sides."""
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text))
    return "".join(c for c in decomposed if not unicodedata.category(c).startswith("M")).lower()


def words_of(text: str) -> list[str]:
    """Every word of `text`, folded, in order (the same word again included): what the index holds for a text, and what the plain match and the tests compare with."""
    return _TOKEN.findall(fold(text))


def search_terms(text: Optional[str], limit: int = SEARCH_WORDS) -> list[str]:
    """The words a person typed in the search box: folded, each ONCE, at most `limit` of them. Every word counts, however short or common (they typed it), and nothing else does: a
    quote, `*`, `AND`, `OR`, `-`, `:` and brackets are punctuation or ordinary words, never syntax. No word at all gives an empty list, which means no search (DESIGN.md §45.4 rule 2)."""
    found: list[str] = []
    for word in words_of(text or ""):
        if word not in found:
            found.append(word)
    return found[:limit]


def match_string(terms: Sequence[str]) -> str:
    """The FTS5 query for `terms`: each word in quotes with a `*` (the start of a word is enough), separated by spaces (all of them must be found). A word is letters and digits only, so
    it can hold no quote to break out of; this is the one place a query is made, and nothing a person typed reaches it except as such a word."""
    return " ".join(f'"{term}"*' for term in terms)


def clean_notes(raw: Any) -> str:
    """A note as it is stored: line breaks kept (as `\\n`), tabs kept, every other control or invisible character removed, white space at the ends trimmed. Empty means no note.
    The length is the caller's to check (`NOTES_CHARS`), so that the page can be told in words how far over it is."""
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C")).strip()


def clean_tags(raw: Sequence[Any]) -> list[str]:
    """Typed tags as they are stored: each cleaned as a saved prompt's tag is (`builder.normalize_tag`: lower case, 30 characters at most, no comma, white space made one space), the
    empty ones dropped and each once, in the order given. The count is the caller's to check (`TAGS_MAX`)."""
    return list(dict.fromkeys(tag for tag in (normalize_tag(item) for item in raw) if tag))


def index_fields(mode: str, prompt: str, lyrics: Optional[str], options_json: Optional[str]) -> tuple[str, str]:
    """What a run puts in the word index besides its notes and tags: (its `prompt`, its `extra`). `extra` is a music run's lyrics (they are NOT in the prompt column) and a reading's source
    name (a file's name, or the web address and its host) from its options; a picture or a Vector run has none. Not indexed, on purpose (§45.4 rule 1): a negative prompt, the settings,
    and the words a Vector run's recipe adds."""
    extra: list[str] = [lyrics] if lyrics else []
    if mode == "speech" and options_json:
        try:
            source = json.loads(options_json).get("source")
        except (ValueError, AttributeError):
            source = None
        if isinstance(source, dict):
            extra.extend(source[key] for key in ("name", "host", "url") if isinstance(source.get(key), str))
    return prompt, " ".join(extra)


# The size of a picture run, as the card shows it (`sizeText` in the page): the first result picture that is not in the bin, else the size that was asked for. Two SQL expressions over
# the `runs` table, one for each side, so that a filter and the list of sizes read the same thing. Fixed text with no placeholder.
_FIRST_PICTURE = "(SELECT i.{column} FROM images i WHERE i.run_id = runs.id AND i.kind = 'output' AND i.deleted_at IS NULL ORDER BY i.idx LIMIT 1)"
RUN_WIDTH = f"COALESCE({_FIRST_PICTURE.format(column='width')}, runs.width)"
RUN_HEIGHT = f"COALESCE({_FIRST_PICTURE.format(column='height')}, runs.height)"

_SIZE = re.compile(r"^([0-9]{1,5})x([0-9]{1,5})$")


def parse_size(text: Optional[str]) -> Optional[tuple[int, int]]:
    """`2752x1536` as (2752, 1536), or None for anything else (digits only, one `x`): a size is two whole numbers and nothing a person could make into more."""
    found = _SIZE.match(text or "")
    return (int(found.group(1)), int(found.group(2))) if found else None


# The words of the plain match, used where this SQLite has no FTS5 (DESIGN.md §45.7): everything the index would hold, in one lower-cased text. A reading's options stand in for its
# source name (they hold it among a few other words), so the plain match finds more than the index would; that is the price of not having one.
PLAIN_HAYSTACK = (
    "lower(runs.prompt || ' ' || COALESCE(runs.lyrics, '') || ' ' || CASE WHEN runs.mode = 'speech' THEN runs.options_json ELSE '' END || ' ' || "
    "COALESCE((SELECT n.notes FROM run_notes n WHERE n.run_id = runs.id), '') || ' ' || "
    "COALESCE((SELECT group_concat(t.tag, ' ') FROM run_tags t WHERE t.run_id = runs.id), ''))"
)


def plain_pattern(term: str) -> str:
    """The LIKE pattern for one word of the plain match: the word as a substring, with LIKE's own characters made literal (a word is letters and digits, so there are none, but
    the pattern does not rely on that)."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


__all__ = [
    "FACETS_LISTED", "NOTES_CHARS", "PLAIN_HAYSTACK", "RUN_ANNOTATION_SCHEMA", "RUN_HEIGHT", "RUN_SEARCH_SQL", "RUN_WIDTH", "SEARCH_CHARS", "SEARCH_WORDS", "SUGGEST_MAX",
    "TAGS_LISTED", "TAGS_MAX", "TAG_CHARS", "clean_notes", "clean_tags", "fold", "index_fields", "match_string", "parse_size", "plain_pattern", "search_terms", "words_of",
]
