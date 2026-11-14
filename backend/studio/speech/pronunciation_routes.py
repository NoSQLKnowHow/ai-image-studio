"""The routes of the pronunciation list (DESIGN.md §44.3, §44.6): the entries, and Try it.

The list is one list for every engine, voice and reading. These routes only keep it and show what it would do to a sentence; a reading takes its own copy of the
entries it uses when it is made (`service.py`), so nothing here can change a reading that exists. Nothing in this file names an engine.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..serialize import utcnow
from .pronounce import ENTRIES_MAX, TRY_CHARS, Entry, EntryProblem, Matcher, check_say, check_word, word_key
from .routes import error

ENTRY_ID = re.compile(r"^[0-9a-f]{32}$")


class EntryBody(BaseModel):
    """A new entry. The words are checked by `pronounce.check_word` and `check_say`, which say what is wrong in words; the length limits here only keep a request small."""

    model_config = ConfigDict(extra="forbid", strict=True)
    word: str = Field(max_length=1000)
    say: str = Field(max_length=1000)
    match_case: bool = False


class EntryChange(BaseModel):
    """What to change in an entry: any of the three; what is left out stays as it is."""

    model_config = ConfigDict(extra="forbid", strict=True)
    word: Optional[str] = Field(default=None, max_length=1000)
    say: Optional[str] = Field(default=None, max_length=1000)
    match_case: Optional[bool] = None


class TryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(max_length=TRY_CHARS)


def shown(row: Any) -> dict[str, Any]:
    """An entry as the page sees it."""
    return {"id": row["id"], "word": row["word"], "say": row["say"], "match_case": bool(row["match_case"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}


def add_routes(app: FastAPI) -> None:
    def store_of(request: Request):
        return request.app.state.jobs.speech.store

    def problem(exc: EntryProblem):
        return error(422, exc.message, exc.code, None, exc.field)

    taken = lambda word: error(409, f"“{word}” is already on the list. Change that entry instead.", "word_taken", None, "word")  # noqa: E731

    @app.get("/api/speech/pronunciations")
    async def list_entries(request: Request) -> Any:
        """Every entry, by word. The page reads the limits from `capabilities.limits.speech`."""
        rows = await asyncio.to_thread(store_of(request).pronunciations)
        return {"entries": [shown(r) for r in rows]}

    @app.post("/api/speech/pronunciations", status_code=201)
    async def add_entry(body: EntryBody, request: Request) -> Any:
        try:
            word = check_word(body.word)
            say = check_say(body.say, word)
        except EntryProblem as exc:
            return problem(exc)
        now = utcnow()
        row = {"id": secrets.token_hex(16), "word": word, "word_key": word_key(word), "say": say, "match_case": int(body.match_case), "created_at": now, "updated_at": now}
        outcome = await asyncio.to_thread(store_of(request).insert_pronunciation, row, ENTRIES_MAX)
        if outcome == "word_taken":
            return taken(word)
        if outcome == "full":
            return error(409, f"The list holds {ENTRIES_MAX:,} entries and is full. Delete one you no longer need.", "full")
        return shown(row)

    @app.patch("/api/speech/pronunciations/{entry_id}")
    async def change_entry(entry_id: str, body: EntryChange, request: Request) -> Any:
        store = store_of(request)
        current = await asyncio.to_thread(store.pronunciation, entry_id) if ENTRY_ID.match(entry_id) else None
        if current is None:
            return error(404, "That entry is not on the list (it may have been deleted).", "not_found")
        try:
            word = check_word(body.word if body.word is not None else current["word"])
            say = check_say(body.say if body.say is not None else current["say"], word)
        except EntryProblem as exc:
            return problem(exc)
        match_case = bool(current["match_case"]) if body.match_case is None else body.match_case
        outcome = await asyncio.to_thread(store.update_pronunciation, entry_id, word, word_key(word), say, match_case, utcnow())
        if outcome == "word_taken":
            return taken(word)
        if outcome == "not_found":  # deleted between the read and the write
            return error(404, "That entry is not on the list (it may have been deleted).", "not_found")
        return shown(await asyncio.to_thread(store.pronunciation, entry_id))

    @app.delete("/api/speech/pronunciations/{entry_id}", status_code=204)
    async def delete_entry(entry_id: str, request: Request) -> Response:
        gone = ENTRY_ID.match(entry_id) and await asyncio.to_thread(store_of(request).delete_pronunciation, entry_id)
        return Response(status_code=204) if gone else error(404, "That entry is not on the list (it may have been deleted).", "not_found")

    @app.post("/api/speech/pronunciations/try")
    async def try_it(body: TryBody, request: Request) -> Any:
        """What would be spoken for a text, with the list as it is now, and which entries changed it (DESIGN.md §44.3): the words only, no engine and no sound."""
        rows = await asyncio.to_thread(store_of(request).pronunciations)
        matcher = Matcher(Entry(r["word"], r["say"], bool(r["match_case"])) for r in rows)
        applied = matcher.apply(body.text)
        used = [{"word": matcher.entries[i].word, "say": matcher.entries[i].say, "count": n} for i, n in sorted(applied.counts.items())]
        return {"spoken": applied.spoken, "changed": applied.spoken != body.text, "used": used}
