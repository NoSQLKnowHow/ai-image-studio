"""Schema 13 (DESIGN.md §45.7, criteria 400 and 421, 422): the tables of tags and notes and the word index over runs.

What these protect: a database made by the 1.24 studio (schema 11) is upgraded after a copy is kept, loses nothing, and ends up exactly the database a new one is (the same
tables, columns and indexes); **every run that already exists is found by a search afterwards** (a search that found only new runs would be a bug); the upgrade only ADDS tables,
so the `runs` table is as it was; a second start changes nothing and makes no second copy; an index that has fallen out of step is made again at the next start; and a SQLite
without FTS5 still opens the database, with the plain match standing in for the index.

A schema 11 database is made by taking a current one and putting it back: the three tables dropped and the version set to 11. That is what 1.24 left, and what the upgrade
is built to find."""

from __future__ import annotations

import json
import sqlite3

import pytest

from test_bin import columns
from test_builder_schema import stored_version
from test_db import run_row

from studio import db as db_module
from studio.db import SCHEMA_VERSION, Database
from studio.runfilter import RunFilter

NEW_TABLES = {"run_tags", "run_notes", "run_search"}


def sql_of_everything(path) -> list[tuple]:
    """Every table, index and view the database has, by name with the SQL that made it: what two databases must share to be the same database. (The FTS5 table's own shadow tables are in
    the list by name, so an index made the other way would show.)"""
    conn = sqlite3.connect(path)
    try:
        return sorted(conn.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
    finally:
        conn.close()


def as_schema_11(path) -> None:
    """Put a current database back to what schema 11 left: none of the new tables, version 11. Dropping the index table drops its shadow tables too; the two ordinary tables take their
    index with them."""
    conn = sqlite3.connect(path)
    try:
        for table in ("run_tags", "run_notes", "run_search"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
        conn.commit()
    finally:
        conn.close()


def database_as_1_24(tmp_path):
    """A schema 11 database holding three runs, in every way a run can differ for a search (a picture with a prompt, a music run whose words are in its lyrics, a reading with a source
    name, and a run in the bin), and the path of a new one made the same way, to compare with."""
    path, fresh = tmp_path / "studio.sqlite", tmp_path / "fresh.sqlite"
    Database(fresh).close()
    db = Database(path)
    try:
        picture = {**run_row(1), "prompt": "a lighthouse at dusk"}
        music = {**run_row(2), "mode": "music", "prompt": "calm piano", "lyrics": "twinkle twinkle little star", "width": None, "height": None}
        reading = {**run_row(3, "done"), "mode": "speech", "prompt": "Moby Dick", "options_json": json.dumps({"source": {"kind": "file", "name": "whale-book.epub"}}), "pinned": 1}
        for row in (picture, music, reading):
            assert db.insert_run_if_capacity(row, cap=100)
        assert db.bin_run(reading["id"], "2026-10-02T00:00:00.000Z") == "binned"
    finally:
        db.close()
    as_schema_11(path)
    return path, fresh


def found(db: Database, text: str) -> list[str]:
    """The prompts of the runs a search for `text` finds, newest first (the bin included, so that what the index holds is what is seen)."""
    rows, _more = db.list_runs(100, run_filter=RunFilter(q=text, deleted=None))
    return [row["prompt"] for row in rows]


def test_the_schema_is_13_and_a_new_database_has_the_three_tables_and_the_index(tmp_path):
    assert SCHEMA_VERSION == 13  # 1.26: tags, notes and the word index (1.24 made it 11; 1.25's pronunciation list and speakers made it 12)
    path = tmp_path / "studio.sqlite"
    db = Database(path)
    try:
        assert db.has_run_fts is True
    finally:
        db.close()
    names = {name for (_type, name, _sql) in sql_of_everything(path)}
    assert NEW_TABLES <= names and "idx_run_tags_tag" in names
    assert stored_version(path) == "13"
    assert not list(tmp_path.glob("*before-schema*"))  # a new database has nothing to keep a copy of


def test_a_schema_11_database_is_upgraded_after_a_copy_and_keeps_every_run(tmp_path):
    path, fresh = database_as_1_24(tmp_path)
    assert stored_version(path) == "11" and not NEW_TABLES & {name for (_t, name, _s) in sql_of_everything(path)}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    before = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY seq")]
    columns_before = columns(path, "runs")
    conn.close()

    migrated = Database(path)
    try:
        assert stored_version(path) == "13"
        # the same database as a new one makes: every table, column and index, with the SQL that made it
        assert sql_of_everything(path) == sql_of_everything(fresh)
        # and the `runs` table was not touched at all: the same columns, in the same order, and every run with every value it had (the upgrade only adds tables)
        assert columns(path, "runs") == columns_before == columns(fresh, "runs")
        assert [dict(r) for r in migrated.read_all("SELECT * FROM runs ORDER BY seq")] == before
        assert sqlite3.connect(path).execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        migrated.close()
    copy = tmp_path / "studio.sqlite.before-schema-13"
    assert copy.exists() and stored_version(copy) == "11" and not NEW_TABLES & {name for (_t, name, _s) in sql_of_everything(copy)}  # the database as 1.24 left it: a way back
    assert [r[0] for r in sqlite3.connect(copy).execute("SELECT id FROM runs ORDER BY seq")] == [run_row(n)["id"] for n in (1, 2, 3)]


def test_every_run_that_already_exists_is_found_after_the_upgrade(tmp_path):
    """The point of the upgrade step: the index is new, so it holds nothing until the upgrade fills it. A run made before 1.26 is found by its prompt, a music run by its lyrics, a reading by
    the name of its source, and a run in the bin by its words (the bin stays in the index, so Restore needs nothing)."""
    path, _fresh = database_as_1_24(tmp_path)
    db = Database(path)
    try:
        assert db.read_one("SELECT COUNT(*) AS n FROM run_search")["n"] == 3
        assert found(db, "lighthouse") == ["a lighthouse at dusk"]
        assert found(db, "twinkle") == ["calm piano"]  # the lyrics are not in the prompt column, and are searched
        assert found(db, "whale") == ["Moby Dick"] and found(db, "epub") == ["Moby Dick"]  # the source's name; the reading is in the bin
        # and the history search (the default filter) leaves the run in the bin out, as it always does
        rows, _more = db.list_runs(100, run_filter=RunFilter(q="moby"))
        assert rows == []
    finally:
        db.close()


def test_a_second_start_changes_nothing_and_makes_no_second_copy(tmp_path):
    path, _fresh = database_as_1_24(tmp_path)
    Database(path).close()
    copy = tmp_path / "studio.sqlite.before-schema-13"
    first_copy = copy.read_bytes()
    shape = sql_of_everything(path)
    again = Database(path)
    try:
        assert again.read_one("SELECT COUNT(*) AS n FROM run_search")["n"] == 3  # not indexed twice
    finally:
        again.close()
    assert copy.read_bytes() == first_copy and sql_of_everything(path) == shape


def test_a_1_19_database_reaches_schema_13_in_one_start_and_its_runs_are_found(tmp_path):
    """An old database goes through the rebuild of schema 10 (which makes a new `runs` table) and ends with the new tables and an index that holds its runs."""
    from test_speech_schema import database_as_1_19

    path, fresh, _before, _prompt = database_as_1_19(tmp_path)
    db = Database(path)
    try:
        assert stored_version(path) == "13"
        # the same database as a new one, except for how SQLite spells the name of a table that was rebuilt and renamed (`"runs"`, quoted): the rebuild is schema 10's, and its columns
        # are compared by `columns` instead
        same = lambda path: [row for row in sql_of_everything(path) if row[1] != "runs"]  # noqa: E731
        assert same(path) == same(fresh) and columns(path, "runs") == columns(fresh, "runs")
        count = db.read_one("SELECT COUNT(*) AS n FROM runs WHERE hidden = 0")["n"]
        assert db.read_one("SELECT COUNT(*) AS n FROM run_search")["n"] == count > 0
    finally:
        db.close()


def test_an_index_that_is_out_of_step_is_made_again_at_the_next_start(tmp_path):
    """Whatever left the index behind (a run inserted by hand, a restore from a copy of the files): at start-up it does not hold one row for each run, so it is rebuilt from the runs."""
    path = tmp_path / "studio.sqlite"
    db = Database(path)
    try:
        assert db.insert_run_if_capacity({**run_row(1), "prompt": "a red fox"}, cap=10)
        # a run written straight into the table, past the code that keeps the index (what a hand edit or a bug would do)
        with db.tx() as c:
            columns_ = ", ".join(run_row(2))
            c.execute(f"INSERT INTO runs ({columns_}) VALUES ({', '.join('?' for _ in run_row(2))})", tuple({**run_row(2), "prompt": "a grey wolf"}.values()))
        assert found(db, "wolf") == []  # not indexed: the search cannot see it
    finally:
        db.close()
    repaired = Database(path)
    try:
        assert found(repaired, "wolf") == ["a grey wolf"] and found(repaired, "fox") == ["a red fox"]
        assert repaired.read_one("SELECT COUNT(*) AS n FROM run_search")["n"] == 2
    finally:
        repaired.close()


def test_without_fts5_the_database_opens_and_a_search_is_a_plain_match(tmp_path, monkeypatch):
    """Where the SQLite in the image has no FTS5 the studio must still start (DESIGN.md §45.7): the statement that makes the index is refused, the studio goes on without one, and a search
    uses a plain match. The plain match finds a word anywhere in a word (more than the index would), and ignores case; tags and notes are found by it too."""
    monkeypatch.setattr(db_module, "RUN_SEARCH_SQL", "CREATE VIRTUAL TABLE IF NOT EXISTS run_search USING a_module_that_does_not_exist(run_id)")
    path = tmp_path / "studio.sqlite"
    db = Database(path)
    try:
        assert db.has_run_fts is False
        assert db.insert_run_if_capacity({**run_row(1), "prompt": "A Red Fox", "model_id": "m"}, cap=10)
        assert db.insert_run_if_capacity({**run_row(2), "prompt": "a lighthouse"}, cap=10)
        assert db.set_run_tags(run_row(2)["id"], ["winter"]) == "ok" and db.set_run_notes(run_row(2)["id"], "for the poster", "2026-10-02T00:00:00.000Z") == "ok"
        assert found(db, "fox") == ["A Red Fox"] and found(db, "RED") == ["A Red Fox"]
        assert found(db, "ox") == ["A Red Fox"]  # a word inside a word: what a plain match does, and the index does not
        assert found(db, "winter") == ["a lighthouse"] and found(db, "poster") == ["a lighthouse"]  # the tags and the notes are in it
        assert found(db, "red lighthouse") == []  # every word must still be found, and no run has both
        assert found(db, '"*-:') == ["a lighthouse", "A Red Fox"]  # no word in it: no search, as with the index
        counts = db.run_counts({"all": RunFilter(q="fox")})
        assert counts["image"]["all"] == 1
        assert db.reindex_runs() == 0  # nothing to index, and nothing breaks
        assert db.delete_run(run_row(1)["id"]) == "deleted"  # deleting needs no index
    finally:
        db.close()
    # and it opens again, still without one
    Database(path).close()


def test_a_fresh_database_with_fts5_and_one_without_have_the_same_ordinary_tables(tmp_path, monkeypatch):
    """The index is the only thing a SQLite without FTS5 lacks; every ordinary table is the same either way."""
    with_fts = tmp_path / "with.sqlite"
    Database(with_fts).close()
    monkeypatch.setattr(db_module, "RUN_SEARCH_SQL", "CREATE VIRTUAL TABLE IF NOT EXISTS run_search USING a_module_that_does_not_exist(run_id)")
    without = tmp_path / "without.sqlite"
    Database(without).close()
    ordinary = lambda path: [row for row in sql_of_everything(path) if not row[1].startswith("run_search")]  # noqa: E731
    assert ordinary(with_fts) == ordinary(without)


@pytest.mark.parametrize("previous", ["12", "11"])
def test_an_upgrade_from_12_or_11_indexes_every_run(tmp_path, previous):
    """12 is 1.25's schema (the pronunciation list and speakers), so a database may arrive at 13 from either: 'before 13' is the rule, not 'from 11'."""
    path, _fresh = database_as_1_24(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (previous,))
    conn.commit()
    conn.close()
    db = Database(path)
    try:
        assert db.read_one("SELECT COUNT(*) AS n FROM run_search")["n"] == 3 and stored_version(path) == "13"
    finally:
        db.close()
