import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Prepared, PreparedParagraph } from "./speechTypes";
import { SPEECH_CHOICE_KEY } from "./speech";
import { jsonBody, memoryStore, press, prepareBook, prepared, part, server, show, typeText } from "./speechTestSupport";
import { CAPS, SPEECH_CONTRACT } from "./testdata";

// Several voices in one text, on the Speech tab (DESIGN.md §44.5, criteria 363, 364, 366, 368 and 369): the "Find speakers" switch, the list of names the review found with a voice for each,
// what Make reading sends, and what is left out of it. The server is a table of answers; the rules of the choices themselves are in speakers.test.ts.

const NOTHING = { status: 404, body: { detail: "Not expected here.", code: "not_found" } };
const READING = SPEECH_CONTRACT.reading_run;

beforeEach(() => void server(() => NOTHING));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const line = (speaker: string | null, text: string, kind: "paragraph" | "heading" = "paragraph"): PreparedParagraph =>
  ({ kind, text, skipped: null, speaker, label: speaker ? `${speaker}:` : null });

/** The review of a short script, as the server makes it: a heading, a line of narration, two names with two lines each. The words of a speaker's line are without its name. */
const SCRIPT_PARTS = [
  { ...part("The Gate", []), paragraphs: [line(null, "The Gate", "heading"), line(null, "The sea went on all night."), line("Alice", "Did you hear the sea last night?"), line("Bob", "I did, and I did not sleep."),
    line("Alice", "Then we should go and look."), line("Bob", "Yes.")] },
  { ...part("Later", []), paragraphs: [line("Alice", "It was gone by morning, as it always is."), line(null, "She shut the door.")] },
];
const SCRIPT: Prepared = prepared(SCRIPT_PARTS, {
  speakers: [{ name: "Alice", lines: 3, sample: "Did you hear the sea last night?" }, { name: "Bob", lines: 2, sample: "I did, and I did not sleep." }],
  options: { clean: true, read_extras: false, speakers: true },
});

/** A stand-in that prepares the script (or what `reply` says) and queues a reading. */
const queueing = (review: Prepared = SCRIPT) =>
  server((call) => (call.url === "/api/speech/prepare" ? { body: review } : call.url === "/api/readings" ? { status: 201, body: READING } : NOTHING));
const speakersList = () => screen.getByRole("region", { name: /Speakers found/ });
const choose = (name: string, voice: string) => fireEvent.change(screen.getByLabelText(`Voice for ${name}`), { target: { value: voice } });
const reading = (calls: ReturnType<typeof server>) => jsonBody(calls.find((c) => c.url === "/api/readings") as never);

/** A review is prepared and Anna is the main voice (the tab starts on the first voice it is given, which is one the person made). */
async function review(): Promise<void> {
  await prepareBook();
  fireEvent.click(await screen.findByRole("radio", { name: /^Anna$/ }));
}

describe("the Find speakers switch", () => {
  it("is on at first, beside the other two, and is sent with the text", async () => {
    const calls = queueing();
    show();
    expect((screen.getByRole("checkbox", { name: /Find speakers/ }) as HTMLInputElement).checked).toBe(true);
    typeText("Alice: Hello there, how are you?\nBob: I am well, thank you.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    expect(jsonBody(calls[0]).speakers).toBe(true);
  });

  it("is sent as off when it is off, and the review that comes back has no list of speakers", async () => {
    const calls = queueing(prepared(SCRIPT_PARTS, { speakers: [], options: { clean: true, read_extras: false, speakers: false } }));
    show();
    fireEvent.click(screen.getByRole("checkbox", { name: /Find speakers/ }));
    await prepareBook();
    expect(jsonBody(calls[0]).speakers).toBe(false);
    expect(screen.queryByRole("region", { name: /Speakers found/ })).toBeNull();
  });

  it("changed after a review says so, as the other two do, and asks for Prepare again", async () => {
    queueing();
    show();
    await prepareBook();
    expect(screen.queryByText(/switches above have changed/)).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: /Find speakers/ }));
    expect(screen.getByText(/switches above have changed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /Find speakers/ }));
    expect(screen.queryByText(/switches above have changed/)).toBeNull(); // put back, so nothing is stale
  });
});

describe("the list of speakers", () => {
  it("is not there for a text with no speakers", async () => {
    queueing(prepared(SCRIPT_PARTS.map((p) => ({ ...p, paragraphs: p.paragraphs.map((q) => line(null, q.text, q.kind)) })), { speakers: [] }));
    show();
    await review();
    expect(screen.queryByRole("region", { name: /Speakers found/ })).toBeNull();
  });

  it("names each speaker with how many lines it has, the first of them and a voice list that starts with the main voice", async () => {
    queueing();
    show();
    await review();
    const list = within(speakersList());
    expect(list.getByRole("heading", { name: "Speakers found (2)" })).toBeTruthy();
    const items = list.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(list.getByRole("heading", { name: "Speakers found (2)" }).parentElement as HTMLElement).queryByText(/names that begin the most lines/)).toBeNull(); // all of them are listed
    expect(within(items[0]).getByText("3 lines")).toBeTruthy();
    expect(within(items[0]).getByText("“Did you hear the sea last night?”")).toBeTruthy();
    expect((within(items[0]).getByRole("checkbox", { name: /Read Alice as a speaker/ }) as HTMLInputElement).checked).toBe(true);
    const select = screen.getByLabelText("Voice for Alice") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(select.options[0].textContent).toBe("Main voice (Anna)");
  });

  it("offers the voices of the engine that can be used with the main voice, and says why not the others", async () => {
    queueing();
    show();
    await review();
    const select = screen.getByLabelText("Voice for Alice") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Main voice (Anna)", "Anna", "Bea", "Carl", "Dan"]); // the engine's own voices, as the main voice is one
    expect(within(speakersList()).getByRole("note").textContent).toMatch(/different parts of the model, so everyone in a reading has to be one kind/);
  });

  it("offers the voices the person made, and not the engine's own, when the main voice is one the person made", async () => {
    queueing();
    show();
    await review();
    fireEvent.click(screen.getByRole("radio", { name: new RegExp(`^${SPEECH_CONTRACT.engine_voices.saved[0].name}$`) }));
    const select = screen.getByLabelText("Voice for Alice") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual([`Main voice (${SPEECH_CONTRACT.engine_voices.saved[0].name})`, ...SPEECH_CONTRACT.engine_voices.saved.map((v) => v.name)]);
  });

  it("offers an engine that cannot clone its own voices with no note, since any of them can be used together", async () => {
    queueing();
    show(CAPS, false, { store: memoryStore({ [SPEECH_CHOICE_KEY]: JSON.stringify({ engine: "fake-basic" }) }) });
    await prepareBook();
    fireEvent.click(await screen.findByRole("radio", { name: /^Basic One$/ }));
    const select = screen.getByLabelText("Voice for Alice") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Main voice (Basic One)", "Basic One", "Basic Two"]);
    expect(within(speakersList()).queryByRole("note")).toBeNull();
  });

  it("turns a name off with its tick: its voice list is then disabled, and the name is shown as read as written", async () => {
    queueing();
    show();
    await review();
    const tick = screen.getByRole("checkbox", { name: /Read Bob as a speaker/ }) as HTMLInputElement;
    fireEvent.click(tick);
    expect(tick.checked).toBe(false);
    expect((screen.getByLabelText("Voice for Bob") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText("Voice for Alice") as HTMLSelectElement).disabled).toBe(false); // the other is as it was
  });

  it("says when the text had more names than are listed", async () => {
    queueing(prepared(SCRIPT_PARTS, { speakers: Array.from({ length: CAPS.limits.speech.speakers_max }, (_, i) => ({ name: i === 0 ? "Alice" : i === 1 ? "Bob" : `Name${i}`, lines: 2, sample: "x" })) }));
    show();
    await review();
    expect(within(speakersList()).getByText(new RegExp(`Only the ${CAPS.limits.speech.speakers_max} names that begin the most lines are listed`))).toBeTruthy();
  });

  it("shows a speaker's paragraph with the name beside it, without the name in the words", async () => {
    queueing();
    show();
    await review();
    fireEvent.click(within(screen.getByRole("list", { name: "Parts" })).getAllByRole("button", { name: "Show text" })[0]);
    const text = screen.getByRole("list", { name: "Text of part 1" });
    expect(within(text).getAllByText("Alice")).toHaveLength(2);
    expect((within(text).getByLabelText("Paragraph 3 of part 1") as HTMLTextAreaElement).value).toBe("Did you hear the sea last night?");
    fireEvent.click(screen.getByRole("checkbox", { name: /Read Alice as a speaker/ }));
    expect(within(text).getAllByText("Alice: read as text")).toHaveLength(2); // a name turned off says its line is read as it is written
  });
});

describe("Make reading with several voices", () => {
  it("sends the speakers with the voice chosen for each, and their lines without the name", async () => {
    const calls = queueing();
    show();
    await review();
    choose("Alice", "preset:bea");
    press("Make reading");
    await screen.findByText(/^Queued /);
    const body = reading(calls);
    expect(body.speakers).toEqual([{ name: "Alice", voice: "bea", saved_voice: null }, { name: "Bob", voice: "", saved_voice: null }]); // Bob: no voice chosen is the main voice
    expect(body.parts[0].paragraphs).toEqual([
      { kind: "heading", text: "The Gate" },
      { kind: "paragraph", text: "The sea went on all night." },
      { kind: "paragraph", text: "Did you hear the sea last night?", speaker: "Alice" },
      { kind: "paragraph", text: "I did, and I did not sleep.", speaker: "Bob" },
      { kind: "paragraph", text: "Then we should go and look.", speaker: "Alice" },
      { kind: "paragraph", text: "Yes.", speaker: "Bob" },
    ]);
  });

  it("sends a voice the person made as saved_voice, when the main voice is one the person made", async () => {
    const calls = queueing();
    show();
    await review();
    const [first, second] = SPEECH_CONTRACT.engine_voices.saved;
    fireEvent.click(screen.getByRole("radio", { name: new RegExp(`^${first.name}$`) }));
    choose("Alice", `saved:${second.id}`);
    press("Make reading");
    await screen.findByText(/^Queued /);
    expect(reading(calls)).toMatchObject({ voice: "", saved_voice: first.id, speakers: [{ name: "Alice", voice: "", saved_voice: second.id }, { name: "Bob", voice: "", saved_voice: null }] });
  });

  it("sends a name that was turned off as it was written, label and all, with no speaker", async () => {
    const calls = queueing();
    show();
    await review();
    fireEvent.click(screen.getByRole("checkbox", { name: /Read Bob as a speaker/ }));
    press("Make reading");
    await screen.findByText(/^Queued /);
    const body = reading(calls);
    expect(body.speakers.map((s: { name: string }) => s.name)).toEqual(["Alice"]);
    expect(body.parts[0].paragraphs.slice(3, 4)).toEqual([{ kind: "paragraph", text: "Bob: I did, and I did not sleep." }]);
    expect(body.parts[0].paragraphs[5]).toEqual({ kind: "paragraph", text: "Bob: Yes." });
  });

  it("does not send a speaker whose lines were all left out with their part", async () => {
    const calls = queueing(prepared([SCRIPT_PARTS[0], { ...SCRIPT_PARTS[1], paragraphs: [line("Carol", "I am only in the part that is left out."), line("Carol", "Me too, every word.")] }],
      { speakers: [...SCRIPT.speakers, { name: "Carol", lines: 2, sample: "I am only in the part that is left out." }] }));
    show();
    await review();
    fireEvent.click(within(screen.getByRole("list", { name: "Parts" })).getAllByRole("checkbox")[1]); // leave out the second part
    press("Make reading");
    await screen.findByText(/^Queued /);
    expect(reading(calls).speakers.map((s: { name: string }) => s.name)).toEqual(["Alice", "Bob"]);
  });

  it("falls back to the main voice for a voice that is no longer on offer, and shows it as the main voice", async () => {
    const calls = queueing();
    show();
    await review();
    choose("Alice", "preset:bea");
    fireEvent.click(screen.getByRole("radio", { name: new RegExp(`^${SPEECH_CONTRACT.engine_voices.saved[0].name}$`) })); // the main voice is now one the person made: Bea is not on offer
    const shown = screen.getByLabelText("Voice for Alice") as HTMLSelectElement;
    expect(shown.selectedOptions[0]?.textContent).toMatch(/^Main voice \(/); // the box says the main voice, and is not left blank with a voice that is not in it
    press("Make reading");
    await screen.findByText(/^Queued /);
    expect(reading(calls).speakers[0]).toEqual({ name: "Alice", voice: "", saved_voice: null });
  });

  it("sends no speakers for a text with none, as it always did", async () => {
    const calls = queueing(prepared(SCRIPT_PARTS.map((p) => ({ ...p, paragraphs: p.paragraphs.map((q) => line(null, q.text, q.kind)) })), { speakers: [] }));
    show();
    await review();
    press("Make reading");
    await screen.findByText(/^Queued /);
    expect(reading(calls).speakers).toEqual([]);
    expect(reading(calls).parts[0].paragraphs.every((p: object) => !("speaker" in p))).toBe(true);
  });

  it("shows the server's words when it refuses a cast, and keeps the choices", async () => {
    server((call) => (call.url === "/api/speech/prepare" ? { body: SCRIPT } : call.url === "/api/readings"
      ? { status: 422, body: { detail: "The speaker 'Alice' cannot have that voice with this main voice.", code: "speaker_variant", field: "speakers", hint: "Give every speaker a voice of the same kind." } } : NOTHING));
    show();
    await review();
    choose("Alice", "preset:bea");
    press("Make reading");
    expect(await screen.findByText(/cannot have that voice with this main voice/)).toBeTruthy();
    expect(screen.getByText("Give every speaker a voice of the same kind.")).toBeTruthy();
    expect((screen.getByLabelText("Voice for Alice") as HTMLSelectElement).value).toBe("preset:bea"); // nothing is thrown away
  });

  it("asks before a new review throws the choices away, and starts them again when it replaces the review: every name on, with the main voice", async () => {
    queueing();
    show();
    await review();
    choose("Alice", "preset:bea");
    fireEvent.click(screen.getByRole("checkbox", { name: /Read Bob as a speaker/ }));
    typeText("A different text, long enough to be prepared on its own.");
    press("Prepare text");
    expect(screen.getByRole("group", { name: "Replace the review?" })).toBeTruthy(); // the choices are work of the person's: asked, not lost
    press("Replace the review");
    await waitFor(() => expect(screen.queryByRole("group", { name: "Replace the review?" })).toBeNull());
    await screen.findByRole("region", { name: /Speakers found/ });
    expect((screen.getByLabelText("Voice for Alice") as HTMLSelectElement).value).toBe("");
    expect((screen.getByRole("checkbox", { name: /Read Bob as a speaker/ }) as HTMLInputElement).checked).toBe(true);
  });

  it("does not ask for a review whose choices are the ones it began with", async () => {
    queueing();
    show();
    await review();
    typeText("A different text, long enough to be prepared on its own.");
    press("Prepare text");
    expect(screen.queryByRole("group", { name: "Replace the review?" })).toBeNull();
  });
});
