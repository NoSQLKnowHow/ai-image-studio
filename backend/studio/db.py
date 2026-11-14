"""SQLite storage for runs and images (DESIGN.md §8, §21.7).

The API process is the only writer. All access goes through one connection guarded
by a lock, which is plenty for a single-GPU, few-users app.

Schema versions: 1 = the first release (runs, images). 2 = adds run_inputs (the images an edit was
given, in order). The step from 1 to 2 only adds a table, but a database that has been opened by 2 is
refused by 1, so before migrating, the database is copied to studio.sqlite.before-schema-2: a way back.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from .builder.schema import BUILDER_SCHEMA, PROMPT_SEARCH_SQL
from .speech.schema import SPEECH_SCHEMA
from .projects import name_key as project_name_key
from .runfilter import KINDS, RunFilter
from .runsearch import FACETS_LISTED, RUN_ANNOTATION_SCHEMA, RUN_HEIGHT, RUN_SEARCH_SQL, RUN_WIDTH, TAGS_LISTED, index_fields

# 13: the tables of tags, notes and the word index over runs (run_tags, run_notes, run_search; DESIGN.md §45.7), added to the end, so no run is touched; 12: speech_pieces.speaker and the speech_pronunciations table (§44.6);
# 11: runs.variation_json and runs.family_id (§43.4); 10: mode 'speech', runs.queue_key and runs.hidden, and the Speech tab's tables (§38.8); 9: the Prompt builder's tables and runs.prompt_id, runs.prompt_version (§37.4); 8: vectors.deleted_at, for a single version of a Vector run in the bin (DESIGN.md §35.4); 7: mode 'vector' and the vectors table (§31.12.6); 6: images.deleted_at and tracks.deleted_at, for a single picture in the bin (§33.3);
# 5: the projects table and runs.project_id, for project folders (§32.4); 4: runs have deleted_at and restored_at, for the bin (§30);
# 3: mode 'music', lyrics, tracks (§26.5)
SCHEMA_VERSION = 13
log = logging.getLogger("studio.db")

# A run that is no longer queued or running. Only such a run can have a single picture sent to the bin (DESIGN.md §33.2 item 1).
_FINISHED = ("done", "failed", "canceled")
# The three kinds of thing a run holds that can be sent to the bin one at a time (a picture, a track, a version of a Vector run), and the table each is in
_ITEM_TABLES = {"image": "images", "track": "tracks", "vector": "vectors"}

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
    mode             TEXT NOT NULL CHECK (mode IN ('generate','edit','music','vector','speech')),
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
    project_id       TEXT,
    prompt_id        TEXT,
    prompt_version   INTEGER,
    queue_key        REAL,
    hidden           INTEGER NOT NULL DEFAULT 0,
    variation_json   TEXT,
    family_id        TEXT
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
    created_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, seq);
-- `images.deleted_at`, `tracks.deleted_at` and `vectors.deleted_at` (DESIGN.md §33.3, §35.4) are null, or the moment a single picture, track or version was sent to the bin. It stays in
-- the table, and its files stay on the disk, until its days are over; the run's own `deleted_at` is a different thing (the whole run in the bin).
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
    created_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tracks_run ON tracks(run_id, idx);
-- The versions of a vector run (DESIGN.md §31.12.6): one row for each drawing that the clean-up turned into an SVG. The three paths are relative to the
-- data folder (<data>/vector/<run>/<idx>.svg, <idx>-layers.zip, <idx>.png). `check_json` is the clean-up's check as it made it (the page shows it), and
-- `colours` and `layers` are copies of two of its numbers so that a list needs no parsing. `deleted_at` (schema 8, DESIGN.md §35.4) is null, or the moment
-- this one version was sent to the bin; it is the LAST column, so that a database upgraded from schema 7 (where it is added with ALTER TABLE) has the same
-- columns in the same order as a new one. The drawing (<idx>-source.png) has no column: its file is found from the run and the idx.
CREATE TABLE IF NOT EXISTS vectors (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    seed        INTEGER NOT NULL,
    width_mm    REAL NOT NULL,
    height_mm   REAL NOT NULL,
    colours     INTEGER NOT NULL,
    layers      INTEGER NOT NULL,
    bytes       INTEGER NOT NULL,
    check_json  TEXT NOT NULL,
    svg_path    TEXT NOT NULL,
    zip_path    TEXT NOT NULL,
    png_path    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_vectors_run ON vectors(run_id, idx);
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
""" + BUILDER_SCHEMA + SPEECH_SCHEMA + RUN_ANNOTATION_SCHEMA

INTERRUPTED_MESSAGE = "Interrupted by a server restart."


class DatabaseError(Exception):
    pass


class _AlreadyClaimed(Exception):
    """A staged upload was taken or deleted between looking at it and claiming it."""


# The order the queue runs in (DESIGN.md section 40): by `queue_key` where a run has one, else by arrival (`seq`), and `seq` last to break a tie. Nothing else: not whether a
# run is a draft, which used to be the first term. A draft still jumps the queue, but it does so when it ARRIVES (`Database._place_draft` gives it its place among the
# waiting runs), and from then on its place is its own like any other run's, so that a person can drag it down (1.22) and it stays there.
QUEUE_ORDER = "COALESCE(queue_key, seq), seq"


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
            # Schemas 3, 7 and 10 each widen the CHECK on runs.mode ('music', then 'vector', then 'speech'), which SQLite cannot alter in place, so an older
            # database's table is rebuilt once, to the current definition, whichever older schema it was at (a rebuild at 10 also does what a rebuild at 7
            # or at 3 would have). The two columns schema 10 adds come with the new definition; `_add_speech_columns` is for a table that is already new enough.
            if previous is not None and previous < 10:
                self._rebuild_runs(previous)
            self._add_bin_columns()
            self._add_project_column()
            self._add_picture_bin_columns()
            self._add_prompt_columns()
            self._add_speech_columns()
            self._add_variation_columns()
            self._add_piece_speaker_column()
            self.has_fts5 = self._add_prompt_search()
            self.has_run_fts = self._add_run_search()
            self._index_existing_runs(previous)
            self._record_version(previous)
            self._queue_order = QUEUE_ORDER

    def close(self) -> None:
        with self._lock:
            self._conn.close()

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

    def _rebuild_runs(self, previous: int) -> None:
        """Schemas 3 and 7: let `runs` hold the newer modes ('music', then 'vector'). SQLite cannot change a CHECK in place, so this is its documented way:
        a new table, copy, drop the old, rename. The new table is made from this module's own definition, so a database migrated here and one made fresh
        are the same.

        Which columns are copied is not a list written down here: it is every column the old table has that the new one has too. A list would be one more
        place to forget the columns that schemas 4 to 6 added (`deleted_at`, `restored_at`, `project_id`), and the rebuild would drop them without a
        word; a test compares a migrated database with a fresh one for this. A column the old table lacks (a schema 3 database has no bin columns) is
        left to its default, NULL, and the `_add_*` steps that follow find nothing to add."""
        definition = _SCHEMA[_SCHEMA.index("CREATE TABLE IF NOT EXISTS runs ("):]
        definition = definition[:definition.index(");") + 2].replace("CREATE TABLE IF NOT EXISTS runs (", "CREATE TABLE runs_new (", 1)
        self._conn.execute("PRAGMA foreign_keys=OFF")  # not allowed inside a transaction, and dropping runs must not cascade
        try:
            with self.tx() as c:
                c.execute(definition)
                old = [row["name"] for row in c.execute("PRAGMA table_info(runs)")]
                new = {row["name"] for row in c.execute("PRAGMA table_info(runs_new)")}
                carried = ", ".join(name for name in old if name in new)
                c.execute(f"INSERT INTO runs_new ({carried}) SELECT {carried} FROM runs")
                c.execute("DROP TABLE runs")
                c.execute("ALTER TABLE runs_new RENAME TO runs")
                c.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, seq)")
                broken = c.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise DatabaseError(f"the migration would leave {len(broken)} row(s) pointing at runs that are gone")
        finally:
            self._conn.execute("PRAGMA foreign_keys=ON")
        log.info("the runs table was rebuilt (schema %d to %d)", previous, SCHEMA_VERSION)

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

    def _add_picture_bin_columns(self) -> None:
        """Schemas 6 and 8: the column that sends a single picture, track or version to the bin (DESIGN.md §33.3, §35.4), added to each table that lacks it; then the
        indexes on it (which cannot be made before the column exists, so they are not in `_SCHEMA`). The index is what lets the daily clean-up find
        what is due without reading every picture."""
        for table in ("images", "tracks", "vectors"):
            have = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if "deleted_at" not in have:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TEXT")
            self._conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_deleted ON {table}(deleted_at)")

    def _add_prompt_columns(self) -> None:
        """Schema 9: the two columns that say which prompt, and which of its versions, a run was made from (DESIGN.md §37.3 rule 3), added to a table that lacks
        them. They are plain columns and not foreign keys, like `project_id`: deleting a prompt for good leaves them. They are the last columns of `runs`, in
        the order `_SCHEMA` has them, so that ALTER TABLE gives the same table as CREATE TABLE does; the index cannot be made before the column exists, so
        it is not in `_SCHEMA`."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}
        for column, kind in (("prompt_id", "TEXT"), ("prompt_version", "INTEGER")):
            if column not in have:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {kind}")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_prompt ON runs(prompt_id)")

    def _add_speech_columns(self) -> None:
        """Schema 10: the two last columns of `runs` (DESIGN.md §38.8), added to a table that lacks them. `queue_key` is where a reading that goes back to the
        queue after each part stands in it (null for every other run); `hidden` marks the short jobs behind a voice sample or a voice being designed, which are
        queued like runs but are not history."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}
        if "queue_key" not in have:
            self._conn.execute("ALTER TABLE runs ADD COLUMN queue_key REAL")
        if "hidden" not in have:
            self._conn.execute("ALTER TABLE runs ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")

    def _add_variation_columns(self) -> None:
        """Schema 11: the two last columns of `runs` (DESIGN.md §43.4), added to a table that lacks them. `variation_json` is where a run that was made with More like
        this came from, `{run_id, image_id, how}` (null for every other run); `family_id` is the id of the first run of its family, indexed, so that a family can be
        listed (`RunFilter.family`). Plain columns and not foreign keys, like `project_id` and `prompt_id`: deleting the run it came from for good leaves the link. They are
        the last columns, in the order `_SCHEMA` has them, so that ALTER TABLE gives the same table as CREATE TABLE does; the index cannot be made before the column
        exists, so it is not in `_SCHEMA`."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}
        for column in ("variation_json", "family_id"):
            if column not in have:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} TEXT")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_family ON runs(family_id)")

    def _add_piece_speaker_column(self) -> None:
        """Schema 12: the LAST column of `speech_pieces` (DESIGN.md §44.5), added to a table that lacks it. `speaker` is the name of the speaker a piece belongs to (null for the
        narrator and for every piece made before 1.25). It is the last column, in the order `SPEECH_SCHEMA` has it, so that ALTER TABLE gives the same table as CREATE TABLE
        does. The new table of schema 12, `speech_pronunciations`, needs no step here: `_SCHEMA` creates what is missing."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(speech_pieces)")}
        if "speaker" not in have:
            self._conn.execute("ALTER TABLE speech_pieces ADD COLUMN speaker TEXT")

    def _add_prompt_search(self) -> bool:
        """Schema 9: the word index over prompts (DESIGN.md §37.4). Returns whether this SQLite has FTS5; where it has not, the studio keeps working and
        searching falls back to a plain match (`has_fts5` is False)."""
        try:
            self._conn.execute(PROMPT_SEARCH_SQL)
        except sqlite3.OperationalError as error:
            log.warning("this SQLite has no FTS5 (%s): searching prompts will use a plain match", error)
            return False
        return True

    def _add_run_search(self) -> bool:
        """Schema 13: the word index over runs (DESIGN.md §45.7). Returns whether this SQLite has FTS5; where it has not, the studio keeps working and searching runs falls back to a plain
        match (`has_run_fts` is False). The tables of tags and notes are made by `_SCHEMA`; only this one can fail, so it is a statement of its own."""
        try:
            self._conn.execute(RUN_SEARCH_SQL)
        except sqlite3.OperationalError as error:
            log.warning("this SQLite has no FTS5 (%s): searching runs will use a plain match", error)
            return False
        return True

    def _index_existing_runs(self, previous: Optional[int]) -> None:
        """Index the runs that already exist (DESIGN.md §45.7): a search that found only new runs would be a bug. Done when the database is upgraded from before schema 13 (the index
        is new, so it holds nothing), and again whenever the index does not hold one row for each run that is not a hidden job, which also repairs an index that something did by hand
        left out of step. A new database has no runs, so it has nothing to do."""
        if not self.has_run_fts:
            return
        if previous is not None and previous < 13:
            log.info("indexed %d runs for search", self.reindex_runs())
        elif self._run_search_count() != self._indexable_run_count():
            log.warning("the runs' word index was not in step with the runs; indexed %d runs for search", self.reindex_runs())

    def _run_search_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM run_search").fetchone()[0]

    def _indexable_run_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM runs WHERE hidden = 0").fetchone()[0]

    def reindex_runs(self) -> int:
        """Make the word index match the runs, from scratch, in one transaction; returns how many runs it holds. The notes and tags are read once, not once for each run."""
        if not self.has_run_fts:
            return 0
        with self.tx() as c:
            c.execute("DELETE FROM run_search")
            notes = {r["run_id"]: r["notes"] for r in c.execute("SELECT run_id, notes FROM run_notes")}
            tags: dict[str, list[str]] = {}
            for r in c.execute("SELECT run_id, tag FROM run_tags ORDER BY tag"):
                tags.setdefault(r["run_id"], []).append(r["tag"])
            rows = c.execute("SELECT id, mode, prompt, lyrics, options_json FROM runs WHERE hidden = 0").fetchall()
            for r in rows:
                prompt, extra = index_fields(r["mode"], r["prompt"], r["lyrics"], r["options_json"])
                c.execute("INSERT INTO run_search (run_id, prompt, extra, notes, tags) VALUES (?, ?, ?, ?, ?)",
                          (r["id"], prompt, extra, notes.get(r["id"], ""), " ".join(tags.get(r["id"], []))))
            return len(rows)

    def index_run(self, c: sqlite3.Connection, run_id: str) -> None:
        """Write the one row of the word index for a run (DESIGN.md §45.7): its words, its lyrics or source, its note and its tags. Called INSIDE the transaction that makes the run or
        changes its note or tags, so the index is never behind. A hidden job (the short work behind a voice sample) is not history and is not indexed. Where this SQLite has no FTS5
        there is no index and nothing to do."""
        if not self.has_run_fts:
            return
        c.execute("DELETE FROM run_search WHERE run_id=?", (run_id,))
        run = c.execute("SELECT mode, prompt, lyrics, options_json, hidden FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None or run["hidden"]:
            return
        prompt, extra = index_fields(run["mode"], run["prompt"], run["lyrics"], run["options_json"])
        note = c.execute("SELECT notes FROM run_notes WHERE run_id=?", (run_id,)).fetchone()
        tags = " ".join(r["tag"] for r in c.execute("SELECT tag FROM run_tags WHERE run_id=? ORDER BY tag", (run_id,)))
        c.execute("INSERT INTO run_search (run_id, prompt, extra, notes, tags) VALUES (?, ?, ?, ?, ?)", (run_id, prompt, extra, note["notes"] if note else "", tags))

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

    def read_one(self, sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
        """One row of a SELECT, read under the connection's lock (for the Prompt builder's store, which keeps its own SQL in its own module)."""
        return self._one(sql, args)

    def read_all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        """All the rows of a SELECT, read under the connection's lock."""
        return self._all(sql, args)

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
                self.index_run(c, run["id"])  # findable by search from the moment it exists (DESIGN.md §45.7)
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
                if run.get("status") == "queued" and self._is_draft(run.get("options_json")):
                    self._place_draft(c, run["id"])
                return "ok"
        except _AlreadyClaimed as gone:
            return f"claimed:{gone.args[0]}"

    def prompt_refs_for_runs(self, rows: Sequence[sqlite3.Row]) -> dict[str, dict[str, Any]]:
        """Where each run that was made from a saved prompt came from (DESIGN.md §37.3 rule 3), by run id: {id, title, version, deleted, edited}. `deleted` is true for a
        prompt in the bin and for one deleted for good (whose title is then gone: None). `edited` is true when the words the run sent are not those of the version (white
        space aside); it is only judged for a run whose prompt is the version's text, which is every kind but music, whose description is built from fields."""
        wanted = sorted({r["prompt_id"] for r in rows if r["prompt_id"]})
        if not wanted:
            return {}
        marks = ",".join("?" * len(wanted))
        prompts = {r["id"]: r for r in self._all(f"SELECT id, title, deleted_at FROM prompts WHERE id IN ({marks})", tuple(wanted))}
        texts = {(r["prompt_id"], r["n"]): r["text"] for r in self._all(f"SELECT prompt_id, n, text FROM prompt_versions WHERE prompt_id IN ({marks})", tuple(wanted))}
        found: dict[str, dict[str, Any]] = {}
        for r in rows:
            if not r["prompt_id"]:
                continue
            prompt, text = prompts.get(r["prompt_id"]), texts.get((r["prompt_id"], r["prompt_version"]))
            found[r["id"]] = {
                "id": r["prompt_id"], "title": prompt["title"] if prompt is not None else None, "version": r["prompt_version"],
                "deleted": prompt is None or prompt["deleted_at"] is not None,
                "edited": r["mode"] != "music" and text is not None and " ".join(r["prompt"].split()) != " ".join(text.split()),
            }
        return found

    def variation_refs_for_runs(self, rows: Sequence[sqlite3.Row]) -> dict[str, dict[str, Any]]:
        """Where each run that was made with More like this came from (DESIGN.md §43.4), by run id: `{run_id, image_id, how, source: {prompt, state}}`, where `state` says what
        has become of the run it came from: `present`, `in_bin` or `gone` (deleted for good, so that its words are gone too: None). A run that is not a variation is not in
        the answer. The link itself is kept as it was made: it stays when the source goes, and the card says so."""
        links: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r["variation_json"]:
                links[r["id"]] = json.loads(r["variation_json"])
        if not links:
            return {}
        wanted = sorted({link["run_id"] for link in links.values()})
        marks = ",".join("?" * len(wanted))
        sources = {s["id"]: s for s in self._all(f"SELECT id, prompt, deleted_at FROM runs WHERE id IN ({marks})", tuple(wanted))}
        found: dict[str, dict[str, Any]] = {}
        for run_id, link in links.items():
            source = sources.get(link["run_id"])
            state = "gone" if source is None else "in_bin" if source["deleted_at"] is not None else "present"
            found[run_id] = {**link, "source": {"prompt": source["prompt"] if source is not None else None, "state": state}}
        return found

    def variation_counts_for_runs(self, ids: Sequence[str]) -> dict[str, int]:
        """How many variations each run in `ids` has (DESIGN.md §43.4): the runs in the history (not in the bin) whose `family_id` is its id. A run with none is not in the
        answer. A variation of a variation has its family's first run's id as its `family_id`, so only that first run has a count."""
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        sql = f"SELECT family_id, COUNT(*) AS n FROM runs WHERE family_id IN ({marks}) AND deleted_at IS NULL AND hidden = 0 GROUP BY family_id"
        return {r["family_id"]: r["n"] for r in self._all(sql, tuple(ids))}

    def picture_of_run(self, run_id: str, image_id: str) -> bool:
        """Whether `image_id` is a picture of this run that is not in the bin: a result, or one of an edit's own copies of its input pictures (DESIGN.md §43.6)."""
        return self._one("SELECT 1 FROM images WHERE id=? AND run_id=? AND deleted_at IS NULL", (image_id, run_id)) is not None

    def get_run(self, run_id: str) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM runs WHERE id=?", (run_id,))

    def list_runs(self, limit: int, before_id: Optional[str] = None, run_filter: Optional[RunFilter] = None,
                  kind: Optional[str] = None) -> tuple[list[sqlite3.Row], bool]:
        """Newest first, only the runs `run_filter` lets through (DESIGN.md §29.5), and with `kind` ("image", "music" or "vector", the tabs of
        `runfilter.KINDS`) only the runs of that tab. Returns (rows, has_more). Raises KeyError for an unknown cursor. The cursor may be a run the
        filter does not show (one un-kept since the page was loaded): it only says where to start."""
        # The filter's conditions come first. Each is a fixed SQL fragment with a placeholder; its values are bound, never pasted into the
        # text, so nothing the page sent can become SQL.
        clauses, values = (run_filter or RunFilter()).conditions(self.has_run_fts)
        # The short jobs behind a voice sample or a designed voice are runs that are not history (DESIGN.md §38.4): no list or count shows them
        clauses.append("hidden = 0")
        # The tab is one more condition, on the run's `mode`: one `?` for each mode the tab holds, all bound. An unknown kind is a KeyError, which the
        # API never lets through (it only accepts the four) and which would otherwise silently show every run.
        if kind is not None:
            modes = KINDS[kind]
            clauses.append(f"mode IN ({', '.join('?' for _ in modes)})")
            values.extend(modes)
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

    def _count_bin(self, run_filter: RunFilter, modes: Sequence[str]) -> tuple[int, int]:
        """(things in the bin, of which pictures or tracks) for one tab, within whatever else the filter says (kept, project). The runs in the
        bin count one each; a picture or track in the bin counts one when its run is still in the history (one in a run that is itself in the
        bin is not listed, §33.2 item 5, so it is not counted)."""
        clauses, values = replace(run_filter, deleted=None).conditions(self.has_run_fts)  # the filter without its bin rule: it is applied by hand below
        clauses.append("hidden = 0")
        clauses.append(f"mode IN ({', '.join('?' for _ in modes)})")
        args = (*values, *modes)
        where = " AND ".join(clauses)
        runs = self._one(f"SELECT COUNT(*) AS n FROM runs WHERE {where} AND deleted_at IS NOT NULL", args)["n"]
        history = f"SELECT id FROM runs WHERE {where} AND deleted_at IS NULL"
        items = sum(self._one(f"SELECT COUNT(*) AS n FROM {table} WHERE deleted_at IS NOT NULL AND run_id IN ({history})", args)["n"]
                    for table in _ITEM_TABLES.values())
        return runs + items, items

    def run_counts(self, counted: Mapping[str, RunFilter]) -> dict[str, dict[str, int]]:
        """How many runs each tab holds, for each named filter: {kind: {name: n}} (DESIGN.md §29.6). A filter on the bin counts things (§33.1)."""
        counts: dict[str, dict[str, int]] = {}
        # one count per tab (the run modes the tab shows) and per named filter
        for kind, modes in KINDS.items():
            counts[kind] = {}
            for name, run_filter in counted.items():
                # the bin counts THINGS, not runs (DESIGN.md §33.1): each run in it, and each picture or track in it of a run that is not. The
                # pictures alone are answered beside it, as `<name>_items`, so a page can say "3 runs and 2 pictures"
                if run_filter.deleted is True:
                    counts[kind][name], counts[kind][f"{name}_items"] = self._count_bin(run_filter, modes)
                    continue
                clauses, values = run_filter.conditions(self.has_run_fts)
                clauses.append("hidden = 0")
                # restrict the count to the tab's modes: one `?` placeholder for each
                clauses.append(f"mode IN ({', '.join('?' for _ in modes)})")
                row = self._one(f"SELECT COUNT(*) AS n FROM runs WHERE {' AND '.join(clauses)}", (*values, *modes))
                counts[kind][name] = row["n"]
        return counts

    def queued_ids(self) -> list[str]:
        return [r["id"] for r in self._all(f"SELECT id FROM runs WHERE status='queued' ORDER BY {self._queue_order}")]

    def next_queued(self) -> Optional[sqlite3.Row]:
        return self._one(f"SELECT * FROM runs WHERE status='queued' ORDER BY {self._queue_order} LIMIT 1")

    def queue_rows(self) -> list[sqlite3.Row]:
        """The waiting runs in the order they will be worked on, with what the Queue panel says of each (DESIGN.md section 40.4)."""
        return self._all(f"SELECT id, mode, prompt, options_json, hidden, started_at FROM runs WHERE status='queued' ORDER BY {self._queue_order}")

    @staticmethod
    def _is_draft(options_json: Any) -> bool:
        """Whether a run's stored options say it is a draft: a quick small picture (DESIGN.md section 22.2) or a voice sample (section 38), both of which jump the queue.
        Read here and not by SQLite's JSON functions, so the queue does not depend on whether this SQLite has them."""
        try:
            return bool(json.loads(options_json).get("draft")) if options_json else False
        except (ValueError, AttributeError):
            return False

    def _apply_order(self, c: sqlite3.Connection, ids: Sequence[str]) -> None:
        """Make the waiting runs `ids` run in exactly that order, by giving each a new `queue_key`, evenly spaced between the newest run's `seq` and half way to the next one.
        That is the region `_next_queue_key` and a later arrival already understand: every key is after every run made so far and before every run made later, so a run
        that arrives afterwards still goes to the back, and a reading that goes back to the queue after a part goes behind these. Rewriting them all, instead of finding
        a number between two neighbours, means the keys never run out of room however often runs are moved."""
        last = c.execute("SELECT COALESCE((SELECT seq FROM sqlite_sequence WHERE name='runs'), 0)").fetchone()[0]
        for place, run_id in enumerate(ids, 1):
            c.execute("UPDATE runs SET queue_key=? WHERE id=?", (last + 0.5 * place / (len(ids) + 1), run_id))

    def _place_draft(self, c: sqlite3.Connection, run_id: str) -> None:
        """A draft that has just arrived goes to the front of the line: after the drafts that are already at the front, and before every run that is not a draft (the rule
        of DESIGN.md section 22.2, kept as it was). It arrives at the back like every run; if a run that is not a draft is waiting ahead of that, it is moved in front of it.
        A draft a person has dragged further down is not at the front, so it is not counted, and stays where it was put."""
        others = [r for r in c.execute(f"SELECT id, options_json FROM runs WHERE status='queued' ORDER BY {self._queue_order}").fetchall() if r["id"] != run_id]
        first_other = next((i for i, r in enumerate(others) if not self._is_draft(r["options_json"])), None)
        if first_other is None:
            return  # only drafts are waiting (or nothing): the back of the line is already the place after them
        ids = [r["id"] for r in others]
        ids.insert(first_other, run_id)
        self._apply_order(c, ids)

    def move_queued(self, run_id: str, before: Optional[str] = None, after: Optional[str] = None) -> str:
        """Put a waiting run just before (`before`) or just after (`after`) another waiting run (DESIGN.md section 40.2). Returns 'ok'; 'not_found' (no such run);
        'not_movable' (it is not waiting, or it has started: a reading that has made a part and is waiting for its next turn counts as started); 'target_gone' (the other
        run is no longer waiting: it started, or was canceled, since the person looked); or 'invalid' (no other run, or the run itself). The run that is going is not in
        the queue, so nothing can be put before it. One transaction, so a run that is picked up at that moment is either moved first or refused."""
        target = before or after
        if not target or target == run_id:
            return "invalid"
        with self.tx() as c:
            row = c.execute("SELECT status, started_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["status"] != "queued" or row["started_at"] is not None:
                return "not_movable"
            old = [r["id"] for r in c.execute(f"SELECT id FROM runs WHERE status='queued' ORDER BY {self._queue_order}").fetchall()]
            if target not in old:
                return "target_gone"
            ids = [i for i in old if i != run_id]
            ids.insert(ids.index(target) + (0 if before else 1), run_id)
            if ids != old:  # dropped where it already is: nothing to do
                self._apply_order(c, ids)
            return "ok"

    @staticmethod
    def _next_queue_key(c: sqlite3.Connection) -> float:
        """The place in the queue a run takes if it arrives now: after every run made so far and before every run made later. `seq` is what runs are made in
        (AUTOINCREMENT, so its last value is in `sqlite_sequence` even if the newest run has been deleted); a key just above it is later than all of them and
        earlier than the next. Runs that arrive in the same instant are told apart by adding a millionth for each. No clock is involved, so a clock that
        jumps cannot reorder the queue."""
        last = c.execute("SELECT COALESCE((SELECT seq FROM sqlite_sequence WHERE name='runs'), 0)").fetchone()[0]
        top = c.execute("SELECT MAX(queue_key) FROM runs WHERE queue_key >= ? AND queue_key < ?", (last, last + 1)).fetchone()[0]
        return (top if top is not None else float(last)) + 1e-6

    def requeue_run(self, run_id: str) -> bool:
        """A reading that has made a part goes back to the back of the queue (DESIGN.md §38.6): running -> queued with a new place. A job made while the part
        was being worked on is ahead of it. False if the run is gone or is not running."""
        with self.tx() as c:
            row = c.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "running":
                return False
            c.execute("UPDATE runs SET status='queued', queue_key=? WHERE id=?", (self._next_queue_key(c), run_id))
            return True

    def restart_run(self, run_id: str, cap: int) -> str:
        """Continue or Retry a reading: canceled or failed -> queued, at the back, with its error cleared. Returns 'ok', 'not_found', 'not_restartable' (it is
        queued, running or done) or 'full' (`cap` runs are already waiting)."""
        with self.tx() as c:
            row = c.execute("SELECT status, mode FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["status"] not in ("canceled", "failed") or row["mode"] != "speech":
                return "not_restartable"
            if c.execute("SELECT COUNT(*) FROM runs WHERE status='queued'").fetchone()[0] >= cap:
                return "full"
            c.execute("UPDATE runs SET status='queued', finished_at=NULL, error_message=NULL, error_hint=NULL, queue_key=? WHERE id=?",
                      (self._next_queue_key(c), run_id))
            return "ok"

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
        """Keep (or stop keeping) a run. Returns 'ok' (also when it already was so), 'not_found', 'locked': a run that is in a project stays
        kept (DESIGN.md §32.3), so stopping to keep it is refused, or 'always_kept': a reading never expires and is kept from the start (§38.8), so
        there is nothing to stop. Checked and written in one transaction, so a run filed at the same moment is never left un-kept."""
        with self.tx() as c:
            row = c.execute("SELECT project_id, mode FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if not pinned and row["mode"] == "speech":
                return "always_kept"
            if not pinned and row["project_id"] is not None:
                return "locked"
            c.execute("UPDATE runs SET pinned=? WHERE id=?", (int(pinned), run_id))
            return "ok"

    # An expiry counts from the later of when a run was made and when it was last restored (DESIGN.md §30.4).
    # Which runs the clean-up may take: not kept, not in a project, not already in the bin, finished (never one that is queued or running), and
    # old enough. Old enough counts from the later of when the run was made and when it was last restored, so a restore starts a fresh clock.
    # "Not in a project" is said here although a filed run is always kept (§32.3 item 4): it holds even if something clears the Keep flag by hand.
    # A reading never expires (DESIGN.md §38.8, your answer 13: a book can be 2 GB and is listened to over weeks): `mode != 'speech'`. The short hidden jobs
    # are speech runs too, and are cleaned up by their own janitor, not by this one.
    _EXPIRED = ("pinned=0 AND project_id IS NULL AND deleted_at IS NULL AND status IN ('done','failed','canceled') AND mode != 'speech' "
                "AND COALESCE(restored_at, created_at) < ?")

    # the one place that removes a run and what hangs off it (its pictures, tracks and vector versions, its tags and note, and its row of the word index); every delete goes
    # through here, so none forgets a table, and the index is never left holding a run that is gone (DESIGN.md §45.7)
    def _delete_rows(self, c: sqlite3.Connection, ids: Sequence[str]) -> None:
        for run_id in ids:
            c.execute("DELETE FROM run_tags WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM run_notes WHERE run_id=?", (run_id,))
            if self.has_run_fts:
                c.execute("DELETE FROM run_search WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM images WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM tracks WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM vectors WHERE run_id=?", (run_id,))
            for table in ("speech_pieces", "speech_parts", "speech_positions"):  # a reading's rows (the cascade would do it; every table is named here as the others are)
                c.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
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

    # ------------------------------------------- single pictures, tracks and versions in the bin (DESIGN.md §33, §35)
    # Each of these takes `kind` ("image", "track" or "vector") and the item's id, does everything in one transaction, and answers (result, run id); the
    # run id is None when the item was not found. They never touch a file: the job manager removes files after the rows are gone.
    def _item_and_run(self, c: sqlite3.Connection, kind: str, item_id: str) -> tuple[Optional[sqlite3.Row], Optional[sqlite3.Row]]:
        item = c.execute(f"SELECT * FROM {_ITEM_TABLES[kind]} WHERE id=?", (item_id,)).fetchone()
        # a staged upload is an image no run owns: it is not part of the history, so it is not found here
        if item is None or item["run_id"] is None:
            return None, None
        return item, c.execute("SELECT status, deleted_at FROM runs WHERE id=?", (item["run_id"],)).fetchone()

    def _others_present(self, c: sqlite3.Connection, kind: str, run_id: str, item_id: str) -> int:
        """How many of a run's pictures (or tracks) other than this one are not in the bin: 0 means this is the run's last."""
        only_results = " AND kind='output'" if kind == "image" else ""  # an edit's source images are in the images table too
        row = c.execute(
            f"SELECT COUNT(*) AS n FROM {_ITEM_TABLES[kind]} WHERE run_id=? AND deleted_at IS NULL AND id != ?{only_results}", (run_id, item_id)).fetchone()
        return row["n"]

    def bin_picture(self, kind: str, item_id: str, now: str) -> tuple[str, Optional[str]]:
        """Send a picture or track to the bin. Results: 'picture' (it is in the bin, also when it already was: its date does not move), 'run'
        (it was the run's last, so the run went to the bin instead, §33.2 item 3), 'not_found', 'not_a_result' (an edit's source image),
        'not_finished' (the run is queued or running) and 'run_in_bin'."""
        with self.tx() as c:
            item, run = self._item_and_run(c, kind, item_id)
            if item is None:
                return "not_found", None
            run_id = item["run_id"]
            if kind == "image" and item["kind"] != "output":
                return "not_a_result", run_id
            if run["status"] not in _FINISHED:
                return "not_finished", run_id
            if run["deleted_at"] is not None:
                return "run_in_bin", run_id
            if item["deleted_at"] is not None:
                return "picture", run_id
            # the last picture is not deleted by itself: the run goes, with all its pictures, as if Delete had been pressed on the run
            if self._others_present(c, kind, run_id, item_id) == 0:
                c.execute("UPDATE runs SET deleted_at=? WHERE id=?", (now, run_id))
                return "run", run_id
            c.execute(f"UPDATE {_ITEM_TABLES[kind]} SET deleted_at=? WHERE id=?", (now, item_id))
            return "picture", run_id

    def restore_picture(self, kind: str, item_id: str) -> tuple[str, Optional[str]]:
        """Take a picture or track out of the bin, back into its run at its own place (its `idx` never changed). Results: 'restored',
        'not_found', 'not_a_result', 'not_in_bin' and 'run_in_bin' (the run has to be restored first, §33.2 item 6)."""
        with self.tx() as c:
            item, run = self._item_and_run(c, kind, item_id)
            if item is None:
                return "not_found", None
            run_id = item["run_id"]
            if kind == "image" and item["kind"] != "output":
                return "not_a_result", run_id
            if item["deleted_at"] is None:
                return "not_in_bin", run_id
            if run["deleted_at"] is not None:
                return "run_in_bin", run_id
            c.execute(f"UPDATE {_ITEM_TABLES[kind]} SET deleted_at=NULL WHERE id=?", (item_id,))
            return "restored", run_id

    def delete_picture(self, kind: str, item_id: str) -> tuple[str, Optional[str], Optional[sqlite3.Row]]:
        """Delete a picture or track for good (its files are the caller's to remove): (result, run id, the item's row). 'picture' (just it),
        'run' (it was the run's last, so the whole run is deleted, §33.3), 'not_found', 'not_a_result', 'not_finished' and 'run_in_bin'. One in the
        bin goes whatever the run is doing; one that is not needs a finished run in the history, as sending it to the bin does."""
        with self.tx() as c:
            item, run = self._item_and_run(c, kind, item_id)
            if item is None:
                return "not_found", None, None
            run_id = item["run_id"]
            if kind == "image" and item["kind"] != "output":
                return "not_a_result", run_id, None
            if item["deleted_at"] is None:
                if run["status"] not in _FINISHED:
                    return "not_finished", run_id, None
                if run["deleted_at"] is not None:
                    return "run_in_bin", run_id, None
                if self._others_present(c, kind, run_id, item_id) == 0:
                    self._delete_rows(c, [run_id])
                    return "run", run_id, item
            c.execute(f"DELETE FROM {_ITEM_TABLES[kind]} WHERE id=?", (item_id,))
            return "picture", run_id, item

    def purge_binned_items(self, cutoff: Optional[str], limit: int) -> list[tuple[str, sqlite3.Row]]:
        """Delete for good up to `limit` pictures, `limit` tracks and `limit` versions that went into the bin before `cutoff` (all of them when `cutoff`
        is None), wherever their run is (§33.2 item 7, §35.3 item 6). Returns [(kind, row)] so that the files can follow."""
        purged: list[tuple[str, sqlite3.Row]] = []
        with self.tx() as c:
            for kind, table in _ITEM_TABLES.items():
                if cutoff is None:
                    rows = c.execute(f"SELECT * FROM {table} WHERE deleted_at IS NOT NULL ORDER BY deleted_at LIMIT ?", (limit,)).fetchall()
                else:
                    rows = c.execute(f"SELECT * FROM {table} WHERE deleted_at IS NOT NULL AND deleted_at < ? ORDER BY deleted_at LIMIT ?", (cutoff, limit)).fetchall()
                for row in rows:
                    c.execute(f"DELETE FROM {table} WHERE id=?", (row["id"],))
                    purged.append((kind, row))
        return purged

    # ---------------------------------------------------------------- projects (DESIGN.md §32)
    def list_projects(self, project_id: Optional[str] = None) -> list[dict[str, Any]]:
        """The projects, A to Z (ignoring case), each with how many runs of each tab it holds, and how many prompts and chats: {id, name, created_at,
        counts: {image, music, vector, prompts, chats}}. The counts are of what is on show; a run, prompt or chat in the bin is not counted. With `project_id`, only that project (a
        list of one, or empty)."""
        with self._lock:
            where, args = ("WHERE id=?", (project_id,)) if project_id is not None else ("", ())
            projects = self._all(f"SELECT id, name, created_at FROM projects {where} ORDER BY name_key, id", args)
            found = {p["id"]: {"id": p["id"], "name": p["name"], "created_at": p["created_at"], "counts": {**{kind: 0 for kind in KINDS}, "prompts": 0, "chats": 0}} for p in projects}
            # one grouped query for all the counts: runs by project and mode, then each mode is added to its tab
            rows = self._all("SELECT project_id, mode, COUNT(*) AS n FROM runs WHERE project_id IS NOT NULL AND deleted_at IS NULL GROUP BY project_id, mode")
            # prompts and chats are counted in their own words (DESIGN.md §37.3 rule 5); those in the bin are not on show, so they are not counted
            builder = {table: self._all(f"SELECT project_id, COUNT(*) AS n FROM {table} WHERE project_id IS NOT NULL AND deleted_at IS NULL GROUP BY project_id")
                       for table in ("prompts", "chats")}
        for table, counted in builder.items():
            for row in counted:
                if row["project_id"] in found:
                    found[row["project_id"]]["counts"][table] = row["n"]
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
            # The Prompt builder's side (DESIGN.md §37.3 rule 5): no prompt or chat is deleted, in the bin or not, and none is left pointing at a project that is
            # gone; a preference of the project is turned OFF and keeps its project_id, so it is listed as no longer in a project and never starts applying
            # everywhere
            c.execute("UPDATE prompts SET project_id=NULL WHERE project_id=?", (project_id,))
            c.execute("UPDATE chats SET project_id=NULL WHERE project_id=?", (project_id,))
            c.execute("UPDATE preferences SET enabled=0 WHERE project_id=?", (project_id,))
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
        exactly what it was, DESIGN.md §32.3 item 3). A reading is always kept (§38.8), so for it `keep` makes no difference. Returns 'unfiled', 'not_found'
        or 'not_in_project'."""
        with self.tx() as c:
            row = c.execute("SELECT project_id, mode FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["project_id"] is None:
                return "not_in_project"
            c.execute("UPDATE runs SET project_id=NULL" + ("" if keep or row["mode"] == "speech" else ", pinned=0") + " WHERE id=?", (run_id,))
            return "unfiled"

    # ----------------------------------------------------- tags, notes and facets (DESIGN.md §45)
    def tags_for_runs(self, run_ids: Sequence[str]) -> dict[str, list[str]]:
        """The tags of each run in `run_ids`, A to Z; a run with none has an empty list."""
        found: dict[str, list[str]] = {run_id: [] for run_id in run_ids}
        if not run_ids:
            return found
        marks = ",".join("?" * len(run_ids))
        for row in self._all(f"SELECT run_id, tag FROM run_tags WHERE run_id IN ({marks}) ORDER BY tag", tuple(run_ids)):
            found[row["run_id"]].append(row["tag"])
        return found

    def notes_for_runs(self, run_ids: Sequence[str]) -> dict[str, str]:
        """The note of each run in `run_ids` that has one; a run without a note is not in the answer."""
        if not run_ids:
            return {}
        marks = ",".join("?" * len(run_ids))
        return {r["run_id"]: r["notes"] for r in self._all(f"SELECT run_id, notes FROM run_notes WHERE run_id IN ({marks})", tuple(run_ids))}

    def _annotatable(self, c: sqlite3.Connection, run_id: str) -> str:
        """'ok' for a run whose tags and note may be changed, 'not_found' (no such run, or a hidden job, which is not history) or 'in_bin' (the bin only reads, DESIGN.md §45.4 rule 12)."""
        row = c.execute("SELECT deleted_at, hidden FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["hidden"]:
            return "not_found"
        return "in_bin" if row["deleted_at"] is not None else "ok"

    def set_run_tags(self, run_id: str, tags: Sequence[str]) -> str:
        """Replace a run's tags with `tags` (already cleaned, each once, at most `TAGS_MAX`), and keep the word index in step, in one transaction. Returns 'ok', 'not_found' or 'in_bin'."""
        with self.tx() as c:
            outcome = self._annotatable(c, run_id)
            if outcome != "ok":
                return outcome
            c.execute("DELETE FROM run_tags WHERE run_id=?", (run_id,))
            c.executemany("INSERT INTO run_tags (run_id, tag) VALUES (?, ?)", [(run_id, tag) for tag in tags])
            self.index_run(c, run_id)
            return "ok"

    def set_run_notes(self, run_id: str, notes: str, now: str) -> str:
        """Set a run's note to `notes` (already cleaned and checked); an empty text clears it. Keeps the word index in step, in one transaction. Returns 'ok', 'not_found' or 'in_bin'."""
        with self.tx() as c:
            outcome = self._annotatable(c, run_id)
            if outcome != "ok":
                return outcome
            if notes:
                c.execute("INSERT INTO run_notes (run_id, notes, updated_at) VALUES (?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET notes=excluded.notes, updated_at=excluded.updated_at",
                          (run_id, notes, now))
            else:
                c.execute("DELETE FROM run_notes WHERE run_id=?", (run_id,))
            self.index_run(c, run_id)
            return "ok"

    def tag_counts(self, limit: int = TAGS_LISTED) -> list[dict[str, Any]]:
        """The tags in use, most used first (then A to Z), each with the number of runs that have it: [{tag, count}]. Only runs in the history count (not the bin, not a hidden job):
        this is what the editor's autocomplete offers (DESIGN.md §45.4 rule 9)."""
        rows = self._all(
            "SELECT t.tag AS tag, COUNT(*) AS n FROM run_tags t JOIN runs r ON r.id = t.run_id WHERE r.deleted_at IS NULL AND r.hidden = 0 GROUP BY t.tag ORDER BY n DESC, t.tag LIMIT ?", (limit,))
        return [{"tag": r["tag"], "count": r["n"]} for r in rows]

    def run_facets(self, kind: str) -> dict[str, list[dict[str, Any]]]:
        """What the Model and Size drop-downs offer for one tab (DESIGN.md §45.7): {models: [{value, count}], sizes: [{width, height, count}]}, most runs first, at most `FACETS_LISTED`
        of each. Every run of the tab that is not a hidden job counts, in the history or in the bin, so a model that only the bin holds can still be chosen in the Deleted view. Sizes are
        the picture runs' (the Images tab's); every other tab's list is empty."""
        modes = KINDS[kind]
        marks = ", ".join("?" for _ in modes)
        models = self._all(f"SELECT model_id AS value, COUNT(*) AS n FROM runs WHERE hidden = 0 AND mode IN ({marks}) GROUP BY model_id ORDER BY n DESC, model_id LIMIT ?", (*modes, FACETS_LISTED))
        sizes: list[sqlite3.Row] = []
        if kind == "image":
            sizes = self._all(
                f"SELECT w, h, COUNT(*) AS n FROM (SELECT {RUN_WIDTH} AS w, {RUN_HEIGHT} AS h FROM runs WHERE hidden = 0 AND mode IN ('generate', 'edit')) "
                "WHERE w IS NOT NULL AND h IS NOT NULL GROUP BY w, h ORDER BY n DESC, w DESC, h DESC LIMIT ?", (FACETS_LISTED,))
        return {"models": [{"value": r["value"], "count": r["n"]} for r in models], "sizes": [{"width": r["w"], "height": r["h"], "count": r["n"]} for r in sizes]}

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
        # the tracks that are not in the bin: what the card and the player show, numbered by position (DESIGN.md §33.1)
        for row in self._all(f"SELECT * FROM tracks WHERE deleted_at IS NULL AND run_id IN ({placeholders}) ORDER BY run_id, idx", tuple(run_ids)):
            result[row["run_id"]].append(row)
        return result

    def binned_tracks_for_runs(self, run_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
        """The tracks of each run that are in the bin, in the order they were made (DESIGN.md §33.3)."""
        result: dict[str, list[sqlite3.Row]] = {rid: [] for rid in run_ids}
        if not run_ids:
            return result
        placeholders = ", ".join("?" for _ in run_ids)
        for row in self._all(f"SELECT * FROM tracks WHERE deleted_at IS NOT NULL AND run_id IN ({placeholders}) ORDER BY run_id, idx", tuple(run_ids)):
            result[row["run_id"]].append(row)
        return result

    # ---------------------------------------------------------------- vectors (DESIGN.md §31.12.6)
    def add_vector(self, vector: dict[str, Any]) -> None:
        """Record one finished version of a vector run (its files are already in place)."""
        columns = ", ".join(vector)
        placeholders = ", ".join("?" for _ in vector)
        with self._lock:
            self._conn.execute(f"INSERT INTO vectors ({columns}) VALUES ({placeholders})", tuple(vector.values()))

    def get_vector(self, vector_id: str) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM vectors WHERE id=?", (vector_id,))

    def vectors_for_runs(self, run_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
        """The versions of each run that are not in the bin, in the order they were drawn: what the card and the viewer show, numbered by position
        (DESIGN.md §35.4), as `images_for_runs` and `tracks_for_runs` are for pictures and tracks."""
        result: dict[str, list[sqlite3.Row]] = {rid: [] for rid in run_ids}
        if not run_ids:
            return result
        placeholders = ", ".join("?" for _ in run_ids)
        for row in self._all(f"SELECT * FROM vectors WHERE deleted_at IS NULL AND run_id IN ({placeholders}) ORDER BY run_id, idx", tuple(run_ids)):
            result[row["run_id"]].append(row)
        return result

    def binned_vectors_for_runs(self, run_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
        """The versions of each run that are in the bin, in the order they were drawn (DESIGN.md §35.4)."""
        result: dict[str, list[sqlite3.Row]] = {rid: [] for rid in run_ids}
        if not run_ids:
            return result
        placeholders = ", ".join("?" for _ in run_ids)
        for row in self._all(f"SELECT * FROM vectors WHERE deleted_at IS NOT NULL AND run_id IN ({placeholders}) ORDER BY run_id, idx", tuple(run_ids)):
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
        # the results that are not in the bin: what the card and the viewer show, numbered by position (DESIGN.md §33.1)
        rows = self._all(
            f"SELECT * FROM images WHERE kind='output' AND deleted_at IS NULL AND run_id IN ({placeholders}) ORDER BY run_id, idx",
            tuple(run_ids),
        )
        for row in rows:
            result[row["run_id"]].append(row)
        return result

    def binned_images_for_runs(self, run_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
        """The result pictures of each run that are in the bin, in the order they were made (DESIGN.md §33.3)."""
        result: dict[str, list[sqlite3.Row]] = {rid: [] for rid in run_ids}
        if not run_ids:
            return result
        placeholders = ", ".join("?" for _ in run_ids)
        rows = self._all(
            f"SELECT * FROM images WHERE kind='output' AND deleted_at IS NOT NULL AND run_id IN ({placeholders}) ORDER BY run_id, idx",
            tuple(run_ids),
        )
        for row in rows:
            result[row["run_id"]].append(row)
        return result
