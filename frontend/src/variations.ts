// More like this and A/B compare (DESIGN.md section 43): what the page works out before it asks the server anything. All of it is pure, so it is tested without a browser.
//
// There are two ways to make more of a picture you like. *More takes of the prompt* is the run's own request again with new random seeds. *Variations of this picture* is an edit
// run that takes the picked picture as its input, and is off until the machine has been told that the editing model does it well (section 43.3). Either way the page builds an ordinary
// create-run body, as it does for Regenerate larger, and adds the link; the server checks the link and keeps it.

import { retryRequest } from "./options";
import type { Capabilities, CreateRunBody, ImageInfo, ImageRun, Run, VariationHow, WithVariation } from "./types";

/** The most pictures one More like this makes. The run's own count is not used, since a run of 8 pictures is not what a click should start. */
export const MAX_TAKES = 4;

/** How many pictures More like this may ask for: 1 to 4, and never more than the server allows in a run (`STUDIO_MAX_IMAGES_PER_RUN`). */
export const takesLimit = (caps: Pick<Capabilities, "limits">): number => Math.max(1, Math.min(MAX_TAKES, caps.limits.num_images.max));

/** What the dialog asks. `image` is the picture that was on the screen when the button was pressed in the viewer, or null when it was pressed on a card. `words` are the prompt, which the
 *  person may have changed: they are sent as they are, never added to. */
export interface MoreLikeThisAsk {
  how: VariationHow;
  count: number;
  words: string;
  image: Pick<ImageInfo, "id"> | null;
}

/** Whether More like this is offered for a run at all: a picture run that has finished and has a picture, and is not in the bin (a run there only reads, DESIGN.md section 30). */
export function canMoreLikeThis(run: ImageRun): boolean {
  return run.status === "done" && run.deleted_at === null && run.images.length > 0;
}

/** More takes of the prompt: the run's own request again (an edit's input pictures too), with the seed not locked, so that the server picks a fresh one and none of the pictures repeats the
 *  source's (the same seed with the same settings makes the same picture, DESIGN.md section 43.8 item 2), and the number of pictures that was asked for. Nothing else changes: size, steps,
 *  guidance and negative words are the source's. The viewer and the card make the same request; the viewer only says which picture it was looking at. */
export function moreTakesRequest(run: ImageRun, ask: MoreLikeThisAsk): WithVariation<CreateRunBody> {
  const again = retryRequest(run);
  return {
    ...again, // (a run that is itself a variation carries its own link here; the explicit `variation_of` below replaces it: the new run is More like this of THIS run)
    prompt: ask.words.trim() || run.prompt,
    options: { ...again.options, seed: null, num_images: ask.count },
    variation_of: { run_id: run.id, image_id: ask.image?.id ?? null, how: "seeds" },
  };
}

/** Variations of this picture: an edit of the picked picture. The picture is the only input; the size is left on Auto, so the result follows the picture; the steps, guidance and negative words
 *  are the source's. Only built once the choice has been switched on (`pictureChoice`), and the server refuses it otherwise. */
export function pictureVariationRequest(run: ImageRun, image: Pick<ImageInfo, "id">, ask: MoreLikeThisAsk): WithVariation<CreateRunBody> {
  const o = run.options;
  return {
    mode: "edit",
    prompt: ask.words.trim() || run.prompt,
    input_images: [{ image_id: image.id }],
    options: {
      width: null,
      height: null,
      steps: o.steps,
      seed: null,
      num_images: ask.count,
      negative_prompt: o.negative_prompt,
      cfg_scale: o.cfg_scale,
      transparent: o.transparent,
      resolution: o.resolution === 2048 ? 2048 : 1024,
    },
    variation_of: { run_id: run.id, image_id: image.id, how: "picture" },
  };
}

/** The body for what the dialog asked. A picture variation made from a card (no picture on the screen) uses the run's only picture; `pictureChoice` has already said no for a run with several. */
export function variationRequest(run: ImageRun, ask: MoreLikeThisAsk): WithVariation<CreateRunBody> {
  if (ask.how === "seeds") return moreTakesRequest(run, ask);
  const image = ask.image ?? run.images[0];
  return pictureVariationRequest(run, image, { ...ask, image });
}

/** Whether a choice in the dialog can be used, and if not why (the words shown beside the greyed choice). */
export interface Choice {
  enabled: boolean;
  reason: string | null;
}

/** More takes needs nothing: it is the model that is already loaded. */
export const takesChoice: Choice = { enabled: true, reason: null };

/** Variations of this picture: switched on for this machine (`capabilities.variations`), editing available, and a picture to start from: the one on the screen, or, from a card, the run's only one. */
export function pictureChoice(caps: Pick<Capabilities, "modes" | "variations">, run: ImageRun, image: Pick<ImageInfo, "id"> | null): Choice {
  if (!caps.variations?.from_picture) return { enabled: false, reason: caps.variations?.reason ?? "Not tried on this machine yet." };
  if (!caps.modes.includes("edit")) return { enabled: false, reason: "Editing is not available on this machine." };
  if (image === null && run.images.length !== 1) return { enabled: false, reason: "Open one picture in the viewer and press More like this there." };
  return { enabled: true, reason: null };
}

/** Words cut to `limit` characters with an ellipsis, white space made single: what a chip or a label says of a prompt. */
export function cut(words: string, limit = 40): string {
  const text = words.split(/\s+/).filter(Boolean).join(" ");
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

/** What the card's first chip says: where the run came from, and what has become of that run (DESIGN.md section 43.4); null for a run that is not a variation. */
export function sourceChip(run: Pick<ImageRun, "variation_of">): string | null {
  const link = run.variation_of;
  if (!link) return null;
  if (link.source.state === "in_bin") return "Variation of a run in the bin";
  if (link.source.state === "gone" || link.source.prompt === null) return "Variation of a run that is gone";
  return `Variation of “${cut(link.source.prompt)}”`;
}

/** The second chip: how many variations this run has; null when it has none. */
export function countChip(run: Pick<ImageRun, "variation_count">): string | null {
  return run.variation_count ? `Variations (${run.variation_count})` : null;
}

/** The id that the family view is asked for from a run's chip: the family's first run, which is the run itself when it has variations and its `family_id` when it is one of them. Null for a run that
 *  is in no family. */
export function familyOf(run: Pick<ImageRun, "id" | "family_id" | "variation_count">): string | null {
  return run.family_id ?? (run.variation_count ? run.id : null);
}

/** When a variation is made from a source that is not in the history any more, a Retry cannot keep the link (the server refuses a source that is in the bin or gone): it keeps the link only
 *  while the source is there. */
export const keepsLink = (run: Pick<ImageRun, "variation_of">): boolean => !!run.variation_of && run.variation_of.source.state === "present";

// ---- the compare basket (section 43.5) ---------------------------------------------------------------------------------------------------

/** One picture put in the basket: enough to find it again among the runs the page holds. */
export interface BasketItem {
  runId: string;
  imageId: string;
}

/** The basket holds two pictures, from any runs. */
export const BASKET_MAX = 2;

export type Basket = readonly BasketItem[];

export const basketHas = (basket: Basket, imageId: string): boolean => basket.some((item) => item.imageId === imageId);

/** Add a picture, or take it out if it is in already (the viewer's Add to compare is a toggle). A third picture is not added: `full` says so, and the page offers to replace the first. */
export function basketToggle(basket: Basket, item: BasketItem): { basket: Basket; full: boolean } {
  if (basketHas(basket, item.imageId)) return { basket: basket.filter((held) => held.imageId !== item.imageId), full: false };
  if (basket.length >= BASKET_MAX) return { basket, full: true };
  return { basket: [...basket, item], full: false };
}

/** Put a picture in place of the first one (the oldest), which is what a full basket offers. The picture that was second becomes first. */
export function basketReplaceFirst(basket: Basket, item: BasketItem): Basket {
  if (basketHas(basket, item.imageId)) return basket;
  return basket.length < BASKET_MAX ? [...basket, item] : [...basket.slice(1), item];
}

/** The two sides the other way round: what Swap sides does. */
export const basketSwap = (basket: Basket): Basket => (basket.length === 2 ? [basket[1], basket[0]] : basket);

/** A picture ready to be shown in the compare view: where to get it, how big it is, what to call it, and whether its run is kept (for Keep this one). A result and an edit's source
 *  picture are the same to it; a source has no seed, so its label says "source image N" in place of one. */
export interface ComparePicture {
  runId: string;
  id: string;
  url: string;
  width: number;
  height: number;
  hasAlpha: boolean;
  label: string;
  pinned: boolean;
}

/** A basket item as a picture to show, found among the runs the page holds: a result of its run, or one of an edit's own source pictures. Null if its run is not here, is in the bin, is not a
 *  picture run, or the picture is not there any more (it was deleted, or sent to the bin). */
export function resolveBasketItem(runs: Readonly<Record<string, Run>>, item: BasketItem): ComparePicture | null {
  const held = runs[item.runId];
  if (!held || (held.mode !== "generate" && held.mode !== "edit") || held.deleted_at) return null;
  const run = held as ImageRun;
  const result = run.images.find((picture) => picture.id === item.imageId);
  if (result) return { runId: run.id, id: result.id, url: result.url, width: result.width, height: result.height, hasAlpha: result.has_alpha, label: pictureLabel(run, result), pinned: run.pinned };
  const source = run.inputs.find((input) => input.id === item.imageId);
  if (source) {
    const label = `${cut(run.prompt)} · source image ${source.position} · ${source.width}×${source.height}`;
    return { runId: run.id, id: source.id, url: source.url, width: source.width, height: source.height, hasAlpha: source.has_alpha, label, pinned: run.pinned };
  }
  return null;
}

/** What labels a side of the compare view: the words cut short, the seed and the size. */
export const pictureLabel = (run: Pick<ImageRun, "prompt">, image: Pick<ImageInfo, "seed" | "width" | "height">): string =>
  `${cut(run.prompt)} · seed ${image.seed} · ${image.width}×${image.height}`;

// ---- the slider (section 43.5) ------------------------------------------------------------------------------------------------------------

/** How far a key moves the divider, in percent of the width. */
export const SLIDER_STEP = 5;

/** A position kept between the two ends: 0 shows only the second picture, 100 only the first. */
export const clampPercent = (value: number): number => Math.min(100, Math.max(0, value));

/** The divider's new place for a key, or null for a key that does not move it. Arrows move it a step (the way the arrow points), Page Up and Page Down two steps, Home and End to the ends. */
export function sliderKey(value: number, key: string): number | null {
  switch (key) {
    case "ArrowLeft":
    case "ArrowDown":
      return clampPercent(value - SLIDER_STEP);
    case "ArrowRight":
    case "ArrowUp":
      return clampPercent(value + SLIDER_STEP);
    case "PageDown":
      return clampPercent(value - 2 * SLIDER_STEP);
    case "PageUp":
      return clampPercent(value + 2 * SLIDER_STEP);
    case "Home":
      return 0;
    case "End":
      return 100;
    default:
      return null;
  }
}

/** The divider's place, in percent, when a pointer is at `clientX` over a box that starts at `left` and is `width` wide. A box with no width (not laid out yet) puts it in the middle. */
export function percentAt(clientX: number, left: number, width: number): number {
  return width > 0 ? clampPercent(((clientX - left) / width) * 100) : 50;
}
