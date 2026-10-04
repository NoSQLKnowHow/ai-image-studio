"""Writing the music description in the layout the model's card recommends (DESIGN.md §26.2).

The Music tab does this in the browser (frontend/src/music.ts) and `scripts/minimax_music.py` does it here; both must
give the same text for the same fields, which a golden test on each side pins (backend/tests/test_musicprompt.py and
frontend/src/music.test.ts use the same cases). Nothing in the server depends on it: a run's description is sent as text.
"""

from __future__ import annotations

from typing import Mapping

INSTRUMENTAL_PHRASE = "Instrumental, no vocals."
INSTRUMENTAL_LYRICS = "[Instrumental]"
FIELDS = ("genre", "mood", "bpm", "key", "instruments", "voice")  # the page's fields, in the order it shows them


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())


def _sentence(text: str) -> str:
    text = _clean(text)
    return text if not text or text[-1] in ".!?" else text + "."


def key_sentence(key: str) -> str:
    """"C major" -> "key is C, and scale is major."; a key with no scale -> "key is C."; "F# minor" and "Bb Dorian" work too."""
    parts = _clean(key).split(" ", 1)
    if not parts[0]:
        return ""
    return f"key is {parts[0]}, and scale is {parts[1]}." if len(parts) == 2 else f"key is {parts[0]}."


def build_description(fields: Mapping[str, object], instrumental: bool = True) -> str:
    """The description for these fields. Empty fields leave their line out; with lyrics (instrumental False) the
    Vocal Details section appears when there is a voice description."""
    genre, mood, bpm = _clean(fields.get("genre")), _clean(fields.get("mood")), _clean(fields.get("bpm"))
    key, instruments, voice = _clean(fields.get("key")), _clean(fields.get("instruments")), _clean(fields.get("voice"))
    lines = ["Global Metadata"]
    attributes = [part for part in (f"bpm is {bpm}." if bpm else "", key_sentence(key), _sentence(genre)) if part]
    if attributes:
        lines.append("Basic Attributes: " + " ".join(attributes))
    if mood:
        lines.append("Global Emotional Progression: " + _sentence(mood))
    if instrumental:
        lines.append(INSTRUMENTAL_PHRASE)
    elif voice:
        lines += ["Vocal Details", "Vocal Gender & Timbre: " + _sentence(voice)]
    if instruments:
        lines += ["Arrangement", "Instrument Lifecycle Description: " + _sentence(instruments)]
    return "\n".join(lines)


# The cases both sides must agree on: (fields, instrumental, expected description).
GOLDEN = [
    ({"genre": "acoustic pop", "mood": "warm and intimate, building gently", "bpm": "96", "key": "C major",
      "instruments": "fingerpicked guitar and soft piano; brushed drums enter in the second half"}, True,
     "Global Metadata\n"
     "Basic Attributes: bpm is 96. key is C, and scale is major. acoustic pop.\n"
     "Global Emotional Progression: warm and intimate, building gently.\n"
     "Instrumental, no vocals.\n"
     "Arrangement\n"
     "Instrument Lifecycle Description: fingerpicked guitar and soft piano; brushed drums enter in the second half."),
    ({"genre": "indie folk", "mood": "Hopeful.", "bpm": "88", "key": "F# minor", "voice": "soft female lead, breathy",
      "instruments": "acoustic guitar"}, False,
     "Global Metadata\n"
     "Basic Attributes: bpm is 88. key is F#, and scale is minor. indie folk.\n"
     "Global Emotional Progression: Hopeful.\n"
     "Vocal Details\n"
     "Vocal Gender & Timbre: soft female lead, breathy.\n"
     "Arrangement\n"
     "Instrument Lifecycle Description: acoustic guitar."),
    ({"genre": "  lo-fi \n hip hop  ", "key": "Bb"}, True,
     "Global Metadata\nBasic Attributes: key is Bb. lo-fi hip hop.\nInstrumental, no vocals."),
    ({}, True, "Global Metadata\nInstrumental, no vocals."),
    ({"voice": "ignored when instrumental", "mood": "calm"}, True,
     "Global Metadata\nGlobal Emotional Progression: calm.\nInstrumental, no vocals."),
    ({}, False, "Global Metadata"),
]
