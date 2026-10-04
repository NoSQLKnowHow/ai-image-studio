"""The real Qwen-Image-2.1 pipeline (diffusers `QwenImage21Pipeline`). Runs in the worker only.

Written against diffusers main @ 578c9b2c (2026-10-01). Its `__call__` takes, among others:
prompt, image (one or more condition images, for Edit), negative_prompt, true_cfg_scale
(1.0 = no guidance, Qwen's recommended default; a negative prompt only matters above 1),
height/width (divisible by 32), num_inference_steps (default 40), generator,
callback_on_step_end and output_resolution. Optional arguments are checked against the
real signature, so a different diffusers build degrades gracefully instead of crashing.
"""

from __future__ import annotations

import errno
import inspect
import logging
from typing import Any

from PIL import Image

from ..sysinfo import MEMORY_HINT
from .base import ImageJob, OutOfMemory, PipelineError, PipelineLoadError, PipelineUnavailable, StepCallback, load_inputs
from .hub import load_with_hub_mode

log = logging.getLogger("studio.pipelines.real")

PIPELINE_CLASS = "QwenImage21Pipeline"
REBUILD_HINT = "Get the latest code (git pull) and rebuild the image: docker compose build"
NO_GPU_HINT = (
    "Start the container with GPU access (the compose file reserves the GPU; with docker run use "
    "--gpus all). Check that `nvidia-smi` works on the host."
)
ARCH_HINT = (
    "This PyTorch build has no kernels for the Spark's GPU (GB10). The image must be based on NVIDIA's "
    "PyTorch container 25.10 or newer: set NGC_TAG in .env, then docker compose build."
)
NETWORK_NAMES = frozenset({
    "LocalEntryNotFoundError", "OfflineModeIsEnabled", "ConnectionError", "ConnectTimeout", "ReadTimeout",
    "Timeout", "ProxyError", "SSLError", "MaxRetryError", "ConnectError", "TimeoutException", "NetworkError",
})
ACCESS_NAMES = frozenset({"RepositoryNotFoundError", "GatedRepoError", "RevisionNotFoundError", "HFValidationError"})


def _names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def root_cause(exc: BaseException) -> BaseException:
    """The innermost exception of a chain. diffusers wraps a failed lazy import in a RuntimeError
    whose text is little more than "look up to see its traceback"; the reason is what it came from."""
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        inner = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
        if inner is None:
            break
        exc = inner
    return exc


def describe_error(exc: BaseException, limit: int = 500) -> str:
    """'ImportError: cannot import name ...' for the root cause, on one line."""
    root = root_cause(exc)
    text = " ".join(str(root).split()) or "(no message)"
    return f"{type(root).__name__}: {text[:limit]}{'…' if len(text) > limit else ''}"


def _is_oom(exc: BaseException, torch: Any) -> bool:
    oom_type = getattr(getattr(torch, "cuda", None), "OutOfMemoryError", None)
    return (isinstance(oom_type, type) and isinstance(exc, oom_type)) or "out of memory" in str(exc).lower()


def _arch_mismatch(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "no kernel image" in text or "not compatible with the current pytorch" in text


def import_runtime() -> tuple[Any, Any]:
    """Import torch and the pipeline class, translating every failure into PipelineUnavailable."""
    try:
        import torch
    except ImportError as exc:
        raise PipelineUnavailable(
            "PyTorch is not installed in this environment.",
            hint="Run the studio from its container image, which is built on NVIDIA's PyTorch image for the Spark.",
        ) from exc
    try:
        import diffusers
    except ImportError as exc:
        raise PipelineUnavailable("The diffusers package is not installed.", hint=REBUILD_HINT) from exc
    except Exception as exc:
        raise PipelineUnavailable(
            f"Importing diffusers failed: {describe_error(exc)}",
            hint="Usually a version clash between torch, transformers and diffusers. " + REBUILD_HINT,
        ) from exc
    try:
        pipeline_cls = getattr(diffusers, PIPELINE_CLASS)
    except AttributeError as exc:
        raise PipelineUnavailable(
            f"diffusers {getattr(diffusers, '__version__', '?')} does not provide {PIPELINE_CLASS}.",
            hint="The image installs diffusers from a pinned GitHub commit that has it. " + REBUILD_HINT,
        ) from exc
    except Exception as exc:  # e.g. transformers too old for the Qwen3-VL text encoder
        raise PipelineUnavailable(
            f"Loading {PIPELINE_CLASS} failed: {describe_error(exc)}",
            hint="A library in the image doesn't match what diffusers expects; the message names it. "
                 + REBUILD_HINT,
        ) from exc
    # When torch or transformers fail to import, diffusers doesn't raise: it hands out a placeholder
    # class (from diffusers.utils.dummy_*_objects) that only errors once you try to load a model.
    if type(pipeline_cls).__name__ == "DummyObject" or ".dummy_" in getattr(pipeline_cls, "__module__", ""):
        raise PipelineUnavailable(
            f"diffusers is installed, but {PIPELINE_CLASS} is only a placeholder: its PyTorch or transformers "
            "side failed to import.",
            hint="Usually transformers is missing or doesn't match; "
                 "`python -c 'import transformers'` in the container shows why. " + REBUILD_HINT,
        )
    return torch, pipeline_cls


def describe_supports(pipeline_cls: Any) -> dict[str, bool]:
    """Which optional features this pipeline build accepts, from its real call signature."""
    try:
        params = inspect.signature(pipeline_cls.__call__).parameters
    except (TypeError, ValueError):
        log.warning("could not inspect %s.__call__; optional features disabled", PIPELINE_CLASS)
        return {"negative_prompt": False, "cfg_scale": False, "step_progress": False, "transparent": True, "edit": False,
                "resolution": False}
    var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

    def has(name: str) -> bool:
        return var_kw or name in params

    return {
        "negative_prompt": has("negative_prompt"),
        "cfg_scale": has("true_cfg_scale"),
        "step_progress": has("callback_on_step_end"),
        "transparent": True,  # model card: RGBA output via the prompt format
        "edit": has("image"),
        "resolution": has("output_resolution"),  # sizes every input and, on Auto, the result
    }


def device_info(torch: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"torch": getattr(torch, "__version__", "?"), "cuda": getattr(torch.version, "cuda", None)}
    if torch.cuda.is_available():
        major, minor = torch.cuda.get_device_capability(0)
        info.update(name=torch.cuda.get_device_name(0), capability=f"{major}.{minor}")
    return info


def require_gpu(torch: Any) -> None:
    if not torch.cuda.is_available():
        built = getattr(torch.version, "cuda", None) or "none (CPU-only build)"
        raise PipelineUnavailable(
            f"No CUDA GPU is visible to PyTorch {torch.__version__} (built for CUDA {built}).", hint=NO_GPU_HINT)


def probe() -> dict[str, Any]:
    """GPU-free check run at server start: can the pipeline be imported and is a GPU visible?"""
    torch, pipeline_cls = import_runtime()
    require_gpu(torch)
    import diffusers

    return {
        "pipeline": "real",
        "supports": describe_supports(pipeline_cls),
        "device": device_info(torch),
        "diffusers": getattr(diffusers, "__version__", "?"),
    }


def normalize_alpha(image: Image.Image) -> Image.Image:
    """The VAE decodes four channels; a fully opaque result is stored as plain RGB."""
    if image.mode == "RGBA" and image.getchannel("A").getextrema() == (255, 255):
        return image.convert("RGB")
    return image


def translate_load_error(exc: Exception, torch: Any, offline: bool) -> PipelineError:
    names = _names(exc)
    status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc).lower()
    if _is_oom(exc, torch):
        return OutOfMemory("Ran out of memory while loading the model.", hint=MEMORY_HINT)
    if _arch_mismatch(exc):
        return PipelineLoadError(f"CUDA cannot run on this GPU: {exc}", hint=ARCH_HINT)
    if isinstance(exc, ImportError):
        return PipelineLoadError(f"A required Python package is missing: {exc}", hint=REBUILD_HINT)
    if isinstance(exc, PermissionError) or (isinstance(exc, OSError) and exc.errno == errno.EACCES):
        return PipelineLoadError(
            f"Can't write to the model cache: {exc}",
            hint="The folder mounted at /models must be writable by the studio's user. On the host run: "
                 "sudo chown -R $(id -u):$(id -g) <your HF cache folder>, or set HF_CACHE_DIR to a folder you own.",
        )
    if "LocalEntryNotFoundError" in names and offline:
        return PipelineLoadError("The model is not in the local cache.",
                                 hint="STUDIO_LOCAL_FILES_ONLY=true never downloads. Set it to auto (or false) once, "
                                      "so the model can be downloaded, then back if you like.")
    if names & ACCESS_NAMES or status in (401, 403):
        return PipelineLoadError(
            f"Cannot access the model on Hugging Face: {exc}",
            hint="Check STUDIO_MODEL. If the model is gated or private, accept its terms on huggingface.co and "
                 "set HF_TOKEN in the .env file.",
        )
    if names & NETWORK_NAMES or "couldn't connect" in text or "connection" in text:
        return PipelineLoadError(
            f"Cannot reach Hugging Face to download the model: {exc}",
            hint="Check the Spark's internet connection (the first start downloads the weights).",
        )
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return PipelineLoadError("The disk is full; the model download could not finish.",
                                 hint="Free space on the volume that holds the model cache.")
    return PipelineLoadError(f"Loading the model failed: {type(exc).__name__}: {exc}")


def translate_generation_error(exc: Exception, torch: Any, edit: bool = False) -> PipelineError:
    if _is_oom(exc, torch):
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        # An edit costs about (number of images) x (resolution)^2, so the first things to reduce are those.
        advice = ("Use 1K instead of 2K, fewer input images, or fewer images per click. " if edit
                  else "Use a smaller size or fewer images per click. ")
        return OutOfMemory("Ran out of memory while generating.", hint=advice + MEMORY_HINT)
    if _arch_mismatch(exc):
        return PipelineError(f"CUDA cannot run on this GPU: {exc}", hint=ARCH_HINT)
    if isinstance(exc, ValueError):
        return PipelineError(f"The model rejected the request: {exc}")
    return PipelineError(f"Image generation failed: {type(exc).__name__}: {exc}")


class RealPipeline:
    name = "real"

    def __init__(self, model: str, cpu_offload: bool = False, hub_mode: str = "auto"):
        self.model = model
        self.cpu_offload = cpu_offload
        self.hub_mode = hub_mode  # DESIGN.md §26.11: "auto" cache first, "offline" never online, "online" as before
        self._pipe: Any = None
        self._torch: Any = None
        self._supports: dict[str, bool] = {}

    def load(self) -> dict[str, Any]:
        torch, pipeline_cls = import_runtime()
        require_gpu(torch)
        log.info("loading %s (bfloat16%s, files: %s)", self.model, ", model CPU offload" if self.cpu_offload else "", self.hub_mode)

        def fetch(offline: bool) -> Any:
            kwargs: dict[str, Any] = {"dtype": torch.bfloat16}
            if offline:
                kwargs["local_files_only"] = True
            return pipeline_cls.from_pretrained(self.model, **kwargs)

        try:
            pipe = load_with_hub_mode(fetch, self.hub_mode, self.model)
            if self.cpu_offload:
                pipe.enable_model_cpu_offload()
            else:
                pipe.to("cuda")
        except Exception as exc:
            raise translate_load_error(exc, torch, self.hub_mode == "offline") from exc
        configure = getattr(pipe, "set_progress_bar_config", None)
        if callable(configure):
            configure(disable=True)  # progress is reported through the protocol instead
        self._pipe, self._torch = pipe, torch
        self._supports = describe_supports(pipeline_cls)
        return {"pipeline": self.name, "supports": dict(self._supports), "device": device_info(torch)}

    def generate(self, job: ImageJob, index: int, seed: int, on_step: StepCallback) -> Image.Image:
        if self._pipe is None:
            raise PipelineError("The model is not loaded.")
        torch = self._torch
        kwargs: dict[str, Any] = {
            "prompt": job.prompt,
            "num_inference_steps": job.steps,
            "generator": torch.Generator("cuda").manual_seed(seed),
        }
        if job.mode == "edit":
            if not self._supports.get("edit"):
                raise PipelineError("This build of the image pipeline can't edit images.", hint=REBUILD_HINT)
            kwargs["image"] = load_inputs(job.input_paths)  # as stored, in order, never flattened: the VAE reads the alpha
            if job.resolution and self._supports.get("resolution"):
                kwargs["output_resolution"] = job.resolution
        if job.width and job.height:  # Auto leaves it to the pipeline: about resolution^2 pixels, shaped like the last image
            kwargs["width"], kwargs["height"] = job.width, job.height
        if job.negative_prompt and self._supports.get("negative_prompt"):
            kwargs["negative_prompt"] = job.negative_prompt
        if job.cfg_scale is not None and self._supports.get("cfg_scale"):
            kwargs["true_cfg_scale"] = job.cfg_scale
        if self._supports.get("step_progress"):
            def callback(pipe: Any, step_index: int, timestep: Any, callback_kwargs: dict) -> dict:
                on_step(min(step_index + 1, job.steps), job.steps)
                return callback_kwargs

            kwargs["callback_on_step_end"] = callback
        try:
            image = self._pipe(**kwargs).images[0]
        except Exception as exc:
            raise translate_generation_error(exc, torch, edit=job.mode == "edit") from exc
        if not self._supports.get("step_progress"):
            on_step(job.steps, job.steps)
        return normalize_alpha(image)
