#!/usr/bin/env python3
"""Does an ESRGAN-class upscaler run on this machine, and how fast? (DESIGN.md §27.5, route B of Make 4K)

This is a probe, not a feature: run it once on the Spark and tell me what it printed. It loads one upscaler model with
`spandrel` (pure Python, so there is no ARM64 build to go wrong), upscales a picture with it, and reports the device,
the time and the memory. If Make 4K accepts the picture (any shape up to a 2x enlargement) it also makes two 4K files from it,
one the way Make 4K does it today (a Lanczos resize) and one from the model's output, so you can look at them side by side.

Nothing in the studio uses this script, and it installs nothing: the studio image contains neither this script nor
`spandrel`. Copy the script into the running container and install spandrel there (both last until the container is
recreated, which is what you want from a probe):

  docker compose cp scripts/upscale_probe.py studio:/tmp/upscale_probe.py
  docker compose exec studio pip install --user spandrel
  docker compose exec studio python /tmp/upscale_probe.py --model /models/upscalers/RealESRGAN_x2plus.pth \\
      --image /data/images/<run id>/0.png --out /data/upscale-probe

  # A quick first check (a few seconds): only the top-left 512x288 of the picture
  docker compose exec studio python /tmp/upscale_probe.py --model /models/upscalers/RealESRGAN_x2plus.pth --crop 512x288

`/models` is your Hugging Face cache folder on the host (HF_CACHE_DIR, by default ~/.cache/huggingface), so put the model
file at ~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth. Real-ESRGAN's x2 model is a 64 MB file from the project's
release page (BSD-3-Clause licence on the code; read the model's terms yourself):
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth
Without --image a synthetic 2752x1536 test picture is made, which is enough to prove the model runs but tells you little
about how it looks: a picture the studio made is the real test.

Output, in --out: input.png (what was upscaled), model-x<scale>.png (the model's own output), and for a 16:9 picture
4k-lanczos.png and 4k-from-model.png (for a picture Make 4K accepts). Exit codes: 0 ok, 1 unexpected error, 2 bad arguments, 3 environment problem
(a package is missing, or CUDA was asked for and is not there), 4 the model could not be loaded, 5 upscaling failed,
6 output (disk) problem, 130 interrupted.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "backend", Path("/app/backend")):  # a checkout, or the studio image
    if (candidate / "studio").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:  # the tiling is the studio's own (Enlarge uses it too), so what is probed is what runs
    from studio.tiling import DEFAULT_OVERLAP, DEFAULT_TILE, Tile, padding_needed, tile_boxes  # noqa: F401  (re-exported)
    from studio.tiling import upscale_tiled as upscale  # noqa: F401  (the probe's name for it)
except ImportError:  # an image from before 1.11, or a copy of this script with no checkout beside it
    print("PROBE FAILED: the studio's code (studio/tiling.py, version 1.11 or later) is not importable here. Run this inside a studio "
          "container built from version 1.11 or later, or from a checkout of the repository.", file=sys.stderr)
    sys.exit(3)


# ------------------------------------------------------------------ pure arithmetic (no torch needed)
def parse_size(text: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a size like 512x288") from None
    if width < 8 or height < 8:
        raise argparse.ArgumentTypeError("a size must be at least 8x8")
    return width, height


def synthetic_picture(width: int = 2752, height: int = 1536) -> Image.Image:
    """A deterministic test picture with smooth colour, fine lines and text, so an upscaler has something to do. A smoke
    test only: it shows that the model runs, not how it treats a real picture."""
    picture = Image.linear_gradient("L").resize((width, height)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    for x in range(0, width, 24):
        draw.line([(x, 0), (x, height)], fill=(40 + x * 150 // width, 90, 160), width=1)
    for y in range(0, height, 24):
        draw.line([(0, y), (width, y)], fill=(200, 60 + y * 150 // height, 90), width=1)
    for i in range(8):
        radius = min(width, height) // 10 + i * 17
        centre = (width // 2 + (i - 4) * 90, height // 2)
        draw.ellipse([centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius], outline=(250, 250, 240), width=3)
    draw.text((width // 12, height // 12), "upscale probe: fine text, thin lines and circles", fill=(255, 255, 255))
    return picture


def meminfo_available_gb() -> Optional[float]:
    """Free memory the system could give out, in GB. On the Spark the CPU and the GPU share one pool, so this is the number
    that matters (nvidia-smi says N/A for memory there)."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1_048_576
    except (OSError, ValueError, IndexError):
        pass
    return None


# ------------------------------------------------------------------ the model (loaded in run(); the tiling is studio/tiling.py)
# ------------------------------------------------------------------ the command
class Problem(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, type=Path, help="an upscaler model file (.pth or .safetensors) that spandrel can load")
    ap.add_argument("--image", type=Path, help="the picture to upscale (default: a synthetic 2752x1536 test picture)")
    ap.add_argument("--out", type=Path, default=Path("upscale-probe"), help="where the results go (default: ./upscale-probe)")
    ap.add_argument("--crop", type=parse_size, metavar="WxH", help="only upscale the top-left WxH of the picture: a quick first check")
    ap.add_argument("--tile", type=int, default=DEFAULT_TILE, help=f"tile size in source pixels (default {DEFAULT_TILE})")
    ap.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP, help=f"context around each tile (default {DEFAULT_OVERLAP})")
    ap.add_argument("--dtype", choices=("fp32", "fp16", "bf16"), default="fp32", help="number format (default fp32; fp16 and bf16 are faster if the model allows)")
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto", help="auto uses the GPU when there is one")
    return ap


def say(text: str = "") -> None:
    print(text, flush=True)


def run(args: argparse.Namespace) -> None:
    try:
        import numpy  # noqa: F401
        import torch
    except ImportError as exc:
        raise Problem(3, f"A package is missing ({exc.name}). Run this inside the studio container, which has PyTorch.") from exc
    try:
        import spandrel
    except ImportError as exc:
        raise Problem(3, "spandrel is not installed. Install it with:  pip install --user spandrel  "
                         "(in the studio container: docker compose exec studio pip install --user spandrel)") from exc

    if args.device == "cuda" and not torch.cuda.is_available():
        raise Problem(3, "--device cuda was asked for, but PyTorch cannot see a GPU here.")
    device = torch.device("cuda" if args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available()) else "cpu")
    dtype = {"fp32": torch.float32, "fp16": torch.float16, "bf16": torch.bfloat16}[args.dtype]
    if args.tile < 64 or args.overlap < 0 or args.overlap * 2 >= args.tile:
        raise Problem(2, "--tile must be at least 64, and --overlap at least 0 and under half of it.")
    if not args.model.is_file():
        raise Problem(2, f"No such model file: {args.model}")

    say("== this machine")
    say(f"  python {sys.version.split()[0]}   torch {torch.__version__}   CUDA {torch.version.cuda}   spandrel {getattr(spandrel, '__version__', '?')}")
    if device.type == "cuda":
        capability = torch.cuda.get_device_capability()
        say(f"  GPU: {torch.cuda.get_device_name()}  (compute capability {capability[0]}.{capability[1]})")
        torch.cuda.reset_peak_memory_stats()
    else:
        say("  no GPU is being used: this is the CPU, which is slow for a whole picture")
    free_before = meminfo_available_gb()
    if free_before is not None:
        say(f"  memory available to the system: {free_before:.1f} GB")

    say("== the model")
    started = time.perf_counter()
    try:
        model = spandrel.ModelLoader().load_from_file(args.model)
    except Exception as exc:  # spandrel raises its own unsupported-model errors, torch raises on a bad file
        raise Problem(4, f"The model could not be loaded: {type(exc).__name__}: {exc}") from exc
    if getattr(model, "purpose", "SR") != "SR":
        raise Problem(4, f"This is not an upscaler (its purpose is {model.purpose}).")
    load_seconds = time.perf_counter() - started
    say(f"  {args.model.name}: {model.architecture.name}, x{model.scale}, tags {list(model.tags)}   loaded in {load_seconds:.1f} s")
    allowed = {"fp32": True, "fp16": model.supports_half, "bf16": model.supports_bfloat16}[args.dtype]
    if not allowed:
        say(f"  this model does not support {args.dtype}; using fp32")
        dtype = torch.float32
    try:
        model.to(device, dtype).eval()
    except Exception as exc:
        raise Problem(4, f"The model could not be moved to {device} as {args.dtype}: {type(exc).__name__}: {exc}") from exc

    say("== the picture")
    try:
        picture = Image.open(args.image).convert("RGB") if args.image else synthetic_picture()
    except (OSError, ValueError) as exc:
        raise Problem(2, f"The picture could not be read: {exc}") from exc
    if args.crop:
        picture = picture.crop((0, 0, min(args.crop[0], picture.width), min(args.crop[1], picture.height)))
    say(f"  {picture.width}x{picture.height}" + ("  (a crop)" if args.crop else "  (synthetic)" if not args.image else ""))
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        picture.save(args.out / "input.png")
    except OSError as exc:
        raise Problem(6, f"Could not write to {args.out}: {exc.strerror or exc}") from exc

    say("== upscaling")
    times: list[float] = []

    def progress(number: int, total: int, seconds: float) -> None:
        times.append(seconds)
        if number == 1 or number == total or number % 10 == 0:
            say(f"  tile {number} of {total}: {seconds:.2f} s")

    started = time.perf_counter()
    try:
        result = upscale(model, picture, args.tile, args.overlap, device, dtype, progress)
    except Exception as exc:
        raise Problem(5, f"Upscaling failed: {type(exc).__name__}: {exc}"
                         + ("  (out of memory: try a smaller --tile)" if "out of memory" in str(exc).lower() else "")) from exc
    total_seconds = time.perf_counter() - started
    scale = int(model.scale)

    try:
        result.save(args.out / f"model-x{scale}.png")
    except OSError as exc:
        raise Problem(6, f"Could not write to {args.out}: {exc.strerror or exc}") from exc
    written = ["input.png", f"model-x{scale}.png"]
    notes = four_k_files(picture, result, scale, args.out, written)

    say("== result")
    detail = f"{len(times)} tile{'s' if len(times) != 1 else ''} of up to {args.tile}; the first took {times[0]:.2f} s and includes one-time set-up"
    if len(times) > 1:
        detail += f", the rest averaged {sum(times[1:]) / len(times[1:]):.2f} s"
    say(f"  {picture.width}x{picture.height} -> {result.width}x{result.height} in {total_seconds:.1f} s ({detail})")
    if device.type == "cuda":
        say(f"  peak GPU memory held by PyTorch: {torch.cuda.max_memory_allocated() / 1_073_741_824:.2f} GB")
    free_after = meminfo_available_gb()
    if free_before is not None and free_after is not None:
        say(f"  memory available to the system: {free_before:.1f} GB before, {free_after:.1f} GB after")
    for note in notes:
        say(f"  {note}")
    say(f"  written to {args.out}: {', '.join(written)}")
    say("PROBE OK")


def four_k_files(picture: Image.Image, result: Image.Image, scale: int, out: Path, written: list[str]) -> list[str]:
    """For a picture Make 4K accepts, the two 4K files to compare. Returns notes for the report."""
    try:
        from studio import fourk
    except ImportError:
        return ["the studio's Make 4K code is not importable here, so no 4K comparison files were made"]
    try:
        plan = fourk.plan_4k(picture.width, picture.height)
    except fourk.NotEligible as exc:
        return [f"no 4K comparison files: {exc}"]
    try:
        fourk.make_4k(out / "input.png", out / "4k-lanczos.png")
        box = tuple(value * scale for value in plan.box)
        result.resize((plan.out_width, plan.out_height), Image.Resampling.LANCZOS, box=box).save(out / "4k-from-model.png", compress_level=1)
    except OSError as exc:
        raise Problem(6, f"Could not write to {out}: {exc.strerror or exc}") from exc
    written += ["4k-lanczos.png", "4k-from-model.png"]
    return [f"4k-lanczos.png is what Make 4K makes today; 4k-from-model.png is the model's output cut and sized the same way (to {plan.out_width}x{plan.out_height}): compare them at 100%"]


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = parser().parse_args(argv)
        run(args)
    except Problem as problem:
        print(f"PROBE FAILED: {problem}", file=sys.stderr)
        return problem.code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception as exc:  # a probe should say what broke, not show a traceback first
        print(f"PROBE FAILED: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
