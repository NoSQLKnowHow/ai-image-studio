"""One Enlarge, in its own process (DESIGN.md §28.3).

    python -m studio.upscale_job --model <file> --device auto|cuda|cpu --src <picture> --dst <copy>

The server starts this for each enlargement: it loads the upscaler model with `spandrel`, enlarges the picture (`studio/fourk.py`
decides how: one or two x2 passes, then one Lanczos pass to the exact size), writes the copy atomically and exits. It is a
separate process so that PyTorch is never loaded into the API process, and so that a crash or an out-of-memory here cannot take down
the image model.

Exit codes (the server turns each into an answer for the page): 0 made; 2 the picture is not eligible (the message says why);
3 the environment (PyTorch or spandrel missing, or CUDA asked for and not there); 4 the model could not be loaded or is not a x2
upscaler; 5 the run failed (out of memory is named as such); 6 the copy could not be written; 7 the picture file is gone; 8 the
picture file could not be read; 9 not enough memory (the GPU is being used by something else, usually a picture being generated);
130 interrupted. On failure the last line on stderr is `ENLARGE FAILED: <reason>`.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, UnidentifiedImageError

from . import fourk
from .tiling import DEFAULT_OVERLAP, DEFAULT_TILE, upscale_tiled

EXIT_OK, EXIT_NOT_ELIGIBLE, EXIT_ENVIRONMENT, EXIT_MODEL, EXIT_FAILED, EXIT_OUTPUT, EXIT_GONE, EXIT_UNREADABLE, EXIT_OUT_OF_MEMORY = 0, 2, 3, 4, 5, 6, 7, 8, 9
OUT_OF_MEMORY = ("Not enough memory for the upscaler right now: something else is using the GPU (a picture being generated, or another program). "
                 "Enlarge waits for the studio's own work, so try again in a moment; if it keeps happening, STUDIO_UPSCALER_DEVICE=cpu "
                 "works without the GPU (it is slow).")


class Problem(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def is_out_of_memory(exc: BaseException) -> bool:
    """CUDA says "out of memory" (as an OutOfMemoryError or, on the GB10, an AcceleratorError), the CPU allocator "can't allocate
    memory", and Python raises MemoryError."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(phrase in text for phrase in ("out of memory", "outofmemory", "memoryerror", "allocate memory"))


def problem_for(exc: BaseException, code: int, message: str) -> Problem:
    """The Problem for a failure that would exit with `code`, unless what happened was running out of memory: that is its own
    answer (a model that "could not be moved to cuda" because the GPU was full is not a damaged model file)."""
    return Problem(EXIT_OUT_OF_MEMORY, OUT_OF_MEMORY) if is_out_of_memory(exc) else Problem(code, message)


def load_engine(model_path: Path, device_choice: str) -> tuple[fourk.UpscaleFunction, str, str]:
    """Load the model and return (the function `fourk.render_enlarged` wants, the model's name for the file's note, the device).
    PyTorch and spandrel are imported here and nowhere else in the studio's server code."""
    try:
        import numpy  # noqa: F401
        import torch
    except ImportError as exc:
        raise Problem(EXIT_ENVIRONMENT, f"A package is missing ({exc.name}); Enlarge needs the studio container, which has PyTorch.") from exc
    try:
        import spandrel
    except ImportError as exc:
        raise Problem(EXIT_ENVIRONMENT, "spandrel is not installed in this container. It is part of the image from version 1.11: "
                                        "rebuild it with `docker compose up -d --build`.") from exc
    if device_choice == "cuda" and not torch.cuda.is_available():
        raise Problem(EXIT_ENVIRONMENT, "STUDIO_UPSCALER_DEVICE=cuda was asked for, but PyTorch cannot see a GPU here.")
    device = torch.device("cuda" if device_choice == "cuda" or (device_choice == "auto" and torch.cuda.is_available()) else "cpu")
    try:
        model = spandrel.ModelLoader().load_from_file(model_path)
    except Exception as exc:  # spandrel raises its own unsupported-model errors; torch raises on a damaged file
        raise problem_for(exc, EXIT_MODEL, f"The upscaler model {model_path.name} could not be loaded: {type(exc).__name__}: {exc}") from exc
    if getattr(model, "purpose", "SR") != "SR":
        raise Problem(EXIT_MODEL, f"{model_path.name} is not an upscaler model (its purpose is {model.purpose}).")
    if int(model.scale) != 2:
        raise Problem(EXIT_MODEL, f"{model_path.name} is a ×{model.scale} model; Enlarge needs a ×2 one "
                                  f"(it runs it once or twice, as the picture needs).")
    try:
        model.to(device, torch.float32).eval()
    except Exception as exc:  # on a full GPU this is where it shows: "CUDA error: out of memory"
        raise problem_for(exc, EXIT_MODEL, f"The upscaler model could not be moved to {device}: {type(exc).__name__}: {exc}") from exc

    def upscale(image: Image.Image, passes: int) -> Image.Image:
        for _ in range(passes):
            image = upscale_tiled(model, image, DEFAULT_TILE, DEFAULT_OVERLAP, device, torch.float32)
        return image

    return upscale, f"{model.architecture.name} ×{model.scale} ({model_path.name})", str(device)


def run(args: argparse.Namespace, load: Optional[Callable[[Path, str], tuple[fourk.UpscaleFunction, str, str]]] = None) -> str:
    """Do one enlargement; returns the line to print on success. Raises Problem. `load` (default: `load_engine`, looked up when
    called) is a seam for the tests."""
    load = load or load_engine
    if not args.model.is_file():
        raise Problem(EXIT_MODEL, f"The upscaler model file is not there: {args.model}")
    started = time.perf_counter()
    try:  # judged on the file first, so that a picture that cannot be enlarged never pays for loading the model
        with Image.open(args.src) as opened:
            fourk.plan_enlarge(opened.width, opened.height)
    except FileNotFoundError as exc:
        raise Problem(EXIT_GONE, f"The picture file is gone: {args.src}") from exc
    except UnidentifiedImageError as exc:
        raise Problem(EXIT_UNREADABLE, "The picture file could not be read.") from exc
    except fourk.NotEligible as exc:
        raise Problem(EXIT_NOT_ELIGIBLE, str(exc)) from exc
    upscale, label, device = load(args.model, args.device)
    try:
        fourk.make_enlarged(args.src, args.dst, upscale, label)
    except fourk.NotEligible as exc:
        raise Problem(EXIT_NOT_ELIGIBLE, str(exc)) from exc
    except FileNotFoundError as exc:
        raise Problem(EXIT_GONE, f"The picture file is gone: {args.src}") from exc
    except UnidentifiedImageError as exc:
        raise Problem(EXIT_UNREADABLE, "The picture file could not be read.") from exc
    except OSError as exc:
        raise Problem(EXIT_OUTPUT, f"The enlarged picture could not be saved: {exc.strerror or exc}") from exc
    except Exception as exc:  # the model or its tiling failed
        raise problem_for(exc, EXIT_FAILED, f"Enlarging failed: {type(exc).__name__}: {exc}") from exc
    size = fourk.file_size(args.dst)
    shape = f"{size[0]}x{size[1]}" if size else "?"
    return f"ENLARGED {shape} in {time.perf_counter() - started:.1f} s on {device} with {label}"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Enlarge one picture to the 4K frame with an upscaler model (DESIGN.md §28).")
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--dst", required=True, type=Path)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = parser().parse_args(argv)
        print(run(args), flush=True)
    except Problem as problem:
        print(f"ENLARGE FAILED: {problem}", file=sys.stderr, flush=True)
        return problem.code
    except KeyboardInterrupt:
        print("ENLARGE FAILED: interrupted", file=sys.stderr, flush=True)
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        print(f"ENLARGE FAILED: unexpected {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
