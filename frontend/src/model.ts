// Loading and unloading the model from the header (DESIGN.md §25, §26.1). What is offered, and what a screen reader hears,
// are pure functions of the server's worker status, so the rules can be tested without a browser.

import type { ModelName, WorkerState, WorkerStatus } from "./types";

export type ModelKind = "load" | "unload";

/** One press of the button: Load or Unload, and which model (the one the current tab is about). */
export interface ModelAction {
  kind: ModelKind;
  model: ModelName;
}

/** What the user asked for, and the state the worker was in at the time (so the old state is not taken for the answer).
 *  The model counts too: loading the music model over a ready image model goes from "ready" to "ready" again. */
export interface ModelAsk extends ModelAction {
  from: WorkerState;
  fromModel: ModelName;
}

export const MODEL_LABEL: Record<ModelName, string> = { image: "Image model", music: "Music model" };

/** The button to offer next to the model pill for the tab that is open (`tab` is the model that tab is about):
 *  - Unload model when that tab's model is loaded and idle;
 *  - Load model when nothing is loaded or the last load had a problem, and Load when the *other* model is loaded and
 *    idle (the server unloads it first). Load needs an idle timeout above 0, or the model would be unloaded the moment
 *    it is ready;
 *  - nothing while a model loads or a run is going, when the server cannot run the model at all, and on the Music tab
 *    when the music model cannot run here. */
export function modelAction(worker: WorkerStatus, tab: ModelName, musicAvailable = true): ModelAction | null {
  if (tab === "music" && !musicAvailable) return null;
  if (worker.state === "ready" && worker.model === tab) return { kind: "unload", model: tab };
  const startable = worker.state === "unloaded" || worker.state === "error" || (worker.state === "ready" && worker.model !== tab);
  if (startable && worker.idle_timeout_min > 0) return { kind: "load", model: tab };
  return null;
}

/** The words on the button. The Images tab keeps the plain "Load model" it has had since 1.7. */
export function modelActionLabel(action: ModelAction): string {
  if (action.kind === "unload") return "Unload model";
  return action.model === "music" ? "Load music model" : "Load model";
}

export function modelActionTitle(action: ModelAction, worker: WorkerStatus): string {
  if (action.kind === "unload") return "Unload the model now to give its memory back. The next run loads it again.";
  const other = worker.state === "ready" && worker.model !== action.model;
  const swap = other ? ` The ${worker.model} model is unloaded first.` : "";
  return action.model === "music"
    ? `Load the music model now, so it is ready when you press Make music.${swap}`
    : `Load the model now, so it is ready when you press Generate.${swap}`;
}

/** What to announce as the model answers a request, and whether that is the last word on it. Null while the worker is
 *  still in the state (and holding the model) it was when the user asked. */
export function modelAnnouncement(ask: ModelAsk, worker: WorkerStatus): { text: string; settled: boolean } | null {
  if (worker.state === ask.from && worker.model === ask.fromModel) return null;
  const name = ask.model === "music" ? "music" : "image";
  if (ask.kind === "unload") return worker.state === "unloaded" ? { text: `${MODEL_LABEL[ask.model]} unloaded.`, settled: true } : null;
  switch (worker.state) {
    case "loading":
      return { text: `Loading the ${name} model…`, settled: false };
    case "ready":
    case "busy":
      // The old model can still be the one that is ready for a moment while the switch is under way.
      return worker.model === ask.model ? { text: `${MODEL_LABEL[ask.model]} ready.`, settled: true } : null;
    case "error":
    case "unavailable":
      return { text: `${MODEL_LABEL[ask.model]} problem. ${worker.detail ?? ""}`.trim(), settled: true };
    default:
      return null;
  }
}
