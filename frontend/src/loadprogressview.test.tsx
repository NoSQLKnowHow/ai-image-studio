import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Header } from "./components/Header";
import { LoadProgressView } from "./components/LoadProgress";
import { RunCard } from "./components/RunCard";
import { SpeechPanel } from "./components/SpeechPanel";
import { TrackCard } from "./components/TrackCard";
import { VectorCard } from "./components/VectorCard";
import { AddVoiceDialog, DesignVoiceDialog } from "./components/VoiceDialogs";
import { createEmitter } from "./emitter";
import type { Passage, SpeechJob, StagedRecording } from "./speechTypes";
import { memoryStore, server, type Answer, type Call } from "./speechTestSupport";
import { CAPS, makeMusicRun, makeRun, makeTrack, makeVectorRun, SPEECH_CONTRACT, STATUS } from "./testdata";
import type { LoadProgress, WorkerStatus } from "./types";
import type { PassageState } from "./usePassage";

// Where the page shows how far a model's load has got (DESIGN.md section 39.7, criteria 300 to 305): the shared view, the header's pill and panel, the three kinds of card that
// wait for the model, the Speech tab, and the two voice dialogs. The wording itself is in loadprogress.test.ts.

const GIB = 1024 ** 3;
const MIB = 1024 ** 2;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const worker = (progress: Partial<LoadProgress> | null, over: Partial<WorkerStatus> = {}): WorkerStatus => ({
  ...STATUS.worker, state: "loading", model: "speech", engine: "qwen3-tts", variant: "design",
  progress: progress === null ? null : { phase: "downloading", done_bytes: 2 * GIB, total_bytes: 4 * GIB, elapsed_s: 185, after: null, ...progress }, ...over,
});

// ------------------------------------------------------------------ the shared view
describe("the view of a load", () => {
  it("shows nothing when no load is under way, or the server did not say how far", () => {
    const { container, rerender } = render(<LoadProgressView worker={null} />);
    expect(container.firstChild).toBeNull();
    rerender(<LoadProgressView worker={worker(null)} />);
    expect(container.firstChild).toBeNull();
    rerender(<LoadProgressView worker={worker({}, { state: "ready" })} />);
    expect(container.firstChild).toBeNull();
    rerender(<LoadProgressView worker={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("says what is downloading, how much has arrived and of what, and shows it as a bar that is half full", () => {
    render(<LoadProgressView worker={worker({ done_bytes: 2 * GIB, total_bytes: 4 * GIB, elapsed_s: 185 })} />);
    expect(screen.getByText("Downloading Qwen3-TTS for designing a voice")).toBeTruthy();
    expect(screen.getByText(/2\.0 GB of about 4\.0 GB · 3 min 5 s/)).toBeTruthy();
    const bar = screen.getByRole("progressbar", { name: "Downloading Qwen3-TTS for designing a voice" });
    expect(bar.getAttribute("aria-valuenow")).toBe("50");
    expect(bar.className).not.toContain("indeterminate");
    expect((bar.firstChild as HTMLElement).style.width).toBe("50%");   // written as 50.0%, and read back as the CSS parser normalises it
  });

  it("shows a bar that moves instead of one that fills where no size is known, and does not give it a value it does not have", () => {
    render(<LoadProgressView worker={worker({ total_bytes: null, done_bytes: 300 * MIB })} />);
    expect(screen.getByText(/300 MB so far/)).toBeTruthy();
    const bar = screen.getByRole("progressbar");
    expect(bar.className).toContain("indeterminate");
    expect(bar.hasAttribute("aria-valuenow")).toBe(false);
  });

  it("says loading into memory with only the time, as a bar that moves", () => {
    render(<LoadProgressView worker={worker({ phase: "loading", elapsed_s: 40 })} />);
    expect(screen.getByText("Loading Qwen3-TTS for designing a voice into memory")).toBeTruthy();
    expect(screen.getByText(/· 40 s/)).toBeTruthy();
    expect(screen.queryByText(/GB/)).toBeNull();
    expect(screen.getByRole("progressbar").className).toContain("indeterminate");
  });

  it("says which model was unloaded to make room, only when one was", () => {
    const { rerender } = render(<LoadProgressView worker={worker({ after: "speech:qwen3-tts:custom" })} />);
    expect(screen.getByText("Qwen3-TTS for preset voices was unloaded to make room.")).toBeTruthy();
    rerender(<LoadProgressView worker={worker({ after: null })} />);
    expect(screen.queryByText(/was unloaded to make room/)).toBeNull();
  });

  it("keeps its numbers out of the live region: they change every second and would be read out each time", () => {
    render(<LoadProgressView worker={worker({})} />);
    expect(screen.getByText(/2\.0 GB of about 4\.0 GB/).getAttribute("aria-live")).toBe("off");
    expect(screen.queryByRole("status")).toBeNull();                                   // and it announces nothing unless it is told this is the place to
  });
});

describe("what a screen reader is told (criterion 304)", () => {
  it("is the phase, once, where the view is told to announce, and not every time the numbers move", () => {
    const { rerender } = render(<LoadProgressView worker={worker({ done_bytes: 1 * GIB })} announce />);
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("Downloading Qwen3-TTS for designing a voice.");
    rerender(<LoadProgressView worker={worker({ done_bytes: 2 * GIB, elapsed_s: 200 })} announce />);
    expect(screen.getByRole("status").textContent).toBe("Downloading Qwen3-TTS for designing a voice.");   // the same words: nothing new to say
    expect(screen.getByRole("status").textContent).not.toMatch(/\b(MB|GB|min)\b|\d+ s\b|%/);   // no size, no time: those change every second
    rerender(<LoadProgressView worker={worker({ phase: "loading" })} announce />);
    expect(screen.getByRole("status").textContent).toBe("Loading Qwen3-TTS for designing a voice into memory.");   // a change of phase is said
  });

  it("says a different model when the model that is loading changes", () => {
    const { rerender } = render(<LoadProgressView worker={worker({})} announce />);
    rerender(<LoadProgressView worker={worker({}, { variant: "clone" })} announce />);
    expect(screen.getByRole("status").textContent).toBe("Downloading Qwen3-TTS for your own voices.");
  });

  it("says nothing once the load is over", () => {
    const { rerender } = render(<LoadProgressView worker={worker({})} announce />);
    rerender(<LoadProgressView worker={worker({}, { state: "ready", progress: null })} announce />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});

// ------------------------------------------------------------------ the header
describe("the header's pill and its panel (criterion 300)", () => {
  const header = (w: WorkerStatus, tab: "image" | "music" | "speech" = "speech") =>
    render(<Header status={{ ...STATUS, worker: w }} now={Date.now()} busy={false} tab={tab} musicAvailable speech={{ available: true, engine: "qwen3-tts" }} onModel={vi.fn(async () => true)} />);

  it("adds how far the download has got to the pill, in a short form, beside its name", () => {
    header(worker({ done_bytes: 2 * GIB, total_bytes: 4 * GIB }));
    const pill = screen.getByRole("button", { name: /Loading speech model/ });
    expect(pill.textContent).toContain("Loading speech model…");
    expect(pill.textContent).toContain("· 2.0 GB of 4.0 GB");
  });

  it("says only the time while the model is loaded into memory", () => {
    header(worker({ phase: "loading", elapsed_s: 42 }));
    expect(screen.getByRole("button", { name: /Loading speech model/ }).textContent).toContain("· 42 s");
  });

  it("shows the whole view in the panel that the pill opens", () => {
    header(worker({ after: "speech:qwen3-tts:custom" }));
    expect(screen.queryByText(/Downloading Qwen3-TTS for designing a voice/)).toBeNull();   // not until it is opened
    fireEvent.click(screen.getByRole("button", { name: /Loading speech model/ }));
    const panel = screen.getByRole("region", { name: "Model details" });
    expect(within(panel).getByText("Downloading Qwen3-TTS for designing a voice")).toBeTruthy();
    expect(within(panel).getByText("Qwen3-TTS for preset voices was unloaded to make room.")).toBeTruthy();
  });

  it("is as it was when the server says nothing of how far (an older one), and once the model is ready", () => {
    header(worker(null));
    expect(screen.getByRole("button", { name: /Loading speech model/ }).textContent).toBe("Loading speech model…");
    cleanup();
    header(worker(null, { state: "ready", unload_at: null }));
    expect(screen.getByRole("button", { name: /Speech model ready/ }).textContent).not.toMatch(/GB|MB|\d+ s/);
  });

  it("speaks for the image and music models as well", () => {
    header(worker({ done_bytes: 10 * GIB, total_bytes: 31 * GIB }, { model: "image", engine: null, variant: null }), "image");
    expect(screen.getByRole("button", { name: /Loading image model/ }).textContent).toContain("· 10.0 GB of 31.0 GB");
    cleanup();
    header(worker({ phase: "loading", elapsed_s: 7 }, { model: "music", engine: null, variant: null }), "music");
    expect(screen.getByRole("button", { name: /Loading music model/ }).textContent).toContain("· 7 s");
  });
});

// ------------------------------------------------------------------ the cards that wait for the model
const nothing = () => undefined;
const projects = { projects: [], nameMax: 60, onFile: nothing, onUnfile: nothing, onCreate: async () => null };
const NOW = Date.parse("2026-10-15T10:00:00.000Z");

describe("a run that waits for the model says how far the model has got (criterion 301)", () => {
  const loadingImage = worker({ done_bytes: 12 * GIB, total_bytes: 31 * GIB }, { model: "image", engine: null, variant: null });

  const image = (run = makeRun({ status: "running", progress: null }), w: WorkerStatus | null = loadingImage) =>
    render(<RunCard run={run} now={NOW} workerState={w?.state ?? null} worker={w} canEdit making4k={new Set()} enlarging={new Set()} enlargeWaiting={new Set()} transient={false} viewScope="kept"
      upscaler={null} onReuse={nothing} onRegenerateLarger={nothing} onMake4K={nothing} onEnlarge={nothing} onEditThis={nothing} onRetry={nothing} onCancel={nothing} onToggleKeep={nothing}
      onRestore={nothing} onDelete={nothing} onCopy={nothing} onOpenImage={nothing} projects={projects} />);
  const music = (run = makeMusicRun({ status: "running", progress: null }), w: WorkerStatus | null = worker({ phase: "loading", elapsed_s: 30 }, { model: "music", engine: null, variant: null })) =>
    render(<TrackCard run={run} now={NOW} workerState={w?.state ?? null} worker={w} transient={false} viewScope="kept" projects={projects} onReuse={nothing} onRetry={nothing} onCancel={nothing}
      onToggleKeep={nothing} onRestore={nothing} onDelete={nothing} onDeleteTrack={nothing} onCopy={nothing} />);
  const vector = (run = makeVectorRun({ status: "running", progress: null }), w: WorkerStatus | null = worker({ done_bytes: 1 * GIB, total_bytes: 2 * GIB }, { model: "image", engine: null, variant: null })) =>
    render(<VectorCard run={run} now={NOW} workerState={w?.state ?? null} worker={w} transient={false} viewScope="kept" projects={projects} onOpenVersion={nothing} onReuse={nothing} onRetry={nothing}
      onCancel={nothing} onToggleKeep={nothing} onRestore={nothing} onDelete={nothing} onCopySvg={nothing} />);

  it("an image: the download of the image model, with what has arrived of what", () => {
    image();
    expect(screen.getByText("Downloading the image model")).toBeTruthy();
    expect(screen.getByText(/12\.0 GB of about 31\.0 GB/)).toBeTruthy();
    expect(screen.queryByText(/the first load takes a while/)).toBeNull();
  });

  it("a track: the loading of the music model into memory, with the time", () => {
    music();
    expect(screen.getByText("Loading the music model into memory")).toBeTruthy();
    expect(screen.getByText(/^· 30 s$/)).toBeTruthy();   // not /· 30 s/: "· 30 steps" is in the card too
  });

  it("a vector drawing: the same, for the model it waits for", () => {
    vector();
    expect(screen.getByText("Downloading the image model")).toBeTruthy();
    expect(screen.getByText(/1\.0 GB of about 2\.0 GB/)).toBeTruthy();
  });

  it("keeps the line it has always had when the server says nothing of how far (an older one)", () => {
    image(undefined, worker(null, { model: "image", engine: null, variant: null }));
    expect(screen.getByText("Loading the model… (the first load takes a while)")).toBeTruthy();
    cleanup();
    music(undefined, worker(null, { model: "music", engine: null, variant: null }));
    expect(screen.getByText("Loading the music model… (the first load takes a while)")).toBeTruthy();
    cleanup();
    vector(undefined, worker(null, { model: "image", engine: null, variant: null }));
    expect(screen.getByText("Loading the model… (the first load takes a while)")).toBeTruthy();
  });

  it("keeps the line it has always had when no worker is passed at all", () => {
    image(undefined, null);
    expect(screen.getByText("Starting…")).toBeTruthy();
  });

  it("says it is stopping, and not how far the model has got, once Cancel has been pressed", () => {
    image(makeRun({ status: "running", progress: null, canceling: true }));
    expect(screen.getByText(/^Stopping… \(a model that is still loading finishes loading first\)/)).toBeTruthy();
    expect(screen.queryByText(/Downloading/)).toBeNull();
  });

  it("a track and a drawing say they are stopping too, not how far the model has got", () => {
    music(makeMusicRun({ status: "running", progress: null, canceling: true }));
    expect(screen.getByText(/^Stopping… \(a model that is still loading finishes loading first\)/)).toBeTruthy();
    expect(screen.queryByText(/Loading the music model into memory/)).toBeNull();
    cleanup();
    vector(makeVectorRun({ status: "running", progress: null, canceling: true }));
    expect(screen.getByText(/^Stopping… \(a model that is still loading finishes loading first\)/)).toBeTruthy();
    expect(screen.queryByText(/Downloading/)).toBeNull();
  });

  it("is not shown by a track or a drawing either when the worker is not loading", () => {
    music(undefined, worker({ phase: "loading" }, { state: "busy", model: "music", engine: null, variant: null }));
    expect(screen.queryByText(/into memory/)).toBeNull();
    expect(screen.getByText("Starting…")).toBeTruthy();
    cleanup();
    vector(undefined, worker({}, { state: "ready", model: "image", engine: null, variant: null }));
    expect(screen.queryByText(/Downloading/)).toBeNull();
    expect(screen.getByText("Starting…")).toBeTruthy();
  });

  it("is the run's own progress, not the model's, once the run has started", () => {
    image(makeRun({ status: "running", progress: { image: 1, of: 1, step: 5, steps: 20 } as never }));
    expect(screen.getByText(/step 5 of 20/)).toBeTruthy();
    expect(screen.queryByText(/Downloading/)).toBeNull();
  });

  it("is not shown when the worker is not loading", () => {
    image(undefined, worker({}, { state: "busy", model: "image", engine: null, variant: null }));
    expect(screen.queryByText(/Downloading/)).toBeNull();
    expect(screen.getByText("Starting…")).toBeTruthy();
  });

  it("puts the track's progress where the music model has none yet, and a track that is done shows nothing of it", () => {
    music(makeMusicRun({ status: "done", tracks: [makeTrack(0)] }));
    expect(screen.queryByText(/Loading the music model into memory/)).toBeNull();
  });
});

// ------------------------------------------------------------------ the Speech tab
const PASSAGE = SPEECH_CONTRACT.passage as unknown as Passage;
const DESIGN_DONE = SPEECH_CONTRACT.design_job as unknown as SpeechJob;
const STAGED = SPEECH_CONTRACT.staged_recording as unknown as StagedRecording;
const loaded: PassageState = { passage: PASSAGE, loading: false, problem: null, retry: vi.fn() };
const ENGINE = { id: "qwen3-tts", title: "Qwen3-TTS" };
const flush = () => act(async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
});

beforeEach(() => {
  vi.spyOn(window.HTMLMediaElement.prototype, "play").mockImplementation(async () => undefined);
  vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});

describe("the Speech tab (criterion 302)", () => {
  it("shows how far a speech model that is loading has got, under the engines", () => {
    server(() => ({ body: { presets: [], saved: [] } }));
    render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} worker={worker({ after: "speech:qwen3-tts:custom" })} />);
    expect(screen.getByText("Downloading Qwen3-TTS for designing a voice")).toBeTruthy();
    expect(screen.getByText("Qwen3-TTS for preset voices was unloaded to make room.")).toBeTruthy();
  });

  it("does not show the load of the image or the music model, which is not the Speech tab's business", () => {
    server(() => ({ body: { presets: [], saved: [] } }));
    render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} worker={worker({}, { model: "image", engine: null, variant: null })} />);
    expect(screen.queryByText(/Downloading/)).toBeNull();
  });

  it("shows nothing when no worker is passed", () => {
    server(() => ({ body: { presets: [], saved: [] } }));
    render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} />);
    expect(screen.queryByText(/Downloading|Loading .* into memory/)).toBeNull();
  });

  // The tab hands the worker on to the two dialogs it opens: each shows the view as well as the tab's own, so the words are there twice once a dialog is waiting for the model.
  describe("and the dialogs it opens", () => {
    const tab = () => {
      server((call) => {
        if (call.url === "/api/speech/passage") return { body: PASSAGE };
        if (call.url === "/api/speech/voices/design") return { body: { ...DESIGN_DONE, status: "queued", files: [] } };
        if (call.url === "/api/speech/recordings") return { body: STAGED };
        if (call.url === "/api/speech/samples") return { body: { ready: false, url: null, job: { ...DESIGN_DONE, status: "queued", files: [] } } };
        return { status: 404, body: { detail: "n/a", code: "not_found" } };
      });
      render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} jobs={createEmitter<SpeechJob>()} worker={worker({})} />);
    };
    const DOWNLOADING = "Downloading Qwen3-TTS for designing a voice";

    it("Design a voice, while its samples are made", async () => {
      tab();
      await flush();
      expect(screen.getAllByText(DOWNLOADING)).toHaveLength(1);                           // the tab's own
      fireEvent.click(screen.getByRole("button", { name: "Design a voice" }));
      await flush();
      fireEvent.change(screen.getByRole("textbox", { name: "Describe the voice" }), { target: { value: "a warm Irish woman in her forties" } });
      fireEvent.click(screen.getByRole("button", { name: "Make three samples" }));
      await flush();
      expect(screen.getAllByText(DOWNLOADING)).toHaveLength(2);                           // and the dialog's
    });

    it("Add my voice, while the new voice is made to speak", async () => {
      tab();
      await flush();
      fireEvent.click(screen.getByRole("button", { name: "Add my voice" }));
      await flush();
      fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [new File([new Uint8Array(5000)], "me.wav", { type: "audio/wav" })] } });
      await flush();
      expect(screen.getAllByText(DOWNLOADING)).toHaveLength(1);
      fireEvent.click(screen.getByRole("button", { name: /Play sample of the new voice/ }));
      await flush();
      expect(screen.getAllByText(DOWNLOADING)).toHaveLength(2);
    });
  });
});

// ------------------------------------------------------------------ the two voice dialogs
describe("Design a voice, while the three samples wait for the model (criterion 303)", () => {
  function open(w: WorkerStatus | null, answer: (call: Call) => Answer = () => ({ body: { ...DESIGN_DONE, status: "queued", files: [] } })) {
    server(answer);
    return render(<DesignVoiceDialog open onOpenChange={vi.fn()} engine={ENGINE} passage={loaded} jobs={createEmitter<SpeechJob>()} worker={w} onSaved={vi.fn()} />);
  }
  const ask = async () => {
    fireEvent.change(screen.getByRole("textbox", { name: "Describe the voice" }), { target: { value: "a warm Irish woman in her forties" } });
    fireEvent.click(screen.getByRole("button", { name: "Make three samples" }));
    await flush();
  };

  it("shows the download, and says its phase to a screen reader, once the samples have been asked for", async () => {
    open(worker({ after: "speech:qwen3-tts:custom" }));
    expect(screen.queryByText("Downloading Qwen3-TTS for designing a voice")).toBeNull();   // nothing to wait for yet
    await ask();
    expect(screen.getByText(/Making three samples…/)).toBeTruthy();
    expect(screen.getByText("Downloading Qwen3-TTS for designing a voice")).toBeTruthy();
    expect(screen.getByText("Qwen3-TTS for preset voices was unloaded to make room.")).toBeTruthy();
    expect(screen.getAllByRole("status").some((s) => s.textContent === "Downloading Qwen3-TTS for designing a voice.")).toBe(true);
  });

  it("is as it was when the model is not loading, or the server does not say how far", async () => {
    open(worker({}, { state: "ready", progress: null }));
    await ask();
    expect(screen.getByText(/Making three samples…/)).toBeTruthy();
    expect(screen.queryByText(/Downloading|into memory/)).toBeNull();
    cleanup();
    open(null);
    await ask();
    expect(screen.queryByText(/Downloading|into memory/)).toBeNull();
  });
});

describe("Add my voice, while the new voice is made to speak (criterion 303)", () => {
  it("shows how far the model has got while the sample of the new voice is made", async () => {
    // the sample of the new voice is a job that waits its turn: the answer to the request is "not ready yet" and carries the job (SampleAnswer), and nothing ends it in this test
    const queued = { ready: false, url: null, job: { ...DESIGN_DONE, status: "queued", files: [] } };
    server((call) => (call.url === "/api/speech/samples" ? { body: queued } : call.url === "/api/speech/recordings" ? { body: STAGED } : { body: queued.job }));
    render(<AddVoiceDialog open onOpenChange={vi.fn()} engine={ENGINE} limits={CAPS.limits.speech} passage={loaded} jobs={createEmitter<SpeechJob>()}
      worker={worker({ phase: "loading", elapsed_s: 20 }, { variant: "clone" })} onSaved={vi.fn()} />);
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [new File([new Uint8Array(5000)], "me.wav", { type: "audio/wav" })] } });
    await flush();
    expect(screen.queryByText(/into memory/)).toBeNull();                                  // before the sample is asked for
    fireEvent.click(screen.getByRole("button", { name: /Play sample of the new voice/ }));
    await flush();
    expect(screen.getByText("Loading Qwen3-TTS for your own voices into memory")).toBeTruthy();
  });
});
