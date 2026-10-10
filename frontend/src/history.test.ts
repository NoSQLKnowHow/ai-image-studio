import { describe, expect, it } from "vitest";
import cases from "../../backend/tests/filter_cases.json";
import {
  FILTER_KEY, NO_FILTER, NO_PROJECT, ONLY_DELETED, ONLY_KEPT, canBin, filterKey, filterParams, isDefault, matches, parseFilterKey, readFilter, saveFilter, showsWorking,
  visibleRuns, watchWorking, withProject, working,
  type HistoryFilter, type View,
} from "./history";
import type { KeyValueStore } from "./options";
import { makeMusicRun, makeRun } from "./testdata";
import type { Run, RunStatus } from "./types";

// ------------------------------------------------------------------ the table both sides are tested against (DESIGN.md §29.5, criterion 120)
// The server runs each case through its SQL (backend/tests/test_runfilter.py); here the page's `matches` gets the same runs.
// the table's status words, as the page's RunStatus type
const STATUSES: Record<string, RunStatus> = { queued: "queued", running: "running", done: "done", failed: "failed", canceled: "canceled" };

// build a page `Run` from one row of the shared table (music rows become music runs; a deleted row is in the bin)
function tableRun(entry: { id: string; mode: string; status: string; pinned: boolean; deleted: boolean; project: string | null }): Run {
  const extra = { id: entry.id, status: STATUSES[entry.status], pinned: entry.pinned, deleted_at: entry.deleted ? "2026-10-02T00:00:00.000Z" : null, project_id: entry.project };
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

  // the page counts the table the way the server counts it in SQL: both must give the numbers the table lists
  it("the page's counts of the table agree with the server's", () => {
    const count = (kind: "image" | "music", filter: HistoryFilter) =>
      runs.filter((run) => (run.mode === "music") === (kind === "music") && matches(run, filter)).length;
    const of = (kind: "image" | "music", project: string | null = null) => ({
      all: count(kind, withProject(NO_FILTER, project)), kept: count(kind, withProject(ONLY_KEPT, project)), deleted: count(kind, withProject(ONLY_DELETED, project)),
    });
    expect({ image: of("image"), music: of("music") }).toEqual(cases.counts);
    // ... and within each project, and within "no project": the numbers on the filter bar follow the project chosen (DESIGN.md §32.5)
    for (const [project, expected] of Object.entries(cases.counts_by_project)) {
      expect({ image: of("image", project), music: of("music", project) }, `within ${project}`).toEqual(expected);
    }
  });
});

// ------------------------------------------------------------------ projects (DESIGN.md §32)
// The project is the first filter that holds a value: null (any), "none" (runs in no project) or a project's id.
describe("the project of a history filter", () => {
  const ID = "0123456789abcdef0123456789abcdef";
  const OTHER = "fedcba9876543210fedcba9876543210";

  it("is part of the filter's name, after the others, and only a filter with no part set is the default", () => {
    expect(filterKey(withProject(NO_FILTER, ID))).toBe(`project:${ID}`);
    expect(filterKey(withProject(NO_FILTER, NO_PROJECT))).toBe("project:none");
    expect(filterKey(withProject(ONLY_KEPT, ID))).toBe(`kept+project:${ID}`);
    expect(filterKey(withProject(ONLY_DELETED, ID))).toBe(`deleted+project:${ID}`);
    // every project is a view of its own in the store, and so is the same project under another choice
    const names = [NO_FILTER, withProject(NO_FILTER, ID), withProject(NO_FILTER, OTHER), withProject(NO_FILTER, NO_PROJECT), withProject(ONLY_KEPT, ID)].map(filterKey);
    expect(new Set(names).size).toBe(names.length);
    expect(isDefault(withProject(NO_FILTER, ID))).toBe(false);
    expect(isDefault(withProject(NO_FILTER, null))).toBe(true);
  });

  it("changes only the project: Kept and Deleted stay as they were", () => {
    expect(withProject(ONLY_KEPT, ID)).toEqual({ kept: true, deleted: false, project: ID });
    expect(withProject(withProject(ONLY_DELETED, ID), null)).toEqual(ONLY_DELETED);
  });

  it("is sent to the server as the project parameter, and only when there is one", () => {
    expect(filterParams(withProject(NO_FILTER, ID))).toEqual({ project: ID });
    expect(filterParams(withProject(NO_FILTER, NO_PROJECT))).toEqual({ project: "none" });
    expect(filterParams(withProject(ONLY_KEPT, ID))).toEqual({ kept: "true", project: ID });
    expect(filterParams(withProject(ONLY_DELETED, ID))).toEqual({ deleted: "true", project: ID });
  });

  it("is the server's rule for one run: a project's id, or none, or any", () => {
    const filed = makeRun({ project_id: ID, pinned: true });
    const plain = makeRun({ project_id: null });
    expect([matches(filed, NO_FILTER), matches(plain, NO_FILTER)]).toEqual([true, true]); // no project chosen: every run of the history
    expect([matches(filed, withProject(NO_FILTER, ID)), matches(plain, withProject(NO_FILTER, ID))]).toEqual([true, false]);
    expect([matches(filed, withProject(NO_FILTER, OTHER))]).toEqual([false]);
    expect([matches(filed, withProject(NO_FILTER, NO_PROJECT)), matches(plain, withProject(NO_FILTER, NO_PROJECT))]).toEqual([false, true]);
    // a project that does not exist matches nothing, and is not a problem
    expect(matches(filed, withProject(NO_FILTER, "zz"))).toBe(false);
  });

  it("combines with the bin: a run keeps its project in it, and the history does not show it", () => {
    const binned = makeRun({ project_id: ID, pinned: true, deleted_at: "2026-10-02T00:00:00.000Z" });
    expect(matches(binned, withProject(NO_FILTER, ID))).toBe(false);
    expect(matches(binned, withProject(ONLY_DELETED, ID))).toBe(true);
    expect(matches(binned, withProject(ONLY_DELETED, OTHER))).toBe(false);
  });

  it("shows working runs in a project's view too, whether or not they are in it (a job you started stays visible)", () => {
    expect(showsWorking(withProject(NO_FILTER, ID))).toBe(true);
    expect(showsWorking(withProject(ONLY_DELETED, ID))).toBe(false); // nothing works in the bin
  });
});

// What the browser remembers about the project: it is checked and never trusted, and what an older version saved still reads as it did.
describe("the stored form of a filter with a project", () => {
  const ID = "0123456789abcdef0123456789abcdef";
  const memory = (initial: Record<string, string> = {}): KeyValueStore => {
    const saved = { ...initial };
    return { available: true, get: (key) => saved[key] ?? null, set: (key, value) => { saved[key] = value; } };
  };

  it("round-trips every choice of Show combined with a project", () => {
    for (const base of [NO_FILTER, ONLY_KEPT, { kept: false, deleted: false, project: null }, ONLY_DELETED]) {
      for (const project of [ID, NO_PROJECT]) {
        const store = memory();
        saveFilter(withProject(base, project), store);
        expect(readFilter(store)).toEqual(withProject(base, project));
      }
    }
  });

  it("still reads what 1.12 and 1.13 saved", () => {
    expect(readFilter(memory({ [FILTER_KEY]: "kept" }))).toEqual(ONLY_KEPT);
    expect(readFilter(memory({ [FILTER_KEY]: "deleted" }))).toEqual(ONLY_DELETED);
    expect(readFilter(memory({ [FILTER_KEY]: "all" }))).toEqual(NO_FILTER);
  });

  it("falls back to the default for a project that is not an id or none, a part twice, or text it does not know", () => {
    for (const odd of [
      "project:", "project:zz", "project:ABCDEF0123456789ABCDEF0123456789", `project:${ID}x`, "project:NONE", // not an id
      `project:${ID}+project:none`, "kept+kept", "kept+not-kept", // a family twice
      `kept+deleted+project:${ID}`, "any+project:none", // a filter the page cannot offer
      "project", "+", "kept+", "+project:none", // not a name at all
    ]) {
      expect(parseFilterKey(odd), odd).toBeNull();
      expect(readFilter(memory({ [FILTER_KEY]: odd })), odd).toEqual(NO_FILTER);
    }
  });

  it("reads the parts in any order, because only the page writes them in one", () => {
    expect(parseFilterKey(`project:${ID}+kept`)).toEqual({ kept: true, deleted: false, project: ID });
    expect(parseFilterKey(`deleted+project:${ID}`)).toEqual({ kept: null, deleted: true, project: ID });
  });
});

// ------------------------------------------------------------------ the filter itself
describe("a history filter", () => {
  it("is named, and the default is only 'either'", () => {
    // every filter the page can build, and some it cannot (kept in the bin, either): each must have a name of its own, since each gets its
    // own view in the store
    const filters: HistoryFilter[] = [NO_FILTER, ONLY_KEPT, { kept: false, deleted: false, project: null }, ONLY_DELETED, { kept: true, deleted: true, project: null }, { kept: null, deleted: null, project: null }];
    expect(filters.map(filterKey)).toEqual(["all", "kept", "not-kept", "deleted", "kept+deleted", "any"]);
    expect(new Set(filters.map(filterKey)).size).toBe(filters.length); // every filter has a name of its own: a view each
    expect(filters.map(isDefault)).toEqual([true, false, false, false, false, false]);
  });

  it("asks the server for what it is, and for nothing when it is the default", () => {
    expect(filterParams(NO_FILTER)).toEqual({});
    expect(filterParams(ONLY_KEPT)).toEqual({ kept: "true" });
    expect(filterParams({ kept: false, deleted: false, project: null })).toEqual({ kept: "false" });
    expect(filterParams(ONLY_DELETED)).toEqual({ deleted: "true" });
    expect(filterParams({ kept: true, deleted: true, project: null })).toEqual({ kept: "true", deleted: "true" });
  });

  it("is a rule about one thing: whether the run is kept, whatever else it is", () => {
    for (const status of ["queued", "running", "done", "failed", "canceled"] as const) {
      expect(matches(makeRun({ status, pinned: true }), ONLY_KEPT)).toBe(true);
      expect(matches(makeRun({ status, pinned: false }), ONLY_KEPT)).toBe(false);
      expect(matches(makeRun({ status, pinned: false }), { kept: false, deleted: false, project: null })).toBe(true);
      expect(matches(makeRun({ status, pinned: true }), NO_FILTER)).toBe(true);
    }
  });

  // The core rule of the bin: no filter shows a binned run unless it asks for the bin (so a run that is kept and then deleted is not in
  // Kept), and the bin shows only binned runs.
  it("keeps the bin out of every filter that does not ask for it, and the history out of the bin", () => {
    const inBin = makeRun({ pinned: true, deleted_at: "2026-10-02T00:00:00.000Z" });
    const plain = makeRun({ pinned: true });
    expect([NO_FILTER, ONLY_KEPT].map((filter) => matches(inBin, filter))).toEqual([false, false]);
    expect([NO_FILTER, ONLY_KEPT].map((filter) => matches(plain, filter))).toEqual([true, true]);
    expect([matches(inBin, ONLY_DELETED), matches(plain, ONLY_DELETED)]).toEqual([true, false]);
    expect(matches(inBin, { kept: null, deleted: null, project: null })).toBe(true); // either
  });

  it("shows working runs in every filtered view but the bin, where nothing works", () => {
    expect([NO_FILTER, ONLY_KEPT, { kept: false, deleted: false, project: null }, ONLY_DELETED].map(showsWorking)).toEqual([false, true, true, false]);
  });

  it("knows what is working", () => {
    expect(["queued", "running"].map((status) => working({ status: status as RunStatus }))).toEqual([true, true]);
    expect(["done", "failed", "canceled"].map((status) => working({ status: status as RunStatus }))).toEqual([false, false, false]);
  });
});

// The page decides from the run and the setting whether Delete means "to the bin" or "for good". It is a pure function, so it is tested
// here without a browser.
describe("whether Delete moves a run to the bin (DESIGN.md §30.2)", () => {
  const deleted = "2026-10-02T00:00:00.000Z";

  it("does for a run that has finished, whatever way it finished", () => {
    for (const status of ["done", "failed", "canceled"] as const) expect(canBin({ status, deleted_at: null }, 30)).toBe(true);
  });

  it("does not for a run that is waiting or running: it made nothing worth keeping, or is not finished", () => {
    for (const status of ["queued", "running"] as const) expect(canBin({ status, deleted_at: null }, 30)).toBe(false);
  });

  it("does not when there is no bin, or the run is in it already (that is Delete forever)", () => {
    expect(canBin({ status: "done", deleted_at: null }, 0)).toBe(false);
    expect(canBin({ status: "done", deleted_at: deleted }, 30)).toBe(false);
  });

  it("does with a bin of a single day", () => {
    expect(canBin({ status: "done", deleted_at: null }, 1)).toBe(true);
  });
});

describe("what the browser remembers", () => {
  // an in-memory stand-in for the browser's storage, which also records what was saved
  const memory = (initial: Record<string, string> = {}): KeyValueStore & { saved: Record<string, string> } => {
    const saved = { ...initial };
    return { available: true, saved, get: (key) => saved[key] ?? null, set: (key, value) => { saved[key] = value; } };
  };

  it("round-trips", () => {
    for (const filter of [NO_FILTER, ONLY_KEPT, { kept: false, deleted: false, project: null }, ONLY_DELETED]) {
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
  // Helpers. `at(n)`: a time n minutes in. `run`: a finished, un-kept run made at that time. `index`: the store's cache and its newest-first
  // order. `ready`: a view whose first page has arrived. `ids`: read the ids back, so the assertions stay short.
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
  // `none`: nothing is being watched yet; `table`: the runs as the store holds them, by id
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
    const filter: HistoryFilter = { kept: false, deleted: false, project: null };
    const watched = watchWorking(none, table(run("w", { status: "running", pinned: true })), filter).watched;
    expect([...watched]).toEqual(["w"]);
    expect(watchWorking(watched, table(run("w", { status: "done", pinned: true })), filter).left.map((r) => r.id)).toEqual(["w"]);
  });
});
