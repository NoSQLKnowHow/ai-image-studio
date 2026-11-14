import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { builderApi } from "./builderApi";
import { NO_FILTER } from "./history";
import { CAPS, makeRun } from "./testdata";
import { useAnnotationActions } from "./useAnnotationActions";

// How the page reaches the server for tags and notes (DESIGN.md §45.8): the run that comes back goes into the store, a run that stops matching the view because of the change leaves it
// with a toast that can undo it, the list of tags in use is remembered for a few seconds and forgotten after a change, and Suggest's reachability is asked sparingly.

afterEach(() => vi.restoreAllMocks());
beforeEach(() => vi.useRealTimers());

function setup(filter = NO_FILTER, caps = CAPS) {
  const dispatch = vi.fn();
  const push = vi.fn();
  const refocus = { current: false };
  const hook = renderHook(() => useAnnotationActions({ caps, filter, dispatch, push, refocus }));
  return { dispatch, push, refocus, actions: () => hook.result.current };
}

describe("changing tags and notes", () => {
  it("puts the run the server answers into the store", async () => {
    const run = makeRun({ id: "r1" });
    const after = { ...run, tags: ["fox"] };
    vi.spyOn(api, "putRunTags").mockResolvedValue(after);
    const { actions, dispatch, push } = setup();
    expect(await actions().setTags(run, ["fox"])).toBe(after);
    expect(api.putRunTags).toHaveBeenCalledWith("r1", ["fox"]);
    expect(dispatch).toHaveBeenCalledWith({ type: "runUpsert", run: after });
    expect(push).not.toHaveBeenCalled(); // still in the view: nothing to say
  });

  it("takes a run out of a search it no longer matches, with a toast, focus for the filter bar, and an Undo that sets the old tags back", async () => {
    const run = makeRun({ id: "r1", prompt: "a lighthouse", tags: ["fox"] });
    const after = { ...run, tags: [] };
    const put = vi.spyOn(api, "putRunTags").mockResolvedValueOnce(after).mockResolvedValueOnce(run);
    const { actions, push, refocus, dispatch } = setup({ ...NO_FILTER, q: "fox" });
    await actions().setTags(run, []);
    expect(refocus.current).toBe(true);
    expect(push).toHaveBeenCalledTimes(1);
    const [kind, text, button] = push.mock.calls[0];
    expect(kind).toBe("info");
    expect(text).toBe("“a lighthouse” no longer matches this view, so it is not in the list.");
    expect(button.label).toBe("Undo");
    button.run();
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put).toHaveBeenLastCalledWith("r1", ["fox"]); // Undo sets back the tags it had
    await vi.waitFor(() => expect(dispatch).toHaveBeenLastCalledWith({ type: "runUpsert", run }));
  });

  it("does not say it for a note that leaves the run in the view, or in a view with no filter", async () => {
    const run = makeRun({ id: "r1", prompt: "a fox" });
    vi.spyOn(api, "putRunNotes").mockResolvedValue({ ...run, notes: "n" });
    const searched = setup({ ...NO_FILTER, q: "fox" });
    await searched.actions().setNotes(run, "n");
    expect(searched.push).not.toHaveBeenCalled();
    expect(searched.refocus.current).toBe(false);
  });

  it("takes a run out of a view when removing its note makes it not match, and Undo sets the note back", async () => {
    const run = makeRun({ id: "r1", prompt: "a lighthouse", notes: "poster" });
    const put = vi.spyOn(api, "putRunNotes").mockResolvedValueOnce({ ...run, notes: "" }).mockResolvedValueOnce(run);
    const { actions, push } = setup({ ...NO_FILTER, q: "poster" });
    await actions().setNotes(run, "");
    push.mock.calls[0][2].run();
    await vi.waitFor(() => expect(put).toHaveBeenLastCalledWith("r1", "poster"));
  });

  it("lets a refusal through for the editor to say", async () => {
    vi.spyOn(api, "putRunTags").mockRejectedValue(new Error("in the bin"));
    const { actions, dispatch } = setup();
    await expect(actions().setTags(makeRun(), ["a"])).rejects.toThrow("in the bin");
    expect(dispatch).not.toHaveBeenCalled();
  });
});

describe("the tags in use", () => {
  it("are remembered for a few seconds and asked for again after a change", async () => {
    const list = vi.spyOn(api, "listTags").mockResolvedValue([{ tag: "fox", count: 1 }]);
    vi.spyOn(api, "putRunTags").mockImplementation(async (id, tags) => ({ ...makeRun({ id }), tags }));
    const { actions } = setup();
    await actions().knownTags();
    await actions().knownTags();
    expect(list).toHaveBeenCalledTimes(1);
    await actions().setTags(makeRun(), ["x"]);
    await actions().knownTags();
    expect(list).toHaveBeenCalledTimes(2); // forgotten by the change
  });

  it("are asked for again once the few seconds have gone", async () => {
    vi.useFakeTimers();
    const list = vi.spyOn(api, "listTags").mockResolvedValue([]);
    const { actions } = setup();
    await actions().knownTags();
    vi.advanceTimersByTime(5001);
    await actions().knownTags();
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe("what Suggest says about itself", () => {
  it("takes the capabilities' words, and says so when a server of before 1.26 has none", () => {
    const off = { ...CAPS, annotations: { search: "words" as const, suggest: { available: false, reason: "Suggest is switched off." } } };
    expect(setup(NO_FILTER, off).actions().suggest).toEqual({ available: false, reason: "Suggest is switched off." });
    const old = { ...CAPS, annotations: undefined };
    expect(setup(NO_FILTER, old).actions().suggest.available).toBe(false);
  });

  it("asks whether Ollama can be reached, remembers the answer, and has none to give when it is not set up or cannot be asked", async () => {
    const status = vi.spyOn(builderApi, "status").mockResolvedValue({ configured: true, reachable: false, model: "m", vision: false, think: "on", message: "Can't reach the Ollama server.", hint: "Check it." });
    const { actions } = setup();
    expect(await actions().reachability()).toEqual({ reachable: false, message: "Can't reach the Ollama server.", hint: "Check it." });
    await actions().reachability();
    expect(status).toHaveBeenCalledTimes(1);
    status.mockResolvedValue({ configured: false, reachable: false, model: "", vision: false, think: "on", message: "x", hint: null });
    const second = setup();
    expect(await second.actions().reachability()).toBeNull(); // not set up is the capabilities' reason, not an unreachable server
    status.mockRejectedValue(new Error("down"));
    const third = setup();
    expect(await third.actions().reachability()).toBeNull();
  });

  it("passes the suggestions through and the server's refusal on", async () => {
    vi.spyOn(api, "suggestTags").mockResolvedValueOnce(["a", "b"]).mockRejectedValueOnce(new Error("no Ollama"));
    const { actions } = setup();
    expect(await actions().suggestTags(makeRun({ id: "r" }))).toEqual(["a", "b"]);
    await expect(actions().suggestTags(makeRun())).rejects.toThrow("no Ollama");
  });
});
