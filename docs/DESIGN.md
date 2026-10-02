# Qwen-Image Web Studio — Design Specification (round 5 — design approved, awaiting your go to build)

Living document. **Nothing here is built yet.**

Status labels: **DECIDED** = you chose it, or explicitly delegated it. **PROPOSED** = an implementation detail that you chose not to review line by line; I will go with it unless you object, and you can challenge any of it at any time. **OPEN** = needs an answer.

## 1. Purpose

A containerised web app on the DGX Spark that generates and edits images with Qwen-Image-2.1 from a browser, and keeps a browsable history of every run (image + prompt + settings) so past prompts can be reviewed and tweaked.

## 2. Version 1 scope

**DECIDED — in scope**
- Text-to-image
- Image editing from an uploaded image
- Transparent (RGBA) toggle
- Batches: N images per click
- History of past runs with "reuse this prompt and settings"
- Light and dark themes
- An **Options** button beside the prompt, holding every setting for the run, saved in the browser for next time

**PROPOSED — out of scope for v1**
- User accounts / login (an optional shared-token hook is reserved, §11)
- Choosing between multiple models
- Masks / inpainting, upscaling, LoRAs
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

**Capabilities without a loaded model:** the API process can't inspect a pipeline that isn't loaded, so at start-up it runs a short GPU-free probe subprocess that imports `diffusers` and inspects `QwenImage21Pipeline.__call__` (the same idea as the CLI's early signature check). The result is cached. If the import fails the state is `unavailable` with the reason.

**Download filenames:** `generate_halloween-town-dawn_2048x2048_s42_20260930-141502.png`, using the same scheme as the CLI (`edit_` runs include the source's name). PNG text metadata carries prompt, seed, steps, model.

## 8. Data model and storage (PROPOSED)

SQLite in WAL mode; the API process is the only writer.

**`runs`** (one per click): `id`, `created_at`, `started_at`, `finished_at`, `status` (queued / running / done / failed / canceled), `mode`, `prompt`, `effective_prompt` (after the RGBA wrapper), `negative_prompt`, `transparent`, `width`, `height` (null = auto), `steps`, `cfg_scale`, `seed` (of image 0), `num_images`, `model_id`, `input_image_id`, `error_message`, `error_hint`, `pinned`, `options_json` (exact snapshot, so Reuse/Retry are faithful).

**`images`**: `id`, `run_id`, `idx`, `seed`, `width`, `height`, `has_alpha`, `bytes`, `path`, `thumb_path`, `created_at`. Uploaded reference images are rows here too, flagged `kind = input` (this is what lets "Edit this" and uploads share one code path).

**Files** under the data volume: `images/<run>/<idx>.png`, `thumbs/<run>/<idx>.webp`, `inputs/<id>.png` (uploads are re-encoded to PNG), `studio.sqlite`. All file access is by database id; client-supplied filenames are never used in paths.

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

## 17. Build order (PROPOSED)

| Milestone | Delivers | Done when |
|---|---|---|
| M1 | Backend skeleton, database, API, **fake pipeline**, in-memory queue | API tests pass; I can create runs and watch fake images appear |
| M2 | Real worker process + pipeline, idle unload, capability probe | Fake path still green; real path only checkable on the Spark |
| M3 | Front-end shell, theming, prompt bar, Options drawer, options persistence | Criteria 2, 3, 15, 16 pass in Playwright |
| M4 | Timeline, run cards, SSE live updates, Reuse, download, delete | Criteria 4, 5, 6, 14 pass |
| M5 | Edit mode: uploads, all four inputs | Criteria 7, 13 pass |
| M6 | Queue cap, cancel, retention, pin, failure handling | Criteria 8, 9, 11, 12, 18 pass |
| M7 | Containerfile, compose, docs | Image builds; criterion 17 on the Spark |
| M8 | Spark smoke test with you | Criteria 1, 10, 17 and the real-hardware checklist |

Each milestone is committed separately. **After each milestone I stop, report what works and what the tests showed, and wait for your go-ahead (decision #22).** Each milestone is pushed to `ai-image-studio` so you can review the diff on GitHub.

**Batches (decided 2026-10-02):** milestones are grouped into batches, **one pull request per batch**, so you can test something real sooner. Batch 2 is **M2 + M3 + M4 + M7** (the real model, the web page and the container); M7 moved ahead of M5 and M6 so the studio can run on the Spark before Edit mode and the queue extras exist. M5, M6 and M8 follow.

**Progress:**

- **M1** (2026-10-01, branch `m1-backend`): backend, database, API, queue, worker process and fake pipeline.
- **Batch 2** (2026-10-02, branch `batch2-real-model-ui`):
  - **M2:** the real `QwenImage21Pipeline` (checked against the diffusers source, not guessed: sizes must be multiples of 32, output can be RGBA, the per-step callback and `true_cfg_scale` exist); a start-up capability check; idle unload; the memory pre-flight from `/proc/meminfo`; errors translated into actionable messages.
  - **M3–M4:** the web page as specified in §5, with live updates. The browser tests found and fixed real bugs: a run that failed within milliseconds could show "Queued" forever (the live stream now opens with a consistent snapshot and older copies of a run never replace newer ones, §7), images requested while their run was being deleted gave a 500, and keyboard focus was lost when dialogs closed.
  - **M7:** `Dockerfile`, `compose.yaml`, `.env.example`, `docs/SPARK_TEST.md`.
  - Tests: backend 120 (pytest), front end 34 (Vitest) and 10 in the browser (Playwright; 50/50 over five repeats).
  - **Not verified, and not verifiable from my sandbox:** the image build (no Docker daemon) and anything on the real GPU. Checked instead: hadolint, `docker compose config`, dependency resolution for linux/arm64 + Python 3.12 with NVIDIA's torch held fixed, and the build-time check script in every branch. The Spark checklist covers the rest.
- Left for later: uploads and Edit mode (M5); cancel, pin and retention (M6; `STUDIO_RETENTION_DAYS` is read but nothing expires yet); the Spark smoke test with you (M8).

## 18. Open items and facts to verify

1. **Hermes and memory** — per NVIDIA's playbook, Hermes Agent uses a local LLM served by vLLM in a Docker container. Whether yours does, which model, and how much of the 128 GB it claims is unknown. This sets the memory budget (§9a). *Please run the commands in my message.*
2. **Model license** — third-party summaries say Qwen-Image-2.1 is non-commercial or research-only. Not verified against the official license. Check before other people use this for workshop material.
3. **Multiple reference images for editing** — third-party pages say up to 10; the model card snippet shows one. Verify on the model card; it changes the Edit UI (several thumbnails, ordering).
4. **Pipeline arguments** — resolved in M2 from the source at the pinned commit: `negative_prompt`, `true_cfg_scale` (default 1.0, i.e. no guidance; the negative prompt only matters above 1), `callback_on_step_end` and `_interrupt` exist. The start-up check reads the signature anyway, and the UI hides what isn't supported.
5. **`diffusers` commit to pin** — resolved: `578c9b2c6636ab2424a0e56186268b83623656b2` (2026-10-01), in `backend/requirements-container.txt`.
6. **Base image / PyTorch build** — NGC `nvcr.io/nvidia/pytorch` 25.10 or later is reported to support the GB10 (`sm_121`). The Dockerfile defaults to `25.10-py3` (`NGC_TAG` changes it); confirm or move to a newer tag after the first build on the Spark.
7. **Size and memory limits** — max pixels (4.5 MP) and the 8-image cap are guesses until measured on the Spark next to Hermes.
8. **GitHub repository** — resolved. `NoSQLKnowHow/ai-image-studio` (private) was created by you and attached to my session with push access. Earlier, the GitHub integration was refused (HTTP 403) when creating repositories and when pushing to `LiveLabs-Image-Dev`; that repository is deliberately not used for this project.
9. **Memory budget** — the model's real footprint (transformer, text encoder, VAE, activations at 2K) next to Hermes' LLM server; sets `STUDIO_MIN_FREE_GB` and the size and batch caps.
10. **NVIDIA's pages were unreachable** — docs.nvidia.com and build.nvidia.com are blocked from my sandbox, so §9a and §12 rest on search summaries and community posts. Before building, compare them with the current Container Runtime, NGC and Hermes playbook pages.

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

## 20. Approval record

- **Round 5 (2026-09-30):** you reviewed the key proposals and confirmed: separate worker process (#13), starting defaults and limits (#20), security approach (#21), and review after every milestone (#22). All other PROPOSED items are implementation details left to my judgement (see the status labels at the top).
- **2026-10-01:** you gave the go for M1 ("Go, start M1.").
- **2026-10-02:** you asked for the next sections so you could test for real, chose **real model + container + web UI** as the next batch, and **one pull request per batch** (§17).
- **Still pending from you (not blocking M1–M7):** the `docker ps` / `docker stats` / `free -h` / `nvidia-smi` output from the Spark (sets the memory budget, §18 items 1 and 9).
- **Repository (2026-09-30):** you asked that nothing for this project be written to `LiveLabs-Image-Dev` and that it get its own repository. Decided: private, personal account; first called `dgx-spark-image-studio`, renamed `ai-image-studio` the same day. You created it on GitHub and it was attached to my session. The earlier commits (CLI script, design spec) were replayed into it with their messages intact and removed from the LiveLabs clone.
