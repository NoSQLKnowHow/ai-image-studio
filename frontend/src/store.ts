// Client state: runs (kept newest first), server status and capabilities, connection state.
// Live events and fetched pages both funnel through this reducer, so ordering rules live in one place.

import { NO_FILTER, filterKey, isDefault, type Counts, type HistoryFilter, type View } from "./history";
import type { Capabilities, Progress, Project, Run, RunStatus, RunsPage, Status, WorkerStatus } from "./types";

export type Connection = "connecting" | "open" | "lost";

export interface State {
  caps: Capabilities | null;
  status: Status | null;
  runs: Record<string, Run>;
  order: string[]; // newest first
  views: Record<string, View>; // one per history filter (DESIGN.md §29.5): has its first page arrived, and where does the next start
  counts: Counts | null; // how many runs each tab holds and how many are kept, from the server (§29.6), within the project `countsProject`
  countsProject: string | null; // the project `counts` is about: null for the whole history (DESIGN.md §32.5). A count is only used for the filter it describes.
  totals: Counts | null; // the same counts for the whole history, whatever project is chosen: Empty bin empties the whole bin, so it needs these
  countsStale: number; // goes up whenever a run is made, deleted, kept, un-kept or filed, so the page asks for the counts again
  projects: Project[] | null; // the project folders, A to Z, with how many runs each holds (DESIGN.md §32); null until the first answer
  projectsStale: number; // goes up whenever the projects, or what is filed in them, change, so the page asks for the list again
  gone: Record<string, true>; // deleted runs: a late response must not bring one back
  connection: Connection;
  serverStopping: boolean;
}

export const initialState: State = {
  caps: null,
  status: null,
  runs: {},
  order: [],
  views: {},
  counts: null,
  countsProject: null,
  totals: null,
  countsStale: 0,
  projects: null,
  projectsStale: 0,
  gone: {},
  connection: "connecting",
  serverStopping: false,
};

export type Action =
  | { type: "caps"; caps: Capabilities }
  | { type: "status"; status: Status }
  | { type: "worker"; worker: WorkerStatus }
  | { type: "runsLoaded"; page: RunsPage; append: boolean; filter?: HistoryFilter } // append: an older page; otherwise the newest (a snapshot)
  | { type: "counts"; counts: Counts; project: string | null } // the counts within a project (null: the whole history)
  | { type: "totals"; counts: Counts } // the counts for the whole history
  | { type: "projects"; projects: Project[] }
  | { type: "projectsChanged" } // the server says a project, or what is filed in one, changed: read the list again
  | { type: "runUpsert"; run: Run }
  | { type: "runProgress"; id: string; progress: Progress }
  | { type: "runDeleted"; id: string }
  | { type: "queue"; running: string | null; positions: Record<string, number>; enlargeWaiting: string[] }
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
      // A page arrives for one filter. Every page feeds the one cache of runs (`runs`); each filter keeps its own bookkeeping in `views`:
      // has its first page arrived, where does its next page start, and how far has it loaded.
      const filter = action.filter ?? NO_FILTER;
      const key = filterKey(filter);
      const before = state.views[key];
      const last = page.runs.at(-1);
      // This page's effect on its view. It is ready once a first (non-append) page has arrived. `nextBefore` is where the next older page
      // starts. `boundary` is the last run just loaded: older runs in the cache (loaded for another filter) must not be shown by this view,
      // or they would appear out of sequence.
      let view: View = { ready: !!before?.ready || !append, nextBefore: page.next_before, boundary: page.next_before && last ? { created_at: last.created_at, id: last.id } : null };
      let runs: Record<string, Run>;
      if (append) {
        runs = { ...state.runs };
      } else if (!isDefault(filter)) {
        // A filtered first page only adds to what is held: a run missing from it may have been un-kept as well as deleted, and
        // deletions arrive as events. A page that is read again (the connection came back) keeps the deeper place its view had reached.
        runs = { ...state.runs };
        if (before?.ready && page.next_before) view = before;
      } else {
        // The newest page, read by the server after the event stream subscribed (see the hello
        // event): the truth for its window, so a run in that window that's missing was deleted.
        // Anything newer that it lacks arrives as an event after it. Older pages already loaded are
        // kept, unless this page is the whole history.
        const oldest = last?.created_at;
        runs = {};
        if (oldest && page.next_before) {
          for (const run of Object.values(state.runs)) if (run.created_at < oldest) runs[run.id] = run;
        }
        if (Object.keys(runs).length && before) view = { ...before, ready: true };
      }
      for (const run of page.runs) if (!state.gone[run.id]) runs[run.id] = latest(state.runs[run.id], run);
      return { ...state, runs, order: newestFirst(runs), views: { ...state.views, [key]: view } };
    }
    case "counts":
      return { ...state, counts: action.counts, countsProject: action.project };
    case "totals":
      return { ...state, totals: action.counts };
    case "projects":
      return { ...state, projects: action.projects };
    case "projectsChanged":
      return { ...state, projectsStale: state.projectsStale + 1 };
    case "runUpsert": {
      const current = state.runs[action.run.id];
      if (state.gone[action.run.id]) return state;
      const run = latest(current, action.run);
      if (run === current) return state;
      const runs = { ...state.runs, [run.id]: run };
      // a run filed, moved or taken out changes the counts (those of the project, and the numbers on the Project drop-down) and the projects' own list
      const projectChange = !!current && current.project_id !== run.project_id;
      const countsChange = !current || current.pinned !== run.pinned || (current.deleted_at === null) !== (run.deleted_at === null) || projectChange; // a new run, or one kept, un-kept, deleted, restored or filed: the counts have moved
      return {
        ...state, runs, order: current ? state.order : newestFirst(runs),
        countsStale: state.countsStale + (countsChange ? 1 : 0),
        projectsStale: state.projectsStale + (projectChange ? 1 : 0),
      };
    }
    case "runProgress": {
      const run = state.runs[action.id];
      if (!run || finished(run)) return state; // a late step from before the run finished
      return { ...state, runs: { ...state.runs, [run.id]: { ...run, status: "running", progress: action.progress } } };
    }
    case "runDeleted": {
      const gone = { ...state.gone, [action.id]: true as const };
      // any delete may change a count, and the run may have been filed in a project, so the page is told to ask the server again for both
      const countsStale = state.countsStale + 1;
      const projectsStale = state.projectsStale + 1;
      if (!state.runs[action.id]) return { ...state, gone, countsStale, projectsStale };
      const runs = { ...state.runs };
      delete runs[action.id];
      return { ...state, runs, gone, countsStale, projectsStale, order: state.order.filter((id) => id !== action.id) };
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
        ? { ...state.status, queue: { ...state.status.queue, running: action.running, queued: Object.keys(action.positions).length, enlarge_waiting: action.enlargeWaiting } }
        : null;
      return { ...state, runs: changed ? runs : state.runs, status };
    }
    case "connection":
      return { ...state, connection: action.value, serverStopping: action.value === "open" ? false : state.serverStopping };
    case "serverStopping":
      return { ...state, serverStopping: true };
  }
}
