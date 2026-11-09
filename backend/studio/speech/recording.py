"""A person's recording of their voice, checked and made ready to be a reference (DESIGN.md §38.4, §38.11; criterion 266).

The upload is a stranger's file. It is read here and nowhere else, and what comes out is never the upload but the studio's own: mono 16-bit samples with the
silence trimmed from both ends, which the caller writes as a WAV. The original is not kept.

  * A plain 16-bit PCM WAV, the commonest case and the one a phone's voice recorder app makes, is read in this process. No program is needed for it.
  * Anything else (MP3, M4A/AAC, OGG, FLAC, WebM, other kinds of WAV) is given to `ffmpeg` in a subprocess. The kind of file is decided HERE, from its first
    bytes, against a short list; ffmpeg is then told that one demuxer with `-f` and never guesses, so a file that is really something else (a playlist that
    names other files or addresses, which is how ffmpeg has been made to read files it should not) is refused before ffmpeg starts, or read as the one format it
    was announced as. The input is a file in a private temporary folder, because an M4A from a phone often keeps its index at the end and cannot be read from a
    pipe; `-protocol_whitelist file` lets ffmpeg open that file and nothing else. A bare environment, a time limit, and a cap on the audio it may write.

The checks are the ones the page promises, in this order, each with its own words: it can be read at all; it is not silent; it is not shorter than the
minimum or longer than the maximum (measured after the silence at the ends is trimmed); it is not clipped.
"""

from __future__ import annotations

import array
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

DECODE_RATE = 24000  # what ffmpeg is asked to give: the rate the speech engines work at, so a voice is the same whatever it was recorded in
WINDOW_MS = 20  # silence is judged in windows of this length
SILENT_BELOW = 400  # a window whose loudest sample is under this (about -38 dBFS) is silence; 32768 is full scale
KEEP_MS = 120  # this much silence is left at each end after trimming, so a word is not clipped at its edge
CLIPPED_AT = 32700  # a sample this close to full scale counts as clipped
CLIPPED_FRACTION = 0.01  # more than 1% of the samples at full scale is a distorted recording
EXTRA_SECONDS = 60  # ffmpeg may write this much more than the longest recording allowed, to cover silence that will be trimmed
MAX_PLAIN_CHANNELS = 8


class RecordingRefused(Exception):
    """The recording cannot be used; `code` is stable (the page and the tests use it) and `message` says what is wrong in words a person can act on."""

    def __init__(self, code: str, message: str, hint: Optional[str] = None, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.hint, self.status = code, message, hint, status


@dataclass(frozen=True)
class Recording:
    pcm: bytes  # mono, 16-bit little-endian, silence trimmed from both ends
    sample_rate: int
    seconds: float
    peak: float  # the loudest sample as a share of full scale, 0 to 1
    clipped: float  # the share of samples at full scale, 0 to 1
    trimmed_seconds: float  # how much silence was taken from the ends (both together)

    def as_dict(self) -> dict[str, float]:
        return {"seconds": round(self.seconds, 2), "peak": round(self.peak, 3), "clipped": round(self.clipped, 4), "trimmed_seconds": round(self.trimmed_seconds, 2)}


# ---------------------------------------------------------------- reading the file
def _plain_wav(data: bytes) -> Optional[tuple[array.array, int]]:
    """(mono samples, rate) of a plain 16-bit PCM WAV, or None if this is not one (then ffmpeg is asked). A stream whose length field is zero or all ones
    (what a recorder writes before it knows the length) is read to the end of the data."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        tag, length = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        if tag == b"fmt ":
            if length < 16 or body + 16 > len(data):
                return None
            code, channels, rate, _bytes_per_second, _align, bits = struct.unpack("<HHIIHH", data[body:body + 16])
            if code not in (1, 0xFFFE) or bits != 16 or not 1 <= channels <= MAX_PLAIN_CHANNELS or not 1000 <= rate <= 384_000:
                return None  # not 16-bit integer samples: let ffmpeg decide
            fmt = (channels, rate)
        elif tag == b"data":
            if fmt is None:
                return None
            end = len(data) if length in (0, 0xFFFFFFFF) or body + length > len(data) else body + length
            frame = 2 * fmt[0]
            raw = data[body:body + ((end - body) // frame) * frame]
            samples = array.array("h")
            samples.frombytes(raw)
            if sys.byteorder == "big":
                samples.byteswap()
            return _mono(samples, fmt[0]), fmt[1]
        pos = body + length + (length % 2)
    return None


def _mono(samples: array.array, channels: int) -> array.array:
    """The channels averaged into one."""
    if channels == 1:
        return samples
    count = len(samples) // channels
    mixed = array.array("h", bytes(2 * count))
    columns = [samples[c::channels] for c in range(channels)]
    for i in range(count):
        total = 0
        for column in columns:
            total += column[i]
        mixed[i] = total // channels
    return mixed


def sniff(data: bytes) -> Optional[str]:
    """The ffmpeg demuxer for a file that starts like one of the formats the page names, else None. This list is the whole of what ffmpeg is ever asked to read."""
    head = data[:16]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp":
        return "mov"  # MP4, M4A, 3GP: one family, and the one demuxer reads them all
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"  # Matroska and WebM
    if head[:3] == b"ID3":
        return "mp3"  # an MP3 usually begins with its tag
    if len(head) >= 2 and head[0] == 0xFF:
        second = head[1]
        if second & 0xF6 == 0xF0:
            return "aac"  # an ADTS frame: twelve bits of sync, and layer 00
        third = head[2] if len(head) >= 3 else 0
        if second & 0xE0 == 0xE0 and second & 0x06 != 0x00 and second & 0x18 != 0x08 and third >> 4 != 0xF and (third >> 2) & 3 != 3:
            return "mp3"  # an MPEG audio frame: eleven bits of sync, a layer and a version that exist, a bitrate and a sample rate that exist
    return None


def _with_ffmpeg(data: bytes, ffmpeg: str, timeout: float, max_seconds: float) -> tuple[array.array, int]:
    """Decode with ffmpeg: mono, 16-bit, `DECODE_RATE`, at most `max_seconds + EXTRA_SECONDS` of it. Raises RecordingRefused."""
    demuxer = sniff(data)
    if demuxer is None:
        raise RecordingRefused("unreadable", "That file could not be read as audio.", "Use a recording made on a phone or computer: WAV, MP3, M4A, OGG, FLAC or WebM.", 415)
    try:
        with tempfile.TemporaryDirectory(prefix="recording-") as folder:
            source = Path(folder) / "input"
            source.write_bytes(data)
            command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file", "-f", demuxer, "-i", str(source), "-vn", "-sn", "-dn",
                       "-map", "0:a:0", "-t", str(int(max_seconds) + EXTRA_SECONDS), "-ac", "1", "-ar", str(DECODE_RATE), "-f", "s16le", "pipe:1"]
            done = subprocess.run(command, capture_output=True, timeout=timeout, env={"PATH": "/usr/local/bin:/usr/bin:/bin"}, check=False, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise RecordingRefused("no_ffmpeg", "This recording is not a plain WAV file, and the program that reads other kinds (ffmpeg) is not installed here.",
                               "Save the recording as a WAV file and upload that, or ask whoever runs this studio to install ffmpeg.", 415) from None
    except subprocess.TimeoutExpired:
        raise RecordingRefused("decode_timeout", f"Reading that recording took longer than {int(timeout)} second{'' if int(timeout) == 1 else 's'}, so it was stopped.",
                               "Try a shorter recording, or save it as a WAV file.") from None
    except OSError as exc:
        raise RecordingRefused("decode_failed", f"Could not start the program that reads recordings: {exc.strerror or exc}.", status=500) from None
    if done.returncode != 0 or len(done.stdout) < 2:
        raise RecordingRefused("unreadable", "That file could not be read as audio.", "Use a recording made on a phone or computer: WAV, MP3, M4A, OGG, FLAC or WebM.", 415)
    samples = array.array("h")
    samples.frombytes(done.stdout[:len(done.stdout) - (len(done.stdout) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples, DECODE_RATE


# ---------------------------------------------------------------- the checks
def _trim(samples: array.array, rate: int) -> tuple[array.array, bool]:
    """The samples without the silence at both ends (KEEP_MS of it left), and whether any window was loud enough to count as sound."""
    window = max(1, rate * WINDOW_MS // 1000)
    count = len(samples)
    first = last = None
    for start in range(0, count, window):
        chunk = samples[start:start + window]
        if max(max(chunk), -min(chunk)) >= SILENT_BELOW:
            if first is None:
                first = start
            last = min(count, start + window)
    if first is None or last is None:
        return array.array("h"), False
    keep = rate * KEEP_MS // 1000
    return samples[max(0, first - keep):min(count, last + keep)], True


def inspect(data: bytes, *, min_seconds: float, max_seconds: float, ffmpeg: str = "ffmpeg", timeout: float = 30.0) -> Recording:
    """Read an uploaded recording and check it. Raises RecordingRefused, naming which check failed."""
    if not data:
        raise RecordingRefused("empty", "That file is empty.")
    plain = _plain_wav(data)
    samples, rate = plain if plain is not None else _with_ffmpeg(data, ffmpeg, timeout, max_seconds)
    raw_seconds = len(samples) / rate
    overall = max_seconds + EXTRA_SECONDS  # silence at the ends is trimmed before the length is judged, but not without limit: ffmpeg is told to stop here
    if raw_seconds >= overall - 0.5:
        raise RecordingRefused("too_long", f"That recording is more than {overall:.0f} seconds long altogether, silence included, and the longest allowed is "
                                           f"{max_seconds:.0f} seconds of speech.", "Record just the passage, and stop when you have read it.")
    trimmed, any_sound = _trim(samples, rate)
    if not any_sound:
        raise RecordingRefused("silent", "That recording is silent: no sound could be heard in it.",
                               "Check the microphone, record again, and speak at the normal volume of a conversation.")
    seconds = len(trimmed) / rate
    if seconds > max_seconds:
        raise RecordingRefused("too_long", f"That recording is {seconds:.0f} seconds of speech and the longest allowed is {max_seconds:.0f}.",
                               "Record just the passage, and stop when you have read it.")
    if seconds < min_seconds:
        raise RecordingRefused("too_short", f"That recording is {seconds:.1f} seconds of speech and the shortest allowed is {min_seconds:.0f}.",
                               "Read the whole passage, at an unhurried pace.")
    peak = max(max(trimmed), -min(trimmed)) / 32768
    loud = sum(1 for s in trimmed if s >= CLIPPED_AT or s <= -CLIPPED_AT)
    clipped = loud / len(trimmed)
    if clipped > CLIPPED_FRACTION:
        raise RecordingRefused("clipped", f"That recording is distorted: {clipped:.0%} of it is at the loudest level the file can hold.",
                               "Record again a little further from the microphone, or with the volume lower.")
    if sys.byteorder == "big":
        trimmed.byteswap()
    return Recording(pcm=trimmed.tobytes(), sample_rate=rate, seconds=seconds, peak=peak, clipped=clipped, trimmed_seconds=raw_seconds - seconds)
