import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Header } from "./components/Header";
import { STATUS } from "./testdata";
import type { ModelName, Status, WorkerState } from "./types";

// The header on the Speech tab (DESIGN.md §38.2, criterion 292): the Load and Unload button acts on the engine the tab shows, the pill names the speech model, and the
// other tabs' buttons are as they were.

afterEach(cleanup);

const status = (state: WorkerState, model: ModelName = "image", engine: string | null = null): Status => ({ ...STATUS, worker: { ...STATUS.worker, state, model, engine, variant: engine ? "default" : null } });
const header = (s: Status, tab: ModelName, speech?: { available: boolean; engine: string | null }, onModel = vi.fn(async () => true)) =>
  render(<Header status={s} now={Date.now()} busy={false} tab={tab} musicAvailable speech={speech} onModel={onModel} />);

describe("the model button on the Speech tab", () => {
  it("is not there until the tab says which engine it shows (the image model's button is not offered by mistake)", () => {
    header(status("unloaded"), "speech");
    expect(screen.queryByRole("button", { name: /Load/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Unload/ })).toBeNull();
  });

  it("is not there when no engine can run", () => {
    header(status("unloaded"), "speech", { available: false, engine: "fake" });
    expect(screen.queryByRole("button", { name: /Load/ })).toBeNull();
  });

  it("loads the engine shown: named in the request, and said to be the speech model", () => {
    const onModel = vi.fn(async () => true);
    header(status("unloaded"), "speech", { available: true, engine: "fake" }, onModel);
    const button = screen.getByRole("button", { name: "Load speech model" });
    expect(button.getAttribute("data-model")).toBe("speech");
    expect(button.getAttribute("title")).toBe("Load the speech engine shown now, so it is ready when you press Make reading.");
    fireEvent.click(button);
    expect(onModel).toHaveBeenCalledWith({ kind: "load", model: "speech", engine: "fake" });
  });

  it("unloads when that engine is the one that is loaded", () => {
    const onModel = vi.fn(async () => true);
    header(status("ready", "speech", "fake"), "speech", { available: true, engine: "fake" }, onModel);
    fireEvent.click(screen.getByRole("button", { name: "Unload model" }));
    expect(onModel).toHaveBeenCalledWith({ kind: "unload", model: "speech", engine: "fake" });
  });

  it("offers to load the engine shown when another engine is the one that is loaded", () => {
    header(status("ready", "speech", "fake-basic"), "speech", { available: true, engine: "fake" });
    expect(screen.getByRole("button", { name: "Load speech model" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Unload model" })).toBeNull();
  });

  it("says in the title what is unloaded first when a different model is loaded", () => {
    header(status("ready", "image"), "speech", { available: true, engine: "fake" });
    expect(screen.getByRole("button", { name: "Load speech model" }).getAttribute("title")).toMatch(/The image model is unloaded first\.$/);
  });
});

describe("the pill", () => {
  it("names the speech model when that is what is loaded, and says a reading is being made", () => {
    header(status("ready", "speech", "fake"), "speech", { available: true, engine: "fake" });
    expect(screen.getByRole("button", { name: /Speech model ready/ })).toBeTruthy();
    cleanup();
    header(status("busy", "speech", "fake"), "speech", { available: true, engine: "fake" });
    expect(screen.getByRole("button", { name: /Making a reading/ })).toBeTruthy();
  });

  it("is as it was for the other models", () => {
    header(status("busy", "image"), "image");
    expect(screen.getByRole("button", { name: /Generating/ })).toBeTruthy();
    cleanup();
    header(status("busy", "music"), "music");
    expect(screen.getByRole("button", { name: /Making music/ })).toBeTruthy();
  });
});

describe("the other tabs' buttons", () => {
  it("are what they were: Load model on Images, Load music model on Music, whatever the Speech tab is showing", () => {
    header(status("unloaded"), "image", { available: true, engine: "fake" });
    expect(screen.getByRole("button", { name: "Load model" }).getAttribute("data-model")).toBe("image");
    cleanup();
    header(status("unloaded"), "music", { available: true, engine: "fake" });
    expect(screen.getByRole("button", { name: "Load music model" }).getAttribute("data-model")).toBe("music");
  });
});
