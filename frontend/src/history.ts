// History filters (DESIGN.md §29.5): the page's side of the server's `RunFilter` (backend/studio/runfilter.py), and what the history
// shows for a filter. Both sides are tested against one table of cases (backend/tests/filter_cases.json).
//
// Adding a filter: a field here and in `filterKey`, `filterParams` and `matches`; a control in `components/FilterBar.tsx`; and a case in the
// table. The store keeps one view per filter (`filterKey`), so nothing there changes.

import type { KeyValueStore } from "./options";
import type { Run } from "./types";

/** One field per filter. `kept`: `null` = either, `true` = only runs that are kept, `false` = only runs that are not (nothing in the
 *  page asks for that yet; the server and `matches` already do it). */
export interface HistoryFilter {
  kept: boolean | null;
}

// The two filters the page offers today: everything, and only the runs that are kept
export const NO_FILTER: HistoryFilter = { kept: null };
export const ONLY_KEPT: HistoryFilter = { kept: true };

export const FILTER_KEY = "studio.history.filter.v1";

export const isDefault = (filter: HistoryFilter): boolean => filter.kept === null;

/** A stable name for a filter: the key of its view in the store, and what the browser remembers. */
export function filterKey(filter: HistoryFilter): string {
  return filter.kept === null ? "all" : filter.kept ? "kept" : "not-kept";
}

/** The query parameters of `GET /api/runs` for a filter: only what is set. */
export function filterParams(filter: HistoryFilter): Record<string, string> {
  return filter.kept === null ? {} : { kept: String(filter.kept) };
}

/** The server's rule for one run (`RunFilter.conditions`): the page uses it to decide which runs it already holds a filter shows. */
export function matches(run: Pick<Run, "pinned">, filter: HistoryFilter): boolean {
  return filter.kept === null || run.pinned === filter.kept;
}

/** A queued or running run. A filtered view shows these whether or not they match (§29.3). */
export const working = (run: Pick<Run, "status">): boolean => run.status === "queued" || run.status === "running";

/** What the browser remembers is read defensively: a name this version does not know is the default. */
export function readFilter(store: KeyValueStore): HistoryFilter {
  switch (store.get(FILTER_KEY)) {
    case "kept":
      return { kept: true };
    case "not-kept":
      return { kept: false };
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
    // With a filter on, a run that is still working is always shown, at the top, even if it doesn't match: it is in the view only while it
    // works. Every other run must match the filter AND lie within what this view has loaded.
    if (!isDefault(filter) && working(run)) active.push(run);
    else if (matches(run, filter) && within(run, boundary)) shown.push(run);
  }
  return [...active, ...shown];
}

/** The runs that are in a filtered view only while they work (§29.3): which are still being watched, and which have just left the view
 *  (finished, failed or canceled without being kept). A run kept while it worked, or deleted, is no longer watched and is not reported. */
export function watchWorking(watched: ReadonlySet<string>, runs: Record<string, Run>, filter: HistoryFilter): { watched: Set<string>; left: Run[] } {
  const next = new Set<string>();
  const left: Run[] = [];
  if (isDefault(filter)) return { watched: next, left }; // nothing is shown only for working in the unfiltered list
  // watch every run that is working and doesn't match the filter: it is in the view only for now
  for (const run of Object.values(runs)) if (working(run) && !matches(run, filter)) next.add(run.id);
  // of those watched last time, report each that has since finished without matching: it has just left the view
  for (const id of watched) {
    const run = runs[id];
    if (!run) continue; // deleted
    if (!working(run) && !matches(run, filter)) left.push(run); // finished without being kept; one still working is watched on, one kept stays
  }
  return { watched: next, left };
}

/** The counts the server keeps (`GET /api/runs/counts`): per tab, how many runs there are and how many are kept. */
export interface Counts {
  image: { all: number; kept: number };
  music: { all: number; kept: number };
}
