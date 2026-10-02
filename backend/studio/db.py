"""SQLite storage for runs and images (DESIGN.md §8, §21.7).

The API process is the only writer. All access goes through one connection guarded
by a lock, which is plenty for a single-GPU, few-users app.

Schema versions: 1 = the first release (runs, images). 2 = adds run_inputs (the images an edit was
given, in order). The step from 1 to 2 only adds a table, but a database that has been opened by 2 is
refused by 1, so before migrating, the database is copied to studio.sqlite.before-schema-2: a way back.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

SCHEMA_VERSION = 2
log = logging.getLogger("studio.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    id               TEXT NOT NULL UNIQUE,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    status           TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','canceled')),
    mode             TEXT NOT NULL CHECK (mode IN ('generate','edit')),
    prompt           TEXT NOT NULL,
    effective_prompt TEXT NOT NULL,
    negative_prompt  TEXT,
    transparent      INTEGER NOT NULL DEFAULT 0,
    width            INTEGER,
    height           INTEGER,
    steps            INTEGER NOT NULL,
    cfg_scale        REAL,
    seed             INTEGER NOT NULL,
    num_images       INTEGER NOT NULL,
    model_id         TEXT NOT NULL,
    input_image_id   TEXT,
    error_message    TEXT,
    error_hint       TEXT,
    pinned           INTEGER NOT NULL DEFAULT 0,
    options_json     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS images (
    id          TEXT PRIMARY KEY,
    run_id      TEXT REFERENCES runs(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('output','input')),
    idx         INTEGER,
    seed        INTEGER,
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    has_alpha   INTEGER NOT NULL,
    bytes       INTEGER NOT NULL,
    path        TEXT NOT NULL,
    thumb_path  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, seq);
CREATE INDEX IF NOT EXISTS idx_images_run ON images(run_id, idx);
CREATE INDEX IF NOT EXISTS idx_images_staged ON images(kind, run_id, created_at);
-- The images an edit was given, in the order the model sees them (position 1 is "image 1"). A run owns its
-- inputs: they are copies, and go with the run. An input image row has run_id set once a run claims it; before
-- that it is a staged upload (run_id NULL).
CREATE TABLE IF NOT EXISTS run_inputs (
    run_id    TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL CHECK (position >= 1),
    image_id  TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    role      TEXT NOT NULL DEFAULT 'reference' CHECK (role IN ('reference','marked','mask')),
    PRIMARY KEY (run_id, position)
);
"""

INTERRUPTED_MESSAGE = "Interrupted by a server restart."


class DatabaseError(Exception):
    pass


class _AlreadyClaimed(Exception):
    """A staged upload was taken or deleted between looking at it and claiming it."""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            previous = self._stored_version()  # None: a new database
            if previous is not None and previous > SCHEMA_VERSION:
                raise DatabaseError(
                    f"The database was created by a newer version of the studio (schema {previous}, "
                    f"this build understands {SCHEMA_VERSION}). Upgrade the studio or use another data directory."
                )
            if previous is not None and previous < SCHEMA_VERSION:
                self._copy_before_migration(path, previous)
            self._conn.executescript(_SCHEMA)  # every step so far only adds tables and indexes, so this is the migration
            self._record_version(previous)
            self._queue_order = self._choose_queue_order()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _choose_queue_order(self) -> str:
        """Drafts run ahead of waiting full-size runs (DESIGN.md §22.2), then in order of arrival. The flag lives in
        options_json, which SQLite's JSON functions can read; if this SQLite was built without them, fall back to
        plain arrival order rather than fail."""
        try:
            self._conn.execute("SELECT json_extract('{\"draft\": true}', '$.draft')").fetchone()
        except sqlite3.OperationalError:
            log.warning("this SQLite has no JSON functions: drafts will not jump the queue")
            return "seq"
        return "COALESCE(json_extract(options_json, '$.draft'), 0) DESC, seq"

    def _stored_version(self) -> Optional[int]:
        has_meta = self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        if has_meta is None:
            return None
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row is not None else None

    def _copy_before_migration(self, path: Path, previous: int) -> None:
        """Keep a copy of the database as the older version left it. Made with SQLite's own backup, so it is
        consistent even though the database is in WAL mode. An existing copy is never replaced: it is the
        oldest, and a failed earlier attempt must not overwrite the pristine one."""
        target = path.with_name(f"{path.name}.before-schema-{SCHEMA_VERSION}")
        if target.exists():
            log.info("keeping the existing %s", target.name)
            return
        partial = target.with_name(target.name + ".part")
        partial.unlink(missing_ok=True)
        copy = sqlite3.connect(str(partial))
        try:
            self._conn.backup(copy)
        finally:
            copy.close()
        os.replace(partial, target)
        log.warning(
            "upgrading the database from schema %d to %d; a copy of the old one is in %s "
            "(an older studio can only be run against that copy)", previous, SCHEMA_VERSION, target)

    def _record_version(self, previous: Optional[int]) -> None:
        with self.tx() as c:
            if previous is None:
                c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif previous < SCHEMA_VERSION:
                c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _one(self, sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # ------------------------------------------------------------------ runs
    def insert_run_if_capacity(self, run: dict[str, Any], cap: int) -> bool:
        """Insert a queued run without inputs unless `cap` runs are already waiting. Atomic."""
        return self.insert_run(run, cap) == "ok"

    def insert_run(
        self, run: dict[str, Any], cap: int, inputs: Sequence[dict[str, Any]] = (), claims: Sequence[str] = ()
    ) -> str:
        """Insert a queued run together with its inputs, in one transaction. `inputs` are image rows (with
        run_id, position and role) the run now owns; `claims` are the staged uploads they were made from, which
        are consumed. Returns "ok", "full" (`cap` runs already waiting), or "claimed:<id>" when a staged upload
        was already taken, or deleted, by someone else; nothing is changed unless it is "ok"."""
        try:
            with self.tx() as c:
                waiting = c.execute("SELECT COUNT(*) FROM runs WHERE status='queued'").fetchone()[0]
                if waiting >= cap:
                    return "full"
                columns = ", ".join(run)
                c.execute(f"INSERT INTO runs ({columns}) VALUES ({', '.join('?' for _ in run)})", tuple(run.values()))
                for item in inputs:
                    image = {k: v for k, v in item.items() if k not in ("position", "role")}
                    c.execute(f"INSERT INTO images ({', '.join(image)}) VALUES ({', '.join('?' for _ in image)})",
                              tuple(image.values()))
                    c.execute("INSERT INTO run_inputs (run_id, position, image_id, role) VALUES (?, ?, ?, ?)",
                              (run["id"], item["position"], image["id"], item["role"]))
                for upload_id in dict.fromkeys(claims):  # the same upload used twice is claimed once
                    taken = c.execute("DELETE FROM images WHERE id=? AND run_id IS NULL AND kind='input'", (upload_id,))
                    if taken.rowcount != 1:
                        raise _AlreadyClaimed(upload_id)  # tx() rolls everything back
                return "ok"
        except _AlreadyClaimed as gone:
            return f"claimed:{gone.args[0]}"

    def get_run(self, run_id: str) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM runs WHERE id=?", (run_id,))

    def list_runs(self, limit: int, before_id: Optional[str] = None) -> tuple[list[sqlite3.Row], bool]:
        """Newest first. Returns (rows, has_more). Raises KeyError for an unknown cursor."""
        if before_id is not None:
            cursor = self._one("SELECT seq FROM runs WHERE id=?", (before_id,))
            if cursor is None:
                raise KeyError(before_id)
            rows = self._all("SELECT * FROM runs WHERE seq < ? ORDER BY seq DESC LIMIT ?", (cursor["seq"], limit + 1))
        else:
            rows = self._all("SELECT * FROM runs ORDER BY seq DESC LIMIT ?", (limit + 1,))
        return rows[:limit], len(rows) > limit

    def queued_ids(self) -> list[str]:
        return [r["id"] for r in self._all(f"SELECT id FROM runs WHERE status='queued' ORDER BY {self._queue_order}")]

    def next_queued(self) -> Optional[sqlite3.Row]:
        return self._one(f"SELECT * FROM runs WHERE status='queued' ORDER BY {self._queue_order} LIMIT 1")

    def mark_running(self, run_id: str, now: str) -> bool:
        """queued -> running. False if the run is gone or no longer queued."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status='running', started_at=? WHERE id=? AND status='queued'", (now, run_id)
            )
            return cur.rowcount == 1

    def cancel_queued(self, run_id: str, now: str) -> bool:
        """queued -> canceled. False if the run is gone or has already started."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status='canceled', finished_at=? WHERE id=? AND status='queued'", (now, run_id)
            )
            return cur.rowcount == 1

    def finish_run(
        self, run_id: str, status: str, now: str, error: Optional[str] = None, hint: Optional[str] = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status=?, finished_at=?, error_message=?, error_hint=? "
                "WHERE id=? AND status='running'",
                (status, now, error, hint, run_id),
            )

    def set_pinned(self, run_id: str, pinned: bool) -> bool:
        """Keep (or stop keeping) a run. False if there is no such run."""
        with self._lock:
            cur = self._conn.execute("UPDATE runs SET pinned=? WHERE id=?", (int(pinned), run_id))
            return cur.rowcount == 1

    def delete_expired(self, cutoff: str, limit: int) -> list[str]:
        """Delete up to `limit` finished runs created before `cutoff` that are not kept, and return their
        ids. Selecting and deleting happen in one transaction, so a run kept at the same moment is never
        caught by a sweep that has already chosen it."""
        with self.tx() as c:
            ids = [
                r["id"] for r in c.execute(
                    "SELECT id FROM runs WHERE pinned=0 AND status IN ('done','failed','canceled') "
                    "AND created_at < ? ORDER BY seq LIMIT ?", (cutoff, limit)).fetchall()
            ]
            for run_id in ids:
                c.execute("DELETE FROM images WHERE run_id=?", (run_id,))
                c.execute("DELETE FROM runs WHERE id=?", (run_id,))
            return ids

    def delete_run(self, run_id: str) -> str:
        """Returns 'deleted', 'not_found' or 'running' (refused)."""
        with self.tx() as c:
            row = c.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["status"] == "running":
                return "running"
            c.execute("DELETE FROM images WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM runs WHERE id=?", (run_id,))
            return "deleted"

    def recover_interrupted(self, now: str) -> list[str]:
        """Runs left 'running' by a previous process become failed (their finished images are kept)."""
        with self.tx() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM runs WHERE status='running'").fetchall()]
            c.execute(
                "UPDATE runs SET status='failed', finished_at=?, error_message=? WHERE status='running'",
                (now, INTERRUPTED_MESSAGE),
            )
            return ids

    def count_queued(self) -> int:
        return self._one("SELECT COUNT(*) AS n FROM runs WHERE status='queued'")["n"]

    # ---------------------------------------------------------------- images
    def add_image(self, image: dict[str, Any]) -> None:
        columns = ", ".join(image)
        placeholders = ", ".join("?" for _ in image)
        with self._lock:
            self._conn.execute(f"INSERT INTO images ({columns}) VALUES ({placeholders})", tuple(image.values()))

    def get_image(self, image_id: str) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM images WHERE id=?", (image_id,))

    # ------------------------------------------------- inputs and staged uploads
    def inputs_for_runs(self, run_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
        """The images each run was given, in position order."""
        result: dict[str, list[sqlite3.Row]] = {rid: [] for rid in run_ids}
        if not run_ids:
            return result
        placeholders = ", ".join("?" for _ in run_ids)
        rows = self._all(
            "SELECT ri.run_id AS run_id, ri.position AS position, ri.role AS role, i.id AS id, i.width AS width, "
            "i.height AS height, i.has_alpha AS has_alpha, i.bytes AS bytes, i.path AS path, i.thumb_path AS thumb_path "
            f"FROM run_inputs ri JOIN images i ON i.id = ri.image_id WHERE ri.run_id IN ({placeholders}) "
            "ORDER BY ri.run_id, ri.position",
            tuple(run_ids),
        )
        for row in rows:
            result[row["run_id"]].append(row)
        return result

    def get_staged(self, upload_id: str) -> Optional[sqlite3.Row]:
        """An uploaded image that no run has claimed yet."""
        return self._one("SELECT * FROM images WHERE id=? AND kind='input' AND run_id IS NULL", (upload_id,))

    def delete_staged(self, upload_id: str) -> Optional[sqlite3.Row]:
        """Remove a staged upload; returns its row (so the files can follow), or None if there is no such upload."""
        with self.tx() as c:
            row = c.execute("SELECT * FROM images WHERE id=? AND kind='input' AND run_id IS NULL", (upload_id,)).fetchone()
            if row is not None:
                c.execute("DELETE FROM images WHERE id=?", (upload_id,))
            return row

    def delete_expired_staged(self, cutoff: str, limit: int) -> list[sqlite3.Row]:
        """Remove staged uploads created before `cutoff`; returns their rows."""
        with self.tx() as c:
            rows = c.execute(
                "SELECT * FROM images WHERE kind='input' AND run_id IS NULL AND created_at < ? ORDER BY created_at LIMIT ?",
                (cutoff, limit),
            ).fetchall()
            for row in rows:
                c.execute("DELETE FROM images WHERE id=?", (row["id"],))
            return rows

    def known_ids(self) -> tuple[set[str], set[str]]:
        """(every run id, every staged upload id): what folders on disk may legitimately belong to."""
        runs = {r["id"] for r in self._all("SELECT id FROM runs")}
        staged = {r["id"] for r in self._all("SELECT id FROM images WHERE kind='input' AND run_id IS NULL")}
        return runs, staged

    def images_for_runs(self, run_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
        result: dict[str, list[sqlite3.Row]] = {rid: [] for rid in run_ids}
        if not run_ids:
            return result
        placeholders = ", ".join("?" for _ in run_ids)
        rows = self._all(
            f"SELECT * FROM images WHERE kind='output' AND run_id IN ({placeholders}) ORDER BY run_id, idx",
            tuple(run_ids),
        )
        for row in rows:
            result[row["run_id"]].append(row)
        return result
