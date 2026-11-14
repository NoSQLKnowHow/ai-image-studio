"""The chapter list of a reading (DESIGN.md §44.4, criteria 354 and 355): what the times are, to the millisecond, and how the two files of the ZIP are written.

`chapters.py` is plain arithmetic and text, so it is tested here without a server, a database or ffmpeg. What these protect: a chapter that starts where its part really
starts (the error does not grow with the number of parts, which is what adding rounded times would do), one chapter that ends exactly where the next begins, a last chapter
that ends at the length of the whole book, and two files that a tool can read: a line of `chapters.txt` is `HH:MM:SS.mmm Title` and the ffmetadata file escapes what ffmpeg's
format says must be escaped.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from studio.speech.chapters import (Chapter, PartLength, chapters_of, chapters_txt, clock, ffmetadata, tidy_title, to_ms, total_seconds)


def part(title: str, frames: int, rate: int = 24000) -> PartLength:
    return PartLength(title, frames, rate)


# ---------------------------------------------------------------- one rounding
def test_a_time_is_rounded_once_to_the_nearest_millisecond_and_a_half_goes_up():
    assert to_ms(Fraction(0)) == 0
    assert to_ms(Fraction(1, 2000)) == 1  # exactly half a millisecond: up
    assert to_ms(Fraction(1, 2001)) == 0  # a hair under half: down
    assert to_ms(Fraction(3, 2000)) == 2
    assert to_ms(Fraction(145141, 24000)) == 6048  # the length used when ffmpeg's own output was looked at: 6.0475416... s


# ---------------------------------------------------------------- where the chapters start (criterion 355)
def test_each_chapter_starts_at_the_sum_of_the_lengths_before_it():
    parts = [part("One", 24000 * 3 + 137), part("Two", 24000 * 2 + 5), part("Three", 24000 + 999)]
    chapters = chapters_of(parts)
    assert [c.title for c in chapters] == ["One", "Two", "Three"]
    assert [c.start_ms for c in chapters] == [0, 3006, 5006]  # 72137/24000 = 3.00571 s; plus 48005/24000 = 2.00021 s
    assert [c.end_ms for c in chapters] == [3006, 5006, 6048]


def test_one_chapter_ends_exactly_where_the_next_begins_and_the_last_ends_at_the_whole_length():
    parts = [part(f"Part {n}", 24001 + n * 7) for n in range(1, 40)]
    chapters = chapters_of(parts)
    assert all(a.end_ms == b.start_ms for a, b in zip(chapters, chapters[1:]))  # no gap and no overlap, whatever the rounding
    assert chapters[0].start_ms == 0
    assert chapters[-1].end_ms == to_ms(total_seconds(parts))


def test_the_error_does_not_grow_with_the_number_of_parts():
    """A hundred parts, each a little over a second (24001 frames at 24 kHz): adding the rounded lengths (1000 ms each) would put the last chapter 4 ms early, and more for a longer
    book. The times are rounded once, from the exact total, so the last chapter starts within half a millisecond of where it really does."""
    parts = [part(f"{n}", 24001) for n in range(100)]
    chapters = chapters_of(parts)
    exact_start_of_last = Fraction(99 * 24001, 24000)
    assert abs(Fraction(chapters[-1].start_ms, 1000) - exact_start_of_last) <= Fraction(1, 2000)
    assert chapters[-1].start_ms == 99004 != 99 * 1000  # (adding rounded lengths gives 99000)
    assert abs(Fraction(chapters[-1].end_ms, 1000) - Fraction(100 * 24001, 24000)) <= Fraction(1, 2000)


def test_parts_at_different_sample_rates_are_added_exactly_too():
    """A reading has one rate, but the arithmetic must not depend on it: 22,050 frames at 22,050 Hz is exactly a second, and 24,000 at 24,000 is another."""
    chapters = chapters_of([part("A", 22050, 22050), part("B", 24000, 24000), part("C", 11025, 22050)])
    assert [(c.start_ms, c.end_ms) for c in chapters] == [(0, 1000), (1000, 2000), (2000, 2500)]


def test_a_part_with_nothing_in_it_is_a_chapter_with_no_length():
    chapters = chapters_of([part("A", 24000), part("Empty", 0), part("B", 24000)])
    assert [(c.start_ms, c.end_ms) for c in chapters] == [(0, 1000), (1000, 1000), (1000, 2000)]


def test_a_part_with_no_usable_length_is_refused():
    for bad in (part("A", 100, 0), part("A", -1)):
        with pytest.raises(ValueError):
            chapters_of([bad])


def test_a_long_book_is_exact_to_the_millisecond():
    """Twelve hours in a hundred and forty-four parts of 5 minutes and 3 frames: the last chapter ends at the exact length rounded once."""
    parts = [part(f"Chapter {n}", 24000 * 300 + 3) for n in range(144)]
    chapters = chapters_of(parts)
    assert chapters[-1].end_ms == to_ms(Fraction(144 * (24000 * 300 + 3), 24000))
    assert chapters[-1].end_ms == 43_200_018  # 12 hours and 18 ms: the three extra frames of every part, 144 x 3 / 24000 = 0.018 s


# ---------------------------------------------------------------- titles
def test_a_title_is_one_line_and_an_empty_one_is_part_n():
    assert tidy_title("  Chapter\n\n One \t ") == "Chapter One"
    chapters = chapters_of([part("  ", 24000), part("The \n Second", 24000), part("", 24000)])
    assert [c.title for c in chapters] == ["Part 1", "The Second", "Part 3"]  # the number is the part's place, counting from 1


# ---------------------------------------------------------------- chapters.txt
def test_the_clock_has_hours_minutes_seconds_and_milliseconds():
    assert clock(0) == "00:00:00.000"
    assert clock(59_999) == "00:00:59.999"
    assert clock(3_723_456) == "01:02:03.456"
    assert clock(100 * 3_600_000) == "100:00:00.000"  # a hundred hours has three digits: the hours are never cut to two


def test_chapters_txt_is_one_line_for_each_chapter_with_its_start_and_title():
    text = chapters_txt([Chapter("Prologue", 0, 61_500), Chapter("Chapter 1: A start", 61_500, 3_723_456)])
    assert text == "00:00:00.000 Prologue\n00:01:01.500 Chapter 1: A start\n"


def test_a_title_with_a_line_break_is_one_line_in_chapters_txt():
    assert chapters_txt([Chapter("Two\nlines  here", 0, 1)]) == "00:00:00.000 Two lines here\n"  # a line break would make a chapter of the second line


def test_chapters_txt_of_no_chapters_is_empty():
    assert chapters_txt([]) == ""


def test_chapters_txt_keeps_unicode_titles():
    assert chapters_txt([Chapter("Ünïcödé — 第一章", 0, 1)]) == "00:00:00.000 Ünïcödé — 第一章\n"


# ---------------------------------------------------------------- chapters.ffmetadata
def test_the_ffmetadata_file_has_the_header_and_a_block_for_each_chapter_with_a_millisecond_timebase():
    text = ffmetadata([Chapter("One", 0, 3006), Chapter("Two", 3006, 5006)])
    assert text == (";FFMETADATA1\n"
                    "\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3006\ntitle=One\n"
                    "\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=3006\nEND=5006\ntitle=Two\n")


def test_the_ffmetadata_file_carries_the_title_and_other_tags_of_the_whole_file_before_the_chapters():
    text = ffmetadata([Chapter("One", 0, 10)], "A Book", {"genre": "Audiobook", "comment": "Machine-generated speech."})
    assert text.startswith(";FFMETADATA1\ntitle=A Book\ngenre=Audiobook\ncomment=Machine-generated speech.\n\n[CHAPTER]")


def test_what_ffmpegs_format_says_must_be_escaped_is():
    """`=`, `;`, `#` and `\\` each need a backslash before them in a value, or ffmpeg reads the line differently from how it was meant."""
    text = ffmetadata([Chapter("a=b;c#d\\e", 0, 10)], "x=y")
    assert "title=x\\=y\n" in text
    assert "title=a\\=b\\;c\\#d\\\\e\n" in text


def test_a_title_cannot_start_a_new_line_in_the_file():
    text = ffmetadata([Chapter("one\n[CHAPTER]\nSTART=0", 0, 10)])
    lines = text.splitlines()
    # a title's line breaks are not line breaks in the file (the title is made one line first), so what it says cannot start a chapter of its own or add a start
    assert lines.count("[CHAPTER]") == 1 and [line for line in lines if line.startswith("START=")] == ["START=0"]
    assert lines[-1] == "title=one [CHAPTER] START\\=0"
