// Using a prompt in another tab (DESIGN.md §37.2, criteria 235, 236, 242, 243): what a prompt's words and settings become in the Vector form and the Music fields, kept to each
// form's own rules, and what the Images options become with a prompt's settings laid over them. Pure functions: the page asks them, shows what they say, and does the rest. A
// setting that does not fit the form is left out and named, never forced in, and nothing here queues a run (rule 11: only a button the person presses does).

import type { Draft } from "./builderTypes";
import { FIELD_NAMES, buildDescription, emptyFields, hasDescription, type MusicForm } from "./music";
import type { Capabilities, Range } from "./types";
import { WHITE, inkCount, normaliseColour, paletteProblem, rebuildRecipe, type VectorForm } from "./vector";

export interface Fill<F> {
  form: F;
  applied: string[]; // what was set, in words, for the toast
  skipped: string[]; // what the prompt had that the form could not take, in words
}

const within = (value: unknown, range: Range): value is number => typeof value === "number" && Number.isFinite(value) && value >= range.min && value <= range.max;
const whole = (value: unknown, range: Range): value is number => within(value, range) && Number.isInteger(value);
const mm = (n: number): string => String(Number(n.toFixed(3)));

/** The Vector form with a prompt's idea and settings put in: the idea, the kind, the colours, the inks and the size of the sheet. A hand-written recipe is dropped (the new idea
 *  must be what is sent). Inks that would break the palette rules (white, twice, more than the colours leave room for) are left out and named. */
export function fillVector(form: VectorForm, draft: Pick<Draft, "prompt" | "settings">, caps: Capabilities): Fill<VectorForm> {
  const limits = caps.limits.vector;
  const s = draft.settings;
  const applied: string[] = [];
  const skipped: string[] = [];
  let next: VectorForm = { ...rebuildRecipe(form), idea: draft.prompt.trim() };

  if (typeof s.kind === "string") {
    if (limits.kinds.includes(s.kind as VectorForm["kind"])) { next.kind = s.kind as VectorForm["kind"]; applied.push(`kind ${s.kind}`); } else skipped.push(`kind ${s.kind}`);
  }
  if (s.colours !== undefined) {
    if (whole(s.colours, limits.colours)) {
      next = { ...next, colours: s.colours, palette: next.palette.slice(0, inkCount(s.colours)) };
      applied.push(`${s.colours} colours`);
    } else skipped.push(`${String(s.colours)} colours`);
  }
  if (Array.isArray(s.palette) && s.palette.length) {
    const inks = s.palette.map((c) => normaliseColour(c));
    const usable = inks.every((c): c is string => c !== null && c !== WHITE) && new Set(inks).size === inks.length && !paletteProblem(inks as string[], next.colours);
    if (usable) { next = { ...next, palette: inks as string[] }; applied.push(`${inks.length} ${inks.length === 1 ? "ink" : "inks"}`); } else skipped.push("the inks");
  }
  const width = s.width_mm, height = s.height_mm;
  if (width !== undefined || height !== undefined) {
    const w = width === undefined ? next.widthMm : width, h = height === undefined ? next.heightMm : height;
    if (within(w, limits.size_mm) && within(h, limits.size_mm)) {
      next = { ...next, widthMm: w, heightMm: h };
      applied.push(`${mm(w)} × ${mm(h)} mm`);
    } else skipped.push("the size");
  }
  return { form: next, applied, skipped };
}

/** The Music form with a prompt's description, fields, lyrics and length put in. The description is the prompt's words; when they are what the fields would build the form is
 *  left to build them, otherwise they are kept as written (the form's "edited" state). Lyrics turn "Add lyrics" on; `instrumental` turns it off. */
export function fillMusic(form: MusicForm, draft: Pick<Draft, "prompt" | "settings">, caps: Capabilities): Fill<MusicForm> {
  const limits = caps.limits.music;
  const s = draft.settings;
  const applied: string[] = [];
  const skipped: string[] = [];
  const fields = emptyFields();
  const given = (s.fields ?? {}) as Record<string, unknown>;
  for (const name of FIELD_NAMES) {
    const value = given[name];
    if (typeof value !== "string" || !value.trim()) continue;
    if (value.trim().length > limits.field_chars) { skipped.push(name === "bpm" ? "the tempo" : `the ${name}`); continue; }
    fields[name] = value.trim();
    applied.push(name === "bpm" ? `tempo ${value.trim()}` : `${name} ${value.trim()}`);
  }
  let next: MusicForm = { ...form, fields };

  const lyrics = typeof s.lyrics === "string" ? s.lyrics.trim() : "";
  if (lyrics) {
    if (lyrics.length > limits.lyrics_chars) skipped.push("the lyrics");
    else { next = { ...next, lyricsOn: true, lyrics }; applied.push("lyrics"); }
  } else if (s.instrumental === true) {
    next = { ...next, lyricsOn: false, lyrics: "" };
    applied.push("instrumental");
  }
  const words = draft.prompt.trim();
  if (words.length > limits.description_chars) {
    skipped.push("the description");
    next = { ...next, edited: false, text: "" };
  } else if (words && words !== buildDescription(next.fields, !next.lyricsOn)) {
    next = { ...next, edited: true, text: words };
  } else next = { ...next, edited: false, text: "" };

  if (s.seconds !== undefined) {
    if (whole(s.seconds, limits.duration)) { next = { ...next, duration: s.seconds }; applied.push(`${s.seconds} seconds`); } else skipped.push(`${String(s.seconds)} seconds`);
  }
  return { form: next, applied, skipped };
}

/** Whether a Music form made from a prompt says anything at all (the words, or a field): the check Generate now makes before it queues. */
export const musicHasWords = (form: MusicForm): boolean => hasDescription(form);

/** What the toast says besides what was set: the settings the form could not take. */
export function skippedText(skipped: string[]): string {
  if (skipped.length === 0) return "";
  const list = skipped.length === 1 ? skipped[0] : `${skipped.slice(0, -1).join(", ")} and ${skipped[skipped.length - 1]}`;
  return ` Left out, because the form cannot take ${skipped.length === 1 ? "it" : "them"}: ${list}.`;
}
