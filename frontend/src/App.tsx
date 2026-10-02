import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { Header } from "./components/Header";
import { ConfirmDelete, Lightbox } from "./components/Dialogs";
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
  loadOptions,
  optionsFromRun,
  optionsProblem,
  retryRequest,
  saveOptions,
  type Options,
} from "./options";
import { initialState, reducer } from "./store";
import type { Run } from "./types";
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
  const [loadingOlder, setLoadingOlder] = useState(false);
  const { toasts, push, dismiss } = useToasts();
  const now = useNow(30_000);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const { caps, status } = state;

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

  const submit = useCallback(async () => {
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
      dispatch({ type: "runUpsert", run: await api.createRun(buildRequest(prompt, options, caps)) });
    } catch (error) {
      const err = error as ApiError;
      if (err.status === 422) setFormProblem(err.message);
      else push("error", err.status === 429 ? err.message : `Couldn't start the run: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  }, [caps, options, prompt, submitting, push]);

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
    push("info", `Loaded the prompt and options. Seed locked to ${run.options.seed}.`);
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    promptRef.current?.focus({ preventScroll: true });
  };

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
              onOpenOptions={() => setOptionsOpen(true)}
              onSubmit={() => void submit()}
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
                      onRetry={() => void retry(run)}
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
      <ConfirmDelete run={pendingDelete} onCancel={() => setPendingDelete(null)} onConfirm={() => void confirmDelete()} />
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </>
  );
}
