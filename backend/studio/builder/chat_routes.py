"""The chat routes of the Prompt builder (DESIGN.md section 37.4): chats, their messages (streamed), Stop, and the pictures attached to a chat.

A reply is written by `engine.BuilderEngine` in a thread and sent here as server-sent events: `waiting` (another reply is being written; this one is next), `busy` (an image
is being made, so this may be slower), `start`, `context`, `thinking`, `delta` (the reply's text so far), `retry` (the answer was not in the structure asked for, asking again),
then `result`, or `stopped`, or `error`. Closing the connection stops the reply and keeps none of it; **Stop** (its own route) stops it and keeps what was written (rule 6).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..events import format_sse
from ..inputs import UploadError
from ..serialize import utcnow
from . import CHIP_CHARS, CHIPS_MAX, QUESTIONS_MAX, TITLE_CHARS
from .engine import DEFAULT_TITLE, TITLE_FROM_WORDS, BuilderEngine, Event, ReplyJob, ReplyRequest, message_payload
from .files import MAX_PER_MESSAGE, media_type, remove_chat_files, remove_picture, save_picture
from .instructions import INTENTS
from .ollama import OllamaError
from .routes import STATUS_FOR, not_set_up
from .schema import DETAILS, FOR_KINDS

log = logging.getLogger("studio.builder")
WORDS_MAX = 4000  # the longest message a person may send


def error(status: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


class ChatCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    title: Optional[str] = Field(default=None, max_length=TITLE_CHARS)
    for_: Literal["images", "vector", "music", "edit"] = Field(default="images", alias="for")
    project_id: Optional[str] = None
    detail: Literal["short", "normal", "rich"] = "normal"
    prompt_id: Optional[str] = None


class ChatPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    title: Optional[str] = Field(default=None, min_length=1, max_length=TITLE_CHARS)
    for_: Optional[Literal["images", "vector", "music", "edit"]] = Field(default=None, alias="for")
    project_id: Optional[str] = None
    detail: Optional[Literal["short", "normal", "rich"]] = None


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(max_length=40)
    chips: list[str] = Field(default_factory=list, max_length=CHIPS_MAX + 1)
    mode: Literal["chips", "surprise", "skip"] = "chips"


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    prompt: str = ""
    negative: Optional[str] = None
    settings: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(default="", max_length=WORDS_MAX)
    answers: list[AnswerIn] = Field(default_factory=list, max_length=QUESTIONS_MAX)
    intent: Literal["message", "write_now", "variations", "flesh_out", "refine", "describe", "help_edit"] = "message"
    draft: Optional[DraftIn] = None
    think: Optional[bool] = None
    attachments: list[str] = Field(default_factory=list, max_length=MAX_PER_MESSAGE)
    similar: bool = True
    exclude: list[str] = Field(default_factory=list, max_length=20)
    retry: bool = False
    run_id: Optional[str] = None


class FromImage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    image_id: str


def chat_payload(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "title": row["title"], "for": row["for_kind"], "project_id": row["project_id"], "detail": row["detail"], "prompt_id": row["prompt_id"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "deleted_at": row["deleted_at"]}


def attachment_payload(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "url": f"/api/builder/attachments/{row['id']}", "width": row["width"], "height": row["height"], "name": row["name"], "message_seq": row["message_seq"]}


def run_note(db: Any, run_id: str) -> str:
    """What a Refine tells the model about the run being discussed: the settings and the seed it used, and the prompt it was made from."""
    row = db.get_run(run_id)
    if row is None:
        return ""
    options = json.loads(row["options_json"] or "{}")
    bits = [f"mode {row['mode']}", f"size {row['width']}x{row['height']}" if row["width"] else "", f"steps {row['steps']}",
            f"guidance {row['cfg_scale']:g}" if row["cfg_scale"] else "", f"seed {row['seed']}", "a draft" if options.get("draft") else ""]
    note = "The picture was made with: " + ", ".join(b for b in bits if b) + "."
    if row["negative_prompt"]:
        note += f" Negative prompt used: {row['negative_prompt']}"
    return note + f"\nThe prompt used was:\n{row['prompt']}"


def add_chat_routes(app: FastAPI) -> None:
    def parts(request: Request):
        state = request.app.state
        return state.builder, state.builder_store, state.db, state.storage, state.bus

    def changed(request: Request, chat_id: str) -> None:
        request.app.state.bus.publish("builder.changed", {"kind": "chat", "id": chat_id})

    def usable(request: Request, chat_id: str):
        """The chat, or the response to send instead. A chat in the bin is read only: restore it first."""
        _, store, *_ = parts(request)
        row = store.get_chat(chat_id)
        if row is None:
            return None, error(404, "That chat doesn't exist.", "not_found")
        return row, None

    def check_links(request: Request, project_id: Optional[str], prompt_id: Optional[str] = None) -> Optional[JSONResponse]:
        _, store, db, *_ = parts(request)
        if project_id is not None and not db.list_projects(project_id):
            return error(404, "That project doesn't exist.", "no_project")
        if prompt_id is not None:
            prompt = store.get_prompt(prompt_id)
            if prompt is None:
                return error(404, "That prompt doesn't exist.", "not_found")
            if prompt["deleted_at"] is not None:
                return error(409, "That prompt is in the bin.", "prompt_in_bin")
        return None

    # ------------------------------------------------------------------ chats
    @app.get("/api/builder/chats")
    async def list_chats(request: Request, project: Optional[str] = None, deleted: bool = False) -> Any:
        _, store, *_ = parts(request)
        chats = await asyncio.to_thread(store.list_chats, project=project, deleted=deleted)
        return {"chats": [{**chat_payload(c), "last_text": c["last_text"], "message_count": c["message_count"]} for c in chats]}

    @app.post("/api/builder/chats", status_code=201)
    async def create_chat(body: ChatCreate, request: Request) -> Any:
        _, store, *_ = parts(request)
        problem = check_links(request, body.project_id, body.prompt_id)
        if problem is not None:
            return problem
        title = " ".join((body.title or "").split())[:TITLE_FROM_WORDS] or DEFAULT_TITLE
        chat_id = store.create_chat(now=utcnow(), title=title, for_kind=body.for_, project_id=body.project_id, detail=body.detail, prompt_id=body.prompt_id)
        changed(request, chat_id)
        return chat_payload(store.get_chat(chat_id))

    @app.get("/api/builder/chats/{chat_id}")
    async def get_chat(chat_id: str, request: Request) -> Any:
        row, problem = usable(request, chat_id)
        if problem is not None:
            return problem
        _, store, *_ = parts(request)
        attachments: dict[int, list[Any]] = {}
        for a in store.attachments(chat_id):
            attachments.setdefault(a["message_seq"], []).append(a)
        return {
            "chat": chat_payload(row),
            "messages": [message_payload(m, attachments.get(m["seq"], ())) for m in store.messages(chat_id)],
            "draft": store.last_draft(chat_id),
            "pending": [attachment_payload(a) for a in attachments.get(0, ())],
            "preferences": len(store.active_preferences(row["project_id"])),
        }

    @app.patch("/api/builder/chats/{chat_id}")
    async def patch_chat(chat_id: str, body: ChatPatch, request: Request) -> Any:
        row, problem = usable(request, chat_id)
        if problem is not None:
            return problem
        _, store, *_ = parts(request)
        sent = body.model_dump(exclude_unset=True, by_alias=False)
        fields: dict[str, Any] = {}
        if "title" in sent and sent["title"] is not None:
            title = " ".join(sent["title"].split())
            if not title:
                return error(422, "Give the chat a name.", "bad_title")
            fields["title"] = title
        if "for_" in sent and sent["for_"] is not None:
            fields["for_kind"] = sent["for_"]
        if "detail" in sent and sent["detail"] is not None:
            fields["detail"] = sent["detail"]
        if "project_id" in sent:  # null takes it out of its project
            problem = check_links(request, sent["project_id"])
            if problem is not None:
                return problem
            fields["project_id"] = sent["project_id"]
        store.update_chat(chat_id, now=utcnow(), **fields)
        changed(request, chat_id)
        return chat_payload(store.get_chat(chat_id))

    @app.post("/api/builder/chats/{chat_id}/bin")
    async def bin_chat(chat_id: str, request: Request) -> Any:
        _, store, *_ = parts(request)
        if store.bin_item("chat", chat_id, utcnow()) == "not_found":
            return error(404, "That chat doesn't exist.", "not_found")
        changed(request, chat_id)
        return chat_payload(store.get_chat(chat_id))

    @app.post("/api/builder/chats/{chat_id}/restore")
    async def restore_chat(chat_id: str, request: Request) -> Any:
        _, store, *_ = parts(request)
        result = store.restore_item("chat", chat_id, utcnow())
        if result == "not_found":
            return error(404, "That chat doesn't exist.", "not_found")
        if result == "not_in_bin":
            return error(409, "That chat isn't in the bin.", "not_in_bin")
        changed(request, chat_id)
        return chat_payload(store.get_chat(chat_id))

    @app.delete("/api/builder/chats/{chat_id}")
    async def delete_chat(chat_id: str, request: Request) -> Any:
        _, store, _, storage, _ = parts(request)
        registry: dict[str, ReplyJob] = request.app.state.builder_jobs
        if chat_id in registry:
            return error(409, "A reply is being written in that chat. Stop it first.", "reply_in_progress")
        if store.delete_item("chat", chat_id) == "not_found":
            return error(404, "That chat doesn't exist.", "not_found")
        await asyncio.to_thread(remove_chat_files, storage, chat_id)
        changed(request, chat_id)
        return {"deleted": chat_id}

    # ------------------------------------------------------------------ a message, and its streamed reply
    @app.post("/api/builder/chats/{chat_id}/messages")
    async def send_message(chat_id: str, body: MessageIn, request: Request) -> Any:
        service, store, db, *_ = parts(request)
        if not service.configured:
            return not_set_up()
        row, problem = usable(request, chat_id)
        if problem is not None:
            return problem
        if row["deleted_at"] is not None:
            return error(409, "That chat is in the bin. Restore it to carry on.", "chat_in_bin")
        registry: dict[str, ReplyJob] = request.app.state.builder_jobs
        if chat_id in registry:
            return error(409, "A reply is already being written in this chat.", "reply_in_progress")
        if not body.retry and body.intent == "message" and not body.text.strip() and not body.answers and not body.attachments:
            return error(422, "Write something, or choose an answer.", "empty_message")
        if body.intent in ("flesh_out", "refine", "describe", "help_edit") and not INTENTS[body.intent]:
            return error(422, "That isn't something the builder can do.", "bad_intent")
        if body.draft is not None and len(body.draft.prompt) > request.app.state.settings.max_prompt_chars:
            return error(422, f"The draft is longer than the {request.app.state.settings.max_prompt_chars} characters the studio allows.", "draft_too_long")
        if body.retry:
            last = store.messages(chat_id)
            if not last or last[-1]["role"] != "user":
                return error(409, "There is nothing to send again.", "nothing_to_retry")
        draft = None
        if body.draft is not None:
            draft = body.draft.model_dump()
        engine: BuilderEngine = request.app.state.builder_engine
        req = ReplyRequest(text=body.text, answers=[a.model_dump() for a in body.answers], intent=body.intent, draft=draft, think=body.think, attachments=body.attachments,
                           similar=body.similar, exclude=body.exclude, retry=body.retry, run_note=run_note(db, body.run_id) if body.run_id else "")
        job = ReplyJob()
        registry[chat_id] = job
        slots: asyncio.Semaphore = request.app.state.builder_slots
        bus = request.app.state.bus

        async def stream():
            queue: asyncio.Queue[Optional[Event]] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            worker: Optional[asyncio.Future[None]] = None
            try:
                if slots.locked():  # one reply at a time (rule 9): this one waits its turn, and says so
                    yield format_sse("waiting", {})
                async with slots:
                    if job.stopped:
                        yield format_sse("stopped", {"message": None})
                        return
                    if db.read_one("SELECT 1 FROM runs WHERE status = 'running' AND mode != 'music' LIMIT 1") is not None:
                        yield format_sse("busy", {"images": True})  # the GPU is making a picture: replies may be slower

                    def work() -> None:
                        try:
                            for event in engine.reply(chat_id, req, job):
                                loop.call_soon_threadsafe(queue.put_nowait, event)
                        except OllamaError as exc:
                            loop.call_soon_threadsafe(queue.put_nowait, Event("error", {"code": f"ollama_{exc.kind}", "message": exc.message, "hint": exc.hint, "status": STATUS_FOR.get(exc.kind, 502)}))
                        except Exception:  # a bug: say so briefly, log the rest, and leave the chat as it was
                            log.exception("a Prompt builder reply failed")
                            loop.call_soon_threadsafe(queue.put_nowait, Event("error", {"code": "builder_failed", "message": "The builder hit a problem it didn't expect.",
                                                                                          "hint": "Try again; the details are in the studio's log.", "status": 500}))
                        finally:
                            loop.call_soon_threadsafe(queue.put_nowait, None)

                    worker = loop.run_in_executor(None, work)
                    while True:
                        event = await queue.get()
                        if event is None:
                            break
                        yield format_sse(event.name, event.data)
                        if event.name in ("result", "stopped"):
                            bus.publish("builder.changed", {"kind": "chat", "id": chat_id})
            finally:
                job.stop(keep=False)  # the connection closed, or the reply is over: either way nothing more is to be written, and nothing partial is kept
                registry.pop(chat_id, None)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/builder/chats/{chat_id}/stop")
    async def stop_reply(chat_id: str, request: Request) -> Any:
        job = request.app.state.builder_jobs.get(chat_id)
        if job is None:
            return error(409, "No reply is being written in this chat.", "no_reply")
        job.stop(keep=True)
        return {"stopping": True}

    # ------------------------------------------------------------------ pictures
    async def kept(request: Request, chat_id: str, data: bytes, name: Optional[str]) -> Any:
        _, store, _, storage, _ = parts(request)
        if len(store.attachments(chat_id, pending=True)) >= MAX_PER_MESSAGE:
            return error(422, f"A message can carry at most {MAX_PER_MESSAGE} pictures.", "too_many")
        try:
            path, width, height, _ = await asyncio.to_thread(save_picture, storage, chat_id, data)
        except UploadError as exc:
            return error(exc.status, exc.message, "bad_picture")
        attachment_id = store.add_attachment(chat_id, path=path, width=width, height=height, name=(name or "")[:120] or None)
        return JSONResponse(status_code=201, content=attachment_payload(store.get_attachment(attachment_id)))

    @app.post("/api/builder/chats/{chat_id}/attachments")
    async def attach_upload(chat_id: str, request: Request, name: Optional[str] = None) -> Any:
        """Attach a picture: the file itself is the request body, as for an edit's upload. It waits with the chat until a message is sent."""
        row, problem = usable(request, chat_id)
        if problem is not None:
            return problem
        limit = request.app.state.settings.max_upload_mb * 1024 * 1024
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                return error(413, f"The file is larger than {request.app.state.settings.max_upload_mb} MB.", "too_large")
        return await kept(request, chat_id, bytes(data), name)

    @app.post("/api/builder/chats/{chat_id}/attachments/from-image")
    async def attach_from_image(chat_id: str, body: FromImage, request: Request) -> Any:
        """Attach a copy of a picture the studio already has: one from the history, or one waiting in the Edit tray."""
        row, problem = usable(request, chat_id)
        if problem is not None:
            return problem
        _, _, db, storage, _ = parts(request)
        image = db.get_image(body.image_id)
        if image is None:
            return error(404, "That picture doesn't exist.", "not_found")
        try:
            data = await asyncio.to_thread(storage.abs(image["path"]).read_bytes)
        except (OSError, ValueError):
            return error(404, "That picture's file is missing.", "file_missing")
        return await kept(request, chat_id, data, None)

    @app.delete("/api/builder/attachments/{attachment_id}")
    async def remove_attachment(attachment_id: str, request: Request) -> Any:
        _, store, _, storage, _ = parts(request)
        row = store.delete_attachment(attachment_id)
        if row is None:
            return error(404, "That picture isn't waiting to be sent.", "not_found")
        await asyncio.to_thread(remove_picture, storage, row["path"])
        return {"deleted": attachment_id}

    @app.get("/api/builder/attachments/{attachment_id}")
    async def get_attachment(attachment_id: str, request: Request) -> Any:
        _, store, _, storage, _ = parts(request)
        row = store.get_attachment(attachment_id)
        if row is None:
            return error(404, "That picture doesn't exist.", "not_found")
        path = storage.abs(row["path"])
        if not path.is_file():
            return error(404, "That picture's file is missing.", "file_missing")
        return FileResponse(path, media_type=media_type(row["path"]), headers={"Cache-Control": "private, max-age=3600"})
