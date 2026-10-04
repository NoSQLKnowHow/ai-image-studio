// The Music tab's logic (DESIGN.md §26): the fields that build the description, the form and its saved copy, the request,
// and the words on a track card. Pure functions, so every rule is tested without a browser.

import type { KeyValueStore } from "./options";
import type { Capabilities, CreateMusicBody, MusicRun, Progress, Range, WorkerStatus } from "./types";

export const MUSIC_FORM_KEY = "studio.music.v1";
export const TAB_KEY = "studio.tab.v1";
/** The model's name, which its licence asks to be shown wherever it is used (DESIGN.md §26.8). */
export const MUSIC_MODEL_NAME = "MiniMax-Music3";
export const MUSIC_MODEL_URL = "https://huggingface.co/MiniMaxAI/MiniMax-Music3";

export const INSTRUMENTAL_PHRASE = "Instrumental, no vocals.";

/** The page's boxes, in the order it shows them. `voice` only exists with lyrics. */
export const FIELD_NAMES = ["genre", "mood", "bpm", "key", "instruments", "voice"] as const;
export type FieldName = (typeof FIELD_NAMES)[number];
export type Fields = Record<FieldName, string>;

/** The section tags the model's card lists. In the lyrics each goes on a line of its own (DESIGN.md §26.2). */
export const SECTION_TAGS = ["Intro", "Verse", "Pre-Chorus", "Chorus", "Post-Chorus", "Bridge", "Solo", "Instrumental", "Outro"] as const;

/** The length shortcuts (DESIGN.md §26.9): the real model is slow, so a short try is one click away. */
export const DURATION_CHIPS = [15, 30, 60, 120, 180, 300] as const;

// ------------------------------------------------------------------ the description (decision #42)
const clean = (text: unknown): string => String(text ?? "").split(/\s+/).filter(Boolean).join(" ");
const sentence = (text: string): string => {
  const t = clean(text);
  return !t || ".!?".includes(t[t.length - 1]) ? t : `${t}.`;
};

/** "C major" → "key is C, and scale is major."; a key with no scale → "key is C." */
export function keySentence(key: string): string {
  const t = clean(key);
  if (!t) return "";
  const space = t.indexOf(" ");
  return space < 0 ? `key is ${t}.` : `key is ${t.slice(0, space)}, and scale is ${t.slice(space + 1)}.`;
}

/** The description for these fields, in the layout the model's card recommends. Empty fields leave their line out; with
 *  lyrics (`instrumental` false) the Vocal Details section appears when there is a voice. This must give the same text
 *  as backend/studio/musicprompt.py (the same golden cases are tested on both sides). */
export function buildDescription(fields: Partial<Record<string, string>>, instrumental: boolean): string {
  const [genre, mood, bpm] = [clean(fields.genre), clean(fields.mood), clean(fields.bpm)];
  const [key, instruments, voice] = [clean(fields.key), clean(fields.instruments), clean(fields.voice)];
  const lines = ["Global Metadata"];
  const attributes = [bpm ? `bpm is ${bpm}.` : "", keySentence(key), sentence(genre)].filter(Boolean);
  if (attributes.length) lines.push(`Basic Attributes: ${attributes.join(" ")}`);
  if (mood) lines.push(`Global Emotional Progression: ${sentence(mood)}`);
  if (instrumental) lines.push(INSTRUMENTAL_PHRASE);
  else if (voice) lines.push("Vocal Details", `Vocal Gender & Timbre: ${sentence(voice)}`);
  if (instruments) lines.push("Arrangement", `Instrument Lifecycle Description: ${sentence(instruments)}`);
  return lines.join("\n");
}

// ------------------------------------------------------------------ the form
export interface MusicForm {
  fields: Fields;
  lyricsOn: boolean; // false = instrumental
  lyrics: string;
  edited: boolean; // the description was changed by hand: the fields no longer rewrite it (until Rebuild)
  text: string; // that hand-written description; only used while `edited`
  duration: number; // seconds, an upper bound
  tracks: number; // versions of the same description
  steps: number; // rendering steps
  seedLocked: boolean; // false = a new random seed every run, as for pictures (decision #11)
  seed: number;
}

export const emptyFields = (): Fields => ({ genre: "", mood: "", bpm: "", key: "", instruments: "", voice: "" });

export function defaultMusicForm(caps: Capabilities): MusicForm {
  const m = caps.limits.music;
  return {
    fields: emptyFields(), lyricsOn: false, lyrics: "", edited: false, text: "",
    duration: m.duration.default, tracks: m.tracks.min, steps: m.steps.default, seedLocked: false, seed: 42,
  };
}

/** The description that will be sent: the hand-written one if there is one, else what the fields build. */
export function descriptionOf(form: MusicForm): string {
  return form.edited ? form.text : buildDescription(form.fields, !form.lyricsOn);
}

/** Whether there is anything to describe: a hand-written description, or a field that says something. (The builder
 *  always writes a heading, so an empty form must not count as a description.) Voice only counts with lyrics. */
export function hasDescription(form: MusicForm): boolean {
  if (form.edited) return form.text.trim() !== "";
  return FIELD_NAMES.some((name) => (name !== "voice" || form.lyricsOn) && form.fields[name].trim() !== "");
}

const inRange = (value: unknown, range: Range): value is number => typeof value === "number" && Number.isInteger(value) && value >= range.min && value <= range.max;

/** The reason a form cannot be sent yet, in words, or null. */
export function musicProblem(form: MusicForm, caps: Capabilities): string | null {
  const m = caps.limits.music;
  if (!hasDescription(form)) return "Describe the music: fill in at least one field, or write the description yourself.";
  const description = descriptionOf(form).trim();
  if (description.length > m.description_chars) return `The description is ${description.length} characters; the limit is ${m.description_chars}.`;
  if (form.lyricsOn && !form.lyrics.trim()) return "Write the lyrics, or turn Add lyrics off for an instrumental track.";
  if (form.lyricsOn && form.lyrics.trim().length > m.lyrics_chars) return `The lyrics are ${form.lyrics.trim().length} characters; the limit is ${m.lyrics_chars}.`;
  if (!inRange(form.duration, m.duration)) return `Length must be a whole number of seconds from ${m.duration.min} to ${m.duration.max}.`;
  if (!inRange(form.tracks, m.tracks)) return `Versions must be from ${m.tracks.min} to ${m.tracks.max}.`;
  if (!inRange(form.steps, m.steps)) return `Rendering steps must be from ${m.steps.min} to ${m.steps.max}.`;
  if (form.seedLocked && !inRange(form.seed, caps.limits.seed)) return `The seed must be from ${caps.limits.seed.min} to ${caps.limits.seed.max}.`;
  return null;
}

/** Every saved value is checked on its own, so an old or damaged one resets only itself (as for the picture options). */
export function sanitizeMusicForm(raw: unknown, caps: Capabilities): { form: MusicForm; repaired: boolean } {
  const defaults = defaultMusicForm(caps);
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return { form: defaults, repaired: raw !== null && raw !== undefined };
  const r = raw as Record<string, unknown>;
  const m = caps.limits.music;
  let repaired = false;
  function pick<K extends keyof MusicForm>(key: K, valid: (v: unknown) => boolean): MusicForm[K] {
    if (!(key in r)) return defaults[key];
    if (valid(r[key])) return r[key] as MusicForm[K];
    repaired = true;
    return defaults[key];
  }
  const fields = emptyFields();
  const savedFields = r.fields;
  if (savedFields !== undefined) {
    if (savedFields === null || typeof savedFields !== "object" || Array.isArray(savedFields)) repaired = true;
    else {
      for (const name of FIELD_NAMES) {
        const value = (savedFields as Record<string, unknown>)[name];
        if (value === undefined) continue;
        if (typeof value === "string" && value.length <= m.field_chars) fields[name] = value;
        else repaired = true;
      }
    }
  }
  const lyricsOn = pick("lyricsOn", (v) => typeof v === "boolean");
  const form: MusicForm = {
    fields,
    lyricsOn,
    lyrics: pick("lyrics", (v) => typeof v === "string" && v.length <= m.lyrics_chars),
    edited: pick("edited", (v) => typeof v === "boolean"),
    text: pick("text", (v) => typeof v === "string" && v.length <= m.description_chars),
    duration: pick("duration", (v) => inRange(v, m.duration)),
    tracks: pick("tracks", (v) => inRange(v, m.tracks)),
    steps: pick("steps", (v) => inRange(v, m.steps)),
    seedLocked: pick("seedLocked", (v) => typeof v === "boolean"),
    seed: pick("seed", (v) => inRange(v, caps.limits.seed)),
  };
  if (form.edited && !form.text.trim()) form.edited = false; // an empty hand-written description is no description
  return { form, repaired };
}

export function loadMusicForm(caps: Capabilities, store: KeyValueStore): { form: MusicForm; repaired: boolean } {
  const text = store.get(MUSIC_FORM_KEY);
  if (text === null) return { form: defaultMusicForm(caps), repaired: false };
  try {
    return sanitizeMusicForm(JSON.parse(text), caps);
  } catch {
    return { form: defaultMusicForm(caps), repaired: true };
  }
}

export function saveMusicForm(form: MusicForm, store: KeyValueStore): void {
  store.set(MUSIC_FORM_KEY, JSON.stringify(form));
}

// ------------------------------------------------------------------ requests
/** What the fields said, for the server to keep for Reuse. Empty ones are left out, and so is Voice without lyrics. */
function fieldsToSend(form: MusicForm): Record<string, string> {
  const out: Record<string, string> = {};
  for (const name of FIELD_NAMES) {
    if (name === "voice" && !form.lyricsOn) continue;
    const value = form.fields[name].trim();
    if (value) out[name] = value;
  }
  return out;
}

/** The request for this form. No lyrics means an instrumental track (the server sends the [Instrumental] tag). */
export function buildMusicRequest(form: MusicForm): CreateMusicBody {
  return {
    mode: "music",
    prompt: descriptionOf(form).trim(),
    ...(form.lyricsOn ? { lyrics: form.lyrics.trim() } : {}),
    options: {
      duration: form.duration,
      tracks: form.tracks,
      steps: form.steps,
      seed: form.seedLocked ? form.seed : null,
      fields: fieldsToSend(form),
    },
  };
}

/** Retry: the same request again, the same seed included. */
export function musicRetryRequest(run: MusicRun): CreateMusicBody {
  return {
    mode: "music",
    prompt: run.prompt,
    ...(run.lyrics ? { lyrics: run.lyrics } : {}),
    options: { duration: run.options.duration, tracks: run.options.tracks, steps: run.options.steps, seed: run.options.seed, fields: run.options.fields },
  };
}

/** Reuse: the fields, the description (marked as hand-written if it is not what the fields build), the lyrics and the
 *  settings, with the seed locked, clamped to today's limits. */
export function musicFormFromRun(run: MusicRun, caps: Capabilities, current: MusicForm): MusicForm {
  const fields = emptyFields();
  for (const name of FIELD_NAMES) {
    const value = run.options.fields[name];
    if (typeof value === "string") fields[name] = value;
  }
  const lyricsOn = run.lyrics !== null;
  const edited = run.prompt !== buildDescription(fields, !lyricsOn);
  const merged: MusicForm = {
    ...current,
    fields, lyricsOn, lyrics: run.lyrics ?? current.lyrics, edited, text: run.prompt,
    duration: run.options.duration, tracks: run.options.tracks, steps: run.options.steps, seedLocked: true, seed: run.options.seed,
  };
  return sanitizeMusicForm(merged, caps).form;
}

/** Put `[Tag]` on a line of its own at the caret (or over the selection): text on the same line as a tag is dropped
 *  by the model (DESIGN.md §26.2), so the tag gets a line break before it if it is not at a line start, and one after,
 *  where the caret lands. Null if the result would be longer than `limit`. */
export function insertTag(text: string, from: number, to: number, tag: string, limit: number): { text: string; caret: number } | null {
  const before = text.slice(0, from);
  let after = text.slice(to);
  const lead = before === "" || before.endsWith("\n") ? "" : "\n";
  if (after.startsWith("\n")) after = after.slice(1); // the line break after the tag is the one that was there
  const inserted = `${lead}[${tag}]\n`;
  const next = before + inserted + after;
  if (next.length > limit) return null;
  return { text: next, caret: before.length + inserted.length };
}

// ------------------------------------------------------------------ words
/** "45 s", "1 min", "1 min 30 s". */
export function durationText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m} min ${rest} s` : `${m} min`;
}

/** A track's length as a player shows it: "0:47", "1:00", "10:05". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function chipsFor(range: Range): number[] {
  return DURATION_CHIPS.filter((seconds) => seconds >= range.min && seconds <= range.max);
}

/** A short name for a track card (its accessible name, the viewer of a screen reader): the genre if there is one, else
 *  the mood or the instruments, else the start of the description. */
export function musicTitle(run: MusicRun): string {
  const f = run.options.fields;
  const pick = f.genre || f.mood || f.instruments;
  return clean(pick || run.prompt.replace(/^Global Metadata\s*/, "")).slice(0, 80);
}

export function musicSeedText(run: MusicRun): string {
  const { seed, tracks } = run.options;
  return tracks > 1 ? `seeds ${seed}–${seed + tracks - 1}` : `seed ${seed}`;
}

/** The one line of settings under a track card's description. */
export function musicMeta(run: MusicRun, took: string | null): string[] {
  const o = run.options;
  const meta = [`up to ${durationText(o.duration)}`, `${o.steps} steps`, musicSeedText(run)];
  if (o.tracks > 1) meta.splice(1, 0, `${o.tracks} versions`);
  if (took) meta.push(took);
  return meta;
}

/** A track's own line: which version, and how long the model really made it (it can end a piece sooner than asked). */
export function trackLabel(index: number, total: number, seconds: number, asked: number): string {
  const which = total > 1 ? `Version ${index + 1} of ${total}` : "Track";
  const length = clock(seconds);
  return Math.round(seconds) < asked ? `${which} · ${length} (you allowed up to ${clock(asked)})` : `${which} · ${length}`;
}

export type PhaseKey = "compose" | "render";
export interface Phase {
  key: PhaseKey;
  label: string;
  fraction: number; // 0 to 1
}

const ORDER: Record<string, number> = { compose: 0, render: 1, finish: 2 };

/** The two phases of making a track, each with its own bar (DESIGN.md §26.1): composing, frame by frame, then
 *  rendering, step by step. A phase before the one in progress is full, one after it is empty. `finish` fills both. */
export function musicPhases(progress: Progress | null): Phase[] {
  const at = progress?.stage ? ORDER[progress.stage] ?? 0 : -1;
  const own = progress ? Math.min(1, Math.max(0, progress.step / Math.max(1, progress.steps))) : 0;
  const fill = (index: number): number => (at > index ? 1 : at === index ? own : 0);
  return [
    { key: "compose", label: "Composing", fraction: fill(0) },
    { key: "render", label: "Rendering", fraction: fill(1) },
  ];
}

/** What a running card says under its bars. */
export function musicProgressText(progress: Progress | null, canceling: boolean): string {
  if (!progress) return canceling ? "Stopping…" : "Starting…";
  const version = progress.of > 1 ? `Version ${progress.image} of ${progress.of} · ` : "";
  const stage = progress.stage;
  const detail =
    stage === "finish" ? "finishing the sound…"
    : stage === "render" ? `rendering, step ${progress.step} of ${progress.steps}`
    : `composing, ${Math.round((progress.step / Math.max(1, progress.steps)) * 100)}%`;
  return `${version}${detail}${canceling ? " · stopping at the next step…" : ""}`;
}

/** What the Music tab says about the model, so a long first wait is no surprise (DESIGN.md §26.4). Null when the music
 *  model is loaded and nothing needs saying. */
export function musicModelNote(worker: WorkerStatus): string | null {
  const live = worker.state === "ready" || worker.state === "busy";
  if (worker.model === "music" && live) return null;
  if (worker.model === "music" && worker.state === "loading") return "The music model is loading. A track you send now waits for it, then starts.";
  if (worker.model === "image" && live) {
    return "The image model is loaded. A track replaces it with the music model, which takes a while the first time; your next picture loads the image model again.";
  }
  return "The music model isn't loaded. It loads for your first track, which takes a while (Load music model does it now).";
}

// ------------------------------------------------------------------ the tab
export type TabId = "images" | "music";
export const TABS: { id: TabId; label: string }[] = [
  { id: "images", label: "Images" },
  { id: "music", label: "Music" },
];

export function readTab(store: KeyValueStore): TabId {
  const saved = store.get(TAB_KEY);
  return saved === "music" ? "music" : "images";
}

export function saveTab(tab: TabId, store: KeyValueStore): void {
  store.set(TAB_KEY, tab);
}

/** The tab one step from `current` for an arrow key (wrapping), or the first or last for Home and End. */
export function tabAfterKey(current: TabId, key: string): TabId | null {
  const at = TABS.findIndex((tab) => tab.id === current);
  switch (key) {
    case "ArrowRight":
      return TABS[(at + 1) % TABS.length].id;
    case "ArrowLeft":
      return TABS[(at - 1 + TABS.length) % TABS.length].id;
    case "Home":
      return TABS[0].id;
    case "End":
      return TABS[TABS.length - 1].id;
    default:
      return null;
  }
}
