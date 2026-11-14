import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { PronunciationEntry } from "./speechTypes";
import { isProblem, usePronunciations } from "./usePronunciations";

// The pronunciation list the page holds (DESIGN.md §44.3): read when the tab can be used, kept in step with each change, and never showing an entry the server did not accept.
// The API is a table of stand-ins, so what the hook asks is looked at and nothing leaves the test.

afterEach(cleanup);

const entry = (id: string, word: string, say = "x", match_case = false): PronunciationEntry => ({ id, word, say, match_case, created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z" });
const settle = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

function setup(initial: PronunciationEntry[] = [], enabled = true) {
  const api = {
    pronunciations: vi.fn(async () => ({ entries: initial })),
    addPronunciation: vi.fn(async (body: { word: string; say: string; match_case?: boolean }) => entry("new", body.word, body.say, !!body.match_case)),
    changePronunciation: vi.fn(async (id: string, change: { word?: string; say?: string }) => ({ ...entry(id, change.word ?? "w", change.say ?? "s") })),
    deletePronunciation: vi.fn(async () => undefined),
    tryPronunciation: vi.fn(async () => ({ spoken: "win", changed: true, used: [{ word: "Nguyen", say: "win", count: 1 }] })),
  };
  const hook = renderHook(({ on }) => usePronunciations(on, api), { initialProps: { on: enabled } });
  return { api, ...hook };
}

describe("reading the list", () => {
  it("is read once when the tab can be used, and shown by word ignoring case", async () => {
    const t = setup([entry("1", "zebra"), entry("2", "Apple"), entry("3", "mango")]);
    expect(t.result.current.loaded).toBe(false);
    await act(settle);
    expect(t.result.current.loaded).toBe(true);
    expect(t.result.current.entries.map((e) => e.word)).toEqual(["Apple", "mango", "zebra"]);
    expect(t.api.pronunciations).toHaveBeenCalledTimes(1);
  });

  it("is not read while the tab cannot be used", async () => {
    const t = setup([entry("1", "a")], false);
    await act(settle);
    expect(t.api.pronunciations).not.toHaveBeenCalled();
    t.rerender({ on: true });
    await act(settle);
    expect(t.api.pronunciations).toHaveBeenCalledTimes(1);
  });

  it("says when it could not be read, with the server's words, and reads again when asked", async () => {
    const t = setup();
    t.api.pronunciations.mockRejectedValueOnce(new ApiError("The database is busy.", 503, null, [], "Try again."));
    t.rerender({ on: false });
    t.rerender({ on: true });
    await act(settle);
    expect(t.result.current.problem).toEqual({ message: "The database is busy.", hint: "Try again.", field: null });
    act(() => t.result.current.reload());
    await act(settle);
    expect(t.result.current.problem).toBeNull();
    expect(t.result.current.loaded).toBe(true);
  });
});

describe("adding", () => {
  it("shows the entry the server made, in its place, and returns no problem", async () => {
    const t = setup([entry("1", "zebra")]);
    await act(settle);
    let outcome: unknown = "unset";
    await act(async () => { outcome = await t.result.current.add({ word: "Apple", say: "ap", match_case: true }); });
    expect(outcome).toBeNull();
    expect(t.api.addPronunciation).toHaveBeenCalledWith({ word: "Apple", say: "ap", match_case: true });
    expect(t.result.current.entries.map((e) => e.word)).toEqual(["Apple", "zebra"]);
  });

  it("shows nothing of an entry the server refused, and returns the server's words and the box they are about", async () => {
    const t = setup();
    await act(settle);
    t.api.addPronunciation.mockRejectedValueOnce(new ApiError("“Nguyen” is already on the list.", 409, "word_taken", [{ field: "word", message: "“Nguyen” is already on the list." }]));
    let outcome: unknown;
    await act(async () => { outcome = await t.result.current.add({ word: "Nguyen", say: "win" }); });
    expect(outcome).toEqual({ message: "“Nguyen” is already on the list.", hint: null, field: "word" });
    expect(t.result.current.entries).toEqual([]);
  });

  it("says something plain when it could not even reach the server", async () => {
    const t = setup();
    await act(settle);
    t.api.addPronunciation.mockRejectedValueOnce(new Error("offline"));
    let outcome: unknown;
    await act(async () => { outcome = await t.result.current.add({ word: "a", say: "b" }); });
    expect(outcome).toEqual({ message: "The entry could not be added.", hint: null, field: null });
  });
});

describe("changing", () => {
  it("shows the entry as the server answered, in its new place", async () => {
    const t = setup([entry("1", "Apple"), entry("2", "mango")]);
    await act(settle);
    await act(async () => { expect(await t.result.current.change("1", { word: "zebra", say: "zee" })).toBeNull(); });
    expect(t.api.changePronunciation).toHaveBeenCalledWith("1", { word: "zebra", say: "zee" });
    expect(t.result.current.entries.map((e) => e.word)).toEqual(["mango", "zebra"]);
  });

  it("leaves the entry as it was when the server refuses, and says why", async () => {
    const t = setup([entry("1", "Apple")]);
    await act(settle);
    t.api.changePronunciation.mockRejectedValueOnce(new ApiError("That is the word itself.", 422, "same", [{ field: "say", message: "That is the word itself." }]));
    let outcome: unknown;
    await act(async () => { outcome = await t.result.current.change("1", { say: "Apple" }); });
    expect((outcome as { field: string }).field).toBe("say");
    expect(t.result.current.entries[0].word).toBe("Apple");
  });
});

describe("deleting", () => {
  it("takes the entry off the list once the server has", async () => {
    const t = setup([entry("1", "a"), entry("2", "b")]);
    await act(settle);
    await act(async () => { expect(await t.result.current.remove("1")).toBeNull(); });
    expect(t.api.deletePronunciation).toHaveBeenCalledWith("1");
    expect(t.result.current.entries.map((e) => e.id)).toEqual(["2"]);
  });

  it("takes it off the list too when the server says it was not there (deleted on another screen)", async () => {
    const t = setup([entry("1", "a")]);
    await act(settle);
    t.api.deletePronunciation.mockRejectedValueOnce(new ApiError("That entry is not on the list.", 404, "not_found"));
    await act(async () => { expect(await t.result.current.remove("1")).toBeNull(); });
    expect(t.result.current.entries).toEqual([]);
  });

  it("keeps the entry, and says why, when the delete failed for another reason", async () => {
    const t = setup([entry("1", "a")]);
    await act(settle);
    t.api.deletePronunciation.mockRejectedValueOnce(new ApiError("The database is busy.", 503));
    let outcome: unknown;
    await act(async () => { outcome = await t.result.current.remove("1"); });
    expect((outcome as { message: string }).message).toBe("The database is busy.");
    expect(t.result.current.entries).toHaveLength(1);
  });
});

describe("trying a sentence", () => {
  it("asks the server and returns what it said", async () => {
    const t = setup();
    await act(settle);
    let answer: unknown;
    await act(async () => { answer = await t.result.current.tryIt("Nguyen"); });
    expect(t.api.tryPronunciation).toHaveBeenCalledWith("Nguyen");
    expect(isProblem(answer as never)).toBe(false);
    expect((answer as { spoken: string }).spoken).toBe("win");
  });

  it("returns the server's refusal as a problem", async () => {
    const t = setup();
    await act(settle);
    t.api.tryPronunciation.mockRejectedValueOnce(new ApiError("Too much text.", 422));
    let answer: unknown;
    await act(async () => { answer = await t.result.current.tryIt("x"); });
    expect(isProblem(answer as never)).toBe(true);
  });
});
