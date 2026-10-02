# Qwen-Image Web Studio — Design Specification (round 6: version 2 planned)

Living document. **Version 1 is built and running on your Spark** (Generate, history, Options, themes, container: milestones M1–M4 and M7). **§21 specifies version 2, editing with several images plus the run housekeeping of M6 (cancel, keep, auto-expiry); nothing in §21 is built yet.** §1–§20 describe version 1 and the shared design; where §21 differs, §21 wins.

Status labels: **DECIDED** = you chose it, or explicitly delegated it. **PROPOSED** = an implementation detail that you chose not to review line by line; I will go with it unless you object, and you can challenge any of it at any time. **OPEN** = needs an answer.

## 1. Purpose

A containerised web app on the DGX Spark that generates and edits images with Qwen-Image-2.1 from a browser, and keeps a browsable history of every run (image + prompt + settings) so past prompts can be reviewed and tweaked.

## 2. Version 1 scope

**DECIDED — in scope**
- Text-to-image
- Image editing from an uploaded image (**not in v1 as delivered; re-planned as version 2 with several images, §21**)
- Transparent (RGBA) toggle
- Batches: N images per click
- History of past runs with "reuse this prompt and settings"
- Light and dark themes
- An **Options** button beside the prompt, holding every setting for the run, saved in the browser for next time

**PROPOSED — out of scope for v1**
- User accounts / login (an optional shared-token hook is reserved, §11)
- Choosing between multiple models
- Masks / inpainting (**now planned for version 2, §21.5**), upscaling, LoRAs
- LLM-based prompt rewriting

## 3. Decision log

| # | Topic | Decision | Status |
|---|---|---|---|
| 1 | Access | Open on the LAN, no login | DECIDED |
| 2 | Front end | React + TypeScript, built with Vite | DECIDED |
| 3 | GPU memory | Model loaded on demand, unloaded after an idle timeout | DECIDED |
| 4 | Options storage | Browser `localStorage` | DECIDED |
| 5 | History storage location | On the server, identical from every browser | DECIDED |
| 6 | Main layout | Prompt bar on top, results timeline below (one column, newest first) | DECIDED |
| 7 | Concurrency | One shared queue, capped length | DECIDED (cap 10 pending, env var) |
| 8 | History retention | Auto-expire runs after N days, plus manual delete | DECIDED (N = 30, env var) |
| 9 | Options panel | Slide-over drawer on desktop, bottom sheet on phones | DECIDED |
| 10 | Mode switch | Explicit **Generate \| Edit** toggle above the prompt | DECIDED |
| 11 | Seed | Random each run, seed recorded on every result. A "Lock seed" checkbox keeps it fixed. **Reuse copies the run's seed and turns Lock on.** | DECIDED |
| 12 | Edit input methods | Choose-file button, drag-and-drop, paste from clipboard, "Edit this" button on past results | DECIDED |
| 13 | Backend | Python (FastAPI + uvicorn), because `diffusers` is Python. **The model runs in a separate GPU worker process** that the API starts on demand and that exits when idle (§4, §9) | DECIDED |
| 14 | History format | SQLite + image files on a mounted volume | PROPOSED |
| 15 | Idle timeout | 15 minutes, env var | DECIDED |
| 16 | Container runtime and orchestration | **Docker + Compose.** You delegated this to NVIDIA's documented approach: Docker with the NVIDIA Container Toolkit is preinstalled on the Spark (§12) | DECIDED (delegated) |
| 17 | Model weights source | Downloaded on first use into a mounted Hugging Face cache volume | DECIDED |
| 18 | Uploaded source images | Kept with the run; deleted when the run expires or is deleted | DECIDED |
| 19 | Behaviour when too little memory is free (e.g. Hermes' LLM server is holding most of it) | Pre-flight memory check before loading the model (§9a). If short, **fail fast** with clear instructions; you fix it and click Retry. No waiting in the queue, and the studio never controls the LLM container | DECIDED |
| 20 | Starting defaults and limits | Size 2048×2048 (1:1), 40 steps, 1 image per click (max 8), queue cap 10, idle unload 15 min, runs expire after 30 days (§6, §13) | DECIDED |
| 21 | Security for v1 | As §11: no login; required custom header plus no CORS; upload validation; strict CSP; non-root container; no Docker socket; `STUDIO_TOKEN` reserved but off (setting it stops the server from starting, so it can't give a false sense of protection) | DECIDED |
| 22 | Review cadence | I stop after **every milestone** (M1–M8), report what works and what the tests showed, and wait for your go-ahead | DECIDED |
| 23 | Repository | A dedicated **private** GitHub repository, `NoSQLKnowHow/ai-image-studio` (renamed from the working name `dgx-spark-image-studio`), separate from `LiveLabs-Image-Dev`. Nothing for this project is written to `LiveLabs-Image-Dev` | DECIDED |
| 24 | Multi-image editing (v2) | One edit can use **several uploaded images with one prompt**, up to the cap (#26). Replaces the one-reference rule of §5.4 (§21) | DECIDED |
| 25 | Referring to images (v2) | Number badges (1, 2, 3 …) on the tray's images, matching the order the model sees; clicking a badge inserts "image N" at the prompt's cursor (§21.4) | DECIDED |
| 26 | Image cap (v2) | 4 by default, configurable up to 10 (`STUDIO_MAX_INPUT_IMAGES`); raised after measuring on the Spark | DECIDED |
| 27 | Local edits (v2) | Specified in v2, built after multi-image editing works (§21.5, M5d) | DECIDED |
| 28 | Edit output size (v2) | Auto (about 1 MP, shape from the last image) with a 1K / 2K choice, default 1K (§21.4) | DECIDED |
| 29 | M6 is part of v2 | The housekeeping the v1 plan left for M6 (**cancel** a queued or running job, **Keep** a run, **auto-expiry** with a warning) is built as part of version 2, not as a separate release (§21.11) | DECIDED |

## 4. Architecture (DECIDED: separate worker process)

```
 Browser (React SPA)  <--- HTTP/JSON + server-sent events --->  API process (FastAPI)
                                                                  - serves the built UI
                                                                  - job queue, SQLite, thumbnails
                                                                       |  spawns / stops (JSON lines over stdio)
                                                                       v
                                                                GPU worker process
                                                                  - loads QwenImage21Pipeline
                                                                  - runs one job at a time
                                                                  - exits after the idle timeout
```

Why a separate worker process:
- Exiting the process is the one guaranteed way to give GPU memory back, which matters if Hermes shares the Spark's memory.
- A CUDA out-of-memory error or crash kills the worker, not the web server. The page stays up and reports the failure.
- The worker never touches the database or the network (other than the Hugging Face download). The API process is the only SQLite writer.

Consequences of the decisions:
- **Vite**: multi-stage container build. Node builds the UI; only static files ship in the final image (no Node at runtime).
- **No login**: anyone on the network can generate, view, cancel and delete. Because there is no identity, "cancel only my jobs" cannot be enforced; any viewer can cancel any queued job (PROPOSED: accept this, LAN-trust model).
- **Idle unload**: the first job after a quiet spell is slower, so the UI must show the model state.
- The Spark is **aarch64**: the image must be built for arm64 (simplest is to build on the Spark itself).

## 5. Page specification

### 5.1 Screen anatomy

```
┌────────────────────────────────────────────────────┐
│ ◐ Studio                     [model: ready] [☾/☀]  │  header
├────────────────────────────────────────────────────┤
│ [ Generate | Edit ]                                 │  mode toggle (DECIDED)
│   (Edit only: reference image drop zone)            │
│ [ prompt (multi-line, grows) ................... ]  │  prompt bar
│ [ Options ⚙ ]                        [ Generate ▶ ] │
├────────────────────────────────────────────────────┤
│ QUEUE: 1 running, 2 waiting                         │  only when non-empty
├────────────────────────────────────────────────────┤
│ ┌──────┐  "Halloween town at dawn…"                 │  run card
│ │ img  │  Generate · 2048×2048 · 40 steps · seed 42 │
│ └──────┘  [Reuse] [Edit this] [⬇] [Keep] [🗑]        │
└────────────────────────────────────────────────────┘
```

- Header shows a **model state pill**: unloaded / loading / ready (with idle countdown) / busy / error / unavailable. `unavailable` means the pipeline can't be imported (e.g. `diffusers` too old); the pill links to the reason and the fix. (PROPOSED)
- Ctrl/Cmd+Enter submits. The Generate button stays usable while jobs are queued, up to the cap. (PROPOSED)
- Single column so it works down to phone width. (PROPOSED)
- The prompt text is also kept in the browser as an unsent draft. (PROPOSED)

### 5.2 Themes — PROPOSED
- Follows the OS setting (`prefers-color-scheme`) by default; the header button cycles system → light → dark; choice saved in `localStorage`.
- Theme applied before first paint (no flash of the wrong theme).
- Colours are design tokens (CSS variables), at least WCAG AA contrast in both themes, visible keyboard focus, respects reduced-motion.

### 5.3 Options drawer — DECIDED form, PROPOSED details
- Opens from the Options button: slide-over from the right on desktop (page stays visible), bottom sheet on phones. Esc or ✕ closes; focus is trapped while open and returned to the button on close.
- Sections: Size · Sampling (steps, seed, guidance) · Output (images per click, transparent) · Advanced (negative prompt). **Reset to defaults** at the bottom.
- Changes apply immediately to the *next* run and are saved to `localStorage` as you type (debounced). No "Save" button.
- Controls the loaded pipeline does not support are hidden, not disabled (see §7 `GET /api/capabilities`). Their stored values are kept in case support appears later.

### 5.4 Edit mode — DECIDED inputs, PROPOSED details

> **Version 1 text; superseded by §21.4**, which allows several reference images (decision #24). The four input methods and the rest of the behaviour carry over as described there.

- A reference image can arrive four ways: choose-file button, drag-and-drop onto the prompt area, paste from the clipboard, or **Edit this** on any past result.
- Shows a thumbnail of the reference with a remove (✕) button. One reference image in v1 (see open item 3).
- In Edit mode, size defaults to **Auto — from input** (the pipeline sizes the result), matching the CLI behaviour. Choosing a size overrides it.
- **Transparent** is hidden in Edit mode (the model card documents it for text-to-image only).

### 5.5 Run card — PROPOSED
- One card per **click**. A batch of N shows N thumbnails in a small grid, sharing the prompt; each image records its own seed.
- Shows: full prompt (collapsed after a few lines, expandable), negative prompt if used, mode badge (Generate / Edit / Transparent), size, steps, seed(s), model, time created, how long it took, status (queued / running / done / failed / canceled), and for Edit runs a thumbnail of the source.
- Running cards show progress: image i of N and, if the pipeline supports a per-step callback, step k of T; otherwise a spinner with elapsed time.
- Failed cards show the error message and a **Retry** button (resubmits the stored options).
- Click an image → lightbox.
- Actions: **Reuse** (loads prompt + options + seed into the bar and drawer, Lock seed on, and switches the mode toggle to the run's mode), **Edit this**, **Download**, **Copy prompt**, **Keep** (exempt from auto-expiry), **Delete** (with confirmation).
- Cards update live in every open browser (server-sent events).

### 5.5a Queue — DECIDED, details PROPOSED
- One GPU worker, one job at a time; a job = one click (its N images run one after another).
- Pending cap 10 (env var). Beyond that, Generate is refused with a clear message (HTTP 429, shown inline).
- Queued jobs show position and can be canceled. Cancelling a running job takes effect between images, and between steps only if the pipeline exposes a per-step callback (to verify).

### 5.6 Retention — DECIDED, details PROPOSED
- Runs older than N days (default 30, env var) are deleted with their image files, at startup and once a day. Pending and running runs are never expired.
- Because auto-expiry can silently discard something you wanted: **Keep** exempts a run, and the card shows "expires in X days" when fewer than 7 remain. Confirm or drop this mitigation.

## 6. Options panel contents

Every value is saved in `localStorage` and restored on the next visit. Server-side validation enforces every limit again.

| Option | Applies to | Default | Limits / notes |
|---|---|---|---|
| Size | both | 1:1 = 2048×2048 (Edit: Auto — from input) | 7 model-card presets: 1:1 2048×2048, 4:3 2400×1792, 3:4 1792×2400, 3:2 2528×1696, 2:3 1696×2528, 16:9 2752×1536, 9:16 1536×2752; or Custom W×H: each side 256–4096, multiple of 32 (the pipeline's `check_inputs` requires it; found in M2), total ≤ 4.5 MP (limits PROPOSED, to be tested on the Spark) |
| Steps | both | 40 | 1–100 |
| Seed | both | Random each run; Lock seed off | 0–4294967295 |
| Images per click | both | 1 | 1–8 |
| Negative prompt | both | empty | shown only if the pipeline supports it |
| Guidance strength | both | model default | sent as `true_cfg_scale`; 0.1–20; shown only if supported |
| Transparent (RGBA) | Generate | off | wraps the prompt in the model card's recommended format; saves an alpha PNG |

The prompt lives in the prompt bar, max 8000 characters (env var). Mode is the toggle above it; both are remembered in the browser.

**Persistence rules (PROPOSED):** stored under a versioned key (`studio.options.v1`). On load, each field is validated independently; an invalid or missing field falls back to its default, so a corrupt or old value can never break the page. If `localStorage` is unavailable (private window), the page works with in-memory options and shows a small notice.

**Not per-run (server configuration):** model id/path, idle timeout, CPU offload, queue cap, retention days, storage path, port, upload limits (§13).

## 7. API (PROPOSED)

All under `/api`. JSON unless noted. Mutating requests require the header `X-Studio-Client: 1` (see §11).

| Method & path | Purpose | Notes |
|---|---|---|
| `GET /api/health` | Liveness for the container healthcheck | Always cheap; does not touch the GPU |
| `GET /api/status` | Model state, idle countdown, queue length, system-memory figures (§9a) | Also pushed over SSE |
| `GET /api/capabilities` | Which options the pipeline supports, and all limits and defaults | See below |
| `POST /api/uploads` | Upload a reference image (multipart) | Returns `upload_id`; 413 too large, 415 wrong type, 422 undecodable |
| `POST /api/runs` | Create a run | Body: mode, prompt, options, `input_image` (`{upload_id}` or `{image_id}`). 201 with the run; 422 field errors; 429 queue full |
| `GET /api/runs?limit=&before=` | List runs, newest first | Cursor pagination; "load more" in the UI |
| `GET /api/runs/{id}` | One run with its images | |
| `POST /api/runs/{id}/cancel` | Cancel queued or running | 409 if already finished |
| `PATCH /api/runs/{id}` | `{pinned: bool}` | The only mutable field |
| `DELETE /api/runs/{id}` | Delete run and files | 409 if running (cancel first) |
| `GET /api/images/{id}` | Full PNG | `?download=1` sets a meaningful filename (below) |
| `GET /api/images/{id}/thumb` | WebP thumbnail | |
| `GET /api/events` | Server-sent events | `hello` (`{status, runs}`: the status plus the newest page of runs, read after the stream subscribed, so it is a consistent starting point), `run.created`, `run.updated` (full run), `run.progress` (step progress), `run.deleted`, `queue.updated` (positions), `worker.state`, `overflow`, `shutdown` (the server is stopping; the stream then ends). A `: ping` comment every 15 s keeps proxies from closing the stream. Every connection, first or reconnect, starts from its `hello`; the client never lets an older copy of a run (a late POST response, a stale page) replace a newer one, since runs only move forward (queued → running → finished) |

**Version 2 changes the upload and run-creation calls** (several inputs per run, `DELETE /api/uploads/{id}`, `resolution`): see §21.6.

**Capabilities without a loaded model:** the API process can't inspect a pipeline that isn't loaded, so at start-up it runs a short GPU-free probe subprocess that imports `diffusers` and inspects `QwenImage21Pipeline.__call__` (the same idea as the CLI's early signature check). The result is cached. If the import fails the state is `unavailable` with the reason.

**Download filenames:** `generate_halloween-town-dawn_2048x2048_s42_20260930-141502.png`, using the same scheme as the CLI (`edit_` runs include the source's name). PNG text metadata carries prompt, seed, steps, model.

## 8. Data model and storage (PROPOSED)

SQLite in WAL mode; the API process is the only writer.

**`runs`** (one per click): `id`, `created_at`, `started_at`, `finished_at`, `status` (queued / running / done / failed / canceled), `mode`, `prompt`, `effective_prompt` (after the RGBA wrapper), `negative_prompt`, `transparent`, `width`, `height` (null = auto), `steps`, `cfg_scale`, `seed` (of image 0), `num_images`, `model_id`, `input_image_id`, `error_message`, `error_hint`, `pinned`, `options_json` (exact snapshot, so Reuse/Retry are faithful).

**`images`**: `id`, `run_id`, `idx`, `seed`, `width`, `height`, `has_alpha`, `bytes`, `path`, `thumb_path`, `created_at`. Uploaded reference images are rows here too, flagged `kind = input` (this is what lets "Edit this" and uploads share one code path).

**Files** under the data volume: `images/<run>/<idx>.png`, `thumbs/<run>/<idx>.webp`, `inputs/<id>.png` (uploads are re-encoded to PNG), `studio.sqlite`. All file access is by database id; client-supplied filenames are never used in paths.

**Version 2** adds a `run_inputs` table (ordered inputs per run) and a schema migration: §21.7.

**Restart behaviour:** queued runs persist and resume; a run that was `running` when the server stopped is marked failed ("interrupted by restart") and keeps any images already finished.

## 9. Worker and queue behaviour (PROPOSED)

- API ↔ worker: JSON lines over stdio. Commands: `load`, `run`, `cancel`, `shutdown`. Events: `state`, `progress`, `image_done`, `error`.
- The worker is started lazily when a job needs it. After the queue has been empty for the idle timeout, the API sends `shutdown` and waits for the process to exit; a job arriving mid-shutdown waits for the exit, then a fresh worker starts.
- The worker saves each PNG straight to the data volume and reports the path; the API validates, makes the thumbnail and writes the database row.
- Loading follows the CLI's proven settings: `dtype=torch.bfloat16`, `.to("cuda")` (or CPU offload if configured), seed via `torch.Generator("cuda").manual_seed(seed + i)`.
- Memory is logged at load and after each image and shown in `/api/status`, using system-memory figures rather than NVML (§9a), so the real footprint next to Hermes can be measured on the Spark instead of guessed.

## 9a. Unified memory on the Spark (new in round 4; PROPOSED)

The Spark's GB10 shares one 128 GB memory pool between CPU and GPU. From NVIDIA's playbooks and community reports (search summaries, not the pages themselves):

- There is no separate VRAM figure, so NVML memory queries (the memory columns of `nvidia-smi`, some monitoring tools) can report "Not Supported" or misleading numbers.
- The Linux page cache (including cached model files) lives in the same pool and is invisible to GPU tools. NVIDIA playbooks repeatedly advise flushing it on the **host** when a load fails with out-of-memory despite apparent free memory: `sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'`. That needs host root, so a container cannot do it for you.
- Hermes' local LLM server (vLLM) is commonly set to claim a large fraction of the pool. One write-up of NVIDIA's Hermes recipe uses `gpu_memory_utilization 0.85`, which would leave very little for an image model (the reported 7B transformer alone is roughly 14 GB in bf16, before the text encoder, VAE and 2K activations). Whether that applies to your Spark is unknown (§18 item 1).

Consequences for the design:

- `/api/status` reports **system memory** (`MemAvailable` from `/proc/meminfo`, container cgroup usage, the worker's RSS), plus `torch.cuda.memory_allocated()` where it works. It does not rely on NVML.
- **Pre-flight check:** before loading the model the API compares `MemAvailable` with a configured minimum (`STUDIO_MIN_FREE_GB`; default to be measured on the Spark). If short, the job is not started; the message says what to do (lower the LLM server's memory setting, stop it, or flush the page cache on the host). This stops a doomed load from dragging the whole machine into swap. The job then **fails immediately** (decision #19): you fix the memory situation and click Retry. It does not wait in the queue, and the studio never controls the LLM container.
- Acceptance criterion 10 is verified by the worker process exiting and `MemAvailable` rising by about the model's footprint, not by `nvidia-smi`.

## 10. Error handling (PROPOSED)

| Situation | Behaviour |
|---|---|
| Field validation fails | 422 with per-field messages, shown next to the control |
| Queue full | 429, inline message "Queue is full (10 waiting)" |
| Upload wrong type / too large / corrupt | 415 / 413 / 422 with a clear message; nothing stored |
| Too little free memory before load (§9a) | Run marked failed at once, before any load starts; message says how to free memory; Retry works afterwards |
| CUDA out of memory | Run marked failed with hint (smaller size, fewer images, enable CPU offload); worker restarts on the next job |
| Model load fails (HF auth, network, disk, missing package) | Pill shows `error` with the message and hint; the job fails; the next job tries a fresh load (no retry loop) |
| GPU architecture mismatch ("no kernel image") | Same as above with the PyTorch-build hint from the CLI |
| Worker crashes | Run failed; already-finished images kept; API keeps serving |
| Partial batch | "2 of 4 completed" shown; finished images stay |
| Disk full | Run failed with a clear message; banner on the page |
| SSE connection drops | Client reconnects with backoff and refetches |
| `diffusers` missing the pipeline | State `unavailable` with the install hint |

The error messages and hints reuse the wording already written for the CLI script.

## 11. Security (DECIDED for v1)

The threat model is "trusted LAN, no login", so the goal is to limit accidents and cross-site tricks, not to stop a determined local attacker.

- **Cross-site requests:** no CORS headers; every mutating request needs the custom header `X-Studio-Client: 1`, which forces a browser preflight that the server refuses. This stops a web page the user happens to visit from queuing jobs or deleting history on the Spark.
- **Uploads:** accept PNG/JPEG/WebP only; verify by decoding with Pillow; cap file size (20 MB), pixel count (16 MP) and use Pillow's decompression-bomb protection; re-encode to PNG on store; random ids.
- **Output safety:** React escapes everything (no `dangerouslySetInnerHTML`); `Content-Security-Policy` restricting scripts to same origin; `X-Content-Type-Options: nosniff`.
- **Container:** runs as a non-root user, no privileged mode, only the two volumes mounted, and **no Docker socket** (the studio cannot start or stop other containers).
- **No internet exposure:** documented as unsupported without adding auth. `STUDIO_TOKEN` is reserved for a future shared password; in v1 the server **refuses to start** if it is set, so it can never give a false sense of protection.
- **Host allow-list (optional; added in M1):** `STUDIO_ALLOWED_HOSTS=spark.lan,192.168.1.20` rejects requests whose `Host` header isn't listed, which blocks DNS-rebinding tricks (a web page pointing a host name of its own at the Spark). Off by default because the Spark's LAN name and address aren't known in advance.

## 12. Container and deployment (PROPOSED, based on NVIDIA's Spark documentation)

**What NVIDIA's documentation and playbooks say** (from search summaries; the pages themselves were not reachable from my sandbox, see §18 item 10):

- Docker, the NVIDIA Container Runtime and the NVIDIA Container Toolkit come **preinstalled and configured** on the Spark. NVIDIA's check is `docker run -it --runtime=nvidia --gpus=all nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 bash`.
- For the Spark's GB10 (Blackwell, `sm_121`, aarch64), NGC's `nvcr.io/nvidia/pytorch:25.10-py3` and later are reported to work; 25.10 ships CUDA 13.0.
- **Hermes** is Nous Research's Hermes Agent. NVIDIA's playbook runs its local LLM in a **Docker container running vLLM**, serving an OpenAI-compatible API on port 8000. A community write-up shows the launch shape `docker run -d --gpus all --ipc=host -v ~/.cache/huggingface:/root/.cache/huggingface -p 8000:8000 …` (not NVIDIA's own page).
- NVIDIA's web-UI playbooks (ComfyUI on port 8188, Open WebUI) are reached at `http://<spark-ip>:<port>` on the LAN or, recommended by NVIDIA, through an **NVIDIA Sync** SSH tunnel with a custom app entry.

**Design:**

- **Multi-stage build.** Node builds the React app. The final stage is `FROM nvcr.io/nvidia/pytorch:<tag>`, where `<tag>` is the newest `YY.MM-py3` (at least 25.10) that I test on the Spark, pinned exactly. `diffusers` comes from a pinned GitHub commit; other dependencies are pinned.
- **Keep NVIDIA's PyTorch.** NGC images ship NVIDIA's own PyTorch build, and a careless `pip install` can replace it with a generic wheel. The build keeps torch pinned and is checked inside the built image with `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`. (Expected NGC behaviour; verify on the Spark.)
- **Compose service.** `runtime: nvidia` plus a GPU reservation (`deploy.resources.reservations.devices` with `driver: nvidia`, `count: all`, `capabilities: [gpu]`); `shm_size: 2gb` (`ipc: host`, as in NVIDIA's examples, is the fallback if needed); `restart: unless-stopped`; `user` set to the Spark user's UID:GID so mounted folders stay writable.
- **Volumes.** `./data:/data` for the database and images. The Hugging Face cache is mounted at `/models` with `HF_HOME=/models`; the host side defaults to `~/.cache/huggingface`, the same cache the vLLM container uses, so nothing downloads twice.
- **Port.** Container 8080, published as `8080:8080` for LAN access (decision #1). Hermes' vLLM uses 8000 and NVIDIA's ComfyUI playbook 8188, so there is no clash. For tunnel-only use, publish `127.0.0.1:8080:8080` and add a custom app in NVIDIA Sync. If the page isn't reachable from the LAN, check the Spark's firewall.
- **Operations.** Healthcheck `GET /api/health`; logs to stdout; **build on the Spark itself** (arm64).
- A `compose.yaml` and the equivalent `docker run` one-liner ship in the repo.
- **Backups:** `scripts/backup.sh` and `scripts/restore.sh` (README "Backing up and restoring") save and restore the four things that live outside the container: `./data`, `.env`, the built image and, optionally, the model cache. A backup is **one `.tar` file** (an ordinary tar of already-compressed pieces plus a manifest and checksums) so it can be copied to a NAS; it is re-read and its checksums checked before it gets its final name, and `restore.sh --verify` checks a copy without Docker. The studio is stopped only while `./data` is packed (SQLite in WAL mode must be at rest), and a restore reads the `.tar` in place and never deletes anything: what is in the way is moved aside. Their tests, `scripts/tests/backup_restore_test.sh`, run without Docker.
- The unified-memory caveats are in §9a.

## 13. Configuration reference (PROPOSED; all environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `STUDIO_MODEL` | `Qwen/Qwen-Image-2.1` | Hub id or local path |
| `STUDIO_DATA_DIR` | `/data` | Database, images, thumbnails |
| `HF_HOME` | `/models` | Hugging Face cache (mounted from the host, §12) |
| `HF_TOKEN` | unset | Only if the repo is gated |
| `STUDIO_LOCAL_FILES_ONLY` | `false` | Never touch the network |
| `STUDIO_CPU_OFFLOAD` | `false` | Use model CPU offload |
| `STUDIO_MIN_FREE_GB` | unset = off; `compose.yaml` sets `40` | Minimum `MemAvailable` required before loading the model (§9a). 40 is a starting estimate (about 14 GB transformer plus text encoder, VAE and activations, with headroom), to be replaced by the figure measured on the Spark. `0` turns it off |
| `STUDIO_IDLE_TIMEOUT_MIN` | `15` | Unload the model after this idle time |
| `STUDIO_QUEUE_CAP` | `10` | Max pending jobs |
| `STUDIO_RETENTION_DAYS` | `30` | Auto-expire age; `0` disables |
| `STUDIO_MAX_IMAGES_PER_RUN` | `8` | Batch cap |
| `STUDIO_MAX_PROMPT_CHARS` | `8000` | Prompt length cap |
| `STUDIO_MAX_UPLOAD_MB` | `20` | Upload size cap |
| `STUDIO_MAX_INPUT_IMAGES` | `4` | **v2:** images per edit, 1–10 (§21.9) |
| `STUDIO_UPLOAD_TTL_HOURS` | `24` | **v2:** how long an unclaimed staged upload is kept (§21.9) |
| `STUDIO_STATIC_DIR` | `<repo>/frontend/dist` if built; `/app/static` in the image | The built web page (added in M2) |
| `STUDIO_HOST` | `0.0.0.0` | Listen address inside the container (added in M1) |
| `STUDIO_PORT` | `8080` | Listen port |
| `STUDIO_ALLOWED_HOSTS` | empty (any) | Comma-separated host names to accept (§11; added in M1) |
| `STUDIO_PIPELINE` | `real` | `fake` uses the test pipeline (§15) |
| `STUDIO_FAKE_STEP_DELAY_MS` | `30` | Fake pipeline only: delay per step (added in M1) |
| `STUDIO_FAKE_LOAD_FAIL` | unset | Fake pipeline only: simulate a model load failure (added in M1) |
| `STUDIO_TOKEN` | unset | Reserved; setting it stops the server from starting (§11) |

## 14. Tech stack and repo layout (PROPOSED)

- **Backend:** Python 3.11+, FastAPI, uvicorn, pydantic (request validation), Pillow, SQLite via the standard library.
- **Front end:** React 19 with TypeScript (strict), Vite, Radix UI Dialog for the drawer and dialogs, plain CSS with variables for theming. Unit tests with Vitest, browser tests with Playwright against the real backend running the fake pipeline.
- **Layout:**

```
ai-image-studio/   (repository root)
  backend/studio/    api, queue, worker, pipeline (real + fake), db, storage, config
  backend/tests/
  frontend/          Vite + React + TS (src/, e2e/)
  Dockerfile         (built on the Spark; docker/ has its build-time check and healthcheck)
  compose.yaml, .env.example
  docs/DESIGN.md
  scripts/qwen_image.py   (the CLI; stays standalone for now)
  scripts/backup.sh, restore.sh, tests/   (backup and restore, with their tests)
```

The server re-implements the size presets, RGBA wrapper, filename scheme and error translations rather than importing the CLI script. Sharing a module is a later refactor, not a v1 task.

## 15. Testing strategy (PROPOSED)

**The key idea: a fake pipeline.** With `STUDIO_PIPELINE=fake` the worker produces deterministic synthetic images (seed-dependent colours, the prompt drawn on it, optional alpha), simulates progress and delay, and can inject faults (out-of-memory, load failure, crash) on demand. The whole stack can then be developed and tested without a GPU or the weights — which is exactly my situation in this sandbox (no GPU, no access to the model).

Faults are injected per run with prompt directives: `[fake:error]`, `[fake:oom]`, `[fake:crash]` and `[fake:noise]` (stray output on stdout), optionally `@N` to target image N only, e.g. `[fake:crash@1]`. `STUDIO_FAKE_LOAD_FAIL=1` simulates a load failure. Fake images are clearly labelled "FAKE PIPELINE". (Added in M1.)

- **Unit:** validation and limits, slug and filenames, queue ordering and cap, retention and pinning, options persistence parsing (corrupt/old localStorage).
- **API integration:** pytest with the fake worker — every endpoint, SSE events, restart recovery, failure paths.
- **End-to-end:** Playwright with Chromium against the running container-less stack — themes, Options persistence across reload, Reuse locking the seed, all four Edit inputs, queue cap and cancel, phone-width layout, keyboard use.
- **Real hardware (manual, on the Spark, checklist supplied):** real model load and generation, idle unload actually frees GPU memory (`nvidia-smi`), behaviour next to Hermes, real out-of-memory handling, real timings.

**What I cannot verify from here:** anything involving the real model, CUDA, GPU memory, aarch64 builds, or Hermes. Those are covered only by the manual Spark checklist.

## 16. Acceptance criteria (PROPOSED)

1. The page opens from another LAN device with no login.
2. The theme follows the OS by default; the choice persists across reload with no flash.
3. Every option persists across reload; Reset restores defaults; corrupt `localStorage` falls back to defaults without a crash.
4. Generate produces a card with image, prompt, settings and seed; it survives a server restart and appears in a second browser.
5. A batch of N gives N images with seeds base … base+N−1.
6. The seed is random by default; Lock seed repeats it; Reuse copies prompt, options and seed and turns Lock on.
7. Edit works via file picker, drag-and-drop, paste and "Edit this"; the result card shows the source.
8. Transparent output is an RGBA PNG when the model returns alpha; otherwise the card warns.
9. A second click while running queues with a position; the 11th pending job is refused with a clear message; a queued job can be canceled; a running job stops between images.
10. After the idle timeout the worker process has exited and the system's available memory has risen by about the model's footprint (measured with `/proc/meminfo`, not `nvidia-smi`, §9a); the next job reloads it and the state pill tracks it.
11. Simulated out-of-memory, load failure and worker crash each yield a failed card with an actionable message; the server keeps serving; the next job works.
12. Runs older than N days are removed with their files; pinned runs are kept; queued and running runs are never removed.
13. Wrong-type, oversize and corrupt uploads are rejected with clear messages.
14. Downloads have the meaningful filename; PNG metadata holds prompt and seed.
15. At 360 px width the page has no horizontal scroll and Options is a bottom sheet.
16. Keyboard: drawer focus trap, Esc closes, Ctrl/Cmd+Enter submits, all controls reachable.
17. On the Spark, `docker compose up` from a clean checkout starts the server with a green healthcheck, and data persists across recreating the container.
18. With too little free memory, the pre-flight check fails the job immediately with an actionable message (Retry works once memory is free), and the machine is not pushed into swap.

**Version 2 adds criteria 19–30** (§21.10). Criterion 7 ("Edit works via file picker, drag-and-drop, paste and 'Edit this'") is replaced by 19–27, which cover several images.

## 17. Build order (PROPOSED)

| Milestone | Delivers | Done when |
|---|---|---|
| M1 | Backend skeleton, database, API, **fake pipeline**, in-memory queue | API tests pass; I can create runs and watch fake images appear |
| M2 | Real worker process + pipeline, idle unload, capability probe | Fake path still green; real path only checkable on the Spark |
| M3 | Front-end shell, theming, prompt bar, Options drawer, options persistence | Criteria 2, 3, 15, 16 pass in Playwright |
| M4 | Timeline, run cards, SSE live updates, Reuse, download, delete | Criteria 4, 5, 6, 14 pass |
| M5 | Edit mode: uploads, all four inputs. **Replaced by M5a–M5d for version 2 (§21.11)** | Criteria 13, 19–30 |
| M6 | Queue cap, cancel, retention, pin, failure handling. **Now part of version 2 (decision #29); what remains is in §21.11** | Criteria 8, 9, 11, 12, 18 pass |
| M7 | Containerfile, compose, docs | Image builds; criterion 17 on the Spark |
| M8 | Spark smoke test with you | Criteria 1, 10, 17 and the real-hardware checklist |

Each milestone is committed separately. **After each milestone I stop, report what works and what the tests showed, and wait for your go-ahead (decision #22).** Each milestone is pushed to `ai-image-studio` so you can review the diff on GitHub.

**Batches (decided 2026-10-02):** milestones are grouped into batches, **one pull request per batch**, so you can test something real sooner. Each batch's pull request targets `main` directly, never another batch's branch: a stacked pull request merges into its base branch, not `main`, unless that base has been deleted first (that is how batch 2 first landed on `m1-backend`). Batch 2 is **M2 + M3 + M4 + M7** (the real model, the web page and the container); M7 moved ahead of M5 and M6 so the studio can run on the Spark before Edit mode and the queue extras exist. M5, M6 and M8 follow.

**Progress:**

- **M1** (2026-10-01, branch `m1-backend`): backend, database, API, queue, worker process and fake pipeline.
- **Batch 2** (2026-10-02; reviewed as #2, merged to `main` via #3):
  - **M2:** the real `QwenImage21Pipeline` (checked against the diffusers source, not guessed: sizes must be multiples of 32, output can be RGBA, the per-step callback and `true_cfg_scale` exist); a start-up capability check; idle unload; the memory pre-flight from `/proc/meminfo`; errors translated into actionable messages.
  - **M3–M4:** the web page as specified in §5, with live updates. The browser tests found and fixed real bugs: a run that failed within milliseconds could show "Queued" forever (the live stream now opens with a consistent snapshot and older copies of a run never replace newer ones, §7), images requested while their run was being deleted gave a 500, and keyboard focus was lost when dialogs closed.
  - **M7:** `Dockerfile`, `compose.yaml`, `.env.example`, `docs/SPARK_TEST.md`.
  - Tests: backend 120 (pytest), front end 34 (Vitest) and 10 in the browser (Playwright; 50/50 over five repeats).
  - **Not verified, and not verifiable from my sandbox:** the image build (no Docker daemon) and anything on the real GPU. Checked instead: hadolint, `docker compose config`, dependency resolution for linux/arm64 + Python 3.12 with NVIDIA's torch held fixed, and the build-time check script in every branch. The Spark checklist covers the rest.
- Left for later: uploads and Edit mode (M5); cancel, pin and retention (M6; `STUDIO_RETENTION_DAYS` is read but nothing expires yet); the Spark smoke test with you (M8).

## 18. Open items and facts to verify

1. **Hermes and memory** — per NVIDIA's playbook, Hermes Agent uses a local LLM served by vLLM in a Docker container. Whether yours does, which model, and how much of the 128 GB it claims is unknown. This sets the memory budget (§9a). *Please run the commands in my message.*
2. **Model license** — verified 2026-10-02 against the licence file on Hugging Face (§19): the **Qwen Research License Agreement**, released 2026-09-20, grants use "FOR NON-COMMERCIAL PURPOSES ONLY", where "Non-Commercial" means "for research or evaluation purposes only". Commercial use needs a separate licence from Qwen (the agreement names a contact address). Other conditions: passing the Materials on needs a copy of the agreement and a notice; using the Materials or their outputs to create, train or improve a distributed AI model needs "Built with Qwen" or "Improved using Qwen"; disputes fall under the laws of China, with the Hangzhou courts. **The text says nothing explicit about who may use generated images**, so whether a given use counts as commercial is for you, or Qwen, to decide; I am not a lawyer. The studio does not contain or redistribute the weights; they are downloaded from Hugging Face at run time.
3. **Multiple reference images for editing** — resolved for the pipeline in round 6: the pinned source takes a list, numbers the images and uses the last one's shape (§21.2). The **ten-image maximum is confirmed by Qwen's post, model card and README** (§21.2); what the Spark can handle is still to be measured (§21.12 item 2).
4. **Pipeline arguments** — resolved in M2 from the source at the pinned commit: `negative_prompt`, `true_cfg_scale` (default 1.0, i.e. no guidance; the negative prompt only matters above 1), `callback_on_step_end` and `_interrupt` exist. The start-up check reads the signature anyway, and the UI hides what isn't supported.
5. **`diffusers` commit to pin** — resolved: `578c9b2c6636ab2424a0e56186268b83623656b2` (2026-10-01), in `backend/requirements-container.txt`.
6. **Base image / PyTorch build** — NGC `nvcr.io/nvidia/pytorch` 25.10 or later is reported to support the GB10 (`sm_121`). The Dockerfile defaults to `25.10-py3` (`NGC_TAG` changes it); confirm or move to a newer tag after the first build on the Spark. **First build on the Spark (2026-10-02):** 25.10 ships PyTorch `2.9.0a0+145a3a7bda.nv25.10`, numpy 2.1.0, flash_attn 2.7.4, Transformer Engine 2.8.0, apex and **torchao 0.14.0**. diffusers imports torchao whenever it is installed and needs `FqnToConfig` (torchao 0.15+), so the pipeline could not be imported; the image now removes torchao, which the studio doesn't use.
7. **Size and memory limits** — max pixels (4.5 MP) and the 8-image cap are guesses until measured on the Spark next to Hermes.
8. **GitHub repository** — resolved. `NoSQLKnowHow/ai-image-studio` (private) was created by you and attached to my session with push access. Earlier, the GitHub integration was refused (HTTP 403) when creating repositories and when pushing to `LiveLabs-Image-Dev`; that repository is deliberately not used for this project.
9. **Memory budget** — the model's real footprint (transformer, text encoder, VAE, activations at 2K) next to Hermes' LLM server; sets `STUDIO_MIN_FREE_GB` and the size and batch caps.
10. **NVIDIA's pages were unreachable** — docs.nvidia.com and build.nvidia.com are blocked from my sandbox, so §9a and §12 rest on search summaries and community posts. Before building, compare them with the current Container Runtime, NGC and Hermes playbook pages.
11. **Two quirks of the CLI script** (`scripts/qwen_image.py`, deliberately left as is): its size warning uses a multiple-of-16 rule, but the pipeline requires 32; and it flattens an RGBA input to RGB, while the pipeline's image encoder reads all four channels (§21.2 point 5). The studio does neither.

## 19. References (as returned by search; not independently opened)

- NVIDIA DGX Spark docs: NVIDIA Container Runtime for Docker — https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html
- NVIDIA DGX Spark docs: NGC — https://docs.nvidia.com/dgx/dgx-spark/ngc.html
- NVIDIA Spark playbook: Hermes Agent — https://build.nvidia.com/spark/hermes-agent
- NVIDIA Spark playbook: ComfyUI — https://build.nvidia.com/playbooks/comfyui
- NVIDIA Spark playbook: connect to your Spark (NVIDIA Sync) — https://build.nvidia.com/spark/connect-to-your-spark
- NVIDIA developer forum: memory creep on the Spark — https://forums.developer.nvidia.com/t/memory-creep-on-dgx-spark-where-your-128-gb-actually-goes-and-how-to-stop-it/364886
- Community: Hermes on DGX Spark with vLLM — https://cloudburn.dev/posts/hermes-dgx-spark-update/
- Docker Compose GPU support — https://docs.docker.com/compose/how-tos/gpu-support/

Note: NVIDIA publishes a ComfyUI playbook for the Spark, and ComfyUI reportedly supports Qwen-Image-2.1. It is node-based rather than the simple prompt page specified here, so it is noted only for completeness.

### Read directly (2026-10-02)

These were opened and read in full, not taken from search results. The announcement is a JavaScript page, so it was read by rendering it in a headless browser.

- Announcement: Qwen-Image-2.1: Compact, Efficient, and Unified Image Creation (2026-09-20) — https://qwen.ai/blog?id=qwen-image-2.1
- Model card — https://huggingface.co/Qwen/Qwen-Image-2.1
- Licence (Qwen Research License Agreement) — https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE
- GitHub README (editing, transparency, prompt rewriting, architecture) — https://github.com/QwenLM/Qwen-Image-2.1
- `diffusers` source at the pinned commit — https://github.com/huggingface/diffusers/tree/578c9b2c6636ab2424a0e56186268b83623656b2

## 20. Approval record

- **Round 5 (2026-09-30):** you reviewed the key proposals and confirmed: separate worker process (#13), starting defaults and limits (#20), security approach (#21), and review after every milestone (#22). All other PROPOSED items are implementation details left to my judgement (see the status labels at the top).
- **2026-10-01:** you gave the go for M1 ("Go, start M1.").
- **2026-10-02:** you asked for the next sections so you could test for real, chose **real model + container + web UI** as the next batch, and **one pull request per batch** (§17).
- **Still pending from you (not blocking M1–M7):** the `docker ps` / `docker stats` / `free -h` / `nvidia-smi` output from the Spark (sets the memory budget, §18 items 1 and 9).
- **Round 6 (2026-10-02):** version 1 was built (batch 2: real model, web page, container), built on the Spark and generating images. You asked for version 2 to add **editing with several uploaded images**, to be specified before any code. After a search of what Qwen announced and a read of the pinned pipeline source, you decided #24–#28: several images per edit, numbered badges with insert-into-prompt, a cap of 4 configurable to 10, local edits specified now and built second, and Auto size with a 1K/2K choice. Later the same day, with network access to the Qwen pages opened for the session, I read the announcement, the model card, the licence and the GitHub README directly; §21 and §18 were corrected from them (the prompt rewriter and the mask convention, which the search summaries had wrong or missing; the licence, now verified), and three refinements (R1–R3, §21.3) await your decision. You then decided that **M6 is part of v2** (#29). Details are §21 and are PROPOSED until you review them.
- **Repository (2026-09-30):** you asked that nothing for this project be written to `LiveLabs-Image-Dev` and that it get its own repository. Decided: private, personal account; first called `dgx-spark-image-studio`, renamed `ai-image-studio` the same day. You created it on GitHub and it was attached to my session. The earlier commits (CLI script, design spec) were replayed into it with their messages intact and removed from the LiveLabs clone.

## 21. Version 2: editing with several images, and run housekeeping (decisions #24–#29 DECIDED; details PROPOSED)

### 21.1 What v2 is, and what it replaces

- **Version 1** is what is built and running on your Spark: Generate (text-to-image), history, the Options drawer, themes, the container (milestones M1–M4 and M7). **Edit mode is not part of v1 as delivered**: the mode switch shows "Edit — soon".
- **Version 2 is editing**, with your addition: **upload several images for one prompt** (decision #24). It replaces the one-reference-image rule of §5.4, the "image editing from an uploaded image" line of §2 and milestone M5 (§17). Masks and local edits, listed as out of scope in §2, come in as the second half of v2 (§21.5).
- **Version 2 also includes M6** (decision #29): the housekeeping v1 left unbuilt, namely cancel, Keep and auto-expiry. §21.11 says what is already built and what remains, and how it fits with the editing work.
- Where §21 differs from §1–§20, **§21 wins** for v2. The other sections are not rewritten; they say "see §21" where it matters.
- **How sure am I?** Three kinds of evidence are labelled throughout:
  - **[verified]**: read from the pinned `diffusers` source (commit `578c9b2`), the code the studio actually runs.
  - **[Qwen]**: read directly on 2026-10-02 from Qwen's announcement post, the model card and licence on Hugging Face, and the model's GitHub README (§19). Earlier in this round I only had search summaries; they were right about the ten-image limit and the licence caveat, but two things needed correcting (the prompt rewriter, and the mask colour convention). §21.2 has the corrected versions.
  - **[unconfirmed]**: not stated by any source I could read. To be tested on the Spark (§21.12).

### 21.2 What the model does with several images

**[verified]**

1. `image` is a **flat list**, applied to the one prompt of the run (the studio sends one prompt per run).
2. The text encoder receives the images **in list order, numbered** `<image1>`, `<image2>`, … So "the first image" or "image 2" in a prompt means list position, and the studio's numbering must be exactly the submit order.
3. **Every input is resized to about `output_resolution`² pixels**, each keeping its own aspect ratio (sides rounded to multiples of 32), and goes through **both** the text encoder and the VAE. With `output_resolution` 1024 that is about 1 MP per image; at 2048 about 4 MP. So **the cost of an edit grows with (number of images) × (resolution)²**, at least in proportion; the real curve is to be measured on the Spark.
4. With no explicit height and width, the **output shape follows the last image's aspect ratio**, at about `output_resolution`² pixels (default 1024, so about **1 MP**, not the 2048×2048 that Generate uses). An explicit size sets the output only; the inputs are still resized by `output_resolution`.
5. **Transparent (RGBA) inputs:** the alpha is composited over white only for the text encoder's copy; the VAE still reads all four channels. So the studio must **never flatten an upload**. (The CLI does; see §18 item 11.)
6. There is **no mask parameter** and no per-image weight. `use_kv_cache` (default on) computes the condition images' keys and values once and reuses them, so extra images should cost mostly once rather than per step (to be measured).

**[Qwen]** (announcement post, model card, GitHub README)

- **Up to 10 reference images** in one edit, combined into one composition. The post's examples: six portraits into one group photograph, five items (a model, clothes, shoes, a bag, a hat) into an outfit, ten pieces of furniture into a room. The README's own example prompt uses no numbers: *"These three characters are sitting around a campfire in a forest."*
- **Local edits, three ways:**
  1. **Circles in different colours**, with the colours named in the prompt: *"Remove the metal watch in the blue circle, change the hair in the red circle to black, and replace the area in the green circle with gray short-sleeved linen pajamas."*
  2. **Painted annotations**, for example an area **marked in white**, where the model then adds a diver.
  3. **A separate mask: "the original image and a separate mask as two inputs"**, which keeps the whole original visible (circles and paint cover part of it).
- **Transparency is chosen by the prompt.** The recommended format: *"This is an RGBA image with transparency. <your description>. The image has alpha channel and the background is transparent."* The studio's existing wrapper produces exactly this. The post also shows **editing a transparent image while keeping its transparent background**, **editing text inside a transparent layer**, and **extracting a subject from an ordinary RGB photograph as an RGBA layer**.
- **Prompt rewriting is official and recommended.** Qwen publishes two fine-tuned checkpoints, `Qwen/Qwen-Image-2.1-PE-T2I` and `Qwen/Qwen-Image-2.1-PE-I2I` (Qwen3.5-VL 9B, per the README), that expand a short prompt into a detailed one. The editing one **sees the input images** and returns the rewritten prompt plus either a new aspect ratio (`wh_ratio`) or an instruction that the output **follows the shape of a named input** (`ratio_follow`, for example `<image1>`). It is a separate model, not part of the pipeline.
- **Architecture:** text encoder Qwen3-VL 8B; a 64-channel RGBA autoencoder with 16× spatial compression; a 7B-parameter, 32-layer transformer; **prefix KV cache reuse**, so the input images and the instruction are computed once and reused, which is why extra images cost less than they look. Native 2K; the recommended sizes are the seven presets of §6.
- **Licence: the Qwen Research License Agreement** (released 2026-09-20). **"Non-Commercial" is defined as "for research or evaluation purposes only"**, and commercial use needs a separate licence from Qwen. Passing the Materials on requires a copy of the agreement and a notice. Using the Materials or their outputs to create, train or improve an AI model that is distributed requires showing "Built with Qwen" or "Improved using Qwen". The text says nothing explicit about who owns or may use *generated images*. See §18 item 2.

**[unconfirmed]**: not stated by any source I could read

- **That a prompt can refer to images by number** ("image 2"). The pipeline numbers them `<image1>` … in the text encoder's input and Qwen's own rewriter writes `<image1>`, so it is plausible, but the model card never says it, and its example avoids numbers. Test on the Spark.
- **The mask convention:** which colour means "edit here", whether the mask must be the same size as the original, and how to refer to it in the prompt. Only the painted-annotation example (white marks the place) hints at it.

### 21.3 Decisions

| # | Topic | Decision | Status |
|---|---|---|---|
| 24 | Multi-image editing | One edit can use **several uploaded images with one prompt**, up to the cap (#26). Replaces the one-reference rule of §5.4 | DECIDED |
| 25 | Referring to images | Every image in the tray shows a **number badge** (1, 2, 3 …) that matches the order the model sees. **Clicking a badge inserts "image N" at the prompt's cursor** | DECIDED |
| 26 | Image cap | **4 by default, configurable up to 10** (`STUDIO_MAX_INPUT_IMAGES`). Raised only after memory and time are measured on the Spark next to Hermes | DECIDED |
| 27 | Local edits | **Specified in v2, built after multi-image editing works** (§21.5, milestone M5d) | DECIDED |
| 28 | Edit output size | **Auto (about 1 MP, shape from the last image) with a 1K / 2K resolution choice**; default 1K. Choosing a size in Options overrides Auto | DECIDED |
| 29 | M6 is part of v2 | **Cancel** (queued and running jobs), **Keep** (pin) and **auto-expiry** with a warning, left unbuilt by the v1 plan, are built as part of version 2 (§21.11) | DECIDED |

**Refinements proposed after reading Qwen's pages (not yet decided, so not in the table above):**

- **R1, a "follows image N" selector instead of reordering for shape.** The pipeline gives the result the last image's shape. Reordering just to change the shape is awkward, and it makes a mask placed last set the shape by accident. Instead, Size on Auto shows **"Result follows image [N ▾]"**, defaulting to the pipeline's own choice (the last image that is not a mask), and the studio computes an explicit width and height from the chosen image with the pipeline's own arithmetic. Qwen's rewriter does the same thing with `ratio_follow`.
- **R2, Transparent is offered in Edit mode.** v1 hid it because the model card only showed it for text-to-image; the post now documents editing transparent images and extracting subjects as RGBA layers. The toggle wraps the prompt in the recommended format, as in Generate.
- **R3, an optional "Improve prompt" step** using Qwen's official rewriter (§21.2). It is a separate 9B model with real costs (§21.12 item 3), so it is a question for you, not a default.

### 21.4 Edit mode on the page (supersedes §5.4)

**The reference tray** sits above the prompt in Edit mode: a wrapping grid of thumbnails (3 across on a phone, so the page never scrolls sideways) plus an **Add images** tile.

- **Four ways in** (decision #12), all of which *add* to the tray:
  1. the **Add images** tile / choose-file button, with multiple selection;
  2. **drag-and-drop** of one or several files onto the prompt area;
  3. **paste** an image from the clipboard (appended);
  4. **Edit this** on any past result (the result is appended and the mode switches to Edit).
- **Each thumbnail** shows its **number badge**, a ✕ to remove it, and a drag handle. Upload progress shows on the thumbnail itself.
- **Order matters, so reordering is first-class:** drag a thumbnail to a new place, or use its **Move earlier / Move later** buttons (the keyboard route; focus stays on the moved thumbnail). The numbers update at once. A live region announces each change ("Image 3 added", "Moved to position 1").
- **Inserting a reference:** clicking a badge inserts `image N` at the caret of the prompt (replacing any selected text, adding a space where needed) and returns focus to the prompt. With an empty prompt, the placeholder suggests: *"Refer to images by number, e.g. put the dog from image 1 into the scene from image 2."*
- **The shape control** (refinement R1): with Size on Auto, a selector under the tray reads **"Result follows image [3 ▾]"**. It defaults to the last image that is not a mask (the pipeline's own rule) and can be set to any image. Choosing another image reorders nothing: the studio computes an explicit width and height from that image's aspect ratio at the chosen resolution (side lengths rounded to multiples of 32). If R1 is not accepted, this becomes the original always-visible note, "The result takes its shape from image N (the last one)", and only reordering changes it.
- **The cap:** the Add tile reads "3 of 4" and is disabled at the cap. Dropping more files than fit adds the first ones that fit and says how many were skipped. A file that fails validation is rejected **on its own**, with the reason; the others are kept.
- **Uploading starts as soon as a file is added** (staged on the server), so Generate is quick. Generate is disabled while uploads are running and while the tray is empty ("Add at least one image to edit.").
- **Not persisted:** the tray is kept while the page is open, including when you switch modes, but not across a reload (uploads are cheap to redo, and this avoids pointing at files the server has since cleaned up). The prompt draft is still kept, as in v1.

**Options in Edit mode**

- **Size:** Auto (default; shows "follows image N") or any preset or custom size (§6). A fixed size overrides the shape only.
- **Resolution: 1K | 2K** (new, Edit only, default 1K; decision #28). It sets `output_resolution`, which sizes the output *and every input* (§21.2 point 3). Help text: "1K is about 1 megapixel and quicker. 2K is about 4 and costs far more with several images."
- **Cost hint:** next to the Resolution control, a short line grows with `images × (resolution ÷ 1024)²` ("1 unit", "4 units", "16 units") and turns into a warning above a threshold ("This edit is heavy: expect a long run, or running out of memory"). The thresholds are set from Spark measurements (M5c, §21.11), not guessed now.
- Steps, seed, guidance, negative prompt and images per click behave as in Generate.
- **Transparent** (refinement R2): offered in Edit as well, wrapping the prompt in the recommended format. An input with an alpha channel is always kept as is, and the result card reports whether the output has alpha, as it does today. Whether the wrapper is needed when the input is already transparent is [unconfirmed]: the toggle is off by default, a transparent input shows a hint, and the Spark test (§21.11, M5c) settles it. A prompt starter, **"Extract the subject"**, covers the post's photo-to-RGBA example.

**Run card for an Edit run**

- A strip of the **numbered source thumbnails** (1 … N, in order) before the result images; each opens in the lightbox, which pages through the sources and then the results. The prompt's "image 2" can thus be matched to a picture.
- The meta line reads like "Edit · 3 images · 2048×1152 · 1K · 40 steps · seed 42".
- **Reuse** restores the prompt, the options **and all the inputs in order**, copied into a fresh tray. If an input file is gone, the slot is named instead of silently dropped. **Retry** resubmits the same inputs.
- **Edit this** on a result adds it to the tray (it becomes the last image unless you reorder, and the shape note says so).

### 21.5 Local edits (milestone M5d)

Qwen documents three ways to mark where an edit goes ([Qwen], §21.2), and the pipeline has no mask parameter [verified], so each is done **to the pixels, in the browser, before upload**:

- **Circles and marks:** an editor opened from a tray thumbnail, with an ellipse tool, a freehand brush, undo and clear, and **named colours: red, blue, green and white first** (Qwen's own examples), then yellow and black. The legend shows each colour's name, so the prompt can say what Qwen's example says: *"change the hair in the red circle to black"*. **Done** bakes the marks into a **derived PNG** that takes the thumbnail's place (same number); the original is kept so the marks can be removed.
- **Painted annotation:** the same brush in white ("the area marked in white").
- **Mask:** the original image plus a separate black-and-white mask, as **two inputs**. The studio flags the mask as a mask for display only (the model just sees one more numbered image). **The polarity is [unconfirmed]**: Qwen does not say which colour means "edit here". White is the working assumption (it is how the painted-annotation example marks the place); M5c tests both ways on the Spark *before* M5d is built. The editor can paint a mask directly (white where you brush, black elsewhere) and export it at the original image's size.
- **Both count towards the cap** and are stored with the run (the original and the marked or mask version), so the card shows exactly what was sent.
- **The shape trap goes away with R1:** the shape selector never offers a mask as the source and its default skips masks. Without R1, a mask placed last would set the shape, and the UI would warn and offer **Move mask earlier**.
- **Out of scope:** layers, selection tools, non-destructive history.
- **To confirm on the Spark:** mask polarity, whether the mask must match the original's size, and the best prompt wording for each of the three ways.

### 21.6 API changes (supersede the rows in §7)

| Method & path | Change |
|---|---|
| `POST /api/uploads` | Unchanged per file (multipart), called once per image, in parallel. Returns `upload_id`, `width`, `height`, `has_alpha` and a thumbnail URL. 413 / 415 / 422 per file as in §10 |
| `DELETE /api/uploads/{id}` | **New.** Removes a staged upload (the ✕ before submitting) |
| `POST /api/runs` | `input_image` is replaced by **`input_images`: an ordered list, 1 … cap, of `{upload_id}` or `{image_id}`** (a past result or input). Edit-mode options add **`resolution`** (1024 or 2048, default 1024); `width`/`height` null = Auto, plus **`shape_from`** (1-based index of the image the result follows, refinement R1; default the last non-mask image) and **`transparent`**, which is now allowed in Edit (R2). Each list item is validated on its own and errors name the position: `input_images[2]` |
| `GET /api/capabilities` | Adds `limits.input_images {min, max}`, `limits.resolutions [1024, 2048]`, and `supports.multi_image`. The start-up check cannot see inside a loaded pipeline, so `multi_image` is assumed wherever `edit` is, and confirmed by the real-hardware test (§21.10). If the pipeline rejects a list, the run fails with a clear message |

The singular `input_image` of §7 was never implemented, so nothing breaks by replacing it.

### 21.7 Data model (schema version 2)

- **New table `run_inputs(run_id, position, image_id, role)`**, primary key `(run_id, position)`; `role` is `reference`, `marked` or `mask` (display only, §21.5).
- **A run owns its inputs** (decision #18: kept with the run, deleted with it). When an input is a past result or a past input (*Edit this*, Reuse), the file is **copied** into the new run's own input rows, never shared. Deleting or expiring the original run therefore cannot break another run. The cost is disk: up to cap × 20 MB per run at worst, typically a few MB per image.
- The v1 column `runs.input_image_id` was never written by any shipped feature. The migration (`schema_version` 1 → 2) is additive: it creates `run_inputs` and leaves that column alone.
- `options_json` also records `resolution` and the ordered roles, so Reuse and Retry stay faithful.
- **Staged uploads** live in their own folder and are deleted if no run claims them within **24 hours** (`STUDIO_UPLOAD_TTL_HOURS`), at start-up and hourly.
- **Expiry and delete take a run's inputs with its outputs** (M6, §21.11): a pinned run keeps both; staged uploads follow the 24-hour rule above. Because a run owns copies of its inputs, an input copied into a newer run survives the expiry of its source.
- Uploads are **re-encoded to PNG without flattening alpha** (§21.2 point 5). The other upload rules (§11) are unchanged: PNG/JPEG/WebP only, decoded and verified, 20 MB and 16 MP per image.

### 21.8 Worker and pipeline

- `ImageJob.input_path` (one) becomes **`input_paths` (a list, in order)**, plus `resolution`.
- The worker loads each file **without flattening**, calls the pipeline with `image=[…]` in order and `output_resolution=resolution`, and passes `height`/`width` only when Size is a preset or custom size, or when `shape_from` names an image other than the pipeline's default (the studio then computes them from that image with the pipeline's own arithmetic); plain Auto leaves them unset. Seeds, batches, progress and cancellation behave exactly as in Generate.
- **Memory:** `STUDIO_MIN_FREE_GB` still guards only the model load. A big edit (many images at 2K) can still run out of memory *during* the run. The existing out-of-memory message gains an Edit-specific hint: "use 1K or fewer images".
- **The fake pipeline** (§15) learns the same list: it draws a small numbered strip of the inputs onto the result, so the tests can check order, count and the rule that the last image sets the shape, without a GPU.

### 21.9 Configuration (additions to §13)

| Variable | Default | Meaning |
|---|---|---|
| `STUDIO_MAX_INPUT_IMAGES` | `4` | Images per edit; 1–10 (decision #26) |
| `STUDIO_UPLOAD_TTL_HOURS` | `24` | How long an unclaimed staged upload is kept |

### 21.10 Acceptance criteria for v2 (continue §16)

19. Up to the cap, images can be added by all four methods (several files in one pick, several in one drop, paste, Edit this), each gets its number badge, and at the cap further adds are refused with a message, never dropped silently.
20. Reordering renumbers the badges, and the order submitted is the order shown (checked in the stored `run_inputs`).
21. With Size on Auto the result's shape follows the chosen image (by default the last one that is not a mask; selectable, with no reordering, if R1 is accepted), the page says which, and an explicit size overrides it.
22. Clicking a badge inserts "image N" at the caret.
23. An Edit card shows every source in order. Reuse restores prompt, options and all inputs in order; Retry resubmits the same inputs; a missing input is named.
24. Deleting the run an input came from does not break another run that used it.
25. A PNG input with transparency is stored and sent with its alpha intact. With Transparent on (R2) the prompt is wrapped in the recommended format and the card says whether alpha came back.
26. A bad file in a multi-file add is rejected alone; an expired or missing upload at submit gives a 422 naming its position; over-cap is refused by the page and by the server.
27. 1K and 2K set `output_resolution`; Auto at 1K gives about 1 MP, rounded to multiples of 32.
28. **On the Spark:** a 2-image and a 4-image edit complete at 1K, and one at 2K; memory and times are recorded; a prompt that refers to "image 1" and "image 2" is followed by the result; the cost-hint thresholds and the default cap are set from the measurements.
29. *(M5d)* A circle or brush mark baked into an image changes only that region; the card shows original and marked.
30. *(M5d)* A mask image works with the polarity found in M5c; a mask never sets the result's shape.
31. *(M6)* Cancelling a running edit stops it promptly (between steps if the pipeline allows it, otherwise between images), keeps the images already finished, marks the run canceled, and leaves the worker ready for the next job. A queued job is canceled at once.
32. *(M6)* Expiry removes a run's inputs together with its outputs; a pinned (Kept) run keeps both; an input copied into a newer run survives its source's expiry; queued and running runs are never expired.

### 21.11 Build order for v2 (replaces M5 in §17)

| Milestone | Delivers | Done when |
|---|---|---|
| M6 | Cancel, Keep and auto-expiry (details below). The v1 plan's remaining housekeeping, now part of v2 (decision #29) | Criteria 9 and 12 of §16, and 31–32 |
| M5a | Backend: multi-file uploads and staging, the cleanup job, `run_inputs` and the schema migration, `POST /api/runs` with `input_images`, worker and fake pipeline pass a list | API tests; fake edits show all sources in order |
| M5b | The page: tray, the four inputs, badges and insert, reorder, cap, shape note, Resolution control with cost hint, run-card sources, Reuse, Retry, Edit this | Criteria 19–27 in Playwright |
| M5c | The Spark test for edits (checklist supplied): 2- and 4-image edits at 1K, one at 2K, an alpha input; prompts that refer to "image 1" and "image 2"; a transparent edit and a subject extraction; **a mask, tried with both polarities and sizes**; memory and time recorded; cap and cost-hint thresholds set | Criterion 28, and the [unconfirmed] items of §21.2 settled |
| M5d | Local edits: mark-up editor and mask (§21.5), after M5c has settled the mask convention | Criteria 29–30 |
| M5e | *Only if R3 is accepted:* the "Improve prompt" step (§21.12 item 3) | Its own criteria, written when decided |

**Proposed order: M6, then M5a, M5b, M5c, M5d (and M5e if R3 is accepted).** Reasons: M6 is the smallest piece and independent of the editing design; it closes a gap you feel today (no way to stop a long run), and cancel will matter even more for multi-image edits and before the heavy edits M5c measures. Its one point of contact with editing, expiry removing a run's inputs, is handled in M5a, which has to extend delete to inputs anyway. Say so if you would rather have another order.

Each is its own pull request into `main` (never stacked), and I stop after each for your review (decision #22). M8, the Spark smoke test together, comes last.

**M6: what is built, and what remains**

- **Already built:** the pending cap with a clear 429 message (M1), delete with confirmation, which refuses while a run is running (M1), and the failure handling: out of memory, load failures, crashes and the memory pre-flight, each with an actionable message (M1–M2).
- **Cancel.** `POST /api/runs/{id}/cancel`. A queued job becomes canceled at once. A running job stops **between steps**: the pipeline has a per-step callback and an interrupt flag [verified], so the worker can stop mid-image; finished images of the batch are kept and the run shows "2 of 4 completed". The worker stays loaded for the next job. UI: a Cancel button on queued and running cards; stopping a running job asks first ("Finished images are kept").
- **Keep.** `PATCH /api/runs/{id}` with `{pinned: bool}`, a **Keep** button and a small badge on kept cards. Kept runs are exempt from expiry. (The database column already exists.)
- **Auto-expiry.** A janitor deletes runs older than `STUDIO_RETENTION_DAYS` (default 30; 0 turns it off) with all their files, at start-up and then daily; never queued, running or kept runs. A card shows **"expires in N days"** when fewer than 7 remain, so nothing you wanted vanishes silently (§5.6). The setting is read today but nothing acts on it; `.env.example` will lose its "not active yet" note.
- **To check on the Spark:** how quickly a cancel takes effect mid-step at 2K.

### 21.12 Open items for v2, and ideas parked

**Open**

1. **Decide the three refinements of §21.3:** R1 (the "follows image N" selector), R2 (Transparent in Edit) and R3 (the optional prompt rewriter). R1 and R2 are small and I recommend both.
2. **Measure on the Spark** (M5c): time and memory for 1, 2 and 4 images at 1K and 2K, next to Hermes. This sets the cap's default, the cost-hint thresholds and any size limits for edits.
3. **The prompt rewriter (R3).** It is official and recommended by Qwen, but it is a separate **9B vision-language model** (about 18 GB in bf16 by the usual arithmetic, not stated by Qwen) next to a main model whose footprint is still an estimate (the studio starts from 40 GB, to be measured, §9a), on a Spark that Hermes shares. Open questions: load it only when "Improve prompt" is clicked and unload it again, in its own worker process, as the main model is (decision #13)? Show the rewritten prompt for you to edit and approve, never apply it silently? Honour its `wh_ratio` and `ratio_follow` suggestions? What licence do the two rewriter checkpoints carry (not read yet)? Time to rewrite a prompt on the Spark?
4. **Settle the [unconfirmed] items** (§21.2) on the Spark: referring to images by number, and the mask conventions.
5. **Confirm the order of work** (§21.11): M6 is part of v2 (decision #29); I propose it first, then M5a–M5d.
6. **The licence** (§18 item 2) now has a definite answer to act on: non-commercial, research or evaluation only. Whether your use is covered is your call, or a question for Qwen.

**Parked (not in v2, and not scheduled)**

- **Prompt starters** for the jobs Qwen itself shows: virtual try-on (a person, clothes, shoes, a bag, a hat), a group portrait from separate portraits, a room from furniture images, subject extraction to a transparent layer, a panorama from a selfie, an infographic from a photograph, a storyboard from a three-view character sheet. A small menu in Edit mode; cheap and now well grounded; not scheduled.
- **Sequential editing** (successive local edits assembled into an animation, as in Qwen's capybara example) is already served by "Edit this" chaining; no extra feature.
- Upscaling, LoRAs (the pipeline has a LoRA loader) and choosing between models stay out, as in §2.
