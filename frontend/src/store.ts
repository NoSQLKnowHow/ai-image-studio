// Client state: runs (kept newest first), server status and capabilities, connection state.
// Live events and fetched pages both funnel through this reducer, so ordering rules live in one place.

import type { Capabilities, Progress, Run, RunStatus, RunsPage, Status, WorkerStatus } from "./types";

export type Connection = "connecting" | "open" | "lost";

export interface State {
  caps: Capabilities | null;
  status: Status | null;
  runs: Record<string, Run>;
  order: string[]; // newest first
  nextBefore: string | null;
  runsReady: boolean; // the first snapshot has arrived (until then "no runs" would be a guess)
  gone: Record<string, true>; // deleted runs: a late response must not bring one back
  connection: Connection;
  serverStopping: boolean;
}

export const initialState: State = {
  caps: null,
  status: null,
  runs: {},
  order: [],
  nextBefore: null,
  runsReady: false,
  gone: {},
  connection: "connecting",
  serverStopping: false,
};

export type Action =
  | { type: "caps"; caps: Capabilities }
  | { type: "status"; status: Status }
  | { type: "worker"; worker: WorkerStatus }
  | { type: "runsLoaded"; page: RunsPage; append: boolean } // append: an older page; otherwise the newest (a snapshot)
  | { type: "runUpsert"; run: Run }
  | { type: "runProgress"; id: string; progress: Progress }
  | { type: "runDeleted"; id: string }
  | { type: "queue"; running: string | null; positions: Record<string, number> }
  | { type: "connection"; value: Connection }
  | { type: "serverStopping" };

function newestFirst(runs: Record<string, Run>): string[] {
  return Object.values(runs)
    .sort((a, b) => (a.created_at === b.created_at ? (a.id < b.id ? 1 : -1) : a.created_at < b.created_at ? 1 : -1))
    .map((r) => r.id);
}

// A run only moves forward: queued → running → finished. Snapshots, POST responses and events travel
// on different connections, so they can arrive out of order; an older picture never replaces a newer.
const STAGE: Record<RunStatus, number> = { queued: 0, running: 1, done: 2, failed: 2, canceled: 2 };
const finished = (run: Run) => STAGE[run.status] === 2;

function latest(current: Run | undefined, incoming: Run): Run {
  if (!current || STAGE[incoming.status] > STAGE[current.status]) return incoming;
  if (STAGE[incoming.status] < STAGE[current.status]) return current;
  // Same stage: take the incoming copy, but a snapshot of a running run may predate its latest step.
  return incoming.status === "running" && !incoming.progress && current.progress ? { ...incoming, progress: current.progress } : incoming;
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "caps":
      return { ...state, caps: action.caps };
    case "status":
      return { ...state, status: action.status };
    case "worker":
      return state.status ? { ...state, status: { ...state.status, worker: action.worker } } : state;
    case "runsLoaded": {
      const { page, append } = action;
      let runs: Record<string, Run>;
      let nextBefore = page.next_before;
      if (append) {
        runs = { ...state.runs };
      } else {
        // The newest page, read by the server after the event stream subscribed (see the hello
        // event): the truth for its window, so a run in that window that's missing was deleted.
        // Anything newer that it lacks arrives as an event after it. Older pages already loaded are
        // kept, unless this page is the whole history.
        const oldest = page.runs.at(-1)?.created_at;
        runs = {};
        if (oldest && page.next_before) {
          for (const run of Object.values(state.runs)) if (run.created_at < oldest) runs[run.id] = run;
        }
        if (Object.keys(runs).length) nextBefore = state.nextBefore;
      }
      for (const run of page.runs) if (!state.gone[run.id]) runs[run.id] = latest(state.runs[run.id], run);
      return { ...state, runs, order: newestFirst(runs), nextBefore, runsReady: state.runsReady || !append };
    }
    case "runUpsert": {
      const current = state.runs[action.run.id];
      if (state.gone[action.run.id]) return state;
      const run = latest(current, action.run);
      if (run === current) return state;
      const runs = { ...state.runs, [run.id]: run };
      return { ...state, runs, order: current ? state.order : newestFirst(runs) };
    }
    case "runProgress": {
      const run = state.runs[action.id];
      if (!run || finished(run)) return state; // a late step from before the run finished
      return { ...state, runs: { ...state.runs, [run.id]: { ...run, status: "running", progress: action.progress } } };
    }
    case "runDeleted": {
      const gone = { ...state.gone, [action.id]: true as const };
      if (!state.runs[action.id]) return { ...state, gone };
      const runs = { ...state.runs };
      delete runs[action.id];
      return { ...state, runs, gone, order: state.order.filter((id) => id !== action.id) };
    }
    case "queue": {
      let changed = false;
      const runs = { ...state.runs };
      for (const run of Object.values(state.runs)) {
        if (run.status !== "queued") continue;
        const position = action.positions[run.id] ?? null;
        if (position !== run.queue_position) {
          runs[run.id] = { ...run, queue_position: position };
          changed = true;
        }
      }
      const status = state.status
        ? { ...state.status, queue: { ...state.status.queue, running: action.running, queued: Object.keys(action.positions).length } }
        : null;
      return { ...state, runs: changed ? runs : state.runs, status };
    }
    case "connection":
      return { ...state, connection: action.value, serverStopping: action.value === "open" ? false : state.serverStopping };
    case "serverStopping":
      return { ...state, serverStopping: true };
  }
}
