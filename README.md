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

## Build and run on the DGX Spark

The studio runs in a Docker container that you build **on the Spark itself**. The Spark is an arm64
machine, so an image built on an Intel/AMD PC or a Mac will not run on it. Run everything below on the
Spark, as your normal user: not with `sudo`, and not as root.

**The short version**, once the checks under "Before you start" pass:

```bash
git clone https://github.com/NoSQLKnowHow/ai-image-studio.git && cd ai-image-studio
cp .env.example .env
sed -i "s/^STUDIO_UID=.*/STUDIO_UID=$(id -u)/; s/^STUDIO_GID=.*/STUDIO_GID=$(id -g)/" .env
mkdir -p data
docker compose up -d --build        # builds the image, then starts it
```

Then open `http://<spark-address>:8080` from any device on your network. The rest of this section
explains each step and what to do when one fails.

### Before you start

| You need | How to check |
|---|---|
| Docker that works without `sudo`, with NVIDIA's runtime (both come with DGX OS) | `docker ps` lists containers without an error |
| Docker can see the GPU | `docker run --rm --runtime=nvidia --gpus=all nvcr.io/nvidia/cuda:13.0.1-base-ubuntu24.04 nvidia-smi` prints a table naming the **GB10**. Its memory columns may say "Not Supported"; that is normal on the Spark |
| Internet access during the build | The build downloads from `nvcr.io` (NVIDIA), Docker Hub (a small Node image), `registry.npmjs.org`, PyPI and GitHub. **No NVIDIA account or login is needed**: NVIDIA's registry allows anonymous pulls of the image used here |
| Access to this repository | It is private, so `git clone` needs your GitHub credentials |
| Free disk space | NVIDIA's PyTorch base image is an **8.3 GiB download** (compressed) and unpacks to considerably more. The model, downloaded the first time you generate an image, is another **31 GiB**, kept outside the image in `~/.cache/huggingface`. As a rough budget, allow **60 GB** free. Check with `df -h ~` and `docker info \| grep "Docker Root Dir"` (then `df -h` on that folder) |

### 1. Get the code and configure it

```bash
git clone https://github.com/NoSQLKnowHow/ai-image-studio.git
cd ai-image-studio
cp .env.example .env
sed -i "s/^STUDIO_UID=.*/STUDIO_UID=$(id -u)/; s/^STUDIO_GID=.*/STUDIO_GID=$(id -g)/" .env
mkdir -p data
```

The container runs as **your** user, so the history in `./data` and the model cache belong to you.
`.env` holds every setting, explained inside it; the defaults are fine for a first build.

### 2. Build the image

```bash
docker compose build --progress=plain 2>&1 | tee build.log
```

`--progress=plain` prints every step in full, so a failure explains itself, and `tee` keeps a copy in
`build.log`. What the build does, in order:

| Step | What happens |
|---|---|
| 1 | Pulls a small Node image and builds the web page (`npm ci`, `npm run build`) |
| 2 | Pulls **NVIDIA's PyTorch image**, `nvcr.io/nvidia/pytorch:25.10-py3`. This is the long part of the first build |
| 3 | Notes which packages NVIDIA built for the Spark's GPU (PyTorch and friends) and holds them fixed, so nothing can quietly replace them with generic builds |
| 4 | Installs the Python packages: `diffusers` from a pinned GitHub commit (Qwen-Image-2.1 is not in a release yet), `transformers`, `accelerate` and the web server |
| 5 | Removes `torchao`, an extra library NVIDIA bundles that stops `diffusers` from importing the Qwen-Image pipeline |
| 6 | Copies in the server and the web page |
| 7 | **Runs a self-check** (`docker/check_image.py`): the pipeline imports, PyTorch is a CUDA build, the page is present. If something doesn't fit, **the build stops here**, with the reason, instead of failing later at your first image |

A successful build ends with lines like these (your version numbers may differ):

```
check_image: Python 3.12.3; installed: torch==2.9.0a0+145a3a7bda.nv25.10, ... diffusers==0.41.0.dev0, ...
check_image: torch 2.9.0a0+145a3a7bda.nv25.10 (CUDA 13.0), diffusers 0.41.0.dev0, transformers 5.18.0
check_image: pipeline features: {'negative_prompt': True, 'cfg_scale': True, 'step_progress': True, 'transparent': True, 'edit': True}
check_image: GPU visible now: no (expected during a build)
check_image: OK
```

"GPU visible now: no" is normal: Docker does not give builds a GPU. Find these lines again with
`grep check_image build.log`.

**Plain Docker instead of Compose:** `docker build -t ai-image-studio:local .` builds the same image
(add `--build-arg NGC_TAG=<release>` to pick another NVIDIA release, below). Compose is still what you
use to start it, because it also connects the GPU, your folders and your settings.

### 3. Start it

```bash
docker compose up -d
docker compose ps                  # STATUS "healthy" within a minute or two
docker compose logs -f studio      # Ctrl+C stops following; the server keeps running
```

In the log, look for `serving the web UI`, `ready (pipeline=real …)` and
`capability check passed: {… 'name': 'NVIDIA GB10' …}`. That last line means PyTorch inside the
container can see and use the GPU.

Open `http://<spark-address>:8080` from any device on your network (`hostname -I` on the Spark lists
its addresses). **The first image you generate downloads the model** (31 GiB), so it takes much longer
than later ones. After that it is read from the cache.

### 4. Updating and rebuilding

```bash
git pull
docker compose up -d --build
```

Docker reuses every step that has not changed (the build output marks them `CACHED`), so a rebuild
after a code change takes seconds to a couple of minutes and downloads nothing large. Two things
force a long rebuild: `--no-cache`, which re-downloads the Python packages (use it only if told to),
and changing `NGC_TAG`, which pulls another multi-GB base image. A rebuild never touches the model
cache or your history.

**A different NVIDIA PyTorch release:** set `NGC_TAG` in `.env` (default `25.10-py3`) and rebuild. The
studio has been built and checked with `25.10-py3` only. The registry also has `25.11-py3`,
`25.12-py3` and `26.08-py3`, but they are untested; if one fails the self-check, the message says why,
and you can go back to `25.10-py3`.

### If the build fails

| You see | What it means, and what to do |
|---|---|
| `permission denied … /var/run/docker.sock` | Your user isn't in the `docker` group: `sudo usermod -aG docker $USER`, then log out and back in. Don't fix it by running Compose with `sudo`: that changes your home folder, and with it the model cache folder |
| `no space left on device` | `df -h` and `docker system df` show where the space went. `docker image prune` and `docker builder prune` free what Docker no longer uses (they ask first) |
| Timeouts or connection errors while pulling or installing | The Spark can't reach that site. `curl -I https://nvcr.io/v2/` should answer `401 Unauthorized`: that is the registry saying it is reachable and wants a token, which Docker fetches by itself. Check the network or proxy |
| `exec format error` | The image was built for a different kind of CPU. Build it on the Spark |
| `check_image: PROBLEM: …` | The build's self-check found a mismatch. The problem line names the library, and the lines above it list the installed versions and the full traceback. To share it: `sed -n '/check_image: Python/,$p' build.log`. Example: `cannot import name 'FqnToConfig' from 'torchao.quantization'` was caused by a library NVIDIA bundles, and the Dockerfile now removes it |
| `unknown or invalid runtime name: nvidia` (when starting) | Docker isn't set up with NVIDIA's runtime: `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |

Problems after the build, such as memory, the model download or the page not loading, are in the
troubleshooting table of [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md), which also walks through the
first run step by step and says what each step should show.

### Good to know

- **Settings** live in `.env`; [`.env.example`](.env.example) explains each one.
- **Data:** the history and images are in `./data`. The model is cached in
  `~/.cache/huggingface`, shared with anything else on the Spark that uses it, and is downloaded once.
- **Memory:** the Spark's 128 GB is shared with everything else, Hermes' LLM server included. The
  studio refuses to load the model when less than `STUDIO_MIN_FREE_GB` is free (40 GB to start
  with, until measured), and unloads it after 15 idle minutes.
- **No login:** anyone who can reach the port can use it. Keep it on a trusted network, or set
  `STUDIO_BIND=127.0.0.1` and use an SSH tunnel or NVIDIA Sync.

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
