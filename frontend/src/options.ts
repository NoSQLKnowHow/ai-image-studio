// Per-run options: defaults, validation, browser persistence, and turning them into requests
// (DESIGN.md §6). Every saved field is validated on its own, so a corrupt or outdated value can
// only ever reset that one field to its default, never break the page.

import type { Capabilities, CreateRunBody, FullSize, InputRef, Mode, Range, Run } from "./types";

export const OPTIONS_KEY = "studio.options.v1";
export const PROMPT_KEY = "studio.prompt.v1";
export const CUSTOM = "custom";
/** Edit's own size choice: let the pipeline size the result from the images (DESIGN.md §21.4). */
export const AUTO = "auto";
export const DEFAULT_GUIDANCE = 4;

/** Edit's output resolution: 1K (about 1 megapixel) or 2K (about 4). It sizes the result and every input. */
export const RESOLUTIONS = [1024, 2048] as const;
export type Resolution = (typeof RESOLUTIONS)[number];
export const resolutionLabel = (resolution: number): string => (resolution === 2048 ? "2K" : "1K");

/** Percent of the selected size's width and height (DESIGN.md §22.1). */
export const SCALES = [100, 75, 50, 25] as const;
export type Scale = (typeof SCALES)[number];

export interface Size {
  width: number;
  height: number;
}

export interface Options {
  mode: Mode;
  aspect: string; // Generate's size: a preset name from the capabilities, or CUSTOM
  editAspect: string; // Edit's size: AUTO, a preset name, or CUSTOM (the width and height below are shared)
  resolution: Resolution; // Edit only: 1K or 2K
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
    editAspect: AUTO,
    resolution: 1024,
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
    editAspect: pick("editAspect", (v) => v === AUTO || v === CUSTOM || (typeof v === "string" && v in caps.aspect_ratios)),
    resolution: pick("resolution", (v) => (caps.limits.resolutions as readonly unknown[]).includes(v) && (RESOLUTIONS as readonly unknown[]).includes(v)),
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
/** The size choice that applies now: Edit has its own (it can be Auto), Generate has its own. */
export function sizeKey(options: Options): string {
  return options.mode === "edit" ? options.editAspect : options.aspect;
}

/** Edit with Size on Auto: no size is sent, and the pipeline sizes the result from the images (Resolution and,
 *  with several images, "Result follows image N" decide how). */
export function isAutoSize(options: Options): boolean {
  return options.mode === "edit" && options.editAspect === AUTO;
}

export function baseSize(options: Options, caps: Capabilities): Size {
  const key = sizeKey(options);
  if (key === CUSTOM) return { width: options.customWidth, height: options.customHeight };
  const [width, height] = caps.aspect_ratios[key] ?? caps.aspect_ratios[caps.defaults.aspect_ratio]; // Auto: only a stand-in
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
  return isAutoSize(options) ? 100 : usableScale(options.scale, baseSize(options, caps), caps);
}

/** The size as the page words it: "Auto" for an Edit that lets the pipeline decide, else width × height. */
export function sizeLabel(options: Options, caps: Capabilities): string {
  if (isAutoSize(options)) return "Auto";
  const { width, height } = resolveSize(options, caps);
  return `${width}×${height}`;
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
  return sizeKey(options) === CUSTOM ? sizeProblem(options.customWidth, options.customHeight, caps) : null;
}

/** True when `full` is bigger than `size` in at least one side and smaller in neither: the only case the server
 *  accepts as "the full size of this run" and the only one Regenerate larger is offered for (DESIGN.md §23.1). */
export function isLarger(full: Size, size: Size): boolean {
  return full.width >= size.width && full.height >= size.height && (full.width > size.width || full.height > size.height);
}

/** What a run made smaller than selected remembers about the size it stands in for: the chosen size at 100%, with
 *  the chosen steps. Nothing when this run is already the full size. */
function fullSizeFor(size: Size, options: Options, caps: Capabilities): { full?: FullSize } {
  const base = baseSize(options, caps);
  return isLarger(base, size) ? { full: { width: base.width, height: base.height, steps: options.steps } } : {};
}

/** A Generate request: the size chosen at the scale chosen (and, when that is smaller, the size to go back to). */
export function buildRequest(prompt: string, options: Options, caps: Capabilities): CreateRunBody {
  const { width, height } = resolveSize({ ...options, mode: "generate" }, caps);
  const supports = caps.supports;
  return {
    mode: "generate",
    prompt: prompt.trim(),
    options: {
      width,
      height,
      steps: options.steps,
      seed: options.seedLocked ? options.seed : null, // null: the server picks one and records it
      num_images: options.numImages,
      negative_prompt: supports.negative_prompt && options.negativePrompt.trim() ? options.negativePrompt.trim() : null,
      cfg_scale: supports.cfg_scale ? options.guidance : null,
      transparent: !!supports.transparent && options.transparent,
      ...fullSizeFor({ width, height }, { ...options, mode: "generate" }, caps),
    },
  };
}

/** An Edit request (DESIGN.md §21.6): the images in the order shown, the resolution, and either an explicit size or,
 *  with Size on Auto, none, optionally with `shapeFrom` naming the image the result follows (decision #30). A fixed
 *  size is scaled by the scale picker like Generate's. Never a draft, and never a `full` size (Generate only). */
export function buildEditRequest(prompt: string, options: Options, caps: Capabilities, inputs: InputRef[], shapeFrom: number | null): CreateRunBody {
  const edit: Options = { ...options, mode: "edit" };
  const auto = isAutoSize(edit);
  const size = auto ? null : resolveSize(edit, caps);
  const supports = caps.supports;
  return {
    mode: "edit",
    prompt: prompt.trim(),
    input_images: inputs,
    options: {
      width: size?.width ?? null,
      height: size?.height ?? null,
      steps: options.steps,
      seed: options.seedLocked ? options.seed : null,
      num_images: options.numImages,
      negative_prompt: supports.negative_prompt && options.negativePrompt.trim() ? options.negativePrompt.trim() : null,
      cfg_scale: supports.cfg_scale ? options.guidance : null,
      transparent: !!supports.transparent && options.transparent,
      resolution: options.resolution,
      ...(auto && shapeFrom ? { shape_from: shapeFrom } : {}),
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
      ...fullSizeFor({ width, height }, options, caps),
    },
  };
}

/** The size and steps Regenerate larger would use for this run, or null when it isn't offered: only a finished
 *  Generate run that remembers a full size bigger than itself (so not a full-size run, a failed or canceled one, or one
 *  made before version 1.4). */
export function largerTarget(run: Run): FullSize | null {
  const { width, height, full } = run.options;
  if (run.status !== "done" || run.mode !== "generate" || !full || !width || !height) return null;
  return isLarger(full, { width, height }) ? full : null;
}

/** Regenerate larger: the same prompt, options and seeds at the full size and steps, as an ordinary run (no draft, and
 *  no `full` of its own, so it has no button in turn). The picture will differ from the small one (DESIGN.md §23.1).
 *  Given `image` (the viewer), it enlarges that one image: its own seed and a single image (§24.1). */
export function largerRequest(run: Run, image?: { seed: number }): CreateRunBody | null {
  const target = largerTarget(run);
  if (!target) return null;
  const o = run.options;
  return {
    mode: "generate",
    prompt: run.prompt,
    options: {
      width: target.width,
      height: target.height,
      steps: target.steps,
      seed: image ? image.seed : o.seed,
      num_images: image ? 1 : o.num_images,
      negative_prompt: o.negative_prompt,
      cfg_scale: o.cfg_scale,
      transparent: o.transparent,
      draft: false,
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
  if (run.mode === "edit") return editOptionsFromRun(run, caps, current);
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

/** Reuse of an Edit run: the same options, with Size back on Auto if the run had no size, and the resolution it
 *  used; the images come back in the tray (the page does that). Seed locked, as for any Reuse (decision #11). */
function editOptionsFromRun(run: Run, caps: Capabilities, current: Options): Options {
  const { width, height } = run.options;
  const match = width && height ? presetAndScale(width, height, caps) : null;
  const size = !width || !height
    ? { editAspect: AUTO, scale: 100 as Scale }
    : { editAspect: match?.aspect ?? CUSTOM, scale: match?.scale ?? (100 as Scale), customWidth: match ? current.customWidth : width, customHeight: match ? current.customHeight : height };
  const merged: Options = {
    ...current,
    mode: caps.modes.includes("edit") ? "edit" : current.mode,
    ...size,
    resolution: run.options.resolution === 2048 ? 2048 : 1024,
    negativePrompt: run.options.negative_prompt ?? "",
    guidance: run.options.cfg_scale,
    transparent: run.options.transparent,
    steps: run.options.steps,
    seedLocked: true,
    seed: run.options.seed,
    numImages: run.options.num_images,
  };
  return sanitizeOptions(merged, caps).options;
}

/** Retry: exactly the same request again, same seed included. */
export function retryRequest(run: Run): CreateRunBody {
  const o = run.options;
  return {
    mode: run.mode,
    prompt: run.prompt,
    // An edit's images are named by their ids: the run owns copies, and the server copies them again for the new run.
    ...(run.mode === "edit" ? { input_images: run.inputs.map((input) => ({ image_id: input.id, role: input.role })) } : {}),
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
      ...(o.full ? { full: o.full } : {}), // so the retried run gets its Regenerate larger button too
      ...(o.resolution ? { resolution: o.resolution } : {}),
      ...(o.shape_from ? { shape_from: o.shape_from } : {}),
    },
  };
}

export function summarize(options: Options, caps: Capabilities): string {
  const scale = effectiveScale(options, caps);
  const parts = [`${sizeLabel(options, caps)}${scale < 100 ? ` (${scale}%)` : ""}`];
  if (options.mode === "edit") parts.push(resolutionLabel(options.resolution));
  parts.push(`${options.steps} steps`, options.seedLocked ? `seed ${options.seed}` : "random seed");
  if (options.numImages > 1) parts.push(`${options.numImages} images`);
  if (caps.supports.cfg_scale && options.guidance !== null) parts.push(`guidance ${options.guidance}`);
  if (options.transparent) parts.push("transparent");
  return parts.join(" · ");
}

export function randomSeed(max: number): number {
  const value = new Uint32Array(1);
  crypto.getRandomValues(value);
  return value[0] % (max + 1);
}
