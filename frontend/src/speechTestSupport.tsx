import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { SpeechPanel, type SpeechPanelProps } from "./components/SpeechPanel";
import type { KeyValueStore } from "./options";
import type { Prepared, PreparedPart } from "./speechTypes";
import { CAPS, SPEECH_CONTRACT, makePrepared } from "./testdata";
import type { Capabilities } from "./types";

// What the tests of the Speech tab have in common (DESIGN.md §38.2): a stand-in for the server, a text already prepared, and a way to put the tab on the screen.

export interface Call {
  url: string;
  init: RequestInit & { headers: Record<string, string> };
}
export type Answer = { status?: number; body: unknown } | Promise<{ status?: number; body: unknown }>;

/** The voices of an engine: the contract's for the full fake engine (four presets and the voices that were made for it), and for the basic one its own two presets and none
 *  of the person's (an engine that cannot clone has no saved voices, as the server says). */
export const voicesOf = (engine: string) => engine === "fake-basic"
  ? {
    engine,
    presets: [
      { id: "basic_one", label: "Basic One", gender: "female", language: "English", accent: null, description: "", styleable: false, sample: { ready: false, url: null } },
      { id: "basic_two", label: "Basic Two", gender: "male", language: "English", accent: null, description: "", styleable: false, sample: { ready: false, url: null } },
    ],
    saved: [],
  }
  : { ...SPEECH_CONTRACT.engine_voices, engine };

/** Answer every call with `answer(call)`; the calls are returned for the test to look at. The tab also asks for the voices of the engine it shows, which most tests are not
 *  about: those are answered with the contract's (or by `voices`), and are returned in `asked`. Since 1.25 it also reads the pronunciation list when it opens (DESIGN.md
 *  §44.3), which is just as little the business of most tests: that read is answered with the list given (empty unless a test says otherwise) and is not in the calls. */
export function server(answer: (call: Call) => Answer, voices: (engine: string) => Answer = (engine) => ({ body: voicesOf(engine) }),
  pronunciations: { entries: unknown[] } = { entries: [] }): Call[] & { asked: string[]; listReads: number } {
  const calls = [] as unknown as Call[] & { asked: string[]; listReads: number };
  calls.asked = [];
  calls.listReads = 0; // how many times the list was read: a tab that cannot make speech has no use for it
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: Call["init"]) => {
    if (url === "/api/speech/pronunciations" && (!init?.method || init.method === "GET")) {
      calls.listReads += 1;
      return new Response(JSON.stringify(pronunciations), { status: 200 });
    }
    if (url.startsWith("/api/speech/engines/")) {
      const engine = url.split("/")[4];
      calls.asked.push(engine);
      const { status = 200, body } = await voices(engine);
      return new Response(JSON.stringify(body), { status });
    }
    const call = { url, init };
    calls.push(call);
    const { status = 200, body } = await answer(call);
    return new Response(status === 204 ? null : JSON.stringify(body), { status });
  }));
  return calls;
}

export const part = (title: string, paragraphs: [string, string, (string | null)?][], extra: Partial<PreparedPart> = {}): PreparedPart => ({
  title, chars: paragraphs.filter((p) => !p[2]).reduce((n, p) => n + p[1].length, 0), minutes: 1, include: true, note: null,
  paragraphs: paragraphs.map(([kind, text, skipped = null]) => ({ kind: kind as "heading" | "paragraph", text, skipped, speaker: null, label: null })), ...extra,
});
export const prepared = (parts: PreparedPart[], extra: Partial<Prepared> = {}): Prepared => makePrepared({ title: "A short book", parts, warnings: [], cleaning: [], ...extra });
export const BOOK = prepared([
  part("Chapter one", [["heading", "Chapter one"], ["paragraph", "It was a bright cold day in April."]]),
  part("Chapter two", [["paragraph", "The second chapter begins here, with some words."]]),
  part("Chapter three", [["paragraph", "And the third."]]),
]);

export const memoryStore = (initial: Record<string, string> = {}): KeyValueStore & { data: Record<string, string> } => {
  const data = { ...initial };
  return { available: true, get: (key) => data[key] ?? null, set: (key, value) => void (data[key] = value), data };
};

/** The tab on the screen, with a store of its own unless one is given. */
export const show = (caps: Capabilities = CAPS, hidden = false, extra: Partial<SpeechPanelProps> = {}) =>
  render(<SpeechPanel hidden={hidden} caps={caps} store={memoryStore()} {...extra} />);

export const typeText = (text: string) => fireEvent.change(screen.getByLabelText("Paste the text"), { target: { value: text } });
export const press = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
export const parts = () => within(screen.getByRole("list", { name: "Parts" })).getAllByRole("listitem", { name: "" }).filter((li) => li.classList.contains("speech-part"));
export const jsonBody = (call: Call) => JSON.parse(call.init.body as string);

/** Paste a text and prepare it, so that there is a review (whatever the stand-in server answers the prepare with). */
export async function prepareBook(): Promise<void> {
  typeText("Some words to read, enough to be a text.");
  press("Prepare text");
  await screen.findByRole("heading", { name: "What will be read" });
}
