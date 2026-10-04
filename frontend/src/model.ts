// Loading and unloading the model from the header (DESIGN.md §25). What is offered, and what a screen reader hears,
// are pure functions of the server's worker status, so the rules can be tested without a browser.

import type { WorkerState, WorkerStatus } from "./types";

export type ModelKind = "load" | "unload";

/** What the user asked for, and the state the model was in at the time (so the old state is not taken for the answer). */
export interface ModelAsk {
  kind: ModelKind;
  from: WorkerState;
}

/** The button to offer next to the model pill: Load when the model is not loaded or had a problem (and the idle timeout
 *  is above 0, or it would be unloaded the moment it is ready), Unload when it is loaded and idle, otherwise none. Not
 *  while it loads or generates, and not when the server cannot run the model at all. */
export function modelAction(worker: WorkerStatus): ModelKind | null {
  if (worker.state === "ready") return "unload";
  if ((worker.state === "unloaded" || worker.state === "error") && worker.idle_timeout_min > 0) return "load";
  return null;
}

export const MODEL_ACTION_LABEL: Record<ModelKind, string> = { load: "Load model", unload: "Unload model" };

export const MODEL_ACTION_TITLE: Record<ModelKind, string> = {
  load: "Load the model now, so it is ready when you press Generate.",
  unload: "Unload the model now to give its memory back. The next run loads it again.",
};

/** What to announce as the model answers a request, and whether that is the last word on it. Null while the state is
 *  still the one it was in when the user asked. */
export function modelAnnouncement(ask: ModelAsk, worker: WorkerStatus): { text: string; settled: boolean } | null {
  if (worker.state === ask.from) return null;
  if (ask.kind === "unload") return worker.state === "unloaded" ? { text: "Model unloaded.", settled: true } : null;
  switch (worker.state) {
    case "loading":
      return { text: "Loading the model…", settled: false };
    case "ready":
    case "busy":
      return { text: "Model ready.", settled: true };
    case "error":
    case "unavailable":
      return { text: `Model problem. ${worker.detail ?? ""}`.trim(), settled: true };
    default:
      return null;
  }
}
