// Reordering the queue (DESIGN.md section 40): what the Queue panel works out before it asks the server anything. All of it is pure. A person moves a waiting job by dragging
// it, or by the arrows beside it; either way the request names the waiting job to put it before or after, and not a place number, so that a queue that changed while they
// were dragging cannot send it somewhere they did not mean.

import type { QueueItem, QueueKind, QueueMove } from "./types";

/** What the panel calls the model a job needs: a drawing is made with the image model, but it is called a drawing, as its tab is. A voice is a sample or a new voice made
 *  for the Speech tab, which has no card of its own. A kind from a newer server is just a job. */
const KIND_WORDS: Record<QueueKind, string> = { image: "Image", music: "Music", vector: "Drawing", speech: "Speech", voice: "Voice" };
export const kindWords = (kind: QueueKind): string => KIND_WORDS[kind] ?? "Job";

/** The place a job dragged to height `y` would take: 0 is before the first waiting job and `rects.length` is after the last. A job is passed when the pointer is below
 *  the middle of its row, so the line that shows where it will go changes half way through each row, which is where a hand expects it to. `rects` are the rows' boxes
 *  from top to bottom. */
export function slotAt(rects: readonly { top: number; bottom: number }[], y: number): number {
  return rects.filter((rect) => y > (rect.top + rect.bottom) / 2).length;
}

/** The request that puts `items[from]` in `slot` (0 = in front of the first, `items.length` = after the last), or null where that is where it already is: just in front
 *  of itself, or just after itself. */
export function moveForSlot(items: readonly QueueItem[], from: number, slot: number): QueueMove | null {
  const n = items.length;
  if (!Number.isInteger(from) || !Number.isInteger(slot) || from < 0 || from >= n || slot < 0 || slot > n) return null;
  if (slot === from || slot === from + 1) return null;
  const id = items[from].id;
  return slot < n ? { id, before: items[slot].id } : { id, after: items[n - 1].id };
}

/** Whether the arrow in `direction` (-1 up, 1 down) does anything for `items[index]`: it can be moved at all, and there is a job to go past. The job it goes past may be one
 *  that has started: a started job cannot be moved, but a job can be put in front of it or behind it. */
export function canStep(items: readonly QueueItem[], index: number, direction: -1 | 1): boolean {
  return !!items[index]?.movable && !!items[index + direction];
}

/** The request for an arrow: up puts the job before the one above it, down puts it after the one below; null at the ends and for a job that cannot be moved. */
export function stepMove(items: readonly QueueItem[], index: number, direction: -1 | 1): QueueMove | null {
  if (!canStep(items, index, direction)) return null;
  const id = items[index].id;
  const next = items[index + direction].id;
  return direction < 0 ? { id, before: next } : { id, after: next };
}

/** The waiting jobs as they will be once the server has done what `move` asks, with their places renumbered. Shown at once so that a drop does not wait for the network;
 *  the server's own answer replaces it. A request that names a job that is not there changes nothing. */
export function applyMove(items: readonly QueueItem[], move: QueueMove): QueueItem[] {
  const target = move.before ?? move.after;
  const moving = items.find((item) => item.id === move.id);
  if (!moving || !target || target === move.id || !items.some((item) => item.id === target)) return [...items];
  const rest = items.filter((item) => item.id !== move.id);
  const at = rest.findIndex((item) => item.id === target) + (move.before !== undefined ? 0 : 1);
  return [...rest.slice(0, at), moving, ...rest.slice(at)].map((item, index) => ({ ...item, position: index + 1 }));
}

/** What a screen reader is told when a job has been moved: its words and its new place. Nothing for a job that is not in the list. */
export function movedWords(items: readonly QueueItem[], id: string): string | null {
  const index = items.findIndex((item) => item.id === id);
  return index < 0 ? null : `Moved “${items[index].title}” to place ${index + 1} of ${items.length}.`;
}
