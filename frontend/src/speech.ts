// The Speech tab's logic, with no React in it (DESIGN.md §38.2, §38.7), so that each rule can be tested on its own, as builder.ts and vector.ts do for their tabs.
//
// Four parts: the review of a text before it is read (parts to include, rename, merge and edit, and the request that comes of it), the choice of an engine and a
// voice (what is offered, what is said of each, whether a style or a language applies), the words on a reading's card, and the arithmetic of the player (which
// piece is being spoken, where to resume). Nothing here names an engine: what an engine can do comes from `capabilities.speech`.

import type { KeyValueStore } from "./options";
import { pronunciationLine } from "./pronunciation";
import type {
  EngineState, PartDetail, PartPiece, PositionBody, PresetVoice, Prepared, ReadingBody, ReadingInfo, ReadingParagraphBody, ReadingPart, ReadingPosition, ReadingSpeakerBody, SavedVoice,
  SourceInfo, SpeechCaps, SpeechEngine, SampleBody, SpeechJob, SpeechLimits, StagedRecording,
} from "./speechTypes";
import type { Cast } from "./speakers";
import type { SpeechRun } from "./types";

// ------------------------------------------------------------------ numbers as words
/** "2:14:07" for two hours, fourteen minutes and seven seconds; "14:07" under an hour; "0:05" under a minute. For the player's clock and for where to resume. */
export function clockText(seconds: number): string {
  const whole = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** "11 h 20 min", "45 min", "3 min", "less than a minute": how long a text will be spoken, or how much of it is made. Rounded to the minute, because the figure is an
 *  estimate until the engine has run. */
export function lengthText(seconds: number): string {
  const minutes = Math.round(Math.max(0, seconds) / 60);
  if (minutes < 1) return "less than a minute";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${String(m).padStart(2, "0")} min`;
}

/** "about 20 min", but "less than a minute" and not "about less than a minute": an estimate of a length, said as a phrase that reads on its own. */
export function aboutLength(seconds: number): string {
  const text = lengthText(seconds);
  return text === "less than a minute" ? text : `about ${text}`;
}

/** "35 MB", "2 GB": what a reading will take on disk. One significant place is enough for an estimate; a gigabyte is a thousand megabytes, as on the disk's own
 *  figure in the server's refusal. */
export function audioSizeText(bytes: number): string {
  // the limits are where the rounded figure would reach 1000 of the unit below: "999,999 bytes" is "1 MB" and not "1000 KB"
  if (bytes < 999_500) return `${Math.max(1, Math.round(bytes / 1000))} KB`;
  if (bytes < 999_500_000) return `${Math.round(bytes / 1_000_000)} MB`;
  const gb = bytes / 1_000_000_000;
  return `${gb < 10 ? Number(gb.toFixed(1)) : Math.round(gb)} GB`;
}

const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

// ------------------------------------------------------------------ the review of a text (§38.2, §38.5)
/** The longest a part's title may be: the server's `PartBody.title` limit. */
export const PART_TITLE_MAX = 400;

export interface DraftParagraph {
  kind: "heading" | "paragraph";
  text: string;
  skipped: string | null; // why it will not be read (code, a table), or null; the server decides this, and the switch "read code and tables too" asks it again
  edited: boolean; // the person changed its words here
  speaker: string | null; // the speaker whose line it is (DESIGN.md §44.5); `text` is then the words without the name that began the line
  label: string | null; // the words that began the line (`Alice:`), put back in front of the words if the person turns that name off
}

/** A part as it is being reviewed. `key` is what React keeps it by: it stays with a part when others are merged or renamed. */
export interface DraftPart {
  key: number;
  title: string;
  include: boolean;
  note: string | null;
  paragraphs: DraftParagraph[];
}

/** The review the server sent, made into something that can be changed. */
export function draftFromPrepared(prepared: Prepared): DraftPart[] {
  return prepared.parts.map((part, key) => ({
    key,
    title: part.title,
    include: part.include,
    note: part.note,
    paragraphs: part.paragraphs.map((p) => ({ kind: p.kind, text: p.text, skipped: p.skipped, edited: false, speaker: p.speaker ?? null, label: p.label ?? null })),
  }));
}

/** The characters of a part that will be read: every paragraph that is not skipped, its words as they stand now (spaces between words count once, as the server
 *  counts them). */
export function partChars(part: DraftPart): number {
  return part.paragraphs.reduce((n, p) => (p.skipped === null ? n + collapse(p.text).length : n), 0);
}

const collapse = (text: string): string => text.split(/\s+/).filter(Boolean).join(" ");

/** Why a part cannot be joined to the one after it, or null when it can. Joining an included part to an excluded one would bring words into the reading that the
 *  person had left out, or leave some out that they wanted, so the two must be alike: both included, or both not. */
export function mergeProblem(parts: readonly DraftPart[], index: number): string | null {
  if (index < 0 || index >= parts.length - 1) return "There is no part after this one.";
  if (parts[index].include !== parts[index + 1].include) return "Include both parts, or leave both out, before joining them.";
  return null;
}

/** Part `index` and the one after it as one part: the first one's title, then all the paragraphs of both. Unchanged if they cannot be joined. */
export function mergeWithNext(parts: readonly DraftPart[], index: number): DraftPart[] {
  if (mergeProblem(parts, index) !== null) return [...parts];
  const [first, second] = [parts[index], parts[index + 1]];
  const joined: DraftPart = { ...first, paragraphs: [...first.paragraphs, ...second.paragraphs], note: first.note ?? second.note };
  return [...parts.slice(0, index), joined, ...parts.slice(index + 2)];
}

const changed = (parts: readonly DraftPart[], index: number, change: (part: DraftPart) => DraftPart): DraftPart[] =>
  parts.map((part, i) => (i === index ? change(part) : part));

export const setInclude = (parts: readonly DraftPart[], index: number, include: boolean): DraftPart[] => changed(parts, index, (p) => ({ ...p, include }));

/** A new title, cut at the limit. An empty one is allowed: the server then names the part "Part n". */
export const renamePart = (parts: readonly DraftPart[], index: number, title: string): DraftPart[] =>
  changed(parts, index, (p) => ({ ...p, title: title.slice(0, PART_TITLE_MAX) }));

/** New words for one paragraph. A paragraph emptied out is not read (the server leaves out text with nothing in it). */
export const editParagraph = (parts: readonly DraftPart[], index: number, paragraph: number, text: string): DraftPart[] =>
  changed(parts, index, (p) => ({ ...p, paragraphs: p.paragraphs.map((q, j) => (j === paragraph ? { ...q, text, edited: q.edited || text !== q.text } : q)) }));

/** The parts that will be read: included, and with something in them to say. */
export const partsToRead = (parts: readonly DraftPart[]): DraftPart[] => parts.filter((p) => p.include && partChars(p) > 0);

// ------------------------------------------------------------------ the source (§38.2, §38.5): what is checked before the server is asked
/** The kinds of file the server reads, by their extension (its `FILE_KINDS`): the page offers these and says so when another is chosen. */
export const FILE_EXTENSIONS = [".txt", ".text", ".md", ".markdown", ".pdf", ".epub", ".docx"] as const;
export const FILE_ACCEPT = FILE_EXTENSIONS.join(",");
const FILE_KINDS_WORDS = "Choose a .txt, .md, .pdf, .epub or .docx file.";

/** Why a file cannot be read, in words, or null when it may be sent. These are the cheap checks the server makes first, made here as well so that a book of 50 MB is not
 *  uploaded only to be refused for its name. The limit is megabytes of 1,048,576 bytes, as the server counts them. */
export function fileProblem(file: { name: string; size: number }, limits: Pick<SpeechLimits, "upload_mb">): string | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot === -1 ? "" : file.name.slice(dot).toLowerCase();
  const shown = file.name || "That file";
  if (!(FILE_EXTENSIONS as readonly string[]).includes(extension)) return `${shown} is not a kind of file that can be read. ${FILE_KINDS_WORDS}`;
  if (file.size === 0) return `${shown} is empty.`;
  if (file.size > limits.upload_mb * 1024 * 1024) return `${shown} is ${Math.ceil(file.size / (1024 * 1024))} MB; a file can be at most ${limits.upload_mb} MB.`;
  return null;
}

/** The address as it will be sent: trimmed, and `https://` put in front of one typed without a scheme ("example.org/page"). */
export function normalizeUrl(text: string): string {
  const trimmed = text.trim();
  return trimmed && !/^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? `https://${trimmed}` : trimmed;
}

/** Why an address cannot be fetched, or null. Only the shape is judged here: which addresses are allowed (public sites, ports 80 and 443) is the server's to decide
 *  and it says why in its own words. */
export function urlProblem(text: string): string | null {
  const url = normalizeUrl(text);
  if (!url) return "Give the web address of the page to read.";
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "A web address starts with http:// or https://.";
    return parsed.hostname ? null : "That is not a web address.";
  } catch {
    return "That is not a web address.";
  }
}

/** "12,345 characters, about 14 min to speak": what the box under the pasted text says. */
export function pastedTextLine(text: string, limits: Pick<SpeechLimits, "chars_per_second">): string {
  const chars = text.length;
  if (!chars) return "";
  const about = limits.chars_per_second > 0 ? `, ${aboutLength(chars / limits.chars_per_second)} to speak` : "";
  return `${chars.toLocaleString("en-US")} ${chars === 1 ? "character" : "characters"}${about}`;
}

// ------------------------------------------------------------------ the review: what has been changed
/** Whether the person has changed the review since the server made it: a part left out or put back, renamed, joined or edited. Pressing Prepare again replaces the review,
 *  and this is what decides whether the page asks first. */
export function reviewChanged(parts: readonly DraftPart[], prepared: Prepared): boolean {
  if (parts.length !== prepared.parts.length) return true;
  return parts.some((part, i) => {
    const original = prepared.parts[i];
    return part.title !== original.title || part.include !== original.include || part.paragraphs.some((p) => p.edited) || part.paragraphs.length !== original.paragraphs.length;
  });
}

/** The three switches of a text: clean it up, read code and tables too, and find the speakers (lines that begin with a name and a colon, §44.5). */
export interface Switches {
  clean: boolean;
  readExtras: boolean;
  speakers: boolean;
}

/** Whether the switches now differ from the ones the review was asked for with: the review does not change by itself, and the page says to prepare again. The
 *  page compares with what it asked, not with the `options` the server sends back, so that it does not depend on the server repeating them. */
export const switchesChanged = (made: Switches, now: Switches): boolean => made.clean !== now.clean || made.readExtras !== now.readExtras || made.speakers !== now.speakers;

/** "5 of 6 parts included · about 1 h 20 min of audio": the line above the parts. */
export function reviewLine(parts: readonly DraftPart[], limits: Pick<SpeechLimits, "chars_per_second">): string {
  const totals = readingTotals(parts, limits, 0);
  return `${totals.parts} of ${plural(parts.length, "part")} included · ${aboutLength(totals.seconds)} of audio`;
}

/** How long one part will take to speak, for its row: "about 12 min". */
export const partLengthText = (part: DraftPart, limits: Pick<SpeechLimits, "chars_per_second">): string =>
  limits.chars_per_second > 0 ? aboutLength(partChars(part) / limits.chars_per_second) : "";

/** What a paragraph that will not be read says, from the reason the server gave. */
export function skippedText(reason: string): string {
  switch (reason) {
    case "code":
      return "Not read: code";
    case "table":
      return "Not read: table";
    default:
      return `Not read: ${reason}`;
  }
}

// ------------------------------------------------------------------ what a reading will take (§38.2 "Make reading")
export interface ReadingTotals {
  parts: number;
  chars: number;
  seconds: number; // of audio, estimated from the length of the text
  bytes: number; // on disk: 16-bit mono at the engine's rate
}

/** The size of the reading as the review stands: what the line above Make reading says. The length is the characters over how much a narrator speaks in a second
 *  (the server's own figure, in `limits`); the disk is that many seconds of 16-bit mono at the engine's sample rate. It is an estimate and says "about". */
export function readingTotals(parts: readonly DraftPart[], limits: Pick<SpeechLimits, "chars_per_second">, sampleRate: number): ReadingTotals {
  const read = partsToRead(parts);
  const chars = read.reduce((n, p) => n + partChars(p), 0);
  const seconds = limits.chars_per_second > 0 ? chars / limits.chars_per_second : 0;
  return { parts: read.length, chars, seconds, bytes: Math.round(seconds * sampleRate * 2) };
}

/** "12 parts, about 11 h 20 min of audio, about 2 GB. Kokoro-82M, Heart." */
export function totalsLine(totals: ReadingTotals, engineTitle: string, voiceLabel: string): string {
  const what = `${plural(totals.parts, "part")}, ${aboutLength(totals.seconds)} of audio, about ${audioSizeText(totals.bytes)}.`;
  return voiceLabel ? `${what} ${engineTitle}, ${voiceLabel}.` : `${what} ${engineTitle}.`;
}

// ------------------------------------------------------------------ engines (§38.3)
/** What an engine's state is called on its pill, and how it is drawn. */
export const ENGINE_STATES: Record<EngineState, { label: string; tone: "good" | "warn" | "bad" | "wait" }> = {
  ready: { label: "Ready", tone: "good" },
  checking: { label: "Checking…", tone: "wait" },
  needs_memory: { label: "Needs more memory", tone: "warn" },
  not_installed: { label: "Not installed", tone: "bad" },
  error: { label: "Error", tone: "bad" },
};

/** Whether a reading can be asked for with this engine: the server accepts an engine that is ready, and one short of memory (the worker makes room for it). */
export const engineUsable = (engine: SpeechEngine): boolean => engine.state === "ready" || engine.state === "needs_memory";

/** The licence badge's words: the licence's name, and that it is for non-commercial use where the engine says so. */
export function licenceBadge(engine: SpeechEngine): string {
  return engine.licence.noncommercial ? `${engine.licence.name} · non-commercial use only` : engine.licence.name;
}

/** The engine to start from: the one remembered, if it is there and usable, else the first that is usable, else the first there is (so its reason can be shown). */
export function chooseEngine(caps: SpeechCaps, remembered: string | null): SpeechEngine | null {
  const engines = caps.engines;
  return engines.find((e) => e.id === remembered && engineUsable(e)) ?? engines.find(engineUsable) ?? engines.find((e) => e.id === remembered) ?? engines[0] ?? null;
}

// ------------------------------------------------------------------ voices (§38.4)
export type GenderFilter = "any" | "female" | "male";

/** The chips above the list: Female or Male (or neither chosen), and English. */
export interface VoiceFilter {
  gender: GenderFilter;
  english: boolean;
}

export const NO_VOICE_FILTER: VoiceFilter = { gender: "any", english: false };

/** Whether a preset passes the chips. A voice whose source states no gender or no language does not pass a chip that asks for one: the list must not claim what the
 *  source did not (criterion 261). */
export function voiceMatches(voice: PresetVoice, filter: VoiceFilter): boolean {
  if (filter.gender !== "any" && voice.gender !== filter.gender) return false;
  if (filter.english && voice.language !== "English") return false;
  return true;
}

export const filterVoices = (voices: readonly PresetVoice[], filter: VoiceFilter): PresetVoice[] => voices.filter((v) => voiceMatches(v, filter));

/** What the source says of a voice, as one line: "Female · English · American". What it does not say is said so ("accent not stated"), never guessed from a name
 *  (criterion 261). A voice that states no gender or language says that it does not, in the same way. */
export function voiceFacts(voice: Pick<PresetVoice, "gender" | "language" | "accent">): string {
  const gender = voice.gender === "female" ? "Female" : voice.gender === "male" ? "Male" : "Gender not stated";
  return [gender, voice.language ?? "Language not stated", voice.accent ?? "Accent not stated"].join(" · ");
}

/** What is said of a voice the person made. */
export function savedVoiceFacts(voice: Pick<SavedVoice, "kind" | "seconds">): string {
  const how = voice.kind === "designed" ? "Designed from a description" : "Cloned from a recording";
  return voice.seconds ? `${how} · ${lengthSeconds(voice.seconds)}` : how;
}

const lengthSeconds = (seconds: number): string => (seconds < 60 ? `${Math.round(seconds)} s` : lengthText(seconds));

/** The voice that was chosen, as the list holds it: a preset by its id, or a voice made by the person by its id. */
export type VoicePick = { kind: "preset"; id: string } | { kind: "saved"; id: string };

export function findVoice(pick: VoicePick | null, presets: readonly PresetVoice[], saved: readonly SavedVoice[]): PresetVoice | SavedVoice | null {
  if (!pick) return null;
  return (pick.kind === "preset" ? presets.find((v) => v.id === pick.id) : saved.find((v) => v.id === pick.id)) ?? null;
}

export const isSaved = (voice: PresetVoice | SavedVoice): voice is SavedVoice => "kind" in voice;
export const voiceName = (voice: PresetVoice | SavedVoice): string => (isSaved(voice) ? voice.name : voice.label);

/** The voice to start from when the remembered one is not there (a voice was deleted, or another engine is chosen): the first of the person's own voices, else the
 *  first preset that passes the filter, else the first preset. The person's own come first, as in the list. */
export function firstVoice(presets: readonly PresetVoice[], saved: readonly SavedVoice[], filter: VoiceFilter): VoicePick | null {
  if (saved.length) return { kind: "saved", id: saved[0].id };
  const preset = filterVoices(presets, filter)[0] ?? presets[0];
  return preset ? { kind: "preset", id: preset.id } : null;
}

// ------------------------------------------------------------------ style and language (§38.2, criterion 264)
export const STYLE_EXAMPLES = [
  "calm and warm, like an audiobook narrator",
  "slow and gentle, like a bedtime story",
  "bright and cheerful",
  "serious and clear, like a news reader",
];

export const STYLE_LABEL = "How should it sound?";

/** What to show where the style belongs: the box, with examples, for a voice that takes a style; a line saying why not for one that does not (it does not just
 *  disappear). */
export type StyleControl = { kind: "box"; label: string; examples: string[] } | { kind: "line"; text: string };

export function styleControl(engine: SpeechEngine, voice: PresetVoice | SavedVoice | null): StyleControl {
  if (!engine.can_style) return { kind: "line", text: `${engine.title} does not take a style note.` };
  if (voice && isSaved(voice)) return { kind: "line", text: "A voice you made speaks as it was recorded or described, so it takes no style note." };
  if (voice && !voice.styleable) return { kind: "line", text: "This voice does not take a style note." };
  return { kind: "box", label: STYLE_LABEL, examples: STYLE_EXAMPLES };
}

/** The language list, or null where the engine takes none (the voice decides): Auto first, then the engine's own languages. */
export const languageChoices = (engine: SpeechEngine): string[] | null => (engine.languages.length ? ["Auto", ...engine.languages] : null);

/** The language to send: null for Auto, for none chosen, and for an engine that takes none or does not speak the one named. */
export function languageToSend(engine: SpeechEngine, language: string): string | null {
  return engine.languages.includes(language) ? language : null;
}

/** The style to send: the note, trimmed, only for a preset voice of an engine that takes a style and a voice that does (the server refuses it otherwise, and it
 *  would mean nothing); else null. */
export function styleToSend(engine: SpeechEngine, voice: PresetVoice | SavedVoice | null, style: string): string | null {
  const control = styleControl(engine, voice);
  const note = style.trim();
  return control.kind === "box" && note ? note : null;
}

// ------------------------------------------------------------------ making a reading
/** Why Make reading cannot be pressed yet, in words, or null when it can. The first problem found. */
export function readingProblem(parts: readonly DraftPart[], engine: SpeechEngine | null, voice: PresetVoice | SavedVoice | null): string | null {
  if (!engine) return "There is no speech engine to read with.";
  if (!engineUsable(engine)) return engine.state === "checking" ? `${engine.title} is still being checked.` : engine.reason ?? `${engine.title} cannot run here.`;
  if (!voice) return "Choose a voice.";
  if (!partsToRead(parts).length) return parts.length ? "Include at least one part that has some words in it." : "There is no text to read yet.";
  return null;
}

/** What a paragraph is sent as (§44.5). A line of a speaker who is on goes with the name and without its label. A line of a name that was turned off goes as it was written: the
 *  label is put back in front of the words and no speaker is named, so the main voice reads it, name and all. */
export function paragraphToSend(paragraph: { kind: "heading" | "paragraph"; text: string; speaker: string | null; label: string | null }, cast: Cast): ReadingParagraphBody {
  if (!paragraph.speaker) return { kind: paragraph.kind, text: paragraph.text };
  if (cast[paragraph.speaker]?.on ?? true) return { kind: paragraph.kind, text: paragraph.text, speaker: paragraph.speaker };
  return { kind: paragraph.kind, text: `${paragraph.label ?? `${paragraph.speaker}:`} ${paragraph.text}` };
}

/** The request for a reading (`POST /api/readings`): the parts that will be read, their paragraphs that will be spoken, the engine and the voice. The title is the
 *  one the person left in the box, else the one the review gave. `speakers` (already chosen by `speakersToSend`) and the `cast` they came from are what makes it a reading in
 *  several voices; with neither, it is the reading it was before release 1.25. */
export function buildReadingBody(
  title: string, source: SourceInfo | Record<string, unknown>, parts: readonly DraftPart[], engine: SpeechEngine, voice: PresetVoice | SavedVoice, style: string,
  language: string, speakers: ReadingSpeakerBody[] = [], cast: Cast = {},
): ReadingBody {
  const saved = isSaved(voice);
  const shownTitle = collapse(title) || "Reading";
  return {
    title: shownTitle.slice(0, PART_TITLE_MAX),
    source: { ...source },
    engine: engine.id,
    voice: saved ? "" : voice.id,
    saved_voice: saved ? voice.id : null,
    style: styleToSend(engine, voice, style),
    language: languageToSend(engine, language),
    parts: partsToRead(parts).map((part) => ({
      title: collapse(part.title),
      paragraphs: part.paragraphs.filter((p) => p.skipped === null && collapse(p.text)).map((p) => paragraphToSend({ ...p, text: collapse(p.text) }, cast)),
    })),
    speakers,
  };
}

// ------------------------------------------------------------------ Play sample (§38.4, criterion 262)
/** Where a voice's sample button is: nothing happening, waiting for the sample to be made (a short job that goes ahead of waiting readings), playing it, or ready to be
 *  played by a press (the browser would not let it start by itself). A sample that could not be made or played is `idle` again, with its reason beside the voice. */
export type SamplePhase = "idle" | "waiting" | "playing" | "ready";

/** What "Play sample" asks for: a preset by its id, a voice the person made by its id, never both. */
export function sampleBody(engine: Pick<SpeechEngine, "id">, voice: PresetVoice | SavedVoice): SampleBody {
  return isSaved(voice) ? { engine: engine.id, saved_voice: voice.id } : { engine: engine.id, voice: voice.id };
}

/** The key a voice's sample state is kept under: a preset and a saved voice may have the same id, so the kind is part of it. */
export const sampleKey = (voice: PresetVoice | SavedVoice): string => `${isSaved(voice) ? "saved" : "preset"}:${voice.id}`;

/** The name of the sample button, which says what pressing it does now. */
export function sampleLabel(name: string, phase: SamplePhase): string {
  switch (phase) {
    case "waiting":
      return `Making the sample of ${name}…`;
    case "playing":
      return `Stop the sample of ${name}`;
    default:
      return `Play sample of ${name}`;
  }
}

/** The words on the button itself. */
export const sampleWords = (phase: SamplePhase): string => (phase === "waiting" ? "Making…" : phase === "playing" ? "Stop" : phase === "ready" ? "Hear it" : "Play sample");

// ------------------------------------------------------------------ a reading's card (§38.2)
/** Where the text came from, in a few words: "Pasted", a host name, a file name. */
export function sourceText(source: SourceInfo | Record<string, unknown> | null | undefined): string {
  const s = (source ?? {}) as { kind?: string; host?: string; name?: string };
  if (s.kind === "url") return s.host || "A web page";
  if (s.kind === "file") return s.name || "A file";
  return "Pasted";
}

/** The first part that is not finished (0-based), or the last part when all are. What "Part 5 of 12" counts. */
const currentPart = (reading: ReadingInfo): number => {
  const open = reading.parts.findIndex((p) => p.state !== "done");
  return open === -1 ? Math.max(0, reading.parts.length - 1) : open;
};

/** The bar on a card: "Part 5 of 12 · paragraph 41 of 96 · 3 h 02 min made". When nothing is made yet it says that. */
export function progressLine(reading: ReadingInfo): string {
  if (reading.pieces_done === 0) return `${plural(reading.parts_total, "part")} · ${plural(reading.pieces_total, "paragraph")} · none made yet`;
  const where = reading.parts_done === reading.parts_total ? `All ${plural(reading.parts_total, "part")}` : `Part ${currentPart(reading) + 1} of ${reading.parts_total}`;
  return `${where} · paragraph ${reading.pieces_done} of ${reading.pieces_total} · ${lengthText(reading.seconds_made)} made`;
}

/** What a canceled reading says (criterion 279): "Canceled · 3 of 12 parts". What was made is kept, and Continue makes the rest. */
export const canceledLine = (reading: ReadingInfo): string => `Canceled · ${reading.parts_done} of ${plural(reading.parts_total, "part")}`;

/** How long is left, when the server can say: "about 14 min to go". */
export function toGoText(reading: ReadingInfo): string | null {
  return reading.seconds_to_go ? `${aboutLength(reading.seconds_to_go)} to go` : null;
}

/** Whether there is something to play: at least one part is finished, or a piece of an unfinished one is (a piece plays as soon as it exists, §38.7). */
export const canListen = (reading: ReadingInfo | null): boolean => !!reading && reading.pieces_done > 0;

/** True when the person had got somewhere in the reading: past the first moment of it. */
const hasPlace = (position: ReadingPosition | null): position is ReadingPosition => !!position && (position.part > 0 || position.offset_ms >= 1000);

/** "Listen", or "Continue listening" when they stopped somewhere. */
export const listenLabel = (reading: ReadingInfo | null): string => (reading && hasPlace(reading.position) ? "Continue listening" : "Listen");

/** "Continue listening from 2:14:07 in part 5", or null when there is no place to continue from. */
export function continueText(reading: ReadingInfo | null): string | null {
  if (!reading || !hasPlace(reading.position)) return null;
  return `Continue listening from ${clockText(reading.position.offset_ms / 1000)} in part ${reading.position.part + 1}`;
}

/** What is said when a reading has been queued: its title, how many parts, and about how long. The card for it is in the history. */
export function queuedText(run: SpeechRun): string {
  const r = run.reading;
  const title = r?.title || run.options.title || run.prompt;
  if (!r) return `Queued “${title}”.`;
  const said = pronunciationLine(r.pronunciations); // how much of the pronunciation list the reading took with it (DESIGN.md §44.3)
  return `Queued “${title}”: ${plural(r.parts_total, "part")}, ${aboutLength(r.seconds_total)}.${said ? ` ${said}` : ""}`;
}

/** The facts under a card's title: where the text came from, the engine and the voice. */
export function readingFacts(run: SpeechRun): string {
  const r = run.reading;
  const engine = r?.engine_title ?? run.options.engine_title ?? run.options.engine;
  return [sourceText(r?.source ?? run.options.source), engine, run.options.voice.label].join(" · ");
}

/** A reading's title for a card and a heading. */
export const readingTitle = (run: SpeechRun): string => run.reading?.title || run.options.title || run.prompt;

// ------------------------------------------------------------------ the player (§38.7)
export const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2] as const;
export const SKIP_SECONDS = 15; // the buttons back and forward
export const ARROW_SECONDS = 10; // the arrow keys

/** The speed one step up or down the list from `current`, staying at the ends. A speed that is not in the list (a stored one gone stale) is taken as the nearest. */
export function stepSpeed(current: number, direction: 1 | -1): number {
  const nearest = SPEEDS.reduce((best, s, i) => (Math.abs(s - current) < Math.abs(SPEEDS[best] - current) ? i : best), 0);
  return SPEEDS[Math.min(SPEEDS.length - 1, Math.max(0, nearest + direction))];
}

export const speedText = (speed: number): string => `${speed}×`;

/** A time moved by `delta` seconds, kept inside the part: not before the start, not past the end. */
export const seekedTime = (time: number, delta: number, total: number): number => Math.min(Math.max(0, total), Math.max(0, time + delta));

export type PlayerAction = "toggle" | "previous" | "next" | "back" | "forward";

/** What a key does when the player has focus: Space plays and pauses, J and K go to the previous and next paragraph (J is the key on the left), the arrow keys move
 *  by ten seconds. Null for any other key, which the browser handles as it always does. */
export function playerAction(key: string): PlayerAction | null {
  switch (key) {
    case " ":
    case "Spacebar":
      return "toggle";
    case "j":
    case "J":
      return "previous";
    case "k":
    case "K":
      return "next";
    case "ArrowLeft":
      return "back";
    case "ArrowRight":
      return "forward";
    default:
      return null;
  }
}

/** Where a piece starts in its part, in seconds; null for a piece that is not made yet. */
export const pieceStart = (piece: PartPiece, sampleRate: number): number | null => (piece.start_frame === null || !sampleRate ? null : piece.start_frame / sampleRate);

/** The piece being spoken at `time` seconds into the part: the last made piece that has started by then. The pauses are inside the pieces, so a piece runs to the
 *  start of the next. Null when nothing is made. Pieces are made in order, so those that are made are a run from the start. */
export function pieceAt(pieces: readonly PartPiece[], time: number, sampleRate: number): number | null {
  let found: number | null = null;
  for (const piece of pieces) {
    const start = pieceStart(piece, sampleRate);
    if (start === null) break;
    if (start <= time + 1e-6) found = piece.idx;
    else break;
  }
  return found;
}

/** The made piece one step from `current` (J and K): the one before or the one after. Null at the ends, and past the last made piece. */
export function neighbourPiece(pieces: readonly PartPiece[], current: number | null, step: 1 | -1): number | null {
  const made = pieces.filter((p) => p.start_frame !== null).map((p) => p.idx);
  if (!made.length) return null;
  if (current === null) return step === 1 ? made[0] : null;
  const at = made.indexOf(current);
  if (at === -1) return null;
  return made[at + step] ?? null;
}

/** What to tell the server of where the person is: the part, the piece that time falls in, and the time from the start of the part, in whole milliseconds. */
export function positionFor(partIndex: number, pieces: readonly PartPiece[], time: number, sampleRate: number): PositionBody {
  return { part: partIndex, piece: pieceAt(pieces, time, sampleRate) ?? 0, offset_ms: Math.max(0, Math.round(time * 1000)) };
}

/** The time in the part to resume at, in seconds: the stored one, not past what is made (a part that was shorter then than it is now is no problem; one that is
 *  shorter now cannot happen, pieces are not taken away). */
export function resumeTime(position: ReadingPosition, detail: Pick<PartDetail, "total_frames" | "sample_rate">): number {
  const total = detail.sample_rate ? detail.total_frames / detail.sample_rate : Infinity;
  return Math.min(position.offset_ms / 1000, total);
}

/** The part a reading opens at: the one the person stopped in if there is one and it is there, else the first. */
export const startingPart = (reading: ReadingInfo): number =>
  reading.position && reading.position.part < reading.parts.length ? reading.position.part : 0;

// ------------------------------------------------------------------ what the player plays, and where it goes (§38.7)
/** What the player puts in an audio element to play a part from a given time. A finished part is one file (§38.7), so seeking in it is the browser's. A part still being
 *  made has only its pieces, each its own file: the player plays the one the time falls in. `start` is where that piece starts in the part, `at` is the seconds into the
 *  piece (or, for the whole part, into the part) to play from. */
export type PlaySource =
  | { kind: "part"; url: string; at: number }
  | { kind: "piece"; piece: number; url: string; start: number; at: number };
export type PieceSource = Extract<PlaySource, { kind: "piece" }>;

/** The seconds of audio a part has so far: all of it once the part is complete. 0 before anything is made (the engine's rate is not known yet). */
export const partSeconds = (detail: Pick<PartDetail, "total_frames" | "sample_rate">): number => (detail.sample_rate ? detail.total_frames / detail.sample_rate : 0);

/** A made piece as the player plays it: its own file and where it starts in the part, from its beginning. Null for a piece that is not made, or that does not exist. */
export function pieceSource(detail: PartDetail, idx: number): PieceSource | null {
  const piece = detail.pieces.find((p) => p.idx === idx);
  const start = piece ? pieceStart(piece, detail.sample_rate ?? 0) : null;
  return piece && start !== null && piece.url ? { kind: "piece", piece: idx, url: piece.url, start, at: 0 } : null;
}

/** What to play for the part from `time` seconds into it: the whole file once the part is complete, else the piece the time falls in. A time past what is made is the end of
 *  the last piece that is made (a click beyond what exists plays the end, not nothing), and one before the start is the start. Null when nothing in the part is made. */
export function sourceFor(detail: PartDetail, time: number): PlaySource | null {
  const t = Math.max(0, time);
  if (detail.complete && detail.url) return { kind: "part", url: detail.url, at: Math.min(t, partSeconds(detail)) };
  const rate = detail.sample_rate ?? 0;
  const idx = pieceAt(detail.pieces, t, rate);
  const found = idx === null ? null : pieceSource(detail, idx);
  if (!found) return null;
  const length = (detail.pieces.find((p) => p.idx === idx)?.frames ?? 0) / rate;
  return { ...found, at: Math.min(t - found.start, length) };
}

/** The made piece after `idx`, as the player would play it next (to be loaded while this one plays). Null when there is none made yet. */
export function nextPieceSource(detail: PartDetail, idx: number): PieceSource | null {
  const next = neighbourPiece(detail.pieces, idx, 1);
  return next === null ? null : pieceSource(detail, next);
}

/** Seconds into the part for a place `elementTime` seconds into what the audio element plays: the same for the whole part, `start` later for a piece. */
export const partTime = (source: PlaySource, elementTime: number): number => (source.kind === "part" ? elementTime : source.start + elementTime);

/** Seconds into the whole reading: what the parts before this one hold, and `time` into this one. */
export const wholeTime = (reading: Pick<ReadingInfo, "parts">, part: number, time: number): number =>
  reading.parts.slice(0, part).reduce((sum, p) => sum + p.seconds, 0) + time;

/** How far into a paragraph it is when "previous paragraph" starts that paragraph again and not the one before it, as an audiobook player's "previous" does. */
export const RESTART_AFTER = 3;

/** Where J goes, in seconds into the part: the start of the paragraph being spoken once it is more than a few seconds in, else the start of the one before. Null at the
 *  very start of the part (the view then goes to the part before), and when nothing is made. */
export function previousTime(pieces: readonly PartPiece[], time: number, sampleRate: number): number | null {
  const current = pieceAt(pieces, time, sampleRate);
  const here = pieces.find((p) => p.idx === current);
  const start = here ? pieceStart(here, sampleRate) : null;
  if (current === null || start === null) return null;
  if (time - start > RESTART_AFTER) return start;
  const before = neighbourPiece(pieces, current, -1);
  const earlier = pieces.find((p) => p.idx === before);
  if (earlier) return pieceStart(earlier, sampleRate);
  return time > start + 1e-6 ? start : null;
}

/** Where K goes, in seconds into the part: the start of the next paragraph that is made. Null at the end of what is made (the view then goes to the next part). */
export function nextTime(pieces: readonly PartPiece[], time: number, sampleRate: number): number | null {
  const after = neighbourPiece(pieces, pieceAt(pieces, time, sampleRate), 1);
  const piece = pieces.find((p) => p.idx === after);
  return piece ? pieceStart(piece, sampleRate) : null;
}

/** Whether a part can be played at all: something of it is made. */
export const partPlayable = (part: ReadingPart): boolean => part.made > 0;

/** What the parts list says under a part's title: how long it is, and how far it has got if it is not all made. */
export function partListText(part: ReadingPart): string {
  if (part.state === "done") return clockText(part.seconds);
  if (part.made === 0) return "Not made yet";
  return `${clockText(part.seconds)} made so far · ${part.made} of ${plural(part.pieces, "paragraph")}`;
}

/** Whether a piece is made, so that it can be heard and sought to. */
export const pieceMade = (piece: PartPiece): boolean => piece.start_frame !== null;

/** The text sizes of the reading view, smallest first: the four the design asks for, as a multiple of the page's own text size. */
export const TEXT_SIZES = [
  { id: "small", label: "Small", scale: 1 },
  { id: "medium", label: "Medium", scale: 1.25 },
  { id: "large", label: "Large", scale: 1.6 },
  { id: "larger", label: "Extra large", scale: 2 },
] as const;
export type TextSizeId = (typeof TEXT_SIZES)[number]["id"];

/** What the player remembers in this browser: its speed and how the text is shown. */
export interface PlayerPrefs {
  speed: number;
  textSize: TextSizeId;
  spaced: boolean; // wider line spacing
  follow: boolean; // keep the spoken paragraph in view
}

export const DEFAULT_PLAYER_PREFS: PlayerPrefs = { speed: 1, textSize: "medium", spaced: false, follow: true };
export const PLAYER_PREFS_KEY = "studio.speech.player.v1";

/** The stored preferences, each one checked: a value that is not one of the allowed ones (stored by another version, or by hand) is replaced by the default, and
 *  the rest are kept. Anything that is not an object gives the defaults. */
export function readPlayerPrefs(store: KeyValueStore): PlayerPrefs {
  try {
    const raw = JSON.parse(store.get(PLAYER_PREFS_KEY) ?? "null") as Partial<Record<keyof PlayerPrefs, unknown>> | null;
    if (!raw || typeof raw !== "object") return { ...DEFAULT_PLAYER_PREFS };
    const speed = SPEEDS.find((s) => s === raw.speed);
    const size = TEXT_SIZES.find((t) => t.id === raw.textSize);
    return {
      speed: speed ?? DEFAULT_PLAYER_PREFS.speed,
      textSize: size?.id ?? DEFAULT_PLAYER_PREFS.textSize,
      spaced: typeof raw.spaced === "boolean" ? raw.spaced : DEFAULT_PLAYER_PREFS.spaced,
      follow: typeof raw.follow === "boolean" ? raw.follow : DEFAULT_PLAYER_PREFS.follow,
    };
  } catch {
    return { ...DEFAULT_PLAYER_PREFS };
  }
}

export function savePlayerPrefs(prefs: PlayerPrefs, store: KeyValueStore): void {
  store.set(PLAYER_PREFS_KEY, JSON.stringify(prefs));
}

// ------------------------------------------------------------------ what the page remembers of its choices (§38.2: "the last choice is remembered")
export const SPEECH_CHOICE_KEY = "studio.speech.v1";

export interface SpeechChoice {
  engine: string | null;
  voice: VoicePick | null;
  style: string;
  language: string;
  filter: VoiceFilter;
}

export const NO_CHOICE: SpeechChoice = { engine: null, voice: null, style: "", language: "Auto", filter: NO_VOICE_FILTER };

/** The remembered choice, made safe: every field is checked and the ones that are not as they should be fall back, so a hand-edited or old value cannot break the
 *  page. Whether the engine and the voice still exist is judged later, against the capabilities and the engine's voices. */
export function readChoice(store: KeyValueStore): SpeechChoice {
  try {
    const raw = JSON.parse(store.get(SPEECH_CHOICE_KEY) ?? "null") as Record<string, unknown> | null;
    if (!raw || typeof raw !== "object") return { ...NO_CHOICE };
    const voice = raw.voice as { kind?: unknown; id?: unknown } | null | undefined;
    const filter = (raw.filter ?? {}) as { gender?: unknown; english?: unknown };
    return {
      engine: typeof raw.engine === "string" ? raw.engine : null,
      voice: voice && (voice.kind === "preset" || voice.kind === "saved") && typeof voice.id === "string" ? { kind: voice.kind, id: voice.id } : null,
      style: typeof raw.style === "string" ? raw.style.slice(0, 500) : "",
      language: typeof raw.language === "string" ? raw.language : "Auto",
      filter: {
        gender: filter.gender === "female" || filter.gender === "male" ? filter.gender : "any",
        english: filter.english === true,
      },
    };
  } catch {
    return { ...NO_CHOICE };
  }
}

export function saveChoice(choice: SpeechChoice, store: KeyValueStore): void {
  store.set(SPEECH_CHOICE_KEY, JSON.stringify(choice));
}

// ------------------------------------------------------------------ short jobs: samples and designs (§38.4)
/** Whether a sample or a design has finished one way or another. */
export const jobSettled = (job: Pick<SpeechJob, "status">): boolean => job.status === "done" || job.status === "failed" || job.status === "canceled";

/** The three clips of a finished design, in order (their index in the files, not in the order the server listed them). */
export function candidateClips(job: SpeechJob): { index: number; url: string }[] {
  return job.files.filter((f) => typeof f.index === "number").map((f) => ({ index: f.index as number, url: f.url })).sort((a, b) => a.index - b.index);
}

// ------------------------------------------------------------------ the person's own voices: designing one, adding one by a recording (§38.4)
/** The kinds of recording the studio reads (§38.4): what a phone or a computer makes, by the ending of the file's name. The server decodes what it is given with ffmpeg and
 *  decides; this is the cheap check made first so that a file of another kind is not uploaded only to be refused. */
export const RECORDING_EXTENSIONS = [".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".webm"] as const;
/** For the file box's `accept`: any audio, and the endings above for a browser that does not say a file is audio. */
export const RECORDING_ACCEPT = ["audio/*", ...RECORDING_EXTENSIONS].join(",");
const RECORDING_KINDS_WORDS = "Use a WAV, MP3, M4A, AAC, OGG, FLAC or WebM recording.";

/** Why a recording cannot be sent, in words, or null when it may be. The size limit is megabytes of 1,048,576 bytes, as the server counts them. Whether it is long enough,
 *  not silent and not clipped is the server's to say, and is shown as it says it. */
export function recordingProblem(file: { name: string; size: number }, limits: Pick<SpeechLimits, "recording_mb">): string | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot === -1 ? "" : file.name.slice(dot).toLowerCase();
  const shown = file.name || "That file";
  if (!(RECORDING_EXTENSIONS as readonly string[]).includes(extension)) return `${shown} is not a kind of recording that can be read. ${RECORDING_KINDS_WORDS}`;
  if (file.size === 0) return `${shown} is empty.`;
  if (file.size > limits.recording_mb * 1024 * 1024) return `${shown} is ${Math.ceil(file.size / (1024 * 1024))} MB; a recording can be at most ${limits.recording_mb} MB.`;
  return null;
}

/** What is said of a recording that passed the server's checks: how long it is, and the silence taken from its ends if that was worth saying (half a second or more). */
export function recordingFacts(recording: Pick<StagedRecording, "seconds" | "trimmed_seconds">): string {
  const length = `${lengthSeconds(recording.seconds)} long`;
  return recording.trimmed_seconds >= 0.5 ? `${length}, with ${recording.trimmed_seconds.toFixed(1)} s of silence taken from the ends` : length;
}

/** Why a name cannot be used for a voice, or null. The name is judged as it will be kept: trimmed. */
export function voiceNameProblem(name: string, max: number): string | null {
  const trimmed = name.trim();
  if (!trimmed) return "Give the voice a name.";
  if (trimmed.length > max) return `A name can be at most ${max} characters; this one is ${trimmed.length}.`;
  return null;
}

/** Why a description cannot be sent to make a voice from, or null. */
export function descriptionProblem(description: string, max: number): string | null {
  const trimmed = description.trim();
  if (!trimmed) return "Describe the voice you would like.";
  if (trimmed.length > max) return `A description can be at most ${max} characters; this one is ${trimmed.length}.`;
  return null;
}

/** The question before a voice is deleted: what is lost, and what is not. */
export const deleteVoiceQuestion = (name: string): string =>
  `Delete the voice “${name}”? Readings already made with it keep their audio and show the voice's name as it was. A voice that is deleted cannot be got back.`;
