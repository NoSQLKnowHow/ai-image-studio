import { describe, expect, it } from "vitest";
import {
  ARROW_SECONDS, DEFAULT_PLAYER_PREFS, ENGINE_STATES, NO_CHOICE, NO_VOICE_FILTER, PART_TITLE_MAX, PLAYER_PREFS_KEY, SPEECH_CHOICE_KEY, SPEEDS, STYLE_EXAMPLES, TEXT_SIZES,
  audioSizeText, buildReadingBody, canListen, canceledLine, candidateClips, chooseEngine, clockText, continueText, draftFromPrepared, editParagraph, engineUsable,
  filterVoices, findVoice, firstVoice, jobSettled, languageChoices, languageToSend, lengthText, licenceBadge, listenLabel, mergeProblem, mergeWithNext, neighbourPiece,
  partChars, partsToRead, pieceAt, pieceStart, playerAction, positionFor, progressLine, readChoice, readPlayerPrefs, readingFacts, readingProblem, readingTitle,
  readingTotals, renamePart, resumeTime, saveChoice, savePlayerPrefs, savedVoiceFacts, seekedTime, setInclude, sourceText, speedText, startingPart, stepSpeed,
  styleControl, styleToSend, toGoText, totalsLine, voiceFacts, voiceMatches, voiceName,
  RECORDING_ACCEPT, RECORDING_EXTENSIONS, deleteVoiceQuestion, descriptionProblem, recordingFacts, recordingProblem, voiceNameProblem,
  RESTART_AFTER, nextPieceSource, nextTime, partListText, partPlayable, partSeconds, partTime, pieceMade, pieceSource, previousTime, sourceFor, wholeTime,
  FILE_ACCEPT, FILE_EXTENSIONS, aboutLength, fileProblem, queuedText, sampleBody, sampleKey, sampleLabel, sampleWords, normalizeUrl, partLengthText, pastedTextLine, reviewChanged, reviewLine, skippedText, switchesChanged, urlProblem,
} from "./speech";
import type { DraftPart } from "./speech";
import type { KeyValueStore } from "./options";
import type { PartPiece, PresetVoice, ReadingInfo, SavedVoice, SpeechEngine, SpeechJob } from "./speechTypes";
import { CAPS, SPEECH_CONTRACT, makePartDetail, makePrepared, makeSpeechRun } from "./testdata";

// The Speech tab's logic with no page around it (DESIGN.md §38). The fixtures are the contract's (backend/tests/speech_contract.json): what the server sent when
// its fake engines ran, so a rule here is tried on the shapes the page will really get.

const memoryStore = (initial: Record<string, string> = {}): KeyValueStore & { data: Record<string, string> } => {
  const data = { ...initial };
  return { available: true, get: (key) => data[key] ?? null, set: (key, value) => void (data[key] = value), data };
};

const ENGINES = CAPS.speech.engines;
const FULL = ENGINES.find((e) => e.id === "fake") as SpeechEngine; // style, cloning, design; English and German
const BASIC = ENGINES.find((e) => e.id === "fake-basic") as SpeechEngine; // none of those; non-commercial licence
const PRESETS = SPEECH_CONTRACT.engine_voices.presets as unknown as PresetVoice[];
const SAVED = SPEECH_CONTRACT.engine_voices.saved as unknown as SavedVoice[];
const ANNA = PRESETS.find((v) => v.id === "anna") as PresetVoice;

const preset = (overrides: Partial<PresetVoice> = {}): PresetVoice => ({ ...ANNA, ...overrides });
const saved = (overrides: Partial<SavedVoice> = {}): SavedVoice => ({ ...SAVED[0], ...overrides });
const engine = (overrides: Partial<SpeechEngine> = {}): SpeechEngine => ({ ...FULL, ...overrides });

/** A draft of parts, each given as [title, [paragraph texts], include?]. */
const draft = (...parts: [string, string[], boolean?][]): DraftPart[] =>
  parts.map(([title, texts, include = true], key) => ({
    key, title, include, note: null, paragraphs: texts.map((text) => ({ kind: "paragraph" as const, text, skipped: null, edited: false })),
  }));

describe("the fixtures are the server's", () => {
  it("has two engines, one that can do everything and one that can do nothing extra", () => {
    expect(ENGINES.map((e) => e.id)).toEqual(["fake", "fake-basic"]);
    expect([FULL.can_style, FULL.can_clone, FULL.can_design]).toEqual([true, true, true]);
    expect([BASIC.can_style, BASIC.can_clone, BASIC.can_design]).toEqual([false, false, false]);
    expect(BASIC.licence.noncommercial).toBe(true);
  });
});

// ------------------------------------------------------------------ numbers as words
describe("clockText", () => {
  it.each([[0, "0:00"], [5, "0:05"], [65, "1:05"], [3599, "59:59"], [3600, "1:00:00"], [8047, "2:14:07"], [59.9, "0:59"], [-3, "0:00"], [NaN, "0:00"]])(
    "%s seconds is %s", (seconds, text) => expect(clockText(seconds)).toBe(text));
});

describe("lengthText", () => {
  it.each([[0, "less than a minute"], [29, "less than a minute"], [30, "1 min"], [45 * 60, "45 min"], [3600, "1 h"], [3660, "1 h 01 min"], [40800, "11 h 20 min"], [-5, "less than a minute"]])(
    "%s seconds is %s", (seconds, text) => expect(lengthText(seconds)).toBe(text));
});

describe("audioSizeText", () => {
  it.each([[500, "1 KB"], [35_400, "35 KB"], [999_499, "999 KB"], [999_500, "1 MB"], [35_400_000, "35 MB"], [999_499_999, "999 MB"], [999_500_000, "1 GB"], [1_500_000_000, "1.5 GB"],
    [1_958_400_000, "2 GB"], [12_300_000_000, "12 GB"], [9_940_000_000, "9.9 GB"], [9_960_000_000, "10 GB"]])("%s bytes is %s", (bytes, text) => expect(audioSizeText(bytes)).toBe(text));
});

// ------------------------------------------------------------------ the review
describe("the draft of a review", () => {
  it("is made from the server's review, every paragraph unedited", () => {
    const prepared = makePrepared();
    const parts = draftFromPrepared(prepared);
    expect(parts).toHaveLength(prepared.parts.length);
    expect(parts[0]).toMatchObject({ key: 0, title: prepared.parts[0].title, include: true, note: null });
    expect(parts[0].paragraphs.map((p) => p.text)).toEqual(prepared.parts[0].paragraphs.map((p) => p.text));
    expect(parts.every((p) => p.paragraphs.every((q) => q.edited === false))).toBe(true);
  });

  it("keeps a part that the server left out (an EPUB's front matter) left out, with its note", () => {
    const prepared = makePrepared({ parts: [{ ...makePrepared().parts[0], include: false, note: "Front matter" }] });
    expect(draftFromPrepared(prepared)[0]).toMatchObject({ include: false, note: "Front matter" });
  });

  it("counts the characters that will be read: words as they stand, a space between them once, skipped paragraphs not at all", () => {
    const part = draft(["P", ["a  b   c", "  dd "]])[0];
    part.paragraphs.push({ kind: "paragraph", text: "a code block that is not read", skipped: "code", edited: false });
    expect(partChars(part)).toBe(5 + 2);
  });
});

describe("merging parts", () => {
  const parts = draft(["One", ["a1", "a2"]], ["Two", ["b1"]], ["Three", ["c1"], false]);

  it("joins a part and the one after it: the first title, all the paragraphs, in order", () => {
    const merged = mergeWithNext(parts, 0);
    expect(merged.map((p) => p.title)).toEqual(["One", "Three"]);
    expect(merged[0].paragraphs.map((p) => p.text)).toEqual(["a1", "a2", "b1"]);
    expect(merged[0].key).toBe(0); // it stays the first one's, so the page keeps what it showed of it
    expect(parts).toHaveLength(3); // the list it was given is not changed
  });

  it("is not offered for the last part", () => {
    expect(mergeProblem(parts, 2)).toMatch(/no part after/);
    expect(mergeWithNext(parts, 2)).toEqual(parts);
  });

  it("is not offered between an included part and one that is left out, either way round, and says what to do", () => {
    expect(mergeProblem(parts, 1)).toMatch(/Include both parts, or leave both out/);
    expect(mergeWithNext(parts, 1)).toEqual(parts);
    const other = draft(["One", ["a1"], false], ["Two", ["b1"]]);
    expect(mergeProblem(other, 0)).toMatch(/Include both/);
  });

  it("is offered between two that are both left out", () => {
    const both = draft(["One", ["a1"], false], ["Two", ["b1"], false]);
    expect(mergeProblem(both, 0)).toBeNull();
    expect(mergeWithNext(both, 0)).toHaveLength(1);
  });

  it("takes the note of the first, or of the second when the first has none", () => {
    const [a, b] = draft(["One", ["a1"]], ["Two", ["b1"]]);
    expect(mergeWithNext([{ ...a, note: "first" }, { ...b, note: "second" }], 0)[0].note).toBe("first");
    expect(mergeWithNext([a, { ...b, note: "second" }], 0)[0].note).toBe("second");
  });

  it("does nothing for a place that is not in the list", () => {
    expect(mergeProblem(parts, -1)).not.toBeNull();
    expect(mergeProblem(parts, 9)).not.toBeNull();
  });
});

describe("changing a part", () => {
  const parts = draft(["One", ["a1", "a2"]], ["Two", ["b1"]]);

  it("includes and leaves out exactly the part it is told to", () => {
    const out = setInclude(parts, 1, false);
    expect(out.map((p) => p.include)).toEqual([true, false]);
    expect(parts[1].include).toBe(true);
  });

  it("renames one part, cutting a title at the limit, and allows an empty title (the server then says 'Part n')", () => {
    expect(renamePart(parts, 0, "Chapter one").map((p) => p.title)).toEqual(["Chapter one", "Two"]);
    expect(renamePart(parts, 0, "x".repeat(PART_TITLE_MAX + 50))[0].title).toHaveLength(PART_TITLE_MAX);
    expect(renamePart(parts, 1, "")[1].title).toBe("");
  });

  it("edits one paragraph of one part and marks only it as edited", () => {
    const out = editParagraph(parts, 0, 1, "new words");
    expect(out[0].paragraphs.map((p) => [p.text, p.edited])).toEqual([["a1", false], ["new words", true]]);
    expect(out[1]).toBe(parts[1]);
  });

  it("keeps a paragraph marked as edited once it has been, whatever is done to it after: other words, the same words again, or the first words put back", () => {
    const once = editParagraph(parts, 0, 0, "other");
    expect(editParagraph(once, 0, 0, "other")[0].paragraphs[0].edited).toBe(true); // the same words as it has now: nothing new, and nothing forgotten
    expect(editParagraph(once, 0, 0, "a1")[0].paragraphs[0].edited).toBe(true); // the first words put back: the page cannot know they are the first
    expect(editParagraph(once, 0, 0, "yet other")[0].paragraphs[0].edited).toBe(true);
  });

  it("does not mark a paragraph edited when its words are the same", () => {
    expect(editParagraph(parts, 0, 0, "a1")[0].paragraphs[0].edited).toBe(false);
  });

  it("reads only parts that are included and have something to say (an emptied paragraph says nothing)", () => {
    const emptied = editParagraph(setInclude(parts, 1, false), 0, 0, "  ");
    expect(partsToRead(emptied).map((p) => p.title)).toEqual(["One"]); // "One" still has a2
    expect(partsToRead(editParagraph(emptied, 0, 1, ""))).toEqual([]);
  });
});

describe("what a reading will take", () => {
  const limits = { chars_per_second: 14 };

  it("is the characters over the speaking rate, and the bytes of that much 16-bit mono at the engine's rate", () => {
    const parts = draft(["One", ["x".repeat(1400)]], ["Two", ["y".repeat(2800)]]);
    const totals = readingTotals(parts, limits, 24000);
    expect(totals).toEqual({ parts: 2, chars: 4200, seconds: 300, bytes: 300 * 24000 * 2 });
  });

  it("counts only what will be read", () => {
    const parts = draft(["One", ["x".repeat(140)]], ["Two", ["y".repeat(1400)], false]);
    expect(readingTotals(parts, limits, 24000)).toMatchObject({ parts: 1, chars: 140, seconds: 10 });
  });

  it("is nothing for nothing, and does not divide by a rate of nothing", () => {
    expect(readingTotals([], limits, 24000)).toEqual({ parts: 0, chars: 0, seconds: 0, bytes: 0 });
    expect(readingTotals(draft(["One", ["abc"]]), { chars_per_second: 0 }, 24000).seconds).toBe(0);
  });

  it("is said as the design says it", () => {
    const totals = { parts: 12, chars: 0, seconds: 40800, bytes: 1_958_400_000 };
    expect(totalsLine(totals, "Kokoro-82M", "Heart")).toBe("12 parts, about 11 h 20 min of audio, about 2 GB. Kokoro-82M, Heart.");
    expect(totalsLine({ ...totals, parts: 1 }, "Piper", "")).toBe("1 part, about 11 h 20 min of audio, about 2 GB. Piper.");
  });
});

// ------------------------------------------------------------------ engines
describe("engines", () => {
  it("names every state the server can send, once", () => {
    expect(Object.keys(ENGINE_STATES).sort()).toEqual(["checking", "error", "needs_memory", "not_installed", "ready"]);
    expect(ENGINE_STATES.needs_memory.label).toBe("Needs more memory");
  });

  it("can be asked for when ready or short of memory, and not otherwise", () => {
    expect(engineUsable(engine({ state: "ready" }))).toBe(true);
    expect(engineUsable(engine({ state: "needs_memory" }))).toBe(true);
    for (const state of ["checking", "not_installed", "error"] as const) expect(engineUsable(engine({ state }))).toBe(false);
  });

  it("says its licence, and that it is for non-commercial use where it is", () => {
    expect(licenceBadge(FULL)).toBe("Test only");
    expect(licenceBadge(BASIC)).toBe("Test only (non-commercial) · non-commercial use only");
  });

  it("starts from the remembered engine when it is usable, else the first that is, else the remembered one so its reason shows, else the first", () => {
    const down = (id: string, state: SpeechEngine["state"]) => ({ ...CAPS.speech, engines: CAPS.speech.engines.map((e) => (e.id === id ? { ...e, state } : e)) });
    expect(chooseEngine(CAPS.speech, "fake-basic")?.id).toBe("fake-basic");
    expect(chooseEngine(CAPS.speech, "gone")?.id).toBe("fake");
    expect(chooseEngine(CAPS.speech, null)?.id).toBe("fake");
    expect(chooseEngine(down("fake-basic", "not_installed"), "fake-basic")?.id).toBe("fake");
    const none = { ...CAPS.speech, engines: CAPS.speech.engines.map((e) => ({ ...e, state: "not_installed" as const })) };
    expect(chooseEngine(none, "fake-basic")?.id).toBe("fake-basic");
    expect(chooseEngine(none, "gone")?.id).toBe("fake");
    expect(chooseEngine({ ...CAPS.speech, engines: [] }, null)).toBeNull();
  });
});

// ------------------------------------------------------------------ voices
describe("voices", () => {
  const voices = [
    preset({ id: "a", gender: "female", language: "English" }),
    preset({ id: "b", gender: "male", language: "English" }),
    preset({ id: "c", gender: "female", language: "German" }),
    preset({ id: "d", gender: null, language: null }),
  ];
  const ids = (list: PresetVoice[]) => list.map((v) => v.id);

  it("shows every voice when no chip is chosen", () => {
    expect(ids(filterVoices(voices, NO_VOICE_FILTER))).toEqual(["a", "b", "c", "d"]);
  });

  it("filters by gender and by English, together, keeping the order", () => {
    expect(ids(filterVoices(voices, { gender: "female", english: false }))).toEqual(["a", "c"]);
    expect(ids(filterVoices(voices, { gender: "male", english: false }))).toEqual(["b"]);
    expect(ids(filterVoices(voices, { gender: "any", english: true }))).toEqual(["a", "b"]);
    expect(ids(filterVoices(voices, { gender: "female", english: true }))).toEqual(["a"]);
  });

  it("does not let a voice that states no gender or language pass a chip that asks for one (it must not claim what the source did not say)", () => {
    expect(voiceMatches(voices[3], { gender: "female", english: false })).toBe(false);
    expect(voiceMatches(voices[3], { gender: "any", english: true })).toBe(false);
    expect(voiceMatches(voices[3], NO_VOICE_FILTER)).toBe(true);
  });

  it("says what the source states, and says so where it does not (criterion 261)", () => {
    expect(voiceFacts(preset({ gender: "female", language: "English", accent: "American" }))).toBe("Female · English · American");
    expect(voiceFacts(preset({ gender: "male", language: "English", accent: null }))).toBe("Male · English · Accent not stated");
    expect(voiceFacts(preset({ gender: null, language: null, accent: null }))).toBe("Gender not stated · Language not stated · Accent not stated");
  });

  it("says how a voice the person made came about", () => {
    expect(savedVoiceFacts({ kind: "designed", seconds: 4.57 })).toBe("Designed from a description · 5 s");
    expect(savedVoiceFacts({ kind: "cloned", seconds: 10 })).toBe("Cloned from a recording · 10 s");
    expect(savedVoiceFacts({ kind: "cloned", seconds: 75 })).toBe("Cloned from a recording · 1 min");
    expect(savedVoiceFacts({ kind: "cloned", seconds: null })).toBe("Cloned from a recording");
  });

  it("finds the voice that was picked, among presets or the person's own, by kind and id", () => {
    expect(findVoice({ kind: "preset", id: "anna" }, PRESETS, SAVED)).toEqual(ANNA);
    expect(findVoice({ kind: "saved", id: SAVED[0].id }, PRESETS, SAVED)).toEqual(SAVED[0]);
    expect(findVoice({ kind: "saved", id: "anna" }, PRESETS, SAVED)).toBeNull(); // an id of the other kind is not a match
    expect(findVoice({ kind: "preset", id: "nope" }, PRESETS, SAVED)).toBeNull();
    expect(findVoice(null, PRESETS, SAVED)).toBeNull();
  });

  it("names a voice by its label, or by the name the person gave it", () => {
    expect(voiceName(ANNA)).toBe("Anna");
    expect(voiceName(saved({ name: "My own voice" }))).toBe("My own voice");
  });

  it("starts from the person's own voice first, else the first preset the chips allow, else the first preset", () => {
    expect(firstVoice(voices, [saved({ id: "s1" })], NO_VOICE_FILTER)).toEqual({ kind: "saved", id: "s1" });
    expect(firstVoice(voices, [], { gender: "male", english: false })).toEqual({ kind: "preset", id: "b" });
    expect(firstVoice(voices, [], { gender: "male", english: true })).toEqual({ kind: "preset", id: "b" });
    expect(firstVoice([voices[2]], [], { gender: "male", english: false })).toEqual({ kind: "preset", id: "c" }); // none passes: the first there is
    expect(firstVoice([], [], NO_VOICE_FILTER)).toBeNull();
  });
});

// ------------------------------------------------------------------ style and language
describe("style", () => {
  it("is a box, with examples, for a voice that takes a style on an engine that does", () => {
    expect(styleControl(FULL, ANNA)).toEqual({ kind: "box", label: "How should it sound?", examples: STYLE_EXAMPLES });
    expect(styleControl(FULL, null).kind).toBe("box"); // nothing chosen yet: the box is there to be filled in
  });

  it("is a line saying why not, and never nothing, for the others (criterion 264)", () => {
    expect(styleControl(BASIC, preset())).toEqual({ kind: "line", text: "Fake basic speech does not take a style note." });
    expect(styleControl(FULL, preset({ styleable: false }))).toEqual({ kind: "line", text: "This voice does not take a style note." });
    const line = styleControl(FULL, saved());
    expect(line.kind === "line" && line.text).toMatch(/takes no style note/);
  });

  it("is sent trimmed, and only where the box is", () => {
    expect(styleToSend(FULL, ANNA, "  calm and slow ")).toBe("calm and slow");
    expect(styleToSend(FULL, ANNA, "   ")).toBeNull();
    expect(styleToSend(BASIC, preset(), "calm")).toBeNull();
    expect(styleToSend(FULL, preset({ styleable: false }), "calm")).toBeNull();
    expect(styleToSend(FULL, saved(), "calm")).toBeNull();
  });
});

describe("language", () => {
  it("is a list with Auto first where the engine takes one, and nothing where the voice decides", () => {
    expect(languageChoices(FULL)).toEqual(["Auto", "English", "German"]);
    expect(languageChoices(BASIC)).toBeNull();
  });

  it("is sent only when it is one the engine speaks: Auto, an empty choice and a stale one are not", () => {
    expect(languageToSend(FULL, "German")).toBe("German");
    for (const language of ["Auto", "", "French"]) expect(languageToSend(FULL, language)).toBeNull();
    expect(languageToSend(BASIC, "English")).toBeNull();
  });
});

// ------------------------------------------------------------------ making a reading
describe("whether a reading can be asked for", () => {
  const parts = draft(["One", ["some words"]]);

  it("can, with something to read, a usable engine and a voice", () => {
    expect(readingProblem(parts, FULL, ANNA)).toBeNull();
    expect(readingProblem(parts, engine({ state: "needs_memory" }), ANNA)).toBeNull();
  });

  it("says why not, the first reason found, in this order: no engine, engine not usable (its own reason), no voice, nothing to read", () => {
    expect(readingProblem(parts, null, ANNA)).toMatch(/no speech engine/);
    expect(readingProblem(parts, engine({ state: "checking" }), ANNA)).toMatch(/still being checked/);
    expect(readingProblem(parts, engine({ state: "not_installed", reason: "Its libraries are not installed." }), ANNA)).toBe("Its libraries are not installed.");
    expect(readingProblem(parts, engine({ state: "error", reason: null }), ANNA)).toBe("Fake speech cannot run here.");
    expect(readingProblem(parts, engine({ state: "not_installed", reason: "Not installed." }), null)).toBe("Not installed."); // the engine's trouble comes before the voice
    expect(readingProblem(parts, null, null)).toMatch(/no speech engine/); // and no engine before everything
    expect(readingProblem(setInclude(parts, 0, false), FULL, null)).toBe("Choose a voice."); // a voice is asked for before the text is judged
    expect(readingProblem(parts, FULL, null)).toBe("Choose a voice.");
    expect(readingProblem(setInclude(parts, 0, false), FULL, ANNA)).toMatch(/Include at least one part/);
    expect(readingProblem([], FULL, ANNA)).toBe("There is no text to read yet.");
  });
});

describe("the request for a reading", () => {
  const parts = draft(["  One ", ["first  paragraph", "   ", "second"]], ["Two", ["hidden"], false], ["Three", ["only skipped"]]);
  parts[2].paragraphs[0].skipped = "code";

  it("leaves out a paragraph that is skipped (code, a table) from a part that has other words to say", () => {
    const mixed = draft(["Mixed", ["spoken words", "code that is not read", "more spoken words"]]);
    mixed[0].paragraphs[1].skipped = "code";
    const body = buildReadingBody("A book", { kind: "text" }, mixed, FULL, ANNA, "", "");
    expect(body.parts[0].paragraphs.map((p) => p.text)).toEqual(["spoken words", "more spoken words"]);
  });

  it("has the parts that will be read, their spoken paragraphs, with spaces collapsed, and nothing else", () => {
    const body = buildReadingBody("A book", { kind: "text" }, parts, FULL, ANNA, "", "Auto");
    expect(body.parts).toEqual([{ title: "One", paragraphs: [{ kind: "paragraph", text: "first paragraph" }, { kind: "paragraph", text: "second" }] }]);
  });

  it("names the engine and a preset voice, with no saved voice", () => {
    const body = buildReadingBody("A book", { kind: "text" }, parts, FULL, ANNA, "", "Auto");
    expect(body).toMatchObject({ title: "A book", engine: "fake", voice: "anna", saved_voice: null, style: null, language: null, source: { kind: "text" } });
  });

  it("names a saved voice by its id and leaves the preset voice empty", () => {
    const body = buildReadingBody("A book", { kind: "text" }, parts, FULL, saved({ id: "s1" }), "calm", "German");
    expect(body).toMatchObject({ voice: "", saved_voice: "s1", style: null, language: "German" }); // a saved voice takes no style
  });

  it("sends the style and the language where they apply", () => {
    const body = buildReadingBody("A book", { kind: "text" }, parts, FULL, ANNA, " calm and slow ", "English");
    expect(body).toMatchObject({ style: "calm and slow", language: "English" });
    const plain = buildReadingBody("A book", { kind: "text" }, parts, BASIC, preset(), "calm", "English");
    expect(plain).toMatchObject({ style: null, language: null });
  });

  it("falls back to 'Reading' for an empty title, and cuts a long one", () => {
    expect(buildReadingBody("   ", { kind: "text" }, parts, FULL, ANNA, "", "").title).toBe("Reading");
    expect(buildReadingBody("x".repeat(PART_TITLE_MAX + 9), { kind: "text" }, parts, FULL, ANNA, "", "").title).toHaveLength(PART_TITLE_MAX);
  });

  it("copies the source, so the request does not share an object with the review", () => {
    const source = { kind: "url", url: "https://example.org/a", host: "example.org" };
    const body = buildReadingBody("A book", source, parts, FULL, ANNA, "", "");
    expect(body.source).toEqual(source);
    expect(body.source).not.toBe(source);
  });
});

// ------------------------------------------------------------------ a reading's card
describe("where the text came from", () => {
  it("says Pasted, the host of a web page, or the name of a file", () => {
    expect(sourceText({ kind: "text" })).toBe("Pasted");
    expect(sourceText({ kind: "paste" })).toBe("Pasted");
    expect(sourceText(null)).toBe("Pasted");
    expect(sourceText({ kind: "url", url: "https://example.org/a", host: "example.org" })).toBe("example.org");
    expect(sourceText({ kind: "url", url: "https://example.org/a", host: "" })).toBe("A web page");
    expect(sourceText({ kind: "file", name: "book.epub" })).toBe("book.epub");
    expect(sourceText({ kind: "file", name: "" })).toBe("A file");
  });
});

describe("a reading's progress", () => {
  const reading = (changes: Partial<ReadingInfo> = {}): ReadingInfo => ({ ...makeSpeechRun().reading as ReadingInfo, ...changes });
  const parts = (...states: ("done" | "started" | "waiting")[]) => states.map((state, idx) => ({ idx, title: `P${idx}`, chars: 10, pieces: 2, made: state === "done" ? 2 : state === "started" ? 1 : 0, seconds: 1, state }));

  it("says what there is when nothing is made yet", () => {
    const r = reading({ parts: parts("waiting", "waiting"), parts_total: 2, parts_done: 0, pieces_done: 0, pieces_total: 4, seconds_made: 0 });
    expect(progressLine(r)).toBe("2 parts · 4 paragraphs · none made yet");
    expect(progressLine(reading({ parts: parts("waiting"), parts_total: 1, parts_done: 0, pieces_done: 0, pieces_total: 1 }))).toBe("1 part · 1 paragraph · none made yet");
  });

  it("says the part being made, the paragraph, and how much is made, as the design shows it", () => {
    const r = reading({ parts: parts("done", "done", "started", "waiting"), parts_total: 4, parts_done: 2, pieces_done: 5, pieces_total: 8, seconds_made: 3 * 3600 + 2 * 60 });
    expect(progressLine(r)).toBe("Part 3 of 4 · paragraph 5 of 8 · 3 h 02 min made");
  });

  it("says all the parts when all are done", () => {
    const r = reading({ parts: parts("done", "done"), parts_total: 2, parts_done: 2, pieces_done: 4, pieces_total: 4, seconds_made: 600 });
    expect(progressLine(r)).toBe("All 2 parts · paragraph 4 of 4 · 10 min made");
  });

  it("says Canceled with how many parts are done (criterion 279)", () => {
    expect(canceledLine(reading({ parts_done: 3, parts_total: 12 }))).toBe("Canceled · 3 of 12 parts");
    expect(canceledLine(reading({ parts_done: 0, parts_total: 1 }))).toBe("Canceled · 0 of 1 part");
  });

  it("says how long is left only when the server knows", () => {
    expect(toGoText(reading({ seconds_to_go: 840 }))).toBe("about 14 min to go");
    expect(toGoText(reading({ seconds_to_go: null }))).toBeNull();
  });
});

describe("listening", () => {
  const at = (part: number, offset_ms: number, extra: Partial<ReadingInfo> = {}) => ({ ...(makeSpeechRun().reading as ReadingInfo), position: { part, piece: 0, offset_ms }, ...extra });

  it("can be done once anything is made, including a part that is not finished", () => {
    expect(canListen(null)).toBe(false);
    expect(canListen(at(0, 0, { pieces_done: 0 }))).toBe(false);
    expect(canListen(at(0, 0, { pieces_done: 1, parts_done: 0 }))).toBe(true);
  });

  it("is Listen with no place to continue from, and Continue listening with one", () => {
    expect(listenLabel(null)).toBe("Listen");
    expect(listenLabel({ ...at(0, 0), position: null })).toBe("Listen");
    expect(listenLabel(at(0, 500))).toBe("Listen"); // a moment into the first part is where anyone starts
    expect(listenLabel(at(0, 1000))).toBe("Continue listening");
    expect(listenLabel(at(2, 0))).toBe("Continue listening");
  });

  it("says where, as the design does: the time in the part, and the part's number", () => {
    expect(continueText(at(4, (2 * 3600 + 14 * 60 + 7) * 1000))).toBe("Continue listening from 2:14:07 in part 5");
    expect(continueText(at(0, 62_000))).toBe("Continue listening from 1:02 in part 1");
    expect(continueText(at(0, 0))).toBeNull();
    expect(continueText(null)).toBeNull();
  });

  it("opens at the part the person stopped in, if it is there, else the first", () => {
    expect(startingPart(at(1, 5000))).toBe(1);
    expect(startingPart(at(9, 5000))).toBe(0); // a part that is not there (the reading cannot lose parts, but a stale number must not break the page)
    const count = (at(0, 0).parts as unknown[]).length;
    expect(startingPart(at(count - 1, 5000))).toBe(count - 1); // the last part is there
    expect(startingPart(at(count, 5000))).toBe(0); // one past the last is not
    expect(startingPart({ ...at(0, 0), position: null })).toBe(0);
  });
});

describe("what a card says of a reading", () => {
  it("has the source, the engine and the voice, from what the server recorded", () => {
    const run = makeSpeechRun();
    expect(readingFacts(run)).toBe("Pasted · Fake speech · Anna");
    const web = makeSpeechRun({ reading: { ...(run.reading as ReadingInfo), source: { kind: "url", url: "https://example.org/x", host: "example.org" } } });
    expect(readingFacts(web)).toBe("example.org · Fake speech · Anna");
  });

  it("has the title the person gave", () => {
    const run = makeSpeechRun();
    expect(readingTitle(run)).toBe("A test reading");
    expect(readingTitle({ ...run, reading: null, options: { ...run.options, title: null }, prompt: "From the prompt" })).toBe("From the prompt");
  });
});

// ------------------------------------------------------------------ the player
describe("speed", () => {
  it("has the six the design asks for, from 0.75 to 2, each said with a ×", () => {
    expect([...SPEEDS]).toEqual([0.75, 1, 1.25, 1.5, 1.75, 2]);
    expect(speedText(1.25)).toBe("1.25×");
    expect(speedText(2)).toBe("2×");
  });

  it("steps up and down the list and stops at its ends", () => {
    expect(stepSpeed(1, 1)).toBe(1.25);
    expect(stepSpeed(1, -1)).toBe(0.75);
    expect(stepSpeed(2, 1)).toBe(2);
    expect(stepSpeed(0.75, -1)).toBe(0.75);
  });

  it("takes a speed that is not on the list as the nearest one before it steps", () => {
    expect(stepSpeed(1.1, 1)).toBe(1.25); // nearest 1, one up
    expect(stepSpeed(1.4, -1)).toBe(1.25); // nearest 1.5, one down
    expect(stepSpeed(9, -1)).toBe(1.75); // nearest 2, one down
  });
});

describe("moving in time", () => {
  it("moves by the amount, and stays inside the part", () => {
    expect(seekedTime(100, 15, 3600)).toBe(115);
    expect(seekedTime(100, -15, 3600)).toBe(85);
    expect(seekedTime(5, -15, 3600)).toBe(0);
    expect(seekedTime(3595, 15, 3600)).toBe(3600);
    expect(ARROW_SECONDS).toBe(10);
  });
});

describe("keys of the player", () => {
  it.each([
    [" ", "toggle"], ["Spacebar", "toggle"], ["j", "previous"], ["J", "previous"], ["k", "next"], ["K", "next"], ["ArrowLeft", "back"], ["ArrowRight", "forward"],
  ])("%j does %s", (key, action) => expect(playerAction(key)).toBe(action));

  it("leaves every other key to the browser", () => {
    for (const key of ["Enter", "Tab", "a", "ArrowUp", "ArrowDown", "Escape", ""]) expect(playerAction(key)).toBeNull();
  });
});

describe("which piece is being spoken", () => {
  const rate = 1000; // a rate that makes a frame a millisecond, so a start is easy to read
  const piece = (idx: number, start: number | null, extra: Partial<PartPiece> = {}): PartPiece => ({
    idx, kind: "paragraph", text: `p${idx}`, continues: false, status: start === null ? "pending" : "done", frames: 1000, start_frame: start, error: null, url: null, ...extra,
  });
  const pieces = [piece(0, 0), piece(1, 2000), piece(2, 5000), piece(3, null), piece(4, null)];

  it("is the last made piece that has started by that time", () => {
    expect(pieceAt(pieces, 0, rate)).toBe(0);
    expect(pieceAt(pieces, 1.999, rate)).toBe(0);
    expect(pieceAt(pieces, 2, rate)).toBe(1); // exactly at a start: that piece
    expect(pieceAt(pieces, 4.9, rate)).toBe(1);
    expect(pieceAt(pieces, 5, rate)).toBe(2);
  });

  it("is the last made piece for a time past it, and never a piece that is not made", () => {
    expect(pieceAt(pieces, 999, rate)).toBe(2);
  });

  it("is nothing when nothing is made, or the rate is not known", () => {
    expect(pieceAt([piece(0, null)], 3, rate)).toBeNull();
    expect(pieceAt([], 3, rate)).toBeNull();
    expect(pieceAt(pieces, 3, 0)).toBeNull();
  });

  it("is the first piece for a time before the first starts", () => {
    expect(pieceAt([piece(0, 500)], 0.1, rate)).toBeNull(); // nothing has started: a part's first piece starts at 0 in practice, and none is claimed otherwise
  });

  it("knows where a piece starts, in seconds, or that it has not", () => {
    expect(pieceStart(pieces[1], rate)).toBe(2);
    expect(pieceStart(pieces[3], rate)).toBeNull();
    expect(pieceStart(pieces[1], 0)).toBeNull();
  });

  it("finds the paragraph before and after, among those that are made", () => {
    expect(neighbourPiece(pieces, 1, 1)).toBe(2);
    expect(neighbourPiece(pieces, 1, -1)).toBe(0);
    expect(neighbourPiece(pieces, 0, -1)).toBeNull();
    expect(neighbourPiece(pieces, 2, 1)).toBeNull(); // 3 is not made: there is nothing to go to yet
    expect(neighbourPiece(pieces, null, 1)).toBe(0);
    expect(neighbourPiece(pieces, null, -1)).toBeNull();
    expect(neighbourPiece(pieces, 4, -1)).toBeNull(); // a piece that is not made is not a place to go back from
    expect(neighbourPiece([piece(0, null)], null, 1)).toBeNull();
  });

  it("says where the person is: the part, the piece that time is in, and the time in the part in whole milliseconds", () => {
    expect(positionFor(3, pieces, 6.2504, rate)).toEqual({ part: 3, piece: 2, offset_ms: 6250 });
    expect(positionFor(3, pieces, 6.2506, rate).offset_ms).toBe(6251); // to the nearest millisecond, not cut off
    expect(positionFor(0, [], 3, rate)).toEqual({ part: 0, piece: 0, offset_ms: 3000 });
    expect(positionFor(0, pieces, -2, rate).offset_ms).toBe(0);
  });

  it("works on the part the server sent: its second piece starts where its first ended", () => {
    const detail = makePartDetail();
    const rateOfPart = detail.sample_rate as number;
    const second = detail.pieces[1];
    expect(pieceStart(second, rateOfPart)).toBeCloseTo((detail.pieces[0].frames as number) / rateOfPart, 6);
    expect(pieceAt(detail.pieces, 0.5, rateOfPart)).toBe(0);
    expect(pieceAt(detail.pieces, (second.start_frame as number) / rateOfPart, rateOfPart)).toBe(1);
  });
});

describe("where to resume", () => {
  it("is the stored time in the part, in seconds", () => {
    expect(resumeTime({ part: 0, piece: 1, offset_ms: 12_500 }, { total_frames: 48000 * 60, sample_rate: 48000 })).toBe(12.5);
  });

  it("is not past what is made", () => {
    expect(resumeTime({ part: 0, piece: 1, offset_ms: 90_000 }, { total_frames: 24000 * 60, sample_rate: 24000 })).toBe(60);
  });

  it("is the stored time when the part's rate is not known yet", () => {
    expect(resumeTime({ part: 0, piece: 0, offset_ms: 4000 }, { total_frames: 0, sample_rate: null })).toBe(4);
  });
});

describe("what the player remembers", () => {
  it("starts with the defaults, which are speed 1, medium text, normal spacing, following along", () => {
    expect(readPlayerPrefs(memoryStore())).toEqual({ speed: 1, textSize: "medium", spaced: false, follow: true });
    expect(DEFAULT_PLAYER_PREFS).toEqual({ speed: 1, textSize: "medium", spaced: false, follow: true });
  });

  it("brings back what was saved", () => {
    const store = memoryStore();
    savePlayerPrefs({ speed: 1.5, textSize: "larger", spaced: true, follow: false }, store);
    expect(readPlayerPrefs(store)).toEqual({ speed: 1.5, textSize: "larger", spaced: true, follow: false });
  });

  it("has four text sizes, growing", () => {
    expect(TEXT_SIZES.map((t) => t.id)).toEqual(["small", "medium", "large", "larger"]);
    const scales = TEXT_SIZES.map((t) => t.scale);
    expect([...scales].sort((a, b) => a - b)).toEqual(scales);
  });

  it("replaces a value that is not an allowed one and keeps the rest", () => {
    const store = memoryStore({ [PLAYER_PREFS_KEY]: JSON.stringify({ speed: 3, textSize: "huge", spaced: "yes", follow: false }) });
    expect(readPlayerPrefs(store)).toEqual({ speed: 1, textSize: "medium", spaced: false, follow: false });
  });

  it.each(["{not json", "null", "5", '"text"', "[]"])("gives the defaults for %s", (text) => {
    expect(readPlayerPrefs(memoryStore({ [PLAYER_PREFS_KEY]: text }))).toEqual(DEFAULT_PLAYER_PREFS);
  });
});

describe("what the page remembers of the choice of voice", () => {
  it("starts with no choice", () => {
    expect(readChoice(memoryStore())).toEqual(NO_CHOICE);
  });

  it("brings back what was saved, field for field", () => {
    const store = memoryStore();
    const choice = { engine: "fake", voice: { kind: "saved" as const, id: "abc" }, style: "calm", language: "German", filter: { gender: "female" as const, english: true } };
    saveChoice(choice, store);
    expect(readChoice(store)).toEqual(choice);
  });

  it("repairs each field that is wrong and keeps the others", () => {
    const wrong = { engine: 5, voice: { kind: "robot", id: "x" }, style: "kept", language: 7, filter: { gender: "other", english: "yes" } };
    expect(readChoice(memoryStore({ [SPEECH_CHOICE_KEY]: JSON.stringify(wrong) }))).toEqual({ ...NO_CHOICE, style: "kept" });
  });

  it("cuts a style that is absurdly long", () => {
    const text = JSON.stringify({ style: "x".repeat(5000) });
    expect(readChoice(memoryStore({ [SPEECH_CHOICE_KEY]: text })).style).toHaveLength(500);
  });

  it.each(["{not json", "null", "5", '"text"'])("gives no choice for %s", (text) => {
    expect(readChoice(memoryStore({ [SPEECH_CHOICE_KEY]: text }))).toEqual(NO_CHOICE);
  });
});

// ------------------------------------------------------------------ the short jobs
describe("samples and designs", () => {
  const job = (changes: Partial<SpeechJob> = {}): SpeechJob => ({ ...(SPEECH_CONTRACT.design_job as unknown as SpeechJob), ...changes });

  it("are settled when they have finished, whichever way", () => {
    for (const status of ["done", "failed", "canceled"]) expect(jobSettled(job({ status }))).toBe(true);
    for (const status of ["queued", "running"]) expect(jobSettled(job({ status }))).toBe(false);
  });

  it("have their candidate clips in order of index, whatever order they were listed in, and not the sample's file", () => {
    const files = [{ key: "c2", index: 2, url: "/c/2" }, { key: "c0", index: 0, url: "/c/0" }, { key: "c1", index: 1, url: "/c/1" }, { key: "sample", url: "/s" }];
    expect(candidateClips(job({ files }))).toEqual([{ index: 0, url: "/c/0" }, { index: 1, url: "/c/1" }, { index: 2, url: "/c/2" }]);
  });
});

// ------------------------------------------------------------------ a canceled reading's card
describe("the note on a canceled reading", () => {
  it("says how many parts are done, as the design does, whatever the other kinds of run say", async () => {
    const { canceledText } = await import("./format");
    const run = makeSpeechRun({ status: "canceled" });
    const reading = run.reading as ReadingInfo;
    expect(canceledText({ ...run, reading: { ...reading, parts_done: 3, parts_total: 12 } })).toBe("Canceled · 3 of 12 parts");
    expect(canceledText({ ...run, reading: null })).toBe("Canceled.");
  });
});

// ------------------------------------------------------------------ the source (§38.2): what is checked before the server is asked
describe("choosing a file", () => {
  const limits = { upload_mb: 50 };
  const MB = 1024 * 1024;

  it("offers the kinds the server reads, and says so in the accept list", () => {
    expect([...FILE_EXTENSIONS]).toEqual([".txt", ".text", ".md", ".markdown", ".pdf", ".epub", ".docx"]);
    expect(FILE_ACCEPT).toBe(".txt,.text,.md,.markdown,.pdf,.epub,.docx");
  });

  it.each(["book.txt", "notes.TEXT", "README.md", "a.markdown", "paper.PDF", "novel.epub", "letter.docx", "my.file.name.txt"])("%s may be sent", (name) => {
    expect(fileProblem({ name, size: 1000 }, limits)).toBeNull();
  });

  it.each(["picture.png", "old.doc", "sheet.xlsx", "noextension", "archive.zip", "page.html", ".txt.exe", ""])("%j is refused, naming what to choose", (name) => {
    expect(fileProblem({ name, size: 1000 }, limits)).toMatch(/is not a kind of file that can be read\. Choose a \.txt, \.md, \.pdf, \.epub or \.docx file\.$/);
  });

  it("names the file in the refusal, or says 'That file' when it has no name", () => {
    expect(fileProblem({ name: "picture.png", size: 5 }, limits)).toMatch(/^picture\.png is not/);
    expect(fileProblem({ name: "", size: 5 }, limits)).toMatch(/^That file is not/);
  });

  it("refuses an empty file, and one over the limit, naming the limit in megabytes as the server counts them", () => {
    expect(fileProblem({ name: "a.txt", size: 0 }, limits)).toBe("a.txt is empty.");
    expect(fileProblem({ name: "a.txt", size: 50 * MB }, limits)).toBeNull(); // exactly the limit is allowed
    expect(fileProblem({ name: "a.txt", size: 50 * MB + 1 }, limits)).toBe("a.txt is 51 MB; a file can be at most 50 MB.");
    expect(fileProblem({ name: "big.epub", size: 83.4 * MB }, limits)).toBe("big.epub is 84 MB; a file can be at most 50 MB.");
  });

  it("judges the extension before the size, so a huge picture is told it is a picture", () => {
    expect(fileProblem({ name: "huge.png", size: 900 * MB }, limits)).toMatch(/not a kind of file/);
  });
});

describe("a web address", () => {
  it("is trimmed, and given https:// when it has no scheme", () => {
    expect(normalizeUrl("  example.org/page  ")).toBe("https://example.org/page");
    expect(normalizeUrl("http://example.org")).toBe("http://example.org");
    expect(normalizeUrl("HTTPS://Example.org")).toBe("HTTPS://Example.org");
    expect(normalizeUrl("   ")).toBe("");
  });

  it.each(["https://example.org/a", "http://example.org", "example.org/page", "https://sub.example.co.uk/a?b=c#d", "https://93.184.216.34/"])("%s is a web address", (text) => {
    expect(urlProblem(text)).toBeNull();
  });

  it("asks for one when there is none", () => {
    expect(urlProblem("")).toBe("Give the web address of the page to read.");
    expect(urlProblem("   ")).toBe("Give the web address of the page to read.");
  });

  it.each(["ftp://example.org/file", "file:///etc/passwd", "javascript:alert(1)", "mailto:a@b.c", "localhost:8000/x"])("%s is not one the page will send", (text) => {
    expect(urlProblem(text)).toBe("A web address starts with http:// or https://.");
  });

  it.each(["https://", "http://:80", "https:// spaces in it"])("%s is not a web address", (text) => {
    expect(urlProblem(text)).toBe("That is not a web address.");
  });
});

describe("the line under pasted text", () => {
  const limits = { chars_per_second: 14 };
  it("counts the characters and says how long they take to speak", () => {
    expect(pastedTextLine("x".repeat(12345), limits)).toBe("12,345 characters, about 15 min to speak");
    expect(pastedTextLine("x", limits)).toBe("1 character, less than a minute to speak"); // (not "about less than a minute")
  });
  it("says nothing for nothing", () => {
    expect(pastedTextLine("", limits)).toBe("");
  });
  it("gives only the count when the speaking rate is not known", () => {
    expect(pastedTextLine("x".repeat(1500), { chars_per_second: 0 })).toBe("1,500 characters");
  });
});

// ------------------------------------------------------------------ the review: what has been changed
describe("whether the review has been changed", () => {
  const prepared = makePrepared();

  it("has not, as it came from the server", () => {
    expect(reviewChanged(draftFromPrepared(prepared), prepared)).toBe(false);
  });

  it("has, once a part is left out, put back, renamed, edited or joined", () => {
    const parts = draftFromPrepared(prepared);
    expect(reviewChanged(setInclude(parts, 0, false), prepared)).toBe(true);
    expect(reviewChanged(renamePart(parts, 0, "Another"), prepared)).toBe(true);
    expect(reviewChanged(editParagraph(parts, 0, 0, "other words"), prepared)).toBe(true);
    const two = makePrepared({ parts: [prepared.parts[0], { ...prepared.parts[0], title: "Second" }] });
    expect(reviewChanged(mergeWithNext(draftFromPrepared(two), 0), two)).toBe(true);
  });

  it("has not, when a part is renamed and then given its own title again", () => {
    const parts = draftFromPrepared(prepared);
    expect(reviewChanged(renamePart(renamePart(parts, 0, "Another"), 0, prepared.parts[0].title), prepared)).toBe(false);
  });

  it("has, if the server's own exclusion is overruled", () => {
    const left = makePrepared({ parts: [{ ...prepared.parts[0], include: false }] });
    expect(reviewChanged(setInclude(draftFromPrepared(left), 0, true), left)).toBe(true);
  });
});

describe("the switches", () => {
  const made = { clean: true, readExtras: false };
  it("are the same as the ones the review was asked for with, or have changed since", () => {
    expect(switchesChanged(made, { clean: true, readExtras: false })).toBe(false);
    expect(switchesChanged(made, { clean: false, readExtras: false })).toBe(true);
    expect(switchesChanged(made, { clean: true, readExtras: true })).toBe(true);
    expect(switchesChanged(made, { clean: false, readExtras: true })).toBe(true);
  });
});

describe("the lines of the review", () => {
  const limits = { chars_per_second: 14 };

  it("says how many parts are included and how long they take", () => {
    const parts = draft(["One", ["x".repeat(14 * 60 * 30)]], ["Two", ["y".repeat(14 * 60 * 50)]], ["Three", ["z".repeat(100)], false]);
    expect(reviewLine(parts, limits)).toBe("2 of 3 parts included · about 1 h 20 min of audio");
    expect(reviewLine(draft(["One", ["x".repeat(14 * 60 * 5)]]), limits)).toBe("1 of 1 part included · about 5 min of audio");
    expect(reviewLine(setInclude(draft(["One", ["x"]]), 0, false), limits)).toBe("0 of 1 part included · less than a minute of audio");
  });

  it("says an estimate as a phrase that reads on its own: 'about 12 min', but 'less than a minute' and never 'about less than a minute'", () => {
    expect(aboutLength(12 * 60)).toBe("about 12 min");
    expect(aboutLength(3600)).toBe("about 1 h");
    expect(aboutLength(20)).toBe("less than a minute");
    expect(aboutLength(0)).toBe("less than a minute");
    expect(totalsLine({ parts: 1, chars: 10, seconds: 5, bytes: 240_000 }, "Piper", "Jenny")).toBe("1 part, less than a minute of audio, about 240 KB. Piper, Jenny.");
    expect(toGoText({ ...(makeSpeechRun().reading as ReadingInfo), seconds_to_go: 20 })).toBe("less than a minute to go");
    expect(partLengthText(draft(["One", ["x".repeat(14)]])[0], { chars_per_second: 14 })).toBe("less than a minute");
  });

  it("says how long one part takes, and nothing without a speaking rate", () => {
    const part = draft(["One", ["x".repeat(14 * 60 * 12)]])[0];
    expect(partLengthText(part, limits)).toBe("about 12 min");
    expect(partLengthText(part, { chars_per_second: 0 })).toBe("");
  });

  it("says why a paragraph is not read", () => {
    expect(skippedText("code")).toBe("Not read: code");
    expect(skippedText("table")).toBe("Not read: table");
    expect(skippedText("footnote")).toBe("Not read: footnote");
  });
});

// ------------------------------------------------------------------ Play sample
describe("asking for a sample", () => {
  it("names a preset by its id, and a voice the person made by its id, never both", () => {
    expect(sampleBody(FULL, ANNA)).toEqual({ engine: "fake", voice: "anna" });
    expect(sampleBody(FULL, saved({ id: "s1" }))).toEqual({ engine: "fake", saved_voice: "s1" });
    expect(Object.keys(sampleBody(FULL, ANNA))).not.toContain("saved_voice");
    expect(Object.keys(sampleBody(FULL, saved()))).not.toContain("voice");
  });

  it("keeps the state of a sample under a key that tells a preset from a saved voice with the same id", () => {
    expect(sampleKey(ANNA)).toBe("preset:anna");
    expect(sampleKey(saved({ id: "anna" }))).toBe("saved:anna");
    expect(sampleKey(ANNA)).not.toBe(sampleKey(saved({ id: "anna" })));
  });

  it("names its button for what pressing it does now", () => {
    expect(sampleLabel("Anna", "idle")).toBe("Play sample of Anna");
    expect(sampleLabel("Anna", "ready")).toBe("Play sample of Anna");
    expect(sampleLabel("Anna", "waiting")).toBe("Making the sample of Anna…");
    expect(sampleLabel("Anna", "playing")).toBe("Stop the sample of Anna");
    expect([sampleWords("idle"), sampleWords("waiting"), sampleWords("playing"), sampleWords("ready")]).toEqual(["Play sample", "Making…", "Stop", "Hear it"]);
  });
});

describe("what is said when a reading is queued", () => {
  it("has its title, its parts and about how long", () => {
    const run = makeSpeechRun();
    expect(queuedText(run)).toBe("Queued “A test reading”: 2 parts, less than a minute.");
    const long = makeSpeechRun({ reading: { ...(run.reading as ReadingInfo), parts_total: 12, seconds_total: 40800 } });
    expect(queuedText(long)).toBe("Queued “A test reading”: 12 parts, about 11 h 20 min.");
    expect(queuedText(makeSpeechRun({ reading: { ...(run.reading as ReadingInfo), parts_total: 1 } }))).toMatch(/: 1 part, /);
  });

  it("falls back to the title in the options, then the prompt, and says only that it is queued for a run with no reading", () => {
    const run = makeSpeechRun();
    expect(queuedText({ ...run, reading: null })).toBe("Queued “A test reading”.");
    expect(queuedText({ ...run, reading: { ...(run.reading as ReadingInfo), title: null } })).toMatch(/^Queued “A test reading”: /);
    expect(queuedText({ ...run, reading: null, options: { ...run.options, title: null }, prompt: "From the prompt" })).toBe("Queued “From the prompt”.");
  });
});


// ------------------------------------------------------------------ what the player plays, and where it goes (§38.7)
describe("what the player plays and where it goes", () => {
  const rate = 1000; // a frame is a millisecond, so a start can be read off
  const piece = (idx: number, start: number | null, frames = 2000): PartPiece => ({
    idx, kind: "paragraph", text: `p${idx}`, continues: false, status: start === null ? "pending" : "done", frames: start === null ? null : frames,
    start_frame: start, error: null, url: start === null ? null : `/p/${idx}.wav`,
  });
  // a part with `made` of five pieces made, two seconds each, and none of it whole
  const partial = (made = 3) => makePartDetail({
    complete: false, url: null, download_url: null, sample_rate: rate, total_frames: made * 2000,
    pieces: [0, 1, 2, 3, 4].map((i) => piece(i, i < made ? i * 2000 : null)),
  });
  const whole = () => makePartDetail({ complete: true, url: "/p/all.wav", sample_rate: rate, total_frames: 6000, pieces: [piece(0, 0), piece(1, 2000), piece(2, 4000)] });

  describe("how long a part is", () => {
    it("is its frames over the engine's rate, and nothing before there is a rate", () => {
      expect(partSeconds({ total_frames: 6000, sample_rate: 1000 })).toBe(6);
      expect(partSeconds({ total_frames: 78624, sample_rate: 24000 })).toBeCloseTo(3.276, 3);
      expect(partSeconds({ total_frames: 0, sample_rate: null })).toBe(0);
      expect(partSeconds({ total_frames: 5000, sample_rate: null })).toBe(0); // (cannot be, but never a division by nothing)
    });
  });

  describe("a piece as it is played", () => {
    it("is its own file and where it starts in the part", () => {
      expect(pieceSource(partial(), 1)).toEqual({ kind: "piece", piece: 1, url: "/p/1.wav", start: 2, at: 0 });
    });
    it("is nothing when it is not made, has no file, or is not there", () => {
      expect(pieceSource(partial(), 3)).toBeNull();
      expect(pieceSource(partial(), 99)).toBeNull();
      const noFile = partial();
      noFile.pieces[1] = { ...noFile.pieces[1], url: null };
      expect(pieceSource(noFile, 1)).toBeNull();
    });
  });

  describe("what to play from a time", () => {
    it("is the whole file for a part that is complete, from that time", () => {
      expect(sourceFor(whole(), 4.5)).toEqual({ kind: "part", url: "/p/all.wav", at: 4.5 });
    });
    it("keeps the time inside the whole part: not before its start, not past its end", () => {
      expect(sourceFor(whole(), -3)).toEqual({ kind: "part", url: "/p/all.wav", at: 0 });
      expect(sourceFor(whole(), 99)).toEqual({ kind: "part", url: "/p/all.wav", at: 6 });
    });
    it("is the piece the time falls in, and how far into it, for a part still being made", () => {
      expect(sourceFor(partial(), 0)).toEqual({ kind: "piece", piece: 0, url: "/p/0.wav", start: 0, at: 0 });
      expect(sourceFor(partial(), 2.5)).toEqual({ kind: "piece", piece: 1, url: "/p/1.wav", start: 2, at: 0.5 });
      expect(sourceFor(partial(), 2)).toEqual({ kind: "piece", piece: 1, url: "/p/1.wav", start: 2, at: 0 }); // exactly at a start: that piece
      expect(sourceFor(partial(), 5.75)).toEqual({ kind: "piece", piece: 2, url: "/p/2.wav", start: 4, at: 1.75 });
    });
    it("is the end of the last piece made when the time is past what is made, and the start when it is before it", () => {
      expect(sourceFor(partial(), 99)).toEqual({ kind: "piece", piece: 2, url: "/p/2.wav", start: 4, at: 2 }); // that piece is two seconds long
      expect(sourceFor(partial(), -4)).toEqual({ kind: "piece", piece: 0, url: "/p/0.wav", start: 0, at: 0 });
    });
    it("is nothing when nothing of the part is made", () => {
      expect(sourceFor(partial(0), 0)).toBeNull();
      expect(sourceFor(makePartDetail({ complete: false, url: null, sample_rate: null, total_frames: 0, pieces: [] }), 3)).toBeNull();
    });
    it("plays the pieces of a part that says it is complete but has no file (never trust one half of two)", () => {
      const odd = { ...partial(3), complete: true, url: null };
      expect(sourceFor(odd, 1)?.kind).toBe("piece");
    });
  });

  describe("the piece after", () => {
    it("is the next made piece, to be loaded while this one plays", () => {
      expect(nextPieceSource(partial(), 0)).toEqual({ kind: "piece", piece: 1, url: "/p/1.wav", start: 2, at: 0 });
      expect(nextPieceSource(partial(), 1)?.piece).toBe(2);
    });
    it("is nothing after the last one made, or after one that is not made", () => {
      expect(nextPieceSource(partial(), 2)).toBeNull();
      expect(nextPieceSource(partial(), 3)).toBeNull();
      expect(nextPieceSource(partial(0), 0)).toBeNull();
    });
  });

  describe("times", () => {
    it("a time in the part is the element's own for the whole part, and the piece's start later for a piece", () => {
      expect(partTime({ kind: "part", url: "/x", at: 0 }, 12.5)).toBe(12.5);
      expect(partTime({ kind: "piece", piece: 2, url: "/x", start: 4, at: 0 }, 1.25)).toBe(5.25);
    });
    it("a time in the whole reading adds what the parts before hold", () => {
      const reading = { parts: [{ seconds: 100 }, { seconds: 250 }, { seconds: 40 }] } as ReadingInfo;
      expect(wholeTime(reading, 0, 7)).toBe(7);
      expect(wholeTime(reading, 1, 7)).toBe(107);
      expect(wholeTime(reading, 2, 7)).toBe(357);
    });
  });

  describe("previous and next paragraph", () => {
    // three paragraphs of ten seconds each: 0, 10 and 20, and a fourth not made
    const long = [piece(0, 0, 10000), piece(1, 10000, 10000), piece(2, 20000, 10000), piece(3, null)];

    it("K goes to the start of the next paragraph that is made", () => {
      expect(nextTime(long, 3, rate)).toBe(10);
      expect(nextTime(long, 10, rate)).toBe(20); // exactly at a start is in that paragraph: the next is the one after
      expect(nextTime(long, 19.9, rate)).toBe(20);
    });
    it("K has nowhere to go from the last paragraph made, or when nothing is made", () => {
      expect(nextTime(long, 25, rate)).toBeNull();
      expect(nextTime([piece(0, null)], 0, rate)).toBeNull();
      expect(nextTime([], 0, rate)).toBeNull();
    });
    it("J goes to the start of the paragraph before, in the first seconds of one", () => {
      expect(previousTime(long, 11, rate)).toBe(0); // 1 s into the second
      expect(previousTime(long, 21, rate)).toBe(10);
      expect(previousTime(long, 10, rate)).toBe(0); // exactly at the start
    });
    it("J starts the paragraph again once it is more than three seconds in, as an audiobook player's previous does", () => {
      expect(RESTART_AFTER).toBe(3);
      expect(previousTime(long, 15, rate)).toBe(10); // 5 s in
      expect(previousTime(long, 13.5, rate)).toBe(10); // 3.5 s in
      expect(previousTime(long, 13, rate)).toBe(0); // exactly three seconds in is still "the first seconds"
    });
    it("J in the first paragraph goes back to its start, and from the very start has nowhere to go", () => {
      expect(previousTime(long, 2, rate)).toBe(0);
      expect(previousTime(long, 7, rate)).toBe(0); // more than three seconds in: its own start, which is the start
      expect(previousTime(long, 0, rate)).toBeNull();
    });
    it("J has nowhere to go when nothing is made", () => {
      expect(previousTime([piece(0, null)], 0, rate)).toBeNull();
      expect(previousTime([], 5, rate)).toBeNull();
    });
  });

  describe("the parts list", () => {
    const part = (state: "done" | "started" | "waiting", made: number, pieces: number, seconds: number) => ({ idx: 0, title: "T", chars: 10, pieces, made, seconds, state });
    it("a part with something made can be played, and one with nothing cannot", () => {
      expect(partPlayable(part("done", 3, 3, 12))).toBe(true);
      expect(partPlayable(part("started", 1, 3, 4))).toBe(true);
      expect(partPlayable(part("waiting", 0, 3, 0))).toBe(false);
    });
    it("says how long a finished part is, how far one that is not has got, and that one not begun is not made", () => {
      expect(partListText(part("done", 3, 3, 3725))).toBe("1:02:05");
      expect(partListText(part("started", 2, 7, 95))).toBe("1:35 made so far · 2 of 7 paragraphs");
      expect(partListText(part("started", 1, 1, 4))).toBe("0:04 made so far · 1 of 1 paragraph");
      expect(partListText(part("waiting", 0, 3, 0))).toBe("Not made yet");
    });
    it("a piece is made when it has a place in the part's audio", () => {
      expect(pieceMade(piece(0, 0))).toBe(true);
      expect(pieceMade(piece(3, null))).toBe(false);
    });
  });
});


// ------------------------------------------------------------------ the person's own voices (§38.4)
describe("a voice of one's own: the checks made before anything is sent, and the words", () => {
  const limits = { recording_mb: 25 };
  const MB = 1024 * 1024;

  describe("a recording before it is sent", () => {
    it.each(RECORDING_EXTENSIONS)("may be a %s file, in capitals too", (ending) => {
      expect(recordingProblem({ name: `me${ending}`, size: 1000 }, limits)).toBeNull();
      expect(recordingProblem({ name: `ME${ending.toUpperCase()}`, size: 1000 }, limits)).toBeNull();
    });

    it("has the kinds the design names, and no others", () => {
      expect([...RECORDING_EXTENSIONS]).toEqual([".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".webm"]);
      expect(RECORDING_ACCEPT).toBe("audio/*,.wav,.mp3,.m4a,.aac,.ogg,.flac,.webm");
    });

    it("is refused, saying what to use, when it is another kind or has no ending", () => {
      const words = "is not a kind of recording that can be read. Use a WAV, MP3, M4A, AAC, OGG, FLAC or WebM recording.";
      expect(recordingProblem({ name: "voice.txt", size: 10 }, limits)).toBe(`voice.txt ${words}`);
      expect(recordingProblem({ name: "voice.mp4", size: 10 }, limits)).toBe(`voice.mp4 ${words}`);
      expect(recordingProblem({ name: "voice", size: 10 }, limits)).toBe(`voice ${words}`);
      expect(recordingProblem({ name: "my.wav.exe", size: 10 }, limits)).toBe(`my.wav.exe ${words}`);
    });

    it("is refused when it is empty", () => {
      expect(recordingProblem({ name: "me.wav", size: 0 }, limits)).toBe("me.wav is empty.");
    });

    it("is refused when it is larger than the server takes, a megabyte being 1,048,576 bytes", () => {
      expect(recordingProblem({ name: "me.wav", size: 25 * MB }, limits)).toBeNull();
      expect(recordingProblem({ name: "me.wav", size: 25 * MB + 1 }, limits)).toBe("me.wav is 26 MB; a recording can be at most 25 MB.");
    });

    it("says 'That file' of one with no name", () => {
      expect(recordingProblem({ name: "", size: 10 }, limits)).toMatch(/^That file is not a kind of recording/);
    });

    it("judges the kind before the size, and the size of an empty file before its length", () => {
      expect(recordingProblem({ name: "big.txt", size: 100 * MB }, limits)).toMatch(/not a kind of recording/);
      expect(recordingProblem({ name: "none.wav", size: 0 }, { recording_mb: 0 })).toBe("none.wav is empty.");
    });
  });

  describe("what is said of a recording that passed", () => {
    it("is how long it is, with the silence taken from its ends when that was half a second or more", () => {
      expect(recordingFacts({ seconds: 10, trimmed_seconds: 0 })).toBe("10 s long");
      expect(recordingFacts({ seconds: 10, trimmed_seconds: 0.49 })).toBe("10 s long");
      expect(recordingFacts({ seconds: 10, trimmed_seconds: 0.5 })).toBe("10 s long, with 0.5 s of silence taken from the ends");
      expect(recordingFacts({ seconds: 24.6, trimmed_seconds: 1.234 })).toBe("25 s long, with 1.2 s of silence taken from the ends");
    });
    it("says a long one in minutes", () => {
      expect(recordingFacts({ seconds: 95, trimmed_seconds: 0 })).toBe("2 min long");
    });
  });

  describe("the name of a voice", () => {
    it("must be there, whatever is around it", () => {
      expect(voiceNameProblem("", 60)).toBe("Give the voice a name.");
      expect(voiceNameProblem("   \t ", 60)).toBe("Give the voice a name.");
      expect(voiceNameProblem("Kirk", 60)).toBeNull();
    });
    it("may be as long as the limit, judged as it will be kept (trimmed), and no longer", () => {
      expect(voiceNameProblem("x".repeat(60), 60)).toBeNull();
      expect(voiceNameProblem(`  ${"x".repeat(60)}  `, 60)).toBeNull();
      expect(voiceNameProblem("x".repeat(61), 60)).toBe("A name can be at most 60 characters; this one is 61.");
    });
  });

  describe("the description of a voice to design", () => {
    it("must be there, and may be as long as the limit and no longer", () => {
      expect(descriptionProblem("  ", 300)).toBe("Describe the voice you would like.");
      expect(descriptionProblem("a warm voice", 300)).toBeNull();
      expect(descriptionProblem("x".repeat(300), 300)).toBeNull();
      expect(descriptionProblem(`${"x".repeat(300)}   `, 300)).toBeNull();
      expect(descriptionProblem("x".repeat(301), 300)).toBe("A description can be at most 300 characters; this one is 301.");
    });
  });

  it("asks before a voice is deleted, and says what is not lost", () => {
    expect(deleteVoiceQuestion("Kirk")).toBe("Delete the voice “Kirk”? Readings already made with it keep their audio and show the voice's name as it was. A voice that is deleted cannot be got back.");
  });
});
