import { describe, expect, it } from "vitest";
import { toApiError } from "./api";
import { duration, seedText, timeAgo } from "./format";
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
    const next = reducer(state, { type: "queue", running: "r0", positions: { q1: 1, q2: 2 } });
    expect([next.runs.q1.queue_position, next.runs.q2.queue_position]).toEqual([1, 2]);
    expect(next.status?.queue).toMatchObject({ running: "r0", queued: 2 });
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
    expect(initialState.runsReady).toBe(false);
    const older = reducer(initialState, { type: "runsLoaded", page: { runs: [makeRun()], next_before: null }, append: true });
    expect(older.runsReady).toBe(false);
    expect(withRuns().runsReady).toBe(true);
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
