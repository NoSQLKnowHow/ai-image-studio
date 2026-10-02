"""RealPipeline against fake `torch` and `diffusers` modules.

The fake QwenImage21Pipeline copies the real `__call__` signature from diffusers main
@ 578c9b2c (pipeline_qwenimage21.py), including the torch.no_grad-style wrapper, so the
signature introspection and argument handling are exercised exactly as on the Spark.
"""

from __future__ import annotations

import errno
import functools
import sys
import types
from types import SimpleNamespace

import pytest
from PIL import Image

from studio.pipelines.base import ImageJob, OutOfMemory, PipelineError, PipelineLoadError, PipelineUnavailable
from studio.pipelines.real import RealPipeline, probe


class FakeOOM(RuntimeError):
    pass


# locals() inside the fake __call__ also lists the closure variables it uses; leave those out
CLOSURE_NAMES = frozenset({"self", "fakes", "render"})


def no_grad(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper


class Fakes:
    """Installs fake torch/diffusers; records what the code under test does with them."""

    def __init__(self, monkeypatch, *, cuda=True, signature="full", load_exc=None, call_exc=None, alpha=255):
        self.load_kwargs = None
        self.moved: list[str] = []
        self.calls: list[dict] = []
        self.progress_disabled = None
        self.empty_cache_calls = 0
        fakes = self

        torch = types.ModuleType("torch")
        torch.__version__ = "2.9.0a0+nv25.10"
        torch.version = SimpleNamespace(cuda="13.0")
        torch.bfloat16 = "torch.bfloat16"

        class Generator:
            def __init__(self, device):
                self.device, self.seed = device, None

            def manual_seed(self, seed):
                self.seed = seed
                return self

        def empty_cache():
            fakes.empty_cache_calls += 1

        torch.Generator = Generator
        torch.cuda = SimpleNamespace(
            is_available=lambda: cuda, get_device_name=lambda i: "NVIDIA GB10",
            get_device_capability=lambda i: (12, 1), empty_cache=empty_cache, OutOfMemoryError=FakeOOM,
        )

        def render(width, height, steps, callback, kwargs):
            if call_exc is not None:
                raise call_exc
            if callback is not None:
                for i in range(steps):
                    out = callback(None, i, 1000 - i, {"latents": "L"})
                    assert isinstance(out, dict) and "latents" in out
            return SimpleNamespace(images=[Image.new("RGBA", (width or 1024, height or 1024), (10, 20, 30, alpha))])

        class Base:
            @classmethod
            def from_pretrained(cls, model, **kwargs):
                fakes.load_kwargs = (model, kwargs)
                if load_exc is not None:
                    raise load_exc
                return cls()

            def to(self, device):
                fakes.moved.append(device)
                return self

            def enable_model_cpu_offload(self):
                fakes.moved.append("offload")

            def set_progress_bar_config(self, **kwargs):
                fakes.progress_disabled = kwargs.get("disable")

        class FullPipeline(Base):  # the real signature
            @no_grad
            def __call__(self, prompt=None, image=None, negative_prompt=None, true_cfg_scale=1.0, height=None,
                         width=None, num_inference_steps=40, sigmas=None, num_images_per_prompt=1, generator=None,
                         latents=None, prompt_embeds=None, prompt_embeds_mask=None, negative_prompt_embeds=None,
                         negative_prompt_embeds_mask=None, output_type="pil", return_dict=True,
                         attention_kwargs=None, callback_on_step_end=None,
                         callback_on_step_end_tensor_inputs=("latents",), output_resolution=1024, use_kv_cache=True):
                kwargs = {k: v for k, v in locals().items() if k not in CLOSURE_NAMES}
                fakes.calls.append({k: v for k, v in kwargs.items() if v is not None})
                return render(width, height, num_inference_steps, callback_on_step_end, kwargs)

        class MinimalPipeline(Base):  # an older/smaller build
            @no_grad
            def __call__(self, prompt=None, height=None, width=None, num_inference_steps=40, generator=None):
                fakes.calls.append({k: v for k, v in locals().items() if k not in CLOSURE_NAMES and v is not None})
                return render(width, height, num_inference_steps, None, {})

        diffusers = types.ModuleType("diffusers")
        diffusers.__version__ = "0.41.0.dev0"
        if signature == "full":
            diffusers.QwenImage21Pipeline = FullPipeline
        elif signature == "minimal":
            diffusers.QwenImage21Pipeline = MinimalPipeline
        monkeypatch.setitem(sys.modules, "torch", torch)
        monkeypatch.setitem(sys.modules, "diffusers", diffusers)


def job(**extra) -> ImageJob:
    values = dict(run_id="a" * 32, mode="generate", prompt="a lighthouse", negative_prompt=None, width=1024,
                  height=768, steps=5, cfg_scale=None, seeds=[42], transparent=False, model_id="Qwen/Qwen-Image-2.1")
    values.update(extra)
    return ImageJob(**values)


def loaded(monkeypatch, **fake_options) -> tuple[RealPipeline, Fakes]:
    fakes = Fakes(monkeypatch, **fake_options)
    pipe = RealPipeline("Qwen/Qwen-Image-2.1")
    pipe.load()
    return pipe, fakes


# ---------------------------------------------------------------- probe / import
def test_probe_reads_supported_features_from_the_real_signature(monkeypatch):
    Fakes(monkeypatch)
    result = probe()
    assert result["supports"] == {"negative_prompt": True, "cfg_scale": True, "step_progress": True,
                                  "transparent": True, "edit": True}
    assert result["device"]["name"] == "NVIDIA GB10" and result["device"]["capability"] == "12.1"
    assert result["diffusers"] == "0.41.0.dev0"


def test_probe_on_a_smaller_pipeline_build_disables_optional_features(monkeypatch):
    Fakes(monkeypatch, signature="minimal")
    assert probe()["supports"] == {"negative_prompt": False, "cfg_scale": False, "step_progress": False,
                                   "transparent": True, "edit": False}


def test_missing_torch_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(PipelineUnavailable, match="PyTorch is not installed"):
        probe()


def test_old_diffusers_without_the_pipeline(monkeypatch):
    Fakes(monkeypatch, signature="none")
    with pytest.raises(PipelineUnavailable) as info:
        probe()
    assert "0.41.0.dev0 does not provide QwenImage21Pipeline" in info.value.message
    assert "docker compose build" in info.value.hint


def test_no_gpu_in_the_container(monkeypatch):
    Fakes(monkeypatch, cuda=False)
    with pytest.raises(PipelineUnavailable) as info:
        probe()
    assert "No CUDA GPU is visible" in info.value.message and "--gpus all" in info.value.hint


# ---------------------------------------------------------------- load
def test_load_uses_bfloat16_on_the_gpu_and_silences_progress_bars(monkeypatch):
    pipe, fakes = loaded(monkeypatch)
    assert fakes.load_kwargs == ("Qwen/Qwen-Image-2.1", {"dtype": "torch.bfloat16"})
    assert fakes.moved == ["cuda"] and fakes.progress_disabled is True


def test_load_options(monkeypatch):
    fakes = Fakes(monkeypatch)
    RealPipeline("/models/qwen", cpu_offload=True, local_files_only=True).load()
    assert fakes.load_kwargs == ("/models/qwen", {"dtype": "torch.bfloat16", "local_files_only": True})
    assert fakes.moved == ["offload"]


def _named(name: str, base=Exception, **attrs):
    exc = type(name, (base,), {})("boom")
    for key, value in attrs.items():
        setattr(exc, key, value)
    return exc


@pytest.mark.parametrize("exc,local_only,kind,fragment", [
    (FakeOOM("CUDA out of memory. Tried to allocate 20 GiB"), False, OutOfMemory, "drop_caches"),
    (_named("GatedRepoError"), False, PipelineLoadError, "HF_TOKEN"),
    (_named("HfHubHTTPError", response=SimpleNamespace(status_code=401)), False, PipelineLoadError, "HF_TOKEN"),
    (PermissionError(errno.EACCES, "Permission denied", "/models/hub"), False, PipelineLoadError, "chown"),
    (ConnectionError("Name or service not known"), False, PipelineLoadError, "internet"),
    (_named("LocalEntryNotFoundError"), True, PipelineLoadError, "STUDIO_LOCAL_FILES_ONLY"),
    (ImportError("accelerate is required", name="accelerate"), False, PipelineLoadError, "docker compose build"),
    (RuntimeError("CUDA error: no kernel image is available for execution"), False, PipelineLoadError, "NGC_TAG"),
    (OSError(errno.ENOSPC, "No space left on device"), False, PipelineLoadError, "Free space"),
])
def test_load_errors_carry_actionable_hints(monkeypatch, exc, local_only, kind, fragment):
    Fakes(monkeypatch, load_exc=exc)
    with pytest.raises(kind) as info:
        RealPipeline("Qwen/Qwen-Image-2.1", local_files_only=local_only).load()
    assert fragment in (info.value.hint or "") + info.value.message


# ---------------------------------------------------------------- generate
def test_generate_passes_the_right_arguments_and_reports_every_step(monkeypatch):
    pipe, fakes = loaded(monkeypatch)
    steps = []
    image = pipe.generate(job(negative_prompt="blurry", cfg_scale=4.0), 0, 42, lambda s, t: steps.append((s, t)))
    call = fakes.calls[-1]
    assert call["prompt"] == "a lighthouse" and call["num_inference_steps"] == 5
    assert (call["width"], call["height"]) == (1024, 768)
    assert call["generator"].device == "cuda" and call["generator"].seed == 42
    assert call["negative_prompt"] == "blurry" and call["true_cfg_scale"] == 4.0
    assert steps == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    assert image.mode == "RGB" and image.size == (1024, 768)  # fully opaque RGBA -> RGB


def test_generate_keeps_real_transparency(monkeypatch):
    pipe, _ = loaded(monkeypatch, alpha=0)
    assert pipe.generate(job(transparent=True), 0, 1, lambda s, t: None).mode == "RGBA"


def test_generate_leaves_size_to_the_pipeline_when_not_set(monkeypatch):
    pipe, fakes = loaded(monkeypatch)
    pipe.generate(job(width=None, height=None), 0, 1, lambda s, t: None)
    assert "width" not in fakes.calls[-1] and "height" not in fakes.calls[-1]


def test_generate_omits_arguments_the_build_does_not_accept(monkeypatch):
    pipe, fakes = loaded(monkeypatch, signature="minimal")
    steps = []
    pipe.generate(job(negative_prompt="blurry", cfg_scale=4.0), 0, 7, lambda s, t: steps.append((s, t)))
    assert set(fakes.calls[-1]) == {"prompt", "height", "width", "num_inference_steps", "generator"}
    assert steps == [(5, 5)]  # no per-step callback available: one final progress report


@pytest.mark.parametrize("exc,kind,fragment", [
    (FakeOOM("CUDA out of memory"), OutOfMemory, "smaller size"),
    (ValueError("`height` and `width` have to be divisible by 32"), PipelineError, "rejected the request"),
    (RuntimeError("something odd"), PipelineError, "Image generation failed: RuntimeError"),
])
def test_generate_errors(monkeypatch, exc, kind, fragment):
    pipe, fakes = loaded(monkeypatch, call_exc=exc)
    with pytest.raises(kind) as info:
        pipe.generate(job(), 0, 1, lambda s, t: None)
    assert fragment in (info.value.hint or "") + info.value.message
    if kind is OutOfMemory:
        assert fakes.empty_cache_calls == 1


def test_generate_before_load_is_an_error():
    with pytest.raises(PipelineError, match="not loaded"):
        RealPipeline("x").generate(job(), 0, 1, lambda s, t: None)
