"""Schema 11 (DESIGN.md §43.4, criterion 340): the two columns that say where a run was made More like this of, and the family it belongs to.

What these protect: a database made by the 1.22 studio (schema 10) is upgraded after a copy is kept, loses nothing, and ends up exactly the table a new database has
(the same columns, types and order: `ALTER TABLE` and `CREATE TABLE` must give one table, which is why the columns are the last two and the index is made after them); a
second start changes nothing and makes no second copy; and the link works once the columns are there.

A schema 10 database is made by taking a current one and putting it back: the index and the two columns dropped and the version set to 10. That is the table 1.22 had, and
it is what the upgrade is built to find.

1.25 moved the schema on to 12 (DESIGN.md §44.6: `speech_pieces.speaker` and the `speech_pronunciations` table), so what these tests pin, deliberately, is now: the
version a database ends at is 12; the copy kept before an upgrade is named for the version it was upgraded TO (`before-schema-12`) and holds the database as it was
(still version 10); and the schema 10 database they start from has what schema 12 added taken away as well, so that it is a faithful 1.22 database. The two columns of schema 11
are still the ones this file is about. Schema 12's own checks are in test_speech_schema12.py."""

from __future__ import annotations

import sqlite3

from test_bin import columns
from test_builder_schema import stored_version
from test_db import run_row

from studio.db import SCHEMA_VERSION, Database
from studio.runfilter import RunFilter

NEW_COLUMNS = [("variation_json", "TEXT"), ("family_id", "TEXT")]


def indexes_of(path) -> set[str]:
    return {row[0] for row in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='index'")}


def as_schema_10(path) -> None:
    """Put a current database back to what schema 10 left: no family index, no `variation_json` or `family_id`, version 10. Since 1.25 that also means no
    `speech_pieces.speaker` and no `speech_pronunciations` table (schema 12), which a 1.22 database never had."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP TABLE speech_pronunciations")
        conn.execute("ALTER TABLE speech_pieces DROP COLUMN speaker")
        conn.execute("DROP INDEX idx_runs_family")
        conn.execute("ALTER TABLE runs DROP COLUMN variation_json")
        conn.execute("ALTER TABLE runs DROP COLUMN family_id")
        conn.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
        conn.commit()
    finally:
        conn.close()


def database_as_1_22(tmp_path):
    """A schema 10 database holding two runs (one of them kept) and the path of a new one made the same way, to compare the table with."""
    path, fresh = tmp_path / "studio.sqlite", tmp_path / "fresh.sqlite"
    Database(fresh).close()
    db = Database(path)
    try:
        for n in (1, 2):
            assert db.insert_run_if_capacity({**run_row(n), "pinned": int(n == 1)}, cap=100)
    finally:
        db.close()
    as_schema_10(path)
    return path, fresh


def test_a_new_database_has_the_two_columns_of_schema_11_last_and_the_index(tmp_path):
    assert SCHEMA_VERSION == 12  # schema 11 added these columns; 1.25 is at 12 (test_speech_schema12.py)
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    assert columns(path, "runs")[-2:] == NEW_COLUMNS
    assert stored_version(path) == "12" and "idx_runs_family" in indexes_of(path)


def test_a_schema_10_database_is_upgraded_after_a_copy_and_keeps_every_run(tmp_path):
    path, fresh = database_as_1_22(tmp_path)
    assert stored_version(path) == "10" and not dict(columns(path, "runs")).keys() & {"variation_json", "family_id"}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    before = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY seq")]
    conn.close()

    migrated = Database(path)
    try:
        assert stored_version(path) == "12"
        assert columns(path, "runs") == columns(fresh, "runs")  # the same table as a new database makes, in the same order
        after = [dict(r) for r in migrated.read_all("SELECT * FROM runs ORDER BY seq")]
        assert [{k: v for k, v in row.items() if k not in ("variation_json", "family_id")} for row in after] == before  # every run, every column it had
        assert all(row["variation_json"] is None and row["family_id"] is None for row in after)  # and none is a variation
        assert "idx_runs_family" in indexes_of(path)
        assert sqlite3.connect(path).execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrated.get_run(run_row(1)["id"])["pinned"] == 1  # a column that is easy to lose in a careless rebuild
    finally:
        migrated.close()
    copy = tmp_path / "studio.sqlite.before-schema-12"  # named for the version the upgrade went TO
    assert copy.exists() and stored_version(copy) == "10" and "variation_json" not in dict(columns(copy, "runs"))  # the database as 1.22 left it: a way back
    assert [r[0] for r in sqlite3.connect(copy).execute("SELECT id FROM runs ORDER BY seq")] == [run_row(n)["id"] for n in (1, 2)]


def test_after_the_upgrade_a_link_can_be_stored_read_back_and_listed_by_family(tmp_path):
    path, _ = database_as_1_22(tmp_path)
    db = Database(path)
    try:
        first, second = run_row(1)["id"], run_row(2)["id"]
        link = '{"run_id": "%s", "image_id": null, "how": "seeds"}' % first
        with db.tx() as c:
            c.execute("UPDATE runs SET variation_json=?, family_id=? WHERE id=?", (link, first, second))
        assert db.get_run(second)["family_id"] == first
        assert db.variation_refs_for_runs([db.get_run(second)])[second]["source"]["state"] == "present"
        rows, _more = db.list_runs(10, run_filter=RunFilter(family=first))
        assert {row["id"] for row in rows} == {first, second}  # the first run itself, and the one made from it
        assert db.variation_counts_for_runs([first, second]) == {first: 1}
    finally:
        db.close()


def test_a_second_start_changes_nothing_and_makes_no_second_copy(tmp_path):
    path, _ = database_as_1_22(tmp_path)
    Database(path).close()
    copy = tmp_path / "studio.sqlite.before-schema-12"
    first_copy = copy.read_bytes()
    shape = sqlite3.connect(path).execute("SELECT group_concat(sql) FROM sqlite_master ORDER BY name").fetchone()
    Database(path).close()
    assert copy.read_bytes() == first_copy
    assert sqlite3.connect(path).execute("SELECT group_concat(sql) FROM sqlite_master ORDER BY name").fetchone() == shape


def test_a_1_19_database_reaches_the_current_schema_in_one_start(tmp_path):
    """An old database goes through the rebuild of schema 10 and ends with the new columns too: the rebuild uses the current definition, which has them."""
    from test_speech_schema import database_as_1_19

    path, fresh, _before, _prompt = database_as_1_19(tmp_path)
    Database(path).close()
    assert stored_version(path) == "12" and columns(path, "runs") == columns(fresh, "runs") and columns(path, "runs")[-2:] == NEW_COLUMNS
