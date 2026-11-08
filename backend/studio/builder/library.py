"""The Prompt builder's library: saved prompts and their versions, tags, search, the pictures made from them, and the standing preferences
(DESIGN.md section 37.3 rules 1-5, 7; criteria 229, 238, 245, 246, 247).

It sits on `BuilderStore` (the same connection, lock and transactions). Versions are ONLY ever added: nothing here updates or deletes a row of `prompt_versions`, and
saving a version identical to the newest makes none and says so (rule 2). Everything a person types arrives already checked by the routes; this module trusts its
callers about lengths and uses the schema's CHECKs for the rest.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from typing import Any, Mapping, Optional, Sequence

from . import PREFERENCES_MAX
from .diff import changed, encoded, settings_diff, word_diff
from .schema import FOR_KINDS, SOURCES
from .store import BuilderStore, StoreError, _check

SORTS = ("updated", "newest", "title")
_ORDER = {"updated": "p.updated_at DESC, p.id", "newest": "p.created_at DESC, p.id", "title": "p.title COLLATE NOCASE, p.id"}
FIRST_WORDS = 240  # the start of a prompt shown on its card
THUMBS = 3  # the latest pictures made from a prompt shown on its card
_WORD = re.compile(r"\w+", re.UNICODE)
UNSET: Any = object()  # "not given", for a field that may be set to None


def _newest(column: str) -> str:
    return f"(SELECT {column} FROM prompt_versions v WHERE v.prompt_id = p.id ORDER BY v.n DESC LIMIT 1)"


def search_terms(text: str, limit: int = 8) -> list[str]:
    """The words a person typed in the search box, lower-cased, each once. Every word counts, however short or common: they typed it."""
    found: list[str] = []
    for word in _WORD.findall(text.casefold()):
        if word not in found:
            found.append(word)
    return found[:limit]


class Library:
    def __init__(self, store: BuilderStore) -> None:
        self.store, self.db = store, store.db

    # ------------------------------------------------------------------ versions
    def add_version(self, prompt_id: str, *, now: str, text: str, negative: Optional[str] = None, settings: Optional[Mapping[str, Any]] = None, note: Optional[str] = None,
                    source: str = "hand", chat_id: Optional[str] = None, message_seq: Optional[int] = None) -> tuple[str, Optional[int]]:
        """Add the next version of a prompt. Returns ('added', n), or ('unchanged', n) when the text, the negative prompt and the settings are those of the newest version
        (nothing is made), or ('not_found', None). The version numbers go 1, 2, 3 … and a number is never used twice."""
        stored = encoded(settings)
        negative = (negative or "").strip() or None
        with self.db.tx() as c:
            if c.execute("SELECT 1 FROM prompts WHERE id=?", (prompt_id,)).fetchone() is None:
                return "not_found", None
            newest = c.execute("SELECT n, text, negative, settings FROM prompt_versions WHERE prompt_id=? ORDER BY n DESC LIMIT 1", (prompt_id,)).fetchone()
            if newest is not None and newest["text"] == text and (newest["negative"] or None) == negative and encoded(json.loads(newest["settings"])) == stored:
                return "unchanged", newest["n"]
            n = (newest["n"] if newest is not None else 0) + 1
            c.execute(
                "INSERT INTO prompt_versions (id, prompt_id, n, text, negative, settings, note, source, chat_id, message_seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (secrets.token_hex(16), prompt_id, n, text, negative, stored, note, _check(source, SOURCES, "source"), chat_id, message_seq, now))
            c.execute("UPDATE prompts SET updated_at=? WHERE id=?", (now, prompt_id))
            self.store.reindex(c, prompt_id)
            return "added", n

    def make_current(self, prompt_id: str, n: int, *, now: str) -> tuple[str, Optional[int]]:
        """'Make this the current one': copy version `n` as a new, latest version (history is never rewritten). Returns what `add_version` does, or ('no_version', None)."""
        old = self.db.read_one("SELECT * FROM prompt_versions WHERE prompt_id=? AND n=?", (prompt_id, n))
        if old is None:
            return ("no_version", None) if self.store.get_prompt(prompt_id) is not None else ("not_found", None)
        return self.add_version(prompt_id, now=now, text=old["text"], negative=old["negative"], settings=json.loads(old["settings"]), note=f"Made current again from version {n}",
                                source="hand")

    def get_version(self, prompt_id: str, n: int) -> Optional[sqlite3.Row]:
        return self.db.read_one("SELECT * FROM prompt_versions WHERE prompt_id=? AND n=?", (prompt_id, n))

    def compare(self, prompt_id: str, a: int, b: int) -> Optional[dict[str, Any]]:
        """Two versions side by side: the words that changed in the text and in the negative prompt, and each setting (criterion 230). None when either is missing."""
        first, second = self.get_version(prompt_id, a), self.get_version(prompt_id, b)
        if first is None or second is None:
            return None
        text, negative = word_diff(first["text"], second["text"]), word_diff(first["negative"], second["negative"])
        settings = settings_diff(json.loads(first["settings"]), json.loads(second["settings"]))
        brief = lambda v: {"n": v["n"], "note": v["note"], "source": v["source"], "created_at": v["created_at"]}  # noqa: E731
        return {"a": brief(first), "b": brief(second), "text": text, "negative": negative, "settings": settings,
                "changed": changed(text) or changed(negative) or any(row["status"] != "same" for row in settings)}

    # ------------------------------------------------------------------ the prompt itself
    def update_prompt(self, prompt_id: str, *, now: str, title: Any = UNSET, for_kind: Any = UNSET, project_id: Any = UNSET, favourite: Any = UNSET,
                      tags: Any = UNSET) -> Optional[dict[str, Any]]:
        """Change a prompt's title, what it is for, its project, its star or its tags (only those given; `project_id` None takes it out of its project). Returns
        {'project_changed': bool, 'unfiled_runs': n} or None when there is no such prompt. `unfiled_runs` is how many of its earlier pictures' runs could be filed in
        the new project too (rule 4): nothing is moved here."""
        if for_kind is not UNSET:
            _check(for_kind, FOR_KINDS, "for")
        with self.db.tx() as c:
            row = c.execute("SELECT project_id FROM prompts WHERE id=?", (prompt_id,)).fetchone()
            if row is None:
                return None
            for column, value in (("title", title), ("for_kind", for_kind), ("project_id", project_id)):
                if value is not UNSET:
                    c.execute(f"UPDATE prompts SET {column}=? WHERE id=?", (value, prompt_id))
            if favourite is not UNSET:
                c.execute("UPDATE prompts SET favourite=? WHERE id=?", (1 if favourite else 0, prompt_id))
            if tags is not UNSET:
                c.execute("DELETE FROM prompt_tags WHERE prompt_id=?", (prompt_id,))
                for tag in dict.fromkeys(tags):
                    c.execute("INSERT INTO prompt_tags (prompt_id, tag) VALUES (?, ?)", (prompt_id, tag))
            c.execute("UPDATE prompts SET updated_at=? WHERE id=?", (now, prompt_id))
            self.store.reindex(c, prompt_id)
            moved = project_id is not UNSET and project_id != row["project_id"]
        return {"project_changed": moved, "unfiled_runs": len(self.unfiled_runs(prompt_id)) if moved and project_id is not None else 0}

    def unfiled_runs(self, prompt_id: str) -> list[str]:
        """The runs made from this prompt that are in no project, not in the bin, and not quick tries: the ones 'Also file its pictures' runs' would file (rule 4)."""
        rows = self.db.read_all(
            "SELECT id, options_json FROM runs WHERE prompt_id = ? AND project_id IS NULL AND deleted_at IS NULL AND status IN ('done', 'failed', 'canceled', 'queued', 'running') ORDER BY seq",
            (prompt_id,))
        return [r["id"] for r in rows if not json.loads(r["options_json"] or "{}").get("draft")]

    # ------------------------------------------------------------------ reading
    def _thumbs(self, prompt_ids: Sequence[str]) -> dict[str, list[dict[str, str]]]:
        """The latest few pictures made from each prompt (not in the bin; a quick try counts: it is a picture made from it)."""
        found: dict[str, list[dict[str, str]]] = {pid: [] for pid in prompt_ids}
        if not prompt_ids:
            return found
        marks = ",".join("?" * len(prompt_ids))
        rows = self.db.read_all(
            f"SELECT r.prompt_id AS pid, i.id AS image_id FROM runs r JOIN images i ON i.run_id = r.id AND i.kind = 'output' AND i.deleted_at IS NULL "
            f"WHERE r.prompt_id IN ({marks}) AND r.deleted_at IS NULL ORDER BY r.seq DESC, i.idx", tuple(prompt_ids))
        for r in rows:
            if len(found[r["pid"]]) < THUMBS:
                found[r["pid"]].append({"id": r["image_id"], "url": f"/api/images/{r['image_id']}/thumb"})
        return found

    def _summaries(self, rows: Sequence[sqlite3.Row], bin_days: int) -> list[dict[str, Any]]:
        ids = [r["id"] for r in rows]
        tags: dict[str, list[str]] = {pid: [] for pid in ids}
        if ids:
            for t in self.db.read_all(f"SELECT prompt_id, tag FROM prompt_tags WHERE prompt_id IN ({','.join('?' * len(ids))}) ORDER BY tag", tuple(ids)):
                tags[t["prompt_id"]].append(t["tag"])
        thumbs = self._thumbs(ids)
        return [{
            "id": r["id"], "title": r["title"], "for": r["for_kind"], "favourite": bool(r["favourite"]), "tags": tags[r["id"]], "project_id": r["project_id"],
            "version": r["latest"], "versions": r["version_count"], "text": " ".join((r["text"] or "").split())[:FIRST_WORDS], "created_at": r["created_at"],
            "updated_at": r["updated_at"], "deleted_at": r["deleted_at"], "thumbs": thumbs[r["id"]], "pictures": r["picture_runs"],
        } for r in rows]

    def list_prompts(self, *, q: Optional[str] = None, tag: Optional[str] = None, favourite: bool = False, project: Optional[str] = None, deleted: bool = False,
                     sort: str = "updated", limit: int = 300) -> list[dict[str, Any]]:
        """The saved prompts a person is looking at: the search box (words in the title, the newest text and the tags; each word matches the start of a word), a tag, the
        stars, a project ('none' for no project) and the bin, in the order asked."""
        if sort not in SORTS:
            raise StoreError(f"sort {sort!r} is not one of {', '.join(SORTS)}")
        where, args = ["p.deleted_at IS NOT NULL" if deleted else "p.deleted_at IS NULL"], []
        if favourite:
            where.append("p.favourite = 1")
        if project == "none":
            where.append("p.project_id IS NULL")
        elif project:
            where.append("p.project_id = ?")
            args.append(project)
        if tag:
            where.append("EXISTS (SELECT 1 FROM prompt_tags t WHERE t.prompt_id = p.id AND t.tag = ?)")
            args.append(tag)
        terms = search_terms(q or "")
        if terms:
            if self.db.has_fts5:
                where.append("p.id IN (SELECT prompt_id FROM prompt_search WHERE prompt_search MATCH ?)")
                args.append(" AND ".join('"' + t.replace('"', '""') + '"*' for t in terms))
            else:
                haystack = f"lower(p.title || ' ' || COALESCE({_newest('text')}, '') || ' ' || COALESCE((SELECT group_concat(tag, ' ') FROM prompt_tags t WHERE t.prompt_id = p.id), ''))"
                for term in terms:
                    where.append(f"{haystack} LIKE ? ESCAPE '\\'")
                    args.append("%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        rows = self.db.read_all(
            f"SELECT p.*, {_newest('text')} AS text, {_newest('n')} AS latest, (SELECT COUNT(*) FROM prompt_versions v WHERE v.prompt_id = p.id) AS version_count, "
            f"(SELECT COUNT(*) FROM runs r WHERE r.prompt_id = p.id AND r.deleted_at IS NULL) AS picture_runs "
            f"FROM prompts p WHERE {' AND '.join(where)} ORDER BY {_ORDER[sort]} LIMIT ?", (*args, limit))
        return self._summaries(rows, 0)

    def detail(self, prompt_id: str) -> Optional[dict[str, Any]]:
        """A prompt with every version, newest first, and under each the runs made from it (their pictures), and the chats about it."""
        rows = self.db.read_all(
            f"SELECT p.*, {_newest('text')} AS text, {_newest('n')} AS latest, (SELECT COUNT(*) FROM prompt_versions v WHERE v.prompt_id = p.id) AS version_count, "
            f"(SELECT COUNT(*) FROM runs r WHERE r.prompt_id = p.id AND r.deleted_at IS NULL) AS picture_runs FROM prompts p WHERE p.id = ?", (prompt_id,))
        if not rows:
            return None
        summary = self._summaries(rows, 0)[0]
        by_version: dict[int, list[dict[str, Any]]] = {}
        for r in self.db.read_all(
                "SELECT r.id AS run_id, r.prompt_version AS n, r.mode, r.status, r.seed, r.options_json, r.created_at, i.id AS image_id FROM runs r "
                "LEFT JOIN images i ON i.run_id = r.id AND i.kind = 'output' AND i.deleted_at IS NULL WHERE r.prompt_id = ? AND r.deleted_at IS NULL ORDER BY r.seq DESC, i.idx",
                (prompt_id,)):
            runs = by_version.setdefault(r["n"], [])
            if not runs or runs[-1]["run_id"] != r["run_id"]:
                runs.append({"run_id": r["run_id"], "mode": r["mode"], "status": r["status"], "seed": r["seed"], "created_at": r["created_at"],
                             "draft": bool(json.loads(r["options_json"] or "{}").get("draft")), "images": []})
            if r["image_id"]:
                runs[-1]["images"].append({"id": r["image_id"], "url": f"/api/images/{r['image_id']}", "thumb_url": f"/api/images/{r['image_id']}/thumb"})
        versions = [{
            "n": v["n"], "text": v["text"], "negative": v["negative"], "settings": json.loads(v["settings"]), "note": v["note"], "source": v["source"], "chat_id": v["chat_id"],
            "message_seq": v["message_seq"], "created_at": v["created_at"], "runs": by_version.get(v["n"], []),
        } for v in self.db.read_all("SELECT * FROM prompt_versions WHERE prompt_id=? ORDER BY n DESC", (prompt_id,))]
        chats = [{"id": c["id"], "title": c["title"], "updated_at": c["updated_at"]}
                 for c in self.db.read_all("SELECT id, title, updated_at FROM chats WHERE prompt_id=? AND deleted_at IS NULL ORDER BY updated_at DESC", (prompt_id,))]
        newest = versions[0] if versions else None
        return {**summary, "negative": newest["negative"] if newest else None, "settings": newest["settings"] if newest else {},
                "text": newest["text"] if newest else "", "versions": versions, "chats": chats}

    # ------------------------------------------------------------------ preferences (rule 7)
    def preferences(self) -> list[dict[str, Any]]:
        """Every standing note, in the order the person put them, each with whether it belongs to a project that is gone (listed apart as 'No longer in a project')."""
        rows = self.db.read_all(
            "SELECT f.*, (f.project_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = f.project_id)) AS orphaned FROM preferences f ORDER BY f.position, f.created_at, f.id")
        return [{"id": r["id"], "text": r["text"], "project_id": r["project_id"], "enabled": bool(r["enabled"]), "source": r["source"], "position": r["position"],
                 "created_at": r["created_at"], "orphaned": bool(r["orphaned"])} for r in rows]

    def add_preference(self, *, now: str, text: str, project_id: Optional[str] = None, source: str = "hand") -> tuple[str, Optional[str]]:
        """Add a note. Returns ('added', id), ('duplicate', id) when the person already has it (ignoring case), or ('full', None) at the most the studio keeps."""
        with self.db.tx() as c:
            same = c.execute("SELECT id FROM preferences WHERE lower(text) = lower(?) AND project_id IS ?", (text, project_id)).fetchone()
            if same is not None:
                return "duplicate", same["id"]
            if c.execute("SELECT COUNT(*) FROM preferences").fetchone()[0] >= PREFERENCES_MAX:
                return "full", None
            preference_id = secrets.token_hex(16)
            position = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM preferences").fetchone()[0]
            c.execute("INSERT INTO preferences (id, text, project_id, enabled, source, position, created_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
                      (preference_id, text, project_id, source, position, now))
            return "added", preference_id

    def update_preference(self, preference_id: str, *, text: Any = UNSET, enabled: Any = UNSET, project_id: Any = UNSET) -> str:
        """Edit a note, switch it on or off, or change where it applies. Returns 'updated', 'not_found', 'duplicate' or 'no_project' (a note of a project that is gone cannot
        be switched on until it is given a place again)."""
        with self.db.tx() as c:
            row = c.execute(
                "SELECT f.*, (f.project_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = f.project_id)) AS orphaned FROM preferences f WHERE f.id=?",
                (preference_id,)).fetchone()
            if row is None:
                return "not_found"
            new_text = row["text"] if text is UNSET else text
            new_project = row["project_id"] if project_id is UNSET else project_id
            if enabled is True and row["orphaned"] and project_id is UNSET:
                return "no_project"
            if c.execute("SELECT 1 FROM preferences WHERE lower(text) = lower(?) AND project_id IS ? AND id != ?", (new_text, new_project, preference_id)).fetchone():
                return "duplicate"
            c.execute("UPDATE preferences SET text=?, project_id=? WHERE id=?", (new_text, new_project, preference_id))
            if enabled is not UNSET:
                c.execute("UPDATE preferences SET enabled=? WHERE id=?", (1 if enabled else 0, preference_id))
            return "updated"

    def delete_preference(self, preference_id: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM preferences WHERE id=?", (preference_id,)).rowcount == 1
