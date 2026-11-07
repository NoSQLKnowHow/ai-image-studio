"""The Prompt builder (DESIGN.md §37, version 1.19): an LLM hosted in the person's own Ollama that asks questions until a prompt has been written.

This package is the server's side of it. `ollama.py` is the client (the Python standard library only: the studio needs no new package for it),
`service.py` holds what the routes share (the settings, the client, what was last found out about the server), and `routes.py` the HTTP routes under
`/api/builder`. The numbers below are the limits the server checks every answer against and tells the page (`limits.builder` in the capabilities).
"""

from __future__ import annotations

from typing import Any

# What the model may give back and what a person may save (DESIGN.md §37.5). Chosen here, in one place, so that the page, the checks and the
# instructions the model is given all say the same numbers. Tags and preferences are PROPOSED (§37 gives no number for them).
QUESTIONS_MAX = 3  # at most three questions at a time (§37.2)
CHIPS_MIN, CHIPS_MAX = 3, 5  # chips under each question
CHIP_CHARS = 60
REPLY_CHARS = 2000
TITLE_CHARS = 120
NOTE_CHARS = 300  # the note on a version ("made it moodier; added lighting")
TAGS_MAX = 12  # tags on one prompt
TAG_CHARS = 30
PREFERENCES_MAX = 50  # standing notes in all
PREFERENCE_CHARS = 300


def builder_limits() -> dict[str, Any]:
    """`limits.builder` of the capabilities: what the page needs to show its counters and to stop a person typing what the server would refuse."""
    return {
        "questions_max": QUESTIONS_MAX,
        "chips": {"min": CHIPS_MIN, "max": CHIPS_MAX},
        "chip_chars": CHIP_CHARS,
        "reply_chars": REPLY_CHARS,
        "title_chars": TITLE_CHARS,
        "note_chars": NOTE_CHARS,
        "tags_max": TAGS_MAX,
        "tag_chars": TAG_CHARS,
        "preferences_max": PREFERENCES_MAX,
        "preference_chars": PREFERENCE_CHARS,
    }
