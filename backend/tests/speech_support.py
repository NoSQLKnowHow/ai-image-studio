"""Helpers the speech tests share (DESIGN.md §38): building a job for the speech worker, driving a worker in this process, and a toy engine.

Kept in one module so that a change to the shape of a job is made in one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from studio.pipelines import make_pipeline
from studio.speech.engine import EngineBase, EngineInfo, Licence, PieceRequest, Speech, Voice
from studio.worker import Worker

RUN_ID = "0123456789abcdef0123456789abcdef"
OTHER_RUN_ID = "fedcba9876543210fedcba9876543210"


def piece(n: int = 0, text: str = "Hello there, this is a short test of the voice.", kind: str = "paragraph", pause_ms: int = 100,
          run_id: str = RUN_ID, part: int = 0, seed: Optional[int] = None) -> dict[str, Any]:
    """One piece of a job, written to the place a reading's pieces go (`speech/readings/<run>/<part>/<piece>.wav`)."""
    return {"key": f"{part}-{n}", "text": text, "kind": kind, "seed": n if seed is None else seed, "pause_ms": pause_ms,
            "out": f"speech/readings/{run_id}/{part:04d}/{n:05d}.wav"}


def speech_job(pieces: Optional[list[dict[str, Any]]] = None, task: str = "reading", engine: str = "fake", variant: str = "default",
               voice: str = "anna", run_id: str = RUN_ID, **extra: Any) -> dict[str, Any]:
    """A job as the API would send it. Keyword arguments replace or add fields; passing `voice=""` leaves the voice out, as a saved voice or a design does."""
    job: dict[str, Any] = {"run_id": run_id, "mode": "speech", "task": task, "engine": engine, "variant": variant,
                           "pieces": pieces if pieces is not None else [piece(0), piece(1)], "voice": voice}
    job.update(extra)
    return job


class Capture:
    """Stands in for the worker's emitter and keeps every event it was asked to send."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: str, **fields: Any) -> None:
        if event == "load_progress":  # the progress of a load is a side channel (DESIGN.md section 39) and is tested in test_load_progress.py; this test is about the events of a run
            return
        self.events.append({"event": event, **fields})

    def kinds(self) -> list[str]:
        return [e["event"] for e in self.events]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == kind]


def in_process_worker(data_dir: Path, engine: str = "fake", variant: str = "", step_delay_ms: int = 0) -> tuple[Worker, Capture]:
    """A speech worker running in this process with the fake pipeline, so a test can call `run` and look at the events without a subprocess."""
    emit = Capture()
    pipeline = make_pipeline("fake", model="", cpu_offload=False, hub_mode="auto", fake_step_delay_ms=step_delay_ms, kind="speech",
                             engine=engine, variant=variant)
    return Worker(pipeline, data_dir, emit, kind="speech"), emit  # type: ignore[arg-type]


class ToyEngine(EngineBase):
    """An engine nobody has heard of: the whole of what a new model needs to be (criterion 260). It "speaks" a fixed number of silent samples
    per character, so a test can compute exactly how long each piece is."""

    INFO = EngineInfo(
        id="toy", title="Toy engine", licence=Licence("None"), languages=(), can_style=False, can_clone=False, can_design=False,
        max_chars=100, min_chars=10, sample_rate=8000, interruptible=False, memory_gb=0.0, model_id="toy",
    )
    PRESETS = (Voice("toy_voice", "Toy Voice", gender="female"),)

    @classmethod
    def probe(cls) -> dict[str, Any]:
        return {"device": "toy"}

    def load(self) -> dict[str, Any]:
        return {"device": "toy"}

    def synthesize(self, request: PieceRequest, cancelled: Callable[[], bool]) -> Speech:
        return Speech(pcm=bytes(2 * 80 * len(request.text)), sample_rate=8000)  # 10 ms of silence for each character
