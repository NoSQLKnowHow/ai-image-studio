"""A recording of a person's voice, read and checked (DESIGN.md §38.4, §38.11; criterion 266).

The upload is a stranger's file, so these tests are as much about what must be refused, and in whose words, as about what is accepted: a recording that is
silent, too short, too long, clipped or not audio is refused with its own code and message; the silence at the ends is trimmed; and the program that reads
the formats other than a plain WAV (ffmpeg) is started with no way to reach a file or the network and a bare environment.

Criterion 291: no test here needs ffmpeg unless it is about ffmpeg. A plain 16-bit WAV is read in this process, and the tests that make an MP3 or an M4A skip
where this machine's ffmpeg cannot make one.
"""

from __future__ import annotations

import array
import math
import shutil
import stat
import struct
import subprocess
from pathlib import Path

import pytest

from studio import wavfile
from studio.speech import recording
from studio.speech.recording import EXTRA_SECONDS, KEEP_MS, Recording, RecordingRefused

MIN, MAX = 8.0, 60.0
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def tone(seconds: float, rate: int = 24000, amplitude: int = 8000, hz: float = 220.0) -> array.array:
    """A sine tone of the given loudness: stands in for speech, which is all the checks look at (how loud, for how long)."""
    count = int(seconds * rate)
    return array.array("h", (int(amplitude * math.sin(2 * math.pi * hz * n / rate)) for n in range(count)))


def silence(seconds: float, rate: int = 24000) -> array.array:
    return array.array("h", bytes(2 * int(seconds * rate)))


def wav(*parts: array.array, rate: int = 24000, channels: int = 1) -> bytes:
    pcm = b"".join(p.tobytes() for p in parts)
    return wavfile.wav_header(rate, channels, len(pcm)) + pcm


def check(data: bytes, **kw) -> Recording:
    return recording.inspect(data, min_seconds=kw.pop("min_seconds", MIN), max_seconds=kw.pop("max_seconds", MAX), **kw)


def refused(data: bytes, **kw) -> RecordingRefused:
    with pytest.raises(RecordingRefused) as caught:
        check(data, **kw)
    return caught.value


# ---------------------------------------------------------------- what is accepted
def test_a_plain_wav_of_speech_is_accepted_without_any_other_program():
    result = check(wav(tone(10)), ffmpeg="/does/not/exist")  # (no ffmpeg to be had: a plain WAV does not need one)
    assert result.sample_rate == 24000 and result.seconds == pytest.approx(10, abs=0.02) and len(result.pcm) == 2 * 240000
    assert 0.2 < result.peak < 0.3 and result.clipped == 0 and result.trimmed_seconds == pytest.approx(0, abs=0.02)
    assert result.as_dict() == {"seconds": round(result.seconds, 2), "peak": round(result.peak, 3), "clipped": 0.0, "trimmed_seconds": round(result.trimmed_seconds, 2)}


def test_the_recordings_own_rate_is_kept_not_assumed():
    assert check(wav(tone(10, 44100), rate=44100)).sample_rate == 44100
    assert check(wav(tone(10, 16000), rate=16000)).sample_rate == 16000


def test_two_channels_are_mixed_into_one():
    """A stereo file with sound on the left only comes out mono at half the level: the channels are averaged, not one of them taken."""
    left = tone(10)
    interleaved = array.array("h")
    for sample in left:
        interleaved.extend((sample, 0))
    result = check(wav(interleaved, channels=2))
    assert result.seconds == pytest.approx(10, abs=0.02) and result.peak == pytest.approx(0.12, abs=0.02)  # (8000 / 2) / 32768


def test_silence_at_both_ends_is_trimmed_leaving_a_little_so_a_word_is_not_cut():
    result = check(wav(silence(2), tone(10), silence(3)))
    assert result.seconds == pytest.approx(10 + 2 * KEEP_MS / 1000, abs=0.05)
    assert result.trimmed_seconds == pytest.approx(5 - 2 * KEEP_MS / 1000, abs=0.05)
    samples = array.array("h")
    samples.frombytes(result.pcm)
    keep = 24000 * KEEP_MS // 1000
    assert max(abs(s) for s in samples[:keep // 2]) < 400 and max(abs(s) for s in samples[keep + 480:keep + 4800]) > 4000  # silence, then the sound


def test_a_pause_inside_the_speech_is_not_trimmed():
    result = check(wav(tone(5), silence(3), tone(5)))
    assert result.seconds == pytest.approx(13, abs=0.05)


def test_a_wav_whose_length_was_not_known_when_it_was_written_is_read_to_its_end():
    """A recorder that streams writes 0 or 0xFFFFFFFF where the data length goes. Both mean "to the end of the file"."""
    body = tone(10).tobytes()
    for placeholder in (0, 0xFFFFFFFF):
        head = bytearray(wavfile.wav_header(24000, 1, len(body)))
        head[-4:] = struct.pack("<I", placeholder)
        assert check(bytes(head) + body, ffmpeg="/does/not/exist").seconds == pytest.approx(10, abs=0.02), placeholder


def test_a_stray_byte_at_the_end_is_ignored():
    assert check(wav(tone(10)) + b"\x01").seconds == pytest.approx(10, abs=0.02)


# ---------------------------------------------------------------- what is refused, and why
def test_a_silent_recording_is_refused_as_silent_whatever_its_length():
    for length in (1, 10, 90):
        error = refused(wav(silence(length)))
        assert error.code == "silent" and "silent" in error.message and error.hint and error.status == 422, length


def test_a_recording_too_quiet_to_be_speech_is_silent_too():
    assert refused(wav(tone(10, amplitude=100))).code == "silent"


def test_a_recording_that_is_too_short_says_how_short_and_what_is_wanted():
    error = refused(wav(tone(5)))
    assert error.code == "too_short" and "5.0 seconds" in error.message and "8" in error.message and error.hint


def test_the_shortest_recording_allowed_is_accepted_and_one_a_little_shorter_is_not():
    assert check(wav(tone(8.0))).seconds == pytest.approx(8, abs=0.02)
    assert refused(wav(tone(7.8))).code == "too_short"


def test_the_length_is_judged_after_the_silence_is_trimmed():
    """Six seconds of speech with a long silence on each side is a recording of six seconds, not of twenty."""
    assert refused(wav(silence(7), tone(6), silence(7))).code == "too_short"


def test_a_recording_that_is_too_long_says_so():
    error = refused(wav(tone(65)))
    assert error.code == "too_long" and "65 seconds" in error.message and "60" in error.message and error.hint


def test_the_longest_recording_allowed_is_accepted_and_one_a_little_longer_is_not():
    assert check(wav(tone(60))).seconds == pytest.approx(60, abs=0.02)
    assert refused(wav(tone(60.5))).code == "too_long"


def test_a_file_far_longer_than_that_is_refused_for_its_whole_length_not_its_speech():
    """A long file mostly silence: refused on its length altogether, in words that do not claim it holds too much speech."""
    error = refused(wav(silence(100), tone(10), silence(100)))
    assert error.code == "too_long" and "altogether" in error.message and "silence included" in error.message


def test_a_distorted_recording_is_refused_as_clipped_and_says_how_much():
    loud = array.array("h", [32767 if (n // 40) % 2 == 0 else -32768 for n in range(24000 * 10)])
    error = refused(wav(loud))
    assert error.code == "clipped" and "distorted" in error.message and "%" in error.message and error.hint


def test_a_few_samples_at_full_scale_are_not_clipping():
    samples = tone(10)
    for n in range(0, 2400, 10):  # 240 samples of 240,000: 0.1%
        samples[n + 50000] = 32767
    result = check(wav(samples))
    assert 0 < result.clipped < 0.01 and result.peak > 0.99


def test_nothing_at_all_is_refused():
    assert refused(b"").code == "empty"


# ---------------------------------------------------------------- a file that is not a plain wav goes to ffmpeg
OGG = b"OggS" + bytes(200)  # what the stand-in programs below are given: it looks like one of the formats ffmpeg is allowed to read, and is not one


def test_without_ffmpeg_a_file_that_is_not_a_plain_wav_is_refused_and_the_remedy_is_said(tmp_path):
    for data in (b"ID3\x03\x00\x00\x00\x00\x00\x00" + bytes(2000), OGG, wav(tone(10))[:30]):
        error = refused(data, ffmpeg=str(tmp_path / "no-such-ffmpeg"))
        assert error.code == "no_ffmpeg" and error.status == 415 and "WAV" in error.hint, data[:10]


@pytest.mark.parametrize("head,demuxer", [
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", "wav"), (b"OggS\x00\x02", "ogg"), (b"fLaC\x00\x00", "flac"), (b"\x00\x00\x00\x20ftypM4A \x00", "mov"),
    (b"\x00\x00\x00\x18ftypmp42", "mov"), (b"\x1a\x45\xdf\xa3\x9f\x42", "matroska"), (b"ID3\x04\x00", "mp3"), (b"\xff\xfb\x90\x64", "mp3"),
    (b"\xff\xf3\x90\x64", "mp3"), (b"\xff\xf1\x50\x80", "aac"), (b"\xff\xf9\x50\x80", "aac"),
])
def test_the_container_is_recognised_from_its_first_bytes(head, demuxer):
    assert recording.sniff(head + bytes(32)) == demuxer


@pytest.mark.parametrize("head", [
    b"", b"abc", b"#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n", b"ffconcat version 1.0\nfile /etc/passwd\n", b"<?xml version", b"\x89PNG\r\n\x1a\n", b"MZ\x90\x00",
    b"\xff\xe1\x00", b"\xff\xe8\x90", b"\xff\xfb\xf0", b"\xff\xfb\x9c", b"#!/bin/sh\n", b"http://example.org/a.mp3",  # (the last two of the frame heads: a bitrate and a sample rate that do not exist)
])
def test_anything_else_is_not_a_format_ffmpeg_is_asked_to_read(head):
    """A playlist, a concat script, an address, a program: the things ffmpeg could be made to follow to other files or the network. None is a recording."""
    assert recording.sniff(head + bytes(32)) is None


def test_a_file_that_is_not_one_of_the_formats_never_reaches_ffmpeg(tmp_path):
    record = tmp_path / "ran.txt"
    program = stand_in(tmp_path, f"echo ran > {record}\nexit 0")
    for data in (b"#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n" + bytes(100), b"plain text, not audio" * 10):
        error = refused(data, ffmpeg=program)
        assert error.code == "unreadable" and error.status == 415
    assert not record.exists()  # ffmpeg was not even started


@pytest.mark.parametrize("variant", ["float", "8bit"])
def test_a_wav_of_another_kind_than_16_bit_integers_is_given_to_ffmpeg(tmp_path, variant):
    code, bits = (3, 32) if variant == "float" else (1, 8)
    head = b"RIFF" + struct.pack("<I", 36 + 100) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, code, 1, 24000, 24000 * bits // 8, bits // 8, bits)
    data = head + b"data" + struct.pack("<I", 100) + bytes(100)
    assert refused(data, ffmpeg=str(tmp_path / "no-such-ffmpeg")).code == "no_ffmpeg"  # it did not take the in-process road


def stand_in(tmp_path: Path, body: str) -> str:
    """A program that takes ffmpeg's place and does what `body` (shell) says: so a test can see how it was called and make it misbehave."""
    path = tmp_path / "fake-ffmpeg"
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_ffmpeg_is_told_to_open_nothing_but_its_input_and_to_stop_after_a_while(tmp_path):
    record = tmp_path / "args.txt"
    program = stand_in(tmp_path, f'echo "$@" > {record}\nexit 1')
    refused(OGG, ffmpeg=program)
    args = record.read_text().split()
    assert args[args.index("-protocol_whitelist") + 1] == "file" and "-nostdin" in args  # it may open a file, and no address, pipe or device
    assert args[args.index("-f") + 1] == "ogg" and args.index("-f") < args.index("-i")  # and it was told the format before the input: it does not guess
    assert args[args.index("-t") + 1] == str(int(MAX) + EXTRA_SECONDS) and args[args.index("-ac") + 1] == "1" and args[args.index("-ar") + 1] == "24000"
    assert args[args.index("-map") + 1] == "0:a:0" and "-vn" in args and "-sn" in args and "-dn" in args and args[-1] == "pipe:1"


def test_ffmpeg_gets_a_bare_environment_not_the_studios(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "a-secret-token")
    monkeypatch.setenv("STUDIO_SOMETHING", "private")
    record = tmp_path / "env.txt"
    refused(OGG, ffmpeg=stand_in(tmp_path, f"env > {record}\nexit 1"))
    seen = record.read_text()
    assert "a-secret-token" not in seen and "STUDIO_" not in seen and "HOME=" not in seen and "PATH=" in seen


def test_a_conversion_that_takes_too_long_is_stopped_and_says_so(tmp_path):
    error = refused(OGG, ffmpeg=stand_in(tmp_path, "sleep 10"), timeout=1)
    assert error.code == "decode_timeout" and error.message == "Reading that recording took longer than 1 second, so it was stopped." and error.hint


def test_a_conversion_that_fails_or_gives_nothing_is_an_unreadable_file(tmp_path):
    for body in ("exit 1", "exit 0", "printf x"):  # fails; succeeds with no audio; gives less than one sample
        error = refused(OGG, ffmpeg=stand_in(tmp_path, body))
        assert error.code == "unreadable" and error.status == 415 and "could not be read as audio" in error.message, body


def test_what_ffmpeg_gives_is_checked_like_any_recording(tmp_path):
    """The program's output is not trusted either: silence from it is silence, too much of it is too long."""
    quiet = tmp_path / "quiet.raw"
    quiet.write_bytes(silence(10).tobytes())
    assert refused(OGG, ffmpeg=stand_in(tmp_path, f"cat {quiet}")).code == "silent"
    loud = tmp_path / "loud.raw"
    loud.write_bytes(tone(12).tobytes())
    result = check(OGG, ffmpeg=stand_in(tmp_path, f"cat {loud}"))
    assert result.sample_rate == 24000 and result.seconds == pytest.approx(12, abs=0.02)  # (ffmpeg is asked for 24 kHz mono, so that is the rate)


# ---------------------------------------------------------------- with the real ffmpeg (skipped where there is none)
def encode(tmp_path: Path, suffix: str, seconds: float = 10) -> bytes:
    """A recording in another format, made by ffmpeg from a WAV. Skips where this ffmpeg cannot make the format."""
    source = tmp_path / "source.wav"
    source.write_bytes(wav(tone(seconds)))
    out = tmp_path / f"out{suffix}"
    done = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(source), str(out)], capture_output=True)
    if done.returncode != 0 or not out.is_file():
        pytest.skip(f"this ffmpeg cannot make {suffix} files")
    return out.read_bytes()


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
@pytest.mark.parametrize("suffix", [".flac", ".ogg", ".mp3", ".m4a", ".webm", ".opus"])
def test_the_formats_the_page_names_are_read(tmp_path, suffix):
    result = check(encode(tmp_path, suffix))
    assert result.sample_rate == 24000 and result.seconds == pytest.approx(10, abs=0.5) and result.peak > 0.1


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_a_stereo_wav_of_24_bit_samples_is_read_through_ffmpeg(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(wav(tone(10)))
    out = tmp_path / "wide.wav"
    done = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(source), "-ac", "2", "-c:a", "pcm_s24le", str(out)], capture_output=True)
    assert done.returncode == 0
    assert check(out.read_bytes()).seconds == pytest.approx(10, abs=0.1)


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_a_file_that_says_it_is_audio_and_is_not_is_unreadable_to_the_real_ffmpeg_too():
    assert refused(b"OggS" + b"this is not an ogg stream, whatever its first bytes say" * 100).code == "unreadable"
    assert refused(b"\x00\x00\x00\x20ftypM4A " + bytes(500)).code == "unreadable"


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_an_m4a_whose_index_is_at_the_end_is_read_which_a_pipe_could_not(tmp_path):
    """A phone's voice recorder writes the index of an M4A last. ffmpeg cannot read such a file from a pipe, which is why the upload is given to it as a file."""
    data = encode(tmp_path, ".m4a")
    assert data.index(b"moov") > data.index(b"mdat")  # this ffmpeg wrote it the way a phone does: the data first, the index after
    assert check(data).seconds == pytest.approx(10, abs=0.5)


def test_the_temporary_file_holds_the_upload_and_is_gone_afterwards(tmp_path):
    record, where = tmp_path / "copy.bin", tmp_path / "where.txt"
    script = f'while [ $# -gt 0 ]; do if [ "$1" = "-i" ]; then cp "$2" {record}; echo "$2" > {where}; fi; shift; done\nexit 1'
    refused(OGG, ffmpeg=stand_in(tmp_path, script))
    assert record.read_bytes() == OGG and not Path(where.read_text().strip()).exists()  # (the folder it was in is removed even though the program failed)


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_a_truncated_mp3_gives_what_it_has_and_is_judged_on_that(tmp_path):
    data = encode(tmp_path, ".mp3", 12)
    result = check(data[: len(data) // 2 + 1], min_seconds=1)
    assert 2 < result.seconds < 10
