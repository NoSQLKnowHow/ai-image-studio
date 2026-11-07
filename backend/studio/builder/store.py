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
import re
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

    def last_draft(self, chat_id: str) -> Optional[dict[str, Any]]:
        """The current draft of a chat: the newest one in its messages, whether the model wrote it or the person edited it by hand (their message carries the edited
        draft). None when no message has had one yet."""
        for row in self.db.read_all("SELECT content FROM chat_messages WHERE chat_id=? AND content IS NOT NULL ORDER BY seq DESC", (chat_id,)):
            content = json.loads(row["content"])
            draft = content.get("draft")
            if isinstance(draft, dict) and str(draft.get("prompt") or "").strip():
                return draft
        return None

    def list_chats(self, *, project: Optional[str] = None, deleted: bool = False, limit: int = 300) -> list[dict[str, Any]]:
        """The chats, newest activity first, each with a line of its last message. `project` is a project's id, or "none" for the chats in no project, or None for
        all; `deleted` lists the bin instead of the chats on show."""
        where, args = ["c.deleted_at IS NOT NULL" if deleted else "c.deleted_at IS NULL"], []
        if project == "none":
            where.append("c.project_id IS NULL")
        elif project:
            where.append("c.project_id = ?")
            args.append(project)
        rows = self.db.read_all(
            "SELECT c.*, (SELECT text FROM chat_messages m WHERE m.chat_id = c.id ORDER BY m.seq DESC LIMIT 1) AS last_text, "
            "(SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = c.id) AS message_count "
            f"FROM chats c WHERE {' AND '.join(where)} ORDER BY c.updated_at DESC, c.id LIMIT ?", (*args, limit))
        return [{**dict(r), "last_text": " ".join((r["last_text"] or "").split())[:120]} for r in rows]

    _CHAT_FIELDS = {"title": "title", "for_kind": "for_kind", "project_id": "project_id", "detail": "detail"}

    def update_chat(self, chat_id: str, *, now: str, **fields: Any) -> str:
        """Change a chat's title, what it is for, its project or its detail level (only the fields given). Returns 'updated' or 'not_found'."""
        for name in fields:
            if name not in self._CHAT_FIELDS:
                raise StoreError(f"a chat has no {name!r} to change")
        if "for_kind" in fields:
            _check(fields["for_kind"], FOR_KINDS, "for")
        if "detail" in fields:
            _check(fields["detail"], DETAILS, "detail")
        with self.db.tx() as c:
            if c.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone() is None:
                return "not_found"
            for name, value in fields.items():
                c.execute(f"UPDATE chats SET {self._CHAT_FIELDS[name]}=? WHERE id=?", (value, chat_id))
            c.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
            return "updated"

    # ------------------------------------------------------------------ a chat's pictures (DESIGN.md section 37.3 rule 13)
    # A picture is attached before the message it goes with exists, so it waits with message_seq 0 and is given its message's number when that is sent.
    def add_attachment(self, chat_id: str, *, path: str, width: int, height: int, name: Optional[str], message_seq: int = 0) -> Optional[str]:
        with self.db.tx() as c:
            if c.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone() is None:
                return None
            attachment_id = secrets.token_hex(16)
            c.execute("INSERT INTO chat_attachments (id, chat_id, message_seq, path, width, height, name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (attachment_id, chat_id, message_seq, path, width, height, name))
            return attachment_id

    def get_attachment(self, attachment_id: str) -> Optional[sqlite3.Row]:
        return self.db.read_one("SELECT * FROM chat_attachments WHERE id=?", (attachment_id,))

    def attachments(self, chat_id: str, *, pending: bool = False) -> list[sqlite3.Row]:
        """A chat's pictures in the order they were attached; with `pending`, only those not yet sent with a message."""
        return self.db.read_all(f"SELECT * FROM chat_attachments WHERE chat_id=?{' AND message_seq = 0' if pending else ''} ORDER BY rowid", (chat_id,))

    def send_attachments(self, chat_id: str, ids: Sequence[str], message_seq: int) -> list[str]:
        """Attach the waiting pictures named in `ids` (and in this chat) to a message, in the order named. Returns the ids that were."""
        sent: list[str] = []
        with self.db.tx() as c:
            for attachment_id in ids:
                cur = c.execute("UPDATE chat_attachments SET message_seq=? WHERE id=? AND chat_id=? AND message_seq=0", (message_seq, attachment_id, chat_id))
                if cur.rowcount == 1:
                    sent.append(attachment_id)
        return sent

    def delete_attachment(self, attachment_id: str) -> Optional[sqlite3.Row]:
        """Remove a picture that is still waiting (not yet sent with a message). Returns its row (so the caller can remove the file) or None."""
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM chat_attachments WHERE id=? AND message_seq=0", (attachment_id,)).fetchone()
            if row is not None:
                c.execute("DELETE FROM chat_attachments WHERE id=?", (attachment_id,))
            return row

    # ------------------------------------------------------------------ what the model is told about the person (rules 7 and 8)
    def active_preferences(self, project_id: Optional[str]) -> list[str]:
        """The standing notes that apply to a chat: the enabled ones for everywhere, and those of the chat's project, in the order the person put them."""
        rows = self.db.read_all(
            "SELECT text FROM preferences WHERE enabled = 1 AND (project_id IS NULL OR project_id = ?) ORDER BY position, created_at, id", (project_id,))
        return [r["text"] for r in rows]

    def all_preference_texts(self) -> list[str]:
        return [r["text"] for r in self.db.read_all("SELECT text FROM preferences")]

    _WORD = re.compile(r"\w+", re.UNICODE)
    _STOP = frozenset("the and for with that this from are was were has have into onto over under about but not you your our their its they them his her she him who how what when where which while".split())

    @classmethod
    def words_of(cls, text: str, limit: int = 12) -> list[str]:
        """The words of an idea that are worth looking for: at least three letters (or a number), not a common word, each once."""
        found: list[str] = []
        for word in cls._WORD.findall(text.casefold()):
            if (len(word) >= 3 or word.isdigit()) and word not in cls._STOP and word not in found:
                found.append(word)
        return found[:limit]

    def similar_prompts(self, text: str, *, project_id: Optional[str] = None, exclude: Sequence[str] = (), limit: int = 3) -> list[dict[str, Any]]:
        """Up to `limit` of the person's saved prompts (not in the bin) that share the most words with `text`, those in `project_id` first, for the model to see as
        examples of their taste (rule 8). Words are looked for in the title, the newest text and the tags, with the word index where there is one."""
        words = self.words_of(text)
        if not words:
            return []
        skip = set(exclude)
        newest = "(SELECT text FROM prompt_versions v WHERE v.prompt_id = p.id ORDER BY v.n DESC LIMIT 1)"
        negative = "(SELECT negative FROM prompt_versions v WHERE v.prompt_id = p.id ORDER BY v.n DESC LIMIT 1)"
        if self.db.has_fts5:
            query = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
            rows = self.db.read_all(
                f"SELECT p.id, p.title, p.for_kind, p.project_id, {newest} AS text, {negative} AS negative FROM prompt_search s JOIN prompts p ON p.id = s.prompt_id "
                "WHERE prompt_search MATCH ? AND p.deleted_at IS NULL ORDER BY (p.project_id IS NOT NULL AND p.project_id = ?) DESC, s.rank LIMIT ?",
                (query, project_id, limit + len(skip)))
        else:
            wanted = [f"%{w}%" for w in words]
            haystack = f"lower(p.title || ' ' || COALESCE({newest}, '') || ' ' || COALESCE((SELECT group_concat(tag, ' ') FROM prompt_tags t WHERE t.prompt_id = p.id), ''))"
            score = " + ".join(f"({haystack} LIKE ?)" for _ in words)
            rows = self.db.read_all(
                f"SELECT p.id, p.title, p.for_kind, p.project_id, {newest} AS text, {negative} AS negative, ({score}) AS hits FROM prompts p WHERE p.deleted_at IS NULL "
                "AND hits > 0 ORDER BY (p.project_id IS NOT NULL AND p.project_id = ?) DESC, hits DESC, p.updated_at DESC LIMIT ?", (*wanted, project_id, limit + len(skip)))
        return [{k: r[k] for k in ("id", "title", "for_kind", "text", "negative")} for r in rows if r["id"] not in skip][:limit]

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
