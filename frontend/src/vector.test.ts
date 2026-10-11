import { describe, expect, it, vi } from "vitest";
import { NO_FILTER, ONLY_DELETED, ONLY_KEPT, filterParams, matches, visibleRuns, withProject } from "./history";
import { binContents, canceledText, deleteProjectQuestion, emptyBinQuestion, emptiedText, projectContents, type BinContents } from "./format";
import { browserStore, type KeyValueStore } from "./options";
import { CAPS, VECTOR_CONTRACT, makeMusicRun, makeRun, makeVectorItem, makeVectorRun } from "./testdata";
import type {
  Capabilities, CreateVectorBody, VectorAvailability, VectorCheck, VectorItem, VectorLayer, VectorLimits, VectorNote, VectorOptionsSnapshot, VectorRecipe,
  VectorRecipeRequest, VectorRun,
} from "./types";
import { isImageRun, isMusicRun, isVectorRun } from "./types";
import {
  DEFAULT_PNG_PX, INK_SUGGESTIONS, VECTOR_FORM_KEY, VECTOR_PNG_RANGE, WHITE, addInk, bleedApplies, buildVectorRequest, bytesText, coloursText, defaultVectorForm,
  editRecipe, effectiveArrangement, inkCount, kindLabel, layersLine, lengthText, loadVectorForm, mmText, normaliseColour, paletteProblem, pngDownloadUrl, pngRange,
  presetMatches, rebuildRecipe, reconcileReusedRecipe, recipeRequest, recipeToSend, removeInk, sanitizeVectorForm, saveVectorForm, setInk, smallestFeatureText,
  unavailableText, vectorFormFromRun, vectorMeta, vectorPhases, vectorProblem, vectorProgressText, vectorRetryRequest, vectorSeedText, vectorTitle, vectorUsable,
  versionLabel, withColours, type VectorForm,
} from "./vector";

// The Vector tab's pure logic (DESIGN.md §31.12.8): the form and where its limits come from, the palette rules, the line about layers, the request, Reuse and
// Retry, the saved form, the counts and the filter for a third kind of run, and the words on a card. The fixture is the contract file the server is tested
// against (backend/tests/vector_contract.json), so a page that drifts from the server fails here.

const memoryStore = (initial: Record<string, string> = {}): KeyValueStore & { data: Record<string, string> } => {
  const data = { ...initial };
  return { available: true, data, get: (key) => data[key] ?? null, set: (key, value) => { data[key] = value; } };
};

/** Capabilities with every Vector limit changed to a number the page cannot have written down: whatever the page does with the limits, it must come from here. */
const ODD: VectorLimits = {
  ...CAPS.limits.vector,
  colours: { min: 3, max: 6, default: 4 },
  size_mm: { min: 10, max: 900, default: [250, 120] },
  versions: { min: 2, max: 3 },
  min_feature_mm: { min: 0.1, max: 5, default: 0.3 },
  kerf_mm: { min: 0.01, max: 0.5, default: 0.02 },
  bleed_mm: { min: 0.2, max: 3, default: 0.7 },
  shape_layer_cap: 12,
  kinds: ["flat", "line"],
  layers_by: ["shape", "colour"],
  arrangements: ["stacked", "side_by_side"],
  presets: [{ id: "bench", name: "Bench", width_mm: 400, height_mm: 250 }],
  defaults: { kind: "line", layers_by: "shape", arrangement: "stacked" },
  recipe_max_chars: 500,
};
const ODD_CAPS: Capabilities = { ...CAPS, limits: { ...CAPS.limits, vector: ODD } };

/** The form the contract's `create_request` describes. */
const FOX: VectorForm = {
  idea: "a fox head", kind: "flat", colours: 4, palette: ["#FF0000", "#00AA00"], widthMm: 100, heightMm: 100, layersBy: "colour", arrangement: "side_by_side",
  minFeatureMm: 0.5, kerfMm: 0, bleedMm: 0.5, numImages: 1, seedLocked: true, seed: 1234, recipeEdited: false, recipeText: "",
};

// ------------------------------------------------------------------ the page and the contract cannot drift apart
// A `Record<keyof T, true>` literal must list every key of the type, and no other (the compiler checks both), so each one below is the type's keys written
// out. The tests compare it with the keys of the contract file, which is what the server sends: a key added on either side fails one of the two.
describe("the types match the contract file", () => {
  const keys = (record: Record<string, true>) => Object.keys(record).sort();
  const same = (record: Record<string, true>, example: object) => expect(keys(record)).toEqual(Object.keys(example).sort());
  const c = VECTOR_CONTRACT;

  it("the run, its options, its versions and their check", () => {
    const run: Record<keyof VectorRun, true> = {
      id: true, status: true, prompt: true, effective_prompt: true, model_id: true, created_at: true, started_at: true, finished_at: true, error: true, pinned: true,
      expires_at: true, deleted_at: true, purge_at: true, project_id: true, queue_position: true, progress: true, canceling: true, mode: true, options: true,
      lyrics: true, inputs: true, images: true, binned_images: true, tracks: true, binned_tracks: true, vectors: true,
    };
    same(run, c.run);
    const options: Record<keyof VectorOptionsSnapshot, true> = {
      colours: true, palette: true, kind: true, width_mm: true, height_mm: true, layers_by: true, arrangement: true, bleed_mm: true, min_feature_mm: true, kerf_mm: true,
      steps: true, seed: true, num_images: true, recipe: true, seed_was_random: true, negative: true, draw_width: true, draw_height: true,
    };
    same(options, c.run.options);
    const item: Record<keyof VectorItem, true> = {
      id: true, idx: true, seed: true, width_mm: true, height_mm: true, colours: true, layers: true, bytes: true, check: true, url: true, download_url: true,
      zip_url: true, png_url: true, png_download_url: true, preview_url: true,
    };
    same(item, c.run.vectors[0]);
    const check: Record<keyof VectorCheck, true> = {
      colours_asked: true, colours_found: true, white: true, size_mm: true, layers_by: true, arrangement: true, svg_bytes: true, layers: true, smallest_feature_mm: true,
      removed: true, mm_per_pixel: true, notes: true,
    };
    same(check, c.run.vectors[0].check);
    const layer: Record<keyof VectorLayer, true> = { index: true, id: true, name: true, colour: true, picture_colour: true, paths: true, length_mm: true };
    same(layer, c.run.vectors[0].check.layers[0]);
    const note: Record<keyof VectorNote, true> = { code: true, message: true };
    same(note, c.run.vectors[0].check.notes[0]);
  });

  it("the request, the recipe and the capabilities", () => {
    const body: Record<keyof CreateVectorBody, true> = { mode: true, prompt: true, options: true };
    same(body, c.create_request);
    const options: Record<keyof CreateVectorBody["options"], true> = {
      colours: true, palette: true, kind: true, width_mm: true, height_mm: true, layers_by: true, arrangement: true, bleed_mm: true, min_feature_mm: true,
      kerf_mm: true, steps: true, seed: true, num_images: true, recipe: true,
    };
    same(options, c.create_request.options);
    const recipeRequest: Record<keyof VectorRecipeRequest, true> = { prompt: true, kind: true, colours: true, palette: true };
    same(recipeRequest, c.recipe_request);
    const recipe: Record<keyof VectorRecipe, true> = { positive: true, negative: true };
    same(recipe, c.recipe_response);
    // `png_px` is the page's own optional extra (the contract does not carry the PNG range): every other key of the type is the contract's
    const limits: Record<Exclude<keyof VectorLimits, "png_px">, true> = {
      colours: true, size_mm: true, versions: true, min_feature_mm: true, kerf_mm: true, bleed_mm: true, shape_layer_cap: true, kinds: true, layers_by: true,
      arrangements: true, presets: true, defaults: true, recipe_max_chars: true,
    };
    same(limits, c.capabilities.limits_vector);
    const availability: Record<keyof VectorAvailability, true> = { available: true, reason: true, hint: true };
    same(availability, c.capabilities.vector);
  });

  it("the test data is the contract's, made into the page's types", () => {
    expect(CAPS.limits.vector).toEqual(c.capabilities.limits_vector);
    expect(CAPS.vector).toEqual(c.capabilities.vector);
    expect(CAPS.modes).toContain("vector");
    const run = makeVectorRun({ id: "x" });
    expect(Object.keys(run).sort()).toEqual(Object.keys(c.run).sort());
    expect(run.vectors[0].check).toEqual(c.run.vectors[0].check);
  });

  it("every kind of run has `vectors`, an empty list unless it is a Vector run (the contract says it is always present)", () => {
    expect(makeRun().vectors).toEqual([]);
    expect(makeMusicRun().vectors).toEqual([]);
    expect(makeVectorRun().vectors).toHaveLength(1);
  });
});

// ------------------------------------------------------------------ the form: defaults and limits come from the capabilities
describe("the form's defaults and limits", () => {
  it("takes every default from the capabilities (the contract's here)", () => {
    expect(defaultVectorForm(CAPS)).toEqual({
      idea: "", kind: "silhouette", colours: 2, palette: [], widthMm: 100, heightMm: 100, layersBy: "colour", arrangement: "side_by_side",
      minFeatureMm: 0.5, kerfMm: 0, bleedMm: 0.5, numImages: 1, seedLocked: false, seed: 42, recipeEdited: false, recipeText: "",
    });
  });

  it("takes them from the capabilities, not from numbers of its own", () => {
    expect(defaultVectorForm(ODD_CAPS)).toEqual({
      idea: "", kind: "line", colours: 4, palette: [], widthMm: 250, heightMm: 120, layersBy: "shape", arrangement: "stacked",
      minFeatureMm: 0.3, kerfMm: 0.02, bleedMm: 0.7, numImages: 2, seedLocked: false, seed: 42, recipeEdited: false, recipeText: "",
    });
  });

  it("checks every field against the capabilities' limits, in words that name them", () => {
    const base: VectorForm = { ...defaultVectorForm(ODD_CAPS), idea: "a fox" };
    expect(vectorProblem(base, ODD_CAPS)).toBeNull();
    const problem = (patch: Partial<VectorForm>) => vectorProblem({ ...base, ...patch }, ODD_CAPS);
    expect(problem({ colours: 2 })).toBe("Colours must be a whole number from 3 to 6.");
    expect(problem({ colours: 7 })).toBe("Colours must be a whole number from 3 to 6.");
    expect(problem({ colours: 4.5 })).toBe("Colours must be a whole number from 3 to 6.");
    expect(problem({ widthMm: 9.9 })).toBe("Each side of the sheet must be from 10 to 900 mm.");
    expect(problem({ heightMm: 901 })).toBe("Each side of the sheet must be from 10 to 900 mm.");
    expect(problem({ widthMm: 10, heightMm: 900 })).toBeNull(); // the ends are allowed
    expect(problem({ minFeatureMm: 0.05 })).toBe("The smallest feature must be from 0.1 to 5 mm.");
    expect(problem({ minFeatureMm: 5.1 })).toBe("The smallest feature must be from 0.1 to 5 mm.");
    expect(problem({ kerfMm: 0 })).toBe("Kerf must be from 0.01 to 0.5 mm.");
    expect(problem({ kerfMm: 0.6 })).toBe("Kerf must be from 0.01 to 0.5 mm.");
    expect(problem({ bleedMm: 0.1 })).toBe("Bleed must be from 0.2 to 3 mm.");
    expect(problem({ bleedMm: 3.5 })).toBe("Bleed must be from 0.2 to 3 mm.");
    expect(problem({ numImages: 1 })).toBe("Versions must be from 2 to 3.");
    expect(problem({ numImages: 4 })).toBe("Versions must be from 2 to 3.");
    expect(problem({ kind: "silhouette" })).toBe("Choose a kind of artwork."); // not one of this server's kinds
    expect(problem({ layersBy: "colour", arrangement: "side_by_side" })).toBeNull();
    expect(vectorProblem({ ...base, seedLocked: true, seed: -1 }, ODD_CAPS)).toBe("The seed must be from 0 to 4294967295.");
    expect(vectorProblem({ ...base, seedLocked: false, seed: -1 }, ODD_CAPS)).toBeNull(); // an unlocked seed is not sent, so it is not checked
    expect(problem({ idea: "x".repeat(8001) })).toBe("The prompt is 8001 characters; the limit is 8000.");
  });

  it("needs an idea", () => {
    expect(vectorProblem(defaultVectorForm(CAPS), CAPS)).toBe("Describe what to make: write your idea in the Prompt box.");
    expect(vectorProblem({ ...defaultVectorForm(CAPS), idea: "   " }, CAPS)).toBe("Describe what to make: write your idea in the Prompt box.");
  });

  it("checks the hand-written recipe: not empty, and within the server's limit", () => {
    const base: VectorForm = { ...defaultVectorForm(ODD_CAPS), idea: "a fox" };
    expect(vectorProblem({ ...base, recipeEdited: true, recipeText: "  " }, ODD_CAPS)).toMatch(/words the studio sends are empty/);
    expect(vectorProblem({ ...base, recipeEdited: true, recipeText: "x".repeat(501) }, ODD_CAPS)).toBe("The words the studio sends are 501 characters; the limit is 500.");
    expect(vectorProblem({ ...base, recipeEdited: true, recipeText: "x".repeat(500) }, ODD_CAPS)).toBeNull();
    expect(vectorProblem({ ...base, recipeEdited: false, recipeText: "" }, ODD_CAPS)).toBeNull(); // not edited: the text is not used, so it is not checked
  });

  it("refuses a palette the server would refuse, before it is sent", () => {
    const base: VectorForm = { ...FOX };
    expect(vectorProblem({ ...base, palette: ["#FF0000", "#FF0000"] }, CAPS)).toBe("#FF0000 is listed twice.");
    expect(vectorProblem({ ...base, palette: ["#FFFFFF"] }, CAPS)).toMatch(/White is the bare material/);
    expect(vectorProblem({ ...base, colours: 2, palette: ["#FF0000", "#00AA00"] }, CAPS)).toBe("At most 1 colour besides white: white is always included and counts.");
  });

  it("knows whether Vector can run: the server says so and lists the mode, and says why not in its own words", () => {
    expect(vectorUsable(CAPS)).toBe(true);
    expect(vectorUsable({ ...CAPS, vector: { available: false, reason: "no libraries", hint: "pip install" } })).toBe(false);
    expect(vectorUsable({ ...CAPS, modes: ["generate", "edit", "music"] })).toBe(false); // available but not listed: the image pipeline cannot generate
    expect(unavailableText({ ...CAPS, vector: { available: false, reason: "The Vector libraries are not installed here (vtracer).", hint: "Rebuild the image." } }))
      .toEqual({ reason: "The Vector libraries are not installed here (vtracer).", hint: "Rebuild the image." });
    expect(unavailableText({ ...CAPS, vector: { available: false, reason: null, hint: null } })).toEqual({ reason: "Vector can't run on this server as it is set up.", hint: null });
  });
});

// ------------------------------------------------------------------ the palette: white is never in it
describe("the palette", () => {
  it("has room for the colours less white: with N colours there are N - 1 inks", () => {
    expect([2, 3, 8].map(inkCount)).toEqual([1, 2, 7]);
    expect(inkCount(1)).toBe(0);
    expect(inkCount(0)).toBe(0); // never negative
  });

  it("writes a colour as the server wants it, and nothing else is a colour", () => {
    expect(normaliseColour("#ff00aa")).toBe("#FF00AA");
    expect(normaliseColour("#FF00AA")).toBe("#FF00AA");
    for (const bad of ["ff00aa", "#ff00a", "#ff00aaa", "#gg00aa", "red", "", null, undefined, 5]) expect(normaliseColour(bad)).toBeNull();
  });

  it("never lets white in: a colour input answering white is refused with the reason and the palette stays as it was", () => {
    const palette = ["#FF0000", "#00AA00"];
    for (const white of ["#ffffff", "#FFFFFF"]) {
      const next = setInk(palette, 0, white);
      expect(next.palette).toEqual(palette);
      expect(next.refused).toBe("White is the bare material and has no layer. Pick another colour.");
    }
    expect(WHITE).toBe("#FFFFFF");
    expect(INK_SUGGESTIONS).not.toContain(WHITE); // nor is it ever offered
  });

  it("refuses a colour another ink already has, but lets an ink keep its own colour", () => {
    const palette = ["#FF0000", "#00AA00"];
    expect(setInk(palette, 1, "#ff0000")).toEqual({ palette, refused: "#FF0000 is already one of the inks. Pick another colour." });
    expect(setInk(palette, 0, "#ff0000")).toEqual({ palette, refused: null }); // the same colour again is not a clash with itself
  });

  it("changes one ink and leaves the others, in capitals", () => {
    expect(setInk(["#FF0000", "#00AA00"], 1, "#0000ff")).toEqual({ palette: ["#FF0000", "#0000FF"], refused: null });
    expect(setInk(["#FF0000"], 0, "not a colour")).toEqual({ palette: ["#FF0000"], refused: null }); // nothing to change
    expect(setInk(["#FF0000"], 3, "#0000ff").palette).toEqual(["#FF0000"]); // an ink that is not there
    expect(setInk(["#FF0000"], -1, "#0000ff").palette).toEqual(["#FF0000"]);
  });

  it("adds an ink with the first suggested colour not in use, and never more inks than there is room for", () => {
    expect(addInk([], 4)).toEqual([INK_SUGGESTIONS[0]]);
    expect(addInk(["#000000"], 4)).toEqual(["#000000", INK_SUGGESTIONS[1]]);
    expect(addInk(["#FF0000"], 4)).toEqual(["#FF0000", "#000000"]); // skips what is taken, not what comes first by position
    expect(addInk(["#000000", "#FF0000"], 3)).toEqual(["#000000", "#FF0000"]); // 3 colours = 2 inks: full
    expect(addInk([], 1)).toEqual([]);
    // with every ink chosen there is always a free suggestion left for the last one (7 inks, 8 suggestions)
    let palette: string[] = [];
    for (let i = 0; i < 7; i++) palette = addInk(palette, 8);
    expect(new Set(palette).size).toBe(7);
    expect(paletteProblem(palette, 8)).toBeNull();
  });

  it("lets the picture choose an ink again: its colour leaves the palette", () => {
    expect(removeInk(["#FF0000", "#00AA00", "#0000FF"], 1)).toEqual(["#FF0000", "#0000FF"]);
    expect(removeInk(["#FF0000"], 5)).toEqual(["#FF0000"]);
  });

  it("keeps the first colours that still fit when the number of colours goes down, and every other field as it was", () => {
    const form = { ...FOX, colours: 4, palette: ["#FF0000", "#00AA00", "#0000FF"] };
    expect(withColours(form, 3)).toEqual({ ...form, colours: 3, palette: ["#FF0000", "#00AA00"] });
    expect(withColours(form, 2).palette).toEqual(["#FF0000"]);
    expect(withColours(form, 5)).toEqual({ ...form, colours: 5 }); // more colours leave room: nothing is added
  });

  it("is checked the way the server checks it", () => {
    expect(paletteProblem(["#FF0000", "#00AA00"], 4)).toBeNull();
    expect(paletteProblem([], 2)).toBeNull();
    expect(paletteProblem(["#FF0000", "#00AA00"], 2)).toBe("At most 1 colour besides white: white is always included and counts.");
    expect(paletteProblem(["#FF0000", "#00AA00", "#0000FF"], 3)).toBe("At most 2 colours besides white: white is always included and counts.");
    expect(paletteProblem(["#ff0000"], 4)).toBe("#ff0000 is not a colour: it must be written #RRGGBB in capitals.");
    expect(paletteProblem(["#FFFFFF"], 4)).toBe("White is the bare material and has no layer: list only the other colours.");
    expect(paletteProblem(["#FF0000", "#FF0000"], 4)).toBe("#FF0000 is listed twice.");
  });
});

// ------------------------------------------------------------------ the line that says how many layers there will be
describe("the layers line", () => {
  it("by colour: at most one layer for each colour but white (white counts toward the number and gets none)", () => {
    expect(layersLine({ colours: 2, layersBy: "colour" }, CAPS.limits.vector)).toBe("1 layer. White, the bare material, gets none.");
    expect(layersLine({ colours: 3, layersBy: "colour" }, CAPS.limits.vector)).toBe("Up to 2 layers, one for each ink. White, the bare material, gets none.");
    expect(layersLine({ colours: 8, layersBy: "colour" }, CAPS.limits.vector)).toBe("Up to 7 layers, one for each ink. White, the bare material, gets none.");
  });

  it("by shape: up to the cap the server gives, one layer for each separate piece", () => {
    expect(layersLine({ colours: 4, layersBy: "shape" }, CAPS.limits.vector)).toBe("Up to 30 pieces: one layer for each separate piece. White, the bare material, gets none.");
    expect(layersLine({ colours: 4, layersBy: "shape" }, ODD)).toBe("Up to 12 pieces: one layer for each separate piece. White, the bare material, gets none.");
  });
});

// ------------------------------------------------------------------ what is greyed
describe("what is greyed", () => {
  it("by shape the arrangement is always side by side, whatever the control says", () => {
    expect(effectiveArrangement({ layersBy: "shape", arrangement: "stacked" })).toBe("side_by_side");
    expect(effectiveArrangement({ layersBy: "shape", arrangement: "side_by_side" })).toBe("side_by_side");
    expect(effectiveArrangement({ layersBy: "colour", arrangement: "stacked" })).toBe("stacked");
    expect(effectiveArrangement({ layersBy: "colour", arrangement: "side_by_side" })).toBe("side_by_side");
  });

  it("bleed is used only when the layers are stacked by colour", () => {
    expect(bleedApplies({ layersBy: "colour", arrangement: "stacked" })).toBe(true);
    expect(bleedApplies({ layersBy: "colour", arrangement: "side_by_side" })).toBe(false);
    expect(bleedApplies({ layersBy: "shape", arrangement: "stacked" })).toBe(false); // by shape they are side by side
  });

  it("a size matches a preset only when both sides do", () => {
    const preset = { width_mm: 600, height_mm: 307 };
    expect(presetMatches({ widthMm: 600, heightMm: 307 }, preset)).toBe(true);
    expect(presetMatches({ widthMm: 600, heightMm: 306 }, preset)).toBe(false);
    expect(presetMatches({ widthMm: 599, heightMm: 307 }, preset)).toBe(false);
  });
});

// ------------------------------------------------------------------ the recipe: the page shows what the server writes, and edits are the person's
describe("the recipe box", () => {
  it("asks for the words only when there is an idea, with exactly the contract's keys", () => {
    expect(recipeRequest({ ...FOX })).toEqual(VECTOR_CONTRACT.recipe_request);
    expect(recipeRequest({ ...FOX, idea: "" })).toBeNull();
    expect(recipeRequest({ ...FOX, idea: "  \n " })).toBeNull();
    expect(recipeRequest({ ...FOX, idea: "  a fox head  " })?.prompt).toBe("a fox head"); // trimmed
  });

  it("changes what is asked when the kind, the colours or the palette change, and for nothing else", () => {
    const asked = (patch: Partial<VectorForm>) => JSON.stringify(recipeRequest({ ...FOX, ...patch }));
    expect(asked({ kind: "line" })).not.toBe(asked({}));
    expect(asked({ colours: 5 })).not.toBe(asked({}));
    expect(asked({ palette: ["#FF0000"] })).not.toBe(asked({}));
    expect(asked({ widthMm: 50, heightMm: 20, layersBy: "shape", numImages: 3, seed: 9, kerfMm: 0.1 })).toBe(asked({}));
  });

  it("sends nothing of its own until the box is edited: the server builds the words (null)", () => {
    expect(recipeToSend({ recipeEdited: false, recipeText: "left over text" })).toBeNull();
    expect(recipeToSend({ recipeEdited: true, recipeText: "My own words." })).toBe("My own words.");
  });

  it("an edit marks the box as edited with the text, and Rebuild takes it away again", () => {
    const edited = editRecipe(FOX, "My own words.");
    expect(edited).toEqual({ ...FOX, recipeEdited: true, recipeText: "My own words." });
    expect(rebuildRecipe(edited)).toEqual({ ...FOX, recipeEdited: false, recipeText: "" });
  });

  it("after Reuse, keeps the run's words as an edit only if they are not what the server writes now", () => {
    expect(reconcileReusedRecipe(FOX, "A fox head, flat colour artwork", "A fox head, flat colour artwork")).toBe(FOX); // the run used the server's own words
    expect(reconcileReusedRecipe(FOX, "My own words.", "A fox head, flat colour artwork")).toEqual({ ...FOX, recipeEdited: true, recipeText: "My own words." });
    // the server's words could not be had: what the run used is what it keeps
    expect(reconcileReusedRecipe(FOX, "My own words.", null)).toEqual({ ...FOX, recipeEdited: true, recipeText: "My own words." });
  });
});

// ------------------------------------------------------------------ the request
describe("the request", () => {
  it("is exactly the contract's create_request for the form that describes it", () => {
    expect(buildVectorRequest(FOX, CAPS)).toEqual(VECTOR_CONTRACT.create_request);
    // ... down to the order of the keys the server's examples use, so that a request is read the same way by a person
    expect(Object.keys(buildVectorRequest(FOX, CAPS).options)).toEqual(Object.keys(VECTOR_CONTRACT.create_request.options));
  });

  it("sends the seed only when it is locked, and the model's own steps (the form has no box for them)", () => {
    expect(buildVectorRequest({ ...FOX, seedLocked: false }, CAPS).options.seed).toBeNull();
    expect(buildVectorRequest({ ...FOX, seedLocked: true, seed: 7 }, CAPS).options.seed).toBe(7);
    expect(buildVectorRequest(FOX, { ...CAPS, defaults: { ...CAPS.defaults, steps: 25 } }).options.steps).toBe(25);
  });

  it("sends the hand-written words as `recipe`, and null otherwise", () => {
    expect(buildVectorRequest({ ...FOX, recipeEdited: true, recipeText: "My own words." }, CAPS).options.recipe).toBe("My own words.");
    expect(buildVectorRequest({ ...FOX, recipeEdited: false, recipeText: "stale" }, CAPS).options.recipe).toBeNull();
  });

  it("trims the prompt, which is the idea only (the recipe is not part of it)", () => {
    expect(buildVectorRequest({ ...FOX, idea: "  a fox head \n" }, CAPS).prompt).toBe("a fox head");
  });

  it("sends side by side by shape, so that the run's record says what was done, and what was chosen otherwise", () => {
    expect(buildVectorRequest({ ...FOX, layersBy: "shape", arrangement: "stacked" }, CAPS).options.arrangement).toBe("side_by_side");
    expect(buildVectorRequest({ ...FOX, layersBy: "colour", arrangement: "stacked" }, CAPS).options.arrangement).toBe("stacked");
  });

  it("sends the values as they stand, each under its own key", () => {
    const form: VectorForm = {
      idea: "x", kind: "line", colours: 6, palette: ["#0000FF", "#FFA500", "#800080"], widthMm: 321.5, heightMm: 12.25, layersBy: "shape", arrangement: "stacked",
      minFeatureMm: 0.75, kerfMm: 0.125, bleedMm: 2.5, numImages: 3, seedLocked: true, seed: 99, recipeEdited: false, recipeText: "",
    };
    expect(buildVectorRequest(form, CAPS)).toEqual({
      mode: "vector", prompt: "x",
      options: {
        colours: 6, palette: ["#0000FF", "#FFA500", "#800080"], kind: "line", width_mm: 321.5, height_mm: 12.25, layers_by: "shape", arrangement: "side_by_side",
        bleed_mm: 2.5, min_feature_mm: 0.75, kerf_mm: 0.125, steps: 40, seed: 99, num_images: 3, recipe: null,
      },
    });
  });

  it("does not share the palette with the form (a later change to the form cannot change a request already built)", () => {
    const form = { ...FOX, palette: ["#FF0000"] };
    const body = buildVectorRequest(form, CAPS);
    expect(body.options.palette).not.toBe(form.palette);
    expect(recipeRequest(form)?.palette).not.toBe(form.palette);
  });

  it("Retry is the same request again: the same seed, and the words the run used", () => {
    const run = makeVectorRun();
    const body = vectorRetryRequest(run);
    expect(body).toEqual({
      mode: "vector", prompt: "a fox head",
      options: {
        colours: 4, palette: ["#FF0000", "#00AA00"], kind: "flat", width_mm: 100, height_mm: 100, layers_by: "colour", arrangement: "side_by_side", bleed_mm: 0.5,
        min_feature_mm: 0.5, kerf_mm: 0, steps: 40, seed: 1234, num_images: 1, recipe: "A fox head, flat colour artwork ...",
      },
    });
    // it has the keys of a first request, no more and no fewer
    expect(Object.keys(body.options)).toEqual(Object.keys(VECTOR_CONTRACT.create_request.options));
  });
});

// ------------------------------------------------------------------ Reuse
describe("Reuse", () => {
  // a run that differs from the form's defaults in every field, so that a field Reuse forgot would show
  const run = makeVectorRun({
    prompt: "an owl", options: {
      ...makeVectorRun().options, colours: 6, palette: ["#0000FF", "#FFA500"], kind: "line", width_mm: 321.5, height_mm: 45, layers_by: "shape", arrangement: "stacked",
      bleed_mm: 1.5, min_feature_mm: 0.8, kerf_mm: 0.2, num_images: 3, seed: 555, recipe: "The owl, in words.",
    },
  });

  it("restores every field of the form from the run's options, with the seed locked", () => {
    const current: VectorForm = { ...defaultVectorForm(CAPS), idea: "something else", recipeEdited: true, recipeText: "old edit", seedLocked: false, seed: 1 };
    expect(vectorFormFromRun(run, CAPS, current)).toEqual({
      idea: "an owl", kind: "line", colours: 6, palette: ["#0000FF", "#FFA500"], widthMm: 321.5, heightMm: 45, layersBy: "shape", arrangement: "stacked",
      minFeatureMm: 0.8, kerfMm: 0.2, bleedMm: 1.5, numImages: 3, seedLocked: true, seed: 555, recipeEdited: false, recipeText: "",
    });
  });

  it("clamps what no longer fits today's limits instead of loading something the server would refuse", () => {
    const form = vectorFormFromRun(run, ODD_CAPS, defaultVectorForm(ODD_CAPS));
    // 6 colours, 3 versions and kind "line" fit ODD; a width of 321.5 does; a min feature of 0.8 does; kerf 0.2 does; bleed 1.5 does
    expect(form).toMatchObject({ colours: 6, numImages: 3, kind: "line", widthMm: 321.5 });
    const tooBig = makeVectorRun({ options: { ...run.options, colours: 8, num_images: 4, width_mm: 1000, kerf_mm: 0.9, kind: "silhouette" } });
    const clamped = vectorFormFromRun(tooBig, ODD_CAPS, defaultVectorForm(ODD_CAPS));
    expect(clamped).toMatchObject({ colours: 4, numImages: 2, widthMm: 250, kerfMm: 0.02, kind: "line" }); // each out-of-range field takes today's default
  });

  it("keeps the fields of the current form that a run does not carry", () => {
    const current = { ...defaultVectorForm(CAPS), seed: 3 };
    expect(vectorFormFromRun(run, CAPS, current).seed).toBe(555); // the run's, locked
  });
});

// ------------------------------------------------------------------ what the browser remembers
describe("the saved form", () => {
  it("is saved and read back whole", () => {
    const store = memoryStore();
    const form: VectorForm = { ...FOX, recipeEdited: true, recipeText: "My words." };
    saveVectorForm(form, store);
    expect(JSON.parse(store.data[VECTOR_FORM_KEY])).toEqual(form);
    expect(loadVectorForm(CAPS, store)).toEqual({ form, repaired: false });
  });

  it("is the default form when nothing is saved, and when what is saved is not JSON or not an object", () => {
    expect(loadVectorForm(CAPS, memoryStore())).toEqual({ form: defaultVectorForm(CAPS), repaired: false });
    expect(loadVectorForm(CAPS, memoryStore({ [VECTOR_FORM_KEY]: "{not json" }))).toEqual({ form: defaultVectorForm(CAPS), repaired: true });
    expect(loadVectorForm(CAPS, memoryStore({ [VECTOR_FORM_KEY]: "[1,2]" }))).toEqual({ form: defaultVectorForm(CAPS), repaired: true });
    expect(loadVectorForm(CAPS, memoryStore({ [VECTOR_FORM_KEY]: "7" }))).toEqual({ form: defaultVectorForm(CAPS), repaired: true });
    expect(loadVectorForm(CAPS, memoryStore({ [VECTOR_FORM_KEY]: "null" }))).toEqual({ form: defaultVectorForm(CAPS), repaired: false });
  });

  // Each saved value is checked on its own: a bad one resets itself and nothing else. One case for each field, with a value the limits refuse.
  const defaults = defaultVectorForm(CAPS);
  const BAD: [keyof VectorForm, unknown][] = [
    ["idea", 5], ["idea", "x".repeat(8001)], ["kind", "photo"], ["colours", 1], ["colours", 9], ["colours", 3.5], ["palette", "red"], ["palette", ["#ff0000"]],
    ["palette", ["#FFFFFF"]], ["palette", ["#FF0000", "#FF0000"]], ["widthMm", 4], ["widthMm", 1201], ["widthMm", "100"], ["heightMm", 0], ["heightMm", null],
    ["layersBy", "size"], ["arrangement", "piled"], ["minFeatureMm", 0.01], ["minFeatureMm", 11], ["kerfMm", -1], ["kerfMm", 2], ["bleedMm", 6], ["bleedMm", -0.1],
    ["numImages", 0], ["numImages", 5], ["numImages", 1.5], ["seedLocked", "yes"], ["seed", -5], ["seed", 4294967296], ["recipeEdited", 1], ["recipeText", 7],
    ["recipeText", "x".repeat(2001)],
  ];
  it.each(BAD)("a saved %s of %j resets that field only", (field, value) => {
    const saved = { ...FOX, [field]: value };
    const { form, repaired } = sanitizeVectorForm(saved, CAPS);
    expect(repaired).toBe(true);
    expect(form[field]).toEqual(defaults[field]);
    for (const other of Object.keys(FOX) as (keyof VectorForm)[]) {
      // every other field is what was saved, except that a palette is judged with the number of colours (a bad colours count makes the palette wait for its own check)
      if (other === field || (field === "colours" && other === "palette") || (field === "recipeText" && other === "recipeEdited")) continue;
      expect(form[other], `${other} stays`).toEqual(FOX[other]);
    }
  });

  it("keeps a good value at each end of its range", () => {
    const edges: VectorForm = {
      ...FOX, colours: 8, palette: ["#000000", "#FF0000", "#00AA00", "#0000FF", "#FFA500", "#800080", "#A52A2A"], widthMm: 5, heightMm: 1200, minFeatureMm: 0.05, kerfMm: 1,
      bleedMm: 0, numImages: 4, seed: 4294967295, idea: "x".repeat(8000),
    };
    expect(sanitizeVectorForm(edges, CAPS)).toEqual({ form: edges, repaired: false });
    const low: VectorForm = { ...FOX, colours: 2, palette: ["#FF0000"], widthMm: 1200, heightMm: 5, minFeatureMm: 10, kerfMm: 0, bleedMm: 5, numImages: 1, seed: 0 };
    expect(sanitizeVectorForm(low, CAPS)).toEqual({ form: low, repaired: false });
  });

  it("judges the limits of the capabilities it is given, not the contract's", () => {
    const stored = { ...FOX, colours: 8, palette: [], widthMm: 1000 };
    expect(sanitizeVectorForm(stored, CAPS).repaired).toBe(false);
    const odd = sanitizeVectorForm(stored, ODD_CAPS);
    expect(odd.repaired).toBe(true);
    expect(odd.form).toMatchObject({ colours: 4, widthMm: 250 });
  });

  it("takes a field that an older version did not save from the defaults, and says nothing about it", () => {
    const { form, repaired } = sanitizeVectorForm({ idea: "a fox" }, CAPS);
    expect(repaired).toBe(false);
    expect(form).toEqual({ ...defaults, idea: "a fox" });
  });

  it("an empty hand-written recipe is no recipe", () => {
    expect(sanitizeVectorForm({ ...FOX, recipeEdited: true, recipeText: "  " }, CAPS).form.recipeEdited).toBe(false);
    expect(sanitizeVectorForm({ ...FOX, recipeEdited: true, recipeText: "words" }, CAPS).form.recipeEdited).toBe(true);
  });

  it("works with storage blocked: nothing is read, nothing is saved, and nothing throws", () => {
    // the browser's store wraps localStorage in try/catch; with every access throwing, it reports `available: false` and reads nothing
    const blocked = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } };
    vi.stubGlobal("localStorage", blocked);
    try {
      const store = browserStore();
      expect(store.available).toBe(false);
      expect(loadVectorForm(CAPS, store)).toEqual({ form: defaultVectorForm(CAPS), repaired: false });
      expect(() => saveVectorForm(FOX, store)).not.toThrow();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

// ------------------------------------------------------------------ the third kind of run in the history
describe("counts and the filter for the third kind of run", () => {
  it("tells the three kinds of run apart", () => {
    const [image, music, vector] = [makeRun(), makeMusicRun(), makeVectorRun()];
    expect([image, music, vector].map((run) => [isImageRun(run), isMusicRun(run), isVectorRun(run)])).toEqual([[true, false, false], [false, true, false], [false, false, true]]);
    expect(isImageRun(makeRun({ mode: "edit" }))).toBe(true);
  });

  it("a Vector run matches the filters by its own state (it has no pictures of its own in the bin)", () => {
    const plain = makeVectorRun({ pinned: false });
    const kept = makeVectorRun({ pinned: true });
    const binned = makeVectorRun({ deleted_at: "2026-10-02T00:00:00.000Z", purge_at: "2026-11-01T00:00:00.000Z" });
    expect([plain, kept, binned].map((run) => matches(run, NO_FILTER))).toEqual([true, true, false]);
    expect([plain, kept, binned].map((run) => matches(run, ONLY_KEPT))).toEqual([false, true, false]);
    expect([plain, kept, binned].map((run) => matches(run, ONLY_DELETED))).toEqual([false, false, true]);
    // ... and by its project
    const filed = makeVectorRun({ project_id: "a".repeat(32), pinned: true });
    expect(matches(filed, withProject(NO_FILTER, "a".repeat(32)))).toBe(true);
    expect(matches(filed, withProject(NO_FILTER, "none"))).toBe(false);
  });

  it("is sent to the server as the same parameters as for the other kinds (the tab is not a parameter: each tab takes its own kind from the list)", () => {
    expect(filterParams(ONLY_KEPT)).toEqual({ kept: "true" });
    expect(filterParams(withProject(ONLY_DELETED, "a".repeat(32)))).toEqual({ deleted: "true", project: "a".repeat(32) });
  });

  it("shows a Vector run in the list among the others, newest first, and a tab takes its own kind", () => {
    const image = makeRun({ id: "r1", created_at: "2026-10-02T10:00:01.000Z" });
    const vector = makeVectorRun({ id: "v1", created_at: "2026-10-02T10:00:03.000Z" });
    const music = makeMusicRun({ id: "m1", created_at: "2026-10-02T10:00:02.000Z" });
    const runs = { r1: image, v1: vector, m1: music };
    const shown = visibleRuns(runs, ["v1", "m1", "r1"], undefined, NO_FILTER);
    expect(shown.map((run) => run.id)).toEqual(["v1", "m1", "r1"]);
    expect(shown.filter(isVectorRun).map((run) => run.id)).toEqual(["v1"]);
    expect(shown.filter(isImageRun).map((run) => run.id)).toEqual(["r1"]);
    expect(shown.filter(isMusicRun).map((run) => run.id)).toEqual(["m1"]);
  });

  it("counts the bin of the Vector tab as runs only (a Vector run is binned whole)", () => {
    expect(binContents({ image: { deleted: 5, deleted_items: 2 }, music: { deleted: 1, deleted_items: 1 }, vector: { deleted: 4 } })).toEqual({
      image: { runs: 3, items: 2 }, music: { runs: 0, items: 1 }, vector: { runs: 4, items: 0 },
    });
  });

  it("says what Empty bin deletes, with the Vector runs in the total and named by tab", () => {
    const bin = (image: [number, number], music: [number, number], vector: number): BinContents => ({
      image: { runs: image[0], items: image[1] }, music: { runs: music[0], items: music[1] }, vector: { runs: vector, items: 0 },
    });
    // only Vector runs: "2 runs ... (2 on Vector)", as the question has always been worded for runs
    expect(emptyBinQuestion(bin([0, 0], [0, 0], 2), false)).toBe("Delete 2 runs for good (2 on Vector), with their files. This can't be undone.");
    expect(emptyBinQuestion(bin([1, 0], [0, 0], 1), false)).toBe("Delete 2 runs for good (1 on Images, 1 on Vector), with their files. This can't be undone.");
    // with pictures in the bin too, each tab says of what
    expect(emptyBinQuestion(bin([1, 2], [0, 0], 3), false)).toBe("Delete 4 runs and 2 pictures for good (1 run and 2 pictures on Images, 3 runs on Vector), with their files. This can't be undone.");
    expect(emptyBinQuestion(bin([0, 0], [1, 1], 1), true)).toContain("This is the whole bin, not only the project you are looking at.");
    // a bin with nothing of Vector's says nothing of it, and a bin built before Vector (no `vector` part) is read as having none
    expect(emptyBinQuestion(bin([2, 0], [0, 0], 0), false)).toBe("Delete 2 runs for good (2 on Images), with their files. This can't be undone.");
    expect(emptyBinQuestion({ image: { runs: 2, items: 0 }, music: { runs: 0, items: 0 } }, false)).toBe("Delete 2 runs for good (2 on Images), with their files. This can't be undone.");
    expect(emptiedText(3, 0)).toBe("Emptied the bin: 3 runs deleted for good.");
  });

  it("names Vector runs in a project's contents and in the question before a project is deleted", () => {
    expect(projectContents({ image: 3, music: 1, vector: 2 })).toBe("3 pictures, 1 track, 2 vectors");
    expect(projectContents({ image: 0, music: 0, vector: 1 })).toBe("1 vector");
    expect(projectContents({ image: 1, music: 0 })).toBe("1 picture"); // the server's list may not count Vector runs: then nothing is said of them
    expect(projectContents({ image: 0, music: 0, vector: 0 })).toBe("no runs");
    expect(deleteProjectQuestion({ name: "Logo", counts: { image: 1, music: 0, vector: 2 } })).toBe(
      "Delete the project “Logo”? Its 3 runs (1 picture, 2 vectors) are not deleted: they stay kept and are no longer in a project.");
    expect(deleteProjectQuestion({ name: "Only", counts: { image: 0, music: 0, vector: 1 } })).toContain("Its 1 run (1 vector) is not deleted");
  });

  it("says what a canceled Vector run kept, in versions", () => {
    const none = makeVectorRun({ status: "canceled", vectors: [], options: { ...makeVectorRun().options, num_images: 3 } });
    expect(canceledText(none)).toBe("Canceled before any version was finished.");
    const some = makeVectorRun({ status: "canceled", vectors: [makeVectorItem(0)], options: { ...makeVectorRun().options, num_images: 3 } });
    expect(canceledText(some)).toBe("Canceled. 1 of 3 versions finished and kept.");
    const one = makeVectorRun({ status: "canceled", vectors: [makeVectorItem(0)], options: { ...makeVectorRun().options, num_images: 1 } });
    expect(canceledText(one)).toBe("Canceled. 1 of 1 version finished and kept.");
  });
});

// ------------------------------------------------------------------ the words on a card
describe("the words on a card", () => {
  const item = makeVectorItem(0);

  it("writes millimetres without noise, and a cut length in millimetres or metres", () => {
    expect([100, 100.5, 0.62, 12.3456, 99.9999].map(mmText)).toEqual(["100", "100.5", "0.62", "12.346", "100"]);
    expect([412.6, 96.44, 0.04, 999.94, 1000, 12345.6].map(lengthText)).toEqual(["412.6 mm", "96.4 mm", "0 mm", "999.9 mm", "1 m", "12.35 m"]);
  });

  it("says the colours found against the colours asked, and what white is", () => {
    expect(coloursText({ colours_asked: 4, colours_found: 4, white: true })).toBe("4 colours found, 4 asked. White, the bare material, is one of them and has no layer.");
    expect(coloursText({ colours_asked: 4, colours_found: 3, white: true })).toBe("3 colours found, 4 asked. White, the bare material, is one of them and has no layer.");
    expect(coloursText({ colours_asked: 2, colours_found: 1, white: false })).toBe("1 colour found, 2 asked. No white in the picture.");
  });

  it("says the smallest feature against the limit asked for, or that none was found", () => {
    expect(smallestFeatureText(0.62, 0.5)).toBe("Smallest feature: 0.62 mm (your limit is 0.5 mm).");
    expect(smallestFeatureText(null, 0.5)).toBe("Smallest feature: none found (your limit is 0.5 mm).");
  });

  it("names a version by its place when it was asked for, so a gap shows which version failed", () => {
    expect(versionLabel({ idx: 2, seed: 9 }, 3)).toBe("Version 3 of 3");
    expect(versionLabel({ idx: 0, seed: 1234 }, 1)).toBe("Seed 1234");
    expect(versionLabel({ idx: 0, seed: 1234 }, 2)).toBe("Version 1 of 2");
  });

  it("writes a file size", () => {
    expect([999, 1000, 18432, 999_999, 1_000_000, 2_500_000].map(bytesText)).toEqual(["999 B", "1 KB", "18.4 KB", "1000 KB", "1 MB", "2.5 MB"]);
  });

  it("names the kinds of artwork, and a kind it does not know as the server wrote it", () => {
    expect(["silhouette", "flat", "line", "other"].map(kindLabel)).toEqual(["Silhouette or stencil", "Flat colour art", "Line art", "other"]);
  });

  it("writes the settings line of a card, and the seeds of several versions", () => {
    const run = makeVectorRun();
    expect(vectorMeta(run, null)).toEqual(["100 × 100 mm", "4 colours", "flat colour art", "layers by colour, side by side", "seed 1234"]);
    expect(vectorMeta(run, "8s").at(-1)).toBe("8s");
    const shape = makeVectorRun({ options: { ...run.options, layers_by: "shape", arrangement: "stacked", num_images: 3, width_mm: 600, height_mm: 307.5 } });
    expect(vectorMeta(shape, null)).toEqual(["600 × 307.5 mm", "4 colours", "flat colour art", "layers by shape", "seeds 1234–1236"]);
    expect(vectorMeta(makeVectorRun({ options: { ...run.options, arrangement: "stacked" } }), null)[3]).toBe("layers by colour, stacked");
    expect(vectorSeedText(run)).toBe("seed 1234");
  });

  it("shortens a long prompt for the card's accessible name", () => {
    expect(vectorTitle({ prompt: "  a   fox\nhead " })).toBe("a fox head");
    expect(vectorTitle({ prompt: "x".repeat(200) })).toBe(`${"x".repeat(79)}…`);
    expect(vectorTitle({ prompt: "x".repeat(80) })).toBe("x".repeat(80));
  });

  it("builds the PNG link from the server's own, with the size added", () => {
    expect(pngDownloadUrl(item, 2048)).toBe(`/api/vectors/${item.id}/png?download=1&size=2048`);
    expect(pngDownloadUrl({ png_download_url: "/api/vectors/x/png" }, 16)).toBe("/api/vectors/x/png?size=16");
    expect(DEFAULT_PNG_PX).toBe(2048);
  });

  it("takes the PNG range from the server if it sends one, else the route's own (16 to 8192)", () => {
    expect(VECTOR_PNG_RANGE).toEqual({ min: 16, max: 8192 });
    expect(pngRange(CAPS.limits.vector)).toEqual({ min: 16, max: 8192 });
    expect(pngRange({ ...CAPS.limits.vector, png_px: { min: 32, max: 4096 } })).toEqual({ min: 32, max: 4096 });
  });
});

// ------------------------------------------------------------------ progress: the two stages
describe("progress", () => {
  const at = (stage: "draw" | "trace" | undefined, step: number, steps = 40, image = 1, of = 1) => ({ image, of, step, steps, stage });

  it("shows two stages, drawing and tracing, and before any progress both are empty", () => {
    expect(vectorPhases(null)).toEqual([{ key: "draw", label: "Drawing", fraction: 0 }, { key: "trace", label: "Tracing", fraction: 0 }]);
  });

  it("fills the drawing bar by its steps while the model works, and leaves tracing empty", () => {
    expect(vectorPhases(at("draw", 10))).toEqual([{ key: "draw", label: "Drawing", fraction: 0.25 }, { key: "trace", label: "Tracing", fraction: 0 }]);
    expect(vectorPhases(at(undefined, 40))[0].fraction).toBe(1); // no stage is the drawing
    expect(vectorPhases(at("draw", 99))[0].fraction).toBe(1); // never past full
    expect(vectorPhases(at("draw", -3))[0].fraction).toBe(0);
    expect(vectorPhases(at("draw", 5, 0))[0].fraction).toBe(1); // no steps to divide by: counted as one
  });

  it("when tracing, drawing is full and tracing is under way with nothing to measure (no steps to count)", () => {
    expect(vectorPhases(at("trace", 0))).toEqual([{ key: "draw", label: "Drawing", fraction: 1 }, { key: "trace", label: "Tracing", fraction: null }]);
  });

  it("says what is going on, with the version when there are several, and that it is stopping", () => {
    expect(vectorProgressText(null, false)).toBe("Starting…");
    expect(vectorProgressText(null, true)).toBe("Stopping…");
    expect(vectorProgressText(at("draw", 12), false)).toBe("drawing, step 12 of 40");
    expect(vectorProgressText(at("trace", 1, 1), false)).toBe("tracing the drawing into layers…");
    expect(vectorProgressText(at("draw", 12, 40, 2, 3), false)).toBe("Version 2 of 3 · drawing, step 12 of 40");
    expect(vectorProgressText(at("trace", 1, 1, 3, 3), true)).toBe("Version 3 of 3 · tracing the drawing into layers… · stopping at the next step…");
  });
});
