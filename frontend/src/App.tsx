import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { Header } from "./components/Header";
import { ConfirmCancel, ConfirmDelete, Lightbox, cardReuseButton } from "./components/Dialogs";
import { ConnectionBanner, QueueBar, Toasts } from "./components/Feedback";
import { OptionsDrawer } from "./components/OptionsDrawer";
import { PromptBar } from "./components/PromptBar";
import { RunCard } from "./components/RunCard";
import { copyText, useNow, useToasts } from "./hooks";
import {
  PROMPT_KEY,
  browserStore,
  buildRequest,
  defaultOptions,
  draftRequest,
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
import type { CreateRunBody, Run } from "./types";
import { useEventStream } from "./useEvents";

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
  const [lightbox, setLightbox] = useState<{ runId: string; index: number } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Run | null>(null);
  const [pendingCancel, setPendingCancel] = useState<Run | null>(null);
  const inFlight = useRef(new Set<string>()); // "cancel:<id>" / "keep:<id>": one request per control at a time
  const [loadingOlder, setLoadingOlder] = useState(false);
  const { toasts, push, dismiss } = useToasts();
  const now = useNow(30_000);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const { caps, status } = state;
  const version = status?.version;

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
  useEffect(() => {
    store.set(PROMPT_KEY, prompt);
  }, [prompt, store]);

  const send = useCallback(async (build: (prompt: string, options: Options, caps: NonNullable<typeof state.caps>) => CreateRunBody) => {
    if (!caps || !options || !prompt.trim() || submitting) return;
    const problem = optionsProblem(options, caps);
    if (problem) {
      setFormProblem(`Check the options: ${problem}`);
      setOptionsOpen(true);
      return;
    }
    setSubmitting(true);
    setFormProblem(null);
    try {
      dispatch({ type: "runUpsert", run: await api.createRun(build(prompt, options, caps)) });
    } catch (error) {
      const err = error as ApiError;
      if (err.status === 422) setFormProblem(err.message);
      else push("error", err.status === 429 ? err.message : `Couldn't start the run: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  }, [caps, options, prompt, submitting, push]);

  const submit = useCallback(() => send(buildRequest), [send]);
  const submitDraft = useCallback(() => send(draftRequest), [send]);

  // A "check the options" message is stale as soon as the options change.
  const changeOptions = (next: Options) => {
    setOptions(next);
    setFormProblem(null);
  };

  const reuse = (run: Run) => {
    if (!caps || !options) return;
    setPrompt(run.prompt);
    setOptions(optionsFromRun(run, caps, options));
    setFormProblem(null);
    push("info", run.options.draft
      ? "Loaded the prompt. Your size, steps and seed are unchanged, so Generate makes the full-size image."
      : `Loaded the prompt and options. Seed locked to ${run.options.seed}.`);
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    promptRef.current?.focus({ preventScroll: true });
  };

  const regenerateLarger = (run: Run) =>
    once(`larger:${run.id}`, async () => {
      const body = largerRequest(run);
      if (!body) return;
      try {
        dispatch({ type: "runUpsert", run: await api.createRun(body) });
        push("info", `Queued at ${body.options.width}×${body.options.height}. A bigger size is a new picture, so expect it to differ from this one.`);
      } catch (error) {
        const err = error as ApiError;
        push("error", err.status === 429 ? err.message : `Couldn't regenerate: ${err.message}`);
      }
    });

  const retry = async (run: Run) => {
    try {
      dispatch({ type: "runUpsert", run: await api.createRun(retryRequest(run)) });
    } catch (error) {
      push("error", `Couldn't retry: ${(error as Error).message}`);
    }
  };

  const copy = async (run: Run) => {
    const ok = await copyText(run.prompt);
    push(ok ? "success" : "error", ok ? "Prompt copied." : "Couldn't copy. Select the prompt text instead.");
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

  const toggleKeep = (run: Run) =>
    once(`keep:${run.id}`, async () => {
      try {
        dispatch({ type: "runUpsert", run: await api.keepRun(run.id, !run.pinned) });
      } catch (error) {
        const err = error as ApiError;
        push("error", err.status === 404 ? "That run no longer exists." : `Couldn't ${run.pinned ? "stop keeping" : "keep"} it: ${err.message}`);
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
    if (!state.nextBefore) return;
    setLoadingOlder(true);
    try {
      dispatch({ type: "runsLoaded", page: await api.listRuns(state.nextBefore), append: true });
    } catch (error) {
      push("error", `Couldn't load older runs: ${(error as Error).message}`);
    } finally {
      setLoadingOlder(false);
    }
  };

  if (startupError) {
    return (
      <main className="app-main startup-error">
        <h1>Can't reach the studio</h1>
        <p>{startupError}</p>
        <button type="button" className="button primary" onClick={() => void start()}>Try again</button>
      </main>
    );
  }

  const lightboxRun = lightbox ? state.runs[lightbox.runId] ?? null : null;

  return (
    <>
      <a className="skip-link" href="#prompt">Skip to the prompt</a>
      <Header status={status} now={now} />
      <ConnectionBanner connection={state.connection} serverStopping={state.serverStopping} />
      <main className="app-main">
        {!caps || !options ? (
          <p className="loading" role="status">Connecting to the studio…</p>
        ) : (
          <>
            <PromptBar
              ref={promptRef}
              caps={caps}
              options={options}
              prompt={prompt}
              submitting={submitting}
              problem={formProblem}
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
            <section className="timeline" aria-label="Your runs">
              {!state.runsReady ? (
                <p className="loading" role="status">Loading your runs…</p>
              ) : state.order.length === 0 ? (
                <div className="empty-state">
                  <p className="empty-title">No images yet</p>
                  <p>Describe something above and press Generate. Every run lands here with its prompt and settings.</p>
                </div>
              ) : (
                state.order.map((id) => {
                  const run = state.runs[id];
                  return (
                    <RunCard
                      key={id}
                      run={run}
                      now={now}
                      workerState={status?.worker.state ?? null}
                      onReuse={() => reuse(run)}
                      onRegenerateLarger={() => void regenerateLarger(run)}
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
              {state.nextBefore && (
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
              onChange={changeOptions}
              onReset={() => changeOptions(defaultOptions(caps))}
            />
          </>
        )}
      </main>
      <Lightbox
        run={lightboxRun}
        index={lightbox?.index ?? 0}
        onIndex={(index) => setLightbox((current) => (current ? { ...current, index } : current))}
        onClose={() => setLightbox(null)}
      />
      <ConfirmCancel run={pendingCancel} onBack={() => setPendingCancel(null)} onConfirm={confirmCancel} />
      <ConfirmDelete run={pendingDelete} onCancel={() => setPendingDelete(null)} onConfirm={() => void confirmDelete()} />
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </>
  );
}
