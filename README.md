# ai-image-studio

Generate and edit images with Qwen-Image-2.1 on an NVIDIA DGX Spark: a web page on your LAN with
a prompt box, an Options drawer that remembers your settings, and a history of every run so you
can reuse and tweak prompts.

## Status

| Part | State |
|---|---|
| Web studio: backend, real model, web page, container (milestones M1–M4, M7) | Built and tested with a fake test pipeline (backend, front-end and in-browser tests). **Not yet run on the Spark:** the image build and the real model need Docker and the GB10, which my sandbox doesn't have. [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) is the checklist for that first run. |
| Version 2, part 1: run housekeeping (built, tested with the fake pipeline; **not yet run on the Spark**) | **Cancel** a queued or running job (finished images are kept), **Keep** a run so it never expires, and **auto-expiry** of runs older than `STUDIO_RETENTION_DAYS` (default 30) with a warning in a card's last week. [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 14 is the checklist. |
| Version 2, part 2: editing (planned, not built) | **Editing with several images** for one prompt: numbered images you can reference in the prompt, a "result follows image N" selector, a 1K/2K choice, and later local edits (marks and masks). Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §21. |
| Still to come | The Spark smoke test together (M8). |
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

### 5. Backing up and restoring

The container itself holds almost nothing worth saving, so "backing up the container" really means
saving four things that live outside it:

| What | Where it lives | If you lose it |
|---|---|---|
| **Your history and images** | `./data` in the repo folder | **Gone for good.** This is the part that matters |
| Your settings | `.env` | Easy to recreate, unless it holds a token |
| The built image | `ai-image-studio:local` in Docker | Rebuildable, but a rebuild is not identical: it downloads the packages again |
| The model | `~/.cache/huggingface` (31 GiB) | Re-downloadable, but Qwen could have changed it in the meantime |

**Back up:**

```bash
scripts/backup.sh ~/backups              # history, image and settings
scripts/backup.sh --model ~/backups      # ... and the 31 GiB model too
```

The result is **one file**, such as `~/backups/ai-image-studio-backup-20261002-143015.tar`, that you can
copy anywhere: a NAS, an external drive, another machine. It is an ordinary tar file (not compressed as a
whole, because what is inside already is), and any archive tool can open it. Inside:

| Piece | What it is |
|---|---|
| `data.tar.gz` | your history and images |
| `image.tar.gz` | the Docker image, exactly as built (left out with `--no-image`) |
| `env.backup` | your `.env` (left out with `--no-env`) |
| `model-cache.tar` | the model's files, only with `--model` |
| `MANIFEST.txt`, `SHA256SUMS` | what was backed up (including the git commit and image id), and a checksum for every other piece |

- **Downtime is a few seconds.** The database must be at rest to be copied consistently, so the studio
  is stopped while `./data` is packed and started again straight away. The image and the model are
  copied while it runs.
- **It checks its own work.** Once the `.tar` is finished, the script reads it back and compares every
  checksum. The file only gets its real name if that passes, so a `.tar` you can see is a good one.
  (`--no-verify` skips this to save time.)
- **It won't cut off your work.** If a picture is being generated or jobs are waiting, the script says
  so and stops (exit code 3). Try again later, or add `--interrupt` to go ahead anyway.
- **It cleans up after itself.** If a backup fails part-way, the studio is started again and nothing
  half-written is left behind.
- **Disk space:** the destination needs room for the backup plus, briefly, a copy of its biggest piece.
  Without `--model` the backup is about the size of the compressed Docker image: roughly 10 GB, since
  NVIDIA's base image alone is an 8.3 GiB compressed download. The model adds about 31 GiB.
- `--dry-run` shows what would happen and changes nothing, a good first run. `--keep N` deletes the
  oldest backups afterwards, keeping the newest N. `--yes` stops it asking questions. `--help` lists
  everything.

**Copy it to your NAS**, then check the copy:

```bash
cp ~/backups/ai-image-studio-backup-20261002-143015.tar /mnt/nas/backups/       # a mounted share
# or: rsync -ah --progress ~/backups/ai-image-studio-backup-20261002-143015.tar user@nas:/volume1/backups/

scripts/restore.sh --verify /mnt/nas/backups/ai-image-studio-backup-20261002-143015.tar
```

`--verify` reads the whole file and checks every checksum, changes nothing, and needs no Docker, so it
also works on another Linux machine that has these scripts. It prints "This backup is intact" or says
which piece is damaged. Do this before you delete the original: a copy that stopped half way, or was
damaged on the way, is exactly what it catches.

**Restore:**

```bash
scripts/restore.sh /mnt/nas/backups/ai-image-studio-backup-20261002-143015.tar
```

- The `.tar` is read where it is: nothing is unpacked to a temporary place first. From a NAS share it
  reads the file twice (once to check it, once to restore), so on a slow link it is quicker to copy it
  back to the Spark first.
- It checks the checksums first, and inspects the archives before unpacking anything.
- **It never deletes anything.** If `./data` already has files in it, the restore is refused. Add
  `--force` and the current folder is moved aside to `data.before-restore-<time>` instead, and the same
  goes for `.env` and the model folder. Delete those yourself when you are sure.
- It loads the Docker image (the current `ai-image-studio:local` stays available as
  `ai-image-studio:before-restore-<time>`), restores `.env`, and restores the model if the backup has it and
  it isn't already in the cache.
- The studio is stopped while this happens. If it was running, it is started again, from the restored
  image; otherwise add `--start`, or run `docker compose up -d --no-build` yourself.
- If the code on this machine is at a different commit from the backup, it tells you, and how to match
  it (`git checkout <commit>`).
- Only restore backups you made yourself: archives are unpacked into your folders.

**Worth knowing**

- **The `.tar` can contain your Hugging Face token** (in `.env`). The script makes it readable only by you,
  but a copy on a NAS has whatever permissions the NAS gives it. Keep it private, or leave the token out
  with `--no-env`.
- **A backup on the same disk won't survive that disk failing.** That is what the NAS copy is for.
- **Going back after an upgrade needs the data too.** Later versions change the database's layout
  (version 2 does), and the studio refuses to open a database newer than it understands. To go back,
  restore the image *and* the data from a backup made before the upgrade. Take one before every upgrade.
- **A free safety net before a rebuild** (instant, no extra space): `docker tag ai-image-studio:local
  ai-image-studio:previous` keeps the current image, which a rebuild would otherwise orphan.
- **Scheduling** (optional). For example, every Sunday at 03:30, keeping the newest four:
  `30 3 * * 0  cd ~/ai-image-studio && scripts/backup.sh --yes --keep 4 ~/backups >> ~/backups/backup.log 2>&1`
  (add it with `crontab -e`). If the studio is busy then, it skips that week and says so in the log.
  Run the command by hand first to be sure it works on your machine.

<details>
<summary>Looking inside the .tar, or doing it by hand, without the scripts</summary>

The `.tar` is a plain tar file, so on any computer (including the NAS, if it has a shell):

```bash
tar -tf ai-image-studio-backup-20261002-143015.tar                  # list what is inside
mkdir unpacked && tar -xf ai-image-studio-backup-20261002-143015.tar -C unpacked
(cd unpacked && sha256sum -c SHA256SUMS)                           # every line should say OK
```

To make a backup without the scripts, run these in the repo folder, as your normal user. `BK` is where
the pieces go; the last line packs them into one file:

```bash
BK=~/backups/ai-image-studio-$(date +%Y%m%d); mkdir -p "$BK"
git rev-parse HEAD > "$BK/git-commit.txt"
cp .env "$BK/env.backup" && chmod 600 "$BK/env.backup"

docker compose stop                                   # the database must be at rest
tar -czf "$BK/data.tar.gz" data                       # your history and images
docker compose start
docker save ai-image-studio:local | gzip > "$BK/image.tar.gz"
tar -cf "$BK/model-cache.tar" -C ~/.cache/huggingface hub/models--Qwen--Qwen-Image-2.1   # optional

tar -cf "$BK.tar" -C "$BK" .                          # one file (restore.sh won't read this one: it needs the manifest)
```

To restore by hand:

```bash
docker compose stop
tar -xzf "$BK/data.tar.gz"                            # unpacks ./data (move any existing one away first)
cp "$BK/env.backup" .env
gunzip -c "$BK/image.tar.gz" | docker load
tar -xf "$BK/model-cache.tar" -C ~/.cache/huggingface # only if you backed it up
docker compose up -d --no-build
```

</details>

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
  with, until measured), and unloads it after 30 idle minutes (`STUDIO_IDLE_TIMEOUT_MIN`).
- **Cancel, Keep and clean-up:** **Cancel** on a card stops a queued job at once, or a running one
  within a step (images already finished stay). Runs are deleted automatically 30 days after they
  were made (`STUDIO_RETENTION_DAYS`; `0` = never) unless you press **Keep** on them, and a card
  warns you in its last week.
- **No login:** anyone who can reach the port can use it. Keep it on a trusted network, or set
  `STUDIO_BIND=127.0.0.1` and use an SSH tunnel or NVIDIA Sync.

## Before you use the output

Qwen-Image-2.1 is released under the **Qwen Research License Agreement**: use is allowed
**for research or evaluation purposes only**, and commercial use needs a separate licence from
Qwen. Read it before using the images for anything else:
[LICENSE](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE). The agreement says nothing
explicit about generated images, so if you're unsure whether a use counts as commercial, ask Qwen.
This repository does not contain the model; it is downloaded from Hugging Face when the studio first
runs. Details: `docs/DESIGN.md` §18 item 2.

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

### Backup scripts

`scripts/backup.sh` and `scripts/restore.sh` have their own tests, which run anywhere without Docker or a
GPU (a stand-in replaces `docker`; the archives, checksums and database are real):

```bash
bash scripts/tests/backup_restore_test.sh
```

## Command-line tool

Needs a CUDA-enabled PyTorch and a `diffusers` build that includes `QwenImage21Pipeline`
(not in a PyPI release at the time of writing: install `diffusers` from GitHub).

```bash
./scripts/qwen_image.py --help
./scripts/qwen_image.py generate "A bright neon shop sign, rainy day" --aspect-ratio 16:9
./scripts/qwen_image.py edit "Change the background to a sunset beach" --image input.png
./scripts/qwen_image.py generate "test" --dry-run      # shows the plan, loads nothing
```
