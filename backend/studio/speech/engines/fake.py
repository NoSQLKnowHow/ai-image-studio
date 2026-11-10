"""The fake speech engines: what every automated test and the browser tests use (DESIGN.md §38.3, criterion 291).

Like the fake image and music pipelines they prove the plumbing and not the sound: the same text, voice, style and seed always give the same short
tone-and-noise-free WAV, a piece is made much faster than real time, progress and Cancel work, and a few directives in the text make a piece fail or
stall so the tests can reach the unhappy paths.

There are two, so that "an engine that cannot do something" can be tested as well as one that can:
  * `fake`        takes a style note on one voice, can clone a voice and design one, has three variants and speaks at 24 kHz;
  * `fake-basic`  takes none of that, has no language list, and speaks at 22,050 Hz (so a reading's sample rate is never assumed to be 24 kHz).

`STUDIO_FAKE_SPEECH_UNAVAILABLE=fake-basic` (a comma list of engine ids) makes those engines fail their start-up check as an engine whose library is missing
does, so that a test can see the page and the capabilities cope with one engine that cannot run. It is read by the worker process, which is
where the check runs.

Directives (anywhere in a piece's text): `[fake:error]` fails the piece, `[fake:oom]` fails it with an out-of-memory error, `[fake:slow]` makes it
take two seconds in small slices that check for Cancel.
"""

from __future__ import annotations

import math
import os
import time
import zlib
from typing import Any

from ... import wavfile
from ...pipelines.base import Canceled, OutOfMemory, PipelineError, PipelineUnavailable
from ...pipelines.progress import fake_download_bytes, fake_load, fake_plan
from ..engine import CancelCheck, EngineBase, EngineInfo, EngineSettings, Licence, PieceRequest, Speech, Voice

ERROR_DIRECTIVE = "[fake:error]"
OOM_DIRECTIVE = "[fake:oom]"
SLOW_DIRECTIVE = "[fake:slow]"
SLOW_SECONDS = 2.0  # how long a [fake:slow] piece takes
SLICE_SECONDS = 0.05  # how often a slow piece looks for Cancel


def _tone(key: str, seconds: float, sample_rate: int) -> bytes:
    """A sine tone whose pitch and wobble come from `key`, so different voices, styles and seeds sound (and compare) different and the same key
    always gives the same bytes. 10 ms fades at both ends so that pieces joined one after the other do not click."""
    hashed = zlib.crc32(key.encode("utf-8"))
    pitch = 160.0 + (hashed % 240)  # 160-400 Hz
    wobble = 2.0 + (hashed >> 8) % 5  # 2-6 Hz of amplitude wobble, like syllables
    count = max(1, int(seconds * sample_rate))
    fade = max(1, min(count // 2, int(0.01 * sample_rate)))
    samples = []
    for n in range(count):
        t = n / sample_rate
        level = 0.25 * (0.6 + 0.4 * math.sin(2 * math.pi * wobble * t)) * math.sin(2 * math.pi * pitch * t)
        if n < fade:
            level *= n / fade
        elif n >= count - fade:
            level *= (count - n) / fade
        samples.append(level)
    return wavfile.pcm16_from_samples([samples])


class _FakeBase(EngineBase):
    """The behaviour the two fake engines share: deterministic tones, the directives, the optional delay and Cancel."""

    @classmethod
    def probe(cls) -> dict[str, Any]:
        broken = [name.strip() for name in os.environ.get("STUDIO_FAKE_SPEECH_UNAVAILABLE", "").split(",")]
        if cls.INFO.id in broken:
            raise PipelineUnavailable(f"The {cls.INFO.title} engine's library is missing.", hint="This is a test; unset STUDIO_FAKE_SPEECH_UNAVAILABLE.")
        return {"device": "fake"}

    def _fake_repo(self) -> str:
        return f"fake/speech-{self.INFO.id}-{self.settings.variant}"

    def download_plan(self) -> Any:
        return fake_plan(self._fake_repo())

    def load(self) -> dict[str, Any]:
        delay = self.settings.step_delay_ms / 1000  # a little time to load, so a test can see the "loading" state of a worker
        if fake_download_bytes():  # a pretend download is asked for (DESIGN.md section 39): it needs a load long enough to be watched
            try:
                delay = max(delay, int(os.environ.get("STUDIO_FAKE_LOAD_DELAY_MS", "0")) / 1000)
            except ValueError:
                pass
            fake_load(self._fake_repo(), delay)
        elif delay:
            time.sleep(delay)
        return {"device": "fake"}

    def _wait(self, seconds: float, cancelled: CancelCheck) -> None:
        """Take `seconds`, in slices, looking for Cancel between them. Raises Canceled."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cancelled():
                raise Canceled()
            time.sleep(min(SLICE_SECONDS, max(0.0, end - time.monotonic())))

    def synthesize(self, request: PieceRequest, cancelled: CancelCheck) -> Speech:
        if cancelled():
            raise Canceled()
        text = request.text
        if ERROR_DIRECTIVE in text:
            raise PipelineError("The fake speech engine was asked to fail on this piece.", hint="This is a test; remove [fake:error] from the text.")
        if OOM_DIRECTIVE in text:
            raise OutOfMemory("The fake speech engine ran out of memory.", hint="This is a test; remove [fake:oom] from the text.")
        if SLOW_DIRECTIVE in text:
            self._wait(SLOW_SECONDS, cancelled)
        elif self.settings.step_delay_ms:
            self._wait(self.settings.step_delay_ms / 1000, cancelled)
        seconds = min(8.0, 0.3 + 0.012 * len(text))
        # Everything that makes a real voice different is in the key: the preset or the saved voice (by its words, not its path, which moves),
        # the style, the language, the seed, the description of a voice being designed, and the engine variant.
        ref = f"{request.ref.kind}:{request.ref.text}" if request.ref is not None else ""
        key = "|".join([self.INFO.id, self.settings.variant, request.voice, ref, request.style or "", request.language or "", str(request.seed),
                        request.design or "", text])
        return Speech(pcm=_tone(key, seconds, self.INFO.sample_rate), sample_rate=self.INFO.sample_rate)


class FakeSpeech(_FakeBase):
    """The full-featured fake: a style on one voice, cloning and design."""

    INFO = EngineInfo(
        id="fake", title="Fake speech", licence=Licence("Test only", False, ""), languages=("English", "German"),
        can_style=True, can_clone=True, can_design=True, max_chars=400, min_chars=40, sample_rate=24000, interruptible=True,
        memory_gb=0.1, model_id="fake-speech", variants=("default", "clone", "design"), preset_variant="default",
        clone_variant="clone", design_variant="design", notes="A stand-in that makes tones, for tests.",
    )
    PRESETS = (
        Voice("anna", "Anna", gender="female", language="English", accent="American", description="A bright test voice.", styleable=True),
        Voice("bea", "Bea", gender="female", language="English", accent=None),
        Voice("carl", "Carl", gender="male", language="English", accent="British"),
        Voice("dan", "Dan", gender="male", language="German", accent=None),
    )


class FakeBasicSpeech(_FakeBase):
    """The plain fake: no style, no cloning, no design, no language list, 22,050 Hz."""

    INFO = EngineInfo(
        id="fake-basic", title="Fake basic speech", licence=Licence("Test only (non-commercial)", True, "A test notice for the licence badge."),
        languages=(), can_style=False, can_clone=False, can_design=False, max_chars=300, min_chars=20, sample_rate=22050, interruptible=False,
        memory_gb=0.05, model_id="fake-basic-speech", notes="A stand-in with no extras, for tests.",
    )
    PRESETS = (
        Voice("basic_one", "Basic One", gender="female", language="English"),
        Voice("basic_two", "Basic Two", gender="male", language="English"),
    )


ENGINE_CLASSES = (FakeSpeech, FakeBasicSpeech)


def make(engine_id: str, settings: EngineSettings) -> _FakeBase:
    """The fake engine called `engine_id`. Raises KeyError."""
    for cls in ENGINE_CLASSES:
        if cls.INFO.id == engine_id:
            return cls(settings)
    raise KeyError(engine_id)
