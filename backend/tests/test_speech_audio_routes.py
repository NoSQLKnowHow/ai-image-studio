"""The audio of a reading, over HTTP (DESIGN.md §38.7, criteria 282 to 288): a part as one WAV built from its pieces, a piece on its own as soon as it is made
(play as it is made), range requests so a player can seek, the download names, the ZIP of a whole reading with its playlist, and where the listener stopped.

The unit tests of the byte-level pieces (the WAV built from pieces, the range parser, the ZIP writer) are in test_speech_audio.py; these check that the routes
put them together correctly with a real reading made by the fake engine.
"""

from __future__ import annotations

import io
import wave
import zipfile
from typing import Any

import pytest
from conftest import create_run, wait_for
from test_speech_readings import ERROR, SLOW, finished_reading, make_reading, part, wait_until


def wav_of(data: bytes) -> tuple[int, int, int, int, bytes]:
    """(channels, sample width, rate, frames, samples) of a WAV, read with the standard library, which is a second opinion on the header we wrote."""
    with wave.open(io.BytesIO(data)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes(), w.readframes(w.getnframes())


@pytest.fixture
def done(client) -> dict[str, Any]:
    """A finished reading of two parts (3 and 2 pieces) made with the full fake engine at 24 kHz."""
    return finished_reading(client)


def part_wav(client, run_id: str, idx: int = 0, **kw):
    return client.get(f"/api/speech/{run_id}/parts/{idx}.wav", **kw)


# ---------------------------------------------------------------- the WAV of a part
def test_a_part_is_one_wav_that_is_its_pieces_end_to_end(client, done):
    response = part_wav(client, done["id"])
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav" and response.headers["accept-ranges"] == "bytes"
    assert int(response.headers["content-length"]) == len(response.content)
    channels, width, rate, frames, samples = wav_of(response.content)
    assert (channels, width, rate) == (1, 2, 24000)
    detail = client.get(f"/api/readings/{done['id']}/parts/0").json()
    assert frames == detail["total_frames"] == sum(p["frames"] for p in detail["pieces"])  # the length is exactly the sum of the pieces (criterion 282)
    # the samples are the pieces' samples, in order, with nothing added or lost between them
    joined = b"".join(wav_of(client.get(p["url"]).content)[4] for p in detail["pieces"])
    assert samples == joined


def test_the_second_part_is_its_own_wav_not_the_whole_reading(client, done):
    first, second = (wav_of(part_wav(client, done["id"], n).content) for n in (0, 1))
    assert first[3] > second[3] > 0  # three paragraphs against two
    assert first[4] != second[4]


def test_a_wav_says_it_is_machine_generated_speech_and_names_the_engine_and_voice(client, done):
    head = part_wav(client, done["id"]).content[:2048]
    assert b"machine-generated speech" in head and b"Fake speech" in head and b"Anna" in head and b"ai-image-studio" in head
    assert b"cloned" not in head  # a preset voice is not described as made from a recording


def test_an_engines_licence_notice_travels_inside_every_wav_it_made(client):
    """Criterion 283: the notice an engine's licence asks for is in the file itself, so it stays with the audio when the file is passed on. Only the
    plain fake engine has one."""
    run = finished_reading(client, engine="fake-basic", voice="basic_one")
    head = part_wav(client, run["id"]).content[:2048]
    assert b"A test notice for the licence badge." in head and b"Fake basic speech" in head and b"Basic One" in head


def test_the_part_has_a_download_name_made_of_the_title_part_voice_and_date(client, done):
    plain = part_wav(client, done["id"])
    assert "content-disposition" not in plain.headers  # playing it is not downloading it
    named = client.get(f"/api/speech/{done['id']}/parts/1.wav", params={"download": "1"})
    disposition = named.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "reading_test-reading_part02_anna_" in disposition  # ("a" is dropped from a title's words, as everywhere in the studio) and disposition.count(".wav") >= 1


@pytest.mark.parametrize("header,first,last", [
    ("bytes=0-43", 0, 43),           # the WAV header alone
    ("bytes=44-", 44, None),         # from the first sample to the end
    ("bytes=-100", None, None),      # the last hundred bytes
    ("bytes=10-10", 10, 10),         # one byte
])
def test_a_range_gets_exactly_those_bytes_and_says_where_they_are(client, done, header, first, last):
    whole = part_wav(client, done["id"]).content
    response = part_wav(client, done["id"], headers={"Range": header})
    size = len(whole)
    if first is None:
        first, last = size - 100, size - 1
    last = size - 1 if last is None else last
    assert response.status_code == 206 and response.content == whole[first:last + 1]
    assert response.headers["content-range"] == f"bytes {first}-{last}/{size}" and int(response.headers["content-length"]) == last - first + 1


def test_a_range_across_the_end_of_one_piece_and_the_start_of_the_next_is_seamless(client, done):
    """The case the virtual file exists for: bytes that straddle two files on the disk."""
    detail = client.get(f"/api/readings/{done['id']}/parts/0").json()
    whole = part_wav(client, done["id"]).content
    boundary = len(whole) - sum(p["frames"] for p in detail["pieces"]) * 2 + detail["pieces"][0]["frames"] * 2  # where piece 0 ends in the WAV
    got = part_wav(client, done["id"], headers={"Range": f"bytes={boundary - 7}-{boundary + 8}"})
    assert got.status_code == 206 and got.content == whole[boundary - 7:boundary + 9] and len(got.content) == 16


def test_a_range_past_the_end_is_refused_and_a_range_it_cannot_read_is_ignored(client, done):
    size = len(part_wav(client, done["id"]).content)
    past = part_wav(client, done["id"], headers={"Range": f"bytes={size + 10}-"})
    assert past.status_code == 416 and past.headers["content-range"] == f"bytes */{size}"
    for odd in ("bytes=abc-def", "bytes=0-5,10-15", "pages=1-2", "bytes=5"):
        assert part_wav(client, done["id"], headers={"Range": odd}).status_code == 200, odd  # the whole file, as a server may answer


def test_a_part_that_is_not_finished_has_no_wav_but_its_made_pieces_can_be_played(client):
    run = wait_for(client, make_reading(client, [part("One", 1, 2, {1: ERROR})])["id"])
    assert run["status"] == "failed"
    unfinished = part_wav(client, run["id"])
    assert unfinished.status_code == 409 and unfinished.json()["code"] == "not_finished"
    pieces = client.get(f"/api/readings/{run['id']}/parts/0").json()["pieces"]
    assert pieces[0]["url"] and pieces[1]["url"] is None  # play as it is made: the made one has an address, the other none
    assert client.get(pieces[0]["url"]).status_code == 200
    assert client.get(f"/api/speech/{run['id']}/parts/0/pieces/1.wav").status_code == 404  # and asking for the other anyway is not found


def test_a_piece_is_a_wav_of_its_own_and_a_player_can_seek_in_it(client, done):
    piece = client.get(f"/api/speech/{done['id']}/parts/0/pieces/0.wav")
    assert piece.status_code == 200 and piece.headers["content-type"] == "audio/wav" and wav_of(piece.content)[:3] == (1, 2, 24000)
    ranged = client.get(f"/api/speech/{done['id']}/parts/0/pieces/0.wav", headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206 and ranged.content == piece.content[:10]


def test_a_piece_includes_the_pause_after_it_so_pieces_laid_end_to_end_are_the_part(client, done):
    detail = client.get(f"/api/readings/{done['id']}/parts/0").json()
    heading_free = [p for p in detail["pieces"] if p["kind"] == "paragraph"]
    # the fake engine speaks 0.3 s + 0.012 s per character; the pause (450 ms by default) is added to every piece by the worker
    for p in heading_free:
        spoken = min(8.0, 0.3 + 0.012 * len(p["text"]))
        assert p["frames"] / 24000 == pytest.approx(spoken + 0.45, abs=0.01)


def test_the_start_of_each_piece_in_the_part_is_where_the_pieces_before_it_end(client, done):
    detail = client.get(f"/api/readings/{done['id']}/parts/0").json()
    starts, expected = [p["start_frame"] for p in detail["pieces"]], 0
    for p in detail["pieces"]:
        assert p["start_frame"] == expected
        expected += p["frames"]
    assert starts[0] == 0 and detail["total_frames"] == expected and detail["complete"] is True
    assert detail["url"] == f"/api/speech/{done['id']}/parts/0.wav" and detail["download_url"].endswith("?download=1")


@pytest.mark.parametrize("path", ["parts/7.wav", "parts/0/pieces/99.wav", "parts/9/pieces/0.wav"])
def test_audio_that_does_not_exist_is_not_found(client, done, path):
    assert client.get(f"/api/speech/{done['id']}/{path}").status_code == 404


def test_audio_of_a_picture_or_a_stranger_is_not_found(client, done):
    picture = wait_for(client, create_run(client)["id"])
    for run_id in (picture["id"], "d" * 32, "short", "Z" * 32):
        assert client.get(f"/api/speech/{run_id}/parts/0.wav").status_code == 404, run_id
        assert client.get(f"/api/speech/{run_id}/parts/0/pieces/0.wav").status_code == 404, run_id
        assert client.get(f"/api/speech/{run_id}/download.zip").status_code == 404, run_id


def test_a_piece_whose_file_has_gone_is_an_error_not_a_crash(client, done):
    storage = client.app.state.jobs.storage
    (storage.speech / "readings" / done["id"] / "0000" / "00001.wav").unlink()
    response = part_wav(client, done["id"])
    assert response.status_code == 500 and response.json()["code"] == "audio_unreadable"
    assert part_wav(client, done["id"], 1).status_code == 200  # and the other part is not affected


def test_a_piece_with_damaged_audio_is_an_error_not_a_crash(client, done):
    storage = client.app.state.jobs.storage
    (storage.speech / "readings" / done["id"] / "0000" / "00000.wav").write_bytes(b"this is not a wav file at all")
    response = part_wav(client, done["id"])
    assert response.status_code == 500 and response.json()["code"] == "audio_unreadable"


# ---------------------------------------------------------------- the ZIP of a whole reading
def test_the_zip_has_every_part_stored_and_a_playlist_in_order(client, done):
    response = client.get(f"/api/speech/{done['id']}/download.zip")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith("attachment;") and "reading_test-reading_" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None  # every CRC is right
    names = archive.namelist()
    # 1.25 added two chapter lists AFTER the parts and the playlist (DESIGN.md §44.4); what was there before is where it was, in the same order, so this test has changed
    # in one place only: the count and the tail. The chapter lists themselves are tested in test_speech_audiobook.py.
    assert len(names) == 5 and names[2] == "playlist.m3u" and names[0].startswith("reading_test-reading_part01_anna_") and names[1].startswith("reading_test-reading_part02_")
    assert names[3:] == ["chapters.txt", "chapters.ffmetadata"]
    assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())  # audio does not shrink, and a book is large (criterion 286)
    for n, name in enumerate(names[:2]):
        assert archive.read(name) == part_wav(client, done["id"], n).content  # the same bytes the player gets
    playlist = archive.read("playlist.m3u").decode("utf-8").splitlines()
    assert playlist[0] == "#EXTM3U" and playlist[2] == names[0] and playlist[4] == names[1]
    assert playlist[1].startswith("#EXTINF:") and playlist[1].endswith(",One") and playlist[3].endswith(",Two")


def test_the_zip_of_a_reading_still_being_made_has_the_parts_that_are_finished(client):
    run = make_reading(client, [part("One", 1, 1), part("Two", 2, 1, {0: SLOW})])
    wait_until(client, run["id"], lambda r: r["reading"]["parts_done"] == 1, "one part finished")
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/speech/{run['id']}/download.zip").content))
    assert [n.split("_part")[-1][:2] for n in archive.namelist() if n.endswith(".wav")] == ["01"]
    client.post(f"/api/runs/{run['id']}/cancel")


def test_the_zip_of_a_reading_with_nothing_finished_is_refused(client):
    run = wait_for(client, make_reading(client, [part("One", 1, 1, {0: ERROR})])["id"])
    refused = client.get(f"/api/speech/{run['id']}/download.zip")
    assert refused.status_code == 409 and refused.json()["code"] == "not_finished"


# ---------------------------------------------------------------- where the listener stopped
def test_the_position_is_kept_one_for_each_reading_and_replaced_by_the_next(client, done):
    assert done["reading"]["position"] is None
    first = client.put(f"/api/readings/{done['id']}/position", json={"part": 1, "piece": 0, "offset_ms": 1500})
    assert first.status_code == 204 and first.content == b""
    assert client.get(f"/api/readings/{done['id']}").json()["reading"]["position"] == {"part": 1, "piece": 0, "offset_ms": 1500}
    client.put(f"/api/readings/{done['id']}/position", json={"part": 0, "piece": 2, "offset_ms": 0})
    assert client.get(f"/api/runs/{done['id']}").json()["reading"]["position"] == {"part": 0, "piece": 2, "offset_ms": 0}  # replaced, not added
    assert client.app.state.jobs.db.read_one("SELECT COUNT(*) AS n FROM speech_positions")["n"] == 1


@pytest.mark.parametrize("body", [
    {"part": -1, "piece": 0, "offset_ms": 0}, {"part": 0, "piece": -1, "offset_ms": 0}, {"part": 0, "piece": 0, "offset_ms": -5},
    {"part": 0, "piece": 0}, {"part": "a", "piece": 0, "offset_ms": 0}, {"part": 0, "piece": 0, "offset_ms": 0, "extra": 1}, {"part": 0.5, "piece": 0, "offset_ms": 0},
])
def test_a_position_that_makes_no_sense_is_refused(client, done, body):
    assert client.put(f"/api/readings/{done['id']}/position", json=body).status_code == 422
    assert client.get(f"/api/readings/{done['id']}").json()["reading"]["position"] is None


def test_a_position_for_something_that_is_not_a_reading_is_not_found(client, done):
    picture = wait_for(client, create_run(client)["id"])
    for run_id in (picture["id"], "c" * 32, "x"):
        assert client.put(f"/api/readings/{run_id}/position", json={"part": 0, "piece": 0, "offset_ms": 0}).status_code == 404, run_id


def test_every_change_to_a_reading_needs_the_client_header(client, done):
    """The guard that stops another web page from driving this studio (security.py) covers the new routes too."""
    for method, url, body in [
        ("post", "/api/readings", {}), ("post", f"/api/readings/{done['id']}/continue", None),
        ("patch", f"/api/readings/{done['id']}/parts/0/pieces/0", {"text": "x"}),
        ("put", f"/api/readings/{done['id']}/position", {"part": 0, "piece": 0, "offset_ms": 0}),
    ]:
        kwargs: dict[str, Any] = {"json": body} if body is not None else {}
        refused = getattr(client, method)(url, headers={"X-Studio-Client": ""}, **kwargs)
        assert refused.status_code == 403 and refused.json()["code"] == "missing_client_header", (method, url)


# ---------------------------------------------------------------- the bin, projects and deleting for good
def test_deleting_a_reading_for_good_removes_its_files_and_its_rows(client, done):
    storage, db = client.app.state.jobs.storage, client.app.state.jobs.db
    folder = storage.speech / "readings" / done["id"]
    assert folder.is_dir() and len(list(folder.rglob("*.wav"))) == 5
    assert client.delete(f"/api/runs/{done['id']}").status_code == 204
    assert not folder.exists() and client.get(f"/api/readings/{done['id']}").status_code == 404
    for table in ("speech_parts", "speech_pieces", "speech_positions"):
        assert db.read_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table


def test_a_reading_in_the_bin_is_kept_until_the_bin_is_emptied(client, done):
    storage = client.app.state.jobs.storage
    folder = storage.speech / "readings" / done["id"]
    binned = client.post(f"/api/runs/{done['id']}/bin")
    assert binned.status_code == 200 and binned.json()["deleted_at"] and folder.is_dir()
    assert client.get("/api/runs", params={"kind": "speech"}).json()["runs"] == []  # gone from the history...
    assert [r["id"] for r in client.get("/api/runs", params={"kind": "speech", "deleted": "true"}).json()["runs"]] == [done["id"]]  # ...and listed in the bin
    assert part_wav(client, done["id"]).status_code == 200  # it can still be listened to from the bin, as a picture in the bin can still be seen
    assert client.post(f"/api/runs/{done['id']}/restore").json()["deleted_at"] is None
    client.post(f"/api/runs/{done['id']}/bin")
    assert client.delete("/api/bin").json()["deleted"] == 1 and not folder.exists()


def test_a_reading_can_be_filed_in_a_project_and_is_then_kept_and_counted(client, done):
    project = client.post("/api/projects", json={"name": "Audiobooks"}).json()
    filed = client.put(f"/api/runs/{done['id']}/project", json={"project_id": project["id"]})
    assert filed.status_code == 200 and filed.json()["project_id"] == project["id"] and filed.json()["pinned"] is True
    counts = client.get("/api/projects").json()["projects"][0]["counts"]
    assert counts["speech"] == 1 and counts["image"] == 0
    assert [r["id"] for r in client.get("/api/runs", params={"project": project["id"], "kind": "speech"}).json()["runs"]] == [done["id"]]
