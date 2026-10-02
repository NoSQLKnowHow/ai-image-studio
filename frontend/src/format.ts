import type { Run } from "./types";

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

export function untilText(iso: string | null, now: number = Date.now()): string | null {
  if (!iso) return null;
  const seconds = Math.max(0, Math.round((Date.parse(iso) - now) / 1000));
  if (seconds < 60) return "less than a minute";
  const minutes = Math.round(seconds / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

/** "2048×2048" from the finished images, else from the requested size, else "auto". */
export function sizeText(run: Run): string {
  const img = run.images[0];
  if (img) return `${img.width}×${img.height}`;
  const { width, height } = run.options;
  return width && height ? `${width}×${height}` : "auto size";
}

export function seedText(run: Run): string {
  const first = run.options.seed;
  const n = run.options.num_images;
  return n > 1 ? `seeds ${first}–${first + n - 1}` : `seed ${first}`;
}
