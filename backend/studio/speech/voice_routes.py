"""The routes of voices (DESIGN.md §38.4, §38.9): the voices of an engine, samples, designing a voice from a description, uploading a recording to clone, saving,
renaming and deleting a voice.

The short jobs behind a sample and a design are queued here and followed through `GET /api/speech/jobs/{id}` (and the `speech.job` event); the files they make
are served by the routes below. Nothing in this file names an engine.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..jobs import QueueFull
from ..pipelines.base import PipelineError
from .passage import CONSENT_STATEMENT, PASSAGE, SAMPLE_SENTENCE
from .recording import RecordingRefused, inspect
from .refused import ReadingRefused
from .routes import error, refusal
from .voices import DESCRIPTION_MAX, HEX_ID, NAME_MAX

DECODE_AT_ONCE = 2  # recordings read at the same time: reading one can take a few seconds of CPU and some megabytes


class SampleBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    engine: str
    voice: str = ""
    saved_voice: Optional[str] = None
    recording: Optional[str] = None


class DesignBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    description: str = Field(max_length=5000)
    engine: Optional[str] = None


class CandidateRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job: str
    index: int = Field(ge=0, le=2)


class SaveVoiceBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["designed", "cloned"]
    name: str = Field(max_length=1000)
    candidate: Optional[CandidateRef] = None  # a designed voice: which clip of which design
    recording: Optional[str] = None  # a cloned voice: the id an upload gave
    engine: Optional[str] = None  # a cloned voice: the engine it is for
    consent: bool = False  # a cloned voice: the statement was ticked


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(max_length=1000)


def add_routes(app: FastAPI, read_limited_body) -> None:
    decoding = asyncio.Semaphore(DECODE_AT_ONCE)

    def jobs_of(request: Request):
        return request.app.state.jobs

    def voices_of(request: Request):
        return jobs_of(request).speech.voices

    def engines_of(request: Request) -> dict[str, dict[str, Any]]:
        return {e["id"]: e for e in jobs_of(request).speech_status()["engines"]}

    def wav(path) -> Response:
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})

    not_found = lambda: error(404, "Not found.", "not_found")  # noqa: E731

    # ------------------------------------------------------------------ what there is
    @app.get("/api/speech/engines/{engine_id}/voices")
    async def engine_voices(engine_id: str, request: Request) -> Any:
        """The engine's preset voices, and the voices you made for it (DESIGN.md §38.4). Each has whether its sample is kept."""
        if engine_id not in engines_of(request):
            return error(404, f"There is no speech engine {engine_id!r} on this server.", "unknown_engine")
        try:
            return await asyncio.to_thread(voices_of(request).for_engine, engine_id)
        except PipelineError as exc:  # an engine whose library is missing has no list to give: the page shows its pill instead
            return error(409, exc.message, "engine_unavailable", exc.hint)

    @app.get("/api/speech/passage")
    async def passage(request: Request) -> Any:
        """The words to read aloud to add your own voice, the sentence a sample says, and the statement to tick: the page shows exactly these."""
        s = request.app.state.settings
        return {"text": PASSAGE, "sample_sentence": SAMPLE_SENTENCE, "consent": CONSENT_STATEMENT,
                "voice_seconds": {"min": s.speech_voice_min_seconds, "max": s.speech_voice_max_seconds}, "name_max": NAME_MAX, "description_max": DESCRIPTION_MAX}

    # ------------------------------------------------------------------ samples
    @app.post("/api/speech/samples")
    async def make_sample(body: SampleBody, request: Request) -> Any:
        """"Play sample": a kept sample is answered at once; otherwise a short job is queued ahead of waiting readings (never ahead of the part being made),
        and a second press while it waits finds the first job instead of queueing another."""
        voices = voices_of(request)
        try:
            target = await asyncio.to_thread(voices.sample_target, engines_of(request), body.engine, body.voice, body.saved_voice, body.recording)
            if voices.sample_exists(target):
                return {"ready": True, "url": target.url, "job": None}
            active = jobs_of(request).speech.store.active_job_for(target.key)
            if active is not None:
                return JSONResponse(status_code=202, content={"ready": False, "url": None, "job": voices.job_status(active)})
            job = await jobs_of(request).submit_hidden(voices.sample_run(target))
        except ReadingRefused as exc:
            return refusal(exc)
        except QueueFull as exc:
            return error(429, str(exc), "queue_full")
        return JSONResponse(status_code=202, content={"ready": False, "url": None, "job": job})

    @app.get("/api/speech/samples/{group}/{name}.wav")
    async def get_sample(group: str, name: str, request: Request) -> Any:
        path = voices_of(request).sample_file(group, name)
        return not_found() if path is None else wav(path)

    # ------------------------------------------------------------------ designing a voice
    @app.post("/api/speech/voices/design", status_code=202)
    async def design_voice(body: DesignBody, request: Request) -> Any:
        """Make three candidate clips of a voice described in words. Queued like a sample; the page follows it with `GET /api/speech/jobs/{id}`."""
        try:
            run = await asyncio.to_thread(voices_of(request).design_run, engines_of(request), body.description, body.engine)
            return await jobs_of(request).submit_hidden(run)
        except ReadingRefused as exc:
            return refusal(exc)
        except QueueFull as exc:
            return error(429, str(exc), "queue_full")

    @app.get("/api/speech/jobs/{run_id}")
    async def speech_job(run_id: str, request: Request) -> Any:
        row = jobs_of(request).speech.store.hidden_job(run_id) if HEX_ID.match(run_id) else None
        return not_found() if row is None else voices_of(request).job_status(row)

    @app.get("/api/speech/candidates/{run_id}/{index}.wav")
    async def get_candidate(run_id: str, index: int, request: Request) -> Any:
        path = voices_of(request).candidate_file(run_id, index)
        return not_found() if path is None else wav(path)

    # ------------------------------------------------------------------ a recording to clone
    @app.post("/api/speech/recordings", status_code=201)
    async def upload_recording(request: Request) -> Any:
        """A recording of a voice, as the request body. It is read and checked here (DESIGN.md §38.4, §38.11); what is kept is the studio's own mono WAV with
        the silence at the ends trimmed, until it is saved as a voice or `STUDIO_SPEECH_TEMP_HOURS` pass. The upload itself is not kept."""
        s = request.app.state.settings
        data = await read_limited_body(request, s.speech_recording_max_mb)
        if isinstance(data, JSONResponse):
            return data
        try:
            async with decoding:
                recording = await asyncio.to_thread(inspect, data, min_seconds=s.speech_voice_min_seconds, max_seconds=s.speech_voice_max_seconds,
                                                    ffmpeg=s.speech_ffmpeg, timeout=s.speech_decode_timeout_s)
            return await asyncio.to_thread(voices_of(request).stage, recording)
        except RecordingRefused as exc:
            return error(exc.status, exc.message, exc.code, exc.hint, "recording")

    @app.get("/api/speech/recordings/{recording_id}.wav")
    async def get_recording(recording_id: str, request: Request) -> Any:
        path = voices_of(request).staged_path(recording_id)
        return not_found() if path is None else wav(path)

    # ------------------------------------------------------------------ the voices you made
    @app.post("/api/speech/voices", status_code=201)
    async def save_voice(body: SaveVoiceBody, request: Request) -> Any:
        """Save a candidate of a design, or a recording you uploaded, as a voice. A recording needs the statement ticked."""
        voices = voices_of(request)
        try:
            if body.kind == "designed":
                if body.candidate is None or body.recording is not None:
                    raise ReadingRefused(422, "one_source", "A designed voice is saved from one of the three clips of a design.", "candidate")
                return await asyncio.to_thread(voices.save_designed, engines_of(request), body.candidate.job, body.candidate.index, body.name)
            if body.recording is None or body.candidate is not None or not body.engine:
                raise ReadingRefused(422, "one_source", "A cloned voice is saved from a recording you uploaded, for an engine.", "recording")
            return await asyncio.to_thread(voices.save_cloned, engines_of(request), body.engine, body.recording, body.name, body.consent)
        except ReadingRefused as exc:
            return refusal(exc)

    @app.patch("/api/speech/voices/{voice_id}")
    async def rename_voice(voice_id: str, body: RenameBody, request: Request) -> Any:
        if not HEX_ID.match(voice_id):
            return not_found()
        try:
            return await asyncio.to_thread(voices_of(request).rename, voice_id, body.name)
        except ReadingRefused as exc:
            return refusal(exc)

    @app.delete("/api/speech/voices/{voice_id}", status_code=204)
    async def delete_voice(voice_id: str, request: Request) -> Response:
        if not HEX_ID.match(voice_id):
            return not_found()
        try:
            await asyncio.to_thread(voices_of(request).delete, voice_id)
        except ReadingRefused as exc:
            return refusal(exc)
        return Response(status_code=204)
