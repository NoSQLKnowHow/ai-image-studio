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

// A prompt cut to one line of at most `max` characters, for a toast
function shorten(text: string, max = 48): string {
  const flat = text.trim().replace(/\s+/g, " ");
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
}

/** The toast after Keep was pressed on a kept run in the Kept view (DESIGN.md §29.4): what happened and when the run will be deleted.
 *  The date is the card's own (`expires_at`: its age plus the retention), and "around" because the clean-up runs once a day. */
export function unkeptText(
  run: { prompt: string; expires_at: string | null },
  now: number = Date.now(),
  format: { locale?: string; timeZone?: string } = {},
): string {
  // The toast has three parts: what happened (`lead`), how to undo it (`again`), and when the run will be deleted (chosen below)
  const lead = `No longer kept: “${shorten(run.prompt)}”.`;
  const again = "unless you Keep it again";
  // the time left until the run's expiry date (its age plus the retention); NaN when it has none
  const left = run.expires_at ? Date.parse(run.expires_at) - now : Number.NaN;
  // no usable date (the retention may be off): say only that it will be deleted when it is old enough
  if (!Number.isFinite(left)) return `${lead} It will be deleted when it is old enough, ${again}.`;
  // already past its time: the daily clean-up takes it next
  if (left <= 0) return `${lead} It is past its time, so it will be deleted at the next daily clean-up, ${again}.`;
  // less than a day left: a date would mislead, since the clean-up runs once a day
  if (left < DAY_MS) return `${lead} It will be deleted within a day, ${again}.`;
  // more than a day left: say the date, and the number of whole days to it
  const days = Math.floor(left / DAY_MS);
  const date = new Date(run.expires_at as string).toLocaleDateString(format.locale, { day: "numeric", month: "short", timeZone: format.timeZone });
  return `${lead} It will be deleted around ${date}, in ${days} ${days === 1 ? "day" : "days"}, ${again}.`;
}

/** What keeps a card in a filtered view when it is not the filter's kind of run: it works, and a job you started stays visible (§29.3). It
 *  leaves when it is done unless it has become one of the filter's runs, which is a different act for each filter: Keep it (the Kept view),
 *  or file it in the project (a project's view, DESIGN.md §32). */
export type ViewScope = "kept" | "project";

/** The toast when a run that was shown in a filtered view only while it worked has finished without joining it (§29.3, §32). */
export function leftTheViewText(run: { prompt: string; status: string }, scope: ViewScope = "kept"): string {
  const what = run.status === "done" ? "is done" : run.status === "failed" ? "failed" : "was canceled";
  return `“${shorten(run.prompt)}” ${what}. ${scope === "kept" ? "It is not kept, so it is not in this view." : "It is not in this project, so it is not in this view."}`;
}

/** The note on a card that is in the Kept view only because it is working (§29.3). */
export const WORKING_IN_KEPT_NOTE = "Shown while it works. It stays in this view only if you Keep it.";

/** The same note in a project's view (§32): a job you start while a project is chosen stays visible, and stays only if it is filed there. */
export const WORKING_IN_PROJECT_NOTE = "Shown while it works. It stays in this view only if you add it to this project.";

/** The note for a card that is in a filtered view only because it is working, whichever filter it is. */
export const workingNote = (scope: ViewScope): string => (scope === "kept" ? WORKING_IN_KEPT_NOTE : WORKING_IN_PROJECT_NOTE);

/** The toast after a run was moved to the bin (DESIGN.md §30.1). */
export function binnedText(run: { prompt: string }, binDays: number): string {
  return `Deleted “${shorten(run.prompt)}”. It stays in Deleted for ${binDays} ${binDays === 1 ? "day" : "days"}.`;
}

/** The toast after a restore (§30.4): a kept run is still kept; any other has a fresh clock, so it says how long that is. */
export function restoredText(
  run: { prompt: string; pinned: boolean; expires_at: string | null },
  now: number = Date.now(),
  projectName: string | null = null,
): string {
  // A kept run is still kept; any other run has a fresh clock, so say how long it is (rounded, never less than one day)
  const lead = `Restored “${shorten(run.prompt)}”.`;
  // a run that was filed comes back to its project (DESIGN.md §32.3 item 5), and a filed run is always kept
  if (projectName !== null) return `${lead} It is back in the project “${projectName}”, and still kept.`;
  if (run.pinned) return `${lead} It is still kept.`;
  const left = run.expires_at ? Date.parse(run.expires_at) - now : Number.NaN;
  if (!Number.isFinite(left)) return lead;
  const days = Math.max(1, Math.round(left / DAY_MS));
  return `${lead} It has a fresh ${days} ${days === 1 ? "day" : "days"}.`;
}

/** The line on a card in the bin (§30.1): since when, and until when. The date is the server's (`purge_at`); "around" because the
 *  clean-up that deletes it for good runs once a day. */
export function binNote(
  run: { deleted_at: string | null; purge_at: string | null },
  now: number = Date.now(),
  format: { locale?: string; timeZone?: string } = {},
): string | null {
  if (!run.deleted_at) return null;
  // a short date such as "12 Oct", in the given locale and time zone (the defaults are the browser's)
  const day = (iso: string) => new Date(iso).toLocaleDateString(format.locale, { day: "numeric", month: "short", timeZone: format.timeZone });
  const since = `In the bin since ${day(run.deleted_at)}.`;
  const left = run.purge_at ? Date.parse(run.purge_at) - now : Number.NaN;
  // no purge date from the server (the bin may be off): say only since when
  if (!Number.isFinite(left)) return since;
  // already past its time: the daily clean-up takes it next
  if (left <= 0) return `${since} It will be deleted for good at the next daily clean-up.`;
  // less than a day left: a date would mislead, since the clean-up runs once a day
  if (left < DAY_MS) return `${since} It will be deleted for good within a day.`;
  const days = Math.floor(left / DAY_MS);
  return `${since} It will be deleted for good around ${day(run.purge_at as string)}, in ${days} ${days === 1 ? "day" : "days"}.`;
}

/** The toast after the bin was emptied (§30.1). */
export function emptiedText(n: number): string {
  return n === 0 ? "The bin was already empty." : `Emptied the bin: ${n} ${n === 1 ? "run" : "runs"} deleted for good.`;
}

// ------------------------------------------------------------------ project folders (DESIGN.md §32)
/** What a project holds, in words: "3 pictures, 1 track", or "no runs". The numbers are of runs in the history, each tab counted on its own. */
export function projectContents(counts: { image: number; music: number }): string {
  const parts = [
    counts.image ? `${counts.image} ${counts.image === 1 ? "picture" : "pictures"}` : "",
    counts.music ? `${counts.music} ${counts.music === 1 ? "track" : "tracks"}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "no runs";
}

/** The toast after a run was filed in a project (§32.1): it was kept in the same step, so it says so. */
export function filedText(run: { prompt: string }, projectName: string): string {
  return `Filed “${shorten(run.prompt)}” in ${projectName}. It is kept.`;
}

/** The toast after a filed run was moved to another project. */
export function movedText(run: { prompt: string }, from: string, to: string): string {
  return `Moved “${shorten(run.prompt)}” from ${from} to ${to}.`;
}

/** The toast after a run was taken out of its project: it stays kept, so that it cannot expire by surprise (§32.3 item 3). */
export function takenOutText(projectName: string): string {
  return `Taken out of ${projectName}. It is still kept.`;
}

/** The tooltip of the Keep button on a filed card, where it is locked (§32.3 item 2): why it cannot be turned off, and what to do. The
 *  name is null when the page does not know it yet (the list of projects has not arrived): it then says only that it is in a project. */
export function keepLockedTitle(projectName: string | null): string {
  return `Kept because it is in ${projectName === null ? "a project" : `the project “${projectName}”`}. Take it out of the project first.`;
}

/** The question when a project is deleted (§32.1): its runs are not deleted, and it says how many and of what. */
export function deleteProjectQuestion(project: { name: string; counts: { image: number; music: number } }): string {
  const runs = project.counts.image + project.counts.music;
  const lead = `Delete the project “${project.name}”?`;
  if (runs === 0) return `${lead} It has no runs.`;
  return `${lead} Its ${runs} ${runs === 1 ? "run" : "runs"} (${projectContents(project.counts)}) ${runs === 1 ? "is" : "are"} not deleted: ${runs === 1 ? "it stays" : "they stay"} kept and ${runs === 1 ? "is" : "are"} no longer in a project.`;
}
