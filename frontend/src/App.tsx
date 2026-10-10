import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { Header } from "./components/Header";
import { MusicPanel } from "./components/MusicPanel";
import { Tabs, panelId, tabId } from "./components/Tabs";
import { ConfirmCancel, ConfirmDelete, ConfirmEmptyBin, Lightbox, cardReuseButton, type ViewerNotice } from "./components/Dialogs";
import { ConnectionBanner, QueueBar, Toasts } from "./components/Feedback";
import { EmptyHistory, FilterBar, focusFilterBar } from "./components/FilterBar";
import { OptionsDrawer } from "./components/OptionsDrawer";
import { ProjectsDialog } from "./components/ProjectsDialog";
import type { ProjectControls } from "./components/ProjectMenu";
import { PromptBar } from "./components/PromptBar";
import { RunCard } from "./components/RunCard";
import { UpscalePicture } from "./components/UpscalePicture";
import { Tray } from "./components/Tray";
import { enlargeFailedText, enlargedText, fourKFailedText, fourKMadeText, saveBlob, upscaleFailedText, upscaledText } from "./fourk";
import { binnedText, emptiedText, filedText, leftTheViewText, movedText, restoredText, takenOutText, unkeptText, type ViewScope } from "./format";
import { NO_FILTER, NO_PROJECT, canBin, filterKey, isDefault, matches, readFilter, saveFilter, showsWorking, visibleRuns, watchWorking, withProject, working, type HistoryFilter } from "./history";
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
import { isImageRun, isMusicRun, type CreateRunBody, type FourKTarget, type ImageInfo, type ImageRun, type MusicRun, type Project, type Run } from "./types";
import { useEventStream } from "./useEvents";
import { useTray } from "./useTray";
import { viewerItems, viewerKnown } from "./viewer";

const NO_PROJECTS: Project[] = []; // the list of projects before it has arrived: one array, so nothing re-renders for a new empty one

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
  const [pendingEmptyBin, setPendingEmptyBin] = useState(false); // the "Empty the bin?" question is open (DESIGN.md §30.1)
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
  const [managingProjects, setManagingProjects] = useState(false); // the Manage projects dialog is open (DESIGN.md §32.1)
  const [loadOlderFailed, setLoadOlderFailed] = useState(false);
  const { toasts, push, dismiss } = useToasts();
  const now = useNow(30_000);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const caretKnown = useRef(false); // the prompt has been focused at least once, so its caret means something
  const { caps, status } = state;
  const tray = useTray(caps);
  const version = status?.version;
  // The history is filtered on the server. The store keeps one "view" per filter (which pages have arrived, and where the next starts)
  // over one shared cache of runs; `visible` is what the chosen filter shows, and each tab takes its own kind from it.
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
  // the name the server counts this filter under (null: it has no count)
  const countName = filter.deleted === true && filter.kept === null ? "deleted" : filter.deleted === false ? (filter.kept === true ? "kept" : filter.kept === null ? "all" : null) : null;
  // The counts are asked for within the project that is chosen (DESIGN.md §32.5), so they are only used for the filter they describe: a number
  // that arrived for another project must never decide that this one is empty
  const scopedCounts = state.countsProject === filter.project ? state.counts : null;
  const knownEmpty = !!scopedCounts && countName !== null && scopedCounts[tabKind][countName] === 0; // the server says this tab has none
  // The project folders (DESIGN.md §32), and which kind of filter is on: it decides what a card shown only while it works says (§29.3, §32.1)
  const projects = state.projects ?? NO_PROJECTS;
  const projectNameMax = caps?.limits.project_name_max ?? 60;
  const viewScope: ViewScope = filter.project !== null ? "project" : "kept";
  const nameOfProject = (id: string | null): string | null => (id === null ? null : projects.find((project) => project.id === id)?.name ?? null);
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
      // (A run in a project stays kept, DESIGN.md §32.3, and its card's Keep button is locked, so this is not reached for one. A stale card, one
      // that does not know yet that the run was filed on another page, gets the server's refusal instead: see the catch below.)
      // Stopping to keep a run takes its card out of the Kept view. A run that is still working stays: it is shown while it works.
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
        // filed in a project on another page meanwhile: it stays kept, and the server says how to change that
        if (err.code === "run_in_project") push("info", err.message);
        else push("error", err.status === 404 ? "That run no longer exists." : `Couldn't ${run.pinned ? "stop keeping" : "keep"} it: ${err.message}`);
      }
    });

  // The Undo on the "No longer kept" toast: keep the run again. It comes back into the view by itself, since the cache still holds it.
  const keepAgain = (run: Run) =>
    once(`keep:${run.id}`, async () => {
      try {
        dispatch({ type: "runUpsert", run: await api.keepRun(run.id, true) });
      } catch (error) {
        const err = error as ApiError;
        push(err.status === 404 ? "info" : "error", err.status === 404 ? "That run no longer exists, so it cannot be kept again." : `Couldn't keep it again: ${err.message}`);
      }
    });

  // Delete (DESIGN.md §30): a finished run goes to the bin, with a toast and Undo; a run in the bin, a run that is only waiting, and any run when
  // there is no bin are deleted for good.
  const binDays = caps?.limits.bin_days ?? 0;

  const confirmDelete = async () => {
    const run = pendingDelete;
    setPendingDelete(null);
    if (!run) return;
    try {
      // a finished run goes to the bin: the page keeps the run (it now has `deleted_at`, which takes it out of the list) and the toast offers
      // Undo. Anything else is deleted for good, and the page drops it.
      if (canBin(run, binDays)) {
        const updated = await api.binRun(run.id);
        dispatch({ type: "runUpsert", run: updated });
        push("info", binnedText(run, binDays), { label: "Undo", run: () => void restore(updated, false) });
      } else {
        await api.deleteRun(run.id);
        dispatch({ type: "runDeleted", id: run.id });
      }
    } catch (error) {
      push("error", `Couldn't delete: ${(error as Error).message}`);
    }
  };

  // Restore a run from the bin: it goes back as it was, with a fresh clock if it is not kept (§30.4). Pressed on a card in the bin, the card
  // leaves the list, so keyboard focus is looked after as for Keep (§29.4); from the Undo toast there is nothing to look after.
  const restore = (run: Run, fromCard: boolean) =>
    once(`restore:${run.id}`, async () => {
      try {
        const updated = await api.restoreRun(run.id);
        if (fromCard) refocus.current = true;
        dispatch({ type: "runUpsert", run: updated });
        push("info", restoredText(updated, Date.now(), nameOfProject(updated.project_id)));
      } catch (error) {
        const err = error as ApiError;
        // 404: someone deleted it for good meanwhile. 409: it is not in the bin any more (restored elsewhere). Both are said quietly, since
        // nothing is wrong; anything else is an error.
        push(err.status === 404 || err.status === 409 ? "info" : "error",
          err.status === 404 ? "That run no longer exists." : err.status === 409 ? "That run is not in the bin any more." : `Couldn't restore it: ${err.message}`);
      }
    });

  // ------------------------------------------------------------------ project folders (DESIGN.md §32)
  // What a failed project request says, in words for the place that asked: a refused name or a taken one is the server's own message, a project
  // that has gone is said quietly, and anything else is an error.
  const projectProblem = (error: unknown, what: string): string => {
    const err = error as ApiError;
    if (err.status === 404) return "That project no longer exists.";
    return err.status === 422 || err.status === 409 ? err.message : `Couldn't ${what}: ${err.message}`;
  };
  // the list as the page holds it, with one project added or replaced, in order (the server's own order comes back with the next read)
  const withProjectInList = (project: Project): Project[] =>
    [...projects.filter((other) => other.id !== project.id), project].sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

  // File a run in a project, and keep it: the same call moves a run that is filed already. The toast says which, and Undo puts the run back exactly
  // as it was: out of the project and not kept if it was neither, or back in the project it came from (§32.3 item 3).
  const fileInto = (run: Run, target: Project) =>
    once(`file:${run.id}`, async () => {
      const before = { projectId: run.project_id, pinned: run.pinned };
      const from = nameOfProject(run.project_id);
      try {
        const updated = await api.fileRun(run.id, target.id);
        // the card leaves the list when the view is No project, or another project: keyboard focus is looked after as for Keep (§29.4)
        if (matches(run, filter) && !matches(updated, filter)) refocus.current = true;
        dispatch({ type: "runUpsert", run: updated });
        push("info", from !== null ? movedText(run, from, target.name) : filedText(run, target.name), { label: "Undo", run: () => void undoFiling(updated, before) });
      } catch (error) {
        const err = error as ApiError;
        push(err.status === 404 || err.status === 409 ? "info" : "error",
          err.status === 404 ? (err.message.startsWith("Run") ? "That run no longer exists." : "That project no longer exists.")
            : err.status === 409 ? "That run is in the bin. Restore it before filing it in a project." : `Couldn't file it: ${err.message}`);
      }
    });

  // The Undo of a filing, a move or a taking out: the run goes back to where it was. From no project it is taken out, and un-kept if it was not
  // kept before (keep=false); from a project it is filed back there (it stays kept either way). No keyboard focus is looked after here, as it is
  // for filing and taking out: those are done from a card that is in the view and may leave it, but an Undo comes from the toast, and puts a card
  // back where it was shown (a card that the action had taken out of the view comes back into it; one that was filed into the view was not in it
  // before, so the view it is Undone in is never one that shows it).
  const undoFiling = (run: Run, before: { projectId: string | null; pinned: boolean }) =>
    once(`file:${run.id}`, async () => {
      try {
        const back = before.projectId === null ? await api.unfileRun(run.id, before.pinned) : await api.fileRun(run.id, before.projectId);
        dispatch({ type: "runUpsert", run: back });
      } catch (error) {
        const err = error as ApiError;
        push(err.status === 404 || err.status === 409 ? "info" : "error",
          err.status === 404 ? "That run or that project no longer exists, so it cannot be put back." : err.status === 409 ? "That run is not where it was filed any more." : `Couldn't put it back: ${err.message}`);
      }
    });

  // Take a run out of its project. It stays kept; the toast says so, and Undo files it back.
  const takeOut = (run: Run) =>
    once(`file:${run.id}`, async () => {
      const from = run.project_id;
      try {
        const updated = await api.unfileRun(run.id, true);
        if (matches(run, filter) && !matches(updated, filter)) refocus.current = true;
        dispatch({ type: "runUpsert", run: updated });
        push("info", takenOutText(nameOfProject(from) ?? "its project"), from === null ? undefined : { label: "Undo", run: () => void undoFiling(updated, { projectId: from, pinned: true }) });
      } catch (error) {
        const err = error as ApiError;
        push(err.status === 404 || err.status === 409 ? "info" : "error",
          err.status === 404 ? "That run no longer exists." : err.status === 409 ? "That run is not in a project any more." : `Couldn't take it out: ${err.message}`);
      }
    });

  // Make a project and file the run in it, right from the card's drop-down. A refused name is returned for the field to show; the project made
  // is added to the list at once (the server's own list follows), so the filing can name it.
  const createAndFile = async (run: Run, name: string): Promise<string | null> => {
    let project: Project;
    try {
      project = await api.createProject(name);
    } catch (error) {
      return projectProblem(error, "make the project");
    }
    dispatch({ type: "projects", projects: withProjectInList(project) });
    await fileInto(run, project);
    return null;
  };

  // The Manage projects dialog: each answers with a message when it could not be done, or null when it was.
  const createProject = async (name: string): Promise<string | null> => {
    try {
      dispatch({ type: "projects", projects: withProjectInList(await api.createProject(name)) });
      return null;
    } catch (error) {
      return projectProblem(error, "make the project");
    }
  };
  const renameProject = async (project: Project, name: string): Promise<string | null> => {
    try {
      dispatch({ type: "projects", projects: withProjectInList(await api.renameProject(project.id, name)) });
      return null;
    } catch (error) {
      return projectProblem(error, "rename the project");
    }
  };
  const deleteProject = async (project: Project): Promise<string | null> => {
    try {
      await api.deleteProject(project.id);
      dispatch({ type: "projects", projects: projects.filter((other) => other.id !== project.id) });
      push("info", `Deleted the project “${project.name}”. Its runs are kept.`);
      return null;
    } catch (error) {
      return projectProblem(error, "delete the project");
    }
  };
  // what a card needs to offer projects (DESIGN.md §32.1)
  const projectControls: ProjectControls = {
    projects,
    nameMax: projectNameMax,
    onFile: (run, id) => {
      const target = projects.find((project) => project.id === id);
      if (target) void fileInto(run, target);
    },
    onUnfile: (run) => void takeOut(run),
    onCreate: createAndFile,
  };

  // Empty bin: everything in it, both tabs, for good. The server tells every page by `run.deleted`; this page also drops what it holds, so
  // that the list does not wait for the events.
  const confirmEmptyBin = async () => {
    setPendingEmptyBin(false);
    try {
      const { deleted } = await api.emptyBin();
      for (const run of Object.values(state.runs)) if (run.deleted_at !== null) dispatch({ type: "runDeleted", id: run.id });
      push("info", emptiedText(deleted));
    } catch (error) {
      push("error", `Couldn't empty the bin: ${(error as Error).message}`);
    }
  };

  const loadOlder = async () => {
    if (!view?.nextBefore) return;
    setLoadingOlder(true);
    try {
      dispatch({ type: "runsLoaded", page: await api.listRuns(view.nextBefore, filter), append: true, filter });
    } catch (error) {
      // stop the effect that reads the next page by itself from trying again and again after an error
      setLoadOlderFailed(true);
      push("error", `Couldn't load older runs: ${(error as Error).message}`);
    } finally {
      setLoadingOlder(false);
    }
  };

  // Choosing a filter: switch to it and remember the choice. The effect below reads its first page.
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

  // The counts on the filter bar are the server's; asked for again, after a short pause, whenever a run is made, deleted, kept, un-kept or
  // filed. With a project chosen the bar's numbers are the project's (DESIGN.md §32.5), so two are read: the whole history's (which Empty bin
  // needs, since it empties the whole bin) and the project's. Each is stored with the project it describes.
  useEffect(() => {
    if (state.connection !== "open") return;
    let stale = false; // another change, or another project, has made this answer out of date
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const whole = await api.runCounts(null);
          if (stale) return;
          dispatch({ type: "totals", counts: whole });
          if (filter.project === null) dispatch({ type: "counts", counts: whole, project: null });
          else {
            const within = await api.runCounts(filter.project);
            if (!stale) dispatch({ type: "counts", counts: within, project: filter.project });
          }
        } catch {
          /* the numbers stay as they were; the next change asks again */
        }
      })();
    }, 250);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [state.connection, state.countsStale, filter.project]);

  // The project folders (DESIGN.md §32): read when the page connects (at once: the filter and the cards need the names), and again, after a
  // short pause, whenever the server says a project or what is filed in one changed.
  useEffect(() => {
    if (state.connection !== "open") return;
    let stale = false;
    const timer = setTimeout(() => {
      void api.listProjects().then((list) => { if (!stale) dispatch({ type: "projects", projects: list }); }).catch(() => undefined);
    }, state.projects === null ? 0 : 250);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [state.connection, state.projectsStale]);

  // A project this browser remembers that no longer exists (another page deleted it): the filter goes back to any project, and a toast says so.
  useEffect(() => {
    if (state.projects === null || filter.project === null || filter.project === NO_PROJECT) return;
    if (state.projects.some((project) => project.id === filter.project)) return;
    changeFilter(withProject(filter, null));
    push("info", "That project no longer exists.");
  }, [state.projects, filter.project]);

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
  const watched = useRef<ReadonlySet<string>>(new Set());
  useEffect(() => {
    const next = watchWorking(watched.current, state.runs, filter);
    watched.current = next.watched;
    for (const run of next.left) push("info", leftTheViewText(run, viewScope), { label: "Show all", run: () => changeFilter(NO_FILTER) });
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
  const transient = (run: Run) => showsWorking(filter) && working(run) && !matches(run, filter); // in this view only while it works (§29.3)
  // the whole bin, both tabs, whatever project is chosen: what Empty bin deletes (the counts on the bar are for the tab and the project shown)
  const bin = state.totals ? state.totals.image.deleted + state.totals.music.deleted : null;
  // the one filter bar, shown above the list on both tabs (the Music tab is handed it)
  const filterBar = (
    <FilterBar filter={filter} keptCount={scopedCounts?.[tabKind].kept ?? null} deletedCount={scopedCounts?.[tabKind].deleted ?? null} binTotal={bin}
      projects={state.projects} tab={tabKind} onChange={changeFilter} onEmptyBin={() => setPendingEmptyBin(true)} onManageProjects={() => setManagingProjects(true)} />
  );
  // what replaces "Loading…" when the first page of a filtered list could not be read: the reason, and a way to try again
  const problem =
    filterProblem && !isDefault(filter) ? (
      <div className="empty-state" role="alert">
        <p className="empty-title">{filter.deleted === true ? "Couldn't load your deleted runs" : "Couldn't load your kept runs"}</p>
        <p>{filterProblem}</p>
        <button type="button" className="button small" onClick={() => void loadFirstPage(filter)}>Try again</button>
      </div>
    ) : null;
  // what an empty list says: that it is still looking (older pages may hold some), or the empty state for this filter
  const emptyFor = (kind: "image" | "music") =>
    lookingForMore ? <p className="loading" role="status">Looking through your older runs…</p> : (
      <EmptyHistory kind={kind} filter={filter} binDays={binDays} project={filter.project === null || filter.project === NO_PROJECT ? null : projects.find((project) => project.id === filter.project) ?? null}
        onShowAll={() => changeFilter(NO_FILTER)} onShowAnyProject={() => changeFilter(withProject(filter, null))} />
    );
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
                      viewScope={viewScope}
                      projects={projectControls}
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
                      onRestore={() => void restore(run, true)}
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
              viewScope={viewScope}
              projects={projectControls}
              more={more}
              loadingOlder={loadingOlder}
              now={now}
              push={push}
              onRun={(run: MusicRun) => dispatch({ type: "runUpsert", run })}
              onLoadOlder={() => void loadOlder()}
              onCancel={requestCancel}
              onToggleKeep={(run) => void toggleKeep(run)}
              onRestore={(run) => void restore(run, true)}
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
        readOnly={!!lightboxRun?.deleted_at}
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
      <ConfirmDelete run={pendingDelete} binDays={binDays} projectName={nameOfProject(pendingDelete?.project_id ?? null)} onCancel={() => setPendingDelete(null)} onConfirm={() => void confirmDelete()} />
      <ConfirmEmptyBin open={pendingEmptyBin} images={state.totals?.image.deleted ?? 0} music={state.totals?.music.deleted ?? 0} wholeBin={filter.project !== null}
        onCancel={() => setPendingEmptyBin(false)} onConfirm={() => void confirmEmptyBin()} />
      <ProjectsDialog open={managingProjects} projects={projects} nameMax={projectNameMax} onClose={() => setManagingProjects(false)}
        onCreate={createProject} onRename={renameProject} onDelete={deleteProject} />
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </>
  );
}
