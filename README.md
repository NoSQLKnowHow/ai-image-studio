# ai-image-studio

Generate and edit images with Qwen-Image-2.1 on an NVIDIA DGX Spark: a web page on your LAN with
a prompt box, an Options drawer that remembers your settings, and a history of every run so you
can reuse and tweak prompts.

## Status

| Part | State |
|---|---|
| Web studio: backend, real model, web page, container (milestones M1–M4, M7) | Built and tested with a fake test pipeline (backend, front-end and in-browser tests). **Not yet run on the Spark:** the image build and the real model need Docker and the GB10, which my sandbox doesn't have. [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) is the checklist for that first run. |
| Still to come | Edit mode with uploads (M5); cancel, pin and auto-expiry (M6); the Spark smoke test together (M8). Plan and decisions: [`docs/DESIGN.md`](docs/DESIGN.md). |
| `scripts/qwen_image.py`: command-line tool for text-to-image, image editing, transparent (RGBA) output | Written and exercised with mocks only. **Not yet run on a real GPU.** |

## Run it on the Spark

On the Spark, as your normal user (not with `sudo`):

```bash
git clone https://github.com/NoSQLKnowHow/ai-image-studio.git && cd ai-image-studio
cp .env.example .env
sed -i "s/^STUDIO_UID=.*/STUDIO_UID=$(id -u)/; s/^STUDIO_GID=.*/STUDIO_GID=$(id -g)/" .env
mkdir -p data
docker compose up -d --build
```

Then open `http://<spark-address>:8080` from any device on your network. The first build pulls
NVIDIA's PyTorch image and the first image downloads the model, so both take a while.

- **Settings** live in `.env`; [`.env.example`](.env.example) explains each one.
- **Data:** the history and images are in `./data`. The model is cached in
  `~/.cache/huggingface`, shared with anything else on the Spark that uses it.
- **Memory:** the Spark's 128 GB is shared with everything else, Hermes' LLM server included. The
  studio refuses to load the model when less than `STUDIO_MIN_FREE_GB` is free (40 GB to start
  with, until measured), and unloads it after 15 idle minutes.
- **No login:** anyone who can reach the port can use it. Keep it on a trusted network, or set
  `STUDIO_BIND=127.0.0.1` and use an SSH tunnel or NVIDIA Sync.
- **Update:** `git pull && docker compose up -d --build`.

The first time, follow [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) step by step: it says what each
step should show and what to do when it doesn't.

## Before you use the output

Check the Qwen-Image-2.1 model license. Third-party summaries say it may be non-commercial or
research-only; this has **not** been verified against the official license.

## Development

Nothing here needs a GPU: the **fake pipeline** (`STUDIO_PIPELINE=fake`) draws labelled test images,
and the tests use it. The real model only runs in the container.

### Backend (Python 3.11+)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

# the server on http://127.0.0.1:8080 (serves the web page too, once frontend/dist is built)
STUDIO_PIPELINE=fake STUDIO_DATA_DIR=./data STUDIO_HOST=127.0.0.1 python -m studio

python -m pytest
```

Mutating API requests need the header `X-Studio-Client: 1`, for example:

```bash
curl -X POST http://127.0.0.1:8080/api/runs -H 'X-Studio-Client: 1' -H 'Content-Type: application/json' \
     -d '{"prompt": "a lighthouse at dusk", "options": {"width": 1024, "height": 1024, "steps": 20}}'
```

`requirements-server.txt` holds the web server's pins and is shared by `requirements.txt`
(development) and `requirements-container.txt` (the image, which adds diffusers, transformers and
accelerate on top of NVIDIA's PyTorch). All configuration is by environment variable:
`docs/DESIGN.md` §13.

### Web page (Node 22)

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, with /api proxied to the backend above (STUDIO_API)
npm test           # unit tests (Vitest)
npm run build      # type-check, then build into frontend/dist
npm run e2e        # browser tests (Playwright) against the built page and a fake-pipeline backend
```

`npm run e2e` starts its own backend, using `backend/.venv/bin/python` (set `STUDIO_PYTHON` to use
another) and a temporary data folder. It needs Chromium; if it isn't installed, run
`npx playwright install chromium` once.

## Command-line tool

Needs a CUDA-enabled PyTorch and a `diffusers` build that includes `QwenImage21Pipeline`
(not in a PyPI release at the time of writing: install `diffusers` from GitHub).

```bash
./scripts/qwen_image.py --help
./scripts/qwen_image.py generate "A bright neon shop sign, rainy day" --aspect-ratio 16:9
./scripts/qwen_image.py edit "Change the background to a sunset beach" --image input.png
./scripts/qwen_image.py generate "test" --dry-run      # shows the plan, loads nothing
```
