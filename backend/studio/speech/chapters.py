"""The chapters of a reading, with exact times (DESIGN.md §44.4).

A chapter is a PART: the parts of the review, with the titles the person gave them, in order. No other notion of a chapter exists. This module turns the lengths of the
finished parts into chapter times and writes the two chapter lists that go in the ZIP (`chapters.txt` and `chapters.ffmetadata`); the M4B (`audiobook.py`) is built from
the same list, so the three agree to the millisecond.

The times are exact in the sense that matters: a part's length is a whole number of samples (the size of its pieces' files, which the server already knows to write the
part's WAV header), divided by its sample rate, and kept as an exact fraction (`fractions.Fraction`) through the addition of every part before it. It is rounded ONCE, to
the nearest millisecond, when a time is written. So a chapter starts within half a millisecond of where its part really starts, one chapter ends exactly where the next
begins, and the last ends at the length of the whole book, however many parts there are and whatever their sample rates. Adding rounded times instead would let the error
grow with the number of parts. Nothing here is measured by playing the audio or estimated from the text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional, Sequence


@dataclass(frozen=True)
class PartLength:
    """A finished part as the chapter list needs it: its title and how long it is, as a whole number of frames (one sample for each channel) at a sample rate."""

    title: str
    frames: int
    sample_rate: int


@dataclass(frozen=True)
class Chapter:
    """One chapter: its title, and where it starts and ends in the book, in whole milliseconds. `end_ms` of one chapter is `start_ms` of the next."""

    title: str
    start_ms: int
    end_ms: int


def to_ms(seconds: Fraction) -> int:
    """An exact time in seconds as whole milliseconds, rounded to the nearest (a half goes up). The one place a time is rounded."""
    return math.floor(seconds * 1000 + Fraction(1, 2))


def tidy_title(title: str) -> str:
    """A title as one line: white space of any kind (including line breaks) becomes one space, so a title cannot break the line format of a chapter list."""
    return " ".join(title.split())


def chapters_of(parts: Sequence[PartLength]) -> list[Chapter]:
    """The chapters of finished parts, in order. A part whose title is empty is called "Part n" (n counting from 1), as the server names a part with no title."""
    chapters: list[Chapter] = []
    elapsed = Fraction(0)
    for number, part in enumerate(parts, 1):
        if part.sample_rate <= 0 or part.frames < 0:
            raise ValueError(f"part {number} has no usable length")
        begins = elapsed
        elapsed = elapsed + Fraction(part.frames, part.sample_rate)  # exact: no rounding until a time is written
        chapters.append(Chapter(tidy_title(part.title) or f"Part {number}", to_ms(begins), to_ms(elapsed)))
    return chapters


def total_seconds(parts: Sequence[PartLength]) -> Fraction:
    """The exact length of all the parts end to end, in seconds."""
    return sum((Fraction(p.frames, p.sample_rate) for p in parts), Fraction(0))


def clock(ms: int) -> str:
    """`HH:MM:SS.mmm`. The hours have at least two digits and as many more as the book needs (a hundred-hour book is `100:00:00.000`)."""
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def chapters_txt(chapters: Sequence[Chapter]) -> str:
    """`chapters.txt`: one line for each chapter, `HH:MM:SS.mmm Title` (DESIGN.md §44.4). Plain, and read by podcast and video tools, by audiobook players that import a list,
    and by a person."""
    return "".join(f"{clock(c.start_ms)} {tidy_title(c.title)}\n" for c in chapters)


def _escape(value: str) -> str:
    """A value in ffmpeg's metadata file format: `=`, `;`, `#`, `\\` and a line break each need a backslash before them (a line break stays a line break: ffmpeg's own
    rule). Titles are one line (`tidy_title`), so the line break is only for a value that did not come through it."""
    return "".join("\\" + char if char in "=;#\\\n" else char for char in value)


def ffmetadata(chapters: Sequence[Chapter], title: str = "", tags: Optional[dict[str, str]] = None) -> str:
    """`chapters.ffmetadata`: ffmpeg's own metadata file (`;FFMETADATA1`, then a `[CHAPTER]` for each chapter with a 1/1000 timebase, a start, an end and the title).
    `ffmpeg -i joined.wav -i chapters.ffmetadata -map_metadata 1 -map_chapters 1 ...` puts exactly these chapters on a file the person has joined themselves, and it is
    what the studio itself gives ffmpeg to build the M4B, so the file and the ZIP's list are the same. `tags` are more tags of the whole file (the M4B's genre and the
    machine-generated note); the ZIP's copy has none."""
    lines = [";FFMETADATA1"]
    if tidy_title(title):
        lines.append(f"title={_escape(tidy_title(title))}")
    for key, value in (tags or {}).items():
        lines.append(f"{key}={_escape(tidy_title(value))}")
    for chapter in chapters:
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={chapter.start_ms}", f"END={chapter.end_ms}", f"title={_escape(tidy_title(chapter.title))}"]
    return "\n".join(lines) + "\n"
