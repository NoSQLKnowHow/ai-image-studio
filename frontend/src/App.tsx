import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { Header } from "./components/Header";
import { MusicPanel } from "./components/MusicPanel";
import { Tabs, panelId, tabId } from "./components/Tabs";
import { ConfirmCancel, ConfirmDelete, Lightbox, cardReuseButton, type ViewerNotice } from "./components/Dialogs";
import { ConnectionBanner, QueueBar, Toasts } from "./components/Feedback";
import { EmptyHistory, FilterBar, focusFilterBar } from "./components/FilterBar";
import { OptionsDrawer } from "./components/OptionsDrawer";
import { PromptBar } from "./components/PromptBar";
import { RunCard } from "./components/RunCard";
import { UpscalePicture } from "./components/UpscalePicture";
import { Tray } from "./components/Tray";
import { enlargeFailedText, enlargedText, fourKFailedText, fourKMadeText, saveBlob, upscaleFailedText, upscaledText } from "./fourk";
import { leftTheViewText, unkeptText } from "./format";
import { NO_FILTER, filterKey, isDefault, matches, readFilter, saveFilter, visibleRuns, working, type HistoryFilter } from "./history";
import { copyText, useNow, useToasts } from "./hooks";
import type { ModelAction } from "./model";
import { readTab, saveTab, type TabId } from "./music";
import {
  PROMPT_KEY,
  browserStore,
  buildEditRequest,
  buildRequest,
  defaultOptions,
  draftRequest,
  isAutoSize,
  largerRequest,
  loadOptions,
  optionsFromRun,
  optionsProblem,
  retryRequest,
  saveOptions,
  type Options,
  type Scale,
} from "./options";
import { initialState, reducer } from "./store";
import { editCost, followedPosition, inputRefs, insertReference, shapeFromForRequest, submitBlock, type KnownImage } from "./tray";
import { isImageRun, isMusicRun, type CreateRunBody, type FourKTarget, type ImageInfo, type ImageRun, type MusicRun, type Run } from "./types";
import { useEventStream } from "./useEvents";
import { useTray } from "./useTray";
import { viewerItems, viewerKnown } from "./viewer";

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const store = useMemo(browserStore, []);
  const [options, setOptions] = useState<Options | null>(null);
  const [prompt, setPrompt] = useState(() => store.get(PROMPT_KEY) ?? "");
  const [startupError, setStartupError] = useState<string | null>(null);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formProblem, setFormProblem] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<{ runId: string; index: number; notice?: ViewerNotice } | null>(null);
  const lightboxOpen = useRef(false); // read when a request made from the viewer is answered, which may be after it closed
  useEffect(() => {
    lightboxOpen.current = lightbox !== null;
  }, [lightbox]);
  const [pendingDelete, setPendingDelete] = useState<Run | null>(null);
  const [pendingCancel, setPendingCancel] = useState<Run | null>(null);
  const inFlight = useRef(new Set<string>()); // "cancel:<id>" / "keep:<id>": one request per control at a time
  const [making4k, setMaking4k] = useState<ReadonlySet<string>>(new Set()); // images whose 4K copy is being made (DESIGN.md §27)
  const [enlarging, setEnlarging] = useState<ReadonlySet<string>>(new Set()); // images being enlarged with the upscaler model (DESIGN.md §28)
  const [upscaling, setUpscaling] = useState(false); // a picture from the computer is being upscaled (DESIGN.md §27.9)
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [modelBusy, setModelBusy] = useState(false); // a Load or Unload request is on its way (DESIGN.md §25)
  const [tab, setTab] = useState<TabId>(() => readTab(store)); // Images or Music (DESIGN.md §26.1), remembered
  const [filter, setFilter] = useState<HistoryFilter>(() => readFilter(store)); // All or Kept, for both tabs (DESIGN.md §29), remembered
  const [filterProblem, setFilterProblem] = useState<string | null>(null); // why the filtered list could not be loaded
  const [loadOlderFailed, setLoadOlderFailed] = useState(false);
  const { toasts, push, dismiss } = useToasts();
  const now = useNow(30_000);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const caretKnown = useRef(false); // the prompt has been focused at least once, so its caret means something
  const { caps, status } = state;
  const tray = useTray(caps);
  const version = status?.version;
  const key = filterKey(filter);
  const view = state.views[key];
  const runsReady = !!view?.ready;
  const more = !!view?.nextBefore;
  const visible = useMemo(() => visibleRuns(state.runs, state.order, view, filter), [state.runs, state.order, view, filter]);
  // Each tab shows only its own runs (DESIGN.md §26.1); the queue they share is on both.
  const imageRuns = useMemo(() => visible.filter(isImageRun), [visible]);
  const musicRuns = useMemo(() => visible.filter(isMusicRun), [visible]);
  const tabKind = tab === "music" ? "music" : "image";
  const tabRuns = tab === "music" ? musicRuns : imageRuns;
  const countName = filter.kept === true ? "kept" : filter.kept === null ? "all" : null;
  const knownEmpty = !!state.counts && countName !== null && state.counts[tabKind][countName] === 0; // the server says this tab has none
  const lookingForMore = runsReady && tabRuns.length === 0 && more && !knownEmpty && !loadOlderFailed; // none on this tab yet: the next page may have some
  const waitingList = status?.queue.enlarge_waiting;
  const enlargeWaiting = useMemo<ReadonlySet<string>>(() => new Set(waitingList ?? []), [waitingList]); // Enlarge requests queued behind a picture (§28.3)

  // The tab says which build the server is running, too.
  useEffect(() => {
    document.title = version ? `AI Image Studio v${version}` : "AI Image Studio";
  }, [version]);

  const start = useCallback(async () => {
    setStartupError(null);
    try {
      const nextCaps = await api.capabilities();
      dispatch({ type: "caps", caps: nextCaps });
      const loaded = loadOptions(nextCaps, store);
      setOptions(loaded.options);
      if (loaded.repaired) push("info", "Some saved options were out of date and have been reset.");
      if (!store.available) push("info", "This browser is blocking storage, so your options won't be remembered.");
    } catch (error) {
      setStartupError(error instanceof Error ? error.message : String(error));
    }
  }, [store, push]);

  useEffect(() => {
    void start();
  }, [start]);

  useEventStream(dispatch, {
    onCapabilitiesChanged: () => void api.capabilities().then((c) => dispatch({ type: "caps", caps: c })).catch(() => undefined),
  });

  useEffect(() => {
    if (options) saveOptions(options, store);
  }, [options, store]);
  // A picture dropped anywhere but the prompt card would make the browser open it and leave the page.
  useEffect(() => {
    const stop = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => {
      window.removeEventListener("dragover", stop);
      window.removeEventListener("drop", stop);
    };
  }, []);
  useEffect(() => {
    store.set(PROMPT_KEY, prompt);
  }, [prompt, store]);

  const send = useCallback(async (build: (prompt: string, options: Options, caps: NonNullable<typeof state.caps>) => CreateRunBody) => {
    if (!caps || !options || !prompt.trim() || submitting) return null;
    const problem = optionsProblem(options, caps);
    if (problem) {
      setFormProblem(`Check the options: ${problem}`);
      setOptionsOpen(true);
      return null;
    }
    setSubmitting(true);
    setFormProblem(null);
    try {
      const run = await api.createRun(build(prompt, options, caps));
      dispatch({ type: "runUpsert", run });
      return run;
    } catch (error) {
      const err = error as ApiError;
      if (err.status === 422) setFormProblem(err.message);
      else push("error", err.status === 429 ? err.message : `Couldn't start the run: ${err.message}`);
      return null;
    } finally {
      setSubmitting(false);
    }
  }, [caps, options, prompt, submitting, push]);

  // Generate sends the size and scale; Edit sends the tray's images in order. Once an edit is sent the server owns
  // copies of them, so the tray now points at those and stays usable for the next try.
  const submit = async () => {
    if (!options) return;
    if (options.mode !== "edit") {
      await send(buildRequest);
      return;
    }
    if (submitBlock(tray.items)) return;
    const inputs = inputRefs(tray.items);
    const shapeFrom = shapeFromForRequest(tray.items, tray.shapeKey);
    const run = await send((text, chosen, capabilities) => buildEditRequest(text, chosen, capabilities, inputs, shapeFrom));
    if (run) tray.adoptRun(run);
  };
  const submitDraft = useCallback(() => send(draftRequest), [send]);

  // Load or unload the model from the header. The new state arrives over the event stream like every other change, so
  // the answer is not used to move the pill: it could overtake a later event and make the pill go backwards.
  const changeModel = useCallback(async ({ kind, model }: ModelAction) => {
    setModelBusy(true);
    try {
      await (kind === "load" ? api.loadModel(model) : api.unloadModel(model));
      return true;
    } catch (error) {
      push("error", `Couldn't ${kind} the ${model === "music" ? "music model" : "model"}: ${(error as ApiError).message}`);
      return false;
    } finally {
      setModelBusy(false);
    }
  }, [push]);

  // A "check the options" message is stale as soon as the options change.
  const changeOptions = (next: Options) => {
    setOptions(next);
    setFormProblem(null);
  };

  const changeTab = (next: TabId) => {
    setTab(next);
    saveTab(next, store);
  };

  const reuse = (run: ImageRun) => {
    if (!caps || !options) return;
    setPrompt(run.prompt);
    setOptions(optionsFromRun(run, caps, options));
    setFormProblem(null);
    let message = run.options.draft
      ? "Loaded the prompt. Your size, steps and seed are unchanged, so Generate makes the full-size image."
      : `Loaded the prompt and options. Seed locked to ${run.options.seed}.`;
    if (run.mode === "edit" && caps.modes.includes("edit")) {
      // An edit comes back with its images, in order, copied into a fresh tray (they stay with the original run too).
      const sources = viewerItems(run).filter((item) => item.kind === "source").map((item) => viewerKnown(run, item));
      const outcome = tray.replaceWith(sources);
      tray.setShapeKey(run.options.shape_from ? outcome.keys[run.options.shape_from - 1] ?? null : null);
      message = `Loaded the prompt, options and ${outcome.added} ${outcome.added === 1 ? "image" : "images"}. Seed locked to ${run.options.seed}.`
        + (outcome.skipped ? ` ${outcome.skipped} did not fit: one edit takes at most ${tray.cap}.` : "");
    }
    push("info", message);
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    promptRef.current?.focus({ preventScroll: true });
  };

  // From a card the answer is a toast; from the viewer (`image` given: that one image, DESIGN.md §24) it is a note
  // inside the viewer, because the page behind a modal, toasts included, is hidden from screen readers.
  const regenerateLarger = (run: ImageRun, image?: ImageInfo) =>
    once(`larger:${run.id}:${image?.id ?? "run"}`, async () => {
      const body = largerRequest(run, image);
      if (!body) return;
      const tell = (kind: "info" | "error", text: string) =>
        image && lightboxOpen.current ? setLightbox((current) => (current ? { ...current, notice: { kind, text } } : current)) : push(kind, text);
      try {
        dispatch({ type: "runUpsert", run: await api.createRun(body) });
        tell("info", `Queued ${image ? "this image " : ""}at ${body.options.width}×${body.options.height}. A bigger size is a new picture, so expect it to differ from this one.`);
      } catch (error) {
        const err = error as ApiError;
        tell("error", err.status === 429 ? err.message : `Couldn't regenerate: ${err.message}`);
      }
    });

  // Make the 4K copy of one result image (DESIGN.md §27). The answer is the run, with the image's `four_k` filled in. As for
  // Regenerate larger, the answer is a toast on a card and a note inside the viewer when it is open when the answer comes.
  const tellAboutImage = (kind: "info" | "error", text: string) =>
    lightboxOpen.current ? setLightbox((current) => (current ? { ...current, notice: { kind, text } } : current)) : push(kind, text);

  const make4k = (image: FourKTarget) =>
    once(`4k:${image.id}`, async () => {
      const tell = tellAboutImage;
      setMaking4k((current) => new Set(current).add(image.id));
      try {
        const run = await api.makeFourK(image.id);
        dispatch({ type: "runUpsert", run });
        tell("info", fourKMadeText([...run.images, ...run.inputs].find((candidate) => candidate.id === image.id) ?? image));
      } catch (error) {
        const err = error as ApiError;
        tell("error", fourKFailedText(err.status, err.message));
      } finally {
        setMaking4k((current) => {
          const next = new Set(current);
          next.delete(image.id);
          return next;
        });
      }
    });

  // Enlarge one image to the 4K frame with the upscaler model (DESIGN.md §28). It can take a minute or more, so the button says so
  // meanwhile; the answer is the run, with the image's `four_k` filled in (method `model`), told as Make 4K's is.
  const enlargeImage = (image: FourKTarget) =>
    once(`enlarge:${image.id}`, async () => {
      setEnlarging((current) => new Set(current).add(image.id));
      try {
        const run = await api.enlargeImage(image.id);
        dispatch({ type: "runUpsert", run });
        tellAboutImage("info", enlargedText([...run.images, ...run.inputs].find((candidate) => candidate.id === image.id) ?? image));
      } catch (error) {
        const err = error as ApiError;
        tellAboutImage("error", enlargeFailedText(err.status, err.message, err.hint));
      } finally {
        setEnlarging((current) => {
          const next = new Set(current);
          next.delete(image.id);
          return next;
        });
      }
    });

  // A picture from this computer: sent, made 4K and saved as a download. Nothing is added to the history (DESIGN.md §27.9).
  const upscalePicture = (file: File) =>
    once("upscale-picture", async () => {
      setUpscaling(true);
      try {
        const picture = await api.upscalePicture(file);
        saveBlob(picture.blob, picture.filename);
        push("success", upscaledText(file.name, picture));
      } catch (error) {
        push("error", upscaleFailedText(file.name, (error as ApiError).message));
      } finally {
        setUpscaling(false);
      }
    });

  /** Edit this: add a picture the server already has to the tray, and switch to Edit. From the viewer a refusal is
   *  shown inside it (§24.2). Returns whether the picture was added. */
  const editThis = (image: KnownImage, fromViewer: boolean): boolean => {
    if (!caps?.modes.includes("edit") || !options) return false;
    if (tray.room === 0) {
      const text = `One edit takes at most ${tray.cap} images. Remove one from the tray first.`;
      if (fromViewer) setLightbox((current) => (current ? { ...current, notice: { kind: "error", text } } : current));
      else push("error", text);
      return false;
    }
    const n = tray.items.length + 1;
    tray.addKnown([image]);
    setOptions({ ...options, mode: "edit" });
    setFormProblem(null);
    setLightbox(null);
    push("info", `Added as image ${n}. Describe the change, or add more pictures.`);
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    promptRef.current?.focus({ preventScroll: true });
    return true;
  };

  /** Put "image N" in the prompt at the caret (at the end if the prompt has never been focused). */
  const insertImageRef = (position: number) => {
    const box = promptRef.current;
    const from = caretKnown.current && box ? box.selectionStart : prompt.length;
    const to = caretKnown.current && box ? box.selectionEnd : prompt.length;
    const result = insertReference(prompt, from, to, position, caps?.limits.prompt_chars);
    if (!result) {
      push("error", "The prompt is too long to add that.");
      return;
    }
    setPrompt(result.text);
    setFormProblem(null);
    requestAnimationFrame(() => {
      box?.focus({ preventScroll: true });
      box?.setSelectionRange(result.caret, result.caret);
    });
  };

  /** Pictures dropped on the prompt card or pasted into the prompt. Generate has no use for them. */
  const handleFiles = (files: File[]) => {
    if (!caps || !options || files.length === 0) return;
    if (options.mode !== "edit") {
      push("info", caps.modes.includes("edit") ? "Switch to Edit to work with pictures." : "Editing isn't available.");
      return;
    }
    tray.addFiles(files);
  };

  const applyStarter = () => {
    if (!options || prompt.trim()) return;
    setPrompt("Extract the main subject of image 1.");
    changeOptions({ ...options, transparent: true });
    push("info", "Prompt filled in, and Transparent is on.");
  };

  const retry = async (run: ImageRun) => {
    try {
      dispatch({ type: "runUpsert", run: await api.createRun(retryRequest(run)) });
    } catch (error) {
      push("error", `Couldn't retry: ${(error as Error).message}`);
    }
  };

  const copy = async (run: Run) => {
    const what = isMusicRun(run) ? "description" : "prompt";
    const ok = await copyText(run.prompt);
    push(ok ? "success" : "error", ok ? `${what[0].toUpperCase()}${what.slice(1)} copied.` : `Couldn't copy. Select the ${what} text instead.`);
  };

  /** Run `task` unless the same one is already under way for this run (a double click, or Enter held down). */
  const once = async (key: string, task: () => Promise<void>) => {
    if (inFlight.current.has(key)) return;
    inFlight.current.add(key);
    try {
      await task();
    } finally {
      inFlight.current.delete(key);
    }
  };

  // A queued run is taken out of the queue at once; a running one asks first, because it stops the work.
  const requestCancel = (run: Run) => {
    if (run.status === "running") {
      setPendingCancel(run);
      return;
    }
    // The Cancel button is about to disappear; keep a keyboard user's place on the same card.
    cardReuseButton(run.id)?.focus({ preventScroll: true });
    void cancel(run);
  };

  const cancel = (run: Run) =>
    once(`cancel:${run.id}`, async () => {
      try {
        dispatch({ type: "runUpsert", run: await api.cancelRun(run.id) });
      } catch (error) {
        const err = error as ApiError;
        if (err.status === 409) push("info", "That run had already finished.");
        else if (err.status === 404) push("info", "That run no longer exists.");
        else push("error", `Couldn't cancel: ${err.message}`);
      }
    });

  const confirmCancel = () => {
    const run = pendingCancel;
    setPendingCancel(null);
    if (run) void cancel(run);
  };

  // Keep or stop keeping. In the Kept view, stopping takes the card out of the list, so a toast says so, says when the run will be
  // deleted, and offers Undo (DESIGN.md §29.4); and keyboard focus, which was on the card, moves to the filter bar.
  const toggleKeep = (run: Run) =>
    once(`keep:${run.id}`, async () => {
      const leaves = run.pinned && filter.kept === true && !working(run);
      try {
        const updated = await api.keepRun(run.id, !run.pinned);
        dispatch({ type: "runUpsert", run: updated });
        if (leaves && !updated.pinned) {
          refocus.current = true; // see the effect below: once the list has been drawn without the card
          push("info", unkeptText(updated), { label: "Undo", run: () => void keepAgain(updated) });
        }
      } catch (error) {
        const err = error as ApiError;
        push("error", err.status === 404 ? "That run no longer exists." : `Couldn't ${run.pinned ? "stop keeping" : "keep"} it: ${err.message}`);
      }
    });

  const keepAgain = (run: Run) =>
    once(`keep:${run.id}`, async () => {
      try {
        dispatch({ type: "runUpsert", run: await api.keepRun(run.id, true) });
      } catch (error) {
        const err = error as ApiError;
        push(err.status === 404 ? "info" : "error", err.status === 404 ? "That run no longer exists, so it cannot be kept again." : `Couldn't keep it again: ${err.message}`);
      }
    });

  const confirmDelete = async () => {
    const run = pendingDelete;
    setPendingDelete(null);
    if (!run) return;
    try {
      await api.deleteRun(run.id);
      dispatch({ type: "runDeleted", id: run.id });
    } catch (error) {
      push("error", `Couldn't delete: ${(error as Error).message}`);
    }
  };

  const loadOlder = async () => {
    if (!view?.nextBefore) return;
    setLoadingOlder(true);
    try {
      dispatch({ type: "runsLoaded", page: await api.listRuns(view.nextBefore, filter), append: true, filter });
    } catch (error) {
      setLoadOlderFailed(true);
      push("error", `Couldn't load older runs: ${(error as Error).message}`);
    } finally {
      setLoadingOlder(false);
    }
  };

  const changeFilter = (next: HistoryFilter) => {
    setFilter(next);
    saveFilter(next, store);
  };

  // The stream's own first page is unfiltered, so the first page of a filtered list is read from the server: when the filter is chosen,
  // and again whenever the connection comes back, so that a run kept or un-kept meanwhile is not missed (DESIGN.md §29.5).
  const loadFirstPage = useCallback(async (which: HistoryFilter) => {
    setFilterProblem(null);
    try {
      dispatch({ type: "runsLoaded", page: await api.listRuns(null, which), append: false, filter: which });
    } catch (error) {
      setFilterProblem((error as Error).message);
    }
  }, []);
  useEffect(() => {
    if (isDefault(filter) || state.connection !== "open") return;
    void loadFirstPage(filter);
  }, [key, state.connection, loadFirstPage]);

  // The counts on the filter bar are the server's; asked for again, after a short pause, whenever a run is made, deleted, kept or un-kept.
  useEffect(() => {
    if (state.connection !== "open") return;
    const timer = setTimeout(() => {
      void api.runCounts().then((counts) => dispatch({ type: "counts", counts })).catch(() => undefined);
    }, 250);
    return () => clearTimeout(timer);
  }, [state.connection, state.countsStale]);

  // A tab with nothing to show yet, while older runs are still to be read: read them, rather than say there is nothing (§29.1).
  useEffect(() => {
    setLoadOlderFailed(false);
  }, [key, tab]);
  useEffect(() => {
    if (lookingForMore && !loadingOlder) void loadOlder();
  }, [lookingForMore, loadingOlder, view?.nextBefore]);

  // The card that had keyboard focus has just left the Kept view: when the list has been drawn without it, focus is on the page and
  // goes to the filter bar instead (DESIGN.md §29.4). Done here, after the draw, because the card is still there until then.
  const refocus = useRef(false);
  useEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    if (!document.activeElement || document.activeElement === document.body) focusFilterBar();
  }, [visible]);

  // A run that is in a filtered view only while it works leaves it when it is done, unless it was kept; the page says so (§29.3).
  const watched = useRef(new Set<string>());
  useEffect(() => {
    if (isDefault(filter)) {
      watched.current.clear();
      return;
    }
    for (const run of Object.values(state.runs)) if (working(run) && !matches(run, filter)) watched.current.add(run.id);
    for (const id of [...watched.current]) {
      const run = state.runs[id];
      if (!run || (working(run) && matches(run, filter))) {
        watched.current.delete(id); // deleted, or kept while it worked: nothing to say
      } else if (!working(run)) {
        watched.current.delete(id);
        if (!matches(run, filter)) push("info", leftTheViewText(run), { label: "Show all", run: () => changeFilter(NO_FILTER) });
      }
    }
  }, [state.runs, filter]);

  if (startupError) {
    return (
      <main className="app-main startup-error">
        <h1>Can't reach the studio</h1>
        <p>{startupError}</p>
        <button type="button" className="button primary" onClick={() => void start()}>Try again</button>
      </main>
    );
  }

  const found = lightbox ? state.runs[lightbox.runId] : undefined;
  const lightboxRun = found && isImageRun(found) ? found : null;
  const transient = (run: Run) => !isDefault(filter) && working(run) && !matches(run, filter); // in this view only while it works (§29.3)
  const filterBar = <FilterBar filter={filter} keptCount={state.counts?.[tabKind].kept ?? null} onChange={changeFilter} />;
  const problem =
    filterProblem && !isDefault(filter) ? (
      <div className="empty-state" role="alert">
        <p className="empty-title">Couldn't load your kept runs</p>
        <p>{filterProblem}</p>
        <button type="button" className="button small" onClick={() => void loadFirstPage(filter)}>Try again</button>
      </div>
    ) : null;
  const emptyFor = (kind: "image" | "music") =>
    lookingForMore ? <p className="loading" role="status">Looking through your older runs…</p> : <EmptyHistory kind={kind} filter={filter} onShowAll={() => changeFilter(NO_FILTER)} />;
  const musicAvailable = !!caps?.modes.includes("music");
  const canEdit = !!caps?.modes.includes("edit");
  const editing = options?.mode === "edit";
  const cost = caps && options ? editCost(tray.items.length, options.resolution, caps.limits.edit_warn_units) : null;

  return (
    <>
      <a className="skip-link" href="#prompt">Skip to the prompt</a>
      <Header status={status} now={now} busy={modelBusy} tab={tab === "music" ? "music" : "image"} musicAvailable={musicAvailable} onModel={changeModel} />
      <ConnectionBanner connection={state.connection} serverStopping={state.serverStopping} />
      <main className="app-main">
        {!caps || !options ? (
          <p className="loading" role="status">Connecting to the studio…</p>
        ) : (
          <>
            <Tabs tab={tab} onTab={changeTab} busy={{ images: imageRuns.some(working), music: musicRuns.some(working) }} />
            <div className="tab-panel" role="tabpanel" id={panelId("images")} aria-labelledby={tabId("images")} hidden={tab !== "images"}>
            <PromptBar
              ref={promptRef}
              caps={caps}
              options={options}
              prompt={prompt}
              submitting={submitting}
              problem={formProblem}
              tray={
                <Tray
                  items={tray.items}
                  cap={tray.cap}
                  notes={tray.notes}
                  announcement={tray.announcement}
                  showShape={isAutoSize(options)}
                  followed={followedPosition(tray.items, tray.shapeKey)}
                  heavy={cost?.heavy ? `This edit is heavy (${cost.label}): expect a long run, or running out of memory. Use 1K or fewer images.` : null}
                  onAdd={(files) => void tray.addFiles(files)}
                  onRemove={tray.remove}
                  onMoveBy={tray.moveBy}
                  onMoveTo={tray.moveTo}
                  onInsert={insertImageRef}
                  onShape={tray.setShapeKey}
                  onMissing={tray.markMissing}
                  onDismissNote={tray.dismissNote}
                />
              }
              blockReason={editing ? submitBlock(tray.items) : null}
              starterAvailable={!prompt.trim()}
              onFiles={handleFiles}
              onStarter={applyStarter}
              onPromptFocus={() => {
                caretKnown.current = true;
              }}
              onPrompt={(text) => {
                setPrompt(text);
                if (formProblem) setFormProblem(null);
              }}
              onMode={(mode) => setOptions({ ...options, mode })}
              onScale={(scale: Scale) => changeOptions({ ...options, scale })}
              onOpenOptions={() => setOptionsOpen(true)}
              onSubmit={() => void submit()}
              onDraft={() => void submitDraft()}
            />
            <QueueBar status={status} />
            <UpscalePicture busy={upscaling} onPick={(file) => void upscalePicture(file)} />
            {filterBar}
            <section className="timeline" aria-label="Your runs">
              {!runsReady ? (
                problem ?? <p className="loading" role="status">Loading your runs…</p>
              ) : imageRuns.length === 0 ? (
                emptyFor("image")
              ) : (
                imageRuns.map((run) => {
                  const id = run.id;
                  return (
                    <RunCard
                      key={id}
                      run={run}
                      now={now}
                      workerState={status?.worker.state ?? null}
                      canEdit={canEdit}
                      making4k={making4k}
                      enlarging={enlarging}
                      enlargeWaiting={enlargeWaiting}
                      transient={transient(run)}
                      upscaler={caps?.upscaler ?? null}
                      onReuse={() => reuse(run)}
                      onRegenerateLarger={() => void regenerateLarger(run)}
                      onMake4K={(image) => void make4k(image)}
                      onEnlarge={(image) => void enlargeImage(image)}
                      onEditThis={() => {
                        const result = viewerItems(run).find((item) => item.kind === "result");
                        if (result) editThis(viewerKnown(run, result), false);
                      }}
                      onRetry={() => void retry(run)}
                      onCancel={() => requestCancel(run)}
                      onToggleKeep={() => void toggleKeep(run)}
                      onDelete={() => setPendingDelete(run)}
                      onCopy={() => void copy(run)}
                      onOpenImage={(index) => setLightbox({ runId: id, index })}
                    />
                  );
                })
              )}
              {more && (
                <button type="button" className="button load-more" onClick={() => void loadOlder()} disabled={loadingOlder}>
                  {loadingOlder ? "Loading…" : "Load older runs"}
                </button>
              )}
            </section>
            <OptionsDrawer
              open={optionsOpen}
              onOpenChange={setOptionsOpen}
              caps={caps}
              options={options}
              trayCount={tray.items.length}
              hasAlphaInput={tray.items.some((item) => item.hasAlpha)}
              onChange={changeOptions}
              onReset={() => changeOptions(defaultOptions(caps))}
            />
            </div>
            <MusicPanel
              hidden={tab !== "music"}
              caps={caps}
              status={status}
              store={store}
              runs={musicRuns}
              runsReady={runsReady}
              filterBar={filterBar}
              loadProblem={problem}
              empty={emptyFor("music")}
              isTransient={transient}
              more={more}
              loadingOlder={loadingOlder}
              now={now}
              push={push}
              onRun={(run: MusicRun) => dispatch({ type: "runUpsert", run })}
              onLoadOlder={() => void loadOlder()}
              onCancel={requestCancel}
              onToggleKeep={(run) => void toggleKeep(run)}
              onDelete={setPendingDelete}
              onCopy={(run) => void copy(run)}
            />
          </>
        )}
      </main>
      <Lightbox
        run={lightboxRun}
        index={lightbox?.index ?? 0}
        notice={lightbox?.notice ?? null}
        canEdit={canEdit}
        making4k={making4k}
        enlarging={enlarging}
        enlargeWaiting={enlargeWaiting}
        upscaler={caps?.upscaler ?? null}
        onMake4K={(image) => void make4k(image)}
        onEnlarge={(image) => void enlargeImage(image)}
        onEditThis={(image) => editThis(image, true)}
        onIndex={(index) => setLightbox((current) => (current ? { ...current, index, notice: undefined } : current))}
        onRegenerateLarger={(image) => lightboxRun && void regenerateLarger(lightboxRun, image)}
        onClose={() => setLightbox(null)}
      />
      <ConfirmCancel run={pendingCancel} onBack={() => setPendingCancel(null)} onConfirm={confirmCancel} />
      <ConfirmDelete run={pendingDelete} onCancel={() => setPendingDelete(null)} onConfirm={() => void confirmDelete()} />
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </>
  );
}
