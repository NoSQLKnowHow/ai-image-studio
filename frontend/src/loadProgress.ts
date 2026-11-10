// What the page says while a model loads (DESIGN.md section 39). The server reports how much of the model has arrived in the cache and how long the load has taken
// (`worker.progress`); this turns that into words. Nothing here guesses: where the server could not know a size, none is shown, and loading into memory, which has no
// measure, is shown as the time it has taken and nothing else.

import type { LoadProgress, ModelName, WorkerStatus } from "./types";

const MIB = 1024 ** 2;
const GIB = 1024 ** 3;

/** A size as `du -sh` would give it (binary units, which is what the person will compare it with), in the words "MB" and "GB". */
export function sizeWords(bytes: number): string {
  if (!(bytes > 0)) return "0 MB";
  if (bytes >= GIB) return `${(bytes / GIB).toFixed(1)} GB`;
  const mb = bytes / MIB;
  if (mb >= 100) return `${Math.round(mb)} MB`;
  return mb < 0.05 ? "0 MB" : `${mb.toFixed(1)} MB`;
}

/** How long, in whole seconds, minutes or hours: "40 s", "3 min 5 s", "1 h 2 min". */
export function elapsedWords(seconds: number): string {
  const s = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** The names of the speech engines by id, as the server gives them in `capabilities.speech.engines`. The page writes no engine's name itself: the next model is added on the
 *  server alone (DESIGN.md §38, criterion 260), and its name arrives the way the others' do. */
export type EngineTitles = ReadonlyMap<string, string>;
export const NO_TITLES: EngineTitles = new Map();
export const engineTitlesOf = (engines: readonly { id: string; title: string }[] | null | undefined): EngineTitles => new Map((engines ?? []).map((engine) => [engine.id, engine.title]));

// What the server's variant word says of a model that is one of several for an engine: Qwen3-TTS is three models, one for each kind of voice (DESIGN.md §38.18), and which one is
// loading is what explains a wait, and why switching kind of voice loads again. Said by the word and not by the engine, so that another engine that names its models the same
// way is said the same way. (Maps, since the words come from the server: "constructor" is not a kind of voice.)
const KINDS_OF_VOICE: ReadonlyMap<string, string> = new Map([["custom", "for preset voices"], ["design", "for designing a voice"], ["clone", "for your own voices"]]);

/** What is loading, as a person would say it: "the image model", "Qwen3-TTS for designing a voice", "Kokoro". */
export function modelWords(model: ModelName, engine?: string | null, variant?: string | null, titles: EngineTitles = NO_TITLES): string {
  if (model === "image") return "the image model";
  if (model === "music") return "the music model";
  const name = engine ? titles.get(engine) : undefined;
  if (!name) return "the speech model";
  const kind = variant ? KINDS_OF_VOICE.get(variant) : undefined;
  return kind ? `${name} ${kind}` : name;
}

/** The sentence about the model that was unloaded to make room, from the server's model key ("image", "music", "speech:qwen3-tts:custom"), or null. */
export function afterWords(key: string | null | undefined, titles: EngineTitles = NO_TITLES): string | null {
  if (!key) return null;
  const [kind, engine, variant] = key.split(":");
  if (kind !== "image" && kind !== "music" && kind !== "speech") return null;
  const what = modelWords(kind, engine, variant, titles);
  return `${what.charAt(0).toUpperCase()}${what.slice(1)} was unloaded to make room.`;
}

export interface LoadView {
  phase: LoadProgress["phase"];
  headline: string; // what is going on: it changes only when the phase or the model does, so it is what is announced
  detail: string; // the numbers, which change every second: "2.1 GB of about 4.5 GB · 3 min 5 s"
  pill: string; // the short form for the header's pill: "2.1 GB of 4.5 GB", "40 s"
  fraction: number | null; // 0 to 1 where a size to expect is known, else null: a bar that moves instead of one that fills
  after: string | null;
}

/** What to show of a load that is under way; null when the worker is not loading or the server did not say how far (an older one). */
export function loadView(worker: WorkerStatus | null | undefined, titles: EngineTitles = NO_TITLES): LoadView | null {
  const p = worker?.progress;
  if (!worker || worker.state !== "loading" || !p) return null;
  const what = modelWords(worker.model, worker.engine, worker.variant, titles);
  const time = p.elapsed_s >= 1 ? elapsedWords(p.elapsed_s) : "";
  const known = p.total_bytes !== null && p.total_bytes > 0;
  if (p.phase === "downloading") {
    const size = known ? `${sizeWords(p.done_bytes)} of about ${sizeWords(p.total_bytes as number)}` : `${sizeWords(p.done_bytes)} so far`;
    return {
      phase: p.phase, headline: `Downloading ${what}`, detail: time ? `${size} · ${time}` : size,
      pill: known ? `${sizeWords(p.done_bytes)} of ${sizeWords(p.total_bytes as number)}` : sizeWords(p.done_bytes),
      fraction: known ? Math.min(1, Math.max(0, p.done_bytes / (p.total_bytes as number))) : null, after: afterWords(p.after, titles),
    };
  }
  return {
    phase: p.phase, headline: p.phase === "starting" ? `Starting to load ${what}…` : `Loading ${what} into memory`, detail: time, pill: time,
    fraction: null, after: afterWords(p.after, titles),
  };
}

/** What a screen reader is told when the phase changes (not every second: the numbers are not announced). */
export function loadAnnouncement(view: LoadView): string {
  return `${view.headline.replace(/…$/, "")}.`;
}
