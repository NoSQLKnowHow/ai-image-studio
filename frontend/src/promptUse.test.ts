import { describe, expect, it } from "vitest";
import { usedText } from "./builder";
import { buildDescription, defaultMusicForm, emptyFields } from "./music";
import { fillMusic, fillVector, skippedText } from "./promptUse";
import { CAPS } from "./testdata";
import { defaultVectorForm, editRecipe } from "./vector";

// Using a prompt in the Vector form and the Music fields (DESIGN.md §37.2, criterion 242): what goes in, what is left out and named, nothing forced past the form's rules.

const vlimits = CAPS.limits.vector;
const vform = () => defaultVectorForm(CAPS);
const mform = () => defaultMusicForm(CAPS);
const draft = (prompt: string, settings: Record<string, unknown> = {}) => ({ prompt, settings });

describe("the Vector form from a prompt", () => {
  it("puts the idea in, trimmed, and changes nothing else when the prompt has no settings", () => {
    const before = vform();
    const { form, applied, skipped } = fillVector(before, draft("  a fox, flat  "), CAPS);
    expect(form).toEqual({ ...before, idea: "a fox, flat" });
    expect(applied).toEqual([]);
    expect(skipped).toEqual([]);
  });

  it("sets the kind, the colours, the inks and the size of the sheet, and says so", () => {
    const { form, applied, skipped } = fillVector(vform(), draft("a fox", { kind: "line", colours: 4, palette: ["#ff0000", "#00aa00"], width_mm: 120, height_mm: 80 }), CAPS);
    expect(form).toMatchObject({ idea: "a fox", kind: "line", colours: 4, palette: ["#FF0000", "#00AA00"], widthMm: 120, heightMm: 80 });
    expect(applied).toEqual(["kind line", "4 colours", "2 inks", "120 × 80 mm"]);
    expect(skipped).toEqual([]);
  });

  it("leaves out a kind the studio does not have, colours out of range, and a size out of range, naming each", () => {
    const before = vform();
    const { form, applied, skipped } = fillVector(before, draft("x", { kind: "sculpture", colours: vlimits.colours.max + 1, width_mm: vlimits.size_mm.max + 1, height_mm: 50 }), CAPS);
    expect(form).toEqual({ ...before, idea: "x" });
    expect(applied).toEqual([]);
    expect(skipped).toEqual(["kind sculpture", `${vlimits.colours.max + 1} colours`, "the size"]);
  });

  it("will not take inks that break the palette rules: white, the same twice, too many for the colours, or not colours", () => {
    for (const palette of [["#FFFFFF"], ["#FF0000", "#ff0000"], ["#FF0000", "#00AA00", "#0000FF"], ["red"]]) {
      const { form, skipped } = fillVector(vform(), draft("x", { colours: 3, palette }), CAPS);
      expect(form.palette).toEqual([]);
      expect(skipped).toContain("the inks");
    }
  });

  it("takes fewer colours and keeps only the inks that still fit, or none if they are replaced", () => {
    const start = { ...vform(), colours: 5, palette: ["#FF0000", "#00AA00", "#0000FF"] };
    expect(fillVector(start, draft("x", { colours: 2 }), CAPS).form.palette).toEqual(["#FF0000"]);
    expect(fillVector(start, draft("x", { colours: 4, palette: ["#800080"] }), CAPS).form.palette).toEqual(["#800080"]);
  });

  it("changes only the side it was given, keeping the other from the form", () => {
    const start = { ...vform(), widthMm: 90, heightMm: 70 };
    expect(fillVector(start, draft("x", { width_mm: 100 }), CAPS).form).toMatchObject({ widthMm: 100, heightMm: 70 });
    expect(fillVector(start, draft("x", { height_mm: 60 }), CAPS).form).toMatchObject({ widthMm: 90, heightMm: 60 });
  });

  it("drops a recipe written by hand, so that the new idea is what is sent", () => {
    const { form } = fillVector(editRecipe(vform(), "my own words"), draft("a fox"), CAPS);
    expect(form.recipeEdited).toBe(false);
    expect(form.recipeText).toBe("");
  });

  it("does not touch the form it was given", () => {
    const before = vform();
    const snapshot = structuredClone(before);
    fillVector(before, draft("a fox", { colours: 3, palette: ["#FF0000"] }), CAPS);
    expect(before).toEqual(snapshot);
  });
});

describe("the Music fields from a prompt", () => {
  it("puts in the fields, lyrics and length, and keeps the prompt's words as the description when the fields would not build them", () => {
    const { form, applied, skipped } = fillMusic(mform(), draft("A warm slow song about the sea", { fields: { genre: "folk", mood: "wistful", bpm: "72" }, lyrics: "Salt on the wind", seconds: 90 }), CAPS);
    expect(form.fields).toMatchObject({ genre: "folk", mood: "wistful", bpm: "72", key: "", instruments: "", voice: "" });
    expect(form).toMatchObject({ lyricsOn: true, lyrics: "Salt on the wind", duration: 90, edited: true, text: "A warm slow song about the sea" });
    expect(applied).toEqual(["genre folk", "mood wistful", "tempo 72", "lyrics", "90 seconds"]);
    expect(skipped).toEqual([]);
  });

  it("lets the form build the description when the prompt's words are exactly what the fields build, and keeps them as written when they are not", () => {
    const fields = { genre: "folk", mood: "wistful" };
    const built = buildDescription({ ...emptyFields(), ...fields }, true);
    expect(fillMusic(mform(), draft(built, { fields }), CAPS).form).toMatchObject({ edited: false, text: "" });
    expect(fillMusic(mform(), draft(`${built} Plus a flute.`, { fields }), CAPS).form).toMatchObject({ edited: true, text: `${built} Plus a flute.` });
    expect(fillMusic(mform(), draft("", { fields }), CAPS).form).toMatchObject({ edited: false, text: "" }); // no words: the fields speak
  });

  it("turns lyrics off for an instrumental piece and clears the old ones", () => {
    const start = { ...mform(), lyricsOn: true, lyrics: "old words" };
    const { form, applied } = fillMusic(start, draft("calm piano", { instrumental: true }), CAPS);
    expect(form).toMatchObject({ lyricsOn: false, lyrics: "" });
    expect(applied).toContain("instrumental");
  });

  it("leaves lyrics as they were when the prompt says nothing about them", () => {
    const start = { ...mform(), lyricsOn: true, lyrics: "kept words" };
    expect(fillMusic(start, draft("calm piano"), CAPS).form).toMatchObject({ lyricsOn: true, lyrics: "kept words" });
  });

  it("leaves out a length, lyrics, a field or a description that is too long, naming each", () => {
    const m = CAPS.limits.music;
    const { form, skipped } = fillMusic(mform(), draft("x".repeat(m.description_chars + 1), {
      seconds: m.duration.max + 1, lyrics: "y".repeat(m.lyrics_chars + 1), fields: { genre: "z".repeat(m.field_chars + 1), mood: "calm" },
    }), CAPS);
    expect(form.duration).toBe(mform().duration);
    expect(form.lyricsOn).toBe(false);
    expect(form.fields.genre).toBe("");
    expect(form.fields.mood).toBe("calm");
    expect(form.edited).toBe(false);
    expect(skipped).toEqual(["the genre", "the lyrics", "the description", `${m.duration.max + 1} seconds`]);
  });

  it("ignores a length that is not a whole number of seconds", () => {
    expect(fillMusic(mform(), draft("x", { seconds: 61.5 }), CAPS).skipped).toEqual(["61.5 seconds"]);
  });

  it("does not touch the form it was given", () => {
    const before = mform();
    const snapshot = structuredClone(before);
    fillMusic(before, draft("x", { fields: { genre: "folk" }, lyrics: "la" }), CAPS);
    expect(before).toEqual(snapshot);
  });
});

describe("the words about what was left out", () => {
  it("says nothing when nothing was", () => expect(skippedText([])).toBe(""));
  it("names one or several", () => {
    expect(skippedText(["the size"])).toBe(" Left out, because the form cannot take it: the size.");
    expect(skippedText(["the size", "the inks", "kind x"])).toBe(" Left out, because the form cannot take them: the size, the inks and kind x.");
  });
  it("goes after the toast about what was set, for the box that was filled", () => {
    expect(usedText(["kind line", "4 colours"], "idea")).toBe("Filled the idea, set kind line and 4 colours.");
    expect(usedText([], "description")).toBe("Filled the description.");
    expect(usedText(["16:9"])).toBe("Filled the prompt, set 16:9.");
  });
});
