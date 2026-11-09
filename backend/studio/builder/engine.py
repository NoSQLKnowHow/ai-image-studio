"""One reply of the Prompt builder, from the person's message to the saved answer (DESIGN.md section 37.3 rules 6-11, 18; criteria 224-228, 232-234, 255-257).

`BuilderEngine.reply` is a blocking generator of `Event`s; the route runs it in a thread and sends the events to the page as server-sent events. In order:

1. The person's message is SAVED FIRST. Whatever happens next (Ollama gone, a time-out, a refusal), the chat has what they said, and Retry sends it again (rule 10).
2. What the model is sent is assembled from the instruction files, the chat so far and the current draft (`instructions`, `context`); the oldest turns are dropped, never the draft.
3. The reply streams: the reasoning arrives as `thinking` events (shown, never stored), the reply's own words as `delta` events with the text so far.
4. The answer is read and checked (`answer.read_answer`). One that cannot be read is asked again ONCE with thinking off, whatever the setting; one that still cannot be read is
   kept as plain text, with no chips and no change to the draft. A part of an answer that breaks the studio's rules is dropped and the reply says so.
5. The answer is saved. A reply that was STOPPED is saved as far as it got, marked, with no change to the draft; a reply whose connection simply closed is not saved.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional, Sequence

from ..config import Settings
from ..serialize import utcnow
from ..storage import Storage
from .answer import Answer, BadAnswer, Limits, answer_schema, plain_answer, read_answer
from .context import Turn, build_messages
from .instructions import INTENTS, Example, pictures_note, system_prompt
from .ollama import OllamaError
from .service import BuilderService
from .store import BuilderStore

log = logging.getLogger("studio.builder")

TITLE_FROM_WORDS = 60  # a new chat is named from the first words of the idea, until the model gives it a title
DEFAULT_TITLE = "New chat"  # what a chat made with no name is called; like the first words, a name the person did not choose
SIMILAR_MAX = 3  # DESIGN.md section 37.3 rule 8
ANSWER_MODES = ("chips", "surprise", "skip")


@dataclass
class ReplyRequest:
    """What the page sends with a message (already checked by the route's model)."""

    text: str = ""
    answers: Sequence[Mapping[str, Any]] = ()  # [{id, chips: [...], mode: chips|surprise|skip}]
    intent: str = "message"
    draft: Optional[Mapping[str, Any]] = None  # the draft as the person has it now (edited by hand, perhaps)
    think: Optional[bool] = None
    attachments: Sequence[str] = ()  # ids of pictures waiting in this chat, in order
    similar: bool = True  # look at the person's saved prompts on a first reply (rule 8)
    exclude: Sequence[str] = ()  # saved prompts the person left out
    retry: bool = False  # send the last message again; it adds no message of its own
    run_note: str = ""  # what a Refine knows about the run (its settings and seed); said to the model with the result


@dataclass
class Event:
    name: str
    data: dict[str, Any] = field(default_factory=dict)


class ReplyJob:
    """The handle on a reply that is being written: Stop (keeping what was written), or closing the connection (keeping nothing). Safe from any thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.keep = True
        self.call: Any = None
        self.partial = ""  # what the model has written in the try that is under way (not the reasoning), for a reply that is stopped
        self.started = time.monotonic()

    def stop(self, keep: bool = True) -> None:
        with self._lock:
            if not self._stop.is_set():
                self.keep = keep
            self._stop.set()
            call = self.call
        if call is not None:
            call.cancel()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def attach(self, call: Any) -> None:
        with self._lock:
            self.call = call
        if self._stop.is_set():
            call.cancel()


def message_payload(row: Any, attachments: Sequence[Any] = ()) -> dict[str, Any]:
    """A stored message as the page gets it."""
    return {
        "seq": row["seq"], "role": row["role"], "text": row["text"], "content": json.loads(row["content"]) if row["content"] else None,
        "model": row["model"], "created_at": row["created_at"],
        "attachments": [{"id": a["id"], "url": f"/api/builder/attachments/{a['id']}", "width": a["width"], "height": a["height"], "name": a["name"]} for a in attachments],
    }


class BuilderEngine:
    def __init__(self, settings: Settings, service: BuilderService, store: BuilderStore, storage: Storage) -> None:
        self.settings, self.service, self.store, self.storage = settings, service, store, storage

    # ------------------------------------------------------------------ the person's message
    def _filled_answers(self, chat_id: str, answers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Each answer with the text of its question, which the model is told (the page sends only the id); an id the last reply did not ask is ignored."""
        asked: dict[str, str] = {}
        for row in reversed(self.store.messages(chat_id)):
            if row["role"] == "assistant" and row["content"]:
                asked = {q["id"]: q["text"] for q in json.loads(row["content"]).get("questions", [])}
                break
        filled = []
        for answer in answers:
            qid, mode = str(answer.get("id", "")), answer.get("mode", "chips")
            if qid not in asked or mode not in ANSWER_MODES:
                continue
            chips = [str(c) for c in answer.get("chips", [])] if mode == "chips" else []
            if mode == "chips" and not chips:
                continue
            filled.append({"id": qid, "question": asked[qid], "mode": mode, "chips": chips})
        return filled

    def _turns(self, chat_id: str) -> list[Turn]:
        by_message: dict[int, list[str]] = {}
        for row in self.store.attachments(chat_id):
            if row["message_seq"]:
                try:
                    by_message.setdefault(row["message_seq"], []).append(base64.b64encode(self.storage.abs(row["path"]).read_bytes()).decode("ascii"))
                except (OSError, ValueError):
                    log.warning("a picture of a chat is missing from the disk")
        return [Turn(role=m["role"], text=m["text"], content=json.loads(m["content"]) if m["content"] else None, images=by_message.get(m["seq"], ())) for m in self.store.messages(chat_id)]

    # ------------------------------------------------------------------ one call to the model
    def _call(self, job: ReplyJob, model: str, messages: list[dict[str, Any]], think: Optional[bool], schema: dict[str, Any], started: float) -> Iterator[Event]:
        """Stream one try. Yields `thinking`, `delta` and `first` events; returns the whole text the model wrote (what is in `content`, not the reasoning)."""
        from .answer import reply_so_far

        call = self.service.chat(model, messages, think=think, schema=schema)
        job.attach(call)
        job.partial = ""
        buffer, shown, thought = "", "", False
        for piece in call:
            if job.stopped:
                raise OllamaError("stopped", "The reply was stopped.")
            if piece.kind == "thinking":
                thought = True
                yield Event("thinking", {"text": piece.text, "seconds": round(time.monotonic() - started, 1)})
            elif piece.kind == "content":
                buffer += piece.text
                job.partial = buffer
                now = reply_so_far(buffer)
                if now is not None and now != shown:
                    shown = now
                    yield Event("delta", {"text": shown, "thought": thought})
        return buffer

    # ------------------------------------------------------------------ the reply
    def reply(self, chat_id: str, req: ReplyRequest, job: ReplyJob) -> Iterator[Event]:
        chat = self.store.get_chat(chat_id)
        kind, detail, project = chat["for_kind"], chat["detail"], chat["project_id"]
        if req.retry:
            messages = self.store.messages(chat_id)
            if not messages or messages[-1]["role"] != "user":
                raise OllamaError("refused", "There is nothing to retry.", None)
            user_seq = messages[-1]["seq"]
        else:
            content = {"answers": self._filled_answers(chat_id, req.answers), "intent": req.intent, "draft": dict(req.draft) if req.draft else None, "think": req.think}
            user_seq = self.store.add_message(chat_id, now=utcnow(), role="user", text=req.text.strip(), content=content)
            self.store.send_attachments(chat_id, req.attachments, user_seq)
        started = time.monotonic()
        yield Event("start", {"seq": user_seq})

        stored = self.store.messages(chat_id)
        first_reply = not any(m["role"] == "assistant" for m in stored)
        last_content = json.loads(stored[-1]["content"]) if stored[-1]["content"] else {}
        intent = last_content.get("intent", req.intent) if req.retry else req.intent
        draft = last_content.get("draft") if (req.retry or req.draft) else None
        draft = draft or (dict(req.draft) if req.draft else None) or self.store.last_draft(chat_id)

        preferences = self.store.active_preferences(project)
        examples: list[dict[str, Any]] = []
        if req.similar and (first_reply or intent == "flesh_out"):
            first_words = next((m["text"] for m in stored if m["role"] == "user"), "")
            examples = self.store.similar_prompts(first_words, project_id=project, exclude=req.exclude, limit=SIMILAR_MAX)
        turns = self._turns(chat_id)
        sent = self.store.attachments(chat_id)
        mine = [a for a in sent if a["message_seq"] == user_seq]
        pictures = pictures_note(len(mine), [a["name"] for a in mine if a["name"]], self.service.can_see(bool(mine))) if mine else ""
        system = system_prompt(kind, self.settings, detail=detail, preferences=preferences, draft=draft, pictures=pictures, run_note=req.run_note,
                               examples=[Example(e["title"], e["for_kind"], e["text"] or "", e["negative"]) for e in examples])
        built = build_messages(system, turns, intent_line=INTENTS.get(intent, ""), num_ctx=self.settings.ollama_num_ctx)
        info = {"preferences": len(preferences), "examples": [{"id": e["id"], "title": e["title"]} for e in examples], "dropped": built.dropped}
        yield Event("context", info)

        model = self.service.model_for(bool(any(m.get("images") for m in built.messages)))
        schema = answer_schema(kind, variations=intent == "variations")
        limits, known = Limits.of(self.settings), tuple(self.store.all_preference_texts())
        text = ""
        try:
            text = yield from self._call(job, model, built.messages, self.service.think(req.think), schema, started)
            try:
                answer = read_answer(text, kind, limits, want_variations=intent == "variations", known_preferences=known)
            except BadAnswer:
                yield Event("retry", {})
                again = yield from self._call(job, model, built.messages, False, schema, time.monotonic())  # thinking off, whatever the setting (criterion 257)
                text = again or text
                try:
                    answer = read_answer(again, kind, limits, want_variations=intent == "variations", known_preferences=known)
                except BadAnswer:
                    if not text.strip():
                        raise OllamaError("bad_answer", "The model gave an empty answer.", "Try again, or turn Think off for this message.")
                    answer = plain_answer(text)
        except OllamaError as exc:
            if exc.kind != "stopped":
                raise
            yield from self._stopped(chat_id, job, model, info)
            return
        yield self._saved(chat_id, answer, model, info, first_reply)

    def _saved(self, chat_id: str, answer: Answer, model: str, info: dict[str, Any], first_reply: bool) -> Event:
        content = {**answer.to_content(), "context": info}
        seq = self.store.add_message(chat_id, now=utcnow(), role="assistant", text=answer.reply, content=content, model=model)
        if first_reply:
            chat = self.store.get_chat(chat_id)
            first = " ".join(next((m["text"] for m in self.store.messages(chat_id) if m["role"] == "user"), "").split())[:TITLE_FROM_WORDS]
            if chat["title"] in (DEFAULT_TITLE, first):  # a name nobody chose; a chat the person named keeps their name
                better = answer.title if answer.title and not answer.plain else first
                if better and better != chat["title"]:
                    self.store.update_chat(chat_id, now=utcnow(), title=better)
        row = next(m for m in self.store.messages(chat_id) if m["seq"] == seq)
        return Event("result", {"message": message_payload(row), "chat": self._chat(chat_id)})

    def _stopped(self, chat_id: str, job: ReplyJob, model: str, info: dict[str, Any]) -> Iterator[Event]:
        """Stop keeps what was written, marked, and changes nothing else; closing the connection keeps nothing."""
        from .answer import reply_so_far

        if not job.keep:
            return
        written = (reply_so_far(job.partial) or "").strip()
        if not written:
            yield Event("stopped", {"message": None})
            return
        content = {"reply": written, "title": None, "questions": [], "draft": None, "variations": [], "remember": [], "tags": [], "done": False, "notices": [],
                   "plain": False, "stopped": True, "context": info}
        seq = self.store.add_message(chat_id, now=utcnow(), role="assistant", text=written, content=content, model=model)
        row = next(m for m in self.store.messages(chat_id) if m["seq"] == seq)
        yield Event("stopped", {"message": message_payload(row), "chat": self._chat(chat_id)})

    def _chat(self, chat_id: str) -> dict[str, Any]:
        row = self.store.get_chat(chat_id)
        return {k: row[k] for k in ("id", "title", "for_kind", "project_id", "detail", "prompt_id", "created_at", "updated_at", "deleted_at")}
