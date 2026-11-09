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

// ------------------------------------------------------------------ the Speech tab's engine (DESIGN.md §38.2, criterion 292)
// "The speech model" is one engine in one variant, so the button acts on the engine the tab shows, and a different engine that is loaded is swapped for it.
describe("the same button on the Speech tab acts on the engine shown", () => {
  const target = { available: true, engine: "kokoro" };
  const loaded = (state: WorkerState, engine: string | null, extra: Partial<WorkerStatus> = {}) => worker(state, { model: "speech", engine, variant: "default", ...extra });

  it("Load, naming the engine, when nothing is loaded or the last load had a problem", () => {
    expect(modelAction(worker("unloaded"), "speech", true, target)).toEqual({ kind: "load", model: "speech", engine: "kokoro" });
    expect(modelAction(loaded("error", "kokoro"), "speech", true, target)).toEqual({ kind: "load", model: "speech", engine: "kokoro" });
  });

  it("Unload when that engine is loaded and idle", () => {
    expect(modelAction(loaded("ready", "kokoro"), "speech", true, target)).toEqual({ kind: "unload", model: "speech", engine: "kokoro" });
  });

  it("Load, to swap, when another engine is loaded and idle, or another model", () => {
    expect(modelAction(loaded("ready", "piper"), "speech", true, target)).toEqual({ kind: "load", model: "speech", engine: "kokoro" });
    expect(modelAction(worker("ready"), "speech", true, target)).toEqual({ kind: "load", model: "speech", engine: "kokoro" }); // the image model is loaded
  });

  it("neither while a model loads or a reading is made, nor when no engine can run or none is shown", () => {
    expect(modelAction(loaded("loading", "kokoro"), "speech", true, target)).toBeNull();
    expect(modelAction(loaded("busy", "kokoro"), "speech", true, target)).toBeNull();
    expect(modelAction(worker("unloaded"), "speech", true, { available: false, engine: "kokoro" })).toBeNull();
    expect(modelAction(worker("unloaded"), "speech", true, { available: true, engine: null })).toBeNull();
    expect(modelAction(worker("unloaded"), "speech")).toBeNull(); // the page has not said what is shown
  });

  it("no Load when the idle timeout is 0, but Unload is still there", () => {
    expect(modelAction(loaded("unloaded", null, { idle_timeout_min: 0 }), "speech", true, target)).toBeNull();
    expect(modelAction(loaded("ready", "kokoro", { idle_timeout_min: 0 }), "speech", true, target)).toEqual({ kind: "unload", model: "speech", engine: "kokoro" });
  });

  it("the engine is not a thing the other tabs' buttons have", () => {
    expect(modelAction(worker("unloaded"), "image")).toEqual({ kind: "load", model: "image" });
    expect(modelAction(worker("unloaded"), "music")).toEqual({ kind: "load", model: "music" });
  });

  it("says Load speech model, and what it does, and what is unloaded first", () => {
    const action = { kind: "load" as const, model: "speech" as const, engine: "kokoro" };
    expect(modelActionLabel(action)).toBe("Load speech model");
    expect(modelActionTitle(action, worker("unloaded"))).toBe("Load the speech engine shown now, so it is ready when you press Make reading.");
    expect(modelActionTitle(action, loaded("ready", "piper"))).toMatch(/The speech model is unloaded first\.$/);
    expect(modelActionTitle(action, worker("ready"))).toMatch(/The image model is unloaded first\.$/);
    expect(modelActionTitle({ kind: "unload", model: "speech", engine: "kokoro" }, worker("ready"))).toMatch(/^Unload the model now/);
  });

  it("announces the engine that was asked for, and does not take another engine's 'ready' for it", () => {
    const asked = { kind: "load" as const, model: "speech" as const, engine: "kokoro", from: "ready" as WorkerState, fromModel: "speech" as ModelName, fromEngine: "piper" };
    expect(modelAnnouncement(asked, loaded("ready", "piper"))).toBeNull(); // still the old one: no answer yet
    expect(modelAnnouncement(asked, loaded("loading", "kokoro"))).toEqual({ text: "Loading the speech model…", settled: false });
    expect(modelAnnouncement(asked, loaded("ready", "kokoro"))).toEqual({ text: "Speech model ready.", settled: true });
    expect(modelAnnouncement(asked, loaded("error", "kokoro", { detail: "Not enough memory." }))).toEqual({ text: "Speech model problem. Not enough memory.", settled: true });
  });

  it("loading one engine over another that is ready is not 'ready' again until the engine is the new one", () => {
    const asked = { kind: "load" as const, model: "speech" as const, engine: "kokoro", from: "ready" as WorkerState, fromModel: "speech" as ModelName, fromEngine: "piper" };
    expect(modelAnnouncement(asked, loaded("busy", "piper"))).toBeNull();
    expect(modelAnnouncement(asked, loaded("ready", "kokoro"))?.settled).toBe(true);
  });

  it("announces an unload of the speech model", () => {
    const asked = { kind: "unload" as const, model: "speech" as const, engine: "kokoro", from: "ready" as WorkerState, fromModel: "speech" as ModelName, fromEngine: "kokoro" };
    expect(modelAnnouncement(asked, loaded("ready", "kokoro"))).toBeNull();
    expect(modelAnnouncement(asked, worker("unloaded"))).toEqual({ text: "Speech model unloaded.", settled: true });
  });
});
