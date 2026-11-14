// Tags and notes on a run (DESIGN.md §45): the rules the tag editor follows, as plain functions so that each can be tested alone, and the context through which a card and the
// viewer reach the server.
//
// The server cleans every tag and note again and is the authority (`backend/studio/runsearch.py`); what is here only decides what to show and what to send, so that a person is
// told in words what happened (*“fox” is already a tag*, *A run can have 12 tags*) before a request is made, and so that the editor's list is right a moment before the server's
// answer arrives. A tag that the page cleans slightly differently from the server (the server folds the case of an `ß`, the page lower-cases it) is not a problem: the answer is the
// run as the server has it, and the editor shows that.

import { createContext, useContext } from "react";
import type { AnnotationLimits, Run, TagCount } from "./types";

/** What the page assumes until the server's capabilities say otherwise (a server of before 1.26 sends none, and has no tags either). */
export const DEFAULT_LIMITS: AnnotationLimits = { tags_max: 12, tag_chars: 30, notes_chars: 2000, search_chars: 200, search_words: 10 };

/** A tag as the server stores it: trimmed, control characters and commas made spaces, white space made one space, lower case, at most `max` characters. Empty when nothing usable is left. */
export function cleanTag(text: string, max: number = DEFAULT_LIMITS.tag_chars): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(/[\u0000-\u001f\u007f-\u009f,]/g, " ").replace(/\s+/g, " ").trim().toLowerCase().slice(0, max).trim();
}

/** What a person typed in the tag box, as the tags it holds: split at commas, each cleaned, the empty ones dropped, each once, in order (*fox, winter* is two tags). */
export function typedTags(text: string, max: number = DEFAULT_LIMITS.tag_chars): string[] {
  return [...new Set(text.split(",").map((part) => cleanTag(part, max)).filter(Boolean))];
}

/** What adding typed tags to a run's tags would do: the tags it would have (A to Z, as the server lists them), which of the typed ones are new and which the run has already, and
 *  whether it would be over the limit (in which case nothing is added: the editor says so in words). */
export function planAdd(current: readonly string[], typed: readonly string[], max: number = DEFAULT_LIMITS.tags_max): { next: string[]; added: string[]; already: string[]; tooMany: boolean } {
  const added = typed.filter((tag) => !current.includes(tag));
  const already = typed.filter((tag) => current.includes(tag));
  const next = [...current, ...added].sort();
  return { next, added, already, tooMany: next.length > max };
}

/** The tags the autocomplete offers for what is typed: tags in use that the run does not have, whose text (or any word of it) starts with what was typed; with nothing typed, the most
 *  used. Most used first, as the server lists them; at most `limit`. */
export function autocompleteFor(known: readonly TagCount[], typed: string, have: readonly string[], limit = 8): TagCount[] {
  const wanted = cleanTag(typed.split(",").at(-1) ?? "");
  return known
    .filter((entry) => !have.includes(entry.tag) && (wanted === "" || entry.tag.startsWith(wanted) || entry.tag.split(" ").some((word) => word.startsWith(wanted))))
    .slice(0, limit);
}

/** What the editor says after tags were added, in words. */
export function addedText(added: readonly string[], already: readonly string[]): string {
  if (added.length === 0) return already.length === 1 ? `“${already[0]}” is already a tag.` : "Those are already tags.";
  return added.length === 1 ? `Added the tag “${added[0]}”.` : `Added ${added.length} tags: ${added.map((tag) => `“${tag}”`).join(", ")}.`;
}

/** What the editor says when a run would have more tags than it may. */
export const tooManyText = (max: number): string => `A run can have ${max} tags. Remove one to add another.`;

/** What the editor says after a tag was removed. */
export const removedText = (tag: string): string => `Removed the tag “${tag}”.`;

/** What the editor says after the note was saved, changed or cleared. */
export const noteSavedText = (notes: string): string => (notes === "" ? "Note cleared." : "Note saved.");

/** The words of the counter beside the note: how much of the limit is used. */
export const noteCounter = (text: string, max: number): string => `${text.length} of ${max}`;

/** The first lines of a note for a card (the whole note is shown by Show all): at most this many characters, cut at a line break or a space where there is one, with an ellipsis. */
export function noteExcerpt(notes: string, max = 220): { text: string; cut: boolean } {
  const flat = notes.trim();
  if (flat.length <= max && flat.split("\n").length <= 3) return { text: flat, cut: false };
  const lines = flat.split("\n").slice(0, 3).join("\n");
  const head = lines.length > max ? lines.slice(0, max) : lines;
  const space = head.lastIndexOf(" ");
  return { text: `${(space > max * 0.6 ? head.slice(0, space) : head).trimEnd()}…`, cut: true };
}

/** Whether a run's tags and note may be changed from the page: not in the bin (it only reads, DESIGN.md §45.4 rule 12). */
export const canAnnotate = (run: Pick<Run, "deleted_at">): boolean => run.deleted_at === null;

/** What Suggest says about itself, from what the capabilities and the Prompt builder's status know: whether it can be pressed, and if not why, in words. */
export interface SuggestState {
  available: boolean;
  reason: string | null;
}

/** What a look at the Prompt builder's status found (`GET /api/builder/status`), for the Suggest button: null when it could not be asked. */
export interface Reachability {
  reachable: boolean;
  message: string | null;
  hint: string | null;
}

/** The words beside a greyed Suggest button: the capabilities' reason, or, when Ollama is set up but cannot be reached, what the status says (and how to put it right). */
export function suggestWhy(state: SuggestState, reach: Reachability | null): string | null {
  if (!state.available) return state.reason ?? "Suggest is not available.";
  if (reach && !reach.reachable) return [reach.message ?? "Ollama can't be reached right now.", reach.hint].filter(Boolean).join(" ");
  return null;
}

/** How the page reaches the server for tags and notes. A card and the viewer take it from the context (like `PromptLinks`, DESIGN.md §37.2), so it need not be passed through the
 *  panels of every tab. Each action answers the run as the server now has it, or rejects with an `ApiError` that the editor shows in words. */
export interface AnnotationActions {
  limits: AnnotationLimits;
  setTags: (run: Run, tags: string[]) => Promise<Run>;
  setNotes: (run: Run, notes: string) => Promise<Run>;
  knownTags: () => Promise<TagCount[]>; // the tags in use, for the autocomplete: asked when the box is used, remembered for a few seconds
  suggest: SuggestState;
  suggestTags: (run: Run) => Promise<string[]>;
  reachability: () => Promise<Reachability | null>; // is Ollama reachable now? null when it could not be asked
}

export const AnnotationsContext = createContext<AnnotationActions | null>(null);
export const useAnnotations = (): AnnotationActions | null => useContext(AnnotationsContext);
