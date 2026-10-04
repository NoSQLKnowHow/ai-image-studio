"""16-bit PCM WAV files with an information note, using only the standard library (DESIGN.md §26.5).

The studio writes the music itself rather than leaning on a sound library: the container's PyTorch image has none, the
test environment has no numpy, and a WAV is simple. The file carries a RIFF `LIST/INFO` chunk, which is where a track
keeps the note that it is machine-generated (the model's licence asks for that disclosure when music is shared).
"""

from __future__ import annotations

import array
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

# The INFO tags the studio writes, by the name it uses for them.
INFO_TAGS = {
    "title": b"INAM",
    "comment": b"ICMT",
    "software": b"ISFT",
    "date": b"ICRD",
    "genre": b"IGNR",
    "copyright": b"ICOP",
}
_TAG_NAMES = {tag: name for name, tag in INFO_TAGS.items()}
_MAX_INFO_BYTES = 2000


class WavError(Exception):
    pass


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    frames: int
    bytes: int  # the size of the file
    info: dict[str, str] = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def _info_chunk(info: dict[str, str]) -> bytes:
    parts = []
    for name, tag in INFO_TAGS.items():
        value = info.get(name)
        if not value:
            continue
        data = value.encode("utf-8", "replace")[:_MAX_INFO_BYTES] + b"\0"  # a text chunk ends with a NUL
        parts.append(_chunk(tag, data))
    return _chunk(b"LIST", b"INFO" + b"".join(parts)) if parts else b""


def write_wav(path: Path, pcm: bytes, sample_rate: int, channels: int = 2, info: Optional[dict[str, str]] = None) -> int:
    """Write `pcm` (interleaved little-endian signed 16-bit samples) as a WAV file, atomically (a temporary file, then
    a rename, so nothing ever reads half a track). Returns the size of the file."""
    if channels < 1 or sample_rate < 1:
        raise WavError("a WAV needs at least one channel and a positive sample rate")
    if len(pcm) % (2 * channels):
        raise WavError("the samples do not fill a whole number of frames")
    fmt = struct.pack("<HHIIHH", 1, channels, sample_rate, sample_rate * channels * 2, channels * 2, 16)
    body = b"WAVE" + _chunk(b"fmt ", fmt) + _info_chunk(info or {}) + _chunk(b"data", pcm)
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    try:
        with open(partial, "wb") as handle:
            handle.write(data)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return len(data)


def read_wav(path: Path) -> WavInfo:
    """What a studio-written (or any plain PCM) WAV file says about itself. Raises WavError for anything else."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WavError(f"cannot read {path.name}: {exc.strerror or exc}") from exc
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise WavError(f"{path.name} is not a WAV file")
    pos = 12
    fmt: Optional[tuple[int, int, int, int]] = None
    frames: Optional[int] = None
    info: dict[str, str] = {}
    while pos + 8 <= len(raw):
        tag = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if tag == b"fmt " and len(body) >= 16:
            code, channels, rate, _byte_rate, align, bits = struct.unpack("<HHIIHH", body[:16])
            fmt = (code, channels, rate, bits)
            if code != 1 or bits != 16:
                raise WavError(f"{path.name} is not 16-bit PCM")
            frames_per = align or 1
        elif tag == b"data":
            if fmt is None:
                raise WavError(f"{path.name} has its data before its format")
            frames = len(body) // frames_per
        elif tag == b"LIST" and body[:4] == b"INFO":
            inner = 4
            while inner + 8 <= len(body):
                key = body[inner:inner + 4]
                length = struct.unpack("<I", body[inner + 4:inner + 8])[0]
                text = body[inner + 8:inner + 8 + length].rstrip(b"\0").decode("utf-8", "replace")
                if key in _TAG_NAMES:
                    info[_TAG_NAMES[key]] = text
                inner += 8 + length + (length % 2)
        pos += 8 + size + (size % 2)
    if fmt is None or frames is None:
        raise WavError(f"{path.name} has no audio in it")
    return WavInfo(sample_rate=fmt[2], channels=fmt[1], frames=frames, bytes=len(raw), info=info)


def pcm16_from_samples(channels: Sequence[Sequence[float]]) -> bytes:
    """Interleaved 16-bit samples from one list of floats in [-1, 1] per channel (what the fake pipeline makes).
    Values outside the range are clipped."""
    count = len(channels[0])
    out = array.array("h", bytes(2 * len(channels) * count))
    for c, samples in enumerate(channels):
        if len(samples) != count:
            raise WavError("the channels are not all the same length")
        out[c::len(channels)] = array.array("h", (max(-32768, min(32767, int(round(v * 32767)))) for v in samples))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def pcm16_from_array(audio: Any) -> bytes:
    """Interleaved 16-bit samples from an array of shape (channels, samples) of floats in [-1, 1], as the music
    pipeline returns it (numpy, imported here because only the real worker has it)."""
    import numpy as np

    samples = np.clip(np.asarray(audio, dtype=np.float32).T, -1.0, 1.0)  # (samples, channels): interleaved when flattened
    return np.ascontiguousarray((samples * 32767.0).round().astype("<i2")).tobytes()
