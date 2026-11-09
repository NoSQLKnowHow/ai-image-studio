import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SpeechPanel } from "./components/SpeechPanel";
import { createEmitter } from "./emitter";
import type { SpeechJob } from "./speechTypes";
import { BOOK, jsonBody, memoryStore, press, prepareBook, prepared, part, server, show, voicesOf } from "./speechTestSupport";
import { CAPS, SPEECH_CONTRACT } from "./testdata";
import type { Capabilities, SpeechRun } from "./types";
import { SPEECH_CHOICE_KEY } from "./speech";

// The Speech tab's second half (DESIGN.md §38.2, §38.4, criteria 259, 261, 262, 264, 267, 289, 292): the engines and how each is, the voices of the engine shown with their
// samples, the style and the language, and Make reading with what it says before it does. The server is a table of answers; the audio element's play and pause are watched.

const NOTHING = { status: 404, body: { detail: "Not expected here.", code: "not_found" } };
const READING = SPEECH_CONTRACT.reading_run;
const radio = (name: RegExp) => screen.getByRole("radio", { name }) as HTMLInputElement;
const engineRadio = (title: string) => radio(new RegExp(`^${title}$`));
const voiceRadio = (name: string) => radio(new RegExp(`^${name}$`));
const describedBy = (element: HTMLElement) => (element.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean).map((id) => document.getElementById(id)?.textContent ?? "").join(" | ");
const voiceList = (name: string) => screen.getByRole("list", { name });
const voiceNames = (list: string) => within(voiceList(list)).getAllByRole("radio").map((r) => (r.closest("label")?.querySelector(".speech-voice-name") as HTMLElement).textContent);
const inCaps = (changes: (engines: Capabilities["speech"]["engines"]) => Capabilities["speech"]["engines"]): Capabilities => ({ ...CAPS, speech: { ...CAPS.speech, engines: changes(CAPS.speech.engines) } });

let play: ReturnType<typeof vi.spyOn>;
let pause: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  server(() => NOTHING);
  play = vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(async () => undefined);
  pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ------------------------------------------------------------------ the engines
describe("the engines", () => {
  it("are listed with what each is and how it is now, and the first that can be used is the one shown", () => {
    show();
    const group = screen.getByRole("group", { name: "Engine" });
    expect(within(group).getAllByRole("radio")).toHaveLength(2);
    expect(engineRadio("Fake speech").checked).toBe(true);
    expect(engineRadio("Fake basic speech").checked).toBe(false);
    expect(within(group).getAllByText("Ready")).toHaveLength(2);
    expect(describedBy(engineRadio("Fake speech"))).toBe("Ready | Test only"); // how it is and its licence are its description, not run into its name
  });

  it("say their licence, and say that it is for non-commercial use where it is, with the notice under the engine (criterion 289)", () => {
    show();
    expect(screen.getByText("Test only")).toBeTruthy();
    expect(screen.getByText("Test only (non-commercial) · non-commercial use only")).toBeTruthy();
    expect(screen.getByText("A test notice for the licence badge.")).toBeTruthy();
    expect(screen.getByText("A stand-in that makes tones, for tests.")).toBeTruthy(); // what the engine says of itself
  });

  it("say nothing of a reason for an engine that is ready or being checked", () => {
    const caps = inCaps((engines) => [{ ...engines[0], state: "ready", reason: "A leftover reason that should not be shown.", hint: "A leftover hint." }, { ...engines[1], state: "checking", reason: "Another leftover." }]);
    show(caps);
    expect(screen.queryByText(/leftover/i)).toBeNull();
  });

  it("say why one cannot be used, and what to do, in words and not only in colour (criterion 259)", () => {
    const caps = inCaps((engines) => [
      engines[0],
      { ...engines[1], state: "not_installed", reason: "Fake basic's libraries are not installed.", hint: "Rebuild the image." },
    ]);
    show(caps);
    expect(screen.getByText("Not installed")).toBeTruthy();
    expect(screen.getByText(/Fake basic's libraries are not installed\./)).toBeTruthy();
    expect(screen.getByText("Rebuild the image.")).toBeTruthy();
  });

  it("say that one is short of memory, with by how much, and that it can still be used", () => {
    const caps = inCaps((engines) => [{ ...engines[0], state: "needs_memory", reason: "It needs 6 GB and 2 GB are free.", hint: "Unload the image model." }, engines[1]]);
    show(caps);
    expect(screen.getByText("Needs more memory")).toBeTruthy();
    expect(screen.getByText(/It needs 6 GB and 2 GB are free\./)).toBeTruthy();
    expect(engineRadio("Fake speech").checked).toBe(true); // short of memory is not unusable: the worker makes room
  });

  it("say they are being checked, with no reason", () => {
    const caps = inCaps((engines) => [{ ...engines[0], state: "checking" }, engines[1]]);
    show(caps);
    expect(screen.getByText("Checking…")).toBeTruthy();
    expect(engineRadio("Fake basic speech").checked).toBe(true); // the first that can be used is shown
  });

  it("show the one remembered if it can be used, and else the first that can", () => {
    show(CAPS, false, { store: memoryStore({ [SPEECH_CHOICE_KEY]: JSON.stringify({ engine: "fake-basic" }) }) });
    expect(engineRadio("Fake basic speech").checked).toBe(true);
    cleanup();
    show(CAPS, false, { store: memoryStore({ [SPEECH_CHOICE_KEY]: JSON.stringify({ engine: "gone" }) }) });
    expect(engineRadio("Fake speech").checked).toBe(true);
    cleanup();
    const caps = inCaps((engines) => [engines[0], { ...engines[1], state: "error", reason: "It failed." }]);
    show(caps, false, { store: memoryStore({ [SPEECH_CHOICE_KEY]: JSON.stringify({ engine: "fake-basic" }) }) });
    expect(engineRadio("Fake speech").checked).toBe(true); // the remembered one cannot be used now
  });

  it("are chosen by their radio, the choice is remembered, and the header is told which engine is shown (criterion 292)", async () => {
    const store = memoryStore();
    const onEngine = vi.fn();
    show(CAPS, false, { store, onEngine });
    expect(onEngine).toHaveBeenLastCalledWith("fake");
    fireEvent.click(engineRadio("Fake basic speech"));
    expect(engineRadio("Fake basic speech").checked).toBe(true);
    expect(onEngine).toHaveBeenLastCalledWith("fake-basic");
    expect(JSON.parse(store.data[SPEECH_CHOICE_KEY]).engine).toBe("fake-basic");
    cleanup();
    show(CAPS, false, { store });
    expect(engineRadio("Fake basic speech").checked).toBe(true); // back as it was left
  });

  it("can be looked at even when one cannot be used: its reason is shown, and there are no voices to list and no Make reading", async () => {
    const caps = inCaps((engines) => [{ ...engines[0], state: "not_installed", reason: "Not installed here.", hint: null }, { ...engines[1], state: "error", reason: "Failed." }]);
    server(() => ({ body: BOOK }));
    show(caps);
    expect(screen.getByText("Choose an engine that is ready to see its voices.")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Show only" })).toBeNull();
    expect(screen.queryByLabelText("How should it sound?")).toBeNull();
  });

  it("do not have their voices asked for while they cannot be used, and are asked for as soon as they can", async () => {
    const calls = server(() => NOTHING);
    const checking = inCaps((engines) => engines.map((e) => ({ ...e, state: "checking" as const })));
    const { rerender } = render(<SpeechPanel hidden={false} caps={checking} store={memoryStore()} />);
    expect(screen.getByText("Fake speech is still being checked.")).toBeTruthy();
    expect(calls.asked).toEqual([]);
    rerender(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} />);
    await waitFor(() => expect(calls.asked).toEqual(["fake"]));
  });
});

// ------------------------------------------------------------------ the voices
describe("the voices", () => {
  it("are the engine's own, asked for by its id, with the person's own first and the presets after (criterion 267)", async () => {
    const calls = server(() => NOTHING);
    show();
    await screen.findByRole("list", { name: "My voices" });
    expect(calls.asked).toEqual(["fake"]);
    expect(voiceNames("My voices")).toEqual(SPEECH_CONTRACT.engine_voices.saved.map((v) => v.name));
    expect(voiceNames("Voices")).toEqual(["Anna", "Bea", "Carl", "Dan"]);
    expect(screen.getByRole("heading", { name: "My voices" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Voices" })).toBeTruthy(); // two groups, so each is named
  });

  it("are another engine's when another engine is chosen, and an engine that cannot clone has none of the person's", async () => {
    const calls = server(() => NOTHING);
    show();
    await screen.findByRole("list", { name: "Voices" });
    fireEvent.click(engineRadio("Fake basic speech"));
    await screen.findByRole("radio", { name: /^Basic One$/ });
    expect(calls.asked).toEqual(["fake", "fake-basic"]);
    expect(screen.queryByRole("list", { name: "My voices" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "My voices" })).toBeNull();
    expect(voiceNames("Voices")).toEqual(["Basic One", "Basic Two"]); // one list, so no 'Voices' heading to tell it from another
    expect(screen.queryByRole("heading", { name: "Voices" })).toBeNull();
    expect(screen.queryByRole("radio", { name: /^Anna$/ })).toBeNull();
  });

  it("say what the source states, and 'not stated' where it states nothing; nothing is guessed from a name (criterion 261)", async () => {
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    // the voice is named by its name alone; what the source says of it is its description (so a screen reader says them apart)
    expect(describedBy(voiceRadio("Anna"))).toBe("Female · English · American | A bright test voice.");
    expect(describedBy(voiceRadio("Bea"))).toBe("Female · English · Accent not stated"); // (the fake engine's Bea has no words of her own: nothing more to say)
    expect(describedBy(voiceRadio("Dan"))).toContain("Male · German · Accent not stated");
  });

  it("say how a voice of the person's came about", async () => {
    show();
    await screen.findByRole("list", { name: "My voices" });
    const mine = within(voiceList("My voices"));
    expect(mine.getByText(/Cloned from a recording · 10 s/)).toBeTruthy();
    expect(mine.getByText(/Designed from a description · 5 s/)).toBeTruthy();
  });

  it("start with the person's own first voice, and an explicit choice replaces it and is remembered", async () => {
    const store = memoryStore();
    show(CAPS, false, { store });
    await screen.findByRole("list", { name: "My voices" });
    const first = SPEECH_CONTRACT.engine_voices.saved[0];
    expect((within(voiceList("My voices")).getAllByRole("radio")[0] as HTMLInputElement).checked).toBe(true);
    fireEvent.click(voiceRadio("Carl"));
    expect(voiceRadio("Carl").checked).toBe(true);
    expect((within(voiceList("My voices")).getAllByRole("radio")[0] as HTMLInputElement).checked).toBe(false);
    expect(JSON.parse(store.data[SPEECH_CHOICE_KEY]).voice).toEqual({ kind: "preset", id: "carl" });
    cleanup();
    show(CAPS, false, { store });
    await screen.findByRole("radio", { name: /^Carl$/ });
    expect(voiceRadio("Carl").checked).toBe(true); // remembered
    expect(first.id).toBeTruthy();
  });

  it("fall back to the first voice when the remembered one is not there any more", async () => {
    const store = memoryStore({ [SPEECH_CHOICE_KEY]: JSON.stringify({ engine: "fake", voice: { kind: "saved", id: "deleted-long-ago" } }) });
    show(CAPS, false, { store });
    await screen.findByRole("list", { name: "My voices" });
    expect((within(voiceList("My voices")).getAllByRole("radio")[0] as HTMLInputElement).checked).toBe(true);
  });

  it("do not take a remembered preset of another engine for one of this engine's", async () => {
    const store = memoryStore({ [SPEECH_CHOICE_KEY]: JSON.stringify({ engine: "fake-basic", voice: { kind: "preset", id: "anna" } }) });
    show(CAPS, false, { store });
    await screen.findByRole("radio", { name: /^Basic One$/ });
    expect(voiceRadio("Basic One").checked).toBe(true); // Anna is not Basic's: the first voice there is
  });

  it("say they are loading, and say it in the server's words when they cannot be read", async () => {
    server(() => NOTHING, () => new Promise(() => undefined));
    show();
    expect(await screen.findByText("Loading the voices…")).toBeTruthy();
    cleanup();
    server(() => NOTHING, () => ({ status: 409, body: { detail: "Fake speech cannot run here.", code: "engine_unavailable", hint: "Rebuild the image." } }));
    show();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Fake speech cannot run here.");
    expect(alert.textContent).toContain("Rebuild the image.");
  });
});

describe("the chips", () => {
  const chip = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

  it("filter the presets by gender and by English, and are pressed or not", async () => {
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    expect(["Female", "Male", "English"].map((n) => chip(n).getAttribute("aria-pressed"))).toEqual(["false", "false", "false"]);
    fireEvent.click(chip("Female"));
    expect(chip("Female").getAttribute("aria-pressed")).toBe("true");
    expect(voiceNames("Voices")).toEqual(["Anna", "Bea"]);
    fireEvent.click(chip("Female")); // pressing it again takes it off
    expect(voiceNames("Voices")).toEqual(["Anna", "Bea", "Carl", "Dan"]);
    fireEvent.click(chip("Male"));
    expect(voiceNames("Voices")).toEqual(["Carl", "Dan"]);
    fireEvent.click(chip("English"));
    expect(voiceNames("Voices")).toEqual(["Carl"]);
    fireEvent.click(chip("English")); // English can be taken off again too
    expect(chip("English").getAttribute("aria-pressed")).toBe("false");
    expect(voiceNames("Voices")).toEqual(["Carl", "Dan"]);
    fireEvent.click(chip("English"));
    fireEvent.click(chip("Female")); // one gender at a time: Female replaces Male
    expect(chip("Male").getAttribute("aria-pressed")).toBe("false");
    expect(voiceNames("Voices")).toEqual(["Anna", "Bea"]);
  });

  it("offer 'Show every voice' only while some chip is on", async () => {
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    expect(screen.queryByRole("button", { name: "Show every voice" })).toBeNull();
    fireEvent.click(chip("English"));
    expect(screen.getByRole("button", { name: "Show every voice" })).toBeTruthy();
    fireEvent.click(chip("English"));
    expect(screen.queryByRole("button", { name: "Show every voice" })).toBeNull();
  });

  it("do not touch the person's own voices", async () => {
    show();
    await screen.findByRole("list", { name: "My voices" });
    fireEvent.click(chip("Male"));
    expect(voiceNames("My voices")).toEqual(SPEECH_CONTRACT.engine_voices.saved.map((v) => v.name));
  });

  it("keep the voice that is chosen in the list, even if the chips would hide it", async () => {
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(voiceRadio("Anna"));
    fireEvent.click(chip("Male"));
    expect(voiceNames("Voices")).toEqual(["Anna", "Carl", "Dan"]);
    expect(voiceRadio("Anna").checked).toBe(true);
  });

  it("say when nothing matches and offer every voice again", async () => {
    show();
    await screen.findByRole("radio", { name: /^Basic One$/ }).catch(() => undefined);
    fireEvent.click(await screen.findByRole("radio", { name: /^Anna$/ }));
    fireEvent.click(chip("Male"));
    fireEvent.click(chip("English"));
    fireEvent.click(voiceRadio("Carl")); // the one choice stays, so there is always a list: choose then filter it out
    fireEvent.click(chip("Female"));
    expect(voiceNames("Voices")).toEqual(["Anna", "Bea", "Carl"]); // (Carl is kept because he is chosen)
    fireEvent.click(screen.getByRole("button", { name: "Show every voice" }));
    expect(voiceNames("Voices")).toEqual(["Anna", "Bea", "Carl", "Dan"]);
    expect(["Female", "Male", "English"].map((n) => chip(n).getAttribute("aria-pressed"))).toEqual(["false", "false", "false"]);
  });

  it("are remembered", async () => {
    const store = memoryStore();
    show(CAPS, false, { store });
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(chip("Female"));
    fireEvent.click(chip("English"));
    cleanup();
    show(CAPS, false, { store });
    await screen.findByRole("radio", { name: /^Anna$/ });
    expect(chip("Female").getAttribute("aria-pressed")).toBe("true");
    expect(chip("English").getAttribute("aria-pressed")).toBe("true");
  });
});

// ------------------------------------------------------------------ Play sample
describe("Play sample", () => {
  const sampleButton = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
  const sampleCalls = (calls: ReturnType<typeof server>) => calls.filter((c) => c.url === "/api/speech/samples");

  it("asks for a preset by its id, plays what comes back, and the button says Stop while it plays", async () => {
    const calls = server((call) => (call.url === "/api/speech/samples" ? { body: { ready: true, url: "/api/speech/samples/fake/anna.wav", job: null } } : NOTHING));
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(sampleButton("Play sample of Anna"));
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    expect(jsonBody(sampleCalls(calls)[0])).toEqual({ engine: "fake", voice: "anna" });
    expect((document.querySelector("audio") as HTMLAudioElement).getAttribute("src")).toBe("/api/speech/samples/fake/anna.wav");
    expect(await screen.findByRole("button", { name: "Stop the sample of Anna" })).toBeTruthy();
    fireEvent.click(sampleButton("Stop the sample of Anna"));
    expect(pause).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Play sample of Anna" })).toBeTruthy();
  });

  it("asks for a voice of the person's by its id, with the engine", async () => {
    const calls = server((call) => (call.url === "/api/speech/samples" ? { body: { ready: true, url: "/mine.wav", job: null } } : NOTHING));
    show();
    const mine = SPEECH_CONTRACT.engine_voices.saved[0];
    await screen.findByRole("button", { name: `Play sample of ${mine.name}` });
    fireEvent.click(sampleButton(`Play sample of ${mine.name}`));
    await waitFor(() => expect(sampleCalls(calls)).toHaveLength(1));
    expect(jsonBody(sampleCalls(calls)[0])).toEqual({ engine: "fake", saved_voice: mine.id });
  });

  it("waits for one that has to be made, and plays it when the event says it is done (criterion 262)", async () => {
    const jobs = createEmitter<SpeechJob>();
    const queued: SpeechJob = { ...(SPEECH_CONTRACT.sample_job as unknown as SpeechJob), id: "j1", status: "queued", files: [] };
    server((call) => (call.url === "/api/speech/samples" ? { status: 202, body: { ready: false, url: null, job: queued } } : NOTHING));
    show(CAPS, false, { jobs });
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(sampleButton("Play sample of Anna"));
    expect(await screen.findByRole("button", { name: "Making the sample of Anna…" })).toBeTruthy();
    expect(play).not.toHaveBeenCalled();
    jobs.emit({ ...queued, status: "done", files: [{ key: "sample", url: "/made.wav" }] });
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    expect((document.querySelector("audio") as HTMLAudioElement).getAttribute("src")).toBe("/made.wav");
  });

  it("says why it could not be made, beside the voice, with what to do", async () => {
    server((call) => (call.url === "/api/speech/samples" ? { status: 409, body: { detail: "Fake speech is still being checked.", code: "engine_checking", hint: "Try again in a moment." } } : NOTHING));
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(sampleButton("Play sample of Anna"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Fake speech is still being checked.");
    expect(alert.textContent).toContain("Try again in a moment.");
    expect(voiceRadio("Anna").closest("li")?.contains(alert)).toBe(true); // it is under the voice that was asked for, not under another
    expect(voiceRadio("Bea").closest("li")?.querySelector("[role=alert]")).toBeNull();
  });

  it("stops when the tab is hidden, so that no sound comes from a tab that is not on the screen", async () => {
    server((call) => (call.url === "/api/speech/samples" ? { body: { ready: true, url: "/a.wav", job: null } } : NOTHING));
    const { rerender } = render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} />);
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(sampleButton("Play sample of Anna"));
    await screen.findByRole("button", { name: "Stop the sample of Anna" });
    pause.mockClear();
    rerender(<SpeechPanel hidden caps={CAPS} store={memoryStore()} />);
    expect(pause).toHaveBeenCalled();
  });

  it("stops when another engine is chosen", async () => {
    server((call) => (call.url === "/api/speech/samples" ? { body: { ready: true, url: "/a.wav", job: null } } : NOTHING));
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(sampleButton("Play sample of Anna"));
    await screen.findByRole("button", { name: "Stop the sample of Anna" });
    pause.mockClear();
    fireEvent.click(engineRadio("Fake basic speech"));
    expect(pause).toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------ the style and the language
describe("the style", () => {
  const choose = async (voice: string) => {
    await screen.findByRole("radio", { name: new RegExp(`^${voice}$`) });
    fireEvent.click(voiceRadio(voice));
  };

  it("is a box with examples for a voice that takes one (criterion 264), and an example fills it", async () => {
    show();
    await choose("Anna");
    const box = screen.getByLabelText("How should it sound?") as HTMLTextAreaElement;
    expect(box.value).toBe("");
    const examples = within(screen.getByRole("group", { name: "Examples" })).getAllByRole("button");
    expect(examples.length).toBeGreaterThanOrEqual(3);
    fireEvent.click(examples[0]);
    expect(box.value).toBe(examples[0].textContent);
    fireEvent.change(box, { target: { value: "slowly" } });
    expect(box.value).toBe("slowly");
  });

  it("is a line saying why not, and not nothing, for a voice that takes none, for the engine's other voices, and for one the person made", async () => {
    show();
    await choose("Bea");
    expect(screen.queryByLabelText("How should it sound?")).toBeNull();
    expect(screen.getByText("This voice does not take a style note.")).toBeTruthy();
    const mine = SPEECH_CONTRACT.engine_voices.saved[0];
    fireEvent.click(screen.getByRole("radio", { name: new RegExp(`^${mine.name}$`) }));
    expect(screen.getByText(/A voice you made speaks as it was recorded or described, so it takes no style note\./)).toBeTruthy();
  });

  it("is a line naming the engine when the engine takes none", async () => {
    show();
    fireEvent.click(engineRadio("Fake basic speech"));
    await screen.findByRole("radio", { name: /^Basic One$/ });
    expect(screen.getByText("Fake basic speech does not take a style note.")).toBeTruthy();
  });

  it("is remembered with the rest of the choice", async () => {
    const store = memoryStore();
    show(CAPS, false, { store });
    await choose("Anna");
    fireEvent.change(screen.getByLabelText("How should it sound?"), { target: { value: "calm and low" } });
    cleanup();
    show(CAPS, false, { store });
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(voiceRadio("Anna"));
    expect((screen.getByLabelText("How should it sound?") as HTMLTextAreaElement).value).toBe("calm and low");
  });
});

describe("the language", () => {
  it("is a list, with Auto first, only where the engine takes one", async () => {
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    const list = screen.getByLabelText("Language") as HTMLSelectElement;
    expect(Array.from(list.options).map((o) => o.value)).toEqual(["Auto", "English", "German"]);
    expect(list.value).toBe("Auto");
    fireEvent.change(list, { target: { value: "German" } });
    expect(list.value).toBe("German");
    fireEvent.click(engineRadio("Fake basic speech"));
    await screen.findByRole("radio", { name: /^Basic One$/ });
    expect(screen.queryByLabelText("Language")).toBeNull(); // the voice decides
  });

  it("shows Auto when the one remembered is not one the engine speaks", async () => {
    const store = memoryStore({ [SPEECH_CHOICE_KEY]: JSON.stringify({ engine: "fake", language: "Klingon" }) });
    show(CAPS, false, { store });
    await screen.findByRole("radio", { name: /^Anna$/ });
    expect((screen.getByLabelText("Language") as HTMLSelectElement).value).toBe("Auto");
  });
});

// ------------------------------------------------------------------ Make reading
describe("Make reading", () => {
  /** A stand-in that prepares the book and queues a reading. */
  const queueing = (reply: { status: number; body: unknown } = { status: 201, body: READING }) =>
    server((call) => (call.url === "/api/speech/prepare" ? { body: BOOK } : call.url === "/api/readings" ? reply : NOTHING));
  const make = () => screen.getByRole("button", { name: /Make reading|Queuing/ }) as HTMLButtonElement;

  it("is not there until there is a text to read", () => {
    show();
    expect(screen.queryByRole("button", { name: "Make reading" })).toBeNull();
  });

  it("says what it will do before it does it: the parts, how long, how big, the engine and the voice (§38.2)", async () => {
    queueing();
    show();
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(voiceRadio("Anna"));
    expect(screen.getByText(/^3 parts, less than a minute of audio, about \d+ KB\. Fake speech, Anna\.$/)).toBeTruthy();
    fireEvent.click(within(screen.getByRole("list", { name: "Parts" })).getAllByRole("checkbox")[2]); // leave the last part out
    expect(screen.getByText(/^2 parts, /)).toBeTruthy();
    fireEvent.click(engineRadio("Fake basic speech"));
    await screen.findByRole("radio", { name: /^Basic One$/ });
    expect(screen.getByText(/Fake basic speech, Basic One\.$/)).toBeTruthy();
  });

  it("queues the reading with what was chosen, and says so, and tells the page and the toast", async () => {
    const calls = queueing();
    const onQueued = vi.fn();
    const push = vi.fn();
    show(CAPS, false, { onQueued, push });
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(voiceRadio("Anna"));
    fireEvent.change(screen.getByLabelText("How should it sound?"), { target: { value: "  calm and slow  " } });
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "German" } });
    fireEvent.change(screen.getByLabelText("Title of the reading"), { target: { value: "My own title" } });
    press("Make reading");
    expect(await screen.findByText(/^Queued “A test reading”: 2 parts, /)).toBeTruthy();
    const reading = calls.find((c) => c.url === "/api/readings");
    expect(reading?.init.method).toBe("POST");
    const body = jsonBody(reading as NonNullable<typeof reading>);
    expect(Object.keys(body).sort()).toEqual(SPEECH_CONTRACT.requests.reading); // exactly the fields the server's model has
    expect(body).toMatchObject({ title: "My own title", engine: "fake", voice: "anna", saved_voice: null, style: "calm and slow", language: "German", source: BOOK.source });
    expect(body.parts.map((p: { title: string }) => p.title)).toEqual(["Chapter one", "Chapter two", "Chapter three"]);
    expect(body.parts[0].paragraphs).toEqual([{ kind: "heading", text: "Chapter one" }, { kind: "paragraph", text: "It was a bright cold day in April." }]);
    expect(onQueued).toHaveBeenCalledTimes(1);
    expect((onQueued.mock.calls[0][0] as SpeechRun).mode).toBe("speech");
    expect(push).toHaveBeenCalledWith("success", expect.stringMatching(/^Queued “A test reading”/));
  });

  it("names a voice the person made by its id, and sends no style for it, whatever is in the box", async () => {
    const calls = queueing();
    show();
    await prepareBook();
    await screen.findByRole("list", { name: "My voices" });
    const mine = SPEECH_CONTRACT.engine_voices.saved[0];
    fireEvent.click(screen.getByRole("radio", { name: new RegExp(`^${mine.name}$`) }));
    press("Make reading");
    await screen.findByText(/^Queued /);
    expect(jsonBody(calls.find((c) => c.url === "/api/readings") as never)).toMatchObject({ voice: "", saved_voice: mine.id, style: null });
  });

  it("sends only what the parts say: what is left out stays out", async () => {
    const calls = server((call) => (call.url === "/api/speech/prepare" ? { body: prepared([part("One", [["paragraph", "Words one."], ["paragraph", "let x = 1;", "code"]]), part("Two", [["paragraph", "Words two."]])]) } : call.url === "/api/readings" ? { status: 201, body: READING } : NOTHING));
    show();
    await prepareBook();
    fireEvent.click(within(screen.getByRole("list", { name: "Parts" })).getAllByRole("checkbox")[1]);
    await screen.findByRole("radio", { name: /^Anna$/ });
    press("Make reading");
    await screen.findByText(/^Queued /);
    const body = jsonBody(calls.find((c) => c.url === "/api/readings") as never);
    expect(body.parts).toEqual([{ title: "One", paragraphs: [{ kind: "paragraph", text: "Words one." }] }]);
  });

  it("is off, with the reason beside it, when nothing is included, and on again when something is", async () => {
    queueing();
    show();
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    for (const box of within(screen.getByRole("list", { name: "Parts" })).getAllByRole("checkbox")) fireEvent.click(box);
    expect(make().disabled).toBe(true);
    expect(screen.getByText("Include at least one part that has some words in it.")).toBeTruthy();
    expect(make().getAttribute("aria-describedby")).toBe(screen.getByText("Include at least one part that has some words in it.").id);
    fireEvent.click(within(screen.getByRole("list", { name: "Parts" })).getAllByRole("checkbox")[0]);
    expect(make().disabled).toBe(false);
  });

  it("is off, with the engine's own reason, when the engine cannot be used", async () => {
    queueing();
    const caps = inCaps((engines) => [{ ...engines[0], state: "not_installed", reason: "Fake speech's libraries are not installed.", hint: null }, { ...engines[1], state: "error", reason: "Failed." }]);
    show(caps);
    await prepareBook();
    expect(make().disabled).toBe(true);
    expect(screen.getAllByText(/Fake speech's libraries are not installed\./).length).toBeGreaterThan(0);
  });

  it("is off while the request is on its way, so that a second press cannot queue a second reading", async () => {
    let release: (value: { status: number; body: unknown }) => void = () => undefined;
    const calls = server((call) => (call.url === "/api/speech/prepare" ? { body: BOOK } : call.url === "/api/readings" ? new Promise((resolve) => { release = resolve; }) : NOTHING));
    show();
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    press("Make reading");
    await waitFor(() => expect(make().textContent).toBe("Queuing…"));
    expect(make().disabled).toBe(true);
    fireEvent.click(make());
    expect(calls.filter((c) => c.url === "/api/readings")).toHaveLength(1);
    release({ status: 201, body: READING });
    await screen.findByText(/^Queued /);
    expect(make().textContent).toBe("Make reading");
    expect(make().disabled).toBe(false);
  });

  it("can be pressed again after one was queued, for another voice, and says what the last one was", async () => {
    const calls = queueing();
    show();
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    fireEvent.click(voiceRadio("Carl"));
    press("Make reading");
    await screen.findByText(/^Queued /);
    fireEvent.click(voiceRadio("Dan"));
    press("Make reading");
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/readings")).toHaveLength(2));
    expect(jsonBody(calls.filter((c) => c.url === "/api/readings")[1]).voice).toBe("dan");
  });

  it("says the server's refusal in its own words, with what to do, and nothing is said to be queued (criterion 274)", async () => {
    const onQueued = vi.fn();
    queueing({ status: 507, body: { detail: "This reading needs about 2.1 GB of disk and 0.4 GB is free.", code: "storage_full", hint: "Delete readings you no longer need, or free disk space, and try again." } });
    show(CAPS, false, { onQueued });
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    press("Make reading");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("This reading needs about 2.1 GB of disk and 0.4 GB is free.");
    expect(alert.textContent).toContain("Delete readings you no longer need");
    expect(screen.queryByText(/^Queued /)).toBeNull();
    expect(onQueued).not.toHaveBeenCalled();
    expect(make().disabled).toBe(false); // it can be tried again
  });

  it("says something plain when the studio cannot be reached", async () => {
    server((call) => { if (call.url === "/api/speech/prepare") return { body: BOOK }; throw new TypeError("network"); });
    show();
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    press("Make reading");
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach the studio server.");
  });

  it("takes down what was said of the last one while the next is on its way", async () => {
    let hold: (value: { status: number; body: unknown }) => void = () => undefined;
    let n = 0;
    server((call) => (call.url === "/api/speech/prepare" ? { body: BOOK } : call.url === "/api/readings" ? (n++ === 0 ? { status: 201, body: READING } : new Promise((resolve) => { hold = resolve; })) : NOTHING));
    show();
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    press("Make reading");
    await screen.findByText(/^Queued /);
    press("Make reading");
    await waitFor(() => expect(make().textContent).toBe("Queuing…"));
    expect(screen.queryByText(/^Queued /)).toBeNull(); // the line about the last one is not left standing over the next
    hold({ status: 201, body: READING });
    await screen.findByText(/^Queued /);
  });

  it("forgets the last answer when it is pressed again", async () => {
    let n = 0;
    server((call) => (call.url === "/api/speech/prepare" ? { body: BOOK } : call.url === "/api/readings" ? (n++ === 0 ? { status: 422, body: { detail: "Not this time.", code: "x" } } : { status: 201, body: READING }) : NOTHING));
    show();
    await prepareBook();
    await screen.findByRole("radio", { name: /^Anna$/ });
    press("Make reading");
    await screen.findByRole("alert");
    press("Make reading");
    await screen.findByText(/^Queued /);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("the voices a reading can be made with, taken from the server", () => {
  it("are asked for again by the engine's id, named in the request to the server", async () => {
    const calls = server(() => NOTHING);
    show();
    await screen.findByRole("radio", { name: /^Anna$/ });
    expect(calls.asked).toEqual(["fake"]);
    expect(voicesOf("fake").presets).toHaveLength(4);
  });
});
