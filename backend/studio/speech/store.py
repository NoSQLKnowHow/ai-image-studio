"""The SQL of readings (DESIGN.md §38.8). Everything here is a fixed statement with bound values; the text of a piece is data and never part of a statement.

A reading is a row of `runs` with mode 'speech' plus its `speech_parts` and `speech_pieces`. The job manager and the routes go through this class, so
the shape of the tables is known in one place.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional, Sequence

from ..db import Database

SPEED_WEIGHT = 0.3  # how much the newest measurement counts in an engine's average speed (an exponential moving average)


class SpeechStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ making a reading
    def insert_reading(self, run: dict[str, Any], parts: Sequence[dict[str, Any]], pieces: Sequence[dict[str, Any]], cap: int) -> str:
        """Insert a queued reading, its parts and all its pieces in one transaction. Returns "ok" or "full" (`cap` runs already waiting; nothing is changed)."""
        with self.db.tx() as c:
            if c.execute("SELECT COUNT(*) FROM runs WHERE status='queued'").fetchone()[0] >= cap:
                return "full"
            c.execute(f"INSERT INTO runs ({', '.join(run)}) VALUES ({', '.join('?' for _ in run)})", tuple(run.values()))
            self.db.index_run(c, run["id"])  # a reading's title and source name are searchable from the start (DESIGN.md §45.7)
            c.executemany("INSERT INTO speech_parts (run_id, idx, title, chars) VALUES (:run_id, :idx, :title, :chars)", parts)
            c.executemany("INSERT INTO speech_pieces (run_id, part_idx, idx, kind, text, chars, continues, seed) "
                          "VALUES (:run_id, :part_idx, :idx, :kind, :text, :chars, :continues, :seed)", pieces)
            return "ok"

    # ------------------------------------------------------------------ reading what is there
    def reading(self, run_id: str) -> Optional[sqlite3.Row]:
        """The run row, if it is a visible reading."""
        return self.db.read_one("SELECT * FROM runs WHERE id=? AND mode='speech' AND hidden=0", (run_id,))

    def parts(self, run_id: str) -> list[sqlite3.Row]:
        return self.db.read_all("SELECT * FROM speech_parts WHERE run_id=? ORDER BY idx", (run_id,))

    def pieces(self, run_id: str, part_idx: int) -> list[sqlite3.Row]:
        return self.db.read_all("SELECT * FROM speech_pieces WHERE run_id=? AND part_idx=? ORDER BY idx", (run_id, part_idx))

    def piece(self, run_id: str, part_idx: int, idx: int) -> Optional[sqlite3.Row]:
        return self.db.read_one("SELECT * FROM speech_pieces WHERE run_id=? AND part_idx=? AND idx=?", (run_id, part_idx, idx))

    def totals(self, run_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """For each reading: its parts with how many pieces each has and how many are made, and the sums. One query for the parts and one for the counts,
        however many readings are asked about (the history asks for a page of them at once)."""
        if not run_ids:
            return {}
        marks = ", ".join("?" for _ in run_ids)
        found: dict[str, dict[str, Any]] = {run_id: {"parts": [], "pieces": 0, "made": 0, "frames": 0, "bytes": 0, "sample_rate": None} for run_id in run_ids}
        counts = {(r["run_id"], r["part_idx"]): r for r in self.db.read_all(
            f"SELECT run_id, part_idx, COUNT(*) AS total, SUM(status='done') AS made, COALESCE(SUM(frames), 0) AS frames, COALESCE(SUM(bytes), 0) AS bytes, "
            f"MAX(sample_rate) AS sample_rate FROM speech_pieces WHERE run_id IN ({marks}) GROUP BY run_id, part_idx", tuple(run_ids))}
        for part in self.db.read_all(f"SELECT * FROM speech_parts WHERE run_id IN ({marks}) ORDER BY run_id, idx", tuple(run_ids)):
            count = counts.get((part["run_id"], part["idx"]))
            entry = {"idx": part["idx"], "title": part["title"], "chars": part["chars"], "pieces": count["total"] if count else 0,
                     "made": count["made"] if count else 0, "frames": count["frames"] if count else 0, "sample_rate": count["sample_rate"] if count else None}
            summary = found[part["run_id"]]
            summary["parts"].append(entry)
            summary["pieces"] += entry["pieces"]
            summary["made"] += entry["made"]
            summary["frames"] += entry["frames"]
            summary["bytes"] += count["bytes"] if count else 0
            summary["sample_rate"] = summary["sample_rate"] or entry["sample_rate"]
        return found

    def next_pieces(self, run_id: str) -> list[sqlite3.Row]:
        """The pieces still to make in the first part that has any, in order (DESIGN.md §38.6 item 3: a reading makes one part at a time, then goes back to
        the queue). Empty when the reading is complete."""
        first = self.db.read_one("SELECT part_idx FROM speech_pieces WHERE run_id=? AND status='queued' ORDER BY part_idx, idx LIMIT 1", (run_id,))
        if first is None:
            return []
        return self.db.read_all("SELECT * FROM speech_pieces WHERE run_id=? AND part_idx=? AND status='queued' ORDER BY idx", (run_id, first["part_idx"]))

    def remaining(self, run_id: str) -> tuple[int, int]:
        """(characters, pieces) not yet made."""
        row = self.db.read_one("SELECT COALESCE(SUM(chars), 0) AS chars, COUNT(*) AS n FROM speech_pieces WHERE run_id=? AND status='queued'", (run_id,))
        return int(row["chars"]), int(row["n"])

    # ------------------------------------------------------------------ changing it
    def mark_piece_done(self, run_id: str, part_idx: int, idx: int, frames: int, sample_rate: int, size: int, path: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE speech_pieces SET status='done', frames=?, sample_rate=?, bytes=?, path=?, error=NULL WHERE run_id=? AND part_idx=? AND idx=?",
                      (frames, sample_rate, size, path, run_id, part_idx, idx))

    def set_piece_error(self, run_id: str, part_idx: int, idx: int, message: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE speech_pieces SET error=? WHERE run_id=? AND part_idx=? AND idx=?", (message, run_id, part_idx, idx))

    def edit_piece(self, run_id: str, part_idx: int, idx: int, text: str) -> str:
        """Change the text of a piece that has not been made (DESIGN.md §38.6 item 6). Returns "ok", "not_found" or "made"."""
        with self.db.tx() as c:
            row = c.execute("SELECT status FROM speech_pieces WHERE run_id=? AND part_idx=? AND idx=?", (run_id, part_idx, idx)).fetchone()
            if row is None:
                return "not_found"
            if row["status"] != "queued":
                return "made"
            c.execute("UPDATE speech_pieces SET text=?, chars=?, error=NULL WHERE run_id=? AND part_idx=? AND idx=?", (text, len(text), run_id, part_idx, idx))
            c.execute("UPDATE speech_parts SET chars=(SELECT COALESCE(SUM(chars), 0) FROM speech_pieces WHERE run_id=? AND part_idx=?) WHERE run_id=? AND idx=?",
                      (run_id, part_idx, run_id, part_idx))
            return "ok"

    # ------------------------------------------------------------------ voices the person made
    def voice(self, voice_id: str) -> Optional[sqlite3.Row]:
        return self.db.read_one("SELECT * FROM voices WHERE id=?", (voice_id,))

    def voices(self, engine: Optional[str] = None) -> list[sqlite3.Row]:
        """The saved voices, newest first; those of one engine if `engine` is given (a voice belongs to the engine it was made for, DESIGN.md §38.4)."""
        if engine is None:
            return self.db.read_all("SELECT * FROM voices ORDER BY created_at DESC, id")
        return self.db.read_all("SELECT * FROM voices WHERE engine=? ORDER BY created_at DESC, id", (engine,))

    def insert_voice(self, row: dict[str, Any]) -> str:
        """Add a voice. Returns "ok", or "name_taken" if another voice has the same name once case is folded away (`name_key` is unique)."""
        try:
            with self.db.tx() as c:
                c.execute(f"INSERT INTO voices ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
        except sqlite3.IntegrityError:
            return "name_taken"
        return "ok"

    def rename_voice(self, voice_id: str, name: str, name_key: str) -> str:
        """"ok", "not_found" or "name_taken" (another voice already has the name). Renaming a voice to its own name in other letters is allowed."""
        try:
            with self.db.tx() as c:
                changed = c.execute("UPDATE voices SET name=?, name_key=? WHERE id=?", (name, name_key, voice_id)).rowcount
        except sqlite3.IntegrityError:
            return "name_taken"
        return "ok" if changed else "not_found"

    def delete_voice(self, voice_id: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM voices WHERE id=?", (voice_id,)).rowcount == 1

    def runs_using_voice(self, voice_id: str) -> int:
        """How many readings and short jobs that are queued or running speak in this voice: a voice they need cannot be deleted from under them."""
        return int(self.db.read_one(
            "SELECT COUNT(*) AS n FROM runs WHERE mode='speech' AND status IN ('queued','running') "
            "AND json_extract(options_json, '$.voice.id') = ?", (voice_id,))["n"])

    # ------------------------------------------------------------------ the short jobs behind a sample or a designed voice
    def hidden_jobs(self, statuses: Sequence[str]) -> list[sqlite3.Row]:
        marks = ", ".join("?" for _ in statuses)
        return self.db.read_all(f"SELECT * FROM runs WHERE mode='speech' AND hidden=1 AND status IN ({marks}) ORDER BY seq", tuple(statuses))

    def hidden_job(self, run_id: str) -> Optional[sqlite3.Row]:
        return self.db.read_one("SELECT * FROM runs WHERE id=? AND mode='speech' AND hidden=1", (run_id,))

    def active_job_for(self, target: str) -> Optional[sqlite3.Row]:
        """A queued or running short job already making the same thing (the same sample), so that pressing Play sample again does not queue it twice."""
        return self.db.read_one(
            "SELECT * FROM runs WHERE mode='speech' AND hidden=1 AND status IN ('queued','running') AND json_extract(options_json, '$.target') = ? "
            "ORDER BY seq LIMIT 1", (target,))

    def delete_hidden_jobs_before(self, cutoff: str) -> list[str]:
        """Remove finished short jobs made before `cutoff` (they are not history). Returns their ids."""
        with self.db.tx() as c:
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM runs WHERE mode='speech' AND hidden=1 AND status IN ('done','failed','canceled') AND COALESCE(finished_at, created_at) < ?",
                (cutoff,)).fetchall()]
            for run_id in ids:
                c.execute("DELETE FROM runs WHERE id=?", (run_id,))
        return ids

    # ------------------------------------------------------------------ position
    def position(self, run_id: str) -> Optional[sqlite3.Row]:
        return self.db.read_one("SELECT * FROM speech_positions WHERE run_id=?", (run_id,))

    def set_position(self, run_id: str, part_idx: int, piece_idx: int, offset_ms: int, now: str) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO speech_positions (run_id, part_idx, piece_idx, offset_ms, updated_at) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(run_id) DO UPDATE SET part_idx=excluded.part_idx, piece_idx=excluded.piece_idx, offset_ms=excluded.offset_ms, "
                      "updated_at=excluded.updated_at", (run_id, part_idx, piece_idx, offset_ms, now))

    # ------------------------------------------------------------------ speeds
    def speed(self, engine: str, variant: str) -> Optional[float]:
        """The measured characters of text spoken per second of work, or None before the engine has made a piece."""
        row = self.db.read_one("SELECT chars_per_second FROM speech_speeds WHERE engine=? AND variant=?", (engine, variant))
        return float(row["chars_per_second"]) if row else None

    def record_speed(self, engine: str, variant: str, chars_per_second: float, now: str) -> None:
        """Fold a measurement into the engine's average (an exponential moving average, so the figure follows the machine but one slow piece does not swing it)."""
        with self.db.tx() as c:
            row = c.execute("SELECT chars_per_second, samples FROM speech_speeds WHERE engine=? AND variant=?", (engine, variant)).fetchone()
            if row is None:
                c.execute("INSERT INTO speech_speeds (engine, variant, chars_per_second, samples, updated_at) VALUES (?, ?, ?, 1, ?)",
                          (engine, variant, chars_per_second, now))
            else:
                blended = (1 - SPEED_WEIGHT) * row["chars_per_second"] + SPEED_WEIGHT * chars_per_second
                c.execute("UPDATE speech_speeds SET chars_per_second=?, samples=samples+1, updated_at=? WHERE engine=? AND variant=?",
                          (blended, now, engine, variant))
