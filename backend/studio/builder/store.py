"""The Prompt builder's side of the database (DESIGN.md §37.3, §37.4): prompts and their versions, chats and their messages, and the bin.

It uses the studio's one connection and its lock (`Database.tx`, `read_one`, `read_all`), so it is part of the same transactions and the same
file; it is a separate module only because `db.py` is already long. This part holds what every later piece stands on: making chats and prompts,
the bin (Delete, Restore, Delete forever, the daily clean-up), the counts for the tab and the filters, and the word index kept in step with the
prompts. Reading lists, searching, editing and the rules about versions are added with the routes that use them.

Nothing here touches a file. A chat's pictures are in `<data>/builder/<chat>/`; the caller removes that folder after a chat is deleted for good, as the job
manager removes a run's files after its rows are gone.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any, Mapping, Optional, Sequence

from ..db import Database
from .schema import DETAILS, FOR_KINDS, ROLES, SOURCES

# The two things that can be in the bin, and the table of each
_TABLES = {"prompt": "prompts", "chat": "chats"}


class StoreError(ValueError):
    """The store was asked for something the schema does not hold (an unknown kind, a bad value); the routes check first, so this is a bug, not input."""


def _check(value: str, allowed: Sequence[str], what: str) -> str:
    if value not in allowed:
        raise StoreError(f"{what} {value!r} is not one of {', '.join(allowed)}")
    return value


class BuilderStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ chats
    def create_chat(self, *, now: str, title: str, for_kind: str = "images", project_id: Optional[str] = None, detail: str = "normal",
                    prompt_id: Optional[str] = None) -> str:
        """Make a chat, empty. It is saved from here on (rule 6): the first message is added with `add_message`. Returns its id."""
        chat_id = secrets.token_hex(16)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO chats (id, title, for_kind, prompt_id, project_id, detail, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, title, _check(for_kind, FOR_KINDS, "for"), prompt_id, project_id, _check(detail, DETAILS, "detail"), now, now))
        return chat_id

    def add_message(self, chat_id: str, *, now: str, role: str, text: str, content: Optional[Mapping[str, Any]] = None, model: Optional[str] = None) -> Optional[int]:
        """Add a message at the end of a chat and return its `seq` (from 1), or None when there is no such chat. The number is chosen inside the transaction,
        so two messages at once cannot take the same one."""
        with self.db.tx() as c:
            if c.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone() is None:
                return None
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_messages WHERE chat_id=?", (chat_id,)).fetchone()[0]
            c.execute(
                "INSERT INTO chat_messages (id, chat_id, seq, role, text, content, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (secrets.token_hex(16), chat_id, seq, _check(role, ROLES, "role"), text, None if content is None else json.dumps(content, sort_keys=True), model, now))
            c.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
            return seq

    def get_chat(self, chat_id: str) -> Optional[sqlite3.Row]:
        return self.db.read_one("SELECT * FROM chats WHERE id=?", (chat_id,))

    def messages(self, chat_id: str) -> list[sqlite3.Row]:
        return self.db.read_all("SELECT * FROM chat_messages WHERE chat_id=? ORDER BY seq", (chat_id,))

    # ------------------------------------------------------------------ prompts
    def create_prompt(self, *, now: str, title: str, text: str, negative: Optional[str] = None, settings: Optional[Mapping[str, Any]] = None,
                      note: Optional[str] = None, source: str = "hand", for_kind: str = "images", project_id: Optional[str] = None, tags: Sequence[str] = (),
                      favourite: bool = False, chat_id: Optional[str] = None, message_seq: Optional[int] = None) -> str:
        """Make a prompt with its version 1 and its tags, in one transaction, and index it. `tags` are already normalised. Returns the prompt's id."""
        prompt_id = secrets.token_hex(16)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO prompts (id, title, for_kind, project_id, favourite, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (prompt_id, title, _check(for_kind, FOR_KINDS, "for"), project_id, 1 if favourite else 0, now, now))
            c.execute(
                "INSERT INTO prompt_versions (id, prompt_id, n, text, negative, settings, note, source, chat_id, message_seq, created_at) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (secrets.token_hex(16), prompt_id, text, negative, json.dumps(settings or {}, sort_keys=True), note, _check(source, SOURCES, "source"),
                 chat_id, message_seq, now))
            for tag in dict.fromkeys(tags):  # in order, once each
                c.execute("INSERT INTO prompt_tags (prompt_id, tag) VALUES (?, ?)", (prompt_id, tag))
            self.reindex(c, prompt_id)
        return prompt_id

    def get_prompt(self, prompt_id: str) -> Optional[sqlite3.Row]:
        return self.db.read_one("SELECT * FROM prompts WHERE id=?", (prompt_id,))

    def versions(self, prompt_id: str) -> list[sqlite3.Row]:
        """The versions of a prompt, oldest first (1, 2, 3 …)."""
        return self.db.read_all("SELECT * FROM prompt_versions WHERE prompt_id=? ORDER BY n", (prompt_id,))

    def tags(self, prompt_id: str) -> list[str]:
        return [r["tag"] for r in self.db.read_all("SELECT tag FROM prompt_tags WHERE prompt_id=? ORDER BY tag", (prompt_id,))]

    # ------------------------------------------------------------------ the word index (DESIGN.md §37.4)
    def reindex(self, c: sqlite3.Connection, prompt_id: str) -> None:
        """Write the one row of the word index for a prompt: its title, the text of its newest version, and its tags. Called inside the transaction that
        changes any of them, so the index is never behind. Where this SQLite has no FTS5 there is no index and nothing to do."""
        if not self.db.has_fts5:
            return
        c.execute("DELETE FROM prompt_search WHERE prompt_id=?", (prompt_id,))
        row = c.execute(
            "SELECT p.title AS title, "
            "(SELECT text FROM prompt_versions v WHERE v.prompt_id = p.id ORDER BY v.n DESC LIMIT 1) AS text, "
            "(SELECT group_concat(tag, ' ') FROM prompt_tags t WHERE t.prompt_id = p.id) AS tags "
            "FROM prompts p WHERE p.id=?", (prompt_id,)).fetchone()
        if row is not None:
            c.execute("INSERT INTO prompt_search (prompt_id, title, text, tags) VALUES (?, ?, ?, ?)", (prompt_id, row["title"], row["text"] or "", row["tags"] or ""))

    def reindex_all(self) -> int:
        """Make the word index match the prompts, from scratch; returns how many prompts it holds. Needed when the index was made after prompts existed
        (a SQLite that gained FTS5 since they were saved). Cheap enough to do at start-up when the counts differ (`ensure_search_in_step`)."""
        if not self.db.has_fts5:
            return 0
        with self.db.tx() as c:
            c.execute("DELETE FROM prompt_search")
            ids = [r["id"] for r in c.execute("SELECT id FROM prompts").fetchall()]
            for prompt_id in ids:
                self.reindex(c, prompt_id)
            return len(ids)

    def ensure_search_in_step(self) -> bool:
        """At start-up: rebuild the index if it does not hold one row for every prompt. Returns whether it had to."""
        if not self.db.has_fts5:
            return False
        indexed = self.db.read_one("SELECT COUNT(*) AS n FROM prompt_search")["n"]
        prompts = self.db.read_one("SELECT COUNT(*) AS n FROM prompts")["n"]
        if indexed == prompts:
            return False
        self.reindex_all()
        return True

    # ------------------------------------------------------------------ the bin (DESIGN.md §37.3 rule 1)
    def bin_item(self, kind: str, item_id: str, now: str) -> str:
        """Move a prompt or a chat to the bin. Returns 'binned' (also when it already was: the first date counts) or 'not_found'. A prompt takes its versions
        with it, which is only to say they stay on it; its chats stay where they are, and so do the pictures made from it."""
        table = _TABLES[kind]
        with self.db.tx() as c:
            row = c.execute(f"SELECT deleted_at FROM {table} WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["deleted_at"] is None:
                c.execute(f"UPDATE {table} SET deleted_at=? WHERE id=?", (now, item_id))
            return "binned"

    def restore_item(self, kind: str, item_id: str, now: str) -> str:
        """Take one out of the bin, with a fresh clock. Returns 'restored', 'not_found' or 'not_in_bin'."""
        table = _TABLES[kind]
        with self.db.tx() as c:
            row = c.execute(f"SELECT deleted_at FROM {table} WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["deleted_at"] is None:
                return "not_in_bin"
            c.execute(f"UPDATE {table} SET deleted_at=NULL, restored_at=? WHERE id=?", (now, item_id))
            return "restored"

    def _delete_rows(self, c: sqlite3.Connection, kind: str, ids: Sequence[str]) -> None:
        """The one place that removes a prompt or a chat and what hangs off it, so that none forgets a table. The versions, tags, messages and attachments go
        by the foreign keys; a chat that was about a deleted prompt keeps its messages and loses the link (ON DELETE SET NULL); runs made from a prompt, and
        versions that name a chat, are plain columns and stay as they are."""
        for item_id in ids:
            if kind == "prompt" and self.db.has_fts5:
                c.execute("DELETE FROM prompt_search WHERE prompt_id=?", (item_id,))
            c.execute(f"DELETE FROM {_TABLES[kind]} WHERE id=?", (item_id,))

    def delete_item(self, kind: str, item_id: str) -> str:
        """Delete a prompt or a chat for good, whether or not it is in the bin. Returns 'deleted' or 'not_found'."""
        with self.db.tx() as c:
            if c.execute(f"SELECT 1 FROM {_TABLES[kind]} WHERE id=?", (item_id,)).fetchone() is None:
                return "not_found"
            self._delete_rows(c, kind, [item_id])
            return "deleted"

    def purge(self, cutoff: Optional[str], limit: int) -> dict[str, list[str]]:
        """Delete for good up to `limit` prompts and up to `limit` chats that went into the bin before `cutoff` (all of them when `cutoff` is None: Empty bin,
        or no bin is configured). Returns the ids of each, so that the caller can remove the folders of the chats."""
        gone: dict[str, list[str]] = {}
        with self.db.tx() as c:
            for kind, table in _TABLES.items():
                if cutoff is None:
                    rows = c.execute(f"SELECT id FROM {table} WHERE deleted_at IS NOT NULL ORDER BY deleted_at, id LIMIT ?", (limit,)).fetchall()
                else:
                    rows = c.execute(f"SELECT id FROM {table} WHERE deleted_at IS NOT NULL AND deleted_at < ? ORDER BY deleted_at, id LIMIT ?", (cutoff, limit)).fetchall()
                gone[kind] = [r["id"] for r in rows]
                self._delete_rows(c, kind, gone[kind])
        return gone

    # ------------------------------------------------------------------ counts (GET /api/builder/counts)
    def counts(self) -> dict[str, Any]:
        """What the tab and its filters show: prompts and chats on show, those in the bin, and (for prompts) the favourites, the projects with their
        counts, the ones in no project, and the tags with theirs. Everything but `deleted` counts what is NOT in the bin."""
        def projects_of(table: str) -> dict[str, int]:
            rows = self.db.read_all(f"SELECT project_id, COUNT(*) AS n FROM {table} WHERE project_id IS NOT NULL AND deleted_at IS NULL GROUP BY project_id")
            return {r["project_id"]: r["n"] for r in rows}

        def one(sql: str) -> int:
            return self.db.read_one(sql)["n"]

        tags = self.db.read_all(
            "SELECT t.tag AS tag, COUNT(*) AS n FROM prompt_tags t JOIN prompts p ON p.id = t.prompt_id WHERE p.deleted_at IS NULL GROUP BY t.tag ORDER BY t.tag")
        return {
            "prompts": {
                "all": one("SELECT COUNT(*) AS n FROM prompts WHERE deleted_at IS NULL"),
                "favourites": one("SELECT COUNT(*) AS n FROM prompts WHERE deleted_at IS NULL AND favourite=1"),
                "deleted": one("SELECT COUNT(*) AS n FROM prompts WHERE deleted_at IS NOT NULL"),
                "no_project": one("SELECT COUNT(*) AS n FROM prompts WHERE deleted_at IS NULL AND project_id IS NULL"),
                "projects": projects_of("prompts"),
                "tags": {r["tag"]: r["n"] for r in tags},
            },
            "chats": {
                "all": one("SELECT COUNT(*) AS n FROM chats WHERE deleted_at IS NULL"),
                "deleted": one("SELECT COUNT(*) AS n FROM chats WHERE deleted_at IS NOT NULL"),
                "no_project": one("SELECT COUNT(*) AS n FROM chats WHERE deleted_at IS NULL AND project_id IS NULL"),
                "projects": projects_of("chats"),
            },
        }
