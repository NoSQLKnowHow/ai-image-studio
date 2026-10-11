"""Schema 7 (DESIGN.md §31.12.6, criterion 144): `runs.mode` accepts 'vector' and the `vectors` table exists.

SQLite cannot change a CHECK in place, so the migration rebuilds `runs`. What these tests protect: a database from 1.16 (schema 6) and every older one
is migrated without losing a run or one column of it (the rebuild must carry what schemas 4 to 6 added, `deleted_at`, `restored_at` and `project_id`),
a copy of the old database is made first and left as it was, and a migrated database is the same as a fresh one. The old databases are made by the
builders the earlier schemas' tests use (each now with the mode CHECK of its time, `schema_helpers.as_before_vector`) and by `v6_database` below.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from schema_helpers import as_before_vector, table_sql
from test_music_db import columns_of, count, make_v2, sql_of
from test_picture_bin import v5_database
from test_projects import v3_database, v4_database

from studio.db import SCHEMA_VERSION, Database, DatabaseError

NOW = "2026-10-11T10:00:00.000Z"
RUN_GENERATE, RUN_MUSIC, RUN_EDIT = "a" * 32, "b" * 32, "c" * 32
PROJECT = "1" * 32


def v6_database(path) -> None:
    """A schema 6 database as 1.16 leaves it: the current tables with the mode CHECK of that time and no `vectors` table, holding three runs that
    use every column schemas 4 to 6 added (a kept run filed in a project and restored from the bin, a music run in the bin with lyrics, a failed edit
    with an error), their pictures and tracks (one of each in the bin), and the project. Their `seq` numbers have gaps (10, 20, 35), as those of a database that has had runs
    deleted do, so that a rebuild that numbered them again would be noticed. Written to be noticed if a migration loses or mangles any of it."""
    Database(path).close()  # the tables, made by this code
    as_before_vector(path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
    conn.execute("INSERT INTO projects (id, name, name_key, created_at) VALUES (?, 'Logo', 'logo', '2026-10-01T00:00:00.000Z')", (PROJECT,))
    runs = [
        (RUN_GENERATE, "2026-10-01T10:00:00.000Z", "done", "generate", "a lighthouse", 1, None, "2026-10-05T00:00:00.000Z", PROJECT, None, None, None),
        (RUN_MUSIC, "2026-10-02T10:00:00.000Z", "done", "music", "warm piano", 0, "la la la", None, None, "2026-10-06T00:00:00.000Z", None, None),
        (RUN_EDIT, "2026-10-03T10:00:00.000Z", "failed", "edit", "make it blue", 0, None, None, None, None, "boom", "try again"),
    ]
    for seq, (run_id, created, status, mode, prompt, pinned, lyrics, restored, project, deleted, error, hint) in zip((10, 20, 35), runs):
        conn.execute(
            "INSERT INTO runs (seq, id, created_at, started_at, finished_at, status, mode, prompt, effective_prompt, negative_prompt, transparent, width, "
            "height, steps, cfg_scale, seed, num_images, model_id, input_image_id, error_message, error_hint, pinned, options_json, lyrics, "
            "deleted_at, restored_at, project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'blurry', 1, 256, 128, 20, 4.5, 77, 2, 'm', NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (seq, run_id, created, created, created, status, mode, prompt, prompt + " (sent)", error, hint, pinned, json.dumps({"steps": 20, "n": prompt}),
             lyrics, deleted, restored, project))
    for n in (1, 2):
        conn.execute("INSERT INTO images (id, run_id, kind, idx, seed, width, height, has_alpha, bytes, path, created_at, deleted_at) "
                     "VALUES (?, ?, 'output', ?, ?, 8, 8, 0, 1, ?, '2026-10-01T10:00:00.000Z', ?)",
                     (f"{n:032x}", RUN_GENERATE, n, n, f"images/a/{n}.png", NOW if n == 2 else None))
        conn.execute("INSERT INTO tracks (id, run_id, idx, seed, seconds, sample_rate, channels, bytes, path, created_at, deleted_at) "
                     "VALUES (?, ?, ?, ?, 5.0, 24000, 1, 1, ?, '2026-10-02T10:00:00.000Z', ?)",
                     (f"{n + 16:032x}", RUN_MUSIC, n, n, f"audio/b/{n}.wav", NOW if n == 2 else None))
    conn.commit()
    conn.close()


def run_rows(path) -> list[dict]:
    """Every run of a database as plain dicts, oldest first, whatever columns it has (so an old database and a migrated one can be compared)."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM runs ORDER BY seq")]
    finally:
        conn.close()


def vector_run(run_id: str, **extra) -> dict:
    """A run of mode 'vector' as the database wants it."""
    row = {"id": run_id, "created_at": NOW, "status": "queued", "mode": "vector", "prompt": "a fox head", "effective_prompt": "A fox head, flat.",
           "negative_prompt": "gradient", "width": 1536, "height": 1536, "steps": 40, "cfg_scale": 4.0, "seed": 7, "num_images": 2, "model_id": "m",
           "options_json": json.dumps({"colours": 2})}
    row.update(extra)
    return row


def vector_row(vector_id: str, run_id: str, idx: int = 0, **extra) -> dict:
    """A `vectors` row as `Database.add_vector` wants it."""
    row = {"id": vector_id, "run_id": run_id, "idx": idx, "seed": 7 + idx, "width_mm": 100.0, "height_mm": 50.5, "colours": 2, "layers": 1, "bytes": 123,
           "check_json": json.dumps({"colours_found": 2}), "svg_path": f"vector/{run_id}/{idx}.svg", "zip_path": f"vector/{run_id}/{idx}-layers.zip",
           "png_path": f"vector/{run_id}/{idx}.png", "created_at": NOW}
    row.update(extra)
    return row


def full_columns(path, table: str) -> list[tuple]:
    """Name, type, not-null, default and primary-key position of each column, in order: what `PRAGMA table_info` says apart from the column's number."""
    return columns_of(path, table)


def schema_names(path) -> dict[str, dict[str, str]]:
    """Every table and index of a database, by name, with its CREATE statement, written in one way (SQLite quotes the name of a renamed table, and
    writes a column that was added later without the line breaks of one that was made with the table)."""
    conn = sqlite3.connect(path)
    try:
        found: dict[str, dict[str, str]] = {"table": {}, "index": {}}
        for kind, name, sql in conn.execute("SELECT type, name, sql FROM sqlite_master WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"):
            text = re.sub(r"\s+", " ", (sql or "").replace('"', "")).strip()
            found[kind][name] = re.sub(r" ?([(),]) ?", r"\1", text)  # a column added by ALTER is written as `, deleted_at TEXT)`, a made one over lines
        return found
    finally:
        conn.close()


OLD_DATABASES = [(make_v2, "1.7 (schema 2)"), (v3_database, "1.12 (schema 3)"), (v4_database, "1.13 (schema 4)"), (v5_database, "1.14 (schema 5)"),
                 (v6_database, "1.16 (schema 6)")]


# ------------------------------------------------------------------ the number
def test_the_schema_is_7():
    assert SCHEMA_VERSION == 7  # Vector's: when the next release changes the schema, this is the line to move


# ------------------------------------------------------------------ migrating 1.16 (schema 6), losing nothing
def test_a_1_16_database_is_migrated_and_every_run_keeps_every_column(tmp_path):
    path = tmp_path / "studio.sqlite"
    v6_database(path)
    before = run_rows(path)
    assert len(before) == 3 and "vector" not in table_sql(path, "runs")  # it really is an old one: its runs table does not know the new mode
    Database(path).close()
    after = run_rows(path)
    assert after == before  # every row, every column (deleted_at, restored_at, project_id, lyrics, error, seq...), by value
    # the columns schemas 4 to 6 added were really in what was compared, with values (a rebuild that dropped them would have made them NULL or absent)
    assert after[0]["project_id"] == PROJECT and after[0]["restored_at"] == "2026-10-05T00:00:00.000Z" and after[0]["pinned"] == 1
    assert after[1]["deleted_at"] == "2026-10-06T00:00:00.000Z" and after[1]["lyrics"] == "la la la"
    assert after[2]["error_message"] == "boom" and after[2]["cfg_scale"] == 4.5 and after[2]["transparent"] == 1
    assert [row["seq"] for row in after] == [10, 20, 35]  # and the place each run has in the history, which the cursor of a page is made from
    assert count(path, "images") == 2 and count(path, "tracks") == 2 and count(path, "projects") == 1  # the rows that point at runs survived too
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "7"
        # the pictures in the bin are still in it: the other schema 6 column was not touched either
        assert conn.execute("SELECT COUNT(*) FROM images WHERE deleted_at IS NOT NULL").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_the_migrated_runs_table_accepts_a_vector_run_and_its_history_order_and_numbering_go_on(tmp_path):
    path = tmp_path / "studio.sqlite"
    v6_database(path)
    seqs = [row["seq"] for row in run_rows(path)]
    db = Database(path)
    try:
        assert db.insert_run_if_capacity(vector_run("d" * 32), cap=10)  # the old CHECK would have refused this
        newest = db.get_run("d" * 32)
        assert newest["mode"] == "vector" and newest["seq"] > max(seqs)  # numbering continues after the old runs, so the newest is still the newest
        rows, _ = db.list_runs(10, None, None)
        assert [r["id"] for r in rows] == ["d" * 32, RUN_EDIT, RUN_GENERATE]  # the history order of the old runs is as it was (the music run is in the bin)
    finally:
        db.close()


def test_the_copy_of_the_1_16_database_is_made_first_and_left_as_it_was(tmp_path):
    path = tmp_path / "studio.sqlite"
    v6_database(path)
    Database(path).close()
    copy = tmp_path / "studio.sqlite.before-schema-7"
    assert copy.is_file()
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "6"  # as it was
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
        with pytest.raises(sqlite3.IntegrityError):  # and it still has the old CHECK: an older studio can be run against it
            conn.execute("INSERT INTO runs (id, created_at, status, mode, prompt, effective_prompt, steps, seed, num_images, model_id, options_json) "
                         "VALUES (?, ?, 'queued', 'vector', 'p', 'p', 1, 1, 1, 'm', '{}')", ("e" * 32, NOW))
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='vectors'").fetchone()[0] == 0
    finally:
        conn.close()


# ------------------------------------------------------------------ every older database, and fresh ones
@pytest.mark.parametrize("build", [build for build, _ in OLD_DATABASES], ids=[name for _, name in OLD_DATABASES])
def test_a_migrated_database_is_the_same_as_a_fresh_one(tmp_path, build):
    migrated, fresh = tmp_path / "migrated.sqlite", tmp_path / "fresh.sqlite"
    build(migrated)
    kept = [(row["id"], row["created_at"], row["mode"]) for row in run_rows(migrated)]
    Database(migrated).close()
    # looked at after the FIRST start and before any second one: the next start creates a missing index by itself (`CREATE ... IF NOT EXISTS`), which
    # would hide a rebuild that forgot one
    Database(fresh).close()
    first_look = schema_names(migrated)
    assert first_look == schema_names(fresh)  # the same tables and indexes, with the same CREATE statements (the CHECKs included)
    for table in ("runs", "images", "tracks", "run_inputs", "projects", "vectors"):
        assert full_columns(migrated, table) == full_columns(fresh, table), table
    assert [(row["id"], row["created_at"], row["mode"]) for row in run_rows(migrated)] == kept  # no run was lost
    copy = tmp_path / "migrated.sqlite.before-schema-7"
    first = copy.read_bytes()
    Database(migrated).close()  # starting it again changes nothing: the copy is not made again, and nothing is rebuilt
    assert copy.read_bytes() == first and schema_names(migrated) == first_look


@pytest.mark.parametrize("build", [build for build, _ in OLD_DATABASES], ids=[name for _, name in OLD_DATABASES])
def test_every_older_database_gets_the_vector_mode_and_table(tmp_path, build):
    path = tmp_path / "studio.sqlite"
    build(path)
    db = Database(path)
    try:
        assert db.insert_run_if_capacity(vector_run("d" * 32), cap=10)  # the CHECK was widened (the old builders really have the old one)
        db.add_vector(vector_row("f" * 32, "d" * 32))
        assert [row["id"] for row in db.vectors_for_runs(["d" * 32])["d" * 32]] == ["f" * 32]
    finally:
        db.close()
    assert "vector" in sql_of(path, "runs")


def test_a_database_that_is_already_at_schema_7_is_not_rebuilt_again(tmp_path, monkeypatch):
    Database(tmp_path / "studio.sqlite").close()
    monkeypatch.setattr(Database, "_rebuild_runs", lambda self, previous: pytest.fail("a current database must not be rebuilt"))
    Database(tmp_path / "studio.sqlite").close()
    assert not list(tmp_path.glob("*before-schema*"))  # and no copy was made for it either


def test_the_rebuild_happens_once_and_after_the_copy(tmp_path, monkeypatch):
    """A 1.16 database is rebuilt exactly once, and only once the copy of it exists (a failed rebuild must have a way back)."""
    path = tmp_path / "studio.sqlite"
    v6_database(path)
    seen: list[tuple[int, bool]] = []
    original = Database._rebuild_runs

    def watching(self, previous):
        seen.append((previous, (tmp_path / "studio.sqlite.before-schema-7").is_file()))
        original(self, previous)

    monkeypatch.setattr(Database, "_rebuild_runs", watching)
    Database(path).close()
    assert seen == [(6, True)]


def test_a_failed_rebuild_leaves_the_database_as_it_was(tmp_path, monkeypatch):
    """The rebuild is one transaction: if it fails half way, `runs` is still the old table with every row, and the old schema number stands."""
    path = tmp_path / "studio.sqlite"
    v6_database(path)
    before = run_rows(path)

    def broken(self, previous):  # fails after the old table is dropped, the worst moment
        with self.tx() as c:
            c.execute("DROP TABLE runs")
            raise RuntimeError("the disk went away")

    monkeypatch.setattr(Database, "_rebuild_runs", broken)
    with pytest.raises(RuntimeError):
        Database(path)
    monkeypatch.undo()
    assert run_rows(path) == before
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "6"
    finally:
        conn.close()
    Database(path).close()  # and starting again, with the fault gone, finishes the job
    assert run_rows(path) == before


def test_a_rebuild_that_would_orphan_a_picture_is_refused(tmp_path):
    """The rebuild's own check: a picture that points at a run that does not exist would be found, and the migration stopped, not hidden."""
    path = tmp_path / "studio.sqlite"
    v6_database(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO images (id, run_id, kind, idx, seed, width, height, has_alpha, bytes, path, created_at) "
                 "VALUES (?, ?, 'output', 0, 0, 8, 8, 0, 1, 'x', '2026-10-01T10:00:00.000Z')", ("9" * 32, "f" * 32))  # a run that is not there
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseError, match="pointing at runs that are gone"):
        Database(path)


# ------------------------------------------------------------------ the vectors table
def test_the_vectors_table_has_the_columns_of_the_contract(tmp_path):
    Database(tmp_path / "studio.sqlite").close()
    assert [name for name, *_ in columns_of(tmp_path / "studio.sqlite", "vectors")] == [
        "id", "run_id", "idx", "seed", "width_mm", "height_mm", "colours", "layers", "bytes", "check_json", "svg_path", "zip_path", "png_path", "created_at"]
    types = {name: kind for name, kind, *_ in columns_of(tmp_path / "studio.sqlite", "vectors")}
    assert types["width_mm"] == types["height_mm"] == "REAL" and types["layers"] == types["bytes"] == "INTEGER" and types["check_json"] == "TEXT"
    assert "deleted_at" not in types  # a vector run is binned whole (§31.12.9 item 3): there is no per-version bin


def test_the_vectors_table_has_an_index_on_the_run(tmp_path):
    Database(tmp_path / "studio.sqlite").close()
    conn = sqlite3.connect(tmp_path / "studio.sqlite")
    try:
        indexes = [row[1] for row in conn.execute("PRAGMA index_list(vectors)")]
        assert any([row[2] for row in conn.execute(f"PRAGMA index_info({name})")][:1] == ["run_id"] for name in indexes)
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


def test_vectors_come_back_in_the_order_they_were_drawn_and_only_for_their_run(db):
    one, two, empty = "1" * 32, "2" * 32, "3" * 32
    for run_id in (one, two, empty):
        assert db.insert_run_if_capacity(vector_run(run_id), cap=10)
    db.add_vector(vector_row("b" * 32, one, 1))  # added out of order on purpose
    db.add_vector(vector_row("a" * 32, one, 0))
    db.add_vector(vector_row("c" * 32, two, 0))
    found = db.vectors_for_runs([one, two, empty])
    assert [row["id"] for row in found[one]] == ["a" * 32, "b" * 32] and [row["id"] for row in found[two]] == ["c" * 32] and found[empty] == []
    assert db.vectors_for_runs([]) == {}
    assert db.get_vector("c" * 32)["run_id"] == two and db.get_vector("d" * 32) is None


def test_the_row_keeps_what_it_was_given(db):
    assert db.insert_run_if_capacity(vector_run("1" * 32), cap=10)
    db.add_vector(vector_row("a" * 32, "1" * 32, 3, seed=99, width_mm=152.4, height_mm=101.6, colours=4, layers=3, bytes=18432))
    row = db.get_vector("a" * 32)
    assert (row["idx"], row["seed"], row["width_mm"], row["height_mm"], row["colours"], row["layers"], row["bytes"]) == (3, 99, 152.4, 101.6, 4, 3, 18432)
    assert json.loads(row["check_json"]) == {"colours_found": 2} and row["svg_path"].endswith("/3.svg") and row["created_at"] == NOW


def test_a_vector_needs_a_run(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.add_vector(vector_row("a" * 32, "9" * 32))  # no such run: the foreign key refuses it


def test_deleting_a_run_deletes_its_vectors_and_only_its_own(db):
    one, two = "1" * 32, "2" * 32
    for run_id in (one, two):
        assert db.insert_run_if_capacity(vector_run(run_id), cap=10)
    db.add_vector(vector_row("a" * 32, one))
    db.add_vector(vector_row("b" * 32, two))
    assert db.delete_run(one) == "deleted"
    assert db.get_vector("a" * 32) is None and db.get_vector("b" * 32) is not None


def test_the_cascade_removes_the_vectors_of_a_run_deleted_by_the_database_itself(db):
    assert db.insert_run_if_capacity(vector_run("1" * 32), cap=10)
    db.add_vector(vector_row("a" * 32, "1" * 32))
    with db.tx() as c:
        c.execute("DELETE FROM runs WHERE id=?", ("1" * 32,))  # not through `delete_run`: only the foreign key can remove the vector now
    assert db.get_vector("a" * 32) is None


def test_every_way_a_run_is_deleted_removes_its_vector_rows_even_where_foreign_keys_are_off(db):
    """`_delete_rows` removes what hangs off a run by hand, as it does for pictures and tracks, so a delete is complete whatever the connection's foreign
    key setting; with it off, the cascade cannot be what removes them."""
    db._conn.execute("PRAGMA foreign_keys=OFF")
    for n in range(1, 5):
        run_id = f"{n:032x}"
        assert db.insert_run_if_capacity(vector_run(run_id, status="queued"), cap=10)
        db.add_vector(vector_row(f"{n + 16:032x}", run_id))
    with db.tx() as c:
        c.execute("UPDATE runs SET status='done', finished_at=?, created_at=?, deleted_at=? WHERE id IN (?, ?)", (NOW, "2000-01-01T00:00:00.000Z", NOW,
                                                                                                                  f"{3:032x}", f"{4:032x}"))
        c.execute("UPDATE runs SET status='done', finished_at=?, created_at=? WHERE id IN (?, ?)", (NOW, "2000-01-01T00:00:00.000Z", f"{1:032x}", f"{2:032x}"))
    assert db.delete_run(f"{1:032x}") == "deleted"
    assert db.delete_expired("2001-01-01T00:00:00.000Z", 10) == [f"{2:032x}"]  # run 2 is old and not in the bin: the retention sweep takes it
    assert db.purge_bin(None, 10) == [f"{3:032x}", f"{4:032x}"]  # the bin's two
    assert db._one("SELECT COUNT(*) AS n FROM vectors")["n"] == 0


# ------------------------------------------------------------------ listing one tab (`GET /api/runs?kind=`)
def test_list_runs_can_be_narrowed_to_one_tab_and_pages_within_it(db):
    for n, mode in enumerate(["generate", "vector", "music", "vector", "edit", "vector"], start=1):
        made = vector_run(f"{n:032x}", mode=mode, created_at=f"2026-10-01T00:00:{n:02d}.000Z")
        assert db.insert_run_if_capacity(made, cap=10)
    ids = lambda rows: [int(row["id"], 16) for row in rows]  # noqa: E731 - the run's number, which is its place in the list above
    assert ids(db.list_runs(10, None, None, "vector")[0]) == [6, 4, 2]
    assert ids(db.list_runs(10, None, None, "image")[0]) == [5, 1]  # generate and edit are one tab
    assert ids(db.list_runs(10, None, None, "music")[0]) == [3]
    assert ids(db.list_runs(10, None, None, None)[0]) == [6, 5, 4, 3, 2, 1]  # no kind: every tab, as before
    page, more = db.list_runs(2, None, None, "vector")
    assert ids(page) == [6, 4] and more
    rest, more = db.list_runs(2, page[-1]["id"], None, "vector")
    assert ids(rest) == [2] and not more


def test_the_filter_and_the_tab_apply_together(db):
    for n, (mode, pinned) in enumerate([("vector", 1), ("vector", 0), ("generate", 1)], start=1):
        assert db.insert_run_if_capacity(vector_run(f"{n:032x}", mode=mode, pinned=pinned, created_at=f"2026-10-01T00:00:{n:02d}.000Z"), cap=10)
    from studio.runfilter import RunFilter
    rows, _ = db.list_runs(10, None, RunFilter(kept=True), "vector")
    assert [int(row["id"], 16) for row in rows] == [1]
