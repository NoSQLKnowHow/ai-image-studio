import { describe, expect, it } from "vitest";
import { castLine, initialCast, linesText, paragraphToSend, pickAvailable, pickFrom, pickKey, speakersToSend, speakersUsed, voicesFor, withChoice, type Cast } from "./speakers";
import type { PreparedSpeaker, PresetVoice, SavedVoice, SpeechEngine } from "./speechTypes";

// Several voices in one text, as the page keeps them (DESIGN.md §44.5, criteria 364 and 366): the choices, which voices an engine can use together, what is sent. The server
// decides again; these rules keep the page from offering what an engine cannot do, and say why in words.

const preset = (id: string, label = id): PresetVoice => ({ id, label, gender: null, language: null, accent: null, description: "", styleable: false, sample: { ready: false, url: null } });
const mine = (id: string, name = id): SavedVoice => ({ id, name, kind: "cloned", engine: "e", description: null, seconds: 10, created_at: "", consent_at: null, sample: { ready: false, url: null } });
const engine = (changes: Partial<SpeechEngine> = {}): Pick<SpeechEngine, "title" | "can_clone" | "preset_variant" | "clone_variant"> =>
  ({ title: "Test engine", can_clone: true, preset_variant: "custom", clone_variant: "clone", ...changes });
const SPEAKERS: PreparedSpeaker[] = [{ name: "Alice", lines: 3, sample: "Hello" }, { name: "Bob", lines: 2, sample: "Hi" }];

describe("the choices", () => {
  it("start with every name on and the main voice", () => {
    expect(initialCast(SPEAKERS)).toEqual({ Alice: { on: true, voice: null }, Bob: { on: true, voice: null } });
    expect(initialCast([])).toEqual({});
  });

  it("change one name at a time and leave the others", () => {
    const cast = withChoice(initialCast(SPEAKERS), "Alice", { voice: { kind: "preset", id: "bea" } });
    expect(cast.Alice).toEqual({ on: true, voice: { kind: "preset", id: "bea" } });
    expect(cast.Bob).toEqual({ on: true, voice: null });
    expect(withChoice(cast, "Alice", { on: false }).Alice).toEqual({ on: false, voice: { kind: "preset", id: "bea" } }); // the voice is kept for when it is switched on again
    expect(withChoice({}, "Zed", { on: false }).Zed).toEqual({ on: false, voice: null }); // a name the cast did not have starts as the default
  });

  it("say how many lines", () => {
    expect(linesText(1)).toBe("1 line");
    expect(linesText(12)).toBe("12 lines");
  });
});

describe("a voice pick as a list value", () => {
  it("round-trips, and nothing is the main voice", () => {
    expect(pickKey({ kind: "preset", id: "bea" })).toBe("preset:bea");
    expect(pickKey(null)).toBe("");
    expect(pickFrom("saved:abc")).toEqual({ kind: "saved", id: "abc" });
    expect(pickFrom("preset:a:b")).toEqual({ kind: "preset", id: "a:b" }); // an id may hold a colon
    expect(pickFrom("")).toBeNull();
    expect(pickFrom("other:x")).toBeNull();
  });
});

describe("which voices can be used together (criterion 366)", () => {
  const presets = [preset("anna"), preset("bea"), preset("carl")];
  const saved = [mine("v1", "My voice"), mine("v2", "Warm")];

  it("offers only the kind of voice the main voice is, where the engine keeps them in two parts of its model", () => {
    const fromPreset = voicesFor(engine(), presets[0], presets, saved);
    expect(fromPreset.presets).toHaveLength(3);
    expect(fromPreset.saved).toEqual([]);
    expect(fromPreset.note).toMatch(/speaks its own voices and the ones you made with different parts of the model, so everyone in a reading has to be one kind: here, the voices it comes with\./);
    const fromSaved = voicesFor(engine(), saved[0], presets, saved);
    expect(fromSaved.presets).toEqual([]);
    expect(fromSaved.saved).toHaveLength(2);
    expect(fromSaved.note).toMatch(/here, the voices you made\./);
  });

  it("offers both kinds where the engine speaks both with the same part of its model", () => {
    const offer = voicesFor(engine({ clone_variant: "custom" }), presets[0], presets, saved);
    expect(offer.presets).toHaveLength(3);
    expect(offer.saved).toHaveLength(2);
    expect(offer.note).toBeNull();
  });

  it("says an engine with one voice reads everyone in it, and how to have the names said", () => {
    const offer = voicesFor(engine({ title: "Piper", can_clone: false, clone_variant: null }), presets[0], [presets[0]], []);
    expect(offer.presets).toHaveLength(1);
    expect(offer.note).toBe("Piper has one voice, so everyone is read in it. Switch a name off to have the name said as part of the text.");
  });

  it("says so when only one voice can be used with the main one, though the engine has several", () => {
    const offer = voicesFor(engine(), saved[0], presets, [saved[0]]);
    expect(offer.note).toMatch(/^Only one voice can be used together with the main voice here/);
  });

  it("offers none of the person's own voices to an engine that cannot clone, whatever its variants say", () => {
    const plain = engine({ can_clone: false, preset_variant: "default", clone_variant: "default" }); // (not a real engine: the same name for both kinds, which would make them one)
    expect(voicesFor(plain, preset("a"), [preset("a"), preset("b")], [mine("m")]).saved).toEqual([]);
  });

  it("offers nothing without a main voice, and nothing of the person's own to an engine that cannot clone", () => {
    expect(voicesFor(engine(), null, presets, saved)).toEqual({ presets: [], saved: [], note: null });
    expect(voicesFor(engine({ can_clone: false, clone_variant: null }), presets[0], presets, saved).saved).toEqual([]);
  });

  it("knows a pick that is no longer on offer", () => {
    const offer = { presets: [presets[0]], saved: [] as SavedVoice[] };
    expect(pickAvailable({ kind: "preset", id: "anna" }, offer)).toBe(true);
    expect(pickAvailable({ kind: "preset", id: "bea" }, offer)).toBe(false);
    expect(pickAvailable({ kind: "saved", id: "v1" }, offer)).toBe(false);
    expect(pickAvailable(null, offer)).toBe(false);
  });
});

describe("the speakers that are sent", () => {
  const offer = { presets: [preset("bea"), preset("carl")], saved: [] as SavedVoice[] };
  const used = new Set(["Alice", "Bob"]);

  it("are the names that are on and have a line that will be read, each with its voice", () => {
    const cast: Cast = { Alice: { on: true, voice: { kind: "preset", id: "bea" } }, Bob: { on: true, voice: null } };
    expect(speakersToSend(SPEAKERS, cast, used, offer)).toEqual([{ name: "Alice", voice: "bea", saved_voice: null }, { name: "Bob", voice: "", saved_voice: null }]);
  });

  it("leave out a name that is off, and a name with no line in what is read (its part was left out)", () => {
    const cast: Cast = { Alice: { on: false, voice: null }, Bob: { on: true, voice: null } };
    expect(speakersToSend(SPEAKERS, cast, used, offer).map((s) => s.name)).toEqual(["Bob"]);
    expect(speakersToSend(SPEAKERS, initialCast(SPEAKERS), new Set(["Alice"]), offer).map((s) => s.name)).toEqual(["Alice"]);
  });

  it("send a voice of the person's own as saved_voice, and fall back to the main voice for one that is gone", () => {
    const withSaved = { presets: [], saved: [mine("v1")] };
    expect(speakersToSend(SPEAKERS.slice(0, 1), { Alice: { on: true, voice: { kind: "saved", id: "v1" } } }, used, withSaved)[0]).toEqual({ name: "Alice", voice: "", saved_voice: "v1" });
    expect(speakersToSend(SPEAKERS.slice(0, 1), { Alice: { on: true, voice: { kind: "saved", id: "v9" } } }, used, withSaved)[0]).toEqual({ name: "Alice", voice: "", saved_voice: null });
  });

  it("treat a name the cast does not know as on, with the main voice", () => {
    expect(speakersToSend(SPEAKERS, {}, used, offer)).toHaveLength(2);
  });
});

describe("a paragraph as it is sent", () => {
  const line = { kind: "paragraph" as const, text: "Hello there.", speaker: "Alice", label: "Alice:" };

  it("is just its words when nobody speaks it", () => {
    expect(paragraphToSend({ kind: "paragraph", text: "Words.", speaker: null, label: null }, {})).toEqual({ kind: "paragraph", text: "Words." });
    expect(paragraphToSend({ kind: "heading", text: "Chapter", speaker: null, label: null }, {})).toEqual({ kind: "heading", text: "Chapter" });
  });

  it("names the speaker, without the label, while the name is on", () => {
    expect(paragraphToSend(line, { Alice: { on: true, voice: null } })).toEqual({ kind: "paragraph", text: "Hello there.", speaker: "Alice" });
    expect(paragraphToSend(line, {})).toEqual({ kind: "paragraph", text: "Hello there.", speaker: "Alice" });
  });

  it("puts the label back and names no one when the name is turned off, so it is read as written", () => {
    expect(paragraphToSend(line, { Alice: { on: false, voice: null } })).toEqual({ kind: "paragraph", text: "Alice: Hello there." });
    expect(paragraphToSend({ ...line, label: null }, { Alice: { on: false, voice: null } }).text).toBe("Alice: Hello there."); // and a missing label is the name and a colon
    expect(paragraphToSend({ ...line, label: "ALICE:" }, { Alice: { on: false, voice: null } }).text).toBe("ALICE: Hello there."); // as it was spelled in the text
  });
});

describe("the names that have a line in what will be read", () => {
  const draftPart = (include: boolean, ...lines: [string | null, string, (string | null)?][]) => ({
    key: 0, title: "P", include, note: null,
    paragraphs: lines.map(([speaker, text, skipped = null]) => ({ kind: "paragraph" as const, text, skipped, edited: false, speaker, label: speaker ? `${speaker}:` : null })),
  });

  it("are the speakers of the included parts", () => {
    expect([...speakersUsed([draftPart(true, ["Alice", "Hello."], [null, "Narration."], ["Bob", "Hi."])])].sort()).toEqual(["Alice", "Bob"]);
  });

  it("leave out a part that is left out, a line that is not read and a line the person emptied", () => {
    // each of them is in a part that is read (it has a line of Alice's), so only the line itself can leave a name out
    const parts = [draftPart(true, ["Alice", "Hello."], ["Carol", "x", "code"], ["Dave", "   "]), draftPart(false, ["Bob", "Hi."])];
    expect([...speakersUsed(parts)]).toEqual(["Alice"]);
  });

  it("are none for a part with nothing to read, even when it has a name in it", () => {
    expect(speakersUsed([draftPart(true, ["Carol", "x", "code"])]).size).toBe(0);
  });

  it("are none when nobody speaks", () => {
    expect(speakersUsed([draftPart(true, [null, "Just words."])]).size).toBe(0);
    expect(speakersUsed([]).size).toBe(0);
  });
});

describe("what a card says of the cast of a reading", () => {
  it("lists each speaker with the voice they were given, or the main voice", () => {
    expect(castLine([{ name: "Alice", voice: { label: "Bea" } }, { name: "Bob", voice: null }])).toBe("Speakers: Alice in Bea, Bob in the main voice.");
  });

  it("says nothing for a reading with no speakers, or one made before speakers existed", () => {
    expect(castLine([])).toBeNull();
    expect(castLine(undefined)).toBeNull();
    expect(castLine(null)).toBeNull();
  });
});
