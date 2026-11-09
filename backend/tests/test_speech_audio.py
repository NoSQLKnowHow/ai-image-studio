"""The byte-level pieces of a reading's audio (DESIGN.md §38.7, criteria 282, 285, 286): the WAV of a part built from its pieces without being stored, the
parser of range requests, and the ZIP written as it streams, including the ZIP64 forms a book of several gigabytes needs.

The ZIP is read back with Python's own `zipfile`, an independent reader of the format, so these tests do not just check the writer against itself.
"""

from __future__ import annotations

import io
import random
import struct
import zipfile
from pathlib import Path

import pytest

import studio.speech.audio as audio_module
from studio import wavfile
from studio.speech.audio import PartAudio, RangeNotSatisfiable, ZipMember, parse_range, piece_file, stream_zip
from studio.wavfile import WavError


def pcm(frames: int, seed: int = 0) -> bytes:
    """`frames` mono 16-bit samples that differ from one piece to the next, so a byte from the wrong place shows."""
    rng = random.Random(seed)
    return struct.pack(f"<{frames}h", *[rng.randint(-30000, 30000) for _ in range(frames)])


def write(path: Path, frames: int, seed: int = 0, rate: int = 24000, info=None) -> bytes:
    data = pcm(frames, seed)
    wavfile.write_wav(path, data, rate, channels=1, info=info)
    return data


@pytest.fixture
def pieces(tmp_path):
    """Three pieces of different lengths (one is a single sample, so a boundary can fall anywhere), and their samples."""
    lengths = [1000, 1, 2500]
    data = [write(tmp_path / f"{n}.wav", frames, seed=n) for n, frames in enumerate(lengths)]
    return [piece_file(tmp_path / f"{n}.wav") for n in range(3)], data


# ---------------------------------------------------------------- finding the samples in a piece's file
def test_the_samples_are_found_after_the_header_with_or_without_an_information_chunk(tmp_path):
    plain = write(tmp_path / "plain.wav", 100)
    noted = write(tmp_path / "noted.wav", 100, info={"title": "T", "comment": "a note that makes the header longer"})
    for name, data in (("plain", plain), ("noted", noted)):
        found = piece_file(tmp_path / f"{name}.wav")
        raw = (tmp_path / f"{name}.wav").read_bytes()
        assert (found.sample_rate, found.channels, found.length) == (24000, 1, len(data)) and raw[found.offset:found.offset + found.length] == data
    assert piece_file(tmp_path / "noted.wav").offset > piece_file(tmp_path / "plain.wav").offset


def test_a_file_that_is_not_a_wav_of_ours_is_refused_in_words(tmp_path):
    cases = {"text.wav": b"not audio at all, just text", "short.wav": b"RIFF", "empty.wav": b""}
    for name, content in cases.items():
        (tmp_path / name).write_bytes(content)
        with pytest.raises(WavError, match="is not a WAV file"):
            piece_file(tmp_path / name)
    with pytest.raises(WavError, match="cannot read"):
        piece_file(tmp_path / "missing.wav")


def test_float_samples_and_a_file_with_no_audio_are_refused(tmp_path):
    header = b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, 24000, 96000, 4, 32)  # format 3: floating point
    (tmp_path / "float.wav").write_bytes(header + b"data" + struct.pack("<I", 0))
    with pytest.raises(WavError, match="not 16-bit PCM"):
        piece_file(tmp_path / "float.wav")
    pcm_header = b"RIFF" + struct.pack("<I", 28) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
    (tmp_path / "none.wav").write_bytes(pcm_header)
    with pytest.raises(WavError, match="no audio"):
        piece_file(tmp_path / "none.wav")


def test_a_file_cut_short_reports_only_the_samples_it_has(tmp_path):
    write(tmp_path / "cut.wav", 1000)
    raw = (tmp_path / "cut.wav").read_bytes()
    (tmp_path / "cut.wav").write_bytes(raw[:-600])
    assert piece_file(tmp_path / "cut.wav").length == 2000 - 600


# ---------------------------------------------------------------- a part built from pieces
def test_the_whole_part_is_its_header_and_then_every_piece_in_order(pieces):
    files, data = pieces
    part = PartAudio(files)
    whole = b"".join(part.iter_bytes())
    assert len(whole) == part.size and whole[len(part.header):] == b"".join(data) and part.data_bytes == sum(len(d) for d in data)
    with __import__("wave").open(io.BytesIO(whole)) as w:  # and the standard library agrees it is one WAV
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 24000, 3501)
    assert part.seconds == pytest.approx(3501 / 24000)


def test_every_byte_range_gives_the_same_bytes_as_cutting_the_whole_file(pieces):
    """The property that makes seeking safe: for any start and end, including every boundary and one on either side, the range is the slice."""
    files, data = pieces
    part = PartAudio(files)
    whole = b"".join(part.iter_bytes())
    head = len(part.header)
    edges = {0, 1, head - 1, head, head + 1, head + len(data[0]) - 1, head + len(data[0]), head + len(data[0]) + 1, head + len(data[0]) + len(data[1]),
             head + len(data[0]) + len(data[1]) + 1, len(whole) - 2, len(whole) - 1}
    for start in sorted(edges):
        for end in sorted(edges):
            if end >= start:
                assert b"".join(part.iter_bytes(start, end)) == whole[start:end + 1], (start, end)
    rng = random.Random(7)
    for _ in range(200):
        start = rng.randrange(len(whole))
        end = rng.randrange(start, len(whole))
        assert b"".join(part.iter_bytes(start, end)) == whole[start:end + 1]


def test_a_range_past_the_end_is_cut_to_it_and_one_wholly_past_gives_nothing(pieces):
    part = PartAudio(pieces[0])
    whole = b"".join(part.iter_bytes())
    assert b"".join(part.iter_bytes(len(whole) - 3, len(whole) + 999)) == whole[-3:]
    assert list(part.iter_bytes(len(whole), len(whole) + 5)) == [] and list(part.iter_bytes(len(whole) + 1)) == []


def test_only_the_files_a_range_needs_are_opened(pieces):
    """A seek into the last piece must not read the first: the first file is deleted, and the range still works."""
    files, data = pieces
    part = PartAudio(files)
    files[0].path.unlink()
    start = len(part.header) + len(data[0]) + len(data[1])
    assert b"".join(part.iter_bytes(start, start + 99)) == data[2][:100]
    with pytest.raises(FileNotFoundError):
        b"".join(part.iter_bytes())  # but the whole of it needs the file that is gone


def test_a_piece_shorter_than_its_header_said_is_an_error_not_silence(pieces, tmp_path):
    files, _ = pieces
    part = PartAudio(files)
    raw = files[2].path.read_bytes()
    files[2].path.write_bytes(raw[:-100])  # cut after the part was set up
    with pytest.raises(WavError, match="shorter than its header says"):
        b"".join(part.iter_bytes())


def test_a_big_piece_is_streamed_in_chunks_not_read_whole(tmp_path):
    write(tmp_path / "big.wav", 100_000)  # 200 KB, three chunks of 64 KB
    chunks = list(PartAudio([piece_file(tmp_path / "big.wav")]).iter_bytes())
    assert len(chunks) >= 4 and max(len(c) for c in chunks) <= audio_module.CHUNK


def test_pieces_of_different_kinds_of_audio_cannot_be_laid_end_to_end(tmp_path):
    write(tmp_path / "a.wav", 10, rate=24000)
    write(tmp_path / "b.wav", 10, rate=22050)
    with pytest.raises(WavError, match="not all the same kind"):
        PartAudio([piece_file(tmp_path / "a.wav"), piece_file(tmp_path / "b.wav")])
    with pytest.raises(WavError, match="no audio made yet"):
        PartAudio([])


def test_the_information_note_is_in_the_header(pieces):
    part = PartAudio(pieces[0], {"title": "Chapter One", "comment": "machine-generated speech"})
    assert b"Chapter One" in part.header and b"machine-generated speech" in part.header
    assert b"".join(part.iter_bytes())[len(part.header):] == b"".join(pieces[1])


# ---------------------------------------------------------------- range requests
@pytest.mark.parametrize("header,size,expected", [
    (None, 100, None), ("", 100, None), ("bytes=0-9", 100, (0, 9)), ("bytes=90-", 100, (90, 99)), ("bytes=-10", 100, (90, 99)),
    ("bytes=0-999", 100, (0, 99)), ("bytes=-999", 100, (0, 99)), ("bytes=99-99", 100, (99, 99)), ("bytes= 5-6 ", 100, (5, 6)),
    ("bytes=0-0", 1, (0, 0)),
    ("pages=1-2", 100, None), ("bytes=0-5,10-15", 100, None), ("bytes=5", 100, None), ("bytes=a-b", 100, None), ("bytes=-", 100, None),
])
def test_what_range_header_asks_for(header, size, expected):
    assert parse_range(header, size) == expected


@pytest.mark.parametrize("header", ["bytes=100-", "bytes=500-600", "bytes=-0", "bytes=10-5"])
def test_a_range_that_cannot_be_satisfied_is_said_to_be_so(header):
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, 100)


# ---------------------------------------------------------------- the ZIP
def member(name: str, content: bytes, chunk: int = 1000) -> ZipMember:
    return ZipMember(name, len(content), lambda: (content[i:i + chunk] for i in range(0, len(content), chunk)))


def central_entries(blob: bytes) -> dict[str, dict]:
    """The raw central-directory records of a ZIP, by member name: the 32-bit fields exactly as written and the ZIP64 extra field, if any. Python's reader
    hides the difference between 'the value is in the 32-bit field' and 'the value is in the ZIP64 field' (it just returns the value), so a test that wants to
    know which was used has to read the bytes."""
    entries: dict[str, dict] = {}
    at = blob.rfind(b"PK\x01\x02")
    start = blob.find(b"PK\x01\x02")
    at = start
    while at != -1 and blob[at:at + 4] == b"PK\x01\x02":
        (_sig, _made, _need, _flags, _method, _time, _date, crc, packed, size, name_len, extra_len, comment_len, _disk, _internal, _external,
         offset) = struct.unpack("<IHHHHHHIIIHHHHHII", blob[at:at + 46])
        name = blob[at + 46:at + 46 + name_len].decode("utf-8")
        extra = blob[at + 46 + name_len:at + 46 + name_len + extra_len]
        entries[name] = {"packed": packed, "size": size, "offset": offset, "zip64": extra[4:] if extra[:2] == b"\x01\x00" else None}
        at += 46 + name_len + extra_len + comment_len
    return entries


def read_zip(blob: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(blob))


def test_a_zip_of_stored_members_is_read_back_exactly_and_its_checksums_hold():
    members = {"one.wav": pcm(5000, 1), "two.wav": pcm(1, 2), "empty.bin": b"", "playlist.m3u": b"#EXTM3U\none.wav\n"}
    archive = read_zip(b"".join(stream_zip([member(n, c) for n, c in members.items()], stamp=1_700_000_000)))
    assert archive.testzip() is None and archive.namelist() == list(members)
    assert {n: archive.read(n) for n in members} == members
    assert all(i.compress_type == zipfile.ZIP_STORED and i.file_size == len(members[i.filename]) for i in archive.infolist())


def test_a_name_in_another_script_is_kept_exactly():
    name = "reading_Ünïcode_部分_01.wav"
    archive = read_zip(b"".join(stream_zip([member(name, b"abc")])))
    assert archive.namelist() == [name] and archive.read(name) == b"abc"
    assert archive.infolist()[0].flag_bits & 0x800  # bit 11: the name is UTF-8, which is how a reader knows not to take it for a legacy code page


def test_a_date_before_1980_is_clamped_since_the_format_starts_there():
    archive = read_zip(b"".join(stream_zip([member("a", b"x")], stamp=0)))
    assert archive.infolist()[0].date_time[0] >= 1980


def test_the_zip_is_a_stream_the_first_bytes_come_before_the_last_member_is_asked_for():
    asked: list[str] = []

    def chunks(name: str):
        def produce():
            asked.append(name)
            yield b"data"
        return produce

    stream = stream_zip([ZipMember("a", 4, chunks("a")), ZipMember("b", 4, chunks("b"))])
    next(stream)  # the header of the first member
    assert asked == []
    next(stream)  # its data
    assert asked == ["a"]  # and the second member has not been touched
    list(stream)
    assert asked == ["a", "b"]


def test_a_member_that_turns_out_a_different_size_than_promised_is_an_error():
    short = ZipMember("a", 10, lambda: iter([b"12345"]))
    with pytest.raises(WavError, match="was 5 bytes long, not the 10"):
        b"".join(stream_zip([short]))


def test_a_zip_without_members_is_still_a_zip():
    archive = read_zip(b"".join(stream_zip([])))
    assert archive.namelist() == []


def test_members_of_zip64_size_are_written_in_the_zip64_form_and_read_back(monkeypatch):
    """A member of 4 GiB cannot be made in a test, so the threshold is lowered to 100 bytes: the code path is the same, the number in it is smaller."""
    monkeypatch.setattr(audio_module, "ZIP64_LIMIT", 100)
    members = {"big.wav": pcm(500, 3), "small.txt": b"tiny", "big2.wav": pcm(200, 4)}
    blob = b"".join(stream_zip([member(n, c, chunk=64) for n, c in members.items()]))
    archive = read_zip(blob)
    assert archive.testzip() is None and {n: archive.read(n) for n in members} == members
    info = {i.filename: i for i in archive.infolist()}
    assert info["big.wav"].extract_version == 45 and info["small.txt"].extract_version in (20, 45)  # (45 where its offset needed ZIP64)
    assert b"\x50\x4b\x06\x06" in blob and b"\x50\x4b\x06\x07" in blob  # the ZIP64 end-of-directory record and its locator are there
    # and the sizes really are in the ZIP64 field: the 32-bit ones say "look there" and the field holds the true size, twice (stored: packed = unpacked)
    raw = central_entries(blob)
    assert (raw["big.wav"]["packed"], raw["big.wav"]["size"]) == (0xFFFFFFFF, 0xFFFFFFFF) and raw["big.wav"]["zip64"][:16] == struct.pack("<QQ", 1000, 1000)
    # the small member keeps its sizes in the plain fields; it starts past the (lowered) limit, so only its OFFSET is in the ZIP64 field, and nothing else
    offset = {i.filename: i.header_offset for i in archive.infolist()}["small.txt"]
    assert (raw["small.txt"]["packed"], raw["small.txt"]["size"], raw["small.txt"]["offset"]) == (4, 4, 0xFFFFFFFF)
    assert raw["small.txt"]["zip64"] == struct.pack("<Q", offset)


def test_an_archive_that_starts_a_member_beyond_the_32_bit_reach_records_its_offset_in_the_zip64_field(monkeypatch):
    monkeypatch.setattr(audio_module, "ZIP64_LIMIT", 300)
    members = {"a.bin": b"a" * 250, "b.bin": b"b" * 10, "c.bin": b"c" * 10}  # b and c start past 300 bytes into the archive
    blob = b"".join(stream_zip([member(n, c) for n, c in members.items()]))
    archive = read_zip(blob)
    assert archive.testzip() is None and {n: archive.read(n) for n in members} == members
    raw, true_offsets = central_entries(blob), {i.filename: i.header_offset for i in archive.infolist()}
    assert raw["a.bin"]["offset"] == 0 and raw["a.bin"]["zip64"] is None  # starts at the beginning: the plain form
    for name in ("b.bin", "c.bin"):  # started past the limit: the 32-bit field says "look in the ZIP64 field", and the field has the true offset
        assert raw[name]["offset"] == 0xFFFFFFFF and struct.pack("<Q", true_offsets[name]) in raw[name]["zip64"] and true_offsets[name] >= 300


def test_more_than_65535_members_need_the_zip64_end_records():
    count = 65_540
    blob = b"".join(stream_zip([ZipMember(f"{n}", 0, lambda: iter(())) for n in range(count)]))
    assert b"\x50\x4b\x06\x06" in blob
    archive = read_zip(blob)
    assert len(archive.namelist()) == count and archive.namelist()[-1] == str(count - 1)


def test_the_zip64_mark_is_what_the_format_fixes_and_the_limit_is_separate():
    """ZIP64_LIMIT is the knob the tests turn; ZIP64_MARK is the 0xFFFFFFFF the format puts in a 32-bit field to say 'look in the ZIP64 field', and no test may move it."""
    assert audio_module.ZIP64_MARK == 0xFFFFFFFF and audio_module.ZIP64_LIMIT == 0xFFFFFFFF
