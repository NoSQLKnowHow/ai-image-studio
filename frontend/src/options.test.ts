import { describe, expect, it } from "vitest";
import {
  AUTO,
  CUSTOM,
  OPTIONS_KEY,
  SCALES,
  baseSize,
  buildEditRequest,
  buildRequest,
  defaultOptions,
  draftRequest,
  draftSize,
  effectiveScale,
  isAutoSize,
  isLarger,
  largerRequest,
  largerTarget,
  loadOptions,
  optionsFromRun,
  optionsProblem,
  resolutionLabel,
  resolveSize,
  retryRequest,
  sanitizeOptions,
  scaleProblem,
  scaledSide,
  scaledSize,
  saveOptions,
  sizeKey,
  sizeLabel,
  summarize,
  usableScale,
  type KeyValueStore,
  type Scale,
} from "./options";
import { CAPS, makeRun } from "./testdata";
import type { Capabilities, ImageRun, Mode } from "./types";

function memoryStore(initial: Record<string, string> = {}): KeyValueStore & { data: Record<string, string> } {
  const data = { ...initial };
  return { available: true, data, get: (k) => data[k] ?? null, set: (k, v) => void (data[k] = v) };
}

describe("defaults and persistence", () => {
  it("starts from the server's defaults with a random seed", () => {
    const o = defaultOptions(CAPS);
    expect(o).toMatchObject({ mode: "generate", aspect: "1:1", steps: 40, numImages: 1, seedLocked: false, guidance: null });
  });

  it("round-trips through storage", () => {
    const store = memoryStore();
    const changed = { ...defaultOptions(CAPS), aspect: "16:9", steps: 12, seedLocked: true, seed: 7, numImages: 3 };
    saveOptions(changed, store);
    expect(loadOptions(CAPS, store)).toEqual({ options: changed, repaired: false });
  });

  it("survives corrupt JSON", () => {
    expect(loadOptions(CAPS, memoryStore({ [OPTIONS_KEY]: "{not json" }))).toEqual({ options: defaultOptions(CAPS), repaired: true });
  });

  it("resets only the fields that are invalid", () => {
    const { options, repaired } = sanitizeOptions(
      { aspect: "5:4", steps: 500, seed: -1, numImages: 2, negativePrompt: "blurry", guidance: "lots", transparent: true, junk: 1 },
      CAPS,
    );
    expect(repaired).toBe(true);
    expect(options.aspect).toBe("1:1");
    expect(options.steps).toBe(40);
    expect(options.seed).toBe(42);
    expect(options.guidance).toBeNull();
    expect(options).toMatchObject({ numImages: 2, negativePrompt: "blurry", transparent: true });
    expect("junk" in options).toBe(false);
  });

  it("treats missing fields (added in a later version) as defaults without complaint", () => {
    expect(sanitizeOptions({ steps: 20 }, CAPS)).toEqual({ options: { ...defaultOptions(CAPS), steps: 20 }, repaired: false });
  });

  it("rejects a mode the server doesn't offer (an older server, or a pipeline that can't edit)", () => {
    const noEdit: Capabilities = { ...CAPS, modes: ["generate"] as Mode[] };
    expect(sanitizeOptions({ mode: "edit" }, noEdit).options.mode).toBe("generate");
    expect(sanitizeOptions({ mode: "edit" }, CAPS).options.mode).toBe("edit");
  });

  it("resets an invalid custom size", () => {
    const { options, repaired } = sanitizeOptions({ aspect: CUSTOM, customWidth: 1000, customHeight: 1024 }, CAPS);
    expect(repaired).toBe(true);
    expect([options.customWidth, options.customHeight]).toEqual([2048, 2048]);
  });
});

describe("building requests", () => {
  it("sends a null seed when not locked, so the server picks and records one", () => {
    const body = buildRequest("  a fox  ", defaultOptions(CAPS), CAPS);
    expect(body).toEqual({
      mode: "generate",
      prompt: "a fox",
      options: { width: 2048, height: 2048, steps: 40, seed: null, num_images: 1, negative_prompt: null, cfg_scale: null, transparent: false },
    });
  });

  it("uses the locked seed, custom size, guidance, negative prompt and transparency", () => {
    const o = { ...defaultOptions(CAPS), aspect: CUSTOM, customWidth: 1024, customHeight: 768, seedLocked: true, seed: 99,
      guidance: 4, negativePrompt: " blurry ", transparent: true };
    expect(buildRequest("x", o, CAPS).options).toEqual({
      width: 1024, height: 768, steps: 40, seed: 99, num_images: 1, negative_prompt: "blurry", cfg_scale: 4, transparent: true,
    });
  });

  it("leaves out what the loaded pipeline doesn't support", () => {
    const caps = { ...CAPS, supports: { ...CAPS.supports, negative_prompt: false, cfg_scale: false } };
    const o = { ...defaultOptions(CAPS), guidance: 4, negativePrompt: "blurry" };
    expect(buildRequest("x", o, caps).options).toMatchObject({ negative_prompt: null, cfg_scale: null });
  });

  it("blocks an invalid custom size before it reaches the server", () => {
    expect(optionsProblem({ ...defaultOptions(CAPS), aspect: CUSTOM, customWidth: 1040, customHeight: 1024 }, CAPS)).toMatch(/multiple of 32/);
    expect(optionsProblem({ ...defaultOptions(CAPS), aspect: CUSTOM, customWidth: 4096, customHeight: 2048 }, CAPS)).toMatch(/MP/);
  });

  it("summarizes the options for the button", () => {
    const o = { ...defaultOptions(CAPS), aspect: "16:9", steps: 20, seedLocked: true, seed: 5, numImages: 2, guidance: 4 };
    expect(summarize(o, CAPS)).toBe("2752×1536 · 20 steps · seed 5 · 2 images · guidance 4");
  });
});

describe("reuse and retry", () => {
  it("reuse restores everything and locks the seed", () => {
    const run = makeRun({ options: { width: 2752, height: 1536, steps: 25, seed: 1234, seed_was_random: true, num_images: 2,
      negative_prompt: "blurry", cfg_scale: 3.5, transparent: false } });
    const o = optionsFromRun(run, CAPS, defaultOptions(CAPS));
    expect(o).toMatchObject({ aspect: "16:9", steps: 25, seedLocked: true, seed: 1234, numImages: 2, negativePrompt: "blurry", guidance: 3.5 });
  });

  it("reuse of a non-preset size switches to a custom size", () => {
    const run = makeRun({ options: { ...makeRun().options, width: 1024, height: 768 } });
    expect(optionsFromRun(run, CAPS, defaultOptions(CAPS))).toMatchObject({ aspect: CUSTOM, customWidth: 1024, customHeight: 768 });
  });

  it("retry repeats the exact request, seed included", () => {
    const run = makeRun({ options: { width: 1024, height: 768, steps: 25, seed: 777, seed_was_random: true, num_images: 2,
      negative_prompt: "blurry", cfg_scale: 4, transparent: false } });
    expect(retryRequest(run)).toEqual({
      mode: "generate",
      prompt: run.prompt,
      options: { width: 1024, height: 768, steps: 25, seed: 777, num_images: 2, negative_prompt: "blurry", cfg_scale: 4, transparent: false, draft: false },
    });
  });
});


describe("the scale picker (DESIGN.md §22.1)", () => {
  const at = (aspect: string, scale: Scale, extra = {}) => ({ ...defaultOptions(CAPS), aspect, scale, ...extra });

  it("offers 100, 75, 50 and 25 percent, and starts at 100", () => {
    expect([...SCALES]).toEqual([100, 75, 50, 25]);
    expect(defaultOptions(CAPS).scale).toBe(100);
  });

  it("scales each side to the nearest multiple of 32, halves upward", () => {
    expect([100, 75, 50, 25].map((s) => scaledSide(2048, s, 32))).toEqual([2048, 1536, 1024, 512]);
    expect(scaledSide(2400, 50, 32)).toBe(1216); // 1200 is not a multiple of 32 (37.5 rounds up to 38)
    expect(scaledSide(1792, 50, 32)).toBe(896);
    expect(scaledSide(2752, 50, 32)).toBe(1376);
    expect(scaledSide(2528, 50, 32)).toBe(1280); // 39.5 rounds up
    expect(scaledSide(1696, 50, 32)).toBe(864); // 26.5 rounds up
  });

  it("every preset at every scale is a legal size close to the preset's shape", () => {
    for (const [name, [w, h]] of Object.entries(CAPS.aspect_ratios)) {
      for (const scale of SCALES) {
        const size = scaledSize({ width: w, height: h }, scale, CAPS);
        expect(size.width % 32, `${name} ${scale}%`).toBe(0);
        expect(size.height % 32, `${name} ${scale}%`).toBe(0);
        expect(size.width).toBeGreaterThanOrEqual(CAPS.limits.size.min);
        expect(size.height).toBeGreaterThanOrEqual(CAPS.limits.size.min);
        expect(size.width).toBeLessThanOrEqual(w);
        expect(Math.abs(size.width / size.height / (w / h) - 1), `${name} ${scale}%`).toBeLessThan(0.05);
        expect(scaleProblem({ width: w, height: h }, scale, CAPS)).toBeNull();
      }
    }
  });

  it("100% is the chosen size exactly, even a custom one that is not a multiple of 32", () => {
    expect(scaledSize({ width: 1000, height: 700 }, 100, CAPS)).toEqual({ width: 1000, height: 700 });
  });

  it("sends the scaled size and shows it", () => {
    const half = at("1:1", 50, { steps: 20 });
    expect(resolveSize(half, CAPS)).toEqual({ width: 1024, height: 1024 });
    expect(buildRequest("x", half, CAPS).options).toMatchObject({ width: 1024, height: 1024, steps: 20 });
    expect(summarize(half, CAPS)).toBe("1024×1024 (50%) · 20 steps · random seed");
    expect(summarize(at("1:1", 100), CAPS)).toBe("2048×2048 · 40 steps · random seed");
    expect(buildRequest("x", at("4:3", 50), CAPS).options).toMatchObject({ width: 1216, height: 896 });
  });

  it("applies to a custom size too", () => {
    const custom = at(CUSTOM, 50, { customWidth: 1536, customHeight: 1024 });
    expect(resolveSize(custom, CAPS)).toEqual({ width: 768, height: 512 });
    expect(baseSize(custom, CAPS)).toEqual({ width: 1536, height: 1024 });
  });

  it("does not offer a scale that would take a side under the minimum, and falls back to the nearest larger", () => {
    const small = { width: 512, height: 512 };
    expect(scaleProblem(small, 25, CAPS)).toMatch(/would be 128×128; each side must be at least 256/);
    expect(scaleProblem(small, 50, CAPS)).toBeNull(); // exactly 256
    expect(usableScale(25, small, CAPS)).toBe(50);
    const tiny = { width: 256, height: 256 };
    expect([75, 50, 25].map((s) => usableScale(s as Scale, tiny, CAPS))).toEqual([100, 100, 100]);
    const custom = at(CUSTOM, 25, { customWidth: 512, customHeight: 512 });
    expect(effectiveScale(custom, CAPS)).toBe(50);
    expect(resolveSize(custom, CAPS)).toEqual({ width: 256, height: 256 });
    expect(summarize(custom, CAPS)).toContain("256×256 (50%)");
    expect(custom.scale).toBe(25); // the saved choice is untouched: it comes back when the size allows it
    expect(effectiveScale({ ...custom, aspect: "1:1" }, CAPS)).toBe(25);
  });

  it("is remembered with the other options, and options saved before it existed read as 100%", () => {
    const store = memoryStore();
    saveOptions(at("16:9", 50), store);
    expect(loadOptions(CAPS, store).options.scale).toBe(50);
    expect(sanitizeOptions({ aspect: "16:9", steps: 30 }, CAPS)).toEqual({
      options: { ...defaultOptions(CAPS), aspect: "16:9", steps: 30 },
      repaired: false, // an option added later is not "repaired"
    });
    const bad = sanitizeOptions({ scale: 60, steps: 30 }, CAPS);
    expect(bad.options).toMatchObject({ scale: 100, steps: 30 });
    expect(bad.repaired).toBe(true);
    expect(sanitizeOptions({ scale: "50" }, CAPS).options.scale).toBe(100);
  });

  it("Reuse brings back a preset at a scale when the size is exactly that, preferring 100%", () => {
    const reuse = (width: number, height: number, current = defaultOptions(CAPS)) =>
      optionsFromRun(makeRun({ options: { ...makeRun().options, width, height } }), CAPS, current);
    expect(reuse(1024, 1024)).toMatchObject({ aspect: "1:1", scale: 50 });
    expect(reuse(2048, 2048)).toMatchObject({ aspect: "1:1", scale: 100 });
    expect(reuse(1376, 768)).toMatchObject({ aspect: "16:9", scale: 50 });
    expect(reuse(1216, 896)).toMatchObject({ aspect: "4:3", scale: 50 });
    expect(reuse(512, 512)).toMatchObject({ aspect: "1:1", scale: 25 });
    const custom = reuse(640, 576, { ...defaultOptions(CAPS), scale: 50 });
    expect(custom).toMatchObject({ aspect: CUSTOM, customWidth: 640, customHeight: 576, scale: 100 }); // no stale 50% on top
    expect(resolveSize(custom, CAPS)).toEqual({ width: 640, height: 576 });
  });

  it("Reuse prefers the larger scale when two presets could explain the same size", () => {
    // 4096x4096 at 50% and 2048x2048 at 100% are both 2048x2048: the plain one wins.
    const tie = { ...CAPS, aspect_ratios: { big: [4096, 4096], small: [2048, 2048] } as Capabilities["aspect_ratios"] };
    const run = makeRun({ options: { ...makeRun().options, width: 2048, height: 2048 } });
    expect(optionsFromRun(run, tie, { ...defaultOptions(tie), aspect: "big" })).toMatchObject({ aspect: "small", scale: 100 });
  });
});

describe("drafts (DESIGN.md §22.2)", () => {
  it("keep the chosen shape with the long side at the draft size", () => {
    const sizes = Object.fromEntries(
      Object.entries(CAPS.aspect_ratios).map(([name, [width, height]]) => [name, draftSize({ width, height }, CAPS)]),
    );
    expect(sizes).toEqual({
      "1:1": { width: 512, height: 512 }, "4:3": { width: 512, height: 384 }, "3:4": { width: 384, height: 512 },
      "3:2": { width: 512, height: 352 }, "2:3": { width: 352, height: 512 }, "16:9": { width: 512, height: 288 },
      "9:16": { width: 288, height: 512 },
    });
  });

  it("never take a side under the minimum, or over the draft size", () => {
    expect(draftSize({ width: 4096, height: 256 }, CAPS)).toEqual({ width: 512, height: 256 }); // 32 would be too small
    expect(draftSize({ width: 256, height: 256 }, CAPS)).toEqual({ width: 256, height: 256 }); // never bigger than what you chose
    expect(draftSize({ width: 384, height: 256 }, CAPS)).toEqual({ width: 384, height: 256 });
    const tiny = { ...CAPS, limits: { ...CAPS.limits, draft: { long_side: 256, steps: 12 } } };
    expect(draftSize({ width: 2752, height: 1536 }, tiny)).toEqual({ width: 256, height: 256 }); // only a square fits
  });

  it("are one small image, at most the draft steps, flagged, whatever the scale", () => {
    const options = { ...defaultOptions(CAPS), aspect: "16:9", scale: 50 as Scale, steps: 40, numImages: 4 };
    expect(draftRequest("  a lighthouse ", options, CAPS)).toEqual({
      mode: "generate", prompt: "a lighthouse",
      options: { width: 512, height: 288, steps: 12, seed: null, num_images: 1, negative_prompt: null, cfg_scale: null, transparent: false, draft: true,
                 full: { width: 2752, height: 1536, steps: 40 } }, // the chosen size at 100%, with the chosen steps (not the draft's 12)
    });
    expect(draftRequest("x", { ...options, steps: 5 }, CAPS).options.steps).toBe(5); // never more steps than you chose
  });

  it("carry the locked seed, the negative prompt, guidance and Transparent", () => {
    const options = { ...defaultOptions(CAPS), seedLocked: true, seed: 99, negativePrompt: " blurry ", guidance: 4, transparent: true };
    expect(draftRequest("x", options, CAPS).options).toMatchObject({ seed: 99, negative_prompt: "blurry", cfg_scale: 4, transparent: true });
  });

  it("are sent again as drafts on Retry, and ordinary runs never are", () => {
    const draft = makeRun({ options: { ...makeRun().options, width: 512, height: 288, steps: 12, draft: true } });
    expect(retryRequest(draft).options).toMatchObject({ width: 512, height: 288, steps: 12, draft: true });
    expect(retryRequest(makeRun()).options.draft).toBe(false);
  });

  it("Reuse on a draft brings back the prompt-side options but not its size, steps or seed", () => {
    const draft = makeRun({
      options: { width: 512, height: 288, steps: 12, seed: 7, seed_was_random: true, num_images: 1, negative_prompt: "blurry",
                 cfg_scale: 4, transparent: true, draft: true },
    });
    const mine = { ...defaultOptions(CAPS), aspect: "4:3", scale: 50 as Scale, steps: 40, seedLocked: false, numImages: 3 };
    expect(optionsFromRun(draft, CAPS, mine)).toEqual({ ...mine, negativePrompt: "blurry", guidance: 4, transparent: true });
  });
});

describe("regenerate larger (DESIGN.md §23)", () => {
  const FULL = { width: 2048, height: 2048, steps: 40 };
  const smallRun = (over: Record<string, unknown> = {}, run: Partial<ImageRun> = {}) =>
    makeRun({ options: { ...makeRun().options, width: 1024, height: 1024, steps: 40, full: FULL, ...over }, ...run });

  describe("what a run remembers", () => {
    it("a run at a scale under 100% records the chosen size at 100% and the chosen steps", () => {
      const options = { ...defaultOptions(CAPS), aspect: "16:9", scale: 50 as Scale, steps: 30 };
      const body = buildRequest("x", options, CAPS);
      expect([body.options.width, body.options.height]).toEqual([1376, 768]);
      expect(body.options.full).toEqual({ width: 2752, height: 1536, steps: 30 });
    });

    it("a custom size is the full size too", () => {
      const options = { ...defaultOptions(CAPS), aspect: "custom", customWidth: 992, customHeight: 640, scale: 75 as Scale };
      expect(buildRequest("x", options, CAPS).options.full).toEqual({ width: 992, height: 640, steps: 40 });
    });

    it("a run at 100% records none", () => {
      expect(buildRequest("x", defaultOptions(CAPS), CAPS).options).not.toHaveProperty("full");
    });

    it("a scale that can't be used (it falls back to 100%) records none", () => {
      const options = { ...defaultOptions(CAPS), aspect: "custom", customWidth: 256, customHeight: 256, scale: 50 as Scale };
      const body = buildRequest("x", options, CAPS);
      expect([body.options.width, body.options.height]).toEqual([256, 256]);
      expect(body.options).not.toHaveProperty("full");
    });

    it("an edit records none (the server only takes it for Generate)", () => {
      const options = { ...defaultOptions(CAPS), mode: "edit" as const, editAspect: "1:1", scale: 50 as Scale };
      expect(buildEditRequest("x", options, CAPS, [{ upload_id: "u" }], null).options).not.toHaveProperty("full");
    });

    it("a draft records the chosen size at 100% whatever the scale picker says, with the chosen steps", () => {
      for (const scale of [100, 50, 25] as Scale[]) {
        const options = { ...defaultOptions(CAPS), scale, steps: 25 };
        expect(draftRequest("x", options, CAPS).options.full).toEqual({ width: 2048, height: 2048, steps: 25 });
      }
    });

    it("a draft as big as the chosen size records none: there is nothing bigger to go back to", () => {
      const options = { ...defaultOptions(CAPS), aspect: "custom", customWidth: 512, customHeight: 512 };
      const body = draftRequest("x", options, CAPS);
      expect([body.options.width, body.options.height]).toEqual([512, 512]);
      expect(body.options).not.toHaveProperty("full");
    });

    it("Retry sends it again, so the retried run has its button too; an ordinary run adds none", () => {
      expect(retryRequest(smallRun()).options.full).toEqual(FULL);
      expect(retryRequest(makeRun()).options).not.toHaveProperty("full");
      expect(retryRequest(smallRun({ full: null })).options).not.toHaveProperty("full");
    });
  });

  describe("isLarger", () => {
    it.each([
      [{ width: 512, height: 512 }, { width: 256, height: 256 }, true],
      [{ width: 512, height: 256 }, { width: 256, height: 256 }, true], // bigger in one side is enough
      [{ width: 256, height: 512 }, { width: 256, height: 256 }, true],
      [{ width: 256, height: 256 }, { width: 256, height: 256 }, false], // the same
      [{ width: 512, height: 128 }, { width: 256, height: 256 }, false], // smaller in one side
      [{ width: 128, height: 512 }, { width: 256, height: 256 }, false],
      [{ width: 128, height: 128 }, { width: 256, height: 256 }, false],
    ])("%j against %j is %s", (full, size, expected) => {
      expect(isLarger(full, size)).toBe(expected);
    });
  });

  describe("when the button is offered", () => {
    it("on a finished Generate run that remembers a bigger size, and names it", () => {
      expect(largerTarget(smallRun())).toEqual(FULL);
    });

    it.each(["queued", "running", "failed", "canceled"] as const)("not on a %s run", (status) => {
      expect(largerTarget(smallRun({}, { status }))).toBeNull();
    });

    it("not on a run with no record (made at full size, or before 1.4), or a null one", () => {
      expect(largerTarget(makeRun())).toBeNull();
      expect(largerTarget(smallRun({ full: null }))).toBeNull();
      expect(largerTarget(smallRun({ full: undefined }))).toBeNull();
    });

    it("not when the record isn't bigger than the run, or is smaller in a side", () => {
      expect(largerTarget(smallRun({ full: { width: 1024, height: 1024, steps: 40 } }))).toBeNull();
      expect(largerTarget(smallRun({ full: { width: 2048, height: 512, steps: 40 } }))).toBeNull();
    });

    it("not on an edit run, nor one with no size", () => {
      expect(largerTarget(smallRun({}, { mode: "edit" }))).toBeNull();
      expect(largerTarget(smallRun({ width: null, height: null }))).toBeNull();
    });
  });

  describe("the run it sends", () => {
    it("is the full size and steps, with the same prompt, seed and everything else, as an ordinary run", () => {
      const run = smallRun({
        seed: 777, num_images: 3, negative_prompt: "blurry", cfg_scale: 4, transparent: true, steps: 12,
        full: { width: 1792, height: 1024, steps: 35 }, width: 896, height: 512,
      }, { prompt: "a lighthouse, but kinder" });
      expect(largerRequest(run)).toEqual({
        mode: "generate", prompt: "a lighthouse, but kinder",
        options: { width: 1792, height: 1024, steps: 35, seed: 777, num_images: 3, negative_prompt: "blurry", cfg_scale: 4, transparent: true, draft: false },
      });
    });

    it("uses the seed the server recorded for a draft that picked its own", () => {
      const draft = smallRun({ draft: true, seed: 123456789, seed_was_random: true, num_images: 1, steps: 12, width: 512, height: 512 });
      expect(largerRequest(draft)?.options).toMatchObject({ seed: 123456789, num_images: 1, draft: false, steps: 40, width: 2048, height: 2048 });
    });

    it("carries no full size of its own, so it has no button in turn", () => {
      expect(largerRequest(smallRun())?.options).not.toHaveProperty("full");
    });

    it("is nothing when the button isn't offered", () => {
      expect(largerRequest(makeRun())).toBeNull();
      expect(largerRequest(smallRun({}, { status: "failed" }))).toBeNull();
    });
  });

  describe("from the viewer: one image (DESIGN.md §24.1)", () => {
    const several = () => smallRun({ seed: 1000, num_images: 3, negative_prompt: "blurry", cfg_scale: 4, transparent: true });

    it("uses the seed of the image being viewed and a single image; nothing else differs from the run's request", () => {
      const run = several();
      const all = largerRequest(run)!;
      const one = largerRequest(run, { seed: 1002 })!;
      expect(one.options).toEqual({ ...all.options, seed: 1002, num_images: 1 });
      expect([all.options.seed, all.options.num_images]).toEqual([1000, 3]); // the card's request is unchanged
    });

    it("for a run of one image is the card's request", () => {
      const run = smallRun({ seed: 42, num_images: 1 });
      expect(largerRequest(run, { seed: 42 })).toEqual(largerRequest(run));
    });

    it("is nothing when the button isn't offered", () => {
      expect(largerRequest(makeRun(), { seed: 1 })).toBeNull();
      expect(largerRequest(smallRun({}, { status: "canceled" }), { seed: 1 })).toBeNull();
    });
  });
});

// ------------------------------------------------------------------ editing (DESIGN.md §21.4)
describe("editing: the size choice and the resolution", () => {
  const edit = (over: Partial<ReturnType<typeof defaultOptions>> = {}) => ({ ...defaultOptions(CAPS), mode: "edit" as const, ...over });

  it("Edit starts on Auto at 1K", () => {
    const d = defaultOptions(CAPS);
    expect([d.editAspect, d.resolution]).toEqual([AUTO, 1024]);
  });

  it("options saved before editing existed read as those defaults, without being called repaired", () => {
    const old = { mode: "generate", aspect: "16:9", customWidth: 2048, customHeight: 2048, scale: 50, steps: 30, seedLocked: false, seed: 42,
      numImages: 1, negativePrompt: "", guidance: null, transparent: false };
    const { options, repaired } = sanitizeOptions(old, CAPS);
    expect(repaired).toBe(false);
    expect([options.editAspect, options.resolution, options.aspect]).toEqual([AUTO, 1024, "16:9"]);
  });

  it("accepts Auto, a preset or Custom for Edit's size, and repairs anything else", () => {
    for (const good of [AUTO, CUSTOM, "4:3"]) expect(sanitizeOptions({ editAspect: good }, CAPS)).toMatchObject({ options: { editAspect: good }, repaired: false });
    const bad = sanitizeOptions({ editAspect: "banana" }, CAPS);
    expect(bad.repaired).toBe(true);
    expect(bad.options.editAspect).toBe(AUTO);
  });

  it("Generate's size cannot be Auto", () => {
    expect(sanitizeOptions({ aspect: AUTO }, CAPS)).toMatchObject({ options: { aspect: "1:1" }, repaired: true });
  });

  it("accepts the resolutions the server offers and repairs anything else", () => {
    expect(sanitizeOptions({ resolution: 2048 }, CAPS)).toMatchObject({ options: { resolution: 2048 }, repaired: false });
    expect(sanitizeOptions({ resolution: 1536 }, CAPS)).toMatchObject({ options: { resolution: 1024 }, repaired: true });
    const only1k: Capabilities = { ...CAPS, limits: { ...CAPS.limits, resolutions: [1024] } };
    expect(sanitizeOptions({ resolution: 2048 }, only1k)).toMatchObject({ options: { resolution: 1024 }, repaired: true });
  });

  it("the size choice that applies depends on the mode", () => {
    const o = { ...defaultOptions(CAPS), aspect: "16:9", editAspect: "3:4" };
    expect(sizeKey({ ...o, mode: "generate" })).toBe("16:9");
    expect(sizeKey({ ...o, mode: "edit" })).toBe("3:4");
    expect(baseSize({ ...o, mode: "generate" }, CAPS)).toEqual({ width: 2752, height: 1536 });
    expect(baseSize({ ...o, mode: "edit" }, CAPS)).toEqual({ width: 1792, height: 2400 });
  });

  it("only an Edit on Auto is Auto", () => {
    expect(isAutoSize(edit())).toBe(true);
    expect(isAutoSize(edit({ editAspect: "1:1" }))).toBe(false);
    expect(isAutoSize({ ...defaultOptions(CAPS), mode: "generate", editAspect: AUTO })).toBe(false);
  });

  it("an Edit on Auto has no scale, whatever the scale picker had", () => {
    expect(effectiveScale(edit({ scale: 50 }), CAPS)).toBe(100);
    expect(effectiveScale(edit({ editAspect: "1:1", scale: 50 }), CAPS)).toBe(50);
  });

  it("the size is worded Auto, or as width × height", () => {
    expect(sizeLabel(edit(), CAPS)).toBe("Auto");
    expect(sizeLabel(edit({ editAspect: "16:9" }), CAPS)).toBe("2752×1536");
    expect(sizeLabel(edit({ editAspect: "16:9", scale: 50 }), CAPS)).toBe("1376×768");
    expect(sizeLabel({ ...defaultOptions(CAPS), mode: "generate" }, CAPS)).toBe("2048×2048");
  });

  it("only a custom size that is in use is checked", () => {
    const badCustom = { customWidth: 1000, customHeight: 1000 };
    expect(optionsProblem(edit({ editAspect: CUSTOM, ...badCustom }), CAPS)).toContain("multiple of 32");
    expect(optionsProblem(edit({ editAspect: AUTO, aspect: CUSTOM, ...badCustom }), CAPS)).toBeNull(); // Generate's choice, not Edit's
    expect(optionsProblem({ ...defaultOptions(CAPS), mode: "generate", editAspect: CUSTOM, ...badCustom }, CAPS)).toBeNull();
  });

  it("the resolution is worded 1K or 2K", () => {
    expect([resolutionLabel(1024), resolutionLabel(2048)]).toEqual(["1K", "2K"]);
  });
});

describe("editing: the request", () => {
  const edit = (over: Partial<ReturnType<typeof defaultOptions>> = {}) => ({ ...defaultOptions(CAPS), mode: "edit" as const, ...over });
  const inputs = [{ upload_id: "u1" }, { image_id: "i2" }];

  it("on Auto sends no size, the images in order, and the resolution", () => {
    expect(buildEditRequest("  put it there ", edit(), CAPS, inputs, null)).toEqual({
      mode: "edit", prompt: "put it there", input_images: inputs,
      options: { width: null, height: null, steps: 40, seed: null, num_images: 1, negative_prompt: null, cfg_scale: null, transparent: false, resolution: 1024 },
    });
  });

  it("sends the resolution chosen", () => {
    expect(buildEditRequest("x", edit({ resolution: 2048 }), CAPS, inputs, null).options.resolution).toBe(2048);
  });

  it("names the image the result follows, but only on Auto", () => {
    expect(buildEditRequest("x", edit(), CAPS, inputs, 1).options.shape_from).toBe(1);
    expect(buildEditRequest("x", edit(), CAPS, inputs, null).options).not.toHaveProperty("shape_from");
    expect(buildEditRequest("x", edit({ editAspect: "1:1" }), CAPS, inputs, 1).options).not.toHaveProperty("shape_from");
  });

  it("with a size chosen, sends it, at the scale chosen", () => {
    const body = buildEditRequest("x", edit({ editAspect: "16:9", scale: 50 }), CAPS, inputs, null);
    expect([body.options.width, body.options.height]).toEqual([1376, 768]);
    const custom = buildEditRequest("x", edit({ editAspect: CUSTOM, customWidth: 1024, customHeight: 512 }), CAPS, inputs, null);
    expect([custom.options.width, custom.options.height]).toEqual([1024, 512]);
  });

  it("carries the seed, negative prompt, guidance, images per click and Transparent", () => {
    const options = edit({ seedLocked: true, seed: 99, negativePrompt: " blurry ", guidance: 4, numImages: 3, transparent: true });
    expect(buildEditRequest("x", options, CAPS, inputs, null).options).toMatchObject({
      seed: 99, negative_prompt: "blurry", cfg_scale: 4, num_images: 3, transparent: true,
    });
  });

  it("is an Edit whatever mode the options say, and never a draft or a regenerate-larger", () => {
    const body = buildEditRequest("x", { ...edit({ editAspect: "1:1", scale: 50 }), mode: "generate" }, CAPS, inputs, null);
    expect(body.mode).toBe("edit");
    expect(body.options).not.toHaveProperty("draft");
    expect(body.options).not.toHaveProperty("full");
  });

  it("the Generate request ignores Edit's choices", () => {
    const body = buildRequest("x", { ...defaultOptions(CAPS), mode: "edit", aspect: "16:9", editAspect: AUTO, resolution: 2048 }, CAPS);
    expect(body.mode).toBe("generate");
    expect([body.options.width, body.options.height]).toEqual([2752, 1536]);
    expect(body).not.toHaveProperty("input_images");
    expect(body.options).not.toHaveProperty("resolution");
  });

  it("Transparent is allowed in Edit (decision #31)", () => {
    expect(buildEditRequest("x", edit({ transparent: true }), CAPS, inputs, null).options.transparent).toBe(true);
    const unsupported: Capabilities = { ...CAPS, supports: { ...CAPS.supports, transparent: false } };
    expect(buildEditRequest("x", edit({ transparent: true }), unsupported, inputs, null).options.transparent).toBe(false);
  });
});

describe("editing: Reuse, Retry and the summary", () => {
  const input = (position: number, id = `in${position}`) => ({
    position, role: "reference" as const, id, width: 600, height: 400, has_alpha: false, url: `/api/images/${id}`, thumb_url: `/api/images/${id}/thumb`, can_4k: false, four_k_size: null, can_enlarge: false, enlarge_size: null, four_k: null,
  });
  const editRun = (options: Partial<ImageRun["options"]> = {}, extra: Partial<ImageRun> = {}): ImageRun =>
    makeRun({
      mode: "edit",
      inputs: [input(1), input(2)],
      options: { ...makeRun().options, width: null, height: null, steps: 30, seed: 7, resolution: 1024, shape_from: null, roles: ["reference", "reference"], ...options },
      ...extra,
    });

  it("Reuse of an Edit run on Auto goes back to Edit on Auto, with its resolution, seed locked", () => {
    const next = optionsFromRun(editRun({ resolution: 2048, num_images: 2, negative_prompt: "blurry", cfg_scale: 4, transparent: true }), CAPS,
      { ...defaultOptions(CAPS), editAspect: "3:4", scale: 50 });
    expect(next).toMatchObject({ mode: "edit", editAspect: AUTO, scale: 100, resolution: 2048, steps: 30, seedLocked: true, seed: 7, numImages: 2,
      negativePrompt: "blurry", guidance: 4, transparent: true });
  });

  it("Reuse of an Edit run with a size restores the preset and scale that give it, else a custom size", () => {
    const preset = optionsFromRun(editRun({ width: 1376, height: 768 }), CAPS, defaultOptions(CAPS));
    expect([preset.editAspect, preset.scale]).toEqual(["16:9", 50]);
    const custom = optionsFromRun(editRun({ width: 1000 - 8, height: 640 }), CAPS, defaultOptions(CAPS));
    expect([custom.editAspect, custom.customWidth, custom.customHeight]).toEqual([CUSTOM, 992, 640]);
  });

  it("Reuse of an Edit run leaves Generate's own size alone", () => {
    const current = { ...defaultOptions(CAPS), aspect: "9:16", scale: 25 as Scale };
    expect(optionsFromRun(editRun({ width: 1376, height: 768 }), CAPS, current).aspect).toBe("9:16");
  });

  it("Reuse of a Generate run leaves Edit's choices alone", () => {
    const current = { ...defaultOptions(CAPS), editAspect: "3:2", resolution: 2048 as const };
    const next = optionsFromRun(makeRun(), CAPS, current);
    expect([next.mode, next.editAspect, next.resolution]).toEqual(["generate", "3:2", 2048]);
  });

  it("Reuse of an Edit run on a server that cannot edit stays in the current mode", () => {
    const noEdit: Capabilities = { ...CAPS, modes: ["generate"] as Mode[] };
    expect(optionsFromRun(editRun(), noEdit, defaultOptions(noEdit)).mode).toBe("generate");
  });

  it("Retry of an Edit run sends its images by id, in order, with their roles, and its resolution and shape", () => {
    const run = editRun({ resolution: 2048, shape_from: 1, roles: ["reference", "mask"] }, { inputs: [input(1, "a"), { ...input(2, "b"), role: "mask" }] });
    const body = retryRequest(run);
    expect(body.mode).toBe("edit");
    expect(body.input_images).toEqual([{ image_id: "a", role: "reference" }, { image_id: "b", role: "mask" }]);
    expect(body.options).toMatchObject({ width: null, height: null, resolution: 2048, shape_from: 1, seed: 7, draft: false });
  });

  it("Retry of a Generate run has no images, resolution or shape", () => {
    const body = retryRequest(makeRun());
    expect(body).not.toHaveProperty("input_images");
    expect(body.options).not.toHaveProperty("resolution");
    expect(body.options).not.toHaveProperty("shape_from");
  });

  it("the summary says Auto and the resolution for an Edit, and Transparent in either mode", () => {
    const edit = { ...defaultOptions(CAPS), mode: "edit" as const };
    expect(summarize(edit, CAPS)).toBe("Auto · 1K · 40 steps · random seed");
    expect(summarize({ ...edit, editAspect: "1:1", scale: 50, resolution: 2048, transparent: true }, CAPS)).toBe("1024×1024 (50%) · 2K · 40 steps · random seed · transparent");
    expect(summarize({ ...defaultOptions(CAPS), transparent: true }, CAPS)).toBe("2048×2048 · 40 steps · random seed · transparent");
  });
});
