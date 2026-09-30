#!/usr/bin/env python3
"""Generate and edit images with Qwen-Image-2.1 from the command line.

One script, two modes (both use the same QwenImage21Pipeline):

  generate   text prompt                -> new image
  edit       input image + instruction  -> edited image

Examples:
  # Text to image (2048x2048, 40 steps, seed 42 unless told otherwise)
  ./qwen_image.py generate "A bright neon shop sign, rainy day, wet pavement"

  # Wide image with a custom filename label and a random seed, three variations
  ./qwen_image.py generate "Halloween town at dawn" --aspect-ratio 16:9 \\
      --name halloween-town --random-seed --num-images 3

  # Transparent PNG (wraps the prompt in the model card's RGBA format)
  ./qwen_image.py generate "A cute cartoon dragon sticker" --transparent

  # Edit an existing image
  ./qwen_image.py edit "Change the background to a sunset beach" --image input.png

  # Long prompts: read them from a file or from stdin
  ./qwen_image.py generate --prompt-file prompt.txt
  cat prompt.txt | ./qwen_image.py generate -

  # Check what would happen without loading the model
  ./qwen_image.py generate "test" --dry-run

Output files are PNGs named
  <mode>_[<input>_]<prompt-words>_<W>x<H>_s<seed>_[rgba_]<YYYYmmdd-HHMMSS>.png
and carry the prompt, seed, steps and model in their PNG text metadata.

Exit codes: 0 ok, 1 unexpected error, 2 bad arguments, 3 environment problem
(missing packages / no CUDA), 4 model load failed, 5 generation failed,
6 output (disk) problem, 130 interrupted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import gc
import inspect
import logging
import os
import random
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("qwen_image")

DEFAULT_MODEL = "Qwen/Qwen-Image-2.1"
DEFAULT_STEPS = 40
DEFAULT_SEED = 42
DEFAULT_ASPECT = "1:1"

# From the model card ("Supported Aspect Ratios"): (width, height)
ASPECT_RATIOS = {
    "1:1": (2048, 2048),
    "4:3": (2400, 1792),
    "3:4": (1792, 2400),
    "3:2": (2528, 1696),
    "2:3": (1696, 2528),
    "16:9": (2752, 1536),
    "9:16": (1536, 2752),
}

# From the model card ("Transparent Image Generation (RGBA)")
TRANSPARENT_PREFIX = "This is an RGBA image with transparency."
TRANSPARENT_SUFFIX = "The image has alpha channel and the background is transparent."

SIZE_MULTIPLE = 16
MAX_SEED = 2**32 - 1
MAX_SLUG_WORDS = 6
MAX_SLUG_BYTES = 60  # keeps filenames well under the 255-byte filesystem limit
MAX_STEM_BYTES = 40
# Skipped when building the filename label so the subject words fit (use --name to choose your own).
FILLER_WORDS = frozenset(
    "a an the of in on at to and or with by for from into is are was very".split()
)


# --------------------------------------------------------------------------- #
# Errors (each maps to a distinct process exit code)
# --------------------------------------------------------------------------- #
class ScriptError(Exception):
    exit_code = 1

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.hint = hint


class UsageError(ScriptError):
    exit_code = 2


class SetupError(ScriptError):
    exit_code = 3


class ModelLoadError(ScriptError):
    exit_code = 4


class GenerationError(ScriptError):
    exit_code = 5


class OutputError(ScriptError):
    exit_code = 6


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def _int_arg(minimum: int, maximum: Optional[int] = None):
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
        if value < minimum or (maximum is not None and value > maximum):
            bound = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
            raise argparse.ArgumentTypeError(f"{value} is out of range (must be {bound})")
        return value

    return parse


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not value > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "prompt", nargs="?",
        help="text prompt; use '-' to read it from stdin (or use --prompt-file)",
    )
    common.add_argument("--prompt-file", metavar="FILE", help="read the prompt from a UTF-8 text file")
    common.add_argument("--negative-prompt", metavar="TEXT", help="things to steer away from")
    common.add_argument(
        "--cfg-scale", type=_positive_float, metavar="X",
        help="guidance strength, passed to the pipeline as true_cfg_scale "
             "(unset = model default; rejected early if the pipeline has no such argument)",
    )
    common.add_argument("--steps", type=_int_arg(1, 500), default=DEFAULT_STEPS,
                        help="denoising steps (default: %(default)s)")

    seed = common.add_mutually_exclusive_group()
    seed.add_argument("--seed", type=_int_arg(0, MAX_SEED), default=DEFAULT_SEED,
                      help="random seed (default: %(default)s); image N of a batch uses seed+N")
    seed.add_argument("--random-seed", action="store_true", help="pick a random seed (it is printed and put in the filename)")
    common.add_argument("--num-images", type=_int_arg(1, 64), default=1, metavar="N",
                        help="how many images to make, one after another (default: %(default)s)")

    size = common.add_argument_group(
        "size", "generate defaults to 2048x2048 (1:1). edit keeps the pipeline's own sizing "
                "unless you set one of these.")
    size.add_argument("--aspect-ratio", choices=list(ASPECT_RATIOS),
                      help="model-card size preset: " + ", ".join(f"{k}={w}x{h}" for k, (w, h) in ASPECT_RATIOS.items()))
    size.add_argument("--width", type=_int_arg(64), help="custom width in pixels (needs --height)")
    size.add_argument("--height", type=_int_arg(64), help="custom height in pixels (needs --width)")

    out = common.add_argument_group("output")
    where = out.add_mutually_exclusive_group()
    where.add_argument("-o", "--output", metavar="FILE.png",
                       help="exact output file (a batch adds _01, _02, ... before the extension)")
    where.add_argument("--output-dir", metavar="DIR", default=".",
                       help="directory for auto-named files (default: current directory; created if missing)")
    out.add_argument("--name", metavar="LABEL", help="short label for the filename instead of one derived from the prompt")
    out.add_argument("--overwrite", action="store_true", help="allow --output to replace an existing file")

    run = common.add_argument_group("runtime")
    run.add_argument("--model", default=DEFAULT_MODEL, metavar="ID_OR_PATH",
                     help="Hugging Face model id or local directory (default: %(default)s)")
    run.add_argument("--local-files-only", action="store_true",
                     help="never touch the network; use only the local Hugging Face cache / --model directory")
    run.add_argument("--cpu-offload", action="store_true",
                     help="enable_model_cpu_offload() to save GPU memory (needs the 'accelerate' package; slower)")
    run.add_argument("--dry-run", action="store_true", help="validate arguments and show the plan without loading the model")
    run.add_argument("-v", "--verbose", action="store_true", help="debug logging and full tracebacks")

    examples = "Examples:" + __doc__.split("Examples:", 1)[1].split("Output files", 1)[0]
    parser = argparse.ArgumentParser(
        prog="qwen_image.py",
        description="Generate and edit images with Qwen-Image-2.1.",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="{generate,edit}")

    gen = sub.add_parser("generate", parents=[common], help="text -> image")
    gen.add_argument("--transparent", action="store_true",
                     help="ask for a transparent-background RGBA image (wraps the prompt in the model card's recommended format)")

    edit = sub.add_parser("edit", parents=[common], help="edit an existing image with a text instruction")
    edit.add_argument("-i", "--image", required=True, metavar="FILE", help="input image to edit")
    return parser


# --------------------------------------------------------------------------- #
# Job: everything validated up front, before any heavy import or model load
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    mode: str
    prompt: str  # what is sent to the model
    user_prompt: str  # what the user typed (used for filenames)
    steps: int
    seed: int
    num_images: int
    width: Optional[int]
    height: Optional[int]
    model: str
    timestamp: str
    output_dir: Path
    output_file: Optional[Path] = None
    name: Optional[str] = None
    input_path: Optional[Path] = None
    negative_prompt: Optional[str] = None
    cfg_scale: Optional[float] = None
    transparent: bool = False
    overwrite: bool = False
    cpu_offload: bool = False
    local_files_only: bool = False
    dry_run: bool = False


def resolve_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None and args.prompt_file is not None:
        raise UsageError("Give the prompt either on the command line or with --prompt-file, not both.")

    if args.prompt_file is not None:
        path = Path(args.prompt_file).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise UsageError(f"Prompt file not found: {path}") from None
        except UnicodeDecodeError:
            raise UsageError(f"Prompt file is not valid UTF-8: {path}") from None
        except OSError as exc:
            raise UsageError(f"Cannot read prompt file {path}: {exc.strerror or exc}") from None
    elif args.prompt == "-":
        if sys.stdin is None or sys.stdin.isatty():
            raise UsageError("Prompt '-' means 'read stdin', but nothing is piped in.")
        text = sys.stdin.read()
    elif args.prompt is not None:
        text = args.prompt
    else:
        raise UsageError(
            "No prompt given.",
            hint=f'Pass it as an argument: qwen_image.py {args.command} "your prompt" (or use --prompt-file / stdin).',
        )

    text = text.strip()
    if not text:
        raise UsageError("The prompt is empty.")
    return text


def resolve_size(args: argparse.Namespace) -> tuple[Optional[int], Optional[int]]:
    if args.aspect_ratio and (args.width or args.height):
        raise UsageError("Use either --aspect-ratio or --width/--height, not both.")
    if args.aspect_ratio:
        return ASPECT_RATIOS[args.aspect_ratio]
    if (args.width is None) != (args.height is None):
        raise UsageError("--width and --height must be given together.")
    if args.width is not None:
        if args.width % SIZE_MULTIPLE or args.height % SIZE_MULTIPLE:
            log.warning("%dx%d is not a multiple of %d; the pipeline may round or reject it.",
                        args.width, args.height, SIZE_MULTIPLE)
        if args.command == "generate" and (args.width, args.height) not in ASPECT_RATIOS.values():
            log.warning("%dx%d is not one of the model card's supported sizes; quality may vary.",
                        args.width, args.height)
        return args.width, args.height
    if args.command == "generate":
        return ASPECT_RATIOS[DEFAULT_ASPECT]
    return None, None  # edit: let the pipeline size the result from the input image


def apply_transparent_format(prompt: str) -> str:
    if "rgba" in prompt.lower():
        log.info("Prompt already mentions RGBA; using it as-is for --transparent.")
        return prompt
    if prompt[-1] not in ".!?":
        prompt += "."
    return f"{TRANSPARENT_PREFIX} {prompt} {TRANSPARENT_SUFFIX}"


def build_job(args: argparse.Namespace) -> Job:
    user_prompt = resolve_prompt(args)
    transparent = bool(getattr(args, "transparent", False))
    width, height = resolve_size(args)

    seed = random.SystemRandom().randrange(0, MAX_SEED - args.num_images) if args.random_seed else args.seed

    output_file = None
    if args.output:
        output_file = Path(args.output).expanduser()
        if output_file.suffix == "":
            output_file = output_file.with_suffix(".png")
        elif output_file.suffix.lower() != ".png":
            raise UsageError(f"--output must end in .png (got {output_file.suffix!r}); files are always saved as PNG.")
        if output_file.is_dir():
            raise UsageError(f"--output {output_file} is a directory; use --output-dir for that.")

    input_path = None
    if args.command == "edit":
        input_path = Path(args.image).expanduser()
        if not input_path.exists():
            raise UsageError(f"Input image not found: {input_path}")
        if not input_path.is_file():
            raise UsageError(f"Input image is not a file: {input_path}")

    model = args.model
    if model.startswith(("/", "./", "../", "~")) and not Path(model).expanduser().exists():
        raise UsageError(f"Local model path does not exist: {model}")
    if model.startswith("~"):
        model = str(Path(model).expanduser())

    return Job(
        mode=args.command,
        prompt=apply_transparent_format(user_prompt) if transparent else user_prompt,
        user_prompt=user_prompt,
        steps=args.steps,
        seed=seed,
        num_images=args.num_images,
        width=width,
        height=height,
        model=model,
        timestamp=dt.datetime.now().strftime("%Y%m%d-%H%M%S"),
        output_dir=Path(args.output_dir).expanduser(),
        output_file=output_file,
        name=args.name,
        input_path=input_path,
        negative_prompt=args.negative_prompt,
        cfg_scale=args.cfg_scale,
        transparent=transparent,
        overwrite=args.overwrite,
        cpu_offload=args.cpu_offload,
        local_files_only=args.local_files_only,
        dry_run=args.dry_run,
    )


# --------------------------------------------------------------------------- #
# Output naming and writing
# --------------------------------------------------------------------------- #
def slugify(text: str, max_bytes: int = MAX_SLUG_BYTES, max_words: int = MAX_SLUG_WORDS) -> str:
    """Filesystem-safe label from the first few meaningful words. Keeps non-Latin letters."""
    out = ""
    all_words = [w for w in re.split(r"[\W_]+", text.casefold()) if w]
    words = ([w for w in all_words if w not in FILLER_WORDS] or all_words)[:max_words]
    for word in words:
        candidate = f"{out}-{word}" if out else word
        if len(candidate.encode("utf-8")) > max_bytes:
            if not out:  # a single enormous word
                out = candidate.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
            break
        out = candidate
    return out or "untitled"


def auto_filename(job: Job, size: Optional[tuple[int, int]], seed: int) -> str:
    parts = [job.mode]
    if job.input_path is not None:
        parts.append(slugify(job.input_path.stem, MAX_STEM_BYTES, max_words=4))
    parts.append(slugify(job.name) if job.name else slugify(job.user_prompt))
    parts.append(f"{size[0]}x{size[1]}" if size else "WxH")
    parts.append(f"s{seed}")
    if job.transparent:
        parts.append("rgba")
    parts.append(job.timestamp)
    return "_".join(parts) + ".png"


def explicit_path(job: Job, index: int) -> Path:
    assert job.output_file is not None
    if job.num_images == 1:
        return job.output_file
    return job.output_file.with_name(f"{job.output_file.stem}_{index + 1:02d}{job.output_file.suffix}")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OutputError(f"Could not find a free filename next to {path}.")


def output_path(job: Job, index: int, size: Optional[tuple[int, int]]) -> Path:
    if job.output_file:
        return explicit_path(job, index)
    path = job.output_dir / auto_filename(job, size, job.seed + index)
    return path if job.overwrite else unique_path(path)


def check_output_clashes(job: Job) -> None:
    if not job.output_file or job.overwrite:
        return
    for i in range(job.num_images):
        path = explicit_path(job, i)
        if path.exists():
            raise UsageError(f"Output file already exists: {path}", hint="Use --overwrite, or pick another --output.")


def prepare_output_dir(job: Job) -> None:
    """Fail now, not after a 20-minute model load, if we can't write results."""
    directory = (job.output_file.parent if job.output_file else job.output_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
    except OSError as exc:
        raise OutputError(f"Cannot write to output directory {directory}: {exc.strerror or exc}") from exc


def save_image(rt: "Runtime", job: Job, image: Any, path: Path, seed: int) -> None:
    meta = rt.PngInfo()
    fields = {
        "Software": "qwen_image.py",
        "mode": job.mode,
        "model": job.model,
        "prompt": job.prompt,
        "seed": seed,
        "steps": job.steps,
        "size": f"{image.size[0]}x{image.size[1]}",
    }
    if job.negative_prompt:
        fields["negative_prompt"] = job.negative_prompt
    if job.cfg_scale is not None:
        fields["cfg_scale"] = job.cfg_scale
    if job.input_path is not None:
        fields["source_image"] = job.input_path.name
    for key, value in fields.items():
        meta.add_text(key, str(value))

    partial = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(partial, format="PNG", pnginfo=meta)
        os.replace(partial, path)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise OutputError(f"Disk full while writing {path}.") from exc
        raise OutputError(f"Could not save {path}: {exc.strerror or exc}") from exc
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise OutputError(f"Could not encode/save {path}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Runtime (heavy imports live here so --help and --dry-run work anywhere)
# --------------------------------------------------------------------------- #
@dataclass
class Runtime:
    torch: Any
    Image: Any
    ImageOps: Any
    PngInfo: Any
    pipeline_cls: Any


def import_runtime() -> Runtime:
    try:
        import torch
    except ImportError as exc:
        raise SetupError("PyTorch is not installed in this Python environment.",
                         hint="Install a CUDA-enabled PyTorch build for your platform (DGX Spark: NVIDIA's PyTorch container or its aarch64 CUDA wheels).") from exc

    try:
        from PIL import Image, ImageOps
        from PIL.PngImagePlugin import PngInfo
    except ImportError as exc:
        raise SetupError("Pillow is not installed.", hint="pip install pillow") from exc

    try:
        import diffusers
    except ImportError as exc:
        raise SetupError("The 'diffusers' package is not installed.", hint="pip install diffusers transformers accelerate") from exc
    except Exception as exc:  # e.g. incompatible torch/transformers pins
        raise SetupError(f"Importing diffusers failed: {type(exc).__name__}: {exc}",
                         hint="This is usually a version clash between torch, transformers and diffusers.") from exc

    try:
        from diffusers import QwenImage21Pipeline
    except (ImportError, AttributeError) as exc:
        raise SetupError(
            f"diffusers {getattr(diffusers, '__version__', '?')} does not provide QwenImage21Pipeline.",
            hint="Upgrade (pip install -U diffusers). If a released version still lacks it, install from source: "
                 "pip install git+https://github.com/huggingface/diffusers  (see the model card's install notes).",
        ) from exc
    except Exception as exc:
        raise SetupError(f"Could not import QwenImage21Pipeline: {type(exc).__name__}: {exc}") from exc

    return Runtime(torch, Image, ImageOps, PngInfo, QwenImage21Pipeline)


def check_cuda(torch: Any) -> None:
    if not torch.cuda.is_available():
        built = torch.version.cuda or "none - this is a CPU-only build"
        raise SetupError(
            f"PyTorch {torch.__version__} cannot see a CUDA GPU (built for CUDA: {built}).",
            hint="Check that: `nvidia-smi` works; the container exposes the GPU (podman: --device nvidia.com/gpu=all, "
                 "docker: --gpus all); and PyTorch is a CUDA-enabled build (a CPU-only wheel is a common cause on aarch64).",
        )
    major, minor = torch.cuda.get_device_capability(0)
    log.info("GPU: %s (compute capability %d.%d) | torch %s | CUDA %s",
             torch.cuda.get_device_name(0), major, minor, torch.__version__, torch.version.cuda)


def validate_pipeline_kwargs(pipeline_cls: Any, job: Job) -> None:
    """Optional flags aren't in the model card, so check them against the real signature
    *before* spending minutes loading weights."""
    wanted = {}
    if job.negative_prompt:
        wanted["negative_prompt"] = "--negative-prompt"
    if job.cfg_scale is not None:
        wanted["true_cfg_scale"] = "--cfg-scale"
    if not wanted:
        return
    try:
        params = inspect.signature(pipeline_cls.__call__).parameters
    except (TypeError, ValueError):
        log.debug("Could not inspect pipeline signature; skipping optional-argument check.")
        return
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return
    for kwarg, flag in wanted.items():
        if kwarg not in params:
            raise UsageError(
                f"{flag} is not supported by this pipeline version (no {kwarg!r} argument).",
                hint="Arguments it does accept: " + ", ".join(p for p in params if p != "self"),
            )


def load_input_image(rt: Runtime, path: Path) -> Any:
    Image = rt.Image
    try:
        with Image.open(path) as im:
            im.load()
            im = rt.ImageOps.exif_transpose(im)  # honour phone/camera rotation
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                background.alpha_composite(rgba)
                log.warning("Input image has transparency; flattening it onto white for editing.")
                rgb = background.convert("RGB")
            else:
                rgb = im.convert("RGB")
    except Image.DecompressionBombError as exc:
        raise UsageError(f"Input image is absurdly large and was refused: {path}") from exc
    except (Image.UnidentifiedImageError, OSError, ValueError) as exc:
        raise UsageError(f"Cannot read {path} as an image: {exc}") from exc
    log.info("Input image: %s (%dx%d)", path.name, *rgb.size)
    return rgb


def _exc_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def _arch_mismatch(text: str) -> bool:
    return "no kernel image" in text or "not compatible with the current pytorch" in text


ARCH_HINT = ("This PyTorch build has no kernels for this GPU's architecture. Install a PyTorch build that supports it "
             "(recent CUDA 12.8+/13 builds; on a DGX Spark use NVIDIA's PyTorch container or its aarch64 CUDA wheels).")


def translate_load_error(exc: Exception, rt: Runtime, job: Job) -> ScriptError:
    names = _exc_names(exc)
    text = str(exc).lower()
    status = getattr(getattr(exc, "response", None), "status_code", None)

    if isinstance(exc, getattr(rt.torch.cuda, "OutOfMemoryError", ())):
        hint = "Close other GPU jobs (check nvidia-smi)."
        if not job.cpu_offload:
            hint = "Retry with --cpu-offload. " + hint
        return ModelLoadError("Ran out of GPU memory while loading the model.", hint=hint)
    if _arch_mismatch(text):
        return ModelLoadError(f"CUDA cannot run on this GPU: {exc}", hint=ARCH_HINT)
    if isinstance(exc, ImportError):
        missing = getattr(exc, "name", None) or "the missing package"
        return ModelLoadError(f"A required Python package is missing: {exc}", hint=f"pip install {missing}")
    if "LocalEntryNotFoundError" in names and job.local_files_only:
        return ModelLoadError(f"{job.model!r} is not in the local cache.",
                              hint="Drop --local-files-only to download it, or point --model at a local directory.")
    if names & {"RepositoryNotFoundError", "GatedRepoError", "RevisionNotFoundError", "HFValidationError"} or status in (401, 403):
        return ModelLoadError(
            f"Cannot access model {job.model!r}: {exc}",
            hint="Check the model id / local path. If the repo is gated or private, log in with the Hugging Face CLI "
                 "or export HF_TOKEN=<your token>.",
        )
    network_names = {"LocalEntryNotFoundError", "OfflineModeIsEnabled", "ConnectionError", "ConnectTimeout",
                     "ReadTimeout", "Timeout", "ProxyError", "SSLError", "MaxRetryError", "ConnectError",
                     "TimeoutException", "NetworkError"}
    if names & network_names or "couldn't connect" in text or "connection" in text:
        return ModelLoadError(
            f"Cannot reach the Hugging Face Hub: {exc}",
            hint="Check the network / HTTPS_PROXY, or download the model once and rerun with "
                 "--model /path/to/model --local-files-only.",
        )
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return ModelLoadError("Disk full while downloading the model.",
                              hint="Free space or set HF_HOME to a bigger volume.")
    return ModelLoadError(f"Failed to load model {job.model!r}: {type(exc).__name__}: {exc}")


def load_pipeline(rt: Runtime, job: Job) -> Any:
    log.info("Loading %s (bfloat16%s) - this can take a while on first run...",
             job.model, ", model CPU offload" if job.cpu_offload else "")
    started = time.monotonic()
    kwargs: dict[str, Any] = {"dtype": rt.torch.bfloat16}
    if job.local_files_only:
        kwargs["local_files_only"] = True
    try:
        pipe = rt.pipeline_cls.from_pretrained(job.model, **kwargs)
        if job.cpu_offload:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cuda")
    except Exception as exc:
        raise translate_load_error(exc, rt, job) from exc
    log.info("Model ready in %.1fs", time.monotonic() - started)
    return pipe


def translate_generation_error(exc: Exception, rt: Runtime, job: Job) -> ScriptError:
    text = str(exc).lower()
    if isinstance(exc, getattr(rt.torch.cuda, "OutOfMemoryError", ())) or "out of memory" in text:
        hints = ["use a smaller size (e.g. --aspect-ratio 1:1 is 2048x2048; try --width 1024 --height 1024)"]
        if not job.cpu_offload:
            hints.append("add --cpu-offload")
        if job.num_images > 1:
            hints.append("lower --num-images")
        return GenerationError("Ran out of GPU memory while generating.", hint="; ".join(hints).capitalize() + ".")
    if _arch_mismatch(text):
        return GenerationError(f"CUDA cannot run on this GPU: {exc}", hint=ARCH_HINT)
    if isinstance(exc, TypeError) and "unexpected keyword argument" in text:
        return GenerationError(f"The pipeline rejected an argument: {exc}",
                               hint="This diffusers version may not match the model. Try upgrading diffusers.")
    return GenerationError(f"Image generation failed: {type(exc).__name__}: {exc}")


def generate_one(rt: Runtime, pipe: Any, job: Job, source: Any, seed: int) -> Any:
    call: dict[str, Any] = {
        "prompt": job.prompt,
        "num_inference_steps": job.steps,
        "generator": rt.torch.Generator("cuda").manual_seed(seed),
    }
    if source is not None:
        call["image"] = source
    if job.width is not None and job.height is not None:
        call["width"], call["height"] = job.width, job.height
    if job.negative_prompt:
        call["negative_prompt"] = job.negative_prompt
    if job.cfg_scale is not None:
        call["true_cfg_scale"] = job.cfg_scale
    try:
        images = pipe(**call).images
        image = images[0]
    except (AttributeError, IndexError):
        raise GenerationError("The pipeline finished but returned no image.") from None
    except Exception as exc:
        raise translate_generation_error(exc, rt, job) from exc
    if job.transparent and getattr(image, "mode", None) != "RGBA":
        log.warning("--transparent was requested but the result has mode %s (no alpha channel). "
                    "Try rewording the prompt or another seed.", getattr(image, "mode", "?"))
    return image


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def print_plan(job: Job) -> None:
    size = f"{job.width}x{job.height}" if job.width else "pipeline default (from the input image)"
    seeds = ", ".join(str(job.seed + i) for i in range(job.num_images))
    print("Dry run - nothing was loaded or written.")
    print(f"  mode        : {job.mode}")
    print(f"  model       : {job.model}")
    print(f"  prompt      : {job.prompt}")
    if job.input_path:
        print(f"  input image : {job.input_path}")
    if job.negative_prompt:
        print(f"  negative    : {job.negative_prompt}")
    print(f"  size        : {size}")
    print(f"  steps       : {job.steps}")
    print(f"  seed(s)     : {seeds}")
    for i in range(job.num_images):
        size_tuple = (job.width, job.height) if job.width else None
        print(f"  output      : {output_path(job, i, size_tuple)}")


def run(args: argparse.Namespace) -> int:
    job = build_job(args)
    check_output_clashes(job)
    if job.dry_run:
        print_plan(job)
        return 0

    prepare_output_dir(job)
    rt = import_runtime()
    check_cuda(rt.torch)
    validate_pipeline_kwargs(rt.pipeline_cls, job)
    source = load_input_image(rt, job.input_path) if job.input_path else None
    pipe = load_pipeline(rt, job)

    saved: list[Path] = []
    try:
        for i in range(job.num_images):
            seed = job.seed + i
            log.info("Generating image %d/%d (seed %d, %d steps)...", i + 1, job.num_images, seed, job.steps)
            started = time.monotonic()
            image = generate_one(rt, pipe, job, source, seed)
            path = output_path(job, i, tuple(image.size))
            save_image(rt, job, image, path, seed)
            saved.append(path)
            log.info("Saved %s (%.1fs)", path, time.monotonic() - started)
            print(path, flush=True)  # stdout carries only result paths, so this is script-friendly
            del image
            gc.collect()
            rt.torch.cuda.empty_cache()
    except ScriptError:
        if saved:
            log.error("Stopped after %d of %d image(s); those were saved.", len(saved), job.num_images)
        raise
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    try:
        return run(args)
    except ScriptError as exc:
        log.error("%s", exc)
        if exc.hint:
            log.error("  hint: %s", exc.hint)
        if args.verbose and exc.__cause__ is not None:
            log.error("Underlying error:", exc_info=exc.__cause__)
        return exc.exit_code
    except KeyboardInterrupt:
        log.warning("Interrupted.")
        return 130
    except Exception as exc:
        log.error("Unexpected error: %s: %s%s", type(exc).__name__, exc,
                  "" if args.verbose else " (re-run with -v for a traceback)", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
