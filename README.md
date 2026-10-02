# ai-image-studio

Generate and edit images with Qwen-Image-2.1 on an NVIDIA DGX Spark.

## Status

| Part | State |
|---|---|
| `scripts/qwen_image.py` — command-line tool: text-to-image, image editing, transparent (RGBA) output | Written and exercised with mocks only. **Not yet run on a real GPU.** |
| Web studio (container, prompt page, options drawer, run history) | Design: [`docs/DESIGN.md`](docs/DESIGN.md). **Milestone M1 (backend) built**: API, run queue, separate worker process, SQLite history, test ("fake") pipeline. Not built yet: the real model (M2), the web UI (M3+), the container (M7). |

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

## Backend (development)

Needs Python 3.11 or newer. The real model is wired in at milestone M2; until then use the fake
pipeline, which makes clearly labelled test images. (With the default `STUDIO_PIPELINE=real` the
server starts but reports the pipeline as unavailable.)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

# run the server on http://127.0.0.1:8080
STUDIO_PIPELINE=fake STUDIO_DATA_DIR=./data STUDIO_HOST=127.0.0.1 python -m studio

# queue a run (mutating requests need the X-Studio-Client header)
curl -X POST http://127.0.0.1:8080/api/runs -H 'X-Studio-Client: 1' -H 'Content-Type: application/json' \
     -d '{"prompt": "a lighthouse at dusk", "options": {"width": 1024, "height": 1024, "steps": 20}}'

# run the tests
python -m pytest
```

Configuration is by environment variables; the full list is in `docs/DESIGN.md` §13.
