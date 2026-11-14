"""The Speech tab's routes (DESIGN.md §38.9): preparing a source, making and tending readings, and their audio. Voices are added to it as they are built.

Every call here blocks (a fetch, a child process), so each runs in a thread; at most two at a time, because each is a process that can use a processor for
a minute.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import naming
from ..jobs import QueueFull, RunConflict, RunNotFound
from ..serialize import parse_ts, utcnow
from ..storage import StorageError, check_id
from ..wavfile import WavError
from . import prepare_service
from .audio import PartAudio, RangeNotSatisfiable, ZipMember, parse_range, piece_file, stream_zip
from .chapters import PartLength, chapters_of, chapters_txt, ffmetadata
from .service import ParagraphSpec, PartSpec, ReadingRefused, ReadingSpec, SpeakerSpec, audio_info
from .text.errors import PrepareError

PREPARE_AT_ONCE = 2


class PrepareBody(BaseModel):
    """Pasted text or a web address to prepare. Which one is `source`; the other field is ignored."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(pattern="^(text|url)$")
    text: Optional[str] = None
    url: Optional[str] = None
    clean: bool = True  # switch the cleaning rules off to read the raw text
    read_extras: bool = False  # read code blocks and tables as well
    speakers: bool = True  # look for speakers: lines that begin with a name and a colon (DESIGN.md §44.5)


class ParagraphBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["heading", "paragraph"]
    text: str = Field(max_length=2_000_000)
    speaker: Optional[str] = Field(default=None, max_length=200)  # the name of a speaker listed in the request's `speakers` (DESIGN.md §44.5)


class SpeakerBody(BaseModel):
    """A speaker of a reading and the voice that speaks for them: a preset's id or a voice the person made; neither means the main voice (DESIGN.md §44.5)."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(max_length=200)
    voice: str = ""
    saved_voice: Optional[str] = None


class PartBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(max_length=400)
    paragraphs: list[ParagraphBody]


class ReadingBody(BaseModel):
    """A request for a reading (DESIGN.md §38.9): what to read (the parts the person settled on, already prepared), in whose voice, and how."""

    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(max_length=400)
    source: dict[str, Any] = Field(default_factory=dict)
    engine: str
    voice: str = ""
    saved_voice: Optional[str] = None
    style: Optional[str] = None
    language: Optional[str] = None
    speakers: list[SpeakerBody] = Field(default_factory=list)
    parts: list[PartBody]


class PieceBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(max_length=20_000)


class PositionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    part: int = Field(ge=0, le=100_000)
    piece: int = Field(ge=0, le=1_000_000)
    offset_ms: int = Field(ge=0, le=100 * 3600 * 1000)


def error(status: int, detail: str, code: str, hint: Optional[str] = None, field: Optional[str] = None) -> JSONResponse:
    """A refusal in the studio's usual shape: `detail`, `code` and, where there is one, a `hint` and the `field` of the request that is at fault (so the
    page can put the words beside the box they are about)."""
    content: dict[str, Any] = {"detail": detail, "code": code}
    if hint:
        content["hint"] = hint
    if field:
        content["field"] = field
    return JSONResponse(status_code=status, content=content)


def refusal(exc: ReadingRefused) -> JSONResponse:
    return error(exc.status, exc.message, exc.code, exc.hint, exc.field)


def ranged(audio_size: int, produce, request: Request, *, filename: Optional[str] = None) -> Response:
    """A WAV that exists only as a stream, answered with the byte range the player asked for (DESIGN.md §38.7). `produce(first, last)` yields the bytes."""
    try:
        span = parse_range(request.headers.get("range"), audio_size)
    except RangeNotSatisfiable:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{audio_size}"})
    first, last = span if span else (0, audio_size - 1)
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(last - first + 1), "Cache-Control": "private, max-age=3600"}
    if span:
        headers["Content-Range"] = f"bytes {first}-{last}/{audio_size}"
    if filename:
        headers["Content-Disposition"] = naming.content_disposition(filename, default="reading.wav")
    return StreamingResponse(produce(first, last), status_code=206 if span else 200, media_type="audio/wav", headers=headers)


def failure(exc: PrepareError) -> JSONResponse:
    """A refusal in the studio's usual shape: `detail`, `code` and, where there is one, a `hint`."""
    content: dict[str, Any] = {"detail": exc.message, "code": exc.code}
    if exc.hint:
        content["hint"] = exc.hint
    return JSONResponse(status_code=exc.status, content=content)


def add_routes(app: FastAPI, read_limited_body) -> None:
    """Register the routes. `read_limited_body(request, megabytes)` is the API's helper that reads a request body up to a size limit."""
    slots = asyncio.Semaphore(PREPARE_AT_ONCE)

    @app.post("/api/speech/prepare")
    async def prepare(body: PrepareBody, request: Request) -> Any:
        """Pasted text or a web address, prepared for review (nothing is stored). The answer is the parts and paragraphs the page shows."""
        settings = request.app.state.settings
        try:
            async with slots:
                if body.source == "text":
                    if body.text is None or len(body.text) > settings.speech_max_chars * 2:
                        raise PrepareError("too_large" if body.text else "empty", "Paste the text to read (up to a million words or so)." if not body.text
                                           else f"That is more than {settings.speech_max_chars * 2:,} characters; make two readings.")
                    return await asyncio.to_thread(prepare_service.prepare_text, settings, body.text, clean=body.clean, read_extras=body.read_extras, speakers=body.speakers)
                if not body.url:
                    raise PrepareError("bad_address", "Give the web address of the page to read.")
                return await asyncio.to_thread(prepare_service.prepare_url, settings, body.url, clean=body.clean, read_extras=body.read_extras, speakers=body.speakers)
        except PrepareError as exc:
            return failure(exc)

    @app.post("/api/speech/prepare/file")
    async def prepare_file(request: Request, name: str, clean: bool = True, read_extras: bool = False, speakers: bool = True) -> Any:
        """A file, prepared for review. The file is the request body (as for pictures, §21.6); its name, in the query, tells what kind it is."""
        settings = request.app.state.settings
        data = await read_limited_body(request, settings.speech_max_upload_mb)
        if isinstance(data, JSONResponse):
            return data
        try:
            async with slots:
                return await asyncio.to_thread(prepare_service.prepare_file, settings, name, data, clean=clean, read_extras=read_extras, speakers=speakers)
        except PrepareError as exc:
            return failure(exc)


    # ------------------------------------------------------------------ readings (DESIGN.md §38.6, §38.9)
    def jobs_of(request: Request):
        return request.app.state.jobs

    def reading_row(request: Request, run_id: str):
        """The reading's row, or None for an id that is not one (the id is checked first, so nothing odd reaches a path)."""
        try:
            check_id(run_id)
        except StorageError:
            return None
        return jobs_of(request).speech.store.reading(run_id)

    not_found = lambda: error(404, "Reading not found.", "not_found")  # noqa: E731

    @app.post("/api/readings", status_code=201)
    async def create_reading(body: ReadingBody, request: Request) -> Any:
        """Queue a reading: one entry in the queue however many parts it has (it goes back to the queue after each part, §38.6)."""
        spec = ReadingSpec(title=body.title, source=body.source, engine=body.engine, voice=body.voice, saved_voice=body.saved_voice, style=body.style,
                           language=body.language, parts=[PartSpec(p.title, [ParagraphSpec(q.kind, q.text, q.speaker) for q in p.paragraphs]) for p in body.parts],
                           speakers=[SpeakerSpec(s.name, s.voice, s.saved_voice) for s in body.speakers])
        try:
            return await jobs_of(request).submit_reading(spec)
        except ReadingRefused as exc:
            return refusal(exc)
        except QueueFull as exc:
            return error(429, str(exc), "queue_full")

    @app.get("/api/readings/{run_id}")
    async def get_reading(run_id: str, request: Request) -> Any:
        row = reading_row(request, run_id)
        return not_found() if row is None else jobs_of(request).payload(run_id)

    @app.get("/api/readings/{run_id}/parts/{part}")
    async def get_part(run_id: str, part: int, request: Request) -> Any:
        """One part with its pieces: their text, where each starts, and where to fetch their audio (the reading view and its read-along)."""
        row = reading_row(request, run_id)
        detail = None if row is None else await asyncio.to_thread(jobs_of(request).speech.part_detail, row, part)
        return not_found() if detail is None else detail

    @app.post("/api/readings/{run_id}/continue")
    async def continue_reading(run_id: str, request: Request) -> Any:
        """Continue a canceled reading, or retry a failed one, from the first piece not made."""
        if reading_row(request, run_id) is None:
            return not_found()
        try:
            return await jobs_of(request).continue_reading(run_id)
        except RunNotFound:
            return not_found()
        except RunConflict as exc:
            return error(409, str(exc), "not_continuable")
        except QueueFull as exc:
            return error(429, str(exc), "queue_full")

    @app.patch("/api/readings/{run_id}/parts/{part}/pieces/{idx}")
    async def edit_piece(run_id: str, part: int, idx: int, body: PieceBody, request: Request) -> Any:
        """Change the words of a piece that has not been made (for a piece the engine failed on). A piece that is made cannot be changed; while the reading
        is being made no piece can, because the one being worked on might be this one."""
        row = reading_row(request, run_id)
        if row is None:
            return not_found()
        if row["status"] == "running":
            return error(409, "The reading is being made right now. Cancel it, or wait for a part to finish, then change the words.", "busy")
        text = " ".join(body.text.split())
        if not text:
            return error(422, "A piece needs some words.", "empty")
        speech = jobs_of(request).speech
        outcome = await asyncio.to_thread(speech.store.edit_piece, run_id, part, idx, text)
        if outcome == "not_found":
            return not_found()
        if outcome == "made":
            return error(409, "That piece has been made already, so its words cannot be changed.", "already_made")
        jobs_of(request)._publish_run(run_id)
        return {"idx": idx, "text": text}

    @app.put("/api/readings/{run_id}/position", status_code=204)
    async def put_position(run_id: str, body: PositionBody, request: Request) -> Response:
        """Where the listener stopped; one per reading, shared by whoever opens it (there is no login, §11)."""
        row = reading_row(request, run_id)
        if row is None:
            return not_found()
        await asyncio.to_thread(jobs_of(request).speech.store.set_position, run_id, body.part, body.piece, body.offset_ms, utcnow())
        return Response(status_code=204)

    # ------------------------------------------------------------------ audio (DESIGN.md §38.7)
    def part_audio(request: Request, row, part: int) -> PartAudio:
        speech = jobs_of(request).speech
        files = speech.part_files(row, part)
        if not files:
            raise FileNotFoundError(part)
        options = json.loads(row["options_json"])
        parts = {p["idx"]: p["title"] for p in speech.store.parts(row["id"])}
        return PartAudio([piece_file(path) for path in files], audio_info(options, parts.get(part, ""), row["created_at"]))

    def download_name(row, part: int, title: str) -> str:
        options = json.loads(row["options_json"])
        voice = (options.get("voice") or {}).get("label") or (options.get("voice") or {}).get("id") or ""
        return naming.speech_filename(title=options.get("title") or row["prompt"], part=part + 1, voice=voice, created_at=parse_ts(row["created_at"]))

    @app.get("/api/speech/{run_id}/parts/{part}.wav")
    async def get_part_audio(run_id: str, part: int, request: Request, download: bool = False) -> Any:
        """A finished part as one WAV, built from its pieces, with range requests so a player can seek (the pause after each piece is in the pieces)."""
        row = reading_row(request, run_id)
        if row is None:
            return not_found()
        if not any(p["idx"] == part for p in jobs_of(request).speech.store.parts(run_id)):
            return not_found()
        try:
            audio = await asyncio.to_thread(part_audio, request, row, part)
        except FileNotFoundError:
            return error(409, "That part has not been finished yet.", "not_finished")
        except (WavError, StorageError, OSError):
            return error(500, "The audio of that part could not be read.", "audio_unreadable")
        title = next((p["title"] for p in jobs_of(request).speech.store.parts(run_id) if p["idx"] == part), "")
        return ranged(audio.size, audio.iter_bytes, request, filename=download_name(row, part, title) if download else None)

    @app.get("/api/speech/{run_id}/parts/{part}/pieces/{idx}.wav")
    async def get_piece_audio(run_id: str, part: int, idx: int, request: Request) -> Any:
        """One piece, as soon as it is made: a part that is still being made is played piece by piece. FileResponse answers range requests."""
        row = reading_row(request, run_id)
        piece = None if row is None else jobs_of(request).speech.store.piece(run_id, part, idx)
        if piece is None or piece["status"] != "done":
            return not_found()
        try:
            path = jobs_of(request).storage.abs(piece["path"])
        except StorageError:
            return not_found()
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/speech/{run_id}/download.zip")
    async def download_zip(run_id: str, request: Request) -> Any:
        """Every finished part of a reading in a ZIP (stored, written as it streams), with a playlist for a phone's player."""
        row = reading_row(request, run_id)
        if row is None:
            return not_found()
        speech = jobs_of(request).speech
        members: list[ZipMember] = []
        lines = ["#EXTM3U"]
        lengths: list[PartLength] = []  # the finished parts' lengths in samples, for the chapter lists (DESIGN.md §44.4)
        for part in speech.store.parts(run_id):
            try:
                audio = await asyncio.to_thread(part_audio, request, row, part["idx"])
            except FileNotFoundError:
                continue  # a part that is not finished is not in the ZIP
            except (WavError, StorageError, OSError):
                return error(500, f"The audio of part {part['idx'] + 1} could not be read.", "audio_unreadable")
            name = download_name(row, part["idx"], part["title"])
            members.append(ZipMember(name, audio.size, lambda a=audio: a.iter_bytes()))
            lines += [f"#EXTINF:{round(audio.seconds)},{part['title']}", name]
            lengths.append(PartLength(part["title"], audio.data_bytes // (2 * audio.channels), audio.sample_rate))
        if not members:
            return error(409, "No part of this reading has been finished yet.", "not_finished")
        playlist = ("\n".join(lines) + "\n").encode("utf-8")
        members.append(ZipMember("playlist.m3u", len(playlist), lambda: iter([playlist])))
        # The two chapter lists, after the parts and the playlist (which are as they were): the start of each part in the book played from its first part to its last, in
        # the order of the playlist, from the lengths of the files that are in this ZIP (DESIGN.md §44.4)
        chapters = chapters_of(lengths)
        title = json.loads(row["options_json"]).get("title") or row["prompt"]
        for file_name, text in (("chapters.txt", chapters_txt(chapters)), ("chapters.ffmetadata", ffmetadata(chapters, title))):
            data = text.encode("utf-8")
            members.append(ZipMember(file_name, len(data), lambda d=data: iter([d])))
        zip_name = naming.speech_filename(title=(json.loads(row["options_json"]).get("title") or row["prompt"]), part=0, voice="", created_at=parse_ts(row["created_at"]), ext=".zip")
        return StreamingResponse(stream_zip(members, stamp=parse_ts(row["created_at"]).timestamp()), media_type="application/zip",
                                 headers={"Content-Disposition": naming.content_disposition(zip_name, default="reading.zip")})

    # ------------------------------------------------------------------ voices (DESIGN.md §38.4): their own file, registered with the rest
    from . import audiobook_routes, pronunciation_routes, voice_routes  # (here and not at the top: all take `error` (and some `refusal`) from this module)

    voice_routes.add_routes(app, read_limited_body)
    pronunciation_routes.add_routes(app)
    audiobook_routes.add_routes(app)
