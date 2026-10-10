// History filters (DESIGN.md §29.5): the page's side of the server's `RunFilter` (backend/studio/runfilter.py), and what the history
// shows for a filter. Both sides are tested against one table of cases (backend/tests/filter_cases.json).
//
// Adding a filter: a field here and in `filterKey`, `filterParams` and `matches`; a control in `components/FilterBar.tsx`; and a case in the
// table. The store keeps one view per filter (`filterKey`), so nothing there changes.

import type { KeyValueStore } from "./options";
import type { Run } from "./types";

/** One field per filter. `kept`: `null` = either, `true` = only runs that are kept, `false` = only runs that are not (nothing in the
 *  page asks for that yet; the server and `matches` already do it). `deleted`: `false` = the history, without the bin (what every
 *  filter means unless it says otherwise), `true` = only the bin (DESIGN.md §30), `null` = either (nothing in the page asks for it). */
export interface HistoryFilter {
  kept: boolean | null;
  deleted: boolean | null;
}

export const NO_FILTER: HistoryFilter = { kept: null, deleted: false };
export const ONLY_KEPT: HistoryFilter = { kept: true, deleted: false };
export const ONLY_DELETED: HistoryFilter = { kept: null, deleted: true };

export const FILTER_KEY = "studio.history.filter.v1";

export const isDefault = (filter: HistoryFilter): boolean => filter.kept === null && filter.deleted === false;

/** A stable name for a filter: the key of its view in the store, and what the browser remembers. */
export function filterKey(filter: HistoryFilter): string {
  const kept = filter.kept === null ? "" : filter.kept ? "kept" : "not-kept";
  const deleted = filter.deleted === null ? "any" : filter.deleted ? "deleted" : "";
  return [kept, deleted].filter(Boolean).join("+") || "all";
}

/** The query parameters of `GET /api/runs` for a filter: only what is not the default. `deleted=any` is not a thing the server takes,
 *  so a filter that wants both is not sent: the page never builds one. */
export function filterParams(filter: HistoryFilter): Record<string, string> {
  return { ...(filter.kept === null ? {} : { kept: String(filter.kept) }), ...(filter.deleted ? { deleted: "true" } : {}) };
}

/** The server's rule for one run (`RunFilter.conditions`): the page uses it to decide which runs it already holds a filter shows. */
export function matches(run: Pick<Run, "pinned" | "deleted_at">, filter: HistoryFilter): boolean {
  return (filter.kept === null || run.pinned === filter.kept) && (filter.deleted === null || (run.deleted_at !== null) === filter.deleted);
}

/** Whether a filter shows runs that are working whether or not they match it (DESIGN.md §29.3): every filter but the default and the
 *  bin, where nothing is ever working. */
export const showsWorking = (filter: HistoryFilter): boolean => !isDefault(filter) && filter.deleted !== true;

/** A queued or running run. A filtered view shows these whether or not they match (§29.3). */
export const working = (run: Pick<Run, "status">): boolean => run.status === "queued" || run.status === "running";

/** Whether Delete moves a run to the bin (DESIGN.md §30.2): there is a bin, the run has finished, and it is not in the bin already. A run that
 *  is still waiting made nothing worth keeping and is deleted for good, and so is every run when there is no bin; a run in the bin is
 *  deleted for good by Delete forever. */
export function canBin(run: Pick<Run, "status" | "deleted_at">, binDays: number): boolean {
  return binDays > 0 && run.deleted_at === null && !working(run);
}

/** What the browser remembers is read defensively: a name this version does not know is the default. */
export function readFilter(store: KeyValueStore): HistoryFilter {
  switch (store.get(FILTER_KEY)) {
    case "kept":
      return ONLY_KEPT;
    case "not-kept":
      return { kept: false, deleted: false };
    case "deleted":
      return ONLY_DELETED;
    default:
      return NO_FILTER;
  }
}

export function saveFilter(filter: HistoryFilter, store: KeyValueStore): void {
  store.set(FILTER_KEY, filterKey(filter));
}

// ------------------------------------------------------------------ views
/** Where a view has loaded down to: the last run of its last page. Runs older than that are not shown by it (they may be in the cache
 *  because another filter loaded them), so that nothing appears out of sequence. */
export interface Boundary {
  created_at: string;
  id: string;
}

/** What the store knows of one filter's list: whether its first page has arrived, where the next page starts, and the boundary. */
export interface View {
  ready: boolean;
  nextBefore: string | null;
  boundary: Boundary | null;
}

/** Newest first is `created_at` descending, then `id` descending (the store's own order). */
function within(run: Run, boundary: Boundary | null): boolean {
  if (!boundary) return true;
  return run.created_at > boundary.created_at || (run.created_at === boundary.created_at && run.id >= boundary.id);
}

/** The runs a filter shows, newest first: those of the cache that match it and lie within what its view has loaded. With a filter on,
 *  the runs that are working come first, whatever they match (§29.3). */
export function visibleRuns(runs: Record<string, Run>, order: readonly string[], view: View | undefined, filter: HistoryFilter): Run[] {
  const boundary = view?.boundary ?? null;
  const active: Run[] = [];
  const shown: Run[] = [];
  for (const id of order) {
    const run = runs[id];
    if (!run) continue;
    if (showsWorking(filter) && working(run)) active.push(run);
    else if (matches(run, filter) && within(run, boundary)) shown.push(run);
  }
  return [...active, ...shown];
}

/** The runs that are in a filtered view only while they work (§29.3): which are still being watched, and which have just left the view
 *  (finished, failed or canceled without being kept). A run kept while it worked, or deleted, is no longer watched and is not reported. */
export function watchWorking(watched: ReadonlySet<string>, runs: Record<string, Run>, filter: HistoryFilter): { watched: Set<string>; left: Run[] } {
  const next = new Set<string>();
  const left: Run[] = [];
  if (!showsWorking(filter)) return { watched: next, left }; // nothing is shown only for working in the unfiltered list or the bin
  for (const run of Object.values(runs)) if (working(run) && !matches(run, filter)) next.add(run.id);
  for (const id of watched) {
    const run = runs[id];
    if (!run) continue; // deleted
    if (!working(run) && !matches(run, filter)) left.push(run); // finished without being kept; one still working is watched on, one kept stays
  }
  return { watched: next, left };
}

/** The counts the server keeps (`GET /api/runs/counts`): per tab, how many runs there are and how many are kept. */
export interface Counts {
  image: { all: number; kept: number; deleted: number };
  music: { all: number; kept: number; deleted: number };
}
