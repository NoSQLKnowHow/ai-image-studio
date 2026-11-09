"""What the studio does with a reading (DESIGN.md §38.6 to §38.8), tested without a server: a request becoming rows, the job for the next part, taking in the
pieces a worker reports, and the description the page reads. A real database and real files in a temporary folder; no worker.

The worker's reports are the one input that comes from outside the studio's own code, so `accept_piece` is tested with reports that are wrong and hostile
as well as right.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from conftest import make_settings

from studio import wavfile
from studio.db import Database
from studio.speech import SPOKEN_CHARS_PER_SECOND
from studio.speech.service import ParagraphSpec, PartSpec, ReadingRefused, ReadingSpec, SpeechService, audio_info
from studio.storage import Storage, StorageError

READY = {"fake": {"id": "fake", "title": "Fake speech", "state": "ready"}, "fake-basic": {"id": "fake-basic", "title": "Fake basic speech", "state": "ready"}}


def text(n: int) -> str:
    return f"Paragraph {n}: the quick brown fox jumps over the lazy dog, again and again."


class Env:
    """A database, a data folder and the service over them."""

    def __init__(self, tmp_path):
        self.settings = make_settings(tmp_path)
        self.db = Database(self.settings.data_dir / "studio.sqlite")
        self.storage = Storage(self.settings.data_dir)
        self.storage.ensure_layout()
        self.service = SpeechService(self.settings, self.db, self.storage)
        self.store = self.service.store

    def spec(self, parts: Optional[list[PartSpec]] = None, **fields: Any) -> ReadingSpec:
        base: dict[str, Any] = {"title": "A book", "source": {"kind": "paste"}, "engine": "fake", "voice": "anna"}
        base.update(fields)
        return ReadingSpec(parts=parts if parts is not None else [
            # part one is a heading and a paragraph (two pieces: a heading is a piece of its own) and part two is one paragraph: three pieces in all
            PartSpec("One", [ParagraphSpec("heading", "Chapter One"), ParagraphSpec("paragraph", text(1))]),
            PartSpec("Two", [ParagraphSpec("paragraph", text(3))])], **base)

    def insert(self, spec: Optional[ReadingSpec] = None, engines=READY):
        built = self.service.build(spec or self.spec(), engines)
        assert self.store.insert_reading(built.run, built.parts, built.pieces, 100) == "ok"
        return built, self.db.get_run(built.run["id"])

    def write_piece(self, run_id: str, part: int, idx: int, frames: int = 4800, channels: int = 1) -> str:
        """A piece file where the worker would have put it, and its path as the worker reports it (relative to the data folder)."""
        path = self.storage.reading_dir(run_id) / f"{part:04d}" / f"{idx:05d}.wav"
        wavfile.write_wav(path, bytes(2 * channels * frames), 24000, channels=channels)
        return self.storage.rel(path)

    def finish(self, run_id: str, part: int, idx: int, **event: Any) -> dict[str, int]:
        row = self.db.get_run(run_id)
        return self.service.accept_piece(row, {"key": f"{part}-{idx}", "path": self.write_piece(run_id, part, idx), **event})

    def add_voice(self, voice_id: str = "v" * 32, kind: str = "cloned", engine: str = "fake") -> str:
        with self.db.tx() as c:
            c.execute("INSERT INTO voices (id, name, name_key, kind, engine, ref_text, ref_path, seconds, created_at) VALUES (?, 'My voice', 'my voice', ?, ?, "
                      "'the words said', ?, 20.0, '2026-11-08T10:00:00.000Z')", (voice_id, kind, engine, f"speech/voices/{voice_id}/reference.wav"))
        return voice_id


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


# ---------------------------------------------------------------- a request becomes rows
def test_a_request_becomes_one_queued_run_with_its_parts_and_pieces(env):
    built, row = env.insert()
    run = built.run
    assert len(run["id"]) == 32 and run["mode"] == "speech" and run["status"] == "queued" and run["num_images"] == 2 and run["prompt"] == "A book"
    assert [(p["idx"], p["title"]) for p in built.parts] == [(0, "One"), (1, "Two")]
    assert [(p["part_idx"], p["idx"], p["kind"]) for p in built.pieces] == [(0, 0, "heading"), (0, 1, "paragraph"), (1, 0, "paragraph")]
    assert built.parts[0]["chars"] == sum(p["chars"] for p in built.pieces if p["part_idx"] == 0)
    assert built.seconds == pytest.approx(sum(p["chars"] for p in built.pieces) / SPOKEN_CHARS_PER_SECOND)
    assert row["model_id"] == "fake-speech" and row["steps"] == 0


def test_the_options_say_everything_a_later_reader_needs(env):
    options = json.loads(env.insert()[1]["options_json"])
    assert options["task"] == "reading" and options["engine"] == "fake" and options["engine_title"] == "Fake speech" and options["variant"] == "default"
    assert options["voice"] == {"kind": "preset", "id": "anna", "label": "Anna", "gender": "female", "accent": "American"}
    assert options["pause_ms"] == 450 and options["sample_rate"] == 24000 and options["source"] == {"kind": "paste"} and options["style"] is None
    assert options["licence"] == {"name": "Test only", "noncommercial": False, "notice": ""} and options["chars"] == sum(
        p["chars"] for p in env.store.pieces(env.db.read_one("SELECT id FROM runs")["id"], 0)) + len(text(3))


def test_each_piece_has_a_seed_of_its_own_derived_from_the_readings(env):
    built, _ = env.insert()
    seeds = [p["seed"] for p in built.pieces]
    assert len(set(seeds)) == 3 and seeds == [(built.run["seed"] + n) % 2 ** 31 for n in range(3)] and all(0 <= s < 2 ** 31 for s in seeds)
    other, _ = env.insert()
    assert other.run["id"] != built.run["id"]


def test_the_words_are_tidied_blank_paragraphs_dropped_and_blank_parts_left_out(env):
    parts = [PartSpec("Blank", [ParagraphSpec("paragraph", "   ")]),
             PartSpec("Real", [ParagraphSpec("paragraph", "  Spaces   and\nnewlines, made long enough to be a piece of its own.  "), ParagraphSpec("paragraph", "")])]
    built, _ = env.insert(env.spec(parts))
    assert [p["title"] for p in built.parts] == ["Real"] and built.parts[0]["idx"] == 0
    assert built.pieces[0]["text"] == "Spaces and newlines, made long enough to be a piece of its own."


def test_a_saved_voice_is_spoken_through_the_clone_variant_and_takes_no_style(env):
    voice = env.add_voice()
    built, _ = env.insert(env.spec(voice="", saved_voice=voice))
    options = json.loads(built.run["options_json"])
    assert options["variant"] == "clone" and options["voice"] == {"kind": "saved", "id": voice, "label": "My voice", "saved_kind": "cloned"}
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec(voice="", saved_voice=voice, style="warm"), READY)
    assert caught.value.code == "no_style" and caught.value.field == "style"


def test_a_voice_cannot_be_used_by_an_engine_other_than_the_one_it_was_made_for(env):
    voice = env.add_voice(engine="fake-basic")
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec(voice="", saved_voice=voice), READY)
    assert caught.value.code == "voice_engine" and caught.value.status == 422


def test_an_engine_that_is_short_of_memory_is_still_accepted_because_the_worker_will_make_room(env):
    engines = {"fake": {**READY["fake"], "state": "needs_memory"}}
    assert env.insert(env.spec(), engines)[0].run["status"] == "queued"


@pytest.mark.parametrize("state", ["not_installed", "error"])
def test_an_engine_that_cannot_run_is_refused_with_its_own_reason_and_hint(env, state):
    engines = {"fake": {**READY["fake"], "state": state, "reason": "The library is missing.", "hint": "Install it."}}
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec(), engines)
    assert (caught.value.status, caught.value.code, caught.value.message, caught.value.hint) == (409, "engine_unavailable", "The library is missing.", "Install it.")


# ---------------------------------------------------------------- the job for the next part
def test_the_job_is_the_next_part_only_with_the_paths_the_pieces_will_be_written_to(env):
    built, row = env.insert()
    job = env.service.job_for(row)
    assert (job["run_id"], job["mode"], job["task"], job["engine"], job["variant"], job["voice"], job["ref"]) == (built.run["id"], "speech", "reading", "fake", "default", "anna", None)
    assert [p["key"] for p in job["pieces"]] == ["0-0", "0-1"]  # part one only
    assert job["pieces"][0]["out"] == f"speech/readings/{built.run['id']}/0000/00000.wav" and job["pieces"][1]["out"].endswith("0000/00001.wav")
    assert [p["pause_ms"] for p in job["pieces"]] == [900, 450]  # the pause after a heading is twice a paragraph's
    assert [p["text"] for p in job["pieces"]][0] == "Chapter One" and [p["seed"] for p in job["pieces"]] == [built.pieces[0]["seed"], built.pieces[1]["seed"]]


def test_the_job_moves_on_to_the_next_part_as_the_pieces_are_made_and_ends(env):
    built, row = env.insert()
    rid = built.run["id"]
    env.finish(rid, 0, 0)
    assert [p["key"] for p in env.service.job_for(row)["pieces"]] == ["0-1"]  # resumes inside the part
    env.finish(rid, 0, 1)
    assert [p["key"] for p in env.service.job_for(row)["pieces"]] == ["1-0"]
    assert env.service.pieces_left(rid) == 1
    env.finish(rid, 1, 0)
    assert env.service.job_for(row) is None and env.service.pieces_left(rid) == 0


def test_the_pause_and_style_and_language_in_the_job_are_the_readings(env):
    env.settings = env.service.settings = make_settings(env.settings.data_dir.parent, speech_pause_ms=200)
    _, row = env.insert(env.spec(style="warm", language="german"))
    job = env.service.job_for(row)
    assert job["style"] == "warm" and job["language"] == "German" and [p["pause_ms"] for p in job["pieces"]] == [400, 200]


def test_a_saved_voice_goes_to_the_worker_as_its_recording_and_the_words_said_in_it(env):
    voice = env.add_voice(kind="designed")
    _, row = env.insert(env.spec(voice="", saved_voice=voice))
    job = env.service.job_for(row)
    assert job["voice"] == "" and job["ref"] == {"path": f"speech/voices/{voice}/reference.wav", "text": "the words said", "kind": "designed"}


def test_a_reading_whose_voice_has_been_deleted_cannot_be_given_a_job_and_says_why(env):
    voice = env.add_voice()
    _, row = env.insert(env.spec(voice="", saved_voice=voice))
    with env.db.tx() as c:
        c.execute("DELETE FROM voices WHERE id=?", (voice,))
    with pytest.raises(ValueError, match="My voice that this reading uses has been deleted"):
        env.service.job_for(row)


# ---------------------------------------------------------------- taking in a piece the worker reports
def test_a_finished_piece_is_recorded_from_the_file_itself_not_from_what_the_report_says(env):
    built, row = env.insert()
    rid = built.run["id"]
    result = env.finish(rid, 0, 0, frames=999999)  # the report's own idea of the length is ignored
    assert result == {"part": 0, "idx": 0, "frames": 4800}
    piece = env.store.piece(rid, 0, 0)
    assert (piece["status"], piece["frames"], piece["sample_rate"], piece["path"]) == ("done", 4800, 24000, f"speech/readings/{rid}/0000/00000.wav")
    assert piece["bytes"] == (env.storage.root / piece["path"]).stat().st_size


def test_the_engines_speed_is_learned_from_how_long_a_piece_took(env):
    built, _ = env.insert()
    env.finish(built.run["id"], 0, 0, work_seconds=0.5, spoken_chars=70)
    assert env.store.speed("fake", "default") == pytest.approx(140.0)
    env.finish(built.run["id"], 0, 1, work_seconds=0, spoken_chars=70)  # no time to divide by: nothing learned
    env.finish(built.run["id"], 1, 0)  # no figures at all
    assert env.store.speed("fake", "default") == pytest.approx(140.0)


@pytest.mark.parametrize("event,error", [
    ({"key": "nonsense"}, ValueError), ({"key": "a-b"}, ValueError), ({"key": "0-"}, ValueError), ({}, ValueError), ({"key": None}, ValueError),
    ({"key": "7-7"}, ValueError),  # well formed, but this reading has no such piece
])
def test_a_report_without_a_usable_key_is_refused(env, event, error):
    built, row = env.insert()
    with pytest.raises(error):
        env.service.accept_piece(row, {"path": env.write_piece(built.run["id"], 0, 0), **event})


def test_a_report_of_a_file_outside_the_readings_own_folder_is_refused(env):
    """The worker is trusted to write where it was told, but not to be believed about it: a path that points elsewhere (another reading's piece, another
    folder, a file that is not there, one that is not a WAV) fails the reading rather than being recorded."""
    built, row = env.insert()
    rid = built.run["id"]
    other = env.storage.reading_dir("e" * 32) / "0000" / "00000.wav"
    wavfile.write_wav(other, bytes(100), 24000, channels=1)
    (env.storage.speech / "stray.wav").write_bytes(b"x")
    (env.storage.reading_dir(rid) / "0000").mkdir(parents=True, exist_ok=True)
    (env.storage.reading_dir(rid) / "0000" / "00000.txt").write_text("not audio")
    for bad in (env.storage.rel(other), "speech/stray.wav", f"speech/readings/{rid}/0000/missing.wav", f"speech/readings/{rid}/0000/00000.txt",
                f"speech/readings/{rid}/../{'e' * 32}/0000/00000.wav", "../outside.wav", "/etc/passwd"):
        with pytest.raises(StorageError):
            env.service.accept_piece(row, {"key": "0-0", "path": bad})
    assert env.store.piece(rid, 0, 0)["status"] == "queued"  # nothing was recorded


def test_a_piece_that_is_not_mono_speech_is_refused(env):
    built, row = env.insert()
    path = env.write_piece(built.run["id"], 0, 0, channels=2)
    with pytest.raises(ValueError, match="must be mono"):
        env.service.accept_piece(row, {"key": "0-0", "path": path})
    assert env.store.piece(built.run["id"], 0, 0)["status"] == "queued"


def test_a_piece_whose_file_is_damaged_is_refused(env):
    built, row = env.insert()
    path = env.storage.reading_dir(built.run["id"]) / "0000" / "00000.wav"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"RIFF....WAVE")
    with pytest.raises(wavfile.WavError):
        env.service.accept_piece(row, {"key": "0-0", "path": env.storage.rel(path)})


# ---------------------------------------------------------------- a failure, remembered on the piece it happened to
def test_a_failure_is_remembered_on_the_first_piece_not_made(env):
    built, _ = env.insert()
    rid = built.run["id"]
    env.finish(rid, 0, 0)
    assert env.service.mark_failed_piece(rid, "x" * 900) == (0, 1)
    assert env.store.piece(rid, 0, 1)["error"] == "x" * 500 and env.store.piece(rid, 0, 0)["error"] is None  # cut to 500 characters
    env.finish(rid, 0, 1)
    env.finish(rid, 1, 0)
    assert env.service.mark_failed_piece(rid, "late") is None  # everything is made: nothing to blame


# ---------------------------------------------------------------- what the page is told
def test_a_new_reading_is_described_as_waiting_with_nothing_made(env):
    built, row = env.insert()
    reading = env.service.readings([row])[built.run["id"]]
    assert [(p["idx"], p["pieces"], p["made"], p["state"], p["seconds"]) for p in reading["parts"]] == [(0, 2, 0, "waiting", 0.0), (1, 1, 0, "waiting", 0.0)]
    assert (reading["parts_total"], reading["parts_done"], reading["pieces_total"], reading["pieces_done"], reading["waiting"]) == (2, 0, 3, 0, False)
    assert reading["seconds_made"] == 0 and reading["seconds_total"] == pytest.approx(built.seconds, abs=0.1) and reading["seconds_to_go"] is None  # no speed yet
    assert reading["sample_rate"] == 24000 and reading["position"] is None and reading["engine"] == "fake" and reading["title"] == "A book"


def test_a_reading_in_progress_says_which_parts_are_started_and_done_and_what_is_left(env):
    built, row = env.insert()
    rid = built.run["id"]
    env.finish(rid, 0, 0, work_seconds=0.1, spoken_chars=11)
    env.finish(rid, 0, 1)
    env.finish(rid, 1, 0)
    env.store.set_position(rid, 0, 1, 300, "2026-11-08T10:00:00.000Z")
    reading = env.service.readings([env.db.get_run(rid)])[rid]
    assert [p["state"] for p in reading["parts"]] == ["done", "done"] and reading["parts_done"] == 2 and reading["pieces_done"] == 3
    assert reading["seconds_made"] == pytest.approx(3 * 4800 / 24000, abs=0.01) and reading["bytes"] > 0
    assert reading["position"] == {"part": 0, "piece": 1, "offset_ms": 300}
    half = env.insert()[0].run["id"]
    env.finish(half, 0, 0)
    state = env.service.readings([env.db.get_run(half)])[half]
    assert [p["state"] for p in state["parts"]] == ["started", "waiting"]


def test_how_long_to_go_is_given_once_the_engine_speed_is_known_and_only_while_there_is_work(env):
    built, row = env.insert()
    rid = built.run["id"]
    env.store.record_speed("fake", "default", 20.0, "2026-11-08T10:00:00.000Z")
    remaining = env.store.remaining(rid)[0]
    assert env.service.readings([row])[rid]["seconds_to_go"] == -(-remaining // 20)  # rounded up, in whole seconds
    for part, idx in ((0, 0), (0, 1), (1, 0)):
        env.finish(rid, part, idx)
    with env.db.tx() as c:
        c.execute("UPDATE runs SET status='done' WHERE id=?", (rid,))
    assert env.service.readings([env.db.get_run(rid)])[rid]["seconds_to_go"] is None  # nothing left to wait for


def test_between_two_parts_a_reading_is_waiting_for_its_turn_and_not_unstarted(env):
    built, row = env.insert()
    env.finish(built.run["id"], 0, 0)
    assert env.service.readings([row])[built.run["id"]]["waiting"] is True  # queued, with something made
    with env.db.tx() as c:
        c.execute("UPDATE runs SET status='running' WHERE id=?", (built.run["id"],))
    assert env.service.readings([env.db.get_run(built.run["id"])])[built.run["id"]]["waiting"] is False


def test_only_readings_among_the_rows_are_described(env):
    _, row = env.insert()
    assert env.db.insert_run_if_capacity({"id": "1" * 32, "created_at": "2026-11-08T10:00:00.000Z", "status": "queued", "mode": "generate", "prompt": "p",
                                          "effective_prompt": "p", "steps": 1, "seed": 1, "num_images": 1, "model_id": "m", "options_json": "{}"}, cap=10)
    described = env.service.readings([row, env.db.get_run("1" * 32)])
    assert list(described) == [row["id"]] and env.service.readings([]) == {}


def test_a_part_is_described_with_where_each_piece_starts_and_where_to_fetch_it(env):
    built, row = env.insert()
    rid = built.run["id"]
    assert env.service.part_detail(row, 5) is None
    env.finish(rid, 0, 0)
    detail = env.service.part_detail(row, 0)
    assert detail["complete"] is False and detail["url"] is None and detail["download_url"] is None and detail["total_frames"] == 4800
    assert [(p["idx"], p["status"], p["start_frame"], p["url"]) for p in detail["pieces"]] == [
        (0, "done", 0, f"/api/speech/{rid}/parts/0/pieces/0.wav"), (1, "queued", None, None)]
    env.finish(rid, 0, 1)
    detail = env.service.part_detail(row, 0)
    assert detail["complete"] is True and detail["url"] == f"/api/speech/{rid}/parts/0.wav" and detail["sample_rate"] == 24000
    assert [p["start_frame"] for p in detail["pieces"]] == [0, 4800] and detail["total_frames"] == 9600


def test_the_files_of_a_part_are_given_only_once_it_is_whole(env):
    built, row = env.insert()
    rid = built.run["id"]
    assert env.service.part_files(row, 0) == []
    env.finish(rid, 0, 0)
    assert env.service.part_files(row, 0) == []  # half of it is not a part
    env.finish(rid, 0, 1)
    assert [p.name for p in env.service.part_files(row, 0)] == ["00000.wav", "00001.wav"] and env.service.part_files(row, 9) == []


# ---------------------------------------------------------------- the note inside every WAV
def test_the_note_in_a_wav_says_who_made_it_and_what_the_licence_asks():
    note = audio_info({"engine": "kokoro", "engine_title": "Kokoro", "voice": {"kind": "preset", "id": "af_bella", "label": "Bella"},
                       "licence": {"notice": "Apache-2.0."}}, "Chapter 1", "2026-11-08T10:00:00.000Z")
    assert note["title"] == "Chapter 1" and note["software"] == "ai-image-studio" and note["date"] == "2026-11-08"
    assert note["comment"] == "Generated by Kokoro, voice Bella (preset): machine-generated speech. Apache-2.0."


def test_a_cloned_voice_is_said_to_have_been_used_with_the_speakers_agreement():
    voice = {"kind": "saved", "id": "v", "label": "Mine", "saved_kind": "cloned"}
    comment = audio_info({"engine": "qwen3-tts", "voice": voice, "licence": {}}, "t", "2026-11-08T10:00:00.000Z")["comment"]
    assert "Mine (cloned)" in comment and "with the speaker's agreement" in comment
    assert "agreement" not in audio_info({"engine": "e", "voice": {**voice, "saved_kind": "designed"}}, "t", "2026-11-08T10:00:00.000Z")["comment"]


def test_a_long_title_is_cut_for_the_note_and_missing_options_do_not_break_it():
    assert len(audio_info({}, "t" * 500, "2026-11-08T10:00:00.000Z")["title"]) == 200
    assert audio_info({}, "", "2026-11-08T10:00:00.000Z")["comment"].startswith("Generated by None, voice None (preset)")
