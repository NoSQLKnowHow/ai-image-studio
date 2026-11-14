// More like this and A/B compare (DESIGN.md section 43): the requests the page builds, the choices it offers and why one is greyed, the chips, the compare basket and the slider.
// What these protect: a variation always carries the right link and a fresh seed (never the source's), the picture-based choice cannot be asked for on a machine that has not been told it works,
// a retry never sends a link the server would refuse, and the basket and the slider keep to their limits at every edge.

import { describe, expect, it } from "vitest";
import { retryRequest } from "./options";
import { CAPS, makeImage, makeMusicRun, makeRun } from "./testdata";
import type { Capabilities, ImageRun, Run, VariationLink } from "./types";
import {
  BASKET_MAX, MAX_TAKES, SLIDER_STEP, basketHas, basketReplaceFirst, basketSwap, basketToggle, canMoreLikeThis, clampPercent, countChip, cut, familyOf, keepsLink, moreTakesRequest,
  percentAt, pictureChoice, pictureLabel, pictureVariationRequest, resolveBasketItem, sliderKey, sourceChip, takesChoice, takesLimit, variationRequest,
} from "./variations";

const WORDS = "a lighthouse at dusk";
const link = (state: VariationLink["source"]["state"], prompt: string | null = "a harbour"): VariationLink => ({ run_id: "a".repeat(32), image_id: null, how: "seeds", source: { prompt, state } });
const withPictures = (n: number, overrides: Partial<ImageRun> = {}): ImageRun => makeRun({ images: Array.from({ length: n }, (_, i) => makeImage(i)), ...overrides });
const caps = (variations: Capabilities["variations"], modes: Capabilities["modes"] = CAPS.modes): Capabilities => ({ ...CAPS, modes, variations });

// ---- how many pictures ------------------------------------------------------------------------------------------------------------------
describe("how many pictures More like this makes", () => {
  it("is at most 4, and never more than the server allows in a run", () => {
    expect(MAX_TAKES).toBe(4);
    expect(takesLimit(CAPS)).toBe(4); // the fixture allows 8
    expect(takesLimit({ limits: { ...CAPS.limits, num_images: { min: 1, max: 2 } } })).toBe(2);
    expect(takesLimit({ limits: { ...CAPS.limits, num_images: { min: 1, max: 1 } } })).toBe(1);
    expect(takesLimit({ limits: { ...CAPS.limits, num_images: { min: 1, max: 0 } } })).toBe(1); // never none
  });
});

describe("which runs offer More like this", () => {
  it("a finished picture run that has a picture and is not in the bin", () => {
    expect(canMoreLikeThis(withPictures(1))).toBe(true);
    expect(canMoreLikeThis(withPictures(1, { status: "running" }))).toBe(false); // nothing to make more of yet
    expect(canMoreLikeThis(withPictures(1, { status: "failed" }))).toBe(false);
    expect(canMoreLikeThis(withPictures(1, { deleted_at: "2026-10-14T10:00:00.000Z" }))).toBe(false); // a run in the bin only reads
    expect(canMoreLikeThis(withPictures(0))).toBe(false); // every picture deleted
  });
});

// ---- More takes of the prompt ----------------------------------------------------------------------------------------------------------
describe("More takes of the prompt", () => {
  const source = withPictures(2, { options: { ...makeRun().options, seed: 42, num_images: 2, negative_prompt: "blurry", cfg_scale: 4, steps: 40 } });

  it("is the run's own request again with the seed not locked and the number asked for", () => {
    const body = moreTakesRequest(source, { how: "seeds", count: 4, words: WORDS, image: null });
    expect(body.mode).toBe("generate");
    expect(body.options).toMatchObject({ width: 2048, height: 2048, steps: 40, seed: null, num_images: 4, negative_prompt: "blurry", cfg_scale: 4, transparent: false });
    expect(body.options.seed).toBeNull(); // a fresh seed from the server: the source's own would make the same picture again
    expect(body.variation_of).toEqual({ run_id: source.id, image_id: null, how: "seeds" });
  });

  it("is the same request from the viewer, which only says which picture it was looking at", () => {
    const fromCard = moreTakesRequest(source, { how: "seeds", count: 3, words: WORDS, image: null });
    const fromViewer = moreTakesRequest(source, { how: "seeds", count: 3, words: WORDS, image: source.images[1] });
    expect({ ...fromViewer, variation_of: undefined }).toEqual({ ...fromCard, variation_of: undefined });
    expect(fromViewer.variation_of).toEqual({ run_id: source.id, image_id: source.images[1].id, how: "seeds" });
  });

  it("sends the words as they are, trimmed, and falls back to the run's own when they are empty", () => {
    expect(moreTakesRequest(source, { how: "seeds", count: 1, words: `  ${WORDS}, but at dawn  `, image: null }).prompt).toBe(`${WORDS}, but at dawn`);
    expect(moreTakesRequest(source, { how: "seeds", count: 1, words: "   ", image: null }).prompt).toBe(source.prompt);
  });

  it("keeps a draft a draft and keeps its full size, as the source's own settings", () => {
    const draft = withPictures(1, { options: { ...makeRun().options, width: 512, height: 288, steps: 12, draft: true, full: { width: 2752, height: 1536, steps: 40 } } });
    const body = moreTakesRequest(draft, { how: "seeds", count: 4, words: WORDS, image: null });
    expect(body.options).toMatchObject({ width: 512, height: 288, steps: 12, draft: true, full: { width: 2752, height: 1536, steps: 40 } });
  });

  it("is the same edit again for an edit run: its input pictures, resolution and shape", () => {
    const edit = withPictures(1, {
      mode: "edit", inputs: [{ position: 1, role: "reference", id: "in1", width: 100, height: 80, has_alpha: false, url: "/u", thumb_url: null, can_4k: false, four_k_size: null, can_enlarge: false, enlarge_size: null, four_k: null }],
      options: { ...makeRun().options, width: null, height: null, resolution: 2048 },
    });
    const body = moreTakesRequest(edit, { how: "seeds", count: 2, words: "make it blue", image: null });
    expect(body.mode).toBe("edit");
    expect(body.input_images).toEqual([{ image_id: "in1", role: "reference" }]);
    expect(body.options).toMatchObject({ resolution: 2048, width: null, height: null, seed: null, num_images: 2 });
  });

  it("is More like this of THIS run, even when this run is itself a variation of another", () => {
    const variation = withPictures(1, { variation_of: link("present") });
    const body = moreTakesRequest(variation, { how: "seeds", count: 4, words: WORDS, image: null });
    expect(body.variation_of).toEqual({ run_id: variation.id, image_id: null, how: "seeds" }); // not the older link that Retry would have kept
  });
});

// ---- Variations of this picture ----------------------------------------------------------------------------------------------------------
describe("Variations of this picture", () => {
  const source = withPictures(2, { options: { ...makeRun().options, steps: 30, negative_prompt: "text", cfg_scale: 3 } });

  it("is an edit of the picked picture, the only input, with the size left on Auto and a fresh seed", () => {
    const picture = source.images[1];
    const body = pictureVariationRequest(source, picture, { how: "picture", count: 4, words: WORDS, image: picture });
    expect(body.mode).toBe("edit");
    expect(body.input_images).toEqual([{ image_id: picture.id }]);
    expect(body.options).toEqual({ width: null, height: null, steps: 30, seed: null, num_images: 4, negative_prompt: "text", cfg_scale: 3, transparent: false, resolution: 1024 });
    expect(body.variation_of).toEqual({ run_id: source.id, image_id: picture.id, how: "picture" });
  });

  it("keeps a 2K resolution from an edit source and uses 1K otherwise", () => {
    const edit = withPictures(1, { mode: "edit", options: { ...makeRun().options, resolution: 2048 } });
    expect(pictureVariationRequest(edit, edit.images[0], { how: "picture", count: 1, words: WORDS, image: edit.images[0] }).options.resolution).toBe(2048);
    expect(pictureVariationRequest(source, source.images[0], { how: "picture", count: 1, words: WORDS, image: source.images[0] }).options.resolution).toBe(1024);
  });

  it("variationRequest picks the kind from what was asked, and a picture variation from a card uses the run's only picture", () => {
    const single = withPictures(1);
    expect(variationRequest(single, { how: "seeds", count: 4, words: WORDS, image: null }).mode).toBe("generate");
    const fromCard = variationRequest(single, { how: "picture", count: 4, words: WORDS, image: null });
    expect(fromCard.mode).toBe("edit");
    expect(fromCard.input_images).toEqual([{ image_id: single.images[0].id }]);
    expect(fromCard.variation_of).toEqual({ run_id: single.id, image_id: single.images[0].id, how: "picture" });
  });
});

describe("the two choices of the dialog", () => {
  const run = withPictures(2);

  it("More takes is always open", () => expect(takesChoice).toEqual({ enabled: true, reason: null }));

  it("Variations of this picture is greyed, with the server's reason, until the machine is told it works", () => {
    expect(pictureChoice(caps({ from_picture: false, reason: "Not tried on this machine yet." }), run, run.images[0])).toEqual({ enabled: false, reason: "Not tried on this machine yet." });
    expect(pictureChoice(caps(undefined), run, run.images[0])).toEqual({ enabled: false, reason: "Not tried on this machine yet." }); // a server from before 1.24
    expect(pictureChoice(caps({ from_picture: false, reason: null }), run, run.images[0]).reason).toBe("Not tried on this machine yet.");
  });

  it("is greyed where editing is not available, even when switched on", () => {
    const choice = pictureChoice(caps({ from_picture: true, reason: null }, ["generate", "music"]), run, run.images[0]);
    expect(choice).toEqual({ enabled: false, reason: "Editing is not available on this machine." });
  });

  it("from a card needs exactly one picture; with several it says to open one in the viewer", () => {
    const on = caps({ from_picture: true, reason: null });
    expect(pictureChoice(on, run, null)).toEqual({ enabled: false, reason: "Open one picture in the viewer and press More like this there." });
    expect(pictureChoice(on, withPictures(1), null)).toEqual({ enabled: true, reason: null });
    expect(pictureChoice(on, run, run.images[1])).toEqual({ enabled: true, reason: null });
  });
});

// ---- chips ------------------------------------------------------------------------------------------------------------------------------
describe("the chips on a card", () => {
  it("cut makes white space single and ends a long text with an ellipsis within the limit", () => {
    expect(cut("  a   lighthouse \n at dusk ")).toBe("a lighthouse at dusk");
    expect(cut("x".repeat(40))).toBe("x".repeat(40)); // exactly the limit: unchanged
    expect(cut("x".repeat(41))).toBe(`${"x".repeat(39)}…`);
    expect(cut("hello world, this is long", 10)).toBe("hello wor…");
    expect(cut("")).toBe("");
  });

  it("the first chip says where a variation came from, and what has become of that run", () => {
    expect(sourceChip({ variation_of: null })).toBeNull();
    expect(sourceChip({})).toBeNull(); // a server from before 1.24 sends no key
    expect(sourceChip({ variation_of: link("present", "a harbour at night") })).toBe("Variation of “a harbour at night”");
    expect(sourceChip({ variation_of: link("in_bin") })).toBe("Variation of a run in the bin");
    expect(sourceChip({ variation_of: link("gone", null) })).toBe("Variation of a run that is gone");
    expect(sourceChip({ variation_of: link("present", null) })).toBe("Variation of a run that is gone"); // the words are gone, so it is
    expect(sourceChip({ variation_of: link("present", "x".repeat(80)) })).toBe(`Variation of “${"x".repeat(39)}…”`);
  });

  it("the second chip counts the variations a run has, and is not there for none", () => {
    expect(countChip({ variation_count: 0 })).toBeNull();
    expect(countChip({})).toBeNull();
    expect(countChip({ variation_count: 1 })).toBe("Variations (1)");
    expect(countChip({ variation_count: 12 })).toBe("Variations (12)");
  });

  it("the family a chip opens is the first run's id: the run itself when it has variations, its family_id when it is one", () => {
    expect(familyOf({ id: "root", family_id: null, variation_count: 3 })).toBe("root");
    expect(familyOf({ id: "child", family_id: "root", variation_count: 0 })).toBe("root");
    expect(familyOf({ id: "child", family_id: "root" })).toBe("root");
    expect(familyOf({ id: "alone", family_id: null, variation_count: 0 })).toBeNull();
    expect(familyOf({ id: "alone" })).toBeNull();
  });
});

describe("Retry of a variation (criterion 339)", () => {
  it("keeps the link while the run it came from is in the history", () => {
    const run = withPictures(1, { variation_of: { run_id: "b".repeat(32), image_id: "p".repeat(32), how: "seeds", source: { prompt: "x", state: "present" } } });
    expect(keepsLink(run)).toBe(true);
    expect(retryRequest(run).variation_of).toEqual({ run_id: "b".repeat(32), image_id: "p".repeat(32), how: "seeds" }); // the request has no `source`: that is the server's to say
    expect(retryRequest(run).options.seed).toBe(run.options.seed); // and it is the same request, same seed
  });

  it("drops it when the source is in the bin or gone, since the server would refuse it and the retry would be no retry", () => {
    for (const state of ["in_bin", "gone"] as const) {
      const run = withPictures(1, { variation_of: link(state) });
      expect(keepsLink(run)).toBe(false);
      expect("variation_of" in retryRequest(run)).toBe(false);
    }
  });

  it("sends no link at all for a run that is not a variation, so its request is what it always was", () => {
    expect("variation_of" in retryRequest(withPictures(1))).toBe(false);
    expect("variation_of" in retryRequest(withPictures(1, { variation_of: null }))).toBe(false);
  });
});

// ---- the compare basket ------------------------------------------------------------------------------------------------------------------
describe("the compare basket", () => {
  const a = { runId: "r1", imageId: "i1" }, b = { runId: "r2", imageId: "i2" }, c = { runId: "r3", imageId: "i3" };

  it("holds two pictures, and a third is refused with `full` and changes nothing", () => {
    expect(BASKET_MAX).toBe(2);
    const one = basketToggle([], a);
    expect(one).toEqual({ basket: [a], full: false });
    const two = basketToggle(one.basket, b);
    expect(two).toEqual({ basket: [a, b], full: false });
    const third = basketToggle(two.basket, c);
    expect(third.full).toBe(true);
    expect(third.basket).toBe(two.basket); // the same basket, untouched
  });

  it("is a toggle: a picture that is in comes out, and a full basket can take it out to make room", () => {
    expect(basketToggle([a, b], a)).toEqual({ basket: [b], full: false });
    expect(basketHas([a, b], "i2")).toBe(true);
    expect(basketHas([a, b], "i3")).toBe(false);
  });

  it("replaces the first picture when asked, so the second becomes the first", () => {
    expect(basketReplaceFirst([a, b], c)).toEqual([b, c]);
    expect(basketReplaceFirst([a], c)).toEqual([a, c]); // not full: it is added
    expect(basketReplaceFirst([], c)).toEqual([c]);
    expect(basketReplaceFirst([a, b], b)).toEqual([a, b]); // a picture that is in already changes nothing
  });

  it("swaps its two sides, and leaves one picture or none alone", () => {
    expect(basketSwap([a, b])).toEqual([b, a]);
    expect(basketSwap([a])).toEqual([a]);
    expect(basketSwap([])).toEqual([]);
  });

  it("finds a picture among the runs the page holds, result or an edit's own source, and says null when it is not there", () => {
    const run = withPictures(2, { id: "r1", pinned: true });
    const edit = withPictures(1, {
      id: "r3", mode: "edit",
      inputs: [{ position: 2, role: "reference", id: "in2", width: 100, height: 80, has_alpha: true, url: "/api/images/in2", thumb_url: null, can_4k: false, four_k_size: null, can_enlarge: false, enlarge_size: null, four_k: null }],
    });
    const runs: Record<string, Run> = { r1: run, r2: withPictures(1, { id: "r2", deleted_at: "2026-10-14T10:00:00.000Z" }), r3: edit };
    expect(resolveBasketItem(runs, { runId: "r1", imageId: run.images[1].id })).toEqual({
      runId: "r1", id: run.images[1].id, url: run.images[1].url, width: 2048, height: 2048, hasAlpha: false, label: "a lighthouse at dusk · seed 101 · 2048×2048", pinned: true,
    });
    expect(resolveBasketItem(runs, { runId: "r3", imageId: "in2" })).toMatchObject({ id: "in2", url: "/api/images/in2", width: 100, height: 80, hasAlpha: true, pinned: false, label: "a lighthouse at dusk · source image 2 · 100×80" });
    expect(resolveBasketItem(runs, { runId: "r1", imageId: "nope" })).toBeNull(); // the picture was deleted
    expect(resolveBasketItem(runs, { runId: "r2", imageId: runs.r2.images[0]?.id ?? "x" })).toBeNull(); // its run is in the bin
    expect(resolveBasketItem(runs, { runId: "r9", imageId: "x" })).toBeNull(); // its run is not here
    expect(resolveBasketItem({ m: makeMusicRun({ id: "m" }) }, { runId: "m", imageId: "x" })).toBeNull(); // not a picture run
  });

  it("labels a side with the words cut short, the seed and the size", () => {
    expect(pictureLabel({ prompt: "a lighthouse at dusk" }, makeImage(3))).toBe("a lighthouse at dusk · seed 103 · 2048×2048");
    expect(pictureLabel({ prompt: "x".repeat(60) }, { seed: 1, width: 10, height: 20 })).toBe(`${"x".repeat(39)}… · seed 1 · 10×20`);
  });
});

// ---- the slider --------------------------------------------------------------------------------------------------------------------------
describe("the compare slider", () => {
  it("moves a step with the arrows, two with Page Up and Page Down, and to the ends with Home and End", () => {
    expect(SLIDER_STEP).toBe(5);
    expect(sliderKey(50, "ArrowRight")).toBe(55);
    expect(sliderKey(50, "ArrowUp")).toBe(55);
    expect(sliderKey(50, "ArrowLeft")).toBe(45);
    expect(sliderKey(50, "ArrowDown")).toBe(45);
    expect(sliderKey(50, "PageUp")).toBe(60);
    expect(sliderKey(50, "PageDown")).toBe(40);
    expect(sliderKey(50, "Home")).toBe(0);
    expect(sliderKey(50, "End")).toBe(100);
  });

  it("never goes past either end, and ignores a key that is not for it", () => {
    expect(sliderKey(98, "ArrowRight")).toBe(100);
    expect(sliderKey(100, "PageUp")).toBe(100);
    expect(sliderKey(2, "ArrowLeft")).toBe(0);
    expect(sliderKey(0, "PageDown")).toBe(0);
    expect(sliderKey(50, "a")).toBeNull();
    expect(sliderKey(50, "Enter")).toBeNull();
    expect(sliderKey(50, "Tab")).toBeNull(); // Tab must still leave the slider
  });

  it("keeps a position between 0 and 100", () => {
    expect([clampPercent(-5), clampPercent(0), clampPercent(37.5), clampPercent(100), clampPercent(140)]).toEqual([0, 0, 37.5, 100, 100]);
  });

  it("turns a pointer position into a percent of the box, in the middle when the box has no width", () => {
    expect(percentAt(150, 100, 200)).toBe(25);
    expect(percentAt(100, 100, 200)).toBe(0);
    expect(percentAt(300, 100, 200)).toBe(100);
    expect(percentAt(50, 100, 200)).toBe(0); // left of the box
    expect(percentAt(900, 100, 200)).toBe(100); // right of it
    expect(percentAt(150, 100, 0)).toBe(50);
  });
});
