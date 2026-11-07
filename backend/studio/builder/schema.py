"""The tables of schema 9, the Prompt builder's (DESIGN.md §37.4).

They are kept here and not in `db.py` so that the builder is one folder, and are added to the end of `db._SCHEMA`. Every statement is `IF NOT EXISTS`, so
opening a schema 8 database makes the new tables and touches nothing else. Three things are deliberately not plain foreign keys:

- `prompts.project_id`, `chats.project_id` and `preferences.project_id` are plain columns, like `runs.project_id` (§32.4). Deleting a project clears
  the first two and turns the third's preferences off (`Database.delete_project`), in one transaction.
- `prompt_versions.chat_id` is plain too: a version remembers which chat it came from even after that chat is deleted for good (the page then says so).
- `runs.prompt_id` and `runs.prompt_version` (the two columns schema 9 adds to `runs`, in `db.py`) are plain: deleting a prompt for good leaves them,
  and a card keeps saying *From "Lighthouse" v3, deleted*.

`for` is an SQL keyword, so the column is `for_kind` (the API says `for`). Times are the studio's usual `...Z` strings.
"""

from __future__ import annotations

# What a prompt or a chat can be made for (DESIGN.md §37.3 rule 17), and where a version came from (rule 2). The page, the routes and the CHECKs read these.
FOR_KINDS = ("images", "vector", "music", "edit")
DETAILS = ("short", "normal", "rich")
SOURCES = ("builder", "hand", "wording", "revision")
PREFERENCE_SOURCES = ("hand", "suggestion")
ROLES = ("user", "assistant")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


BUILDER_SCHEMA = f"""
-- A prompt: what a person keeps. Its words are in its versions (the newest is the current one). `deleted_at` is the bin, as for runs (§33).
CREATE TABLE IF NOT EXISTS prompts (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    for_kind    TEXT NOT NULL DEFAULT 'images' CHECK (for_kind IN ({_in(FOR_KINDS)})),
    project_id  TEXT,
    favourite   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT,
    restored_at TEXT
);
-- Versions are only ever added (§37.3 rule 2): there is no UPDATE of this table in the code, and no DELETE except with the prompt itself.
CREATE TABLE IF NOT EXISTS prompt_versions (
    id          TEXT PRIMARY KEY,
    prompt_id   TEXT NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    n           INTEGER NOT NULL CHECK (n >= 1),
    text        TEXT NOT NULL,
    negative    TEXT,
    settings    TEXT NOT NULL DEFAULT '{{}}',
    note        TEXT,
    source      TEXT NOT NULL CHECK (source IN ({_in(SOURCES)})),
    chat_id     TEXT,
    message_seq INTEGER,
    created_at  TEXT NOT NULL,
    UNIQUE (prompt_id, n)
);
CREATE TABLE IF NOT EXISTS prompt_tags (
    prompt_id TEXT NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    PRIMARY KEY (prompt_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_prompt_tags_tag ON prompt_tags(tag);
-- A conversation. It is saved from its first message, whether or not it ever becomes a prompt (§37.6 choice 12). `prompt_id` is the prompt it is about
-- (Flesh out, Refine); when that prompt is deleted for good the chat keeps its messages and is simply no longer linked.
CREATE TABLE IF NOT EXISTS chats (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    for_kind    TEXT NOT NULL DEFAULT 'images' CHECK (for_kind IN ({_in(FOR_KINDS)})),
    prompt_id   TEXT REFERENCES prompts(id) ON DELETE SET NULL,
    project_id  TEXT,
    detail      TEXT NOT NULL DEFAULT 'normal' CHECK (detail IN ({_in(DETAILS)})),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT,
    restored_at TEXT
);
-- The messages of a chat, in order (`seq` from 1). `content` is the validated structure of a reply or what a person chose (chips, surprise, skip),
-- as JSON. The model's reasoning is never stored (§37.3 rule 18); a stopped reply is kept as far as it got, marked in `content`.
CREATE TABLE IF NOT EXISTS chat_messages (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL CHECK (seq >= 1),
    role       TEXT NOT NULL CHECK (role IN ({_in(ROLES)})),
    text       TEXT NOT NULL,
    content    TEXT,
    model      TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (chat_id, seq)
);
-- A picture attached to a message: a copy under <data>/builder/<chat>/, shrunk (§37.3 rule 13). `path` is relative to the data folder.
CREATE TABLE IF NOT EXISTS chat_attachments (
    id          TEXT PRIMARY KEY,
    chat_id     TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_seq INTEGER NOT NULL,
    path        TEXT NOT NULL,
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    name        TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_chat ON chat_attachments(chat_id, message_seq);
-- Standing notes told to the model in every reply (§37.3 rule 7). `project_id` null = everywhere. A project that is deleted turns its preferences OFF
-- and leaves this column as it was, so they are listed as no longer in a project and never start applying everywhere (rule 5).
CREATE TABLE IF NOT EXISTS preferences (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    project_id TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    source     TEXT NOT NULL DEFAULT 'hand' CHECK (source IN ({_in(PREFERENCE_SOURCES)})),
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

# The word index over a prompt's title, current text and tags: one row for each prompt, kept in step by the code (`BuilderStore`), not by triggers, so that
# a prompt is indexed as it is read from its newest version. It is a separate statement because SQLite built without FTS5 refuses it, and the studio
# then searches with a plain match instead (DESIGN.md §37.4).
PROMPT_SEARCH_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS prompt_search USING fts5(prompt_id UNINDEXED, title, text, tags, tokenize='unicode61 remove_diacritics 2')
"""
