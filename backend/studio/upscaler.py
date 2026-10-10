"""Enlarge on the server side (DESIGN.md §28.3): whether it can run, and running it.

The API process never imports PyTorch. The real upscaler starts `python -m studio.upscale_job` for each enlargement (the model is
small and loads in about a second, and a crash or an out-of-memory there cannot take the loaded image model with it). With the
fake pipeline a stand-in is used that needs no model: a plain resize that leaves a note in the file, so the page and the tests run
anywhere.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from PIL import Image, UnidentifiedImageError

from . import fourk
from .config import Settings
from .worker_client import BACKEND_ROOT

log = logging.getLogger("studio.upscaler")

TIMEOUT_SECONDS = 30 * 60  # a stuck enlargement is stopped after this long (DESIGN.md §28.3)
MODEL_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth"


class UpscalerError(Exception):
    """Enlarge could not be done. `status` and `code` are the HTTP answer; `hint` says what to do about it, when that is known."""

    status, code = 500, "upscale_failed"

    def __init__(self, detail: str, hint: Optional[str] = None):
        super().__init__(detail)
        self.detail, self.hint = detail, hint


class UpscalerUnavailable(UpscalerError):
    status, code = 503, "upscaler_unavailable"


class UpscaleFailed(UpscalerError):
    status, code = 500, "upscale_failed"


class UpscalerBusy(UpscalerError):
    """Not enough memory right now: try again in a moment (the GPU is being used by something the studio does not control)."""

    status, code = 503, "upscaler_busy"


class UpscaleTimeout(UpscalerError):
    status, code = 504, "upscale_timeout"


@dataclass(frozen=True)
class Availability:
    """Whether Enlarge can run here, and if not, why and what to do (shown by the page, in `GET /api/capabilities`)."""

    available: bool
    model: Optional[str] = None  # the model's file name, when it is known
    reason: Optional[str] = None
    hint: Optional[str] = None

    def as_dict(self) -> dict[str, object]:
        return {"available": self.available, "model": self.model, "reason": self.reason, "hint": self.hint,
                "max_enlargement": fourk.ENLARGE_MAX}


class FakeUpscaler:
    """Stands in for the model with the fake pipeline: enlarges with a bicubic resize, in a thread, and says so in the file."""

    label = "the fake upscaler"

    def availability(self) -> Availability:
        return Availability(True, model="fake")

    @staticmethod
    def _upscale(image: Image.Image, passes: int) -> Image.Image:
        return image.resize((image.width * 2 ** passes, image.height * 2 ** passes), Image.Resampling.BICUBIC)

    async def enlarge(self, src: Path, dst: Path) -> None:
        await asyncio.to_thread(fourk.make_enlarged, src, dst, self._upscale, self.label)


class ModelUpscaler:
    """The real one: a process per enlargement (studio/upscale_job.py)."""

    def __init__(self, settings: Settings, timeout: float = TIMEOUT_SECONDS):
        self.model_path = settings.upscaler_model_path
        self.device = settings.upscaler_device
        self.timeout = timeout

    def availability(self) -> Availability:
        name = self.model_path.name
        if not self.model_path.is_file():
            return Availability(
                False, model=name, reason=f"The upscaler model file is not there: {self.model_path}",
                hint=("Download it once, on the machine that runs the studio, into your Hugging Face cache folder (the container sees it "
                      f"as /models): mkdir -p ~/.cache/huggingface/upscalers && curl -L -o ~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth {MODEL_URL}  "
                      "It is 67 MB; the code is BSD-3-Clause and the model has its own terms, which are yours to read. "
                      "Or set STUDIO_UPSCALER_MODEL to another ×2 upscaler file."))
        for package, what in (("torch", "PyTorch"), ("spandrel", "spandrel (the library that loads the model)")):
            if importlib.util.find_spec(package) is None:
                return Availability(False, model=name, reason=f"{what} is not installed here.",
                                    hint="It is part of the studio image from version 1.11: rebuild it with `docker compose up -d --build`.")
        return Availability(True, model=name)

    def command(self, src: Path, dst: Path) -> list[str]:
        """The command that does one enlargement (a seam for the tests)."""
        return [sys.executable, "-m", "studio.upscale_job", "--model", str(self.model_path), "--device", self.device,
                "--src", str(src), "--dst", str(dst)]

    @staticmethod
    def _environment() -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(BACKEND_ROOT), env.get("PYTHONPATH", "")) if p)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        return env

    async def enlarge(self, src: Path, dst: Path) -> None:
        """Enlarge the picture at `src` into `dst`. Raises fourk.NotEligible, FileNotFoundError, UnidentifiedImageError, OSError (the
        copy could not be written), or an UpscalerError."""
        proc = await asyncio.create_subprocess_exec(
            *self.command(src, dst), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=self._environment())
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            await self._stop(proc, dst)
            raise UpscaleTimeout(f"Enlarging took longer than {self.timeout / 60:g} minutes and was stopped.",
                                 "If it was running on the CPU, a GPU makes it much faster; STUDIO_UPSCALER_DEVICE chooses.") from None
        except asyncio.CancelledError:  # the server is shutting down: do not leave the process behind
            await self._stop(proc, dst)
            raise
        code = proc.returncode
        stdout = out.decode("utf-8", "replace").strip()
        message = self._failure_message(err.decode("utf-8", "replace"))
        if code == 0:
            if stdout:
                log.info("%s", stdout.splitlines()[-1])
            return
        log.warning("enlarge failed (exit code %s): %s", code, message)
        if code == 2:
            raise fourk.NotEligible(message)
        if code == 7:
            raise FileNotFoundError(message)
        if code == 8:
            raise UnidentifiedImageError(message)
        if code == 6:
            raise OSError(message)
        if code == 3:
            raise UpscalerUnavailable(message, "Check the container's GPU and that the image was built with version 1.11 or later.")
        if code == 9:
            raise UpscalerBusy(message, "Enlarge waits for a picture the studio is making; if nothing is generating, something else is using the GPU memory.")
        if code == 4:
            raise UpscaleFailed(message, "Check the model file: it should be Real-ESRGAN's RealESRGAN_x2plus.pth (or another ×2 upscaler).")
        raise UpscaleFailed(message or f"The upscaler stopped unexpectedly (exit code {code}).")

    @staticmethod
    def _failure_message(stderr: str) -> str:
        """The reason the job gave (its last `ENLARGE FAILED:` line), else the last line it printed."""
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith("ENLARGE FAILED:"):
                return line[len("ENLARGE FAILED:"):].strip()
        return lines[-1] if lines else ""

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process, dst: Path) -> None:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        for leftover in dst.parent.glob(dst.name + ".*.part"):  # a half-written file from the killed process
            leftover.unlink(missing_ok=True)


Upscaler = Union[FakeUpscaler, ModelUpscaler]


def make_upscaler(settings: Settings) -> Upscaler:
    return FakeUpscaler() if settings.pipeline == "fake" else ModelUpscaler(settings)
