"""Helpers for the tests that build an old database (the migration tests of schemas 3 to 7).

An old database is made here by taking a database that this code made and undoing what the newer schemas added, because the real old code is not
available to run. Each schema's `without_...` step lives with the test file that first needed it; this one, `as_before_vector`, is shared by all of them
since schema 7 (DESIGN.md §31.12.6) changed something they all had to undo: the CHECK on `runs.mode`.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


def table_sql(path: Path, name: str) -> str:
    """The text of a table's CREATE statement, as SQLite stored it."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
    finally:
        conn.close()


def as_before_vector(path: Path) -> None:
    """Make a database that this code made look as one from before schema 7 does: `runs.mode` may be only generate, edit or music (the CHECK that
    schemas 3 to 6 had), and there is no `vectors` table. Every column and every row of `runs` is kept, and so are its three indexes, so the steps
    that undo the older schemas can follow as before. SQLite cannot change a CHECK in place, so this makes the table again the way the migration
    does, which is also why a test of the migration needs it: a database that already allows 'vector' would not show that the migration widens it."""
    conn = sqlite3.connect(path)
    try:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()[0]
        assert "'music','vector'" in sql, "this code's runs table should allow vector runs"
        older = re.sub(r'^CREATE TABLE "?runs"?', "CREATE TABLE runs_before_vector", sql.replace("'music','vector'", "'music'"), count=1)
        conn.execute("PRAGMA foreign_keys=OFF")  # dropping runs must not cascade to the pictures and tracks that point at it
        conn.execute(older)
        columns = ", ".join(row[1] for row in conn.execute("PRAGMA table_info(runs)"))
        conn.execute(f"INSERT INTO runs_before_vector ({columns}) SELECT {columns} FROM runs")
        conn.execute("DROP TABLE runs")
        conn.execute("ALTER TABLE runs_before_vector RENAME TO runs")
        conn.execute("CREATE INDEX idx_runs_status ON runs(status, seq)")
        conn.execute("CREATE INDEX idx_runs_deleted ON runs(deleted_at)")
        conn.execute("CREATE INDEX idx_runs_project ON runs(project_id)")
        conn.execute("DROP INDEX idx_vectors_run")
        conn.execute("DROP TABLE vectors")
        conn.commit()
    finally:
        conn.close()
