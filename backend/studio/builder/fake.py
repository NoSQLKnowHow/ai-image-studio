"""A scripted model for `STUDIO_BUILDER_LLM=fake` (DESIGN.md section 37.4, criterion 252): the page's tests and the browser tests talk to this instead of a real
model, as `STUDIO_PIPELINE=fake` stands in for the image model. It answers the same way every time, in the structure the real model is asked for, streams its
answer in small pieces, and thinks aloud when it is told to think.

It can be made to misbehave, from the words of the person's message, so that every failure path can be reached without a real model:

    [[error:KIND]]   fail as Ollama would: unreachable, timeout, model_missing, out_of_memory or refused
    [[bad]]          the first try is not JSON when thinking is on; the try with thinking off is fine (the ask-again path, criterion 257)
    [[bad-once]]     the first try of this message is not JSON, whatever the setting, and any later try is fine
    [[bad-always]]   neither try is JSON (the plain-text fallback, criterion 227)
    [[slow]]         write slowly, so that Stop has something to stop
    [[bad-settings]] suggest guidance 40 and a shape that does not exist (the dropped-suggestion path)
    [[many]]         ask five questions;   [[long]] a reply over the limit;   [[markup]] a reply full of markup
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Iterator, Optional

from .ollama import ChatPiece, OllamaError

CHUNK = 14  # characters of the answer in each piece, so that the reply visibly grows
SLOW_PIECES = 60  # a slow answer is this many pieces, 50 ms apart: three seconds
_MARKER = re.compile(r"\[\[([a-z-]+)(?::([a-z_]+))?\]\]")
_KIND = re.compile(r"This prompt is for: ([a-z]+)\.")

ERRORS = {
    "unreachable": ("Can't reach the Ollama server.", "Check that Ollama is running and that STUDIO_OLLAMA_URL is right."),
    "timeout": ("Ollama stopped answering.", "Try again, or raise STUDIO_OLLAMA_TIMEOUT_S."),
    "model_missing": ("Ollama doesn't have the model.", "Pull it on the Ollama machine."),
    "out_of_memory": ("Ollama ran out of memory.", "Unload a model and try again."),
    "refused": ("Ollama refused the request.", None),
}

SETTINGS = {
    "images": {"shape": "16:9", "guidance": 4}, "edit": {"shape": "auto", "guidance": 4},
    "vector": {"kind": "flat", "colours": 3}, "music": {"fields": {"genre": "jazz", "mood": "warm"}, "instrumental": True, "seconds": 60},
}


def _answers_of(users: list[dict[str, Any]]) -> list[str]:
    """The chips and words the person has given after their first line, in order, from the rendered form of their turns (`context.render_user`)."""
    given: list[str] = []
    for number, message in enumerate(users):
        body = re.sub(r"\n*\[From the studio:.*?\]\s*$", "", message["content"], flags=re.S)
        lines = body.split("\n")
        if "My answers to your questions:" in lines:
            at = lines.index("My answers to your questions:")
            answers = [re.sub(r'^- ".*?": ', "", line) for line in lines[at + 1:] if line.startswith("- ")]
            lines = lines[:at]
        else:
            answers = []
        words = [line for line in lines if line.strip()]
        given += (words[1:] if number == 0 else words) + answers
    return [g for g in given if g and not g.startswith("Surprise me") and not g.startswith("Skip")]


def script(messages: list[dict[str, Any]], think: Optional[bool], schema: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The answer, as a dict, for this conversation. The same conversation always gets the same answer."""
    system = messages[0]["content"]
    users = [m for m in messages[1:] if m["role"] == "user"]
    # Suggest tags for a run (DESIGN.md §45.6): the same answer for the same words: the first three longer words of the item's own words, and a tag that says it was scripted
    if schema and set(schema.get("properties", {})) == {"tags"}:
        found = re.search(r"^(?:Words|Title): (.*)$", users[-1]["content"], flags=re.M)
        picked = [w for w in re.findall(r"[a-z]{4,}", (found.group(1) if found else "").lower())]
        return {"tags": [*dict.fromkeys(picked)][:3] + ["scripted"]}
    last = users[-1]["content"]
    kind = (_KIND.search(system) or [None, "images"])[1]
    markers = {m.group(1): m.group(2) for m in _MARKER.finditer(last)}
    idea = _MARKER.sub("", users[0]["content"]).strip().split("\n")[0][:200] or "an idea"
    asked = len(users)
    pictures = sum(len(m.get("images") or []) for m in messages)
    seen = f" I can see {pictures} picture{'s' if pictures != 1 else ''}." if pictures else ""
    wants_variations = bool(schema and "variations" in schema.get("properties", {}))
    write_now = "Write it now" in last
    words = " ".join(idea.split()[:5]).capitalize()

    chips = ["Dusk", "Midnight", "Dawn", "A stormy afternoon"]
    if "many" in markers:
        questions = [{"id": f"q{n}", "text": f"Question {n}?", "chips": ["One", "Two", "Three"]} for n in range(1, 6)]
    elif asked == 1:
        questions = [{"id": "q1", "text": "What time of day?", "chips": chips}, {"id": "q2", "text": "What style should it have?", "chips": ["Photograph", "Watercolour", "Flat illustration"]}]
    elif asked == 2:
        questions = [{"id": "q1", "text": "Which colours?", "chips": ["Warm", "Cold", "Muted", "Bright"]}]
    else:
        questions = []
    finished = write_now or asked >= 3 or wants_variations
    if finished and "many" not in markers:
        questions = []

    extra = _answers_of(users)
    prompt = idea + (", " + ", ".join(extra) if extra else "") + "."
    settings = dict(SETTINGS.get(kind, {}))
    if "bad-settings" in markers:
        settings = {**settings, "guidance": 40, "shape": "21:9"} if kind in ("images", "edit") else {**settings, "colours": 99}
    reply = {
        True: "Here is the finished prompt." + seen,
        False: ("Lovely idea. A few questions." if asked == 1 else "Thank you. One more thing.") + seen,
    }[finished]
    if "long" in markers:
        reply = "x" * 2100
    if "markup" in markers:
        reply = "<b>bold</b> <script>alert(1)</script> [link](http://example.com)"
    answer: dict[str, Any] = {"reply": reply, "title": words, "questions": questions,
                              "draft": {"prompt": prompt, "settings": settings, "note": "added " + (extra[-1] if extra else "the idea")}}
    if kind in ("images", "edit"):
        answer["draft"]["negative"] = "blurry"
    if wants_variations:
        answer["variations"] = [{"label": label, "prompt": f"{prompt[:-1]}, {label}."} for label in ("simpler", "more cinematic", "more detailed")]
    if finished:
        answer["tags"] = ["scripted", kind]
        answer["done"] = True
    if asked == 2 and "remember" not in markers:
        answer["remember"] = ["photographic, natural light"]
    return answer


class FakeCall:
    """What `OllamaClient.chat` returns, for the scripted model: iterate for the pieces; `cancel()` from another thread stops it."""

    def __init__(self, messages: list[dict[str, Any]], think: Optional[bool], schema: Optional[dict[str, Any]], tried: bool = True) -> None:
        self.messages, self.think, self.schema, self.tried = messages, think, schema, tried
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def __iter__(self) -> Iterator[ChatPiece]:
        last = self.messages[-1]["content"]
        markers = {m.group(1): m.group(2) for m in _MARKER.finditer(last)}
        if "error" in markers:
            message, hint = ERRORS.get(markers["error"] or "", ERRORS["refused"])
            raise OllamaError(markers["error"] if markers["error"] in ERRORS else "refused", message, hint)
        slow = "slow" in markers
        if self.think is True:
            for text in ("Considering the idea. ", "Weighing the questions. "):
                self._tick(slow)
                yield ChatPiece("thinking", text)
        if "bad-always" in markers or ("bad" in markers and self.think is not False) or ("bad-once" in markers and not self.tried):
            self._tick(slow)
            yield ChatPiece("content", "I think you want something lovely, and I would be glad to help with that.")
            yield ChatPiece("done", info={})
            return
        text = json.dumps(script(self.messages, self.think, self.schema), ensure_ascii=False)
        pieces = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
        if slow:
            pieces = (pieces + [""] * SLOW_PIECES)[:max(len(pieces), SLOW_PIECES)]
        for piece in pieces:
            self._tick(slow)
            if piece:
                yield ChatPiece("content", piece)
        yield ChatPiece("done", info={})

    def _tick(self, slow: bool) -> None:
        if self._stop.is_set():
            raise OllamaError("stopped", "The reply was stopped.")
        if slow:
            time.sleep(0.05)
        if self._stop.is_set():
            raise OllamaError("stopped", "The reply was stopped.")


class FakeModel:
    """The scripted model, with the one call the engine uses. It remembers which messages it has already been sent, for `[[bad-once]]`."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def chat(self, model: str, messages: list[dict[str, Any]], *, think: Optional[bool] = None, schema: Optional[dict[str, Any]] = None) -> FakeCall:
        key = json.dumps(messages[-1]["content"])
        with self._lock:
            tried = key in self._seen
            self._seen.add(key)
        return FakeCall(messages, think, schema, tried)
