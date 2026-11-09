import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReadingPieceEvent, SpeechJob } from "./speechTypes";
import { SPEECH_CONTRACT } from "./testdata";
import { useEventStream } from "./useEvents";

// The two events the Speech tab hears (DESIGN.md §38.4, §38.7): `speech.job` for a sample or a design, `reading.piece` for a piece of a reading. They are not runs,
// so they do not go through the store; the page that is waiting for them takes them from the handlers. A stand-in for EventSource lets the test send them.

class FakeSource {
  static last: FakeSource | null = null;
  readyState = 1;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();
  constructor(readonly url: string) {
    FakeSource.last = this;
  }
  addEventListener(name: string, fn: (event: MessageEvent<string>) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  close() {
    this.readyState = 2;
  }
  send(name: string, data: unknown) {
    for (const fn of this.listeners.get(name) ?? []) fn(new MessageEvent(name, { data: JSON.stringify(data) }));
  }
}

beforeEach(() => {
  FakeSource.last = null;
  vi.stubGlobal("EventSource", Object.assign(FakeSource, { CONNECTING: 0, OPEN: 1, CLOSED: 2 }));
});
afterEach(() => vi.unstubAllGlobals());

describe("events of the Speech tab", () => {
  it("hands a short job to the page that waits for it, and does not touch the store", () => {
    const dispatch = vi.fn();
    const onSpeechJob = vi.fn();
    renderHook(() => useEventStream(dispatch, { onCapabilitiesChanged: vi.fn(), onSpeechJob }));
    const job = SPEECH_CONTRACT.design_job as unknown as SpeechJob;
    FakeSource.last?.send("speech.job", job);
    expect(onSpeechJob).toHaveBeenCalledExactlyOnceWith(job);
    expect(dispatch).not.toHaveBeenCalled(); // a sample or a design is not a run: no list has it
  });

  it("hands a finished piece to the page that is playing the part", () => {
    const dispatch = vi.fn();
    const onReadingPiece = vi.fn();
    renderHook(() => useEventStream(dispatch, { onCapabilitiesChanged: vi.fn(), onReadingPiece }));
    const piece: ReadingPieceEvent = { run_id: "abc", part: 1, idx: 4, frames: 24000 };
    FakeSource.last?.send("reading.piece", piece);
    expect(onReadingPiece).toHaveBeenCalledExactlyOnceWith(piece);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("is quiet when no page is listening for them (the other tabs)", () => {
    expect(() => {
      renderHook(() => useEventStream(vi.fn(), { onCapabilitiesChanged: vi.fn() }));
      FakeSource.last?.send("speech.job", SPEECH_CONTRACT.design_job);
      FakeSource.last?.send("reading.piece", { run_id: "abc", part: 0, idx: 0, frames: 1 });
    }).not.toThrow();
  });

  it("still passes a reading, which is a run, through the store as every run is", () => {
    const dispatch = vi.fn();
    renderHook(() => useEventStream(dispatch, { onCapabilitiesChanged: vi.fn() }));
    FakeSource.last?.send("run.updated", SPEECH_CONTRACT.reading_run);
    expect(dispatch).toHaveBeenCalledWith({ type: "runUpsert", run: SPEECH_CONTRACT.reading_run });
  });
});
