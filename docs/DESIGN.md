# Qwen-Image Web Studio — Design Specification

Living document. **Last brought up to date 2026-10-09, for version 1.10** (the number the page shows in its title).

**How to read it.** §1–§20 are the version 1 specification and the design shared by everything since; they have been corrected so that what they say about the behaviour of the studio is true today, and where a later section changed or replaced something they say so. §21 specifies **version 2, editing with several images** (plus the run housekeeping of M6); §22–§24 specify the small releases **1.3 to 1.5** that were built before the editing page, the editing page itself (M5b) is **version 1.6**, §25 specifies **1.7**, loading the model ahead of time, §26 specifies **1.8 and 1.9**, music, and §27 specifies **1.10**, Make 4K. "Version 2" names a set of features, not a version number (decision #32). Where a later section differs from an earlier one, **the later one wins**.

Status labels: **DECIDED** = you chose it, or explicitly delegated it. **PROPOSED** = an implementation detail that you chose not to review line by line; I will go with it unless you object, and you can challenge any of it at any time. **OPEN** = needs an answer. **BUILT** says something is in the code on `main`; it does not say it has run on the Spark (the "Spark" column below says that).

## Status at a glance (2026-10-03)

**Releases on `main`.** Every release is its own pull request into `main`, and I stop after each for your review (decision #22).

| Version | What it adds | Spec | Pull request | On the Spark |
|---|---|---|---|---|
| 1.0 | Generate, history, Options, themes, the container (M1–M4, M7) | §1–§20 | M1 branch, #2, #3 | Built and generating images (you, 2026-10-02) |
| 1.1 | Cancel, Keep and auto-expiry (M6); the version in the page title; idle unload 30 min | §21.11 | #10, #11 | Checks in `SPARK_TEST.md` §14 not yet reported back |
| 1.2 | The server side of editing with several images (M5a): uploads, `input_images`, schema 2 | §21.6–§21.8, §21.11 | #12 | §15 not yet reported back; **the real-GPU edit path has never run** |
| 1.3 | Scale picker, Draft, downloadable thumbnails | §22 | #13 | §16 not yet reported back |
| 1.4 | Regenerate larger on a run's card; a disabled Upscale placeholder | §23 | #14 | §17 not yet reported back |
| 1.5 | Regenerate larger in the image viewer, for that one image | §24 | #15 | §17(e) not yet reported back |
| 1.6 | **The editing page (M5b)**: Edit mode on, the reference tray, Resolution and its cost warning, edit run cards, Reuse and Retry for edits, Edit this | §21.4, §21.11 | #17 | §18 (the Spark test for edits, M5c) not yet run; **the real-GPU edit path has never run** |
| 1.7 | **Load model / Unload model** buttons beside the model pill, so the model can be loaded while you write the prompt | §25 | #18 | §19 of `SPARK_TEST.md` not yet reported back |
| 1.8 | **Music, the server side**: music runs, tracks and the audio route, schema 3, one model in memory at a time, the second `diffusers`, models loaded from the local cache; `scripts/minimax_music.py` makes a track from a terminal | §26, §26.11, §26.12 | #20, #22 | §20 of `SPARK_TEST.md` not yet reported back; **the real music model has never run** |
| 1.9 | **Music, the page**: the Images and Music tabs; the Music tab (the fields and the editable description, Add lyrics with the section tags, length, versions, seed and steps); track cards with a player; a model pill and a Load/Unload button that name and act on the open tab's model | §26.1, §26.13 | #21, #22 | the page's part of §20 of `SPARK_TEST.md` not yet reported back; **the real music model has never run** |
| 1.10 | **Make 4K** for any picture: a button on every picture the studio holds (results, including those made before 1.10, and an edit's source images) makes a 4K PNG beside the original (16:9 trimmed to exactly 3840×2160, any other shape enlarged to cover the frame with nothing cut off); **Upscale a picture…** does it for a file from the computer and returns it as a download; the disabled Upscale button is removed; `scripts/upscale_probe.py` probes whether an upscaler model runs on the Spark | §27, §27.9 | #23 | §21 of `SPARK_TEST.md` not yet reported back; **the upscaler probe has never run on the Spark** |

**Tests today (1.10):** backend 772 (pytest; 39 more run only where `torch` is installed: 22 for the real music pipeline, which also needs `diffusers` 0.40.0, and 17 for the upscaler probe's tiling), front end 276 (Vitest) and 122 (Playwright, in a real browser against the real server with the fake pipeline). Everything the studio does has been verified only against that fake pipeline, apart from what you ran yourself on the Spark; §15 says what the fake pipeline can and cannot show.

**What is left to build**, in the order proposed in §21.11:

| # | Milestone | What | Waiting on |
|---|---|---|---|
| 1 | **M5c** | The Spark test for edits (`SPARK_TEST.md` §18, on the page; §15 is the same through the API): whether "image 1" in a prompt works, alpha inputs, mask polarity and size, memory and time; sets the cap's default and the cost warning's threshold | You, on the Spark |
| 2 | **M5d** | Local edits: a mark-up editor and a mask editor (§21.5) | M5c (the mask convention) |
| 3 | **M5e** | "Improve prompt", Qwen's official rewriter (§21.12 item 3) | **Your decision on R3**; only if accepted |
| 4 | **An upscaler model** for Make 4K (route B, §27.5) | Real added detail instead of a plain resize; replaces the Edit-mode Upscale of decision #37, which is dropped | Your run of `scripts/upscale_probe.py` on the Spark (`SPARK_TEST.md` §21 i), then a choice of model and its licence |
| 5 | **Music 1.8** | **Built and merged** (#20, #22; §26.12): the server side of the Music tab and the command-line example | Your run of `SPARK_TEST.md` §20 (the example script on the real model) |
| 6 | **Music 1.9** | **Built and merged** (#21, #22; §26.13): the Music tab, with the fields and preview, lyrics, tracks with a player, and Load/Unload per tab | Your run of `SPARK_TEST.md` §20, including the page |
| 7 | **M8** | The Spark smoke test together | Everything above |

**Open questions for you:** R3 (the prompt rewriter, §21.12 item 3); any veto on drafts jumping the queue (#34), the WebP thumbnail (#35) or raw-body uploads (§21.6); the Spark measurements listed in §18 and §21.12; and, as always, whether your use of the model's licence is covered (§18 item 2).

## 1. Purpose

A containerised web app on the DGX Spark that generates and edits images with Qwen-Image-2.1 from a browser, and keeps a browsable history of every run (image + prompt + settings) so past prompts can be reviewed and tweaked.

## 2. Version 1 scope

**DECIDED — in scope** (everything on this list is built in 1.0 except editing)
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
- Masks / inpainting (**now planned for version 2, §21.5**), LoRAs. **Upscaling**: **Make 4K** (§27, 1.10) resizes a 16:9 picture to 3840×2160, and an upscaler model is being probed (§27.5)
- LLM-based prompt rewriting (**an optional step is proposed and awaits your decision: R3, §21.12 item 3**)

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
| 15 | Idle timeout | **30 minutes**, env var (was 15; you raised it on 2026-10-02 because 15 was too short) | DECIDED |
| 16 | Container runtime and orchestration | **Docker + Compose.** You delegated this to NVIDIA's documented approach: Docker with the NVIDIA Container Toolkit is preinstalled on the Spark (§12) | DECIDED (delegated) |
| 17 | Model weights source | Downloaded on first use into a mounted Hugging Face cache volume | DECIDED |
| 18 | Uploaded source images | Kept with the run; deleted when the run expires or is deleted | DECIDED |
| 19 | Behaviour when too little memory is free (e.g. Hermes' LLM server is holding most of it) | Pre-flight memory check before loading the model (§9a). If short, **fail fast** with clear instructions; you fix it and click Retry. No waiting in the queue, and the studio never controls the LLM container | DECIDED |
| 20 | Starting defaults and limits | Size 2048×2048 (1:1), 40 steps, 1 image per click (max 8), queue cap 10, idle unload 30 min, runs expire after 30 days (§6, §13) | DECIDED |
| 21 | Security for v1 | As §11: no login; required custom header plus no CORS; upload validation; strict CSP; non-root container; no Docker socket; `STUDIO_TOKEN` reserved but off (setting it stops the server from starting, so it can't give a false sense of protection) | DECIDED |
| 22 | Review cadence | I stop after **every milestone** (M1–M8), report what works and what the tests showed, and wait for your go-ahead | DECIDED |
| 23 | Repository | A dedicated **private** GitHub repository, `NoSQLKnowHow/ai-image-studio` (renamed from the working name `dgx-spark-image-studio`), separate from `LiveLabs-Image-Dev`. Nothing for this project is written to `LiveLabs-Image-Dev` | DECIDED |
| 24 | Multi-image editing (v2) | One edit can use **several uploaded images with one prompt**, up to the cap (#26). Replaces the one-reference rule of §5.4 (§21) | DECIDED |
| 25 | Referring to images (v2) | Number badges (1, 2, 3 …) on the tray's images, matching the order the model sees; clicking a badge inserts "image N" at the prompt's cursor (§21.4) | DECIDED |
| 26 | Image cap (v2) | 4 by default, configurable up to 10 (`STUDIO_MAX_INPUT_IMAGES`); raised after measuring on the Spark | DECIDED |
| 27 | Local edits (v2) | Specified in v2, built after multi-image editing works (§21.5, M5d) | DECIDED |
| 28 | Edit output size (v2) | Auto (about 1 MP, shape from the last image) with a 1K / 2K choice, default 1K (§21.4) | DECIDED |
| 29 | M6 is part of v2 | The housekeeping the v1 plan left for M6 (**cancel** a queued or running job, **Keep** a run, **auto-expiry** with a warning) is built as part of version 2, not as a separate release (§21.11) | DECIDED |
| 30 | Result shape (v2) | With Size on Auto, a **"Result follows image [N]"** selector names the image the result's shape follows (default: the last one that is not a mask). Nothing is reordered (§21.4) | DECIDED |
| 31 | Transparent in Edit (v2) | **Offered in Edit as well as Generate**, wrapping the prompt in the recommended format (§21.4) | DECIDED |
| 32 | Version numbers | **major.minor, and each release bumps the minor** (your call, 2026-10-02). **How it turned out:** 1.0 was the first build on the Spark, M6 is 1.1, the editing backend (M5a) 1.2, the scale picker, drafts and thumbnails 1.3, Regenerate larger 1.4, and Regenerate larger in the viewer 1.5. The editing page (M5b) is 1.6 and loading the model ahead of time is 1.7. The spec's "version 2" names a set of features, not a version number: nothing is called 2.0 unless you say so. The number is set in one place, `studio.__version__`, and shown in the page title | DECIDED |
| 33 | Quick size (1.3) | A **scale picker, 100% / 75% / 50% / 25%**, on the prompt bar. It scales the **width and height** of the selected size (50% of 2048×2048 is 1024×1024: a quarter of the pixels, roughly four times faster), rounded to the multiples of 32 the model needs (§22.1) | DECIDED |
| 34 | Draft (1.3) | A **Draft button** makes a small, fast version of the prompt (512 px on the long side, 12 steps) to try the wording before a long run. Drafts jump ahead of waiting full-size runs (never the one running). The full-size image will look different, even with the same seed (§22.2) | DECIDED |
| 35 | Thumbnails (1.3) | Every image gets a small **thumbnail you can download** (WebP, 512 px on the long side, transparency kept), from the run card and from the viewer (§22.3) | DECIDED |
| 36 | Regenerate larger (1.4) | A finished run made **smaller than the size you had selected** (a draft, or a 25 / 50 / 75% run) gets a **Regenerate larger** button: the same prompt, seed and image count sent again at the **full size you had selected, with the steps you had selected**. The run remembers that size, so it works from the history. It will look different from the small image (§23.1) | DECIDED |
| 37 | Upscale (planned) | **The same picture, just bigger**, as a second button beside Regenerate larger. **Not built**: it needed the editing page (built in 1.6) and still needs a Spark test of whether the editing model can refine an image at 2K without changing it. The button is shown disabled until then (§23.2). **Superseded by #44** (§27.7): Edit mode cannot go past 2K, so the plan is dropped and the disabled button removed | DECIDED (the plan); **SUPERSEDED by #44** |
| 38 | Regenerate larger in the viewer (1.5) | The single-image viewer has the **same Regenerate larger button**. It enlarges **that image only**: a new job at the full size and steps, with **that image's own seed** and **one** image (§24.1). Its confirmation, or an error, shows **inside the viewer** (§24.2) | DECIDED (the request); details PROPOSED |
| 39 | Load the model ahead of time (1.7) | A **Load model button next to the model pill** in the header starts loading the model now, without a run, so you can work on the prompt while it loads. When the model is loaded and idle the same place offers **Unload model**, which gives the memory back at once. **No automatic warm-up** (nothing loads because you started typing): the button only (§25) | DECIDED (the request and the three choices); details PROPOSED |
| 40 | Music tab (built in 1.8 and 1.9) | A separate **Music** tab, beside Images, makes music with **MiniMax-Music3**. **Instrumental by default**; an **Add lyrics** switch reveals a lyrics box with the section tags (§26.1) | DECIDED (the request and your answers); details PROPOSED; the server side is built (1.8) and so is the page (1.9) |
| 41 | One model in memory at a time | The music model and the image model are **never loaded together**: starting one unloads the other first (never during a run), so the Spark does not hold both next to Hermes (§26.4) | DECIDED; built in 1.8 |
| 42 | Describing the music | **Fields** (genre, mood, tempo, key, instruments and arrangement, and a voice description when lyrics are on) build the structured description the model's card recommends, shown in a preview you can edit (§26.2) | DECIDED; **NOT built** |
| 43 | Models come from the local cache (1.8) | After the first download **neither model contacts Hugging Face again**: with `STUDIO_LOCAL_FILES_ONLY=auto` (the new default) each model is loaded from the cache, and the network is used only if files are missing from it. `true` never goes online, `false` is the old behaviour (a check of the hub on every load). Applies to the image model as well (§26.11) | DECIDED (your request); details PROPOSED; built in 1.8 |
| 44 | Make 4K (1.10) | You asked for the final output to be **at least 4K at 16:9**. A **Make 4K** button on every picture that is 16:9 (within 2%) makes a **3840×2160 PNG** from it, **beside the original**: trimmed to exactly 16:9 and enlarged with a standard resize (route A; **extended to any picture by #45**). The model cannot make 3840×2160 itself (2160 is not a multiple of 32). A **dedicated upscaler model** (route B) follows once a Spark probe shows one runs there; making 4K **directly** with the model (route C) is an experiment, not a feature. On demand, not automatic; the original stays (§27) | DECIDED (your requirement and the three choices: on demand, trim not stretch, keep both); details PROPOSED; route A built in 1.10 |
| 45 | Make 4K for any picture (1.10) | You asked whether existing pictures and the one you are viewing can be upscaled, and to add whatever could not. **Cover 4K, without trimming** for every shape that is not 16:9: enlarged until it fills 3840×2160 (2160×3840 upright), so a 2048×2048 becomes 3840×3840; offered up to a 2× enlargement and 20 MP. Available for **any picture in the history** (before and after 1.10), **an edit's source images**, and **a picture from your computer** (an **Upscale a picture…** button: you choose a file and get the 4K PNG back as a download; nothing is stored or added to the history) (§27, §27.9) | DECIDED (your request and your three choices); details PROPOSED; built in 1.10 |

**Which decisions are built** (the Status column above says who decided; this says what is in the code):

| Decisions | Built? |
|---|---|
| #1–#23 | Yes in 1.0, with these exceptions. **#7 and #8** were completed in 1.1 (cancel, Keep, auto-expiry; the queue cap and manual delete were already there). **#10** and **#12**: the Generate \| Edit switch is on and the four ways to add an image work since 1.6 (M5b). **#18**: inputs are kept with the run on the server since 1.2, and uploaded from the page since 1.6. #3 and #15 (idle unload, now 30 minutes) are checked only against the fake pipeline unless you tried them on the Spark. |
| #24, #25, #26, #28, #30, #31 | **Yes**: the server side in 1.2 (several inputs per run, the cap, `resolution`, `shape_from`, Transparent in Edit) and the page in 1.6 (M5b: the tray, number badges and click-to-insert, the cap, Resolution, "Result follows image N", Transparent in Edit). #26's default of 4 is still to be raised or lowered by the Spark test (M5c). |
| #27 | Not built (M5d). |
| #29 | Yes (1.1). |
| #32 | In effect. |
| #33, #34, #35 | Yes (1.3). |
| #36 | Yes (1.4). |
| #37 | **Superseded by #44.** A disabled button marked the place (1.4); 1.10 removes it. |
| #44 | **Route A: yes** (1.10). Route B (an upscaler model) waits for the Spark probe; route C is an experiment. |
| #45 | **Yes** (1.10), together with #44's route A. |
| #38 | Yes (1.5). |
| #39 | Yes (1.7). |
| #43 | **Yes** (1.8). |
| #41 | **Yes** (1.8). |
| #40, #42 | **Built:** the server side in 1.8 (music runs, tracks, the audio route, the capabilities) and the page in 1.9 (the tab, the fields and the preview). Neither has run on the real model. |

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
┌─────────────────────────────────────────────────────────────┐
│ ◐ AI Image Studio v1.5              [model: ready] [☾/☀]    │  header
├─────────────────────────────────────────────────────────────┤
│ [ Generate | Edit ]                                          │  mode toggle (Edit adds a tray of pictures)
│ [ prompt (multi-line, grows) ............................ ]  │  prompt bar
│ Scale [100%][75%][50%][25%]  [ Options ⚙ ] [ Draft ⚡ ] [ Generate ▶ ] │
├─────────────────────────────────────────────────────────────┤
│ QUEUE: 1 running, 2 waiting                                  │  only when non-empty
├─────────────────────────────────────────────────────────────┤
│ ┌──────┐ [Done] [Draft]            just now                  │  run card
│ │ img  │ "Halloween town at dawn…"                           │
│ └──────┘ 1024×1024 · 40 steps · seed 42 · 31s                │
│          [Reuse] [Regenerate larger] [Download] [Make 4K]     │
│          [Thumbnail] [Copy prompt] [Keep] [Edit this] [🗑]     │
└─────────────────────────────────────────────────────────────┘
        Make 4K: on a card with one picture that is 16:9 (§27)
```

- The title shows the **version** (`AI Image Studio v1.5`), as does the browser tab. It comes from the server (`studio.__version__`, the one place it is set), so it tells you which build is actually running; on a phone it sits under the title. `index.html` is sent with `Cache-Control: no-cache` (revalidated on every load) and the hashed files under `/assets/` are cached for good, so a rebuilt page appears on the next reload.
- Header shows a **model state pill**: unloaded / loading / ready (with idle countdown) / busy / error / unavailable. `unavailable` means the pipeline can't be imported (e.g. `diffusers` too old); the pill links to the reason and the fix. (PROPOSED) **Since 1.7** a **Load model** or **Unload model** button sits beside it (§25.1), and on a phone the pill says its state in one short word (Unloaded, Loading…, Ready, Working, Problem, Unavailable) because the button takes some of its room.
- Ctrl/Cmd+Enter submits; **Ctrl/Cmd+Shift+Enter** sends a **Draft** (§22.2). The Generate button stays usable while jobs are queued, up to the cap. (PROPOSED)
- The **Scale** buttons (100 / 75 / 50 / 25%) and the **Draft** button sit on the prompt bar beside Options and Generate (§22). On a phone the word "Scale" gives way to the buttons and the size they give is shown beside them.
- Single column so it works down to phone width. (PROPOSED)
- The prompt text is also kept in the browser as an unsent draft. (PROPOSED)

### 5.2 Themes — PROPOSED
- Follows the OS setting (`prefers-color-scheme`) by default; the header button cycles system → light → dark; choice saved in `localStorage`.
- Theme applied before first paint (no flash of the wrong theme).
- Colours are design tokens (CSS variables), at least WCAG AA contrast in both themes, visible keyboard focus, respects reduced-motion.

### 5.3 Options drawer — DECIDED form, PROPOSED details
- Opens from the Options button: slide-over from the right on desktop (page stays visible), bottom sheet on phones. Esc or ✕ closes; focus is trapped while open and returned to the button on close.
- Sections: Size · Sampling (steps, seed, guidance) · Output (images per click, transparent) · Advanced (negative prompt). **Reset to defaults** at the bottom. The Scale buttons are **not** in the drawer; they live on the prompt bar (§22.1), and the Options button's summary shows the size they give ("1024×1024 (50%) · 40 steps …").
- Changes apply immediately to the *next* run and are saved to `localStorage` as you type (debounced). No "Save" button.
- Controls the loaded pipeline does not support are hidden, not disabled (see §7 `GET /api/capabilities`). Their stored values are kept in case support appears later.

### 5.4 Edit mode — DECIDED inputs, PROPOSED details

> **Version 1 text; superseded by §21.4**, which allows several reference images (decision #24). The four input methods and the rest of the behaviour carry over as described there. **Built in 1.6 (milestone M5b): §21.4 describes it and §21.11 records what differs.** The Edit switch is enabled when the pipeline can edit, and the **Edit this** button works.

- A reference image can arrive four ways: choose-file button, drag-and-drop onto the prompt area, paste from the clipboard, or **Edit this** on any past result.
- Shows a thumbnail of the reference with a remove (✕) button. One reference image in v1 (see open item 3).
- In Edit mode, size defaults to **Auto — from input** (the pipeline sizes the result), matching the CLI behaviour. Choosing a size overrides it.
- **Transparent** was hidden in Edit mode in v1 (the model card documented it for text-to-image only); decision #31 offers it in Edit too (§21.4).

### 5.5 Run card — PROPOSED (built)
- One card per **click**. A batch of N shows N thumbnails in a small grid, sharing the prompt; each image records its own seed.
- **Badges:** the status (**Queued · #n**, **Generating**, **Stopping**, **Done**, **Failed**, **Canceled**), **Kept** when you pressed Keep, **Draft** for a draft (§22.2), **Edit** and **Transparent** where they apply, and how long ago it was made.
- Shows: the prompt (collapsed after a few lines, expandable), the negative prompt if used, and a meta line: the **size the run actually used** (a 50% run shows "1024×1024", not the percentage; the "(50%)" appears only in the Options summary), steps, seed(s), guidance, and how long it took. For an Edit run it also shows the numbered source thumbnails above the result, and its meta line starts with the number of images and gives the resolution ("3 images · 1024×1024 · 1K · 40 steps", §21.4). A card shows **"Will be deleted in N days. Press Keep to save it."** when fewer than 7 remain (§5.6).
- Running cards show progress: image i of N and, with the per-step callback, step k of T; otherwise a spinner with elapsed time. A canceled card says how many images finished and were kept.
- Failed cards show the error message, its hint and a **Retry** button (resubmits the stored options, a draft as a draft).
- Click an image → the **viewer** (lightbox): the full image, its seed and size, **Download**, **Make 4K** and then **Download 4K** for a 16:9 picture (§27), **Thumbnail** (§22.3) and, where the card has it, **Regenerate larger** for that one image (§24); the arrow keys and buttons page through a batch (and through an edit's source images and then its results).
- Actions (shown as they apply):
  - **Cancel** on a queued or running card (§5.5a); a running one asks first.
  - **Reuse**: loads the prompt and options into the bar and drawer, **Lock seed on**, and the run's size as a preset and scale where it is exactly one (§22.1); for a **draft** it loads only the prompt-side options and leaves your size, steps and seed (§22.2). It switches the mode toggle to the run's mode, and for an edit brings its images back into the tray, in order (§21.4).
  - **Regenerate larger** on a finished run made smaller than selected (§23); **Make 4K**, then **Download 4K**, on a run with one picture that is 16:9 (§27).
  - **Download** (one image) or **Download…** (opens the viewer to choose), **Thumbnail** (one image), **Copy prompt**.
  - **Keep** (exempt from auto-expiry; a toggle), **Edit this** (on a result with one image: it adds the picture to the tray and switches to Edit; for a batch, open the image in the viewer, which has it for any image, §21.4), **Delete** (with confirmation; refused while it is running).
- Cards update live in every open browser (server-sent events).

### 5.5a Queue — DECIDED, details PROPOSED
- One GPU worker, one job at a time; a job = one click (its N images run one after another).
- Pending cap 10 (env var). Beyond that, Generate is refused with a clear message (HTTP 429, shown inline).
- Queued jobs show position and can be canceled. Cancelling a running job stops it at its next step (built in M6: the worker stops from the pipeline's per-step callback), keeps the images already finished and leaves the model loaded.
- **Drafts jump ahead of waiting full-size runs**, never the running one, and keep their order among themselves; the positions shown follow that order (§22.2). Nothing else changes the order: everything else runs in the order it was queued.

### 5.6 Retention — DECIDED, details PROPOSED
- Runs older than N days (default 30, env var) are deleted with their image files, at startup and once a day. Pending and running runs are never expired, and neither is a run you pressed **Keep** on. "Older" counts from when the run was created, the date shown on its card. A card warns when fewer than 7 days remain (built in M6, §21.11).
- Because auto-expiry can silently discard something you wanted: **Keep** exempts a run, and the card warns when fewer than 7 days remain. **Built in 1.1 (M6, §21.11)** with the 7-day warning described above.

## 6. Options panel contents

Every value is saved in `localStorage` and restored on the next visit. Server-side validation enforces every limit again.

| Option | Applies to | Default | Limits / notes |
|---|---|---|---|
| Size | both | 1:1 = 2048×2048 (Edit: Auto — from input) | 7 model-card presets: 1:1 2048×2048, 4:3 2400×1792, 3:4 1792×2400, 3:2 2528×1696, 2:3 1696×2528, 16:9 2752×1536, 9:16 1536×2752; or Custom W×H: each side 256–4096, multiple of 32 (the pipeline only warns about a size off that grid and rounds it down, so the studio refuses one rather than make a different size from the one asked for; §27.1), total ≤ 4.5 MP (limits PROPOSED, to be tested on the Spark) |
| Steps | both | 40 | 1–100 |
| Seed | both | Random each run; Lock seed off | 0–4294967295 |
| Images per click | both | 1 | 1–8 |
| Negative prompt | both | empty | shown only if the pipeline supports it |
| Guidance strength | both | model default | sent as `true_cfg_scale`; 0.1–20; shown only if supported |
| Transparent (RGBA) | Generate (and Edit, decision #31) | off | wraps the prompt in the model card's recommended format; saves an alpha PNG |
| Scale | Generate (Edit with a fixed size) | 100% | 100 / 75 / 50 / 25% of the selected size's width **and** height, rounded to multiples of 32, no side under 256 (§22.1). On the prompt bar, not in the drawer; saved with the options |
| Resolution | Edit | 1K | 1K or 2K (1024 or 2048): sizes the result and every input (§21.4). Built (server in 1.2, page in 1.6) |

The prompt lives in the prompt bar, max 8000 characters (env var). Mode is the toggle above it; both are remembered in the browser.

**Persistence rules (PROPOSED):** stored under a versioned key (`studio.options.v1`). On load, each field is validated independently; an invalid or missing field falls back to its default, so a corrupt or old value can never break the page (options saved before 1.3 have no scale and read as 100%). If `localStorage` is unavailable (private window), the page works with in-memory options and shows a small notice.

**Not per-run (server configuration):** model id/path, idle timeout, CPU offload, queue cap, retention days, storage path, port, upload limits (§13).

## 7. API (PROPOSED; this table is the API as built in 1.10)

All under `/api`. JSON unless noted. Mutating requests require the header `X-Studio-Client: 1` (see §11).

| Method & path | Purpose | Notes |
|---|---|---|
| `GET /api/health` | Liveness for the container healthcheck | Always cheap; does not touch the GPU |
| `GET /api/status` | Model state, idle countdown, queue length, system-memory figures (§9a) | Also pushed over SSE |
| `GET /api/audio/{id}` | **1.8:** a music track's WAV, with HTTP range requests (an audio player needs them to seek); `?download=1` sends it as an attachment named `music_<genre-or-description-words>_<N>s_s<seed>_<YYYYmmdd-HHMMSS>.wav` (§26.3) | `404` for an unknown id or a missing file |
| `POST /api/model/load` | **1.7:** start loading the model now, without a run (§25.2). **1.8:** an optional `{"model": "image" \| "music"}` body picks the model (§26.3) | `202` started, `200` nothing to do; `409` `not_enough_memory` or `no_idle_time`; `503` `worker_failed` |
| `POST /api/model/unload` | **1.7:** unload the model now (§25.2). **1.8:** the optional body names the model to unload; without it, whichever is loaded | `200`; `409` `busy` while a run is running |
| `GET /api/capabilities` | Which options the pipeline supports, and all limits and defaults | `modes` (`edit` is listed when the pipeline can edit), `supports`, `aspect_ratios`, `defaults`, `limits` (prompt length, `input_images`, `draft` {long side, steps}, `resolutions`, `upload_mb`, `edit_warn_units`, steps, images, seed, guidance, size), `queue_cap`, `device`. See below |
| `POST /api/uploads` | Stage one reference image for an edit. **The file is the raw request body, not multipart** (§21.6) | Returns `upload_id`, `width`, `height`, `has_alpha`, `bytes`, `url`, `thumb_url`; 413 too large, 415 wrong type, 422 undecodable, 507 disk full |
| `DELETE /api/uploads/{id}` | Take back a staged upload no run has claimed | 204; 404 for anything else |
| `POST /api/runs` | Create a run | Body: `mode`, `prompt`, `options` (`width`, `height`, `steps`, `seed`, `num_images`, `negative_prompt`, `cfg_scale`, `transparent`; Generate only: `draft` §22.2, `full` §23.1; Edit only: `resolution`, `shape_from`), and for Edit `input_images`, an ordered list of `{upload_id}` or `{image_id}` (§21.6). 201 with the run; 422 field errors that name the field; 429 queue full; 507 disk full |
| `GET /api/runs?limit=&before=` | List runs, newest first | Cursor pagination; "load more" in the UI |
| `GET /api/runs/{id}` | One run with its images | |
| `POST /api/runs/{id}/cancel` | Cancel queued or running | `200` with the run for a queued run (canceled at once); `202` for a running one (it carries `canceling: true` and becomes `canceled` at the next step); `409` `run_finished` if it already finished; `404` |
| `PATCH /api/runs/{id}` | `{pinned: bool}` | The only mutable field; strict (a real boolean, nothing else, or `422`). Returns the run |
| `DELETE /api/runs/{id}` | Delete run and files | 409 if running (cancel first) |
| `GET /api/images/{id}` | Full PNG | `?download=1` sets a meaningful filename (below) |
| `GET /api/images/{id}/thumb` | WebP thumbnail | `?download=1` sends it as an attachment named like the image with `_thumb.webp`; outputs only (§22.3) |
| `POST /api/images/{id}/4k` | **1.10:** make the 4K copy of a result image: a 3840×2160 PNG beside the original (§27.3) | `201` with the updated run when it was made now, `200` when it was already there; `404` for an unknown image or one that is not a result; `422` `not_4k_eligible` (not 16:9, too small, already 4K: the reason is in `detail`) or `unreadable`; `507` `storage_full`, leaving no partial file |
| `GET /api/images/{id}/4k` | **1.10:** the 4K copy, once made; `?download=1` sends it as an attachment named like the image with `3840x2160` in it | `404` if it has not been made |
| `GET /api/events` | Server-sent events | `hello` (`{status, runs}`: the status plus the newest page of runs, read after the stream subscribed, so it is a consistent starting point), `run.created`, `run.updated` (full run), `run.progress` (step progress), `run.deleted`, `queue.updated` (positions), `worker.state`, `overflow`, `shutdown` (the server is stopping; the stream then ends). A `: ping` comment every 15 s keeps proxies from closing the stream. Every connection, first or reconnect, starts from its `hello`; the client never lets an older copy of a run (a late POST response, a stale page) replace a newer one, since runs only move forward (queued → running → finished) |

**Music (1.8):** `POST /api/runs` takes `mode: "music"`, `lyrics` and the options `duration`, `tracks`, `steps`, `seed` and `fields` (§26.3); a run's payload then has `tracks` (and `lyrics`) as an image run has `images`, and `/api/capabilities` has `modes` listing `music` when the music pipeline can run, `limits.music`, and `music` {`available`, `state`, `reason`, `hint`, `model`}. `worker` in the status and in `worker.state` events says which `model` (`image` or `music`) it holds.

**What a run looks like in the API:** `id`, `status`, `mode`, `prompt`, `effective_prompt`, `options` (the exact snapshot of what the run used: size, steps, seed and whether it was random, images, negative prompt, guidance, transparent, `resolution`, `shape_from`, the inputs' `roles`, `draft`, `full`), `model_id`, the three timestamps, `error` (`message`, `hint`), `pinned`, `expires_at` (worked out from the creation time and `STUDIO_RETENTION_DAYS`, null when the run is kept, still queued or running, or retention is off), `queue_position`, `progress`, `canceling`, `inputs` and `images`. Each image has `can_4k` (the server's rule for whether Make 4K is offered, §27.3) and `four_k` (its 4K copy, or null). The upload and run-creation calls for editing are specified in full in §21.6.

**Capabilities without a loaded model:** the API process can't inspect a pipeline that isn't loaded, so at start-up it runs a short GPU-free probe subprocess that imports `diffusers` and inspects `QwenImage21Pipeline.__call__` (the same idea as the CLI's early signature check). The result is cached. If the import fails the state is `unavailable` with the reason.

**Download filenames:** `generate_halloween-town-dawn_2048x2048_s42_20260930-141502.png`, using the same scheme as the CLI (`edit_` runs include the source's name). PNG text metadata carries prompt, seed, steps, model.

## 8. Data model and storage (PROPOSED)

SQLite in WAL mode; the API process is the only writer.

**`runs`** (one per click): `seq` (arrival order; the queue runs in this order, drafts first, §22.2), `id`, `created_at`, `started_at`, `finished_at`, `status` (queued / running / done / failed / canceled), `mode`, `prompt`, `effective_prompt` (after the RGBA wrapper), `negative_prompt`, `transparent`, `width`, `height` (null = auto), `steps`, `cfg_scale`, `seed` (of image 0), `num_images`, `model_id`, `input_image_id` (never written; kept for compatibility, §21.7), `error_message`, `error_hint`, `pinned`, `options_json` (exact snapshot, so Reuse/Retry are faithful; it also carries `draft`, `full`, `resolution`, `shape_from` and the inputs' roles, so those needed no columns). There is no `expires_at` column: it is worked out from `created_at`.

**Schema 3 (1.8, §26.5):** `runs.mode` also accepts `music` and `runs` has a `lyrics` column (the table is rebuilt by the migration, which keeps a copy of the old database as `studio.sqlite.before-schema-3`); **`tracks`** (`id`, `run_id`, `idx`, `seed`, `seconds`, `sample_rate`, `channels`, `bytes`, `path`, `created_at`) holds a music run's WAV files, which live under `<data>/audio/<run>/`.

**`images`**: `id`, `run_id`, `kind` (`output` or `input`), `idx`, `seed`, `width`, `height`, `has_alpha`, `bytes`, `path`, `thumb_path`, `created_at`. Uploaded reference images are rows here too, flagged `kind = input` (this is what lets "Edit this" and uploads share one code path); one with no `run_id` is a staged upload.

**`run_inputs`** (since schema 2, §21.7): the ordered inputs of an edit. **`meta`**: the schema version (now 2).

**Files** under the data volume: `images/<run>/<idx>.png`, `images/<run>/<idx>-4k.png` (a 4K copy, made on request, §27: no database row, it goes with the run), `thumbs/<run>/<idx>.webp`, `inputs/staged/<id>.png` (uploads waiting; re-encoded to PNG) and `inputs/<run>/<position>.png` (what a run owns), their thumbnails under `thumbs/staged/` and `thumbs/<run>/in-<position>.webp`, `studio.sqlite`, and once after the upgrade to schema 2 `studio.sqlite.before-schema-2` (§21.7). All file access is by database id; client-supplied filenames are never used in paths.

**Version 2** added the `run_inputs` table and a schema migration (1.2): §21.7.

**Restart behaviour:** queued runs persist and resume; a run that was `running` when the server stopped is marked failed ("interrupted by restart") and keeps any images already finished.

## 9. Worker and queue behaviour (PROPOSED)

- API ↔ worker: JSON lines over stdio. Commands: `load`, `run`, `cancel`, `shutdown`. Events: `hello`, `state`, `load_failed`, `run_started`, `progress`, `image_done`, `run_finished`, `run_failed`, `run_canceled`, `protocol_error`, `bye` (and `probe` for the start-up capability check). The worker reads commands in a thread of its own, so a `cancel` is heard while a run is under way (§21.11, M6).
- **Queue order:** drafts first, then everything else in arrival order, never displacing the run in progress (§22.2).
- The worker is started lazily when a job needs it. After the queue has been empty for the idle timeout, the API sends `shutdown` and waits for the process to exit; a job arriving mid-shutdown waits for the exit, then a fresh worker starts.
- **1.8:** the worker program holds one of two models, `--kind image` or `--kind music` (§26.4); the API keeps one process at a time and stops the loaded one, under the same lock, before starting the other. A music job is sent as `{run_id, mode, prompt, lyrics, duration, steps, seeds, model_id}`; each finished track is reported with `track_done`, and `progress` carries a `stage` (`compose`, `render`, `finish`).
- **1.7:** it can also be started, and told to `load`, by the page's **Load model** button, and stopped at once by **Unload model**, without a run (§25). Starting and stopping the process are done under one lock; the idle clock counts from when a load finishes.
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
- **Uploads:** accept PNG/JPEG/WebP only; verify by decoding with Pillow; cap file size (20 MB, `STUDIO_MAX_UPLOAD_MB`, enforced while the body streams in), pixel count (16 MP) and use Pillow's decompression-bomb protection; re-encode to PNG on store; random ids. A staged upload no run claims is deleted after 24 hours (§21.7).
- **Queue-jumping is bounded:** a `draft` run is refused unless it is small, few-stepped and one image (§22.2), so the flag cannot be used to put a big job ahead of the queue.
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

- **Multi-stage build.** Node builds the React app. The final stage is `FROM nvcr.io/nvidia/pytorch:<tag>`, where `<tag>` is the newest `YY.MM-py3` (at least 25.10) that I test on the Spark, pinned exactly. `diffusers` comes from a pinned GitHub commit; other dependencies are pinned. **Since 1.8** the image also holds the released `diffusers==0.40.0` in `/opt/music-libs` (§26.4), put first on the music worker's Python path only, and the build checks that it imports.
- **Keep NVIDIA's PyTorch.** NGC images ship NVIDIA's own PyTorch build, and a careless `pip install` can replace it with a generic wheel. The build keeps torch pinned and is checked inside the built image with `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`. (Expected NGC behaviour; verify on the Spark.)
- **Compose service.** `runtime: nvidia` plus a GPU reservation (`deploy.resources.reservations.devices` with `driver: nvidia`, `count: all`, `capabilities: [gpu]`); `shm_size: 2gb` (`ipc: host`, as in NVIDIA's examples, is the fallback if needed); `restart: unless-stopped`; `user` set to the Spark user's UID:GID so mounted folders stay writable.
- **Volumes.** `./data:/data` for the database and images. The Hugging Face cache is mounted at `/models` with `HF_HOME=/models`; the host side defaults to `~/.cache/huggingface`, the same cache the vLLM container uses, so nothing downloads twice.
- **Port.** Container 8080, published as `8080:8080` for LAN access (decision #1). Hermes' vLLM uses 8000 and NVIDIA's ComfyUI playbook 8188, so there is no clash. For tunnel-only use, publish `127.0.0.1:8080:8080` and add a custom app in NVIDIA Sync. If the page isn't reachable from the LAN, check the Spark's firewall.
- **Operations.** Healthcheck `GET /api/health`; logs to stdout; **build on the Spark itself** (arm64).
- A `compose.yaml` and the equivalent `docker run` one-liner ship in the repo. `compose.yaml` itself reads a few variables that are not studio settings: `STUDIO_UID` and `STUDIO_GID` (the user the container runs as), `STUDIO_BIND` (the host address the port is published on; `127.0.0.1` keeps it off the LAN) and `HF_CACHE_DIR` (the host's model cache). It passes the studio settings of §13 through to the container, including the 1.3 draft limits.
- **Updating:** `git pull && docker compose up -d --build` (README "Updating and rebuilding"). The page's title shows which version is actually running, and `index.html` is revalidated on every load, so a rebuilt page appears on the next reload (§5.1).
- **Backups:** `scripts/backup.sh` and `scripts/restore.sh` (README "Backing up and restoring") save and restore the four things that live outside the container: `./data`, `.env`, the built image and, optionally, the model cache. A backup is **one `.tar` file** (an ordinary tar of already-compressed pieces plus a manifest and checksums) so it can be copied to a NAS; it is re-read and its checksums checked before it gets its final name, and `restore.sh --verify` checks a copy without Docker. The studio is stopped only while `./data` is packed (SQLite in WAL mode must be at rest), and a restore reads the `.tar` in place and never deletes anything: what is in the way is moved aside. Their tests, `scripts/tests/backup_restore_test.sh`, run without Docker.
- The unified-memory caveats are in §9a.

## 13. Configuration reference (PROPOSED; every studio setting, as of 1.5)

| Variable | Default | Meaning |
|---|---|---|
| `STUDIO_MODEL` | `Qwen/Qwen-Image-2.1` | Hub id or local path |
| `STUDIO_DATA_DIR` | `/data` | Database, images, thumbnails |
| `HF_HOME` | `/models` | Hugging Face cache (mounted from the host, §12) |
| `HF_TOKEN` | unset | Only if the repo is gated |
| `STUDIO_LOCAL_FILES_ONLY` | `auto` | **Where model files come from (§26.11).** `auto`: the local cache first, the network only if files are missing from it, so after the first download nothing is fetched again; `true`: never touch the network; `false`: ask the hub on every load (the behaviour before 1.8) |
| `STUDIO_CPU_OFFLOAD` | `false` | Use model CPU offload |
| `STUDIO_MIN_FREE_GB` | unset = off; `compose.yaml` sets `40` | Minimum `MemAvailable` required before loading the model (§9a). 40 is a starting estimate (about 14 GB transformer plus text encoder, VAE and activations, with headroom), to be replaced by the figure measured on the Spark. `0` turns it off |
| `STUDIO_IDLE_TIMEOUT_MIN` | `30` | Unload the model after this idle time |
| `STUDIO_QUEUE_CAP` | `10` | Max pending jobs |
| `STUDIO_RETENTION_DAYS` | `30` | Auto-expire age; `0` disables |
| `STUDIO_MAX_IMAGES_PER_RUN` | `8` | Batch cap |
| `STUDIO_MAX_PROMPT_CHARS` | `8000` | Prompt length cap |
| `STUDIO_MAX_UPLOAD_MB` | `20` | Upload size cap |
| `STUDIO_MAX_INPUT_IMAGES` | `4` | **v2:** images per edit, 1–10 (§21.9) |
| `STUDIO_UPLOAD_TTL_HOURS` | `24` | **v2:** how long an unclaimed staged upload is kept (§21.9) |
| `STUDIO_DRAFT_SIZE` | `512` | **1.3:** a draft's longest side in pixels; 256–1024, a multiple of 32 (§22.2) |
| `STUDIO_DRAFT_STEPS` | `12` | **1.3:** the most steps a draft may use; 1–100 (§22.2) |
| `STUDIO_EDIT_WARN_UNITS` | `8` | **1.6:** the page warns that an edit is heavy above this many units (images × (resolution ÷ 1024)², so 3 images at 2K is 12); `0` = never. 8 is a guess until the Spark has measured it (§21.11, M5c) |
| `STUDIO_MUSIC_MODEL` | `MiniMaxAI/MiniMax-Music3` | **1.8:** the music model's Hugging Face id or a local folder (§26) |
| `STUDIO_MUSIC_MIN_FREE_GB` | unset = off; `compose.yaml` sets `40` | **1.8:** the memory check for the music model, as `STUDIO_MIN_FREE_GB` (about 24 GB loaded; 40 is an estimate) |
| `STUDIO_MUSIC_MAX_SECONDS` | `300` | **1.8:** the longest `duration` a music run may ask for, 10–360 (the model's own limit) |
| `STUDIO_MUSIC_MAX_TRACKS` | `4` | **1.8:** versions per music run, 1–8 |
| `STUDIO_MUSIC_LIBS` | unset; the image sets `/opt/music-libs` | **1.8:** a folder put first on the music worker's Python path (the image's own diffusers 0.40.0) |
| `STUDIO_STATIC_DIR` | `<repo>/frontend/dist` if built; `/app/static` in the image | The built web page (added in M2) |
| `STUDIO_HOST` | `0.0.0.0` | Listen address inside the container (added in M1) |
| `STUDIO_PORT` | `8080` | Listen port |
| `STUDIO_ALLOWED_HOSTS` | empty (any) | Comma-separated host names to accept (§11; added in M1) |
| `STUDIO_PIPELINE` | `real` | `fake` uses the test pipeline (§15) |
| `STUDIO_FAKE_STEP_DELAY_MS` | `30` | Fake pipeline only: delay per step (added in M1) |
| `STUDIO_FAKE_LOAD_FAIL` | unset | Fake pipelines only: simulate a model load failure (added in M1); `once` fails only the first load (1.7) |
| `STUDIO_FAKE_LOAD_DELAY_MS` | `200` | **1.7:** fake pipeline only: how long its "load" takes, so tests can see the loading state (§25.3) |
| `STUDIO_TOKEN` | unset | Reserved; setting it stops the server from starting (§11) |

## 14. Tech stack and repo layout (PROPOSED)

- **Backend:** Python 3.11+, FastAPI, uvicorn, pydantic (request validation), Pillow, SQLite via the standard library.
- **Front end:** React 19 with TypeScript (strict), Vite, Radix UI Dialog for the drawer and dialogs, plain CSS with variables for theming. Unit tests with Vitest, browser tests with Playwright against the real backend running the fake pipeline.
- **Layout:**

```
ai-image-studio/   (repository root)
  backend/studio/    api.py (the routes), jobs.py (queue, cancel, expiry, uploads), runspec.py (checks a run
                     request), db.py, storage.py, inputs.py (uploads and the copies a run owns), presets.py
                     (sizes and limits), serialize.py, events.py, security.py, sysinfo.py, naming.py, config.py,
                     worker.py + worker_client.py (the GPU process and its stdio protocol), pipelines/ (real, fake)
  backend/tests/     pytest: API, queue, cancel, expiry, uploads, edits, drafts, worker protocol, real-pipeline stand-in
  frontend/          Vite + React + TS: src/ (App, options, store, api, components/), e2e/ (Playwright)
  Dockerfile         (built on the Spark); docker/ holds its build-time check, healthcheck and pinned NGC versions
  compose.yaml, .env.example
  docs/DESIGN.md, docs/SPARK_TEST.md
  scripts/qwen_image.py   (the CLI; stays standalone for now)
  scripts/edit_via_api.sh (drives an edit with curl, until the page exists)
  scripts/backup.sh, restore.sh, tests/   (backup and restore, with their tests)
  README.md
```

The server re-implements the size presets, RGBA wrapper, filename scheme and error translations rather than importing the CLI script. Sharing a module is a later refactor, not a v1 task.

## 15. Testing strategy (PROPOSED)

**The key idea: a fake pipeline.** With `STUDIO_PIPELINE=fake` the worker produces deterministic synthetic images (seed-dependent colours, the prompt drawn on it, optional alpha), simulates progress and delay, and can inject faults (out-of-memory, load failure, crash) on demand. The whole stack can then be developed and tested without a GPU or the weights — which is exactly my situation in this sandbox (no GPU, no access to the model).

Faults are injected per run with prompt directives: `[fake:error]`, `[fake:oom]`, `[fake:crash]` and `[fake:noise]` (stray output on stdout), optionally `@N` to target image N only, e.g. `[fake:crash@1]`. `STUDIO_FAKE_LOAD_FAIL=1` simulates a load failure. Fake images are clearly labelled "FAKE PIPELINE". (Added in M1.)

- **Music (1.8):** a fake music pipeline for every automated test (a short, deterministic melody, both progress stages, Cancel, fault directives). The real pipeline's own code is checked a second way, where `torch` and `diffusers` 0.40.0 are installed (not in the plain test environment): `backend/tests/music_tiny.py` builds a **tiny random-weight copy of MiniMax-Music3** from the real `diffusers` and `transformers` classes, and the real `RealMusicPipeline` runs on it on a CPU in seconds. That proves the hooks that report progress and carry out Cancel, the shape and rate of the output and the worker around it; it cannot say anything about the real model's sound, speed or memory.
- **Unit:** validation and limits, slug and filenames, queue ordering and cap, retention and pinning, options persistence parsing (corrupt/old localStorage).
- **API integration:** pytest with the fake worker — every endpoint, SSE events, restart recovery, failure paths.
- **End-to-end:** Playwright with Chromium against the running container-less stack — themes, Options persistence across reload, Reuse locking the seed, all four Edit inputs, queue cap and cancel, phone-width layout, keyboard use.
- **Real hardware (manual, on the Spark, checklist supplied):** real model load and generation, idle unload actually frees GPU memory (`nvidia-smi`), behaviour next to Hermes, real out-of-memory handling, real timings.

**Where the suite stands (1.5):** backend 360 (pytest), front end 90 (Vitest) and 34 (Playwright, a real browser against the real server with the fake pipeline). The fake pipeline also learned editing (it draws a numbered strip of the inputs) and the real pipeline's edit path is exercised against a stand-in with the real call signature, so the plumbing is tested without a GPU.

**How the tests are checked.** After each release I break the code on purpose ("mutation checks": 29 for M6, 62 for M5a, 45 for 1.3, 63 for 1.4, 33 for 1.5) and look for a mutation that no test notices; each survivor has been either a gap in a test, which was then closed, or a change that provably cannot be seen (reported each time). Tests that proved flaky were traced to their cause and fixed rather than retried (§22.6; and §24.4, where the failure was reproduced under CPU stress and the same stress then showed the fix).

**What I cannot verify from here:** anything involving the real model, CUDA, GPU memory, aarch64 builds, or Hermes. Those are covered only by the manual Spark checklist (`docs/SPARK_TEST.md`, one section per release).

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

**Version 2 adds criteria 19–32** (§21.10), and the later releases 33–47 (§22.4, §23.3, §24.3), 48–50 (§21.13) 51–60 (§25.5) 61–75 (§26.10, music) and 76–77 (§26.11, cache). Criterion 7 ("Edit works via file picker, drag-and-drop, paste and 'Edit this'") is replaced by 19–27, which cover several images.

**Where each criterion stands (1.7).** "Automated" means a test passes against the fake pipeline; "Spark" means it needs the real machine and has not been reported back to me.

| Criteria | About | Status |
|---|---|---|
| 2, 3, 4, 5, 6, 9, 11, 12, 13, 14, 15, 16 | Themes, options, Generate and history, seeds, queue and cancel, failures, expiry, uploads, filenames, phone layout, keyboard | **Automated.** (9: a running job now stops between steps, not only between images, since M6.) |
| 8, 18 | Transparent output; the memory pre-flight | Automated with the fake pipeline; **Spark** for the real model |
| 1, 10, 17 | Another LAN device; idle unload frees memory; `docker compose up` and persistence | **Spark.** You built and ran 1.0 there; the worker's exit after the idle time is automated; the memory figures (10) and persistence across recreation (17, `SPARK_TEST.md` §13) have not been reported |
| 7 | Edit through four input methods | **Replaced** by 19–27 |
| 19–27, 48–50 | Editing with several images | **Automated**: the server side (1.2) and the page (1.6, M5b) |
| 28 | Real edits on the Spark | **Spark** (M5c) |
| 29–30 | Mark-up and masks | **Not built** (M5d) |
| 31–32 | Cancel and expiry (M6) | **Automated;** `SPARK_TEST.md` §14 for the real GPU |
| 33–38, 40–47 | Scale, Draft, thumbnails, Regenerate larger, the viewer | **Automated** (a draft's queue priority and limits, the `full` record, the one-image request, the in-viewer note) |
| 39 | Draft and scale timings on the Spark | **Spark** (`SPARK_TEST.md` §16, §17) |
| 51–60 | Load model and Unload model | **Automated** (the server's states, the idle clock, the lock, the memory check; the page's buttons, focus, announcements, phone width). **Spark** for how long the real model takes to load and how much memory Unload gives back (`SPARK_TEST.md` §19) |
| 62, 63 (server), 65, 66 (server), 67–74 | Music: the request and its limits, instrumental, tracks and the audio route, one model at a time, memory, Load and Unload by model, Cancel, Keep and expiry, migration, the note in the WAV | **Automated** (1.8, against the fake pipeline; the pipeline's hooks also against the real `diffusers` 0.40.0 code on tiny random weights); **71, and everything about how the real model sounds, behaves and how fast it is, is a Spark question** |
| 61, 63 (page), 64, 66 (page), 75 | The Music tab itself | **Automated** (1.9: the tabs and the keyboard, the fields and the preview, lyrics, Reuse, the player and its range requests, the phone width). **Spark** for how it feels with real waits and on a phone over your network |
| 76–77 | Models come from the local cache (§26.11) | **Automated** (the decision logic, the settings, the worker's environment, both models); what the real hub does is a **Spark** question |

## 17. Build order (PROPOSED)

| Milestone | Delivers | Done when | Status (2026-10-03) |
|---|---|---|---|
| M1 | Backend skeleton, database, API, **fake pipeline**, in-memory queue | API tests pass; I can create runs and watch fake images appear | **Built** (1.0) |
| M2 | Real worker process + pipeline, idle unload, capability probe | Fake path still green; real path only checkable on the Spark | **Built** (1.0); ran on the Spark |
| M3 | Front-end shell, theming, prompt bar, Options drawer, options persistence | Criteria 2, 3, 15, 16 pass in Playwright | **Built** (1.0) |
| M4 | Timeline, run cards, SSE live updates, Reuse, download, delete | Criteria 4, 5, 6, 14 pass | **Built** (1.0) |
| M5 | Edit mode: uploads, all four inputs. **Replaced by M5a–M5e for version 2 (§21.11)** | Criteria 13, 19–30 | Split: **M5a built** (1.2); **M5b built** (1.6); **M5c–M5e not built** |
| M6 | Queue cap, cancel, retention, pin, failure handling. **Now part of version 2 (decision #29): see §21.11** | Criteria 8, 9, 11, 12, 18 pass | **Built** (cap and failures in 1.0; cancel, Keep and expiry in 1.1) |
| M7 | Containerfile, compose, docs | Image builds; criterion 17 on the Spark | **Built** (1.0); built on the Spark |
| M8 | Spark smoke test with you | Criteria 1, 10, 17 and the real-hardware checklist | **Not done**; last |

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
- **After batch 2, one pull request per release** (§21.11, §22–§24; each also ran its own mutation checks, §15):
  - **1.1 (#10, #11):** cancel, Keep and auto-expiry (M6); the version in the page title, `Cache-Control` for the page, a phone header fix; idle unload raised to 30 minutes. Backend 169, Vitest 44, Playwright 14 (M6 alone was 166 and 13; the release added the rest).
  - **1.2 (#12):** M5a, the editing backend: uploads, several inputs per run, schema 2 with a migration checked against a database written by the real 1.1 code. Backend 312.
  - **1.3 (#13):** scale picker, Draft (queue priority), downloadable thumbnails. Backend 329, Vitest 60, Playwright 21.
  - **1.4 (#14):** Regenerate larger on a card, the `full` record, a disabled Upscale. Backend 360, Vitest 87, Playwright 27.
  - **1.5 (#15):** Regenerate larger in the viewer; a flaky test of mine fixed at its cause. Backend 360, Vitest 90, Playwright 34.
  - **1.6 (#17):** M5b, the editing page (§21.11). Backend 363, Vitest 169, Playwright 68.
  - **1.7 (#18):** Load model and Unload model (§25). Backend 384, Vitest 179, Playwright 75.
- **Left to build:** M5c (the Spark test for edits), M5d (local edits), M5e (only if R3 is accepted), an upscaler model for Make 4K (§27.5) and M8, the Spark smoke test together. The table in "Status at a glance" (top of this document) says what each waits on.

## 18. Open items and facts to verify

1. **Hermes and memory** — per NVIDIA's playbook, Hermes Agent uses a local LLM served by vLLM in a Docker container. Whether yours does, which model, and how much of the 128 GB it claims is unknown. This sets the memory budget (§9a). *Still pending from you: the `docker ps`, `docker stats`, `free -h` and `nvidia-smi` output (§20).*
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
12. **The Spark checks for 1.1 to 1.7 have not been reported back** (`docs/SPARK_TEST.md` §14–§19): how fast a cancel takes effect mid-step at 2K and what it does to memory; real edits, through the API (§15) and now on the page (§18) (none of the real-GPU edit path has ever run); the time and look of drafts and of 50% / 25% runs; how different a regenerated image is from the small one; and how long Load model takes with the real weights and how much memory Unload gives back. Until they are, those releases are verified only against the fake pipeline.
13. **Model behaviours nobody has confirmed** (§21.2 [unconfirmed], §22.1): that a prompt can refer to images by number; the mask convention (which colour means "edit here", and whether the mask must match the original's size); that the same prompt and seed at a larger size gives a different picture (assumed from how latent diffusion models behave; it is the reason Regenerate larger warns you); and whether the editing model can enlarge an image without changing it (it decided Upscale, #37, which §27.7 dropped).
14. **R3, the optional prompt rewriter, awaits your decision** (§21.12 item 3). Only M5e depends on it.
15. **The edit cost warning's threshold is a guess** (`STUDIO_EDIT_WARN_UNITS`, default 8, §21.11 M5b): the Spark test (M5c) should replace it with a measured one, together with the default cap of 4 images.

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
- **Still pending from you (the Spark reports are what M5c and M8 wait on):** the `docker ps` / `docker stats` / `free -h` / `nvidia-smi` output from the Spark (sets the memory budget, §18 items 1 and 9); the reports from the Spark checks of 1.1–1.6 (§18 item 12); the decision on R3 (§18 item 14).
- **Round 6 (2026-10-02):** version 1 was built (batch 2: real model, web page, container), built on the Spark and generating images. You asked for version 2 to add **editing with several uploaded images**, to be specified before any code. After a search of what Qwen announced and a read of the pinned pipeline source, you decided #24–#28: several images per edit, numbered badges with insert-into-prompt, a cap of 4 configurable to 10, local edits specified now and built second, and Auto size with a 1K/2K choice. Later the same day, with network access to the Qwen pages opened for the session, I read the announcement, the model card, the licence and the GitHub README directly; §21 and §18 were corrected from them (the prompt rewriter and the mask convention, which the search summaries had wrong or missing; the licence, now verified), and three refinements (R1–R3, §21.3) await your decision. You then decided that **M6 is part of v2** (#29). Details are §21 and are PROPOSED until you review them.
- **Round 7 (2026-10-02):** you asked for version 2 to be built and I started with M6 (cancel, Keep, auto-expiry), the smallest piece and independent of the editing design. You also accepted refinements **R1** (the "Result follows image N" selector) and **R2** (Transparent in Edit), now decisions #30 and #31. You also raised the **idle unload from 15 to 30 minutes** (decision #15). **R3** (the optional prompt rewriter) is still open; only M5e depends on it. M6 was then built in one pull request (backend, page, tests, docs; §21.11 says what it does and where it differs from the plan). Afterwards you asked for a version number in the page title: it now reads `AI Image Studio v1.1` (1.0 was the first Spark build; M6 is 1.1). I numbered releases as major.minor, as in your example, and you confirmed that each release bumps the minor (decision #32); the editing work will therefore ship as 1.2, 1.3 and so on, not as a 2.0. (As it turned out, 1.2 was the editing backend and 1.3–1.5 were three small additions you asked for before the editing page; the editing page will be 1.6. Decision #32 has the list.)
- **Round 8 (2026-10-02):** you asked for the next set of features and I built **M5a**, the server side of editing with several images, as version 1.2 (§21.11). One change from the spec, flagged there: uploads are a raw request body, not multipart (no new dependency in the container, the size cap enforced while streaming). The page for editing is next (M5b); it needs no further decision from you except R3 if you want the prompt rewriter.
- **Round 9 (2026-10-02):** you asked to press on, and for two small additions first: an easier way to make the image 50% smaller, and a thumbnail button. I asked what each meant. Your answers: 50% smaller is **half the width and height**; the control is a **scale picker (100/75/50/25%)** on the prompt bar; the thumbnail is **both** a quick small draft and an extra thumbnail file with every image; and these get **their own small pull request before M5b**. Decisions #33–#35, specified in §22 (version 1.3).
- **Round 10 (2026-10-02):** version 1.3 was reviewed and merged. You asked whether an image made at a lower resolution could have a button to regenerate it larger. I asked what that should produce; you chose **both**: a **Regenerate larger** button now (the same prompt and seed at the full size you had selected) and an **Upscale** button later (the same picture, bigger, once editing works), "back to the full size" as the step, and a **small v1.4 before M5b**. Decisions #36 and #37, specified in §23.
- **Round 11 (2026-10-02):** version 1.4 (Regenerate larger on a run's card) was reviewed and merged. You asked for the same button in the viewer you get by clicking one image, starting a new job to enlarge that image. Decision #38, specified in §24 as version 1.5.
- **Round 12 (2026-10-03):** version 1.5 was reviewed and merged. You asked what features remain, and then for this specification to be brought up to date with everything built and changed so far. That is this revision. **No behaviour changed.** I read the whole document against the code and corrected what had drifted: the header and a new "Status at a glance" with the release history and what is left; decision #32's list of version numbers; a table of which decisions are built; the screen and run-card descriptions (Draft, Scale, Cancel, Regenerate larger, the viewer); the API, data-model, worker-protocol, layout and configuration tables (including `STUDIO_DRAFT_SIZE` and `STUDIO_DRAFT_STEPS`, which were missing); a table of where each acceptance criterion stands; the build order and its progress; the open items; and stale statements such as "upscaling stays out" (§21.12), which decision #37 had superseded.
- **Round 13 (2026-10-03):** the specification was merged. You asked to work on the next feature, which was **M5b, the editing page**. The decisions behind it were already made (#24–#31), so I built it to §21.4 without further questions and listed the small choices I made in §21.11 for you to veto: how pictures dropped in Generate mode are handled, where Edit this appears, that the cost warning's threshold is a setting (default 8, a guess until the Spark measures it), and the others there. Version 1.6.
- **Round 14 (2026-10-03):** version 1.6 (the editing page) was reviewed and merged. You asked whether there could be a button that loads the model, so that you can work on the prompt instead of waiting for the load after pressing Generate. I answered that it was possible and asked three questions; you chose the button **next to the model pill in the header**, **an Unload button as well**, and **no automatic warm-up** (decision #39, specified in §25 as version 1.7). Two things the code showed me while specifying it, now in §25: the idle clock must start when the load **finishes** (and be refused for `STUDIO_IDLE_TIMEOUT_MIN=0`, where loading ahead would load and instantly unload), and the starting and stopping of the worker needs one lock so that a click cannot meet a half-stopped worker. Small choices of mine for you to veto are in §25.4.
- **Round 15 (2026-10-04):** version 1.7 was built and tested, but could not be pushed (the push was refused as an out-of-place publication, so there is no pull request yet). You then asked whether the interface could make **music on an entirely different tab**, from the code on the **MiniMax-Music3** model card, with a focus on **instrumental** music. I read the card and the licence through a summarising tool, found that the card needs a different `diffusers` commit from the one the image worker is pinned to, and asked three questions. You chose: **instrumental by default with a lyrics box available**, **one model in memory at a time**, and **fields that build the description** (decisions #40–#42). I wrote the design (§26) and stopped, because 1.7 was unpublished. You then said to **go ahead and build 1.8 and 1.9 and to give you a usable example**. Before building I went back to the primary sources (the model repository's files, its raw licence, and the released `diffusers` 0.40.0 source), which **corrected the design**: the sound is 44.1 kHz, not 32; the music pipeline is in the released `diffusers` 0.40.0, which lacks Qwen-Image-2.1, while the image worker's commit lacks music, so two copies are needed; the lyrics may not be empty, so `[Instrumental]` is how instrumental works; and progress and Cancel are possible through hooks, so Cancel does not have to kill the worker. 1.8 (the server) and 1.9 (the page) are built on top of the unpublished 1.7, each on its own branch.
- **Round 16 (2026-10-04):** while 1.8 was being built you asked whether the weights and the model would be **cached locally**, and said that after the first load you do not want it downloading anything if it can be avoided. I answered in writing and made it **decision #43**: `STUDIO_LOCAL_FILES_ONLY` now takes `auto` (the new default: each model loads **from the cache only**, and only a load that fails because **files are missing** is tried again online), `true` (never online) and `false` (the old behaviour, which asked the hub on every load and would have downloaded an update of tens of gigabytes), for both models (§26.11). Building 1.8 (§26.12) turned up one real bug that the tests then pinned down, and corrected the design again where the primary sources disagreed with the first reading (the two copies of `diffusers`, the lyrics that may not be empty). The command-line example you asked for was moved forward into 1.8, so there is something to run on the Spark before the page exists. **1.9 (the page)** then followed (§26.13); its tests also caught that the pill and the Load button needed to know which model they were talking about, which the first design had not said.
- **Repository (2026-09-30):** you asked that nothing for this project be written to `LiveLabs-Image-Dev` and that it get its own repository. Decided: private, personal account; first called `dgx-spark-image-studio`, renamed `ai-image-studio` the same day. You created it on GitHub and it was attached to my session. The earlier commits (CLI script, design spec) were replayed into it with their messages intact and removed from the LiveLabs clone.

## 21. Version 2: editing with several images, and run housekeeping (decisions #24–#32 DECIDED; details PROPOSED; M6, M5a and M5b BUILT, M5c–M5e NOT built)

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
| 30 | Result shape | With Size on Auto, a **"Result follows image [N ▾]"** selector names the image the result's shape follows. It defaults to the pipeline's own choice (the last image that is not a mask) and reorders nothing; the studio computes an explicit width and height from the chosen image | DECIDED |
| 31 | Transparent in Edit | The **Transparent toggle is offered in Edit mode** too, wrapping the prompt in the recommended format, as in Generate | DECIDED |
| 32 | Version numbers | **major.minor, and each release bumps the minor.** 1.0 was the first Spark build, M6 is 1.1, and each release since has taken the next minor (1.2 M5a, 1.3, 1.4, 1.5, 1.6 the editing page, 1.7 loading the model ahead of time; see the table in §3). "Version 2" in this section names the feature set, not the number | DECIDED |

**Refinements proposed after reading Qwen's pages.** R1 and R2 were accepted on 2026-10-02 (decisions #30 and #31, in the table above); R3 is still open:

- **R1 (accepted, #30), a "follows image N" selector instead of reordering for shape.** The pipeline gives the result the last image's shape. Reordering just to change the shape is awkward, and it makes a mask placed last set the shape by accident. Instead, Size on Auto shows **"Result follows image [N ▾]"**, defaulting to the pipeline's own choice (the last image that is not a mask), and the studio computes an explicit width and height from the chosen image with the pipeline's own arithmetic. Qwen's rewriter does the same thing with `ratio_follow`.
- **R2 (accepted, #31), Transparent is offered in Edit mode.** v1 hid it because the model card only showed it for text-to-image; the post now documents editing transparent images and extracting subjects as RGBA layers. The toggle wraps the prompt in the recommended format, as in Generate.
- **R3 (still open), an optional "Improve prompt" step** using Qwen's official rewriter (§21.2). It is a separate 9B model with real costs (§21.12 item 3), so it is a question for you, not a default.

### 21.4 Edit mode on the page (supersedes §5.4) — BUILT (version 1.6, milestone M5b; §21.11 says where it differs)

**The reference tray** sits above the prompt in Edit mode: a wrapping grid of thumbnails (3 across on a phone, so the page never scrolls sideways) plus an **Add images** tile.

- **Four ways in** (decision #12), all of which *add* to the tray:
  1. the **Add images** tile / choose-file button, with multiple selection;
  2. **drag-and-drop** of one or several files onto the prompt area;
  3. **paste** an image from the clipboard (appended);
  4. **Edit this** on any past result (the result is appended and the mode switches to Edit).
- **Each thumbnail** shows its **number badge**, a ✕ to remove it, and a drag handle. Upload progress shows on the thumbnail itself.
- **Order matters, so reordering is first-class:** drag a thumbnail to a new place, or use its **Move earlier / Move later** buttons (the keyboard route; focus stays on the moved thumbnail). The numbers update at once. A live region announces each change ("Image 3 added", "Moved to position 1").
- **Inserting a reference:** clicking a badge inserts `image N` at the caret of the prompt (replacing any selected text, adding a space where needed) and returns focus to the prompt. With an empty prompt, the placeholder suggests: *"Refer to images by number, e.g. put the dog from image 1 into the scene from image 2."*
- **The shape control** (decision #30): with Size on Auto, a selector under the tray reads **"Result follows image [3 ▾]"**. It defaults to the last image that is not a mask (the pipeline's own rule) and can be set to any image. Choosing another image reorders nothing: the studio computes an explicit width and height from that image's aspect ratio at the chosen resolution (side lengths rounded to multiples of 32).
- **The cap:** the Add tile reads "3 of 4" and is disabled at the cap. Dropping more files than fit adds the first ones that fit and says how many were skipped. A file that fails validation is rejected **on its own**, with the reason; the others are kept.
- **Uploading starts as soon as a file is added** (staged on the server), so Generate is quick. Generate is disabled while uploads are running and while the tray is empty ("Add at least one image to edit.").
- **Not persisted:** the tray is kept while the page is open, including when you switch modes, but not across a reload (uploads are cheap to redo, and this avoids pointing at files the server has since cleaned up). The prompt draft is still kept, as in v1.

**Options in Edit mode**

- **Size:** Auto (default; shows "follows image N") or any preset or custom size (§6). A fixed size overrides the shape only.
- **Resolution: 1K | 2K** (new, Edit only, default 1K; decision #28). It sets `output_resolution`, which sizes the output *and every input* (§21.2 point 3). Help text: "1K is about 1 megapixel and quicker. 2K is about 4 and costs far more with several images."
- **Cost hint:** next to the Resolution control, a short line grows with `images × (resolution ÷ 1024)²` ("1 unit", "4 units", "16 units") and turns into a warning above a threshold ("This edit is heavy: expect a long run, or running out of memory"). The thresholds are set from Spark measurements (M5c, §21.11), not guessed now.
- Steps, seed, guidance, negative prompt and images per click behave as in Generate.
- **Transparent** (decision #31): offered in Edit as well, wrapping the prompt in the recommended format. An input with an alpha channel is always kept as is, and the result card reports whether the output has alpha, as it does today. Whether the wrapper is needed when the input is already transparent is [unconfirmed]: the toggle is off by default, a transparent input shows a hint, and the Spark test (§21.11, M5c) settles it. A prompt starter, **"Extract the subject"**, covers the post's photo-to-RGBA example.

**Run card for an Edit run**

- A strip of the **numbered source thumbnails** (1 … N, in order) before the result images; each opens in the lightbox, which pages through the sources and then the results. The prompt's "image 2" can thus be matched to a picture.
- The meta line reads like "Edit · 3 images · 2048×1152 · 1K · 40 steps · seed 42".
- **Reuse** restores the prompt, the options **and all the inputs in order**, copied into a fresh tray. If an input file is gone, the slot is named instead of silently dropped. **Retry** resubmits the same inputs.
- **Edit this** on a result adds it to the tray (it becomes the last image unless you reorder, and the shape note says so).

### 21.5 Local edits (milestone M5d) — NOT BUILT

Qwen documents three ways to mark where an edit goes ([Qwen], §21.2), and the pipeline has no mask parameter [verified], so each is done **to the pixels, in the browser, before upload**:

- **Circles and marks:** an editor opened from a tray thumbnail, with an ellipse tool, a freehand brush, undo and clear, and **named colours: red, blue, green and white first** (Qwen's own examples), then yellow and black. The legend shows each colour's name, so the prompt can say what Qwen's example says: *"change the hair in the red circle to black"*. **Done** bakes the marks into a **derived PNG** that takes the thumbnail's place (same number); the original is kept so the marks can be removed.
- **Painted annotation:** the same brush in white ("the area marked in white").
- **Mask:** the original image plus a separate black-and-white mask, as **two inputs**. The studio flags the mask as a mask for display only (the model just sees one more numbered image). **The polarity is [unconfirmed]**: Qwen does not say which colour means "edit here". White is the working assumption (it is how the painted-annotation example marks the place); M5c tests both ways on the Spark *before* M5d is built. The editor can paint a mask directly (white where you brush, black elsewhere) and export it at the original image's size.
- **Both count towards the cap** and are stored with the run (the original and the marked or mask version), so the card shows exactly what was sent.
- **The shape trap goes away (decision #30):** the shape selector never offers a mask as the source and its default skips masks, so a mask placed last cannot set the result's shape by accident.
- **Out of scope:** layers, selection tools, non-destructive history.
- **To confirm on the Spark:** mask polarity, whether the mask must match the original's size, and the best prompt wording for each of the three ways.

### 21.6 API changes (supersede the rows in §7)

| Method & path | Change |
|---|---|
| `POST /api/uploads` | Called once per image, in parallel. **The file itself is the request body, not multipart** (changed while building M5a: it needs no new dependency pinned into the container, the size limit is enforced while the body streams in, and `curl --data-binary @photo.jpg` is all a script needs). The type is decided by decoding, never by the Content-Type header. Returns `upload_id`, `width`, `height`, `has_alpha`, `bytes`, `url` and `thumb_url` (the ordinary image routes serve a staged upload). **413** too large (bytes or pixels), **415** not a PNG, JPEG or WebP, **422** damaged, **507** disk full |
| `DELETE /api/uploads/{id}` | **New.** Removes a staged upload (the ✕ before submitting). Only for an upload no run has claimed: 404 for anything else, so it cannot delete a run's own image |
| `POST /api/runs` | `input_image` is replaced by **`input_images`: an ordered list, 1 … cap, of `{upload_id}` or `{image_id}`** (a past result or input). Edit-mode options add **`resolution`** (1024 or 2048, default 1024); `width`/`height` null = Auto, plus **`shape_from`** (1-based index of the image the result follows, decision #30; default the last non-mask image) and **`transparent`**, which is now allowed in Edit (decision #31). Each list item is validated on its own and errors name the position: `input_images[2]`. Items may also carry `role` (`reference` by default; `marked` and `mask` are accepted now and used by local edits later). **An error names the position**: `loc` is `["body", "input_images", 1]` (0-based, like FastAPI's own) and the message says "Image 2: …" (1-based, as the page numbers them). **429** when the queue is full (the uploads are kept for another try); **507** if the copies don't fit on disk |
| `GET /api/capabilities` | Adds `limits.input_images {min, max}`, `limits.resolutions [1024, 2048]`, and `supports.multi_image`. The start-up check cannot see inside a loaded pipeline, so `multi_image` is assumed wherever `edit` is, and confirmed by the real-hardware test (§21.10). If the pipeline rejects a list, the run fails with a clear message. M5a adds `limits.upload_mb` too. **`modes` lists `edit` when `supports.edit` is true** (since 1.6; in 1.2 the page could not make an edit yet, so it listed only `generate`), and `limits.edit_warn_units` tells the page the cost warning's threshold (§21.11, M5b) |

The singular `input_image` of §7 was never implemented, so nothing breaks by replacing it.

### 21.7 Data model (schema version 2)

- **New table `run_inputs(run_id, position, image_id, role)`**, primary key `(run_id, position)`; `role` is `reference`, `marked` or `mask` (display only, §21.5). **`position` starts at 1**, because "image 1" is what you see and write in a prompt. An input's `images` row has `kind = 'input'`; while `run_id` is empty it is a **staged upload**, and once a run owns it, `run_id` is set. Deleting a run or an image row removes the link (both cascade).
- **A run owns its inputs** (decision #18: kept with the run, deleted with it). When an input is a past result or a past input (*Edit this*, Reuse), the file is **copied** into the new run's own input rows, never shared. Deleting or expiring the original run therefore cannot break another run. The cost is disk: up to cap × 20 MB per run at worst, typically a few MB per image.
- The v1 column `runs.input_image_id` was never written by any shipped feature. The migration (`schema_version` 1 → 2) is additive: it creates `run_inputs` and leaves that column alone.
- `options_json` also records `resolution` and the ordered roles, so Reuse and Retry stay faithful.
- **On disk:** `inputs/staged/<id>.png` (+ `thumbs/staged/<id>.webp`) for uploads waiting; `inputs/<run>/<position>.png` and `thumbs/<run>/in-<position>.webp` for what a run owns, so the run's own delete takes them.
- **Claiming is atomic.** A new edit copies each input into its own folder, then, in **one database transaction**, adds the run, its input rows and removes the staged uploads it used. If the queue is full, an upload was taken or deleted meanwhile, or the disk is full, **nothing is left behind and the uploads are kept** for another try (a refused request never costs you your uploads). The same upload may appear twice in one edit; it is one claim and two copies. Copying happens before the transaction, so a crash between the two leaves an unowned folder, which the clean-up below removes.
- **Staged uploads** are deleted if no run claims them within **24 hours** (`STUDIO_UPLOAD_TTL_HOURS`), at start-up and hourly. The same sweep removes files and folders nothing in the database owns (a crash between writing and recording), but only once they are an hour old, so a submit that is under way is never touched.
- **The migration keeps a copy.** Before the first start on schema 2, the database is copied (with SQLite's own backup, so it is consistent) to `studio.sqlite.before-schema-2`. An older studio refuses a database a newer one has opened (with a message saying so), so that copy is the way back to 1.1 (stop the studio, delete the leftover `-wal` and `-shm` files, copy it over `studio.sqlite`; the history is then as it was at the upgrade); an existing copy is never replaced. Checked end to end with a database written by the real 1.1 code.
- **Expiry and delete take a run's inputs with its outputs** (M6, §21.11): a pinned run keeps both; staged uploads follow the 24-hour rule above. Because a run owns copies of its inputs, an input copied into a newer run survives the expiry of its source.
- Uploads are **re-encoded to PNG without flattening alpha** (§21.2 point 5): an image with transparency (including a palette image with a transparent colour) becomes RGBA, anything else RGB; a phone photo's rotation tag is applied, so the model sees it upright; metadata is dropped. The other upload rules (§11) are unchanged: PNG/JPEG/WebP only, decoded and verified, 20 MB and 16 MP per image (`STUDIO_MAX_UPLOAD_MB`).

### 21.8 Worker and pipeline

- `ImageJob.input_path` (one) becomes **`input_paths` (a list, in order)**, plus `resolution`. The paths are relative to the data folder; **the worker checks each one is a file inside that run's own input folder** before using it (an edit with none, or a Generate with some, is a malformed job), and records `inputs` (the count) and `resolution` in the result PNG's text chunks.
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
21. With Size on Auto the result's shape follows the chosen image (by default the last one that is not a mask; selectable, with no reordering: decision #30), the page says which, and an explicit size overrides it.
22. Clicking a badge inserts "image N" at the caret.
23. An Edit card shows every source in order. Reuse restores prompt, options and all inputs in order; Retry resubmits the same inputs; a missing input is named.
24. Deleting the run an input came from does not break another run that used it.
25. A PNG input with transparency is stored and sent with its alpha intact. With Transparent on (decision #31) the prompt is wrapped in the recommended format and the card says whether alpha came back.
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
| M6 | Cancel, Keep and auto-expiry (details below). The v1 plan's remaining housekeeping, now part of v2 (decision #29). **BUILT (1.1), reviewed and merged; the Spark checks below are still to do** | Criteria 9 and 12 of §16, and 31–32 (the parts that don't involve editing, which isn't built yet) |
| M5a | Backend: multi-file uploads and staging, the cleanup job, `run_inputs` and the schema migration, `POST /api/runs` with `input_images`, worker and fake pipeline pass a list | API tests; fake edits show all sources in order. **BUILT (1.2), reviewed and merged; the Spark checks below are still to do** |
| M5b | The page: tray, the four inputs, badges and insert, reorder, cap, shape note, Resolution control with cost hint, run-card sources, Reuse, Retry, Edit this. **BUILT (1.6)** | Criteria 19–27 in Playwright |
| M5c | The Spark test for edits (checklist supplied): 2- and 4-image edits at 1K, one at 2K, an alpha input; prompts that refer to "image 1" and "image 2"; a transparent edit and a subject extraction; **a mask, tried with both polarities and sizes**; memory and time recorded; cap and cost-hint thresholds set | Criterion 28, and the [unconfirmed] items of §21.2 settled |
| M5d | Local edits: mark-up editor and mask (§21.5), after M5c has settled the mask convention | Criteria 29–30 |
| M5e | *Only if R3 is accepted:* the "Improve prompt" step (§21.12 item 3) | Its own criteria, written when decided |

**Proposed order: M6, then M5a, M5b, M5c, M5d (and M5e if R3 is accepted).** Reasons: M6 is the smallest piece and independent of the editing design; it closes a gap you feel today (no way to stop a long run), and cancel will matter even more for multi-image edits and before the heavy edits M5c measures. Its one point of contact with editing, expiry removing a run's inputs, is handled in M5a, which has to extend delete to inputs anyway. Say so if you would rather have another order.

Each is its own pull request into `main` (never stacked), and I stop after each for your review (decision #22). M8, the Spark smoke test together, comes last.

**What happened to the order:** M6 and M5a went first, as proposed (1.1, 1.2). Before M5b you asked for three small things that had nothing to do with editing, so they were built as their own releases: the scale picker, drafts and thumbnails (1.3, §22), Regenerate larger (1.4, §23) and Regenerate larger in the viewer (1.5, §24). Then M5b (1.6). The rest of the order stands: M5c is next.

**M6: built (2026-10-02, version 1.1), reviewed and merged (#10, #11)**

- **Already there from v1:** the pending cap with a clear 429 message (M1), delete with confirmation, which refuses while a run is running (M1), and the failure handling: out of memory, load failures, crashes and the memory pre-flight, each with an actionable message (M1–M2).
- **Cancel.** `POST /api/runs/{id}/cancel`. A queued job becomes canceled at once (no confirmation: nothing is lost). A running job stops **at its next step**: the worker raises a private signal (`Canceled`, deliberately not an `Exception`, so nothing between the callback and the worker can swallow it or turn it into a failure) from the pipeline's per-step callback, which is checked on every step. Finished images of the batch are kept; the image under way is discarded, and the card says "Canceled. 2 of 4 images finished and kept." The worker stays loaded (its process id does not change). The page asks first for a running job, shows "Stopping" until the worker confirms, and every open page sees the same state (`canceling` in the run).
  - **How the worker hears it.** The worker used to read its commands one at a time, so a cancel could not be seen during a run. It now reads them in a thread of its own (with `os.read`, because a thread blocked in `sys.stdin` can make Python abort at exit; this was tested by starting and stopping the worker 80 times). That thread acts on `cancel` at once and queues everything else.
  - **Where it deviates from the plan above:** the plan said "the pipeline has an interrupt flag, so the worker can stop mid-image". The studio does not use that flag: after it, the pipeline would still decode the half-finished latents into an image that is then thrown away, and with the 2K size that decode is not free. Raising from the callback skips it. (Stopping mid-loop leaves nothing stale behind, checked in the pinned diffusers source: the callback runs after the step's transformer calls, outside its `cache_context` blocks, and the key/value caches are local variables of the call.)
  - **Edges, all tested:** a cancel that arrives while the model is still loading waits for the load to finish (a load can't be interrupted), then stops before the first image and leaves the model loaded; a cancel that arrives between two images stops before the next one starts, so its prompt encoding is not wasted; one that arrives while the worker process is still starting is honoured before the job is sent; a stale cancel can never hit a later run.
  - **Not covered:** if the worker were wedged inside one step, a cancel would wait for that step. There is no kill timeout, because killing the worker means reloading the model (minutes). Whether any step on the Spark is long enough to matter is one of the checks below.
- **Keep.** `PATCH /api/runs/{id}` with `{"pinned": true|false}` (strict), a **Keep** toggle on finished cards (a real toggle button: `aria-pressed`, the same visible label either way) and a **Kept** badge. Kept runs are exempt from expiry. Deleting a kept run by hand still works, and its confirmation says "You marked it Keep."
- **Auto-expiry.** A janitor deletes finished runs older than `STUDIO_RETENTION_DAYS` (default 30; 0 turns it off) with their files (images and thumbnails), once at start-up (in the background, so a first sweep of thousands of runs never delays the page) and then every 24 hours. Never queued, running or kept runs. "Older" counts from creation, the date on the card. Choosing and deleting happen in one database transaction, so a run you keep at the same moment is never caught; sweeps go in batches of 200; a failed sweep is logged and tried again at the next interval; open pages remove the cards (`run.deleted`). Each run carries `expires_at`; a card shows **"Will be deleted in N days. Press Keep to save it."** when fewer than 7 remain (rounded down, so it never promises more time than there is, and "within a day" in the last 24 hours).
  - **One thing to know:** a run that waited in the queue for longer than the retention period (the server stopped for a month with jobs waiting) would run and then be expired at the next sweep, because it counts from when it was created. This needs the studio to be down for 30 days with work queued; if you would rather the clock start when a run finishes, it is a one-line change. Say so.
  - `.env.example` no longer says "not active yet".
- **Also in this batch:** the idle unload is now **30 minutes** (you asked; decision #15), and R1 and R2 are recorded as decisions #30 and #31.
- **Tests:** backend 166 (44 new, covering the worker protocol over a real process, the API, expiry and Keep); front end 44 Vitest (10 new) and 13 Playwright (3 new; three consecutive full runs passed). Mutation checks: I broke 29 specific things on purpose (no raise in the step callback, a stale cancel never cleared, no check between images, the sweep ignoring Keep, the sweep touching running runs, an inverted age comparison, files left behind, a janitor that never repeats, a missing `aria-pressed`, a cancel without the dialog, and so on). 27 were caught by a failing test, and two of those first survived (a cancel between two images, and the worker's state right after a cancel), which exposed gaps in my tests that I then closed. Of the other two, removing the explicit image-row delete from the sweep changes nothing because the database cascades it anyway (an equivalent change), and a mutant that kills the janitor task hangs the test run instead of failing it. Neither reflects a gap in the shipped code.
- **To check on the Spark** (`docs/SPARK_TEST.md`, section 14): how quickly a cancel takes effect mid-step at 2K; that GPU memory after a cancel is the same as after a normal finish; that Keep and the warning behave on your real history.

**M5a: built (2026-10-02, version 1.2), reviewed and merged (#12)**

The server side of editing with several images. **At 1.2 there was no page for it yet** (it is M5b, built in 1.6), so nothing changed on screen except the version: the Edit switch stayed disabled because `GET /api/capabilities` listed only `generate` in `modes`. You can try it with `scripts/edit_via_api.sh` (section 15 of the Spark checklist).

- **Uploads.** `POST /api/uploads` stages one image; `DELETE /api/uploads/{id}` takes it back; unclaimed ones are removed after `STUDIO_UPLOAD_TTL_HOURS` (hourly, and at start-up), together with files and folders nothing owns. Validation by decoding: PNG, JPEG or WebP, 20 MB, 16 MP; stored as PNG with transparency kept, rotated upright, 16-bit greyscale scaled to 8 bits (Pillow's own conversion clips it to white, which I found by checking pixels and fixed), metadata dropped. **Where it differs from the spec:** the file is the request body, not multipart (§21.6 says why). That is the one thing I would like you to know I changed.
- **Edit runs.** `POST /api/runs` takes `input_images` (1 to `STUDIO_MAX_INPUT_IMAGES`, default 4), `resolution`, `shape_from`, and Transparent. Each image is an upload id or an image id from an earlier run (a result or an input: "Edit this" and Reuse need nothing else). Errors name the position. The run owns copies of its inputs; claiming is one transaction; a refused or failed submit leaves nothing behind and keeps the uploads (§21.7).
- **Shape.** On Auto the result follows the last image, as the pipeline does; `shape_from` follows another by computing the size with a copy of the pipeline's own arithmetic, **pinned by a test to values produced by the pinned diffusers source itself**; a mask never sets the shape (it is skipped by default and refused by name).
- **Data.** Schema 2 (`run_inputs`), migrated in place; the old database is copied first (§21.7). **Checked with a database written by the real 1.1 code:** upgraded, old runs and images intact, an old result used as an edit input, the old code refusing the new database with a clear message, and the saved copy restoring 1.1.
- **Pipelines.** The worker validates every input path against the run's own folder. The fake pipeline follows the last image's shape and draws a numbered strip of the inputs, so tests check order, count and the shape rule. The real pipeline passes `image=[...]` unflattened and `output_resolution`; an out-of-memory in an edit advises 1K or fewer images. **None of the real-pipeline edit path has run on a GPU**: it is verified only against a stand-in with the pipeline's real call signature. Section 15 of the checklist is the first real test.
- **Decisions I made that you may want to change:** positions count from 1 (what you write in a prompt); the same upload may be used twice in one edit; `role` (`reference`, `marked`, `mask`) is accepted and stored now, so local edits (M5d) need no further migration; an absolute path inside the run's folder is accepted by the worker (the API only sends relative ones).
- **Tests:** backend 312 (up from 169), including a real uvicorn server driven by the script; the front end is unchanged apart from the version (44 Vitest, 14 Playwright). **Mutation checks: 62 deliberate breakages, 59 caught by a failing test, 3 equivalent** (a second layer gives the same answer: an upload id naming a run's image is refused by the atomic claim anyway; a missing file is caught again at copy time; the explicit `ORDER BY position` is redundant with the primary key's order today but guarantees it). The checks found one real gap, now tested: the clean-up must never remove the inputs folder of a run that exists, however old; before that test, a mutant that did so passed.
- **Not covered:** a limit on how many staged uploads may wait at once (they are bounded by the 24-hour clean-up, not by count); an animated WebP or PNG uses its first frame; uploads are not de-duplicated.
- **To check on the Spark** (checklist section 15): a real 1K edit with two and four images, then 2K, with time and memory; whether the model follows "image 1" and "image 2"; transparency; and the point at which memory runs out, which sets the default cap.

**M5b: built (2026-10-03, version 1.6), awaiting your review**

The editing page. Edit is now offered wherever the pipeline can edit (the capabilities' `modes` comes from `supports.edit`), and everything in §21.4 is built: the reference tray with its four ways in (the Add tile, drag-and-drop onto the prompt card, paste into the prompt, and **Edit this**), uploads that start at once and show their progress on the picture, number badges that put "image N" at the caret, reordering by drag or by Move earlier / Move later with focus kept on the moved picture, remove, the cap ("3 of 4"), a file that fails rejected on its own with the reason, **Result follows image N** with Size on Auto, Edit's own size choice (Auto by default), the 1K / 2K Resolution with the cost in units and a warning when it is heavy, Transparent in Edit with a hint when an input has transparency, an "Extract the subject" starter, edit run cards with their numbered sources, the viewer paging through sources and then results, and Reuse and Retry for edits.

- **Small decisions I made while building** (tell me if you want any changed):
  - **A picture dropped on the card or pasted in Generate mode is not taken**: a message says to switch to Edit. It does not switch for you, so a stray drop cannot change what Generate does. A file dropped anywhere outside the card is ignored (rather than the browser opening it and leaving the page).
  - **Edit this on a card appears only for a result with one image.** For a batch, open the image in the viewer: the viewer has Edit this for any image, source or result, so you choose which. From the viewer, a full tray is reported inside the viewer, and a successful add closes it and puts you in the prompt.
  - **The tray keeps working after you send.** The server uses up the staged uploads when it claims them, so afterwards the tray points at the run's own copies of the pictures (as Reuse does). Change the prompt and send again, with nothing to re-add.
  - **"Result follows image N" follows the picture, not the number.** Reordering keeps the choice; if that picture is removed it falls back to the last. It sends `shape_from` only for a picture other than the last, which is what the pipeline does anyway.
  - **Edit has its own size choice**, separate from Generate's (they share only the custom width and height). Options saved before 1.6 read as Edit on Auto at 1K. With Size on Auto the scale picker is disabled, with the reason in its tooltip; with a fixed size it works as in Generate.
  - **The cost warning's threshold is a setting, `STUDIO_EDIT_WARN_UNITS` (default 8, 0 = never)**, which the page is told. This section's own rule is that the thresholds come from Spark measurements, so a number built into the page would need a rebuild to change. **8 is a guess** (3 images at 2K is 12; 2 at 2K is 8 and does not warn). M5c should replace it, together with the default cap of 4. The line under the tray warns too, not only the Options drawer, so a heavy edit is seen without opening Options.
  - **Taking a picture out of the tray cleans up after it**: an upload still being sent is stopped (the browser cancels the request, so nothing reaches the server); once its last byte is out the server will stage it anyway, so the page lets the answer land and deletes the file then; a picture already staged is deleted at once. A file over the upload size limit is refused before it is sent.
  - **The starter fills only an empty prompt** ("Extract the main subject of image 1.") and turns Transparent on; it is disabled while the prompt has text, so it never replaces your words.
  - **The Edit switch lost its "soon" label.** If the pipeline cannot edit, Edit is disabled with the reason in its tooltip.
  - The Upscale button's tooltip no longer says it "arrives with editing" (editing has arrived; Upscale waits for the Spark test, §23.2).
- **Server changes** (small): `modes` is derived from `supports.edit`; the new setting `STUDIO_EDIT_WARN_UNITS` and `limits.edit_warn_units`. Nothing else on the server changed; M5a already had the rest.
- **Tests:** backend 363, front end 169 Vitest (the tray rules, Edit's size and request, Reuse and Retry, the viewer's items) and 68 Playwright (the tray, the options, the cards, Reuse and Retry, failures, phone width). **Mutation checks: 105 deliberate breakages (the tray rules, Edit's size and request, Reuse and Retry, the viewer's items, the upload clean-up, the tray and prompt-bar controls, the Options drawer, the cards, and the server's new settings), all caught by a failing test.** Not all at first: in the first pass 94 were caught, 2 survived, 7 did not compile (an unused variable stops the build) and 1 pattern was ambiguous. The survivors were worth having. One showed that **my test for removing a picture mid-upload was vacuous**: a delayed request was cancelled before it reached the server, so it checked nothing, and behind it was a real gap in the page: a picture taken out after its last byte was sent was left staged on the server until the 24-hour sweep, because cancelling threw away the answer that said where it was. It is fixed (below) and now covered by two real tests. The other survivor showed that nothing checked that keyboard focus survives when the arrow you just used becomes disabled; it does now. The rest were rewritten until they compiled and were then caught.
- **Not verified:** everything about the real model: whether it follows "image 1" and "image 2", what it does with a transparent input, how long an edit takes and how much memory it needs (§21.2 [unconfirmed]). Also: dragging real files from your desktop and pasting a real screenshot were tested with simulated drop and paste events, not through a real file manager or clipboard, and the layout was checked at 360 px in Chromium only (not Safari or Firefox, and without real touch). `SPARK_TEST.md` §18 is the checklist; try the drag and the paste yourself.

### 21.12 Open items for v2, and ideas parked

**Open**

1. **Decide R3** (the optional prompt rewriter, §21.3 and item 3 below). R1 and R2 were accepted on 2026-10-02 (decisions #30 and #31). Only M5e depends on R3.
2. **Measure on the Spark** (M5c): time and memory for 1, 2 and 4 images at 1K and 2K, next to Hermes. This sets the cap's default, the cost-hint thresholds and any size limits for edits.
3. **The prompt rewriter (R3).** It is official and recommended by Qwen, but it is a separate **9B vision-language model** (about 18 GB in bf16 by the usual arithmetic, not stated by Qwen) next to a main model whose footprint is still an estimate (the studio starts from 40 GB, to be measured, §9a), on a Spark that Hermes shares. Open questions: load it only when "Improve prompt" is clicked and unload it again, in its own worker process, as the main model is (decision #13)? Show the rewritten prompt for you to edit and approve, never apply it silently? Honour its `wh_ratio` and `ratio_follow` suggestions? What licence do the two rewriter checkpoints carry (not read yet)? Time to rewrite a prompt on the Spark?
4. **Settle the [unconfirmed] items** (§21.2) on the Spark: referring to images by number, and the mask conventions.
5. **The order of work** (§21.11): resolved. M6 was first, then M5a, then the three small releases of §22–§24 at your request, then M5b (1.6); M5c is next.
6. **The licence** (§18 item 2) now has a definite answer to act on: non-commercial, research or evaluation only. Whether your use is covered is your call, or a question for Qwen.

**Parked (not in v2, and not scheduled)**

- **Prompt starters** for the jobs Qwen itself shows: virtual try-on (a person, clothes, shoes, a bag, a hat), a group portrait from separate portraits, a room from furniture images, subject extraction to a transparent layer, a panorama from a selfie, an infographic from a photograph, a storyboard from a three-view character sheet. A small menu in Edit mode; cheap and now well grounded; not scheduled.
- **Sequential editing** (successive local edits assembled into an animation, as in Qwen's capybara example) is already served by "Edit this" chaining; no extra feature.
- LoRAs (the pipeline has a LoRA loader) and choosing between models stay out, as in §2. **Upscaling is no longer parked:** decision #37 plans it as a button that waits for the editing page (§23.2).

### 21.13 Criteria added with the editing page (continue §24.3)

48. In Edit the Options show **Resolution 1K | 2K** and the cost in units (images × (resolution ÷ 1024)²), and warn that the edit is heavy above `STUDIO_EDIT_WARN_UNITS` (default 8; 0 = never): in the drawer and under the tray. The server tells the page the threshold.
49. Edit is offered exactly when the pipeline supports it: the capabilities' `modes` lists `edit` if and only if `supports.edit` is true.
50. **Edit this** adds the picture to the tray and switches to Edit: from a card for a result with one image, and from the viewer for any image, source or result. A full tray is reported where the request was made (a toast from a card, a note inside the viewer), and nothing is added.

## 22. Version 1.3: quick size, drafts and thumbnails (decisions #33–#35 DECIDED; details PROPOSED; BUILT and merged, #13)

Three small additions you asked for on 2026-10-02, built before the editing page (M5b) because they are useful on the Spark at once and have nothing to do with editing. All three are **for Generate**; Edit mode has its own size controls (Resolution 1K/2K, §21.4) and gets thumbnails like any image.

### 22.1 The scale picker (decision #33)

- **What it is.** A group of four buttons on the prompt bar, **100%  75%  50%  25%**, labelled "Scale". It scales the width **and** height of whichever size is selected in Options (a preset or a custom size), so you never have to open Options to try a smaller picture. The Options button's summary shows the size actually used ("1024×1024 (50%) · 40 steps …").
- **The arithmetic.** Each side becomes `round(side × scale ÷ 32) × 32`, because the pipeline needs multiples of 32. Rounding is to the nearest multiple, halves upward. Examples: 2048×2048 at 50% is 1024×1024; 2752×1536 at 50% is 1376×768; **2400×1792 at 50% is 1216×896** (1200 is not a multiple of 32), a hair off the 4:3 shape. The page shows the real numbers, so there are no surprises.
- **A size below the minimum is not offered.** A side may not fall under 256 px (§6). A scale that would do that for the current size is disabled, with the reason in its tooltip. If the saved scale becomes impossible after you change the size, the page uses the nearest larger scale that works and shows that one selected; your saved choice returns when the size allows it. (Every preset allows all four. Only small custom sizes are affected.)
- **It is remembered** with the other options. Options saved by an earlier version have no scale and read as 100%.
- **Reuse and Retry.** The run records the size actually used. **Reuse** looks for the preset-and-scale pair that gives exactly that size (preferring 100%) and restores it, so a run made with "1:1 at 50%" comes back as "1:1 at 50%", not as a custom size. Retry sends the same request again.
- **What it does not do.** The same prompt and seed at a different size is **a different picture** [not stated by Qwen; how latent diffusion models behave; to confirm on the Spark]. The picker is for trying things quickly, not for previewing a large image. The Options help text says so.
- **Edit mode** (built in 1.6): the picker applies to an edit with a fixed size; with Size on Auto, Resolution (1K/2K) is the control and the picker is disabled with that explanation.

### 22.2 Drafts (decision #34)

- **What it is.** A **Draft** button beside Generate. It sends the same prompt as a small, fast run: the selected shape with its **long side at 512 px** (`STUDIO_DRAFT_SIZE`, 256 to 1024, a multiple of 32), at most **12 steps** (`STUDIO_DRAFT_STEPS`), **one image**. The negative prompt, guidance and Transparent carry over. The seed is the locked seed if Lock seed is on, otherwise random. Ctrl+Shift+Enter (Cmd+Shift+Enter on a Mac) makes a draft from the prompt box, as Ctrl+Enter generates.
- **Why it is a different size and not a preview.** A draft is for checking the *idea* and the wording in seconds rather than minutes. Because the same seed at another size is another picture (§22.1), **the full-size image will not match the draft**; the button's tooltip and the card say so. [The speed and the look of a 512 px, 12-step draft on this model are unconfirmed: to measure on the Spark, §22.5. Both numbers are settings so they can be tuned without a rebuild.]
- **It jumps the queue, but never the running job.** A draft goes ahead of *waiting* full-size runs (drafts among themselves keep their order), so it is quick even when a long run is already queued. The run in progress is never interrupted. Positions shown on queued cards follow this order. Without it, a draft clicked behind a four-minute run would be pointless. A draft that is waiting can be canceled like any run.
- **The server keeps drafts small.** A run flagged `draft` must be a Generate with an explicit size whose long side is at most the draft size, at most the draft steps, and one image; otherwise it is refused (422). So the flag can't be used to jump the queue with a big job.
- **On the page.** A draft's card has a **Draft** badge. **Reuse** on a draft loads the prompt, negative prompt, guidance and Transparent, but **not** the draft's size, steps or seed, so your next Generate is full size. **Retry** on a failed draft sends the draft again. To make the full-size version, press **Generate**: the prompt is still in the box and your Options are untouched. In Edit mode the Draft button is disabled ("Drafts are for Generate").
- **Records.** `options.draft` is stored with the run. Drafts expire, can be kept and deleted like any run.

### 22.3 Thumbnails (decision #35)

- The studio already makes a **512 px WebP thumbnail** of every image for the history (§8). This makes it **downloadable**: `GET /api/images/{id}/thumb?download=1` sends it as an attachment named like the full image with `_thumb` and `.webp` (`generate_lighthouse-dusk_2048x2048_s42_20261002-101500_thumb.webp`). Transparency is kept. It is for outputs only.
- **On the page.** A **Thumbnail** button on a run card with one image (next to Download), and in the viewer (the lightbox), beside Download, for whichever image is showing. A run with several images opens the viewer to choose.
- WebP rather than JPEG or PNG: small, supported by every current browser, and keeps transparency. Another format or size is a small change if you need one.

### 22.4 Acceptance criteria (continue §21.10)

33. The scale picker offers 100, 75, 50 and 25%; choosing one changes the size sent to the server (width and height each scaled and rounded to a multiple of 32), the Options summary shows the size used, and the choice survives a reload. Options saved without a scale read as 100%.
34. A scale that would take a side under 256 px is disabled with its reason; if the saved scale becomes impossible the nearest larger one is used and shown.
35. Reuse restores a preset-and-scale pair when the run's size is exactly that (otherwise a custom size); it never leaves a stale scale on top of a custom size that already includes it.
36. The Draft button sends a one-image run with the selected shape at a long side of 512 px (or `STUDIO_DRAFT_SIZE`) and at most 12 steps (or `STUDIO_DRAFT_STEPS`), flagged `draft`; its card says Draft; Reuse on it does not load its size, steps or seed; Retry resends it as a draft.
37. A queued draft runs before queued non-draft runs and after the running one; queue positions agree with that; the server refuses a draft that is large, many-stepped, many-imaged, an edit, or without an explicit size.
38. `GET /api/images/{id}/thumb?download=1` returns the WebP with a `_thumb.webp` attachment name for an output image, and nothing downloadable for anything else; the run card and the viewer offer it.
39. **On the Spark:** the time and the look of a draft, the time at 50% and 25% against 100%, and whether the 512 px / 12 steps defaults should change.

### 22.5 What to measure on the Spark

Draft time and quality at the defaults; a 100% / 50% / 25% run of the same prompt and seed with their times (and how different the pictures are); whether 12 steps is too few for a recognisable picture (the model's own default is 40).

### 22.6 What was built, and what differs from the plan above

- **As specified:** the scale picker with the stated arithmetic (a table in the tests pins 2048, 2400×1792, 2752×1536 and the rest, and every preset at every scale is checked to be a legal size within 5% of its shape); Draft with its server-side limits, its queue priority and its Reuse and Retry behaviour; the thumbnail download.
- **Small decisions made while building** (tell me if you want any changed): a draft is **never bigger than the size you chose** (a 256 px selection gives a 256 px draft, not 512); below a 512 px draft size only squares fit exactly, because no side may be under 256 px (a 256 draft of a 16:9 shape is 256×256); on a phone the word "Scale" gives way to the buttons and the size is shown beside them; `Ctrl+Shift+Enter` makes a draft; the server reads the draft flag from the run's stored options, so there is **no database change** and runs made before 1.3 sort as ordinary runs (if a SQLite without JSON functions is ever met, queue order falls back to plain arrival order with a warning).
- **Tests:** backend 329 (up from 312), front end 60 Vitest (up from 44) and 21 Playwright (up from 14). **Mutation checks: 45 deliberate breakages, all caught by a failing test** (three first survived: two were gaps in my tests, now closed, and one was a mutant whose unused import stopped the build, so it ran against the old page). One test of mine from M5a turned out to be flaky (about 1 run in 4): the background clean-up tasks run a first sweep at start-up that could land in the middle of a test that counts sweeps. The tests that count now ask for a quiet app; six repeats of the affected files, and two full browser runs, were clean.
- **Not verified:** nothing about how a draft or a smaller scale actually looks or how fast it is on the model. That is section 16 of the Spark checklist, and it sets the default 512 px / 12 steps.

## 23. Version 1.4: regenerate at full size (decisions #36–#37 DECIDED; details PROPOSED; Regenerate larger BUILT and merged, #14; Upscale NOT built)

### 23.1 Regenerate larger (decision #36)

- **What it is.** A button on a finished run that was made **smaller than the size you had selected**: a **draft** (§22.2) or a run at **25%, 50% or 75%** (§22.1). It queues the same prompt again at the **full size** and with the **steps** you had selected, in one click. Its tooltip and accessible name say what it will do ("Regenerate at 2048×2048, 40 steps").
- **What "full size" is.** 100% of the size chosen in Options when the run was made (a preset or a custom size), with the steps chosen then (a draft used fewer). This is recorded **with the run** (`options.full`: `width`, `height`, `steps`), so the button works from the history, after a reload and from another browser. The page records it whenever it sends a run that is smaller than selected (a scale under 100%, or a draft smaller than the selected size) and leaves it out otherwise, so a run at full size has no such button. **Runs made before 1.4 have no record and no button** (that includes the few made with 1.3; use **Reuse** and the scale picker for those).
- **What it sends.** The run's prompt, negative prompt, guidance and Transparent; the **same seed** (a draft that picked a random seed recorded it, so it is repeatable); the **same number of images** as the run (a draft has one, so a draft becomes one full-size image, not the four you may have selected); the full size and its steps; **not** a draft. It joins the queue like any ordinary run, behind the runs already waiting (drafts alone jump ahead).
- **It will not match the small image.** The same prompt and seed at a larger size is **another picture** (§22.1; unconfirmed for Qwen, to measure on the Spark). The small image tells you the idea works; it does not show what the big one will look like. The page says so when you press the button. For *the same picture, bigger*, see §23.2.
- **When it is shown.** On a **done** run with a record whose full size is larger than the run's size. Not on a full-size run, nor on a failed or canceled one (those have Retry).
- **Server.** `options.full` is accepted on Generate runs only, validated as a size (multiples of 32, 256–4096 per side, within the pixel limit, steps in range), and refused unless it is larger than the run in at least one side and smaller in neither. It is stored and returned with the run and used for nothing else by the server.

### 23.2 Upscale (decision #37, planned and not built) — SUPERSEDED by §27 (decision #44)

*Kept as the record of the plan. Edit mode cannot take a picture past about 2K, which is why §27 replaces it with Make 4K.*

- **What it would be.** A second button, **Upscale**, that keeps *your picture* and makes it bigger, which Regenerate larger cannot do. The model has no upscaler, so the attempt would be an **edit run with the small image as its only input**, at Resolution 2K, asking for the same image in finer detail. [Unconfirmed: whether the editing model leaves the picture recognisably the same, how much detail it adds, and what it costs in time and memory. Nobody has tried it.]
- **Why it waits.** It needed the editing page (built in 1.6), and it still needs a test on the Spark (M5c) before it is worth a button. If the test shows it does not work well, I will say so and drop it rather than ship a button that disappoints.
- **Meanwhile** the button is shown beside Regenerate larger, **disabled**. Its tooltip said "Arrives with editing" until 1.6; now that editing exists it says it is **not built yet** and what it waits for (the Spark test, M5c).

### 23.3 Acceptance criteria (continue §22.4)

40. A run made at 25%, 50% or 75%, or as a draft smaller than the selected size, records its full size and steps; a run at 100% records none.
41. **Regenerate larger** appears on a done run whose recorded full size is larger than the run, names the target in its tooltip, and queues a run with the same prompt, seed, image count and options at the full size and steps; it is absent on full-size, failed, canceled and older runs.
42. The server accepts `options.full` only for Generate runs, validates it as a size, and refuses one that is not larger than the run.
43. The Upscale button is present on those cards, disabled, and says it is not built yet (until 1.6: that it arrives with editing).
44. On a phone the card's buttons wrap without a sideways scroll.

### 23.4 What was built, and what differs from the plan above

- **As specified:** the `options.full` record, validated by the server as described (Generate only; the same size rules as any size; larger in at least one side and smaller in neither); the page records it on a run made at a scale under 100% and on a draft smaller than the chosen size, and on nothing else; the **Regenerate larger** button on a finished run that remembers a bigger size, naming the size and steps in its tooltip and accessible name; the request it sends; a disabled **Upscale** that says it arrives with editing (until 1.6, when it began to say it is not built yet).
- **Small decisions made while building** (tell me if you want any changed):
  - **A draft's full size is the size chosen in Options at 100%, whatever the Scale buttons said** when you pressed Draft (a draft is always sized from the chosen size, §22.2), and its steps are the steps chosen, not the draft's.
  - **Retry carries the record**, so a draft or small run that failed and is retried gets its button when it finishes.
  - **A draft that is already as big as the chosen size has no button** (a 512 px selection makes a 512 px draft: nothing bigger to go back to).
  - **No scroll to the new run**: it joins the top of the history, as with Retry; a toast says it is queued and that it will differ from the small one.
  - **Clicking twice quickly queues one run** (the same guard as Cancel and Keep). Clicking again once it has finished queues another, as Reuse and Generate would.
  - **The server refuses a record that is not larger, even from the API**, so a hand-made request cannot give a full-size run a button.
  - The schema is unchanged: `full` lives in the run's stored options, which already hold the draft flag. Runs made before 1.4 have no such key and show no button.
- **Tests:** backend 360 (up from 329), front end 87 Vitest (up from 60) and 27 Playwright (up from 21). **Mutation checks: 63 deliberate breakages, 62 caught by a failing test and one equivalent.** Two first got through, both gaps in my tests and now closed: a full-queue message was only checked as a substring, so it passed with an unwanted prefix; and seven mutants did not compile (an unused variable stops the build) so they ran against the old page and were rewritten until they did. The survivor is the card re-checking `largerTarget` (status, mode, larger size) in its own copy of the rule: the server never stores a record that fails it, so no browser test can tell, and the Vitest tests pin the rule itself.
- **Not verified:** whether a regenerated image resembles the small one on the real model (§23.1 says it will not), and how much time the small-then-large route saves. That is section 17 of the Spark checklist.
- **Not built, on purpose:** Upscale (decision #37). The button is a placeholder and does nothing.

## 24. Version 1.5: Regenerate larger in the image viewer (decision #38 DECIDED; details PROPOSED; BUILT and merged, #15)

### 24.1 What it does

- **Where.** Clicking an image on a card opens the viewer (one image, with Download and Thumbnail). For a run that has **Regenerate larger** on its card (§23.1), the viewer has the same button in its top bar, with the same tooltip and accessible name ("Regenerate larger: 2048×2048, 40 steps").
- **What it sends: this image only.** A new job with the run's prompt and options at the **full size and steps recorded with the run** (§23.1), the **seed this image used** (it is in the viewer's title), and **one image**. In a run of four, enlarging image 3 makes one new image, not four; for a run of one it is the same job as the card's button. It is an ordinary run in the history and joins the queue like any other.
- **The viewer stays open**, so you can go on to the next image and enlarge that one too. The new run appears at the top of the history behind it.
- **Same caveat as the card** (§23.1): the larger image will not be the same picture as the one you are looking at. *The same picture, bigger* is Upscale (§23.2), which is still not built, and the viewer does not get a placeholder for it.
- **When it is offered.** Exactly when the card offers it: a **done** Generate run that remembers a full size larger than itself. A canceled run's finished images have no button in the viewer either, as on its card.

### 24.2 Feedback inside the viewer

The viewer is a modal dialog, and while one is open the page behind it is hidden from screen readers, including the toasts that confirm or report problems. So a request made from the viewer reports **inside it**, in a short note at the bottom: "Queued at 2048×2048 …", or the reason it could not be queued (a refusal, a full queue). The note is announced to screen readers, goes away when you move to another image or close the viewer, and is not also shown as a toast. A request made from a card still uses a toast.

### 24.3 Acceptance criteria (continue §23.3)

45. The viewer of a run that has Regenerate larger on its card has it too, naming the target; the viewer of any other run does not.
46. Pressing it queues **one** image at the recorded full size and steps with the viewed image's own seed (for a run of several images, not the others), and says so inside the viewer; a refusal or a full queue is reported there as well, and nothing is queued.
47. A double click queues one run. On a phone the viewer's buttons stay on screen with no sideways scroll, and the image still fits.

### 24.4 What was built, and what differs from the plan above

- **As specified:** the button in the viewer's top bar (same rule, tooltip and accessible name as the card's); a request for the image being viewed only (its own seed, one image, everything else as the card's); the viewer stays open; the answer is shown inside the viewer, in a live region, and clears when you move to another image or close it.
- **Small decisions made while building** (tell me if you want any changed):
  - **The note is not dismissible and does not time out**: it stays while you look at the same image, so you can see what you did. Pressing the button again on the same image queues another job, as on the card.
  - **If the viewer is closed before the answer arrives, the answer becomes a toast**, so it is never lost.
  - **On a phone the viewer's top bar wraps onto a second row** (title above the buttons) so the new button, Download, Thumbnail and Close all stay on screen; the image still fits below.
  - **No Upscale placeholder in the viewer**: you asked for Regenerate larger, and Upscale is still only a promise (§23.2).
  - **Only done runs**, as on the card: the finished images of a canceled run have no button in the viewer.
  - The message in the viewer says "Queued this image at …", and the card's says "Queued at …".
  - No server change at all: the one-image request is an ordinary run with `num_images: 1` and the image's own seed.
- **Tests:** backend 360 (unchanged), front end 90 Vitest (up from 87) and 34 Playwright (up from 27). **Mutation checks: 33 deliberate breakages, 32 caught by a failing test and one equivalent** (the card's request using the run's first image's seed instead of the run's seed: they are the same number for a real run, though not for the unit tests' image-less fixtures). Two mutants first did not compile (an unused variable and a possibly-null value stop the build) and were rewritten until they did, as in §23.4. Before the mutants I added two tests for behaviour I had written but not tested (enlarging two images in a row, and closing the viewer before the answer arrives).
- **A flaky test of mine, found and fixed.** In the final full run one older test (`test_files_nothing_owns_are_removed_…`) failed once, though nothing it covers had changed. It passed 9 of 9 alone, so I reproduced it rather than calling it a fluke: under CPU stress it failed **9 times in 60**. The cause was in my test set-up: the start-up clean-up sweep runs in a worker thread, stopping its loop does not stop a thread already running, and on a busy machine that thread could delete the files the test had just made. Quiet test clients now never start those loops (a change to `tests/conftest.py` only; the server is unchanged, since only one sweep runs at a time there). With the fix the same stress runs gave **0 failures in 60**, and 0 in 12 for the whole housekeeping file; the full suite then passed.
- **Not verified:** the same as §23.4: whether a regenerated image resembles the one you opened, on the real model. Part (e) of section 17 of the Spark checklist covers the viewer.


## 25. Version 1.7: load the model ahead of time (decision #39 DECIDED; details PROPOSED; BUILT, awaiting your review)

You asked for a button that loads the model, so that you can work on the prompt while it loads instead of waiting for the load after pressing Generate. I asked three questions and you chose: the button goes **next to the model pill in the header**; there is **also an Unload button**; and **nothing loads by itself** (no warm-up as you type).

### 25.1 What you see

- **Load model** appears beside the model pill when the pill says *Model not loaded* or *Model problem*, which is when loading it is useful or worth retrying. Pressing it starts the load at once, with no run. The pill changes to *Loading model…*, and to *Model ready · unloads in 30 min* when the load has finished. A run you send meanwhile waits for the load and then runs on that same loaded model: nothing loads twice.
- **Unload model** appears in the same place when the pill says *Model ready*, that is, loaded and not working on a run. Pressing it frees the memory **now**, without waiting out the idle timeout. The next run loads the model again, which takes as long as the first time did. There is **no confirmation question**; see 25.4.
- **Neither button is shown** while the model is loading, while it is generating (Unload would have to stop your run; use Cancel for that), or when the pill says *Model unavailable* (loading cannot work until the server's set-up is fixed, so there is nothing to press).
- **A problem is shown, not hidden.** If there is not enough free memory (the same check a run makes, `STUDIO_MIN_FREE_GB`, decision #19) or the load fails, nothing is left loading, the pill says *Model problem* with the reason and the hint in its details, a message also appears on screen, and **Load model** stays so that you can try again once memory is free.
- **The idle timeout still applies.** A model you loaded with the button is unloaded after the usual idle time, counted **from the moment the load finished**, not from the click. So pressing Load and then going to lunch does not hold the memory for ever.
- **If the idle timeout is `0`** (`STUDIO_IDLE_TIMEOUT_MIN=0`: unload as soon as the queue is empty) loading ahead of time would load the model and unload it again at once, so the button is **not offered** and the server refuses (25.2).
- **Screen readers and keyboard.** Both buttons are ordinary buttons with a name and a tooltip. When one is pressed it disappears, so focus moves to the model pill instead of being lost, and the change is announced ("Loading the model…", "Model ready.", or the problem).
- **On a phone** the header still fits without sideways scrolling: the button is compact and the pill's longer text is already shortened there.

### 25.2 API (added to §7)

| Method & path | Purpose | Answers |
|---|---|---|
| `POST /api/model/load` | Start loading the model without a run | `202` with the status (the body of `GET /api/status`) when a load was started; `200` with the status when there was nothing to do (it is already loading or loaded, or a run is using it); `409` `not_enough_memory` (the same message and hint as a failed run); `409` `no_idle_time` when the idle timeout is `0`; `503` `worker_failed` when the worker process could not be started |
| `POST /api/model/unload` | Unload the model now | `200` with the status; `409` `busy` while a run is running (the model is not touched); `200` with nothing done when nothing is loaded |

Both need the `X-Studio-Client: 1` header like every mutation (§11). An error answer has the usual `detail` and `code` and, for the memory case, also the `hint`. The status's `worker` object gains **`idle_timeout_min`**, which the page uses to decide whether to offer Load. The state changes themselves arrive over the event stream (`worker.state`) as they always did.

### 25.3 How the server does it (the worker's life, §9)

- **Same start, no run.** Loading ahead of time is the first half of what a run does anyway: the memory check, starting the worker process, and telling it to load (the worker's `load` command has existed since 1.0 and was never used). A model that failed to load leaves the worker running, so **Load model again** re-sends `load` to the same worker instead of starting another.
- **One at a time.** Starting and stopping the worker process is done under one lock, shared by a run starting it, a Load, an Unload and the idle timeout. So a click cannot meet a worker that is half stopped: it waits, then acts on what is really there. Unload checks that no run is running **inside** that lock.
- **The idle clock.** The job loop arms the idle timer only when the worker is loaded. While a load is in progress it waits without a timer, and the worker's *ready* (or *load failed*) report wakes it so that the countdown begins then. Unloading wakes it too, so no stale timer is left running behind a worker that is gone.
- **A run during the load.** The worker reads commands in order, so a `run` sent while it is loading waits for the load to finish and then proceeds; the page shows *Loading model…* and then *Generating*.
- **Unload while it is still loading** (API only; the page does not offer it) stops the worker process at once instead of asking it politely, since a worker busy loading would not hear a polite request until the load finished.
- **Fake pipeline for tests.** `STUDIO_FAKE_LOAD_DELAY_MS` (default 200) sets how long the fake pipeline takes to load, so a test can see the loading state and send a run during it.

### 25.4 Small choices I made (tell me if you want any changed)

- **No confirmation on Unload.** It costs a reload (minutes with the real model), but it is only offered when the model is idle, and a dialog for a button you pressed on purpose is friction. If you would rather have one, it is a small change.
- **Unload is not offered while loading.** You chose "loaded and idle". A way to abandon a load that you started by mistake would be a *Cancel loading* button; the server can already do it (25.3), and it is an easy addition if you want it.
- **The page does not use the answer to move the pill.** The state arrives over the event stream, as for every other change, so an answer that overtakes an event can never make the pill go backwards.
- **Retry on a problem is the same button.** *Model problem* with Load model next to it, rather than a separate Retry.

### 25.5 Acceptance criteria (continue §24.3)

51. **Load model** is offered exactly when the model is not loaded or had a problem and the idle timeout is above 0; **Unload model** exactly when it is loaded and idle. Neither is offered while loading, while generating, or when the model is unavailable.
52. Pressing Load model loads the model **without creating a run**: the pill goes to *Loading model…* and then *Model ready*, and the unload countdown starts when the load **finishes**.
53. Loading ahead of time makes the same **memory check** as a run: with too little free memory no worker is started, the state is *Model problem* with the message and hint, the request is answered `409`, and Load model can be pressed again.
54. A run sent **while the model is loading** waits for it, runs on the same worker, and the model is loaded **once**.
55. **Unload model** frees the memory at once when idle and is **refused with `409` while a run is running**, leaving the run untouched. After it, the next run loads the model again.
56. Pressing either button twice, or Load when the model is already loading, loaded or in use, starts nothing extra and unloads nothing it should not.
57. With `STUDIO_IDLE_TIMEOUT_MIN=0` the button is not offered and the server answers `409` `no_idle_time`.
58. After a failed load, Load model **retries on the same worker** and works once the cause is gone.
59. A model loaded with the button is **still unloaded after the idle timeout**, counted from when the load finished.
60. After pressing a button, **keyboard focus is on the model pill** and the new state is announced; on a phone the header does not scroll sideways.

### 25.6 What was built, and what differs from the plan above

- **As specified:** the two buttons beside the pill, shown by the rule of 25.1 (`modelAction`, one pure function with its own tests); `POST /api/model/load` and `/api/model/unload` with the answers of 25.2; the worker's life under one lock; the idle clock that waits out a load and starts when it finishes; the same memory check as a run; focus moved to the pill and the result announced; the idle timeout of `0` refused and not offered.
- **A real bug of my own, found by a test while building.** The first version of the lock let a run that had arrived during an Unload wait for it, as intended, but that run had already been marked as the current one, so the stopped worker's exit report was handed to it as an event and taken for a crash ("The image worker stopped unexpectedly (exit code 0)"). The test for exactly that race failed, and the fix is in `_execute`: the queue of events is emptied **after** the lock is won, not before. A server that only ever stopped the worker from the job loop (before 1.7) could not meet this; a Load or Unload from a request can.
- **Something I had not planned: the pill on a phone.** At 360 px the new button left the pill 107 px, and its label read "Model …". I measured it rather than guessing: **on a phone the pill now says its state in one word** (Unloaded, Loading…, Ready, Working, Problem, Unavailable; the full words are still on wider screens and in the details), **the decorative logo square is hidden**, and a few gaps are smaller. The title and version are still shown. This changes how the header looks on a phone even when no button is shown; it is the price of keeping the state readable.
- **Small choices of mine for you to veto,** besides those in 25.4:
  - A refusal appears as a toast ("Couldn't load the model: …"). **The hint** (for the memory check, how to free memory) is in the pill's details, not the toast.
  - **Load model is offered after a problem (the pill's *Model problem*), but not when the pill says *Model unavailable*,** because loading cannot succeed there until the server's set-up is fixed. The server still tries if asked directly, and reports the same reason again.
  - The details text for *Model not loaded* and *Model problem* now mention Load model.
  - Two test knobs for the fake pipeline: `STUDIO_FAKE_LOAD_DELAY_MS` (a slower "load") and `STUDIO_FAKE_LOAD_FAIL=once` (the first load fails, a retry succeeds). The browser tests' server uses a 1.2 s load so that *Loading model…* can be seen.
- **Tests:** backend 384 (up from 363: 21 new, in `tests/test_model_load.py`, and the two new endpoints added to the missing-header test), front end 179 Vitest (up from 169: the offer rule and the announcements) and 75 Playwright (up from 68: seven new in `e2e/model.spec.ts`, including a phone and the "only what you asked for is announced" case).
- **Mutation checks: 60 deliberate breakages (28 in the server, 13 in the offer rule, 19 in the page and its styles).** The first run caught 54, found one that did not compile, and let 5 through. Four of those were real gaps in my tests, now closed by new tests (the idle timeout stopping the worker without the lock; a worker that cannot be started; the "Unloading the model…" announcement and the disabled button while the request is on its way; a stale request speaking for a load that a run later caused, which first escaped because my assertion retried until the stale text had gone away, so it now reads once). The fifth is **equivalent**: `"busy"` in the list of states for which Load does nothing is also covered by the check that no run is current, so removing it changes nothing anyone can see. After the fixes **59 of the 60 are caught** and that one is equivalent. I dropped one mutant (swapped icons) as untestable: the icons are decorative and hidden from screen readers.
- **One unexplained failure, reported rather than hidden.** In one full browser run (5 unstressed runs of the final code in all) the thumbnails test of 1.3 failed once, waiting for a run card; it passed alone 15 of 15 under heavy CPU load and in the 4 full runs after it, and I could not reproduce it or find a cause (the page does not show the prompt box until it has the server's capabilities, which rules out one guess). Separately, under **deliberate heavy CPU load** (four spinning processes) the 1.6 test that watches an upload at 100% before removing it can miss that moment and fail: it depends on a window of about a second, which I widened with a big picture in 1.6 and cannot make airtight. Neither touches anything 1.7 changed; I left both as they are.
- **Not verified:** how long the real model takes to load, how much memory Load takes and Unload gives back, and whether a worker that is stopped while loading really frees its memory at once on the Spark (`SPARK_TEST.md` §19). Everything here has been checked only against the fake pipeline.

## 26. Music (decisions #40–#43 DECIDED; details PROPOSED; BUILT in 1.8 (the server) and 1.9 (the page))

You asked whether the interface can make music on an entirely different tab, with the code from the model card of **MiniMaxAI/MiniMax-Music3**, and said you want **instrumental** music. I asked three questions and you chose: instrumental by default with a lyrics box available, **one model in memory at a time**, and **fields that build the description**. You then asked me to build it as 1.8 and 1.9 with a usable example.

**What this section is based on.** The first version of it came from the model card as summarised by a tool, and several of its statements turned out to be wrong. Before building I read the primary sources directly: the model repository's own files (its file list with sizes, `modular_model_index.json`, the component configurations, the raw `README.md` and `LICENSE`) and the **source of the released `diffusers` 0.40.0** that contains the pipeline. Where this section says *verified*, it means read in those files. There is still **no GPU here**: nothing below has been run on the real model, and §26.7 says what is still unknown.

### 26.1 What you will see

- **Two tabs under the header, Images and Music.** Images is the studio as it is today (Generate and Edit). Music is new. The choice is remembered, the tabs are reached and switched with the keyboard (arrow keys), and each tab shows **only its own history**: the Music tab lists tracks, the Images tab lists pictures. The queue is shared (one GPU, one run at a time), and a run waiting behind another says so on either tab. If the music model cannot run on this server (its libraries are missing, say), the tab is still there and says why.
- **Describe the music with fields.** *Genre*, *Mood* (the emotional arc, e.g. "warm and intimate, building gently"), *Tempo* (BPM), *Key and scale*, and *Instruments and arrangement* (e.g. "fingerpicked guitar and soft piano; brushed drums and upright bass enter in the second half"). As you type, a **preview** shows the finished description in the **Structured Caption** layout the model's card recommends (§26.2). **The preview is editable**: once you change it by hand the fields stop overwriting it, and *Rebuild from fields* puts it back. With **Add lyrics** on, a *Voice* field appears too ("soft female lead, close and breathy").
- **Instrumental is the default.** With **Add lyrics** off the studio sends the **`[Instrumental]`** tag as the lyrics and writes "Instrumental, no vocals." into the description. (The model refuses an empty lyrics field, so the tag is the way.) **Add lyrics** reveals a lyrics box with buttons that insert the section tags the card lists (`[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`, `[Post-Chorus]`, `[Bridge]`, `[Solo]`, `[Instrumental]`, `[Outro]`) at the caret, each on its own line.
- **Length, versions, seed.** *Duration* from 10 seconds to the server's maximum (default **60**, the card's own example; maximum **300**; the model's own limit is 6 minutes). **Duration is an upper bound**: the model may end a piece sooner, and the card shows the length it really made. *Versions* (1 to 4 different tracks from the same description, seeds base … base+N−1, as for pictures). **Seed** is random by default, with *Lock seed* and *Reuse*, as for pictures. Under *Advanced*, **Rendering steps** (10 to 60, default 30, the pipeline's own default).
- **Each track is a card with a player.** A normal audio player (play, pause, seek), the description and settings, **Download WAV** (named like `music_acoustic-pop_60s_s7_20261004-141502.wav`), and the buttons a picture card has: **Reuse** (brings back the fields, the preview text, the lyrics and the seed), **Keep**, **Delete**, **Cancel** while queued or running, **Retry** after a failure. Auto-expiry applies to tracks as to pictures.
- **Progress and Cancel are real** (§26.4): while a track is made the card shows two phases, *Composing* (frame by frame, up to the length you asked for) and *Rendering* (the flow-matching steps), each with a bar, and **Cancel stops the work within a frame or a step**, without unloading the model.
- **A reminder, since the licence asks for it.** The Music tab names the model ("MiniMax-Music3"), says that the licence requires saying that music is machine-generated when you share it publicly, and every WAV carries a note in its file information ("Generated by MiniMax-Music3 …") with the description used.
- **Load model and Unload model** (1.7) sit beside the pill on both tabs and act on **that tab's model**: on the Music tab, *Load music model* (which unloads the image model first, if it is idle) or *Unload model*. The pill says which model it is talking about (*Image model ready*, *Music model loading…*).

### 26.2 The description (decision #42), and what the model does with it

*Verified in the pipeline source.* The model receives `prompt` (the music description) and `lyrics` as two texts. The pipeline cleans the description (it strips markdown headings, bullets and bold, and rewrites any `<|…|>` tag) and **lowercases the section tags in the lyrics**; the whole assembled text may be at most **5,000 tokens**. In the lyrics **a tag must be on its own line: text on the same line as a leading tag is dropped.** The lyrics may not be empty.

The card recommends a **Structured Caption** with three sections, and its own end-to-end example is written that way, so the builder writes the description like this (an empty field leaves its line out; the Vocal Details section is left out when lyrics are off):

```
Global Metadata
Basic Attributes: bpm is 96. key is C, and scale is major. acoustic pop.
Global Emotional Progression: warm and intimate, building gently.
Instrumental, no vocals.
Arrangement
Instrument Lifecycle Description: fingerpicked guitar and soft piano; brushed drums and upright bass enter in the second half.
```

With lyrics on, a `Vocal Details` section (`Vocal Gender & Timbre: …`) goes between the two. The fields are saved with the run so Reuse can restore them; the server stores them and sends only the finished text. Limits: the studio limits the description to 2,000 characters and the lyrics to 6,000 and says so by field, because it cannot count the model's tokens without loading it; the model refuses anything over 5,000 tokens and the card then reports that.

### 26.3 API (additions to §7 and §21.6)

- `POST /api/runs` takes **`mode: "music"`** with `prompt` (the finished description), optional `lyrics` (absent, empty or only whitespace means **instrumental**: the server then sends `[Instrumental]`), and `options`: `duration` (seconds, an upper bound), `seed`, **`tracks`** (1 to `STUDIO_MUSIC_MAX_TRACKS`, default 4; it takes the place of `num_images`), `steps` (rendering steps, 10 to 60, default 30) and `fields` (an object of short strings, stored for Reuse and not interpreted). The same field errors as for pictures (`422`, naming the field), `429` when the queue is full.
- **`GET /api/audio/{id}`** returns the WAV, **with HTTP range requests** (a player cannot seek without them); `?download=1` sends it as an attachment with the filename above. A run's payload has **`tracks`** (`id`, `idx`, `seed`, `seconds` (what was really made), `sample_rate`, `channels`, `bytes`, `url`, `download_url`) as an image run has `images`, and `lyrics`.
- **`GET /api/capabilities`** lists `music` in `modes` exactly when the music pipeline can run (the fake one always can), gives `limits.music` (`duration` {min, max, default}, `tracks` max, `steps` {min, max, default}, the text limits), and **`music`** {`available`, `reason`, `hint`} so the page can explain an unavailable tab.
- **`POST /api/model/load` and `/unload`** (§25.2) take an optional `{"model": "image" | "music"}`; without it, the image model, as before. `GET /api/status` and the `worker.state` event say **which model** the worker holds or last tried to load (`worker.model`).
- Nothing else changes: `run.*` events, cancel, Keep and delete work on a music run as on any run.

### 26.4 One model at a time (decision #41), and the worker

- **The worker process is the same program started for a different model.** `python -m studio.worker --kind music` loads MiniMax-Music3, `--kind image` loads Qwen-Image as now; the JSON-lines protocol (§9) is the same, with the job carrying the description, lyrics, duration, steps and seeds, and `image_done` replaced for music by **`track_done`** (`idx`, `seed`, `path`, `seconds`, `sample_rate`, `channels`). The API holds **one** worker at a time.
- **Switching.** When the next run (or a Load) needs the other model, the API, under the lock of §25.3, stops the loaded worker, **checks free memory** (the stopped model's memory now counts), and starts the other. It **never does this while a run is running**; a queued run of the other kind waits its turn in the queue, which stays first-in, first-out (drafts first), so a music run between two pictures costs two model loads. (Grouping runs by model to avoid that is an idea, not planned.)
- **The memory check** is the same rule with its own number: `STUDIO_MUSIC_MIN_FREE_GB` (default **40** where `compose.yaml` sets it, an unmeasured starting estimate like the image one). *Verified from the file list:* the model is about 8B parameters of language model (17 GB on disk, bfloat16), a 2.4B-parameter transformer stored as 32-bit floats (9.7 GB on disk, about 5 GB in bfloat16), and about 2 GB of smaller parts, so **roughly 24 GB in memory**, as the card says. If the check fails the run (or the Load) fails with the same message and hint as for pictures, and nothing is left loading. **Note the order:** the other model has already been unloaded by then, so a failed music load leaves nothing loaded.
- **The idle timeout and Unload** apply to whichever model is loaded.
- **Settings** (added to §13 when built): `STUDIO_MUSIC_MODEL` (default `MiniMaxAI/MiniMax-Music3`), `STUDIO_MUSIC_MIN_FREE_GB`, `STUDIO_MUSIC_MAX_SECONDS` (300, up to 360), `STUDIO_MUSIC_MAX_TRACKS` (4) and `STUDIO_MUSIC_LIBS` (a folder put first on the music worker's Python path, set by the image).
- **A second copy of `diffusers` for the music worker.** *Verified:* the commit the image worker is pinned to (`578c9b2…`, §12) has the Qwen-Image-2.1 pipeline and **not** the music one, and the released `diffusers` **0.40.0** has the music pipeline and **not** Qwen-Image-2.1. Neither version serves both, so the image puts **`diffusers==0.40.0`** (the release, not the pull-request commit the card names) into its own folder, `/opt/music-libs`, installed without its dependencies (the image already has them), and starts the music worker with that folder first on its Python path. The image worker never sees it. `docker/check_image.py` imports the music pipeline that way too, so a broken music setup fails the build and not the first track. Without it (a development machine, the tests) the **fake** pipeline needs nothing.
- **Progress and Cancel without changing the model's code** (*verified* against the pipeline source). The composing stage is a Python loop that calls the language model's output layer (`lm_head`) **once per audio frame**; a hook on that layer counts frames (the progress, out of the most the length allows) and, when Cancel has been asked for, raises the same `Canceled` signal the image pipeline uses. The rendering stage reports through a progress bar the pipeline creates for each run; the worker replaces that bar's class with one that reports each step and checks for Cancel. The last stage (turning the result into sound) takes no steps and is not interruptible. So **Cancel takes effect within a frame while composing and within a step while rendering**, and the model stays loaded. This is only checked against the source and, in 1.8, a small random-weight copy of the pipeline on a CPU (§26.7); the real model is the Spark test's job.
- **The fake music pipeline** (for every automated test): the same description, lyrics, duration and seed always produce the same short **tone-and-noise WAV** (shorter than real time, so tests are quick), reports both phases, honours Cancel, and has directives like `[fake:error]` as for pictures. As with pictures, it proves the plumbing, not the music.

### 26.5 Data (schema version 3)

- **`runs`** accepts `mode = 'music'`. The table has a `CHECK` on `mode` that SQLite cannot change in place, so the migration **rebuilds the table**; the old database is copied first and the migration is checked against a database written by the real 1.7 code, as schema 2 was (§21.7). Music settings (the fields, `duration`, `tracks`, whether it was instrumental) are in `options_json`, the finished description is in `prompt`/`effective_prompt`, and the lyrics get a nullable column. Columns that mean nothing for music (size, guidance) are stored as for a run that has none; `steps` holds the rendering steps and `num_images` the number of tracks.
- **`tracks`** (new, like `images`): `id`, `run_id` (cascades with the run), `idx`, `seed`, `seconds`, `sample_rate`, `channels`, `bytes`, `path`, `created_at`.
- **Files:** `<data>/audio/<run>/<idx>.wav`, written by the worker (atomically, with the file information note), checked by the API to be inside the run's own folder before it is trusted, as for pictures. Deleting a run or expiring it removes its audio. The WAV is written by a small routine of the studio's own (16-bit stereo PCM plus an information chunk), which needs nothing beyond the standard library.
- **Sound format.** The model's card says 32 kHz, but the pipeline and the vocoder's configuration say **44.1 kHz** (verified); the studio writes whatever the pipeline reports. At 44.1 kHz, 16-bit stereo, a WAV is about **10.6 MB a minute**: a 5-minute track about 53 MB, four of them 210 MB. With 30-day retention a busy month is gigabytes; **Keep** and the retention setting apply as before. (FLAC or MP3 downloads would be smaller; not planned.)

### 26.6 Releases, and the order of work

- **1.8, the server (built):** schema 3, the music worker kind and the fake and real pipelines, `tracks` and `GET /api/audio`, the one-model-at-a-time switching, `STUDIO_MUSIC_*` settings, the cache-first loading of both models (decision #43), the second copy of `diffusers` in the image and in `check_image.py`, the API tests, and **`scripts/minimax_music.py`**, a command-line example that makes a track with the real model (moved here from 1.9, because it needs no page and is the quickest way to try the model on the Spark). **No visible change** except that the model's name appears in `/api/status`.
- **1.9, the page (built, §26.13):** the tab bar, the Music tab (fields, preview, lyrics, duration, versions, seed, steps), track cards with the player and download, Load/Unload per tab, phone layout and the browser tests.
- **Then you run `SPARK_TEST.md` §20** (written with 1.8, with the page's part added in 1.9): the real model's speed per minute of music, its memory, whether `[Instrumental]` makes an instrumental track, whether "no vocals" in the description helps, and what Cancel does.
- **Publishing.** 1.7 could not be pushed (§20, Round 15), so 1.8 and 1.9 are built on top of it, each on its own branch (`music-1.8`, `music-1.9`) so they can still become one pull request each, in order, once 1.7 is merged.

### 26.7 What I know now, and what I still cannot know from here

**Verified from the model's own files and the 0.40.0 source (and so no longer unknown):**
- `lyrics` must be non-empty; `[Instrumental]` is a documented section tag. The "empty lyrics also works" claim in the summary I first read is false.
- No `trust_remote_code` is needed: every component is a `diffusers` or `transformers` class.
- The output is `(batch, channels, samples)` in [−1, 1] at the **vocoder's rate, 44.1 kHz**.
- `audio_duration` is an upper bound, capped at 9,000 frames (six minutes); the pipeline's `num_inference_steps` defaults to 30 and the other sampling settings are fixed by the pipeline.
- **Progress and Cancel** are possible by hooks (§26.4). The pipeline's own progress bar covers only the rendering stage.
- **Download size:** the repository is **57 GB** in all, but about **29 GB** of that are the components the pipeline loads (the rest, the two `.pth` files and a second copy of the language model, belong to the card's other way of serving it). Whether `from_pretrained` fetches only the components or the whole repository I cannot tell: **have 60 GB free** to be safe. The two `.pth` files are Python pickles: the pipeline does not load them, and I will not.

**Still unknown (the Spark's job):**
1. **Speed.** The card gives none. My arithmetic, not a measurement: the language model must read its 16 GB of weights for every audio frame and there are 25 frames a second, and the Spark's memory moves about 273 GB/s, so composing alone takes **at least about 1.5 seconds per second of music**. Expect minutes for a minute of music, and **a five-minute track could take the best part of half an hour**. That is why Cancel and the progress phases matter, and why 10-second and 30-second durations are one click away.
2. **Whether `diffusers` 0.40.0 imports cleanly next to the image's `transformers` 5.18** (it is newer than the 5.13 the model was exported with, so it should). The 1.8 build checks the import in the image; if it fails, the music tab says why.
3. **Whether `[Instrumental]` alone gives a track with no singing**, and whether "no vocals" in the description changes anything. §20 of the Spark checklist compares.
4. **Real memory** (the arithmetic above says about 24 GB) and **whether the same seed gives the same track** on the real model.

### 26.8 The licence (read directly this time)

The model is under the **MiniMax-Music3 Community License**: an MIT-style licence with conditions. *Read from the raw `LICENSE`:* use, copy, modify and distribute are permitted subject to (1) keeping the copyright notice, (2) obeying the law and the **Acceptable Use Policy** (Exhibit A), (3) for a **commercial product or service**, displaying "MiniMax-Music3" on its interface and, above US$20 million of yearly revenue, getting MiniMax's written authorisation, and (4) if you **provide the model's outputs as a product or service to third parties**, keeping reasonable safeguards against uses and outputs that break the licence. There is an indemnity clause. The Acceptable Use Policy includes: no use that breaks the law or infringes others' rights, harms people, exploits minors, impersonates a person without consent, or defames; and item 11: **no distributing generated content "in or to any public environment" without clearly and prominently disclosing that it is machine-generated**.

What that means for the studio as I read it (I am not a lawyer, and it is your licence to read): a personal studio on your own network is not a commercial product or a service to third parties, so (3) and (4) do not apply; **item 11 does apply the moment you publish a track**. The Music tab names "MiniMax-Music3", reminds you of the disclosure, and writes the note into each WAV. If other people use your studio, (4) starts to matter, and the studio has no login (§11).

### 26.9 Small choices I made (tell me if you want any changed)

- **Separate history per tab**, not one mixed list.
- **Defaults: 60 seconds, a 300-second maximum, up to 4 versions, 30 rendering steps.** All are settings or controls.
- **Duration chips** (15 s, 30 s, 1 min, 2 min, 3 min, 5 min) beside the field, since the slow speed (§26.7) makes a short try the sensible way to test a description. There is no separate "Draft" button.
- **WAV only**, as in the card's example.
- **The note written into each WAV**, the model's name on the tab, and the licence reminder.
- **A 2,000-character description limit and 6,000 for lyrics**, because the real limit is in the model's tokens.
- **A tab that explains itself** when the music model cannot run, rather than a missing tab.

### 26.10 Acceptance criteria (continue §25.5)

61. The page has **Images** and **Music** tabs, reachable and switchable with the keyboard, remembered across a reload, and each shows only its own runs. (1.9)
62. **Music** is offered exactly when the music pipeline can run: the capabilities' `modes` lists `music` if and only if it can; when it cannot, the tab says why. (1.8, 1.9)
63. A music run with **no lyrics** is instrumental: the server sends `[Instrumental]` and the description says "Instrumental, no vocals." With **Add lyrics** on the lyrics are sent as written, and the Voice field exists only then. (1.8, 1.9)
64. The fields **build the description** in the layout of §26.2 and leave out empty ones; editing the preview by hand stops the fields overwriting it until *Rebuild from fields*; **Reuse** restores the fields, the text, the lyrics and the seed. (1.9)
65. **Duration**, **versions** and **steps** are limited by the server (defaults 60 s, 4 and 30, maximums `STUDIO_MUSIC_MAX_SECONDS` and `STUDIO_MUSIC_MAX_TRACKS`); an out-of-range value is refused, naming the field. (1.8)
66. A finished music run shows an **audio player per track** that can seek (range requests), the real length the model made, and **Download WAV** with a meaningful filename. (1.8, 1.9)
67. **Only one model is in memory.** A music run while the image model is loaded stops it first and loads the music model, and the reverse; never while a run is running; the pill says which model. (1.8)
68. Every load makes the **memory check** with its own number; with too little free memory the run or the Load fails with the actionable message and **nothing is left loading**. (1.8)
69. **Load model and Unload model** on each tab act on that tab's model. (1.8, 1.9)
70. **Cancel**: a queued music run is canceled at once; a running one stops within a frame while composing or a step while rendering, the card says *Canceled*, tracks already finished are kept, and the model stays loaded. (1.8)
71. The same description, lyrics, duration, steps and seed give the same track with the **fake** pipeline. For the real model whether they do is a **Spark** question. (1.8; Spark)
72. **Keep, Delete, auto-expiry, Retry** and surviving a restart work for music runs; deleting or expiring a run removes its audio. (1.8)
73. An **existing database migrates** to schema 3 without losing a run, an image or an input, and the old file is kept as a copy. (1.8)
74. The Music tab **names the model and shows the disclosure reminder**, and every WAV carries the machine-generated note. (1.8, 1.9)
75. On a **phone** the tab bar and the Music tab fit with no sideways scroll, the player and its buttons stay on screen. (1.9)

### 26.11 Using the local cache (decision #43)

You asked: after the first load, no downloading if possible.

- **Where the files live.** Both models are kept in the Hugging Face cache, which `compose.yaml` mounts from your host (`HF_CACHE_DIR`, by default `~/.cache/huggingface`) at `/models`. It survives rebuilding and recreating the container and is shared with anything else on the Spark that uses the default cache.
- **What happened before 1.8.** `from_pretrained` asked the Hugging Face hub on every load whether anything had changed. That is a few small requests when nothing has, but **if the model's authors push an update, the next load downloads it**, which for the music model is tens of gigabytes. `STUDIO_LOCAL_FILES_ONLY=true` stopped that for the image model, but it was off by default and it made the very first load fail.
- **What 1.8 does.** `STUDIO_LOCAL_FILES_ONLY` takes three values: **`auto` (the new default):** each model is loaded **from the cache only**, with no network request at all; **only if that fails because files are missing** does the studio try again online (the first load, or after the cache was cleared), and the log says so. **`true`:** never goes online; a missing file fails the load with "the model is not in the local cache" and says how to download it. **`false`:** the old behaviour. The same setting governs both models. Any other failure (out of memory, a broken file) is reported as it was, not retried online.
- **Download it ahead of time, the music model without the parts it never loads.** The first load of the music model downloads about 29 GB, and your first Load model press does exactly that, with the pill saying *Loading…* for the length of the download. To fetch it separately, on the Spark: `hf download MiniMaxAI/MiniMax-Music3 --include "modular_model_index.json" "config.json" "condition_encoder/*" "language_model/*" "rvq_depth_decoder/*" "scheduler/*" "tokenizer/*" "transformer/*" "vocoder/*"` (it leaves out the two `.pth` files and a second copy of the language model that this pipeline does not use). `SPARK_TEST.md` §20 has the command in context.
- **A model with a part missing does not count as loaded.** `diffusers` turns a part that fails to load into a log warning and carries on, so a model folder with a part missing (a download that stopped part-way) used to count as loaded and then fail at the first track. The loader now checks that every part it should have loaded is there. A missing part is treated as "files are missing": in `auto` mode a half-cached model is completed online, and otherwise the load fails with a message that names the parts and says to check the folder (does it hold a folder of that name, and can the studio's user read it?) or to download the model again. The tokenizer is loaded with `fix_mistral_regex=False`: transformers cannot tell that this Qwen tokenizer, loaded from a local folder whose `config.json` has no `transformers_version`, is not a Mistral one, and warned about "incorrect tokenization"; the setting says so and changes nothing else.
- **Nothing else downloads at run time.** The libraries are installed when the image is built, and the studio sets `HF_HUB_DISABLE_TELEMETRY=1` for its workers so they do not report usage.

Acceptance criteria:

76. With `STUDIO_LOCAL_FILES_ONLY=auto` (the default) a model is loaded with the network switched off first, and **only if that fails because files are missing** is the load tried again online; any other failure is reported at once and is not retried online. (1.8)
77. With `true` a model is never loaded online and a missing file fails the load with an actionable message; with `false` the load is as before. The setting applies to the image and the music model alike. (1.8)

### 26.12 What was built in 1.8 (the server), and what was not checked

Built to §26.3–§26.5 and §26.11, plus **`scripts/minimax_music.py`**, which was planned for 1.9 and moved forward so that you have something to run on the Spark before the page exists (it uses the same pipeline class the studio uses, so it is also the quickest check of the real model).

- **Music runs through the API:** `mode: "music"` with the description, optional lyrics (none = instrumental, the server sends `[Instrumental]`), duration, versions, steps, seed and the fields; limits from `STUDIO_MUSIC_MAX_SECONDS` and `STUDIO_MUSIC_MAX_TRACKS`; tracks as WAV files written by the studio's own standard-library writer (a note in each says it is machine-generated), served by `GET /api/audio/{id}` with range requests; the capabilities say whether music can run and why not.
- **One model in memory at a time:** one worker program started for either model; a run or a Load for the other model stops the idle one first, checks free memory with the new model's own number and starts it, all under the lifecycle lock of §25.3. `worker.model` says which model the worker holds.
- **Schema 3** (the table that holds runs is rebuilt to allow music; `tracks` is new), migrated from a copy of a database written by the real 1.7 code; the old file is kept as a copy.
- **Models load from the cache** (decision #43, §26.11), for both models; the worker's environment sets `HF_HUB_DISABLE_TELEMETRY=1`.
- **A second copy of `diffusers`** (0.40.0, in `/opt/music-libs`, first on the music worker's path only) in the image and in `docker/check_image.py`.
- **Tests:** backend 598 (pytest, the fake pipeline) and 22 more that run only where `torch` and `diffusers` 0.40.0 are installed: they run **the real `diffusers` 0.40.0 music pipeline on tiny random-weight components on a CPU** (`tests/music_tiny.py`), which proves the plumbing of the real path (the frame hook and the progress bar, Cancel from inside both stages, the output shape and rate, the worker, the example script and that it puts the music libraries first on its path) without proving anything about the sound. **Mutation checks:** 122 mutants of the music server, the worker, the WAV writer, the cache logic, the real pipeline's hooks, the example script and the worker's naming. The first runs let **11 survive**; each got a test that fails with the mutant and passes without it (checked one by one), except one that is equivalent: a model name that is neither `image` nor `music` is refused twice, by the request schema and by the model manager, with the same status code. Two mistakes of mine in the *checking*, not the code, were found and corrected: the script's clean-up killed other servers' workers (which looked like a flaky Load until I found the cause), and its `git checkout` once wiped a fix I had not committed yet; the affected mutants were run again.

**A bug found on the way (and fixed).** A worker that was stopped on purpose reported its exit as the current event, and the next run that was waiting took it for a crash. It showed first as a flaky Load test in 1.7 and again, in a new form, when switching models. The fix is the same in both places: the stale events are drained after the right worker is ready, inside the lock. Both have tests that failed before the fix.

**A smaller thing found at the end (and fixed).** The worker client wrote "image worker" in its log lines, and the run's error message said "The image worker stopped unexpectedly" even when it was the music worker that had stopped. It now names the worker it is.

**What this does not show, and only the Spark can:** whether the real model loads next to the image worker's libraries (the image build checks the import, not a run); how long it takes per second of music; its real memory; whether `[Instrumental]` alone gives a track with no singing; whether the same seed repeats; how fast Cancel stops it; how big the first download really is (the whole repository, 57 GB, or the 29 GB of components); and that nothing downloads on later loads. `SPARK_TEST.md` §20 is the checklist for all of these.

### 26.13 What was built in 1.9 (the page), and the small choices I made

Built to §26.1, with these details, which the spec left open (tell me which you want changed):

- **Tabs.** Real ARIA tabs: the arrow keys, Home and End move between them (selecting follows focus). Both panels stay on the page and the one that is not open is hidden, so a half-written description is not lost when you switch and a track keeps playing while you look at pictures. A tab with a run queued or going shows a **dot** (and "(working)" for a screen reader) while the other tab is open. The open tab is remembered (`studio.tab.v1`) and so is the Music form (`studio.music.v1`), every value checked on its own when it is loaded, as the picture options are.
- **The model pill names the model** that is loaded: *Image model ready*, *Loading music model…*, *Music model ready*. With nothing loaded it says *Model not loaded*, because there is no model to name. On a phone it keeps its short words (*Ready*) and the full name is its accessible name.
- **The button beside the pill acts on the open tab's model.** The Images tab keeps *Load model* and *Unload model*, unchanged since 1.7; the Music tab has *Load music model* and *Unload model*. When the **other** model is loaded and idle, a tab offers to load its own (the tooltip says the other one is unloaded first) instead of an Unload, so a button never unloads a model its tab is not about. It is never offered while a run is going or a model is loading, nor for music where the server cannot run it. The page names the model in every request (`{"model": "music"}`), and the announcements say *Music model ready.* and so on.
- **The description box** is editable and shown in a fixed-width font, because its line breaks matter. The boxes above it rewrite it as you type until you change it by hand (then it says so); *Rebuild from fields* puts the boxes' version back. **Make music is disabled until something is described** (the builder always writes its heading, so an empty form must not count), and **Add lyrics with no lyrics** is refused on the page with a message rather than sent as an instrumental track.
- **Length, versions, seed.** The length shortcuts offered are the ones inside the server's range; the Seconds box checks the range as you type and only commits a valid number. Versions are 1 to the server's maximum. **Advanced** holds the rendering steps and *Lock seed*; after Reuse its summary says *seed locked to N*.
- **Track cards.** A player per track (it loads the file's header, so the length shows and seeking is immediate), a name that says which version it is and how long the model really made it, with *(you allowed up to 1:00)* when it stopped sooner, **Download WAV**, *Reuse*, *Copy description*, *Keep*, *Delete*, *Cancel* (a running one asks first) and *Retry*. **Only one track plays at a time.** Progress is two bars, *Composing* and *Rendering*, with the version it is on. The download's file name uses the length the model made (`..._6s_...`), not the one you allowed, because that is what is in the file.
- **Each tab shows only its own runs.** *Load older runs* pages through the shared history, so a page of older runs can add nothing to the tab you are on; the button stays until the history is exhausted.
- **When music cannot run** the tab stays and explains: the reason and the hint the server gives, or *Checking the music model…* while the start-up check is still going; no form, no Load button.
- **The licence reminder** and the model's name (a link to its page) are under the form, and the file note is in every WAV.
- **Tests:** front end 257 (Vitest: the description builder with the same golden cases as the server, the form's rules, the request, Reuse, the section tags, the model buttons and announcements) and 98 in a real browser (23 for the Music tab, including a phone width, one-track-at-a-time, range requests and the model swapping). **Mutation checks:** 93 mutants of the page's logic and components; every one is caught, after boundary tests were added for the four the first run missed.

**A thing the browser tests caught in their own wording, not in the page:** the 1.7 tests asserted the old pill and announcement text (*Model ready*), which now names the model; they were updated. And one browser test was thrown off by a **mutation-testing script of mine that killed every worker process on the machine** (`pkill` by name): a reminder to kill only what a test started.

**Not checked, because only the Spark can:** how the page feels with real waits (a track that takes ten minutes), and how the player behaves on a phone over your network.

## 27. Version 1.10: Make 4K (decision #44 DECIDED; details PROPOSED; route A built in 1.10, route B and C not built)

You asked for the final output to be **at least 4K at 16:9**. I read that as **3840×2160** (UHD). DCI 4K, 4096×2160, is not 16:9; say so if you meant it.

### 27.1 What the model can and cannot do (checked in the code and the model's own pages)

- **It cannot make 3840×2160 directly, even with the pixel limit lifted.** Both sides must be multiples of 32 (the pipeline rounds a size down to the grid), and 2160 is 67.5 × 32: it would make 3840×2144. The sizes it can make that are exactly 16:9 are 2048×1152, 2560×1440, 3072×1728, 3584×2016 and 4096×2304; 3840×2160 is not among them. A direct route still needs a trim of 16 pixels.
- **Its own 16:9 size is not exactly 16:9.** The model card's preset is 2752×1536 (4.23 MP), a ratio of 1.7917 against 1.7778: 0.8% too wide.
- **3840×2160 is 8.29 MP**, twice that preset and 1.84 times the studio's own limit of 4.5 MP. That limit is my guess at a safe size (§18 item 7): the pipeline has **no maximum** (the source at the pinned commit checks only the multiple of 32), and the position tables of the transformer reach far beyond 4096 pixels. What the model card says is only that it "natively supports 2K resolution". Nobody has published what it does above that.
- **The Edit-mode Upscale of §23.2 cannot meet this.** Edit's Resolution is 1K or 2K, so its result stays near 4 MP.

### 27.2 Three routes

| Route | What it does | State |
|---|---|---|
| **A. Resize** | Enlarge any picture to cover 3840×2160 with a standard resize (Lanczos); a 16:9 one, such as the model's 2752×1536, is first trimmed to exactly 16:9 so the copy is exactly 3840×2160. No new model, no GPU | **Built in 1.10** (§27.3) |
| **B. An upscaler model** | The same, but the enlarging is done by a super-resolution network (an ESRGAN-class model loaded with `spandrel`, which is pure Python and so has no ARM64 build problem). It adds plausible detail; a resize cannot | **Not built.** `scripts/upscale_probe.py` (built in 1.10) is run once on the Spark to show that one loads and runs there, and how long it takes; the feature is specified after that (§27.5) |
| **C. Directly** | Raise the pixel limit and generate about 3840×2176, then trim 16 pixels | **An experiment, not a feature.** It needs a setting for the pixel limit (offered, not built). It is untested territory for the model: twice the tokens of 2K (32,640 against 16,384), about four times the attention work, no tiling in the image decoder, and composition may break up (§27.6) |

### 27.3 Route A: what 1.10 does

- **Where it is offered.** On **any picture the studio holds**: every **result** of a Generate or Edit run, and every **source** image an edit was given (which can be opened in the viewer). It is in the **viewer** for each of them and on the **card** for a run with one result (as Download and Thumbnail are). Pictures made before 1.10 qualify too: nothing about them has to change. A picture the studio does not hold can be done from the page as well (§27.9). The rule is the server's, sent with each picture as `can_4k` and `four_k_size` (what it would make), so the page repeats none of it.
- **The rule: cover 4K, without trimming.** "4K" is the frame **3840×2160** (**2160×3840** for a portrait picture). A picture is enlarged, keeping its shape, until it covers that frame: the scale is the larger of 3840 ÷ its long side and 2160 ÷ its short side. So 2048×2048 becomes 3840×3840, 2400×1792 becomes 3840×2867 and 2528×1696 becomes 3840×2576; nothing is cut off, and one side is exactly the frame's. It is offered when that enlargement is **more than 1×** (otherwise the picture is already 4K) and **at most 2×** (more than that is a blurry picture, not a 4K one: regenerate larger, or wait for an upscaler model, §27.5), and when the copy is **at most 20 megapixels** (a guard for odd shapes and for pictures from outside; a square 4K copy is 14.7).
- **A 16:9 picture is trimmed to exactly 3840×2160.** A picture within **2%** of 16:9 (or of 9:16, upright) is first trimmed to exactly that shape, **equally from both ends, with no stretching** (for the model's own 2752×1536 that is 21.3 pixels of width, 10.7 from each side), so the copy is exactly the frame a screen has. Any other shape is not trimmed. The cut is a fractional box handed to the resize, so the picture is resampled **once**.
- **The result.** A **PNG** of that size, resampled with Lanczos in that one pass; transparency is kept; the PNG text of the original (prompt, seed, steps, model) is kept and one line is added saying how this file was made. It is encoded at PNG level 1: measured here, 0.6 s against 2.6 s at the default level for a file about 12% larger (14.1 MB against 16.0 MB for a 2752×1536 test picture with fine texture). The resize itself took 0.15 s. These are this sandbox's times, not the Spark's.
- **Where it lives.** `<data>/images/<run>/<idx>-4k.png` for a result and `<data>/inputs/<run>/<position>-4k.png` for a source, **beside the original**, which is never touched. There is **no database change** (schema stays 3): a 4K file "exists" when its file does. It goes when the run goes (delete, auto-expiry), is covered by Keep, and is made atomically (a `.part` file, then a rename). It is made once: asking again returns the one that is there.
- **The API (§7).** `POST /api/images/{id}/4k` makes it (`201`) or finds it (`200`) and returns the **updated run**, as Keep does; `GET /api/images/{id}/4k` sends it, with `?download=1` for an attachment named like the image with the copy's size in it (`3840x3840`; a source is named `source-<position>_<prompt words>_<size>_<time>.png`). Every result image and every source in a run's payload gains `can_4k`, `four_k_size` (`null`, or `width`, `height` and `trimmed`: what Make 4K would make) and `four_k` (`null`, or its `width`, `height`, `bytes`, `url`, `download_url`), so a reload, a restart or another open tab shows the button that applies. Other tabs learn of it from a `run.updated` event. Errors: `404` for an unknown image, a staged upload no run owns, or a missing file, `422` `not_4k_eligible` with the reason in words (or `unreadable` when the picture's file is damaged), `507` when the disk is full (no partial file is left).
- **No queue, no GPU.** It is a few seconds of CPU work in the API process, off the event loop, so it works **while a run is generating**. Two requests at once make one file.
- **The page.** *Make 4K* becomes *Making 4K…* and then *Download 4K* with its size in the tooltip. The tooltip says what it will make and what it does not do: *Make a 3840×3840 copy of this picture with a standard resize. It makes the picture bigger, not sharper: no detail is added.* (for a 16:9 picture: *Make a 3840×2160 copy of this picture, trimmed to exactly 16:9, …*). The answer is a toast on a card and a note **inside the viewer** when made there (§24.2). While it is being made the button stays the same button (*Making 4K…*, `aria-disabled`, so a keyboard user keeps their place and a second press does nothing); when it is done it is replaced by the *Download 4K* link, and **focus goes to that link** if the person had pressed Make 4K and focus was lost (on the page, or on the viewer itself). A person who moved on to something else while it was being made keeps their place. A failure is said in the server's own words after *Couldn't make 4K:*, and the button stays so that it can be tried again. The disabled **Upscale** placeholder of 1.4 is **removed**: Make 4K does its job.

### 27.4 What route A does not do

It adds no detail. At a factor of 1.4 (2752 to 3840 wide) I expect it to look fine on a screen (a square's 1.9× is a bigger ask), but I have not measured that, and an upscaler (route B) is the answer if it does not. It is not offered for a picture that needs more than a 2× enlargement (drafts, small pictures) or that is already 4K, and it does not change the picture you generated, so a 4K file and its original differ in size (and, for 16:9, in a sliver at the sides) only.

### 27.5 Route B: the probe, and how an upscaler would join (feature not built)

**The probe, `scripts/upscale_probe.py` (built in 1.10).** It loads one model with `spandrel`, upscales a picture in tiles with a little context around each, and reports the device, the time and the memory. For a 16:9 picture it also writes the 4K file Make 4K makes today and a 4K file made from the model's output (trimmed by the same box, scaled), to compare. The ×2 model needs sizes in multiples of 4, so each tile is padded for the model and cut back; the cores of the tiles are all that is kept, so they join without seams. It needs `spandrel`, which the image does not contain: the script is copied into the running container (`docker compose cp`) and `pip install --user spandrel` is run there, so nothing is added to the image and nothing lasts past the container. (The image's `.dockerignore` lets through only `scripts/minimax_music.py`, the one script the Dockerfile copies, so the probe is not in the image however you build it.)

**What I could check here.** With the real `RealESRGAN_x2plus.pth` (67,061,725 bytes; the checksum is in `SPARK_TEST.md` §21): `spandrel` 0.4.2 loads it as an ESRGAN ×2 network of 16.7 million parameters that supports fp16 and bfloat16 and needs input sizes in multiples of 4; the probe ran it end to end on a CPU (a 512×288 crop took 14 s there) and its output is a clean, seamless upscale. Its tiling is tested against stand-in models (a tiled result equals the whole-picture result exactly when the overlap covers the model's reach, and differs without overlap, so the test can fail).

**What only the Spark can show:** that it runs on the GB10 (`sm_121`) with NVIDIA's PyTorch (2.9 in the 25.10 container), how long a whole 2752×1536 picture takes, how much memory it holds, and whether bf16 is as good. One source I read, a blog post about a video pipeline in ComfyUI, says Real-ESRGAN x2plus ran on a DGX Spark (316 s to upscale 362 frames from 960×540 to 1080p); it used a pip-installed PyTorch 2.13 with CUDA 13, not NVIDIA's container this studio uses, so it is encouraging and not proof.

**How it would join Make 4K.** The ESRGAN-class models I know of are small (tens of megabytes), so they fit next to whichever model is loaded; whether to keep one in the worker or give it a worker of its own is decided from the probe. Make 4K would offer the upscaler for the 2× step and a Lanczos reduction to exactly 3840×2160 (the detail comes from the network, the exactness from the reduction). An upscaler cannot be applied to the transparent channel; that would be resized as now. **Open:** which model, its licence (each has its own), and whether a network's invented detail is what you want in a face.

### 27.6 Route C: what to watch if you try it

Memory (no tiling in the decode step), time, and above all whether the picture holds together. The comparison that matters is the same prompt through A and through C at 3840×2176; I suggest 2560×1440 first, which is an exactly-16:9 size inside the model's reach.

### 27.7 Decision #37 is superseded

Upscale through Edit mode is dropped from the plan: its ceiling is near 2K, it would need a Spark test nobody has run, and it could change the picture. The disabled button goes with it. If the Spark shows Edit can *refine* a picture without changing it, that can come back as its own feature.

### 27.8 Acceptance criteria (continue §26.11)

78. **Any picture the studio holds**, a result or an edit's source, whose enlargement to cover 3840×2160 (2160×3840 upright) is more than 1× and at most 2×, and whose copy is at most 20 MP, gets **Make 4K** in the viewer (and on the card, for a run with one result). Pictures already 4K, pictures needing more than 2× and copies over 20 MP do not, and the server says which with `can_4k`. A staged upload no run owns has none. (1.10)
79. Make 4K produces the **frame size** for a 16:9 picture within 2% (exactly **3840×2160**, trimmed equally from both ends without stretching; a 9:16 one exactly **2160×3840**), and for any other shape the picture **scaled to cover the frame, not trimmed**: the long side exactly 3840 (or the short side exactly 2160, whichever the enlargement needs) and the other in proportion to the nearest pixel. It is resampled once, transparency is kept and the original file is not changed. (1.10)
80. The file sits **beside the original**, is made once (a second request returns it with `200`), is still there after a reload and a restart, shows as **Download 4K**, and goes when the run is deleted or expires. (1.10)
81. The download is named like the image with `3840x2160`; its PNG text is the original's plus one line. (1.10)
82. A request for an ineligible image is refused with `422` and the reason; an unknown image or a source is `404`; a full disk is `507` and leaves no partial file. (1.10)
83. Two requests at once make **one** file; other open pages learn of it by `run.updated`. (1.10)
84. It works while a run is generating and does not touch the worker. (1.10)
85. The disabled **Upscale** button is gone from cards and from the viewer. (1.10)
86. On a **phone** the viewer's bar, with the new button, fits with no sideways scroll. (1.10)
87. **Spark:** the time and size of a real 4K file, and how it looks next to the original (`SPARK_TEST.md` §21). (Spark)

### 27.9 Any picture: an edit's sources, and a picture from your computer (decision #45)

You asked whether existing pictures, and the picture in the viewer, can be upscaled, and for the code if not. **Existing pictures:** the first version offered Make 4K only for 16:9 pictures at least 1920 wide (it did work on pictures made by 1.9: that was checked by making them with the 1.9 code and opening the same data with 1.10), so not for your default 2048×2048, nor 4:3, 3:2 or portrait pictures, nor an edit's sources, nor a picture from outside. This section closes those gaps.

- **The rule** is §27.3's: cover 4K without trimming, for any shape; exactly 3840×2160 for a 16:9 one.
- **An edit's source images** are pictures the studio holds, so they get Make 4K and Download 4K in the viewer, with their copy beside them in the run's `inputs/` folder; it goes with the run. A source is named `source-<position>_<prompt words>_<size>_<time>.png` when downloaded.
- **A picture from your computer.** The Images tab has an **Upscale a picture…** button above your runs. You choose a PNG, JPEG or WebP file; the page sends it to `POST /api/upscale?name=<the file's name>` (the file is the raw request body, as for uploads, §21.6) and the server answers with the **4K PNG**, which the page saves as `upscale_<name>_<size>_<time>.png`. **Nothing is stored and nothing is added to the history**: it is a tool, not a run, so there is no card, no Keep and no expiry. The file is checked exactly as an upload is (`decode_upload`: the type is decided by decoding, 20 MB, 16 MP, a phone photo's rotation is applied, transparency is kept, 16-bit greyscale is not clipped), then the rule above is applied; a picture the rule does not accept is refused with the reason in words (`422` `not_4k_eligible`), a file that is not an image with `415`, a damaged one with `422`, one over the limits with `413`. At most two are made at once; the rest wait. The same CPU work as a card's Make 4K (a few seconds, no GPU).
- **What it is not:** it is not a way to use the image model, and it adds no detail (§27.4). It is the same resize.

Acceptance criteria (continue §27.8):

88. A picture **made before 1.10** gets Make 4K when it qualifies, with no migration. (1.10)
89. An edit's **source image** has Make 4K in the viewer; its copy sits beside it, is made once, and goes with the run. (1.10)
90. **Upscale a picture…** returns a PNG of the size the rule gives for a PNG, JPEG or WebP file the person chose, named `upscale_<name>_<size>_<time>.png`; a phone photo comes out upright; transparency is kept; **nothing is stored**: no file, no history entry. (1.10)
91. That route refuses, with the reason: a picture needing more than 2×, one already 4K, a copy over 20 MP (`422` `not_4k_eligible`), a file that is not an image (`415`), a damaged file (`422`), an empty file (`422`) and one over the size or pixel limits (`413`); and it needs the `X-Studio-Client` header like every mutation. (1.10)
92. No more than **two** pictures are being made at once through it. (1.10)
93. The viewer's button for a source, and the card's and viewer's for a square, 4:3 or portrait result, say **what size they will make** in their tooltip. (1.10)

### 27.10 What was built in 1.10, and what was not checked

Built to §27.3, §27.5 and §27.9: the rule (`backend/studio/fourk.py`: cover the 4K frame for any shape, trimming only a 16:9 or 9:16 picture to exactly the frame, up to a 2× enlargement and 20 MP), the one-pass resize, the file beside the picture and its two routes (`POST` and `GET /api/images/{id}/4k`) for results **and an edit's source images**, `can_4k`, `four_k_size` and `four_k` on every such picture (the copy's size read from its file's PNG header), one shared build for overlapping requests, the `run.updated` event, `POST /api/upscale` for **a picture from the computer** (checked as an upload is, rotation applied, at most two at once, nothing stored), the page's *Make 4K* / *Making 4K…* / *Download 4K* with the focus handling and a tooltip that names the size, the *Upscale a picture…* button, the removal of the Upscale placeholder, and `scripts/upscale_probe.py`.

- **Tests:** backend 772 (pytest; 173 are new: the base commit has 599), and 39 more run only where `torch` is installed (17 of them new, for the probe's tiling); front end 276 (Vitest; 19 new) and 122 in a real browser (24 new, including a phone width, two open pages, keyboard focus, an edit's sources, the file picker and its refusals, and a download caught from the browser).
- **Mutation checks, 128 mutants of the new code.** *First version (68):* backend 42 of 44 killed (one survivor was dead code, removed with an exhaustive test over every size in the 2% band standing in for the argument; one is equivalent), page logic 10 of 10, browser 13 of 14 (the survivor, *do not store the run the server returned*, is redundant by design: the `run.updated` event delivers the same update, and the reducer's handling of it is unit-tested). *The any-picture extension (60):* backend **39 of 39**, page logic **9 of 9**, browser **12 of 12**. One backend survivor was a real defect: without the check that a picture belongs to a run, a staged upload was refused with a 404 only *after* a stray `…-4k.png` had been written into the staging folder, where nothing would ever clean it up; the test now checks that no file is left. Mutants that did not compile at first (an unused import after the change) were rewritten to ones that do. Listing the mutants before running them also showed behaviours the first tests did not pin (rounding against truncating the cover size, the PNG signature check, a cut-off header, one Lanczos pass, one event for overlapping requests, refusal before any file work, the encoder level); each got a test.
- **Not testable with my tools:** that choosing the *same* file twice in the picker works (the input's value is reset after each choice; Playwright fires its change event whether or not it was).
- **Checked on pictures made by the old code:** three pictures were made with the 1.9 code (2560×1440, 2048×2048 and 1536×864), and the same data directory was opened with 1.10: the first got Make 4K and made a 3840×2160 file, the others correctly had none *under the first version of the rule*; under the cover rule the square now gets it as well.
- **Mistakes of mine, found by the checking and fixed:** a test picture that was 2.2% off 16:9 and so passed for the wrong reason; wrong arithmetic in three tests' expectations (a ratio, a 3840×3840 picture that is already 4K, a slug that drops filler words); a module-level skip that would have skipped the tests that need no PyTorch (the file is now two); a test helper that converted an 8-megapixel picture once per column (30 s to 8 s); and the browser harness's rule that any console error fails a test, which a 507 provoked on purpose tripped (now handled in that one test). Looking at the screenshots found a real gap: when the button is replaced by the link, keyboard focus was lost. It now moves to the link, and three tests cover it.
- **A wrong statement of mine, corrected:** the code comment and §6 said the pipeline's `check_inputs` *requires* sizes in multiples of 32. At the pinned commit it only warns and the pipeline rounds the size down. The studio's rule stays (it refuses a size rather than make a different one), and both places now say what is true.
- **The probe, checked against the real model on a CPU** (§27.5), but not on the Spark.

**Not checked, because only the Spark can:** how long Make 4K takes there and how big the files are (a square's copy is about 15 MP); how a 4K copy looks next to the original (§27.4 is an expectation, not a measurement), and how a 1.9× enlargement (a square) looks against the 1.4× of a 16:9 picture; how long a 12 MP photo from your computer takes; whether the probe's upscaler runs on the GB10 with NVIDIA's PyTorch, how fast, and whether its output beats the resize (`SPARK_TEST.md` §21).
