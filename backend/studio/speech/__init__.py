"""Speech: text read aloud (DESIGN.md §38).

The package is split so that each part can be understood and tested on its own:
  engine.py, registry.py, engines/   the engine interface, the list of engines, one file per engine
  text/                              turning a source (pasted text, a web page, a file) into parts and pieces
  store.py, schema.py                the database tables of readings, pieces, positions and voices
  audio.py                           the WAV and ZIP streams a reading is played and downloaded from
  service.py, routes.py              what the API does with readings, and the HTTP routes of all of Speech
  voices.py, voice_routes.py         presets, samples, voice design, saved voices, and the routes for them
  recording.py, passage.py           reading and checking a recording of a voice; the fixed texts (passage, sample sentence, statement)
  refused.py                         the refusals the services raise and the one check every request for an engine makes
"""

from __future__ import annotations

from typing import Any

from .text.speakers import MAX_SPEAKERS
from .pronounce import ENTRIES_MAX, SAY_MAX, TRY_CHARS, WORD_MAX, WORD_WORDS_MAX

# About how many characters of English a narrator speaks in a second (150 words a minute at about 5.7 characters a word, spaces included). The page and
# the disk check turn a text's length into the length of its audio with this; the engines' measured speeds replace it for the time to make it.
SPOKEN_CHARS_PER_SECOND = 14.0


def speech_limits(settings: Any) -> dict[str, Any]:
    """`capabilities.limits.speech` (DESIGN.md §38.9): the limits the text pipeline and the uploads obey, so the page says them and never repeats a number."""
    return {
        "max_chars": settings.speech_max_chars,
        "part_max_chars": settings.speech_part_max_chars,
        "upload_mb": settings.speech_max_upload_mb,
        "fetch_mb": settings.speech_fetch_max_mb,
        "pause_ms": settings.speech_pause_ms,
        "voice_seconds": {"min": settings.speech_voice_min_seconds, "max": settings.speech_voice_max_seconds},
        "recording_mb": settings.speech_recording_max_mb,
        "chars_per_second": SPOKEN_CHARS_PER_SECOND,
        # the limits of the pronunciation list (DESIGN.md §44.3), so that the page says them and repeats no number of its own
        "pronunciation": {"word_max": WORD_MAX, "words_max": WORD_WORDS_MAX, "say_max": SAY_MAX, "entries_max": ENTRIES_MAX, "try_chars": TRY_CHARS},
        "speakers_max": MAX_SPEAKERS,  # the most speakers one reading has (DESIGN.md §44.5)
    }

