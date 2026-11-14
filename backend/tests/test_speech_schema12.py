"""Schema 12 (DESIGN.md §44.6, criterion 353): the pronunciation list's table and the column that says who speaks a piece.

What these protect: a database made by the 1.24 studio (schema 11) is upgraded after a copy is kept, loses no run, no piece and no voice, and ends up exactly the tables a new
database has (the same columns, types and order: `ALTER TABLE ADD COLUMN` and `CREATE TABLE` must give one table, which is why `speaker` is the LAST column of
`speech_pieces`); a second start changes nothing and makes no second copy; a database this build made is refused by the 1.24 studio, as every schema is; and the new
table and column work once they are there (rules the pronunciation list depends on are in the table itself: one entry for each word once case is folded away).

A schema 11 database is made by taking a current one and putting it back: the table and the column dropped and the version set to 11. That is what 1.24 had, and it is
what the upgrade is built to find."""

from __future__ import annotations

import json
import sqlite3

import pytest
from test_bin import columns
from test_builder_schema import stored_version
from test_db import run_row

from studio import db as db_module
from studio.db import SCHEMA_VERSION, Database, DatabaseError
from studio.speech.store import SpeechStore


def table_names(path) -> set[str]:
    return {row[0] for row in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")}


def as_schema_11(path) -> None:
    """Put a current database back to what schema 11 left: no `speech_pronunciations` table, no `speech_pieces.speaker`, version 11."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP TABLE speech_pronunciations")
        conn.execute("ALTER TABLE speech_pieces DROP COLUMN speaker")
        conn.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
        conn.commit()
    finally:
        conn.close()


def database_as_1_24(tmp_path):
    """A schema 11 database holding a reading (two pieces, one of them made), a voice and a position, and the path of a new database made the same way, to compare with."""
    path, fresh = tmp_path / "studio.sqlite", tmp_path / "fresh.sqlite"
    Database(fresh).close()
    db = Database(path)
    try:
        run = {**run_row(1), "mode": "speech", "options_json": json.dumps({"task": "reading", "voice": {"kind": "preset", "id": "anna"}})}
        parts = [{"run_id": run["id"], "idx": 0, "title": "One", "chars": 20}]
        pieces = [{"run_id": run["id"], "part_idx": 0, "idx": n, "kind": "paragraph", "text": f"Words {n}.", "chars": 8, "continues": 0, "seed": n} for n in (0, 1)]
        assert SpeechStore(db).insert_reading(run, parts, pieces, cap=100) == "ok"
        SpeechStore(db).mark_piece_done(run["id"], 0, 0, 4800, 24000, 9600, "speech/readings/x/0000/00000.wav")
        with db.tx() as c:
            c.execute("INSERT INTO voices (id, name, name_key, kind, engine, ref_text, ref_path, seconds, created_at) "
                      "VALUES ('v1', 'Mine', 'mine', 'cloned', 'fake', 'words', 'speech/voices/v1/reference.wav', 9.0, '2026-10-01T00:00:00Z')")
    finally:
        db.close()
    as_schema_11(path)
    return path, fresh


def test_the_schema_is_12_and_a_new_database_has_the_table_and_the_column_last(tmp_path):
    assert SCHEMA_VERSION == 12
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    assert "speech_pronunciations" in table_names(path)
    assert columns(path, "speech_pieces")[-1] == ("speaker", "TEXT")  # last, so that ALTER TABLE on an older database gives the same table
    assert [c[0] for c in columns(path, "speech_pronunciations")] == ["id", "word", "word_key", "say", "match_case", "created_at", "updated_at"]
    assert stored_version(path) == "12"


def test_a_schema_11_database_is_upgraded_after_a_copy_and_keeps_every_row(tmp_path):
    path, fresh = database_as_1_24(tmp_path)
    assert stored_version(path) == "11" and "speaker" not in dict(columns(path, "speech_pieces")) and "speech_pronunciations" not in table_names(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    pieces_before = [dict(r) for r in conn.execute("SELECT * FROM speech_pieces ORDER BY part_idx, idx")]
    runs_before = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY seq")]
    conn.close()

    migrated = Database(path)
    try:
        assert stored_version(path) == "12"
        # the two tables are what a new database makes, in the same order of columns
        assert columns(path, "speech_pieces") == columns(fresh, "speech_pieces")
        assert columns(path, "speech_pronunciations") == columns(fresh, "speech_pronunciations")
        pieces_after = [dict(r) for r in migrated.read_all("SELECT * FROM speech_pieces ORDER BY part_idx, idx")]
        assert [{k: v for k, v in row.items() if k != "speaker"} for row in pieces_after] == pieces_before  # every piece, every column it had, the made one still made
        assert all(row["speaker"] is None for row in pieces_after)  # and every piece the narrator's
        assert [dict(r) for r in migrated.read_all("SELECT * FROM runs ORDER BY seq")] == runs_before  # no run touched
        assert migrated.read_one("SELECT name FROM voices WHERE id='v1'")["name"] == "Mine"  # a voice is not lost
        assert sqlite3.connect(path).execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        migrated.close()
    copy = tmp_path / "studio.sqlite.before-schema-12"
    assert copy.exists() and stored_version(copy) == "11" and "speaker" not in dict(columns(copy, "speech_pieces"))  # the database as 1.24 left it: a way back
    assert [r[0] for r in sqlite3.connect(copy).execute("SELECT id FROM runs")] == [run_row(1)["id"]]


def test_a_second_start_changes_nothing_and_makes_no_second_copy(tmp_path):
    path, _ = database_as_1_24(tmp_path)
    Database(path).close()
    copy = tmp_path / "studio.sqlite.before-schema-12"
    first_copy = copy.read_bytes()
    shape = sqlite3.connect(path).execute("SELECT group_concat(sql) FROM sqlite_master ORDER BY name").fetchone()
    Database(path).close()
    assert copy.read_bytes() == first_copy
    assert sqlite3.connect(path).execute("SELECT group_concat(sql) FROM sqlite_master ORDER BY name").fetchone() == shape


def test_a_database_made_by_this_build_is_refused_by_1_24(tmp_path, monkeypatch):
    """An older studio that opens this database would write rows without the column and lose the list, so it refuses, as every schema has (criterion 353)."""
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 11)
    with pytest.raises(DatabaseError, match="newer version of the studio"):
        Database(path)


def test_a_new_database_that_was_never_at_schema_11_is_not_given_a_copy(tmp_path):
    Database(tmp_path / "studio.sqlite").close()
    assert not list(tmp_path.glob("*.before-schema-*"))


# ---------------------------------------------------------------- what the table holds itself
@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


def entry(n: int, word: str, key: str = "") -> dict:
    return {"id": f"{n:032x}", "word": word, "word_key": key or word.casefold(), "say": "x", "match_case": 0, "created_at": "2026-11-14T00:00:00Z", "updated_at": "2026-11-14T00:00:00Z"}


def test_there_is_one_entry_for_each_word_once_case_is_folded_away(db):
    """The rule that keeps two entries from ever fighting over a text (§44.8 choice 4) is held by the table, not only by the route."""
    store = SpeechStore(db)
    assert store.insert_pronunciation(entry(1, "Nguyen"), cap=10) == "ok"
    assert store.insert_pronunciation(entry(2, "NGUYEN"), cap=10) == "word_taken"
    assert [r["word"] for r in store.pronunciations()] == ["Nguyen"]


def test_a_piece_can_name_its_speaker_and_the_name_is_kept(db):
    store = SpeechStore(db)
    run = {**run_row(1), "mode": "speech", "options_json": "{}"}
    pieces = [{"run_id": run["id"], "part_idx": 0, "idx": n, "kind": "paragraph", "text": "Hello there.", "chars": 12, "continues": 0, "seed": n, "speaker": who}
              for n, who in enumerate(["Alice", None])]
    assert store.insert_reading(run, [{"run_id": run["id"], "idx": 0, "title": "P", "chars": 24}], pieces, cap=100) == "ok"
    assert [r["speaker"] for r in store.pieces(run["id"], 0)] == ["Alice", None]
