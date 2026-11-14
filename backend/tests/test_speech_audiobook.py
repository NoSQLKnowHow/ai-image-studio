"""Chapter marks (DESIGN.md §44.4, criteria 354 to 361): the two chapter lists in the ZIP, and the M4B audiobook file that is built when it is asked for.

Three kinds of test, each with the tool that suits it:
  * the ZIP and the M4B of a real reading (the real server, the fake speech engine, and the REAL ffmpeg and ffprobe, which these tests need: they skip where this machine's
    ffmpeg has no AAC encoder, as the recording tests skip where it cannot make an MP3). ffprobe is the second opinion on the file: its chapters, its duration, its tags;
  * the builder's behaviour (progress, one build at a time, a stalled or failing ffmpeg, a missing one, the disk), with a stand-in `ffmpeg` that is a small script, so that
    each of those can be made to happen on purpose and quickly;
  * the builder's arithmetic and memory, with a stand-in process that only counts what it is given.
The chapter arithmetic itself (rounding once, exact fractions) is in test_speech_chapters.py.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import tracemalloc
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional

import pytest
from conftest import wait_for
from test_speech_audio_routes import part_wav, wav_of
from test_speech_readings import ERROR, SLOW, finished_reading, make_reading, part, wait_until

from studio import wavfile
from studio.speech import audiobook
from studio.speech.audio import CHUNK, PartAudio, piece_file
from studio.speech.audiobook import Audiobooks, BuildFailed, FinishedPart, build_m4b, estimated_bytes, forget_probes, probe, signature
from studio.speech.chapters import PartLength, chapters_of, clock, to_ms

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
REAL = FFMPEG is not None and FFPROBE is not None and probe(FFMPEG).available
needs_ffmpeg = pytest.mark.skipif(not REAL, reason="needs ffmpeg with an AAC encoder, and ffprobe")


# ---------------------------------------------------------------- helpers
def ffprobe(path: Path) -> dict[str, Any]:
    """What ffprobe says of a file: its streams, its format and its chapters, as JSON."""
    out = subprocess.run([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def zip_of(client, run_id: str) -> zipfile.ZipFile:
    response = client.get(f"/api/speech/{run_id}/download.zip")
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def wait_ready(client, run_id: str, timeout: float = 60.0) -> dict[str, Any]:
    """Poll the audiobook's status until it ends (ready, or failed), and return it."""
    deadline = time.monotonic() + timeout
    while True:
        status = client.get(f"/api/speech/{run_id}/audiobook").json()
        if status["state"] in ("ready", "failed"):
            return status
        if time.monotonic() > deadline:
            raise AssertionError(f"the audiobook never finished: {status}")
        time.sleep(0.05)


def make_m4b(client, run_id: str) -> dict[str, Any]:
    started = client.post(f"/api/speech/{run_id}/audiobook")
    assert started.status_code in (200, 202), started.text
    return wait_ready(client, run_id)


def ms_of_part_lengths(frames_and_rates: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """(start, end) in ms of parts of known lengths, worked out here with the standard library's `Fraction`, as a second opinion on `chapters.py`."""
    out, elapsed = [], Fraction(0)
    for frames, rate in frames_and_rates:
        begins, elapsed = elapsed, elapsed + Fraction(frames, rate)
        out.append((int(begins * 1000 + Fraction(1, 2)), int(elapsed * 1000 + Fraction(1, 2))))
    return out


@pytest.fixture
def done(client):
    """A finished reading of two parts (3 and 2 pieces) made with the fake engine at 24 kHz."""
    return finished_reading(client)


# ---------------------------------------------------------------- the chapter lists in the ZIP (criteria 354 and 355)
def test_the_zip_has_the_chapter_lists_after_the_parts_and_the_playlist_which_are_as_they_were(client, done):
    archive = zip_of(client, done["id"])
    names = archive.namelist()
    assert [n.rsplit("_", 1)[0][-6:] if n.endswith(".wav") else n for n in names][2:] == ["playlist.m3u", "chapters.txt", "chapters.ffmetadata"]
    assert names[0].endswith(".wav") and names[1].endswith(".wav") and archive.testzip() is None  # every CRC is right, the new files' too
    assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
    assert archive.read("playlist.m3u").decode().startswith("#EXTM3U\n")  # untouched


def test_chapters_txt_has_a_line_for_each_part_with_its_exact_start(client, done):
    archive = zip_of(client, done["id"])
    wavs = [n for n in archive.namelist() if n.endswith(".wav")]
    lengths = [(wav_of(archive.read(n))[3], wav_of(archive.read(n))[2]) for n in wavs]  # (frames, rate) of the very files in the ZIP
    expected = ms_of_part_lengths(lengths)
    lines = archive.read("chapters.txt").decode("utf-8").splitlines()
    assert lines == [f"{clock(start)} {title}" for (start, _end), title in zip(expected, ["One", "Two"])]
    assert lines[0] == "00:00:00.000 One" and expected[1][0] == to_ms(Fraction(lengths[0][0], lengths[0][1]))


def test_the_ffmetadata_file_has_the_same_chapters_to_the_millisecond(client, done):
    archive = zip_of(client, done["id"])
    wavs = [n for n in archive.namelist() if n.endswith(".wav")]
    expected = ms_of_part_lengths([(wav_of(archive.read(n))[3], wav_of(archive.read(n))[2]) for n in wavs])
    text = archive.read("chapters.ffmetadata").decode("utf-8")
    assert text.startswith(";FFMETADATA1\ntitle=A test reading\n")
    blocks = [b for b in text.split("[CHAPTER]\n")[1:]]
    found = [(int(b.split("START=")[1].split("\n")[0]), int(b.split("END=")[1].split("\n")[0]), b.split("title=")[1].strip()) for b in blocks]
    assert found == [(s, e, t) for (s, e), t in zip(expected, ["One", "Two"])]
    assert all("TIMEBASE=1/1000" in b for b in blocks)
    assert found[0][1] == found[1][0]  # one chapter ends where the next begins


def test_the_lists_name_only_the_parts_that_are_finished(client):
    run = make_reading(client, [part("One", 1, 1), part("Two", 2, 1, {0: SLOW})])
    wait_until(client, run["id"], lambda r: r["reading"]["parts_done"] == 1, "one part finished")
    archive = zip_of(client, run["id"])
    assert archive.read("chapters.txt").decode().splitlines() == ["00:00:00.000 One"]
    assert archive.read("chapters.ffmetadata").decode().count("[CHAPTER]") == 1
    client.post(f"/api/runs/{run['id']}/cancel")


def test_a_title_with_the_characters_ffmpegs_format_escapes_survives_in_both_lists(client):
    run = finished_reading(client, [{"title": "A=B; C#D \\ end", "paragraphs": part("x", 1, 1)["paragraphs"]}])
    archive = zip_of(client, run["id"])
    assert archive.read("chapters.txt").decode().strip() == "00:00:00.000 A=B; C#D \\ end"  # plain text keeps it as it is
    assert "title=A\\=B\\; C\\#D \\\\ end\n" in archive.read("chapters.ffmetadata").decode()  # ffmpeg's format escapes it


def test_a_part_without_a_title_is_called_part_n_in_both_lists(client):
    run = finished_reading(client, [{"title": "", "paragraphs": part("x", 1, 1)["paragraphs"]}, part("Second", 2, 1)])
    assert zip_of(client, run["id"]).read("chapters.txt").decode().splitlines()[0].endswith(" Part 1")


# ---------------------------------------------------------------- what can be made (criterion 360)
def test_the_capabilities_say_whether_the_audiobook_can_be_made(client):
    from test_speech_server import wait_for_engines

    audiobook_caps = wait_for_engines(client)["speech"]["audiobook"]
    assert set(audiobook_caps) == {"available", "reason", "hint"}
    assert audiobook_caps["available"] is REAL and (audiobook_caps["reason"] is None) is REAL


def stand_in(tmp_path: Path, name: str = "ffmpeg") -> str:
    """A program that pretends to be ffmpeg, for the tests of what the builder does with the real one's behaviour (which it cannot be made to show on demand). It answers
    `-encoders` and `-muxers` as ffmpeg does and, for a build, reads the samples (slowly, or not at all, or fails) and writes a file named by its last argument.
    FAKE_FFMPEG_MODE is `ok`, `noaac` (no AAC encoder), `nomuxer` (cannot write MP4 files), `stall` (never reads), `fail`, `failafter` (writes half a file, then fails); FAKE_FFMPEG_DELAY is the seconds it takes to read each 64 KB; FAKE_FFMPEG_LOG
    names a file that gets a line for each build."""
    script = tmp_path / name
    script.write_text(f'''#!{sys.executable}
import os, sys, time
args = sys.argv[1:]
mode = os.environ.get("FAKE_FFMPEG_MODE", "ok")
if "-encoders" in args:
    print(" A....D aac                  AAC (Advanced Audio Coding)" if mode != "noaac" else " A....D mp3                  MP3 (MPEG audio layer 3)")
    sys.exit(0)
if "-muxers" in args:
    print("  E ipod            iPod H.264 MP4 (MPEG-4 Part 14)" if mode != "nomuxer" else "  E mp3             MP3 (MPEG audio layer 3)")
    sys.exit(0)
log = os.environ.get("FAKE_FFMPEG_LOG")
if log:
    open(log, "a").write("build\\n")
if mode == "stall":
    time.sleep(60)
if mode == "fail":
    sys.stderr.write("Conversion failed: no space left on device\\n")
    sys.exit(1)
if mode == "failafter":
    open(args[-1], "wb").write(b"HALF-A-FILE")  # the file ffmpeg was writing, left half made
    sys.stderr.write("Conversion failed: broken pipe\\n")
    sys.exit(1)
delay = float(os.environ.get("FAKE_FFMPEG_DELAY", "0"))
while sys.stdin.buffer.read(65536):
    time.sleep(delay)
open(args[-1], "wb").write(b"FAKE-M4B" + bytes(100))
''')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def test_without_ffmpeg_the_button_has_its_reason_the_build_is_refused_and_the_zip_still_works(client_factory, tmp_path):
    client = client_factory(speech_ffmpeg=str(tmp_path / "no-such-ffmpeg"))
    run = finished_reading(client)
    status = client.get(f"/api/speech/{run['id']}/audiobook").json()
    assert status["available"] is False and "ffmpeg" in status["reason"] and "not found" in status["reason"] and "ZIP still has every part" in status["hint"]
    assert status["state"] == "idle" and status["parts_made"] == 2  # there are parts to make a file of; it is ffmpeg that is missing
    from test_speech_server import wait_for_engines

    assert wait_for_engines(client)["speech"]["audiobook"] == {"available": False, "reason": status["reason"], "hint": status["hint"]}  # the page knows before a press
    refused = client.post(f"/api/speech/{run['id']}/audiobook")
    assert refused.status_code == 409 and refused.json()["code"] == "audiobook_unavailable" and refused.json()["detail"] == status["reason"] and refused.json()["hint"] == status["hint"]
    archive = zip_of(client, run["id"])  # and the ZIP, with its chapter lists, is as good as ever
    assert "chapters.txt" in archive.namelist() and archive.testzip() is None


def test_an_ffmpeg_without_an_aac_encoder_is_said_to_have_none(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "noaac")
    client = client_factory(speech_ffmpeg=stand_in(tmp_path))
    run = finished_reading(client)
    refused = client.post(f"/api/speech/{run['id']}/audiobook")
    assert refused.status_code == 409 and refused.json()["detail"] == "The audiobook file needs ffmpeg with an AAC encoder, and this server's ffmpeg has none."
    assert "ZIP still has every part" in refused.json()["hint"]
    assert zip_of(client, run["id"]).testzip() is None


def test_an_ffmpeg_that_cannot_write_mp4_files_is_said_to_be_unable_to(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "nomuxer")
    client = client_factory(speech_ffmpeg=stand_in(tmp_path))
    run = finished_reading(client)
    refused = client.post(f"/api/speech/{run['id']}/audiobook")
    assert refused.status_code == 409 and "can write MP4 audiobooks" in refused.json()["detail"]  # it has an AAC encoder, but the file cannot be written


def test_what_is_known_of_ffmpeg_is_kept_a_yes_for_good_a_no_for_a_little_while(tmp_path, monkeypatch):
    forget_probes()
    program = stand_in(tmp_path)
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "noaac")
    assert probe(program, now=1000.0).available is False
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "ok")  # the machine is fixed ...
    assert probe(program, now=1001.0).available is False  # ... but a no is believed for a few seconds, so a polling page does not start a program each time
    assert probe(program, now=1000.0 + audiobook.RECHECK_SECONDS + 1).available is True  # ... and then it is looked at again
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "noaac")
    assert probe(program, now=9999.0).available is True  # a yes is kept for the life of the process
    forget_probes()


def test_a_path_that_is_not_a_program_is_not_ffmpeg(tmp_path):
    forget_probes()
    (tmp_path / "plain.txt").write_text("not a program")
    assert probe(str(tmp_path / "plain.txt")).available is False  # a file that cannot be run is "not found", not an error
    assert "not found" in probe(str(tmp_path / "plain.txt")).reason
    forget_probes()


# ---------------------------------------------------------------- the M4B, made by the real ffmpeg (criteria 356 to 358)
@needs_ffmpeg
def test_the_m4b_is_made_from_the_parts_with_a_chapter_for_each_that_ffprobe_reads_as_the_lists_say(client, done):
    status = make_m4b(client, done["id"])
    assert status["state"] == "ready" and status["error"] is None and status["chapters"] == 2 and status["parts_made"] == status["parts_total"] == 2 and status["complete"] is True
    assert status["url"] == f"/api/speech/{done['id']}/audiobook.m4b" and status["bytes"] > 0 and status["progress"] == 1.0
    taken = client.get(status["url"])
    assert taken.status_code == 200 and len(taken.content) == status["bytes"] and taken.headers["content-type"] == "audio/mp4"
    found = ffprobe(next(client.app.state.jobs.storage.audiobooks_dir.glob("*.m4b")))

    audio = [s for s in found["streams"] if s["codec_type"] == "audio"]
    assert len(audio) == 1 and audio[0]["codec_name"] == "aac" and audio[0]["sample_rate"] == "24000" and audio[0]["channels"] == 1
    assert 0 < int(found["format"]["bit_rate"]) <= 70_000  # at most the 64 kbit/s that was set, give or take the container (a short file of tones comes out lower)

    archive = zip_of(client, done["id"])  # the lists in the ZIP are the chapters in the file, to the millisecond
    listed = [line.split(" ", 1) for line in archive.read("chapters.txt").decode().splitlines()]
    chapters = found["chapters"]
    assert [c["tags"]["title"] for c in chapters] == [title for _clock, title in listed] == ["One", "Two"]
    assert [clock(round(float(c["start_time"]) * 1000)) for c in chapters] == [c for c, _title in listed]
    assert chapters[0]["start"] == 0 and chapters[0]["end"] == chapters[1]["start"] and chapters[0]["time_base"] == "1/1000"

    # the length of the file is the length of the parts end to end, within the frame of AAC the last one is padded to (criterion 356); the end of the last chapter is exact
    wavs = [n for n in archive.namelist() if n.endswith(".wav")]
    exact = sum(Fraction(wav_of(archive.read(n))[3], wav_of(archive.read(n))[2]) for n in wavs)
    assert abs(float(found["format"]["duration"]) - float(exact)) < 0.05
    assert chapters[-1]["end"] == to_ms(exact)


@needs_ffmpeg
def test_the_m4b_carries_a_title_a_genre_and_the_machine_generated_note(client, done):
    make_m4b(client, done["id"])
    found = ffprobe(next(client.app.state.jobs.storage.audiobooks_dir.glob("*.m4b")))
    tags = {k.lower(): v for k, v in found["format"]["tags"].items()}
    assert tags["title"] == "A test reading" and tags["genre"] == "Audiobook"
    assert "machine-generated speech" in tags["comment"] and "Fake speech" in tags["comment"] and "Anna" in tags["comment"]  # as every WAV of it says (§38.4)


@needs_ffmpeg
def test_the_download_is_named_like_the_zip_and_a_player_can_ask_for_a_range(client, done):
    status = make_m4b(client, done["id"])
    whole = client.get(status["url"])
    disposition = whole.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "reading_test-reading_" in disposition and ".m4b" in disposition and "part" not in disposition
    first = client.get(status["url"], headers={"Range": "bytes=0-99"})
    assert first.status_code == 206 and first.content == whole.content[:100] and first.headers["content-range"] == f"bytes 0-99/{len(whole.content)}"
    assert whole.content[4:8] == b"ftyp"  # an MP4 file, with its index at the front where a phone's player wants it (ffmpeg's +faststart)
    assert whole.content.find(b"moov") < whole.content.find(b"mdat")


@needs_ffmpeg
def test_the_file_says_it_is_an_audiobook_in_its_own_first_bytes(client, done):
    """ffmpeg writes the brand of an audiobook only when the name it writes to ends in `.m4b`, and the build writes to `.m4b.part`: the brand is asked for outright, so the file
    is an M4B by what it holds and not only by its extension (found by looking at the first bytes of a file the browser test took)."""
    make_m4b(client, done["id"])
    first = next(client.app.state.jobs.storage.audiobooks_dir.glob("*.m4b")).read_bytes()[:12]
    assert first[4:12] == b"ftypM4B "


@needs_ffmpeg
def test_a_22_khz_engine_makes_a_22_khz_file(client):
    run = finished_reading(client, engine="fake-basic", voice="basic_one")
    make_m4b(client, run["id"])
    found = ffprobe(next(client.app.state.jobs.storage.audiobooks_dir.glob("*.m4b")))
    assert [s["sample_rate"] for s in found["streams"] if s["codec_type"] == "audio"] == ["22050"]  # the reading's own rate: nothing is assumed to be 24 kHz


@needs_ffmpeg
def test_asking_again_for_the_same_parts_gives_the_same_file_at_once_with_no_second_build(client, done, monkeypatch):
    calls = []
    original = audiobook.build_m4b
    monkeypatch.setattr(audiobook, "build_m4b", lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    make_m4b(client, done["id"])
    again = client.post(f"/api/speech/{done['id']}/audiobook")
    assert again.status_code == 200 and again.json()["state"] == "ready"
    # A second build, if one were started, would be a thread that has only just begun when the answer comes back: wait for any build thread to end before counting the builds,
    # so that the count says whether there was a second one and not whether it had got as far as ffmpeg.
    for thread in threading.enumerate():
        if thread.name.startswith("m4b-"):
            thread.join(timeout=60)
    assert len(calls) == 1 and len(list(client.app.state.jobs.storage.audiobooks_dir.glob("*.m4b"))) == 1


@needs_ffmpeg
def test_a_reading_that_has_made_another_part_since_has_another_file_and_the_old_one_is_not_served(client):
    run = make_reading(client, [part("One", 1, 1), part("Two", 2, 1, {0: SLOW})])
    wait_until(client, run["id"], lambda r: r["reading"]["parts_done"] == 1, "one part finished")
    early = make_m4b(client, run["id"])
    assert early["chapters"] == 1 and early["complete"] is False and early["parts_made"] == 1 and early["parts_total"] == 2  # "1 of 2 parts so far", and it says so
    wait_for(client, run["id"])
    late = client.get(f"/api/speech/{run['id']}/audiobook").json()
    assert late["state"] == "idle" and late["complete"] is True and late["url"] is None  # the file of one part is not the file of two
    assert client.get(early["url"]).status_code == 409 and client.get(early["url"]).json()["code"] == "not_built"  # and is never handed over as if it were
    made = make_m4b(client, run["id"])
    assert made["state"] == "ready" and made["chapters"] == 2
    files = list(client.app.state.jobs.storage.audiobooks_dir.glob("*.m4b"))
    assert len(files) == 1 and [c["tags"]["title"] for c in ffprobe(files[0])["chapters"]] == ["One", "Two"]  # the old one went when the new one was there


@needs_ffmpeg
def test_a_title_with_odd_characters_comes_out_of_the_file_as_it_went_in(client):
    run = finished_reading(client, [{"title": "A=B; C#D \\ end — 第一章", "paragraphs": part("x", 1, 1)["paragraphs"]}])
    make_m4b(client, run["id"])
    found = ffprobe(next(client.app.state.jobs.storage.audiobooks_dir.glob("*.m4b")))
    assert found["chapters"][0]["tags"]["title"] == "A=B; C#D \\ end — 第一章"


def test_nothing_finished_is_refused_in_words(client):
    run = wait_for(client, make_reading(client, [part("One", 1, 1, {0: ERROR})])["id"])
    status = client.get(f"/api/speech/{run['id']}/audiobook").json()
    assert status["state"] == "none" and status["parts_made"] == 0 and status["chapters"] == 0
    refused = client.post(f"/api/speech/{run['id']}/audiobook")
    assert refused.status_code == 409 and refused.json()["code"] == "not_finished"


def test_an_unknown_reading_is_not_found_whatever_is_asked(client):
    picture = wait_for(client, __import__("conftest").create_run(client)["id"])
    for run_id in (picture["id"], "d" * 32, "short", "Z" * 32):
        assert client.get(f"/api/speech/{run_id}/audiobook").status_code == 404, run_id
        assert client.post(f"/api/speech/{run_id}/audiobook").status_code == 404, run_id
        assert client.get(f"/api/speech/{run_id}/audiobook.m4b").status_code == 404, run_id


def test_the_file_is_refused_until_it_has_been_made(client, done):
    refused = client.get(f"/api/speech/{done['id']}/audiobook.m4b")
    assert refused.status_code == 409 and refused.json()["code"] == "not_built"


# ---------------------------------------------------------------- a build is a build (criterion 358), with a stand-in ffmpeg
def test_a_build_shows_progress_by_the_part_it_is_in_and_a_second_waits_for_its_turn(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_DELAY", "0.25")  # each 64 KB takes a quarter of a second to take in: a build of a few seconds that can be looked at
    monkeypatch.setenv("FAKE_FFMPEG_LOG", str(tmp_path / "builds.log"))
    client = client_factory(speech_ffmpeg=stand_in(tmp_path))
    first = finished_reading(client)
    second = finished_reading(client)
    started = client.post(f"/api/speech/{first['id']}/audiobook")
    assert started.status_code == 202 and started.json()["state"] in ("waiting", "building")
    waiting = client.post(f"/api/speech/{second['id']}/audiobook")
    assert waiting.status_code == 202
    seen = []
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        a, b = (client.get(f"/api/speech/{r['id']}/audiobook").json() for r in (first, second))
        seen.append((a["state"], b["state"], a["progress"]))
        if a["state"] == b["state"] == "ready":
            break
        time.sleep(0.05)
    assert ("building", "waiting", seen[[s[0:2] for s in seen].index(("building", "waiting"))][2]) in seen  # one at a time: the second said it was waiting
    mid = [p for state, _other, p in seen if state == "building" and p not in (None, 1.0)]
    assert mid and all(0 <= p < 1 for p in mid) and mid == sorted(mid)  # progress moved up from 0 to just under 1, and never down
    assert seen[-1][:2] == ("ready", "ready")
    assert (tmp_path / "builds.log").read_text().count("build") == 2  # and each was built once
    assert client.get(f"/api/speech/{first['id']}/audiobook").json()["part"] is None  # nothing is "in a part" once it is ready


def test_a_build_whose_ffmpeg_takes_in_nothing_is_stopped_and_said_to_have_been(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "stall")
    monkeypatch.setattr(audiobook, "STALL_SECONDS", 2)
    client = client_factory(speech_ffmpeg=stand_in(tmp_path))
    run = finished_reading(client)
    client.post(f"/api/speech/{run['id']}/audiobook")
    status = wait_ready(client, run["id"], timeout=30)
    assert status["state"] == "failed" and "took in no audio for 2 seconds" in status["error"]["message"] and status["error"]["hint"]
    assert not list(client.app.state.jobs.storage.audiobooks_dir.iterdir())  # nothing half-made is left lying
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "ok")  # and it can be tried again
    assert make_m4b(client, run["id"])["state"] == "ready"


def test_an_ffmpeg_that_fails_is_reported_with_its_own_words_and_leaves_nothing_behind(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "fail")
    client = client_factory(speech_ffmpeg=stand_in(tmp_path))
    run = finished_reading(client)
    client.post(f"/api/speech/{run['id']}/audiobook")
    status = wait_ready(client, run["id"])
    assert status["state"] == "failed" and "Conversion failed: no space left on device" in status["error"]["message"]
    assert not list(client.app.state.jobs.storage.audiobooks_dir.iterdir())


def test_the_half_made_file_an_ffmpeg_leaves_when_it_fails_is_removed(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "failafter")
    client = client_factory(speech_ffmpeg=stand_in(tmp_path))
    run = finished_reading(client)
    client.post(f"/api/speech/{run['id']}/audiobook")
    status = wait_ready(client, run["id"])
    assert status["state"] == "failed" and "broken pipe" in status["error"]["message"]
    assert not list(client.app.state.jobs.storage.audiobooks_dir.iterdir())  # the .part ffmpeg wrote is gone, and so is the metadata file
    assert client.get(f"/api/speech/{run['id']}/audiobook.m4b").status_code == 409  # and what was left is never served


def test_a_disk_that_is_too_full_refuses_the_build_with_the_figure(client, done, monkeypatch):
    monkeypatch.setattr(audiobook.shutil, "disk_usage", lambda _path: shutil._ntuple_diskusage(10 * 1024 ** 3, 10 * 1024 ** 3 - 1000, 1000))
    refused = client.post(f"/api/speech/{done['id']}/audiobook")
    assert refused.status_code == 507 and refused.json()["code"] == "storage_full" and "GB of disk" in refused.json()["detail"] and "ZIP does not need the room" in refused.json()["hint"]


def test_the_size_of_the_file_is_estimated_from_the_bit_rate():
    assert estimated_bytes(3600, 64) == 28_800_000  # 64 kbit/s is 8,000 bytes a second: about 29 MB an hour
    assert estimated_bytes(0, 64) == 0


# ---------------------------------------------------------------- the signature of a file
def lengths(*pairs: tuple[str, int]) -> list[PartLength]:
    return [PartLength(title, frames, 24000) for title, frames in pairs]


def test_the_signature_is_twelve_characters_and_depends_on_everything_that_goes_into_the_file():
    base = signature(lengths(("One", 48000), ("Two", 24000)), "A book", 64)
    assert len(base) == 12 and base == signature(lengths(("One", 48000), ("Two", 24000)), "A book", 64)  # the same parts, the same name
    for other in (signature(lengths(("One", 48000)), "A book", 64),  # another part finished
                  signature(lengths(("One", 48000), ("Two", 24001)), "A book", 64),  # a part with another length
                  signature(lengths(("One", 48000), ("2", 24000)), "A book", 64),  # a part with another title
                  signature(lengths(("One", 48000), ("Two", 24000)), "Another", 64),  # another reading title
                  signature(lengths(("One", 48000), ("Two", 24000)), "A book", 96)):  # another bit rate
        assert other != base


# ---------------------------------------------------------------- the builder's arithmetic and memory, with a stand-in process
class StandInProcess:
    """What `subprocess.Popen` gives, but it only counts what it is fed, and writes the file named last when it is waited for."""

    instances: list["StandInProcess"] = []

    def __init__(self, args, **_kw):
        self.args, self.writes, self.closed, self.returncode = args, [], False, None
        self.stdin = self
        self.stderr = io.BytesIO(b"")
        StandInProcess.instances.append(self)

    def write(self, chunk: bytes) -> int:
        self.writes.append(len(chunk))
        return len(chunk)

    def close(self) -> None:
        self.closed = True

    def wait(self) -> int:
        Path(self.args[-1]).write_bytes(b"x")  # the .part file ffmpeg would have written
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def make_parts(tmp_path: Path, shapes: list[tuple[str, int, int]]) -> list[FinishedPart]:
    """Finished parts, each of one piece on disk: (title, frames, rate). The samples are zeros."""
    parts = []
    for n, (title, frames, rate) in enumerate(shapes):
        path = tmp_path / f"p{n}.wav"
        wavfile.write_wav(path, bytes(2 * frames), rate, 1)
        parts.append(FinishedPart(n, title, PartAudio([piece_file(path)], {"title": title})))
    return parts


def test_the_samples_reach_ffmpeg_in_pieces_of_at_most_64_kb_and_all_of_them_without_the_wav_headers(tmp_path):
    StandInProcess.instances.clear()
    parts = make_parts(tmp_path, [("One", 150_000, 24000), ("Two", 80_000, 24000)])
    seen = []
    chapters = build_m4b("ffmpeg", parts, "A book", "note", 64, tmp_path / "out.m4b", lambda fed, total, n: seen.append((fed, total, n)), popen=StandInProcess)
    process = StandInProcess.instances[0]
    assert max(process.writes) <= CHUNK == 64 * 1024  # never more than that at a time: the book is not in memory (criterion 357)
    assert sum(process.writes) == 2 * (150_000 + 80_000) and process.closed  # every sample of both parts, and not a byte of a header
    assert [n for _fed, _total, n in seen][0] == 1 and [n for _f, _t, n in seen][-1] == 2  # it says which part the samples are in
    assert [f for f, _t, _n in seen] == sorted(f for f, _t, _n in seen) and seen[-1][0] == seen[-1][1]  # progress only goes up, and ends at the whole
    assert [c.title for c in chapters] == ["One", "Two"] and chapters[1].start_ms == to_ms(Fraction(150_000, 24000))
    assert (tmp_path / "out.m4b").is_file() and not list(tmp_path.glob("*.part")) and not list(tmp_path.glob("*.meta"))  # renamed when complete; nothing else left


def test_ffmpeg_is_started_with_the_rate_of_the_audio_and_every_value_as_its_own_argument(tmp_path):
    StandInProcess.instances.clear()
    build_m4b("/usr/bin/ffmpeg", make_parts(tmp_path, [("One; rm -rf /", 1000, 22050)]), "x", "n", 96, tmp_path / "a b.m4b", lambda *_: None, popen=StandInProcess)
    args = StandInProcess.instances[0].args
    assert args[0] == "/usr/bin/ffmpeg" and args[args.index("-ar") + 1] == "22050" and args[args.index("-b:a") + 1] == "96k" and args[args.index("-ac") + 1] == "1"
    assert args[-1].endswith("a b.m4b.part")  # a path with a space is one argument: nothing goes through a shell
    assert "-movflags" in args and args[args.index("-movflags") + 1] == "+faststart"


def test_a_long_book_is_built_in_a_few_megabytes_whatever_its_length(tmp_path):
    """Criterion 357: 40 MB of samples (about 14 minutes at 24 kHz; the same code takes eleven hours) goes through with the memory of the chunks, not of the book."""
    StandInProcess.instances.clear()
    parts = make_parts(tmp_path, [(f"Part {n}", 1_000_000, 24000) for n in range(20)])  # 20 x 2 MB
    tracemalloc.start()
    try:
        build_m4b("ffmpeg", parts, "Long", "n", 64, tmp_path / "long.m4b", lambda *_: None, popen=StandInProcess)
        _now, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert sum(StandInProcess.instances[0].writes) == 40_000_000
    assert peak < 3_000_000  # a few chunks and the bookkeeping, a long way under the 40 MB of audio


def test_parts_that_are_not_all_the_same_kind_of_audio_are_refused_before_ffmpeg_is_started(tmp_path):
    StandInProcess.instances.clear()
    with pytest.raises(BuildFailed, match="not all the same kind of audio"):
        build_m4b("ffmpeg", make_parts(tmp_path, [("A", 1000, 24000), ("B", 1000, 22050)]), "x", "n", 64, tmp_path / "o.m4b", lambda *_: None, popen=StandInProcess)
    assert not StandInProcess.instances and not list(tmp_path.glob("o.m4b*"))
    with pytest.raises(BuildFailed, match="No part"):
        build_m4b("ffmpeg", [], "x", "n", 64, tmp_path / "o.m4b", lambda *_: None, popen=StandInProcess)


def test_a_process_that_ends_badly_leaves_no_file_and_the_error_is_a_sentence(tmp_path):
    class Bad(StandInProcess):
        def wait(self) -> int:
            self.returncode = 3
            return 3

    with pytest.raises(BuildFailed, match="ended with code 3") as caught:
        build_m4b("ffmpeg", make_parts(tmp_path, [("A", 1000, 24000)]), "x", "n", 64, tmp_path / "o.m4b", lambda *_: None, popen=Bad)
    assert caught.value.hint and not list(tmp_path.glob("o.m4b*"))


# ---------------------------------------------------------------- where the file lives and when it goes (criterion 359)
def audiobooks_of(client) -> Audiobooks:
    return client.app.state.jobs.speech.audiobooks


def write_file(directory: Path, name: str, age_hours: float, size: int = 100) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(bytes(size))
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


@needs_ffmpeg
def test_the_file_lives_in_the_audiobooks_folder_of_the_speech_data_and_is_named_for_its_reading(client, done):
    make_m4b(client, done["id"])
    storage = client.app.state.jobs.storage
    files = list(storage.audiobooks_dir.glob("*"))
    assert storage.audiobooks_dir == storage.speech / "audiobooks" and len(files) == 1
    assert files[0].name.startswith(f"{done['id']}-") and files[0].suffix == ".m4b" and len(files[0].stem.split("-")[-1]) == 12


def test_the_sweep_removes_files_kept_past_their_day_and_orphaned_partials_but_not_what_is_recent_or_being_made(client_factory):
    client = client_factory(quiet=True)
    books = audiobooks_of(client)
    folder = client.app.state.jobs.storage.audiobooks_dir
    old = write_file(folder, f"{'a' * 32}-aaaaaaaaaaaa.m4b", 25)  # kept past the 24 hours: goes
    recent = write_file(folder, f"{'b' * 32}-bbbbbbbbbbbb.m4b", 23)  # not yet
    orphan = write_file(folder, f"{'c' * 32}-cccccccccccc.m4b.part", 0.1)  # a restart left it: no build here is making it, so it goes whatever its age
    meta = write_file(folder, f"{'c' * 32}-cccccccccccc.m4b.meta", 0.1)
    active = write_file(folder, f"{'d' * 32}-dddddddddddd.m4b.part", 0.1)  # a build here is making this one: it stays
    books._builds["d" * 32] = audiobook._Build("dddddddddddd", 100)
    books._builds["d" * 32].state = "building"
    removed = books.sweep()
    assert removed == {"files": 1, "partial": 2}
    assert not old.exists() and recent.exists() and not orphan.exists() and not meta.exists() and active.exists()


def test_the_hourly_clean_up_does_it_and_says_how_much(client_factory):
    client = client_factory(quiet=True)
    folder = client.app.state.jobs.storage.audiobooks_dir
    write_file(folder, f"{'a' * 32}-aaaaaaaaaaaa.m4b", 48)
    answer = client.portal.call(client.app.state.jobs.sweep_speech_temp)
    assert answer["audiobooks"] == 1 and answer["audiobooks_partial"] == 0 and "recordings" in answer  # (the voice features' own counts are still there)
    assert not list(folder.iterdir())


def test_a_file_that_is_taken_again_is_kept_another_day(client, done):
    if not REAL:
        pytest.skip("needs ffmpeg")
    status = make_m4b(client, done["id"])
    path = next(client.app.state.jobs.storage.audiobooks_dir.glob("*.m4b"))
    old = time.time() - 23 * 3600
    os.utime(path, (old, old))
    assert client.get(status["url"]).status_code == 200  # taken
    assert path.stat().st_mtime > old + 22 * 3600  # so the day starts again (and it is not swept an hour later while a phone is still on its way to it)


def test_a_reading_deleted_for_good_takes_its_audiobook_files_and_nobody_elses(client_factory):
    client = client_factory(quiet=True)
    storage = client.app.state.jobs.storage
    mine = write_file(storage.audiobooks_dir, f"{'a' * 32}-aaaaaaaaaaaa.m4b", 1)
    half = write_file(storage.audiobooks_dir, f"{'a' * 32}-bbbbbbbbbbbb.m4b.part", 1)
    other = write_file(storage.audiobooks_dir, f"{'b' * 32}-aaaaaaaaaaaa.m4b", 1)
    storage.delete_run_files("a" * 32)
    assert not mine.exists() and not half.exists() and other.exists()
