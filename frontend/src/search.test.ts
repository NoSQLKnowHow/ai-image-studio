import { describe, expect, it } from "vitest";
import {
  NO_FILTER, ONLY_DELETED, ONLY_KEPT, SEARCH_WORDS, countsScope, filterKey, filterParams, filtersOn, foldText, hasSearchOrFilters, isDefault, matches, parseFilterKey, readFilter, saveFilter,
  scopeParams, searchText, searchTokens, searchableWords, showsWorking, sizeOf, visibleRuns, watchWorking, withFamily, withProject, withoutSearch, wordsOf, type HistoryFilter, type View,
} from "./history";
import type { KeyValueStore } from "./options";
import { makeImage, makeMusicRun, makeRun, makeSpeechRun, makeVectorRun } from "./testdata";
import type { SourceInfo } from "./speechTypes";

// Search and the Model, Size and Date range filters, in the page's own hands (DESIGN.md §45.4, §45.8): the words rule, what `matches` decides for a run the page holds, the name a
// filter gets in the store, what is sent to the server and what the browser remembers. The shared table of cases (history.test.ts) pins `matches` to the server's SQL for the cases it
// holds; this file pins the rest of the page's side of it, rule by rule.

const filterOf = (over: Partial<HistoryFilter>): HistoryFilter => ({ ...NO_FILTER, ...over });
// a store that keeps what is written to it, and lets a test see all of it
const memory = (initial: Record<string, string> = {}): KeyValueStore & { saved: Record<string, string> } => {
  const saved = { ...initial };
  return { available: true, saved, get: (key) => saved[key] ?? null, set: (key, value) => { saved[key] = value; } };
};

describe("the words of a search (§45.4 rule 2)", () => {
  it("are runs of letters and digits, folded, each once, in order", () => {
    expect(searchTokens("Fox  Winter-snow")).toEqual(["fox", "winter", "snow"]);
    expect(searchTokens("café CAFE Cafe")).toEqual(["cafe"]); // an accent and a capital are not told apart
    expect(searchTokens("fox fox FOX")).toEqual(["fox"]);
    expect(searchTokens('"fox"* AND -winter: (hen)')).toEqual(["fox", "and", "winter", "hen"]); // an operator is an ordinary word, as in the Prompt builder's search
    expect(searchTokens("snake_case under_score")).toEqual(["snake", "case", "under", "score"]); // the underscore separates
    expect(searchTokens("a1 b2 123")).toEqual(["a1", "b2", "123"]);
    expect(searchTokens("日本語 テスト")).toEqual(["日本語", "テスト"]); // a run of letters of any script is a word
  });

  it("are at most ten, and the same word twice does not use one up", () => {
    expect(SEARCH_WORDS).toBe(10);
    expect(searchTokens(Array.from({ length: 30 }, (_, i) => `w${i}`).join(" "))).toEqual(Array.from({ length: 10 }, (_, i) => `w${i}`));
    expect(searchTokens("a a a a a a a a a a b")).toEqual(["a", "b"]);
    expect(searchTokens("w1 w2 w3", 2)).toEqual(["w1", "w2"]); // the limit is a parameter, so the server's can be passed on
  });

  it("are none for no text, null, or only punctuation: that is no search", () => {
    for (const text of ["", "   ", '"*-:()', "…", "—"]) expect(searchTokens(text), text).toEqual([]);
    expect(searchTokens(null)).toEqual([]);
  });

  it("fold the way the server's index does: accents off, ø and ß left alone", () => {
    expect(foldText("Café Ünïcode")).toBe("cafe unicode");
    expect(foldText("ø")).toBe("ø");
    expect(foldText("ß")).toBe("ß"); // no decomposition, so neither side changes it (a `casefold` would make it "ss" and the sides would disagree)
    expect(wordsOf("a_b c-d")).toEqual(["a", "b", "c", "d"]);
    expect(wordsOf("café")).toEqual(["cafe"]); // an accent that is a separate character is taken off too
  });

  it("make the text a filter keeps: trimmed, white space single, and nothing at all when no word is left", () => {
    expect(searchText("  red   fox ")).toBe("red fox");
    expect(searchText('"*-')).toBeNull();
    expect(searchText("")).toBeNull();
  });
});

describe("what a run is found by (§45.4 rule 1)", () => {
  it("is its prompt, a music run's lyrics, a reading's source, its notes and its tags", () => {
    const picture = makeRun({ prompt: "A Red Fox", notes: "chosen for the poster", tags: ["winter", "snow day"] });
    expect(searchableWords(picture)).toEqual(["a", "red", "fox", "chosen", "for", "the", "poster", "winter", "snow", "day"]);
    expect(searchableWords(makeMusicRun({ prompt: "calm piano", lyrics: "twinkle twinkle" }))).toContain("twinkle");
    const reading = makeSpeechRun({ prompt: "Moby Dick" });
    const withSource = (source: SourceInfo | Record<string, unknown>) => searchableWords({ ...reading, options: { ...reading.options, source: source as SourceInfo } });
    expect(withSource({ kind: "file", name: "whale-book.epub" })).toEqual(expect.arrayContaining(["moby", "dick", "whale", "book", "epub"]));
    expect(withSource({ kind: "url", url: "https://example.org/fox-tales", host: "example.org" })).toEqual(expect.arrayContaining(["example", "org", "fox", "tales"]));
    expect(withSource({ kind: "text" })).toEqual(["moby", "dick"]); // pasted text has no source name
    expect(withSource({ name: 5 })).toEqual(["moby", "dick"]); // a value that is not text is left out, not turned into words
  });

  it("is not the negative prompt, the settings, a Vector recipe or the model's name", () => {
    const run = makeRun({ prompt: "a fox", options: { ...makeRun().options, negative_prompt: "blurry" }, model_id: "model-a" });
    expect(searchableWords(run)).toEqual(["a", "fox"]);
    const drawing = makeVectorRun({ prompt: "a fox head", effective_prompt: "flat colour artwork gradient" });
    expect(searchableWords(drawing)).toEqual(["a", "fox", "head"]);
  });

  it("only a reading's options are read for a source: a picture run with a source-shaped option has none", () => {
    const picture = makeRun({ prompt: "x" });
    expect(searchableWords({ ...picture, options: { ...picture.options, source: { name: "secret.epub" } } as never })).toEqual(["x"]);
  });
});

describe("matches: a search (§45.4 rule 2)", () => {
  const fox = makeRun({ prompt: "a red fox in the snow", tags: ["winter"], notes: "for the poster" });
  const q = (text: string): HistoryFilter => filterOf({ q: text });

  it("needs every word to be the start of a word in the run", () => {
    expect(matches(fox, q("fox"))).toBe(true);
    expect(matches(fox, q("fo"))).toBe(true); // the start of a word is enough
    expect(matches(fox, q("ox"))).toBe(false); // the middle of one is not
    expect(matches(fox, q("fox winter"))).toBe(true); // a prompt word and a tag
    expect(matches(fox, q("fox hen"))).toBe(false); // every word, not any
    expect(matches(fox, q("poster"))).toBe(true); // a note alone
  });

  it("ignores case and accents on both sides", () => {
    expect(matches(makeRun({ prompt: "a café at night" }), q("CAFE"))).toBe(true);
    expect(matches(makeRun({ prompt: "A CAFE" }), q("café"))).toBe(true);
  });

  it("is no condition at all when no word is left, and an operator is an ordinary word", () => {
    expect(matches(fox, q('"*-:'))).toBe(true); // nothing to look for
    expect(matches(fox, q("fox AND winter"))).toBe(false); // needs the word "and"
    expect(matches(makeRun({ prompt: "fox and winter" }), q("fox AND winter"))).toBe(true);
  });

  it("combines with Show, the bin and a project, as filters combine", () => {
    const kept = makeRun({ prompt: "fox", pinned: true });
    expect(matches(kept, filterOf({ q: "fox", kept: true }))).toBe(true);
    expect(matches(makeRun({ prompt: "fox", pinned: false }), filterOf({ q: "fox", kept: true }))).toBe(false);
    const binned = makeRun({ prompt: "fox", deleted_at: "2026-10-02T00:00:00.000Z" });
    expect(matches(binned, q("fox"))).toBe(false); // the history does not show the bin
    expect(matches(binned, filterOf({ q: "fox", deleted: true }))).toBe(true);
    expect(matches(makeRun({ prompt: "fox", project_id: "p" }), filterOf({ q: "fox", project: "none" }))).toBe(false);
  });
});

describe("matches: Model, Size and the date range (§45.4 rule 5, §45.5)", () => {
  it("a model is the run's model, exactly", () => {
    const run = makeRun({ model_id: "model-a" });
    expect(matches(run, filterOf({ model: "model-a" }))).toBe(true);
    expect(matches(run, filterOf({ model: "model-a2" }))).toBe(false);
    expect(matches(run, filterOf({ model: "model" }))).toBe(false); // not a prefix
  });

  it("a size is a picture run's: the first picture the card shows, else the size that was asked for", () => {
    const asked = makeRun({ options: { ...makeRun().options, width: 640, height: 480 } });
    expect(sizeOf(asked)).toBe("640x480");
    const made = makeRun({ options: { ...makeRun().options, width: 640, height: 480 }, images: [makeImage(0, { width: 2400, height: 1792 })] });
    expect(sizeOf(made)).toBe("2400x1792"); // the picture that was made wins over what was asked
    const auto = makeRun({ mode: "edit", options: { ...makeRun().options, width: null, height: null } });
    expect(sizeOf(auto)).toBeNull(); // an edit on Auto size that has made nothing yet has no size to match
    expect(matches(asked, filterOf({ size: "640x480" }))).toBe(true);
    expect(matches(made, filterOf({ size: "640x480" }))).toBe(false);
    expect(matches(auto, filterOf({ size: "640x480" }))).toBe(false);
    expect(matches(asked, filterOf({ size: "480x640" }))).toBe(false); // width and height, not either
  });

  it("a music track, a drawing and a reading have no size, whatever the database holds for them", () => {
    for (const run of [makeMusicRun(), makeVectorRun(), makeSpeechRun()]) {
      expect(sizeOf(run), run.mode).toBeNull();
      expect(matches(run, filterOf({ size: "256x256" })), run.mode).toBe(false);
    }
  });

  it("a drawing whose recipe happens to carry a width and height still has no size", () => {
    // The kind decides, not whether the options have numbers: a Vector run's own size is in millimetres and must never match a picture size.
    const drawing = makeVectorRun();
    drawing.options = { ...drawing.options, width: 256, height: 256 } as never;
    expect(sizeOf(drawing)).toBeNull();
    expect(matches(drawing, filterOf({ size: "256x256" }))).toBe(false);
  });

  it("a date range is of instants: at the start is in, at the end is out, either end may be missing", () => {
    const at = (created_at: string) => makeRun({ created_at });
    const day = filterOf({ since: "2026-10-02T00:00:00.000Z", until: "2026-10-03T00:00:00.000Z" });
    expect([at("2026-10-01T23:59:59.999Z"), at("2026-10-02T00:00:00.000Z"), at("2026-10-02T12:00:00.000Z"), at("2026-10-02T23:59:59.999Z"), at("2026-10-03T00:00:00.000Z")]
      .map((run) => matches(run, day))).toEqual([false, true, true, true, false]);
    expect(matches(at("2026-10-05T00:00:00.000Z"), filterOf({ since: "2026-10-02T00:00:00.000Z" }))).toBe(true);
    expect(matches(at("2026-10-01T00:00:00.000Z"), filterOf({ until: "2026-10-02T00:00:00.000Z" }))).toBe(true);
    // a moment written without milliseconds is the same moment: the comparison is of instants, not of text
    expect(matches(at("2026-10-02T00:00:00Z"), filterOf({ since: "2026-10-02T00:00:00.000Z" }))).toBe(true);
    expect(matches(at("2026-10-02T00:00:00Z"), filterOf({ until: "2026-10-02T00:00:00.000Z" }))).toBe(false);
  });
});

describe("the name a filter gets (§45.8)", () => {
  it("names a search, a model, a size and a range after the other parts", () => {
    expect(filterKey(filterOf({ q: "red fox" }))).toBe("q:red%20fox");
    expect(filterKey(filterOf({ model: "model-a" }))).toBe("model:model-a");
    expect(filterKey(filterOf({ size: "2752x1536" }))).toBe("size:2752x1536");
    expect(filterKey(filterOf({ since: "2026-10-02T00:00:00.000Z", until: "2026-10-03T00:00:00.000Z" }))).toBe("since:2026-10-02T00:00:00.000Z+until:2026-10-03T00:00:00.000Z");
    expect(filterKey({ ...ONLY_KEPT, project: "p", q: "fox", model: "m" })).toBe("kept+project:p+q:fox+model:m");
  });

  it("is made of text that cannot contain what separates its parts, so no two filters share a name", () => {
    // the words and the model's name can hold "+" and ":"; encoded, they cannot make one filter look like another
    const tricky = [
      filterOf({ q: "a+model:x" }), filterOf({ q: "a", model: "x" }), filterOf({ q: "a+q:b" }), filterOf({ q: "a b" }), filterOf({ q: "a%20b" }), filterOf({ model: "a+size:1x1" }),
      filterOf({ model: "a", size: "1x1" }), filterOf({ q: "kept" }), ONLY_KEPT, filterOf({ model: "kept" }), filterOf({ q: "all" }), NO_FILTER, filterOf({ q: "é" }), filterOf({ q: "e" }),
    ];
    const names = tricky.map(filterKey);
    expect(new Set(names).size).toBe(names.length);
  });

  it("makes a filter with any of the five set no longer the default, and tells how many of the Filters row are on", () => {
    expect(isDefault(NO_FILTER)).toBe(true);
    for (const over of [{ q: "x" }, { model: "m" }, { size: "1x1" }, { since: "2026-10-02T00:00:00.000Z" }, { until: "2026-10-02T00:00:00.000Z" }]) {
      expect(isDefault(filterOf(over as Partial<HistoryFilter>)), JSON.stringify(over)).toBe(false);
      expect(hasSearchOrFilters(filterOf(over as Partial<HistoryFilter>))).toBe(true);
    }
    expect(hasSearchOrFilters(ONLY_KEPT)).toBe(false);
    expect(filtersOn(filterOf({ q: "x" }))).toBe(0); // the search has a box of its own
    expect(filtersOn(filterOf({ model: "m", size: "1x1" }))).toBe(2);
    expect(filtersOn(filterOf({ since: "2026-10-02T00:00:00.000Z", until: "2026-10-03T00:00:00.000Z" }))).toBe(1); // a range counts once, whether it has one end or two
    expect(filtersOn(filterOf({ until: "2026-10-03T00:00:00.000Z" }))).toBe(1);
  });

  it("is taken off by withoutSearch, which leaves Show, Project and a family as they were", () => {
    const busy: HistoryFilter = { kept: true, deleted: false, project: "p", family: null, q: "fox", model: "m", size: "1x1", since: "a", until: "b" };
    expect(withoutSearch(busy)).toEqual({ ...ONLY_KEPT, project: "p" });
    // a family view starts from the whole history: the search is not carried in
    expect(withFamily(busy, "f".repeat(32))).toEqual({ ...NO_FILTER, family: "f".repeat(32) });
  });
});

describe("what is sent to the server (§45.7)", () => {
  it("is each of the five that is set, and nothing for the others", () => {
    expect(filterParams(NO_FILTER)).toEqual({});
    expect(filterParams(filterOf({ q: "red fox" }))).toEqual({ q: "red fox" });
    expect(filterParams({ ...ONLY_KEPT, q: "fox", model: "m", size: "2752x1536", since: "2026-10-02T00:00:00.000Z", until: "2026-10-03T00:00:00.000Z" })).toEqual({
      kept: "true", q: "fox", model: "m", size: "2752x1536", since: "2026-10-02T00:00:00.000Z", until: "2026-10-03T00:00:00.000Z",
    });
  });

  it("is the same five for the counts, which also take the project", () => {
    expect(scopeParams({ q: "fox", model: null, size: null, since: null, until: "2026-10-03T00:00:00.000Z" })).toEqual({ q: "fox", until: "2026-10-03T00:00:00.000Z" });
    expect(scopeParams(NO_FILTER)).toEqual({});
  });

  it("names what the counts are about: the whole history, a project as it always did, or a search", () => {
    expect(countsScope(NO_FILTER)).toBeNull();
    expect(countsScope(ONLY_KEPT)).toBeNull(); // Show does not change the scope: the counts are for every Show choice
    expect(countsScope(withProject(NO_FILTER, "p1"))).toBe("p1");
    expect(countsScope(withProject(NO_FILTER, "none"))).toBe("none");
    const searched = countsScope(filterOf({ q: "fox" }));
    expect(searched).not.toBeNull();
    expect(new Set([searched, countsScope(filterOf({ q: "hen" })), countsScope(filterOf({ q: "fox", project: "p1" })), countsScope(filterOf({ model: "fox" })), "p1", null]).size).toBe(6);
    expect(countsScope(filterOf({ q: "fox" }))).toBe(searched); // the same scope, the same name
    expect(countsScope({ ...ONLY_KEPT, q: "fox" })).toBe(searched); // Show is not part of the scope
  });
});

describe("what the browser remembers (§45.4 rule 8)", () => {
  it("is Show and Project, and not the search, the model, the size or the dates", () => {
    const store = memory();
    saveFilter({ ...ONLY_KEPT, project: "a".repeat(32), q: "fox", model: "m", size: "1x1", since: "2026-10-02T00:00:00.000Z", until: "2026-10-03T00:00:00.000Z" }, store);
    expect(readFilter(store)).toEqual({ ...ONLY_KEPT, project: "a".repeat(32) });
    // and not a trace of them in what was written: the stored text is the name of the Show and Project choice alone
    expect(Object.values(store.saved)).toEqual([`kept+project:${"a".repeat(32)}`]);
  });

  it("never reads a search back from what an older or odder browser stored", () => {
    for (const text of ["q:fox", "kept+q:fox", "model:m", "since:2026-10-02T00:00:00.000Z", "size:1x1"]) expect(parseFilterKey(text), text).toBeNull();
    expect(readFilter(memory({ "studio.history.filter.v1": "kept+q:fox" }))).toEqual(NO_FILTER); // not understood: the default, not a half-read filter
  });
});

describe("views and what shows while a search is on (§45.8)", () => {
  const view: View = { ready: true, nextBefore: null, boundary: null };
  const table = (...runs: ReturnType<typeof makeRun>[]) => ({ runs: Object.fromEntries(runs.map((r) => [r.id, r])), order: runs.map((r) => r.id) });

  it("shows the runs of the cache that match, and a run that arrives is shown by the page's own rule", () => {
    const hit = makeRun({ id: "hit", prompt: "a red fox" });
    const miss = makeRun({ id: "miss", prompt: "a lighthouse" });
    const { runs, order } = table(hit, miss);
    expect(visibleRuns(runs, order, view, filterOf({ q: "fox" })).map((r) => r.id)).toEqual(["hit"]);
    // a run that has just been made on another page and arrives by an event: it joins the view if its words match, and not otherwise
    const arrived = makeRun({ id: "arrived", prompt: "another fox" });
    const next = table(arrived, hit, miss);
    expect(visibleRuns(next.runs, next.order, view, filterOf({ q: "fox" })).map((r) => r.id)).toEqual(["arrived", "hit"]);
  });

  it("shows a run that is working whether or not it matches, at the top, as the Kept view does", () => {
    const working = makeRun({ id: "w", status: "running", prompt: "a lighthouse" });
    const hit = makeRun({ id: "hit", prompt: "a red fox" });
    const { runs, order } = table(hit, working);
    expect(showsWorking(filterOf({ q: "fox" }))).toBe(true);
    expect(visibleRuns(runs, order, view, filterOf({ q: "fox" })).map((r) => r.id)).toEqual(["w", "hit"]);
    expect(showsWorking(filterOf({ q: "fox", deleted: true }))).toBe(false); // nothing works in the bin
  });

  it("tells when a run that was shown only while it worked has finished without matching", () => {
    const f = filterOf({ q: "fox" });
    const working = makeRun({ id: "w", status: "running", prompt: "a lighthouse" });
    const first = watchWorking(new Set(), table(working).runs, f);
    expect([...first.watched]).toEqual(["w"]);
    const done = { ...working, status: "done" as const };
    expect(watchWorking(first.watched, table(done).runs, f).left.map((r) => r.id)).toEqual(["w"]);
    // one that matches stays, and is not reported
    const matching = { ...done, prompt: "a fox" };
    expect(watchWorking(first.watched, table(matching).runs, f).left).toEqual([]);
  });

  it("leaves the unfiltered list and the bin alone", () => {
    expect(watchWorking(new Set(["x"]), {}, ONLY_DELETED).watched.size).toBe(0);
  });
});
