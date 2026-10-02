import { describe, expect, it } from "vitest";
import {
  CUSTOM,
  OPTIONS_KEY,
  buildRequest,
  defaultOptions,
  loadOptions,
  optionsFromRun,
  optionsProblem,
  retryRequest,
  sanitizeOptions,
  saveOptions,
  summarize,
  type KeyValueStore,
} from "./options";
import { CAPS, makeRun } from "./testdata";

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

  it("rejects modes the server doesn't offer yet", () => {
    expect(sanitizeOptions({ mode: "edit" }, CAPS).options.mode).toBe("generate");
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
      options: { width: 1024, height: 768, steps: 25, seed: 777, num_images: 2, negative_prompt: "blurry", cfg_scale: 4, transparent: false },
    });
  });
});
