import { findVoice, isSaved, partsToRead, type DraftPart, type VoicePick } from "./speech";
import type { PreparedSpeaker, PresetVoice, ReadingSpeakerBody, SavedVoice, SpeechEngine } from "./speechTypes";

// Several voices in one text, as the page keeps them (DESIGN.md §44.5): the names the review found, what the person chose for each, which voices an engine can use together,
// and what is sent. No React in it, so each rule can be tested on its own. The server decides everything again (`service.build`); these rules keep the page from offering a
// choice that the engine cannot do, and say why in words where something cannot be done.

/** What the person chose for one name: `on` (a speaker, whose label is not read) or off (not a speaker: the line is read as written, label and all, by the main voice), and
 *  the voice, null for the main voice. */
export interface SpeakerChoice {
  on: boolean;
  voice: VoicePick | null;
}

/** The choices for every name of a review, by the name as the review spells it. */
export type Cast = Record<string, SpeakerChoice>;

/** Every name found starts on, with the main voice (DESIGN.md §44.8 choice 13). */
export const initialCast = (speakers: readonly PreparedSpeaker[]): Cast => Object.fromEntries(speakers.map((s) => [s.name, { on: true, voice: null }]));

/** The cast with one name's choice changed. */
export const withChoice = (cast: Cast, name: string, change: Partial<SpeakerChoice>): Cast => ({ ...cast, [name]: { ...(cast[name] ?? { on: true, voice: null }), ...change } });

type Voice = PresetVoice | SavedVoice;

/** Which checkpoint (variant) of an engine speaks a preset voice, or a voice the person made. */
const variantOf = (engine: Pick<SpeechEngine, "preset_variant" | "clone_variant">, saved: boolean): string | null => (saved ? engine.clone_variant : engine.preset_variant);

/** The voices that can be used together with the main voice in one reading, and a sentence about it where there is something to say. An engine holds one checkpoint in memory at
 *  a time, so everyone in a reading has to be of one kind, the kind of the main voice: Qwen3-TTS' preset voices are one checkpoint and the voices you made another. The rule is
 *  read from the engine's own variants, never from its name. */
export interface VoiceOffer {
  presets: PresetVoice[];
  saved: SavedVoice[];
  note: string | null;
}

export function voicesFor(engine: Pick<SpeechEngine, "title" | "can_clone" | "preset_variant" | "clone_variant">, main: Voice | null, presets: readonly PresetVoice[], saved: readonly SavedVoice[]): VoiceOffer {
  if (!main) return { presets: [], saved: [], note: null };
  const kind = variantOf(engine, isSaved(main));
  const offerPresets = variantOf(engine, false) === kind ? [...presets] : [];
  const offerSaved = engine.can_clone && variantOf(engine, true) === kind ? [...saved] : [];
  const count = offerPresets.length + offerSaved.length;
  let note: string | null = null;
  if (count <= 1) {
    note = presets.length === 1 && !engine.can_clone
      ? `${engine.title} has one voice, so everyone is read in it. Switch a name off to have the name said as part of the text.`
      : "Only one voice can be used together with the main voice here, so everyone is read in it. Switch a name off to have the name said as part of the text.";
  } else if (engine.can_clone && variantOf(engine, false) !== variantOf(engine, true) && presets.length > 0 && saved.length > 0) {
    note = `${engine.title} speaks its own voices and the ones you made with different parts of the model, so everyone in a reading has to be one kind: here, ${isSaved(main) ? "the voices you made" : "the voices it comes with"}.`;
  }
  return { presets: offerPresets, saved: offerSaved, note };
}

/** The pick of a voice as a name for the list's `value`, and back. */
export const pickKey = (pick: VoicePick | null): string => (pick ? `${pick.kind}:${pick.id}` : "");
export function pickFrom(key: string): VoicePick | null {
  const [kind, ...rest] = key.split(":");
  const id = rest.join(":");
  return id && (kind === "preset" || kind === "saved") ? { kind, id } : null;
}

/** Whether a pick is still among the voices on offer (a voice deleted, or another engine chosen since, makes it stale: the main voice then speaks). */
export const pickAvailable = (pick: VoicePick | null, offer: Pick<VoiceOffer, "presets" | "saved">): boolean => !!pick && !!findVoice(pick, offer.presets, offer.saved);

/** The lines a name has, as "12 lines". */
export const linesText = (n: number): string => `${n} ${n === 1 ? "line" : "lines"}`;

/** The speakers to send: the names that are on and that have a line in what will be read, each with its voice if that voice is still on offer, else the main voice. */
export function speakersToSend(speakers: readonly PreparedSpeaker[], cast: Cast, used: ReadonlySet<string>, offer: Pick<VoiceOffer, "presets" | "saved">): ReadingSpeakerBody[] {
  return speakers
    .filter((s) => (cast[s.name]?.on ?? true) && used.has(s.name))
    .map((s) => {
      const pick = cast[s.name]?.voice ?? null;
      const usable = pickAvailable(pick, offer) ? pick : null;
      return { name: s.name, voice: usable?.kind === "preset" ? usable.id : "", saved_voice: usable?.kind === "saved" ? usable.id : null };
    });
}

/** The names that have a line in what will be read: in an included part, not left out (code, a table) and not emptied by the person. A name with no such line is not sent. */
export function speakersUsed(parts: readonly DraftPart[]): Set<string> {
  const used = new Set<string>();
  for (const part of partsToRead(parts)) for (const p of part.paragraphs) if (p.speaker && p.skipped === null && p.text.trim()) used.add(p.speaker);
  return used;
}

// What a paragraph is sent as (a speaker's line without its label, or with the label put back when the name is off) is `paragraphToSend` in speech.ts, which builds the request.
export { paragraphToSend } from "./speech";

/** What a reading says of its cast on its card (§44.5): "Speakers: Alice in Bea, Bob in the main voice.", or null for a reading with no speakers. Read from the reading, which keeps
 *  its cast, so it stays true whatever is chosen on the page afterwards. */
export function castLine(speakers: readonly { name: string; voice: { label: string } | null }[] | undefined | null): string | null {
  if (!speakers || speakers.length === 0) return null;
  return `Speakers: ${speakers.map((s) => `${s.name} in ${s.voice ? s.voice.label : "the main voice"}`).join(", ")}.`;
}
