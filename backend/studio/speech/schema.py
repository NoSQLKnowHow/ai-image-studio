"""The Speech tab's tables (schema 10, DESIGN.md §38.8).

A reading is a row of `runs` with mode 'speech' (so the queue, the bin, projects, Cancel and the history work on it unchanged) and these tables hang off it:
  speech_parts      the parts of the reading: a title and a size, in order
  speech_pieces     the pieces of each part: the text to speak, and, once made, the audio file
  speech_positions  where the listener stopped, one for each reading
  speech_speeds     how fast each engine has been measured to work, to say how long a reading will take
  voices            the voices the person made (designed or cloned); the preset voices live in the engines, not here
The run's own new columns, `queue_key` and `hidden`, are in db.py with the rest of `runs`.
"""

from __future__ import annotations

SPEECH_SCHEMA = """
CREATE TABLE IF NOT EXISTS speech_parts (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx    INTEGER NOT NULL,
    title  TEXT NOT NULL,
    chars  INTEGER NOT NULL,
    PRIMARY KEY (run_id, idx)
);
-- One row for each piece. `status` is queued until the audio exists. `text` can be edited while the piece is queued (DESIGN.md §38.6 item 6). `frames` is the length
-- of the audio including the pause that follows the speech, which is why the pieces of a part laid end to end are the part (§38.7).
CREATE TABLE IF NOT EXISTS speech_pieces (
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    part_idx    INTEGER NOT NULL,
    idx         INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('paragraph','heading')),
    text        TEXT NOT NULL,
    chars       INTEGER NOT NULL,
    continues   INTEGER NOT NULL DEFAULT 0,
    seed        INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','done')),
    frames      INTEGER,
    sample_rate INTEGER,
    bytes       INTEGER,
    path        TEXT,
    error       TEXT,
    PRIMARY KEY (run_id, part_idx, idx)
);
CREATE INDEX IF NOT EXISTS idx_speech_pieces_state ON speech_pieces(run_id, status, part_idx, idx);
CREATE TABLE IF NOT EXISTS speech_positions (
    run_id     TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    part_idx   INTEGER NOT NULL,
    piece_idx  INTEGER NOT NULL,
    offset_ms  INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS speech_speeds (
    engine           TEXT NOT NULL,
    variant          TEXT NOT NULL,
    chars_per_second REAL NOT NULL,
    samples          INTEGER NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (engine, variant)
);
-- A voice the person made: designed from a description or cloned from a recording. `ref_path` is the reference clip, relative to the data folder
-- (<data>/speech/voices/<id>/reference.wav); `ref_text` is the words said in it. `consent_*` record the statement ticked for a cloned voice.
CREATE TABLE IF NOT EXISTS voices (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    name_key     TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL CHECK (kind IN ('designed','cloned')),
    engine       TEXT NOT NULL,
    description  TEXT,
    ref_text     TEXT NOT NULL,
    ref_path     TEXT NOT NULL,
    seconds      REAL NOT NULL,
    consent_text TEXT,
    consent_at   TEXT,
    created_at   TEXT NOT NULL
);
"""
