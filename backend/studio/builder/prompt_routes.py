"""The library routes of the Prompt builder (DESIGN.md section 37.4): saved prompts and their versions, the standing preferences, the counts, and the bin.

A prompt is made only when a person asks for it (rule 12 of the small choices); its versions are only ever added; deleting goes to the bin for `STUDIO_BIN_DAYS` (and is for good
at once when that is 0, as for a picture). The settings of a prompt are checked by the same rules a run is (`answer.check_settings`), so one that would be refused when it is
used cannot be saved.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..serialize import purge_time, utcnow
from . import NOTE_CHARS, PREFERENCE_CHARS, TAGS_MAX, TITLE_CHARS, normalize_tag
from .answer import Limits, check_settings
from .chat_routes import error
from .files import remove_chat_files
from .library import SORTS, UNSET, Library

ID = r"^[0-9a-f]{32}$"
Kind = Literal["images", "vector", "music", "edit"]
Source = Literal["builder", "hand", "wording", "revision"]


class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str
    negative: Optional[str] = None
    settings: dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = Field(default=None, max_length=NOTE_CHARS)
    source: Source = "hand"
    chat_id: Optional[str] = Field(default=None, pattern=ID)
    message_seq: Optional[int] = Field(default=None, ge=1)


class PromptCreate(VersionIn):
    title: str = Field(min_length=1, max_length=TITLE_CHARS)
    for_: Kind = Field(default="images", alias="for")
    project_id: Optional[str] = Field(default=None, pattern=ID)
    tags: list[str] = Field(default_factory=list, max_length=TAGS_MAX * 3)
    favourite: bool = False

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class PromptPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    title: Optional[str] = Field(default=None, min_length=1, max_length=TITLE_CHARS)
    for_: Optional[Kind] = Field(default=None, alias="for")
    project_id: Optional[str] = Field(default=None, pattern=ID)
    favourite: Optional[bool] = None
    tags: Optional[list[str]] = Field(default=None, max_length=TAGS_MAX * 3)


class FileRuns(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: Optional[str] = Field(default=None, pattern=ID)


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=PREFERENCE_CHARS * 2)
    project_id: Optional[str] = Field(default=None, pattern=ID)
    source: Literal["hand", "suggestion"] = "hand"


class PreferencePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: Optional[str] = Field(default=None, min_length=1, max_length=PREFERENCE_CHARS * 2)
    enabled: Optional[bool] = None
    project_id: Optional[str] = Field(default=None, pattern=ID)


def clean_line(text: str) -> str:
    return " ".join(text.split())


def add_prompt_routes(app: FastAPI) -> None:
    def parts(request: Request):
        state = request.app.state
        return state.builder_store, state.builder_library, state.db, state.settings

    def changed(request: Request, kind: str, item_id: str) -> None:
        request.app.state.bus.publish("builder.changed", {"kind": kind, "id": item_id})

    def shown(request: Request, summary: dict[str, Any]) -> dict[str, Any]:
        """A prompt for the page: with when it will be deleted for good, if it is in the bin."""
        return {**summary, "purge_at": purge_time(summary["deleted_at"], request.app.state.settings.bin_days)}

    def project_problem(request: Request, project_id: Optional[str]) -> Optional[JSONResponse]:
        if project_id is not None and not request.app.state.db.list_projects(project_id):
            return error(404, "That project doesn't exist.", "no_project")
        return None

    def tags_of(raw: list[str]) -> tuple[Optional[list[str]], Optional[JSONResponse]]:
        tags = list(dict.fromkeys(t for t in (normalize_tag(x) for x in raw) if t))
        if len(tags) > TAGS_MAX:
            return None, error(422, f"A prompt can have at most {TAGS_MAX} tags.", "too_many_tags")
        return tags, None

    def version_problem(request: Request, body: VersionIn, kind: str) -> Optional[JSONResponse]:
        """The words, the negative prompt and the settings of a version, checked as a run is."""
        _, _, _, settings = parts(request)
        if not body.text.strip():
            return error(422, "A prompt needs some words.", "empty_prompt")
        for what, value in (("The prompt", body.text), ("The negative prompt", body.negative or "")):
            if len(value) > settings.max_prompt_chars:
                return error(422, f"{what} is {len(value)} characters; the limit is {settings.max_prompt_chars}.", "too_long")
        kept, notices = check_settings(kind, body.settings, Limits.of(settings))
        if notices or kept != body.settings:
            return JSONResponse(status_code=422, content={"detail": notices[0] if notices else "Those settings aren't ones this kind of prompt has.", "code": "bad_settings", "notices": notices})
        if body.chat_id is not None and request.app.state.builder_store.get_chat(body.chat_id) is None:
            return error(404, "That chat doesn't exist.", "not_found")
        return None

    def editable(request: Request, prompt_id: str):
        store, *_ = parts(request)
        row = store.get_prompt(prompt_id)
        if row is None:
            return None, error(404, "That prompt doesn't exist.", "not_found")
        if row["deleted_at"] is not None:
            return None, error(409, "That prompt is in the bin. Restore it to change it.", "prompt_in_bin")
        return row, None

    def full(request: Request, prompt_id: str, **extra: Any) -> dict[str, Any]:
        _, library, *_ = parts(request)
        return {**shown(request, library.detail(prompt_id)), **extra}

    # ------------------------------------------------------------------ prompts
    @app.get("/api/builder/prompts")
    async def list_prompts(request: Request, q: Optional[str] = Query(None, max_length=200), tag: Optional[str] = Query(None, max_length=60), favourite: bool = False,
                           project: Optional[str] = Query(None, pattern=r"^([0-9a-f]{32}|none)$"), deleted: bool = False,
                           sort: Literal["updated", "newest", "title"] = "updated") -> Any:
        _, library, *_ = parts(request)
        rows = await asyncio.to_thread(library.list_prompts, q=q, tag=normalize_tag(tag) if tag else None, favourite=favourite, project=project, deleted=deleted, sort=sort)
        return {"prompts": [shown(request, r) for r in rows], "sorts": list(SORTS)}

    @app.post("/api/builder/prompts", status_code=201)
    async def create_prompt(body: PromptCreate, request: Request) -> Any:
        store, library, *_ = parts(request)
        problem = version_problem(request, body, body.for_) or project_problem(request, body.project_id)
        if problem is not None:
            return problem
        title = clean_line(body.title)
        if not title:
            return error(422, "Give the prompt a title.", "bad_title")
        tags, problem = tags_of(body.tags)
        if problem is not None:
            return problem
        prompt_id = store.create_prompt(now=utcnow(), title=title, text=body.text.strip(), negative=(body.negative or "").strip() or None, settings=body.settings,
                                        note=body.note, source=body.source, for_kind=body.for_, project_id=body.project_id, tags=tags or [], favourite=body.favourite,
                                        chat_id=body.chat_id, message_seq=body.message_seq)
        changed(request, "prompt", prompt_id)
        request.app.state.bus.publish("projects.changed", {})
        return full(request, prompt_id)

    @app.get("/api/builder/prompts/{prompt_id}")
    async def get_prompt(prompt_id: str, request: Request) -> Any:
        _, library, *_ = parts(request)
        if library.detail(prompt_id) is None:
            return error(404, "That prompt doesn't exist.", "not_found")
        return full(request, prompt_id)

    @app.patch("/api/builder/prompts/{prompt_id}")
    async def patch_prompt(prompt_id: str, body: PromptPatch, request: Request) -> Any:
        _, library, *_ = parts(request)
        row, problem = editable(request, prompt_id)
        if problem is not None:
            return problem
        sent = body.model_dump(exclude_unset=True)
        fields: dict[str, Any] = {}
        if "title" in sent and sent["title"] is not None:
            title = clean_line(sent["title"])
            if not title:
                return error(422, "Give the prompt a title.", "bad_title")
            fields["title"] = title
        if "for_" in sent or "for" in sent:
            kind = sent.get("for_", sent.get("for"))
            if kind is not None:
                fields["for_kind"] = kind
        if "favourite" in sent and sent["favourite"] is not None:
            fields["favourite"] = sent["favourite"]
        if "project_id" in sent:  # null takes it out of its project
            problem = project_problem(request, sent["project_id"])
            if problem is not None:
                return problem
            fields["project_id"] = sent["project_id"]
        if "tags" in sent and sent["tags"] is not None:
            tags, problem = tags_of(sent["tags"])
            if problem is not None:
                return problem
            fields["tags"] = tags
        outcome = library.update_prompt(prompt_id, now=utcnow(), **fields) or {"project_changed": False, "unfiled_runs": 0}
        changed(request, "prompt", prompt_id)
        if outcome["project_changed"]:
            request.app.state.bus.publish("projects.changed", {})
        return full(request, prompt_id, unfiled_runs=outcome["unfiled_runs"])

    # ------------------------------------------------------------------ versions
    def saved(request: Request, prompt_id: str, outcome: str, n: Optional[int]) -> Any:
        if outcome == "not_found":
            return error(404, "That prompt doesn't exist.", "not_found")
        if outcome == "no_version":
            return error(404, "That prompt has no such version.", "no_version")
        if outcome == "unchanged":
            return JSONResponse(status_code=200, content={"created": False, "message": "Nothing changed.", "version": n, "prompt": full(request, prompt_id)})
        changed(request, "prompt", prompt_id)
        return JSONResponse(status_code=201, content={"created": True, "message": f"Saved as version {n}.", "version": n, "prompt": full(request, prompt_id)})

    @app.post("/api/builder/prompts/{prompt_id}/versions")
    async def add_version(prompt_id: str, body: VersionIn, request: Request) -> Any:
        _, library, *_ = parts(request)
        row, problem = editable(request, prompt_id)
        if problem is not None:
            return problem
        problem = version_problem(request, body, row["for_kind"])
        if problem is not None:
            return problem
        outcome, n = library.add_version(prompt_id, now=utcnow(), text=body.text.strip(), negative=body.negative, settings=body.settings, note=body.note, source=body.source,
                                         chat_id=body.chat_id, message_seq=body.message_seq)
        return saved(request, prompt_id, outcome, n)

    @app.post("/api/builder/prompts/{prompt_id}/versions/{n}/restore")
    async def make_current(prompt_id: str, n: int, request: Request) -> Any:
        _, library, *_ = parts(request)
        row, problem = editable(request, prompt_id)
        if problem is not None:
            return problem
        outcome, number = library.make_current(prompt_id, n, now=utcnow())
        return saved(request, prompt_id, outcome, number)

    @app.get("/api/builder/prompts/{prompt_id}/compare")
    async def compare(prompt_id: str, request: Request, a: int = Query(..., ge=1), b: int = Query(..., ge=1)) -> Any:
        _, library, *_ = parts(request)
        found = library.compare(prompt_id, a, b)
        if found is None:
            return error(404, "One of those versions doesn't exist.", "no_version")
        return found

    # ------------------------------------------------------------------ the bin
    @app.post("/api/builder/prompts/{prompt_id}/bin")
    async def bin_prompt(prompt_id: str, request: Request) -> Any:
        store, *_ = parts(request)
        if request.app.state.settings.bin_days <= 0:  # no bin: Delete is for good, as for a picture
            if store.delete_item("prompt", prompt_id) == "not_found":
                return error(404, "That prompt doesn't exist.", "not_found")
            changed(request, "prompt", prompt_id)
            request.app.state.bus.publish("projects.changed", {})
            return {"deleted": prompt_id}
        if store.bin_item("prompt", prompt_id, utcnow()) == "not_found":
            return error(404, "That prompt doesn't exist.", "not_found")
        changed(request, "prompt", prompt_id)
        request.app.state.bus.publish("projects.changed", {})
        return full(request, prompt_id)

    @app.post("/api/builder/prompts/{prompt_id}/restore")
    async def restore_prompt(prompt_id: str, request: Request) -> Any:
        store, *_ = parts(request)
        result = store.restore_item("prompt", prompt_id, utcnow())
        if result == "not_found":
            return error(404, "That prompt doesn't exist.", "not_found")
        if result == "not_in_bin":
            return error(409, "That prompt isn't in the bin.", "not_in_bin")
        changed(request, "prompt", prompt_id)
        request.app.state.bus.publish("projects.changed", {})
        return full(request, prompt_id)

    @app.delete("/api/builder/prompts/{prompt_id}")
    async def delete_prompt(prompt_id: str, request: Request) -> Any:
        store, *_ = parts(request)
        if store.delete_item("prompt", prompt_id) == "not_found":
            return error(404, "That prompt doesn't exist.", "not_found")
        changed(request, "prompt", prompt_id)
        request.app.state.bus.publish("projects.changed", {})
        return {"deleted": prompt_id}

    @app.post("/api/builder/bin/empty")
    async def empty_bin(request: Request) -> Any:
        """Delete for good every prompt and chat in the bin (and the folders of those chats). The pictures made from those prompts are not touched."""
        removed = await request.app.state.jobs.purge_builder_bin(None)
        return {"deleted": removed}

    @app.post("/api/builder/prompts/{prompt_id}/file-runs")
    async def file_runs(prompt_id: str, body: FileRuns, request: Request) -> Any:
        """'Also file its pictures' runs' (rule 4): file the runs made from this prompt that are in no project (quick tries and the bin excepted) in the project given, or in the
        prompt's own. Nothing is moved unless this is asked."""
        store, library, *_ = parts(request)
        row, problem = editable(request, prompt_id)
        if problem is not None:
            return problem
        project_id = body.project_id or row["project_id"]
        if project_id is None:
            return error(422, "Choose a project to file them in.", "no_project")
        problem = project_problem(request, project_id)
        if problem is not None:
            return problem
        filed = []
        for run_id in library.unfiled_runs(prompt_id):
            await request.app.state.jobs.file_run(run_id, project_id)
            filed.append(run_id)
        return {"filed": filed, "project_id": project_id}

    # ------------------------------------------------------------------ preferences
    def preference_problem(request: Request, text: Optional[str], project_id: Any) -> Optional[JSONResponse]:
        if text is not None:
            if len(clean_line(text)) > PREFERENCE_CHARS:
                return error(422, f"A note can be at most {PREFERENCE_CHARS} characters.", "too_long")
            if not clean_line(text):
                return error(422, "A note needs some words.", "empty_note")
        return project_problem(request, project_id) if project_id not in (None, UNSET) else None

    @app.get("/api/builder/preferences")
    async def list_preferences(request: Request) -> Any:
        _, library, *_ = parts(request)
        return {"preferences": library.preferences()}

    @app.post("/api/builder/preferences", status_code=201)
    async def add_preference(body: PreferenceIn, request: Request) -> Any:
        _, library, *_ = parts(request)
        problem = preference_problem(request, body.text, body.project_id)
        if problem is not None:
            return problem
        outcome, preference_id = library.add_preference(now=utcnow(), text=clean_line(body.text), project_id=body.project_id, source=body.source)
        if outcome == "duplicate":
            return error(409, "You already have that note.", "duplicate")
        if outcome == "full":
            from . import PREFERENCES_MAX

            return error(422, f"You can keep at most {PREFERENCES_MAX} notes. Delete one first.", "too_many")
        changed(request, "preferences", preference_id)
        return next(p for p in library.preferences() if p["id"] == preference_id)

    @app.patch("/api/builder/preferences/{preference_id}")
    async def patch_preference(preference_id: str, body: PreferencePatch, request: Request) -> Any:
        _, library, *_ = parts(request)
        sent = body.model_dump(exclude_unset=True)
        text = clean_line(sent["text"]) if sent.get("text") is not None else None
        problem = preference_problem(request, text, sent.get("project_id", UNSET) if "project_id" in sent else UNSET)
        if problem is not None:
            return problem
        outcome = library.update_preference(preference_id, text=UNSET if text is None else text, enabled=sent["enabled"] if sent.get("enabled") is not None else UNSET,
                                            project_id=sent["project_id"] if "project_id" in sent else UNSET)
        if outcome == "not_found":
            return error(404, "That note doesn't exist.", "not_found")
        if outcome == "duplicate":
            return error(409, "You already have that note.", "duplicate")
        if outcome == "no_project":
            return error(409, "This note belonged to a project that is gone. Choose where it applies before switching it on.", "no_project")
        changed(request, "preferences", preference_id)
        return next(p for p in library.preferences() if p["id"] == preference_id)

    @app.delete("/api/builder/preferences/{preference_id}")
    async def delete_preference(preference_id: str, request: Request) -> Any:
        _, library, *_ = parts(request)
        if not library.delete_preference(preference_id):
            return error(404, "That note doesn't exist.", "not_found")
        changed(request, "preferences", preference_id)
        return {"deleted": preference_id}

    # ------------------------------------------------------------------ counts
    @app.get("/api/builder/counts")
    async def counts(request: Request) -> Any:
        store, library, *_ = parts(request)
        found = await asyncio.to_thread(store.counts)
        found["preferences"] = {"all": len(library.preferences()), "enabled": sum(1 for p in library.preferences() if p["enabled"] and not p["orphaned"]),
                                "orphaned": sum(1 for p in library.preferences() if p["orphaned"])}
        return found
