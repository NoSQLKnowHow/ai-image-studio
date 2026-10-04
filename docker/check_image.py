"""Sanity check for the container image. The Dockerfile runs it at build time; you can re-run it
in a running container:  docker compose exec studio python /app/docker/check_image.py

Fails (exit 1) when:
- pip replaced one of NVIDIA's own builds (torch and friends, listed in /app/ngc-pins.txt),
- PyTorch is not a CUDA build,
- the backend can't import the Qwen-Image-2.1 pipeline the way the server will
  (studio.pipelines.real.import_runtime, which also catches diffusers' placeholder classes),
- the music worker can't import the MiniMax-Music3 pipeline with its own copy of diffusers (STUDIO_MUSIC_LIBS),
- the built web page is missing.
No GPU is needed: builds don't get one, so whether CUDA can see a GPU is reported, not required.
"""

from __future__ import annotations

import importlib.metadata as md
import os
import subprocess
import sys
import traceback
from pathlib import Path

PINS = Path("/app/ngc-pins.txt")
# Printed up front, so a failed build log says what was installed. The second half are optional
# extras that NVIDIA's image may bundle and that diffusers/transformers use automatically if present.
REPORTED = ("torch", "torchvision", "triton", "numpy", "transformers", "diffusers", "accelerate",
            "huggingface_hub", "tokenizers", "safetensors", "pillow",
            "flash_attn", "flash_attn_3", "transformer_engine", "torchao", "xformers", "apex", "kernels")


def installed_versions() -> str:
    found = []
    for name in REPORTED:
        try:
            found.append(f"{name}=={md.version(name)}")
        except md.PackageNotFoundError:
            pass
    return ", ".join(found)


def main() -> int:
    problems: list[str] = []
    notes: list[str] = []
    print(f"check_image: Python {sys.version.split()[0]}; installed: {installed_versions()}", flush=True)

    if PINS.exists():
        for line in PINS.read_text().split():
            name, _, wanted = line.partition("==")
            installed = md.version(name)
            if installed != wanted:
                problems.append(f"{name} is {installed}, but NVIDIA's image shipped {wanted}: pip replaced it")
    else:
        notes.append(f"{PINS} not found (outside the image?), skipped the NVIDIA-build check")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from studio.pipelines.base import PipelineUnavailable
    from studio.pipelines.real import describe_supports, import_runtime

    try:
        torch, pipeline_cls = import_runtime()
    except PipelineUnavailable as exc:
        problems.append(f"{exc.message} ({exc.hint})" if exc.hint else exc.message)
        if exc.__cause__ is not None:  # the whole chain, so a failed build explains itself
            print("check_image: traceback of the import failure:", file=sys.stderr, flush=True)
            traceback.print_exception(exc.__cause__, file=sys.stderr)
        pip_check = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
        print("check_image: pip check says:\n" + (pip_check.stdout + pip_check.stderr).strip()[:4000],
              file=sys.stderr, flush=True)
    else:
        import diffusers
        import transformers

        if not torch.version.cuda:
            problems.append(f"PyTorch {torch.__version__} is not a CUDA build")
        notes.append(f"torch {torch.__version__} (CUDA {torch.version.cuda}), diffusers {diffusers.__version__}, "
                     f"transformers {transformers.__version__}")
        notes.append(f"pipeline features: {describe_supports(pipeline_cls)}")
        notes.append("GPU visible now: " + ("yes, " + torch.cuda.get_device_name(0) if torch.cuda.is_available()
                                            else "no (expected during a build)"))

    # The music worker runs with its own copy of diffusers first on its path (DESIGN.md §26.4): check it imports there.
    music_libs = os.environ.get("STUDIO_MUSIC_LIBS", "").strip()
    if music_libs:
        code = ("from studio.pipelines.real_music import import_music_runtime; import diffusers; "
                "torch, cls = import_music_runtime(); print(diffusers.__version__, cls.__name__)")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (music_libs, str(Path(__file__).resolve().parents[1] / "backend"),
                                                                       os.environ.get("PYTHONPATH", "")) if p))
        music = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        if music.returncode:
            problems.append("the music worker cannot import its pipeline with the diffusers in " + music_libs + ": "
                            + (music.stderr.strip().splitlines() or ["(no message)"])[-1]
                            + " (DESIGN.md section 26.4; docker/check_image.py prints the full error below)")
            print("check_image: music import failed:\n" + music.stderr[-4000:], file=sys.stderr, flush=True)
        else:
            notes.append(f"music worker: diffusers {music.stdout.split()[0]} from {music_libs}")
    else:
        notes.append("STUDIO_MUSIC_LIBS is not set (outside the image?), skipped the music import check")

    static = Path(os.environ.get("STUDIO_STATIC_DIR", "/app/static")) / "index.html"
    if not static.is_file():
        problems.append(f"the web page is missing: {static}")

    for note in notes:
        print("check_image:", note)
    for problem in problems:
        print("check_image: PROBLEM:", problem, file=sys.stderr)
    print("check_image:", "FAILED" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
