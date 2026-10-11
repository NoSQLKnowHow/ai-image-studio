// The Vector tab's logic (DESIGN.md §31, version 1.17; the page's part is §31.12.8): the form and its limits, the palette rules, the line that says how many
// layers there will be, the request, Reuse and Retry, the saved copy of the form, and the words on a card. Pure functions, so every rule is tested without
// a browser (the same split as music.ts and options.ts).
//
// Two rules run through all of it, both from §31.12.8:
//  - The limits and defaults come from `capabilities.limits.vector`, never from a number written here. (The one exception is the PNG size range, which the
//    server's capabilities do not carry yet: see `pngRange`.)
//  - The page never writes the recipe, the words that go to the model. It asks `POST /api/vector/recipe` for them and shows what comes back.

import type { KeyValueStore } from "./options";
import type {
  Capabilities, CreateVectorBody, Progress, Range, VectorArrangement, VectorCheck, VectorItem, VectorKind, VectorLayersBy, VectorLimits, VectorRecipeRequest,
  VectorRun,
} from "./types";

export const VECTOR_FORM_KEY = "studio.vector.v1";

/** White is the bare material (DESIGN.md §31.7 answer 2): it counts toward the number of colours, never gets a layer, and is never in the palette. */
export const WHITE = "#FFFFFF";
export const WHITE_LABEL = "White, the bare material, no layer";

/** The PNG size box starts here (the page's choice, §31.12.8: sharp at 2K by default; the stored preview is 1024 and any size up to the range's end is drawn
 *  on request). */
export const DEFAULT_PNG_PX = 2048;
/** The PNG's long side in pixels, as the route accepts it (§31.12.7: 16 to 8192, a 422 outside). `capabilities.limits.vector` does not carry this range in
 *  the contract, so it is written down here once, and `pngRange` prefers the server's if it ever sends `png_px`. */
export const VECTOR_PNG_RANGE: Range = { min: 16, max: 8192 };

/** The colours offered, in this order, when someone adds an ink: the first one the palette does not have yet. There are seven inks at most (8 colours
 *  less white), and this list is longer than that, so one is always free. White is not here and never can be. */
export const INK_SUGGESTIONS = ["#000000", "#FF0000", "#00AA00", "#0000FF", "#FFA500", "#800080", "#A52A2A", "#808080"] as const;

// ------------------------------------------------------------------ the form
export interface VectorForm {
  idea: string; // the Prompt: what to make. The server's recipe adds the wording around it
  kind: VectorKind;
  colours: number; // 2 to 8, white included
  palette: string[]; // the inks the person chose: uppercase #RRGGBB, never white, distinct, at most colours - 1. The picture chooses the rest
  widthMm: number;
  heightMm: number;
  layersBy: VectorLayersBy;
  arrangement: VectorArrangement; // what the person chose; by shape the run uses side by side whatever this says (`effectiveArrangement`)
  minFeatureMm: number;
  kerfMm: number;
  bleedMm: number;
  numImages: number; // versions
  seedLocked: boolean; // false = a new random seed every run, as on the Images tab
  seed: number;
  recipeEdited: boolean; // the box "What the studio sends" was changed by hand: Colours and Kind no longer rewrite it (until Rebuild)
  recipeText: string; // that hand-written text; only used while `recipeEdited`
}

export function defaultVectorForm(caps: Capabilities): VectorForm {
  const v = caps.limits.vector;
  return {
    idea: "", kind: v.defaults.kind, colours: v.colours.default, palette: [], widthMm: v.size_mm.default[0], heightMm: v.size_mm.default[1],
    layersBy: v.defaults.layers_by, arrangement: v.defaults.arrangement, minFeatureMm: v.min_feature_mm.default, kerfMm: v.kerf_mm.default,
    bleedMm: v.bleed_mm.default, numImages: v.versions.min, seedLocked: false, seed: 42, recipeEdited: false, recipeText: "",
  };
}

/** Whether the Vector tab can make anything here: the server says so (`capabilities.vector`) and lists `vector` among the modes (the image pipeline can
 *  generate and the libraries import, §31.12.7). */
export const vectorUsable = (caps: Capabilities): boolean => caps.vector.available && caps.modes.includes("vector");

/** What the page says in place of the form when Vector cannot run: the server's own reason and hint, or a plain sentence when it gave none. */
export function unavailableText(caps: Capabilities): { reason: string; hint: string | null } {
  return { reason: caps.vector.reason ?? "Vector can't run on this server as it is set up.", hint: caps.vector.hint };
}

// ------------------------------------------------------------------ the palette (white is never in it)
/** `#rrggbb` in any case as the stored form, `#RRGGBB`; null for anything else (the colour input only ever gives six hex digits, but a stored value may be
 *  anything). */
export function normaliseColour(text: unknown): string | null {
  return typeof text === "string" && /^#[0-9a-fA-F]{6}$/.test(text) ? text.toUpperCase() : null;
}

/** How many inks a number of colours leaves room for: white is one of the colours and gets no layer. */
export const inkCount = (colours: number): number => Math.max(0, colours - 1);

/** Why a palette cannot be sent with this many colours, or null. The same three rules as the server's `check_options`: at most colours - 1 entries, each
 *  a #RRGGBB in capitals, none white, none twice. */
export function paletteProblem(palette: readonly string[], colours: number): string | null {
  if (palette.length > inkCount(colours)) return `At most ${inkCount(colours)} ${inkCount(colours) === 1 ? "colour" : "colours"} besides white: white is always included and counts.`;
  const seen = new Set<string>();
  for (const colour of palette) {
    if (normaliseColour(colour) !== colour) return `${colour} is not a colour: it must be written #RRGGBB in capitals.`;
    if (colour === WHITE) return "White is the bare material and has no layer: list only the other colours.";
    if (seen.has(colour)) return `${colour} is listed twice.`;
    seen.add(colour);
  }
  return null;
}

/** Change the colour of ink `index` (the colour input's answer). White and a colour another ink already has are refused with the reason, and the palette
 *  is left as it was, so no wrong palette is ever held, not even for a moment. */
export function setInk(palette: readonly string[], index: number, colour: string): { palette: string[]; refused: string | null } {
  const next = normaliseColour(colour);
  if (next === null || index < 0 || index >= palette.length) return { palette: [...palette], refused: null };
  if (next === WHITE) return { palette: [...palette], refused: "White is the bare material and has no layer. Pick another colour." };
  if (palette.some((other, i) => i !== index && other === next)) return { palette: [...palette], refused: `${next} is already one of the inks. Pick another colour.` };
  return { palette: palette.map((other, i) => (i === index ? next : other)), refused: null };
}

/** Choose a colour for the next ink: the first suggestion that is not in use. Nothing is added when every ink already has one. */
export function addInk(palette: readonly string[], colours: number): string[] {
  if (palette.length >= inkCount(colours)) return [...palette];
  const free = INK_SUGGESTIONS.find((colour) => !palette.includes(colour));
  return free ? [...palette, free] : [...palette];
}

/** Let the picture choose ink `index` again: its colour leaves the palette. */
export const removeInk = (palette: readonly string[], index: number): string[] => palette.filter((_, i) => i !== index);

/** Set the number of colours. The palette keeps its first colours that still fit (with fewer colours there is room for fewer inks). */
export function withColours(form: VectorForm, colours: number): VectorForm {
  return { ...form, colours, palette: form.palette.slice(0, inkCount(colours)) };
}

// ------------------------------------------------------------------ the words the form shows
/** The live line under Colours (§31.12.8): how many layers there will be. With N colours that is at most N - 1, because white is the bare material and
 *  gets none; by shape it is up to the cap, one for each separate piece. "At most", because a picture with fewer colours than asked makes fewer layers
 *  and the card says so (§31.12.3 item 1). */
export function layersLine(form: Pick<VectorForm, "colours" | "layersBy">, limits: VectorLimits): string {
  if (form.layersBy === "shape") return `Up to ${limits.shape_layer_cap} pieces: one layer for each separate piece. White, the bare material, gets none.`;
  const layers = inkCount(form.colours);
  return layers === 1
    ? "1 layer. White, the bare material, gets none."
    : `Up to ${layers} layers, one for each ink. White, the bare material, gets none.`;
}

export const KIND_LABELS: Record<VectorKind, string> = { silhouette: "Silhouette or stencil", flat: "Flat colour art", line: "Line art" };
export const KIND_HINTS: Record<VectorKind, string> = {
  silhouette: "One bold shape against nothing.", flat: "Flat areas of solid colour.", line: "Outlines only.",
};
export const kindLabel = (kind: string): string => KIND_LABELS[kind as VectorKind] ?? kind;

export const LAYERS_BY_LABELS: Record<VectorLayersBy, string> = { colour: "Colour", shape: "Shape" };
export const ARRANGEMENT_LABELS: Record<VectorArrangement, string> = { side_by_side: "Side by side", stacked: "Stacked" };

/** The arrangement the run will use: by shape the pieces are separate by construction, so it is side by side whatever the control says (§31.12.3 item 4).
 *  The page greys the control then, and sends this value, so the run's own record says what was done. */
export const effectiveArrangement = (form: Pick<VectorForm, "layersBy" | "arrangement">): VectorArrangement =>
  form.layersBy === "shape" ? "side_by_side" : form.arrangement;

/** The Bleed box is used only when the layers are stacked (§31.12.2: "stacked only"), so it is greyed in every other case. */
export const bleedApplies = (form: Pick<VectorForm, "layersBy" | "arrangement">): boolean => effectiveArrangement(form) === "stacked";

/** A length in millimetres without trailing noise: 100 → "100", 100.5 → "100.5", 0.62 → "0.62". */
export const mmText = (mm: number): string => String(Number(mm.toFixed(3)));

/** Does the size on the form equal a preset's (so its button shows as pressed)? */
export const presetMatches = (form: Pick<VectorForm, "widthMm" | "heightMm">, preset: { width_mm: number; height_mm: number }): boolean =>
  form.widthMm === preset.width_mm && form.heightMm === preset.height_mm;

// ------------------------------------------------------------------ the recipe (the page only shows what the server writes)
/** The body for `POST /api/vector/recipe`, or null while there is no idea to write words around (the route would refuse an empty prompt, and there is
 *  nothing to show yet). */
export function recipeRequest(form: Pick<VectorForm, "idea" | "kind" | "colours" | "palette">): VectorRecipeRequest | null {
  const prompt = form.idea.trim();
  return prompt === "" ? null : { prompt, kind: form.kind, colours: form.colours, palette: [...form.palette] };
}

/** What `options.recipe` carries: the hand-written text once the box was edited, else null, which means "the server builds it" (§31.12.5). */
export const recipeToSend = (form: Pick<VectorForm, "recipeEdited" | "recipeText">): string | null => (form.recipeEdited ? form.recipeText : null);

/** The box "What the studio sends" is changed by hand: from now on it is the text that is sent, and the form no longer rewrites it. */
export const editRecipe = (form: VectorForm, text: string): VectorForm => ({ ...form, recipeEdited: true, recipeText: text });

/** Rebuild: take the hand-written text away, so the box shows what the server writes again. */
export const rebuildRecipe = (form: VectorForm): VectorForm => ({ ...form, recipeEdited: false, recipeText: "" });

/** Reuse restores the recipe a run used, but a run keeps only the text, not whether it was edited by hand (the server stores the text it used either way).
 *  So after Reuse the box starts unedited, and when the server's words for the restored idea arrive they are compared with the run's: the same text means
 *  the run used the server's own (the box stays automatic), a different text means it was changed by hand (or the wording has changed since), and then
 *  that text is kept as an edit. `built` is null when the server's words could not be had; the run's text is kept then too, because it is what the run used. */
export function reconcileReusedRecipe(form: VectorForm, runRecipe: string, built: string | null): VectorForm {
  return built === runRecipe ? form : editRecipe(form, runRecipe);
}

// ------------------------------------------------------------------ checking the form
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const inRange = (value: unknown, range: Range): value is number => isNumber(value) && value >= range.min && value <= range.max;
const isWhole = (value: unknown, range: Range): value is number => inRange(value, range) && Number.isInteger(value);

/** The reason a form cannot be sent yet, in words, or null. The server checks the same limits and answers 422 with its own wording; this is only so that
 *  the person finds out before pressing the button. */
export function vectorProblem(form: VectorForm, caps: Capabilities): string | null {
  const v = caps.limits.vector;
  const idea = form.idea.trim();
  if (idea === "") return "Describe what to make: write your idea in the Prompt box.";
  if (idea.length > caps.limits.prompt_chars) return `The prompt is ${idea.length} characters; the limit is ${caps.limits.prompt_chars}.`;
  if (!isWhole(form.colours, v.colours)) return `Colours must be a whole number from ${v.colours.min} to ${v.colours.max}.`;
  const palette = paletteProblem(form.palette, form.colours);
  if (palette) return palette;
  if (!v.kinds.includes(form.kind)) return "Choose a kind of artwork.";
  if (!inRange(form.widthMm, v.size_mm) || !inRange(form.heightMm, v.size_mm)) return `Each side of the sheet must be from ${v.size_mm.min} to ${v.size_mm.max} mm.`;
  if (!v.layers_by.includes(form.layersBy)) return "Choose what the layers are by: colour or shape.";
  if (!v.arrangements.includes(form.arrangement)) return "Choose an arrangement: side by side or stacked.";
  if (!inRange(form.minFeatureMm, v.min_feature_mm)) return `The smallest feature must be from ${v.min_feature_mm.min} to ${v.min_feature_mm.max} mm.`;
  if (!inRange(form.kerfMm, v.kerf_mm)) return `Kerf must be from ${v.kerf_mm.min} to ${v.kerf_mm.max} mm.`;
  if (!inRange(form.bleedMm, v.bleed_mm)) return `Bleed must be from ${v.bleed_mm.min} to ${v.bleed_mm.max} mm.`;
  if (!isWhole(form.numImages, v.versions)) return `Versions must be from ${v.versions.min} to ${v.versions.max}.`;
  if (form.seedLocked && !isWhole(form.seed, caps.limits.seed)) return `The seed must be from ${caps.limits.seed.min} to ${caps.limits.seed.max}.`;
  if (form.recipeEdited && form.recipeText.trim() === "") return "The words the studio sends are empty: write them, or press Rebuild to have them written again.";
  if (form.recipeEdited && form.recipeText.length > v.recipe_max_chars) return `The words the studio sends are ${form.recipeText.length} characters; the limit is ${v.recipe_max_chars}.`;
  return null;
}

// ------------------------------------------------------------------ the saved copy of the form
/** Every saved value is checked on its own, so an old or damaged one resets only itself (as for the picture options and the Music form). */
export function sanitizeVectorForm(raw: unknown, caps: Capabilities): { form: VectorForm; repaired: boolean } {
  const defaults = defaultVectorForm(caps);
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return { form: defaults, repaired: raw !== null && raw !== undefined };
  const r = raw as Record<string, unknown>;
  const v = caps.limits.vector;
  let repaired = false;
  function pick<K extends keyof VectorForm>(key: K, valid: (value: unknown) => boolean): VectorForm[K] {
    if (!(key in r)) return defaults[key]; // a field added in a later version: its default
    if (valid(r[key])) return r[key] as VectorForm[K];
    repaired = true;
    return defaults[key];
  }
  const colours = pick("colours", (value) => isWhole(value, v.colours));
  const form: VectorForm = {
    idea: pick("idea", (value) => typeof value === "string" && value.length <= caps.limits.prompt_chars),
    kind: pick("kind", (value) => (v.kinds as readonly unknown[]).includes(value)),
    colours,
    palette: pick("palette", (value) => Array.isArray(value) && paletteProblem(value as string[], colours) === null),
    widthMm: pick("widthMm", (value) => inRange(value, v.size_mm)),
    heightMm: pick("heightMm", (value) => inRange(value, v.size_mm)),
    layersBy: pick("layersBy", (value) => (v.layers_by as readonly unknown[]).includes(value)),
    arrangement: pick("arrangement", (value) => (v.arrangements as readonly unknown[]).includes(value)),
    minFeatureMm: pick("minFeatureMm", (value) => inRange(value, v.min_feature_mm)),
    kerfMm: pick("kerfMm", (value) => inRange(value, v.kerf_mm)),
    bleedMm: pick("bleedMm", (value) => inRange(value, v.bleed_mm)),
    numImages: pick("numImages", (value) => isWhole(value, v.versions)),
    seedLocked: pick("seedLocked", (value) => typeof value === "boolean"),
    seed: pick("seed", (value) => isWhole(value, caps.limits.seed)),
    recipeEdited: pick("recipeEdited", (value) => typeof value === "boolean"),
    recipeText: pick("recipeText", (value) => typeof value === "string" && value.length <= v.recipe_max_chars),
  };
  if (form.recipeEdited && !form.recipeText.trim()) form.recipeEdited = false; // an empty hand-written recipe is no recipe
  return { form, repaired };
}

export function loadVectorForm(caps: Capabilities, store: KeyValueStore): { form: VectorForm; repaired: boolean } {
  const text = store.get(VECTOR_FORM_KEY);
  if (text === null) return { form: defaultVectorForm(caps), repaired: false };
  try {
    return sanitizeVectorForm(JSON.parse(text), caps);
  } catch {
    return { form: defaultVectorForm(caps), repaired: true };
  }
}

export function saveVectorForm(form: VectorForm, store: KeyValueStore): void {
  store.set(VECTOR_FORM_KEY, JSON.stringify(form));
}

// ------------------------------------------------------------------ requests
/** The request for this form, with exactly the keys of the contract's `create_request` (backend/tests/vector_contract.json). The steps are the model's
 *  default: the form has no box for them (the smallest reasonable reading of §31.12.8, which lists none). A locked seed is sent as it is, otherwise null
 *  and the server picks and records one. By shape the arrangement sent is side by side (`effectiveArrangement`). */
export function buildVectorRequest(form: VectorForm, caps: Capabilities): CreateVectorBody {
  return {
    mode: "vector",
    prompt: form.idea.trim(),
    options: {
      colours: form.colours,
      palette: [...form.palette],
      kind: form.kind,
      width_mm: form.widthMm,
      height_mm: form.heightMm,
      layers_by: form.layersBy,
      arrangement: effectiveArrangement(form),
      bleed_mm: form.bleedMm,
      min_feature_mm: form.minFeatureMm,
      kerf_mm: form.kerfMm,
      steps: caps.defaults.steps,
      seed: form.seedLocked ? form.seed : null,
      num_images: form.numImages,
      recipe: recipeToSend(form),
    },
  };
}

/** Retry: the same request again, the same seed and the same words included (the recipe the run used, so a later change of the studio's wording does not
 *  change a retry). */
export function vectorRetryRequest(run: VectorRun): CreateVectorBody {
  const o = run.options;
  return {
    mode: "vector",
    prompt: run.prompt,
    options: {
      colours: o.colours, palette: [...o.palette], kind: o.kind, width_mm: o.width_mm, height_mm: o.height_mm, layers_by: o.layers_by,
      arrangement: o.arrangement, bleed_mm: o.bleed_mm, min_feature_mm: o.min_feature_mm, kerf_mm: o.kerf_mm, steps: o.steps, seed: o.seed,
      num_images: o.num_images, recipe: o.recipe,
    },
  };
}

/** Reuse: the idea and every setting of the run, the seed locked (so a changed idea is a fair comparison, as on the other tabs), clamped to today's limits.
 *  The recipe box starts automatic; `reconcileReusedRecipe` decides whether the run's own words were edited by hand. */
export function vectorFormFromRun(run: VectorRun, caps: Capabilities, current: VectorForm): VectorForm {
  const o = run.options;
  const merged: VectorForm = {
    ...current,
    idea: run.prompt, kind: o.kind, colours: o.colours, palette: [...o.palette], widthMm: o.width_mm, heightMm: o.height_mm, layersBy: o.layers_by,
    arrangement: o.arrangement, minFeatureMm: o.min_feature_mm, kerfMm: o.kerf_mm, bleedMm: o.bleed_mm, numImages: o.num_images, seedLocked: true,
    seed: o.seed, recipeEdited: false, recipeText: "",
  };
  return sanitizeVectorForm(merged, caps).form;
}

// ------------------------------------------------------------------ downloads
/** The range a PNG size may be in: the server's if it sends one (`png_px`), else the route's own (§31.12.7). */
export const pngRange = (limits: VectorLimits): Range => limits.png_px ?? VECTOR_PNG_RANGE;

/** The link for a PNG at `size` pixels on the long side: the item's own download link (the server's), with `size` added. The page builds no other URL. */
export function pngDownloadUrl(item: Pick<VectorItem, "png_download_url">, size: number): string {
  return `${item.png_download_url}${item.png_download_url.includes("?") ? "&" : "?"}size=${size}`;
}

// ------------------------------------------------------------------ words on a card
export function vectorTitle(run: Pick<VectorRun, "prompt">): string {
  const flat = run.prompt.trim().replace(/\s+/g, " ");
  return flat.length > 80 ? `${flat.slice(0, 79).trimEnd()}…` : flat;
}

export function vectorSeedText(run: Pick<VectorRun, "options">): string {
  const { seed, num_images: n } = run.options;
  return n > 1 ? `seeds ${seed}–${seed + n - 1}` : `seed ${seed}`;
}

/** The one line of settings under a card's prompt: the sheet, the colours asked, the kind, the layers, the seed and how long it took. */
export function vectorMeta(run: Pick<VectorRun, "options">, took: string | null): string[] {
  const o = run.options;
  const layers = o.layers_by === "shape" ? "layers by shape" : `layers by colour, ${ARRANGEMENT_LABELS[o.arrangement].toLowerCase()}`;
  const meta = [`${mmText(o.width_mm)} × ${mmText(o.height_mm)} mm`, `${o.colours} colours`, kindLabel(o.kind).toLowerCase(), layers, vectorSeedText(run)];
  if (took) meta.push(took);
  return meta;
}

/** "4 found, 4 asked", and whether white is one of them: the card's line about colours (§31.12.8, "the colours found against the colours asked"). */
export function coloursText(check: Pick<VectorCheck, "colours_asked" | "colours_found" | "white">): string {
  const base = `${check.colours_found} ${check.colours_found === 1 ? "colour" : "colours"} found, ${check.colours_asked} asked`;
  return check.white ? `${base}. White, the bare material, is one of them and has no layer.` : `${base}. No white in the picture.`;
}

/** A cut length: millimetres, or metres from a metre up. */
export const lengthText = (mm: number): string => (mm >= 1000 ? `${Number((mm / 1000).toFixed(2))} m` : `${Number(mm.toFixed(1))} mm`);

/** "Smallest feature: 0.62 mm (your limit is 0.5 mm)", or that none was found. */
export function smallestFeatureText(smallest: number | null, limit: number): string {
  return smallest === null ? `Smallest feature: none found (your limit is ${mmText(limit)} mm).` : `Smallest feature: ${mmText(smallest)} mm (your limit is ${mmText(limit)} mm).`;
}

/** "Version 2 of 3": which version, by its place when it was asked for (a version that failed leaves a gap, and the run's error names it). A single version
 *  is only named by its seed. */
export function versionLabel(item: Pick<VectorItem, "idx" | "seed">, asked: number): string {
  return asked > 1 ? `Version ${item.idx + 1} of ${asked}` : `Seed ${item.seed}`;
}

/** "18 KB", "1.2 MB": a file size for the Download buttons. */
export function bytesText(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${Number((bytes / 1000).toFixed(1))} KB`;
  return `${Number((bytes / 1_000_000).toFixed(1))} MB`;
}

// ------------------------------------------------------------------ progress
export type VectorPhaseKey = "draw" | "trace";
export interface VectorPhase {
  key: VectorPhaseKey;
  label: string;
  fraction: number | null; // 0 to 1; null = working but with no measure (the clean-up has no steps to count)
}

const PHASE_ORDER: Record<string, number> = { draw: 0, trace: 1 };

/** The two stages of making a vector (§31.12.5), each with its own bar: drawing, step by step, and then tracing, which has no steps to count, so while it
 *  works its bar is indeterminate (`fraction` null). A stage before the one under way is full, one after it is empty. Before any progress both are empty. */
export function vectorPhases(progress: Progress | null): VectorPhase[] {
  const at = progress ? PHASE_ORDER[progress.stage ?? "draw"] ?? 0 : -1;
  const drawn = progress ? Math.min(1, Math.max(0, progress.step / Math.max(1, progress.steps))) : 0;
  return [
    { key: "draw", label: "Drawing", fraction: at > 0 ? 1 : at === 0 ? drawn : 0 },
    { key: "trace", label: "Tracing", fraction: at === 1 ? null : 0 },
  ];
}

/** What a running card says under its bars. */
export function vectorProgressText(progress: Progress | null, canceling: boolean): string {
  if (!progress) return canceling ? "Stopping…" : "Starting…";
  const version = progress.of > 1 ? `Version ${progress.image} of ${progress.of} · ` : "";
  const detail = progress.stage === "trace" ? "tracing the drawing into layers…" : `drawing, step ${progress.step} of ${progress.steps}`;
  return `${version}${detail}${canceling ? " · stopping at the next step…" : ""}`;
}
