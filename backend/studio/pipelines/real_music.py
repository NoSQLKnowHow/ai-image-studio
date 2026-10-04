"""The real MiniMax-Music3 pipeline (diffusers `ModularPipeline`, in the released diffusers 0.40.0). Runs in the music
worker only (DESIGN.md §26.4).

What the pipeline does (read from the diffusers 0.40.0 source, not guessed): `pipe(prompt=, lyrics=, audio_duration=,
num_inference_steps=, generator=, output="audios")` returns `(batch, channels, samples)` floats in [-1, 1] at
`pipe.sampling_rate` (44.1 kHz). It composes frame by frame in a Python loop that calls the language model's output layer
(`lm_head`) once per audio frame, then renders in windows through a progress bar it creates for the run, then decodes.

Progress and Cancel are done without touching the model's code: a forward hook on `lm_head` counts frames and raises
`Canceled` when asked; for the rendering stage the progress-bar class the pipeline's module uses is swapped, for the
length of one track, for a minimal one that reports each step (and checks for Cancel).
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

from .base import Canceled, MusicJob, MusicProgress, MusicResult, OutOfMemory, PipelineError, PipelineLoadError, PipelineUnavailable
from .hub import load_with_hub_mode
from .real import (
    REBUILD_HINT, _arch_mismatch, _is_oom, describe_error, device_info, require_gpu, translate_load_error,
)
from .. import presets as P
from ..sysinfo import MEMORY_HINT
from ..wavfile import pcm16_from_array

log = logging.getLogger("studio.pipelines.real_music")

MUSIC_REBUILD_HINT = "Rebuild the image (docker compose build): it installs diffusers 0.40.0 for the music worker."
SUPPORTS = {"music": True, "instrumental": True, "step_progress": True, "cancel": True}


def import_music_runtime() -> tuple[Any, Any]:
    """Import torch and the music pipeline, translating every failure into PipelineUnavailable."""
    try:
        import torch
    except ImportError as exc:
        raise PipelineUnavailable(
            "PyTorch is not installed in this environment.",
            hint="Run the studio from its container image, which is built on NVIDIA's PyTorch image for the Spark.",
        ) from exc
    try:
        import diffusers
        from diffusers import ModularPipeline
    except ImportError as exc:
        raise PipelineUnavailable("The diffusers package is not installed.", hint=MUSIC_REBUILD_HINT) from exc
    except Exception as exc:
        raise PipelineUnavailable(
            f"Importing diffusers failed: {describe_error(exc)}",
            hint="Usually a version clash between torch, transformers and diffusers. " + MUSIC_REBUILD_HINT,
        ) from exc
    try:
        from diffusers.modular_pipelines import MiniMaxMusic3ModularPipeline as music_cls
    except Exception as exc:  # an older diffusers (the image worker's commit) has no music pipeline
        raise PipelineUnavailable(
            f"diffusers {getattr(diffusers, '__version__', '?')} does not provide the MiniMax-Music3 pipeline: "
            f"{describe_error(exc)}",
            hint="The music worker needs diffusers 0.40.0 or newer, which the image puts in its own folder "
                 "(STUDIO_MUSIC_LIBS). " + MUSIC_REBUILD_HINT,
        ) from exc
    if type(music_cls).__name__ == "DummyObject" or ".dummy_" in getattr(music_cls, "__module__", ""):
        raise PipelineUnavailable(
            "diffusers is installed, but the MiniMax-Music3 pipeline is only a placeholder: its PyTorch or "
            "transformers side failed to import.",
            hint="`python -c 'import transformers'` in the container shows why. " + MUSIC_REBUILD_HINT,
        )
    return torch, ModularPipeline


def probe() -> dict[str, Any]:
    """GPU-free check run at server start: can the music pipeline be imported and is a GPU visible?"""
    torch, _ = import_music_runtime()
    require_gpu(torch)
    import diffusers

    return {"pipeline": "real", "supports": dict(SUPPORTS), "device": device_info(torch),
            "diffusers": getattr(diffusers, "__version__", "?")}


def translate_music_error(exc: Exception, torch: Any) -> PipelineError:
    if _is_oom(exc, torch):
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        return OutOfMemory("Ran out of memory while making the track.",
                           hint="Use a shorter duration or one version at a time. " + MEMORY_HINT)
    if _arch_mismatch(exc):
        return PipelineError(f"CUDA cannot run on this GPU: {exc}")
    if isinstance(exc, ValueError):
        return PipelineError(f"The music model rejected the request: {exc}",
                             hint="The description and lyrics together may be over the model's 5,000-token limit.")
    return PipelineError(f"Making the track failed: {type(exc).__name__}: {exc}")


class MissingComponents(FileNotFoundError):
    """Some of the model's parts did not load. `diffusers` turns a part that fails to load into a log warning and carries
    on, so without this check a model folder with a part missing (a download that stopped part-way) would count as loaded
    and fail at the first track. It is a FileNotFoundError so that the cache-first logic (hub.py) treats it as "files are
    missing": a half-cached model is completed online in `auto` mode instead of staying half-loaded."""

    def __init__(self, names: list[str]):
        super().__init__(f"These parts of the music model did not load: {', '.join(names)}.")
        self.names = names


def unloaded_components(pipe: Any) -> list[str]:
    """The parts `load_components` would have loaded (the same rule it uses) that are still not there."""
    specs = getattr(pipe, "_component_specs", {}) or {}
    return sorted(name for name, spec in specs.items()
                  if getattr(spec, "default_creation_method", None) == "from_pretrained"
                  and getattr(spec, "pretrained_model_name_or_path", None) is not None
                  and getattr(pipe, name, None) is None)


class _ReportingBar:
    """The smallest stand-in for the progress bar the pipeline makes for its rendering stage. Every update is reported."""

    def __init__(self, report: Any, iterable: Any = None, total: Any = None, **_ignored: Any):
        self._report, self._iterable, self.total, self.n = report, iterable, total, 0

    def update(self, n: int = 1) -> bool:
        self.n += n
        self._report(self.n, self.total)
        return True

    def __iter__(self) -> Iterator[Any]:
        for item in self._iterable or ():
            yield item
            self.update()

    def __enter__(self) -> "_ReportingBar":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        pass

    def __getattr__(self, _name: str) -> Any:  # set_description, refresh, ... all do nothing
        return lambda *a, **k: None


@contextlib.contextmanager
def reporting_progress_bars(report: Any) -> Iterator[None]:
    """For the length of the `with`, the pipeline's progress bars report to `report(step, total)` instead of the console."""
    import diffusers.modular_pipelines.modular_pipeline as modular

    original = modular.tqdm
    modular.tqdm = lambda iterable=None, **kwargs: _ReportingBar(report, iterable, **kwargs)
    try:
        yield
    finally:
        modular.tqdm = original


class RealMusicPipeline:
    name = "real"

    def __init__(self, model: str, hub_mode: str = "auto", device: str = "cuda", dtype: str = "bfloat16"):
        self.model = model
        self.hub_mode = hub_mode  # DESIGN.md §26.11
        self.device = device  # "cuda" for real; the tests run a tiny random-weight copy on "cpu" (float32)
        self.dtype = dtype
        self._pipe: Any = None
        self._torch: Any = None

    def load(self) -> dict[str, Any]:
        torch, modular_pipeline = import_music_runtime()
        if self.device == "cuda":
            require_gpu(torch)
        log.info("loading %s (%s, files: %s)", self.model, self.dtype, self.hub_mode)

        def fetch(offline: bool) -> Any:
            extra: dict[str, Any] = {"local_files_only": True} if offline else {}
            pipe = modular_pipeline.from_pretrained(self.model, **extra)
            # Every component is loaded from the same place as the pipeline's index, even when STUDIO_MUSIC_MODEL
            # is a folder: the index itself names the hub repository.
            pipe.load_components(dtype=getattr(torch, self.dtype), pretrained_model_name_or_path=self.model, **extra)
            missing = unloaded_components(pipe)
            if missing:
                raise MissingComponents(missing)
            return pipe

        try:
            pipe = load_with_hub_mode(fetch, self.hub_mode, self.model)
            pipe.to(self.device)
        except MissingComponents as exc:
            raise PipelineLoadError(
                f"The music model is incomplete: {', '.join(exc.names)} did not load.",
                hint="If STUDIO_MUSIC_MODEL is a folder, check that it holds a folder of that name (a download that stopped "
                     "part-way leaves some out) and that the studio's user can read it; the log has the reason for each part. "
                     "Otherwise download the model again.",
            ) from exc
        except Exception as exc:
            raise translate_load_error(exc, torch, self.hub_mode == "offline") from exc
        configure = getattr(pipe, "set_progress_bar_config", None)
        if callable(configure):
            configure(disable=True)  # progress is reported through the protocol instead
        self._pipe, self._torch = pipe, torch
        return {"pipeline": self.name, "supports": dict(SUPPORTS), "device": device_info(torch)}

    def generate(self, job: MusicJob, index: int, seed: int, on_progress: MusicProgress) -> MusicResult:
        if self._pipe is None:
            raise PipelineError("The music model is not loaded.")
        pipe, torch = self._pipe, self._torch
        frame_rate = float(getattr(pipe, "frame_rate", P.MUSIC_FRAME_RATE))
        max_frames = max(1, min(int(job.duration * frame_rate), P.MUSIC_MAX_FRAMES))
        frames = 0

        def on_frame(_module: Any, _args: Any) -> None:
            nonlocal frames
            frames += 1
            on_progress("compose", min(frames, max_frames), max_frames)

        def on_step(step: int, total: Any) -> None:
            total = int(total or job.steps)
            on_progress("render", min(step, total), total)
            if step >= total:
                on_progress("finish", 0, 1)

        handle = pipe.language_model.lm_head.register_forward_pre_hook(on_frame)
        try:
            with reporting_progress_bars(on_step):
                result = pipe(
                    prompt=job.prompt,
                    lyrics=job.lyrics,
                    audio_duration=float(job.duration),
                    num_inference_steps=job.steps,
                    generator=torch.Generator(self.device).manual_seed(seed),
                    output="audios",
                )
        except Canceled:
            raise
        except Exception as exc:
            raise translate_music_error(exc, torch) from exc
        finally:
            handle.remove()
        audio = result[0]  # (channels, samples), floats in [-1, 1]
        rate = int(pipe.sampling_rate)
        pcm = pcm16_from_array(audio)
        channels = int(audio.shape[0])
        return MusicResult(pcm=pcm, sample_rate=rate, channels=channels, seconds=audio.shape[-1] / rate)
