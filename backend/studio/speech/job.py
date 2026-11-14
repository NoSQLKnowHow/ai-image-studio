"""The job the API sends the speech worker, and the checks the worker makes before it believes one (DESIGN.md §38.3).

One job is one trip to the model: some pieces of one reading (the rest of a part), or the one piece of a sample, or the few candidate clips of a voice
being designed. The API decides every file name; the worker only checks that each lies inside the speech folder of the data directory, as it checks the
paths of the images and tracks it writes. A job that fails the checks is refused whole, with a message, and nothing is written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .engine import VoiceRef

RUN_ID = re.compile(r"^[0-9a-f]{32}$")
TASKS = ("reading", "sample", "design")
MAX_PIECES = 5000  # a part is at most 30,000 characters, so a job never has more pieces than this
MAX_PIECE_CHARS = 20_000  # a hard ceiling on one piece, far above any engine's own limit; the engine's `max_chars` is what the text pipeline uses
MAX_PAUSE_MS = 10_000
KINDS = ("paragraph", "heading")


@dataclass(frozen=True)
class SpeechPiece:
    key: str  # an id the worker sends back with the result (the API's own name for the piece)
    text: str
    kind: str  # "paragraph" or "heading"
    seed: int
    out: str  # where the WAV goes, relative to the data directory, under speech/
    pause_ms: int  # the silence added after the speech, so that pieces joined one after the other have their pauses already in them
    # A speaker with a voice of their own (DESIGN.md §44.5): when `own_voice` these replace the job's voice, reference clip and style for this piece. A piece with none of them,
    # the narrator's, is spoken with the job's, so a job with no speakers is what it always was.
    own_voice: bool = False
    voice: str = ""
    ref: Optional[VoiceRef] = None
    style: Optional[str] = None


@dataclass(frozen=True)
class SpeechJob:
    run_id: str
    task: str  # "reading", "sample" or "design"
    engine: str
    variant: str
    pieces: tuple[SpeechPiece, ...]
    voice: str = ""  # a preset voice's id; empty when `ref` is set or for a design
    ref: Optional[VoiceRef] = None  # a saved voice
    style: Optional[str] = None
    language: Optional[str] = None
    design: Optional[str] = None  # the description of the voice to invent (task "design")

    @classmethod
    def from_dict(cls, raw: Any, data_dir: Path) -> "SpeechJob":
        """Read and check a job as it came over the wire. Raises ValueError with a sentence that says what is wrong."""
        if not isinstance(raw, dict):
            raise ValueError("the job is not an object")
        run_id = raw.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID.match(run_id):
            raise ValueError("bad run_id")
        task = raw.get("task")
        if task not in TASKS:
            raise ValueError(f"unknown task {task!r}")
        engine, variant = raw.get("engine"), raw.get("variant")
        if not isinstance(engine, str) or not engine or not isinstance(variant, str) or not variant:
            raise ValueError("a speech job names its engine and variant")
        pieces_raw = raw.get("pieces")
        if not isinstance(pieces_raw, list) or not pieces_raw or len(pieces_raw) > MAX_PIECES:
            raise ValueError(f"a speech job has between 1 and {MAX_PIECES} pieces")

        speech_root = (data_dir / "speech").resolve()
        pieces = []
        seen: set[str] = set()
        for item in pieces_raw:
            if not isinstance(item, dict):
                raise ValueError("a piece is not an object")
            key, text, kind, out = item.get("key"), item.get("text"), item.get("kind"), item.get("out")
            seed, pause = item.get("seed"), item.get("pause_ms")
            if not isinstance(key, str) or not key or key in seen:
                raise ValueError("every piece needs its own key")
            seen.add(key)
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_PIECE_CHARS:
                raise ValueError("a piece needs some text, and not an enormous amount")
            if kind not in KINDS:
                raise ValueError(f"unknown kind of piece {kind!r}")
            if isinstance(seed, bool) or not isinstance(seed, int) or isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= MAX_PAUSE_MS:
                raise ValueError("a piece needs a whole-number seed and a pause between 0 and 10,000 ms")
            if not isinstance(out, str) or not out.endswith(".wav"):
                raise ValueError("a piece needs the name of a .wav file to write")
            target = (data_dir / out).resolve()
            if speech_root not in target.parents:
                raise ValueError(f"{out!r} is not inside the speech folder")
            own_ref: Optional[VoiceRef] = None
            own_ref_raw = item.get("ref")
            if own_ref_raw is not None:
                if not isinstance(own_ref_raw, dict) or not isinstance(own_ref_raw.get("path"), str) or not isinstance(own_ref_raw.get("text"), str):
                    raise ValueError("a speaker's saved voice needs the path and the words of its reference clip")
                own_path = (data_dir / own_ref_raw["path"]).resolve()
                if (speech_root / "voices") not in own_path.parents or not own_path.is_file():
                    raise ValueError("a speaker's reference clip must be a file inside the voices folder")
                own_ref = VoiceRef(path=str(own_path), text=own_ref_raw["text"], kind=str(own_ref_raw.get("kind") or "cloned"))
            own_voice_id = item.get("voice") or ""
            own_style = item.get("style")
            if not isinstance(own_voice_id, str) or (own_style is not None and not isinstance(own_style, str)):
                raise ValueError("a piece's voice and style must be text")
            pieces.append(SpeechPiece(key=key, text=text, kind=kind, seed=seed, out=out, pause_ms=pause, own_voice=bool(own_voice_id) or own_ref is not None,
                                      voice=own_voice_id, ref=own_ref, style=own_style or None))

        voice = raw.get("voice") or ""
        ref_raw = raw.get("ref")
        ref: Optional[VoiceRef] = None
        if ref_raw is not None:
            if not isinstance(ref_raw, dict) or not isinstance(ref_raw.get("path"), str) or not isinstance(ref_raw.get("text"), str):
                raise ValueError("a saved voice needs the path and the words of its reference clip")
            ref_path = (data_dir / ref_raw["path"]).resolve()
            if (speech_root / "voices") not in ref_path.parents or not ref_path.is_file():
                raise ValueError("a saved voice's reference clip must be a file inside the voices folder")
            ref = VoiceRef(path=str(ref_path), text=ref_raw["text"], kind=str(ref_raw.get("kind") or "cloned"))
        if not isinstance(voice, str):
            raise ValueError("the voice must be text")
        design = raw.get("design")
        if task == "design":
            if not isinstance(design, str) or not design.strip():
                raise ValueError("a design job needs a description of the voice")
        elif not voice and ref is None:
            raise ValueError("a job needs a voice: a preset or a saved one")
        style, language = raw.get("style"), raw.get("language")
        for name, value in (("style", style), ("language", language)):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be text")
        return cls(run_id=run_id, task=task, engine=engine, variant=variant, pieces=tuple(pieces), voice=voice, ref=ref,
                   style=style or None, language=language or None, design=design if isinstance(design, str) else None)
