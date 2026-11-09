import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { createEmitter } from "./emitter";
import type { Passage, SavedVoice, SpeechJob, StagedRecording } from "./speechTypes";
import { SPEECH_CONTRACT } from "./testdata";
import { useClipPlayer } from "./useClipPlayer";
import { usePassage } from "./usePassage";
import { useRecordedVoice } from "./useRecordedVoice";
import { useVoiceDesign } from "./useVoiceDesign";

// The hooks behind the dialogs for a voice of one's own (DESIGN.md §38.4): playing a clip, the passage, designing a voice, adding one by a recording. Each is tried with
// the answers of the server's contract and with the ways those go wrong: late, out of order, refused, given up.

const PASSAGE = SPEECH_CONTRACT.passage as unknown as Passage;
const DESIGN = SPEECH_CONTRACT.design_job as unknown as SpeechJob;
const STAGED = SPEECH_CONTRACT.staged_recording as unknown as StagedRecording;
const DESIGNED = SPEECH_CONTRACT.saved_voice_designed as unknown as SavedVoice;
const CLONED = SPEECH_CONTRACT.saved_voice_cloned as unknown as SavedVoice;

const settle = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};
const designJob = (status: SpeechJob["status"], extra: Partial<SpeechJob> = {}): SpeechJob => ({ ...DESIGN, status, ...extra });

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ------------------------------------------------------------------ one clip at a time
describe("playing a clip", () => {
  function setup() {
    const element = document.createElement("audio");
    const play = vi.fn(async () => undefined);
    const pause = vi.fn();
    Object.assign(element, { play, pause });
    const ref = { current: element }; // one ref for every render, as a component's useRef is
    const view = renderHook(() => useClipPlayer(ref));
    return { element, play, pause, ...view };
  }

  it("plays the clip it is given and says which is playing", async () => {
    const t = setup();
    act(() => t.result.current.play("a", "/clips/a.wav"));
    await act(settle);
    expect(t.element.getAttribute("src")).toBe("/clips/a.wav");
    expect(t.play).toHaveBeenCalledTimes(1);
    expect(t.result.current.playing).toBe("a");
  });

  it("stops the clip that is playing when it is pressed again, and lets go of its file", async () => {
    const t = setup();
    act(() => t.result.current.play("a", "/clips/a.wav"));
    act(() => t.result.current.play("a", "/clips/a.wav"));
    expect(t.pause).toHaveBeenCalled();
    expect(t.element.hasAttribute("src")).toBe(false);
    expect(t.result.current.playing).toBeNull();
    expect(t.play).toHaveBeenCalledTimes(1);
  });

  it("moves to another clip when that is pressed", async () => {
    const t = setup();
    act(() => t.result.current.play("a", "/clips/a.wav"));
    act(() => t.result.current.play("b", "/clips/b.wav"));
    await act(settle);
    expect(t.element.getAttribute("src")).toBe("/clips/b.wav");
    expect(t.result.current.playing).toBe("b");
    expect(t.play).toHaveBeenCalledTimes(2);
  });

  it("is idle when the clip ends", async () => {
    const t = setup();
    act(() => t.result.current.play("a", "/clips/a.wav"));
    act(() => void t.element.dispatchEvent(new Event("ended")));
    expect(t.result.current.playing).toBeNull();
  });

  it("says when the file could not be played, but not when taking its source away to stop", async () => {
    const t = setup();
    act(() => t.result.current.play("a", "/clips/a.wav"));
    act(() => void t.element.dispatchEvent(new Event("error")));
    expect(t.result.current.problem).toBe("The clip could not be played.");
    expect(t.result.current.playing).toBeNull();
    act(() => t.result.current.play("a", "/clips/a.wav")); // pressing again clears it
    expect(t.result.current.problem).toBeNull();
    act(() => t.result.current.stop());
    act(() => void t.element.dispatchEvent(new Event("error"))); // (no source now: stopping is not a failure)
    expect(t.result.current.problem).toBeNull();
  });

  it("says when the browser refuses to play it, but not when the play was only interrupted", async () => {
    const t = setup();
    t.play.mockRejectedValueOnce(new DOMException("blocked", "NotAllowedError"));
    act(() => t.result.current.play("a", "/clips/a.wav"));
    await act(settle);
    expect(t.result.current).toMatchObject({ playing: null, problem: "The clip could not be played." });
    t.play.mockRejectedValueOnce(new DOMException("interrupted", "AbortError"));
    act(() => t.result.current.play("b", "/clips/b.wav"));
    await act(settle);
    expect(t.result.current).toMatchObject({ playing: "b", problem: null });
  });

  it("takes no notice of the refusal of a clip that was stopped or replaced meanwhile", async () => {
    const t = setup();
    let refuse: (error: unknown) => void = () => undefined;
    t.play.mockImplementationOnce(() => new Promise<undefined>((_, reject) => (refuse = reject)));
    act(() => t.result.current.play("a", "/clips/a.wav"));
    act(() => t.result.current.play("b", "/clips/b.wav"));
    refuse(new DOMException("blocked", "NotAllowedError"));
    await act(settle);
    expect(t.result.current).toMatchObject({ playing: "b", problem: null });
  });

  it("does nothing without an element to play in", () => {
    const view = renderHook(() => useClipPlayer({ current: null }));
    act(() => view.result.current.play("a", "/clips/a.wav"));
    expect(view.result.current.playing).toBeNull();
  });

  it("stops the sound when it is left", () => {
    const t = setup();
    act(() => t.result.current.play("a", "/clips/a.wav"));
    t.pause.mockClear();
    t.unmount();
    expect(t.pause).toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------ the passage
describe("the passage and the limits", () => {
  it("is not asked for until it is wanted", async () => {
    const api = { passage: vi.fn(async () => PASSAGE) };
    const view = renderHook(({ wanted }) => usePassage(wanted, api), { initialProps: { wanted: false } });
    await act(settle);
    expect(api.passage).not.toHaveBeenCalled();
    expect(view.result.current.passage).toBeNull();
    view.rerender({ wanted: true });
    await act(settle);
    expect(api.passage).toHaveBeenCalledTimes(1);
    expect(view.result.current.passage).toEqual(PASSAGE);
    expect(view.result.current.loading).toBe(false);
  });

  it("says it is loading, and is asked for only once however often it is wanted", async () => {
    let release: (p: Passage) => void = () => undefined;
    const api = { passage: vi.fn(() => new Promise<Passage>((resolve) => (release = resolve))) };
    const view = renderHook(({ wanted }) => usePassage(wanted, api), { initialProps: { wanted: true } });
    expect(view.result.current.loading).toBe(true);
    release(PASSAGE);
    await act(settle);
    view.rerender({ wanted: false });
    view.rerender({ wanted: true });
    await act(settle);
    expect(api.passage).toHaveBeenCalledTimes(1);
  });

  it("says why when it could not be had, and asks again when told to", async () => {
    const api = { passage: vi.fn(async () => PASSAGE) };
    api.passage.mockRejectedValueOnce(new ApiError("The studio is busy.", 503, "busy", [], "Try again in a moment."));
    const view = renderHook(() => usePassage(true, api));
    await act(settle);
    expect(view.result.current.problem).toEqual({ message: "The studio is busy.", hint: "Try again in a moment." });
    expect(view.result.current.loading).toBe(false);
    act(() => view.result.current.retry());
    await act(settle);
    expect(view.result.current.passage).toEqual(PASSAGE);
    expect(view.result.current.problem).toBeNull();
  });

  it("says so in its own words for a failure that is not the server's", async () => {
    const api = { passage: vi.fn(async () => { throw new Error("offline"); }) };
    const view = renderHook(() => usePassage(true, api as never));
    await act(settle);
    expect(view.result.current.problem).toEqual({ message: "The passage could not be read.", hint: null });
  });

  it("takes no answer once it is left", async () => {
    let release: (p: Passage) => void = () => undefined;
    const api = { passage: vi.fn(() => new Promise<Passage>((resolve) => (release = resolve))) };
    const view = renderHook(() => usePassage(true, api));
    view.unmount();
    release(PASSAGE);
    await settle();
    expect(view.result.current.passage).toBeNull(); // (the last it knew)
  });
});

// ------------------------------------------------------------------ designing a voice
describe("designing a voice", () => {
  function setup(first: SpeechJob = designJob("queued")) {
    const jobs = createEmitter<SpeechJob>();
    const api = {
      design: vi.fn(async (_description: string, _engine?: string) => first),
      job: vi.fn(async (_id: string) => designJob("queued")),
      saveVoice: vi.fn(async (_body: unknown) => DESIGNED),
    };
    const view = renderHook(() => useVoiceDesign({ engine: "fake", jobs, api, pollMs: 2000 }));
    return { jobs, api, ...view };
  }

  it("asks for three samples of the description as it is trimmed, for the engine, and is working", async () => {
    const t = setup();
    expect(t.result.current.phase).toBe("idle");
    act(() => t.result.current.start("  a warm voice  "));
    expect(t.result.current.phase).toBe("working");
    await act(settle);
    expect(t.api.design).toHaveBeenCalledWith("a warm voice", "fake");
  });

  it("has the three clips, in order, when the event says the design is done", async () => {
    const t = setup();
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    act(() => t.jobs.emit(designJob("done", { files: [...DESIGN.files].reverse() })));
    await act(settle);
    expect(t.result.current.phase).toBe("done");
    expect(t.result.current.clips.map((c) => c.index)).toEqual([0, 1, 2]);
    expect(t.result.current.clips[0].url).toBe("/api/speech/candidates/7f6108c5034732e60e81230e03fd5041/0.wav");
  });

  it("finds out by asking when the event was missed", async () => {
    const t = setup();
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    t.api.job.mockResolvedValue(designJob("done"));
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(t.result.current.phase).toBe("done");
  });

  it("has the clips at once when the answer is that the design is already done", async () => {
    const t = setup(designJob("done"));
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    expect(t.result.current.phase).toBe("done");
    expect(t.result.current.clips).toHaveLength(3);
  });

  it("says why when the design failed, with what to do", async () => {
    const t = setup();
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    act(() => t.jobs.emit(designJob("failed", { files: [], error: { message: "The voice engine ran out of memory.", hint: "Unload the other model and try again." } })));
    await act(settle);
    expect(t.result.current).toMatchObject({ phase: "failed", clips: [], problem: { message: "The voice engine ran out of memory.", hint: "Unload the other model and try again." } });
  });

  it("says so in its own words when the design failed without saying why, or came back with no clips", async () => {
    const t = setup();
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    act(() => t.jobs.emit(designJob("canceled", { files: [], error: null })));
    await act(settle);
    expect(t.result.current.problem).toEqual({ message: "The samples could not be made.", hint: null });
    act(() => t.result.current.start("again"));
    await act(settle);
    act(() => t.jobs.emit(designJob("done", { files: [] })));
    await act(settle);
    expect(t.result.current).toMatchObject({ phase: "failed", problem: { message: "The samples could not be made." } });
  });

  it("says why when the request itself is refused", async () => {
    const t = setup();
    t.api.design.mockRejectedValueOnce(new ApiError("The queue is full.", 429, "queue_full", [], "Wait for a reading to finish."));
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    expect(t.result.current).toMatchObject({ phase: "failed", problem: { message: "The queue is full.", hint: "Wait for a reading to finish." } });
    t.api.design.mockRejectedValueOnce(new Error("offline"));
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    expect(t.result.current.problem).toEqual({ message: "The samples could not be asked for.", hint: null });
  });

  it("is told nothing of an earlier description once another is asked", async () => {
    const t = setup();
    act(() => t.result.current.start("first"));
    await act(settle);
    act(() => t.result.current.start("second"));
    await act(settle);
    act(() => t.jobs.emit(designJob("done"))); // both were the same job id in this stand-in: the second wait is the one that is listening
    await act(settle);
    expect(t.result.current.phase).toBe("done");
    expect(t.api.design).toHaveBeenCalledTimes(2);
  });

  it("is left alone by the answer to a description that was given up", async () => {
    const t = setup();
    let release: (j: SpeechJob) => void = () => undefined;
    t.api.design.mockImplementationOnce(() => new Promise<SpeechJob>((resolve) => (release = resolve)));
    act(() => t.result.current.start("first"));
    act(() => t.result.current.reset());
    release(designJob("done"));
    await act(settle);
    expect(t.result.current.phase).toBe("idle");
    expect(t.result.current.clips).toEqual([]);
  });

  it("goes back to nothing when reset, and stops waiting", async () => {
    const t = setup();
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    act(() => t.result.current.reset());
    expect(t.result.current).toMatchObject({ phase: "idle", clips: [], problem: null, saving: false, saveProblem: null });
    act(() => t.jobs.emit(designJob("done")));
    await act(settle);
    expect(t.result.current.phase).toBe("idle"); // the late event changed nothing
    t.api.job.mockClear();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(t.api.job).not.toHaveBeenCalled();
  });

  it("saves the clip chosen under the name as it is trimmed, by the job it came from, and gives back the voice", async () => {
    const t = setup(designJob("done"));
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    let voice: SavedVoice | null = null;
    await act(async () => { voice = await t.result.current.save("  Warm narrator  ", 1); });
    expect(t.api.saveVoice).toHaveBeenCalledWith({ kind: "designed", name: "Warm narrator", candidate: { job: DESIGN.id, index: 1 } });
    expect(voice).toEqual(DESIGNED);
    expect(t.result.current.saving).toBe(false);
  });

  it("says it is saving while it saves", async () => {
    const t = setup(designJob("done"));
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    let release: (v: SavedVoice) => void = () => undefined;
    t.api.saveVoice.mockImplementationOnce(() => new Promise<SavedVoice>((resolve) => (release = resolve)));
    let saved: Promise<SavedVoice | null> = Promise.resolve(null);
    act(() => { saved = t.result.current.save("Warm narrator", 0); });
    expect(t.result.current.saving).toBe(true);
    release(DESIGNED);
    await act(async () => void (await saved));
    expect(t.result.current.saving).toBe(false);
  });

  it("says why a voice could not be saved, gives back nothing, and clears it when asked again", async () => {
    const t = setup(designJob("done"));
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    t.api.saveVoice.mockRejectedValueOnce(new ApiError("A voice with that name already exists.", 409, "name_taken"));
    let voice: SavedVoice | null = DESIGNED;
    await act(async () => { voice = await t.result.current.save("Warm narrator", 0); });
    expect(voice).toBeNull();
    expect(t.result.current.saveProblem).toEqual({ message: "A voice with that name already exists.", hint: null });
    t.api.saveVoice.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await t.result.current.save("Warm narrator", 0); });
    expect(t.result.current.saveProblem).toEqual({ message: "The voice could not be saved.", hint: null });
    act(() => t.result.current.start("another"));
    expect(t.result.current.saveProblem).toBeNull();
  });

  it("has nothing to save before there are clips", async () => {
    const t = setup();
    let voice: SavedVoice | null = DESIGNED;
    await act(async () => { voice = await t.result.current.save("Warm narrator", 0); });
    expect(voice).toBeNull();
    expect(t.api.saveVoice).not.toHaveBeenCalled();
  });

  it("stops waiting when it is left", async () => {
    const t = setup();
    act(() => t.result.current.start("a warm voice"));
    await act(settle);
    t.unmount();
    t.api.job.mockClear();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.api.job).not.toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------ adding a voice by a recording
describe("adding a voice from a recording", () => {
  const limits = { recording_mb: 25 };
  const file = (name = "me.wav", size = 1000) => new File([new Uint8Array(size)], name, { type: "audio/wav" });

  function setup() {
    const api = {
      uploadRecording: vi.fn(async (_file: Blob, _signal?: AbortSignal) => STAGED),
      saveVoice: vi.fn(async (_body: unknown) => CLONED),
    };
    const view = renderHook(() => useRecordedVoice({ engine: "fake", limits, api }));
    return { api, ...view };
  }

  it("sends a recording that looks right and keeps what the server accepted", async () => {
    const t = setup();
    const chosen = file();
    act(() => t.result.current.choose(chosen));
    expect(t.result.current.checking).toBe(true);
    await act(settle);
    expect(t.api.uploadRecording).toHaveBeenCalledTimes(1);
    expect(t.api.uploadRecording.mock.calls[0][0]).toBe(chosen);
    expect(t.result.current).toMatchObject({ staged: STAGED, checking: false, problem: null });
  });

  it("does not send what is plainly wrong, and says why", async () => {
    const t = setup();
    act(() => t.result.current.choose(file("notes.txt")));
    expect(t.api.uploadRecording).not.toHaveBeenCalled();
    expect(t.result.current).toMatchObject({ checking: false, staged: null });
    expect(t.result.current.problem?.message).toMatch(/^notes\.txt is not a kind of recording/);
    act(() => t.result.current.choose(file("big.wav", 26 * 1024 * 1024)));
    expect(t.result.current.problem?.message).toBe("big.wav is 26 MB; a recording can be at most 25 MB.");
  });

  it("shows the server's own words when it refuses a recording (too short, silent, clipped, unreadable), with what to do", async () => {
    const t = setup();
    t.api.uploadRecording.mockRejectedValueOnce(new ApiError("The recording is only 3 seconds long.", 422, "too_short", [], "Record at least 8 seconds."));
    act(() => t.result.current.choose(file()));
    await act(settle);
    expect(t.result.current).toMatchObject({ staged: null, checking: false, problem: { message: "The recording is only 3 seconds long.", hint: "Record at least 8 seconds." } });
    t.api.uploadRecording.mockRejectedValueOnce(new Error("offline"));
    act(() => t.result.current.choose(file()));
    await act(settle);
    expect(t.result.current.problem).toEqual({ message: "The recording could not be sent.", hint: null });
  });

  it("clears an earlier problem and an earlier recording when another file is chosen", async () => {
    const t = setup();
    act(() => t.result.current.choose(file("notes.txt")));
    expect(t.result.current.problem).not.toBeNull();
    act(() => t.result.current.choose(file()));
    expect(t.result.current.problem).toBeNull();
    await act(settle);
    expect(t.result.current.staged).toEqual(STAGED);
    act(() => t.result.current.choose(file("notes.txt")));
    expect(t.result.current.staged).toBeNull();
  });

  it("can be told to stop while the recording is being sent: the request is given up, and that is not a failure", async () => {
    const t = setup();
    let signal: AbortSignal | undefined;
    t.api.uploadRecording.mockImplementationOnce((_f, s) => new Promise<StagedRecording>((_, reject) => {
      signal = s;
      s?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    act(() => t.result.current.choose(file()));
    act(() => t.result.current.stopChecking());
    await act(settle);
    expect(signal?.aborted).toBe(true);
    expect(t.result.current).toMatchObject({ checking: false, problem: null, staged: null });
  });

  it("does not show an abort the server's side reports as a failure", async () => {
    const t = setup();
    t.api.uploadRecording.mockRejectedValueOnce(new DOMException("aborted", "AbortError"));
    act(() => t.result.current.choose(file()));
    await act(settle);
    expect(t.result.current).toMatchObject({ checking: false, problem: null });
  });

  it("takes only the answer for the recording chosen last", async () => {
    const t = setup();
    let first: (r: StagedRecording) => void = () => undefined;
    t.api.uploadRecording.mockImplementationOnce(() => new Promise<StagedRecording>((resolve) => (first = resolve)));
    act(() => t.result.current.choose(file("one.wav")));
    const second = { ...STAGED, id: "second" };
    t.api.uploadRecording.mockResolvedValueOnce(second);
    act(() => t.result.current.choose(file("two.wav")));
    await act(settle);
    first({ ...STAGED, id: "first" }); // the first, late
    await act(settle);
    expect(t.result.current.staged?.id).toBe("second");
  });

  it("puts a recording away, to choose another", async () => {
    const t = setup();
    act(() => t.result.current.choose(file()));
    await act(settle);
    act(() => t.result.current.discard());
    expect(t.result.current).toMatchObject({ staged: null, checking: false, problem: null, saveProblem: null });
  });

  it("saves the recording as a voice under the name as it is trimmed, for the engine, with the statement made", async () => {
    const t = setup();
    act(() => t.result.current.choose(file()));
    await act(settle);
    let voice: SavedVoice | null = null;
    await act(async () => { voice = await t.result.current.save("  My own voice  ", true); });
    expect(t.api.saveVoice).toHaveBeenCalledWith({ kind: "cloned", name: "My own voice", recording: STAGED.id, engine: "fake", consent: true });
    expect(voice).toEqual(CLONED);
  });

  it("does not save without the statement that the voice is the person's own or that its owner agreed: nothing is sent", async () => {
    const t = setup();
    act(() => t.result.current.choose(file()));
    await act(settle);
    let voice: SavedVoice | null = CLONED;
    await act(async () => { voice = await t.result.current.save("My own voice", false); });
    expect(voice).toBeNull();
    expect(t.api.saveVoice).not.toHaveBeenCalled();
  });

  it("has nothing to save before a recording was accepted", async () => {
    const t = setup();
    let voice: SavedVoice | null = CLONED;
    await act(async () => { voice = await t.result.current.save("My own voice", true); });
    expect(voice).toBeNull();
    expect(t.api.saveVoice).not.toHaveBeenCalled();
  });

  it("says why a voice could not be saved, and says it is saving while it does", async () => {
    const t = setup();
    act(() => t.result.current.choose(file()));
    await act(settle);
    let release: (v: SavedVoice) => void = () => undefined;
    t.api.saveVoice.mockImplementationOnce(() => new Promise<SavedVoice>((resolve) => (release = resolve)));
    let saved: Promise<SavedVoice | null> = Promise.resolve(null);
    act(() => { saved = t.result.current.save("My own voice", true); });
    expect(t.result.current.saving).toBe(true);
    release(CLONED);
    await act(async () => void (await saved));
    t.api.saveVoice.mockRejectedValueOnce(new ApiError("The statement is needed.", 422, "consent_required"));
    await act(async () => { await t.result.current.save("My own voice", true); });
    expect(t.result.current).toMatchObject({ saving: false, saveProblem: { message: "The statement is needed.", hint: null } });
    t.api.saveVoice.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await t.result.current.save("My own voice", true); });
    expect(t.result.current.saveProblem).toEqual({ message: "The voice could not be saved.", hint: null });
  });

  it("gives up the recording being sent when it is left", async () => {
    const t = setup();
    let signal: AbortSignal | undefined;
    t.api.uploadRecording.mockImplementationOnce((_f, s) => new Promise<StagedRecording>(() => void (signal = s)));
    act(() => t.result.current.choose(file()));
    t.unmount();
    expect(signal?.aborted).toBe(true);
  });
});


// ------------------------------------------------------------------ what comes late, from an earlier asking
describe("answers that come late, from an earlier asking", () => {
  it("takes the answer of the latest asking for the passage, and not a late one of an earlier asking", async () => {
    const held: Array<{ resolve: (p: Passage) => void; reject: (e: unknown) => void }> = [];
    const api = { passage: vi.fn(() => new Promise<Passage>((resolve, reject) => void held.push({ resolve, reject }))) };
    const view = renderHook(() => usePassage(true, api));
    act(() => view.result.current.retry()); // asked again while the first is still out
    await act(settle);
    expect(api.passage).toHaveBeenCalledTimes(2);
    held[1].resolve(PASSAGE);
    await act(settle);
    held[0].resolve({ ...PASSAGE, name_max: 1 }); // the first, late, and different
    await act(settle);
    expect(view.result.current.passage).toEqual(PASSAGE);
  });

  it("does not show the failure of an earlier asking over a passage that has come", async () => {
    const held: Array<{ resolve: (p: Passage) => void; reject: (e: unknown) => void }> = [];
    const api = { passage: vi.fn(() => new Promise<Passage>((resolve, reject) => void held.push({ resolve, reject }))) };
    const view = renderHook(() => usePassage(true, api));
    act(() => view.result.current.retry());
    await act(settle);
    held[1].resolve(PASSAGE);
    await act(settle);
    held[0].reject(new ApiError("The studio is busy.", 503, "busy"));
    await act(settle);
    expect(view.result.current.problem).toBeNull();
    expect(view.result.current.passage).toEqual(PASSAGE);
  });

  it("is told nothing of the job of an earlier description once another is asked: only the latest one's samples are taken", async () => {
    const jobs = createEmitter<SpeechJob>();
    const api = {
      design: vi.fn(async (_d: string, _e?: string) => designJob("queued", { id: "j1" })),
      job: vi.fn(async (_id: string) => designJob("queued")),
      saveVoice: vi.fn(async (_b: unknown) => DESIGNED),
    };
    api.design.mockResolvedValueOnce(designJob("queued", { id: "j1" })).mockResolvedValueOnce(designJob("queued", { id: "j2" }));
    const view = renderHook(() => useVoiceDesign({ engine: "fake", jobs, api, pollMs: 2000 }));
    act(() => view.result.current.start("first"));
    await act(settle);
    act(() => view.result.current.start("second"));
    await act(settle);
    act(() => jobs.emit(designJob("done", { id: "j1" }))); // the first description's samples, made after another was asked for
    await act(settle);
    expect(view.result.current.phase).toBe("working");
    act(() => jobs.emit(designJob("done", { id: "j2", files: [{ index: 0, key: "c0", url: "/second/0.wav" }] })));
    await act(settle);
    expect(view.result.current.phase).toBe("done");
    expect(view.result.current.clips.map((c) => c.url)).toEqual(["/second/0.wav"]);
  });

  it("takes a design that failed for a failure even if its job still lists files", async () => {
    const jobs = createEmitter<SpeechJob>();
    const api = { design: vi.fn(async () => designJob("queued")), job: vi.fn(async () => designJob("queued")), saveVoice: vi.fn(async () => DESIGNED) };
    const view = renderHook(() => useVoiceDesign({ engine: "fake", jobs, api, pollMs: 2000 }));
    act(() => view.result.current.start("a warm voice"));
    await act(settle);
    act(() => jobs.emit(designJob("failed", { error: { message: "The engine ran out of memory.", hint: null } }))); // (its files are still listed)
    await act(settle);
    expect(view.result.current).toMatchObject({ phase: "failed", clips: [], problem: { message: "The engine ran out of memory." } });
  });

  it("does not show the refusal of a description that was given up", async () => {
    const jobs = createEmitter<SpeechJob>();
    let refuse: (error: unknown) => void = () => undefined;
    const api = {
      design: vi.fn(() => new Promise<SpeechJob>((_, reject) => (refuse = reject))),
      job: vi.fn(async () => designJob("queued")),
      saveVoice: vi.fn(async () => DESIGNED),
    };
    const view = renderHook(() => useVoiceDesign({ engine: "fake", jobs, api, pollMs: 2000 }));
    act(() => view.result.current.start("a warm voice"));
    act(() => view.result.current.reset());
    refuse(new ApiError("The queue is full.", 429, "queue_full"));
    await act(settle);
    expect(view.result.current).toMatchObject({ phase: "idle", problem: null });
  });

  it("forgets why a recording was refused when it is put away", async () => {
    const api = { uploadRecording: vi.fn(async () => STAGED), saveVoice: vi.fn(async () => CLONED) };
    const view = renderHook(() => useRecordedVoice({ engine: "fake", limits: { recording_mb: 25 }, api }));
    act(() => view.result.current.choose(new File([new Uint8Array(10)], "notes.txt")));
    expect(view.result.current.problem).not.toBeNull();
    act(() => view.result.current.discard());
    expect(view.result.current.problem).toBeNull();
  });
});
