import { describe, expect, it } from "vitest";
import { afterWords, elapsedWords, loadAnnouncement, loadView, modelWords, sizeWords } from "./loadProgress";
import { STATUS } from "./testdata";
import type { LoadProgress, WorkerStatus } from "./types";

// What the page says while a model loads (DESIGN.md section 39, criteria 300 to 305): the server's `worker.progress` made into words. Every function here is pure, so the
// wording is tried exactly; what the components do with it is in loadprogressview.test.tsx.

const MIB = 1024 ** 2;
const GIB = 1024 ** 3;

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
    expect(modelWords("image")).toBe("the image model");
    expect(modelWords("music")).toBe("the music model");
    expect(modelWords("image", "qwen3-tts", "design")).toBe("the image model"); // an engine on a model that has none is no business of the words
  });

  it("names which of Qwen3-TTS's three models is loading, since that is what explains the wait and the reload", () => {
    expect(modelWords("speech", "qwen3-tts", "custom")).toBe("Qwen3-TTS for preset voices");
    expect(modelWords("speech", "qwen3-tts", "design")).toBe("Qwen3-TTS for designing a voice");
    expect(modelWords("speech", "qwen3-tts", "clone")).toBe("Qwen3-TTS for your own voices");
  });

  it("names the other engines by themselves, whatever their variant", () => {
    expect(modelWords("speech", "kokoro", "default")).toBe("Kokoro");
    expect(modelWords("speech", "kokoro", "design")).toBe("Kokoro"); // only Qwen3-TTS has kinds of model
    expect(modelWords("speech", "piper")).toBe("Piper");
    expect(modelWords("speech", "voxtral", null)).toBe("Voxtral");
  });

  it("says the speech model when it cannot say more", () => {
    expect(modelWords("speech")).toBe("the speech model");
    expect(modelWords("speech", null, null)).toBe("the speech model");
    expect(modelWords("speech", "fake", "default")).toBe("the speech model");
    expect(modelWords("speech", "qwen3-tts", "no-such-variant")).toBe("Qwen3-TTS");
    expect(modelWords("speech", "qwen3-tts", null)).toBe("Qwen3-TTS");
  });
});

describe("afterWords: the model that was unloaded to make room", () => {
  it("says it in a sentence, from the server's model key", () => {
    expect(afterWords("image")).toBe("The image model was unloaded to make room.");
    expect(afterWords("music")).toBe("The music model was unloaded to make room.");
    expect(afterWords("speech:qwen3-tts:custom")).toBe("Qwen3-TTS for preset voices was unloaded to make room.");
    expect(afterWords("speech:kokoro:default")).toBe("Kokoro was unloaded to make room.");
    expect(afterWords("speech:fake:default")).toBe("The speech model was unloaded to make room.");
  });

  it("says nothing when nothing was, or the key is not one the page knows", () => {
    expect(afterWords(null)).toBeNull();
    expect(afterWords(undefined)).toBeNull();
    expect(afterWords("")).toBeNull();
    expect(afterWords("vector")).toBeNull();
    expect(afterWords("bogus:x:y")).toBeNull();
  });
});

describe("loadView: what to show of a load that is under way (criteria 300 to 303)", () => {
  it("is nothing unless the worker is loading and the server said how far", () => {
    expect(loadView(null)).toBeNull();
    expect(loadView(undefined)).toBeNull();
    expect(loadView(loading(null))).toBeNull();                                           // an older server, or no load
    expect(loadView({ ...loading({}), progress: undefined })).toBeNull();
    for (const state of ["unloaded", "ready", "busy", "error", "unavailable"] as const) expect(loadView(loading({}, { state }))).toBeNull();  // a load that is over
  });

  it("says a load is starting before the worker has reported, with no number to show", () => {
    const view = loadView(loading({ phase: "starting" }, { model: "image", engine: null, variant: null }))!;
    expect(view).toMatchObject({ phase: "starting", headline: "Starting to load the image model…", detail: "", pill: "", fraction: null, after: null });
    expect(loadView(loading({ phase: "starting", elapsed_s: 3.7 }))!).toMatchObject({ detail: "3 s", pill: "3 s" });
  });

  it("says how much of a download has arrived, and of what, when the size is known", () => {
    const view = loadView(loading({ phase: "downloading", done_bytes: 2.1 * GIB, total_bytes: 4.5 * GIB, elapsed_s: 185 }))!;
    expect(view.headline).toBe("Downloading Qwen3-TTS for designing a voice");
    expect(view.detail).toBe("2.1 GB of about 4.5 GB · 3 min 5 s");
    expect(view.pill).toBe("2.1 GB of 4.5 GB");
    expect(view.fraction).toBeCloseTo(2.1 / 4.5, 5);
  });

  it("says only how much has arrived when no size is known, and has no fraction to fill", () => {
    const view = loadView(loading({ phase: "downloading", done_bytes: 512 * MIB, total_bytes: null, elapsed_s: 12 }, { model: "speech", engine: "kokoro", variant: "default" }))!;
    expect(view).toMatchObject({ headline: "Downloading Kokoro", detail: "512 MB so far · 12 s", pill: "512 MB", fraction: null });
    expect(loadView(loading({ phase: "downloading", done_bytes: 512 * MIB, total_bytes: 0 }))!.fraction).toBeNull();   // a size of nothing is none
  });

  it("leaves out the time before the first second, and never fills the bar past its end or below its start", () => {
    expect(loadView(loading({ phase: "downloading", done_bytes: 1 * MIB, total_bytes: 4 * GIB, elapsed_s: 0.4 }))!.detail).toBe("1.0 MB of about 4.0 GB");
    expect(loadView(loading({ phase: "downloading", done_bytes: 5 * GIB, total_bytes: 4 * GIB }))!.fraction).toBe(1);
    expect(loadView(loading({ phase: "downloading", done_bytes: -5, total_bytes: 4 * GIB }))!.fraction).toBe(0);
    expect(loadView(loading({ phase: "downloading", done_bytes: 0, total_bytes: 4 * GIB }))!.fraction).toBe(0);
  });

  it("says loading into memory as the time it has taken and nothing else: it has no measure", () => {
    const view = loadView(loading({ phase: "loading", done_bytes: 4.5 * GIB, total_bytes: 4.5 * GIB, elapsed_s: 40 }, { model: "music", engine: null, variant: null }))!;
    expect(view).toMatchObject({ phase: "loading", headline: "Loading the music model into memory", detail: "40 s", pill: "40 s", fraction: null });
  });

  it("says which model was unloaded to make room, in each phase", () => {
    for (const phase of ["starting", "downloading", "loading"] as const) {
      expect(loadView(loading({ phase, after: "speech:qwen3-tts:custom" }))!.after).toBe("Qwen3-TTS for preset voices was unloaded to make room.");
    }
    expect(loadView(loading({ phase: "loading", after: null }))!.after).toBeNull();
  });

  it("keeps the headline, which is what a screen reader is told, the same while only the numbers change", () => {
    const a = loadView(loading({ phase: "downloading", done_bytes: 1 * GIB, total_bytes: 4 * GIB, elapsed_s: 10 }))!;
    const b = loadView(loading({ phase: "downloading", done_bytes: 2 * GIB, total_bytes: 4 * GIB, elapsed_s: 20 }))!;
    expect(a.headline).toBe(b.headline);
    expect(a.detail).not.toBe(b.detail);
    expect(loadView(loading({ phase: "loading" }))!.headline).not.toBe(a.headline);   // but a change of phase is a change
  });
});

describe("loadAnnouncement", () => {
  it("is the headline, as a sentence", () => {
    expect(loadAnnouncement(loadView(loading({ phase: "downloading" }))!)).toBe("Downloading Qwen3-TTS for designing a voice.");
    expect(loadAnnouncement(loadView(loading({ phase: "loading" }))!)).toBe("Loading Qwen3-TTS for designing a voice into memory.");
    expect(loadAnnouncement(loadView(loading({ phase: "starting" }))!)).toBe("Starting to load Qwen3-TTS for designing a voice.");   // not "…."
  });

  it("carries no number, which would be said every second", () => {
    // not `/\d/`: the name Qwen3-TTS has a digit in it. What must be absent is a size or a time, and the same words must come out whatever the numbers are.
    const a = loadAnnouncement(loadView(loading({ phase: "downloading", done_bytes: 3 * GIB, total_bytes: 4 * GIB, elapsed_s: 99 }))!);
    const b = loadAnnouncement(loadView(loading({ phase: "downloading", done_bytes: 1 * MIB, total_bytes: 9 * GIB, elapsed_s: 5 }))!);
    expect(a).not.toMatch(/\b(MB|GB|min|h)\b|\d+ s\b|%/);
    expect(a).toBe(b);
  });
});
