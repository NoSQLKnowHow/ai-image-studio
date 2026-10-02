// Per-run options: defaults, validation, browser persistence, and turning them into requests
// (DESIGN.md §6). Every saved field is validated on its own, so a corrupt or outdated value can
// only ever reset that one field to its default, never break the page.

import type { Capabilities, CreateRunBody, Mode, Range, Run } from "./types";

export const OPTIONS_KEY = "studio.options.v1";
export const PROMPT_KEY = "studio.prompt.v1";
export const CUSTOM = "custom";
export const DEFAULT_GUIDANCE = 4;

/** Percent of the selected size's width and height (DESIGN.md §22.1). */
export const SCALES = [100, 75, 50, 25] as const;
export type Scale = (typeof SCALES)[number];

export interface Size {
  width: number;
  height: number;
}

export interface Options {
  mode: Mode;
  aspect: string; // a preset name from the capabilities, or CUSTOM
  customWidth: number;
  customHeight: number;
  scale: Scale; // how much of that size to generate at
  steps: number;
  seedLocked: boolean; // false = a new random seed every run (decision #11)
  seed: number;
  numImages: number;
  negativePrompt: string;
  guidance: number | null; // null = model default (no guidance)
  transparent: boolean;
}

export function defaultOptions(caps: Capabilities): Options {
  return {
    mode: caps.modes.includes(caps.defaults.mode) ? caps.defaults.mode : "generate",
    aspect: caps.defaults.aspect_ratio,
    customWidth: caps.defaults.width,
    customHeight: caps.defaults.height,
    scale: 100,
    steps: caps.defaults.steps,
    seedLocked: false,
    seed: 42,
    numImages: caps.defaults.num_images,
    negativePrompt: "",
    guidance: null,
    transparent: caps.defaults.transparent,
  };
}

// ------------------------------------------------------------------ storage that never throws
export interface KeyValueStore {
  readonly available: boolean;
  get(key: string): string | null;
  set(key: string, value: string): void;
}

export function browserStore(): KeyValueStore {
  let available = true;
  try {
    localStorage.setItem("studio.__probe__", "1");
    localStorage.removeItem("studio.__probe__");
  } catch {
    available = false; // private window or blocked storage: work in memory only
  }
  return {
    available,
    get: (key) => {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    set: (key, value) => {
      try {
        localStorage.setItem(key, value);
      } catch {
        /* quota or blocked: options just won't persist */
      }
    },
  };
}

// ------------------------------------------------------------------ validation
const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const inRange = (range: Range) => (v: unknown) => isInt(v) && v >= range.min && v <= range.max;

export function sizeProblem(width: number, height: number, caps: Capabilities): string | null {
  const s = caps.limits.size;
  if (!isInt(width) || !isInt(height)) return "Width and height must be whole numbers.";
  if (width < s.min || width > s.max || height < s.min || height > s.max)
    return `Each side must be between ${s.min} and ${s.max} pixels.`;
  if (width % s.multiple || height % s.multiple) return `Each side must be a multiple of ${s.multiple}.`;
  if (width * height > s.max_pixels)
    return `${width}×${height} is ${((width * height) / 1e6).toFixed(2)} MP; the limit is ${(s.max_pixels / 1e6).toFixed(1)} MP.`;
  return null;
}

export function sanitizeOptions(raw: unknown, caps: Capabilities): { options: Options; repaired: boolean } {
  const defaults = defaultOptions(caps);
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    return { options: defaults, repaired: raw !== null && raw !== undefined };
  }
  const r = raw as Record<string, unknown>;
  let repaired = false;
  function pick<K extends keyof Options>(key: K, valid: (v: unknown) => boolean): Options[K] {
    if (!(key in r)) return defaults[key]; // a field added in a later version: just use its default
    if (valid(r[key])) return r[key] as Options[K];
    repaired = true;
    return defaults[key];
  }
  const { limits } = caps;
  const options: Options = {
    mode: pick("mode", (v) => typeof v === "string" && caps.modes.includes(v as Mode)),
    aspect: pick("aspect", (v) => v === CUSTOM || (typeof v === "string" && v in caps.aspect_ratios)),
    customWidth: pick("customWidth", isInt),
    customHeight: pick("customHeight", isInt),
    scale: pick("scale", (v) => (SCALES as readonly unknown[]).includes(v)),
    steps: pick("steps", inRange(limits.steps)),
    seedLocked: pick("seedLocked", (v) => typeof v === "boolean"),
    seed: pick("seed", inRange(limits.seed)),
    numImages: pick("numImages", inRange(limits.num_images)),
    negativePrompt: pick("negativePrompt", (v) => typeof v === "string" && v.length <= limits.prompt_chars),
    guidance: pick("guidance", (v) => v === null || (isNum(v) && v >= limits.cfg_scale.min && v <= limits.cfg_scale.max)),
    transparent: pick("transparent", (v) => typeof v === "boolean"),
  };
  if (sizeProblem(options.customWidth, options.customHeight, caps)) {
    repaired = true;
    options.customWidth = defaults.customWidth;
    options.customHeight = defaults.customHeight;
  }
  return { options, repaired };
}

export function loadOptions(caps: Capabilities, store: KeyValueStore): { options: Options; repaired: boolean } {
  const text = store.get(OPTIONS_KEY);
  if (text === null) return { options: defaultOptions(caps), repaired: false };
  try {
    return sanitizeOptions(JSON.parse(text), caps);
  } catch {
    return { options: defaultOptions(caps), repaired: true };
  }
}

export function saveOptions(options: Options, store: KeyValueStore): void {
  store.set(OPTIONS_KEY, JSON.stringify(options));
}

// ------------------------------------------------------------------ requests
/** The size chosen in Options, before any scale. */
export function baseSize(options: Options, caps: Capabilities): Size {
  if (options.aspect === CUSTOM) return { width: options.customWidth, height: options.customHeight };
  const [width, height] = caps.aspect_ratios[options.aspect] ?? caps.aspect_ratios[caps.defaults.aspect_ratio];
  return { width, height };
}

/** `side` at `scale` percent, to the nearest multiple the model needs (halves round up): 1200 at 50% of 2400 is
 *  not allowed, so 2400 → 1216 with 32; 2048 → 1024. */
export function scaledSide(side: number, scale: number, multiple: number): number {
  return Math.round((side * scale) / 100 / multiple) * multiple;
}

export function scaledSize(base: Size, scale: number, caps: Capabilities): Size {
  if (scale === 100) return base; // exactly as chosen; never nudged to a multiple
  const { multiple } = caps.limits.size;
  return { width: scaledSide(base.width, scale, multiple), height: scaledSide(base.height, scale, multiple) };
}

/** Why a scale can't be used with this size (a side would fall under the minimum), or null. */
export function scaleProblem(base: Size, scale: Scale, caps: Capabilities): string | null {
  if (scale === 100) return null;
  const { width, height } = scaledSize(base, scale, caps);
  const { min } = caps.limits.size;
  return width < min || height < min
    ? `${scale}% of ${base.width}×${base.height} would be ${width}×${height}; each side must be at least ${min}.`
    : null;
}

/** The scale actually used: the saved one, or the nearest larger one that works for this size. */
export function usableScale(chosen: Scale, base: Size, caps: Capabilities): Scale {
  for (let i = SCALES.indexOf(chosen); i >= 0; i--) if (!scaleProblem(base, SCALES[i], caps)) return SCALES[i];
  return 100;
}

export function effectiveScale(options: Options, caps: Capabilities): Scale {
  return usableScale(options.scale, baseSize(options, caps), caps);
}

/** The size a normal run will use: the chosen size at the effective scale. */
export function resolveSize(options: Options, caps: Capabilities): Size {
  const base = baseSize(options, caps);
  return scaledSize(base, usableScale(options.scale, base, caps), caps);
}

/** A draft keeps the chosen shape but makes its long side the draft size (DESIGN.md §22.2). Sides stay within
 *  the model's minimum, so below 512 only squares fit exactly. */
export function draftSize(base: Size, caps: Capabilities): Size {
  const { multiple, min } = caps.limits.size;
  const long = Math.min(caps.limits.draft.long_side, Math.max(base.width, base.height)); // never bigger than the real size
  const k = long / Math.max(base.width, base.height);
  const fit = (side: number) => Math.min(long, Math.max(min, Math.round((side * k) / multiple) * multiple));
  return { width: fit(base.width), height: fit(base.height) };
}

/** Why these options can't be submitted, or null if they can. */
export function optionsProblem(options: Options, caps: Capabilities): string | null {
  return options.aspect === CUSTOM ? sizeProblem(options.customWidth, options.customHeight, caps) : null;
}

export function buildRequest(prompt: string, options: Options, caps: Capabilities): CreateRunBody {
  const { width, height } = resolveSize(options, caps);
  const supports = caps.supports;
  return {
    mode: options.mode,
    prompt: prompt.trim(),
    options: {
      width,
      height,
      steps: options.steps,
      seed: options.seedLocked ? options.seed : null, // null: the server picks one and records it
      num_images: options.numImages,
      negative_prompt: supports.negative_prompt && options.negativePrompt.trim() ? options.negativePrompt.trim() : null,
      cfg_scale: supports.cfg_scale ? options.guidance : null,
      transparent: options.mode === "generate" && !!supports.transparent && options.transparent,
    },
  };
}

/** A Draft: the chosen shape small, at most the draft steps, one image, flagged so the server lets it jump the
 *  queue and the card can say so. Everything else is as for a normal run. */
export function draftRequest(prompt: string, options: Options, caps: Capabilities): CreateRunBody {
  const { width, height } = draftSize(baseSize(options, caps), caps);
  const supports = caps.supports;
  return {
    mode: "generate",
    prompt: prompt.trim(),
    options: {
      width,
      height,
      steps: Math.min(options.steps, caps.limits.draft.steps),
      seed: options.seedLocked ? options.seed : null,
      num_images: 1,
      negative_prompt: supports.negative_prompt && options.negativePrompt.trim() ? options.negativePrompt.trim() : null,
      cfg_scale: supports.cfg_scale ? options.guidance : null,
      transparent: !!supports.transparent && options.transparent,
      draft: true,
    },
  };
}

/** The preset and scale that give exactly this size, preferring 100%, so "1:1 at 50%" comes back as that. */
function presetAndScale(width: number, height: number, caps: Capabilities): { aspect: string; scale: Scale } | null {
  for (const scale of SCALES) {
    for (const [name, [w, h]] of Object.entries(caps.aspect_ratios)) {
      const size = scaledSize({ width: w, height: h }, scale, caps);
      if (size.width === width && size.height === height) return { aspect: name, scale };
    }
  }
  return null;
}

/** Reuse: everything the run used, seed locked, so a tweaked prompt is a fair comparison (decision #11). A draft is
 *  different: its small size, few steps and seed are not what you want next, so only the prompt-side options come back
 *  and your own size, steps and seed stay as they are (DESIGN.md §22.2). */
export function optionsFromRun(run: Run, caps: Capabilities, current: Options): Options {
  const { width, height } = run.options;
  const common = {
    mode: caps.modes.includes(run.mode) ? run.mode : current.mode,
    negativePrompt: run.options.negative_prompt ?? "",
    guidance: run.options.cfg_scale,
    transparent: run.options.transparent,
  };
  if (run.options.draft) return sanitizeOptions({ ...current, ...common }, caps).options;
  const match = width && height ? presetAndScale(width, height, caps) : null;
  const merged: Options = {
    ...current,
    ...common,
    aspect: match?.aspect ?? (width && height ? CUSTOM : current.aspect),
    scale: match?.scale ?? 100,
    customWidth: !match && width ? width : current.customWidth,
    customHeight: !match && height ? height : current.customHeight,
    steps: run.options.steps,
    seedLocked: true,
    seed: run.options.seed,
    numImages: run.options.num_images,
  };
  return sanitizeOptions(merged, caps).options; // clamp to whatever today's limits are
}

/** Retry: exactly the same request again, same seed included. */
export function retryRequest(run: Run): CreateRunBody {
  const o = run.options;
  return {
    mode: run.mode,
    prompt: run.prompt,
    options: {
      width: o.width,
      height: o.height,
      steps: o.steps,
      seed: o.seed,
      num_images: o.num_images,
      negative_prompt: o.negative_prompt,
      cfg_scale: o.cfg_scale,
      transparent: o.transparent,
      draft: o.draft === true,
    },
  };
}

export function summarize(options: Options, caps: Capabilities): string {
  const { width, height } = resolveSize(options, caps);
  const scale = effectiveScale(options, caps);
  const parts = [`${width}×${height}${scale < 100 ? ` (${scale}%)` : ""}`, `${options.steps} steps`, options.seedLocked ? `seed ${options.seed}` : "random seed"];
  if (options.numImages > 1) parts.push(`${options.numImages} images`);
  if (caps.supports.cfg_scale && options.guidance !== null) parts.push(`guidance ${options.guidance}`);
  if (options.transparent && options.mode === "generate") parts.push("transparent");
  return parts.join(" · ");
}

export function randomSeed(max: number): number {
  const value = new Uint32Array(1);
  crypto.getRandomValues(value);
  return value[0] % (max + 1);
}
