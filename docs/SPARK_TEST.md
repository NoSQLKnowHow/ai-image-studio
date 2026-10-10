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

This is the same thing as section 18, through the API with `curl` instead of the page. The page has had an
Edit mode since 1.6, so section 18 is the one to do; this one stays for checking the API from a terminal. It
is the first time the **real model** is asked to edit, so it is worth trying. You need two or three pictures:
say a photo of an object or pet, and a scene. Any JPEG, PNG or WebP.

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

Update first: `git pull && docker compose up -d --build`; the title should read **v1.5** (reload with
Ctrl+Shift+R). There is no database change. Only runs made with 1.4 or later can have the button.

**a) From a smaller scale.** Turn **Lock seed** on (Options) and set the prompt bar's **Scale** to **50%**.
Generate. Good: when the card says Done it has **Regenerate larger** (hover it: it names the size and steps,
for example "Regenerate at 2048×2048, 40 steps") and a greyed **Upscale** that says it is not built yet.
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

**e) In the viewer (new in 1.5).** Generate three images at 50% (Lock seed on, **3 images** in Options). When
it is Done, click the second image to open the viewer. Good: the top bar has **Regenerate larger** (same
tooltip as the card). Click it. Good: a note inside the viewer says "Queued this image at …" (no toast), the
viewer stays open, and a **new card at the top of the history makes one image** at the full size, with the
second image's seed (the viewer's title shows it, one more than the run's first). **Write down** that it is
one image and not three, and, as in (a), how it compares with the small one.

---

## 18. Editing on the page, and the Spark test for edits (new in 1.6; this is milestone M5c)

Update first: `git pull && docker compose up -d --build`; the title should read **v1.6** (reload with
Ctrl+Shift+R). There is no database change. The **Edit** switch should be enabled; if it is greyed out,
hover it for the reason (the pipeline reports it cannot edit; `docker compose logs studio` says why).

You need a few pictures: a photo of an object or pet, a scene, a PNG with a transparent background, and (for
part f) a black-and-white mask the size of one of the photos (white where a change should go). Keep a
notebook: **times and memory** are what this section is for. Watch memory with `docker stats` or `free -h`
in a second terminal while an edit runs. The first edit after a quiet spell includes loading the model, so do
one throw-away edit first and time the ones after it.

**a) One picture.** Click **Edit**, add one photo (the **Add images** tile), type "Make it look like a
watercolour painting." and click **Generate**. Good: a card with an **Edit** badge, a numbered source
thumbnail (1) above the result, and a meta line like "1 image · 1024×1024 · 1K · 40 steps". **Write down the
time.** Is the subject still recognisably the same?

**b) Several pictures, and "image N".** Add the object and the scene. Click the **1** badge on the object:
"image 1" appears in the prompt where the cursor was. Finish it: "Put the object from image 1 into the scene
from image 2." and send. **Write down whether the result follows the numbers.** (Nobody knows yet: §21.2.)
Then use the arrows to swap the two pictures and send the same prompt again: does "image 1" now mean the
other one?

**c) The shape.** Leave Size on **Auto** and, with two pictures of different shapes, use **Result follows
image** to pick each one in turn. Good: the result takes that picture's shape, about 1 megapixel at 1K.

**d) Resolution, cost and memory (the important one).** In Options choose **Resolution**; the line under it
gives the cost in units (images × (resolution ÷ 1024)²) and a warning above `STUDIO_EDIT_WARN_UNITS` (8).
Make a table of **time and peak memory** for: 1 picture at 1K and 2K; 2 pictures at 1K and 2K; 4 pictures at
1K and, if memory allows, 2K. Good: each completes, or fails with a card that says what to do (use 1K or fewer
pictures) while the server stays up. **Then tell me:** where it really starts to struggle (the number that
should replace 8: set `STUDIO_EDIT_WARN_UNITS` in `.env` and `docker compose up -d`), and whether 4 pictures is
the right default (`STUDIO_MAX_INPUT_IMAGES`, up to 10).

**e) Transparency.** Add the transparent PNG: Options shows a hint that its transparency is kept. Edit it with
Transparent off, then on. Then try the **Extract the subject** starter on a photo of an object. **Write down**
whether the background comes back transparent (the viewer shows a checkerboard) and whether the Transparent
switch was needed when the input already had transparency (§21.2 [unconfirmed]).

**f) A mask** (so local edits can be designed, §21.5). Add the photo as image 1 and the black-and-white mask as
image 2, and write the prompt "Change the area marked in white in image 2 to ...". **Write down which colour the model
treats as "edit here"**: try with white where you want the change, then a mask with the colours swapped; and
whether a mask the same size as the photo matters (try one of a different size).

**g) Edit this.** On a finished single-image card click **Edit this**: the picture is added as image 1 and
the page switches to Edit. For a card with several images, open one in the viewer (it has **Edit this** for any
image, and for an edit's source pictures too).

**h) Reuse and Retry.** **Reuse** an edit's card: the prompt, the options and all its pictures come back, in
order, with the seed locked. If you can make one fail (a picture of 4 at 2K, say), **Retry** sends the same
pictures again.

**i) A phone.** Open the page on a phone: the tray is three pictures across, **Add images** opens the photo
picker, and nothing scrolls sideways.

---

## 19. Load model and Unload model (new in 1.7)

Update first: `git pull && docker compose up -d --build`; the title should read **v1.7** (reload with
Ctrl+Shift+R). There is no database change. You need a second terminal for `free -h` (or `docker stats`), and
the idle timeout at its normal value (30), not the 2 minutes of step 11.

**a) Load without a run.** Start from **Model not loaded** (restart the container if it isn't:
`docker compose restart`). Write down `free -h`. Click **Load model** beside the pill. Good: the button goes
away, the pill says **Loading model…**, focus is on the pill, and **no run card appears**. **Write down how long
until the pill says Model ready** (this is the real load time, first time and then with the files cached, if you
unload and load again) and `free -h` once it is ready: the difference is what Load takes. Then type a prompt and
generate: it should start at once, with no loading pause.

**b) The countdown starts when the load finishes.** Right after the pill says Model ready it should say about
**unloads in 30 minutes**, not 29 or 28 (the load itself does not eat into it).

**c) A run sent while it loads.** Unload, click Load model, and send a Generate straight away. Good: the card
waits, then runs when the load is done, and the model is loaded **once** (one worker; `docker compose logs studio`
shows one "pipeline ... ready" line for that pair).

**d) Unload.** With the model ready, click **Unload model**. Good: the pill says Model not loaded **at once** and
`free -h` shows the memory coming back within a few seconds. **Write down how much comes back** and compare it with
the figure from step 9. While a run is going the button is not shown (the pill says Generating).

**e) The memory check.** Make memory scarce (the way step 12 does) and click Load model. Good: nothing starts, a
message appears on screen, the pill says **Model problem** with the reason (open it for the hint), and **Load
model** is still there to try again once memory is free.

**f) The idle timeout still works.** Load the model with the button and leave it alone. After the idle timeout
(set it to 2 for this, then put it back) the pill goes back to Model not loaded and the memory is returned.

**g) A phone.** On a phone the button is an icon, the pill says one word (Unloaded, Loading…, Ready), and the
header does not scroll sideways.

---

## 20. Music: the model, from a terminal and through the API (new in 1.8)

This section is the first time the **real MiniMax-Music3 model** runs anywhere: everything before it was checked against
a stand-in and, for the pipeline's own code, against a tiny random-weight copy on a CPU. Expect surprises, and write
down what you see. The Music tab (1.9) is checked at the end of this section; the model, its speed and its memory come first, with
`scripts/minimax_music.py`, which uses the very same pipeline class the studio uses.

Update first: `git pull && docker compose up -d --build`; the title should read **v1.8** or later. Look at the build's check
lines: `docker compose build 2>&1 | grep check_image` should include **`music worker: diffusers 0.40.0 from
/opt/music-libs`** and end with `check_image: OK`. If it says PROBLEM, that message is the first thing to send back.
**Free disk:** the music model is about 29 GB (up to 57 GB if the whole repository is fetched). Check
`df -h ~/.cache/huggingface` (or your `HF_CACHE_DIR`) has 60 GB.

**a) Download it ahead of time (optional, about 29 GB).** Press **Load model** later and the download happens then
(the pill says *Loading…* for its whole length); or do it now, only the parts the pipeline loads:

```bash
docker compose exec studio hf download MiniMaxAI/MiniMax-Music3 --include "modular_model_index.json" "config.json" \
  "condition_encoder/*" "language_model/*" "rvq_depth_decoder/*" "scheduler/*" "tokenizer/*" "transformer/*" "vocoder/*"
```

**Write down:** how long it took and `du -sh ~/.cache/huggingface/hub/models--MiniMaxAI--MiniMax-Music3`. (If the
first load later downloads *more*, the pipeline fetches the whole repository: tell me, and how big.)

**b) See what would be sent, loading nothing.**

```bash
docker compose exec studio python /app/scripts/minimax_music.py --genre "acoustic pop" --bpm 96 --key "C major" \
  --mood "warm and intimate, building gently" --instruments "fingerpicked guitar and soft piano" --dry-run
```

Good: the description in the three-section layout, the lyrics `[Instrumental]`, and no model loaded.

**c) A short real track (the important one).** Open a second terminal with `watch -n 2 free -h` (or `docker stats`):

```bash
docker compose exec studio python /app/scripts/minimax_music.py --genre "ambient" --mood "slow, spacious" \
  --instruments "soft pads and a piano" --duration 15 --seed 7 --out /data/try15.wav
```

The file appears at `./data/try15.wav` on the Spark. Good: it prints *loaded in N s*, then *composing*, *rendering*,
*finishing*, then *made N s of music in N s* and the licence reminder. **Write down:** the load time, the time to make 15
seconds (so the seconds of work per second of music), and the **lowest "available" memory** you saw in the other
terminal. The design's guess is at least 1.5 s of work per second of music and about 24 GB loaded (§26.7); say how far off it is.
Then **listen**: does it sound like music, with no singing? **Is the length 15 s?** (It is only an upper bound.)

**d) Longer.** The same with `--duration 60`, and if you are patient `--duration 180`. **Write down** the times: is the
cost per second steady? How long would the 5-minute maximum take? The page shows no estimate until you tell me one.

**e) Instrumental, and what helps.** Listen for any vocals in (c) and (d). Then run the same seed with your own
description text that *leaves out* "Instrumental, no vocals." (`--prompt-file`, with the lyrics still `[Instrumental]`),
and once more with `--lyrics "[Verse]\nla la la\n[Chorus]\nla la la"` to hear what a vocal track is. **Write down**
whether the `[Instrumental]` tag alone is enough, whether the extra sentence changes anything, and whether the same seed
with the same text gives the same track twice (run (c) again and compare the files with `cmp`).

**f) Cancel.** Start a 60-second track and press **Ctrl+C** while it says *composing*, then while it says *rendering*.
Good: it stops within a second or two and exits (code 130), and the next run works. **Write down** how fast it stopped.

**g) No more downloading.** Run (c) again with `--hub offline`: it must work with no network use (it only reads the
cache). Then the same through the studio, which loads from the cache by default (`STUDIO_LOCAL_FILES_ONLY=auto`):
unplug the Spark's network (or block Hugging Face) and press the studio's load below. Good: it loads. If you
can, watch `docker compose logs studio` for anything that looks like a download.

**h) Through the studio's API (the Music tab does exactly this).**

```bash
curl -s -X POST localhost:8080/api/model/load -H 'X-Studio-Client: 1' -H 'Content-Type: application/json' -d '{"model":"music"}'
curl -s localhost:8080/api/status | python3 -m json.tool | grep -E '"state"|"model"|"detail"'   # until state is "ready", model "music"
curl -s -X POST localhost:8080/api/runs -H 'X-Studio-Client: 1' -H 'Content-Type: application/json' \
  -d '{"mode":"music","prompt":"Global Metadata\nBasic Attributes: ambient.\nInstrumental, no vocals.","options":{"duration":15,"tracks":1}}'
# then poll   curl -s localhost:8080/api/runs/<id>   until "status" is "done", and fetch the track:
curl -s -o /tmp/api.wav 'localhost:8080/api/audio/<track id>?download=1' && strings /tmp/api.wav | grep machine-generated
```

Good: the music model loads, the image model is **not** loaded at the same time (`free -h` shows only one), the run
finishes with a track, and the WAV carries the note that it is machine-generated. Then make a picture (Generate): the
music model is unloaded first (`"model": "image"` in the status afterwards) and the memory comes back. **Write down** how
long the switch takes in each direction.

**i) The memory check.** With the music model's check at 40 GB (the default), make memory scarce as in step 12 and press
Load for the music model: nothing starts, the message says *music model*, and nothing is left loaded.

**j) The page (new in 1.9).** Update (`git pull && docker compose up -d --build`; the title should read **v1.9**) and
open the page. **Good:** two tabs, *Images* and *Music*; the arrow keys switch between them and the choice is still
there after a reload; the Music tab has the boxes, the description below them (try changing a box, then the
description by hand, then *Rebuild from fields*), *Add lyrics* (off), the length shortcuts, and under the form the
note naming MiniMax-Music3 and its licence. If the tab says *Music isn't available*, the reason and hint it shows are
the first thing to send back.

**k) A track from the page.** On the Music tab: genre *ambient*, mood *slow, spacious*, instruments *soft pads and a
piano*, length **15 s**, then **Make music**. **Good:** the pill says *Loading music model…* (the first load is long), then
the card shows **Composing** and **Rendering** bars in turn, then a player. **Write down** the same numbers as in (c),
now through the page, and whether the card's *Track · 0:15* is the length you got. Play it **on the Spark's browser
and on your phone** (the page is on your network): it should play and seek. Press *Download WAV* and check the name
(`music_ambient_..._15s_s<seed>_<date>.wav`).

**l) Cancel from the page.** Make a 60 s track and press **Cancel** while the *Composing* bar moves, then (another
track) while *Rendering* does. **Good:** a dialog asks first; *Stop making music* turns the card to *Canceled* within a
second or two, the model is **not** unloaded (the pill still says *Music model ready*), and the next track starts at once.
**Write down** how long the stop took in each phase.

**m) The buttons on each tab.** With the music model loaded: the Music tab offers **Unload model**, the Images tab
offers **Load model**, whose tooltip says the music model is unloaded first. Press it. **Good:** the pill goes
*Loading image model…* then *Image model ready*, and the memory of the music model comes back (`free -h`). Then the Music
tab's **Load music model** swaps them back. **Write down** the time of each swap and the free memory before and after.

**n) A phone.** Open the page on your phone. **Good:** the tabs, the form, the length shortcuts and a finished track
fit the screen with no sideways scrolling, and the player and *Download WAV* are reachable. A screenshot of anything
that does not is the most useful thing you can send.

---

## 21. Make 4K, and the upscaler probe (new in 1.10)

Update first: `git pull && docker compose up -d --build`; the title should read **v1.10** (reload with Ctrl+Shift+R).
There is no database change. You need a 16:9 picture: Options, size **16:9** (2752×1536), generate one at the usual
steps. A 4K copy is a file next to the picture, so `ls -l data/images/<run>/` shows it.

**a) Make 4K.** On the card of a 16:9 picture press **Make 4K**. Good: the button says *Making 4K…* for a few
seconds, a note says *The 4K copy is ready: 3840×2160, N MB*, and the button becomes **Download 4K**. **Write down how
many seconds it took** (the Spark's CPU, not mine) **and the file size**. `ls -l` shows `0.png` and `0-4k.png`.

**b) Look at it.** Download the 4K copy and the original. Open the 4K copy at 100% and the original at 140% in a
viewer you trust. **Good:** the same picture, trimmed by a sliver at the sides (about 21 pixels of the 2752 wide, taken
equally from both sides), with nothing stretched (circles stay round). It will be **softer than a real 4K picture**:
no detail was added. **Write down what you think**: fine for how you will use it, or soft enough that an upscaler
(step i) is worth building. Faces, text and fine textures are where it shows.

**c) Other shapes, and where it is not offered.** Make a **1:1** picture (the default 2048×2048) and press Make 4K: the
tooltip says *3840×3840* and **Good** is a 3840×3840 file with nothing cut off (it is big: about 15 MP; **write down the
seconds and the file size**). A 4:3 or 3:2 picture works too (3840 wide, in proportion). A **Draft**, or any picture
that would need more than a doubling (anything under 1920×1080 or 1920×1920), has no Make 4K, and neither has one
that is already 4K. A run with several pictures has no button on its card; open one in the viewer and it is there,
for that picture only. **Pictures you made before updating** have the button too, if they qualify (open an old one).

**c2) An edit's source images.** Open an edit run's card and click one of its source thumbnails: the viewer has **Make 4K**
for it (if it is big enough), and the copy is for the source, not the result. Use the arrow keys to see that the result has
its own button.

**c3) A picture from your computer.** Press **Upscale a picture…** above your runs and choose a PNG, a JPEG and a WebP
in turn (try a **phone photo**: it should come back upright, whatever way it was held). **Good:** a download starts at once,
named `upscale_<file name>_<size>_<time>.png`, and no card appears. Try a tiny picture and a 4K one: each is refused with
the reason, in words. Try a PNG with transparency: the copy is still transparent. **Write down** the time for a
photo of about 12 MP (the server's CPU does the work).

**d) While a picture is being made.** Start a long run, and press Make 4K on an older picture while it runs.
**Good:** it works at once. **Write down** whether the running job slowed down (the steps per second in the logs).

**e) It stays.** Reload the page: **Download 4K** is still there. `docker compose restart`: still there. Delete the
run: `ls data/images/` no longer has its folder, and the 4K file went with it.

**f) Transparency.** Make a 16:9 picture with **Transparent** on and press Make 4K. **Good:** the 4K copy is still
transparent (open it on a coloured background).

**g) A phone.** In the viewer on your phone the buttons wrap onto a second row; nothing runs off the screen.

**h) The numbers.** The model cannot make 3840×2160 itself (2160 is not a multiple of 32), so there is nothing to
compare this against. If you want to try **making a bigger picture directly** (route C in `docs/DESIGN.md` §27.6), tell me:
that needs a setting for the pixel limit, which I have not built.

**i) The upscaler probe (route B, optional but it decides what comes next).** It checks that an ESRGAN-class model
runs on the Spark's GPU, and it makes two 4K files to compare. Nothing in the studio uses it.

1. On the Spark, download the model into the cache folder the container sees as `/models`:
   `mkdir -p ~/.cache/huggingface/upscalers && curl -L -o ~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth`.
   It is 67,061,725 bytes. When I downloaded it (2026-10-09) its SHA-256 was `49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb` (`sha256sum` it); the project does not publish a checksum that I found, so
   a different one means only that the file has changed. The code is BSD-3-Clause; read the model's terms yourself.
2. `docker compose cp scripts/upscale_probe.py studio:/tmp/upscale_probe.py`. (From version 1.11 `spandrel` is in the image; in 1.10 you also had to run `docker compose exec studio pip install --user spandrel`.)
3. A quick check: `docker compose exec studio python /tmp/upscale_probe.py --model /models/upscalers/RealESRGAN_x2plus.pth --crop 512x288`. **Good:** it prints the GPU, `ESRGAN, x2`, a tile time and `PROBE OK`.
4. The real one, on a 16:9 picture the studio made: `... --image /data/images/<run>/0.png --out /data/upscale-probe`.
   Then try `--dtype bf16`. The results are in `data/upscale-probe/`: put `4k-lanczos.png` (what Make 4K makes) and
   `4k-from-model.png` side by side at 100%.

**Send back:** the whole output of the probe, including the last line and the **GPU** line (it says whether it used the
GPU and which compute capability), the tile times, the peak GPU memory and the memory figures; whether the same run
with `--dtype bf16` was faster and looked the same; and above all **which of the two 4K files looks better to you and
where** (faces, text, foliage). If `pip install` or the run fails, paste the error: that is the answer I am looking for.

---

## 22. Enlarge: the same picture, bigger and sharper (new in 1.11)

Update first: `git pull && docker compose up -d --build`; the title should read **v1.12** (reload with Ctrl+Shift+R). The build log
has a line `check_image: Enlarge: spandrel 0.4.x`; if it says spandrel cannot be imported, send me that line (everything else still
works, and Enlarge says why it is off). There is no database change. Enlarge uses **a model file you download once** and a **separate
short-lived process**, so the image model does not have to be loaded. If it is generating, Enlarge **waits for the picture to finish** and then runs (g): the two do not fit on the GPU together.

**a) The model file.** Before you download anything: generate any 16:9 picture (Options, size **16:9**, 2752×1536). Its card has
**Make 4K** and **Enlarge**, and Enlarge is **dimmed**. Hover it: the tooltip says the model file is not there and says what to do. Press
it: the same words appear as a message. **Good:** nothing is made, and Make 4K still works. Then, on the Spark:
`mkdir -p ~/.cache/huggingface/upscalers && curl -L -o ~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth`
(67,061,725 bytes; the code is BSD-3-Clause; the model has its own terms, which are yours to read), reload the page: **Enlarge is no longer dimmed**.

**b) Enlarge a picture.** Press **Enlarge** on the 16:9 card. **Good:** the button says *Enlarging…*, a note then says *Enlarged to 3840×2160, N MB.
Use Download 4K.*, **Enlarge and Make 4K are gone** and **Download 4K** is there. **Write down how long it took and the file size.** The log
says it too: `docker compose logs studio | grep ENLARGED` shows a line like `ENLARGED 3840x2160 in 12.3 s on cuda with ESRGAN ×2 (RealESRGAN_x2plus.pth)`:
**send me that line**, especially the *device* (it should say `cuda`; `cpu` means the GPU was not used, which is minutes per picture). `ls -l data/images/<run>/`
shows `0.png` and `0-4k-enlarged.png`.

**c) Look at it, against the plain resize.** On a second picture of the same size press **Make 4K**, **Download 4K** and save it as `plain.png`; then press **Enlarge**
(it replaces the plain copy; that is expected), download it again as `enlarged.png`. Open both at 100%. **Write down what you think, and where**
(faces, hair, text, foliage, skies, flat colour): is Enlarge **sharper**? Does it look **natural**? Things to look for: skin that is too smooth or plastic; grain or texture
on flat colour and gradients (I saw some of that on a synthetic gradient with the real model); faint lines at regular spacing across the picture (seams where tiles meet; they would be
every 512 source pixels, so about every 700 pixels of the 4K copy for a 2752-wide picture); small text that got worse. This is the main thing I need from you.

**d) A 50% picture.** Scale **50%** (1376×768) and generate. **Make 4K is not offered** (it would be a 2.8× enlargement); **Enlarge is**, and its tooltip says 3840×2160.
Press it. **Write down the time and how it looks** next to the picture you would get from regenerating at full size (it is the same picture, which is the point).

**e) Two passes.** A custom size of **960×544**: Enlarge runs the model twice (×4) and then reduces. **Write down the time**, and whether it looks worse than a one-pass picture (a second pass can over-smooth).

**f) A square.** The default 2048×2048: Enlarge makes **3840×3840** (the model's 4096×4096 is reduced). **Write down the time**, and the memory while it runs (`free -g` in another terminal).

**g) While a picture is being made.** (This is the check that failed in the first trial, with *CUDA error: out of memory*.) Start a long run (several pictures, many steps),
and press Enlarge on an older picture while it runs. Queue a second run behind it. **Good:** the button says **Waiting…** and is dimmed (that is not an error; hover it for why); the running
picture is not stopped or slowed; **when that run finishes** the button says *Enlarging…* **before the second run starts**, and then the note *Enlarged to 3840×2160…* appears and the second run goes on.
Open the page in a second browser tab while it waits: its button says *Waiting…* too, and shows Download 4K when it is done. **Write down** how long it waited, how long it then took, and whether the log's
`ENLARGED …` line says `cuda`. If you get *Not enough memory for the upscaler right now…* instead, something other than the studio is holding the GPU (the LLM server?): send me `nvidia-smi` and `free -g` taken at that moment.
Also try **Load model** and, while it is loading, Enlarge: it should wait for the load to finish.

**h) When it goes wrong.** (1) Rename the model file, reload: Enlarge is dimmed again, with the reason. (2) Put a file that is not a model in its place (`echo x > ~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth`):
pressing Enlarge says the model could not be loaded, naming the file. Restore the real file afterwards. (3) Press Enlarge twice quickly: one request, one copy.

**i) The CPU, for the record.** Set `STUDIO_UPSCALER_DEVICE=cpu` in `.env`, `docker compose up -d`, and Enlarge one picture. **Write down the time.** Set it back to `auto`.

**j) A phone.** On your phone the card's buttons and the viewer's bar, with **Enlarge** in them, wrap onto more rows; nothing runs off the screen.

**Send back:** the `ENLARGED …` lines; the times and file sizes from (b), (d), (e), (f), (i); your verdict from (c), (d) and (e): **where Enlarge looks better than the plain resize and where it looks worse**;
the memory and slowdown figures from (f) and (g); and anything that did not match "Good". If Enlarge looks worse than Make 4K on your pictures, say so plainly: that decides whether
the model, the settings (`--dtype bf16`, the tile overlap) or the whole idea needs another look, and whether Qwen redrawing (`docs/DESIGN.md` §28.5) is worth trying.

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

## 23. The Kept view, and Enlarge waiting its turn (new in 1.12)

Update first: `git pull && docker compose up -d --build` (on the branch or `main` that carries 1.12); the title should read **v1.12** (reload with Ctrl+Shift+R).
There is no database change. The Enlarge part is §22(g) above; this section is the Kept view, which needs no GPU but is worth looking at with your own history.

**a) The filter bar.** Above your runs: **Show**, **All**, **Kept N**. Press **Keep** on two cards. **Good:** the number beside **Kept** goes up by itself;
choose **Kept** and only those two cards remain; choose **All** and everything is back; reload with **Kept** chosen and it is still chosen. The arrow keys move between the two options.

**b) Both tabs.** With **Kept** chosen on **Images**, open **Music**: the bar there says **Kept** too, with that tab's own count, and shows only kept tracks.

**c) Older kept runs.** If you have more than twenty runs, keep an old one: in **Kept**, it is found without scrolling through everything (*Load older runs* appears only when
there are more than twenty kept). Back in **All** it is not shown out of order.

**d) Generating while Kept is chosen.** Press **Generate**. **Good:** your job appears **at the top** with the note *Shown while it works. It stays in this view only if you Keep it.*;
when it finishes (not kept) it leaves and a toast says *"…" is done. It is not kept, so it is not in this view.* with **Show all**. Do it again and press **Keep** on the working card:
the note goes away and the card stays when the job is done.

**e) Stopping to keep.** In **Kept**, press **Keep** on a kept card. **Good:** the card goes; a toast says *No longer kept: "…". It will be deleted around <date>, in N days, unless you Keep it again.*
with **Undo**; **Undo** brings the card back in its place. A run older than your retention (30 days) says it will go *at the next daily clean-up*. With the keyboard, focus lands on the filter bar.

**f) A phone.** The bar fits, and the toast with **Undo** can be pressed.

**Send back:** anything that did not match "Good", and whether the date in (e) matched the date you expected from the run's age.

## 24. The bin (new in 1.13)

Update first: `git pull && docker compose up -d --build`; the title should read **v1.13** (reload with Ctrl+Shift+R). **This release upgrades the database**
(schema 4): the first start makes a copy beside it, so check, before anything else: `ls -l data/studio.sqlite*` shows `studio.sqlite.before-schema-4`, and your
runs are all there. (If anything is missing, stop and send me the log; the copy is the way back, with an older studio.)

**a) The first clean-up.** Runs older than 30 days that you did not Keep used to be deleted at the first start and then daily; now they are **moved to Deleted**.
Open **Deleted**: if you had such runs they are there, each saying when it will be deleted for good (30 days from today). **Good:** nothing you cared about is gone.

**b) Delete, Undo.** Press **Delete** on a recent card. **Good:** the question says it moves to Deleted and stays 30 days; the card goes; a toast says so with **Undo**; **Undo** brings
the card back where it was. Do it again and this time let the toast go: the run is in **Deleted** (the count beside it went up), with its pictures still opening.

**c) Restore.** In **Deleted** press **Restore** on a run you had kept: the toast says *It is still kept*. On one you had not: *It has a fresh 30 days*. Both are back in All.

**d) Delete forever, Empty bin.** **Delete forever** asks first and then removes the run and its files (`ls data/images/<run id>` is gone). **Empty bin** (top right of the bar) asks,
saying how many on Images and on Music, and then empties both. **Good:** `du -sh data` falls by what was in the bin.

**e) A waiting run.** Start a long run, queue another, and press **Delete** on the waiting one: the question says it cannot be undone, and it does not appear in Deleted.

**f) The setting.** In `.env` put `STUDIO_BIN_DAYS=0` and `docker compose up -d`: Delete is for good again (the old question), and the next clean-up empties whatever is in the bin. Set it back to `30`.

**g) A phone.** The bar with **Deleted** and **Empty bin**, a card in the bin and the questions fit the screen.

**Send back:** the `ls -l data/studio.sqlite*` line, what (a) showed, anything that did not match "Good".

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
10. From step 18 (the Spark test for edits, M5c): the times and memory for each case, whether the model followed "image 1" and "image 2", what it did with a transparent picture, which mask colour worked, and the numbers you suggest for the cap and the cost warning.
11. From step 19: how long Load model takes (first time and cached), how much memory it takes and how much Unload gives back, and anything that did not match "Good".
12. From step 20 a) to i) (the music model, the first real run): the download size and time, the load time, the seconds of work per second of music at 15, 60 and (if you can) 180 seconds, the lowest available memory, whether the track was instrumental with the tag alone, whether the same seed repeats, how fast Ctrl+C stopped it, whether anything downloaded after the first time, and anything that did not match "Good".
13. From step 20 j) to n) (the Music tab on the Spark): the numbers from (k), (l) and (m), whether the track played and seeked on your phone, and a screenshot of anything on the phone that did not fit.
14. From step 21 (Make 4K): the seconds and the file size from (a) and for a square in (c), what you thought of the picture in (b), whether the rotation of a phone photo and the refusals in (c3) were right, the time for a 12 MP photo in (c3), whether a running job slowed down in (d), and anything that did not match "Good". From (i), if you ran it: the whole probe output and which of the two 4K files looks better to you, and where.
15. From step 22 (Enlarge): the `ENLARGED …` log lines (time, device, model), the times and sizes from (b), (d), (e), (f) and (i), your verdict on **where Enlarge looks better or worse than Make 4K** from (c) to (e), the memory and slowdown figures from (f) and (g), and anything that did not match "Good".
16. From step 23 (the Kept view): anything that did not match "Good", and whether the date in (e) was the date you expected.
17. From step 24 (the bin): the `ls -l data/studio.sqlite*` line, what the first clean-up put in Deleted, and anything that did not match "Good".
