import { describe, expect, it } from "vitest";
import {
  entryCountText, entryReady, listButtonLabel, pronunciationLine, sayProblem, takenBy, tidy, triedNothing, usedLines, wordKey, wordProblem,
} from "./pronunciation";
import type { PronunciationEntry, PronunciationLimits } from "./speechTypes";

// The pronunciation list's rules as the page keeps them (DESIGN.md §44.3): the checks that are made before the server is asked, and the words that are said. The server is the
// rule (backend/tests/test_speech_pronounce.py); these tests keep the page from sending what the server will certainly refuse, and from saying a wrong number.

const LIMITS: PronunciationLimits = { word_max: 60, words_max: 6, say_max: 120, entries_max: 1000, try_chars: 5000 };
const entry = (id: string, word: string, say = "x", match_case = false): PronunciationEntry => ({ id, word, say, match_case, created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z" });

describe("tidying a field", () => {
  it("makes every run of white space one space and trims the ends, as the server does", () => {
    expect(tidy("  New \n\t York  ")).toBe("New York");
    expect(tidy("   ")).toBe("");
  });

  it("gives the same key to words that differ only in case or spacing", () => {
    expect(wordKey("  NGUYEN ")).toBe(wordKey("nguyen"));
    expect(wordKey("New  York")).toBe(wordKey("new york"));
  });
});

describe("what is wrong with a word", () => {
  it("says nothing of an empty box, and nothing of a word that is fine", () => {
    expect(wordProblem("", LIMITS)).toBeNull();
    expect(wordProblem("   ", LIMITS)).toBeNull();
    expect(wordProblem("Nguyen", LIMITS)).toBeNull();
    expect(wordProblem("New York", LIMITS)).toBeNull();
  });

  it("refuses a word that is too long, or a phrase of too many words, with the limits the server gave", () => {
    expect(wordProblem("x".repeat(61), LIMITS)).toBe("A word on the list is at most 60 characters and 6 words.");
    expect(wordProblem("a b c d e f g", LIMITS)).toBe("A word on the list is at most 60 characters and 6 words.");
    expect(wordProblem("a b c d e f", LIMITS)).toBeNull(); // six is allowed
    expect(wordProblem("x".repeat(60), LIMITS)).toBeNull();
    expect(wordProblem("a".repeat(30), { ...LIMITS, word_max: 20 })).toBe("A word on the list is at most 20 characters and 6 words."); // the number is the server's, not the page's
  });

  it("refuses a word that does not begin with a letter or a digit", () => {
    expect(wordProblem("-dash", LIMITS)).toBe("A word on the list has to begin with a letter or a digit.");
    expect(wordProblem("$5", LIMITS)).toBe("A word on the list has to begin with a letter or a digit.");
    expect(wordProblem("Ñandú", LIMITS)).toBeNull(); // a letter of any script
    expect(wordProblem("101 Dalmatians", LIMITS)).toBeNull();
  });
});

describe("what is wrong with how it is said", () => {
  it("says nothing of an empty box", () => {
    expect(sayProblem("", "Nguyen", LIMITS)).toBeNull();
  });

  it("refuses a respelling that is too long, or that is the word itself", () => {
    expect(sayProblem("y".repeat(121), "Nguyen", LIMITS)).toBe("A respelling is at most 120 characters.");
    expect(sayProblem("  Nguyen ", "Nguyen", LIMITS)).toMatch(/^That is the word itself/);
    expect(sayProblem("nguyen", "Nguyen", LIMITS)).toBeNull(); // only an exact copy: another spelling is a respelling
    expect(sayProblem("win", "Nguyen", LIMITS)).toBeNull();
  });
});

describe("whether an entry can be sent", () => {
  it("needs both boxes, and neither wrong", () => {
    expect(entryReady("Nguyen", "win", LIMITS)).toBe(true);
    expect(entryReady("", "win", LIMITS)).toBe(false);
    expect(entryReady("Nguyen", "  ", LIMITS)).toBe(false);
    expect(entryReady("-x", "win", LIMITS)).toBe(false);
    expect(entryReady("Nguyen", "Nguyen", LIMITS)).toBe(false);
  });
});

describe("a word that is already on the list", () => {
  const list = [entry("a", "Nguyen"), entry("b", "New York")];

  it("is found ignoring case and spacing", () => {
    expect(takenBy(list, "NGUYEN")?.id).toBe("a");
    expect(takenBy(list, " new   york ")?.id).toBe("b");
    expect(takenBy(list, "Hermes")).toBeNull();
  });

  it("is not taken by the entry being changed: its own word may be written with other capitals", () => {
    expect(takenBy(list, "NGUYEN", "a")).toBeNull();
    expect(takenBy(list, "nguyen", "b")?.id).toBe("a"); // but another entry may not take it
  });
});

describe("the words the page says", () => {
  it("counts entries", () => {
    expect(entryCountText(0)).toBe("No entries yet");
    expect(entryCountText(1)).toBe("1 entry");
    expect(entryCountText(3)).toBe("3 entries");
  });

  it("puts the count on the button, and none when there is none", () => {
    expect(listButtonLabel(0)).toBe("Pronunciation list");
    expect(listButtonLabel(12)).toBe("Pronunciation list (12)");
  });

  it("says what a reading made of the list, and nothing when it changed no word", () => {
    expect(pronunciationLine({ entries: 3, uses: 14 })).toBe("Pronunciation list: 3 entries, 14 words said your way.");
    expect(pronunciationLine({ entries: 1, uses: 1 })).toBe("Pronunciation list: 1 entry, 1 word said your way.");
    expect(pronunciationLine({ entries: 2, uses: 0 })).toBeNull();
    expect(pronunciationLine(undefined)).toBeNull();
    expect(pronunciationLine(null)).toBeNull();
  });

  it("lists the entries that changed a sentence, with how many times", () => {
    expect(usedLines([{ word: "Nguyen", say: "win", count: 1 }, { word: "US", say: "you ess", count: 2 }, { word: "Hermes", say: "her meez", count: 5 }])).toEqual(
      ["Nguyen → win", "US → you ess, twice", "Hermes → her meez, 5 times"]);
    expect(triedNothing).toMatch(/said as it is written/);
  });
});
