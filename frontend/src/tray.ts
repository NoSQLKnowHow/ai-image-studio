// The reference tray of Edit mode (DESIGN.md §21.4): the images that go to the model with the prompt, in the
// order it will see them. Everything here is a pure function of the list, so the rules (the cap, the order, what
// blocks Generate, what "image N" in the prompt refers to) can be tested without a browser.

import type { InputRef } from "./types";

export interface TrayItem {
  key: string; // a stable id for this page only: reordering changes positions, never keys
  name: string; // the file's name, for labels
  state: "uploading" | "ready";
  progress: number; // 0..1 while uploading
  ref: InputRef | null; // how the server is told about it; set once it is ready
  thumbUrl: string | null; // the server's thumbnail, once ready
  previewUrl: string | null; // a local preview while the upload is under way
  width: number | null;
  height: number | null;
  hasAlpha: boolean;
  missing: boolean; // its picture could not be loaded, so the file is probably gone
}

/** An image the server already has (an earlier result or input) that can go straight into the tray. */
export interface KnownImage {
  imageId: string;
  name: string;
  thumbUrl: string | null;
  width: number;
  height: number;
  hasAlpha: boolean;
}

/** How many more images fit. */
export function room(items: readonly TrayItem[], cap: number): number {
  return Math.max(0, cap - items.length);
}

/** Add `added` after the existing items, keeping the first ones that fit; `skipped` is how many did not. */
export function addItems(items: readonly TrayItem[], added: readonly TrayItem[], cap: number): { items: TrayItem[]; skipped: number } {
  const fit = Math.min(room(items, cap), added.length);
  return { items: [...items, ...added.slice(0, fit)], skipped: added.length - fit };
}

export function removeItem(items: readonly TrayItem[], key: string): TrayItem[] {
  return items.filter((item) => item.key !== key);
}

/** Put the item at `toIndex` (0-based, clamped), shifting the others. A key that isn't there changes nothing. */
export function moveItem(items: readonly TrayItem[], key: string, toIndex: number): TrayItem[] {
  const from = items.findIndex((item) => item.key === key);
  if (from < 0) return [...items];
  const to = Math.max(0, Math.min(items.length - 1, toIndex));
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

export function moveBy(items: readonly TrayItem[], key: string, delta: number): TrayItem[] {
  const from = items.findIndex((item) => item.key === key);
  return from < 0 ? [...items] : moveItem(items, key, from + delta);
}

/** The 1-based place the model will see this item at (what "image N" means), or 0 if it isn't in the tray. */
export function positionOf(items: readonly TrayItem[], key: string): number {
  return items.findIndex((item) => item.key === key) + 1;
}

/** Why Generate must wait, or null when the tray can be sent. */
export function submitBlock(items: readonly TrayItem[]): string | null {
  if (items.length === 0) return "Add at least one image to edit.";
  const uploading = items.filter((item) => item.state === "uploading").length;
  if (uploading) return uploading === 1 ? "Waiting for 1 upload to finish." : `Waiting for ${uploading} uploads to finish.`;
  const missing = items.findIndex((item) => item.missing);
  if (missing >= 0) return `Image ${missing + 1} is no longer available. Remove it or add it again.`;
  return null;
}

/** What the server is sent: every ready image, in order. */
export function inputRefs(items: readonly TrayItem[]): InputRef[] {
  return items.flatMap((item) => (item.state === "ready" && item.ref ? [item.ref] : []));
}

/** The place the result's shape follows when Size is Auto: the chosen image, or by default the last (the pipeline's
 *  own rule, DESIGN.md decision #30). 0 for an empty tray. */
export function followedPosition(items: readonly TrayItem[], shapeKey: string | null): number {
  const chosen = shapeKey ? positionOf(items, shapeKey) : 0;
  return chosen || items.length;
}

/** `shape_from` for the request: only when a different image than the default (the last) was chosen. */
export function shapeFromForRequest(items: readonly TrayItem[], shapeKey: string | null): number | null {
  const chosen = shapeKey ? positionOf(items, shapeKey) : 0;
  return chosen && chosen !== items.length ? chosen : null;
}

/** Put "image N" into the prompt at the caret, replacing any selection and adding a space where one is needed.
 *  The caret lands after what was added. Null if the result would be longer than `limit`. */
export function insertReference(text: string, start: number, end: number, n: number, limit = Infinity): { text: string; caret: number } | null {
  const from = Math.max(0, Math.min(text.length, Math.min(start, end)));
  const to = Math.max(0, Math.min(text.length, Math.max(start, end)));
  const before = text.slice(0, from);
  const after = text.slice(to);
  const lead = before && !/[\s(["'“‘]$/.test(before) ? " " : "";
  const trail = after && !/^[\s.,;:!?)\]"'”’]/.test(after) ? " " : "";
  const added = `${lead}image ${n}${trail}`;
  const next = before + added + after;
  return next.length > limit ? null : { text: next, caret: before.length + added.length };
}

/** An edit's cost in "units": one image at 1K is 1, and it grows with the number of images and the square of the
 *  resolution (every input is resized to about resolution² pixels, DESIGN.md §21.2). `heavy` once it is over the
 *  warning threshold from the server (0 = never warn). */
export function editCost(images: number, resolution: number, warnUnits: number): { units: number; label: string; heavy: boolean } {
  const units = Math.max(0, images) * (resolution / 1024) ** 2;
  return { units, label: `${units} unit${units === 1 ? "" : "s"}`, heavy: warnUnits > 0 && units > warnUnits };
}
