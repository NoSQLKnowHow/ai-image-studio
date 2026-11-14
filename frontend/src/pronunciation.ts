import type { PronunciationEntry, PronunciationLimits, PronunciationTry } from "./speechTypes";

// The pronunciation list's rules as the page needs them (DESIGN.md §44.3): the cheap checks that are made before the server is asked, and the words the dialog and
// the cards say. The server decides everything (`pronounce.py` is the rule, and its answer is shown as it says it); these functions only keep the page from sending
// a request that is certain to be refused, and from saying a number of its own: every limit comes from `capabilities.limits.speech.pronunciation`.

const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

/** A field as the server tidies it: every run of white space is one space, and the ends are trimmed. */
export const tidy = (text: string): string => text.split(/\s+/).filter(Boolean).join(" ");

/** The same word for the list: the server folds case away (`str.casefold`), and the nearest the browser has is lower case, which is all this check is for. */
export const wordKey = (word: string): string => tidy(word).toLocaleLowerCase();

/** What is wrong with a word as typed, in words, or null. Nothing is said for an empty box (the button is off, and nagging about a box the person has not used yet helps no one). */
export function wordProblem(word: string, limits: PronunciationLimits): string | null {
  const text = tidy(word);
  if (!text) return null;
  if (text.length > limits.word_max || text.split(" ").length > limits.words_max) {
    return `A word on the list is at most ${limits.word_max} characters and ${limits.words_max} words.`;
  }
  if (!/^[\p{L}\p{N}]/u.test(text)) return "A word on the list has to begin with a letter or a digit.";
  return null;
}

/** What is wrong with how it is said, or null. An empty box is not nagged about, as above. */
export function sayProblem(say: string, word: string, limits: PronunciationLimits): string | null {
  const text = tidy(say);
  if (!text) return null;
  if (text.length > limits.say_max) return `A respelling is at most ${limits.say_max} characters.`;
  if (text === tidy(word)) return "That is the word itself, so it would change nothing. Write it the way it should be said.";
  return null;
}

/** Whether the entry can be sent: both boxes filled in and neither wrong. */
export const entryReady = (word: string, say: string, limits: PronunciationLimits): boolean =>
  !!tidy(word) && !!tidy(say) && !wordProblem(word, limits) && !sayProblem(say, word, limits);

/** The entry that already has this word (ignoring case), other than `exceptId`: Add says so before asking, and Change does when the new word is another entry's. */
export const takenBy = (entries: readonly PronunciationEntry[], word: string, exceptId?: string): PronunciationEntry | null =>
  entries.find((e) => e.id !== exceptId && wordKey(e.word) === wordKey(word)) ?? null;

/** "No entries yet", "1 entry", "3 entries". */
export const entryCountText = (n: number): string => (n === 0 ? "No entries yet" : plural(n, "entry", "entries"));

/** The label of the button that opens the list: it says how many entries there are. */
export const listButtonLabel = (n: number): string => (n === 0 ? "Pronunciation list" : `Pronunciation list (${n})`);

/** What a reading says of the list when it was made: "Pronunciation list: 3 entries, 14 words said your way.", or null when the list changed nothing in it. */
export function pronunciationLine(info: { entries: number; uses: number } | undefined | null): string | null {
  if (!info || info.uses <= 0) return null;
  return `Pronunciation list: ${plural(info.entries, "entry", "entries")}, ${plural(info.uses, "word")} said your way.`;
}

/** Each entry that changed a sentence, for Try it: "Nguyen → win, twice". */
export function usedLines(used: PronunciationTry["used"]): string[] {
  return used.map((u) => `${u.word} → ${u.say}${u.count === 1 ? "" : `, ${u.count === 2 ? "twice" : `${u.count} times`}`}`);
}

/** The words under Try it once the server has answered: what would be spoken is shown by the caller; this says when nothing was changed. */
export const triedNothing = "Nothing in that sentence is on the list, so it is said as it is written.";
