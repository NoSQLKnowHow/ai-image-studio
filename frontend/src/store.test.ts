import { describe, expect, it } from "vitest";
import { toApiError } from "./api";
import { EXPIRY_WARNING_DAYS, WORKING_IN_KEPT_NOTE, binNote, binnedText, canceledText, duration, emptiedText, expiryText, leftTheViewText, restoredText, seedText, timeAgo, unkeptText } from "./format";
import { NO_FILTER, ONLY_KEPT } from "./history";
import { initialState, reducer, type State } from "./store";
import { STATUS, makeRun } from "./testdata";

const withRuns = (...runs: ReturnType<typeof makeRun>[]): State =>
  reducer({ ...initialState, status: STATUS }, { type: "runsLoaded", page: { runs, next_before: null }, append: false });

describe("run list reducer", () => {
  it("keeps runs newest first, whatever order events arrive in", () => {
    const older = makeRun({ id: "a", created_at: "2026-10-02T10:00:00.000Z" });
    const newer = makeRun({ id: "b", created_at: "2026-10-02T11:00:00.000Z" });
    let state = reducer(initialState, { type: "runUpsert", run: older });
    state = reducer(state, { type: "runUpsert", run: newer });
    expect(state.order).toEqual(["b", "a"]);
    state = reducer(state, { type: "runUpsert", run: { ...older, status: "failed" } });
    expect(state.order).toEqual(["b", "a"]);
    expect(state.runs.a.status).toBe("failed");
  });

  it("applies progress and marks the run as running", () => {
    const run = makeRun({ id: "r", status: "queued" });
    const state = reducer(withRuns(run), { type: "runProgress", id: "r", progress: { image: 1, of: 2, step: 5, steps: 40 } });
    expect(state.runs.r).toMatchObject({ status: "running", progress: { step: 5 } });
  });

  it("ignores progress for runs it doesn't know", () => {
    const state = withRuns(makeRun({ id: "x" }));
    expect(reducer(state, { type: "runProgress", id: "nope", progress: { image: 1, of: 1, step: 1, steps: 1 } })).toBe(state);
  });

  it("removes deleted runs", () => {
    const state = reducer(withRuns(makeRun({ id: "x" }), makeRun({ id: "y" })), { type: "runDeleted", id: "x" });
    expect(state.order).toEqual(["y"]);
    expect(state.runs.x).toBeUndefined();
  });

  it("updates queue positions and the status counters", () => {
    const state = withRuns(makeRun({ id: "q1", status: "queued", queue_position: 2 }), makeRun({ id: "q2", status: "queued", queue_position: 3 }));
    const next = reducer(state, { type: "queue", running: "r0", positions: { q1: 1, q2: 2 }, enlargeWaiting: [] });
    expect([next.runs.q1.queue_position, next.runs.q2.queue_position]).toEqual([1, 2]);
    expect(next.status?.queue).toMatchObject({ running: "r0", queued: 2 });
  });

  it("keeps the images whose Enlarge is waiting for the GPU, and clears them when the queue event says none is (DESIGN.md §28.3)", () => {
    const state = withRuns(makeRun({ id: "r0", status: "running" }));
    const waiting = reducer(state, { type: "queue", running: "r0", positions: {}, enlargeWaiting: ["img1", "img2"] });
    expect(waiting.status?.queue.enlarge_waiting).toEqual(["img1", "img2"]);
    const later = reducer(waiting, { type: "queue", running: null, positions: {}, enlargeWaiting: [] });
    expect(later.status?.queue.enlarge_waiting).toEqual([]);
  });

  it("a refresh drops runs deleted while disconnected but keeps older pages", () => {
    const ancient = makeRun({ id: "old", created_at: "2026-09-01T00:00:00.000Z" });
    const gone = makeRun({ id: "gone", created_at: "2026-10-02T10:30:00.000Z" });
    const kept = makeRun({ id: "kept", created_at: "2026-10-02T10:20:00.000Z" });
    let state = withRuns(gone, kept);
    state = reducer(state, { type: "runsLoaded", page: { runs: [ancient], next_before: null }, append: true });
    state = reducer(state, { type: "runsLoaded", page: { runs: [kept], next_before: "kept" }, append: false });
    expect(state.order).toEqual(["kept", "old"]);
  });

  it("is not ready (so it doesn't claim 'no images yet') until the first snapshot", () => {
    expect(initialState.views.all?.ready).toBeUndefined();
    const older = reducer(initialState, { type: "runsLoaded", page: { runs: [makeRun()], next_before: null }, append: true });
    expect(older.views.all.ready).toBe(false);
    expect(withRuns().views.all.ready).toBe(true);
  });

  it("a snapshot that is the whole history drops older runs it doesn't list", () => {
    const old = makeRun({ id: "old", created_at: "2026-09-01T00:00:00.000Z" });
    const kept = makeRun({ id: "kept", created_at: "2026-10-02T10:20:00.000Z" });
    const state = reducer(withRuns(kept, old), { type: "runsLoaded", page: { runs: [kept], next_before: null }, append: false });
    expect(state.order).toEqual(["kept"]);
  });

  it("connection recovery clears the restart banner", () => {
    let state = reducer(initialState, { type: "serverStopping" });
    expect(state.serverStopping).toBe(true);
    state = reducer(state, { type: "connection", value: "open" });
    expect(state.serverStopping).toBe(false);
  });
});

describe("API errors", () => {
  it("turns FastAPI validation errors into field errors", () => {
    const error = toApiError(422, { detail: [{ loc: ["body", "options", "width"], msg: "Must be a multiple of 32.", type: "value_error" }] });
    expect(error.message).toBe("Must be a multiple of 32.");
    expect(error.fieldErrors).toEqual([{ field: "options.width", message: "Must be a multiple of 32." }]);
  });

  it("keeps the server's message and code", () => {
    const error = toApiError(429, { detail: "The queue is full (10 jobs waiting).", code: "queue_full" });
    expect([error.message, error.code, error.status]).toEqual(["The queue is full (10 jobs waiting).", "queue_full", 429]);
  });

  it("explains an unreachable server", () => {
    expect(toApiError(0, null).message).toBe("Can't reach the studio server.");
  });

  it("keeps the server's hint, when it gives one as text, and only then", () => {
    const refused = toApiError(503, { detail: "The upscaler model file is not there.", code: "upscaler_unavailable", hint: "Download it once." });
    expect([refused.message, refused.status, refused.code, refused.hint]).toEqual(["The upscaler model file is not there.", 503, "upscaler_unavailable", "Download it once."]);
    expect(toApiError(500, { detail: "It failed.", code: "upscale_failed" }).hint).toBeNull();
    expect(toApiError(500, { detail: "It failed.", hint: 42 }).hint).toBeNull();
    expect(toApiError(0, null).hint).toBeNull();
  });
});

describe("formatting", () => {
  it("relative times and durations", () => {
    const now = Date.parse("2026-10-02T12:00:00Z");
    expect(timeAgo("2026-10-02T11:59:40Z", now)).toBe("just now");
    expect(timeAgo("2026-10-02T11:55:00Z", now)).toBe("5 min ago");
    expect(timeAgo("2026-10-01T12:00:00Z", now)).toBe("yesterday");
    expect(duration("2026-10-02T12:00:00Z", "2026-10-02T12:01:05Z")).toBe("1m 05s");
    expect(duration(null, "x")).toBeNull();
  });

  it("seed ranges for batches", () => {
    expect(seedText(makeRun({ options: { ...makeRun().options, seed: 10, num_images: 3 } }))).toBe("seeds 10–12");
  });
});

describe("out-of-order arrivals (snapshots, POST responses and events use different connections)", () => {
  const queued = makeRun({ id: "r", status: "queued", queue_position: 1 });
  const failed = { ...queued, status: "failed" as const, queue_position: null, error: { message: "Not enough free memory", hint: null } };

  it("a late POST response doesn't undo a failure that already arrived as an event", () => {
    let state = reducer(withRuns(), { type: "runUpsert", run: failed });
    state = reducer(state, { type: "runUpsert", run: queued });
    expect(state.runs.r.status).toBe("failed");
  });

  it("a stale snapshot doesn't undo it either", () => {
    const state = reducer(withRuns(failed), { type: "runsLoaded", page: { runs: [queued], next_before: null }, append: false });
    expect(state.runs.r.status).toBe("failed");
  });

  it("a late progress step doesn't drag a finished run back to running", () => {
    const state = reducer(withRuns({ ...queued, status: "done", queue_position: null }),
      { type: "runProgress", id: "r", progress: { image: 1, of: 1, step: 39, steps: 40 } });
    expect(state.runs.r.status).toBe("done");
  });

  it("a running snapshot keeps the latest step it already knows", () => {
    let state = reducer(withRuns({ ...queued, status: "running" }), { type: "runProgress", id: "r", progress: { image: 1, of: 1, step: 7, steps: 40 } });
    state = reducer(state, { type: "runUpsert", run: { ...queued, status: "running", progress: null } });
    expect(state.runs.r.progress?.step).toBe(7);
  });

  it("a deleted run stays deleted, even if a late response mentions it", () => {
    let state = reducer(withRuns(), { type: "runDeleted", id: "r" }); // the event beat the POST response
    state = reducer(state, { type: "runUpsert", run: queued });
    state = reducer(state, { type: "runsLoaded", page: { runs: [queued], next_before: null }, append: true });
    expect(state.runs.r).toBeUndefined();
    expect(state.order).toEqual([]);
  });
});

describe("cancel and Keep", () => {
  const running = makeRun({ id: "r", status: "running", options: { ...makeRun().options, num_images: 4 } });

  it("a queued run canceled by the server stays canceled when a late queued copy turns up", () => {
    const queued = makeRun({ id: "q", status: "queued", queue_position: 1 });
    let state = reducer(withRuns(), { type: "runUpsert", run: { ...queued, status: "canceled", queue_position: null } });
    state = reducer(state, { type: "runUpsert", run: queued });
    expect(state.runs.q.status).toBe("canceled");
  });

  it("'stopping' survives progress steps that are still arriving, and ends when the run does", () => {
    let state = reducer(withRuns(running), { type: "runUpsert", run: { ...running, canceling: true } });
    state = reducer(state, { type: "runProgress", id: "r", progress: { image: 2, of: 4, step: 9, steps: 40 } });
    expect(state.runs.r).toMatchObject({ status: "running", canceling: true, progress: { step: 9 } });
    state = reducer(state, { type: "runUpsert", run: { ...running, status: "canceled", canceling: false } });
    expect(state.runs.r).toMatchObject({ status: "canceled", canceling: false });
  });

  it("the answer to the cancel request can arrive after the run has already stopped", () => {
    let state = reducer(withRuns(running), { type: "runUpsert", run: { ...running, status: "canceled" } });
    state = reducer(state, { type: "runUpsert", run: { ...running, canceling: true } }); // the 202 response, late
    expect(state.runs.r).toMatchObject({ status: "canceled", canceling: false });
  });

  it("a progress step that was in flight doesn't bring a canceled run back to life", () => {
    let state = reducer(withRuns(running), { type: "runUpsert", run: { ...running, status: "canceled" } });
    state = reducer(state, { type: "runProgress", id: "r", progress: { image: 2, of: 4, step: 20, steps: 40 } });
    expect(state.runs.r.status).toBe("canceled");
    expect(state.runs.r.progress).toBeNull();
  });

  it("keeping and un-keeping replace the run in place without reordering the list", () => {
    const a = makeRun({ id: "a", created_at: "2026-10-02T10:00:00.000Z" });
    const b = makeRun({ id: "b", created_at: "2026-10-02T11:00:00.000Z" });
    let state = withRuns(a, b);
    state = reducer(state, { type: "runUpsert", run: { ...a, pinned: true, expires_at: null } });
    expect(state.runs.a.pinned).toBe(true);
    expect(state.order).toEqual(["b", "a"]);
    state = reducer(state, { type: "runUpsert", run: { ...a, pinned: false, expires_at: "2026-11-01T00:00:00.000Z" } });
    expect(state.runs.a).toMatchObject({ pinned: false, expires_at: "2026-11-01T00:00:00.000Z" });
  });

  it("a run expired on the server disappears, and a late copy doesn't bring it back", () => {
    const a = makeRun({ id: "a" });
    let state = reducer(withRuns(a), { type: "runDeleted", id: "a" });
    state = reducer(state, { type: "runUpsert", run: a });
    expect(state.order).toEqual([]);
  });
});

describe("expiry and cancel wording", () => {
  const now = Date.parse("2026-10-02T12:00:00Z");
  const inDays = (days: number) => new Date(now + days * 86_400_000).toISOString();

  it("says nothing until fewer than a week remains", () => {
    expect(EXPIRY_WARNING_DAYS).toBe(7);
    expect(expiryText(null, now)).toBeNull();
    expect(expiryText(inDays(30), now)).toBeNull();
    expect(expiryText(inDays(7), now)).toBeNull();
    expect(expiryText(new Date(now + 7 * 86_400_000 - 1000).toISOString(), now)).toBe("Will be deleted in 6 days"); // just under a week
  });

  it("counts whole days down, never promising more than there is", () => {
    expect(expiryText(inDays(5.9), now)).toBe("Will be deleted in 5 days");
    expect(expiryText(inDays(2.1), now)).toBe("Will be deleted in 2 days");
    expect(expiryText(inDays(1.5), now)).toBe("Will be deleted in 1 day");
  });

  it("gets more urgent in the last day, and still makes sense once it is overdue", () => {
    expect(expiryText(inDays(0.5), now)).toBe("Will be deleted within a day");
    expect(expiryText(inDays(-0.2), now)).toBe("Due to be deleted at the next daily clean-up");
    expect(expiryText("not a date", now)).toBeNull();
  });

  it("says what a canceled run kept", () => {
    const base = makeRun({ status: "canceled", options: { ...makeRun().options, num_images: 4 } });
    expect(canceledText(base)).toBe("Canceled before any image was finished.");
    const image = { id: "i", idx: 0, seed: 1, width: 8, height: 8, has_alpha: false, url: "/u", thumb_url: null, download_url: "/d", can_4k: false, four_k_size: null, can_enlarge: false, enlarge_size: null, four_k: null };
    expect(canceledText({ ...base, images: [image, { ...image, id: "j", idx: 1 }] })).toBe("Canceled. 2 of 4 images finished and kept.");
  });
});

// Each history filter has its own view: has its first page arrived, where does its next page start, and how far has it loaded (see
// history.ts). These tests drive the reducer the way the page does.
describe("one view for each filter (DESIGN.md §29.5)", () => {
  // `at(n)`: a time n minutes in, so that runs can be ordered; `kept(id, n)`: a kept run made at that time
  const at = (n: number) => `2026-10-02T10:${String(n).padStart(2, "0")}:00.000Z`;
  const kept = (id: string, n: number) => makeRun({ id, created_at: at(n), pinned: true });

  it("a filtered first page makes the filtered view ready and leaves the other view alone", () => {
    const state = reducer(withRuns(makeRun({ id: "x" })), { type: "runsLoaded", page: { runs: [kept("k", 5)], next_before: null }, append: false, filter: ONLY_KEPT });
    expect(state.views.kept).toEqual({ ready: true, nextBefore: null, boundary: null });
    expect(state.views.all.ready).toBe(true);
    expect(state.runs.k).toBeDefined();
  });

  it("a page that is not the last records where the view has loaded down to, and the next page moves it", () => {
    let state = reducer(initialState, { type: "runsLoaded", page: { runs: [kept("a", 9), kept("b", 8)], next_before: "b" }, append: false, filter: ONLY_KEPT });
    expect(state.views.kept).toEqual({ ready: true, nextBefore: "b", boundary: { created_at: at(8), id: "b" } });
    state = reducer(state, { type: "runsLoaded", page: { runs: [kept("c", 7)], next_before: null }, append: true, filter: ONLY_KEPT });
    expect(state.views.kept).toEqual({ ready: true, nextBefore: null, boundary: null });
  });

  it("a filtered first page only adds: a run missing from it is not taken to be deleted", () => {
    const start = withRuns(makeRun({ id: "was-kept", created_at: at(6), pinned: false }), makeRun({ id: "other", created_at: at(5) }));
    const state = reducer(start, { type: "runsLoaded", page: { runs: [kept("k", 7)], next_before: null }, append: false, filter: ONLY_KEPT });
    expect(state.order).toEqual(["k", "was-kept", "other"]);
  });

  it("a filtered first page read again keeps the deeper place its view had reached", () => {
    let state = reducer(initialState, { type: "runsLoaded", page: { runs: [kept("a", 9), kept("b", 8)], next_before: "b" }, append: false, filter: ONLY_KEPT });
    state = reducer(state, { type: "runsLoaded", page: { runs: [kept("c", 7), kept("d", 6)], next_before: "d" }, append: true, filter: ONLY_KEPT });
    state = reducer(state, { type: "runsLoaded", page: { runs: [kept("a", 9), kept("b", 8)], next_before: "b" }, append: false, filter: ONLY_KEPT });
    expect(state.views.kept).toMatchObject({ nextBefore: "d", boundary: { id: "d" } });
  });

  // The unfiltered snapshot (the event stream's first page) is the truth for its own window, so a run in that window that is missing
  // was deleted. A kept run older than the window, loaded earlier for the Kept view, must survive it.
  it("the unfiltered snapshot still drops what was deleted in its window, whatever the Kept view loaded", () => {
    const ancientKept = kept("ancient", 1);
    let state = reducer(initialState, { type: "runsLoaded", page: { runs: [ancientKept], next_before: null }, append: false, filter: ONLY_KEPT });
    state = reducer(state, { type: "runUpsert", run: makeRun({ id: "gone", created_at: at(55) }) });
    state = reducer(state, { type: "runsLoaded", page: { runs: [makeRun({ id: "kept-now", created_at: at(50) })], next_before: "kept-now" }, append: false });
    expect(state.order).toEqual(["kept-now", "ancient"]); // "gone" lies inside the page's window and is not in the page; the old kept run is below it
    expect(state.views.all).toMatchObject({ ready: true, nextBefore: "kept-now" });
  });

  it("the unfiltered snapshot keeps the place the All view had reached, when it keeps the older runs", () => {
    const newest = makeRun({ id: "n1", created_at: at(50) });
    let state = reducer(initialState, { type: "runsLoaded", page: { runs: [newest, makeRun({ id: "n2", created_at: at(49) })], next_before: "n2" }, append: false });
    state = reducer(state, { type: "runsLoaded", page: { runs: [makeRun({ id: "o1", created_at: at(20) })], next_before: "o1" }, append: true });
    expect(state.views.all).toMatchObject({ nextBefore: "o1", boundary: { id: "o1" } });
    state = reducer(state, { type: "runsLoaded", page: { runs: [newest, makeRun({ id: "n2", created_at: at(49) })], next_before: "n2" }, append: false });
    expect(state.views.all).toMatchObject({ ready: true, nextBefore: "o1", boundary: { id: "o1" } }); // not back at the first page's end
    expect(state.order).toEqual(["n1", "n2", "o1"]);
  });

  it("the counts are kept as the server gave them", () => {
    const counts = { image: { all: 3, kept: 1, deleted: 1 }, music: { all: 2, kept: 0, deleted: 0 } };
    expect(reducer(initialState, { type: "counts", counts }).counts).toEqual(counts);
  });

  // `countsStale` is a counter the page watches. It goes up when something that changes a count happens (a new run, Keep, un-keep, a
  // delete), and only then, so the page asks the server for fresh counts exactly when it needs to.
  it("the counts are stale after a run is made, deleted, kept or un-kept, and not otherwise", () => {
    let state = withRuns(makeRun({ id: "r", pinned: false }));
    const base = state.countsStale;
    state = reducer(state, { type: "runUpsert", run: makeRun({ id: "new", created_at: "2026-10-02T12:00:00.000Z" }) });
    expect(state.countsStale).toBe(base + 1);
    state = reducer(state, { type: "runUpsert", run: { ...state.runs.r, pinned: true } });
    expect(state.countsStale).toBe(base + 2);
    state = reducer(state, { type: "runUpsert", run: { ...state.runs.r, pinned: true } });
    expect(state.countsStale).toBe(base + 2); // the same again: nothing moved
    state = reducer(state, { type: "runUpsert", run: { ...state.runs.new, prompt: "edited", status: "running" } });
    expect(state.countsStale).toBe(base + 2); // progress is not a change in the counts
    state = reducer(state, { type: "runUpsert", run: { ...state.runs.r, deleted_at: "2026-10-03T00:00:00.000Z" } });
    expect(state.countsStale).toBe(base + 3); // moved to the bin
    state = reducer(state, { type: "runUpsert", run: { ...state.runs.r, deleted_at: "2026-10-03T00:00:00.000Z", purge_at: "2026-11-02T00:00:00.000Z" } });
    expect(state.countsStale).toBe(base + 3); // still there: nothing moved
    state = reducer(state, { type: "runUpsert", run: { ...state.runs.r, deleted_at: null } });
    expect(state.countsStale).toBe(base + 4); // restored
    state = reducer(state, { type: "runDeleted", id: "r" });
    expect(state.countsStale).toBe(base + 5);
    state = reducer(state, { type: "runDeleted", id: "never-loaded" }); // a clean-up of a run this page never held
    expect(state.countsStale).toBe(base + 6);
  });

  it("views are named by their filter", () => {
    expect(Object.keys(reducer(initialState, { type: "runsLoaded", page: { runs: [], next_before: null }, append: false, filter: NO_FILTER }).views)).toEqual(["all"]);
  });
});

// The wording is made by pure functions of the run and of "now", so it is tested with a fixed clock, locale and time zone: the date in a
// toast must not depend on where the tests run.
describe("the words for the Kept view (DESIGN.md §29.3, §29.4)", () => {
  const NOW = Date.parse("2026-10-10T12:00:00.000Z");
  const when = (days: number) => new Date(NOW + days * 86_400_000).toISOString();
  const words = (expires_at: string | null, prompt = "a harbour at dawn") => unkeptText({ prompt, expires_at }, NOW, { locale: "en-GB", timeZone: "UTC" });

  it("names the run, and says when it will be deleted, with the date and the days left", () => {
    expect(words(when(19))).toBe("No longer kept: “a harbour at dawn”. It will be deleted around 29 Oct, in 19 days, unless you Keep it again.");
  });

  it("rounds the days down, so that it never promises more time than there is, and says 1 day once", () => {
    expect(words(when(1.9))).toBe("No longer kept: “a harbour at dawn”. It will be deleted around 12 Oct, in 1 day, unless you Keep it again.");
  });

  it("says 'within a day' when less than a day is left", () => {
    expect(words(when(0.5))).toBe("No longer kept: “a harbour at dawn”. It will be deleted within a day, unless you Keep it again.");
  });

  it("says the next clean-up when the time has passed", () => {
    expect(words(when(-3))).toBe("No longer kept: “a harbour at dawn”. It is past its time, so it will be deleted at the next daily clean-up, unless you Keep it again.");
    expect(words(when(0))).toContain("past its time");
  });

  it("does not promise a date for a run that has none", () => {
    expect(words(null)).toBe("No longer kept: “a harbour at dawn”. It will be deleted when it is old enough, unless you Keep it again.");
  });

  it("shortens a long prompt to one line", () => {
    const text = words(when(5), `a very long description ${"that goes on and on ".repeat(8)}\nwith a second line`);
    const quoted = /^No longer kept: “([^”]*)”\./.exec(text)?.[1] ?? "";
    expect(quoted.startsWith("a very long description that goes on and on")).toBe(true);
    expect(quoted.endsWith("…") && quoted.length <= 48).toBe(true);
    expect(text).not.toContain("\n");
  });

  it("says what became of a run that finished without being kept", () => {
    expect(leftTheViewText({ prompt: "a harbour", status: "done" })).toBe("“a harbour” is done. It is not kept, so it is not in this view.");
    expect(leftTheViewText({ prompt: "a harbour", status: "failed" })).toBe("“a harbour” failed. It is not kept, so it is not in this view.");
    expect(leftTheViewText({ prompt: "a harbour", status: "canceled" })).toBe("“a harbour” was canceled. It is not kept, so it is not in this view.");
  });

  it("has a note for the card that is there only while it works", () => {
    expect(WORKING_IN_KEPT_NOTE).toBe("Shown while it works. It stays in this view only if you Keep it.");
  });
});


describe("the words for the bin (DESIGN.md §30.1, §30.4)", () => {
  const NOW = Date.parse("2026-10-10T12:00:00.000Z");
  const at = (days: number) => new Date(NOW + days * 86_400_000).toISOString();
  const note = (deleted_at: string | null, purge_at: string | null) => binNote({ deleted_at, purge_at }, NOW, { locale: "en-GB", timeZone: "UTC" });

  it("says what happened to a run that was deleted, and for how long it stays", () => {
    expect(binnedText({ prompt: "a harbour at dawn" }, 30)).toBe("Deleted “a harbour at dawn”. It stays in Deleted for 30 days.");
    expect(binnedText({ prompt: "a harbour at dawn" }, 1)).toBe("Deleted “a harbour at dawn”. It stays in Deleted for 1 day.");
  });

  it("says a kept run is still kept after a restore", () => {
    expect(restoredText({ prompt: "a harbour", pinned: true, expires_at: null }, NOW)).toBe("Restored “a harbour”. It is still kept.");
  });

  it("says how long the fresh clock of an un-kept run is, in whole days, never fewer than one", () => {
    expect(restoredText({ prompt: "a harbour", pinned: false, expires_at: at(30) }, NOW)).toBe("Restored “a harbour”. It has a fresh 30 days.");
    expect(restoredText({ prompt: "a harbour", pinned: false, expires_at: at(1) }, NOW)).toBe("Restored “a harbour”. It has a fresh 1 day.");
    expect(restoredText({ prompt: "a harbour", pinned: false, expires_at: at(0.2) }, NOW)).toBe("Restored “a harbour”. It has a fresh 1 day.");
  });

  it("says nothing about a clock that the server did not give", () => {
    expect(restoredText({ prompt: "a harbour", pinned: false, expires_at: null }, NOW)).toBe("Restored “a harbour”.");
  });

  it("says since when a run is in the bin and until when, with the date and the days left", () => {
    expect(note(at(-1), at(29))).toBe("In the bin since 9 Oct. It will be deleted for good around 8 Nov, in 29 days.");
    expect(note(at(-29), at(1.5))).toBe("In the bin since 11 Sept. It will be deleted for good around 12 Oct, in 1 day.");
  });

  it("says 'within a day' and 'at the next daily clean-up' near and after the end", () => {
    expect(note(at(-29.5), at(0.5))).toContain("It will be deleted for good within a day.");
    expect(note(at(-31), at(-1))).toContain("It will be deleted for good at the next daily clean-up.");
    expect(note(at(-30), at(0))).toContain("It will be deleted for good at the next daily clean-up."); // the very moment it is due
  });

  it("says only since when when there is no end (a run that is not in the bin has no line at all)", () => {
    expect(note(at(-2), null)).toBe("In the bin since 8 Oct.");
    expect(note(null, null)).toBeNull();
  });

  it("says how many runs the emptied bin held", () => {
    expect(emptiedText(12)).toBe("Emptied the bin: 12 runs deleted for good.");
    expect(emptiedText(1)).toBe("Emptied the bin: 1 run deleted for good.");
    expect(emptiedText(0)).toBe("The bin was already empty.");
  });
});
