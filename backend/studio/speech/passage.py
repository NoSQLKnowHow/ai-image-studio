"""The fixed texts of the voice features (DESIGN.md §38.4).

They are constants and not settings because other things depend on their exact words: a cloned or designed voice is stored with the words said in its
reference clip, and an engine that clones from a recording is given those words with it. A saved voice keeps the passage it was made from, so changing this
text later does not break a voice that already exists.
"""

from __future__ import annotations

# The sentence "Play sample" makes in every voice, so that voices can be compared on the same words (§38.4).
SAMPLE_SENTENCE = "Good evening. This is how I will sound when I read to you."

# The passage a person reads aloud to add their own voice, and the words of the three clips a voice design makes. About 25 seconds at a narrator's pace:
# long enough for a model to hear the voice in several kinds of sentence (a statement, a list, a turn of thought), short enough to read without a slip.
# It is written for this studio, not taken from anywhere (§38.18 item 1: "The Rainbow Passage" was the first idea, and its copyright status is not stated
# where I looked, so it was not used). It has no names or numbers, which people read differently, and no word that is hard to say.
PASSAGE = (
    "On a quiet morning the town wakes slowly. Bakers lift their shutters, a few gulls argue over the harbour, and the first bus rattles up the hill. "
    "Nobody is in a hurry, because nothing important begins before nine. By noon the streets are loud and bright, "
    "yet if you listen closely you can still hear the sea, patient as ever, folding the day into the shore."
)

# What the person ticks before their recording can be saved as a voice. It is stored, with the time, beside the voice (§38.4), and the page shows exactly
# this text, fetched from the server, so what was agreed to and what is on record cannot differ.
CONSENT_STATEMENT = "This is my own voice, or the person whose voice it is has agreed that I may use it."

# The words of each of the three clips a voice design makes differ only in the seed (§38.4): the same passage, three tries.
DESIGN_CANDIDATES = 3
