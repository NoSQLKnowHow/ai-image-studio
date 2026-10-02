"""The schema 1 -> 2 migration and the queries for run inputs and staged uploads (DESIGN.md §21.7)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from studio import presets as P
from studio.db import SCHEMA_VERSION, Database, DatabaseError

# What a database made by version 1.1 looks like: schema 1, with no run_inputs table.
V1_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, started_at TEXT,
    finished_at TEXT, status TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','canceled')),
    mode TEXT NOT NULL CHECK (mode IN ('generate','edit')), prompt TEXT NOT NULL, effective_prompt TEXT NOT NULL,
    negative_prompt TEXT, transparent INTEGER NOT NULL DEFAULT 0, width INTEGER, height INTEGER, steps INTEGER NOT NULL,
    cfg_scale REAL, seed INTEGER NOT NULL, num_images INTEGER NOT NULL, model_id TEXT NOT NULL, input_image_id TEXT,
    error_message TEXT, error_hint TEXT, pinned INTEGER NOT NULL DEFAULT 0, options_json TEXT NOT NULL
);
CREATE TABLE images (
    id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('output','input')), idx INTEGER, seed INTEGER, width INTEGER NOT NULL,
    height INTEGER NOT NULL, has_alpha INTEGER NOT NULL, bytes INTEGER NOT NULL, path TEXT NOT NULL, thumb_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_runs_status ON runs(status, seq);
CREATE INDEX idx_images_run ON images(run_id, idx);
INSERT INTO meta VALUES ('schema_version', '1');
"""
RUN_ID = "a" * 32


def make_v1(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute(
        "INSERT INTO runs (id, created_at, status, mode, prompt, effective_prompt, steps, seed, num_images, model_id, "
        "options_json) VALUES (?, '2026-10-01T10:00:00.000Z', 'done', 'generate', 'a lighthouse', 'a lighthouse', 20, 5, 1, "
        "'m', ?)", (RUN_ID, json.dumps({"steps": 20})))
    conn.execute("INSERT INTO images VALUES ('b' || substr(?, 2), ?, 'output', 0, 5, 64, 64, 0, 100, 'images/x/0.png', NULL, "
                 "'2026-10-01T10:01:00.000Z')", (RUN_ID, RUN_ID))
    conn.commit()
    conn.close()


def tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def version(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------------ the migration
def test_a_version_1_database_is_upgraded_in_place_and_nothing_is_lost(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v1(path)
    db = Database(path)
    try:
        assert SCHEMA_VERSION == 2 and version(path) == "2"
        assert "run_inputs" in tables(path)
        run = db.get_run(RUN_ID)
        assert run["prompt"] == "a lighthouse" and run["status"] == "done"
        assert [r["idx"] for r in db.images_for_runs([RUN_ID])[RUN_ID]] == [0]
        assert db.inputs_for_runs([RUN_ID])[RUN_ID] == []  # old runs simply have no inputs
    finally:
        db.close()


def test_the_old_database_is_copied_first_as_a_way_back(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v1(path)
    Database(path).close()
    copy = tmp_path / "studio.sqlite.before-schema-2"
    assert copy.is_file() and version(copy) == "1" and "run_inputs" not in tables(copy)
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT prompt FROM runs").fetchone()[0] == "a lighthouse"  # the data, as it was
    finally:
        conn.close()
    assert not list(tmp_path.glob("*.part"))


def test_an_existing_copy_is_never_replaced_and_a_second_start_makes_no_new_one(tmp_path):
    path = tmp_path / "studio.sqlite"
    make_v1(path)
    copy = tmp_path / "studio.sqlite.before-schema-2"
    copy.write_bytes(b"the first, pristine copy")  # e.g. left by an earlier attempt
    Database(path).close()
    assert copy.read_bytes() == b"the first, pristine copy"
    path2 = tmp_path / "other" / "studio.sqlite"
    path2.parent.mkdir()
    make_v1(path2)
    Database(path2).close()
    after_first = (path2.parent / "studio.sqlite.before-schema-2").read_bytes()
    Database(path2).close()  # already version 2: nothing to copy, nothing to change
    assert (path2.parent / "studio.sqlite.before-schema-2").read_bytes() == after_first


def test_a_new_database_has_no_copy_and_the_current_version(tmp_path):
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    assert version(path) == "2" and {"runs", "images", "run_inputs", "meta"} <= tables(path)
    assert not list(tmp_path.glob("*before-schema*"))


def test_a_database_from_a_newer_studio_is_still_refused_untouched(tmp_path):
    path = tmp_path / "studio.sqlite"
    Database(path).close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseError, match="newer version"):
        Database(path)
    assert version(path) == "99" and not list(tmp_path.glob("*before-schema*"))


# ------------------------------------------------------------------ inputs and staged uploads
def run_row(run_id: str = RUN_ID, **extra) -> dict:
    row = {"id": run_id, "created_at": "2026-10-02T10:00:00.000Z", "status": "queued", "mode": "edit", "prompt": "p",
           "effective_prompt": "p", "steps": 3, "seed": 1, "num_images": 1, "model_id": "m", "options_json": "{}"}
    row.update(extra)
    return row


def image_row(image_id: str, run_id=None, **extra) -> dict:
    row = {"id": image_id, "run_id": run_id, "kind": "input", "idx": None, "seed": None, "width": 10, "height": 20,
           "has_alpha": 0, "bytes": 5, "path": f"inputs/{image_id}.png", "thumb_path": None, "created_at": "2026-10-02T10:00:00.000Z"}
    row.update(extra)
    return row


def owned(image_id: str, run_id: str, position: int, role: str = "reference") -> dict:
    return image_row(image_id, run_id, position=position, role=role)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


def test_a_run_and_its_inputs_are_stored_together_in_position_order(db):
    assert db.insert_run(run_row(), 10, [owned("2" * 32, RUN_ID, 2), owned("1" * 32, RUN_ID, 1, "mask")]) == "ok"
    rows = db.inputs_for_runs([RUN_ID])[RUN_ID]
    assert [(r["position"], r["id"][0], r["role"], r["width"]) for r in rows] == [(1, "1", "mask", 10), (2, "2", "reference", 10)]
    assert db.images_for_runs([RUN_ID])[RUN_ID] == []  # inputs are not results


def test_claiming_consumes_the_staged_upload_in_the_same_transaction(db):
    staged = "3" * 32
    db.add_image(image_row(staged))
    assert db.get_staged(staged) is not None
    assert db.insert_run(run_row(), 10, [owned("4" * 32, RUN_ID, 1)], claims=[staged]) == "ok"
    assert db.get_staged(staged) is None and db.get_image(staged) is None and db.get_run(RUN_ID) is not None


def test_a_staged_upload_taken_by_someone_else_aborts_everything(db):
    taken = "5" * 32  # never staged, or already claimed
    assert db.insert_run(run_row(), 10, [owned("6" * 32, RUN_ID, 1)], claims=[taken]) == f"claimed:{taken}"
    assert db.get_run(RUN_ID) is None and db.get_image("6" * 32) is None  # no run, no image row, no run_inputs row
    assert db._one("SELECT COUNT(*) AS n FROM run_inputs")["n"] == 0
    assert db.insert_run(run_row(), 10) == "ok"  # and the database is still usable


def test_a_full_queue_changes_nothing(db):
    assert db.insert_run(run_row("1" * 32), 1) == "ok"
    staged = "7" * 32
    db.add_image(image_row(staged))
    assert db.insert_run(run_row("2" * 32), 1, [owned("8" * 32, "2" * 32, 1)], claims=[staged]) == "full"
    assert db.get_staged(staged) is not None and db.get_image("8" * 32) is None and db.get_run("2" * 32) is None


def test_the_same_upload_claimed_twice_in_one_run_is_one_claim(db):
    staged = "9" * 32
    db.add_image(image_row(staged))
    inputs = [owned("a1" * 16, RUN_ID, 1), owned("a2" * 16, RUN_ID, 2)]
    assert db.insert_run(run_row(), 10, inputs, claims=[staged, staged]) == "ok"
    assert len(db.inputs_for_runs([RUN_ID])[RUN_ID]) == 2


def test_only_unclaimed_uploads_count_as_staged_and_can_be_deleted(db):
    staged = "b1" * 16
    db.add_image(image_row(staged))
    db.insert_run(run_row(), 10, [owned("b2" * 16, RUN_ID, 1)])
    assert db.get_staged("b2" * 16) is None and db.delete_staged("b2" * 16) is None  # a run's input is not staged
    assert db.get_image("b2" * 16) is not None
    assert db.delete_staged(staged)["id"] == staged and db.get_staged(staged) is None
    assert db.delete_staged(staged) is None


def test_expired_staged_uploads_are_selected_by_age_and_deleted_with_their_rows_returned(db):
    old, new = "c1" * 16, "c2" * 16
    db.add_image(image_row(old, created_at="2026-10-01T00:00:00.000Z"))
    db.add_image(image_row(new, created_at="2026-10-02T00:00:00.000Z"))
    claimed = "c3" * 16
    db.insert_run(run_row(), 10, [owned(claimed, RUN_ID, 1)])  # old, but owned by a run: never a stale upload
    db._conn.execute("UPDATE images SET created_at='2026-09-01T00:00:00.000Z' WHERE id=?", (claimed,))
    rows = db.delete_expired_staged("2026-10-01T12:00:00.000Z", 10)
    assert [r["id"] for r in rows] == [old] and db.get_staged(new) is not None and db.get_image(claimed) is not None
    assert db.known_ids() == ({RUN_ID}, {new})


def test_deleting_a_run_takes_its_inputs_and_deleting_an_input_image_takes_its_link(db):
    db.insert_run(run_row(), 10, [owned("d1" * 16, RUN_ID, 1), owned("d2" * 16, RUN_ID, 2)])
    with db.tx() as c:
        c.execute("DELETE FROM images WHERE id=?", ("d1" * 16,))
    assert [r["position"] for r in db.inputs_for_runs([RUN_ID])[RUN_ID]] == [2]
    assert db.delete_run(RUN_ID) == "deleted"
    assert db._one("SELECT COUNT(*) AS n FROM run_inputs")["n"] == 0 and db._one("SELECT COUNT(*) AS n FROM images")["n"] == 0


def test_positions_are_unique_within_a_run_and_start_at_one(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_run(run_row(), 10, [owned("e1" * 16, RUN_ID, 1), owned("e2" * 16, RUN_ID, 1)])
    assert db.get_run(RUN_ID) is None  # the failed insert rolled back
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_run(run_row(), 10, [owned("e3" * 16, RUN_ID, 0)])


def test_the_size_arithmetic_is_the_pipelines():
    """Expected values were produced by running `calculate_dimensions` from the pinned diffusers source
    (pipeline_qwenimage21.py @ 578c9b2c), not by this module's copy of it."""
    cases = {
        1024: {(1024, 1024): (1024, 1024), (1024, 768): (1184, 896), (768, 1024): (896, 1184), (1920, 1080): (1376, 768),
               (1080, 1920): (768, 1376), (4000, 3000): (1184, 896), (97, 64): (1248, 832), (2000, 300): (2656, 384),
               (300, 2000): (384, 2656), (333, 777): (672, 1568), (500, 500): (1024, 1024)},
        2048: {(1024, 1024): (2048, 2048), (1024, 768): (2368, 1760), (768, 1024): (1760, 2368), (1920, 1080): (2720, 1536),
               (1080, 1920): (1536, 2720), (97, 64): (2528, 1664), (2000, 300): (5280, 800), (333, 777): (1344, 3136)},
    }
    for resolution, table in cases.items():
        for (w, h), expected in table.items():
            assert P.calculate_dimensions(resolution * resolution, w / h) == expected, (resolution, w, h)
