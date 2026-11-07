"""Schema 9 (DESIGN.md §37.4; criterion 251): the Prompt builder's tables and the two columns of `runs`, the copy made first, and the refusal by an older studio."""

from __future__ import annotations

import sqlite3

import pytest
from test_bin import columns
from test_db import run_row

import studio.db as db_module
from studio.builder.schema import BUILDER_SCHEMA, DETAILS, FOR_KINDS, PREFERENCE_SOURCES, ROLES, SOURCES
from studio.builder.store import BuilderStore
from studio.db import SCHEMA_VERSION, Database, DatabaseError

NEW_TABLES = {"prompts", "prompt_versions", "prompt_tags", "chats", "chat_messages", "chat_attachments", "preferences"}
NOW = "2026-11-01T10:00:00.000Z"


def table_names(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def stored_version(path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    finally:
        conn.close()


def as_schema_8(path) -> None:
    """Make a database that this code made look as one from 1.18 does: none of the builder's tables or its index, `runs` without its two last columns and
    their index, and the stored version 8."""
    if sqlite3.sqlite_version_info < (3, 35):
        pytest.skip("this SQLite cannot DROP COLUMN, which is how the test makes the old table")
    conn = sqlite3.connect(path)
    try:
        for table in ("prompt_search", "prompt_tags", "prompt_versions", "chat_attachments", "chat_messages", "chats", "prompts", "preferences"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("DROP INDEX IF EXISTS idx_runs_prompt")
        conn.execute("ALTER TABLE runs DROP COLUMN prompt_version")
        conn.execute("ALTER TABLE runs DROP COLUMN prompt_id")
        conn.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------ a new database
def test_the_schema_is_9_and_a_new_database_has_the_builders_tables_and_runs_columns(tmp_path):
    assert SCHEMA_VERSION == 9
    path = tmp_path / "studio.sqlite"
    db = Database(path)
    try:
        assert NEW_TABLES <= table_names(path) and "prompt_search" in table_names(path) and db.has_fts5 is True
        assert [c for c in columns(path, "runs")][-3:] == [("project_id", "TEXT"), ("prompt_id", "TEXT"), ("prompt_version", "INTEGER")]
        assert stored_version(path) == "9"
        assert sqlite3.connect(path).execute("PRAGMA foreign_key_check").fetchall() == []
        assert not (tmp_path / "studio.sqlite.before-schema-9").exists()  # a new database has nothing to keep a copy of
    finally:
        db.close()


def test_every_statement_of_the_builders_schema_can_be_run_twice():
    """`IF NOT EXISTS` all through: opening a database again, or one from a build that already made some of them, changes nothing."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(BUILDER_SCHEMA)
    conn.executescript(BUILDER_SCHEMA)


# ------------------------------------------------------------------ the upgrade from schema 8 (1.18)
def test_a_1_18_database_is_upgraded_after_a_copy_and_keeps_every_run_and_project(tmp_path):
    path = tmp_path / "studio.sqlite"
    fresh = tmp_path / "fresh.sqlite"
    Database(fresh).close()
    db = Database(path)
    project = "c" * 32
    assert db.create_project(project, "Logo", NOW) == "created"
    for n in (1, 2, 3):
        assert db.insert_run_if_capacity(run_row(n), cap=100)
    assert db.file_run(run_row(2)["id"], project) == "filed"
    before = [dict(r) for r in db.read_all("SELECT * FROM runs ORDER BY seq")]
    db.close()
    as_schema_8(path)
    assert "prompt_id" not in dict(columns(path, "runs")) and not (NEW_TABLES & table_names(path))

    migrated = Database(path)
    try:
        assert stored_version(path) == "9" and NEW_TABLES <= table_names(path)
        assert columns(path, "runs") == columns(fresh, "runs")  # the same columns, the same types, in the same order: ALTER TABLE gave what CREATE TABLE gives
        after = [dict(r) for r in migrated.read_all("SELECT * FROM runs ORDER BY seq")]
        assert [{k: v for k, v in row.items() if k not in ("prompt_id", "prompt_version")} for row in after] == [
            {k: v for k, v in row.items() if k not in ("prompt_id", "prompt_version")} for row in before]  # every run, every column
        assert all(row["prompt_id"] is None and row["prompt_version"] is None for row in after)
        assert [p["name"] for p in migrated.list_projects()] == ["Logo"] and migrated.list_projects()[0]["counts"]["image"] == 1
        indexes = {r[1] for r in sqlite3.connect(path).execute("PRAGMA index_list(runs)")}
        assert "idx_runs_prompt" in indexes
        # and it works: a prompt can be made and a run can carry it
        store = BuilderStore(migrated)
        prompt = store.create_prompt(now=NOW, title="Lighthouse", text="a lighthouse at dusk")
        with migrated.tx() as c:
            c.execute("UPDATE runs SET prompt_id=?, prompt_version=1 WHERE id=?", (prompt, run_row(1)["id"]))
        assert migrated.get_run(run_row(1)["id"])["prompt_id"] == prompt
    finally:
        migrated.close()
    copy = tmp_path / "studio.sqlite.before-schema-9"
    assert copy.exists() and not (NEW_TABLES & table_names(copy)) and stored_version(copy) == "8"  # the database as 1.18 left it: a way back
    assert [r[0] for r in sqlite3.connect(copy).execute("SELECT id FROM runs ORDER BY seq")] == [run_row(n)["id"] for n in (1, 2, 3)]


def test_an_existing_copy_is_never_replaced_by_a_later_attempt(tmp_path):
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    as_schema_8(path)
    keep = tmp_path / "studio.sqlite.before-schema-9"
    keep.write_bytes(b"the oldest copy")
    Database(path).close()
    assert keep.read_bytes() == b"the oldest copy"


def test_a_database_made_by_this_build_is_refused_by_an_older_one(tmp_path, monkeypatch):
    """The refusal is the check every earlier upgrade relies on: a stored version above the build's own. 1.18 is this code with its number at 8."""
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 8)
    with pytest.raises(DatabaseError, match="newer version of the studio"):
        Database(path)


# ------------------------------------------------------------------ without FTS5
def test_a_sqlite_without_fts5_still_opens_and_keeps_prompts_without_an_index(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "PROMPT_SEARCH_SQL", "CREATE VIRTUAL TABLE IF NOT EXISTS prompt_search USING no_such_module(prompt_id)")
    path = tmp_path / "studio.sqlite"
    db = Database(path)
    try:
        assert db.has_fts5 is False and "prompt_search" not in table_names(path)
        store = BuilderStore(db)
        prompt = store.create_prompt(now=NOW, title="Fox", text="a fox in snow", tags=["animals"])
        assert store.tags(prompt) == ["animals"] and store.ensure_search_in_step() is False and store.reindex_all() == 0
        assert store.delete_item("prompt", prompt) == "deleted"
    finally:
        db.close()


# ------------------------------------------------------------------ the tables hold the rules themselves (whoever writes to them)
@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


def insert(db: Database, sql: str, args: tuple = ()) -> None:
    with db.tx() as c:
        c.execute(sql, args)


def make_prompt(db: Database, prompt_id: str = "p") -> str:
    insert(db, "INSERT INTO prompts (id, title, created_at, updated_at) VALUES (?, 't', ?, ?)", (prompt_id, NOW, NOW))
    return prompt_id


def make_chat(db: Database, chat_id: str = "c") -> str:
    insert(db, "INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, 't', ?, ?)", (chat_id, NOW, NOW))
    return chat_id


@pytest.mark.parametrize("value", FOR_KINDS)
def test_every_kind_a_prompt_or_chat_can_be_for_is_accepted(db, value):
    insert(db, "INSERT INTO prompts (id, title, for_kind, created_at, updated_at) VALUES ('p', 't', ?, ?, ?)", (value, NOW, NOW))
    insert(db, "INSERT INTO chats (id, title, for_kind, created_at, updated_at) VALUES ('c', 't', ?, ?, ?)", (value, NOW, NOW))


@pytest.mark.parametrize("value", DETAILS)
def test_every_detail_level_is_accepted(db, value):
    insert(db, "INSERT INTO chats (id, title, detail, created_at, updated_at) VALUES ('c', 't', ?, ?, ?)", (value, NOW, NOW))


@pytest.mark.parametrize("value", SOURCES)
def test_every_source_of_a_version_is_accepted(db, value):
    """builder, hand, wording, revision (DESIGN.md §37.3 rule 2): the schema and the store say the same four."""
    prompt = make_prompt(db)
    insert(db, "INSERT INTO prompt_versions (id, prompt_id, n, text, source, created_at) VALUES ('v', ?, 1, 'x', ?, ?)", (prompt, value, NOW))
    assert BuilderStore(db).create_prompt(now=NOW, title="t", text="x", source=value)


@pytest.mark.parametrize("value", ROLES)
def test_every_role_of_a_message_is_accepted(db, value):
    chat = make_chat(db)
    insert(db, "INSERT INTO chat_messages (id, chat_id, seq, role, text, created_at) VALUES ('m', ?, 1, ?, 'x', ?)", (chat, value, NOW))


@pytest.mark.parametrize("value", PREFERENCE_SOURCES)
def test_every_source_of_a_preference_is_accepted(db, value):
    insert(db, "INSERT INTO preferences (id, text, source, created_at) VALUES ('f', 'x', ?, ?)", (value, NOW))


def test_the_sets_are_the_ones_the_spec_names():
    assert (FOR_KINDS, DETAILS, SOURCES, ROLES, PREFERENCE_SOURCES) == (
        ("images", "vector", "music", "edit"), ("short", "normal", "rich"), ("builder", "hand", "wording", "revision"), ("user", "assistant"), ("hand", "suggestion"))


@pytest.mark.parametrize("sql,args", [
    ("INSERT INTO prompts (id, title, for_kind, created_at, updated_at) VALUES ('p', 't', 'sculptures', ?, ?)", (NOW, NOW)),
    ("INSERT INTO chats (id, title, for_kind, created_at, updated_at) VALUES ('c', 't', 'sculptures', ?, ?)", (NOW, NOW)),
    ("INSERT INTO chats (id, title, detail, created_at, updated_at) VALUES ('c', 't', 'huge', ?, ?)", (NOW, NOW)),
    ("INSERT INTO preferences (id, text, source, created_at) VALUES ('f', 'x', 'telepathy', ?)", (NOW,)),
])
def test_a_value_outside_the_sets_is_refused_by_the_table(db, sql, args):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, sql, args)


def test_a_version_source_a_role_and_a_number_below_one_are_refused_by_the_table(db):
    prompt, chat = make_prompt(db), make_chat(db)
    for sql, args in [
        ("INSERT INTO prompt_versions (id, prompt_id, n, text, source, created_at) VALUES ('v', ?, 1, 'x', 'magic', ?)", (prompt, NOW)),
        ("INSERT INTO prompt_versions (id, prompt_id, n, text, source, created_at) VALUES ('v', ?, 0, 'x', 'hand', ?)", (prompt, NOW)),
        ("INSERT INTO chat_messages (id, chat_id, seq, role, text, created_at) VALUES ('m', ?, 1, 'system', 'x', ?)", (chat, NOW)),
        ("INSERT INTO chat_messages (id, chat_id, seq, role, text, created_at) VALUES ('m', ?, 0, 'user', 'x', ?)", (chat, NOW)),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, sql, args)
    assert db.read_one("SELECT COUNT(*) AS n FROM prompt_versions")["n"] == 0 and db.read_one("SELECT COUNT(*) AS n FROM chat_messages")["n"] == 0


def test_a_chat_has_each_message_number_once_and_a_prompt_each_version_number_once(db):
    prompt, chat = make_prompt(db), make_chat(db)
    other_chat = make_chat(db, "d")
    insert(db, "INSERT INTO chat_messages (id, chat_id, seq, role, text, created_at) VALUES ('m1', ?, 1, 'user', 'x', ?)", (chat, NOW))
    insert(db, "INSERT INTO chat_messages (id, chat_id, seq, role, text, created_at) VALUES ('m2', ?, 1, 'user', 'x', ?)", (other_chat, NOW))  # another chat's own 1
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "INSERT INTO chat_messages (id, chat_id, seq, role, text, created_at) VALUES ('m3', ?, 1, 'assistant', 'y', ?)", (chat, NOW))
    other_prompt = make_prompt(db, "q")
    insert(db, "INSERT INTO prompt_versions (id, prompt_id, n, text, source, created_at) VALUES ('a', ?, 1, 'x', 'hand', ?)", (prompt, NOW))
    insert(db, "INSERT INTO prompt_versions (id, prompt_id, n, text, source, created_at) VALUES ('b', ?, 1, 'x', 'hand', ?)", (other_prompt, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "INSERT INTO prompt_versions (id, prompt_id, n, text, source, created_at) VALUES ('c', ?, 1, 'z', 'hand', ?)", (prompt, NOW))


def test_what_a_row_gets_when_nothing_is_said(db):
    """The defaults (DESIGN.md §37.4): a new preference is ON, a new prompt is not a favourite, a chat is for Images at Normal detail, nothing is in the bin."""
    make_prompt(db), make_chat(db)
    insert(db, "INSERT INTO preferences (id, text, created_at) VALUES ('f', 'always 16:9', ?)", (NOW,))
    prompt, chat, preference = db.read_one("SELECT * FROM prompts"), db.read_one("SELECT * FROM chats"), db.read_one("SELECT * FROM preferences")
    assert (prompt["favourite"], prompt["for_kind"], prompt["deleted_at"], prompt["restored_at"], prompt["project_id"]) == (0, "images", None, None, None)
    assert (chat["detail"], chat["for_kind"], chat["deleted_at"], chat["prompt_id"], chat["project_id"]) == ("normal", "images", None, None, None)
    assert (preference["enabled"], preference["source"], preference["position"], preference["project_id"]) == (1, "hand", 0, None)
    insert(db, "INSERT INTO prompt_versions (id, prompt_id, n, text, source, created_at) VALUES ('v', 'p', 1, 'x', 'hand', ?)", (NOW,))
    assert db.read_one("SELECT settings, negative, note, chat_id FROM prompt_versions")["settings"] == "{}"


def test_the_word_index_folds_accents_and_case_at_the_level_of_the_table(db):
    insert(db, "INSERT INTO prompt_search (prompt_id, title, text, tags) VALUES ('p', 'Café', 'Zürich at night', '')")
    for word in ("cafe", "CAFÉ", "zurich", "ZÜRICH", "night"):
        assert [r["prompt_id"] for r in db.read_all("SELECT prompt_id FROM prompt_search WHERE prompt_search MATCH ?", (word,))] == ["p"], word
