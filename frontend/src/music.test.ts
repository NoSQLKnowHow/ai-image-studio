import { describe, expect, it } from "vitest";
import {
  DURATION_CHIPS, MUSIC_FORM_KEY, SECTION_TAGS, TAB_KEY, buildDescription, buildMusicRequest, chipsFor, clock, defaultMusicForm, descriptionOf,
  durationText, emptyFields, hasDescription, insertTag, keySentence, loadMusicForm, musicFormFromRun, musicMeta, musicModelNote, musicPhases,
  musicProblem, musicProgressText, musicRetryRequest, musicSeedText, musicTitle, readTab, sanitizeMusicForm, saveMusicForm, saveTab, tabAfterKey,
  trackLabel, type MusicForm,
} from "./music";
import type { KeyValueStore } from "./options";
import { CAPS, STATUS, makeMusicRun } from "./testdata";
import type { Capabilities, WorkerStatus } from "./types";

const memoryStore = (initial: Record<string, string> = {}): KeyValueStore & { data: Record<string, string> } => {
  const data = { ...initial };
  return { available: true, data, get: (k) => data[k] ?? null, set: (k, v) => { data[k] = v; } };
};

const form = (over: Partial<MusicForm> = {}): MusicForm => ({ ...defaultMusicForm(CAPS), ...over });
const withFields = (fields: Partial<Record<string, string>>, over: Partial<MusicForm> = {}): MusicForm =>
  form({ fields: { ...emptyFields(), ...fields }, ...over });

// ---------------------------------------------------------------- the description
// The same cases as backend/studio/musicprompt.py GOLDEN: both sides must write the same text for the same fields.
const GOLDEN: [Record<string, string>, boolean, string][] = [
  [{ genre: "acoustic pop", mood: "warm and intimate, building gently", bpm: "96", key: "C major",
    instruments: "fingerpicked guitar and soft piano; brushed drums enter in the second half" }, true,
   "Global Metadata\n" +
   "Basic Attributes: bpm is 96. key is C, and scale is major. acoustic pop.\n" +
   "Global Emotional Progression: warm and intimate, building gently.\n" +
   "Instrumental, no vocals.\n" +
   "Arrangement\n" +
   "Instrument Lifecycle Description: fingerpicked guitar and soft piano; brushed drums enter in the second half."],
  [{ genre: "indie folk", mood: "Hopeful.", bpm: "88", key: "F# minor", voice: "soft female lead, breathy", instruments: "acoustic guitar" }, false,
   "Global Metadata\n" +
   "Basic Attributes: bpm is 88. key is F#, and scale is minor. indie folk.\n" +
   "Global Emotional Progression: Hopeful.\n" +
   "Vocal Details\n" +
   "Vocal Gender & Timbre: soft female lead, breathy.\n" +
   "Arrangement\n" +
   "Instrument Lifecycle Description: acoustic guitar."],
  [{ genre: "  lo-fi \n hip hop  ", key: "Bb" }, true,
   "Global Metadata\nBasic Attributes: key is Bb. lo-fi hip hop.\nInstrumental, no vocals."],
  [{}, true, "Global Metadata\nInstrumental, no vocals."],
  [{ voice: "ignored when instrumental", mood: "calm" }, true,
   "Global Metadata\nGlobal Emotional Progression: calm.\nInstrumental, no vocals."],
  [{}, false, "Global Metadata"],
];

describe("the description is written the way the server-side builder writes it", () => {
  it.each(GOLDEN)("%j (instrumental: %s)", (fields, instrumental, expected) => {
    expect(buildDescription(fields, instrumental)).toBe(expected);
  });

  it("a key becomes a key and a scale", () => {
    expect(keySentence("C major")).toBe("key is C, and scale is major.");
    expect(keySentence("F# minor")).toBe("key is F#, and scale is minor.");
    expect(keySentence("Bb Dorian")).toBe("key is Bb, and scale is Dorian.");
    expect(keySentence("A")).toBe("key is A.");
    expect(keySentence("  e   natural minor ")).toBe("key is e, and scale is natural minor.");
    expect(keySentence("")).toBe("");
    expect(keySentence("   ")).toBe("");
  });

  it("a sentence that already ends with punctuation is not given a second full stop", () => {
    expect(buildDescription({ mood: "Wow!" }, true)).toContain("Global Emotional Progression: Wow!\n");
    expect(buildDescription({ mood: "Why?" }, true)).toContain("Global Emotional Progression: Why?\n");
    expect(buildDescription({ mood: "calm" }, true)).toContain("Global Emotional Progression: calm.\n");
  });
});

describe("what the form sends as the description", () => {
  it("what the fields build, until the description is edited by hand", () => {
    const f = withFields({ genre: "jazz" });
    expect(descriptionOf(f)).toBe(buildDescription({ genre: "jazz" }, true));
    expect(descriptionOf({ ...f, edited: true, text: "my own words" })).toBe("my own words");
  });

  it("with lyrics on, the builder leaves the instrumental sentence out and uses the voice", () => {
    const f = withFields({ genre: "folk", voice: "warm baritone" }, { lyricsOn: true });
    expect(descriptionOf(f)).toContain("Vocal Gender & Timbre: warm baritone.");
    expect(descriptionOf(f)).not.toContain("Instrumental, no vocals.");
  });

  it("an empty form is not a description (the builder always writes its heading)", () => {
    expect(hasDescription(form())).toBe(false);
    expect(hasDescription(withFields({ genre: "  " }))).toBe(false);
    expect(hasDescription(withFields({ mood: "calm" }))).toBe(true);
  });

  it("a voice does not count when the track is instrumental, because that box is not on the page", () => {
    expect(hasDescription(withFields({ voice: "soft" }))).toBe(false);
    expect(hasDescription(withFields({ voice: "soft" }, { lyricsOn: true }))).toBe(true);
  });

  it("a hand-written description counts on its own, and an empty one does not", () => {
    expect(hasDescription(form({ edited: true, text: "calm piano" }))).toBe(true);
    expect(hasDescription(form({ edited: true, text: "   " }))).toBe(false);
    expect(hasDescription(withFields({ genre: "jazz" }, { edited: true, text: "" }))).toBe(false); // the edit is what is sent
  });
});

// ---------------------------------------------------------------- problems
describe("what stops a form from being sent", () => {
  const ok = withFields({ genre: "jazz" });
  it("nothing for a good form", () => expect(musicProblem(ok, CAPS)).toBeNull());

  it("nothing to describe", () => expect(musicProblem(form(), CAPS)).toMatch(/Describe the music/));

  it("a description over the limit says by how much it is over", () => {
    const f = form({ edited: true, text: "x".repeat(CAPS.limits.music.description_chars + 1) });
    expect(musicProblem(f, CAPS)).toMatch(/2001 characters; the limit is 2000/);
    expect(musicProblem(form({ edited: true, text: "x".repeat(2000) }), CAPS)).toBeNull(); // exactly at the limit is fine
  });

  it("lyrics on but empty: say so, instead of sending what would be an instrumental track", () => {
    expect(musicProblem({ ...ok, lyricsOn: true, lyrics: "  \n " }, CAPS)).toMatch(/Write the lyrics, or turn Add lyrics off/);
    expect(musicProblem({ ...ok, lyricsOn: true, lyrics: "[Verse]\nla" }, CAPS)).toBeNull();
    expect(musicProblem({ ...ok, lyricsOn: false, lyrics: "" }, CAPS)).toBeNull(); // off: the lyrics box is not used
  });

  it("lyrics over the limit", () => {
    expect(musicProblem({ ...ok, lyricsOn: true, lyrics: "x".repeat(6001) }, CAPS)).toMatch(/6001 characters; the limit is 6000/);
  });

  it("length, versions, steps and a locked seed are checked against the server's limits", () => {
    expect(musicProblem({ ...ok, duration: 9 }, CAPS)).toMatch(/from 10 to 300/);
    expect(musicProblem({ ...ok, duration: 10 }, CAPS)).toBeNull();
    expect(musicProblem({ ...ok, duration: 300 }, CAPS)).toBeNull();
    expect(musicProblem({ ...ok, duration: 301 }, CAPS)).toMatch(/from 10 to 300/);
    expect(musicProblem({ ...ok, duration: 12.5 }, CAPS)).toMatch(/whole number/);
    expect(musicProblem({ ...ok, tracks: 0 }, CAPS)).toMatch(/Versions must be from 1 to 4/);
    expect(musicProblem({ ...ok, tracks: 5 }, CAPS)).toMatch(/Versions must be from 1 to 4/);
    expect(musicProblem({ ...ok, steps: 9 }, CAPS)).toMatch(/steps must be from 10 to 60/);
    expect(musicProblem({ ...ok, steps: 61 }, CAPS)).toMatch(/steps must be from 10 to 60/);
    expect(musicProblem({ ...ok, seedLocked: true, seed: -1 }, CAPS)).toMatch(/seed must be from 0/);
    expect(musicProblem({ ...ok, seedLocked: false, seed: -1 }, CAPS)).toBeNull(); // an unlocked seed is not sent
  });

  it("the server's maximum length is the one that counts (a server set to 120 s refuses 180)", () => {
    const small: Capabilities = { ...CAPS, limits: { ...CAPS.limits, music: { ...CAPS.limits.music, duration: { min: 10, max: 120, default: 60 } } } };
    expect(musicProblem({ ...ok, duration: 180 }, small)).toMatch(/from 10 to 120/);
  });
});

// ---------------------------------------------------------------- saved form
describe("the saved form", () => {
  it("round-trips, and starts from the defaults when nothing is saved", () => {
    const store = memoryStore();
    expect(loadMusicForm(CAPS, store)).toEqual({ form: defaultMusicForm(CAPS), repaired: false });
    const f = withFields({ genre: "jazz", bpm: "120" }, { duration: 30, tracks: 2, seedLocked: true, seed: 7, lyricsOn: true, lyrics: "[Verse]\nla", edited: true, text: "mine" });
    saveMusicForm(f, store);
    expect(store.data[MUSIC_FORM_KEY]).toBeDefined();
    expect(loadMusicForm(CAPS, store)).toEqual({ form: f, repaired: false });
  });

  it("each bad value resets only itself and says so", () => {
    const saved = { ...form(), fields: { ...emptyFields(), genre: "jazz" }, duration: 5, tracks: 9, steps: "many", seed: 1.5, lyricsOn: "yes" };
    const { form: loaded, repaired } = sanitizeMusicForm(saved, CAPS);
    expect(repaired).toBe(true);
    expect(loaded.fields.genre).toBe("jazz"); // untouched
    expect(loaded.duration).toBe(60);
    expect(loaded.tracks).toBe(1);
    expect(loaded.steps).toBe(30);
    expect(loaded.seed).toBe(42);
    expect(loaded.lyricsOn).toBe(false);
  });

  it("a field longer than the server allows, or not a string, resets only that field", () => {
    const { form: loaded, repaired } = sanitizeMusicForm({ fields: { genre: "x".repeat(401), mood: 5, key: "A minor" } }, CAPS);
    expect(repaired).toBe(true);
    expect(loaded.fields).toEqual({ ...emptyFields(), key: "A minor" });
  });

  it("a value added in a later version is simply its default, and is not 'repaired'", () => {
    const { form: loaded, repaired } = sanitizeMusicForm({ fields: { genre: "jazz" } }, CAPS);
    expect(repaired).toBe(false);
    expect(loaded.duration).toBe(60);
  });

  it("garbage or a non-object gives the defaults", () => {
    expect(sanitizeMusicForm("nope", CAPS)).toEqual({ form: defaultMusicForm(CAPS), repaired: true });
    expect(sanitizeMusicForm([1], CAPS)).toEqual({ form: defaultMusicForm(CAPS), repaired: true });
    expect(sanitizeMusicForm(null, CAPS)).toEqual({ form: defaultMusicForm(CAPS), repaired: false });
    expect(loadMusicForm(CAPS, memoryStore({ [MUSIC_FORM_KEY]: "{not json" }))).toEqual({ form: defaultMusicForm(CAPS), repaired: true });
  });

  it("limits that shrank since it was saved are applied: a saved 280 s with a server now set to 120 s is reset", () => {
    const small: Capabilities = { ...CAPS, limits: { ...CAPS.limits, music: { ...CAPS.limits.music, duration: { min: 10, max: 120, default: 60 } } } };
    expect(sanitizeMusicForm({ duration: 280 }, small).form.duration).toBe(60);
  });

  it("an 'edited' description with nothing in it is not edited", () => {
    expect(sanitizeMusicForm({ edited: true, text: "" }, CAPS).form.edited).toBe(false);
  });

  it("the defaults come from the server: its default length, and its smallest number of versions", () => {
    const odd: Capabilities = { ...CAPS, limits: { ...CAPS.limits, music: { ...CAPS.limits.music, duration: { min: 10, max: 300, default: 90 }, steps: { min: 10, max: 60, default: 20 } } } };
    const d = defaultMusicForm(odd);
    expect([d.duration, d.steps, d.tracks, d.seedLocked, d.lyricsOn, d.edited]).toEqual([90, 20, 1, false, false, false]);
  });
});

// ---------------------------------------------------------------- the request
describe("the request", () => {
  it("an instrumental track sends no lyrics at all, and a random seed", () => {
    const body = buildMusicRequest(withFields({ genre: "jazz", mood: "calm" }, { duration: 30, tracks: 2 }));
    expect(body).toEqual({
      mode: "music",
      prompt: buildDescription({ genre: "jazz", mood: "calm" }, true),
      options: { duration: 30, tracks: 2, steps: 30, seed: null, fields: { genre: "jazz", mood: "calm" } },
    });
    expect("lyrics" in body).toBe(false);
  });

  it("with lyrics: the lyrics as written (trimmed), the voice among the fields, and a locked seed", () => {
    const body = buildMusicRequest(withFields({ genre: "folk", voice: "warm", bpm: "  " }, { lyricsOn: true, lyrics: "\n[Verse]\nla la\n", seedLocked: true, seed: 99 }));
    expect(body.lyrics).toBe("[Verse]\nla la");
    expect(body.options.seed).toBe(99);
    expect(body.options.fields).toEqual({ genre: "folk", voice: "warm" }); // empty ones are left out
  });

  it("the voice is not sent for an instrumental track, even if the box once had text", () => {
    expect(buildMusicRequest(withFields({ genre: "folk", voice: "warm" })).options.fields).toEqual({ genre: "folk" });
  });

  it("a hand-written description is sent as it is, trimmed", () => {
    expect(buildMusicRequest(withFields({ genre: "folk" }, { edited: true, text: "  my text\n" })).prompt).toBe("my text");
  });

  it("an unlocked seed is null whatever the box says", () => {
    expect(buildMusicRequest(withFields({ genre: "x" }, { seed: 5, seedLocked: false })).options.seed).toBeNull();
  });

  it("retry sends the same thing again, the same seed included", () => {
    const run = makeMusicRun({ prompt: "P", lyrics: "[Verse]\nla", options: { duration: 45, steps: 20, seed: 321, seed_was_random: true, tracks: 3, instrumental: false, fields: { genre: "pop" } } });
    expect(musicRetryRequest(run)).toEqual({ mode: "music", prompt: "P", lyrics: "[Verse]\nla", options: { duration: 45, tracks: 3, steps: 20, seed: 321, fields: { genre: "pop" } } });
    const instrumental = makeMusicRun({ lyrics: null });
    expect("lyrics" in musicRetryRequest(instrumental)).toBe(false);
  });
});

// ---------------------------------------------------------------- reuse
describe("Reuse", () => {
  const current = form({ duration: 15, tracks: 1, seedLocked: false, seed: 1 });

  it("brings back the fields, the settings, and the seed, locked", () => {
    const fields = { genre: "ambient", mood: "slow" };
    const run = makeMusicRun({ prompt: buildDescription(fields, true), options: { duration: 120, steps: 40, seed: 555, seed_was_random: true, tracks: 3, instrumental: true, fields } });
    const reused = musicFormFromRun(run, CAPS, current);
    expect(reused.fields).toEqual({ ...emptyFields(), ...fields });
    expect([reused.duration, reused.steps, reused.tracks, reused.seedLocked, reused.seed]).toEqual([120, 40, 3, true, 555]);
    expect(reused.lyricsOn).toBe(false);
    expect(reused.edited).toBe(false); // the text is exactly what the fields build
    expect(descriptionOf(reused)).toBe(run.prompt);
  });

  it("a description that is not what its fields build comes back as the hand-written one, word for word", () => {
    const run = makeMusicRun({ prompt: "Something I wrote myself\nin two lines", options: { duration: 60, steps: 30, seed: 1, seed_was_random: false, tracks: 1, instrumental: true, fields: { genre: "ambient" } } });
    const reused = musicFormFromRun(run, CAPS, current);
    expect(reused.edited).toBe(true);
    expect(descriptionOf(reused)).toBe("Something I wrote myself\nin two lines");
    expect(reused.fields.genre).toBe("ambient"); // the boxes are restored too, for Rebuild
  });

  it("a run with lyrics turns Add lyrics on, with the lyrics and the voice", () => {
    const fields = { genre: "folk", voice: "soft alto" };
    const run = makeMusicRun({ lyrics: "[Chorus]\nla la", prompt: buildDescription(fields, false), options: { duration: 60, steps: 30, seed: 3, seed_was_random: false, tracks: 1, instrumental: false, fields } });
    const reused = musicFormFromRun(run, CAPS, current);
    expect([reused.lyricsOn, reused.lyrics, reused.fields.voice, reused.edited]).toEqual([true, "[Chorus]\nla la", "soft alto", false]);
  });

  it("an instrumental run does not erase the lyrics a person was writing", () => {
    const writing = form({ lyrics: "half-written verse" });
    const reused = musicFormFromRun(makeMusicRun(), CAPS, writing);
    expect(reused.lyricsOn).toBe(false);
    expect(reused.lyrics).toBe("half-written verse");
  });

  it("settings that today's limits no longer allow are clamped to their defaults instead of failing", () => {
    const small: Capabilities = { ...CAPS, limits: { ...CAPS.limits, music: { ...CAPS.limits.music, duration: { min: 10, max: 120, default: 60 }, tracks: { min: 1, max: 2 } } } };
    const run = makeMusicRun({ options: { duration: 280, steps: 30, seed: 3, seed_was_random: false, tracks: 4, instrumental: true, fields: { genre: "x" } } });
    const reused = musicFormFromRun(run, small, current);
    expect([reused.duration, reused.tracks]).toEqual([60, 1]);
  });
});

// ---------------------------------------------------------------- section tags
describe("section tags go on a line of their own", () => {
  const tag = (text: string, from: number, to: number = from) => insertTag(text, from, to, "Verse", 6000);

  it("into an empty box", () => expect(tag("", 0)).toEqual({ text: "[Verse]\n", caret: 8 }));

  it("at the end of a line: a line break first, so the tag does not share the line with the words", () => {
    expect(tag("la la", 5)).toEqual({ text: "la la\n[Verse]\n", caret: 14 });
  });

  it("at the start of a line: no extra line break", () => {
    expect(tag("la la\n", 6)).toEqual({ text: "la la\n[Verse]\n", caret: 14 });
  });

  it("in the middle of a line, the rest of the line moves below the tag", () => {
    expect(tag("one two", 3)).toEqual({ text: "one\n[Verse]\n two", caret: 12 });
  });

  it("before a line break, that line break becomes the one after the tag (no blank line)", () => {
    expect(tag("one\ntwo", 3)).toEqual({ text: "one\n[Verse]\ntwo", caret: 12 });
  });

  it("over a selection, which it replaces", () => {
    expect(tag("one XX two", 4, 6)).toEqual({ text: "one \n[Verse]\n two", caret: 13 });
  });

  it("refuses to go over the limit", () => {
    expect(insertTag("x".repeat(5990), 5990, 5990, "Pre-Chorus", 6000)).toBeNull();
    expect(insertTag("x".repeat(100), 100, 100, "Verse", 109)?.text.length).toBe(109); // exactly at the limit: fine
    expect(insertTag("x".repeat(100), 100, 100, "Verse", 108)).toBeNull();
  });

  it("the tags are the ones the model's card lists", () => {
    expect([...SECTION_TAGS]).toEqual(["Intro", "Verse", "Pre-Chorus", "Chorus", "Post-Chorus", "Bridge", "Solo", "Instrumental", "Outro"]);
  });
});

// ---------------------------------------------------------------- words
describe("lengths in words and on a clock", () => {
  it("durationText", () => {
    expect([15, 59, 60, 90, 120, 300, 61].map(durationText)).toEqual(["15 s", "59 s", "1 min", "1 min 30 s", "2 min", "5 min", "1 min 1 s"]);
  });
  it("clock", () => {
    expect([0, 47, 59.6, 60, 65, 600, 605.4].map(clock)).toEqual(["0:00", "0:47", "1:00", "1:00", "1:05", "10:00", "10:05"]);
    expect(clock(-3)).toBe("0:00");
  });
  it("the chips are the shortcuts that fit the server's range", () => {
    expect([...DURATION_CHIPS]).toEqual([15, 30, 60, 120, 180, 300]);
    expect(chipsFor({ min: 10, max: 300 })).toEqual([15, 30, 60, 120, 180, 300]);
    expect(chipsFor({ min: 10, max: 120 })).toEqual([15, 30, 60, 120]);
    expect(chipsFor({ min: 20, max: 200 })).toEqual([30, 60, 120, 180]);
  });
  it("a track's label says which version it is and when the model stopped short of what was allowed", () => {
    expect(trackLabel(0, 1, 60, 60)).toBe("Track · 1:00");
    expect(trackLabel(1, 3, 47.2, 60)).toBe("Version 2 of 3 · 0:47 (you allowed up to 1:00)");
    expect(trackLabel(0, 2, 60.4, 60)).toBe("Version 1 of 2 · 1:00");
  });
  it("seeds and the line of settings under a card", () => {
    const one = makeMusicRun();
    expect(musicSeedText(one)).toBe("seed 7");
    const three = makeMusicRun({ options: { ...one.options, tracks: 3, seed: 10 } });
    expect(musicSeedText(three)).toBe("seeds 10–12");
    expect(musicMeta(one, null)).toEqual(["up to 1 min", "30 steps", "seed 7"]);
    expect(musicMeta(three, "4m 05s")).toEqual(["up to 1 min", "3 versions", "30 steps", "seeds 10–12", "4m 05s"]);
  });
  it("a card's title is the genre, else the mood or instruments, else the start of the description", () => {
    const base = makeMusicRun();
    expect(musicTitle(base)).toBe("ambient");
    expect(musicTitle({ ...base, options: { ...base.options, fields: { mood: "slow" } } })).toBe("slow");
    expect(musicTitle({ ...base, options: { ...base.options, fields: {} }, prompt: "Global Metadata\nBasic Attributes: key is C." })).toBe("Basic Attributes: key is C.");
  });
});

// ---------------------------------------------------------------- progress
describe("the two phases of making a track", () => {
  const progress = (stage: "compose" | "render" | "finish" | undefined, step: number, steps: number) => ({ image: 1, of: 1, step, steps, ...(stage ? { stage } : {}) });

  it("composing fills the first bar and leaves the second empty", () => {
    expect(musicPhases(progress("compose", 250, 1000)).map((p) => p.fraction)).toEqual([0.25, 0]);
  });
  it("rendering has the first bar full (composing is over, even if the model stopped before the length allowed) and fills the second", () => {
    expect(musicPhases(progress("render", 15, 30)).map((p) => p.fraction)).toEqual([1, 0.5]);
  });
  it("finishing fills both", () => {
    expect(musicPhases(progress("finish", 0, 1)).map((p) => p.fraction)).toEqual([1, 1]);
  });
  it("before any progress both bars are empty, and a step past the total never overfills", () => {
    expect(musicPhases(null).map((p) => p.fraction)).toEqual([0, 0]);
    expect(musicPhases(progress("compose", 2000, 1000)).map((p) => p.fraction)).toEqual([1, 0]);
  });
  it("the bars are named for what they do", () => {
    expect(musicPhases(null).map((p) => p.label)).toEqual(["Composing", "Rendering"]);
  });

  it("the words under the bars", () => {
    expect(musicProgressText(null, false)).toBe("Starting…");
    expect(musicProgressText(null, true)).toBe("Stopping…");
    expect(musicProgressText(progress("compose", 250, 1000), false)).toBe("composing, 25%");
    expect(musicProgressText(progress("render", 12, 30), false)).toBe("rendering, step 12 of 30");
    expect(musicProgressText(progress("finish", 0, 1), false)).toBe("finishing the sound…");
    expect(musicProgressText({ image: 2, of: 3, step: 5, steps: 30, stage: "render" }, true)).toBe("Version 2 of 3 · rendering, step 5 of 30 · stopping at the next step…");
  });
});

describe("what the Music tab says about the model", () => {
  const w = (state: WorkerStatus["state"], model: WorkerStatus["model"]): WorkerStatus => ({ ...STATUS.worker, state, model });
  it("nothing when the music model is loaded", () => {
    expect(musicModelNote(w("ready", "music"))).toBeNull();
    expect(musicModelNote(w("busy", "music"))).toBeNull();
  });
  it("a track sent while it loads waits for it", () => expect(musicModelNote(w("loading", "music"))).toMatch(/waits for it/));
  it("with the image model loaded it says the music model replaces it", () => expect(musicModelNote(w("ready", "image"))).toMatch(/replaces it/));
  it("when nothing is loaded it says the first track loads the model", () => {
    expect(musicModelNote(w("unloaded", "image"))).toMatch(/isn't loaded/);
    expect(musicModelNote(w("error", "music"))).toMatch(/isn't loaded/);
  });
});

// ---------------------------------------------------------------- tabs
describe("tabs", () => {
  it("the open tab is remembered, and anything else means Images", () => {
    const store = memoryStore();
    expect(readTab(store)).toBe("images");
    saveTab("music", store);
    expect(store.data[TAB_KEY]).toBe("music");
    expect(readTab(store)).toBe("music");
    expect(readTab(memoryStore({ [TAB_KEY]: "video" }))).toBe("images");
  });
  it("arrow keys move between the tabs and wrap, Home and End go to the ends, other keys do nothing", () => {
    expect(tabAfterKey("images", "ArrowRight")).toBe("music");
    expect(tabAfterKey("music", "ArrowRight")).toBe("images");
    expect(tabAfterKey("images", "ArrowLeft")).toBe("music");
    expect(tabAfterKey("music", "ArrowLeft")).toBe("images");
    expect(tabAfterKey("music", "Home")).toBe("images");
    expect(tabAfterKey("images", "End")).toBe("music");
    expect(tabAfterKey("images", "Enter")).toBeNull();
    expect(tabAfterKey("images", "a")).toBeNull();
  });
});
