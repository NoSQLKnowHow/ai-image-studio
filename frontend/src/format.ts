import type { ImageRun, Run } from "./types";

export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export function duration(fromIso: string | null, toIso: string | null): string | null {
  if (!fromIso || !toIso) return null;
  const total = Math.max(0, Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

const DAY_MS = 24 * 60 * 60 * 1000;
/** A card warns about automatic deletion once fewer than this many days remain (DESIGN.md §5.6). */
export const EXPIRY_WARNING_DAYS = 7;

/** "Will be deleted in 3 days" once less than a week remains, else null. Rounds down, so it never
 *  promises more time than there is. */
export function expiryText(iso: string | null, now: number = Date.now()): string | null {
  if (!iso) return null;
  const left = Date.parse(iso) - now;
  if (!Number.isFinite(left) || left >= EXPIRY_WARNING_DAYS * DAY_MS) return null;
  if (left <= 0) return "Due to be deleted at the next daily clean-up";
  if (left < DAY_MS) return "Will be deleted within a day";
  const days = Math.floor(left / DAY_MS);
  return `Will be deleted in ${days} ${days === 1 ? "day" : "days"}`;
}

/** The note on a canceled card: what was kept. */
export function canceledText(run: Run): string {
  const music = run.mode === "music";
  const done = music ? run.tracks.length : run.images.length;
  const thing = music ? "track" : "image";
  if (!done) return `Canceled before any ${thing} was finished.`;
  const total = music ? run.options.tracks : run.options.num_images;
  return `Canceled. ${done} of ${total} ${total === 1 ? thing : `${thing}s`} finished and kept.`;
}

export function untilText(iso: string | null, now: number = Date.now()): string | null {
  if (!iso) return null;
  const seconds = Math.max(0, Math.round((Date.parse(iso) - now) / 1000));
  if (seconds < 60) return "less than a minute";
  const minutes = Math.round(seconds / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

/** "2048×2048" from the finished images, else from the requested size, else "auto". */
export function sizeText(run: ImageRun): string {
  const img = run.images[0];
  if (img) return `${img.width}×${img.height}`;
  const { width, height } = run.options;
  return width && height ? `${width}×${height}` : "auto size";
}

export function seedText(run: ImageRun): string {
  const first = run.options.seed;
  const n = run.options.num_images;
  return n > 1 ? `seeds ${first}–${first + n - 1}` : `seed ${first}`;
}
