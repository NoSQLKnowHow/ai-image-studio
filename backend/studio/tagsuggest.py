"""Suggest: tags for a run from the Prompt builder's language model (DESIGN.md §45.6).

One short call to the model that the Prompt builder already uses (the same client, the same settings). It sends WORDS ONLY (the run's own words, its note, a music run's lyrics, a reading's title
and source, the tags it has, and the person's most used tags so that the model can use their spelling), never a picture, with `think` off so that a press takes seconds, and asks for a JSON
answer of the shape `{"tags": [...]}` through Ollama's `format`. What comes back is cleaned like a typed tag, cut to the number asked, and returned; NOTHING is stored or applied here: the page
shows the suggestions and the person adds each one (decision 65). The studio never unloads Ollama's model (§37.6 choice 17).
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from .builder.ollama import OllamaError
from .builder.service import NOT_SET_UP, BuilderService
from .runsearch import clean_tags, index_fields

LYRICS_CHARS = 600  # how much of a music run's lyrics the model is shown
KNOWN_SENT = 40  # how many of the person's most used tags go with the request, so that the model can reuse their spelling

INSTRUCTIONS = (
    "You suggest short tags for one item in a person's library of generated pictures, music, drawings and readings. "
    "A tag is one or two lower-case words naming a subject, a mood, a style or a use (for example: lighthouse, dusk, flat colour, poster). "
    "Use only what the words you are given say or clearly imply; do not invent details. Prefer a tag the person already uses when it fits, spelled as they spell it. "
    "Never repeat a tag the item already has. Answer with JSON only."
)


def suggest_status(settings: Any, service: BuilderService) -> dict[str, Any]:
    """`capabilities.annotations.suggest`: {available, reason}, from the settings alone (it never asks the network). The reason is in words, for the greyed button."""
    if settings.tag_suggestions <= 0:
        return {"available": False, "reason": "Suggest is switched off (STUDIO_TAG_SUGGESTIONS is 0)."}
    if not (service.fake or service.configured):
        return {"available": False, "reason": NOT_SET_UP.replace("The Prompt builder needs an Ollama server.", "Suggest needs the Prompt builder's Ollama.")}
    return {"available": True, "reason": None}


def answer_schema(count: int) -> dict[str, Any]:
    """The JSON schema sent as Ollama's `format`: an object with a list of at most `count` short texts."""
    return {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": "string"}, "maxItems": count}}, "required": ["tags"]}


def build_messages(run: Any, tags: Sequence[str], notes: str, known: Sequence[str], count: int) -> list[dict[str, Any]]:
    """The conversation: the instructions, then one user message of words. `run` is the runs row."""
    prompt, extra = index_fields(run["mode"], run["prompt"], run["lyrics"], run["options_json"])
    kind = {"generate": "picture", "edit": "picture (an edit)", "music": "music track", "vector": "vector drawing", "speech": "reading"}.get(run["mode"], "item")
    lines = [f"Item: {kind}.", f"{'Title' if run['mode'] == 'speech' else 'Words'}: {prompt}"]
    if extra:
        lines.append(f"{'Source' if run['mode'] == 'speech' else 'Lyrics'}: {extra[:LYRICS_CHARS]}")
    if notes:
        lines.append(f"The person's note: {notes}")
    lines.append(f"Tags it already has: {', '.join(tags) if tags else '(none)'}")
    if known:
        lines.append(f"Tags the person uses elsewhere: {', '.join(known[:KNOWN_SENT])}")
    lines.append(f"Suggest up to {count} tags.")
    return [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": "\n".join(lines)}]


def read_tags(text: str, have: Sequence[str], count: int) -> list[str]:
    """The tags in the model's answer: cleaned as a typed tag is, each once, without the ones the run has, at most `count`. An answer that is not the asked shape raises
    `OllamaError('bad_answer')` (the model said something else)."""
    try:
        data = json.loads(text)
        raw = data["tags"]
        if not isinstance(raw, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise OllamaError("bad_answer", "The model's answer could not be read as tags.", "Try again.") from None
    return [tag for tag in clean_tags(raw) if tag not in have][:count]


def suggest(service: BuilderService, run: Any, tags: Sequence[str], notes: str, known: Sequence[str], count: int) -> list[str]:
    """Ask the model and return the suggestions. Blocking (the route runs it in a thread). Raises OllamaError."""
    messages = build_messages(run, tags, notes, known, count)
    model = service.model_for(False)  # words only: the main model, never the vision model
    call = service.chat(model, messages, think=False, schema=answer_schema(count))
    text = "".join(piece.text for piece in call if piece.kind == "content")
    return read_tags(text, tags, count)
