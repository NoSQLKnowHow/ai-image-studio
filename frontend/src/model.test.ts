import { describe, expect, it } from "vitest";
import { modelAction, modelActionLabel, modelActionTitle, modelAnnouncement } from "./model";
import { STATUS } from "./testdata";
import type { ModelName, WorkerState, WorkerStatus } from "./types";

const worker = (state: WorkerState, extra: Partial<WorkerStatus> = {}): WorkerStatus => ({ ...STATUS.worker, state, detail: null, hint: null, model: "image", ...extra });
const ask = (kind: "load" | "unload", from: WorkerState, model: ModelName = "image", fromModel: ModelName = model) => ({ kind, model, from, fromModel });

describe("which button is offered next to the pill (the Images tab)", () => {
  it("Load model when the model is not loaded, or had a problem", () => {
    expect(modelAction(worker("unloaded"), "image")).toEqual({ kind: "load", model: "image" });
    expect(modelAction(worker("error", { detail: "Not enough free memory" }), "image")).toEqual({ kind: "load", model: "image" });
  });

  it("Unload model when it is loaded and idle", () => {
    expect(modelAction(worker("ready"), "image")).toEqual({ kind: "unload", model: "image" });
  });

  it("neither while it loads, while it generates, or when the server cannot run the model", () => {
    expect(modelAction(worker("loading"), "image")).toBeNull();
    expect(modelAction(worker("busy"), "image")).toBeNull();
    expect(modelAction(worker("unavailable"), "image")).toBeNull();
  });

  it("no Load when the idle timeout is 0 (it would be unloaded the moment it is ready), but Unload is still there", () => {
    expect(modelAction(worker("unloaded", { idle_timeout_min: 0 }), "image")).toBeNull();
    expect(modelAction(worker("error", { idle_timeout_min: 0 }), "image")).toBeNull();
    expect(modelAction(worker("ready", { idle_timeout_min: 0 }), "image")).toEqual({ kind: "unload", model: "image" });
  });

  it("any idle timeout above 0 is enough, even a short one", () => {
    expect(modelAction(worker("unloaded", { idle_timeout_min: 0.5 }), "image")).toEqual({ kind: "load", model: "image" });
  });
});

describe("the same button on the Music tab acts on the music model", () => {
  it("Load music model when nothing is loaded or the last load had a problem", () => {
    expect(modelAction(worker("unloaded"), "music")).toEqual({ kind: "load", model: "music" });
    expect(modelAction(worker("error", { model: "music" }), "music")).toEqual({ kind: "load", model: "music" });
  });

  it("Unload model when the music model is the one loaded and idle", () => {
    expect(modelAction(worker("ready", { model: "music" }), "music")).toEqual({ kind: "unload", model: "music" });
  });

  it("with the image model loaded and idle, the Music tab offers to load the music model, which swaps them", () => {
    expect(modelAction(worker("ready", { model: "image" }), "music")).toEqual({ kind: "load", model: "music" });
    // and the Images tab offers the reverse, rather than an Unload that would unload the other tab's model
    expect(modelAction(worker("ready", { model: "music" }), "image")).toEqual({ kind: "load", model: "image" });
  });

  it("a swap needs an idle timeout above 0 like any Load", () => {
    expect(modelAction(worker("ready", { model: "image", idle_timeout_min: 0 }), "music")).toBeNull();
  });

  it("nothing while the other model works or loads: a swap never happens under a run", () => {
    expect(modelAction(worker("busy", { model: "image" }), "music")).toBeNull();
    expect(modelAction(worker("loading", { model: "image" }), "music")).toBeNull();
    expect(modelAction(worker("busy", { model: "music" }), "image")).toBeNull();
  });

  it("nothing at all when the music model cannot run on this server", () => {
    expect(modelAction(worker("unloaded"), "music", false)).toBeNull();
    expect(modelAction(worker("ready", { model: "image" }), "music", false)).toBeNull();
    expect(modelAction(worker("unloaded"), "image", false)).toEqual({ kind: "load", model: "image" }); // the Images tab is unaffected
  });
});

describe("the words on the button", () => {
  it("the Images tab keeps the plain labels it has had since 1.7; the Music tab names its model", () => {
    expect(modelActionLabel({ kind: "load", model: "image" })).toBe("Load model");
    expect(modelActionLabel({ kind: "load", model: "music" })).toBe("Load music model");
    expect(modelActionLabel({ kind: "unload", model: "image" })).toBe("Unload model");
    expect(modelActionLabel({ kind: "unload", model: "music" })).toBe("Unload model");
  });

  it("the tooltip says which button to press afterwards, and that a swap unloads the other model first", () => {
    expect(modelActionTitle({ kind: "load", model: "image" }, worker("unloaded"))).toMatch(/press Generate/);
    expect(modelActionTitle({ kind: "load", model: "music" }, worker("unloaded"))).toMatch(/press Make music/);
    expect(modelActionTitle({ kind: "load", model: "music" }, worker("unloaded"))).not.toMatch(/unloaded first/);
    expect(modelActionTitle({ kind: "load", model: "music" }, worker("ready", { model: "image" }))).toMatch(/image model is unloaded first/);
  });
});

describe("what is announced after Load or Unload was pressed", () => {
  it("nothing while the worker is still in the state it was in when the button was pressed", () => {
    expect(modelAnnouncement(ask("load", "unloaded"), worker("unloaded"))).toBeNull();
    // a stale problem is not the answer to a request made from the problem state
    expect(modelAnnouncement(ask("load", "error"), worker("error", { detail: "old problem" }))).toBeNull();
    expect(modelAnnouncement(ask("unload", "ready"), worker("ready"))).toBeNull();
  });

  it("a load: loading is not the last word, ready is", () => {
    expect(modelAnnouncement(ask("load", "unloaded"), worker("loading"))).toEqual({ text: "Loading the image model…", settled: false });
    expect(modelAnnouncement(ask("load", "unloaded"), worker("ready"))).toEqual({ text: "Image model ready.", settled: true });
  });

  it("a load that a waiting run picked up straight away is ready too", () => {
    expect(modelAnnouncement(ask("load", "unloaded"), worker("busy"))).toEqual({ text: "Image model ready.", settled: true });
  });

  it("a load that failed says so, with the reason", () => {
    const failed = worker("error", { detail: "Loading the model failed: boom" });
    expect(modelAnnouncement(ask("load", "loading"), failed)).toEqual({ text: "Image model problem. Loading the model failed: boom", settled: true });
    expect(modelAnnouncement(ask("load", "loading"), worker("unavailable"))).toEqual({ text: "Image model problem.", settled: true });
  });

  it("an unload is done when the model is unloaded, and says nothing about anything else", () => {
    expect(modelAnnouncement(ask("unload", "ready"), worker("unloaded"))).toEqual({ text: "Image model unloaded.", settled: true });
    expect(modelAnnouncement(ask("unload", "ready"), worker("busy"))).toBeNull();
  });
});

describe("what is announced when loading one model replaces the other", () => {
  it("loading the music model over a ready image model is not mistaken for 'nothing happened' when the state looks the same", () => {
    const asked = ask("load", "ready", "music", "image");
    expect(modelAnnouncement(asked, worker("ready", { model: "image" }))).toBeNull(); // still the old model
    expect(modelAnnouncement(asked, worker("ready", { model: "music" }))).toEqual({ text: "Music model ready.", settled: true });
    expect(modelAnnouncement(asked, worker("loading", { model: "music" }))).toEqual({ text: "Loading the music model…", settled: false });
  });

  it("a ready state that is still the other model's is not the answer, even if the state changed under it", () => {
    const asked = ask("load", "unloaded", "music", "image");
    expect(modelAnnouncement(asked, worker("ready", { model: "image" }))).toBeNull();
  });

  it("a failed swap names the model that was asked for", () => {
    const failed = worker("error", { model: "music", detail: "Not enough free memory to load the music model" });
    expect(modelAnnouncement(ask("load", "ready", "music", "image"), failed)).toEqual({
      text: "Music model problem. Not enough free memory to load the music model", settled: true,
    });
  });

  it("an unload names its model", () => {
    expect(modelAnnouncement(ask("unload", "ready", "music"), worker("unloaded", { model: "music" }))).toEqual({ text: "Music model unloaded.", settled: true });
  });
});
