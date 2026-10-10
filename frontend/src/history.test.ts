import { describe, expect, it } from "vitest";
import cases from "../../backend/tests/filter_cases.json";
import {
  FILTER_KEY, NO_FILTER, ONLY_DELETED, ONLY_KEPT, filterKey, filterParams, isDefault, matches, readFilter, saveFilter, showsWorking, visibleRuns, watchWorking, working,
  type HistoryFilter, type View,
} from "./history";
import type { KeyValueStore } from "./options";
import { makeMusicRun, makeRun } from "./testdata";
import type { Run, RunStatus } from "./types";

// ------------------------------------------------------------------ the table both sides are tested against (DESIGN.md §29.5, criterion 120)
// The server runs each case through its SQL (backend/tests/test_runfilter.py); here the page's `matches` gets the same runs.
const STATUSES: Record<string, RunStatus> = { queued: "queued", running: "running", done: "done", failed: "failed", canceled: "canceled" };

function tableRun(entry: { id: string; mode: string; status: string; pinned: boolean; deleted: boolean }): Run {
  const extra = { id: entry.id, status: STATUSES[entry.status], pinned: entry.pinned, deleted_at: entry.deleted ? "2026-10-02T00:00:00.000Z" : null };
  return entry.mode === "music" ? makeMusicRun(extra) : makeRun({ ...extra, mode: entry.mode as "generate" | "edit" });
}

describe("the shared table of cases", () => {
  const runs = cases.runs.map(tableRun); // newest first, as the table lists them

  for (const entry of cases.cases) {
    it(entry.name, () => {
      const filter = entry.filter as HistoryFilter;
      expect(runs.filter((run) => matches(run, filter)).map((run) => run.id)).toEqual(entry.expect);
    });
  }

  it("the page's counts of the table agree with the server's", () => {
    const count = (kind: "image" | "music", filter: HistoryFilter) =>
      runs.filter((run) => (run.mode === "music") === (kind === "music") && matches(run, filter)).length;
    const of = (kind: "image" | "music") => ({ all: count(kind, NO_FILTER), kept: count(kind, ONLY_KEPT), deleted: count(kind, ONLY_DELETED) });
    expect({ image: of("image"), music: of("music") }).toEqual(cases.counts);
  });
});

// ------------------------------------------------------------------ the filter itself
describe("a history filter", () => {
  it("is named, and the default is only 'either'", () => {
    const filters: HistoryFilter[] = [NO_FILTER, ONLY_KEPT, { kept: false, deleted: false }, ONLY_DELETED, { kept: true, deleted: true }, { kept: null, deleted: null }];
    expect(filters.map(filterKey)).toEqual(["all", "kept", "not-kept", "deleted", "kept+deleted", "any"]);
    expect(new Set(filters.map(filterKey)).size).toBe(filters.length); // every filter has a name of its own: a view each
    expect(filters.map(isDefault)).toEqual([true, false, false, false, false, false]);
  });

  it("asks the server for what it is, and for nothing when it is the default", () => {
    expect(filterParams(NO_FILTER)).toEqual({});
    expect(filterParams(ONLY_KEPT)).toEqual({ kept: "true" });
    expect(filterParams({ kept: false, deleted: false })).toEqual({ kept: "false" });
    expect(filterParams(ONLY_DELETED)).toEqual({ deleted: "true" });
    expect(filterParams({ kept: true, deleted: true })).toEqual({ kept: "true", deleted: "true" });
  });

  it("is a rule about one thing: whether the run is kept, whatever else it is", () => {
    for (const status of ["queued", "running", "done", "failed", "canceled"] as const) {
      expect(matches(makeRun({ status, pinned: true }), ONLY_KEPT)).toBe(true);
      expect(matches(makeRun({ status, pinned: false }), ONLY_KEPT)).toBe(false);
      expect(matches(makeRun({ status, pinned: false }), { kept: false, deleted: false })).toBe(true);
      expect(matches(makeRun({ status, pinned: true }), NO_FILTER)).toBe(true);
    }
  });

  it("keeps the bin out of every filter that does not ask for it, and the history out of the bin", () => {
    const inBin = makeRun({ pinned: true, deleted_at: "2026-10-02T00:00:00.000Z" });
    const plain = makeRun({ pinned: true });
    expect([NO_FILTER, ONLY_KEPT].map((filter) => matches(inBin, filter))).toEqual([false, false]);
    expect([NO_FILTER, ONLY_KEPT].map((filter) => matches(plain, filter))).toEqual([true, true]);
    expect([matches(inBin, ONLY_DELETED), matches(plain, ONLY_DELETED)]).toEqual([true, false]);
    expect(matches(inBin, { kept: null, deleted: null })).toBe(true); // either
  });

  it("shows working runs in every filtered view but the bin, where nothing works", () => {
    expect([NO_FILTER, ONLY_KEPT, { kept: false, deleted: false }, ONLY_DELETED].map(showsWorking)).toEqual([false, true, true, false]);
  });

  it("knows what is working", () => {
    expect(["queued", "running"].map((status) => working({ status: status as RunStatus }))).toEqual([true, true]);
    expect(["done", "failed", "canceled"].map((status) => working({ status: status as RunStatus }))).toEqual([false, false, false]);
  });
});

describe("what the browser remembers", () => {
  const memory = (initial: Record<string, string> = {}): KeyValueStore & { saved: Record<string, string> } => {
    const saved = { ...initial };
    return { available: true, saved, get: (key) => saved[key] ?? null, set: (key, value) => { saved[key] = value; } };
  };

  it("round-trips", () => {
    for (const filter of [NO_FILTER, ONLY_KEPT, { kept: false, deleted: false }, ONLY_DELETED]) {
      const store = memory();
      saveFilter(filter, store);
      expect(readFilter(store)).toEqual(filter);
    }
  });

  it("is the default when nothing is there, or when what is there is not a filter this version knows", () => {
    expect(readFilter(memory())).toEqual(NO_FILTER);
    for (const odd of ["", "everything", "KEPT", "{\"kept\":true}", "null"]) expect(readFilter(memory({ [FILTER_KEY]: odd }))).toEqual(NO_FILTER);
  });

  it("is the default for the kinds of filter that are not remembered", () => {
    expect(readFilter(memory({ [FILTER_KEY]: "kept+deleted" }))).toEqual(NO_FILTER);
    expect(readFilter(memory({ [FILTER_KEY]: "any" }))).toEqual(NO_FILTER);
  });

  it("is saved under a key with a version in it", () => {
    expect(FILTER_KEY).toBe("studio.history.filter.v1");
  });
});

// ------------------------------------------------------------------ what a view shows
describe("what a filter shows (DESIGN.md §29.2, §29.3, §29.5)", () => {
  const at = (n: number) => `2026-10-02T10:${String(n).padStart(2, "0")}:00.000Z`;
  const run = (id: string, n: number, extra: Partial<Run> = {}) => makeRun({ id, created_at: at(n), status: "done", pinned: false, ...extra } as never);
  const index = (...list: Run[]) => ({ runs: Object.fromEntries(list.map((r) => [r.id, r])), order: [...list].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).map((r) => r.id) });
  const ready = (boundary: View["boundary"] = null, nextBefore: string | null = null): View => ({ ready: true, nextBefore, boundary });
  const ids = (list: Run[]) => list.map((r) => r.id);

  it("All shows every run, newest first", () => {
    const { runs, order } = index(run("a", 1), run("b", 2, { pinned: true }), run("c", 3));
    expect(ids(visibleRuns(runs, order, ready(), NO_FILTER))).toEqual(["c", "b", "a"]);
  });

  it("Kept shows only kept runs", () => {
    const { runs, order } = index(run("a", 1, { pinned: true }), run("b", 2), run("c", 3, { pinned: true }));
    expect(ids(visibleRuns(runs, order, ready(), ONLY_KEPT))).toEqual(["c", "a"]);
  });

  it("Kept shows a working run at the top whether or not it is kept, and each run once", () => {
    const { runs, order } = index(
      run("old-kept", 1, { pinned: true }), run("done", 2), run("working", 3, { status: "running" }),
      run("working-kept", 4, { status: "queued", pinned: true }), run("newest-kept", 5, { pinned: true }),
    );
    expect(ids(visibleRuns(runs, order, ready(), ONLY_KEPT))).toEqual(["working-kept", "working", "newest-kept", "old-kept"]);
  });

  it("All does not move working runs: the list is the history", () => {
    const { runs, order } = index(run("a", 1, { status: "running" }), run("b", 2), run("c", 3, { status: "queued" }));
    expect(ids(visibleRuns(runs, order, ready(), NO_FILTER))).toEqual(["c", "b", "a"]);
  });

  it("a view shows nothing older than where it has loaded down to, even when the cache holds it", () => {
    // another filter loaded the oldest kept run; this view has only reached b
    const { runs, order } = index(run("a", 1, { pinned: true }), run("b", 2, { pinned: true }), run("c", 3, { pinned: true }));
    const view = ready({ created_at: at(2), id: "b" }, "b");
    expect(ids(visibleRuns(runs, order, view, ONLY_KEPT))).toEqual(["c", "b"]);
  });

  it("the boundary run itself is shown, and ties on the time are told apart by the id", () => {
    const { runs, order } = index(run("a", 1, { pinned: true }), run("m", 2, { pinned: true }), run("n", 2, { pinned: true }));
    expect(ids(visibleRuns(runs, order, ready({ created_at: at(2), id: "m" }, "m"), ONLY_KEPT))).toEqual(["n", "m"]);
    expect(ids(visibleRuns(runs, order, ready({ created_at: at(2), id: "n" }, "n"), ONLY_KEPT))).toEqual(["n"]);
  });

  it("a view that has loaded everything has no boundary and shows all it matches", () => {
    const { runs, order } = index(run("a", 1, { pinned: true }), run("b", 2));
    expect(ids(visibleRuns(runs, order, ready(null, null), ONLY_KEPT))).toEqual(["a"]);
  });

  it("a run that was un-kept leaves the Kept view, and a kept one joins it, with no other change", () => {
    const base = index(run("a", 1, { pinned: true }), run("b", 2));
    expect(ids(visibleRuns(base.runs, base.order, ready(), ONLY_KEPT))).toEqual(["a"]);
    const changed = { ...base.runs, a: { ...base.runs.a, pinned: false }, b: { ...base.runs.b, pinned: true } } as typeof base.runs;
    expect(ids(visibleRuns(changed, base.order, ready(), ONLY_KEPT))).toEqual(["b"]);
  });

  it("All leaves out a run in the bin, and Deleted shows only the bin, newest first, with no working run put first", () => {
    const { runs, order } = index(run("a", 1), run("b", 2, { deleted_at: at(30) }), run("c", 3, { deleted_at: at(31), pinned: true }), run("w", 4, { status: "running" }));
    expect(ids(visibleRuns(runs, order, ready(), NO_FILTER))).toEqual(["w", "a"]);
    expect(ids(visibleRuns(runs, order, ready(), ONLY_DELETED))).toEqual(["c", "b"]);
    expect(ids(visibleRuns(runs, order, ready(), ONLY_KEPT))).toEqual(["w"]); // c is kept, but it is in the bin
  });

  it("a run that is deleted leaves the history and joins the bin, and a restored one goes back, with no other change", () => {
    const base = index(run("a", 1, { pinned: true }), run("b", 2));
    const binned = { ...base.runs, a: { ...base.runs.a, deleted_at: at(30) } } as typeof base.runs;
    expect(ids(visibleRuns(binned, base.order, ready(), ONLY_KEPT))).toEqual([]);
    expect(ids(visibleRuns(binned, base.order, ready(), ONLY_DELETED))).toEqual(["a"]);
    expect(ids(visibleRuns(base.runs, base.order, ready(), ONLY_KEPT))).toEqual(["a"]);
  });

  it("a view that does not exist yet shows what the cache holds for it, up to nothing", () => {
    const { runs, order } = index(run("a", 1, { pinned: true }));
    expect(ids(visibleRuns(runs, order, undefined, ONLY_KEPT))).toEqual(["a"]);
  });
});

describe("which runs have left the Kept view (DESIGN.md §29.3)", () => {
  const run = (id: string, extra: Partial<Run> = {}) => makeRun({ id, status: "done", pinned: false, ...extra } as never);
  const table = (...list: Run[]) => Object.fromEntries(list.map((r) => [r.id, r]));
  const none = new Set<string>();

  it("watches a run that is working and not kept, and reports nothing yet", () => {
    const result = watchWorking(none, table(run("w", { status: "running" }), run("kept", { pinned: true }), run("old")), ONLY_KEPT);
    expect([...result.watched]).toEqual(["w"]);
    expect(result.left).toEqual([]);
  });

  it("reports a watched run that finished without being kept, once", () => {
    const before = watchWorking(none, table(run("w", { status: "running" })), ONLY_KEPT);
    const after = watchWorking(before.watched, table(run("w", { status: "done" })), ONLY_KEPT);
    expect(after.left.map((r) => r.id)).toEqual(["w"]);
    expect(after.watched.size).toBe(0);
    expect(watchWorking(after.watched, table(run("w", { status: "done" })), ONLY_KEPT).left).toEqual([]); // not again
  });

  it("reports a failed or canceled one too, because it is gone from the view all the same", () => {
    for (const status of ["failed", "canceled"] as const) {
      const watched = new Set(["w"]);
      expect(watchWorking(watched, table(run("w", { status })), ONLY_KEPT).left.map((r) => r.id)).toEqual(["w"]);
    }
  });

  it("does not report one that is still working", () => {
    const result = watchWorking(new Set(["w"]), table(run("w", { status: "queued" })), ONLY_KEPT);
    expect(result.left).toEqual([]);
    expect([...result.watched]).toEqual(["w"]);
  });

  it("does not report one that was kept while it worked, nor one kept as it finished", () => {
    const whileWorking = watchWorking(new Set(["w"]), table(run("w", { status: "running", pinned: true })), ONLY_KEPT);
    expect(whileWorking.left).toEqual([]);
    expect(whileWorking.watched.size).toBe(0); // it belongs to the view now: no longer watched
    const asItFinished = watchWorking(new Set(["w"]), table(run("w", { status: "done", pinned: true })), ONLY_KEPT);
    expect(asItFinished.left).toEqual([]);
  });

  it("does not report one that was deleted", () => {
    const result = watchWorking(new Set(["w"]), table(), ONLY_KEPT);
    expect(result.left).toEqual([]);
    expect(result.watched.size).toBe(0);
  });

  it("watches nothing in the bin", () => {
    const result = watchWorking(new Set(["w"]), table(run("w", { status: "done" }), run("x", { status: "running" })), ONLY_DELETED);
    expect(result.left).toEqual([]);
    expect(result.watched.size).toBe(0);
  });

  it("watches nothing in the unfiltered list, where a finished run does not leave", () => {
    const result = watchWorking(new Set(["w"]), table(run("w", { status: "done" }), run("x", { status: "running" })), NO_FILTER);
    expect(result.left).toEqual([]);
    expect(result.watched.size).toBe(0);
  });

  it("is told apart by what the filter says: with 'not kept' a kept working run is the one that leaves", () => {
    const filter: HistoryFilter = { kept: false, deleted: false };
    const watched = watchWorking(none, table(run("w", { status: "running", pinned: true })), filter).watched;
    expect([...watched]).toEqual(["w"]);
    expect(watchWorking(watched, table(run("w", { status: "done", pinned: true })), filter).left.map((r) => r.id)).toEqual(["w"]);
  });
});
