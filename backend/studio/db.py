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
from typing import Any, Iterator, Mapping, Optional, Sequence

from .projects import name_key as project_name_key
from .runfilter import KINDS, RunFilter

# 5: the projects table and runs.project_id, for project folders (DESIGN.md §32.4); 4: runs have deleted_at and restored_at, for the bin (§30);
# 3: mode 'music', lyrics, tracks (§26.5)
SCHEMA_VERSION = 5
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
    mode             TEXT NOT NULL CHECK (mode IN ('generate','edit','music')),
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
    options_json     TEXT NOT NULL,
    lyrics           TEXT,
    deleted_at       TEXT,
    restored_at      TEXT,
    project_id       TEXT
);
-- Project folders (DESIGN.md §32.4): a name that runs are filed under. A project is a label in this table, not a folder on the disk, so filing a
-- run moves no file. `name_key` is the name with its case folded away (see projects.py) and is unique, so "Logo" and "logo" are one project.
-- `runs.project_id` is a plain column and not a foreign key: deleting a project clears it on its runs in one transaction (`delete_project`), and
-- a column added to an existing table by ALTER cannot carry the same constraint as one made with it, so the migrated table would differ.
CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    name_key   TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
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
-- The tracks of a music run (DESIGN.md §26.5): one WAV file each, under <data>/audio/<run>/.
CREATE TABLE IF NOT EXISTS tracks (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    seed        INTEGER NOT NULL,
    seconds     REAL NOT NULL,
    sample_rate INTEGER NOT NULL,
    channels    INTEGER NOT NULL,
    bytes       INTEGER NOT NULL,
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracks_run ON tracks(run_id, idx);
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

# Schema 3 changes the CHECK on runs.mode, which SQLite cannot alter in place, so an older database's table is rebuilt
# (SQLite's documented way: a new table, copy, drop the old, rename). The old columns keep their values; lyrics is NULL.
_RUNS_COLUMNS_BEFORE_3 = (
    "seq, id, created_at, started_at, finished_at, status, mode, prompt, effective_prompt, negative_prompt, "
    "transparent, width, height, steps, cfg_scale, seed, num_images, model_id, input_image_id, error_message, "
    "error_hint, pinned, options_json"
)

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
            self._conn.executescript(_SCHEMA)  # creates what is missing: tables and indexes of a newer schema
            if previous is not None and previous < 3:
                self._rebuild_runs_for_music()
            self._add_bin_columns()
            self._add_project_column()
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

    def _rebuild_runs_for_music(self) -> None:
        """Schema 3: let `runs` hold mode 'music' (and a lyrics column). The new table is made from this module's own
        definition, so a database migrated here and one made fresh are the same."""
        definition = _SCHEMA[_SCHEMA.index("CREATE TABLE IF NOT EXISTS runs ("):]
        definition = definition[:definition.index(");") + 2].replace("CREATE TABLE IF NOT EXISTS runs (", "CREATE TABLE runs_new (", 1)
        self._conn.execute("PRAGMA foreign_keys=OFF")  # not allowed inside a transaction, and dropping runs must not cascade
        try:
            with self.tx() as c:
                c.execute(definition)
                c.execute(f"INSERT INTO runs_new ({_RUNS_COLUMNS_BEFORE_3}) SELECT {_RUNS_COLUMNS_BEFORE_3} FROM runs")
                c.execute("DROP TABLE runs")
                c.execute("ALTER TABLE runs_new RENAME TO runs")
                c.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, seq)")
                broken = c.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise DatabaseError(f"the migration would leave {len(broken)} row(s) pointing at runs that are gone")
        finally:
            self._conn.execute("PRAGMA foreign_keys=ON")
        log.info("the runs table was rebuilt for music (schema 3)")

    def _add_bin_columns(self) -> None:
        """Schema 4: the columns of the bin (DESIGN.md §30.5), added to a table that lacks them; then the index on them, which cannot be
        made before the column exists, so it is not in `_SCHEMA`."""
        # the columns the table has already: a database from before schema 4 has neither, a new one has both
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}
        for column in ("deleted_at", "restored_at"):
            if column not in have:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} TEXT")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_deleted ON runs(deleted_at)")

    def _add_project_column(self) -> None:
        """Schema 5: the column that files a run in a project (DESIGN.md §32.4), added to a table that lacks it; then its index (which cannot
        be made before the column exists, so it is not in `_SCHEMA`). The `projects` table itself is made by `_SCHEMA`."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}
        if "project_id" not in have:
            self._conn.execute("ALTER TABLE runs ADD COLUMN project_id TEXT")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id)")

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

    def list_runs(self, limit: int, before_id: Optional[str] = None, run_filter: Optional[RunFilter] = None) -> tuple[list[sqlite3.Row], bool]:
        """Newest first, only the runs `run_filter` lets through (DESIGN.md §29.5). Returns (rows, has_more). Raises KeyError for an
        unknown cursor. The cursor may be a run the filter does not show (one un-kept since the page was loaded): it only says where to start."""
        # The filter's conditions come first. Each is a fixed SQL fragment with a placeholder; its values are bound, never pasted into the
        # text, so nothing the page sent can become SQL.
        clauses, values = (run_filter or RunFilter()).conditions()
        # Paging: `before_id` is the last run of the page before; this page starts just past it, in `seq` order (the order runs were made)
        if before_id is not None:
            cursor = self._one("SELECT seq FROM runs WHERE id=?", (before_id,))
            if cursor is None:
                raise KeyError(before_id)
            clauses.append("seq < ?")
            values.append(cursor["seq"])
        # join the conditions with AND (with none there is no WHERE at all)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # Ask for one row more than a page holds: if it comes, an older page exists (`has_more`), and the extra row is dropped from this page
        rows = self._all(f"SELECT * FROM runs{where} ORDER BY seq DESC LIMIT ?", (*values, limit + 1))
        return rows[:limit], len(rows) > limit

    def run_counts(self, counted: Mapping[str, RunFilter]) -> dict[str, dict[str, int]]:
        """How many runs each tab holds, for each named filter: {kind: {name: n}} (DESIGN.md §29.6)."""
        counts: dict[str, dict[str, int]] = {}
        # one count per tab (the run modes the tab shows) and per named filter
        for kind, modes in KINDS.items():
            counts[kind] = {}
            for name, run_filter in counted.items():
                clauses, values = run_filter.conditions()
                # restrict the count to the tab's modes: one `?` placeholder for each
                clauses.append(f"mode IN ({', '.join('?' for _ in modes)})")
                row = self._one(f"SELECT COUNT(*) AS n FROM runs WHERE {' AND '.join(clauses)}", (*values, *modes))
                counts[kind][name] = row["n"]
        return counts

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

    def set_pinned(self, run_id: str, pinned: bool) -> str:
        """Keep (or stop keeping) a run. Returns 'ok' (also when it already was so), 'not_found', or 'locked': a run that is in a project stays
        kept (DESIGN.md §32.3), so stopping to keep it is refused. Checked and written in one transaction, so a run filed at the same moment
        is never left un-kept."""
        with self.tx() as c:
            row = c.execute("SELECT project_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if not pinned and row["project_id"] is not None:
                return "locked"
            c.execute("UPDATE runs SET pinned=? WHERE id=?", (int(pinned), run_id))
            return "ok"

    # An expiry counts from the later of when a run was made and when it was last restored (DESIGN.md §30.4).
    # Which runs the clean-up may take: not kept, not in a project, not already in the bin, finished (never one that is queued or running), and
    # old enough. Old enough counts from the later of when the run was made and when it was last restored, so a restore starts a fresh clock.
    # "Not in a project" is said here although a filed run is always kept (§32.3 item 4): it holds even if something clears the Keep flag by hand.
    _EXPIRED = ("pinned=0 AND project_id IS NULL AND deleted_at IS NULL AND status IN ('done','failed','canceled') "
                "AND COALESCE(restored_at, created_at) < ?")

    @staticmethod
    # the one place that removes a run and what hangs off it (its pictures and tracks); every delete goes through here, so none forgets a table
    def _delete_rows(c: sqlite3.Connection, ids: Sequence[str]) -> None:
        for run_id in ids:
            c.execute("DELETE FROM images WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM tracks WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM runs WHERE id=?", (run_id,))

    def delete_expired(self, cutoff: str, limit: int) -> list[str]:
        """Delete up to `limit` finished runs that expired before `cutoff` and are not kept, and return their
        ids. Selecting and deleting happen in one transaction, so a run kept at the same moment is never
        caught by a sweep that has already chosen it. (Used when there is no bin: STUDIO_BIN_DAYS=0.)"""
        with self.tx() as c:
            ids = [r["id"] for r in c.execute(f"SELECT id FROM runs WHERE {self._EXPIRED} ORDER BY seq LIMIT ?", (cutoff, limit)).fetchall()]
            self._delete_rows(c, ids)
            return ids

    def expire_to_bin(self, cutoff: str, now: str, limit: int) -> list[str]:
        """Move up to `limit` expired runs to the bin instead of deleting them (DESIGN.md §30.2), and return their ids; one
        transaction, for the same reason as `delete_expired`."""
        with self.tx() as c:
            ids = [r["id"] for r in c.execute(f"SELECT id FROM runs WHERE {self._EXPIRED} ORDER BY seq LIMIT ?", (cutoff, limit)).fetchall()]
            for run_id in ids:
                # mark each as binned, all with the same moment; the files stay where they are
                c.execute("UPDATE runs SET deleted_at=? WHERE id=?", (now, run_id))
            return ids

    def bin_run(self, run_id: str, now: str) -> str:
        """Move a finished run to the bin. Returns 'binned' (also when it already was), 'not_found' or 'not_finished' (queued or running)."""
        with self.tx() as c:
            row = c.execute("SELECT status, deleted_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["status"] not in ("done", "failed", "canceled"):
                return "not_finished"
            # binning a run that is already in the bin is not an error and does not move its date: the first time counts
            if row["deleted_at"] is None:
                c.execute("UPDATE runs SET deleted_at=? WHERE id=?", (now, run_id))
            return "binned"

    def restore_run(self, run_id: str, now: str) -> str:
        """Take a run out of the bin, with a fresh expiry clock (DESIGN.md §30.4). Returns 'restored', 'not_found' or 'not_in_bin'."""
        with self.tx() as c:
            row = c.execute("SELECT deleted_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["deleted_at"] is None:
                return "not_in_bin"
            # out of the bin, and the expiry clock starts again from now (`restored_at` wins over `created_at` in `_EXPIRED`)
            c.execute("UPDATE runs SET deleted_at=NULL, restored_at=? WHERE id=?", (now, run_id))
            return "restored"

    def purge_bin(self, cutoff: Optional[str], limit: int) -> list[str]:
        """Delete for good up to `limit` runs that went into the bin before `cutoff` (all of them when `cutoff` is None), and return their ids."""
        with self.tx() as c:
            # no cutoff means the whole bin (Empty bin, or no bin is configured); otherwise only what went in before the cutoff
            if cutoff is None:
                rows = c.execute("SELECT id FROM runs WHERE deleted_at IS NOT NULL ORDER BY seq LIMIT ?", (limit,)).fetchall()
            else:
                rows = c.execute("SELECT id FROM runs WHERE deleted_at IS NOT NULL AND deleted_at < ? ORDER BY seq LIMIT ?", (cutoff, limit)).fetchall()
            ids = [r["id"] for r in rows]
            self._delete_rows(c, ids)
            return ids

    def delete_run(self, run_id: str) -> str:
        """Returns 'deleted', 'not_found' or 'running' (refused)."""
        with self.tx() as c:
            row = c.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["status"] == "running":
                return "running"
            self._delete_rows(c, [run_id])
            return "deleted"

    # ---------------------------------------------------------------- projects (DESIGN.md §32)
    def list_projects(self, project_id: Optional[str] = None) -> list[dict[str, Any]]:
        """The projects, A to Z (ignoring case), each with how many runs of each tab it holds: {id, name, created_at, counts: {image, music}}.
        The counts are of runs in the history; a run in the bin is not counted (it is not on show). With `project_id`, only that project (a
        list of one, or empty)."""
        with self._lock:
            where, args = ("WHERE id=?", (project_id,)) if project_id is not None else ("", ())
            projects = self._all(f"SELECT id, name, created_at FROM projects {where} ORDER BY name_key, id", args)
            found = {p["id"]: {"id": p["id"], "name": p["name"], "created_at": p["created_at"], "counts": {kind: 0 for kind in KINDS}} for p in projects}
            # one grouped query for all the counts: runs by project and mode, then each mode is added to its tab
            rows = self._all("SELECT project_id, mode, COUNT(*) AS n FROM runs WHERE project_id IS NOT NULL AND deleted_at IS NULL GROUP BY project_id, mode")
        for row in rows:
            project = found.get(row["project_id"])
            if project is None:
                continue  # a run filed in a project that is not in the list asked for
            for kind, modes in KINDS.items():
                if row["mode"] in modes:
                    project["counts"][kind] += row["n"]
        return list(found.values())

    def create_project(self, project_id: str, name: str, now: str) -> str:
        """Make a project. `name` is already normalised (`projects.normalize_name`). Returns 'created', or 'taken' when another project has the
        same name ignoring case (the unique `name_key` refuses it, so two requests at once cannot both succeed)."""
        try:
            with self.tx() as c:
                c.execute("INSERT INTO projects (id, name, name_key, created_at) VALUES (?, ?, ?, ?)", (project_id, name, project_name_key(name), now))
            return "created"
        except sqlite3.IntegrityError:
            return "taken"

    def rename_project(self, project_id: str, name: str) -> str:
        """Rename a project. Returns 'renamed', 'not_found' or 'taken'. A project may be renamed to another spelling of its own name (*logo*
        to *Logo*): its own row is not a clash with itself."""
        try:
            with self.tx() as c:
                cur = c.execute("UPDATE projects SET name=?, name_key=? WHERE id=?", (name, project_name_key(name), project_id))
                return "renamed" if cur.rowcount == 1 else "not_found"
        except sqlite3.IntegrityError:
            return "taken"

    def delete_project(self, project_id: str) -> Optional[list[str]]:
        """Delete a project and take its runs out of it, in one transaction; returns the ids of the runs that were in it, or None if there is
        no such project. No run is deleted or un-kept (DESIGN.md §32.3 item 6): runs in the bin lose the project too, so that a restore
        cannot put one back into a project that is gone."""
        with self.tx() as c:
            if c.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                return None
            ids = [r["id"] for r in c.execute("SELECT id FROM runs WHERE project_id=? ORDER BY seq", (project_id,)).fetchall()]
            c.execute("UPDATE runs SET project_id=NULL WHERE project_id=?", (project_id,))
            c.execute("DELETE FROM projects WHERE id=?", (project_id,))
            return ids

    def file_run(self, run_id: str, project_id: str) -> str:
        """File a run in a project, and keep it, in one transaction (DESIGN.md §32.3 item 1); the same call moves a run that is filed already.
        Returns 'filed', 'not_found' (no such run), 'no_project' (no such project) or 'in_bin' (a run in the bin cannot be filed)."""
        with self.tx() as c:
            run = c.execute("SELECT deleted_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                return "not_found"
            if c.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                return "no_project"
            if run["deleted_at"] is not None:
                return "in_bin"
            c.execute("UPDATE runs SET project_id=?, pinned=1 WHERE id=?", (project_id, run_id))
            return "filed"

    def unfile_run(self, run_id: str, keep: bool = True) -> str:
        """Take a run out of its project. It stays kept unless `keep` is False (which is what Undo of a filing asks for: the run goes back to
        exactly what it was, DESIGN.md §32.3 item 3). Returns 'unfiled', 'not_found' or 'not_in_project'."""
        with self.tx() as c:
            row = c.execute("SELECT project_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["project_id"] is None:
                return "not_in_project"
            c.execute("UPDATE runs SET project_id=NULL" + ("" if keep else ", pinned=0") + " WHERE id=?", (run_id,))
            return "unfiled"

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

    # ---------------------------------------------------------------- tracks (music)
    def add_track(self, track: dict[str, Any]) -> None:
        columns = ", ".join(track)
        placeholders = ", ".join("?" for _ in track)
        with self._lock:
            self._conn.execute(f"INSERT INTO tracks ({columns}) VALUES ({placeholders})", tuple(track.values()))

    def get_track(self, track_id: str) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM tracks WHERE id=?", (track_id,))

    def tracks_for_runs(self, run_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
        result: dict[str, list[sqlite3.Row]] = {rid: [] for rid in run_ids}
        if not run_ids:
            return result
        placeholders = ", ".join("?" for _ in run_ids)
        for row in self._all(f"SELECT * FROM tracks WHERE run_id IN ({placeholders}) ORDER BY run_id, idx", tuple(run_ids)):
            result[row["run_id"]].append(row)
        return result

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

    def input_position(self, image_id: str) -> Optional[int]:
        """The place ("image 1") an image has among the inputs of the run that owns it, or None if it is not one."""
        row = self._one("SELECT position FROM run_inputs WHERE image_id=?", (image_id,))
        return None if row is None else int(row["position"])

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
