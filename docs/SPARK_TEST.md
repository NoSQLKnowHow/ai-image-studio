# First run on the DGX Spark: a checklist

This is the part I could not do from my sandbox: build the image and run the real model on the
Spark's GPU. Work through it in order; each step says what "good" looks like. Steps 1–8 get you to
a first image. Steps 9–13 measure the things the design left open (§18 of `DESIGN.md`).

Run everything **on the Spark, as your normal user** (not `sudo`, not root), in a terminal or over
SSH. Where a step says "note", write the number down: it goes into the report at the end.

> Never paste tokens, passwords or SSH keys into a chat. Put `HF_TOKEN` in `.env` on the Spark if
> it's ever needed, and nowhere else.

---

## 1. Get the code

The repository is private, so the Spark needs your GitHub credentials (an SSH key or a personal
access token, set up the way you normally do).

```bash
git clone https://github.com/NoSQLKnowHow/ai-image-studio.git
cd ai-image-studio                     # main has everything; no branch to switch to
```

## 2. Check Docker and the GPU

```bash
docker ps                              # must work without sudo
docker run --rm --runtime=nvidia --gpus=all nvcr.io/nvidia/cuda:13.0.1-base-ubuntu24.04 nvidia-smi
```

Good: `docker ps` lists containers (perhaps Hermes'), and `nvidia-smi` prints a table naming the
**GB10**. Memory columns may say "Not Supported"; that's normal on the Spark.

- `permission denied ... docker.sock`: add yourself to the docker group, then log out and in:
  `sudo usermod -aG docker $USER`
- `unknown or invalid runtime name: nvidia`: see Troubleshooting.

## 3. Note the memory picture before anything loads

```bash
grep MemAvailable /proc/meminfo
docker ps --format '{{.Names}}\t{{.Image}}'
docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}'
```

Note: `MemAvailable` (in kB; divide by 1048576 for GB), and which containers run, especially a
vLLM one for Hermes, and how much it uses.

## 4. Prepare the settings and folders

```bash
cp .env.example .env
sed -i "s/^STUDIO_UID=.*/STUDIO_UID=$(id -u)/; s/^STUDIO_GID=.*/STUDIO_GID=$(id -g)/" .env
mkdir -p data ~/.cache/huggingface
ls -ld data ~/.cache/huggingface ~/.cache/huggingface/hub 2>/dev/null
```

Good: every folder listed is owned by **you**. If one shows `root` (a container that ran as root
created it), take it back: `sudo chown -R $(id -u):$(id -g) ~/.cache/huggingface` (and/or `data`).
Root-run containers such as vLLM can still use it afterwards.

Optional now, or later in `.env`: `STUDIO_ALLOWED_HOSTS` (the name or address you browse to) and
`STUDIO_BIND=127.0.0.1` if the page should not be reachable from the LAN.

## 5. Build the image

```bash
docker compose build --progress=plain 2>&1 | tee build.log
grep check_image build.log
```

The first build downloads NVIDIA's PyTorch image (well over 10 GB), so give it time.

Good: the `grep` shows lines ending in `check_image: OK`, including the torch, CUDA, diffusers and
transformers versions. Note them.

If the build stops at `check_image: PROBLEM: ...`, that line names the root cause, and the lines
above it hold the installed versions and the full traceback. Send me all of it, from the first
`check_image:` line to the end of the file:

```bash
sed -n '/check_image: Python/,$p' build.log
```

## 6. Start it

```bash
docker compose up -d
docker compose ps                      # STATUS: "health: starting", then "healthy" within ~1-2 min
docker compose logs -f studio          # Ctrl+C stops following; the server keeps running
```

Good, in the log:

- `serving the web UI from /app/static`
- `AI Image Studio 0.1.0 ready (pipeline=real, data=/data)`
- `capability check passed: {... 'name': 'NVIDIA GB10', 'capability': '12.1' ...}` (this can take
  a minute: it imports PyTorch and diffusers in a separate process)

If it says `capability check failed: ...` instead, the message says why; see Troubleshooting.

## 7. Open the page

From your laptop or phone on the same network: `http://<spark-address>:8080` (on the Spark,
`hostname -I` shows its addresses).

Good: the page loads with the light or dark theme following your system. The model pill in the
top-right says **Model not loaded**; click it and the details name the GB10.

## 8. First image: small and quick

1. **Options**: Size **Custom 1024 × 1024**, **Steps 20**. Close the drawer.
2. Prompt: `a lighthouse at dusk, oil painting`. Press **Generate**.

The first run downloads the model into `~/.cache/huggingface` (tens of GB; watch
`docker compose logs -f studio`), then loads it. The pill shows **Loading model…**, then the card
shows step progress.

Note: how long the download took, how long the load took (the log says `pipeline real ready in Ns`),
and how long the image took (shown on the card).

Good: a card with the image, the prompt, the settings and the seed. **Download** gives a file named
like `generate_lighthouse-dusk-oil-painting_1024x1024_s<seed>_<time>.png`.

## 9. Measure the memory footprint (sets `STUDIO_MIN_FREE_GB`)

In a second terminal on the Spark:

```bash
watch -n1 "grep MemAvailable /proc/meminfo"
```

Then, in the page: **Options**, reset to defaults (**2048 × 2048, 40 steps**), and generate one image.

Note:

- `MemAvailable` before you click (the model not loaded): **A**
- the lowest `MemAvailable` while it generates: **B**
- the worker's own memory while loaded:
  `curl -s localhost:8080/api/status | python3 -c 'import json,sys; print(json.load(sys.stdin)["memory"])'`
  (`worker_rss_gb`)

The footprint is about **A − B**. Set `STUDIO_MIN_FREE_GB` in `.env` to that plus 10–20% headroom,
then apply it with `docker compose up -d`.

## 10. The everyday features

- **Batch:** Options, Images per click **2**: one card, two images, seeds *n* and *n*+1.
- **Reuse** on a card: prompt and options come back with the seed locked; Generate again gives the
  same image.
- **Transparent background:** turn it on and try `a red apple, product shot, transparent background`.
  The card shows the image on a checkerboard if it really has alpha; note whether it did.
- **A second click while one is running** queues it with a position (`Queued · #1`).
- On your phone: no sideways scrolling, and Options opens as a bottom sheet.

## 11. Idle unload gives the memory back

```bash
sed -i 's/^STUDIO_IDLE_TIMEOUT_MIN=.*/STUDIO_IDLE_TIMEOUT_MIN=2/' .env && docker compose up -d
```

Generate one image, then wait. The pill says **Model ready · unloads in 2 minutes**, then **Model
not loaded**.

Good: `MemAvailable` rises by about the footprint from step 9, and
`curl -s localhost:8080/api/status | python3 -m json.tool` shows the worker `"state": "unloaded"`.
The next Generate loads it again (faster this time: the files are cached).

Put the timeout back afterwards (`30` is the default, or whatever suits you) and `docker compose up -d`.

## 12. The memory check fails fast

```bash
sed -i 's/^STUDIO_MIN_FREE_GB=.*/STUDIO_MIN_FREE_GB=1000/' .env && docker compose up -d
```

Generate. Good: the card fails **immediately** with `Not enough free memory to load the model: …`
plus what to do. Put your number from step 9 back, `docker compose up -d`, and click **Retry** on
that card: it runs.

## 13. History survives the container

```bash
docker compose down && docker compose up -d
```

Good: the page (after a reload) shows all earlier runs with their images. Stopping it while an
image is generating marks that run failed with `Interrupted because the server was stopped.` (or
`Interrupted by a server restart.` if the container was killed outright); the next run works.

## 14. Cancel, Keep and automatic clean-up (new in this update)

Update first: `git pull && docker compose up -d --build` (the unchanged layers come from the cache).

One thing to know before you do: from now on a run is **deleted 30 days after it was made** (and you
get a warning on its card in the last 7 days). Yours are only days old, so nothing goes today. If you
want to be sure some are never touched, press **Keep** on them (below), or set
`STUDIO_RETENTION_DAYS=0` in `.env` to turn the clean-up off. A backup (`scripts/backup.sh`) first is
cheap insurance.

**a) Cancel a queued job.** Click Generate twice. The second card says `Queued · #1`. Click
**Cancel** on it: it turns **Canceled** at once, with no question asked, and the first job carries on.

**b) Cancel a running job, and time it.** Options: 2048 × 2048, 2 images per click. Generate, wait
until the card shows `step N of 40`, click **Cancel**, then **Stop generating**. Start a stopwatch at
the click.

Good: the card says `Stopping` and then **Canceled** within about one step (a 2K step is a few
seconds: 40 steps take around 4 minutes). If the first image was already finished the card says
`Canceled. 1 of 2 images finished and kept.` and you can open it; if not, `Canceled before any image
was finished.` The pill still says **Model ready** (the model was not unloaded), and the next
Generate starts straight away with no `Loading the model…`. **Write down the seconds from click to
Canceled**; the log has the same moment: `docker compose logs studio | grep -i cancel`.

**c) No memory is left behind.** Note `free -h` (and `nvidia-smi`) after a normal finished image, then
cancel three runs in a row and look again. Good: no steady rise.

**d) (Optional) Cancel during the first load.** `docker compose restart studio`, Generate, and Cancel
straight away. Good: the card says `Stopping… (a model that is still loading finishes loading
first)`, becomes **Canceled** once the load is done, and the model stays loaded for the next run.

**e) Keep.** Click **Keep** on a card: a **Kept** badge appears and the button shows as pressed. Reload
the page, then `docker compose down && docker compose up -d`: it is still kept.

**f) (Optional) See the warning and a real expiry.** This changes the date on **one run**, so use a
throwaway. It ages the newest run by 27 days:

```bash
RUN=$(curl -s 'localhost:8080/api/runs?limit=1' | python3 -c 'import sys, json; print(json.load(sys.stdin)["runs"][0]["id"])')
docker compose exec -T studio python - "$RUN" 27 <<'PY'
import datetime as d, sqlite3, sys
t = (d.datetime.now(d.timezone.utc) - d.timedelta(days=float(sys.argv[2]))).isoformat(timespec="milliseconds").replace("+00:00", "Z")
c = sqlite3.connect("/data/studio.sqlite", timeout=10)
c.execute("UPDATE runs SET created_at=? WHERE id=?", (t, sys.argv[1]))
c.commit()
PY
```

Reload the page. Good: that card says `Will be deleted in 2 days. Press Keep to save it.` Press
**Keep** and the warning goes; press it again and the warning is back. Now run the same command with
`40` instead of `27` on a run that is **not** kept, then `docker compose restart studio`. Good: a few
seconds after start-up the card is gone, and so are its files: `ls data/images | grep "$RUN"` prints
nothing.

## 15. Editing with several images, through the API (new in 1.2)

The web page can't do this yet (it comes in the next update), but the server can, and this is the first
time the **real model** is asked to edit, so it is worth trying now. You need two or three pictures: say a
photo of an object or pet, and a scene. Any JPEG, PNG or WebP.

**Before you update:** this version changes the database layout. The first start upgrades it and keeps a
copy of the old one as `data/studio.sqlite.before-schema-2`. That copy is also the way back to version 1.1
(an older studio refuses a database a newer one has touched, with a message saying so). To go back: check
out the 1.1 code and rebuild, then `docker compose stop`, delete `data/studio.sqlite-wal` and
`data/studio.sqlite-shm` if they exist, copy `data/studio.sqlite.before-schema-2` over
`data/studio.sqlite`, and `docker compose up -d`. The history is then as it was at the upgrade: anything
made since is gone from the page (its image files stay in `data/images`). I tried exactly this with a
database written by the real 1.1 code. A backup (`scripts/backup.sh`) first is still the better safety net.

```bash
git pull && docker compose up -d --build
docker compose logs studio | grep -i "schema"        # "upgrading the database from schema 1 to 2 ..."
curl -s localhost:8080/api/health                    # "version":"1.2"
```

**a) A first edit** (the first run after a quiet spell loads the model, which takes a while):

```bash
scripts/edit_via_api.sh "put the object from image 1 into the scene from image 2" thing.jpg scene.jpg
```

It prints the upload sizes, the progress, and where it saved the result. **Write down:** the seconds from
`running` to `done` (excluding the model load if it had to load), the result's size (`file edit-*.png`),
and whether the result obeys the numbers. Whether a prompt can say "image 1" and "image 2" is not
confirmed by Qwen's documentation, so also try the same edit **without** numbers ("put the object from the
first picture into the second") and say which worked better.

**b) Resolution.** `scripts/edit_via_api.sh --resolution 2048 ...` is about four times the pixels for every
image. Write down the time, and `free -h` while it runs. If it runs out of memory the card says so and
suggests 1K or fewer images; that is a finding too, not a failure of the test.

**c) More images, and the shape.** Try three or four images at 1K. Then `--shape-from 1` (the result follows
image 1's shape instead of the last image's) and `--size 1024x768` (an explicit size). Good: the result
size changes as described, and the images are used in the order you gave them.

**d) Transparency.** With a PNG that has a transparent background as one input, and `--transparent`, ask
for something like "put the subject of image 1 on a transparent background". Open the result in an image
viewer that shows transparency (or the web page, which shows a checkerboard). **Write down** whether the
result has real transparency, and whether it also did without `--transparent`.

**e) The page shows these runs.** They appear in the history with an **Edit** badge (the source
thumbnails come with the page's editing support), and **Cancel**, **Keep** and **Delete** work on them.
Deleting one removes its input pictures too: `ls data/inputs` has one folder per edit run still in the
history, plus `staged` for uploads that no run has used yet (those are removed after 24 hours).

**f) The limits.** `scripts/edit_via_api.sh` with five images should be refused with `at most 4 images` (set
`STUDIO_MAX_INPUT_IMAGES` in `.env` and `docker compose up -d` to change it, up to 10). **If you have time,
raise it and try more images at 1K while watching the memory: that number sets the default cap.**

## 16. Scale, Draft and thumbnails (new in 1.3)

Update first: `git pull && docker compose up -d --build`; the title should read **v1.3** (reload with
Ctrl+Shift+R). There is no database change this time.

**a) The scale picker, and what it saves.** Keep one prompt and **Lock seed** on (Options). Generate the
same prompt at **100%**, **50%** and **25%** and **write down the three times**. Good: 50% takes roughly a
quarter of the time of 100% (a quarter of the pixels) and 25% roughly a sixteenth, plus the same model
load if it had to load. Look at the three pictures side by side: they will **not** be the same picture
with different sizes, even with the seed locked. That is expected (§22.1), and it is worth knowing how
different they are.

**b) Draft.** Type a prompt and click **Draft** (or Ctrl+Shift+Enter). Good: a card with a **Draft**
badge, one image, 512 px on the long side, 12 steps. **Write down** the time, and whether the picture is
good enough to tell whether the prompt works. If 12 steps looks too rough or too slow, say so: both
numbers are settings (`STUDIO_DRAFT_SIZE`, `STUDIO_DRAFT_STEPS` in `.env`, then `docker compose up -d`).

**c) A draft jumps the queue.** Click Generate twice (two full-size runs), then **Draft**. Good: the draft
card says `Queued · #1`, the second full run `Queued · #2`, and the draft finishes before it starts.
The run already in progress is never interrupted.

**d) Reuse on a draft.** Click **Reuse** on the draft's card. Good: the prompt comes back, and your own size,
steps and seed stay as they were, so **Generate** makes the full-size image.

**e) Thumbnails.** On a card with one image, click **Thumbnail**; for several images, open the viewer and
use **Thumbnail** there. Good: you get a `..._thumb.webp` file, 512 px on its long side. With a transparent
image, check that the thumbnail kept its transparency.

---

## 17. Regenerate larger (new in 1.4)

Update first: `git pull && docker compose up -d --build`; the title should read **v1.4** (reload with
Ctrl+Shift+R). There is no database change. Only runs made with 1.4 can have the button.

**a) From a smaller scale.** Turn **Lock seed** on (Options) and set the prompt bar's **Scale** to **50%**.
Generate. Good: when the card says Done it has **Regenerate larger** (hover it: it names the size and steps,
for example "Regenerate at 2048×2048, 40 steps") and a greyed **Upscale** that says it arrives with editing.
Click **Regenerate larger**. Good: a toast says it is queued at that size, and a second card appears at the
full size with the **same seed** and prompt, and no button of its own. **Write down both times.** Then look
at the two pictures: with the same seed they are **not** expected to be the same picture (§23.1). **Write
down how different they are** (same subject? same layout? nothing alike?), because that decides how much
the Upscale button below matters.

**b) From a draft.** Select several images in Options (say 3), then click **Draft**. When it is Done, click
**Regenerate larger** on its card. Good: one full-size image at the size and steps you had selected (not the
draft's 12), using the seed the draft picked (it is on the draft's card and on the new one).

**c) Where there is no button.** Good: none on the full-size card from (a), none on a failed or canceled run
(those have Retry), none on runs made before the update, and none when Scale is 100% (a normal run).

**d) Through the API** (optional). A run can say what size it stands in for. Good: this is accepted, and the
run's JSON has an `options.full`; the second command is refused with a 422 because the "full" size is not
larger:

```sh
curl -s -X POST localhost:8080/api/runs -H 'Content-Type: application/json' -H 'X-Studio-Client: 1' \
  -d '{"prompt": "a lighthouse", "options": {"width": 512, "height": 512, "steps": 20, "full": {"width": 1024, "height": 1024, "steps": 40}}}'
curl -s -X POST localhost:8080/api/runs -H 'Content-Type: application/json' -H 'X-Studio-Client: 1' \
  -d '{"prompt": "a lighthouse", "options": {"width": 1024, "height": 1024, "steps": 20, "full": {"width": 512, "height": 512, "steps": 40}}}'
```

---

## Troubleshooting

| You see | What to do |
|---|---|
| `unknown or invalid runtime name: nvidia` | Register NVIDIA's runtime with Docker: `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`. Or delete the `runtime: nvidia` line from `compose.yaml`: the GPU reservation below it is the `--gpus all` equivalent and may be enough. |
| `could not select device driver "nvidia"` | The NVIDIA Container Toolkit isn't set up for Docker. It ships with DGX OS; check step 2. |
| `The data directory /data is not writable by this process (uid …)` | The message ends with the exact `sudo chown` to run on `./data`. |
| Model pill: **Model unavailable**, `No CUDA GPU is visible` | The container didn't get the GPU. Step 2 must work first. |
| `no kernels for the Spark's GPU (GB10)` | Try a newer NVIDIA PyTorch release: set `NGC_TAG` in `.env` (for example `25.11-py3`), then `docker compose build`. |
| `diffusers is installed, but QwenImage21Pipeline is only a placeholder` | transformers didn't import. `docker compose exec studio python -c 'import transformers'` shows why; send me the output. |
| Download fails with 401/403 or "gated" | Accept the model's terms on Hugging Face if it asks, then set `HF_TOKEN` in `.env` and `docker compose up -d`. |
| `Not enough free memory to load the model` | Hermes' vLLM probably holds the memory: lower its `--gpu-memory-utilization` or stop it, then flush the page cache: `sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'`. Then **Retry**. |
| Out of memory while loading or generating | Same as above; also try a smaller size or fewer images per click. |
| `port is already allocated` | Something else uses 8080: set `STUDIO_PORT` in `.env`. |
| The page loads but never updates live | Check `docker compose logs studio`; the connection banner at the top says if the live stream is down. |

To update later: `git pull && docker compose up -d --build`.

## What to send back

Paste these into the chat (no tokens or passwords; check before pasting):

1. The `check_image:` lines from step 5.
2. The first ~30 lines of `docker compose logs studio` after step 6.
3. The timings from step 8 and the numbers **A**, **B** from step 9.
4. Anything that didn't match "Good", with the message the page or the log showed.
5. Whether transparent output really had transparency (step 10).
6. The numbers from step 14: seconds from clicking **Stop generating** to the card saying **Canceled**, and the memory before and after.
7. The numbers and observations from step 15 (edits through the API).
8. The numbers from step 16: the time of a draft and of a run at each scale, and how the draft looks.
9. From step 17: the two times, and how different the small and the regenerated pictures are.
