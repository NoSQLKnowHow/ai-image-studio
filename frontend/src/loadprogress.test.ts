import { describe, expect, it } from "vitest";
import { NO_TITLES, afterWords, elapsedWords, engineTitlesOf, loadAnnouncement, loadView, modelWords, sizeWords } from "./loadProgress";
import { STATUS } from "./testdata";
import type { LoadProgress, WorkerStatus } from "./types";

// What the page says while a model loads (DESIGN.md section 39, criteria 300 to 305): the server's `worker.progress` made into words. Every function here is pure, so the
// wording is tried exactly; what the components do with it is in loadprogressview.test.tsx.

const MIB = 1024 ** 2;
const GIB = 1024 ** 3;

// The names the page is given by the server (`capabilities.speech.engines`): it writes none of its own (DESIGN.md §38, criterion 260), so these tests give it some.
const TITLES = engineTitlesOf([{ id: "qwen3-tts", title: "Qwen3-TTS" }, { id: "kokoro", title: "Kokoro" }, { id: "piper", title: "Piper" }, { id: "voxtral", title: "Voxtral" }]);
const modelOf = (model: Parameters<typeof modelWords>[0], engine?: string | null, variant?: string | null, titles = NO_TITLES) => modelWords(model, engine, variant, titles);
const words = (model: Parameters<typeof modelWords>[0], engine?: string | null, variant?: string | null) => modelWords(model, engine, variant, TITLES);
const afterOf = (key: string | null | undefined) => afterWords(key, TITLES);
const viewOf = (worker: WorkerStatus | null | undefined) => loadView(worker, TITLES);

const loading = (progress: Partial<LoadProgress> | null, over: Partial<WorkerStatus> = {}): WorkerStatus => ({
  ...STATUS.worker, state: "loading", model: "speech", engine: "qwen3-tts", variant: "design",
  progress: progress === null ? null : { phase: "downloading", done_bytes: 0, total_bytes: null, elapsed_s: 0, after: null, ...progress }, ...over,
});

describe("sizeWords: a size as du -sh would give it", () => {
  it.each([
    [0, "0 MB"], [1, "0 MB"], [40_000, "0 MB"], [300 * 1024, "0.3 MB"], [5.5 * MIB, "5.5 MB"], [99.9 * MIB, "99.9 MB"], [100 * MIB, "100 MB"], [450.4 * MIB, "450 MB"],
    [1023 * MIB, "1023 MB"], [GIB, "1.0 GB"], [2.1 * GIB, "2.1 GB"], [4.5 * GIB, "4.5 GB"], [31 * GIB, "31.0 GB"],
  ])("%s bytes is %s", (bytes, words) => {
    expect(sizeWords(bytes)).toBe(words);
  });

  it("makes nothing of what is not a size", () => {
    expect(sizeWords(Number.NaN)).toBe("0 MB");
    expect(sizeWords(-5 * GIB)).toBe("0 MB");
  });
});

describe("elapsedWords: how long it has taken", () => {
  it.each([[0, "0 s"], [0.9, "0 s"], [40.9, "40 s"], [59, "59 s"], [60, "1 min"], [65, "1 min 5 s"], [185, "3 min 5 s"], [3599, "59 min 59 s"], [3600, "1 h 0 min"], [3725, "1 h 2 min"]])(
    "%s seconds is %s", (seconds, words) => {
      expect(elapsedWords(seconds)).toBe(words);
    });

  it("makes nothing of what is not a time", () => {
    expect(elapsedWords(Number.NaN)).toBe("0 s");
    expect(elapsedWords(-3)).toBe("0 s");
    expect(elapsedWords(Number.POSITIVE_INFINITY)).toBe("0 s");
  });
});

describe("modelWords: what is loading", () => {
  it("names the image and the music model", () => {
    expect(words("image")).toBe("the image model");
    expect(words("music")).toBe("the music model");
    expect(words("image", "qwen3-tts", "design")).toBe("the image model"); // an engine on a model that has none is no business of the words
  });

  it("names which of Qwen3-TTS's three models is loading, since that is what explains the wait and the reload", () => {
    expect(words("speech", "qwen3-tts", "custom")).toBe("Qwen3-TTS for preset voices");
    expect(words("speech", "qwen3-tts", "design")).toBe("Qwen3-TTS for designing a voice");
    expect(words("speech", "qwen3-tts", "clone")).toBe("Qwen3-TTS for your own voices");
  });

  it("names the other engines by themselves, where their variant is not a kind of voice", () => {
    expect(words("speech", "kokoro", "default")).toBe("Kokoro");
    expect(words("speech", "piper")).toBe("Piper");
    expect(words("speech", "voxtral", null)).toBe("Voxtral");
  });

  it("says the kind of voice by the server's word, whichever engine it comes from, and takes the engine's name from what it was given", () => {
    expect(words("speech", "kokoro", "design")).toBe("Kokoro for designing a voice"); // the word is the server's: an engine with one model for each kind is said like Qwen3-TTS
    expect(modelOf("speech", "next-engine", "clone", engineTitlesOf([{ id: "next-engine", title: "Next Engine" }]))).toBe("Next Engine for your own voices"); // no edit of the page for a new model
  });

  it("says the speech model when it cannot say more", () => {
    expect(words("speech")).toBe("the speech model");
    expect(words("speech", null, null)).toBe("the speech model");
    expect(words("speech", "fake", "default")).toBe("the speech model");                  // an engine it was not given a name for
    expect(modelOf("speech", "qwen3-tts", "design")).toBe("the speech model");            // before the capabilities have arrived: no names yet
    expect(words("speech", "qwen3-tts", "no-such-variant")).toBe("Qwen3-TTS");
    expect(words("speech", "qwen3-tts", null)).toBe("Qwen3-TTS");
  });

  it("is not taken in by words from the server that are names of things on every object", () => {
    expect(words("speech", "constructor", "design")).toBe("the speech model");
    expect(words("speech", "__proto__")).toBe("the speech model");
    expect(words("speech", "qwen3-tts", "constructor")).toBe("Qwen3-TTS");
    expect(words("speech", "qwen3-tts", "toString")).toBe("Qwen3-TTS");
  });

  it("is given its names from the capabilities' list of engines, and none from nothing", () => {
    expect(engineTitlesOf(null).size).toBe(0);
    expect(engineTitlesOf(undefined).size).toBe(0);
    expect(engineTitlesOf([]).size).toBe(0);
    expect(engineTitlesOf([{ id: "a", title: "Engine A" }, { id: "b", title: "Engine B" }]).get("b")).toBe("Engine B");
  });
});

describe("afterWords: the model that was unloaded to make room", () => {
  it("says it in a sentence, from the server's model key", () => {
    expect(afterOf("image")).toBe("The image model was unloaded to make room.");
    expect(afterOf("music")).toBe("The music model was unloaded to make room.");
    expect(afterOf("speech:qwen3-tts:custom")).toBe("Qwen3-TTS for preset voices was unloaded to make room.");
    expect(afterOf("speech:kokoro:default")).toBe("Kokoro was unloaded to make room.");
    expect(afterOf("speech:fake:default")).toBe("The speech model was unloaded to make room.");
  });

  it("says nothing when nothing was, or the key is not one the page knows", () => {
    expect(afterOf(null)).toBeNull();
    expect(afterOf(undefined)).toBeNull();
    expect(afterOf("")).toBeNull();
    expect(afterOf("vector")).toBeNull();
    expect(afterOf("bogus:x:y")).toBeNull();
  });
});

describe("loadView: what to show of a load that is under way (criteria 300 to 303)", () => {
  it("is nothing unless the worker is loading and the server said how far", () => {
    expect(viewOf(null)).toBeNull();
    expect(viewOf(undefined)).toBeNull();
    expect(viewOf(loading(null))).toBeNull();                                           // an older server, or no load
    expect(viewOf({ ...loading({}), progress: undefined })).toBeNull();
    for (const state of ["unloaded", "ready", "busy", "error", "unavailable"] as const) expect(viewOf(loading({}, { state }))).toBeNull();  // a load that is over
  });

  it("says a load is starting before the worker has reported, with no number to show", () => {
    const view = viewOf(loading({ phase: "starting" }, { model: "image", engine: null, variant: null }))!;
    expect(view).toMatchObject({ phase: "starting", headline: "Starting to load the image model…", detail: "", pill: "", fraction: null, after: null });
    expect(viewOf(loading({ phase: "starting", elapsed_s: 3.7 }))!).toMatchObject({ detail: "3 s", pill: "3 s" });
  });

  it("says how much of a download has arrived, and of what, when the size is known", () => {
    const view = viewOf(loading({ phase: "downloading", done_bytes: 2.1 * GIB, total_bytes: 4.5 * GIB, elapsed_s: 185 }))!;
    expect(view.headline).toBe("Downloading Qwen3-TTS for designing a voice");
    expect(view.detail).toBe("2.1 GB of about 4.5 GB · 3 min 5 s");
    expect(view.pill).toBe("2.1 GB of 4.5 GB");
    expect(view.fraction).toBeCloseTo(2.1 / 4.5, 5);
  });

  it("says only how much has arrived when no size is known, and has no fraction to fill", () => {
    const view = viewOf(loading({ phase: "downloading", done_bytes: 512 * MIB, total_bytes: null, elapsed_s: 12 }, { model: "speech", engine: "kokoro", variant: "default" }))!;
    expect(view).toMatchObject({ headline: "Downloading Kokoro", detail: "512 MB so far · 12 s", pill: "512 MB", fraction: null });
    expect(viewOf(loading({ phase: "downloading", done_bytes: 512 * MIB, total_bytes: 0 }))!.fraction).toBeNull();   // a size of nothing is none
  });

  it("leaves out the time before the first second, and never fills the bar past its end or below its start", () => {
    expect(viewOf(loading({ phase: "downloading", done_bytes: 1 * MIB, total_bytes: 4 * GIB, elapsed_s: 0.4 }))!.detail).toBe("1.0 MB of about 4.0 GB");
    expect(viewOf(loading({ phase: "downloading", done_bytes: 5 * GIB, total_bytes: 4 * GIB }))!.fraction).toBe(1);
    expect(viewOf(loading({ phase: "downloading", done_bytes: -5, total_bytes: 4 * GIB }))!.fraction).toBe(0);
    expect(viewOf(loading({ phase: "downloading", done_bytes: 0, total_bytes: 4 * GIB }))!.fraction).toBe(0);
  });

  it("says loading into memory as the time it has taken and nothing else: it has no measure", () => {
    const view = viewOf(loading({ phase: "loading", done_bytes: 4.5 * GIB, total_bytes: 4.5 * GIB, elapsed_s: 40 }, { model: "music", engine: null, variant: null }))!;
    expect(view).toMatchObject({ phase: "loading", headline: "Loading the music model into memory", detail: "40 s", pill: "40 s", fraction: null });
  });

  it("says which model was unloaded to make room, in each phase", () => {
    for (const phase of ["starting", "downloading", "loading"] as const) {
      expect(viewOf(loading({ phase, after: "speech:qwen3-tts:custom" }))!.after).toBe("Qwen3-TTS for preset voices was unloaded to make room.");
    }
    expect(viewOf(loading({ phase: "loading", after: null }))!.after).toBeNull();
  });

  it("keeps the headline, which is what a screen reader is told, the same while only the numbers change", () => {
    const a = viewOf(loading({ phase: "downloading", done_bytes: 1 * GIB, total_bytes: 4 * GIB, elapsed_s: 10 }))!;
    const b = viewOf(loading({ phase: "downloading", done_bytes: 2 * GIB, total_bytes: 4 * GIB, elapsed_s: 20 }))!;
    expect(a.headline).toBe(b.headline);
    expect(a.detail).not.toBe(b.detail);
    expect(viewOf(loading({ phase: "loading" }))!.headline).not.toBe(a.headline);   // but a change of phase is a change
  });
});

describe("loadAnnouncement", () => {
  it("is the headline, as a sentence", () => {
    expect(loadAnnouncement(viewOf(loading({ phase: "downloading" }))!)).toBe("Downloading Qwen3-TTS for designing a voice.");
    expect(loadAnnouncement(viewOf(loading({ phase: "loading" }))!)).toBe("Loading Qwen3-TTS for designing a voice into memory.");
    expect(loadAnnouncement(viewOf(loading({ phase: "starting" }))!)).toBe("Starting to load Qwen3-TTS for designing a voice.");   // not "…."
  });

  it("carries no number, which would be said every second", () => {
    // not `/\d/`: the name Qwen3-TTS has a digit in it. What must be absent is a size or a time, and the same words must come out whatever the numbers are.
    const a = loadAnnouncement(viewOf(loading({ phase: "downloading", done_bytes: 3 * GIB, total_bytes: 4 * GIB, elapsed_s: 99 }))!);
    const b = loadAnnouncement(viewOf(loading({ phase: "downloading", done_bytes: 1 * MIB, total_bytes: 9 * GIB, elapsed_s: 5 }))!);
    expect(a).not.toMatch(/\b(MB|GB|min|h)\b|\d+ s\b|%/);
    expect(a).toBe(b);
  });
});
