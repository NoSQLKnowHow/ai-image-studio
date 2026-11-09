"""Schema 10 (DESIGN.md §38.8): `runs` learns the mode 'speech' and two columns, and the Speech tab's tables arrive. The tests make a database as 1.19 left it
(schema 9, with the old CHECK on `runs.mode` that refuses 'speech' and without the new columns), open it with this build, and check that the upgrade keeps every
run and everything hung off it, makes the table the same as a new one, and leaves a copy to go back to. Then the tables' own rules.
"""

from __future__ import annotations

import re
import sqlite3

import pytest
from test_bin import columns
from test_builder_schema import NEW_TABLES, NOW, stored_version, table_names
from test_db import run_row

import studio.db as db_module
from studio.builder.store import BuilderStore
from studio.db import SCHEMA_VERSION, Database, DatabaseError
from studio.speech.schema import SPEECH_SCHEMA

SPEECH_TABLES = {"speech_parts", "speech_pieces", "speech_positions", "speech_speeds", "voices"}


def as_schema_9(path) -> None:
    """Make a database that this code made look as one from 1.19 does: none of the Speech tab's tables, `runs` rebuilt without its two last columns and with the
    CHECK on `mode` that does not know 'speech', and the stored version 9. The rebuild takes the table's own definition out of SQLite and edits it, so the
    result is the table as it was except for what schema 10 added, whatever the earlier schemas put in it."""
    conn = sqlite3.connect(path)
    try:
        for table in SPEECH_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        definition = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()[0]
        old = re.sub(r",\s*queue_key\s+REAL,\s*hidden\s+INTEGER NOT NULL DEFAULT 0", "", definition)
        old = old.replace(",'speech'", "").replace("CREATE TABLE runs (", "CREATE TABLE runs_9 (", 1)
        assert "speech" not in old and "queue_key" not in old and "hidden" not in old, old  # the edit really took
        kept = [r[1] for r in conn.execute("PRAGMA table_info(runs)") if r[1] not in ("queue_key", "hidden")]
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(old)
        conn.execute(f"INSERT INTO runs_9 ({', '.join(kept)}) SELECT {', '.join(kept)} FROM runs")
        conn.execute("DROP TABLE runs")
        conn.execute("ALTER TABLE runs_9 RENAME TO runs")
        conn.execute("CREATE INDEX idx_runs_status ON runs(status, seq)")
        conn.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
        conn.commit()
    finally:
        conn.close()


def indexes_of(path, table: str = "runs") -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA index_list({table})")}
    finally:
        conn.close()


# ---------------------------------------------------------------- a new database
def test_a_new_database_has_the_speech_tables_and_the_two_last_columns(tmp_path):
    path = tmp_path / "studio.sqlite"
    db = Database(path)
    try:
        assert SPEECH_TABLES <= table_names(path) and stored_version(path) == str(SCHEMA_VERSION) == "10"
        assert columns(path, "runs")[-2:] == [("queue_key", "REAL"), ("hidden", "INTEGER")]
        assert sqlite3.connect(path).execute("PRAGMA foreign_key_check").fetchall() == []
        db.read_one("SELECT * FROM runs WHERE mode='speech'")  # the query is valid; and the mode is one a run may have:
        with db.tx() as c:
            run = {**run_row(1), "mode": "speech"}
            c.execute(f"INSERT INTO runs ({', '.join(run)}) VALUES ({', '.join('?' for _ in run)})", tuple(run.values()))
        assert db.get_run(run_row(1)["id"])["mode"] == "speech"
    finally:
        db.close()


def test_every_statement_of_the_speech_schema_can_be_run_twice():
    """`IF NOT EXISTS` all through: opening a database again, or one from a build that already made some of the tables, changes nothing."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY)")
    conn.executescript(SPEECH_SCHEMA)
    conn.executescript(SPEECH_SCHEMA)


# ---------------------------------------------------------------- the upgrade from schema 9 (1.19)
def database_as_1_19(tmp_path):
    """A schema 9 database with runs of every kind, a project, a filed run, and a saved prompt: what an upgrade must not lose."""
    path = tmp_path / "studio.sqlite"
    fresh = tmp_path / "fresh.sqlite"
    Database(fresh).close()
    db = Database(path)
    project = "c" * 32
    assert db.create_project(project, "Logo", NOW) == "created"
    for n, mode in ((1, "generate"), (2, "music"), (3, "vector"), (4, "edit")):
        assert db.insert_run_if_capacity({**run_row(n), "mode": mode}, cap=100)
    assert db.file_run(run_row(2)["id"], project) == "filed"
    prompt = BuilderStore(db).create_prompt(now=NOW, title="Lighthouse", text="a lighthouse at dusk")
    before = [dict(r) for r in db.read_all("SELECT * FROM runs ORDER BY seq")]
    db.close()
    as_schema_9(path)
    return path, fresh, before, prompt


def test_a_1_19_database_is_upgraded_after_a_copy_and_keeps_everything(tmp_path):
    path, fresh, before, prompt = database_as_1_19(tmp_path)
    assert stored_version(path) == "9" and not (SPEECH_TABLES & table_names(path)) and "queue_key" not in dict(columns(path, "runs"))
    with pytest.raises(sqlite3.IntegrityError):  # the old table really does refuse the new mode: the rebuild below is not a no-op
        with sqlite3.connect(path) as conn:
            run = {**run_row(9), "mode": "speech"}
            conn.execute(f"INSERT INTO runs ({', '.join(run)}) VALUES ({', '.join('?' for _ in run)})", tuple(run.values()))

    migrated = Database(path)
    try:
        assert stored_version(path) == "10" and SPEECH_TABLES <= table_names(path)
        assert columns(path, "runs") == columns(fresh, "runs")  # the same columns, types and order as a new database: the rebuild gave what CREATE gives
        after = [dict(r) for r in migrated.read_all("SELECT * FROM runs ORDER BY seq")]
        without_new = lambda rows: [{k: v for k, v in row.items() if k not in ("queue_key", "hidden")} for row in rows]  # noqa: E731
        assert without_new(after) == without_new(before)  # every run, every column it had (`before` was read while the database still had the two new ones)
        assert all(row["queue_key"] is None and row["hidden"] == 0 for row in after)
        assert {"idx_runs_status", "idx_runs_deleted", "idx_runs_project", "idx_runs_prompt"} <= indexes_of(path)  # the rebuild dropped them; they are back
        assert [p["name"] for p in migrated.list_projects()] == ["Logo"] and migrated.list_projects()[0]["counts"]["music"] == 1
        assert BuilderStore(migrated).versions(prompt) and NEW_TABLES <= table_names(path)  # the Prompt builder's data is untouched
        assert sqlite3.connect(path).execute("PRAGMA foreign_key_check").fetchall() == []
        # and it works: a reading can be made, and its rows hang off it
        run = {**run_row(9), "mode": "speech"}
        assert migrated.insert_run_if_capacity(run, cap=100)
        with migrated.tx() as c:
            c.execute("INSERT INTO speech_parts (run_id, idx, title, chars) VALUES (?, 0, 'One', 10)", (run["id"],))
        assert migrated.get_run(run["id"])["mode"] == "speech"
    finally:
        migrated.close()
    copy = tmp_path / "studio.sqlite.before-schema-10"
    assert copy.exists() and stored_version(copy) == "9" and not (SPEECH_TABLES & table_names(copy))  # the database as 1.19 left it: a way back
    assert [r[0] for r in sqlite3.connect(copy).execute("SELECT id FROM runs ORDER BY seq")] == [run_row(n)["id"] for n in (1, 2, 3, 4)]


def test_the_upgrade_keeps_the_pictures_tracks_and_inputs_hanging_off_the_runs(tmp_path):
    """The rebuild drops `runs` and makes it again with foreign keys off; a mistake there would cascade away everything that refers to a run."""
    path = tmp_path / "studio.sqlite"
    db = Database(path)
    assert db.insert_run_if_capacity(run_row(1), cap=10)
    with db.tx() as c:
        c.execute("INSERT INTO images (id, run_id, kind, idx, seed, width, height, has_alpha, bytes, path, created_at) VALUES "
                  "('i1', ?, 'output', 0, 1, 8, 8, 0, 10, 'images/a.png', ?)", (run_row(1)["id"], NOW))
        c.execute("INSERT INTO tracks (id, run_id, idx, seed, seconds, sample_rate, channels, bytes, path, created_at) VALUES "
                  "('t1', ?, 0, 1, 1.0, 44100, 2, 10, 'audio/a.wav', ?)", (run_row(1)["id"], NOW))
    db.close()
    as_schema_9(path)
    migrated = Database(path)
    try:
        assert migrated.read_one("SELECT COUNT(*) AS n FROM images")["n"] == 1 and migrated.read_one("SELECT COUNT(*) AS n FROM tracks")["n"] == 1
    finally:
        migrated.close()


def test_an_existing_copy_is_never_replaced_by_a_later_attempt(tmp_path):
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    as_schema_9(path)
    keep = tmp_path / "studio.sqlite.before-schema-10"
    keep.write_bytes(b"the oldest copy")
    Database(path).close()
    assert keep.read_bytes() == b"the oldest copy"


def test_a_second_start_changes_nothing_and_makes_no_second_copy(tmp_path):
    path, _, _, _ = database_as_1_19(tmp_path)
    Database(path).close()
    first = (tmp_path / "studio.sqlite.before-schema-10").read_bytes()
    schema_after_first = sqlite3.connect(path).execute("SELECT group_concat(sql) FROM sqlite_master ORDER BY name").fetchone()
    Database(path).close()
    assert (tmp_path / "studio.sqlite.before-schema-10").read_bytes() == first
    assert sqlite3.connect(path).execute("SELECT group_concat(sql) FROM sqlite_master ORDER BY name").fetchone() == schema_after_first


def test_a_database_made_by_this_build_is_refused_by_1_19(tmp_path, monkeypatch):
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 9)
    with pytest.raises(DatabaseError, match="newer version of the studio"):
        Database(path)


# ---------------------------------------------------------------- the tables hold the rules themselves
@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


def make_run(db: Database, n: int = 1) -> str:
    assert db.insert_run_if_capacity({**run_row(n), "mode": "speech"}, cap=100)
    return run_row(n)["id"]


def insert(db: Database, sql: str, args: tuple = ()) -> None:
    with db.tx() as c:
        c.execute(sql, args)


PIECE = "INSERT INTO speech_pieces (run_id, part_idx, idx, kind, text, chars, seed{extra}) VALUES (?, 0, ?, ?, 'words', 5, 1{marks})"


def test_a_piece_is_a_paragraph_or_a_heading_and_queued_or_done_and_nothing_else(db):
    run = make_run(db)
    insert(db, PIECE.format(extra="", marks=""), (run, 0, "paragraph"))
    insert(db, PIECE.format(extra="", marks=""), (run, 1, "heading"))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, PIECE.format(extra="", marks=""), (run, 2, "poem"))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, PIECE.format(extra=", status", marks=", 'cooking'"), (run, 3, "paragraph"))
    assert db.read_one("SELECT status, continues, frames FROM speech_pieces WHERE idx=0")["status"] == "queued"  # what a new piece is


def test_a_part_has_each_piece_number_once_and_a_reading_each_part_number_once(db):
    run = make_run(db)
    insert(db, "INSERT INTO speech_parts (run_id, idx, title, chars) VALUES (?, 0, 'One', 5)", (run,))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "INSERT INTO speech_parts (run_id, idx, title, chars) VALUES (?, 0, 'Again', 5)", (run,))
    insert(db, PIECE.format(extra="", marks=""), (run, 0, "paragraph"))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, PIECE.format(extra="", marks=""), (run, 0, "paragraph"))


def test_the_rows_of_a_reading_go_with_the_reading(db):
    run, other = make_run(db), make_run(db, 2)
    for r in (run, other):
        insert(db, "INSERT INTO speech_parts (run_id, idx, title, chars) VALUES (?, 0, 'One', 5)", (r,))
        insert(db, PIECE.format(extra="", marks=""), (r, 0, "paragraph"))
        insert(db, "INSERT INTO speech_positions (run_id, part_idx, piece_idx, offset_ms, updated_at) VALUES (?, 0, 0, 0, ?)", (r, NOW))
    db.delete_expired("9999-01-01T00:00:00.000Z", 10)  # (a speech run never expires: nothing goes)
    assert db.read_one("SELECT COUNT(*) AS n FROM speech_pieces")["n"] == 2
    with db.tx() as c:
        db._delete_rows(c, [run])
    for table in ("speech_parts", "speech_pieces", "speech_positions"):
        assert db.read_one(f"SELECT COUNT(*) AS n, MIN(run_id) AS only FROM {table}")["only"] == other, table  # the other reading keeps its rows


def test_a_row_cannot_hang_off_a_reading_that_is_not_there(db):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "INSERT INTO speech_parts (run_id, idx, title, chars) VALUES ('nope', 0, 'One', 5)")


def test_a_voice_is_designed_or_cloned_and_its_name_is_its_own(db):
    row = "INSERT INTO voices (id, name, name_key, kind, engine, ref_text, ref_path, seconds, created_at) VALUES (?, ?, ?, ?, 'fake', 'said', 'p', 10.0, ?)"
    insert(db, row, ("a", "Mine", "mine", "designed", NOW))
    insert(db, row, ("b", "Yours", "yours", "cloned", NOW))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, row, ("c", "Other", "other", "recorded", NOW))  # a kind the studio does not have
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, row, ("d", "MINE", "mine", "cloned", NOW))  # "Mine" and "MINE" are one name (the key has the case folded away)
    assert db.read_one("SELECT consent_text, consent_at, description FROM voices WHERE id='a'")["consent_at"] is None


def test_one_position_for_each_reading(db):
    run = make_run(db)
    insert(db, "INSERT INTO speech_positions (run_id, part_idx, piece_idx, offset_ms, updated_at) VALUES (?, 0, 0, 0, ?)", (run, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "INSERT INTO speech_positions (run_id, part_idx, piece_idx, offset_ms, updated_at) VALUES (?, 1, 1, 1, ?)", (run, NOW))
