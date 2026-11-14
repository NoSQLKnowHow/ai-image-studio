// Dates in the filter bar (DESIGN.md §45.5): the page reads a date as the person's own calendar day in the person's own time zone, and sends the server two UTC instants.
//
// The server stores `created_at` in UTC and never learns the person's zone, so it is the PAGE that turns "From 1 Nov, To 8 Nov" into "from the start of 1 November here, up to
// (and not including) the start of 9 November here", as instants. The range is half open, so the last day is whole whatever length it has: a day on which the clocks change is 23
// or 25 hours long, and a rule of "plus 24 hours" would cut it short or leak into the next one. All the zone arithmetic goes through `Intl`, which knows the rules of every zone,
// and takes the zone as an argument (the browser's own by default), so the tests can try the awkward ones without changing the machine's clock.

/** The zone the browser is in, as an IANA name (`Europe/Dublin`); falls back to UTC where the browser will not say. */
export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A calendar date (`2026-11-08`) as its year, month and day, or null for text that is not one (a date box that is empty or half typed gives ""; `2026-02-30` is not a date either). */
export function parseDate(text: string): { year: number; month: number; day: number } | null {
  const found = DATE.exec(text);
  if (!found) return null;
  const [year, month, day] = [Number(found[1]), Number(found[2]), Number(found[3])];
  const check = new Date(Date.UTC(year, month - 1, day));
  // Date.UTC rolls February 30 over into March: a date that does not survive the round trip is not a date
  return check.getUTCFullYear() === year && check.getUTCMonth() === month - 1 && check.getUTCDate() === day ? { year, month, day } : null;
}

/** How many minutes the zone is ahead of UTC at the instant `ms` (negative west of Greenwich). Found by asking `Intl` what the zone's wall clock says at that instant, and reading
 *  that wall clock as if it were UTC: the difference is the offset. */
function offsetMinutes(ms: number, zone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(ms));
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value);
  const wall = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return Math.round((wall - Math.floor(ms / 1000) * 1000) / 60000);
}

/** The first instant of a calendar day in a zone, as a UTC instant in the studio's own form (`2026-11-01T05:00:00.000Z`), or null for a date that is not one. The zone's offset is
 *  found at the instant itself, not at midnight UTC, because the offset can change in between (that is what a clock change is): a guess is made with the offset at midnight UTC and
 *  then corrected once with the offset that holds at the guess. */
export function startOfDay(date: string, zone: string = browserZone()): string | null {
  const day = parseDate(date);
  if (!day) return null;
  const midnightUtc = Date.UTC(day.year, day.month - 1, day.day);
  const first = midnightUtc - offsetMinutes(midnightUtc, zone) * 60000;
  const second = midnightUtc - offsetMinutes(first, zone) * 60000;
  return new Date(second).toISOString();
}

/** The calendar day after a date, in the same form (`2026-12-31` is followed by `2027-01-01`), or null for text that is not a date. */
export function dayAfter(date: string): string | null {
  const day = parseDate(date);
  if (!day) return null;
  return new Date(Date.UTC(day.year, day.month - 1, day.day + 1)).toISOString().slice(0, 10);
}

/** The instant the range of a "From" date begins: the start of that day in the zone. */
export const sinceOf = (from: string, zone: string = browserZone()): string | null => startOfDay(from, zone);

/** The instant the range of a "To" date ends, and does not include: the start of the day AFTER it in the zone, so the whole of the last day is in. */
export function untilOf(to: string, zone: string = browserZone()): string | null {
  const next = dayAfter(to);
  return next === null ? null : startOfDay(next, zone);
}

/** The calendar date, in the zone, an instant falls on: what a date box shows for the instant that was made from it. */
export function dateOf(instant: string, zone: string = browserZone()): string {
  const moment = Date.parse(instant);
  if (!Number.isFinite(moment)) return "";
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(moment));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** What the "From" box shows for the instant a range begins at. */
export const fromDate = (since: string | null, zone: string = browserZone()): string => (since === null ? "" : dateOf(since, zone));

/** What the "To" box shows for the instant a range ends at: the last moment IN the range is a millisecond earlier, and that is on the last day. */
export const toDate = (until: string | null, zone: string = browserZone()): string => (until === null ? "" : dateOf(new Date(Date.parse(until) - 1).toISOString(), zone));

/** Why a pair of dates cannot be a range, in words, or null when it can: the page says it under the boxes, and sends nothing until it is put right. Either box may be empty. */
export function rangeProblem(from: string, to: string): string | null {
  if (from !== "" && parseDate(from) === null) return "“From” is not a date.";
  if (to !== "" && parseDate(to) === null) return "“To” is not a date.";
  if (from !== "" && to !== "" && from > to) return "“From” is after “To”: the range would be empty.";
  return null;
}
