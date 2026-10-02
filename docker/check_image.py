"""Sanity check for the container image. The Dockerfile runs it at build time; you can re-run it
in a running container:  docker compose exec studio python /app/docker/check_image.py

Fails (exit 1) when:
- pip replaced one of NVIDIA's own builds (torch and friends, listed in /app/ngc-pins.txt),
- PyTorch is not a CUDA build,
- the backend can't import the Qwen-Image-2.1 pipeline the way the server will
  (studio.pipelines.real.import_runtime, which also catches diffusers' placeholder classes),
- the built web page is missing.
No GPU is needed: builds don't get one, so whether CUDA can see a GPU is reported, not required.
"""

from __future__ import annotations

import importlib.metadata as md
import os
import sys
from pathlib import Path

PINS = Path("/app/ngc-pins.txt")


def main() -> int:
    problems: list[str] = []
    notes: list[str] = []

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
