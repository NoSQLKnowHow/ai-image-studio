"""Deterministic stand-in for Qwen-Image (DESIGN.md §15).

Makes the whole stack testable without a GPU or model weights. Same seed, prompt and
size always give the same pixels. Faults can be injected per run with directives in
the prompt, e.g. "a cat [fake:oom]" or "a cat [fake:crash@1]" (only on image index 1):

    [fake:error]  raise a generic pipeline error
    [fake:oom]    raise an out-of-memory error
    [fake:crash]  kill the worker process abruptly (exit code 3)
    [fake:noise]  write junk to stdout, to prove the protocol channel is protected

A model load failure is simulated by setting STUDIO_FAKE_LOAD_FAIL=1 for the worker (`once` fails only the first
load, so a retry on the same worker can succeed, DESIGN.md §25.3), and a slower load (so a test can
see the loading state, DESIGN.md §25.3) by setting STUDIO_FAKE_LOAD_DELAY_MS.

Edits (DESIGN.md §21.8): the result follows the *last* input's shape when no size is given (at about
RESOLUTION x RESOLUTION pixels, with the pipeline's own arithmetic), and a strip of numbered thumbnails of the
inputs, in the order they were given, is drawn along the bottom edge. Tests read the strip back to check the
order, the count and the shape rule without a GPU (see `strip_cells`).
"""

from __future__ import annotations

import os
import random
import re
import sys
import textwrap
import time
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .. import presets as P
from .base import ImageJob, OutOfMemory, PipelineError, PipelineLoadError, StepCallback, load_inputs

_DIRECTIVE = re.compile(r"\[fake:(error|oom|crash|noise)(?:@(\d+))?\]", re.IGNORECASE)


def parse_directives(prompt: str) -> list[tuple[str, int | None]]:
    return [(m.group(1).lower(), int(m.group(2)) if m.group(2) else None) for m in _DIRECTIVE.finditer(prompt)]


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow without scalable default font
        return ImageFont.load_default()


def render_fake_image(
    *, prompt: str, width: int, height: int, seed: int, index: int, count: int, steps: int, transparent: bool
) -> Image.Image:
    rng = random.Random(seed)
    c1 = tuple(rng.randrange(30, 226) for _ in range(3))
    c2 = tuple(255 - c for c in c1)

    if transparent:
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    else:
        gradient = Image.linear_gradient("L").resize((width, height))
        img = ImageOps.colorize(gradient, c1, c2).convert("RGB")
    draw = ImageDraw.Draw(img)

    short = min(width, height)
    for _ in range(6):
        r = rng.randint(short // 12, short // 4)
        x, y = rng.randint(0, width), rng.randint(0, height)
        color = tuple(rng.randrange(0, 256) for _ in range(3))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color + ((230,) if transparent else ()))

    font_size = max(12, short // 28)
    font = _font(font_size)
    pad = font_size
    chars_per_line = max(16, int((width - 4 * pad) / (font_size * 0.55)))
    lines = [
        "FAKE PIPELINE - test image",
        f"image {index + 1}/{count}  seed {seed}  {width}x{height}  {steps} steps",
        "",
        *textwrap.wrap(prompt, chars_per_line)[:8],
    ]
    line_h = int(font_size * 1.35)
    box_h = line_h * len(lines) + 2 * pad
    top = (height - box_h) // 2
    panel = (0, 0, 0, 200) if transparent else (24, 24, 24)
    draw.rectangle((pad, top, width - pad, top + box_h), fill=panel)
    for i, line in enumerate(lines):
        draw.text((2 * pad, top + pad + i * line_h), line, fill=(255, 255, 255) + ((255,) if transparent else ()), font=font)
    return img


def strip_cells(width: int, height: int, count: int) -> list[tuple[int, int, int, int]]:
    """Where the fake pipeline draws the numbered thumbnails of an edit's inputs: (left, top, right, bottom) of
    each, in input order, along the bottom edge. Always inside the image and never overlapping, however small or
    narrow it is and however many inputs there are. Public so tests can look at the right pixels."""
    short = min(width, height)
    pad = max(1, min(max(4, short // 40), width // (4 * max(count, 1))))
    cell = max(1, min(short // 6, (width - pad * (count + 1)) // max(count, 1)))
    top = max(0, height - pad - cell)
    return [(pad + i * (cell + pad), top, pad + i * (cell + pad) + cell, top + cell) for i in range(count)]


def draw_input_strip(image: Image.Image, inputs: list[Image.Image]) -> Image.Image:
    draw = ImageDraw.Draw(image)
    for i, (box, source) in enumerate(zip(strip_cells(image.width, image.height, len(inputs)), inputs)):
        size = (box[2] - box[0], box[3] - box[1])
        thumb = source.convert("RGBA").resize(size)
        image.paste(thumb, box[:2], thumb)
        if size[0] >= 24:  # room for the number in the corner; smaller thumbnails are left plain
            label = box[0] + 2, box[1] + 2, box[0] + 2 + size[0] // 3, box[1] + 2 + size[1] // 3
            draw.rectangle(label, fill=(0, 0, 0, 255) if image.mode == "RGBA" else (0, 0, 0))
            draw.text((label[0] + 1, label[1]), str(i + 1), fill=(255, 255, 255), font=_font(max(8, size[1] // 4)))
    return image


class FakePipeline:
    name = "fake"
    SUPPORTS = {"negative_prompt": True, "cfg_scale": True, "step_progress": True, "transparent": True, "edit": True,
                "resolution": True}

    def __init__(self, step_delay_ms: int = 30, load_delay_ms: int = 200):
        self.step_delay = step_delay_ms / 1000
        self.load_delay = load_delay_ms / 1000
        self._failed_once = False

    @classmethod
    def probe(cls) -> dict[str, Any]:
        return {"pipeline": cls.name, "supports": dict(cls.SUPPORTS), "device": {"name": "fake (no GPU used)"}}

    def _load_delay(self) -> float:
        raw = os.environ.get("STUDIO_FAKE_LOAD_DELAY_MS", "").strip()
        try:
            return max(0, int(raw)) / 1000 if raw else self.load_delay
        except ValueError:
            return self.load_delay

    def load(self) -> dict[str, Any]:
        fail = os.environ.get("STUDIO_FAKE_LOAD_FAIL", "").strip().lower()
        if fail == "once" and not self._failed_once:
            self._failed_once = True
            fail = "1"
        if fail in ("1", "true", "yes", "on"):
            raise PipelineLoadError(
                "Simulated model load failure (STUDIO_FAKE_LOAD_FAIL is set).",
                hint="Unset STUDIO_FAKE_LOAD_FAIL to let the fake pipeline load.",
            )
        time.sleep(self._load_delay())
        return {"pipeline": self.name, "supports": dict(self.SUPPORTS)}

    def generate(self, job: ImageJob, index: int, seed: int, on_step: StepCallback) -> Image.Image:
        active = {kind for kind, at in parse_directives(job.prompt) if at is None or at == index}
        inputs = load_inputs(job.input_paths) if job.input_paths else []
        for step in range(1, job.steps + 1):
            if self.step_delay:
                time.sleep(self.step_delay)
            on_step(step, job.steps)
            if step == 1 and active:
                self._inject(active)
        width, height = job.width, job.height
        if not (width and height) and inputs:  # Auto: the shape of the last image, as the real pipeline does it
            resolution = job.resolution or P.DEFAULT_RESOLUTION
            width, height = P.calculate_dimensions(resolution * resolution, inputs[-1].width / inputs[-1].height)
        image = render_fake_image(
            prompt=job.prompt, width=width or 1024, height=height or 1024, seed=seed, index=index,
            count=len(job.seeds), steps=job.steps, transparent=job.transparent,
        )
        return draw_input_strip(image, inputs) if inputs else image

    @staticmethod
    def _inject(active: set[str]) -> None:
        if "noise" in active:
            print("fake pipeline: stray print() to stdout")
            sys.stdout.write("fake pipeline: stray sys.stdout.write\n")
            sys.stdout.flush()
            os.write(1, b"fake pipeline: raw write to file descriptor 1\n")
        if "crash" in active:
            os._exit(3)
        if "oom" in active:
            raise OutOfMemory(
                "Simulated out-of-memory ([fake:oom]).",
                hint="Use a smaller size or fewer images per click.",
            )
        if "error" in active:
            raise PipelineError("Simulated pipeline failure ([fake:error]).")
