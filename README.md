# ai-image-studio

Generate and edit images with Qwen-Image-2.1 on an NVIDIA DGX Spark: a web page on your LAN with
a prompt box, an Options drawer that remembers your settings, and a history of every run so you
can reuse and tweak prompts.

## Status

| Part | State |
|---|---|
| Web studio: backend, real model, web page, container (milestones M1–M4, M7) | **Built and running on the Spark** (you built the image there and generated images, 2026-10-02). The automated tests (backend, front-end and in-browser) run against a fake test pipeline, because my sandbox has no GPU or Docker. [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) is the checklist for the real-hardware checks: sections 1–13 for that first build, then one section per release (14 to 19). |
| Version 1.1: run housekeeping (built, tested with the fake pipeline; **its Spark checks not yet reported**) | **Cancel** a queued or running job (finished images are kept), **Keep** a run so it never expires, and **auto-expiry** of runs older than `STUDIO_RETENTION_DAYS` (default 30) with a warning in a card's last week. [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 14 is the checklist. |
| Version 1.2: editing with several images, **the server side** (built and tested with the fake pipeline; **the real GPU path has not been run**) | Upload several images, run one edit over them with a prompt that can say "image 1", "image 2"; a "result follows image N" choice, a 1K/2K choice, Transparent in Edit. The page for it arrived in 1.6 (next row); [`scripts/edit_via_api.sh`](scripts/edit_via_api.sh) drives the same API from a terminal, and [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 15 is that checklist. Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §21. |
| Version 1.3: quick size, drafts and thumbnails (built, tested with the fake pipeline; **not yet run on the Spark**) | A **scale picker** (100 / 75 / 50 / 25%) on the prompt bar makes the image smaller without opening Options; a **Draft** button makes a small, quick try of your prompt that jumps ahead of waiting full-size runs; every image has a **Thumbnail** you can download. Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §22; [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 16 is the checklist. |
| Version 1.4: regenerate at full size (built, tested with the fake pipeline; **not yet run on the Spark**) | A run made smaller than you had selected (a Draft, or Scale below 100%) gets a **Regenerate larger** button that sends the same prompt and seed again at the full size and steps you had selected. It will not be the same picture (a new size is a new picture). (A greyed **Upscale** placeholder that stood beside it was removed in 1.10, which replaced it with Make 4K.) Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §23; [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 17 is the checklist. |
| Version 1.5: Regenerate larger in the image viewer (built, tested with the fake pipeline; **not yet run on the Spark**) | Click an image to open the viewer: it has the same **Regenerate larger** button, which queues a new job for **that one image** (its own seed, one image) at the full size. The answer appears inside the viewer. Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §24; part (e) of [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 17 is the check. |
| Version 1.6: **the editing page** (M5b; built, tested with the fake pipeline; **the real GPU path has not been run**) | Switch to **Edit** and add up to four pictures (the **Add images** tile, drag them onto the prompt box, paste, or **Edit this** on a result). They are numbered in the order the model sees them: click a number to put "image 2" in your prompt, drag or use the arrows to reorder. Size on Auto follows one of your pictures (**Result follows image N**); Options has **Resolution** 1K or 2K, with the cost in units and a warning when an edit looks heavy. Edit cards show their numbered sources; **Reuse** and **Retry** work for edits. Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §21.4 and §21.11; [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 18 is the checklist, and it is also the Spark test for edits (M5c). |
| Version 1.7: **Load model / Unload model** (built, tested with the fake pipeline; **not yet run on the Spark**) | A button beside the model pill starts loading the model **now**, so you can write your prompt while it loads instead of waiting after pressing Generate; when the model is loaded and idle the same place offers **Unload model**, which gives the memory back at once. Nothing loads by itself. Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §25; [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 19 is the checklist. |
| Version 1.8: **Music, the server side** (built, tested with the fake pipeline, and the real pipeline's own code on tiny random weights; **the real music model has never run**) | `POST /api/runs` with `"mode": "music"` makes **instrumental music** (or music with lyrics) with **MiniMax-Music3**: each track is a WAV file with a note that it is machine-generated, served with range requests so a player can seek. Only **one model is in memory at a time**: a music run unloads the image model first, and a picture does the reverse. Every model now loads **from the local cache and only fetches what is missing**. **`scripts/minimax_music.py`** makes a track from a terminal, with no page: `docker compose exec studio python /app/scripts/minimax_music.py --genre "ambient" --duration 15 --out /data/try.wav`. The page for it is version 1.9. Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §26; [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 20 is the checklist for the real model. |
| Version 1.9: **the Music tab** (built, tested with the fake pipeline in a real browser; **the real music model has never run**) | A second tab, **Music**, next to **Images**. Describe the music with boxes (genre, mood, tempo, key, instruments); the **description the model will read** is shown below them, built as you type and editable (*Rebuild from fields* undoes your edits). Music is **instrumental unless you turn on Add lyrics**, which adds a voice box and a lyrics box with buttons that put the section tags (`[Verse]`, `[Chorus]`…) on lines of their own. Choose a length (shortcuts from 15 seconds to 5 minutes), 1 to 4 versions, and Make music. Each track gets a **player**, a **Download WAV** and the usual Reuse, Keep, Delete and Cancel, with two progress bars (composing, rendering). The model pill now says which model is loaded, and the button beside it loads or unloads the model of the tab you are on. Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §26; [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 20 has the page's checks. |
| Version 1.10: **Make 4K** (built, tested with the fake pipeline in a real browser; **not yet run on the Spark**) | A **Make 4K** button on **any picture** the studio holds (on its card; in the viewer for a run with several pictures, and for an **edit's source images**) makes a **4K PNG** beside the original, and **Download 4K** then sits next to Download. A **16:9** picture (such as the model's own 2752×1536) is trimmed to exactly 16:9 (0.8% of the width, equally from both sides) and becomes exactly **3840×2160**; **any other shape** is enlarged, keeping its shape and cutting nothing, until it covers 3840×2160 (your default 2048×2048 becomes **3840×3840**). It works for pictures made before 1.10 too, up to a 2× enlargement. It is **bigger, not sharper**: no detail is added. **Upscale a picture…**, above your runs, does the same for a **file from your computer** (PNG, JPEG or WebP) and gives you the 4K PNG as a download, adding nothing to the history. The model cannot make 3840×2160 itself (2160 is not a multiple of 32). A dedicated upscaler model would add real detail: **[`scripts/upscale_probe.py`](scripts/upscale_probe.py)** is a probe to run once on the Spark to see whether one runs there (nothing in the studio uses it). Specified in [`docs/DESIGN.md`](docs/DESIGN.md) §27; [`docs/SPARK_TEST.md`](docs/SPARK_TEST.md) section 21 is the checklist. |
| Version 1.11: **Enlarge** (built, tested with the fake pipeline in a real browser and with a tiny model through the real code path; **not yet run on the Spark**) | An **Enlarge** button beside Make 4K on every picture (and every edit source): **the same picture, bigger and sharper**, taken to the 4K frame (3840×2160, or 2160×3840 upright; a 16:9 picture is trimmed to exactly 16:9) with an **upscaler model** (Real-ESRGAN x2plus: one ×2 pass up to a 3× enlargement, two above, up to 4×; then one resize to the exact size). It needs the model file, which **you download once** (below); without it the button is dimmed and says so. The copy replaces a Make 4K copy; the original is never touched. |
| Still to come | The Spark test for edits (M5c), local edits (M5d), the optional prompt rewriter (M5e, only if you want it), **Qwen redrawing** of an enlarged picture (an experiment first: `docs/DESIGN.md` §28.5), and the Spark smoke test together (M8). |
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

**Which build am I running?** The page title and the browser tab show the version, for example
`AI Image Studio v1.1`. The same number comes from `curl -s localhost:8080/api/health`. If the page
shows no version, or an older one than `git log` suggests, the page is stale: reload it with
Ctrl+Shift+R (Cmd+Shift+R on a Mac) and check that `docker compose up -d --build` ran to the end.
(Since v1.1 the server tells the browser to revalidate the page on every load, so a plain reload is
enough after an update.)

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

- **Make 4K (1.10):** any picture in the history (a result, or an edit's source image) that would need more than 1× and at most 2× to cover 3840×2160 gets a **Make 4K** button (hover it: it says the size it will make). The copy is a PNG next to the original (`data/images/<run>/<n>-4k.png`, or under `data/inputs/` for a source) which **Download 4K** then sends. It takes a few seconds on the Spark's CPU, needs no GPU and works while a picture is being made. A 16:9 or 9:16 picture is trimmed to exactly the frame; any other shape is enlarged until it covers the frame, with nothing cut off (so a square becomes 3840×3840, about 15 MP and a big file). Bigger, not sharper. Pictures that would need more than a doubling (drafts, small pictures) or are already 4K do not get the button, and the copy never exceeds 20 MP. The 4K file goes when its run is deleted or expires. **Upscale a picture…** (above your runs) takes a file from your computer, checks it as an upload is checked (20 MB, 16 MP, a phone photo's rotation applied) and returns the 4K PNG as a download: nothing is stored.
  own 2752×1536, gets a **Make 4K** button on its card and in the viewer. It makes a 3840×2160 PNG next to the
  original (`data/images/<run>/<n>-4k.png`), which **Download 4K** then sends. It takes a few seconds on the
  Spark's CPU, needs no GPU and works while a picture is being made. It trims the long side to exactly 16:9
  and enlarges with a standard resize: bigger, not sharper. Other shapes (square, 4:3, 3:2…), small pictures
  and pictures that are already 4K do not get the button. The 4K file goes when its run is deleted or expires.
- **Enlarge (1.11):** a second button beside Make 4K that makes **the same picture bigger and sharper**: an upscaler model
  (Real-ESRGAN x2plus) enlarges it, then it is sized to exactly 3840×2160 (or the shape's equivalent), up to a 4× enlargement, so a
  50% picture (1376×768) qualifies and Make 4K cannot do it. It is slower than Make 4K (the GPU, a short-lived process of its own;
  minutes on the CPU). If a picture is being made it **waits for it** (the button says *Waiting…* on every open page, then *Enlarging…*) and goes before the runs still queued, because the image model and the upscaler do not fit on the GPU together; if something outside the studio is holding the GPU it says so (*Not enough memory for the upscaler right now…*). The copy **replaces** a Make 4K copy of that picture
  (`data/images/<run>/<n>-4k-enlarged.png`). It is bigger *and* sharper, but **the extra detail is the model's guess**: faces can come out
  smooth, small text may not sharpen, flat colour can pick up texture. How it looks on your pictures is what the Spark test (SPARK_TEST.md
  section 22) is for. **One-time setup, on the machine that runs the studio** (the studio never downloads it: the model has its own terms,
  which are yours to read):
  ```bash
  mkdir -p ~/.cache/huggingface/upscalers
  curl -L -o ~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth \
    https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth
  ```
  then reload the page. Until the file is there the button is dimmed and its tooltip (and the message if you press it) says so.
  `STUDIO_UPSCALER_MODEL` names another ×2 model file; `STUDIO_UPSCALER_DEVICE` is `auto`, `cuda` or `cpu`.
- **Settings** live in `.env`; [`.env.example`](.env.example) explains each one.
- **Data:** the history and images are in `./data`. The model is cached in
  `~/.cache/huggingface`, shared with anything else on the Spark that uses it, and is downloaded once.
- **Memory:** the Spark's 128 GB is shared with everything else, Hermes' LLM server included. The
  studio refuses to load the model when less than `STUDIO_MIN_FREE_GB` is free (40 GB to start
  with, until measured), and unloads it after 30 idle minutes (`STUDIO_IDLE_TIMEOUT_MIN`).
- **Scale and Draft:** the **Scale** buttons above the prompt make the image 100, 75, 50 or 25% of the
  chosen size in width and height (50% of 2048×2048 is 1024×1024, about four times faster). **Draft** sends
  a small, quick version of your prompt (512 px, at most 12 steps) ahead of any waiting full-size runs;
  it is for trying your wording, and the full-size image will look different, as will any other size.
  Both are settings you can tune: `STUDIO_DRAFT_SIZE`, `STUDIO_DRAFT_STEPS`.
- **Regenerate larger:** a finished Draft, or run made at a Scale under 100%, has a **Regenerate larger**
  button (hover it to see the size and steps). It queues the same prompt and seed at 100% of the size you
  had selected, with the steps you had selected. The result is a different picture from the small one, so
  use it to find a prompt you like, not to enlarge a picture you already like. Older runs have no button.
  The same button is in the viewer you get by clicking one image, where it enlarges just that image.
- **Editing:** **Edit** takes up to four pictures (`STUDIO_MAX_INPUT_IMAGES`, up to ten) and one prompt that can
  refer to them by number. Click a picture's number badge to put "image N" in the prompt; the order is what the
  model sees. **Resolution** (1K or 2K, in Options) sizes every picture you add as well as the result, so cost
  grows with both: the page shows it in units and warns above `STUDIO_EDIT_WARN_UNITS` (8 until the Spark has
  measured it). A picture that is still going up, or gone from the server, blocks Generate and says so.
- **Load model / Unload model:** the model normally loads when you press Generate, which makes the first run after a
  quiet spell slow. **Load model** (beside the model pill in the header, shown when the pill says *Model not loaded*
  or *Model problem*) loads it now, so it is ready by the time your prompt is. It makes the same memory check as a
  run, and the idle countdown (`STUDIO_IDLE_TIMEOUT_MIN`) starts when the load **finishes**. **Unload model** (shown
  when it is loaded and idle) gives the memory back at once. A run you send while it loads waits for it. With
  `STUDIO_IDLE_TIMEOUT_MIN=0` Load is not offered (it would unload again at once). The same two actions are
  `POST /api/model/load` and `POST /api/model/unload`.
- **Music (1.8):** the first load of the music model downloads about **29 GB** (up to 57 GB if the whole repository is
  fetched) and needs `STUDIO_MUSIC_MIN_FREE_GB` (40 to start with, until measured) free; once loaded it takes about 24 GB.
  Making music is **slow**, probably minutes for a minute of music (the Spark's memory speed sets a floor of about 1.5 s
  of work per second of music, an estimate and not a measurement), so try 15 seconds first. A WAV is about 10.6 MB a
  minute. The model's licence asks you to **say that music is machine-generated when you share it publicly**; read it
  in the model repository's `LICENSE`. Settings: `STUDIO_MUSIC_MODEL`, `STUDIO_MUSIC_MIN_FREE_GB`,
  `STUDIO_MUSIC_MAX_SECONDS` (300), `STUDIO_MUSIC_MAX_TRACKS` (4).
- **Music tab (1.9):** the tab's Load button is **Load music model** (the Images tab's stays *Load model*). Only one model is
  in memory, so loading one **unloads the other first**, and making music while the image model is loaded (or a picture
  while the music model is) swaps them for you, which takes a load each time. A track you start while the other model
  is busy waits its turn in the one queue. If the music model cannot run on the server, the tab says why instead of
  showing the form.
- **Models load from the cache:** with `STUDIO_LOCAL_FILES_ONLY=auto` (the default) a model is loaded from the cache
  with no network request at all, and only a load that fails because files are **missing** is tried again online (the
  first load, or after the cache was cleared). `true` never goes online and says how to download a missing file;
  `false` asks the hub on every load, as before 1.8, which would download an update of the model without asking.
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

**The version number** lives in `backend/studio/__init__.py` (`__version__`). The page shows it, the API
reports it, and `frontend/package.json` carries the same number (as `1.1.0`); a test fails if they
disagree, so bump them together. 1.0 was the first build on the Spark; each release since bumps the minor
(1.1, 1.2, 1.3, 1.4, 1.5, ...). "Version 2" in the design document names a set of features, not a version number.

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

### Making music from a terminal (`scripts/minimax_music.py`, new in 1.8)

Makes an instrumental track (or one with lyrics) with MiniMax-Music3 using the same pipeline code as the studio, with no
web page. It needs `torch`, `transformers` and **`diffusers` 0.40.0 or newer** (the released version that has the music
pipeline). The studio's image has all of that, so the easy way is inside the running container:

```bash
docker compose exec studio python /app/scripts/minimax_music.py --genre "ambient" --mood "slow, spacious" \
    --instruments "soft pads and a piano" --duration 15 --out /data/try.wav          # appears in ./data/try.wav
docker compose exec studio python /app/scripts/minimax_music.py --help               # every option
docker compose exec studio python /app/scripts/minimax_music.py --genre "lo-fi hip hop" --bpm 80 --dry-run   # shows what would be sent
```

It loads the model from the local cache and only fetches what is missing (`--hub offline` never touches the network),
prints a progress line for each stage, and writes a 16-bit stereo WAV that says in its file information that it is
machine-generated. The first run downloads about 29 GB. Exit codes: 0 ok, 2 bad arguments, 3 environment problem,
4 model load failed, 5 generation failed, 6 could not write the file, 130 interrupted (Ctrl+C stops it within a second
or two).


### Trying other upscaler models and settings (`scripts/upscale_probe.py`, new in 1.10)

A probe, not a feature: it compares what an ESRGAN-class upscaler makes of a picture with a plain resize, and shows
how fast it runs on the Spark, with options to try (`--dtype bf16`, `--tile`). Enlarge (below) uses the same model and
the same tiling code; the probe is how you try other models and settings (`docs/DESIGN.md` §27.5, §28). From version
1.11 the image contains `spandrel`, so only the script needs copying in (it lasts until the container is recreated):

```bash
docker compose cp scripts/upscale_probe.py studio:/tmp/upscale_probe.py
docker compose exec studio python /tmp/upscale_probe.py --model /models/upscalers/RealESRGAN_x2plus.pth --crop 512x288
```

The model file is not in the repository: put Real-ESRGAN's x2 model (64 MB, from
`https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth`) in
`~/.cache/huggingface/upscalers/` on the Spark. Run with `--image /data/images/<run>/0.png` for a full 16:9 picture
the studio made, and it also writes `4k-lanczos.png` (what Make 4K makes) and `4k-from-model.png` to compare.
`docs/SPARK_TEST.md` section 21 says what to send back.
