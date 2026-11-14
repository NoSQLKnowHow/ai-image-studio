import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { AudiobookStatus } from "./speechTypes";
import { POLL_MS, useAudiobook } from "./useAudiobook";

// One reading's audiobook (DESIGN.md §44.4): nothing is asked until the button is pressed, then the build is started and followed until it ends, one asking at a time. The
// API is a pair of stand-ins and the clock is the tests', so what the hook asks, and when, is looked at.

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const status = (changes: Partial<AudiobookStatus> = {}): AudiobookStatus => ({
  available: true, reason: null, hint: null, state: "idle", parts_made: 2, parts_total: 2, complete: true, seconds: 10, chapters: 2, kbps: 64, estimated_bytes: 80_000,
  progress: null, part: null, bytes: null, url: null, error: null, ...changes,
});
const settle = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

function setup(first: AudiobookStatus, later: AudiobookStatus[] = []) {
  const api = {
    makeAudiobook: vi.fn(async () => first),
    audiobook: vi.fn(async () => later.shift() ?? first),
  };
  return { api, ...renderHook(() => useAudiobook("r".repeat(32), api, POLL_MS)) };
}

describe("before the button is pressed", () => {
  it("asks nothing, and knows nothing", async () => {
    const t = setup(status());
    await act(settle);
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(t.api.makeAudiobook).not.toHaveBeenCalled();
    expect(t.api.audiobook).not.toHaveBeenCalled();
    expect(t.result.current.status).toBeNull();
    expect(t.result.current.busy).toBe(false);
  });
});

describe("pressing it", () => {
  it("asks the server to make the file, for this reading, and shows what it said", async () => {
    const t = setup(status({ state: "ready", url: "/api/speech/x/audiobook.m4b", bytes: 5000 }));
    act(() => t.result.current.make());
    expect(t.result.current.busy).toBe(true);
    await act(settle);
    expect(t.api.makeAudiobook).toHaveBeenCalledWith("r".repeat(32));
    expect(t.result.current.busy).toBe(false);
    expect(t.result.current.status?.state).toBe("ready");
    expect(t.result.current.problem).toBeNull();
  });

  it("does not ask for a file that is ready again, and asks no more about it", async () => {
    const t = setup(status({ state: "ready", url: "/u", bytes: 1 }));
    act(() => t.result.current.make());
    await act(settle);
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(t.api.audiobook).not.toHaveBeenCalled(); // a file that is there is not followed
  });

  it("is not pressed twice while the first press is on its way", async () => {
    const t = setup(status({ state: "ready", url: "/u" }));
    act(() => { t.result.current.make(); t.result.current.make(); });
    await act(settle);
    expect(t.api.makeAudiobook).toHaveBeenCalledTimes(1);
  });
});

describe("following a build", () => {
  it("asks how far it has got once a second until it is ready, one asking at a time", async () => {
    const t = setup(status({ state: "building", progress: 0.1, part: 1 }), [
      status({ state: "building", progress: 0.5, part: 1 }), status({ state: "building", progress: 0.9, part: 2 }), status({ state: "ready", url: "/u", bytes: 9, progress: 1 }),
    ]);
    act(() => t.result.current.make());
    await act(settle);
    expect(t.result.current.status?.progress).toBe(0.1);
    expect(t.api.audiobook).not.toHaveBeenCalled(); // not before a second has passed
    await act(async () => { vi.advanceTimersByTime(POLL_MS - 1); });
    expect(t.api.audiobook).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(1); });
    await act(settle);
    expect(t.api.audiobook).toHaveBeenCalledTimes(1);
    expect(t.result.current.status?.progress).toBe(0.5);
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await act(settle);
    expect(t.result.current.status?.part).toBe(2);
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await act(settle);
    expect(t.result.current.status?.state).toBe("ready");
    const asked = t.api.audiobook.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(10 * POLL_MS); });
    expect(t.api.audiobook).toHaveBeenCalledTimes(asked); // and then not again
  });

  it("follows a build that waits its turn as well", async () => {
    const t = setup(status({ state: "waiting" }), [status({ state: "building", progress: 0.2, part: 1 })]);
    act(() => t.result.current.make());
    await act(settle);
    expect(t.result.current.status?.state).toBe("waiting");
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await act(settle);
    expect(t.result.current.status?.state).toBe("building");
  });

  it("stops at a failure and keeps what the server said of it", async () => {
    const t = setup(status({ state: "building" }), [status({ state: "failed", error: { message: "ffmpeg took in no audio for 300 seconds, so it was stopped.", hint: "Try again." } })]);
    act(() => t.result.current.make());
    await act(settle);
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await act(settle);
    expect(t.result.current.status?.state).toBe("failed");
    expect(t.result.current.status?.error?.message).toMatch(/no audio for 300 seconds/);
    const asked = t.api.audiobook.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(5 * POLL_MS); });
    expect(t.api.audiobook).toHaveBeenCalledTimes(asked);
  });

  it("can be pressed again after a failure, which asks again", async () => {
    const t = setup(status({ state: "failed", error: { message: "x", hint: null } }));
    act(() => t.result.current.make());
    await act(settle);
    act(() => t.result.current.make());
    await act(settle);
    expect(t.api.makeAudiobook).toHaveBeenCalledTimes(2);
  });

  it("is not pressed again while a build is going", async () => {
    const t = setup(status({ state: "building", progress: 0.1 }));
    act(() => t.result.current.make());
    await act(settle);
    act(() => t.result.current.make());
    await act(settle);
    expect(t.api.makeAudiobook).toHaveBeenCalledTimes(1); // the build is going; the press would only start another
  });

  it("stops asking when the page is left", async () => {
    const t = setup(status({ state: "building" }));
    act(() => t.result.current.make());
    await act(settle);
    t.unmount();
    await act(async () => { vi.advanceTimersByTime(10 * POLL_MS); });
    expect(t.api.audiobook).not.toHaveBeenCalled();
  });
});

describe("when the server says no, or cannot be reached", () => {
  it("shows the server's words and hint, and the button can be pressed again", async () => {
    const t = setup(status());
    t.api.makeAudiobook.mockRejectedValueOnce(new ApiError("The audiobook file needs ffmpeg.", 409, "audiobook_unavailable", [], "The ZIP still works."));
    act(() => t.result.current.make());
    await act(settle);
    expect(t.result.current.problem).toEqual({ message: "The audiobook file needs ffmpeg.", hint: "The ZIP still works." });
    expect(t.result.current.busy).toBe(false);
    expect(t.result.current.status).toBeNull();
    act(() => t.result.current.make());
    await act(settle);
    expect(t.api.makeAudiobook).toHaveBeenCalledTimes(2);
    expect(t.result.current.problem).toBeNull(); // the old reason is gone as soon as it is asked again
  });

  it("says something plain for a failure that is not the server's", async () => {
    const t = setup(status());
    t.api.makeAudiobook.mockRejectedValueOnce(new Error("offline"));
    act(() => t.result.current.make());
    await act(settle);
    expect(t.result.current.problem).toEqual({ message: "The audiobook file could not be asked for.", hint: null });
  });

  it("says so when it cannot ask how a build is going, and the build is not lost: pressing again asks again", async () => {
    const t = setup(status({ state: "building" }));
    act(() => t.result.current.make());
    await act(settle);
    t.api.audiobook.mockRejectedValueOnce(new ApiError("Can't reach the studio server.", 0));
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await act(settle);
    expect(t.result.current.problem?.message).toBe("Can't reach the studio server.");
  });
});
