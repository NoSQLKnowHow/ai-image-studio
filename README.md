# dgx-spark-image-studio

Generate and edit images with Qwen-Image-2.1 on an NVIDIA DGX Spark.

## Status

| Part | State |
|---|---|
| `scripts/qwen_image.py` — command-line tool: text-to-image, image editing, transparent (RGBA) output | Written and exercised with mocks only. **Not yet run on a real GPU.** |
| Web studio (container, prompt page, options drawer, run history) | Design complete — see [`docs/DESIGN.md`](docs/DESIGN.md). **Not built yet.** Build order is milestones M1–M8 in the spec. |

## CLI quick start

Needs a CUDA-enabled PyTorch and a `diffusers` build that includes `QwenImage21Pipeline`
(at the time of writing it was not in the PyPI release, so install `diffusers` from GitHub `main`).

```bash
./scripts/qwen_image.py --help
./scripts/qwen_image.py generate "A bright neon shop sign, rainy day" --aspect-ratio 16:9
./scripts/qwen_image.py edit "Change the background to a sunset beach" --image input.png
./scripts/qwen_image.py generate "test" --dry-run      # shows the plan, loads nothing
```

## Before you use the output

Check the Qwen-Image-2.1 model license. Third-party summaries say it may be non-commercial or
research-only; this has **not** been verified against the official license.
