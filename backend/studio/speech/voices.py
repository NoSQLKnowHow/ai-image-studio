"""The voices a person can speak in (DESIGN.md §38.4): the presets an engine ships with, the samples that let them be compared, the voices designed from a
description, and the voices cloned from a recording.

What this module owns:
  * names: a voice's name is its own, compared with case and accents folded away, one to sixty characters;
  * the short jobs behind a sample and behind a voice design (hidden runs of mode 'speech'; they are queued ahead of waiting readings like a Draft, and are in
    no history), and the checks on what the worker reports for them;
  * a recording that has been uploaded but not yet saved as a voice (kept in `speech/voices/_staged`, which is inside the folder the worker is allowed to read
    a reference clip from), and the three candidate clips of a design (`speech/candidates/<job>/<k>.wav`);
  * saving a candidate or a recording as a voice, renaming it, deleting it;
  * clearing away what was never saved, after `STUDIO_SPEECH_TEMP_HOURS`.

A saved voice is a reference clip and the words said in it; no model is trained (§38.4). It is spoken through the clone variant of the engine it was made for.
Refusals are `ReadingRefused`, the same exception the readings use, so the routes answer them in one way.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .. import wavfile
from ..config import Settings
from ..serialize import utcnow
from ..storage import Storage, StorageError
from . import registry
from .passage import CONSENT_STATEMENT, DESIGN_CANDIDATES, PASSAGE, SAMPLE_SENTENCE
from .recording import Recording
from .refused import ReadingRefused, require_engine
from .store import SpeechStore

NAME_MAX = 60
DESCRIPTION_MIN, DESCRIPTION_MAX = 3, 300
SAFE_STEM = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # a name that can be a file name as it is
HEX_ID = re.compile(r"^[0-9a-f]{32}$")
STAGED = "_staged"  # inside speech/voices, so that a staged recording can be previewed through the same check a saved voice's reference passes


def name_key(name: str) -> str:
    """The name with its case, accents-as-written and spacing folded away: "Kirk", "kirk" and " KIRK " are one name."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def file_stem(identifier: str) -> str:
    """A preset's id as a file name: itself where that is safe, else a short digest of it (an engine could name a voice anything)."""
    return identifier if SAFE_STEM.match(identifier) and not identifier.startswith(".") else hashlib.sha1(identifier.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Target:
    """What a sample is of: a preset voice, a saved voice, or a recording not yet saved. `key` identifies it (a second press of the button finds the first)."""

    key: str
    group: str  # the folder under speech/samples/: the engine's id for a preset, "voice" for a saved voice, "staged" for a recording
    stem: str
    engine: str
    variant: str
    voice: str  # a preset's id, else ""
    ref: Optional[dict[str, str]]  # the reference clip and its words, for a saved voice or a recording
    label: str

    @property
    def rel(self) -> str:
        return f"speech/samples/{self.group}/{self.stem}.wav"

    @property
    def url(self) -> str:
        return f"/api/speech/samples/{self.group}/{self.stem}.wav"


class VoiceService:
    def __init__(self, settings: Settings, storage: Storage, store: SpeechStore):
        self.settings, self.storage, self.store = settings, storage, store

    # ------------------------------------------------------------------ places
    @property
    def voices_dir(self) -> Path:
        return self.storage.speech / "voices"

    @property
    def staged_dir(self) -> Path:
        return self.voices_dir / STAGED

    @property
    def samples_dir(self) -> Path:
        return self.storage.speech / "samples"

    @property
    def candidates_dir(self) -> Path:
        return self.storage.speech / "candidates"

    def staged_path(self, recording_id: str) -> Optional[Path]:
        """The file of an uploaded recording that has not been saved, or None (an id that is not one, or one that has gone)."""
        if not HEX_ID.match(recording_id or ""):
            return None
        path = self.staged_dir / f"{recording_id}.wav"
        return path if path.is_file() else None

    def sample_file(self, group: str, stem: str) -> Optional[Path]:
        """A kept sample, or None. Both parts are checked, so nothing but a file inside the samples folder can come back."""
        if not SAFE_STEM.match(group) or not SAFE_STEM.match(stem) or group.startswith(".") or stem.startswith("."):
            return None
        path = (self.samples_dir / group / f"{stem}.wav").resolve()
        return path if self.samples_dir.resolve() in path.parents and path.is_file() else None

    def candidate_file(self, run_id: str, k: int) -> Optional[Path]:
        if not HEX_ID.match(run_id or "") or not 0 <= k < DESIGN_CANDIDATES:
            return None
        path = self.candidates_dir / run_id / f"{k}.wav"
        return path if path.is_file() else None

    # ------------------------------------------------------------------ names
    def clean_name(self, raw: str) -> tuple[str, str]:
        """(the name as it will be shown, its key). Raises ReadingRefused for an empty one or one that is too long."""
        name = " ".join(raw.split())
        if not name:
            raise ReadingRefused(422, "name_empty", "A voice needs a name.", "name")
        if len(name) > NAME_MAX:
            raise ReadingRefused(422, "name_too_long", f"A voice's name is at most {NAME_MAX} characters.", "name")
        return name, name_key(name)

    # ------------------------------------------------------------------ describing voices
    def describe(self, row: sqlite3.Row) -> dict[str, Any]:
        sample = self.sample_file("voice", row["id"])
        return {"id": row["id"], "name": row["name"], "kind": row["kind"], "engine": row["engine"], "description": row["description"], "seconds": row["seconds"],
                "created_at": row["created_at"], "consent_at": row["consent_at"], "sample": {"ready": sample is not None, "url": f"/api/speech/samples/voice/{row['id']}.wav" if sample else None}}

    def for_engine(self, engine_id: str) -> dict[str, Any]:
        """The presets of an engine, each with whether its sample is kept, and the voices made for it (DESIGN.md §38.9)."""
        info = registry.info(engine_id)
        presets = []
        for voice in registry.presets(engine_id):
            stem = file_stem(voice.id)
            kept = self.sample_file(engine_id, stem) is not None
            presets.append({**voice.as_dict(), "sample": {"ready": kept, "url": f"/api/speech/samples/{engine_id}/{stem}.wav" if kept else None}})
        saved = [self.describe(row) for row in self.store.voices(engine_id)] if info.can_clone else []
        return {"engine": engine_id, "presets": presets, "saved": saved}

    # ------------------------------------------------------------------ samples
    def sample_target(self, engines: dict[str, dict[str, Any]], engine_id: str, voice: str = "", saved_voice: Optional[str] = None,
                      recording: Optional[str] = None) -> Target:
        """What a request for a sample asks for, checked. Exactly one of a preset `voice`, a `saved_voice` or a `recording`. Raises ReadingRefused."""
        require_engine(engines, engine_id)
        info = registry.info(engine_id)
        given = [x for x in (voice, saved_voice, recording) if x]
        if len(given) != 1:
            raise ReadingRefused(422, "one_voice", "Ask for one voice: a preset, a saved voice or a recording.", "voice")
        if saved_voice or recording:
            if not info.can_clone:
                raise ReadingRefused(422, "cannot_clone", f"{info.title} cannot speak in a voice made from a recording.", "voice")
            variant = registry.variant_for(info, "sample", True) or info.preset_variant
        else:
            variant = registry.variant_for(info, "sample", False) or info.preset_variant
        if saved_voice:
            row = self.store.voice(saved_voice)
            if row is None:
                raise ReadingRefused(422, "unknown_voice", "That voice is not among the voices you made (it may have been deleted).", "voice")
            if row["engine"] != engine_id:
                raise ReadingRefused(422, "voice_engine", f"That voice was made for another engine, so {info.title} cannot speak in it.", "voice")
            ref = {"path": row["ref_path"], "text": row["ref_text"], "kind": row["kind"]}
            return Target(f"voice:{row['id']}", "voice", row["id"], engine_id, variant, "", ref, row["name"])
        if recording:
            path = self.staged_path(recording)
            if path is None:
                raise ReadingRefused(404, "unknown_recording", "That recording is no longer here. Upload it again.", "recording")
            ref = {"path": self.storage.rel(path), "text": PASSAGE, "kind": "cloned"}
            return Target(f"staged:{recording}", "staged", recording, engine_id, variant, "", ref, "your recording")
        preset = next((v for v in registry.presets(engine_id) if v.id == voice), None)
        if preset is None:
            raise ReadingRefused(422, "unknown_voice", f"{info.title} has no voice called {voice!r}.", "voice")
        return Target(f"preset:{engine_id}:{preset.id}", engine_id, file_stem(preset.id), engine_id, variant, preset.id, None, preset.label)

    def sample_exists(self, target: Target) -> bool:
        return self.sample_file(target.group, target.stem) is not None

    def sample_run(self, target: Target) -> dict[str, Any]:
        """The row of a hidden, queue-jumping run that makes one sample. `draft` in its options is what puts it ahead of waiting full-size runs (§22.2)."""
        run_id = secrets.token_hex(16)
        options = {"task": "sample", "engine": target.engine, "variant": target.variant, "draft": True, "target": target.key, "label": target.label,
                   "voice": {"kind": "preset" if target.voice else "saved", "id": target.voice or target.stem, "label": target.label}, "ref": target.ref,
                   "preset": target.voice, "outs": {"sample": target.rel}, "url": target.url, "text": SAMPLE_SENTENCE}
        return self._hidden_run(run_id, f"Sample: {target.label}", options, seed=secrets.randbelow(2 ** 31))

    # ------------------------------------------------------------------ designing a voice
    def design_run(self, engines: dict[str, dict[str, Any]], description: str, engine_id: Optional[str]) -> dict[str, Any]:
        """The row of a hidden run that makes the candidate clips of a voice described in words. Raises ReadingRefused."""
        text = " ".join(description.split())
        if len(text) < DESCRIPTION_MIN:
            raise ReadingRefused(422, "description_empty", "Describe the voice in a few words: who it is, how old, how it sounds.", "description")
        if len(text) > DESCRIPTION_MAX:
            raise ReadingRefused(422, "description_too_long", f"A description is at most {DESCRIPTION_MAX} characters.", "description")
        if engine_id is None:  # the first engine that can design and can run
            engine_id = next((e for e, entry in engines.items() if entry["state"] in ("ready", "needs_memory") and registry.info(e).can_design), None)
            if engine_id is None:
                raise ReadingRefused(409, "no_design_engine", "No speech engine here can make a voice from a description.", "engine",
                                     "Qwen3-TTS can. Check that it is installed and ready.")
        require_engine(engines, engine_id)
        info = registry.info(engine_id)
        if not info.can_design or not info.design_variant:
            raise ReadingRefused(422, "cannot_design", f"{info.title} cannot make a voice from a description.", "engine")
        if not info.can_clone or not info.clone_variant:  # a designed voice is spoken through the clone route, never by its description alone (criterion 265)
            raise ReadingRefused(422, "design_unusable", f"{info.title} could make the voice but could not speak in it afterwards.", "engine")
        run_id = secrets.token_hex(16)
        outs = {f"c{k}": f"speech/candidates/{run_id}/{k}.wav" for k in range(DESIGN_CANDIDATES)}
        options = {"task": "design", "engine": engine_id, "variant": info.design_variant, "draft": True, "target": f"design:{run_id}", "description": text,
                   "voice": {"kind": "design", "id": "", "label": "A new voice"}, "outs": outs, "text": PASSAGE, "label": text[:80]}
        return self._hidden_run(run_id, f"Design a voice: {text[:80]}", options, seed=secrets.randbelow(2 ** 31 - DESIGN_CANDIDATES))

    def _hidden_run(self, run_id: str, title: str, options: dict[str, Any], seed: int) -> dict[str, Any]:
        return {"id": run_id, "created_at": utcnow(), "status": "queued", "mode": "speech", "prompt": title, "effective_prompt": title, "negative_prompt": None,
                "transparent": 0, "width": None, "height": None, "steps": 0, "cfg_scale": None, "seed": seed, "num_images": 1,
                "model_id": self.settings.speech_model_for(options["engine"]) or registry.info(options["engine"]).model_id,
                "options_json": json.dumps(options), "lyrics": None, "hidden": 1}

    # ------------------------------------------------------------------ the job for a hidden run, and its results
    def job_for(self, row: sqlite3.Row) -> dict[str, Any]:
        """The worker job for a sample or a design. Raises ValueError if what it needs has gone (the reference clip of a deleted voice)."""
        options = json.loads(row["options_json"])
        base = {"run_id": row["id"], "mode": "speech", "engine": options["engine"], "variant": options["variant"], "style": None, "language": None}
        if options["task"] == "sample":
            ref = options.get("ref")
            if ref is not None and not (self.storage.root / ref["path"]).is_file():
                raise ValueError("The recording this sample is made from is no longer here.")
            return {**base, "task": "sample", "voice": options.get("preset") or "", "ref": ref,
                    "pieces": [{"key": "sample", "text": options["text"], "kind": "paragraph", "seed": row["seed"], "out": options["outs"]["sample"], "pause_ms": 0}]}
        return {**base, "task": "design", "voice": "", "ref": None, "design": options["description"],
                "pieces": [{"key": key, "text": options["text"], "kind": "paragraph", "seed": row["seed"] + k, "out": options["outs"][key], "pause_ms": 0}
                           for k, key in enumerate(sorted(options["outs"]))]}

    def accept_file(self, row: sqlite3.Row, event: dict[str, Any]) -> dict[str, Any]:
        """A piece the worker reports for a hidden run. The file must be exactly the one the job named for that key, and mono speech. Raises ValueError or
        StorageError, which fail the job."""
        options = json.loads(row["options_json"])
        key, reported = str(event.get("key")), str(event.get("path"))
        expected = options["outs"].get(key)
        if expected is None:
            raise ValueError(f"the worker reported a piece this job does not have: {key!r}")
        if reported != expected:
            raise StorageError(f"the worker reported {reported!r} for a piece that was to be written to {expected!r}")
        path = self.storage.abs(expected)
        if not path.is_file():
            raise StorageError(f"the worker reported a missing file: {reported!r}")
        info = wavfile.read_wav(path)
        if info.channels != 1:
            raise ValueError("a piece of speech must be mono")
        return {"key": key, "seconds": round(info.seconds, 2)}

    def job_status(self, row: sqlite3.Row) -> dict[str, Any]:
        """What the page asks about a sample or a design: how it stands, and the files that exist so far."""
        options = json.loads(row["options_json"])
        base = {"id": row["id"], "task": options["task"], "status": row["status"], "target": options["target"], "label": options.get("label"),
                "error": {"message": row["error_message"], "hint": row["error_hint"]} if row["error_message"] else None}
        if options["task"] == "sample":
            kept = self.storage.abs(options["outs"]["sample"]).is_file()
            return {**base, "files": [{"key": "sample", "url": options["url"]}] if kept else []}
        files = [{"key": key, "index": k, "url": f"/api/speech/candidates/{row['id']}/{k}.wav"} for k, key in enumerate(sorted(options["outs"]))
                 if self.storage.abs(options["outs"][key]).is_file()]
        return {**base, "files": files, "description": options["description"]}

    # ------------------------------------------------------------------ a recording, staged
    def stage(self, recording: Recording) -> dict[str, Any]:
        """Keep a checked recording until it is saved as a voice (or the sweep takes it): the studio's own WAV, the upload itself is gone."""
        recording_id = secrets.token_hex(16)
        wavfile.write_wav(self.staged_dir / f"{recording_id}.wav", recording.pcm, recording.sample_rate, channels=1,
                          info={"title": "Reference recording", "software": "ai-image-studio", "comment": "A recording of a voice, made ready as a reference."})
        return {"id": recording_id, "url": f"/api/speech/recordings/{recording_id}.wav", **recording.as_dict()}

    # ------------------------------------------------------------------ saving, renaming, deleting
    def save_cloned(self, engines: dict[str, dict[str, Any]], engine_id: str, recording_id: str, name: str, consent: bool) -> dict[str, Any]:
        """Save a staged recording as a voice. The statement must be ticked; it is stored, with the time, beside the voice (criterion 266)."""
        require_engine(engines, engine_id)
        info = registry.info(engine_id)
        if not info.can_clone:
            raise ReadingRefused(422, "cannot_clone", f"{info.title} cannot speak in a voice made from a recording.", "engine")
        if consent is not True:
            raise ReadingRefused(422, "consent_required", "Tick the statement first: the recording must be of your own voice, or of someone who has agreed.", "consent")
        shown, key = self.clean_name(name)
        source = self.staged_path(recording_id)
        if source is None:
            raise ReadingRefused(404, "unknown_recording", "That recording is no longer here. Upload it again.", "recording")
        seconds = wavfile.read_wav(source).seconds
        return self._insert(engine_id, "cloned", shown, key, None, PASSAGE, source, seconds, CONSENT_STATEMENT, utcnow(), move=True)

    def save_designed(self, engines: dict[str, dict[str, Any]], run_id: str, k: int, name: str) -> dict[str, Any]:
        """Save one of the three candidate clips of a design as a voice, with its description. The voice belongs to the engine that designed it."""
        row = self.store.hidden_job(run_id) if HEX_ID.match(run_id or "") else None
        options = json.loads(row["options_json"]) if row is not None else {}
        if row is None or options.get("task") != "design":
            raise ReadingRefused(404, "unknown_design", "That voice design is no longer here. Design it again.", "candidate")
        source = self.candidate_file(run_id, k)
        if source is None:
            raise ReadingRefused(404, "unknown_candidate", "That clip is not here (it may not be made yet, or it has been cleared away).", "candidate")
        engine_id = options["engine"]
        require_engine(engines, engine_id)
        shown, key = self.clean_name(name)
        return self._insert(engine_id, "designed", shown, key, options["description"], PASSAGE, source, wavfile.read_wav(source).seconds, None, None, move=False)

    def _insert(self, engine_id: str, kind: str, name: str, key: str, description: Optional[str], ref_text: str, source: Path, seconds: float,
                consent_text: Optional[str], consent_at: Optional[str], move: bool) -> dict[str, Any]:
        voice_id = secrets.token_hex(16)
        folder = self.voices_dir / voice_id
        target = folder / "reference.wav"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(source, target)  # a copy, so that a refusal below (the name is taken) leaves a staged recording where it was, to be tried again
            outcome = self.store.insert_voice({
                "id": voice_id, "name": name, "name_key": key, "kind": kind, "engine": engine_id, "description": description, "ref_text": ref_text,
                "ref_path": self.storage.rel(target), "seconds": round(seconds, 2), "consent_text": consent_text, "consent_at": consent_at, "created_at": utcnow()})
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if outcome != "ok":
            shutil.rmtree(folder, ignore_errors=True)
            raise ReadingRefused(409, "name_taken", f"You already have a voice called {name!r}.", "name")
        if move:  # saved: the staged copy has done its work
            source.unlink(missing_ok=True)
        return self.describe(self.store.voice(voice_id))

    def rename(self, voice_id: str, name: str) -> dict[str, Any]:
        shown, key = self.clean_name(name)
        outcome = self.store.rename_voice(voice_id, shown, key)
        if outcome == "not_found":
            raise ReadingRefused(404, "not_found", "That voice is not here.")
        if outcome == "name_taken":
            raise ReadingRefused(409, "name_taken", f"You already have a voice called {shown!r}.", "name")
        return self.describe(self.store.voice(voice_id))

    def delete(self, voice_id: str) -> None:
        """Delete a voice and its files. Refused while a reading or a short job that speaks in it is queued or running. Readings already made keep their
        audio and show the voice's name as it was (their options hold the name), but cannot be continued."""
        if self.store.voice(voice_id) is None:
            raise ReadingRefused(404, "not_found", "That voice is not here.")
        in_use = self.store.runs_using_voice(voice_id)
        if in_use:
            raise ReadingRefused(409, "voice_in_use", f"{in_use} reading{'s are' if in_use != 1 else ' is'} being made in this voice or waiting to be.",
                                 hint="Cancel them, or wait until they are finished, and delete the voice then.")
        self.store.delete_voice(voice_id)
        shutil.rmtree(self.voices_dir / voice_id, ignore_errors=True)
        (self.samples_dir / "voice" / f"{voice_id}.wav").unlink(missing_ok=True)

    # ------------------------------------------------------------------ what was never saved
    def sweep(self, now: Optional[float] = None) -> dict[str, int]:
        """Remove recordings that were uploaded and not saved, the candidates of designs, and the finished short jobs, once they are older than
        STUDIO_SPEECH_TEMP_HOURS. Returns how many of each were removed."""
        now = time.time() if now is None else now
        cutoff = now - self.settings.speech_temp_hours * 3600
        removed = {"recordings": 0, "candidates": 0, "jobs": 0}
        if self.staged_dir.is_dir():
            for path in self.staged_dir.iterdir():
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed["recordings"] += 1
        if self.candidates_dir.is_dir():
            for folder in self.candidates_dir.iterdir():
                if folder.is_dir() and folder.stat().st_mtime < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    removed["candidates"] += 1
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(cutoff)) + ".000Z"
        removed["jobs"] = len(self.store.delete_hidden_jobs_before(stamp))
        return removed
