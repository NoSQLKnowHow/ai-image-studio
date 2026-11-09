"""What the model is told (DESIGN.md section 37.5, criteria 224-227, 232-234).

The instructions are text files in `instructions/` (`common.md`, and one for each kind of prompt), so that they can be read, changed and tested; the page cannot
change them. This module fills in the numbers they mention from the studio's own settings, and adds what is true only of THIS chat: the detail level, the
person's standing preferences, a few of their saved prompts as examples of their taste, the current draft, and whether the model can see the pictures.
Everything here is assembled for every reply and none of it is ever dropped to make room (`context.py` drops old turns instead).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .. import presets as P
from .. import vector as V
from ..musicprompt import FIELDS as MUSIC_FIELDS
from . import CHIP_CHARS, CHIPS_MAX, CHIPS_MIN, QUESTIONS_MAX
from .schema import DETAILS, FOR_KINDS

DIRECTORY = Path(__file__).resolve().parent / "instructions"
FILES = {"common": "common.md", **{kind: f"{kind}.md" for kind in FOR_KINDS}}
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")

# How long the finished prompt should be (DESIGN.md section 37.3 rule 15). PROPOSED numbers: the length meter on the page always shows the truth.
DETAIL_TARGETS = {
    "short": "Short: about one or two sentences (roughly 30 to 60 words).",
    "normal": "Normal: one paragraph (roughly 80 to 150 words).",
    "rich": "Rich: several paragraphs (roughly 200 to 400 words).",
}

# What the person just did, said to the model on the turn that follows it. The first six are the buttons of the chat; `message` is a plain turn.
INTENTS = {
    "message": "",
    "write_now": "The person pressed Write it now. Ask nothing more: write the finished prompt now from what you have, leave questions empty, and set done to true.",
    "variations": ("Give exactly three different takes on the current draft, each with a short label that names how it differs (for example simpler, more cinematic, "
                   "more detailed), in the variations list. Do not change the draft itself, and ask no questions."),
    "flesh_out": ("The person wants to flesh out the saved prompt that is the current draft. In your reply say briefly what it already has and what is missing, then ask "
                  "about the most useful missing things, starting with the most important."),
    "refine": ("The person wants to improve this prompt after seeing a result, which is described below. Listen to what they say is wrong, propose an improved draft with "
               "a short note saying what you changed and why, and ask a question only if you need one."),
    "describe": ("The person attached a picture and wants a prompt that would make a picture like it. Describe what you see as a prompt, then ask about what they would "
                 "change."),
    "help_edit": ("The person is writing an edit of the attached pictures, which are numbered in order. Ask what should change, then write the prompt in the form an edit "
                  "wants, referring to the pictures by number."),
}

PICTURES_SEEN = "The person attached {n} picture{s}, numbered in the order given. You can see them: write from what is really in them."
PICTURES_UNSEEN = ("The person attached {n} picture{s}{names}, but you cannot see pictures. You only know how many there are and what they are called: say so in your "
                   "reply, work from that, and ask the person to describe what matters in each.")


@dataclass(frozen=True)
class Example:
    """One of the person's saved prompts, shown to the model as an example of their taste."""

    title: str
    for_kind: str
    text: str
    negative: Optional[str] = None


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    return (DIRECTORY / FILES[name]).read_text(encoding="utf-8")


def _comments_out(text: str) -> str:
    """The owner's notes at the top of a file (an HTML comment) are for the owner, not for the model."""
    return re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)


def values(settings: Any) -> dict[str, str]:
    """Every {{word}} the instruction files may use, from the studio's own constants and settings."""
    return {
        "questions_max": str(QUESTIONS_MAX), "chips_min": str(CHIPS_MIN), "chips_max": str(CHIPS_MAX), "chip_chars": str(CHIP_CHARS),
        "prompt_chars": str(settings.max_prompt_chars),
        "shapes": ", ".join(P.ASPECT_RATIOS), "guidance_min": f"{P.CFG_MIN:g}", "guidance_max": f"{P.CFG_MAX:g}",
        "vector_kinds": ", ".join(V.KINDS), "colours_min": str(V.COLOURS_MIN), "colours_max": str(V.COLOURS_MAX),
        "size_mm_min": f"{V.SIZE_MM_MIN:g}", "size_mm_max": f"{V.SIZE_MM_MAX:g}",
        "seconds_min": str(P.MUSIC_DURATION_MIN), "seconds_max": str(settings.music_max_seconds),
        "music_fields": ", ".join(MUSIC_FIELDS),
    }


def fill(text: str, given: Mapping[str, str]) -> str:
    """Put the numbers in. A word the studio does not know is a mistake in the file and raises, so a typo cannot reach the model as the text `{{shapse}}`."""
    def one(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in given:
            raise KeyError(f"the instruction files use {{{{{name}}}}}, which the studio does not fill in")
        return given[name]

    return _PLACEHOLDER.sub(one, text)


def render_draft(draft: Optional[Mapping[str, Any]]) -> str:
    if not draft or not str(draft.get("prompt") or "").strip():
        return "There is no draft yet."
    lines = ["Prompt:", str(draft["prompt"]).strip()]
    if str(draft.get("negative") or "").strip():
        lines += ["Negative prompt:", str(draft["negative"]).strip()]
    if draft.get("settings"):
        lines += ["Settings:", json.dumps(draft["settings"], sort_keys=True, ensure_ascii=False)]
    return "\n".join(lines)


def pictures_note(count: int, names: Sequence[str], can_see: bool) -> str:
    if count <= 0:
        return ""
    plural = "" if count == 1 else "s"
    if can_see:
        return PICTURES_SEEN.format(n=count, s=plural)
    listed = " (" + ", ".join(f'"{n}"' for n in names[:8]) + ")" if names else ""
    return PICTURES_UNSEEN.format(n=count, s=plural, names=listed)


def system_prompt(kind: str, settings: Any, *, detail: str = "normal", preferences: Sequence[str] = (), examples: Sequence[Example] = (),
                  draft: Optional[Mapping[str, Any]] = None, pictures: str = "", run_note: str = "") -> str:
    """The whole instruction for one reply. The order matters to a model that reads it once: how to help, what this kind likes, then this conversation."""
    if kind not in FOR_KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if detail not in DETAILS:
        raise ValueError(f"unknown detail {detail!r}")
    given = values(settings)
    parts = [fill(_comments_out(_read("common")), given).strip(), fill(_comments_out(_read(kind)), given).strip()]
    here = [f"This prompt is for: {kind}.", f"Detail level: {DETAIL_TARGETS[detail]}"]
    if pictures:
        here.append(pictures)
    parts.append("## This conversation\n\n" + "\n".join(f"- {line}" for line in here))
    if preferences:
        parts.append("## The person's standing preferences\n\nApply these without being asked, and do not ask what they already answer.\n\n" + "\n".join(f"- {p}" for p in preferences))
    if examples:
        shown = ["## Some of the person's saved prompts\n\nThese show their taste. Learn from them; do not copy them."]
        for example in examples:
            body = example.text.strip()
            shown.append(f'### "{example.title}" (for {example.for_kind})\n\n{body[:600] + "…" if len(body) > 600 else body}')
        parts.append("\n\n".join(shown))
    if run_note:
        parts.append("## The result being discussed\n\n" + run_note)
    parts.append("## The current draft\n\n" + render_draft(draft))
    return "\n\n".join(parts)
