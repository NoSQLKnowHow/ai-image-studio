"""Schema 3 (DESIGN.md §26.5): the migration of a database written by the real 1.7 code, the tracks table, and what a
database made fresh looks like. The 1.7 schema below is copied from `studio/db.py` at that release."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from studio.db import SCHEMA_VERSION, Database

V2_SCHEMA = """
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
RUN_A, RUN_B = "a" * 32, "b" * 32
IMAGE_OUT, IMAGE_IN = "c" * 32, "d" * 32


def make_v2(path: Path) -> None:
    """A schema 2 database as 1.7 leaves it: two runs (one an edit with an input), their images, and a few rows that
    would show up if the migration lost or mangled anything."""
    conn = sqlite3.connect(path)
    conn.executescript(V2_SCHEMA)
    conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    conn.execute(
        "INSERT INTO runs (id, created_at, started_at, finished_at, status, mode, prompt, effective_prompt, steps, seed, "
        "num_images, model_id, pinned, options_json) VALUES (?, '2026-10-01T10:00:00.000Z', '2026-10-01T10:00:01.000Z', "
        "'2026-10-01T10:00:09.000Z', 'done', 'generate', 'a lighthouse', 'a lighthouse', 20, 5, 1, 'm', 1, ?)",
        (RUN_A, json.dumps({"steps": 20, "draft": False})))
    conn.execute(
        "INSERT INTO runs (id, created_at, status, mode, prompt, effective_prompt, steps, seed, num_images, model_id, "
        "options_json, error_message, error_hint) VALUES (?, '2026-10-02T10:00:00.000Z', 'failed', 'edit', 'make it blue', "
        "'make it blue', 6, 9, 2, 'm', ?, 'boom', 'try again')", (RUN_B, json.dumps({"steps": 6})))
    conn.execute("INSERT INTO images VALUES (?, ?, 'output', 0, 5, 64, 64, 0, 100, 'images/x/0.png', 'thumbs/x/0.webp', "
                 "'2026-10-01T10:01:00.000Z')", (IMAGE_OUT, RUN_A))
    conn.execute("INSERT INTO images VALUES (?, ?, 'input', NULL, NULL, 32, 32, 1, 50, 'inputs/y/1.png', NULL, "
                 "'2026-10-02T10:00:00.000Z')", (IMAGE_IN, RUN_B))
    conn.execute("INSERT INTO run_inputs VALUES (?, 1, ?, 'reference')", (RUN_B, IMAGE_IN))
    conn.commit()
    conn.close()


def sql_of(path: Path, table: str) -> str:
    conn = sqlite3.connect(path)
    try:
        text = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        return re.sub(r"\s+", " ", text.replace('"', "")).strip()  # SQLite quotes the name of a renamed table
    finally:
        conn.close()


def count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def music_run(run_id: str, **extra) -> dict:
    row = {"id": run_id, "created_at": "2026-10-04T10:00:00.000Z", "status": "queued", "mode": "music", "prompt": "warm piano",
           "effective_prompt": "warm piano", "steps": 30, "seed": 7, "num_images": 2, "model_id": "m",
           "options_json": json.dumps({"duration": 60}), "lyrics": None}
    row.update(extra)
    return row


# ------------------------------------------------------------------ migrating a real 1.7 database
def test_a_1_7_database_is_migrated_and_nothing_is_lost(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v2(path)
    db = Database(path)
    try:
        assert SCHEMA_VERSION == 3
        a, b = db.get_run(RUN_A), db.get_run(RUN_B)
        assert (a["prompt"], a["status"], a["pinned"], a["started_at"], a["finished_at"]) == (
            "a lighthouse", "done", 1, "2026-10-01T10:00:01.000Z", "2026-10-01T10:00:09.000Z")
        assert (b["status"], b["mode"], b["error_message"], b["error_hint"], b["num_images"]) == ("failed", "edit", "boom", "try again", 2)
        assert json.loads(a["options_json"]) == {"steps": 20, "draft": False}
        assert a["lyrics"] is None and b["lyrics"] is None  # old runs have no lyrics
        assert [r["id"] for r in db.images_for_runs([RUN_A])[RUN_A]] == [IMAGE_OUT]
        assert [r["id"] for r in db.inputs_for_runs([RUN_B])[RUN_B]] == [IMAGE_IN]
        assert db.tracks_for_runs([RUN_A])[RUN_A] == []
    finally:
        db.close()


def test_the_1_7_database_is_copied_first_as_a_way_back(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v2(path)
    Database(path).close()
    copy = tmp_path / "studio.sqlite.before-schema-3"
    assert copy.is_file() and count(copy, "runs") == 2
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"  # as it was
        assert "tracks" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert not list(tmp_path.glob("*.part"))


def test_a_second_start_changes_nothing(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v2(path)
    Database(path).close()
    copy_bytes = (tmp_path / "studio.sqlite.before-schema-3").read_bytes()
    before = sql_of(path, "runs")
    Database(path).close()
    assert sql_of(path, "runs") == before and (tmp_path / "studio.sqlite.before-schema-3").read_bytes() == copy_bytes
    assert count(path, "runs") == 2


def test_a_migrated_database_is_the_same_as_a_fresh_one(tmp_path):
    migrated, fresh = tmp_path / "migrated.sqlite", tmp_path / "fresh.sqlite"
    make_v2(migrated)
    Database(migrated).close()
    Database(fresh).close()
    for table in ("runs", "tracks", "images", "run_inputs"):
        assert sql_of(migrated, table) == sql_of(fresh, table), table
    conn = sqlite3.connect(migrated)
    try:
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")}
    finally:
        conn.close()
    assert {"idx_runs_status", "idx_images_run", "idx_tracks_run"} <= indexes


def test_after_the_migration_a_music_run_can_be_made_and_numbering_goes_on(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v2(path)
    db = Database(path)
    try:
        before = max(r["seq"] for r in db._all("SELECT seq FROM runs"))
        assert db.insert_run(music_run("e" * 32, lyrics="[Verse]\nla la"), cap=10) == "ok"
        added = db.get_run("e" * 32)
        assert added["mode"] == "music" and added["lyrics"] == "[Verse]\nla la" and added["seq"] > before
        assert db.queued_ids() == ["e" * 32]
    finally:
        db.close()


def test_the_old_check_on_mode_is_gone_but_the_others_remain(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v2(path)
    db = Database(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_run(music_run("f" * 32, mode="video"), cap=10)
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_run(music_run("f" * 32, status="sleeping"), cap=10)
    finally:
        db.close()


def test_foreign_keys_still_work_after_the_runs_table_was_rebuilt(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v2(path)
    db = Database(path)
    try:
        assert db._one("PRAGMA foreign_keys")[0] == 1
        assert db.delete_run(RUN_B) == "deleted"  # its input row and the link to it go with it
        assert db.inputs_for_runs([RUN_B])[RUN_B] == []
        assert db.get_image(IMAGE_IN) is None
        assert db.get_image(IMAGE_OUT) is not None  # the other run's image is untouched
        with pytest.raises(sqlite3.IntegrityError):  # an image that points at no run is still refused
            db.add_image({"id": "9" * 32, "run_id": "0" * 32, "kind": "output", "idx": 0, "seed": 1, "width": 1, "height": 1,
                          "has_alpha": 0, "bytes": 1, "path": "p", "thumb_path": None, "created_at": "2026-10-04T10:00:00.000Z"})
    finally:
        db.close()


def test_a_database_that_would_be_left_with_a_dangling_row_is_not_migrated(tmp_path):
    from studio.db import DatabaseError

    path = tmp_path / "studio.sqlite"
    make_v2(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO images VALUES ('1' || substr(?, 2), ?, 'output', 0, 1, 8, 8, 0, 1, 'p', NULL, '2026-10-01T10:00:00.000Z')",
                 ("2" * 32, "9" * 32))  # points at a run that never existed: already broken before the migration
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseError, match="pointing at runs that are gone"):
        Database(path)
    assert count(path, "runs") == 2  # the transaction was rolled back: the old table is still there
    assert sqlite3.connect(path).execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"


# ------------------------------------------------------------------ tracks
def track(track_id: str, run_id: str, idx: int = 0, **extra) -> dict:
    row = {"id": track_id, "run_id": run_id, "idx": idx, "seed": 7 + idx, "seconds": 12.5, "sample_rate": 44100, "channels": 2,
           "bytes": 2205044, "path": f"audio/{run_id}/{idx}.wav", "created_at": "2026-10-04T10:00:30.000Z"}
    row.update(extra)
    return row


def test_tracks_are_stored_in_order_and_go_with_their_run(tmp_path):
    db = Database(tmp_path / "studio.sqlite")
    try:
        assert db.insert_run(music_run(RUN_A), cap=10) == "ok"
        db.add_track(track("1" * 32, RUN_A, 1))
        db.add_track(track("2" * 32, RUN_A, 0))
        assert [t["idx"] for t in db.tracks_for_runs([RUN_A])[RUN_A]] == [0, 1]
        assert db.get_track("1" * 32)["seconds"] == 12.5 and db.get_track("0" * 32) is None
        assert db.mark_running(RUN_A, "2026-10-04T10:00:01.000Z")
        db.finish_run(RUN_A, "done", "2026-10-04T10:01:00.000Z")
        assert db.delete_run(RUN_A) == "deleted"
        assert db.tracks_for_runs([RUN_A])[RUN_A] == [] and count(tmp_path / "studio.sqlite", "tracks") == 0
    finally:
        db.close()


def test_expiring_a_run_removes_its_tracks(tmp_path):
    db = Database(tmp_path / "studio.sqlite")
    try:
        db.insert_run(music_run(RUN_A, created_at="2026-01-01T00:00:00.000Z"), cap=10)
        db.add_track(track("1" * 32, RUN_A))
        db.mark_running(RUN_A, "2026-01-01T00:00:01.000Z")
        db.finish_run(RUN_A, "done", "2026-01-01T00:01:00.000Z")
        assert db.delete_expired("2026-02-01T00:00:00.000Z", 10) == [RUN_A]
        assert count(tmp_path / "studio.sqlite", "tracks") == 0
    finally:
        db.close()


def test_a_track_must_belong_to_a_run(tmp_path):
    db = Database(tmp_path / "studio.sqlite")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.add_track(track("1" * 32, "0" * 32))
    finally:
        db.close()
