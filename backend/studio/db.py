"""SQLite storage for runs and images (DESIGN.md §8).

The API process is the only writer. All access goes through one connection guarded
by a lock, which is plenty for a single-GPU, few-users app.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_VERSION = 1

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
"""

INTERRUPTED_MESSAGE = "Interrupted by a server restart."


class DatabaseError(Exception):
    pass


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
            self._conn.executescript(_SCHEMA)
            self._check_version()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _check_version(self) -> None:
        with self.tx() as c:
            row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                c.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row["value"]) > SCHEMA_VERSION:
                raise DatabaseError(
                    f"The database was created by a newer version of the studio (schema {row['value']}, "
                    f"this build understands {SCHEMA_VERSION}). Upgrade the studio or use another data directory."
                )

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
        """Insert a queued run unless `cap` runs are already waiting. Atomic."""
        with self.tx() as c:
            waiting = c.execute("SELECT COUNT(*) FROM runs WHERE status='queued'").fetchone()[0]
            if waiting >= cap:
                return False
            columns = ", ".join(run)
            placeholders = ", ".join("?" for _ in run)
            c.execute(f"INSERT INTO runs ({columns}) VALUES ({placeholders})", tuple(run.values()))
            return True

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
        return [r["id"] for r in self._all("SELECT id FROM runs WHERE status='queued' ORDER BY seq")]

    def next_queued(self) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM runs WHERE status='queued' ORDER BY seq LIMIT 1")

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
