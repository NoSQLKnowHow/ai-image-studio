"""The audio of a reading, as it is played and downloaded (DESIGN.md §38.7, criteria 282 and 286).

A part is stored as its pieces, one WAV file each, with the pause that follows each piece already in its audio. The WAV of the whole part is therefore
never stored: it is built when it is asked for, as the header of a WAV followed by the samples of each piece in turn. That has three consequences, all
wanted: nothing is on the disk twice (a book is about 2 GB, not 4); the part's length is exactly the sum of its pieces; and a player can seek anywhere in
it, because any byte range of the virtual file can be produced without reading the rest (`PartAudio.iter_bytes`).

The download of a whole reading is a ZIP of the parts, written as it streams (`stream_zip`): the parts are stored uncompressed (audio does not shrink
and a book is large), the size of each is known before it starts, and the format is ZIP64 where a size or offset needs it, so a book over 4 GB works.
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from .. import wavfile
from ..wavfile import WavError

CHUNK = 64 * 1024
ZIP64_MARK = 0xFFFFFFFF  # what a 32-bit field holds to say "the real value is in the ZIP64 field": fixed by the format
ZIP64_LIMIT = ZIP64_MARK  # a size or an offset at or above this needs ZIP64 (separate from the mark so that a test can make it small)


# ---------------------------------------------------------------- one piece's file
@dataclass(frozen=True)
class PieceFile:
    """Where the samples are inside a piece's WAV file, and what they are."""

    path: Path
    offset: int  # of the first sample, from the start of the file
    length: int  # of the samples, in bytes
    sample_rate: int
    channels: int


def piece_file(path: Path) -> PieceFile:
    """Read the header of a WAV file this studio wrote and find its samples. Raises WavError for anything that is not 16-bit PCM."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            head = handle.read(8192)
    except OSError as exc:
        raise WavError(f"cannot read {Path(path).name}: {exc.strerror or exc}") from exc
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise WavError(f"{Path(path).name} is not a WAV file")
    pos, fmt = 12, None
    while pos + 8 <= len(head):
        tag, length = head[pos:pos + 4], struct.unpack("<I", head[pos + 4:pos + 8])[0]
        if tag == b"fmt " and len(head) >= pos + 24:
            code, channels, rate, _byte_rate, _align, bits = struct.unpack("<HHIIHH", head[pos + 8:pos + 24])
            if code != 1 or bits != 16:
                raise WavError(f"{Path(path).name} is not 16-bit PCM")
            fmt = (channels, rate)
        elif tag == b"data":
            if fmt is None:
                raise WavError(f"{Path(path).name} has its data before its format")
            start = pos + 8
            return PieceFile(Path(path), start, min(length, max(0, size - start)), fmt[1], fmt[0])
        pos += 8 + length + (length % 2)
    raise WavError(f"{Path(path).name} has no audio in it")


# ---------------------------------------------------------------- a part, built from its pieces
class PartAudio:
    """The WAV of one part, as a file that does not exist: its header and its samples, produced in any byte range on request."""

    def __init__(self, pieces: Sequence[PieceFile], info: Optional[dict[str, str]] = None):
        if not pieces:
            raise WavError("a part with no audio made yet has no WAV")
        rates = {(p.sample_rate, p.channels) for p in pieces}
        if len(rates) != 1:
            raise WavError("the pieces of a part are not all the same kind of audio")
        (self.sample_rate, self.channels), = rates
        self.pieces = list(pieces)
        self.data_bytes = sum(p.length for p in pieces)
        self.header = wavfile.wav_header(self.sample_rate, self.channels, self.data_bytes, info)
        self.size = len(self.header) + self.data_bytes

    @property
    def seconds(self) -> float:
        return self.data_bytes / (2 * self.channels * self.sample_rate)

    def iter_bytes(self, start: int = 0, end: Optional[int] = None) -> Iterator[bytes]:
        """The bytes from `start` to `end` inclusive (to the end if None) of the virtual file. Only the pieces that overlap the range are opened."""
        last = self.size - 1 if end is None else min(end, self.size - 1)
        if start > last:
            return
        position = 0  # where the current section begins in the virtual file
        sections: list[tuple[int, int, Optional[PieceFile]]] = [(0, len(self.header), None)]
        position = len(self.header)
        for piece in self.pieces:
            sections.append((position, position + piece.length, piece))
            position += piece.length
        for begin, finish, piece in sections:  # [begin, finish)
            if finish <= start or begin > last:
                continue
            lo, hi = max(start, begin) - begin, min(last + 1, finish) - begin  # the part of this section wanted, relative to the section
            if piece is None:
                yield self.header[lo:hi]
                continue
            with open(piece.path, "rb") as handle:
                handle.seek(piece.offset + lo)
                remaining = hi - lo
                while remaining > 0:
                    chunk = handle.read(min(CHUNK, remaining))
                    if not chunk:
                        raise WavError(f"{piece.path.name} is shorter than its header says")
                    remaining -= len(chunk)
                    yield chunk


# ---------------------------------------------------------------- byte ranges
class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: Optional[str], size: int) -> Optional[tuple[int, int]]:
    """The (first, last) byte, inclusive, a Range header asks for, or None to send the whole thing (no header, a form not understood, or several ranges).
    Raises RangeNotSatisfiable for a range that starts past the end."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].strip()
    if "," in spec or "-" not in spec:
        return None
    first, _, last = spec.partition("-")
    try:
        if first == "":  # the last N bytes
            count = int(last)
            if count <= 0:
                raise RangeNotSatisfiable()
            return max(0, size - count), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start >= size or start > end:
        raise RangeNotSatisfiable()
    return start, min(end, size - 1)


# ---------------------------------------------------------------- a ZIP written as it streams
@dataclass(frozen=True)
class ZipMember:
    name: str
    size: int  # known before the first byte is produced
    chunks: Callable[[], Iterator[bytes]]


def _dos_time(stamp: float) -> tuple[int, int]:
    t = time.localtime(max(stamp, 315532800))  # not before 1980, which is where the format starts
    return (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2), ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday


def stream_zip(members: Sequence[ZipMember], stamp: Optional[float] = None) -> Iterator[bytes]:
    """A ZIP file of `members`, stored (not compressed), as a stream of bytes. Each member's CRC is worked out while it streams and written after it (a
    "data descriptor"), which is what lets the archive be made without holding or reading anything twice. ZIP64 is used for a member of 4 GiB or more, a
    member that starts that far into the archive, or more than 65,535 members; below those the archive is the plain, older kind."""
    dos_time, dos_date = _dos_time(time.time() if stamp is None else stamp)
    offset = 0
    central: list[bytes] = []
    for member in members:
        name = member.name.encode("utf-8")
        big = member.size >= ZIP64_LIMIT
        extra = struct.pack("<HHQQ", 0x0001, 16, 0, 0) if big else b""  # the local ZIP64 field: sizes are in the descriptor, so these are zero
        local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 45 if big else 20, 0x0808, 0, dos_time, dos_date, 0, ZIP64_MARK if big else 0,
                            ZIP64_MARK if big else 0, len(name), len(extra)) + name + extra
        header_at = offset
        yield local
        offset += len(local)
        crc, written = 0, 0
        for chunk in member.chunks():
            crc = zlib.crc32(chunk, crc)
            written += len(chunk)
            yield chunk
        if written != member.size:
            raise WavError(f"{member.name} was {written} bytes long, not the {member.size} that were promised")
        descriptor = struct.pack("<IIQQ" if big else "<IIII", 0x08074B50, crc, written, written)
        yield descriptor
        offset += written + len(descriptor)
        # the entry for the central directory at the end; any field that does not fit in 32 bits is moved to the ZIP64 field, in the order the format wants
        needs_offset = header_at >= ZIP64_LIMIT
        fields = b"".join([struct.pack("<QQ", written, written) if big else b"", struct.pack("<Q", header_at) if needs_offset else b""])
        central_extra = struct.pack("<HH", 0x0001, len(fields)) + fields if fields else b""
        central.append(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 45, 45 if (big or needs_offset) else 20, 0x0808, 0, dos_time, dos_date, crc,
                                   ZIP64_MARK if big else written, ZIP64_MARK if big else written, len(name), len(central_extra), 0, 0, 0, 0,
                                   ZIP64_MARK if needs_offset else header_at) + name + central_extra)
    directory = b"".join(central)
    directory_at = offset
    yield directory
    offset += len(directory)
    if len(central) >= 0xFFFF or directory_at >= ZIP64_LIMIT or len(directory) >= ZIP64_LIMIT:
        record_at = offset
        yield struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, len(central), len(central), len(directory), directory_at)
        yield struct.pack("<IIQI", 0x07064B50, 0, record_at, 1)
        yield struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, min(len(central), 0xFFFF), min(len(central), 0xFFFF), ZIP64_MARK if len(directory) >= ZIP64_LIMIT else len(directory),
                          ZIP64_MARK if directory_at >= ZIP64_LIMIT else directory_at, 0)
    else:
        yield struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central), len(directory), directory_at, 0)
