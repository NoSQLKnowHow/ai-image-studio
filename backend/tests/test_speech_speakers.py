"""What a speaker line is (DESIGN.md §44.5, criterion 362): one clause of the rule at a time, with the false alarms the rule exists to refuse.

`speakers.py` is plain functions over strings. What these protect: `Alice: Hello there.` is found, and `Note:`, `Warning:`, `Chapter 1:`, `10:30`, a web address, `Q:`, a name
with a lower-case word or a digit, and a label said once in an essay are not; a name must begin two lines and a text must have two such names; and no more than twelve are
offered. A wrong guess takes a word out of the text and changes a voice, so each false alarm named in the brief has a test of its own.
"""

from __future__ import annotations

import pytest

from studio.speech.text.speakers import (LABELS, MAX_SPEAKERS, SAMPLE_CHARS, accepted, candidate, name_key, speaker_keys, starts_speaker_line, tally)


# ---------------------------------------------------------------- one line
@pytest.mark.parametrize("line,name,rest", [
    ("Alice: Hello there.", "Alice", "Hello there."),
    ("ALICE: HELLO", "ALICE", "HELLO"),
    ("Mary Jane: Over here.", "Mary Jane", "Over here."),
    ("Dr. Watson: Remarkable.", "Dr. Watson", "Remarkable."),
    ("Old Tom Bombadil: Hey dol!", "Old Tom Bombadil", "Hey dol!"),
    ("O'Neil: Yes.", "O'Neil", "Yes."),
    ("Jean-Luc: Engage.", "Jean-Luc", "Engage."),
    ("Zoë: Ça va?", "Zoë", "Ça va?"),
    ("Narrator: It was a quiet night.", "Narrator", "It was a quiet night."),
    ("Alice:   spaces after the colon are fine", "Alice", "spaces after the colon are fine"),
    ('Bob: "Quoted speech."', "Bob", '"Quoted speech."'),
    ("Al: Hi.", "Al", "Hi."),  # two letters is a name
])
def test_a_line_that_starts_with_a_name_and_a_colon_and_words_is_a_speaker_line(line, name, rest):
    found = candidate(line)
    assert found is not None and found.name == name and found.rest == rest and found.label == line[:len(name) + 1]  # the label is the name and the colon, as written


@pytest.mark.parametrize("line,why", [
    ("Note: the door is locked.", "a label texts are full of"),
    ("NOTE: shouting", "a label, in any case"),
    ("Warning: wet floor", "a label"),
    ("Caution: hot", "a label"),
    ("Chapter 1: The Beginning", "a digit in the name, and a label"),
    ("Chapter One: The Beginning", "a label in the name"),
    ("Agent 47: Target acquired.", "a digit"),
    ("10:30 is when we leave.", "a time"),
    ("The meeting is at 10:30 tomorrow", "a colon in the middle of a line"),
    ("https://example.com/page", "a web address: the colon is not followed by a space"),
    ("Http://example.com is fine", "a capital, but the colon is followed by a slash"),
    ("Re:thing", "no space after the colon"),
    ("Alice:Hello", "no space after the colon"),
    ("Alice:", "nothing after the colon"),
    ("Alice:   ", "only spaces after the colon"),
    ("Q: Why is the sky blue?", "one letter"),
    ("Q.: Why is the sky blue?", "an initial: two characters but one letter"),
    ("A: Because of the air.", "one letter"),
    ("I: think so", "one letter"),
    ("Note to self: buy milk", "a word that is not capitalised"),
    ("Lord of the Rings: a review", "a word that is not capitalised"),
    ("alice: lower case start", "does not start with a capital"),
    ("Alice Bob Carol Dave: four words", "more than three words"),
    ("A" * 31 + ": too long", "more than 30 characters"),
    ("Remember: lock the door", "a label (and see below: once in a text it is not a speaker either)"),
    ("Example: this", "a label"),
    ("Ingredients: flour", "a label"),
    (": no name", "no name"),
    ("", "empty"),
    ("Hello, world: here", "a comma in the name"),
    ("What is this: a test?", "lower-case words"),
])
def test_a_line_that_is_not_one_is_refused(line, why):
    assert candidate(line) is None, why


def test_a_label_is_matched_ignoring_case_and_in_any_word_of_the_name():
    assert candidate("Example Name: x") is None  # "Example" is a label, though the second word is not
    assert candidate("Dr. Note: x") is None  # and "Dr." followed by a label too
    assert candidate("Notes: x") is None
    assert "note" in LABELS and "warning" in LABELS and "chapter" in LABELS


def test_only_the_first_colon_counts_so_a_colon_in_the_speech_is_part_of_the_speech():
    found = candidate("Alice: Listen: the time is 10:30.")
    assert found is not None and found.name == "Alice" and found.rest == "Listen: the time is 10:30."


def test_two_spellings_of_a_name_are_one_speaker():
    assert name_key("ALICE") == name_key("Alice") == name_key("  alice ")
    assert name_key("Mary  Jane") == name_key("MARY JANE")


# ---------------------------------------------------------------- a text
SCRIPT = [
    "Narrator: It was a quiet night at the lighthouse.",
    "Alice: Did you hear that?",
    "Bob: Hear what? The wind?",
    "Alice: No, a knock, three times.",
    "Bob: Then go and open the door yourself.",
    "The sea went on without them.",
]


def test_a_name_is_counted_for_each_text_that_begins_with_it_and_kept_as_first_written():
    found = tally(["ALICE: hi", "Bob: yes", "Alice: again", "alice: lower case is no label", "Not a label here"])
    assert {k: f.lines for k, f in found.items()} == {"alice": 2, "bob": 1}
    assert found["alice"].name == "ALICE" and found["alice"].order == 0 and found["bob"].order == 1
    assert found["alice"].sample == "hi"  # the first line a name was found on, for the review to show


def test_a_sample_is_cut_at_eighty_characters():
    """The number is written out here, and not read from the constant, so that a change of the constant is noticed (DESIGN.md §44.5: "the first of them", cut at eighty)."""
    assert SAMPLE_CHARS == 80
    assert tally(["Alice: " + "x" * 300, "Bob: y"])["alice"].sample == "x" * 80
    assert tally(["Alice: " + "x" * 79, "Bob: y"])["alice"].sample == "x" * 79  # a line shorter than that is whole


def test_a_script_has_speakers_when_two_names_have_two_lines_each():
    speakers = accepted(tally(SCRIPT))
    assert [f.name for f in speakers.values()] == ["Alice", "Bob"]  # Narrator spoke once: a line, not a speaker
    assert [f.lines for f in speakers.values()] == [2, 2]


def test_a_name_that_begins_one_line_is_not_a_speaker():
    assert accepted(tally(["Alice: one", "Bob: one"])) == {}  # each once: no one has two lines


def test_one_name_alone_is_not_a_conversation():
    assert accepted(tally(["Alice: one", "Alice: two", "Alice: three"])) == {}
    assert accepted(tally(["Alice: one", "Alice: two", "Bob: just once"])) == {}  # only one name has two lines


def test_a_label_said_once_in_an_essay_is_a_line_and_not_a_speaker():
    essay = ["Remember: lock the door.", "A paragraph of the essay goes here, with some words.", "Smith: of the firm, said it was fine.", "More essay."]
    assert speaker_keys(essay) == set()


def test_a_glossary_of_single_entries_has_no_speakers():
    assert speaker_keys([f"Term{chr(97 + n)}: a definition" for n in range(10)]) == set()  # (they are not even names: a digit-free but lower-case-led second letter is fine, yet each is said once)
    assert speaker_keys([f"{w}: a definition" for w in ("Apple", "Berry", "Cherry", "Damson")]) == set()


def test_at_most_twelve_names_are_offered_the_twelve_with_the_most_lines_in_the_order_they_first_appear():
    names = [f"Person{chr(65 + n)}" for n in range(15)]  # PersonA ... PersonO (only letters after the name: a digit would make it no name)
    lines = []
    for n, name in enumerate(names):
        lines += [f"{name}: line"] * (2 if n < 13 else 5)  # the last two names speak the most
    kept = [f.name for f in accepted(tally(lines)).values()]
    # the two that speak most are kept, then ties (two lines each) go to the names seen first; the result is in the order the names first appear
    assert len(kept) == MAX_SPEAKERS == 12
    assert kept == [f"Person{chr(65 + n)}" for n in list(range(10)) + [13, 14]]


def test_a_tie_for_the_last_place_goes_to_the_name_seen_first():
    lines = []
    for n in range(13):
        lines += [f"Name{chr(65 + n)}: x"] * 2
    kept = [f.name for f in accepted(tally(lines)).values()]
    assert len(kept) == 12 and "NameM" not in kept and "NameA" in kept


def test_the_keys_of_a_text_are_what_a_reader_splits_lines_by():
    keys = speaker_keys(SCRIPT)
    assert keys == {"alice", "bob"}
    assert starts_speaker_line("Alice: more", keys) and starts_speaker_line("  BOB: shouting", keys)
    assert not starts_speaker_line("Narrator: one", keys)  # a name that is not a speaker does not start a paragraph
    assert not starts_speaker_line("The sea went on.", keys)
    assert not starts_speaker_line("Alice said: hello", keys)
