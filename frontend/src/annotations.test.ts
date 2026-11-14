import { describe, expect, it } from "vitest";
import {
  DEFAULT_LIMITS, addedText, autocompleteFor, canAnnotate, cleanTag, noteCounter, noteExcerpt, noteSavedText, planAdd, removedText, suggestWhy, tooManyText, typedTags,
} from "./annotations";

// The rules the tag editor follows (DESIGN.md §45.3, §45.4 rules 9 and 10), alone: the server cleans every tag and note again and is the authority, so these decide only what to show
// and what to send. The component tests (annotations.test.tsx) put them in a card.

describe("a tag as typed", () => {
  it("is trimmed, lower case, one space between words, no comma, at most 30 characters", () => {
    expect(cleanTag("  Red   FOX ")).toBe("red fox");
    expect(cleanTag("a,b")).toBe("a b");
    expect(cleanTag("a\tb\nc")).toBe("a b c"); // a control character is a space
    expect(cleanTag("x".repeat(45))).toBe("x".repeat(30));
    expect(cleanTag("   ")).toBe("");
    expect(cleanTag(`${"x".repeat(29)} y`)).toBe("x".repeat(29)); // cut at 30 and trimmed: a tag never ends in a space
    expect(cleanTag("Ünï")).toBe("ünï"); // accents are the person's, and are kept (the search ignores them, the tag does not)
  });

  it("is cut at the limit the server gave", () => {
    expect(cleanTag("abcdef", 3)).toBe("abc");
  });
});

describe("what a person typed in the tag box", () => {
  it("is split at commas into tags, each cleaned, the empty ones dropped, each once", () => {
    expect(typedTags("fox, winter")).toEqual(["fox", "winter"]);
    expect(typedTags("Fox,fox, FOX ,,  ,snow")).toEqual(["fox", "snow"]);
    expect(typedTags("")).toEqual([]);
    expect(typedTags(" , , ")).toEqual([]);
    expect(typedTags("red fox")).toEqual(["red fox"]); // a space is part of a tag
  });
});

describe("adding tags to a run", () => {
  it("gives the tags the run would have, A to Z, and says which are new and which it has", () => {
    expect(planAdd(["winter"], ["fox", "winter"])).toEqual({ next: ["fox", "winter"], added: ["fox"], already: ["winter"], tooMany: false });
    expect(planAdd([], ["b", "a"])).toEqual({ next: ["a", "b"], added: ["b", "a"], already: [], tooMany: false });
  });

  it("refuses to go over the limit, exactly at it being fine", () => {
    const twelve = Array.from({ length: 12 }, (_, i) => `t${String(i).padStart(2, "0")}`);
    expect(planAdd(twelve.slice(0, 11), ["new"]).tooMany).toBe(false); // the twelfth
    expect(planAdd(twelve, ["new"]).tooMany).toBe(true); // the thirteenth
    expect(planAdd(twelve, [twelve[0]]).tooMany).toBe(false); // a tag it has is not another
    expect(planAdd(["a"], ["b", "c"], 2).tooMany).toBe(true); // the limit is the server's
  });
});

describe("the autocomplete", () => {
  const known = [{ tag: "fox", count: 5 }, { tag: "winter", count: 3 }, { tag: "red fox", count: 2 }, { tag: "night", count: 1 }, { tag: "forest", count: 1 }];

  it("offers the tags in use that the run has not, most used first, those that start with what was typed or have a word that does", () => {
    expect(autocompleteFor(known, "fo", [])).toEqual([{ tag: "fox", count: 5 }, { tag: "red fox", count: 2 }, { tag: "forest", count: 1 }]);
    expect(autocompleteFor(known, "fo", ["fox"]).map((o) => o.tag)).toEqual(["red fox", "forest"]); // not one the run has
    expect(autocompleteFor(known, "WIN", []).map((o) => o.tag)).toEqual(["winter"]); // case does not matter
    expect(autocompleteFor(known, "ox", []).map((o) => o.tag)).toEqual([]); // the middle of a word does not
  });

  it("offers the most used when nothing is typed, at most eight, and looks only at what follows the last comma", () => {
    expect(autocompleteFor(known, "", []).map((o) => o.tag)).toEqual(["fox", "winter", "red fox", "night", "forest"]);
    const many = Array.from({ length: 20 }, (_, i) => ({ tag: `t${i}`, count: 20 - i }));
    expect(autocompleteFor(many, "", [])).toHaveLength(8);
    expect(autocompleteFor(known, "snow, wi", []).map((o) => o.tag)).toEqual(["winter"]);
  });
});

describe("what the editor says", () => {
  it("after adding", () => {
    expect(addedText(["fox"], [])).toBe("Added the tag “fox”.");
    expect(addedText(["fox", "snow"], [])).toBe("Added 2 tags: “fox”, “snow”.");
    expect(addedText([], ["fox"])).toBe("“fox” is already a tag.");
    expect(addedText([], ["fox", "snow"])).toBe("Those are already tags.");
  });

  it("when a run would have too many, after removing, and after a note", () => {
    expect(tooManyText(12)).toBe("A run can have 12 tags. Remove one to add another.");
    expect(removedText("fox")).toBe("Removed the tag “fox”.");
    expect(noteSavedText("why")).toBe("Note saved.");
    expect(noteSavedText("")).toBe("Note cleared.");
    expect(noteCounter("abc", 2000)).toBe("3 of 2000");
  });
});

describe("a note on a card", () => {
  it("is shown whole when it is short, and cut with an ellipsis when it is long or has many lines", () => {
    expect(noteExcerpt("chosen for the poster")).toEqual({ text: "chosen for the poster", cut: false });
    expect(noteExcerpt("one\ntwo\nthree")).toEqual({ text: "one\ntwo\nthree", cut: false });
    expect(noteExcerpt("one\ntwo\nthree\nfour")).toEqual({ text: "one\ntwo\nthree…", cut: true });
    const long = `${"word ".repeat(80)}end`;
    const cut = noteExcerpt(long);
    expect(cut.cut).toBe(true);
    expect(cut.text.endsWith("…")).toBe(true);
    expect(cut.text.length).toBeLessThanOrEqual(221);
    expect(cut.text).not.toMatch(/wor…$/); // cut at a space where there is one
  });
});

describe("whether a run can be annotated", () => {
  it("is not when it is in the bin, which only reads", () => {
    expect(canAnnotate({ deleted_at: null })).toBe(true);
    expect(canAnnotate({ deleted_at: "2026-10-02T00:00:00.000Z" })).toBe(false);
  });
});

describe("why Suggest is greyed", () => {
  it("is the capabilities' reason when it is not available, in words", () => {
    expect(suggestWhy({ available: false, reason: "Needs the Prompt builder's Ollama." }, null)).toBe("Needs the Prompt builder's Ollama.");
    expect(suggestWhy({ available: false, reason: null }, null)).toBe("Suggest is not available.");
  });

  it("is what the status found when Ollama is set up but cannot be reached, with the way to put it right", () => {
    expect(suggestWhy({ available: true, reason: null }, { reachable: false, message: "Can't reach the Ollama server.", hint: "Check that Ollama is running." })).toBe("Can't reach the Ollama server. Check that Ollama is running.");
    expect(suggestWhy({ available: true, reason: null }, { reachable: false, message: null, hint: null })).toBe("Ollama can't be reached right now.");
  });

  it("is nothing when it can be pressed: available, and reachable or not known yet", () => {
    expect(suggestWhy({ available: true, reason: null }, null)).toBeNull();
    expect(suggestWhy({ available: true, reason: null }, { reachable: true, message: null, hint: null })).toBeNull();
  });
});

describe("the limits the page assumes until the server says", () => {
  it("are the server's own", () => {
    expect(DEFAULT_LIMITS).toEqual({ tags_max: 12, tag_chars: 30, notes_chars: 2000, search_chars: 200, search_words: 10 });
  });
});
