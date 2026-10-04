"""Deterministic stand-in for MiniMax-Music3 (DESIGN.md §26.4).

Makes the whole music path testable without a GPU or the model: the same description, lyrics, duration, steps and seed
always give the same short melody of sine tones (a few seconds, whatever the duration asked for, so tests are quick), and
a different seed gives a different tune. It reports both stages like the real pipeline (composing frames, rendering
steps), honours Cancel, and takes the same fault directives in the description as the image pipeline:

    [fake:error]  raise a generic pipeline error
    [fake:oom]    raise an out-of-memory error
    [fake:crash]  kill the worker process abruptly (exit code 3)
    [fake:noise]  write junk to stdout, to prove the protocol channel is protected

A load failure is simulated like the image pipeline's (STUDIO_FAKE_LOAD_FAIL, STUDIO_FAKE_LOAD_DELAY_MS).
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import sys
import time
from typing import Any

from .. import wavfile
from .base import MusicJob, MusicProgress, MusicResult, OutOfMemory, PipelineError, PipelineLoadError
from .fake import FakePipeline, parse_directives

SAMPLE_RATE = 22_050
MAX_SECONDS = 6.0  # the fake melody never runs longer than this
NOTE_SECONDS = 0.375
SCALE = (0, 2, 4, 7, 9, 12, 14, 16)  # a major pentatonic run, in semitones
FRAMES = 20  # "composing" steps to report
RENDER_STEPS = 10  # "rendering" steps to report at most


def melody_seed(job: MusicJob, seed: int) -> int:
    digest = hashlib.sha256(f"{seed}|{job.duration}|{job.steps}|{job.lyrics}|{job.prompt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def render_melody(rng: random.Random, seconds: float, lyrics_on: bool) -> bytes:
    """A little tune: one sine note per NOTE_SECONDS, with a soft attack and release; with lyrics a second voice a
    fifth above, so a track "with vocals" is audibly different from an instrumental one."""
    total = int(seconds * SAMPLE_RATE)
    left, right = [0.0] * total, [0.0] * total
    note_len = int(NOTE_SECONDS * SAMPLE_RATE)
    root = 220.0 * 2 ** (rng.randrange(0, 5) / 12)
    pos = 0
    while pos < total:
        pitch = root * 2 ** (rng.choice(SCALE) / 12)
        length = min(note_len, total - pos)
        for i in range(length):
            envelope = min(1.0, i / 200, (length - i) / 400)
            t = i / SAMPLE_RATE
            value = 0.35 * math.sin(2 * math.pi * pitch * t)
            if lyrics_on:
                value += 0.15 * math.sin(2 * math.pi * pitch * 1.5 * t)
            value *= envelope
            left[pos + i] = value
            right[pos + i] = value * 0.9
        pos += note_len
    return wavfile.pcm16_from_samples([left, right])


class FakeMusicPipeline:
    name = "fake"
    SUPPORTS = {"music": True, "instrumental": True, "step_progress": True, "cancel": True}

    def __init__(self, step_delay_ms: int = 30):
        self.step_delay = step_delay_ms / 1000
        self._failed_once = False
        self._images = FakePipeline(step_delay_ms=step_delay_ms)  # for the load-failure and load-delay switches

    @classmethod
    def probe(cls) -> dict[str, Any]:
        return {"pipeline": cls.name, "supports": dict(cls.SUPPORTS), "device": {"name": "fake (no GPU used)"}}

    def load(self) -> dict[str, Any]:
        self._images.load()  # raises PipelineLoadError when STUDIO_FAKE_LOAD_FAIL says so, and waits STUDIO_FAKE_LOAD_DELAY_MS
        return {"pipeline": self.name, "supports": dict(self.SUPPORTS)}

    def generate(self, job: MusicJob, index: int, seed: int, on_progress: MusicProgress) -> MusicResult:
        for directive, only in parse_directives(job.prompt):
            if only is not None and only != index:
                continue
            if directive == "error":
                raise PipelineError("Simulated music generation failure ([fake:error]).")
            if directive == "oom":
                raise OutOfMemory("Simulated out-of-memory ([fake:oom]).", hint="Use a shorter duration.")
            if directive == "crash":
                os._exit(3)
            if directive == "noise":
                print("noise on stdout from the fake music pipeline", flush=True)
                sys.stderr.write("noise on stderr\n")
        seconds = min(float(job.duration), MAX_SECONDS)
        for frame in range(1, FRAMES + 1):
            time.sleep(self.step_delay)
            on_progress("compose", frame, FRAMES)
        steps = max(1, min(job.steps, RENDER_STEPS))
        for step in range(1, steps + 1):
            time.sleep(self.step_delay)
            on_progress("render", step, steps)
        on_progress("finish", 0, 1)
        rng = random.Random(melody_seed(job, seed))
        pcm = render_melody(rng, seconds, lyrics_on=job.lyrics.strip().lower() != "[instrumental]")
        return MusicResult(pcm=pcm, sample_rate=SAMPLE_RATE, channels=2, seconds=seconds)
