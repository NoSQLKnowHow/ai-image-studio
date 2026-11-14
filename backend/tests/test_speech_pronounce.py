"""The pronunciation list's rule, one clause at a time (DESIGN.md §44.3, criteria 347 and 348).

`pronounce.py` is plain functions over strings, so every clause of the rule is tested here without a database, a server or an engine. What these protect: a respelling that
reaches the engine exactly where the person meant and nowhere else (a word inside a longer word, a capital that was not asked for, a phrase cut in half, a respelling
changed by another entry). Each test names the clause it holds; a failure points at the line of the rule that broke.
"""

from __future__ import annotations

import time

import pytest

from studio.speech.pronounce import (ENTRIES_MAX, SAY_MAX, WORD_MAX, WORD_WORDS_MAX, Entry, EntryProblem, Matcher, check_say, check_word, spoken_for, used_entries,
                                     word_key)


def say(text: str, *entries: Entry) -> str:
    """What would be spoken for `text` with these entries."""
    return Matcher(entries).apply(text).spoken


# ---------------------------------------------------------------- what an entry may be (criterion 347)
def test_a_word_is_tidied_and_must_be_something_to_match():
    assert check_word("  New   York ") == "New York"  # runs of white space are one space, the ends are trimmed: the same word typed carelessly
    for wrong, code in [("", "empty"), ("   ", "empty"), ("x" * (WORD_MAX + 1), "too_long"), (" ".join(["w"] * (WORD_WORDS_MAX + 1)), "too_long"),
                        ("bell\x07", "characters"), ("-dash", "start"), ("$5", "start")]:
        with pytest.raises(EntryProblem) as caught:
            check_word(wrong)
        assert caught.value.field == "word" and caught.value.code == code, wrong  # the page puts the words beside the box they are about


def test_the_longest_word_and_the_longest_phrase_are_allowed():
    assert check_word("x" * WORD_MAX) == "x" * WORD_MAX
    assert check_word(" ".join(["ab"] * WORD_WORDS_MAX)) == " ".join(["ab"] * WORD_WORDS_MAX)


def test_a_respelling_is_tidied_and_cannot_be_empty_or_the_word_or_too_long():
    assert check_say(" nuh -  GWEN ", "Nguyen") == "nuh - GWEN"
    for wrong, code in [("", "empty"), ("  ", "empty"), ("y" * (SAY_MAX + 1), "too_long"), ("a\x00b", "characters"), ("Nguyen", "same")]:
        with pytest.raises(EntryProblem) as caught:
            check_say(wrong, "Nguyen")
        assert caught.value.field == "say" and caught.value.code == code, wrong
    assert check_say("nguyen", "Nguyen") == "nguyen"  # only an exact copy is refused: a different spelling is a different respelling


def test_two_words_are_the_same_word_when_only_their_case_differs():
    assert word_key("Nguyen") == word_key("NGUYEN") == word_key("nguyen")
    assert word_key("Straße") == word_key("STRASSE")  # casefold, not lower: the German double s is the same word


def test_the_list_holds_a_thousand_entries():
    assert ENTRIES_MAX == 1000  # the limit the page shows (capabilities.limits.speech.pronunciation) is this constant, not a number of its own


# ---------------------------------------------------------------- whole words (criterion 348)
def test_a_word_is_matched_whole_and_not_inside_a_longer_word():
    entry = Entry("win", "wynn")
    assert say("A win in winter, a twin, winning.", entry) == "A wynn in winter, a twin, winning."


def test_a_word_ends_at_an_apostrophe_or_a_hyphen():
    entry = Entry("Nguyen", "win")
    assert say("Nguyen's car, Nguyen-Smith, and O'Nguyen.", entry) == "win's car, win-Smith, and O'win."  # the point of whole words: a name keeps its 's


def test_an_underscore_or_a_digit_is_part_of_a_word():
    entry = Entry("Nguyen", "win")
    assert say("_Nguyen Nguyen_ 3Nguyen Nguyen3 Nguyen", entry) == "_Nguyen Nguyen_ 3Nguyen Nguyen3 win"


def test_punctuation_round_a_word_is_kept():
    entry = Entry("Nguyen", "win")
    assert say("(Nguyen), “Nguyen.” Nguyen? Nguyen!", entry) == "(win), “win.” win? win!"


def test_a_word_that_ends_in_a_full_stop_is_matched_with_it():
    entry = Entry("St.", "Saint")
    assert say("St. Mary and Dr. St. Clair, but not St.", entry) == "Saint Mary and Dr. Saint Clair, but not Saint"  # the stop is part of the entry, so it goes too
    assert say("Stuff and St", entry) == "Stuff and St"  # without the stop it is not this entry


def test_a_word_with_an_apostrophe_inside_is_matched_literally():
    assert say("Mr O'Brien and Brien", Entry("O'Brien", "oh bryan")) == "Mr oh bryan and Brien"


def test_a_digit_may_begin_a_word():
    assert say("Room 101 and 1010", Entry("101", "one oh one")) == "Room one oh one and 1010"


# ---------------------------------------------------------------- case (criterion 348)
def test_case_does_not_matter_unless_the_entry_says_so():
    entry = Entry("Nguyen", "win")
    assert say("Nguyen nguyen NGUYEN nGuYeN", entry) == "win win win win"


def test_an_entry_that_matches_capitals_leaves_the_other_cases_alone():
    entry = Entry("US", "you ess", match_case=True)
    assert say("Tell us about the US. Us? US!", entry) == "Tell us about the you ess. Us? you ess!"


def test_case_is_folded_for_letters_outside_ascii_too():
    assert say("müller, MÜLLER", Entry("Müller", "Mueller")) == "Mueller, Mueller"


# ---------------------------------------------------------------- phrases, longest first, one pass
def test_a_phrase_matches_across_any_run_of_spaces():
    assert say("I love New   York\nand new york.", Entry("New York", "Noo Yawk")) == "I love Noo Yawk\nand Noo Yawk."  # a line break or double space inside is still the phrase


def test_the_longest_entry_wins_where_several_begin_at_the_same_place():
    entries = [Entry("York", "yawk"), Entry("New York", "noo yawk"), Entry("New York City", "the big apple")]
    assert say("New York City, New York, York.", *entries) == "the big apple, noo yawk, yawk."
    assert say("New York City, New York, York.", *reversed(entries)) == "the big apple, noo yawk, yawk."  # and the order they were made in makes no difference


def test_where_two_entries_overlap_the_one_that_begins_first_wins():
    entries = [Entry("a b", "X"), Entry("b c", "Y")]
    assert say("a b c", *entries) == "X c"  # leftmost, then longest; "b c" begins inside the first match, so it is not applied


def test_it_is_done_in_one_pass_so_a_respelling_is_never_changed_by_another_entry():
    entries = [Entry("a", "b"), Entry("b", "c"), Entry("Nguyen", "win"), Entry("win", "wynn")]
    assert say("a b Nguyen win", *entries) == "b c win wynn"  # not "c c wynn wynn": the "b" that "a" became is not looked at again


def test_a_respelling_is_literal_text_whatever_it_looks_like():
    assert say("Nguyen", Entry("Nguyen", r"\1 \g<0> (?P<x>) $1")) == r"\1 \g<0> (?P<x>) $1"  # a respelling is never read as a pattern or a replacement template


def test_a_text_with_nothing_to_change_is_returned_as_it_is():
    entry = Entry("Nguyen", "win")
    text = "Nothing to change here."
    assert say(text, entry) is text or say(text, entry) == text
    assert Matcher([]).apply(text).spoken == text and not Matcher([])  # an empty list is falsy and changes nothing


def test_no_entry_changes_text_that_does_not_contain_it_even_at_the_ends():
    assert say("Nguyen", Entry("Nguyen", "win")) == "win"  # at the very start and end of the text
    assert say("", Entry("Nguyen", "win")) == ""


# ---------------------------------------------------------------- counting (criterion 350)
def test_the_uses_are_counted_for_each_entry():
    matcher = Matcher([Entry("Nguyen", "win"), Entry("Hermes", "her meez")])
    applied = matcher.apply("Nguyen, Hermes, nguyen and Nguyen.")
    by_word = {matcher.entries[i].word: n for i, n in applied.counts.items()}
    assert by_word == {"Nguyen": 3, "Hermes": 1} and applied.uses == 4


def test_the_entries_a_text_uses_are_picked_out_with_the_number_of_words_changed():
    entries = [Entry("Nguyen", "win"), Entry("Hermes", "her meez"), Entry("Unused", "x")]
    used, uses = used_entries(entries, ["Nguyen met Nguyen.", "Nothing here.", "Nguyen's Hermes."])
    assert [e.word for e in used] == ["Nguyen", "Hermes"] and uses == 4  # the list's own order, and only the entries the words use


def test_a_matcher_given_no_entries_returns_the_text_through_spoken_for():
    assert spoken_for("Nguyen", None) == "Nguyen"
    assert spoken_for("Nguyen", Matcher([])) == "Nguyen"
    assert spoken_for("Nguyen", Matcher([Entry("Nguyen", "win")])) == "win"


def test_an_entry_survives_the_trip_through_the_options_of_a_reading():
    entry = Entry("US", "you ess", match_case=True)
    assert Entry.from_dict(entry.as_dict()) == entry  # a reading keeps its entries as dicts in its options (service.py) and rebuilds them for each job


# ---------------------------------------------------------------- a whole book against a whole list
def test_a_whole_book_is_matched_against_a_thousand_entries_in_a_few_seconds():
    """The reason for scanning word starts and filing the entries by letter: trying every entry at every place would be 1.5 billion tries for a book this size and take
    minutes. The bound is generous (measured on this container: 344,000 characters against 1,001 entries in 0.12 s), so it fails only for a change that brings the slow way back."""
    entries = [Entry(f"{chr(97 + n % 26)}{chr(97 + n // 26 % 26)}zq{n}", f"say{n}") for n in range(1000)]
    paragraph = "The quick brown fox jumps over the lazy dog and Nguyen met aazq0 near the river bank. " * 4
    book = [paragraph] * 1000  # about 340,000 characters: a fifth of the biggest reading
    matcher = Matcher([*entries, Entry("Nguyen", "win")])
    started = time.monotonic()
    total = sum(matcher.apply(piece).uses for piece in book)
    assert time.monotonic() - started < 15
    assert total == 1000 * 4 * 2  # Nguyen and aazq0 in each of the four sentences of every paragraph
