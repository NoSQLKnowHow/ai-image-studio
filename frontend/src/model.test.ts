import { describe, expect, it } from "vitest";
import { modelAction, modelAnnouncement } from "./model";
import { STATUS } from "./testdata";
import type { WorkerState, WorkerStatus } from "./types";

const worker = (state: WorkerState, extra: Partial<WorkerStatus> = {}): WorkerStatus => ({ ...STATUS.worker, state, detail: null, hint: null, ...extra });

describe("which button is offered next to the pill", () => {
  it("Load model when the model is not loaded, or had a problem", () => {
    expect(modelAction(worker("unloaded"))).toBe("load");
    expect(modelAction(worker("error", { detail: "Not enough free memory" }))).toBe("load");
  });

  it("Unload model when it is loaded and idle", () => {
    expect(modelAction(worker("ready"))).toBe("unload");
  });

  it("neither while it loads, while it generates, or when the server cannot run the model", () => {
    expect(modelAction(worker("loading"))).toBeNull();
    expect(modelAction(worker("busy"))).toBeNull();
    expect(modelAction(worker("unavailable"))).toBeNull();
  });

  it("no Load when the idle timeout is 0 (it would be unloaded the moment it is ready), but Unload is still there", () => {
    expect(modelAction(worker("unloaded", { idle_timeout_min: 0 }))).toBeNull();
    expect(modelAction(worker("error", { idle_timeout_min: 0 }))).toBeNull();
    expect(modelAction(worker("ready", { idle_timeout_min: 0 }))).toBe("unload");
  });

  it("any idle timeout above 0 is enough, even a short one", () => {
    expect(modelAction(worker("unloaded", { idle_timeout_min: 0.5 }))).toBe("load");
  });
});

describe("what is announced after Load or Unload was pressed", () => {
  it("nothing while the model is still in the state it was in when the button was pressed", () => {
    expect(modelAnnouncement({ kind: "load", from: "unloaded" }, worker("unloaded"))).toBeNull();
    // a stale problem is not the answer to a request made from the problem state
    expect(modelAnnouncement({ kind: "load", from: "error" }, worker("error", { detail: "old problem" }))).toBeNull();
    expect(modelAnnouncement({ kind: "unload", from: "ready" }, worker("ready"))).toBeNull();
  });

  it("a load: loading is not the last word, ready is", () => {
    expect(modelAnnouncement({ kind: "load", from: "unloaded" }, worker("loading"))).toEqual({ text: "Loading the model…", settled: false });
    expect(modelAnnouncement({ kind: "load", from: "unloaded" }, worker("ready"))).toEqual({ text: "Model ready.", settled: true });
  });

  it("a load that a waiting run picked up straight away is ready too", () => {
    expect(modelAnnouncement({ kind: "load", from: "unloaded" }, worker("busy"))).toEqual({ text: "Model ready.", settled: true });
  });

  it("a load that failed says so, with the reason", () => {
    const failed = worker("error", { detail: "Loading the model failed: boom" });
    expect(modelAnnouncement({ kind: "load", from: "loading" }, failed)).toEqual({ text: "Model problem. Loading the model failed: boom", settled: true });
    expect(modelAnnouncement({ kind: "load", from: "loading" }, worker("unavailable"))).toEqual({ text: "Model problem.", settled: true });
  });

  it("an unload is done when the model is unloaded, and says nothing about anything else", () => {
    expect(modelAnnouncement({ kind: "unload", from: "ready" }, worker("unloaded"))).toEqual({ text: "Model unloaded.", settled: true });
    expect(modelAnnouncement({ kind: "unload", from: "ready" }, worker("busy"))).toBeNull();
  });
});
