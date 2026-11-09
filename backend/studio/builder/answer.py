"""What the model answers, and what the studio keeps of it (DESIGN.md section 37.5, criteria 224, 227, 250, 255, 257).

The model is asked for one JSON object (Ollama's `format` is the schema below) and nothing it says is trusted: `read_answer` parses it, checks every part
against the SAME rules a run is checked against (a shape must be a real preset, the guidance in range, a palette no longer than the colours less white, the
sizes in range, the lengths bounded), and keeps only what is valid. A part that is not valid is DROPPED and `Answer.notices` says so in plain words, so the
reply the person reads tells them what was left out and why. Only an answer that cannot be read at all (not JSON, not an object, nothing in it) raises
`BadAnswer`, and the engine then asks again once with thinking off (rule 10).

Everything that comes out is plain text for the page to show as text: control characters are removed, and nothing here is ever run or queued (rule 11).
`reply_so_far` is the other half of streaming: it reads the `reply` out of a JSON object that is still being written, so the page can show the words as
they come while the questions, the chips and the draft wait for the whole answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .. import presets as P
from .. import vector as V
from ..musicprompt import FIELDS as MUSIC_FIELDS
from . import (CHIP_CHARS, CHIPS_MAX, CHIPS_MIN, LABEL_CHARS, NOTE_CHARS, PREFERENCE_CHARS, QUESTION_CHARS, QUESTIONS_MAX, REMEMBER_MAX, REPLY_CHARS,
               TAGS_MAX, TITLE_CHARS, VARIATIONS, normalize_tag)
from .schema import FOR_KINDS

# What is removed from any text that comes from the model. Newlines and tabs stay in the text that is meant to have lines (the reply, a prompt, lyrics).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f  ‪-‮⁦-⁩]")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class BadAnswer(Exception):
    """The answer cannot be read as the structure asked for, so there is nothing to keep (the engine asks again, once)."""


@dataclass(frozen=True)
class Limits:
    """The numbers an answer is checked against, taken from the studio's own settings so that they are the run's (`Limits.of`)."""

    prompt_chars: int
    draft_steps: int
    music_max_seconds: int

    @classmethod
    def of(cls, settings: Any) -> "Limits":
        return cls(prompt_chars=settings.max_prompt_chars, draft_steps=settings.draft_steps, music_max_seconds=settings.music_max_seconds)


@dataclass
class Question:
    id: str
    text: str
    chips: list[str]


@dataclass
class Draft:
    prompt: str
    negative: Optional[str] = None
    settings: dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class Variation:
    label: str
    prompt: str
    negative: Optional[str] = None


@dataclass
class Answer:
    reply: str = ""
    title: Optional[str] = None
    questions: list[Question] = field(default_factory=list)
    draft: Optional[Draft] = None
    variations: list[Variation] = field(default_factory=list)
    remember: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    done: bool = False
    notices: list[str] = field(default_factory=list)  # what was left out, and why, in words for the person
    plain: bool = False  # the answer could not be read as the structure asked for: `reply` is the model's text as written, and nothing else is kept

    def to_content(self) -> dict[str, Any]:
        """The message as it is stored and sent to the page (the `content` of a chat message)."""
        return {
            "reply": self.reply, "title": self.title,
            "questions": [{"id": q.id, "text": q.text, "chips": list(q.chips)} for q in self.questions],
            "draft": None if self.draft is None else {"prompt": self.draft.prompt, "negative": self.draft.negative, "settings": dict(self.draft.settings), "note": self.draft.note},
            "variations": [{"label": v.label, "prompt": v.prompt, "negative": v.negative} for v in self.variations],
            "remember": list(self.remember), "tags": list(self.tags), "done": self.done, "notices": list(self.notices), "plain": self.plain,
        }


# ------------------------------------------------------------------ the schema the model is given (Ollama's `format`)
def _settings_schema(kind: str) -> dict[str, Any]:
    number, integer, text, flag = {"type": "number"}, {"type": "integer"}, {"type": "string"}, {"type": "boolean"}
    if kind == "vector":
        props: dict[str, Any] = {"kind": {"type": "string", "enum": list(V.KINDS)}, "colours": integer, "palette": {"type": "array", "items": text},
                                 "width_mm": number, "height_mm": number}
    elif kind == "music":
        props = {"fields": {"type": "object", "properties": {name: text for name in MUSIC_FIELDS}}, "instrumental": flag, "lyrics": text, "seconds": integer}
    else:
        shapes = list(P.ASPECT_RATIOS) + (["auto"] if kind == "edit" else [])
        props = {"shape": {"type": "string", "enum": shapes}, "guidance": number, "steps": integer}
    return {"type": "object", "properties": props}


def answer_schema(kind: str, *, variations: bool = False) -> dict[str, Any]:
    """The JSON schema sent as `format`. `reply` is FIRST: the model writes properties in the order given, and the page shows the reply as it grows."""
    text = {"type": "string"}
    draft = {"type": "object", "properties": {"prompt": text, "negative": text, "settings": _settings_schema(kind), "note": text}, "required": ["prompt"]}
    properties: dict[str, Any] = {
        "reply": text,
        "title": text,
        "questions": {"type": "array", "items": {"type": "object", "properties": {"id": text, "text": text, "chips": {"type": "array", "items": text}},
                                                 "required": ["id", "text", "chips"]}},
        "draft": draft,
    }
    if variations:
        properties["variations"] = {"type": "array", "items": {"type": "object", "properties": {"label": text, "prompt": text, "negative": text}, "required": ["label", "prompt"]}}
    properties.update({"remember": {"type": "array", "items": text}, "tags": {"type": "array", "items": text}, "done": {"type": "boolean"}})
    return {"type": "object", "properties": properties, "required": ["reply"]}


# ------------------------------------------------------------------ cleaning
def clean(text: Any, limit: Optional[int] = None, *, lines: bool = False) -> str:
    """Text from the model as plain text: control characters removed, and (unless `lines`) every run of white space made one space; at most `limit` characters."""
    if not isinstance(text, str):
        return ""
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = text.strip() if lines else " ".join(text.split())
    return text[:limit].rstrip() if limit is not None and len(text) > limit else text


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf") else None


def _show(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# ------------------------------------------------------------------ the settings of a draft, checked as a run is
def check_settings(kind: str, raw: Any, limits: Limits) -> tuple[dict[str, Any], list[str]]:
    """The settings the model suggested, with only the valid ones kept, and a sentence for each that was left out. What the keys are depends on what the
    prompt is For (DESIGN.md section 37.5). A key that is not one of them is ignored without a word: it is not a suggestion the person can see."""
    if not isinstance(raw, Mapping):
        return {}, []
    kept: dict[str, Any] = {}
    notices: list[str] = []

    def out(name: str, value: Any, why: str) -> None:
        shown = clean(value, 40) if isinstance(value, str) else (_show(value) if _number(value) is not None else "that")
        notices.append(f"The builder suggested {name} {shown}; {why}, so it was left out.")

    def in_range(name: str, key: str, low: float, high: float, as_int: bool = False) -> None:
        if key not in raw:
            return
        value = _number(raw[key])
        if value is None or (as_int and not value.is_integer()):
            out(name, raw[key], f"{name} must be a {'whole ' if as_int else ''}number")
        elif not low <= value <= high:
            out(name, _show(value), f"the studio allows {_show(low)} to {_show(high)}")
        else:
            kept[key] = int(value) if as_int else value

    if kind in ("images", "edit"):
        if "shape" in raw:
            shape = clean(raw["shape"], 20)
            allowed = list(P.ASPECT_RATIOS) + (["auto"] if kind == "edit" else [])
            if shape in allowed:
                kept["shape"] = shape
            else:
                out("shape", shape, f"the studio's shapes are {', '.join(allowed)}")
        in_range("guidance", "guidance", P.CFG_MIN, P.CFG_MAX)
        in_range("steps", "steps", P.STEPS_MIN, max(P.STEPS_MIN, P.STEPS_MAX), as_int=True)
    elif kind == "vector":
        if "kind" in raw:
            value = clean(raw["kind"], 20)
            if value in V.KINDS:
                kept["kind"] = value
            else:
                out("kind", value, f"the kinds are {', '.join(V.KINDS)}")
        in_range("colours", "colours", V.COLOURS_MIN, V.COLOURS_MAX, as_int=True)
        if "palette" in raw:
            palette = raw["palette"]
            colours = kept.get("colours", V.COLOURS_MAX)
            if not isinstance(palette, list) or not all(isinstance(c, str) and _HEX.match(c.strip()) for c in palette):
                notices.append("The builder suggested inks that are not #RRGGBB colours, so they were left out.")
            elif len(palette) > colours - 1:
                notices.append(f"The builder suggested {len(palette)} inks for {colours} colours; white is the bare material and counts, so at most {colours - 1} are allowed and the inks were left out.")
            else:
                kept["palette"] = [c.strip().upper() for c in palette]
        in_range("width_mm", "width_mm", V.SIZE_MM_MIN, V.SIZE_MM_MAX)
        in_range("height_mm", "height_mm", V.SIZE_MM_MIN, V.SIZE_MM_MAX)
    elif kind == "music":
        if isinstance(raw.get("fields"), Mapping):
            values: dict[str, str] = {}
            for name in MUSIC_FIELDS:  # the six, in the page's order; anything else is ignored
                value = raw["fields"].get(name)
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    text = clean(str(value), P.MUSIC_FIELD_MAX)
                    if text:
                        values[name] = text
            if values:
                kept["fields"] = values
        if "instrumental" in raw and isinstance(raw["instrumental"], bool):
            kept["instrumental"] = raw["instrumental"]
        if "lyrics" in raw:
            lyrics = clean(raw["lyrics"], lines=True)
            if len(lyrics) > P.MUSIC_LYRICS_MAX:
                notices.append(f"The builder wrote lyrics of {len(lyrics)} characters; the studio allows up to {P.MUSIC_LYRICS_MAX}, so they were left out.")
            elif lyrics:
                kept["lyrics"] = lyrics
        in_range("seconds", "seconds", P.MUSIC_DURATION_MIN, limits.music_max_seconds, as_int=True)
    return kept, notices


# ------------------------------------------------------------------ reading an answer
def _load(text: str) -> Any:
    """The JSON in the model's text. A fenced block (```json … ```) and a sentence before or after the object are tolerated, because some models add them
    even when told not to; anything else is not an answer."""
    body = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(body)
    except ValueError:
        pass
    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(body[start:end + 1])
        except ValueError:
            pass
    raise BadAnswer("The answer is not JSON.")


def _question(raw: Any, number: int, taken: set[str], notices: list[str]) -> Optional[Question]:
    if not isinstance(raw, Mapping):
        return None
    text = clean(raw.get("text"), QUESTION_CHARS)
    if not text:
        return None
    qid = clean(raw.get("id"), 20) or f"q{number}"
    if qid in taken:
        qid = f"q{number}"
    while qid in taken:
        qid += "x"
    taken.add(qid)
    chips: list[str] = []
    for chip in raw.get("chips") if isinstance(raw.get("chips"), list) else []:
        value = clean(chip, None)
        if not value or len(value) > CHIP_CHARS or value.casefold() in {c.casefold() for c in chips}:
            continue
        chips.append(value)
    chips = chips[:CHIPS_MAX]
    if len(chips) < CHIPS_MIN:
        if chips or raw.get("chips"):
            notices.append(f"A question (\"{clean(text, 50)}\") came with too few suggestions, so it has none; you can answer it in words.")
        chips = []
    return Question(id=qid, text=text, chips=chips)


def _prompt_text(raw: Any, what: str, limits: Limits, notices: list[str]) -> Optional[str]:
    text = clean(raw, lines=True)
    if not text:
        return None
    if len(text) > limits.prompt_chars:
        notices.append(f"The builder wrote {what} of {len(text)} characters; the studio allows up to {limits.prompt_chars}, so it was left out.")
        return None
    return text


def read_answer(text: str, kind: str, limits: Limits, *, want_variations: bool = False, known_preferences: tuple[str, ...] = ()) -> Answer:
    """Read and check the model's text. Raises `BadAnswer` when there is no answer to keep; otherwise returns what is valid, with `notices` for what is not.
    `known_preferences` are the notes the person already has, so that a suggestion to remember one of them again is not made."""
    if kind not in FOR_KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    data = _load(text)
    if not isinstance(data, dict):
        raise BadAnswer("The answer is not an object.")
    if not isinstance(data.get("reply"), str):
        raise BadAnswer("The answer has no reply.")
    answer = Answer()
    notices = answer.notices

    reply = clean(data["reply"], lines=True)
    if len(reply) > REPLY_CHARS:
        reply = reply[:REPLY_CHARS].rstrip() + "…"
        notices.append(f"The reply was shortened to {REPLY_CHARS} characters.")
    answer.reply = reply
    title = clean(data.get("title"), TITLE_CHARS)
    answer.title = title or None

    raw_questions = data.get("questions") if isinstance(data.get("questions"), list) else []
    taken: set[str] = set()
    for raw in raw_questions:
        if len(answer.questions) >= QUESTIONS_MAX:
            break
        question = _question(raw, len(answer.questions) + 1, taken, notices)
        if question is not None:
            answer.questions.append(question)
    asked = len([q for q in raw_questions if isinstance(q, Mapping) and clean(q.get("text"))])
    if asked > QUESTIONS_MAX:
        notices.append(f"The builder asked {asked} questions; the studio shows {QUESTIONS_MAX} at a time, so the others were left out.")

    raw_draft = data.get("draft")
    if isinstance(raw_draft, Mapping):
        prompt = _prompt_text(raw_draft.get("prompt"), "a prompt", limits, notices)
        if prompt is not None:
            negative = _prompt_text(raw_draft.get("negative"), "a negative prompt", limits, notices) if kind in ("images", "edit") else None
            settings, setting_notices = check_settings(kind, raw_draft.get("settings"), limits)
            notices.extend(setting_notices)
            answer.draft = Draft(prompt=prompt, negative=negative, settings=settings, note=clean(raw_draft.get("note"), NOTE_CHARS))

    if want_variations and isinstance(data.get("variations"), list):
        for raw in data["variations"]:
            if len(answer.variations) >= VARIATIONS or not isinstance(raw, Mapping):
                continue
            prompt = _prompt_text(raw.get("prompt"), "a wording", limits, notices)
            label = clean(raw.get("label"), LABEL_CHARS)
            if prompt is None or not label:
                continue
            negative = _prompt_text(raw.get("negative"), "a negative prompt", limits, notices) if kind in ("images", "edit") else None
            answer.variations.append(Variation(label=label, prompt=prompt, negative=negative))
        if len(answer.variations) != VARIATIONS:
            notices.append(f"The builder gave {len(answer.variations)} usable wording{'' if len(answer.variations) == 1 else 's'} and not {VARIATIONS}.")

    if isinstance(data.get("remember"), list):
        have = {clean(p).casefold() for p in known_preferences}
        for raw in data["remember"]:
            value = clean(raw, PREFERENCE_CHARS)
            if value and value.casefold() not in have and len(answer.remember) < REMEMBER_MAX:
                answer.remember.append(value)
                have.add(value.casefold())

    if isinstance(data.get("tags"), list):
        for raw in data["tags"]:
            tag = normalize_tag(raw)
            if tag and tag not in answer.tags and len(answer.tags) < TAGS_MAX:
                answer.tags.append(tag)
    answer.done = data.get("done") is True

    if not (answer.reply or answer.questions or answer.draft or answer.variations):
        raise BadAnswer("The answer is empty.")
    return answer


def plain_answer(text: str) -> Answer:
    """The model's text as it wrote it, for an answer that could not be read as the structure asked for (even asked again): shown as plain text, with no chips and
    no change to the draft (criterion 227)."""
    reply = clean(text, lines=True)
    notices = []
    if len(reply) > REPLY_CHARS:
        reply = reply[:REPLY_CHARS].rstrip() + "…"
        notices.append(f"The reply was shortened to {REPLY_CHARS} characters.")
    return Answer(reply=reply, plain=True, notices=notices)


# ------------------------------------------------------------------ streaming the reply out of an answer that is still being written
def reply_so_far(buffer: str) -> Optional[str]:
    """The text of `reply` in a JSON object that has not finished arriving, as far as it has arrived; None until `reply` has begun, or when the object does not
    start with it (the model is asked to write it first; if it does not, the reply is shown when the answer is complete). A string cut in the middle of an
    escape (a backslash, or half of \\uXXXX) stops before the escape, so the page never shows half a character."""
    match = re.match(r'\s*\{\s*"reply"\s*:\s*"', buffer)
    if not match:
        return None
    out: list[str] = []
    i, n = match.end(), len(buffer)
    simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    while i < n:
        c = buffer[i]
        if c == '"':
            break
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            break
        e = buffer[i + 1]
        if e in simple:
            out.append(simple[e])
            i += 2
        elif e == "u":
            if i + 6 > n:
                break
            try:
                code = int(buffer[i + 2:i + 6], 16)
            except ValueError:
                break
            i += 6
            if 0xD800 <= code < 0xDC00:  # the first half of a pair: wait for the second
                if i + 6 > n:
                    i -= 6
                    break
                if buffer[i:i + 2] == "\\u":
                    try:
                        low = int(buffer[i + 2:i + 6], 16)
                    except ValueError:
                        break
                    if 0xDC00 <= low < 0xE000:
                        code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                        i += 6
            out.append(chr(code) if not 0xD800 <= code < 0xE000 else "�")
        else:
            out.append(e)
            i += 2
    return _CONTROL.sub("", "".join(out))
