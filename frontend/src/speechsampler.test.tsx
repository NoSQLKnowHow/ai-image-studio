import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { createEmitter } from "./emitter";
import type { EngineVoices, SampleAnswer, SpeechJob } from "./speechTypes";
import { SPEECH_CONTRACT } from "./testdata";
import { useEngineVoices } from "./useEngineVoices";
import { useSampler } from "./useSampler";

// "Play sample" and the list of voices behind it (DESIGN.md §38.4, criterion 262), without the page: what the sampler asks for, waits for and plays, and how it behaves
// when answers come late, out of order, or not at all. The audio element is a stand-in whose play and pause are watched.

const READY: SampleAnswer = { ready: true, url: "/api/speech/samples/fake/anna.wav", job: null };
const jobOf = (id: string, changes: Partial<SpeechJob> = {}): SpeechJob => ({ ...(SPEECH_CONTRACT.sample_job as unknown as SpeechJob), id, ...changes });
const QUEUED: SampleAnswer = { ready: false, url: null, job: jobOf("job1", { status: "queued", files: [] }) };
const BODY = { engine: "fake", voice: "anna" };

function setup(answers: { sample?: () => Promise<SampleAnswer>; job?: () => Promise<SpeechJob> } = {}) {
  const audio = document.createElement("audio");
  const play = vi.fn(async () => undefined);
  const pause = vi.fn();
  Object.assign(audio, { play, pause });
  const jobs = createEmitter<SpeechJob>();
  const api = { sample: vi.fn(answers.sample ?? (async () => READY)), job: vi.fn(answers.job ?? (async () => jobOf("job1", { status: "running", files: [] }))) };
  const hook = renderHook(() => useSampler({ audio: { current: audio }, jobs, api, pollMs: 1000 }));
  return { audio, play, pause, jobs, api, ...hook };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("a sample that is kept", () => {
  it("is played at once, the button says Stop while it plays, and it is idle again when it ends", async () => {
    const { result, audio, play, api } = setup();
    expect(result.current.phase("preset:anna")).toBe("idle");
    await act(async () => result.current.play("preset:anna", BODY));
    expect(api.sample).toHaveBeenCalledExactlyOnceWith(BODY);
    expect(audio.getAttribute("src")).toBe("/api/speech/samples/fake/anna.wav");
    expect(play).toHaveBeenCalledTimes(1);
    expect(result.current.phase("preset:anna")).toBe("playing");
    expect(result.current.phase("preset:bea")).toBe("idle"); // only the voice that plays
    act(() => void audio.dispatchEvent(new Event("ended")));
    expect(result.current.phase("preset:anna")).toBe("idle");
  });

  it("is stopped by pressing the same voice again, without asking the server for anything", async () => {
    const { result, audio, pause, api } = setup();
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => result.current.play("preset:anna", BODY));
    expect(pause).toHaveBeenCalled();
    expect(audio.hasAttribute("src")).toBe(false);
    expect(result.current.phase("preset:anna")).toBe("idle");
    expect(api.sample).toHaveBeenCalledTimes(1);
    expect(result.current.problem).toBeNull(); // taking the sound away is not a failure
  });

  it("is replaced by another voice's sample: the first stops, the second plays", async () => {
    const { result, play, pause, api } = setup();
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => result.current.play("preset:bea", { engine: "fake", voice: "bea" }));
    expect(pause).toHaveBeenCalled();
    expect(api.sample).toHaveBeenCalledTimes(2);
    expect(play).toHaveBeenCalledTimes(2);
    expect(result.current.phase("preset:anna")).toBe("idle");
    expect(result.current.phase("preset:bea")).toBe("playing");
  });
});

describe("a sample that has to be made", () => {
  it("says it is waiting, is played when the event says the job is done, and stops listening", async () => {
    const { result, audio, play, jobs, api } = setup({ sample: async () => QUEUED });
    await act(async () => result.current.play("preset:anna", BODY));
    expect(result.current.phase("preset:anna")).toBe("waiting");
    expect(play).not.toHaveBeenCalled();
    await act(async () => jobs.emit(jobOf("job1", { status: "running", files: [] }))); // still working: nothing yet
    expect(result.current.phase("preset:anna")).toBe("waiting");
    await act(async () => jobs.emit(jobOf("other", { status: "done" }))); // another job's event is not this one's
    expect(play).not.toHaveBeenCalled();
    await act(async () => jobs.emit(jobOf("job1", { status: "done", files: [{ key: "sample", url: "/api/speech/samples/fake/anna.wav" }] })));
    expect(audio.getAttribute("src")).toBe("/api/speech/samples/fake/anna.wav");
    expect(play).toHaveBeenCalledTimes(1);
    expect(result.current.phase("preset:anna")).toBe("playing");
    await act(async () => vi.advanceTimersByTime(5000));
    expect(api.job).not.toHaveBeenCalled(); // it has stopped asking
  });

  it("asks the server how the job is doing when no event comes, and plays when it is done", async () => {
    let n = 0;
    const { result, audio, api } = setup({
      sample: async () => QUEUED,
      job: async () => (++n < 3 ? jobOf("job1", { status: "running", files: [] }) : jobOf("job1", { status: "done", files: [{ key: "sample", url: "/made.wav" }] })),
    });
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => vi.advanceTimersByTime(1000));
    await act(async () => vi.advanceTimersByTime(1000));
    expect(result.current.phase("preset:anna")).toBe("waiting");
    await act(async () => vi.advanceTimersByTime(1000));
    expect(api.job).toHaveBeenCalledTimes(3);
    expect(audio.getAttribute("src")).toBe("/made.wav");
    expect(result.current.phase("preset:anna")).toBe("playing");
  });

  it("goes on asking when one question fails", async () => {
    let n = 0;
    const { result, audio } = setup({
      sample: async () => QUEUED,
      job: async () => { if (++n === 1) throw new TypeError("network"); return jobOf("job1", { status: "done", files: [{ key: "sample", url: "/made.wav" }] }); },
    });
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => vi.advanceTimersByTime(1000));
    await act(async () => vi.advanceTimersByTime(1000));
    expect(audio.getAttribute("src")).toBe("/made.wav");
  });

  it("says why when the job failed, with what to do, and the button is back", async () => {
    const { result, jobs } = setup({ sample: async () => QUEUED });
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => jobs.emit(jobOf("job1", { status: "failed", files: [], error: { message: "Not enough free memory.", hint: "Unload the image model." } })));
    expect(result.current.phase("preset:anna")).toBe("idle");
    expect(result.current.problem).toEqual({ key: "preset:anna", message: "Not enough free memory.", hint: "Unload the image model." });
  });

  it("says it could not be made when the job was canceled with no reason", async () => {
    const { result, jobs } = setup({ sample: async () => QUEUED });
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => jobs.emit(jobOf("job1", { status: "canceled", files: [], error: null })));
    expect(result.current.problem?.message).toBe("The sample could not be made.");
  });

  it("is a problem when the job is done and has no file", async () => {
    const { result, jobs } = setup({ sample: async () => QUEUED });
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => jobs.emit(jobOf("job1", { status: "done", files: [] })));
    expect(result.current.problem?.message).toBe("The sample could not be made.");
    expect(result.current.phase("preset:anna")).toBe("idle");
  });

  it("takes a job that is already finished in the answer without waiting", async () => {
    const done: SampleAnswer = { ready: false, url: null, job: jobOf("job1", { status: "done", files: [{ key: "sample", url: "/quick.wav" }] }) };
    const { result, audio } = setup({ sample: async () => done });
    await act(async () => result.current.play("preset:anna", BODY));
    expect(audio.getAttribute("src")).toBe("/quick.wav");
    expect(result.current.phase("preset:anna")).toBe("playing");
  });

  it("stops waiting when the person presses it again, and ignores the answer that comes after", async () => {
    const { result, play, jobs, api } = setup({ sample: async () => QUEUED });
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => result.current.play("preset:anna", BODY)); // the second press while it waits is a Stop
    expect(result.current.phase("preset:anna")).toBe("idle");
    await act(async () => jobs.emit(jobOf("job1", { status: "done", files: [{ key: "sample", url: "/late.wav" }] })));
    await act(async () => vi.advanceTimersByTime(5000));
    expect(play).not.toHaveBeenCalled();
    expect(api.job).not.toHaveBeenCalled();
  });

  it("ignores the answer to an earlier press when another voice has been asked for since", async () => {
    const waiting: ((answer: SampleAnswer) => void)[] = [];
    const { result, audio } = setup({ sample: () => new Promise<SampleAnswer>((resolve) => { waiting.push(resolve); }) });
    await act(async () => result.current.play("preset:anna", BODY));
    await act(async () => result.current.play("preset:bea", { engine: "fake", voice: "bea" }));
    await act(async () => waiting[1]({ ready: true, url: "/bea.wav", job: null }));
    await act(async () => waiting[0]({ ready: true, url: "/anna.wav", job: null })); // the late one
    expect(audio.getAttribute("src")).toBe("/bea.wav");
    expect(result.current.phase("preset:bea")).toBe("playing");
    expect(result.current.phase("preset:anna")).toBe("idle");
  });
});

describe("when asking fails", () => {
  it("says the server's own words and hint, beside the voice that was asked for", async () => {
    const { result } = setup({ sample: async () => { throw new ApiError("Fake speech is still being checked.", 409, "engine_checking", [], "Try again in a moment."); } });
    await act(async () => result.current.play("preset:anna", BODY));
    expect(result.current.problem).toEqual({ key: "preset:anna", message: "Fake speech is still being checked.", hint: "Try again in a moment." });
    expect(result.current.phase("preset:anna")).toBe("idle");
  });

  it("says something plain for an error that is not the server's, and forgets the old problem at the next press", async () => {
    let first = true;
    const { result } = setup({ sample: async () => { if (first) { first = false; throw new Error("boom"); } return READY; } });
    await act(async () => result.current.play("preset:anna", BODY));
    expect(result.current.problem?.message).toBe("The sample could not be made.");
    await act(async () => result.current.play("preset:anna", BODY));
    expect(result.current.problem).toBeNull();
  });
});

describe("when the browser will not play it", () => {
  it("waits for a press when it would not start by itself, and plays then without asking the server again", async () => {
    const { result, audio, play, api } = setup();
    play.mockRejectedValueOnce(new DOMException("blocked", "NotAllowedError"));
    await act(async () => result.current.play("preset:anna", BODY));
    expect(result.current.phase("preset:anna")).toBe("ready");
    expect(result.current.problem).toBeNull();
    await act(async () => result.current.play("preset:anna", BODY));
    expect(api.sample).toHaveBeenCalledTimes(1);
    expect(play).toHaveBeenCalledTimes(2);
    expect(audio.getAttribute("src")).toBe("/api/speech/samples/fake/anna.wav");
    expect(result.current.phase("preset:anna")).toBe("playing");
  });

  it("says so for any other reason it cannot play, and for a file that fails while it plays", async () => {
    const { result, audio, play } = setup();
    play.mockRejectedValueOnce(new DOMException("broken", "NotSupportedError"));
    await act(async () => result.current.play("preset:anna", BODY));
    expect(result.current.problem).toEqual({ key: "preset:anna", message: "The sample could not be played.", hint: null });
    expect(result.current.phase("preset:anna")).toBe("idle");
    await act(async () => result.current.play("preset:bea", { engine: "fake", voice: "bea" }));
    expect(result.current.phase("preset:bea")).toBe("playing");
    act(() => void audio.dispatchEvent(new Event("error")));
    expect(result.current.problem).toEqual({ key: "preset:bea", message: "The sample could not be played.", hint: null });
    expect(result.current.phase("preset:bea")).toBe("idle");
  });
});

describe("leaving", () => {
  it("stops the sound with stop(), and says nothing of it", async () => {
    const { result, pause } = setup();
    await act(async () => result.current.play("preset:anna", BODY));
    act(() => result.current.stop());
    expect(pause).toHaveBeenCalled();
    expect(result.current.phase("preset:anna")).toBe("idle");
    expect(result.current.problem).toBeNull();
  });

  it("stops asking about a job when the page is left", async () => {
    const { result, api, unmount } = setup({ sample: async () => QUEUED });
    await act(async () => result.current.play("preset:anna", BODY));
    unmount();
    await act(async () => vi.advanceTimersByTime(10_000));
    expect(api.job).not.toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------ an engine's voices
describe("an engine's voices", () => {
  const voicesOf = (engine: string): EngineVoices => ({ ...(SPEECH_CONTRACT.engine_voices as unknown as EngineVoices), engine });
  beforeEach(() => vi.useRealTimers());

  it("are asked for when there is an engine, and are nothing while there is none", async () => {
    const api = { voices: vi.fn(async (engine: string) => voicesOf(engine)) };
    const { result, rerender } = renderHook(({ engine }: { engine: string | null }) => useEngineVoices(engine, api), { initialProps: { engine: null as string | null } });
    expect(result.current.voices).toBeNull();
    expect(api.voices).not.toHaveBeenCalled();
    rerender({ engine: "fake" });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.voices?.engine).toBe("fake"));
    expect(result.current.loading).toBe(false);
    rerender({ engine: null });
    await waitFor(() => expect(result.current.voices).toBeNull());
  });

  it("never shows one engine's voices under another's name, even when the first answer comes last", async () => {
    const waiting = new Map<string, (voices: EngineVoices) => void>();
    const api = { voices: vi.fn((engine: string) => new Promise<EngineVoices>((resolve) => { waiting.set(engine, resolve); })) };
    const { result, rerender } = renderHook(({ engine }: { engine: string }) => useEngineVoices(engine, api), { initialProps: { engine: "fake" } });
    rerender({ engine: "fake-basic" });
    await act(async () => waiting.get("fake-basic")?.(voicesOf("fake-basic")));
    await act(async () => waiting.get("fake")?.(voicesOf("fake"))); // the late answer for the engine that was left
    expect(result.current.voices?.engine).toBe("fake-basic");
  });

  it("says the server's words when they cannot be read, and reads them again when asked", async () => {
    let fail = true;
    const api = { voices: vi.fn(async (engine: string) => { if (fail) throw new ApiError("Fake speech cannot run here.", 409, "engine_unavailable", [], "Rebuild the image."); return voicesOf(engine); }) };
    const { result } = renderHook(() => useEngineVoices("fake", api));
    await waitFor(() => expect(result.current.problem).toEqual({ message: "Fake speech cannot run here.", hint: "Rebuild the image." }));
    expect(result.current.voices).toBeNull();
    fail = false;
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.voices?.engine).toBe("fake"));
    expect(result.current.problem).toBeNull();
    expect(api.voices).toHaveBeenCalledTimes(2);
  });

  it("says something plain for an error that is not the server's", async () => {
    const api = { voices: vi.fn(async () => { throw new Error("boom"); }) };
    const { result } = renderHook(() => useEngineVoices("fake", api));
    await waitFor(() => expect(result.current.problem?.message).toBe("The voices could not be read."));
  });
});
