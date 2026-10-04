"""The studio's own WAV writer and reader (DESIGN.md §26.5): standard library only."""

from __future__ import annotations

import struct
import wave

import pytest

from studio import wavfile
from studio.wavfile import WavError, pcm16_from_samples, read_wav, write_wav


def tone(frames: int = 100) -> bytes:
    return pcm16_from_samples([[0.5] * frames, [-0.25] * frames])


def test_a_written_file_reads_back_with_everything_it_says(tmp_path):
    size = write_wav(tmp_path / "a.wav", tone(441), 44100, 2, {"comment": "machine-generated", "software": "studio"})
    info = read_wav(tmp_path / "a.wav")
    assert (info.sample_rate, info.channels, info.frames, info.bytes) == (44100, 2, 441, size)
    assert info.seconds == pytest.approx(0.01)
    assert info.info == {"comment": "machine-generated", "software": "studio"}


def test_other_tools_can_read_it_too(tmp_path):
    """Python's own `wave` module, which has nothing to do with this code, agrees with what was written."""
    write_wav(tmp_path / "a.wav", tone(1000), 22050, 2, {"title": "t"})
    with wave.open(str(tmp_path / "a.wav"), "rb") as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes()) == (2, 2, 22050, 1000)
        first = struct.unpack("<hh", reader.readframes(1))
    assert first == (16384, -8192)  # 0.5 and -0.25 of full scale: left then right, interleaved


def test_an_odd_length_text_is_padded_so_the_chunks_after_it_still_line_up(tmp_path):
    write_wav(tmp_path / "a.wav", tone(10), 8000, 2, {"comment": "abc", "title": "de"})  # 4 and 3 bytes with their NULs
    info = read_wav(tmp_path / "a.wav")
    assert info.info == {"comment": "abc", "title": "de"} and info.frames == 10


def test_text_that_is_not_ascii_survives(tmp_path):
    write_wav(tmp_path / "a.wav", tone(10), 8000, 2, {"title": "Étude für Elise ♪"})
    assert read_wav(tmp_path / "a.wav").info["title"] == "Étude für Elise ♪"


def test_a_very_long_note_is_cut_not_refused(tmp_path):
    write_wav(tmp_path / "a.wav", tone(10), 8000, 2, {"comment": "x" * 5000})
    assert 1000 < len(read_wav(tmp_path / "a.wav").info["comment"]) <= wavfile._MAX_INFO_BYTES


def test_nothing_is_left_half_written(tmp_path):
    write_wav(tmp_path / "deep" / "er" / "a.wav", tone(10), 8000)  # the folders are made
    assert [p.name for p in (tmp_path / "deep" / "er").iterdir()] == ["a.wav"]


def test_a_failed_write_leaves_neither_a_file_nor_a_part_file(tmp_path, monkeypatch):
    import os

    def boom(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_wav(tmp_path / "a.wav", tone(10), 8000)
    assert list(tmp_path.iterdir()) == []


def test_a_mono_file_works(tmp_path):
    write_wav(tmp_path / "a.wav", pcm16_from_samples([[0.1] * 50]), 8000, 1)
    info = read_wav(tmp_path / "a.wav")
    assert (info.channels, info.frames) == (1, 50)


def test_samples_that_do_not_fill_whole_frames_are_refused(tmp_path):
    with pytest.raises(WavError, match="whole number of frames"):
        write_wav(tmp_path / "a.wav", b"\0\0\0", 8000, 2)
    with pytest.raises(WavError):
        write_wav(tmp_path / "a.wav", b"\0\0", 0, 1)
    assert not list(tmp_path.iterdir())


def test_float_samples_are_clipped_rounded_and_interleaved():
    pcm = pcm16_from_samples([[1.0, -1.0, 2.0, 0.0], [0.0, 0.5, -3.0, 1e-9]])
    assert struct.unpack("<8h", pcm) == (32767, 0, -32767, 16384, 32767, -32768, 0, 0)  # clipped at the ends
    with pytest.raises(WavError):
        pcm16_from_samples([[0.0, 0.0], [0.0]])


@pytest.mark.parametrize("data,message", [
    (b"", "not a WAV"),
    (b"RIFF\0\0\0\0AVI LIST", "not a WAV"),
    (b"not riff at all, just text", "not a WAV"),
])
def test_files_that_are_not_wav_are_refused_with_a_reason(tmp_path, data, message):
    (tmp_path / "a.wav").write_bytes(data)
    with pytest.raises(WavError, match=message):
        read_wav(tmp_path / "a.wav")


def test_only_16_bit_pcm_is_accepted(tmp_path):
    fmt = struct.pack("<HHIIHH", 3, 2, 8000, 64000, 8, 32)  # 32-bit float
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 0)
    (tmp_path / "a.wav").write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(WavError, match="16-bit PCM"):
        read_wav(tmp_path / "a.wav")


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(WavError, match="cannot read"):
        read_wav(tmp_path / "nope.wav")


def test_a_file_with_no_audio_chunk_is_refused(tmp_path):
    fmt = struct.pack("<HHIIHH", 1, 2, 8000, 32000, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
    (tmp_path / "a.wav").write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(WavError, match="no audio"):
        read_wav(tmp_path / "a.wav")


def test_the_array_helper_matches_the_list_helper():
    np = pytest.importorskip("numpy")  # only the real worker has numpy
    left, right = [0.5, -0.5, 0.25], [0.0, 1.0, -1.0]
    assert wavfile.pcm16_from_array(np.array([left, right], dtype="float32")) == pcm16_from_samples([left, right])
    assert wavfile.pcm16_from_array(np.array([[3.0, -3.0], [0.0, 0.0]])) == pcm16_from_samples([[1.0, -1.0], [0.0, 0.0]])
