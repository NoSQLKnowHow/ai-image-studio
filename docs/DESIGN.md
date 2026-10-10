# Qwen-Image Web Studio — Design Specification

Living document. **Last brought up to date 2026-10-10, for version 1.13** (the number the page shows in its title). §31 to §34 are proposals for 1.14 to 1.17 (Vector is 1.17); nothing in them is built.

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
| 1.11 | **Enlarge**: the same picture, bigger and sharper. A button beside Make 4K on every picture the studio holds (results and an edit's source images) enlarges it to the 4K frame with an upscaler model (Real-ESRGAN x2plus, one or two ×2 passes, then one resize to the exact size), up to a 4× enlargement; the copy replaces a Make 4K copy and the original stays. Needs the model file, which you download; says why when it is missing | §28 | (this PR) | §22 of `SPARK_TEST.md` not yet reported back; **the upscaler has never run on the Spark, so how it looks and how long it takes are unknown** |
| 1.12 | **The Kept view**: a filter bar above the history (All \| Kept, with the count of kept runs) that applies to both tabs and is remembered; a running job stays visible at the top of the Kept view; un-keeping there removes the card with a toast that says when it will be deleted, and Undo; the server filters the pages, and the filter shape is built to take more filters. **Also: Enlarge waits for a picture that is being made** instead of failing with an out-of-memory error | §29, §28.9 | (this PR) | §22(g) of `SPARK_TEST.md` is the check for the Enlarge fix; the Kept view needs no Spark |
| 1.13 | **The bin**: a **Deleted** option in the filter bar; Delete (and the daily expiry) moves a run to the bin for 30 days; **Restore**, **Delete forever** and **Empty bin** | §30 | (this PR) | The bin needs no Spark; `SPARK_TEST.md` §24 is the short check |
| 1.14 | **Project folders** (proposed, not built): a **Project** button on every card (a drop-down of your projects, or a new one made on the spot) files the run and keeps it; Keep is locked while a run is filed; a **Project** drop-down in the filter bar and a **Manage** dialog | §32 | — | Not built; nothing for the Spark to check yet |
| 1.15 | **Delete a picture from a run** (proposed, not built): a **Delete picture** button in the viewer sends one picture to the bin for 30 days (the last picture sends the whole run); the Deleted view shows the deleted pictures with **Restore** and **Delete forever** | §33 | — | Not built; nothing for the Spark to check yet |
| 1.16 | **16:9 is the default shape** of a picture (proposed, not built): Generate starts at 2752×1536 instead of 2048×2048 on the page, in the API and in `scripts/qwen_image.py`; a browser's saved 1:1 is moved to 16:9 once; Edit stays on Auto | §34 | — | Not built; one Spark run at the default size to compare time and memory (§34.6 criterion 184) |
| 1.17 | **Vector** (proposed, not built): an SVG from a prompt in N colours, one layer per colour, for a laser | §31 | — | Needs the test-sheet results and answers 5, 7 and 8 in §31.7 first; the Spark would run a laser test sheet and a model probe (§31.9) |

**Tests today (1.13):** backend 1009 (pytest; 65 more run only where `torch` is installed: 28 for the real music pipeline, which also needs `diffusers` 0.40.0, 17 for the upscaler probe's tiling, and 20 for Enlarge's real loading path, which also need `spandrel`), front end 369 (Vitest) and 171 (Playwright, in a real browser against the real server with the fake pipeline). Everything the studio does has been verified only against that fake pipeline, apart from what you ran yourself on the Spark; §15 says what the fake pipeline can and cannot show.

**What is left to build**, in the order proposed in §21.11:

| # | Milestone | What | Waiting on |
|---|---|---|---|
| 1 | **M5c** | The Spark test for edits (`SPARK_TEST.md` §18, on the page; §15 is the same through the API): whether "image 1" in a prompt works, alpha inputs, mask polarity and size, memory and time; sets the cap's default and the cost warning's threshold | You, on the Spark |
| 2 | **M5d** | Local edits: a mark-up editor and a mask editor (§21.5) | M5c (the mask convention) |
| 3 | **M5e** | "Improve prompt", Qwen's official rewriter (§21.12 item 3) | **Your decision on R3**; only if accepted |
| 4 | **An upscaler model** for Make 4K (route B, §27.5): **built as Enlarge in 1.11** (§28) | The same picture, bigger and sharper, up to 4×; replaces the Edit-mode Upscale of decision #37, which is dropped | Your Spark test of Enlarge (`SPARK_TEST.md` §22): how it looks on your pictures and how long it takes; the probe (§21 i) is still the way to compare models and `bf16` |
| 4b | **Qwen redrawing** of an enlarged picture (decision #47, §28.5) | Qwen redraws fine detail in the enlarged picture: possible in principle, unproven | Your wish to go on, then a Spark experiment (a script, like the probe) before anything is built |
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
- Masks / inpainting (**now planned for version 2, §21.5**), LoRAs. **Upscaling**: **Make 4K** (§27, 1.10) resizes a picture to cover 3840×2160, and **Enlarge** (§28, 1.11) does the same with an upscaler model, so the picture is sharper as well as bigger
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
| 46 | Enlarge: the same picture, bigger and sharper (1.11) | You asked why **Regenerate larger** gives another picture, said you want **the same picture, faithful, with extra sharpness** (Qwen redrawing fine detail would be nice as well), and asked for an **Enlarge** button that takes a picture to 4K. **Enlarge** uses an **upscaler model** (Real-ESRGAN x2plus, which keeps the composition because it is not generating anything new), once or twice, then one resize to exactly the 4K frame (the same frame and trim rule as Make 4K), up to a 4× enlargement. It sits **beside Make 4K** on every picture the studio holds; the copy **replaces** a Make 4K copy (one 4K copy per picture). It runs in a short-lived process on the GPU, not in the image worker; the model file is **yours to download** (the studio never fetches it). **Qwen redrawing** is **not built**: how to try it is in §28.5 (decision #47, proposed) (§28) | DECIDED (your request and your answer: faithful first, redrawing as a nice extra); details PROPOSED; built in 1.11; **amended in 1.12** after your Spark trial (it waits for a picture that is being made, §28.9) |
| 48 | A Kept view, and filters that more can join (1.12) | You asked for the history to be filtered to **only the runs you have kept**, and answered the questions: with Kept chosen, **Generate stays in Kept and the running job stays visible at the top** until it finishes (then it leaves unless you kept it); **un-keeping in the Kept view removes the card with a toast and Undo, and the toast says when it will be deleted**; the choice applies to **both tabs**; and the design must let **other filters join later**. A **filter bar** (All \| Kept, with the count of kept runs on the tab) above the history; the server filters (`GET /api/runs?kept=`) because the history comes in pages of twenty; one filter shape on the server, the page and the store so the next filter is a short change (§29). A recoverable delete (*Deleted* view) is a release of its own, **1.13, and needs your answers** (§29.9) | DECIDED (your four answers); details PROPOSED; built in 1.12 |
| 49 | The bin: a Deleted view that holds a deleted run for 30 days (1.13) | You asked that a deleted run be recoverable, and answered: **it stays in the Deleted view for 30 days before it is really deleted, and there should be an option to empty the garbage bin.** A **Deleted** option beside All and Kept in the filter bar; **Delete** moves a finished run to the bin (with a toast and Undo); the daily clean-up moves expired runs there too; after 30 days (`STUDIO_BIN_DAYS`) they go for good; **Restore** puts a run back as it was, with a fresh clock if it is un-kept; **Delete forever** and **Empty bin** ask first. The other questions of §29.9 are my defaults, listed in §30.6 for you to veto (§30) | DECIDED (your 30 days and your Empty bin); the rest PROPOSED; built in 1.13 |
| 50 | Vector: an SVG made from a prompt, in a number of colours, one layer per colour, for a laser (1.17, **proposed**) | You asked for an SVG instead of a PNG from a prompt, to say **how many colours** it has (*black and white is 2; black, white, red and green is 4*), to have **layers**, and for each layer to be **cut on your xTool P2 or engraved with your Gweike G3 Ultra**; and for **the best open-source model**. Proposed: a **Vector** tab; the artwork is made by a model and a **clean-up I write and test guarantees** at most N exact colours, closed shapes, one layer per colour, no doubled edges and a real size in millimetres. **Engine A, draw and trace** (Qwen-Image makes a flat picture, code reduces and traces it) first; **Engine B, a native SVG model** (OmniSVG 1.1 is the candidate) only if a probe on the Spark shows it is better. I cannot name "the best" model from here: §31.2 says what is verified and what is not, and §31.9 is the probe that decides. **You have answered eight of the eleven questions; sizes (5), other formats (7) and the engine (8) are open, with defaults, in §31.7, and I am waiting for what you see in XCS and LightBurn with the test sheet (§31.9)** (§31) | PROPOSED; nothing built |
| 51 | Project folders (1.14) | You asked for **project folders**: when you like a generation you add it to a drop-down of projects, or make a new one right then, and once it is in a project it is **also kept**, so that it is not deleted by accident. You answered: **the whole run** is what is filed; **one project at a time**; **Keep is locked while a run is in a project** (taking it out leaves it kept); and you browse with a **Project drop-down in the filter bar**. A project is a label in the database (no file moves); deleting a project never deletes a run; the other details are my defaults, listed in §32.6 for you to veto (§32) | DECIDED (your four answers); the rest PROPOSED; not built |
| 52 | Delete a picture from a run, into the bin (1.15) | You said you also need to **delete the pictures you do not like from a run**. You answered: on **any finished run**; a deleted picture goes **into the bin for 30 days**, like a run; deleting a run's **last** picture sends **the run** to the bin; a picture's **4K and Enlarge copies go with it**. The Deleted view shows one card per run with its deleted pictures, each with **Restore** and **Delete forever**; the number beside Deleted counts runs and pictures; the other details are my defaults, listed in §33.4 for you to veto (§33) | DECIDED (your four answers); the rest PROPOSED; not built |
| 53 | 16:9 is the default shape of a picture (1.16) | You asked that **16:9 be the default image aspect ratio for any generation**, replacing the 1:1, 2048×2048 of decision #20. You answered: a browser's **saved 1:1 is moved to 16:9, once**, with a note; **Edit stays on Auto** (decision #28); and the **API and the command-line script change too**, so one default holds everywhere. 16:9 is the model card's 2752×1536 (about 4.23 MP, the cost of the old square); the other details are my defaults, listed in §34.5 for you to veto (§34) | DECIDED (your three answers); the rest PROPOSED; not built |

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
| #46 | **Yes** (1.11): Enlarge with an upscaler model. Qwen redrawing (#47) is proposed, not built. |
| #48 | **Yes** (1.12): the Kept view and the filter framework. The *Deleted* view followed in 1.13 (#49). |
| #49 | **Yes** (1.13): the bin, with your 30 days and your Empty bin; the other defaults are in §30.6. |
| #51, #52 | **Not built.** Decided by your answers (project folders in 1.14, §32; deleting a picture in 1.15, §33); the other defaults are in §32.6 and §33.4. |
| #53 | **Not built.** Decided by your three answers (1.16, §34); the other defaults are in §34.5. It changes the default of decision #20 (size) and leaves #28 (Edit's Auto) as it is. |
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
| `GET /api/runs?limit=&before=&kept=&deleted=` | List runs, newest first | Cursor pagination; "load more" in the UI. **1.12:** `kept=true` (only kept runs) or `kept=false` (only runs that are not); absent = either; anything else is `422`. The cursor continues within the filter (§29.5, §29.6). **1.13:** `deleted=true` lists the bin; without it the list is the history and leaves the bin out (§30.5) |
| `GET /api/runs/counts` | **1.12:** how many runs each tab holds and how many are kept (**1.13:** and how many are in the bin): `{"image": {"all": n, "kept": n, "deleted": n}, "music": {...}}` | For the filter bar (§29.6, §30) |
| `POST /api/runs/{id}/bin`, `POST /api/runs/{id}/restore`, `DELETE /api/bin` | **1.13:** move a finished run to the bin; take a run out of it (a fresh clock if it is not kept); delete everything in the bin for good (`{"deleted": n}`) | `404`; `409` `run_not_finished`, `bin_off`, `not_in_bin` (§30.5). `DELETE /api/runs/{id}` is unchanged and means *for good* |
| `GET /api/runs/{id}` | One run with its images | |
| `POST /api/runs/{id}/cancel` | Cancel queued or running | `200` with the run for a queued run (canceled at once); `202` for a running one (it carries `canceling: true` and becomes `canceled` at the next step); `409` `run_finished` if it already finished; `404` |
| `PATCH /api/runs/{id}` | `{pinned: bool}` | The only mutable field; strict (a real boolean, nothing else, or `422`). Returns the run |
| `DELETE /api/runs/{id}` | Delete run and files | 409 if running (cancel first) |
| `GET /api/images/{id}` | Full PNG | `?download=1` sets a meaningful filename (below) |
| `GET /api/images/{id}/thumb` | WebP thumbnail | `?download=1` sends it as an attachment named like the image with `_thumb.webp`; outputs only (§22.3) |
| `POST /api/images/{id}/4k` | **1.10:** make the 4K copy of a result image: a 3840×2160 PNG beside the original (§27.3) | `201` with the updated run when it was made now, `200` when it was already there; `404` for an unknown image or one that is not a result; `422` `not_4k_eligible` (not 16:9, too small, already 4K: the reason is in `detail`) or `unreadable`; `507` `storage_full`, leaving no partial file |
| `GET /api/images/{id}/4k` | **1.10:** the 4K copy, once made (**1.11:** the enlarged one if there is one); `?download=1` sends it as an attachment named like the image with `3840x2160` in it | `404` if it has not been made |
| `POST /api/images/{id}/enlarge` | **1.11:** enlarge a picture to the 4K frame with the upscaler model (§28.3); the same kinds of picture as `/4k`, an edit's source included | `201` with the updated run when it was made now, `200` when an enlarged copy was there already; `404` for an unknown image or a staged upload; `422` `not_enlarge_eligible` (too small for 4×, already 4K, over 20 MP: the reason is in `detail`) or `unreadable`; `503` `upscaler_unavailable` (no model file, or `spandrel` missing: `detail` and `hint` say what to do); `503` `upscaler_busy` (**1.12:** out of memory: something else is holding the GPU; the studio's own pictures are waited for, not refused); `500` `upscale_failed` (the model could not be loaded or the run failed, with the reason); `504` `upscale_timeout`; `507` `storage_full`, leaving no partial file |
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

**Files** under the data volume: `images/<run>/<idx>.png`, `images/<run>/<idx>-4k.png` (a 4K copy, made on request, §27: no database row, it goes with the run) or `images/<run>/<idx>-4k-enlarged.png` (the Enlarge copy, §28, which replaces it), `thumbs/<run>/<idx>.webp`, `inputs/staged/<id>.png` (uploads waiting; re-encoded to PNG) and `inputs/<run>/<position>.png` (what a run owns), their thumbnails under `thumbs/staged/` and `thumbs/<run>/in-<position>.webp`, `studio.sqlite`, and once after the upgrade to schema 2 `studio.sqlite.before-schema-2` (§21.7). All file access is by database id; client-supplied filenames are never used in paths.

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
- **Backups:** `scripts/backup.sh` and `scripts/restore.sh` (README "Backing up and restoring") save and restore the four things that live outside the container: `./data`, `.env`, the built image and **the studio's models in the Hugging Face cache, which are included by default** (`--no-model` leaves them out; `--model`, the old way of asking for the image model alone, is still accepted and changes nothing, so an old cron line keeps working). **"The studio's models" are exactly three, found from the settings the studio itself reads:** the image model (`STUDIO_MODEL`, a `hub/models--…` folder), the music model (`STUDIO_MUSIC_MODEL`, likewise) and the Enlarge upscaler file (`STUDIO_UPSCALER_MODEL`, by default `upscalers/RealESRGAN_x2plus.pth`); **nothing else in the cache is touched**, because Hermes' vLLM and other tools share that folder. **A model that is not there is skipped with a warning that names it, never an error** (it was never downloaded; the setting is a folder path rather than a Hugging Face name; the upscaler is outside `/models`; a name the restore would refuse), so a fresh install or a scheduled run is still backed up; `MANIFEST.txt` says exactly which are inside (`model_paths`, and `format=2` when there are any; a backup without models is still `format=1`, as before). A restore puts back each model that is not already in the cache and leaves the others alone (with `--force` the cached copy is moved aside, never deleted), and still reads the older backups that hold one model (`format=1`, `model_dir`). A backup is **one `.tar` file** (an ordinary tar of already-compressed pieces plus a manifest and checksums) so it can be copied to a NAS; it is re-read and its checksums checked before it gets its final name, and `restore.sh --verify` checks a copy without Docker. The studio is stopped only while `./data` is packed (SQLite in WAL mode must be at rest), and a restore reads the `.tar` in place and never deletes anything: what is in the way is moved aside. Their tests, `scripts/tests/backup_restore_test.sh`, run without Docker.
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
| `STUDIO_UPSCALER_MODEL` | *(the `upscalers` folder of the Hugging Face cache)* | **1.11:** Enlarge's model file (§28.3), as the container sees it; empty = `$HF_HOME/upscalers/RealESRGAN_x2plus.pth`, which is `~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth` on the host. **You download it** (67 MB; the studio never fetches it) |
| `STUDIO_UPSCALER_DEVICE` | `auto` | **1.11:** where Enlarge runs: `auto` (the GPU if there is one), `cuda`, or `cpu` (minutes per picture) |
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
                     worker.py + worker_client.py (the GPU process and its stdio protocol), pipelines/ (real, fake),
                     fourk.py (Make 4K and Enlarge: the rules, §27, §28), upscaler.py + upscale_job.py (Enlarge: the
                     server side and the process that runs the model), tiling.py (shared with scripts/upscale_probe.py)
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

**Version 2 adds criteria 19–32** (§21.10), and the later releases 33–47 (§22.4, §23.3, §24.3), 48–50 (§21.13) 51–60 (§25.5) 61–75 (§26.10, music) 76–77 (§26.11, cache), 78–93 (§27.8, §27.9, Make 4K) and 94–105 (§28.7, Enlarge). Criterion 7 ("Edit works via file picker, drag-and-drop, paste and 'Edit this'") is replaced by 19–27, which cover several images.

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

**The probe, `scripts/upscale_probe.py` (built in 1.10).** It loads one model with `spandrel`, upscales a picture in tiles with a little context around each, and reports the device, the time and the memory. For a 16:9 picture it also writes the 4K file Make 4K makes today and a 4K file made from the model's output (trimmed by the same box, scaled), to compare. The ×2 model needs sizes in multiples of 4, so each tile is padded for the model and cut back; the cores of the tiles are all that is kept, so they join without seams. It needs `spandrel`, which the image contains from 1.11 (Enlarge, §28; in 1.10 it had to be installed in the container with `pip install --user spandrel`), and the studio's own tiling code, `backend/studio/tiling.py`, which Enlarge uses too (so a 1.11 image or a checkout is needed). The script itself is copied into the running container (`docker compose cp`), so it is not part of the image. (The image's `.dockerignore` lets through only `scripts/minimax_music.py`, the one script the Dockerfile copies, so the probe is not in the image however you build it.)

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

## 28. Version 1.11: Enlarge, the same picture bigger and sharper (decision #46 DECIDED; details PROPOSED; built in 1.11; Qwen redrawing, decision #47, not built)

You asked why **Regenerate larger** makes another picture, and said what you want instead: **the same picture, faithful, with extra sharpness**. Having Qwen redraw fine detail "would be nice as well". And an **Enlarge** button that takes a picture to 4K.

### 28.1 Why Regenerate larger gives another picture (and what it is for)

A diffusion model starts from a grid of random noise and shapes it into the picture; this model's grid is the picture's size ÷ 16 (1376×768 starts from 86×48 cells, 2752×1536 from 172×96). The seed is the same, but it fills a differently shaped grid, so the noise is different, and the composition is mostly decided in the first steps. So **the same prompt and seed at another size is another picture**. §22.1 said so ("to confirm on the Spark"); your 50% run confirmed it. Regenerate larger is for *finding* a prompt you like small and then making a new picture at full size. It was never an enlarger, and its tooltip says so. Enlarge is the enlarger.

### 28.2 What Enlarge does

- **Where.** A button **Enlarge** next to **Make 4K** on every picture the studio holds: on a card, in the viewer, for a result and for an edit's source image. It is offered when covering the 4K frame (the same frame and the same trim rule as Make 4K, §27.3, §27.9) is an enlargement of **more than 1× and at most 4×**, and the copy is at most 20 MP. A 50% picture qualifies (1376×768 needs 2.8×); a 25% one (688×384 needs 5.7×) does not, and the refusal says so.
- **How.** An **upscaler model** enlarges the picture ×2 and **once** is enough when the picture needs at most **3×** (what is left is a gentle Lanczos stretch of at most 1.5×); above 3× it is run **twice** (×4). Then **one Lanczos pass** cuts the box (a 16:9 or 9:16 picture is trimmed to exactly the frame, as Make 4K does) and sizes the result to exactly the frame: usually a *reduction* of the model's output, which keeps it sharp. 1376×768 becomes 2752×1536, then 3840×2160. A 2048×2048 square becomes 4096×4096, then 3840×3840.
- **Faithful.** The model does not generate, it enlarges: composition, colours and subjects stay. What it adds is **fine detail it infers** (crisper edges, plausible texture). It cannot recover real detail that is not in the picture, and it is not Qwen: see §28.4.
- **Transparency.** The colour goes through the model; the alpha channel is resized with Lanczos and put back, so a transparent picture stays transparent.
- **One 4K copy per picture.** The enlarged copy is `<idx>-4k-enlarged.png` beside the original (`inputs/<run>/<position>-4k-enlarged.png` for a source). When it is made, a Make 4K copy of that picture (`<idx>-4k.png`) is removed: the better one replaces it. No database change; existence is the state, and it goes with the run. **Download 4K** is whichever copy there is, and says which in its tooltip. While only a Make 4K copy exists, both **Download 4K** and **Enlarge** are shown; once an enlarged copy exists, Make 4K is not offered again.
- **Make 4K stays** as the quick way: a resize, a few seconds, no model, nothing to install.
- **The words.** Tooltip: *"Enlarge to 3840×2160 with an upscaler model: the same picture, sharper than Make 4K, but it takes longer. The extra detail is the model's guess."* While it runs the button says **Enlarging…** and is disabled; when it is done a note names the size, and focus goes to **Download 4K** if it had been lost.

### 28.3 How it runs

- **In a short-lived process, not in the image worker.** The API starts `python -m studio.upscale_job` for each enlargement: it loads the model, enlarges the picture, writes the file and exits. Reasons: the image worker holds the big model and its protocol is built around one model's jobs; a 67 MB upscaler loads in about a second; a crash or an out-of-memory in the upscaler cannot take down a loaded Qwen; the API process never imports PyTorch (it stays as light as it is). It uses the GPU when there is one (`STUDIO_UPSCALER_DEVICE=auto`), else the CPU, which is slow (minutes). **It does not run at the same time as a picture is being made: it waits for it** (amended in 1.12, below; 1.11 said it worked alongside a run, and on the Spark that ended in a CUDA out-of-memory).
- **One at a time.** The GPU gate (next) lets one enlargement run at a time, and requests for the same picture share one. Other open pages learn of the copy by `run.updated`, as for Make 4K. A run takes the time it takes; there is no progress bar (the request stays open and the button says *Enlarging…*); a limit of 30 minutes stops a stuck one.
- **It waits its turn (amended in 1.12).** The first Spark trial pressed Enlarge while a picture was being made and got *The upscaler model could not be moved to cuda: AcceleratorError: CUDA error: out of memory. Check the model file…*: the image model holds most of the GB10's memory, the upscaler's process could not get its share, and the message blamed a model file that was fine. So the GPU has **one gate**. A run holds it for as long as it runs (images and music alike); an enlargement holds it while its process runs. An enlargement asked for while a run is going **waits** and starts when that run is done, **before the runs still queued behind it** (a run is minutes of work, an enlargement seconds on the GPU, and the queue goes on straight after), and in the order asked among enlargements. It also waits while the **Load model** button is loading a model. While it waits, `GET /api/status` and the `queue.updated` event carry `queue.enlarge_waiting` (the ids of the pictures waiting) and **every open page** shows that picture's button as **Waiting…**; the request stays open as before and the note at the end is the same. Asking again for a picture that is waiting joins the same enlargement. Closing the page does not cancel it (the copy is made and the next page shows it); stopping the server does cancel the ones still waiting. Nothing limits the wait, and the 30-minute limit starts when the process does.
- **When the GPU is full for another reason.** Something outside the studio (another container, the LLM server) can hold the memory too. The job process now recognises an out-of-memory wherever it happens (reading the file, moving the model to the device, enlarging) and exits with its own code (9); the API answers `503` `upscaler_busy`, *Not enough memory for the upscaler right now: something else is using the GPU…*, with a hint (try again in a moment; `STUDIO_UPSCALER_DEVICE=cpu` works without the GPU). A model file that really is damaged still says so.
- **What it needs.** (1) `spandrel` (pure Python, in the image: `requirements-container.txt`; NVIDIA's torch is pinned by the build, so it cannot be replaced). (2) **The model file, which you download** (the studio never fetches it: each model has its own licence, and decision #43 keeps downloads in your hands): `STUDIO_UPSCALER_MODEL`, by default `$HF_HOME/upscalers/RealESRGAN_x2plus.pth`, which is `~/.cache/huggingface/upscalers/RealESRGAN_x2plus.pth` on the host (the same place the probe uses, §21 i of `SPARK_TEST.md`). 67,061,725 bytes; the code is BSD-3-Clause; read the model's terms yourself.
- **When it is missing.** `GET /api/capabilities` has `upscaler {available, model, reason, hint}`. The page shows **Enlarge disabled, with the reason as its tooltip** (and the same words if it is pressed). With the fake pipeline a stand-in (a plain resize that leaves a note in the file) is always available, so the page and the tests run anywhere; the real one is checked without importing anything (`spandrel` and `torch` found, the file there).
- **Settings.** `STUDIO_UPSCALER_MODEL` (a file) and `STUDIO_UPSCALER_DEVICE` (`auto`, `cuda` or `cpu`). Fixed for now: tiles of 512 pixels with **64** pixels of overlap (the probe's, with the same code: the tiling lives in `backend/studio/tiling.py` and the probe uses it) and full-precision numbers (the probe measures bf16, which may become a setting). The overlap was measured, not guessed: with the real Real-ESRGAN x2plus on a CPU, a 768×512 crop tiled in 256-pixel tiles differs from one whole-picture pass by 1.3/255 on average at a tile edge, and by up to 24, with 32 pixels of overlap; with 64 it is 0.4 and up to 11, for about 23% more work per tile. A 128 overlap would be better still and costs 2.25× as much; 64 is the compromise.

### 28.4 What Enlarge does not do

- **It is not Qwen.** It is a small network trained to undo blur and noise in ordinary pictures. It makes edges crisper and textures plausible; faces can come out smooth and a little plastic, fine text may not sharpen, and an illustration's flat colour can pick up a texture it did not have. **How it looks on your pictures is not known until you try it** (§22 of `SPARK_TEST.md`). The original is never touched, and Make 4K's copy is a click away to compare, if you made one first.
- **Not above 4×.** A picture that needs more is refused with the reason: a bigger enlargement invents more than it enlarges.
- **No progress bar, no cancel.** A request that is under way finishes (or fails); closing the page does not stop it, and the copy is there when you come back.

### 28.5 Qwen redrawing (decision #47, proposed, not built)

You would like Qwen itself to redraw fine detail in the enlarged picture. It is possible in principle and **unproven**, so it is an experiment before it is a feature. The pinned pipeline has no `strength` setting (no ready-made image-to-image), but it takes `latents` and `sigmas`, so the first thing to try on the Spark is: enlarge the picture (Enlarge, or a plain resize), encode it with the pipeline's VAE, add noise up to a chosen level, and run only the remaining steps with the original prompt. A little noise should keep the composition and let the model redraw texture; more noise redraws more. The other candidate is Edit mode with the enlarged picture as its input and a prompt that asks to keep everything and add detail (its ceiling is 2K). What to find out: whether the composition survives, how much the fine detail drifts, how long it takes, and what the best noise level is. It is a queued job on the image worker, so the image model has to be loaded. I will write the experiment as a script for you to run (like the probe) if you want to go on.

### 28.6 The small choices I made (tell me if you want any changed)

1. **One 4K copy per picture, and Enlarge replaces a Make 4K copy**, rather than two files to choose between: fewer buttons, and the better copy is the one you keep. The cost: an enlarged copy cannot be turned back into a plain resize without deleting the run's folder.
2. **Make 4K stays beside Enlarge** instead of becoming Enlarge: it is quick, needs no model, and it is what you have been using.
3. **Up to 4×, one pass up to 3×, two above.** After one ×2 pass the rest is at most a 1.5× Lanczos stretch; above 3× that stretch would be big enough to blur what the model made. These numbers are a judgement, not a measurement.
4. **A subprocess per enlargement**, not a resident model: a second or so of loading each time, in return for no memory held between uses.
5. **Full precision** for now: it is the safe choice and the model is small.
6. **No automatic download of the model.**
7. **The probe and the studio share one tiling implementation** (`studio/tiling.py`), so the tested code is the code that runs.
8. **(1.12) Enlarge goes right after the run that is going, before the runs still queued.** **Your answer:** *"it should go after the current run, if there is one running currently"* (I had offered the other order, after the queue, as a one-line change). With no run going it starts at once. Enlarge is quick and you are looking at the picture, so the queue behind the running run is not made to wait more than a few seconds for it.
9. **(1.12) The wait is the open request**, as the work itself was: nothing new to cancel, and a closed page loses nothing. The cost is that the request is open for as long as the wait, which is as long as the running picture (and, if it is a long run, many minutes).

### 28.7 Acceptance criteria (continue §27.9)

94. **Enlarge** is offered on any picture the studio holds (a result or an edit's source) whose enlargement to cover the 4K frame is more than 1× and at most 4×, and whose copy is at most 20 MP; it is **enabled** when the upscaler is available, and otherwise shown disabled with the reason. (1.11)
95. The model is run **once** (×2) for an enlargement of at most 3× and **twice** (×4) above that; then **one** Lanczos pass cuts the box (a picture within 2% of 16:9 or 9:16 is trimmed to exactly the frame) and sizes the copy to **exactly** the frame's size, or, for any other shape, to cover it. (1.11)
96. Transparency is kept: the colour is the model's, the alpha is resized and put back. (1.11)
97. The copy sits beside the original as `<idx>-4k-enlarged.png` (`<position>-4k-enlarged.png` for a source), is made once, survives a reload and a restart, goes with the run, and **replaces** a Make 4K copy of that picture. The payload says which it is (`four_k.method` is `model` or `resize`). (1.11)
98. While only a Make 4K copy exists, **Download 4K** and **Enlarge** are both offered; once an enlarged copy exists, Make 4K is not. (1.11)
99. `POST /api/images/{id}/enlarge` answers `201` / `200`, `404`, `422` `not_enlarge_eligible` with the reason, `503` `upscaler_unavailable` with a hint, `500` `upscale_failed` with the reason, `504` `upscale_timeout`, and `507` `storage_full` leaving no partial file. (1.11)
100. Requests for one picture share **one** enlargement; **one** enlargement runs at a time; other open pages learn of the copy by `run.updated`. (1.11)
101. It does not use the image worker, and the API process does not import PyTorch. (1.11) *(It also said that Enlarge works while a run is generating. Amended in 1.12: it waits, see 106.)*
102. `GET /api/capabilities` says whether the upscaler is available and, if not, why and what to do. (1.11)
103. On the page the button says **Enlarging…** and is disabled while it works; a note then names the size, and keyboard focus is not lost when the button is replaced; a failure shows the server's reason. (1.11)
104. On a **phone** the card's and the viewer's buttons, with the new one, fit with no sideways scroll. (1.11)
105. The probe and the studio tile with the **same code**; a tiled result equals the whole-picture result when the overlap covers the model's reach. (1.11)
106. An Enlarge asked for while a run is generating or a model is loading **waits** and starts when the run is done, **before the runs still queued** and in the order asked among enlargements. It is not an error, it does not stop or slow the run, and a failed enlargement gives the turn back. (1.12)
107. While it waits the server says so (`queue.enlarge_waiting` in `GET /api/status` and the `queue.updated` event) and **every open page** shows that picture's button as **Waiting…**, disabled; when its turn comes it reads **Enlarging…**, and the note at the end is the same as without a wait. An enlargement that did not have to wait is never listed. (1.12)
108. An out-of-memory in the upscaler process, whether reading the model, moving it to the device or enlarging, is answered `503` `upscaler_busy` in words that say the GPU is full, not as a damaged model file; any other failure is reported as itself. (1.12)
109. Stopping the server cancels the enlargements that are still waiting. (1.12)

### 28.8 What was built in 1.11, and what was not checked

Built to §28.2–§28.3: the rule (`backend/studio/fourk.py`: `plan_enlarge`, `render_enlarged`, `make_enlarged`, the same frame and trim rule as Make 4K with a 4× limit and one pass up to 3×), the job process (`backend/studio/upscale_job.py`, with an exit code for each way of failing), the server side (`backend/studio/upscaler.py`: availability, the fake stand-in, the process handling with a lock, a 30-minute limit and clean-up of a killed run), the tiling moved into `backend/studio/tiling.py` and used by the probe, the route `POST /api/images/{id}/enlarge`, `can_enlarge`, `enlarge_size` and `four_k.method` in the payload, `capabilities.upscaler`, the two settings, `spandrel` and `einops` pinned in the image requirements (pure-Python wheels; a copy that cannot be imported is a note at build time, not a failed build), and the page: **Enlarge** beside **Make 4K** on cards and in the viewer, its busy and dimmed states, the focus handling, and the error type that keeps the server's hint.

- **Tests:** backend 925 (pytest; 134 are new: the base, 1.10 with the Docker and loader fixes, has 791), 62 more that need `torch` (17 of them new: they use `spandrel`'s own loader, a real process and the whole API with a tiny random-weight ESRGAN), front end 290 (Vitest; 14 new) and 137 in a real browser (15 new: card, viewer, an edit's source, keyboard focus, two open pages, a phone width, the missing model, failures, a reload).
- **Checked with the real model, on a CPU** (Real-ESRGAN x2plus, 67 MB): two synthetic pictures through the real job process, 1280×720 (3.0×, one pass, 191 s) and 960×540 (4.0×, two passes, 255 s; both at once on 4 cores), each exactly 3840×2160. Side by side with a plain resize the model's lines are crisper, and on a smooth gradient it **adds visible grain**, which is the trade this kind of model makes. That is a synthetic picture; real pictures are the Spark test.
- **The tile overlap was measured** (§28.3): 32 pixels left a mean error of 1.3/255 at a tile edge against a whole-picture pass, 64 leaves 0.4. That was a guess of mine in the probe, changed on evidence.
- **Mutation checks, 100 mutants of the new code.** Backend **67 of 68** killed (the survivor is equivalent: a shared future for overlapping requests; the lock and the file check inside it already give the same answers), the real-loader group **7 of 7**, page logic **13 of 13**, browser **10 of 12**. The first runs left three real gaps, now closed: nothing asked again after a *failed* enlargement (a failure that was never forgotten would have been re-raised for every later request), nothing pinned the defaults of the two new settings, and nothing checked that the *viewer's* button says *Enlarging…* while it works. The two browser survivors are redundant by design, as in 1.10: the button's own refusal of a second press (the page's one-request-at-a-time guard does it, and the test counts one request) and storing the run from the response (the `run.updated` event delivers the same update).
- **Mistakes of mine, found by the checking and fixed:** the 32-pixel overlap above; a spec figure (5.6× for a 25% picture; it is 5.7×); several hurried test assertions that could not fail (an `or` fallback, a tautology, a placeholder test with no body), caught on re-reading before the first run of the mutation checks; an icon name that already existed; a guess about a network option (`shuffle_factor`) that did not do what I thought, dropped rather than relied on.
- **Not checked, because only the Spark can:** how long Enlarge takes there (the CPU figures above are an upper bound on the wait, not a measurement of the GPU); that the model runs on the GB10 with NVIDIA's PyTorch and how much memory it holds; how it looks on your pictures, which is the main question (§28.4); whether a running generation slows down while it works.
- **Not checked, because I cannot build the image here:** that `pip install` of the two pinned packages succeeds inside NVIDIA's container. Their wheels exist for linux/aarch64 and Python 3.12 (checked), they are pure Python, and the build keeps NVIDIA's torch pinned, so it should; the build prints `check_image: Enlarge: spandrel …` or says why not.

### 28.9 Amended in 1.12: Enlarge waits for the picture that is being made

**What happened.** On the Spark, Enlarge worked (3840×2160 in 12.4 s on `cuda`), but pressing it **while a picture was being generated** ended in *The upscaler model could not be moved to cuda: AcceleratorError: CUDA error: out of memory. Check the model file…*. Two mistakes of mine: §28.3 said Enlarge works alongside a run ("the upscaler is small, but its real peak is not measured"): I had noted the doubt and still shipped the claim as the behaviour, when the honest design was to wait; and an out-of-memory was recognised only while enlarging, so the same error while loading the model was reported as a damaged model file.

**What changed** (§28.3, criteria 106–109, 101 amended): one **GPU gate** shared by the job loop and Enlarge, so an enlargement waits for the run that is going and goes before the runs still queued; it also waits while a model is being loaded; the page says **Waiting…** on every open page (`queue.enlarge_waiting`); and an out-of-memory anywhere in the upscaler process is `503` `upscaler_busy` with words that say the GPU is full.

**Built:** `JobManager._gpu_gate` and `_take_gpu_turn` (`backend/studio/jobs.py`); exit code 9 and `problem_for` in `upscale_job.py`; `UpscalerBusy` in `upscaler.py`; the *Waiting…* control in `fourk.ts`, `FourKButton`, `RunCard`, `Lightbox`, the store and the event handler.

- **Tests:** backend 948 (pytest; 23 more than 1.11's 925: 13 for the waiting rules in `test_enlarge_wait.py`, the rest in the existing Enlarge files), 3 more that need `torch` (an out-of-memory while moving or loading the model, and a different failure still reported as itself: 20 for Enlarge's real loading path now), front end 295 (Vitest; 5 new) and 142 in a real browser (5 new: a card, the viewer, two open pages, the queue order, a keyboard user who had to wait).
- **Mutation checks, 37 mutants of the new code, 36 killed.** The gate (15), the out-of-memory handling (13 across the backend and real-loader groups), the page logic (5) and the browser (6). The survivor is redundant by design, as in 1.10 and 1.11: the button's own refusal of a second press while it waits (the page's one-request-at-a-time guard does it, and the test counts one request). The first run found **five gaps in my tests**, now closed: nothing required the *now waiting* event to be sent when the wait begins (a later event hid it), nothing counted how often a loading model is looked at (a tight loop would have passed), nothing checked that a copy made during the wait is not made again, nothing pinned the *words* of the out-of-memory message (the test compared the message with itself), and nothing checked that a keyboard user keeps their place through a wait. It also found **one real flaw**: stopping the server cancelled the job loop first, which handed the GPU to a waiting enlargement that then started its process in the middle of the shutdown; waiting enlargements are now cancelled first. And **one test could hang the whole suite when it failed** (the Load-model test left a request waiting for a state that never came); it now restores the state.
- **Not checked, because only the Spark can:** that it all behaves on the real GB10 while the real Qwen is generating (§22(g) of `SPARK_TEST.md` is that check); whether a second kind of GPU user outside the studio (the LLM server) produces `upscaler_busy` as I expect.
- **A limit worth knowing:** the wait is the open request, so a browser or a proxy that drops a request that has been open for many minutes (a long run, then more runs ahead) would show *Couldn't enlarge* on the page while the server still makes the copy and every page then shows Download 4K. If you see that, the cure is a `202` and a page that follows the events; I did not build it because nothing here suggests it happens on a LAN.

---

## 29. Version 1.12: the Kept view, and filters that more can join (decision #48 DECIDED; details PROPOSED)

You asked for the history to be filtered so that it shows **only the runs you have kept**, and for that choice to be selectable. You answered my questions: the running job stays visible in the Kept view until it finishes (§29.3); un-keeping there removes the card with a toast and **Undo**, the toast says when it will be deleted, and a run that was deleted should be recoverable, perhaps through a *Deleted* view (§29.4, §29.9); the choice applies to **both tabs**; and the design must let **other filters join later**, because you will be adding more (§29.5). 1.12 also carries the Enlarge fix of §28.9.

### 29.1 What it looks like

- **A filter bar** above the history, under the Images | Music tabs: *Show* and a two-way switch, **All** and **Kept 12**. The number is the count of kept runs on the tab you are looking at (the server counts them; §29.6). The switch is the same kind of control as the other two-way switches on the page (a radio group; arrow keys move between the options).
- **One choice for both tabs**, remembered by this browser (the rest of the page remembers its choices the same way). The bar is a row that can hold more controls later; on a phone it wraps.
- **Kept chosen:** the list shows only kept runs, newest first, with the same *Show older* button as All. A run that is queued or running is also shown, at the top (§29.3).
- **Nothing kept on this tab:** *No kept images yet* (or *No kept music yet*): *Press Keep on a run to keep it here. Kept runs are never deleted automatically.* and a **Show all** button. The page never says this while older kept runs are still waiting to be loaded; it loads them first.
- The cards are the same as ever: a kept run shows its **Kept** badge and its pressed **Keep** button.

### 29.2 Which runs the Kept view shows, in short

A run that is **kept** (`pinned`), and any run that is **queued or running** whether or not it is kept. Nothing else: a finished run that is not kept is not in the Kept view, however new.

### 29.3 Generating while Kept is chosen (your answer 1)

The view **stays on Kept**. The job you started appears **at the top** with a short note, *Shown while it works. It stays in this view only if you Keep it.* (the Keep button is on the card). When it finishes:
- **kept** (you pressed Keep): it stays, in its place;
- **not kept:** it leaves the view, and a toast says so rather than letting a card vanish: *"<the prompt, shortened>" is done. It is not kept, so it is not in this view.* with **Show all**.

The same goes for a run started by Retry, Regenerate larger, Edit this or from another open page: any run that is working is shown at the top, and the toast is the same. (A run that you kept while it worked is kept when it finishes; Keep and Stop keeping work on a working run.)

### 29.4 Un-keeping in the Kept view (your answer 2)

Pressing **Keep** on a kept card (to stop keeping it) in the Kept view **removes the card** and shows a toast: *No longer kept: "<prompt>". It will be deleted around 29 Oct, in 19 days, unless you Keep it again.* with **Undo**. **Undo** keeps it again and the card comes back in its place. The date is the one the card already works from (the run's age plus the retention, 30 days unless you changed it; the janitor deletes once a day, hence "around"). For a run older than that the toast says *It is past its 30 days, so it will be deleted at the next daily clean-up.* The toast stays 12 seconds (it has a button on it), can be dismissed, and is announced to screen readers.

Stopping to keep a run that another open page changed removes it from this page's Kept view too, **without** a toast (the person who did it was told).

**Recoverable:** until the clean-up the run still exists: **Undo** in the toast, or **All** and **Keep**. After the clean-up deletes it (an un-kept run past its time, or one you delete yourself) nothing brings it back; **§29.9 is the design for that** and needs your answers.

**Keyboard focus** was on the card's Keep button, which has just gone. It goes to the filter switch (the *Kept* option), which stays where it is.

### 29.5 Filters that more can join (your answer 4)

One shape, used in three places, so that the next filter is a short, mechanical change and cannot get out of step between server and page:

- **Server.** `RunFilter` (a frozen dataclass; `backend/studio/runfilter.py`) has one field per filter (`kept: Optional[bool]` now: `True` only kept, `False` only not kept, `None` either). Each field becomes **one fixed SQL condition** with a bound value; no filter is ever built from text sent by the page. `list_runs(limit, before_id, filter)` adds the conditions to the cursor query; `counts()` uses the same conditions. The API turns query parameters into a `RunFilter` in one function, and an unknown value is `422`.
- **Page.** `HistoryFilter` (`frontend/src/history.ts`) is the same record (`{ kept: boolean }` now), with `filterKey(filter)` (a stable string), `matches(run, filter)` (the same rule as the server's, used to decide what a live event shows) and `isDefault(filter)`. What the browser remembers is read defensively: anything it does not recognise becomes the default.
- **Store.** The store keeps **one cache of runs** and, for each filter, a **view**: has its first page arrived, where its next page starts, and how far down it has loaded. A filter shows the runs of the cache that match it **and lie within what that filter has loaded**. So switching between All and Kept is instant once each has loaded, a run is never shown out of sequence (a kept run from three months ago that a Kept page loaded does not appear in All until All has paged down that far), and a run that changes (kept, un-kept, finished, deleted) moves between views by itself.

**Adding the next filter** (a text search, a date range, *Deleted*) touches: a field and its condition in `RunFilter`; a query parameter; a field in `HistoryFilter`, `filterKey` and `matches`; a control in the filter bar; a count; and one row in the table of cases that **both** sides are tested against (§29.8, criterion 120). Nothing else.

### 29.6 The server

- `GET /api/runs?kept=true|false` (absent = either), with the same `limit`, `before` and `next_before` as before; the cursor continues **within the filter**. An unknown `kept` is `422`, an unknown cursor `400`. The event stream is unchanged: the first page it carries is the unfiltered one, the page asks for the filtered one itself.
- `GET /api/runs/counts` is `{"image": {"all": 31, "kept": 4}, "music": {"all": 3, "kept": 1}}` (*image* is Generate and Edit runs). The page asks again after a run is made, deleted, kept or un-kept (a quiet pause first, so a burst is one request).

### 29.7 The small choices I made (tell me if you want any changed)

1. **Working runs are shown in the Kept view, queued ones too**, so you can see what you asked for (your answer 1 said "the running job"; a job waiting its turn is the same thing from your side).
2. **The toast on finishing is mine**, to soften the card vanishing (you called that jarring). Without it the card would simply go.
3. **Un-keeping does not warn in the All view**: the card stays, so there is nothing to explain.
4. **A change made on another page removes the card silently** (no toast): you were not the one who did it.
5. **The count beside Kept is per tab**, not for both: the list you are looking at is per tab.
6. **12 seconds for the toast with Undo**, as for an error (6 seconds is for plain notes).
7. **Focus goes to the switch**, not to the next card, because the next card may not exist and the switch always does.
8. **Not in the address bar.** The choice is remembered by the browser; a link that opens the Kept view is easy to add later through `filterKey`.
9. **Keep is offered on a card that is still working.** It was hidden until the run finished, because nothing needs saving from expiry while a run works; but the Kept view's note says *"it stays in this view only if you Keep it"*, so the button has to be there to press. A run kept while it works is kept when it finishes.
10. **The empty Kept view names the tab:** *No kept images yet* or *No kept music yet*, not *Nothing is kept yet*, because the other tab may have kept runs.

### 29.8 Acceptance criteria (continue §28.7)

110. A **filter bar** with **All** and **Kept** is above the history on both tabs; the choice applies to both, is remembered by the browser, and the number beside Kept is the count of kept runs on the tab shown. (1.12)
111. With Kept chosen the history shows only kept runs, newest first, from the server's filtered pages: a kept run older than the newest twenty is reached with *Show older*, and nothing is shown out of sequence. (1.12)
112. A queued or running run is shown at the top of the Kept view with a note, whether or not it is kept; kept, it stays when it finishes; not kept, it leaves and a toast says so, with **Show all**. (1.12)
113. Starting a run (Generate, Retry, Regenerate larger, Edit this) with Kept chosen does not change the choice. (1.12)
114. Stopping to keep a run in the Kept view removes its card and shows a toast with the date it will be deleted (or *at the next daily clean-up* if it is past its time) and **Undo**; **Undo** keeps it again and the card returns in its place. (1.12)
115. Keyboard focus is not lost when the card goes. (1.12)
116. With nothing kept the Kept view says so and how to keep a run, with **Show all**, and it never says so while older kept runs are not yet loaded. (1.12)
117. `GET /api/runs?kept=true|false`: filtered pages with a cursor that continues within the filter; `422` for an unknown value; `400` for an unknown cursor; `GET /api/runs/counts` as in §29.6; the stream and the unfiltered list unchanged. (1.12)
118. A change made on another open page is shown live in this page's Kept view: a newly kept run appears in its place, an un-kept one goes (without a toast). (1.12)
119. On a **phone** the filter bar and the toast with **Undo** fit with no sideways scroll, and Undo can be pressed. (1.12)
120. The server's rule and the page's `matches` give the same answer on one shared table of cases, and the filter bar, query, counts and views are generic over the filter's fields (§29.5). (1.12)

### 29.9 The Deleted view (1.13, not built): what I need to know

You asked that a deleted run be **recoverable**, perhaps through a *Deleted* view that the same filter bar can hold. That is a release of its own, because it changes what *delete* means: today a delete removes the run's files at once, and nothing can bring them back. A recoverable delete keeps the files for a while. I have not decided any of this for you:

1. **What moves to Deleted?** (a) only runs you delete by hand; (b) also runs the daily clean-up removes (the un-kept ones past their 30 days); (c) both.
2. **How long does it stay recoverable?** A number of days (7?), as a setting like the retention; after that the files go for good.
3. **What does Restore do?** (a) the run returns **kept**, so it cannot expire again at once; (b) it returns un-kept with a fresh clock of 30 days; (c) it returns un-kept with its old clock (an old run would be due again at the next clean-up).
4. **Can you empty it by hand,** and delete one run for good from it?
5. **Disk:** deleted runs still use their pictures' disk space until they are purged (a run of four 2048×2048 pictures is about 25 MB; enlarged copies are 15 to 20 MB each). Is that fine, or should there be a size cap?
6. **Where do runs in progress go?** (Cancel is not delete and is unaffected.)

### 29.10 What was built in 1.12, and what was not checked

Built to §29.1–§29.6: on the server `runfilter.py` (`RunFilter`, `KINDS`, `COUNTED`), `list_runs(limit, before, filter)` and `run_counts` in `db.py`, and `GET /api/runs?kept=` and `GET /api/runs/counts`; on the page `history.ts` (`HistoryFilter`, `matches`, `visibleRuns`, `watchWorking`, what the browser remembers), one view per filter and the counts in the store, `FilterBar` and `EmptyHistory`, a toast that can carry a button, `unkeptText`, and the wiring in `App.tsx`; **Keep** is offered on a card that is still working. The same release carries the Enlarge fix (§28.9).

- **Tests:** backend 967 (pytest; 19 new for the filter, and 3 skipped), 37 more that need `torch` and `spandrel` here (20 for Enlarge's real loading path and 17 for the probe's tiling; the 28 for the real music pipeline need `diffusers` 0.40.0 and were not run in this environment), front end 340 (Vitest; 50 more than 1.11's 290: 5 for Enlarge's waiting and 45 for the shared table of cases, the filter, views, store, the words and the leaving rule) and 157 in a real browser (15 new for the Kept view, on top of 5 for Enlarge's waiting). **Both sides of the filter are tested against one table** (`backend/tests/filter_cases.json`): the server runs it through its SQL, the page through `matches`.
- **Mutation checks, 72 mutants of the Kept code, 70 killed, 2 retired.** Server 13, page logic 33, browser 26. The first run left **four real gaps in my tests**, now closed: nothing tested a page that is exactly full with nothing after it; nothing tested that a snapshot keeps the place the All view had reached; the decision *this run finished without being kept, so say so* sat inside a React effect where no unit test could reach it, so it is now a pure function (`watchWorking`) with its own tests; and the test that a run kept while it works loses its note passed only because it waited for the run to finish, so it now checks while the run is still working. Five of my first mutations did not build (an unused import is a type error here), which counted as *invalid*, not as killed; I redid them as mutations that build, and all were killed. The two retired: one whose code moved into `watchWorking` (the same mutation there is killed), and one that was **equivalent** (a duplicated condition, removed rather than left as a mutant nothing can kill). One thing the mutation checks cannot see: the 12 seconds a toast with a button stays is a timing constant that no test pins.
- **Found by repeating the browser tests, and fixed:** the keyboard focus after un-keeping was moved by a timer that sometimes ran before React had drawn the list without the card, so focus was left on the page. It now moves in an effect after the draw (20 runs in a row pass).
- **Found while building:** Keep was not offered on a card that was still working, so the note *"it stays in this view only if you Keep it"* had nothing to press (§29.7 item 9); and a tab whose newest kept runs are all of the other kind would have said *No kept images yet* with the picture one page further down (§29.1: it reads the next page instead; tested with a page I fabricated in the browser).
- **Not checked:** how the counts query behaves on a history of many thousands of runs (it counts without an index; fine for the hundreds or low thousands a person makes, not measured); any browser but Chromium; how a screen reader announces the toast and the focus move (the markup is the usual one: a live region, a button, a radio group with arrow keys); and the Kept view on the Spark's own history, which is the Spark test (`SPARK_TEST.md` §23).
- **Known limits:** the choice is remembered by each browser and is not shared between them; the counts are asked for 250 ms after a change, so the number can lag a moment; **Undo** after the daily clean-up has deleted the run says that the run no longer exists; and `kept=false` ("only runs that are not kept") works on the server and in `matches` but has no control yet.

---

## 30. Version 1.13: the bin, a Deleted view with a way to empty it (decision #49 DECIDED for what you said; the rest PROPOSED)

You asked that a deleted run be **recoverable**, perhaps through a *Deleted* view in the same filter bar (§29.9). You answered: **it stays there for 30 days before it is really deleted, and there should be a way to empty the "garbage bin"**. You did not answer the other questions of §29.9, and asked me to go ahead, so I chose a default for each and listed it in §30.6 for you to veto.

### 30.1 What it looks like

- **The filter bar** (§29.1) gets a third option: **Show: All | Kept N | Deleted N**. The numbers are for the tab you are looking at. The choice is remembered like the other two, and applies to both tabs.
- **Delete** on a card keeps its name and its question (*Delete this run?*), but now says what happens: *It moves to Deleted and stays there for 30 days; you can restore it from there. After that it is gone for good.* Pressing **Delete** removes the card and shows a toast: *Deleted "…". It stays in Deleted for 30 days.* with **Undo** (which is Restore, §30.4).
- **The Deleted view** lists the runs in the bin as cards with a **Deleted** badge (and no **Keep**). Each says *In the bin since 12 Oct. It will be deleted for good around 11 Nov, in 29 days.* and has **Restore**, **Delete forever** (it asks first) and the two things that are harmless on a deleted run: **Reuse** (loads its prompt and settings into the form) and **Copy prompt**. Its pictures still open in the viewer, to look at or download.
- **Empty bin**, a button in the filter bar's row while **Deleted** is chosen, with the count. It asks first: *Delete 12 runs for good (9 on Images, 3 on Music)? This cannot be undone.* It empties the whole bin, both tabs, and the question says how many of each.
- **An empty bin** says *The bin is empty. Runs you delete stay here for 30 days before they are gone for good.*

### 30.2 What goes to the bin, and for how long

- **A run you delete by hand** (a finished one: done, failed or canceled) goes to the bin. **A run that is still queued** is removed for good, as before: it made nothing worth keeping, and a queued run in the bin would still be picked up by the queue. **A running run** cannot be deleted, as before.
- **A run the daily clean-up expires** (un-kept, finished, older than the retention of 30 days, §5.6) now goes to the bin too, instead of being deleted at once. That is the situation you described in §29: you stop keeping a run, and it is "deleted" later. So an un-kept run now has its 30 days in the history and then 30 more in the bin.
- **In the bin for 30 days** (`STUDIO_BIN_DAYS`, default 30; **0 turns the bin off**, and then Delete and the clean-up delete for good at once, as before 1.13). The clean-up that runs once a day removes the runs whose 30 days are over, with all their files (pictures or tracks, inputs, thumbnails, 4K copies).
- **The files stay on disk while a run is in the bin**, so a deleted run costs the same disk space for 30 days more. There is no size limit; **Empty bin** is the way to get the space back at once.
- A run in the bin is **not in All and not in Kept**, is not counted there, and is not expired again.

### 30.3 What you can do with a run in the bin

**Restore**, **Delete forever**, **Reuse**, **Copy prompt**, and open its pictures. Nothing else is offered: not Keep, Retry, Edit this, Make 4K or Enlarge. (The server does not forbid the rest, it only does not offer it: a script that makes a 4K copy of a deleted picture works, and the copy goes when the run does.)

### 30.4 Restore

**Restore puts the run back as it was**: a kept run is still kept, an un-kept one is un-kept. If it is un-kept it gets **a fresh clock**: its retention counts from the day it was restored, so that a run the clean-up put in the bin is not put there again at once. The toast says which: *Restored "…". It is still kept.* or *Restored "…". It has a fresh 30 days.* It reappears in All (and in Kept if it is kept) at the place its age gives it.

### 30.5 The server

- **Schema 4** adds two columns to `runs`: `deleted_at` (when it went to the bin) and `restored_at` (when it was last restored). A copy of the database is made first, as for every schema change (`studio.sqlite.before-schema-4`); the migration is tested on a 1.7 and on a 1.12 database.
- `POST /api/runs/{id}/bin` moves a finished run to the bin: `200` with the run, `404`, `409` `run_not_finished` (queued or running: delete a queued one with `DELETE`, cancel or wait for a running one). `POST /api/runs/{id}/restore`: `200` with the run, `404`, `409` `not_in_bin`. `DELETE /api/runs/{id}` is **unchanged** and means *for good*, in or out of the bin (a running run is still refused). `DELETE /api/bin` empties the bin: `200` `{"deleted": n}`.
- `GET /api/runs?deleted=true` lists the bin; **without `deleted` the list is the history and leaves the bin out** (as does the list the event stream starts with). `GET /api/runs/counts` gains `deleted` beside `all` and `kept`.
- A run's payload gains `deleted_at` and `purge_at` (when it will be deleted for good; both null when it is not in the bin), and its `expires_at` is null while it is there. `capabilities.limits.bin_days` says how long the bin keeps a run (0 = no bin), so the page can say it in words.
- **Events:** moving to the bin and restoring send `run.updated` (the page's views move the run by themselves, §29.5); deleting for good sends `run.deleted`, one for each run when the bin is emptied.
- **The janitor** (once at start-up, then daily) first deletes for good the runs whose time in the bin is over, then moves the expired runs to the bin. With `STUDIO_BIN_DAYS=0` it deletes the expired runs as it always did.
- The expiry of a run counts from the later of when it was made and when it was last restored.

### 30.6 The small choices I made (tell me if you want any changed)

1. **Both a hand delete and the daily expiry go to the bin** (your words were about the bin in general). The cost is that an un-kept run lives 60 days in all. If you want only hand deletes in the bin, the clean-up changes by one line.
2. **Restore keeps the run as it was** (kept stays kept) **and gives an un-kept run a fresh 30 days**, rather than always making it kept. This is the answer to the question of §29.9 item 3 that I think surprises the least; the alternatives were *always kept* and *the old clock*.
3. **Only finished runs go to the bin**; a queued run is removed for good (§30.2).
4. **The card's button is still *Delete*,** and so is the question; the dialog now explains the bin.
5. **A single run can be deleted for good from the bin**, with a question first, as well as the whole bin being emptied.
6. **Empty bin empties both tabs**, and the question says how many of each. A button that emptied only the tab you are on would leave you not knowing what was left.
7. **The Deleted view is ordered like the others, by when the run was made,** not by when it was deleted. A run you deleted a moment ago is found by its Undo toast, or by its age.
8. **The bin has no size limit** (§30.2).
9. **The Deleted option is always in the bar,** with its count, even when the bin is empty, so that it can be found.
10. **The Undo toast after a delete stays 12 seconds,** as the Undo of §29.4 does.

### 30.7 Acceptance criteria (continue §29.8)

121. **Delete** on a finished run moves it to the bin: its card leaves All and Kept, a toast says so with **Undo**, and the run is in **Deleted** with its files still on disk. A queued run is removed for good. A running run cannot be deleted. (1.13)
122. The Delete dialog says that the run goes to Deleted and for how long (`bin_days`); with `STUDIO_BIN_DAYS=0` it says what it always said and deletes for good. (1.13)
123. The filter bar has **Deleted N** (the count for the tab shown); choosing it shows only the runs in the bin, with the same paging and the same remembered choice as the others. (1.13)
124. A card in the bin shows when it was deleted and when it will be gone for good, has **Restore**, **Delete forever**, **Reuse** and **Copy prompt**, and offers nothing that changes the run. (1.13)
125. **Restore** returns the run to All (and Kept if it is kept) as it was; an un-kept run's retention counts from the restore. A toast says which. (1.13)
126. **Delete forever** asks first, then removes the run and all its files for good. (1.13)
127. **Empty bin** asks first (saying how many on each tab), then removes every run in the bin and its files for good; it is disabled when the bin is empty. (1.13)
128. The daily clean-up **moves** an expired un-kept run to the bin, and **deletes for good** a run that has been in the bin for `bin_days`, with all its files; kept runs and runs in the bin are never expired. With `STUDIO_BIN_DAYS=0` it deletes expired runs as before. (1.13)
129. `POST /api/runs/{id}/bin`, `POST /api/runs/{id}/restore`, `DELETE /api/runs/{id}` (unchanged), `DELETE /api/bin` and `GET /api/runs?deleted=` answer as §30.5 says; the history list and the stream's first page leave the bin out; `counts` has `deleted`. (1.13)
130. Schema 4 is made from a 1.7 and from a 1.12 database without losing a run; a copy of the old database is made first; a migrated database is the same as a fresh one. (1.13)
131. Every open page follows a move to the bin, a restore and a deletion for good live. (1.13)
132. In the Deleted view no run is shown only because it is working, and no toast says that a run left the view. (1.13)
133. On a **phone** the filter bar with three options and Empty bin, a card in the bin and the dialogs fit with no sideways scroll. (1.13)
134. The server's rule and the page's `matches` agree on the shared table of cases, now with the bin in it (§29.5). (1.13)

### 30.8 What was built in 1.13, and what was not checked

Built to §30.1–§30.5: **schema 4** (`runs.deleted_at`, `restored_at`) with its migration and the copy made first; `RunFilter.deleted`; in `db.py` `bin_run`, `restore_run`, `purge_bin`, `expire_to_bin` and `delete_expired` (all counting expiry from the later of *made* and *restored*); in the job manager `bin_run`, `restore_run`, `empty_bin` and the new daily clean-up; `POST /api/runs/{id}/bin` and `/restore`, `DELETE /api/bin` and `?deleted=`; `deleted_at` and `purge_at` in the payload, `bin_days` in the capabilities, `STUDIO_BIN_DAYS`. On the page: the `deleted` field of `HistoryFilter` with `canBin` and `showsWorking`, the third option and **Empty bin** in the filter bar, the cards in the bin and the read-only viewer, the three questions of `ConfirmDelete` and `ConfirmEmptyBin`, the toasts and their wording.

- **Tests:** backend 1009 (pytest; 42 more than 1.12's 967: 28 for the bin and the rest in the files it touched, and 3 skipped), 65 more that need `torch` (not changed), front end 369 (Vitest; 29 more than 1.12's 340) and 171 in a real browser (14 more than 1.12's 157). The table of cases that both sides are tested against (§29.5) now has the bin in it.
- **Mutation checks, 77 mutants of the bin's code, 76 killed.** Server 36, page logic 22, browser 19. The survivor is **equivalent**: a mutation of *Empty bin* that shows it for a filter whose `deleted` is `null` ("either"), which the page never builds. The first run found **gaps in my tests, now closed**: nothing tested the exact moment a run in the bin is due; the empty bin's *"stay here for 30 days"* could ignore the setting without any browser test noticing, because they all run at the default (the empty states and the filter bar are now rendered in unit tests); nothing pinned the name and the default of `STUDIO_BIN_DAYS`; *whether Delete moves a run to the bin* sat inside a React handler where no unit test could reach it, so it is now a function (`canBin`) with tests; and nothing tested that **Empty bin** follows the whole bin and not the tab you are looking at (a bin holding only a track must still be emptiable from Images). Five of my first mutations did not build or changed nothing and were redone.
- **Found while building:** the test helper that clears the history listed only the history, so deleted runs of one browser test leaked into the next (it now empties the bin too); and **three existing tests assumed Delete removes the files at once**, which is no longer true while a run is in the bin: they now check that the files are there in the bin and gone after *Delete forever*. One `ResourceWarning` appeared once in a full backend run; I could not reproduce it in the files this release touches.
- **Not checked:** the first start on the Spark's **real** database (the migration is tested on constructed 1.7 and 1.12 databases and the first start makes a copy; `SPARK_TEST.md` §24 starts with checking it); how the bin behaves with thousands of runs or many 4K copies (emptying it deletes in batches inside the request, so a very large bin takes a while); the daily clean-up on a real clock (tested by ageing runs in the database); any browser but Chromium.
- **Known limits:** the first start of 1.13 moves every un-kept run older than 30 days to the bin at once (they were due for deletion; they now wait 30 days), and the disk is only freed when they are purged or the bin is emptied; the Deleted view is ordered by when a run was *made*, not when it was deleted (§30.6 item 7); the Undo toast belongs to the page that deleted the run.

---

## 31. Version 1.17 (proposed, nothing built): Vector, an SVG made from a prompt, in a number of colours you choose, one layer per colour, for a laser (decision #50 PROPOSED; this section is the specification, and it ends with the questions I need you to answer before I build anything)

*Numbering.* Vector is built after §32 to §34 (project folders in 1.14, deleting a picture in 1.15, the 16:9 default in 1.16), so it is **version 1.17 with schema 7** (§32 takes schema 5 and §33 schema 6). If the order of building changes, these two labels change with it. Its criteria, 135 to 145, keep their numbers.

You asked for three things, in three messages:

1. *"I want to be able to produce a vector image in SVG format from a prompt. So instead of a rasterized PNG, it would instead produce a vector SVG."*
2. *"One of the things I want to be able to do is to say how many colors are in the vector image. Black and white is 2, but black, white, red, and green, are 4 colors. I also need to support layers."*
3. *"Ultimately, what I want to be able to do is to have each layer cut out on a laser cutter like my xTool P2 or engraved with my fiber laser, a Weike G3 Ultra."*

And you asked which model would make the pictures, and that it be **the best open-source model for this**. §31.2 answers that as honestly as I can from here.

### 31.1 What this changes about the design

The third message changes what "an SVG" has to be. A picture that merely *looks* like a vector drawing is not enough for a laser. A laser follows paths, one operation at a time, so the file has to be **flat colour regions, exactly the number of colours you asked for, closed shapes, one layer for each colour, no overlaps that would be cut twice, and a real size in millimetres**. Those are properties of the *file*, and a model cannot be trusted to promise them; **the studio can guarantee them with its own code**, after the model has made the picture. So the design is: *a model makes the artwork, and a clean-up step that I write and test makes it laser-ready.* The clean-up is the part I can test completely without a GPU.

What I checked about your machines (the sources are in §31.11), and what I could not:

- **xTool P2** is a 55W CO₂ laser cutter with a bed of 23.6" × 12.1" (about 600 × 307 mm). Its software is **xTool Creative Space** (XCS), which is what you use with it (it also works with **LightBurn**). Listings for the P2 and its successor the P2S say it takes **SVG, DXF, PNG, JPG and BMP**. *Not confirmed:* how Creative Space treats the layers or colours of an SVG, and whether the P2 (not the P2S) takes exactly that list.
- **Gweike G3 Ultra** pairs a **60W MOPA fibre laser** (adjustable pulse width and frequency, which is what lets a fibre laser colour or deeply engrave metal) with a **40W diode laser** for non-metals; it has a camera for positioning. Gweike publishes a **LightBurn control guide** for the G3, and LightBurn is what you use with it. *Not confirmed:* its working area.

### 31.2 Which model? The honest answer

**I cannot name "the best open-source model" from here, and I do not want to guess.** No independent benchmark compares open text-to-SVG models on laser-style work, and the rankings I found online are third-party aggregations I could not verify. What I did verify, from the models' own pages:

| Candidate | What it is | What is established | The catch |
|---|---|---|---|
| **Qwen-Image, then trace** | The studio's own model draws a flat picture; code reduces it to N colours and traces each colour into closed paths | The model is already installed and running; tracing tools exist (VTracer is MIT-licensed, potrace is GPL) | Tracing flat art works well; photographs make huge, blobby files. Not confirmed: that VTracer installs on the Spark's aarch64 (its README does not say which wheels exist). |
| **OmniSVG 1.1** (4B and 8B) | A model built to write SVG from text or from an image, fine-tuned from Qwen2.5-VL | Text-to-SVG supported; its card says "from simple icons to intricate anime characters"; code **Apache-2.0**, the model's metadata lists apache-2.0; **16 GB** of GPU memory for the 4B; 7.6 GB download; 4 s for 256 tokens up to 83 s for 4096 | The training dataset (MMSVG) is **CC BY-NC-SA 4.0**, non-commercial; whether that reaches the weights is a licence question; **you have said yes to the OmniSVG licence** (§31.7, answer 11). Tested on PyTorch 2.3 / CUDA 12.1 and needs the Cairo library: whether it runs on the Spark's PyTorch and GB10 is unknown. Its SVG has overlapping shapes, gradients and strokes, which need the clean-up. |
| **StarVector** (1B and 8B) | An SVG generator built on a code model | Apache-2.0; excels at *icons, logos, technical diagrams, graphs and charts* | Its README says it "will not work for natural images or illustrations", and shows no text-only inference example (only image-to-SVG). Not a first choice. |
| **A general LLM writing SVG code** | Whatever LLM you serve (your Hermes vLLM server offers an OpenAI-compatible API) | Nothing needs loading in the studio | **Dropped: you said Hermes will not be used** (§31.7, answer 10). (It would have been only as good as the LLM behind it: fine for icons and geometry, weak for detailed illustration.) |
| **Qwen-Image-Layered** | A Qwen model that splits a picture into RGBA layers | It exists and is open | Its layers are *raster*, split by object, not by colour, and not SVG. Only useful if "layer" means "object"; you said a layer is by colour or by shape (§31.7, answer 1), so it is **not** a candidate for now. |

**My recommendation, for laser work, is two engines that share one clean-up:**

- **Engine A, "Draw and trace" (first, the default):** Qwen-Image draws the artwork in a flat style the studio prescribes; the clean-up reduces it to your N colours and traces it. **Why first:** it needs no new model, no new weights, no model switching (it uses the image worker you already have), Qwen-Image is the strongest picture-maker the studio has, and *the colour count, the closed shapes and the layers come from my code, not from the model's goodwill.*
- **Engine B, "Write the SVG" (second, only after a probe on the Spark):** OmniSVG 1.1 (4B first; the 8B if it is better), its output passed through the same clean-up. It would be a third model kind next to the image and music models (decision #41: one model in memory at a time). **Why second:** it is a new model with its own dependencies (a Cairo library, a PyTorch version that may not match NVIDIA's), and its strength, a clean *editable* path structure, is not what a laser needs most.

**What decides between them is a probe, not my opinion** (§31.9): the same prompts through each engine on the Spark, with the time, the memory, the file and what the result looks like at 2 and 4 colours. You choose by looking at the pictures. Until it has run, "Engine A first" is a design judgement, not a measurement.

### 31.3 What you choose (the Vector form)

A third tab, **Vector** (your decision), beside Images and Music, with:

- **Prompt**, and *what the studio adds to it*, shown in full and editable (as the Music tab shows its description): the flat-design wording, the colours, "no gradients, no shading, thick clean outlines, plain background". A fixed negative prompt keeps out gradients, photographs, text and thin lines.
- **Kind of artwork:** *Silhouette or stencil* (shape against nothing), *Flat colour art*, *Line art* (outlines only). The kind changes the wording the studio adds and the defaults below.
- **Colours: 2 to 8** (default 2). **The number is a limit the clean-up enforces:** the result has *at most* N colours, and the page says how many it has (a design that really uses 3 when you asked for 4 has 3; an empty layer is dropped, and the page says so). **You can say which colours** (black, white, red, green… as swatches, or any colour) or let the picture decide.
- **White and the background (your answer: white is not a colour in this regard):** white is the bare material. **It never gets a layer, and nothing is cut or engraved for it.** **It still counts toward *Colours*** (your answer to question 2b, matching your examples): *2* is black plus white, which is one layer; *4* is black, white, red and green, which is three layers. So a design with white among its colours has one layer fewer than its number of colours, and the page says how many layers it will have.
- **Size in millimetres** (width × height, default 100 × 100), with a **machine preset** for each of your two machines once you tell me their working areas. The SVG is made at that real size.
- **Layers by:** *Colour* (the default) or *Shape* (your answer: a layer can be one per colour or one per shape; by *shape* you chose **each separate piece** (question 1b), so a drawing of 40 leaves is 40 layers, numbered, up to a cap that keeps the file usable; §31.10, item 7). **Arrangement, your choice on the form (your answer to question 4: stacked or side by side):**
  - **Side by side:** each layer holds only its own colour's regions; the layers do not overlap. For inlay and for engraving.
  - **Stacked:** each layer also holds everything above it, so that pieces cut from separate sheets can be glued up with no gaps, with a small **bleed** (default 0.5 mm) so edges overlap rather than meet.
- **Cut or engrave is not in the file** (your answer to question 3): every layer is closed shapes in its own exact colour, and you set cut or engrave for each layer in XCS or LightBurn. Whether the shapes are best written *filled* or as *outlines* for those two programs is what the test sheet (§31.9) is for; until it says, they are filled.
- **Smallest feature, in mm** (default 0.5): anything smaller than this, a speck or a gap or a bridge, is removed or flagged, because it will not survive being cut. Your real limit depends on the material and the laser (*open question 5*).
- **Versions** (1 to 4 pictures from the same prompt) and **seed**, as the Images tab has.

### 31.4 What it makes

- **An SVG at real size**: `width="100mm" height="100mm"`, a `viewBox` in millimetres (a file with no units is read differently by different programs, 96 or 72 dots to the inch, which would make a laser cut the wrong size), **one `<g>` per layer** with an id, a name and the layer's colour as its fill so that it looks right on screen. **Every layer's colour is distinct and exact**, because that is how laser software (LightBurn certainly) assigns a cut or engrave setting to a layer.
- **Only what a laser can use:** closed paths, holes as sub-paths, no gradients, no transparency, no filters, no text (text becomes outlines), no embedded pictures, no scripts, no links to anything outside the file, paths simplified to a tolerance, **no shared edge drawn twice** (a laser would cut it twice and burn it), and no stray specks.
- **Per-layer files** in a ZIP (`layer-1-black.svg`, `layer-2-red.svg`…), each at the same size and origin so they register when stacked, **the combined SVG**, and **a PNG at any size you choose** (sharp at 4K from the vector, which is the one thing a raster picture cannot do).
- **A check, shown on the card:** the number of colours, paths per layer, the smallest feature found, the total cut length per layer (a rough idea of the time), and anything that was removed or that needs a look.
- **Copy SVG code** and **Download SVG**, as the other tabs have **Keep**, **Reuse** and **Delete** (with the bin of §30). Vector runs join the history filters of §29 (`vector` is a third kind beside *image* and *music*) and the counts.

### 31.5 How it is made

**Engine A, draw and trace** (all of it, apart from the picture, runs on the CPU and is testable without a GPU):

1. The studio builds the prompt (§31.3) and the image worker makes the picture (`mode: "vector"` is an image run with a different recipe; no model is loaded or unloaded).
2. **Reduce to N colours:** cluster the picture's colours (in a perceptual colour space) to exactly the colours chosen, or to the palette you named; remove the soft fringes at edges; remove regions smaller than the smallest feature.
3. **Trace** each colour's regions into closed curves (VTracer or potrace; *which* is decided by the probe).
4. **Clean up for the laser:** simplify the paths; merge, split or offset them to the arrangement (side by side or stacked, with bleed); when the layers are by shape, give each separate piece its own layer; remove duplicate edges; optionally offset by half the kerf; flag what is smaller than the smallest feature or is a hanging island (the centre of an *O* falls out of a stencil unless it is bridged).
5. Write the SVG and the per-layer files in millimetres, draw the preview, and keep the check's numbers.

**Engine B, write the SVG** (after the probe): the model's SVG is read by a strict parser (no external entities), **reduced to the allowed elements and attributes**, its colours clustered to N, gradients made solid, strokes made into outlines, overlapping shapes united per colour by a geometry library, and then steps 4 and 5 above. The page offers the engine as a choice only if the probe shows it is worth having.

**Where the work happens:** Engine A's picture is made by the image worker; the rest is a short-lived process of its own, like Enlarge's (§28.3), so a crash cannot take a loaded model down.

### 31.6 What it needs

- **A tracer** (`vtracer`, a Rust extension on PyPI, MIT; or `potrace`, GPL, as a separate program) and **a geometry library** for the clean-up (for example `shapely`, BSD; a polygon-offsetting library for the kerf), in the container. *Not confirmed:* that they install on aarch64 inside NVIDIA's image; the build prints a note, as for `spandrel`, rather than failing, and the probe says.
- **A schema change** (schema 7): `runs.mode` gains `'vector'`, which SQLite cannot alter in place, so the table is rebuilt as in schema 3, with a copy of the database made first and a migration test from a 1.16 database. **The rebuild must carry every column that schemas 4 to 6 added to `runs`** (`deleted_at` and `restored_at`, §30.5; `project_id`, §32.4), or it would drop them silently; a test checks that a migrated database has the same columns as a fresh one (criterion 144).
- **Files:** `data/vector/<run>/<n>.svg`, `<n>-layers.zip`, `<n>.png` (the preview); all go with the run, into the bin and out of it.
- **Safety:** an SVG is served as `image/svg+xml` with a `Content-Security-Policy` that forbids scripts and everything else, `X-Content-Type-Options: nosniff`, and the page shows it only in an `<img>`, where scripts never run. The studio makes the SVG itself, but Engine B's output comes from a model, so it is cleaned to an allow-list before it is stored.

### 31.7 Your answers, and what is still open

You have answered eight of the eleven questions in the first draft. I have kept the first draft's numbers so that the text above still points at the right place. What I recorded from each answer:

| # | Question | Your answer | What the spec says |
|---|---|---|---|
| 1 | What does "layer" mean? | "Layer can mean one per color or shape." | Layers **by colour** or **by shape**, a choice on the form (§31.3). |
| 1b | What is a "shape"? | "Each separate piece." | Every unconnected closed piece is its own layer (numbered, up to a cap, §31.10 item 7). Telling a hat from a face is not part of it. |
| 2 | White and the background | "White is not a color in this regard." | White is the bare material: **no layer, nothing cut or engraved for it.** |
| 2b | Does white count toward the colours? | "Yes, white counts." | *2* is black + white, one layer; *4* is black, white, red, green, three layers. |
| 3 | Cut or engrave in the file? | "No, I set it in XCS / LightBurn." | The file does not say which. Every layer is closed shapes in an exact colour. |
| 4 | Stacked or side by side | "I want the option of stacking the layers or put them side by side." | Both, chosen on the form for each run (§31.3). The default is side by side (§31.10). |
| 6 | Software | "The P2 uses XCS and the Gweike uses lightburn." | Recorded in §31.1. |
| 6b | The test sheet | "Yes, I'll do it." | Built: `scripts/laser_test_sheet.py` and `docs/laser_test_sheet.svg` (§31.9). **I am waiting for what you see in XCS and in LightBurn.** |
| 9 | Where it lives | "Vector should be its own tab." | A third tab, **Vector** (§31.3). |
| 10 | Hermes | "Don't worry about Hermes. We won't be using it." | The LLM-writes-the-SVG route is **dropped** (§31.2). |
| 11 | Licence | "Yes to the OmniSVG license." | Engine B is not ruled out on licence. *I read this as "the OmniSVG licence is acceptable to me". It changes none of the facts: the dataset is non-commercial, and whether that reaches the weights is yours to settle if you sell what you make with it.* |

**Still open, with the default I take until you say otherwise:**

- **5. Sizes and limits:** the typical size of your work in mm; the thinnest bridge or gap each machine and material can hold; the kerf if you compensate for it. Defaults: 100 × 100 mm and a smallest feature of 0.5 mm.
- **7. Other formats:** DXF or PDF as well? Default: SVG only in 1.17.
- **8. Engine:** *draw and trace* first, and a native SVG model only if the probe shows it is better. Default: yes.

### 31.8 Acceptance criteria (proposed; they continue §30.7)

135. A **Vector** tab makes an SVG from a prompt; the SVG is the result, not a PNG. (1.17)
136. **Colours (2 to 8)** is enforced by the studio, not requested of the model: the SVG has **at most** that many fill colours, each distinct and exact, and the card says how many it has; named colours are used when given. (1.17)
137. The SVG has **one layer for each colour, or for each shape** (as chosen; `<g>` with an id and a name), **white gets no layer**, and the layers are in the chosen arrangement: side by side without overlap, or stacked with bleed. (1.17)
138. The SVG is at **real size**: `width`/`height` in `mm` and a `viewBox` in mm; the per-layer files have the same size and origin. (1.17)
139. The SVG contains **only** closed paths and groups: no gradient, opacity, filter, text, `<image>`, script, event handler or external reference; no edge shared by two paths is drawn twice; nothing is smaller than the smallest feature, or it is flagged. (1.17)
140. The file does not say cut or engrave: every layer is closed shapes in its own exact colour. The card shows the check (colours, layers, paths per layer, smallest feature, cut length per layer). (1.17)
141. Downloads: the SVG, a ZIP of one SVG per layer, and a **PNG at a size you choose**, drawn from the SVG. (1.17)
142. The SVG is served as `image/svg+xml` with a script-forbidding `Content-Security-Policy` and `nosniff`, and shown only in an `<img>`. (1.17)
143. A vector run has Keep, Reuse, Delete, the bin, the filters and the counts like the others; `vector` is a kind beside `image` and `music`; its files go to the bin and back with it. (1.17)
144. Schema 7 is made from a 1.16 database without losing a run, after a copy of it; a migrated database equals a fresh one. (1.17)
145. The clean-up is **tested without a GPU** on constructed pictures (exact N, closed shapes, no duplicate edges, the arrangements, the smallest feature, the real size), and mutation-checked like the rest of the studio. (1.17)

### 31.9 What I would build first (small, and it answers your questions)

1. **`scripts/laser_test_sheet.py` and `docs/laser_test_sheet.svg` (built; the SVG is in the repository, so there is nothing to run, and the script makes it again on any computer with Python):** one SVG, **100 × 100 mm** (one unit is one millimetre), written the way the Vector tab would write a file: **four layers in four exact colours** (black `#000000`, red `#FF0000`, green `#00FF00`, blue `#0000FF`), closed shapes only, no text. Each layer holds the same five things so that the layers can be compared: a **filled square** and an **outline square** (15 mm), a **ring** (a filled circle, 15 mm, with a 7 mm hole, drawn so that it is a hole under either fill rule), an **outline circle** (15 mm, in the cubic curves a tracer writes) and a **10 × 2 mm scale bar**. Layer 1 also has an outline frame round the whole sheet. Each layer has an id (`layer-2-red`) *and* an Inkscape label (`Layer 2 (red)`) that differ, so that what a program shows tells us which of the two it reads; each shape has an id (`layer2-ring`) so that you can name the one that misbehaves. **Open it in Creative Space (the P2) and in LightBurn (the G3 Ultra) and tell me, for each:** (1) does the design measure 100 × 100 mm (100.1 is fine: that is the frame's line width), and a scale bar 10 mm? (2) what layers appear, and what are they called? (3) can each layer, or each colour in LightBurn, be given its own cut or engrave setting? (4) do the ring's hole and the circles come in as holes and circles, and does an engraving preview of the ring leave the hole empty? (5) are the outline shapes taken as lines and the filled shapes as areas, and which of the two looks right for cutting? (6) is anything missing, moved, joined to another shape, or the wrong colour? A screenshot from each program is better than a description. (An hour of your time at most; it removes the largest unknown, and it decides small choice 8 of §31.10.) **Checked:** 18 tests (`backend/tests/test_laser_test_sheet.py`) read the SVG as XML and parse its paths themselves (the size in millimetres, four exact distinct colours, the same five shapes in every layer, every path closed and made only of M, L, C and Z, nothing off the sheet or touching another shape, round circles, a ring that is a hole under either fill rule, plain ASCII), and one ties the committed file to the script; **33 mutations of the script, 32 killed**. The survivor drops `encoding="utf-8"` from the write and is equivalent, because the file is plain ASCII and no encoding can change a byte of it (a test says so).
2. **`scripts/vector_probe.py`** (run in the studio container on the Spark, like `upscale_probe.py`): the same prompts, at 2 and at 4 colours, through *draw and trace* and through OmniSVG 4B (and 8B), reporting time, memory, number of paths, file size, smallest feature and a contact sheet of the pictures, so that you can pick by eye.
3. **Then, only after your answers (§31.7):** the clean-up and the SVG writer (pure Python, tested without a GPU), the Vector tab, schema 7, and Engine A. Engine B only if the probe says so.

### 31.10 The small choices I made (tell me if you want any changed)

1. **At most N colours, not exactly N.** If the picture really has three colours, you get three layers and a note, not an empty fourth layer.
2. **Millimetres, always.** There is no unitless export.
3. **The first colour of a layer is its colour on screen and in the file,** and it is never the same as another layer's, so the software can tell them apart.
4. **No text in the artwork.** The negative prompt keeps it out, and text from Engine B is outlined. Lettering you want is better added in your laser software, in a font you choose.
5. **The seed makes the picture, not the clean-up:** the clean-up is deterministic, so the same picture and settings give the same SVG.
6. **The arrangement defaults to side by side,** because it never leaves an area to be cut twice; stacking is one choice on the form.
7. **Layers by shape are capped at 30** (my guess at what a laser program will take; I have not checked what XCS or LightBurn accept). In that mode a layer's colour is a *label* from a fixed palette of distinct colours, since laser programs tell layers apart by colour; the card shows the label and the picture's own colour.
8. **Shapes are written filled, with no stroke,** until the test sheet shows what XCS and LightBurn do with filled shapes and with outlines.

### 31.11 Sources

- OmniSVG 1.1 model card: <https://huggingface.co/OmniSVG/OmniSVG1.1_4B>
- StarVector README: <https://cdn.jsdelivr.net/gh/joanrod/star-vector@main/README.md>
- VTracer: <https://github.com/visioncortex/vtracer>
- Qwen-Image-Layered (raster layers): <https://docs.comfy.org/tutorials/image/qwen/qwen-image-layered>
- xTool P2 listing: <https://www.matterhackers.com/store/l/xtool-p2-co2-laser-engraver-and-laser-cutter-55w/sk/MGD5TP18> (the file-format list is from P2S listings: <https://top3dshop.com/product/xtool-p2s-55w-co2-laser-cutter-and-engraver>)
- Gweike G3 Ultra listing: <https://woodartsupply.com/products/gweike-g3-ultra-60w-mopa-40w-diode-dual-laser-engraver-3d-grayscale-color-metal-engraving-16mp-smart-camera-15-000mm-s-fast-fiber-laser-cutter-and-laser-engraver-machine-for-metal-wood-acrylic>

---

## 32. Version 1.14: project folders (decision #51 DECIDED for what you said; the rest PROPOSED; nothing built)

You asked for **project folders**: when you like a generation you want to add it to a drop-down of project folders, or make a new folder right then and there, and **once a generation is in a project it is also kept, so that it is not deleted by accident**. You also said you need to delete the pictures you do not like from a run; that is a release of its own (§33, 1.15). I asked four questions and you answered them:

| # | Question | Your answer |
|---|---|---|
| 1 | What gets filed: the whole run, or single pictures? | **The whole run** (§32.2). |
| 2 | Can a run be in more than one project? | **One project at a time** (§32.2). |
| 3 | What does the Keep button do while a run is in a project? | **Locked while filed** (§32.3). |
| 4 | Where do you browse a project? | **A Project drop-down in the filter bar** (§32.1). |

*Numbering.* §31 (Vector) is a proposal too, and nothing in it is built. So that nothing collides, this section and §33 take §32 and §33, decisions #51 and #52, versions 1.14 and 1.15, schemas 5 and 6, and criteria from 146 on; §34 takes version 1.16, decision #53 and criteria 177 to 184. Vector is built after them, so it is version 1.17 with schema 7 and keeps its criteria, 135 to 145.

### 32.1 What it looks like

- **On every card** (a picture run or a music run), in the row of buttons beside **Keep**: a **Project** button. On a run that is in no project it says **Add to project ▾**. On a run that is in one it shows the project's name, **Logo ▾**, and the card shows its **Kept** badge as ever.
- **The drop-down** lists your projects, A to Z (ignoring case), then a line, then **New project…**. Choosing a project files the run in it (and keeps it, §32.3). Choosing **New project…** turns the drop-down into a one-line field, *Name of the new project*, with **Create** (Enter) and **Cancel** (Esc); **Create** makes the project **and files the run in it, right then**. On a card that is already filed the list shows a tick beside its project, choosing another project **moves** the run, and a last line, **Take out of project**, takes it out.
- **Every filing and every move says so, with Undo** (a toast, 12 seconds, as in §29.4): *Filed "a red fox in the snow…" in Logo. It is kept.* · *Moved "…" from Logo to Pitch.* · *Taken out of Logo. It is still kept.* **Undo** puts the run back exactly as it was before the click (§32.3 item 3).
- **A filed card's Keep button is pressed and locked**, with the tooltip *Kept because it is in the project "Logo". Take it out of the project first.* (§32.3).
- **The filter bar** (§29.1) gets a second control beside **Show: All | Kept | Deleted**: **Project**, a drop-down with **Any project** (the default), **No project**, and then each project with the number of runs in it on the tab you are looking at (*Logo 4*). Next to it a small **Manage** button opens the projects dialog (below). Like the other filters the choice **applies to both tabs**, **combines with All, Kept and Deleted**, and is **remembered by this browser**. With a project chosen, the three numbers beside All, Kept and Deleted are for **that project on this tab** (§32.5), so *All 4 | Kept 4 | Deleted 0* is a true description of what you would see.
- **Manage** opens a dialog: a *New project* field; the list of projects with how many pictures and tracks each holds; **Rename** on each (in place; a name already taken is refused) and **Delete project**, which asks first: *Delete the project "Logo"? Its 4 runs (3 pictures, 1 track) are not deleted: they stay kept and are no longer in a project.*
- **Empty states.** A project with nothing on this tab says *Nothing in "Logo" on Images yet. Use Add to project on a card to file one here.* (it names the tab, because the other tab may have runs in it) with **Show any project**. **No project** with nothing to show says *Every run on this tab is in a project.*
- **A project that has gone.** If the project this browser remembers no longer exists (another page deleted it), the filter falls back to **Any project** and a toast says *That project no longer exists.*
- **A card in the bin** (§30.1) shows its project's name as a chip that cannot be pressed, and has no Project button (§32.3 item 7).

### 32.2 What is filed, and what a project is

- **The whole run** is filed (your answer 1): every picture of a generation, or the tracks of a music run, moves together, because Keep, the bin and the expiry are all per run today and a run must not end up half in a project.
- **One project at a time** (your answer 2), like a real folder: choosing another project moves the run. A run is in exactly one project or in none.
- **Both tabs.** There is one list of projects. A project can hold pictures and tracks; each tab shows its own part of it. A queued or running run can be filed too (it is kept when it finishes, as with Keep, §29.7 item 9).
- **A project is a name, not a folder on the disk.** It is a label in the database: the files stay where they are (`images/<run>/`, `audio/<run>/`), so filing is instant, moves no file, and the backups (which contain the database) already contain your projects.

### 32.3 Keep and projects (your answer 3)

1. **Filing a run keeps it**, in the same step: there is no moment when a filed run is not kept.
2. **While a run is filed, Keep cannot be turned off.** The card's button is locked (§32.1) and the server refuses `{"pinned": false}` for it with `409` `run_in_project`. Pressing Keep on a run that is kept already is still fine (`{"pinned": true}`).
3. **Taking a run out of a project leaves it kept**, so that it cannot expire by surprise; to stop keeping it, take it out and then press **Keep**. The one exception is **Undo of a filing**, which puts the run back exactly as it was before (if it was not kept before, it is not kept now; the toast said it would be).
4. **The daily clean-up never expires a filed run.** The run is kept, and the clean-up's condition also says *not in a project* (§5.6, §30.5), so that it holds even if something cleared the flag by hand.
5. **A filed run can still be deleted by hand** (Delete is a question and goes to the bin for 30 days, §30). The question names the project: *It is in the project "Logo". It moves to Deleted and stays there for 30 days; restoring it puts it back in the project. After that it is gone for good.* A run in the bin keeps its project, so **Restore** puts it back there, kept.
6. **Deleting a project never deletes a run.** The runs stay kept and are no longer in a project, including runs that are in the bin (they come back from the bin unfiled and kept).
7. **A run in the bin cannot be filed** (restore it first): `409` `run_in_bin`.

### 32.4 The server

- **Schema 5.** A table `projects` (`id`, `name`, `name_key`, `created_at`) and a column `runs.project_id` (null, or a project's id), with an index. `name_key` is the name trimmed, with runs of spaces made one, and lower-cased; it is **unique**, so *Logo* and *logo* are the same project. A copy of the database is made first, as for every schema change (`studio.sqlite.before-schema-5`); the migration is tested on a 1.7, a 1.12 and a 1.13 database.
- **A name** is 1 to 60 characters after trimming, with no control characters (`capabilities.limits.project_name_max` says 60, so the page can say it). Refused with `422` `bad_name`; a name already taken (ignoring case) with `409` `name_taken`. A project can be renamed to a different spelling of its own name.
- **Routes.**
  - `GET /api/projects` → `{"projects": [{"id", "name", "created_at", "counts": {"image": 3, "music": 1}}]}` (the counts are runs in the history, not in the bin), A to Z.
  - `POST /api/projects` `{"name"}` → `201` with the project.
  - `PATCH /api/projects/{id}` `{"name"}` → `200`; `404`; `409`; `422`.
  - `DELETE /api/projects/{id}` → `200` `{"unfiled": n}`; `404`. Its runs lose their `project_id` and stay kept.
  - `PUT /api/runs/{id}/project` `{"project_id"}` → `200` with the run. **Files the run and keeps it in one transaction**; the same call **moves** a filed run. `404` (no such run or project), `409` `run_in_bin`.
  - `DELETE /api/runs/{id}/project` → `200` with the run, leaving it kept. With `?keep=false` it also stops keeping it (this is what **Undo** of a filing sends). `404`, `409` `not_in_project`.
  - `PATCH /api/runs/{id}` (Keep) with `pinned: false` on a filed run → `409` `run_in_project`.
- **`RunFilter.project`** (`runfilter.py`, §29.5): `None` is *any project*, `"none"` is *no project* (`project_id IS NULL`), a project's id is that project (`project_id = ?`). It is one fixed SQL condition with a bound value, like the others, and an unknown value is a `422` while a project id that does not exist is simply an empty list. `GET /api/runs?project=<id>|none` and `GET /api/runs/counts?project=<id>|none` take it, and the counts apply it to every counted filter.
- **The run's payload** gains `project_id` (null or the id); the page has the project's name from its own list.
- **Events.** A change to a project (made, renamed, deleted) sends one **`projects.changed`**; the page asks for `GET /api/projects` again (after a quiet 250 ms, as it does for the counts). A change to a run sends `run.updated` as it does for Keep; deleting a project sends `run.updated` for each run it unfiled.
- **The janitor** (§30.5) expires only runs that are not in a project.

### 32.5 The page

- `HistoryFilter` (§29.5) gets its third field, `project: "any" | "none" | <id>`: **the first filter that holds a value and not a yes-or-no**, so `filterKey`, `filterParams` and `matches` each gain one case and the filter bar one control; the store keeps one view per filter key, so nothing else changes. What the browser remembers gains the project; **an old stored value still reads as it did** (with *any project*), and a project the page no longer knows becomes *any project* (§32.1).
- **Counts follow the project.** `GET /api/runs/counts` is asked with the project chosen, so the numbers beside All, Kept and Deleted are for that project on the tab shown. The numbers inside the Project drop-down are the per-project counts of `GET /api/projects`.
- **The store** holds the list of projects and keeps it current from `projects.changed`. The Project button on a card, the drop-down in the filter bar and the Manage dialog all read it.
- **Undo** belongs to the page that filed the run, as in §29.4; if the run has changed in between, Undo says so instead of overwriting it.

### 32.6 The small choices I made (tell me if you want any changed)

1. **Both tabs share one list of projects** (a project can be *a logo and its jingle*).
2. **The Project button is on the card only** in 1.14, not in the picture viewer, because you file a run you like, and the card is where its Keep button already is. Putting it in the viewer as well is a small addition.
3. **Undo of a filing restores the old state exactly** (including *not kept*), while **Take out of project** on purpose leaves the run kept (§32.3 item 3). The two are different clicks with different promises, and each toast says which.
4. **The numbers beside All, Kept and Deleted follow the project you chose** (§32.5), so they match what is on the screen.
5. **A project's runs are ordered like every history**: newest first, by when the run was made, not when it was filed.
6. **No limit on the number of projects**, and no nesting: a project is one flat folder. Names are 60 characters at most, and *Logo* and *logo* are one project.
7. **Deleting a project keeps its runs** and does not un-keep them; the question says so.
8. **Delete on a filed run is the same question as ever** with one added line naming the project, not a second hurdle: it goes to the bin for 30 days and comes back to the project on Restore.
9. **The drop-down on a card is a small pop-up menu with a text field.** If it needs a library that is not installed yet (the page has one, for dialogs), I would pin it like the others and say so in the pull request. It works with the keyboard (arrow keys, Enter, Esc; focus returns to the Project button) and fits a phone.
10. **A working run can be filed**, and is kept when it finishes.
11. **Not in the address bar**, like the other filters (§29.7 item 8).

### 32.7 Acceptance criteria (continue §30.7; §31 holds 135 to 145 on its branch)

146. Every card has a **Project** button, **Add to project ▾** or the project's name; its drop-down lists the projects A to Z, **New project…**, and on a filed card a tick, the others and **Take out of project**; it works with the keyboard and fits a phone. (1.14)
147. Choosing a project files the run **and keeps it** in one step; a toast says so with **Undo**, and Undo puts the run back as it was (filed or not, kept or not). (1.14)
148. **New project…** asks for a name in the drop-down; **Create** makes the project and files the run in it; an empty name, one over 60 characters and one that matches an existing name ignoring case are refused with a message and nothing is made. (1.14)
149. Moving a run to another project, and taking it out, each show a toast with **Undo**; taking it out leaves the run kept. (1.14)
150. A filed run's **Keep** is pressed and locked with a tooltip that says why; `PATCH {"pinned": false}` on it is `409` `run_in_project`; `{"pinned": true}` still works. (1.14)
151. The daily clean-up never expires a filed run, **including one whose `pinned` flag has been cleared by hand in the database**. (1.14)
152. The filter bar has a **Project** drop-down (**Any project**, **No project**, each project with its count for the tab shown); the choice applies to both tabs, combines with All, Kept and Deleted, and is remembered; a remembered project that no longer exists becomes *Any project* with a toast. (1.14)
153. With a project chosen, the list, *Show older* and the numbers beside All, Kept and Deleted are for that project, and the empty state names the project and the tab. (1.14)
154. **Manage** makes, renames (a taken name is refused) and deletes projects; deleting asks first and says the runs stay kept. (1.14)
155. Deleting a project deletes, un-keeps and moves no run's files; its runs are unfiled and kept, including those in the bin. (1.14)
156. **Delete** on a filed run names the project in its question; in Deleted the card shows the project as a chip and has no Project button; **Restore** puts it back in the project (or unfiled, if the project is gone), kept. (1.14)
157. A run in the bin cannot be filed (`409` `run_in_bin`). (1.14)
158. `GET /api/projects`, `POST /api/projects`, `PATCH` and `DELETE /api/projects/{id}`, `PUT` and `DELETE /api/runs/{id}/project` (with `?keep=false`), and `?project=` on the list and the counts answer as §32.4 says. (1.14)
159. **Schema 5** is made from a 1.7, a 1.12 and a 1.13 database without losing a run; a copy of the old database is made first; a migrated database is the same as a fresh one. (1.14)
160. Every open page follows live: a run filed, moved or taken out, and a project made, renamed or deleted. (1.14)
161. On a **phone** the Project button's drop-down, the filter bar with its two controls, and the Manage dialog fit with no sideways scroll. (1.14)
162. The server's rule and the page's `matches` agree on the shared table of cases, now with a project field in it (§29.5). (1.14)

### 32.8 Not in 1.14

Nested folders; a description, colour or note on a project; **downloading a project** as one zip; a Project button in the picture viewer; sharing or exporting a project; and any change to where the files are on the disk. Each is a small step from here if you want it.

---

## 33. Version 1.15: delete a picture from a run, into the bin (decision #52 DECIDED for what you said; the rest PROPOSED; nothing built)

You said that, with projects, **you also need to delete the pictures you do not like from a run** (§32). I asked four questions and you answered them:

| # | Question | Your answer |
|---|---|---|
| 1 | On which runs can you delete a single picture? | **Any finished run**, kept, filed or not (§33.2). |
| 2 | Where does a deleted picture go? | **Into the bin for 30 days**, like a run (§33.1). |
| 3 | What if it is the run's last picture? | **The run goes to the bin** (§33.2). |
| 4 | A separate *delete the 4K copy* button? | **No**: the 4K and Enlarge copies go with their picture (§33.2). |

This is the bin of §30 taught to hold **a single picture** as well as a whole run. It does not depend on projects (§32) and they do not depend on it; I would build projects first, because they are what you asked for first.

### 33.1 What it looks like

- **In the picture viewer** (the large view that opens when you press a picture): a **Delete picture** button beside Make 4K and Enlarge, on every **result** picture of a finished run. (An edit's **source** pictures are not offered: they are the run's inputs, §21.7. A picture of a run that is still working is not offered.) It asks first: *Delete this picture? (image 2 of 4, seed 1234.) It moves to Deleted and stays there for 30 days; you can restore it from there. Its 4K and Enlarge copies go with it.* If the run is in a project the question adds *The run stays in the project "Logo" with its other pictures.* If it is the **last** picture the question says *This is the last picture of this run, so the whole run moves to Deleted.* instead.
- **Afterwards** the picture is gone from the card and the viewer at once: the viewer shows the next picture (or the previous one, if it was the last), or closes if the run left the history. The remaining pictures **keep their seeds and are numbered again by position** (a card of 4 with the second deleted shows *image 1 of 3, 2 of 3, 3 of 3*). A toast says *Deleted image 2 of "…". It stays in Deleted for 30 days.* with **Undo** (which is **Restore**, §33.3).
- **Music:** a music run with more than one version has a **Delete track** button on each track's row, with the same question, toast and rules (a run's only track is its last picture: the run goes to the bin).
- **The Deleted view** (§30.1) gets a second kind of card: **a run that is in the history but has pictures in the bin** shows one card with the run's prompt (*From a run made on 12 Oct: "a red fox in the snow…"*, and its project's chip), and under it **the deleted pictures**, each with its thumbnail, its seed, *In the bin since 14 Oct. It will be gone for good around 13 Nov, in 29 days.* and **Restore** and **Delete forever** (it asks first). Pressing a picture opens it in the viewer, read-only. A run that is itself in the bin shows as in §30.1 with the pictures it has, and **not** its separately deleted pictures (§33.2 item 5).
- **The number beside Deleted** counts **things in the bin**: each run in the bin counts one, and each deleted picture (or track) of a run that is still in the history counts one. **Empty bin** says so: *Delete 12 runs and 5 pictures for good (…)? This cannot be undone.*

### 33.2 The rules

1. **Any finished run** (done, failed or canceled; your answer 1), whether it is kept, filed in a project or neither. Keep and projects protect the *run* from the clean-up and from accidents; deleting one picture is a deliberate act with a question first.
2. **A deleted picture goes to the bin for `bin_days`** (30, `STUDIO_BIN_DAYS`; your answer 2). Its file, its thumbnail and its 4K and Enlarge copies stay on the disk meanwhile, and go for good together (your answer 4). With the bin turned off (`STUDIO_BIN_DAYS=0`) a deleted picture is deleted for good at once, and the question says so.
3. **The last picture** of a run is not deleted separately: deleting it **moves the run to the bin** as **Delete** on the run does (§30.2), with the question saying so (your answer 3). Its pictures and its prompt and settings are all recoverable from Deleted. So deleting pictures never leaves a run with none of its pictures in the history (a run that failed, and so never had any, is another matter).
4. **A run that is working, or in the bin, cannot have a picture deleted** (`409` `run_not_finished` / `run_in_bin`), and a picture that Make 4K or Enlarge is working on, or has waiting, is refused with `409` `image_busy` (the copy being written would be left behind).
5. **A picture is in the bin separately only while its run is in the history.** If the run goes to the bin, the pictures deleted before it stay marked with their own clocks and are not listed (the run's card is); if the run is restored they appear again as deleted pictures, and if their time runs out meanwhile the clean-up deletes them for good. When a run is deleted for good, everything of it goes, deleted pictures included.
6. **Restore** puts a picture back into its run **at its own place** (pictures keep their order). It needs the run to be in the history (`409` `run_in_bin` otherwise: restore the run first).
7. **The clean-up** (§30.5) deletes for good the pictures whose time in the bin is over, as it does runs, wherever their run is; **Empty bin** deletes all of them with the runs in the bin; **Delete forever** on a deleted picture deletes just that picture.
8. **A picture's copies are made by "Make 4K" and "Enlarge" next to it** (`<n>-4k.png`, `<n>-4k-enlarged.png`, §27.3, §28.2); there is no separate way to delete just a copy (your answer 4).

### 33.3 The server

- **Schema 6** adds `deleted_at` to `images` and to `tracks` (null, or when the picture went to the bin); a copy of the database is made first (`studio.sqlite.before-schema-6`), and the migration is tested on a 1.7, a 1.12, a 1.13 and a 1.14 database.
- **Routes.**
  - `POST /api/images/{id}/bin` → `200` `{"moved": "picture" | "run", "run": <payload>}`; `404`; `409` `run_not_finished`, `run_in_bin`, `image_busy`, `not_a_result` (an edit's source), `bin_off`.
  - `POST /api/images/{id}/restore` → `200` with the run; `404`; `409` `not_in_bin`, `run_in_bin`.
  - `DELETE /api/images/{id}` → `204`: **for good**, in or out of the bin, as `DELETE /api/runs/{id}` is for a run. If it is the last picture the run is deleted for good too. The same three routes exist for tracks under `/api/tracks/{id}`.
  - `DELETE /api/bin` (Empty bin, §30.5) also deletes the deleted pictures and tracks, and answers `{"deleted": <runs>, "pictures": <pictures and tracks of runs that stay>}`.
- **The run's payload.** `images` (and `tracks`) are the pictures that are **not** in the bin, so every existing use (the card, the viewer, the numbering) is right without change. New: `binned_images` (and `binned_tracks`), each picture as before plus `deleted_at` and `purge_at`; **empty for a run that is in the bin** (§33.2 item 5).
- **`RunFilter.deleted`** (§30.5): `True` now means *the run is in the bin, **or** it has a picture or track in the bin*; `False` (the history) is unchanged. `matches` has the same rule from the payload (`deleted_at` set, or a non-empty `binned_images` or `binned_tracks`). **The counts** for Deleted become a count of **things** (§33.1), so it is one more function beside the generic one, tested on the shared table (§29.5).
- **Events.** A picture moved to the bin, restored or deleted sends `run.updated` for its run; a run that moves instead sends what §30.5 says.
- **Files.** The storage layer gets one function that removes a picture's file, thumbnail and 4K and Enlarge copies together (the run's folder is not removed).

### 33.4 The small choices I made (tell me if you want any changed)

1. **Delete picture is in the viewer, and on a music track's row, not on the picture thumbnails of a card**: you look at a picture before you decide it is bad, and a button on every thumbnail is a button pressed by accident.
2. **Deleted pictures are one card per run** in the Deleted view, not one card per picture, so four pictures deleted from one run do not make four cards.
3. **Pictures are numbered again by position** after one is deleted, and keep their seeds (the seed is what names a picture).
4. **A source picture of an edit cannot be deleted by itself.** It belongs to its run and goes with it.
5. **The Deleted view is ordered by when the run was made**, as in §30.6 item 7, so a deleted picture sits where its run is.
6. **Delete forever on a deleted picture asks first**, as it does for a run.
7. **A run's `num_images`** (the number you asked for) is left as it was; the card counts the pictures that are there.
8. **The counts treat a run in the bin and a deleted picture alike**, one each, because both are things you can restore.

### 33.5 Acceptance criteria (continue §32.7)

163. The viewer has **Delete picture** on every result picture of a finished run (not on an edit's sources, not on a working run); the question names the picture, the bin and its days, the project if there is one, and says when it is the last picture. (1.15)
164. A deleted picture leaves the card and the viewer at once; the others keep their seeds and are numbered again; the viewer moves on or closes; a toast with **Undo** (Restore) is shown. (1.15)
165. Deleting the last picture moves the whole run to the bin, and Undo restores the run. (1.15)
166. A deleted picture's file, thumbnail and 4K and Enlarge copies stay on the disk while it is in the bin, and are deleted for good together when its days are over, on **Delete forever**, on **Empty bin**, or when its run is deleted for good. (1.15)
167. The Deleted view shows, for a run that is in the history, one card with its deleted pictures (since, until, **Restore**, **Delete forever**); a run that is in the bin shows its remaining pictures and not its earlier deleted ones. (1.15)
168. **Restore** puts a picture back at its place in its run; it is refused while the run is in the bin. (1.15)
169. The number beside **Deleted** and the question of **Empty bin** count runs and pictures (and tracks). (1.15)
170. `POST /api/images/{id}/bin`, `POST /api/images/{id}/restore`, `DELETE /api/images/{id}` (and the three for tracks), `DELETE /api/bin` with `pictures`, and `binned_images` and `binned_tracks` in the payload answer as §33.3 says; `images` and `tracks` leave out what is in the bin. (1.15)
171. With `STUDIO_BIN_DAYS=0` a deleted picture is deleted for good at once and the question says so. (1.15)
172. A picture that Make 4K or Enlarge is working on or waiting for cannot be deleted (`409` `image_busy`). (1.15)
173. **Schema 6** is made from a 1.7, a 1.12, a 1.13 and a 1.14 database without losing a run or a picture; a copy of the old database is made first; a migrated database is the same as a fresh one. (1.15)
174. Every open page follows live: a picture deleted, restored or deleted for good, and a run moved by deleting its last picture. (1.15)
175. On a **phone** the Delete picture button and question, a deleted-pictures card and the dialogs fit with no sideways scroll. (1.15)
176. The server's rule and the page's `matches` agree on the shared table of cases, now with a run that has pictures in the bin (§29.5). (1.15)

### 33.6 Not in 1.15

A way to delete just a picture's 4K or Enlarge copy (your answer 4); deleting an edit's source pictures; deleting several pictures at once (select and delete); deleting a picture from the card without opening the viewer. Each is a small step from here if you want it.

---

## 34. Version 1.16: 16:9 is the default shape of a picture (decision #53 DECIDED for what you said; the rest PROPOSED; nothing built)

You asked that **16:9 be the default image aspect ratio for any generation**. Today the default is **1:1, 2048×2048** (decision #20, §6): it is what Options starts with, what *Reset* goes back to, and what the server makes when a request names no size. I asked three questions and you answered them:

| # | Question | Your answer |
|---|---|---|
| 1 | A browser that has used the studio has already saved its size (1:1 unless you changed it), and a saved choice beats the default. What should 1.16 do about that? | **Move a saved 1:1 to 16:9, once** (§34.2). |
| 2 | Edit's size is *Auto* (the shape of your last source picture). Default it to 16:9 too? | **No: leave Edit on Auto** (§34.3). |
| 3 | The API (a request with no size) and the command-line script `scripts/qwen_image.py` have the same default. Change them too? | **Yes, everywhere** (§34.4). |

This is a change to a default, not a new feature: no database change, no new control, no new route.

### 34.1 What changes

- **The default size of Generate is 16:9, 2752×1536** (the model card's 16:9 preset, the one Options already offers). It is about **4.23 megapixels**, against the 4.19 of 2048×2048, so a picture costs the same time and memory as before and stays inside the 4.5 MP limit (§6).
- **A browser with nothing saved**, a private window, and the **Reset** button in Options all give 16:9 for Generate. The **Custom** width and height start at 2752×1536 (they start at the default size).
- **The scale picker (100%, 75%, 50%, 25%) on the prompt bar, and Draft,** work on whatever size is selected, as ever; at 100% a new picture is 2752×1536.
- **Any other choice is untouched**: another preset, Custom, the scale, the steps, the seed, the number of pictures. Only the one choice that *was the old default* moves (§34.2).

### 34.2 The one-time move of a saved 1:1 (your answer 1)

Every browser that has used the studio has saved a size, and unless it moved off the default that size is **1:1**. A plain change of the default would therefore change nothing on your own page. So **the first time a browser opens the studio after 1.16**:

- if its saved Generate size is **exactly the 1:1 preset**, it becomes **16:9**, and a **one-time note** says so: *The default size is now 16:9, and your saved 1:1 was changed to it. Choose 1:1 in Options to go back.*;
- any other saved size (another preset, Custom, even a Custom of 2048×2048) is **left as it is**;
- the saved **Edit** size is not touched (§34.3).

**It happens once.** The page keeps a second small saved value that says *this browser has been moved*, and writes it on that first load **whether or not anything was moved**; so a 1:1 you choose afterwards stays 1:1, and a browser that starts empty never sees the note. With the browser's storage blocked (a private window) there is nothing saved to move: the page works in memory and starts at 16:9.

**The catch, as I told you:** the page cannot tell *a 1:1 you chose on purpose* from *the old default*, so someone who deliberately kept 1:1 is moved once, and told. One click in Options gives it back.

### 34.3 What does not change (your answer 2, and the rest)

- **Edit mode's size stays Auto** (decision #28, §21.4): an edit takes the shape of your last source picture at about 1 MP unless you pick a size. A saved Edit size, and Reset for Edit, are as before. (Choosing 16:9 for an edit is one click in Options, as now.)
- **Runs that already exist** keep their size. **Reuse** and **Retry** restore *that run's own* size, not the new default; **Regenerate larger**, **Make 4K** and **Enlarge** are unaffected.
- **An explicit size is honoured as ever**: a request that names a width and height, or a preset, gets that. The limits (§6, 256 to 4096 a side, multiples of 32, at most 4.5 MP) are unchanged.
- **Music** has no size and is unaffected.

### 34.4 Where the default lives (your answer 3)

One value, so that every way of making a picture agrees:

- **The server.** `DEFAULT_ASPECT` and `DEFAULT_SIZE` in `backend/studio/presets.py` become `"16:9"` and 2752×1536. They feed `GET /api/capabilities` (`defaults.aspect_ratio`, `width`, `height`, which is how the page learns its default) and the rule in `runspec.py` that gives a **Generate request with no size** the default size. An **Edit request with no size** is still sized by the pipeline (§21.4).
- **The page** reads its default from the capabilities, as it does now; it needs no number of its own. What changes in the page is the one-time move of §34.2.
- **The script `scripts/qwen_image.py`**: `generate` with no size makes **2752×1536**, and its help text, its example comment and the hint in its out-of-memory message (*"--aspect-ratio 1:1 is 2048x2048"*) say so. `--aspect-ratio 1:1` still makes a square.
- **A script of yours that calls the API without a size** will now get a landscape picture where it got a square one. That is the point of your answer, and it is the one place the change can surprise a caller.

### 34.5 The small choices I made (tell me if you want any changed)

1. **The shape is the model card's 16:9 preset, 2752×1536**, not 1920×1080: the pipeline works on a grid of 32 pixels and 1080 is not on it (the studio refuses such a size, §6).
2. **It is a built-in default, not a setting.** There is no `STUDIO_DEFAULT_ASPECT`, as there was none for 1:1. If you want to change it without a release, a setting is a small addition.
3. **Only the shape moves.** A saved scale (say 50%), steps or seed are left alone, so a browser that was at 1:1 and 50% becomes 16:9 and 50% (1376×768).
4. **The note about the move is a plain toast**, shown once, and nothing else announces it.
5. **The Custom fields start at the new default size** for a browser with nothing saved; a saved Custom size is never replaced.
6. **Tests that meant "a square" will say 1:1.** A good many tests rely on the old default without saying so; when I build this they name the square they mean instead of leaning on the default, so that the next change of default does not touch them. This is most of the work of the release.
7. **The text that says the default is 1:1** (decision #20, the Size row of the Options table in §5, the README, the Spark test checklists) is updated when this is built, not now, because today it is still true.

### 34.6 Acceptance criteria (continue §33.5)

177. A browser with nothing saved starts Generate at **16:9** (Options shows the 16:9 preset selected, the size is 2752×1536, and Custom starts at 2752×1536); **Reset** gives the same for Generate. (1.16)
178. On the first load after 1.16, a saved Generate size of exactly the **1:1 preset** becomes 16:9 and a one-time note says so; any other saved size, including a Custom of 2048×2048, and the saved Edit size are left as they were. (1.16)
179. The move **happens once**: after it, choosing 1:1 and reloading keeps 1:1 with no note; a browser that starts empty gets no note; with storage blocked the page works and starts at 16:9. (1.16)
180. **Edit's** default size is still **Auto**, and Reset leaves it there. (1.16)
181. `GET /api/capabilities` gives `defaults.aspect_ratio` `"16:9"`, `width` 2752 and `height` 1536; a **Generate request with no size** makes a 2752×1536 picture (the run, the file and its download name say so); a request that names a size, and an **Edit request with no size**, are unchanged. (1.16)
182. `scripts/qwen_image.py generate` with no size makes 2752×1536 and its help says so; `--aspect-ratio 1:1` still makes 2048×2048. (1.16)
183. Runs made before 1.16 are unchanged, and **Reuse** and **Retry** restore their own size, not the new default. (1.16)
184. A 16:9 picture at the default size needs no more memory than the old square one did: **checked on the Spark** with one run at the default size, writing down the time and the memory (`SPARK_TEST.md`, added when this is built). (1.16)
