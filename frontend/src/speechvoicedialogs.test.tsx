import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SpeechVoices } from "./components/SpeechVoices";
import { AddVoiceDialog, ConfirmDeleteVoice, DesignVoiceDialog } from "./components/VoiceDialogs";
import { createEmitter } from "./emitter";
import type { Passage, PresetVoice, SavedVoice, SpeechJob, StagedRecording } from "./speechTypes";
import { type Answer, type Call, memoryStore, server } from "./speechTestSupport";
import { SpeechPanel } from "./components/SpeechPanel";
import { CAPS, SPEECH_CONTRACT } from "./testdata";
import type { PassageState } from "./usePassage";
import { NO_VOICE_FILTER } from "./speech";

// The dialogs for a voice of one's own, the voices' own list and the panel around them (DESIGN.md §38.4): what is asked for and sent at each step, what is shown of the
// answers, and what a person can and cannot do before the statement is made and the name is given.

const PASSAGE = SPEECH_CONTRACT.passage as unknown as Passage;
const DESIGN_DONE = SPEECH_CONTRACT.design_job as unknown as SpeechJob;
const STAGED = SPEECH_CONTRACT.staged_recording as unknown as StagedRecording;
const DESIGNED = SPEECH_CONTRACT.saved_voice_designed as unknown as SavedVoice;
const CLONED = SPEECH_CONTRACT.saved_voice_cloned as unknown as SavedVoice;
const PRESETS = SPEECH_CONTRACT.engine_voices.presets as unknown as PresetVoice[];
const SAVED = SPEECH_CONTRACT.engine_voices.saved as unknown as SavedVoice[];
const ENGINE = { id: "fake", title: "Fake speech" };

const flush = () => act(async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
});
const loaded: PassageState = { passage: PASSAGE, loading: false, problem: null, retry: vi.fn() };
const queued = (status: SpeechJob["status"] = "queued"): SpeechJob => ({ ...DESIGN_DONE, status, files: status === "done" ? DESIGN_DONE.files : [] });
const press = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

let plays: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  plays = vi.spyOn(window.HTMLMediaElement.prototype, "play").mockImplementation(async () => undefined);
  vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------ Design a voice
describe("Design a voice", () => {
  function open(answer: (call: Call) => Answer = () => ({ body: queued() }), passage: PassageState = loaded) {
    const calls = server(answer);
    const jobs = createEmitter<SpeechJob>();
    const onSaved = vi.fn();
    const onOpenChange = vi.fn();
    const view = render(<DesignVoiceDialog open onOpenChange={onOpenChange} engine={ENGINE} passage={passage} jobs={jobs} onSaved={onSaved} />);
    return { calls, jobs, onSaved, onOpenChange, ...view };
  }
  const describe_ = () => screen.getByRole("textbox", { name: "Describe the voice" }) as HTMLTextAreaElement;
  const type = (text: string) => fireEvent.change(describe_(), { target: { value: text } });
  const ask = async () => {
    type("a warm Irish woman in her forties");
    press("Make three samples");
    await flush();
  };

  it("says what it does, and nothing is shown when it is closed", () => {
    cleanup();
    server(() => ({ body: {} }));
    render(<DesignVoiceDialog open={false} onOpenChange={vi.fn()} engine={ENGINE} passage={loaded} jobs={createEmitter()} onSaved={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    cleanup();
    open();
    expect(screen.getByRole("heading", { name: "Design a voice" })).toBeTruthy();
    expect(screen.getByText(/Fake speech makes three short clips of the same passage in it/)).toBeTruthy();
    expect(screen.getByText(/Whether a particular accent is held is not promised/)).toBeTruthy();
  });

  it("cannot ask for samples until there is a description, nor with one that is too long", () => {
    open();
    expect(button("Make three samples").disabled).toBe(true);
    type("   ");
    expect(button("Make three samples").disabled).toBe(true);
    type("a warm voice");
    expect(button("Make three samples").disabled).toBe(false);
    type("x".repeat(301));
    expect(button("Make three samples").disabled).toBe(true);
    expect(screen.getByText("A description can be at most 300 characters; this one is 301.")).toBeTruthy();
    expect(describe_().getAttribute("aria-invalid")).toBe("true");
    type("x".repeat(300));
    expect(button("Make three samples").disabled).toBe(false);
    expect(describe_().hasAttribute("aria-invalid")).toBe(false);
  });

  it("asks for three samples of the description for the engine, says it is working, and keeps the box still", async () => {
    const t = open();
    await ask();
    const request = t.calls.find((c) => c.url === "/api/speech/voices/design") as Call;
    expect(JSON.parse(request.init.body as string)).toEqual({ description: "a warm Irish woman in her forties", engine: "fake" });
    expect(screen.getByRole("status").textContent).toContain("Making three samples…");
    expect(describe_().disabled).toBe(true);
    expect(button("Make three samples").disabled).toBe(true);
  });

  it("lists the three samples when the event says they are made, each with a Play button, and none chosen", async () => {
    const t = open();
    await ask();
    act(() => t.jobs.emit(queued("done")));
    await flush();
    const samples = within(screen.getByRole("group", { name: "The samples" }));
    expect(samples.getAllByRole("radio").map((r) => (r as HTMLInputElement).checked)).toEqual([false, false, false]);
    expect(samples.getByRole("radio", { name: "Sample 2" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Play sample 3" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Make three new samples" })).toBeTruthy();
    expect(screen.getByText("Choose the sample you want to keep.")).toBeTruthy();
  });

  it("plays a sample, and stops it when pressed again", async () => {
    const t = open();
    await ask();
    act(() => t.jobs.emit(queued("done")));
    await flush();
    const audio = document.querySelector("audio") as HTMLAudioElement;
    press("Play sample 1");
    await flush();
    expect(audio.getAttribute("src")).toBe("/api/speech/candidates/7f6108c5034732e60e81230e03fd5041/0.wav");
    expect(plays).toHaveBeenCalledTimes(1);
    expect(button("Stop sample 1").textContent).toBe("Stop");
    press("Stop sample 1");
    expect(button("Play sample 1").textContent).toBe("Play");
    expect(audio.hasAttribute("src")).toBe(false);
  });

  it("keeps a sample only once one is chosen and a name is given, by the job it came from, and tells the page", async () => {
    const t = open((call) => (call.url === "/api/speech/voices" ? { body: DESIGNED } : { body: queued() }));
    await ask();
    act(() => t.jobs.emit(queued("done")));
    await flush();
    expect(button("Save this voice").disabled).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: "Sample 2" }));
    expect(button("Save this voice").disabled).toBe(true); // no name yet
    expect(screen.queryByText("Choose the sample you want to keep.")).toBeNull();
    fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value: "  Warm narrator  " } });
    expect(button("Save this voice").disabled).toBe(false);
    press("Save this voice");
    await flush();
    const save = t.calls.find((c) => c.url === "/api/speech/voices") as Call;
    expect(JSON.parse(save.init.body as string)).toEqual({ kind: "designed", name: "Warm narrator", candidate: { job: DESIGN_DONE.id, index: 1 } });
    expect(t.onSaved).toHaveBeenCalledWith(DESIGNED);
  });

  it("does not take a name that is too long", async () => {
    const t = open();
    await ask();
    act(() => t.jobs.emit(queued("done")));
    await flush();
    fireEvent.click(screen.getByRole("radio", { name: "Sample 1" }));
    fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value: "x".repeat(61) } });
    expect(screen.getByText("A name can be at most 60 characters; this one is 61.")).toBeTruthy();
    expect(button("Save this voice").disabled).toBe(true);
  });

  it("shows why a voice could not be saved, and stays where it is", async () => {
    const t = open((call) => (call.url === "/api/speech/voices" ? { status: 409, body: { detail: "A voice with that name already exists.", code: "name_taken" } } : { body: queued() }));
    await ask();
    act(() => t.jobs.emit(queued("done")));
    await flush();
    fireEvent.click(screen.getByRole("radio", { name: "Sample 1" }));
    fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value: "Warm narrator" } });
    press("Save this voice");
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("A voice with that name already exists.");
    expect(t.onSaved).not.toHaveBeenCalled();
    expect(button("Save this voice").disabled).toBe(false);
  });

  it("says why the samples could not be made, and offers another try", async () => {
    const t = open();
    await ask();
    act(() => t.jobs.emit({ ...queued("failed"), error: { message: "The voice engine ran out of memory.", hint: "Unload the other model." } }));
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("The voice engine ran out of memory.");
    expect(screen.getByRole("alert").textContent).toContain("Unload the other model.");
    expect(button("Make three new samples").disabled).toBe(false);
    expect(describe_().disabled).toBe(false);
  });

  it("replaces the samples, and the choice, when new ones are made", async () => {
    const t = open();
    await ask();
    act(() => t.jobs.emit(queued("done")));
    await flush();
    fireEvent.click(screen.getByRole("radio", { name: "Sample 3" }));
    press("Make three new samples");
    await flush();
    expect(screen.queryByRole("group", { name: "The samples" })).toBeNull();
    act(() => t.jobs.emit(queued("done")));
    await flush();
    expect((screen.getByRole("radio", { name: "Sample 3" }) as HTMLInputElement).checked).toBe(false);
  });

  it("says when the passage and limits are being loaded, or could not be, and can try again", () => {
    const retry = vi.fn();
    open(() => ({ body: {} }), { passage: null, loading: true, problem: null, retry });
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(button("Make three samples").disabled).toBe(true);
    cleanup();
    open(() => ({ body: {} }), { passage: null, loading: false, problem: { message: "The studio is busy.", hint: "Wait a moment." }, retry });
    expect(screen.getByRole("alert").textContent).toContain("The studio is busy.");
    press("Try again");
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("closes when Close is pressed", () => {
    const t = open();
    press("Close");
    expect(t.onOpenChange).toHaveBeenCalledWith(false);
  });
});

// ------------------------------------------------------------------ Add my voice
describe("Add my voice", () => {
  const limits = CAPS.limits.speech;
  const recording = (name = "me.wav", size = 5000) => new File([new Uint8Array(size)], name, { type: "audio/wav" });
  const choose = async (file: File) => {
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
    await flush();
  };

  function open(answer: (call: Call) => Answer = () => ({ body: STAGED }), passage: PassageState = loaded) {
    const calls = server(answer);
    const jobs = createEmitter<SpeechJob>();
    const onSaved = vi.fn();
    const onOpenChange = vi.fn();
    const view = render(<AddVoiceDialog open onOpenChange={onOpenChange} engine={ENGINE} limits={limits} passage={passage} jobs={jobs} onSaved={onSaved} />);
    return { calls, jobs, onSaved, onOpenChange, ...view };
  }

  it("shows the passage to read, what a recording may be, and why recording is not done here", () => {
    open();
    expect(screen.getByRole("heading", { name: "Add my voice" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Read this aloud" })).toBeTruthy();
    expect(screen.getByText(/On a quiet morning the town wakes slowly/)).toBeTruthy();
    expect(screen.getByText(new RegExp(`A recording of ${limits.voice_seconds.min} to ${limits.voice_seconds.max} seconds`))).toBeTruthy();
    expect(screen.getByText(new RegExp(`up to ${limits.recording_mb} MB`))).toBeTruthy();
    expect(screen.getByText(/only lets a page use the microphone on https or on this computer, and the studio is on plain http/)).toBeTruthy();
    expect(screen.getByText("Choose a recording")).toBeTruthy();
  });

  it("offers a file box that takes audio, named by its button", () => {
    open();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input.accept).toBe("audio/*,.wav,.mp3,.m4a,.aac,.ogg,.flac,.webm");
    expect(screen.getByLabelText("Choose a recording")).toBe(input);
  });

  it("does not send what is plainly wrong, and says why", async () => {
    const t = open();
    await choose(recording("notes.txt"));
    expect(screen.getByRole("alert").textContent).toMatch(/^notes\.txt is not a kind of recording that can be read/);
    expect(t.calls.some((c) => c.url === "/api/speech/recordings")).toBe(false);
  });

  it("sends a recording, says it is checking, and then shows what the server kept", async () => {
    let release: (r: { body: unknown }) => void = () => undefined;
    const held = new Promise<{ body: unknown }>((resolve) => (release = resolve));
    const t = open(() => held);
    await choose(recording());
    expect(screen.getByRole("status").textContent).toBe("Checking the recording…");
    expect(button("Stop")).toBeTruthy();
    expect((document.querySelector('input[type="file"]') as HTMLInputElement).disabled).toBe(true);
    release({ body: STAGED });
    await flush();
    const upload = t.calls.find((c) => c.url === "/api/speech/recordings") as Call;
    expect(upload.init.method).toBe("POST");
    expect((document.querySelector('input[type="file"]') as HTMLInputElement).value).toBe(""); // let go of, so that the same file chosen again is a change
    expect(screen.getByText("10 s long")).toBeTruthy();
    expect(screen.getByText("Choose another recording")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the server's words when it refuses a recording, and the person can choose another", async () => {
    let refuse = true;
    open(() => (refuse ? { status: 422, body: { detail: "The recording is only 3 seconds long.", code: "too_short", hint: "Record at least 8 seconds." } } : { body: STAGED }));
    await choose(recording());
    expect(screen.getByRole("alert").textContent).toContain("The recording is only 3 seconds long.");
    expect(screen.getByRole("alert").textContent).toContain("Record at least 8 seconds.");
    refuse = false;
    await choose(recording("again.wav"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("10 s long")).toBeTruthy();
  });

  it("gives up a recording that is being sent when Stop is pressed, without saying it failed", async () => {
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise((_, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    render(<AddVoiceDialog open onOpenChange={vi.fn()} engine={ENGINE} limits={limits} passage={loaded} jobs={createEmitter()} onSaved={vi.fn()} />);
    await choose(recording());
    press("Stop");
    await flush();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    expect((document.querySelector('input[type="file"]') as HTMLInputElement).disabled).toBe(false);
  });

  describe("with a recording the server accepted", () => {
    const accepted = async (answer?: (call: Call) => Answer) => {
      const t = open(answer ?? ((call) => (call.url === "/api/speech/voices" ? { body: CLONED } : call.url === "/api/speech/samples" ? { body: { ready: true, url: "/api/speech/samples/preview.wav", job: null } } : { body: STAGED })));
      await choose(recording());
      return t;
    };
    const name = (value: string) => fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value } });
    const consent = () => screen.getByRole("checkbox") as HTMLInputElement;

    it("lets the person hear the recording back, and stop it", async () => {
      await accepted();
      press("Hear your recording");
      await flush();
      expect(plays).toHaveBeenCalledTimes(1);
      expect(document.querySelectorAll("audio")[0].getAttribute("src")).toBe(STAGED.url);
      press("Stop");
    });

    it("lets the person hear the studio speak in the new voice, asking for a sample from the recording", async () => {
      const t = await accepted();
      expect(screen.getByText(`It speaks: “${PASSAGE.sample_sentence}”`)).toBeTruthy();
      press("Play sample of the new voice");
      await flush();
      const sample = t.calls.find((c) => c.url === "/api/speech/samples") as Call;
      expect(JSON.parse(sample.init.body as string)).toEqual({ engine: "fake", recording: STAGED.id });
      expect(plays).toHaveBeenCalled();
    });

    it("says why the preview could not be made", async () => {
      await accepted((call) => (call.url === "/api/speech/samples" ? { status: 503, body: { detail: "The voice engine is not loaded.", code: "busy", hint: "Load it first." } } : { body: STAGED }));
      press("Play sample of the new voice");
      await flush();
      expect(screen.getByRole("alert").textContent).toContain("The voice engine is not loaded.");
    });

    it("shows the statement to agree to, unticked, and will not save without it, or without a name", async () => {
      await accepted();
      expect(screen.getByText(PASSAGE.consent)).toBeTruthy();
      expect(consent().checked).toBe(false);
      expect(button("Save this voice").disabled).toBe(true);
      name("Kirk");
      expect(button("Save this voice").disabled).toBe(true); // a name, but no statement
      fireEvent.click(consent());
      expect(button("Save this voice").disabled).toBe(false);
      name("");
      expect(button("Save this voice").disabled).toBe(true); // the statement, but no name
    });

    it("saves the voice for the engine, with the statement made, and tells the page", async () => {
      const t = await accepted();
      name("  My own voice  ");
      fireEvent.click(consent());
      press("Save this voice");
      await flush();
      const save = t.calls.find((c) => c.url === "/api/speech/voices") as Call;
      expect(JSON.parse(save.init.body as string)).toEqual({ kind: "cloned", name: "My own voice", recording: STAGED.id, engine: "fake", consent: true });
      expect(t.onSaved).toHaveBeenCalledWith(CLONED);
    });

    it("shows why a voice could not be saved, and stays", async () => {
      const t = await accepted((call) => (call.url === "/api/speech/voices" ? { status: 409, body: { detail: "A voice with that name already exists.", code: "name_taken" } } : { body: STAGED }));
      name("My own voice");
      fireEvent.click(consent());
      press("Save this voice");
      await flush();
      expect(screen.getByRole("alert").textContent).toContain("A voice with that name already exists.");
      expect(t.onSaved).not.toHaveBeenCalled();
    });

    it("puts the recording away to choose another, and the statement has to be made again for the next", async () => {
      await accepted();
      fireEvent.click(consent());
      press("Put it away");
      expect(screen.queryByText("10 s long")).toBeNull();
      expect(screen.getByText("Choose a recording")).toBeTruthy();
      await choose(recording("second.wav"));
      expect(consent().checked).toBe(false);
    });

    it("says the silence taken from the ends when there was some", async () => {
      await accepted((call) => (call.url === "/api/speech/recordings" ? { body: { ...STAGED, trimmed_seconds: 1.5 } } : { body: {} }));
      expect(screen.getByText("10 s long, with 1.5 s of silence taken from the ends")).toBeTruthy();
    });
  });
});

// ------------------------------------------------------------------ Delete a voice
describe("the question before a voice is deleted", () => {
  const ask = (over: Partial<Parameters<typeof ConfirmDeleteVoice>[0]> = {}) => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(<ConfirmDeleteVoice voice={CLONED} deleting={false} problem={null} onCancel={onCancel} onConfirm={onConfirm} {...over} />);
    return { onCancel, onConfirm };
  };

  it("is not there when there is no voice to ask about", () => {
    ask({ voice: null });
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("names the voice and says what is not lost", () => {
    ask();
    expect(screen.getByRole("heading", { name: "Delete this voice?" })).toBeTruthy();
    expect(screen.getByText(/Delete the voice “My own voice”\? Readings already made with it keep their audio/)).toBeTruthy();
  });

  it("deletes when asked to, and keeps the voice when not", () => {
    const t = ask();
    press("Delete voice");
    expect(t.onConfirm).toHaveBeenCalledTimes(1);
    press("Keep it");
    expect(t.onCancel).toHaveBeenCalledTimes(1);
  });

  it("says it is deleting, and cannot be dismissed or pressed again meanwhile", () => {
    const t = ask({ deleting: true });
    expect(button("Deleting…").disabled).toBe(true);
    expect(button("Keep it").disabled).toBe(true);
    fireEvent.keyDown(screen.getByRole("alertdialog"), { key: "Escape" });
    expect(t.onCancel).not.toHaveBeenCalled();
  });

  it("shows why it could not be deleted", () => {
    ask({ problem: { message: "That voice is being used by a reading that is being made.", hint: "Wait for it to finish." } });
    expect(screen.getByRole("alert").textContent).toContain("being used by a reading that is being made.");
    expect(screen.getByRole("alert").textContent).toContain("Wait for it to finish.");
  });
});

// ------------------------------------------------------------------ the list of voices
describe("the voice list with the person's own voices", () => {
  const sampler = { phase: () => "idle" as const, problem: null, play: vi.fn(), stop: vi.fn() };
  function list(over: Partial<Parameters<typeof SpeechVoices>[0]> = {}) {
    const props = {
      engine: "fake", presets: PRESETS, saved: SAVED, chosen: null, onChoose: vi.fn(), filter: NO_VOICE_FILTER, onFilter: vi.fn(), sampler, loading: false, problem: null, idle: null,
      onRename: vi.fn(async () => null), onDelete: vi.fn(), ...over,
    };
    render(<SpeechVoices {...props} />);
    return props;
  }

  it("has Design a voice and Add my voice above the list only where the engine can", () => {
    list({ tools: { design: vi.fn(), clone: vi.fn() } });
    expect(button("Design a voice")).toBeTruthy();
    expect(button("Add my voice")).toBeTruthy();
    cleanup();
    list({ tools: { design: null, clone: vi.fn() } });
    expect(screen.queryByRole("button", { name: "Design a voice" })).toBeNull();
    expect(button("Add my voice")).toBeTruthy();
    cleanup();
    list({ tools: { design: null, clone: null } });
    expect(screen.queryByRole("button", { name: "Design a voice" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add my voice" })).toBeNull();
    cleanup();
    list();
    expect(screen.queryByRole("button", { name: "Design a voice" })).toBeNull();
  });

  it("presses the buttons it is given", () => {
    const tools = { design: vi.fn(), clone: vi.fn() };
    list({ tools });
    press("Design a voice");
    press("Add my voice");
    expect(tools.design).toHaveBeenCalledTimes(1);
    expect(tools.clone).toHaveBeenCalledTimes(1);
  });

  it("has Rename and Delete for the person's own voices, named by the voice, and not for the presets", () => {
    list();
    expect(button("Rename My own voice")).toBeTruthy();
    expect(button("Delete Warm narrator")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Rename Anna|Delete Anna/ })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^Rename / })).toHaveLength(2);
  });

  it("has neither where the page gives it neither", () => {
    list({ onRename: undefined, onDelete: undefined });
    expect(screen.queryByRole("button", { name: /^Rename / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete / })).toBeNull();
  });

  it("asks the page to delete the voice that was pressed", () => {
    const t = list();
    press("Delete My own voice");
    expect(t.onDelete).toHaveBeenCalledWith(SAVED[0]);
  });

  describe("renaming", () => {
    it("opens a box with the name in it, and Enter keeps the new name, closes the box and puts focus back on the button", async () => {
      vi.useFakeTimers();
      try {
        const t = list();
        press("Rename My own voice");
        const box = screen.getByRole("textbox", { name: "New name for My own voice" }) as HTMLInputElement;
        expect(box.value).toBe("My own voice");
        fireEvent.change(box, { target: { value: "  Kirk  " } });
        await act(async () => void fireEvent.submit(box.closest("form") as HTMLFormElement));
        await act(() => vi.advanceTimersByTimeAsync(10));
        expect(t.onRename).toHaveBeenCalledWith(SAVED[0], "  Kirk  "); // the page trims it
        expect(screen.queryByRole("textbox")).toBeNull();
        expect(document.activeElement).toBe(button("Rename My own voice"));
      } finally {
        vi.useRealTimers();
      }
    });

    it("leaves the name as it was on Escape or Cancel, and asks nothing", async () => {
      vi.useFakeTimers();
      try {
        const t = list();
        press("Rename My own voice");
        fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
        await act(() => vi.advanceTimersByTimeAsync(10));
        expect(screen.queryByRole("textbox")).toBeNull();
        expect(document.activeElement).toBe(button("Rename My own voice"));
        press("Rename My own voice");
        press("Cancel");
        expect(screen.queryByRole("textbox")).toBeNull();
        expect(t.onRename).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });

    it("does not ask the server when the name is the same, only closes", async () => {
      const t = list();
      press("Rename My own voice");
      await act(async () => void fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement));
      expect(t.onRename).not.toHaveBeenCalled();
      expect(screen.queryByRole("textbox")).toBeNull();
      press("Rename My own voice");
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "  My own voice  " } }); // the same once trimmed
      await act(async () => void fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement));
      expect(t.onRename).not.toHaveBeenCalled();
    });

    it("cannot be saved with an empty name or one that is too long, and says so", () => {
      list({ nameMax: 10 });
      press("Rename My own voice");
      const box = screen.getByRole("textbox");
      fireEvent.change(box, { target: { value: "" } });
      expect(button("Save").disabled).toBe(true);
      expect(screen.queryByText("Give the voice a name.")).toBeNull(); // (an empty box is not nagged about)
      fireEvent.change(box, { target: { value: "x".repeat(11) } });
      expect(button("Save").disabled).toBe(true);
      expect(screen.getByText("A name can be at most 10 characters; this one is 11.")).toBeTruthy();
      expect(box.getAttribute("aria-invalid")).toBe("true");
      fireEvent.change(box, { target: { value: "x".repeat(10) } });
      expect(button("Save").disabled).toBe(false);
    });

    it("shows why a name was refused, and keeps the box open", async () => {
      const onRename = vi.fn(async () => ({ message: "A voice with that name already exists.", hint: "Choose another name." }));
      list({ onRename });
      press("Rename My own voice");
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "Warm narrator" } });
      await act(async () => void fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement));
      expect(screen.getByRole("alert").textContent).toContain("A voice with that name already exists.");
      expect(screen.getByRole("alert").textContent).toContain("Choose another name.");
      expect(screen.getByRole("textbox")).toBeTruthy();
    });

    it("cannot be pressed again, or put away, while it is being saved", async () => {
      let release: (p: null) => void = () => undefined;
      const onRename = vi.fn(() => new Promise<null>((resolve) => (release = resolve)));
      list({ onRename });
      press("Rename My own voice");
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "Kirk" } });
      await act(async () => void fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement));
      expect(button("Saving…").disabled).toBe(true);
      expect(button("Cancel").disabled).toBe(true);
      expect((screen.getByRole("textbox") as HTMLInputElement).disabled).toBe(true);
      fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
      expect(screen.getByRole("textbox")).toBeTruthy(); // not put away meanwhile
      await act(async () => release(null));
      expect(screen.queryByRole("textbox")).toBeNull();
    });

    it("has the Rename button off while its box is open", () => {
      list();
      press("Rename My own voice");
      expect(button("Rename My own voice").disabled).toBe(true);
    });
  });
});

// ------------------------------------------------------------------ the panel around it
describe("the Speech tab with the person's own voices", () => {
  function tab(extra: { push?: (k: string, t: string) => void; engine?: string } = {}) {
    const calls = server((call) => {
      if (call.url === "/api/speech/passage") return { body: PASSAGE };
      if (call.url === "/api/speech/voices" && call.init.method === "POST") return { body: DESIGNED };
      if (call.url === "/api/speech/voices/design") return { body: { ...DESIGN_DONE, status: "done" } };
      if (call.init.method === "PATCH" || call.init.method === "DELETE") return { status: call.init.method === "DELETE" ? 204 : 200, body: DESIGNED };
      return { status: 404, body: { detail: "n/a", code: "not_found" } };
    });
    const jobs = createEmitter<SpeechJob>();
    const caps = extra.engine ? { ...CAPS, speech: { ...CAPS.speech, engines: CAPS.speech.engines } } : CAPS;
    const store = memoryStore(extra.engine ? { "studio.speech.v1": JSON.stringify({ engine: extra.engine, voice: null, style: "", language: "Auto", filter: { gender: "any", english: false } }) } : {});
    render(<SpeechPanel hidden={false} caps={caps} store={store} push={extra.push as never} jobs={jobs} />);
    return { calls, jobs };
  }
  const voicesAsked = (calls: ReturnType<typeof server>) => calls.asked.filter((e) => e === "fake").length;

  it("has Design a voice and Add my voice for an engine that can, and not for one that cannot", async () => {
    tab();
    await flush();
    expect(button("Design a voice")).toBeTruthy();
    expect(button("Add my voice")).toBeTruthy();
    cleanup();
    tab({ engine: "fake-basic" });
    await flush();
    expect(screen.queryByRole("button", { name: "Design a voice" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add my voice" })).toBeNull();
  });

  it("does not ask for the passage until a dialog is opened, and then asks once", async () => {
    const t = tab();
    await flush();
    expect(t.calls.some((c) => c.url === "/api/speech/passage")).toBe(false);
    press("Design a voice");
    await flush();
    expect(t.calls.filter((c) => c.url === "/api/speech/passage")).toHaveLength(1);
    press("Close");
    press("Add my voice");
    await flush();
    expect(t.calls.filter((c) => c.url === "/api/speech/passage")).toHaveLength(1);
  });

  it("opens the right dialog from each button", async () => {
    tab();
    await flush();
    press("Add my voice");
    await flush();
    expect(screen.getByRole("heading", { name: "Add my voice" })).toBeTruthy();
    press("Close");
    press("Design a voice");
    await flush();
    expect(screen.getByRole("heading", { name: "Design a voice" })).toBeTruthy();
  });

  it("reloads the voices, chooses the voice that was just made, says so, and closes the dialog", async () => {
    const push = vi.fn();
    const t = tab({ push });
    await flush();
    const before = voicesAsked(t.calls);
    press("Design a voice");
    await flush();
    fireEvent.change(screen.getByRole("textbox", { name: "Describe the voice" }), { target: { value: "a warm voice" } });
    press("Make three samples");
    await flush();
    fireEvent.click(screen.getByRole("radio", { name: "Sample 1" }));
    fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value: "Warm narrator" } });
    press("Save this voice");
    await flush();
    expect(push).toHaveBeenCalledWith("success", "Saved the voice “Warm narrator”.");
    expect(screen.queryByRole("heading", { name: "Design a voice" })).toBeNull();
    expect(voicesAsked(t.calls)).toBe(before + 1); // the list was asked for again
    expect((screen.getByRole("radio", { name: "Warm narrator" }) as HTMLInputElement).checked).toBe(true);
  });

  it("renames a voice by the server and reloads the list", async () => {
    const t = tab();
    await flush();
    const before = voicesAsked(t.calls);
    press("Rename My own voice");
    const box = screen.getByRole("textbox", { name: "New name for My own voice" });
    fireEvent.change(box, { target: { value: "  Kirk  " } });
    await act(async () => void fireEvent.submit(box.closest("form") as HTMLFormElement));
    await flush();
    const rename = t.calls.find((c) => c.init.method === "PATCH") as Call;
    expect(rename.url).toBe(`/api/speech/voices/${SAVED[0].id}`);
    expect(JSON.parse(rename.init.body as string)).toEqual({ name: "Kirk" });
    expect(voicesAsked(t.calls)).toBe(before + 1);
  });

  it("shows the server's words when a rename is refused", async () => {
    const calls = server((call) => (call.init.method === "PATCH" ? { status: 409, body: { detail: "A voice with that name already exists.", code: "name_taken" } } : { status: 404, body: { detail: "n/a", code: "not_found" } }));
    void calls;
    render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} jobs={createEmitter()} />);
    await flush();
    press("Rename My own voice");
    const box = screen.getByRole("textbox", { name: "New name for My own voice" });
    fireEvent.change(box, { target: { value: "Warm narrator" } });
    await act(async () => void fireEvent.submit(box.closest("form") as HTMLFormElement));
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("A voice with that name already exists.");
  });

  it("asks before deleting, deletes by the server, reloads the list, and says so", async () => {
    const push = vi.fn();
    const t = tab({ push });
    await flush();
    const before = voicesAsked(t.calls);
    press("Delete My own voice");
    expect(screen.getByText(/Delete the voice “My own voice”\?/)).toBeTruthy();
    expect(t.calls.some((c) => c.init.method === "DELETE")).toBe(false); // nothing yet
    press("Delete voice");
    await flush();
    const del = t.calls.find((c) => c.init.method === "DELETE") as Call;
    expect(del.url).toBe(`/api/speech/voices/${SAVED[0].id}`);
    expect(push).toHaveBeenCalledWith("success", "Deleted the voice “My own voice”.");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(voicesAsked(t.calls)).toBe(before + 1);
  });

  it("keeps the voice when the question is answered with Keep it", async () => {
    const t = tab();
    await flush();
    press("Delete My own voice");
    press("Keep it");
    await flush();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(t.calls.some((c) => c.init.method === "DELETE")).toBe(false);
  });

  it("says why a voice could not be deleted, in the question, which stays", async () => {
    server((call) => (call.init.method === "DELETE" ? { status: 409, body: { detail: "That voice is being used by a reading.", code: "in_use", hint: "Wait for it to finish." } } : { status: 404, body: { detail: "n/a", code: "not_found" } }));
    render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} jobs={createEmitter()} />);
    await flush();
    press("Delete My own voice");
    press("Delete voice");
    await flush();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(within(screen.getByRole("alertdialog")).getByRole("alert").textContent).toContain("That voice is being used by a reading.");
    expect(within(screen.getByRole("alertdialog")).getByRole("alert").textContent).toContain("Wait for it to finish.");
    press("Keep it"); // and the question can be put away, and asked again with nothing left over
    press("Delete My own voice");
    expect(within(screen.getByRole("alertdialog")).queryByRole("alert")).toBeNull();
  });
});


// ------------------------------------------------------------------ more of what the dialogs and the list do
describe("the details of the dialogs and the list", () => {
  const designed = async (answer: (call: Call) => Answer) => {
    server(answer);
    const jobs = createEmitter<SpeechJob>();
    const onSaved = vi.fn();
    render(<DesignVoiceDialog open onOpenChange={vi.fn()} engine={ENGINE} passage={loaded} jobs={jobs} onSaved={onSaved} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Describe the voice" }), { target: { value: "a warm voice" } });
    press("Make three samples");
    await flush();
    act(() => jobs.emit(queued("done")));
    await flush();
    return { onSaved };
  };
  const basic = (call: Call) => (call.url === "/api/speech/voices" ? { body: DESIGNED } : { body: queued() });

  it("does not nag about an empty name, only keeps Save off", async () => {
    await designed(basic);
    fireEvent.click(screen.getByRole("radio", { name: "Sample 1" }));
    expect(screen.queryByText("Give the voice a name.")).toBeNull();
    fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value: "a" } });
    fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value: "" } });
    expect(screen.queryByText("Give the voice a name.")).toBeNull();
    expect(button("Save this voice").disabled).toBe(true);
  });

  it("keeps Save off when a name is given but no sample is chosen", async () => {
    await designed(basic);
    fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value: "Warm narrator" } });
    expect(button("Save this voice").disabled).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: "Sample 3" }));
    expect(button("Save this voice").disabled).toBe(false);
  });

  it("says it is saving, and cannot be pressed again or have its name changed meanwhile", async () => {
    let release: (r: { body: unknown }) => void = () => undefined;
    const held = new Promise<{ body: unknown }>((resolve) => (release = resolve));
    const t = await designed((call) => (call.url === "/api/speech/voices" ? held : { body: queued() }));
    fireEvent.click(screen.getByRole("radio", { name: "Sample 1" }));
    fireEvent.change(screen.getByLabelText("Name this voice"), { target: { value: "Warm narrator" } });
    press("Save this voice");
    await flush();
    expect(button("Saving…").disabled).toBe(true);
    expect((screen.getByLabelText("Name this voice") as HTMLInputElement).disabled).toBe(true);
    release({ body: DESIGNED });
    await flush();
    expect(t.onSaved).toHaveBeenCalledWith(DESIGNED);
  });

  it("has each sample's own Play and Stop: pressing one does not make another say Stop", async () => {
    await designed(basic);
    press("Play sample 2");
    await flush();
    expect(button("Stop sample 2")).toBeTruthy();
    expect(button("Play sample 1")).toBeTruthy();
    expect(button("Play sample 3")).toBeTruthy();
    press("Play sample 3");
    await flush();
    expect(button("Play sample 2")).toBeTruthy();
    expect(button("Stop sample 3")).toBeTruthy();
  });
});

describe("the list's details", () => {
  const sampler = { phase: () => "idle" as const, problem: null, play: vi.fn(), stop: vi.fn() };
  const base = { engine: "fake", presets: PRESETS, saved: SAVED, chosen: null, onChoose: vi.fn(), filter: NO_VOICE_FILTER, onFilter: vi.fn(), sampler, loading: false, problem: null, idle: null };

  it("shows no row of tools when the engine can do neither", () => {
    render(<SpeechVoices {...base} tools={{ design: null, clone: null }} />);
    expect(document.querySelector(".speech-voice-tools")).toBeNull();
  });

  it("does not send a name again when Enter is pressed while it is being saved", async () => {
    let release: (p: null) => void = () => undefined;
    const onRename = vi.fn(() => new Promise<null>((resolve) => (release = resolve)));
    render(<SpeechVoices {...base} onRename={onRename} onDelete={vi.fn()} />);
    press("Rename My own voice");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Kirk" } });
    const form = screen.getByRole("textbox").closest("form") as HTMLFormElement;
    await act(async () => void fireEvent.submit(form));
    await act(async () => void fireEvent.submit(form)); // Enter again, while the first is out
    expect(onRename).toHaveBeenCalledTimes(1);
    await act(async () => release(null));
  });
});

describe("the tab, in the details of deleting", () => {
  const tabWith = (deleteAnswer: (call: Call) => Answer) => {
    server((call) => {
      if (call.url === "/api/speech/samples") return { body: { ready: true, url: "/api/speech/samples/a.wav", job: null } };
      if (call.init.method === "DELETE") return deleteAnswer(call);
      return { status: 404, body: { detail: "n/a", code: "not_found" } };
    });
    render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} jobs={createEmitter()} />);
  };

  it("stops the sample of a voice that is deleted while it plays", async () => {
    tabWith(() => ({ status: 204, body: null }));
    await flush();
    press("Play sample of My own voice");
    await flush();
    const audio = document.querySelector("audio") as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe("/api/speech/samples/a.wav");
    press("Delete My own voice");
    press("Delete voice");
    await flush();
    expect(audio.hasAttribute("src")).toBe(false);
  });

  it("forgets why the last attempt failed when it is tried again", async () => {
    let release: (r: { status: number; body: unknown }) => void = () => undefined;
    let first = true;
    tabWith(() => {
      if (first) {
        first = false;
        return { status: 409, body: { detail: "That voice is being used by a reading.", code: "in_use" } };
      }
      return new Promise((resolve) => (release = resolve));
    });
    await flush();
    press("Delete My own voice");
    press("Delete voice");
    await flush();
    expect(within(screen.getByRole("alertdialog")).getByRole("alert")).toBeTruthy();
    press("Delete voice"); // again: the second attempt is held
    await flush();
    expect(within(screen.getByRole("alertdialog")).queryByRole("alert")).toBeNull();
    expect(button("Deleting…")).toBeTruthy();
    release({ status: 204, body: null });
    await flush();
  });
});
