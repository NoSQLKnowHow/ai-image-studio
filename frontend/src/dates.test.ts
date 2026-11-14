import { describe, expect, it } from "vitest";
import { browserZone, dateOf, dayAfter, fromDate, parseDate, rangeProblem, sinceOf, startOfDay, toDate, untilOf } from "./dates";

// Dates in the filter bar (DESIGN.md §45.5): a calendar day in the person's own zone becomes a UTC instant, and the range is half open, so the last day is whole whatever its length.
// The zones are named in each test, so that the awkward ones (a clock change, a half-hour offset, a zone ahead of Greenwich on the other side of the date line) are tried here and
// not left to the machine this runs on.

describe("a calendar date", () => {
  it("is read as year, month and day, and anything else is not a date", () => {
    expect(parseDate("2026-11-08")).toEqual({ year: 2026, month: 11, day: 8 });
    for (const bad of ["", "2026-11-8", "2026-13-01", "2026-02-30", "2026-00-10", "2026-11-31", "yesterday", "2026-11-08T00:00:00Z", " 2026-11-08"]) expect(parseDate(bad), bad).toBeNull();
    expect(parseDate("2028-02-29")).not.toBeNull(); // a leap day
    expect(parseDate("2027-02-29")).toBeNull();
  });

  it("is followed by the next day, across a month and a year", () => {
    expect(dayAfter("2026-11-08")).toBe("2026-11-09");
    expect(dayAfter("2026-11-30")).toBe("2026-12-01");
    expect(dayAfter("2026-12-31")).toBe("2027-01-01");
    expect(dayAfter("2028-02-28")).toBe("2028-02-29");
    expect(dayAfter("nope")).toBeNull();
  });
});

describe("the start of a day in a zone, as a UTC instant", () => {
  it("is midnight UTC in UTC, and ahead of or behind it in other zones", () => {
    expect(startOfDay("2026-11-02", "UTC")).toBe("2026-11-02T00:00:00.000Z");
    expect(startOfDay("2026-11-02", "Europe/Dublin")).toBe("2026-11-02T00:00:00.000Z"); // winter: the same as UTC
    expect(startOfDay("2026-07-02", "Europe/Dublin")).toBe("2026-07-01T23:00:00.000Z"); // summer time: an hour ahead, so its midnight is the hour before UTC's
    expect(startOfDay("2026-11-02", "America/Los_Angeles")).toBe("2026-11-02T08:00:00.000Z"); // behind: 8 hours
    expect(startOfDay("2026-11-02", "Asia/Kolkata")).toBe("2026-11-01T18:30:00.000Z"); // half an hour in the offset
    expect(startOfDay("2026-11-02", "Pacific/Auckland")).toBe("2026-11-01T11:00:00.000Z"); // 13 hours ahead, across the date line
  });

  it("is right on the day the clocks change, where the offset differs between the day's start and its end", () => {
    // Los Angeles: clocks go forward on 8 March 2026 at 2 a.m. (the day has 23 hours) and back on 1 November 2026 at 2 a.m. (25 hours)
    expect(startOfDay("2026-03-08", "America/Los_Angeles")).toBe("2026-03-08T08:00:00.000Z"); // midnight is still winter time (-8)
    expect(startOfDay("2026-03-09", "America/Los_Angeles")).toBe("2026-03-09T07:00:00.000Z"); // the next midnight is summer time (-7)
    expect(startOfDay("2026-11-01", "America/Los_Angeles")).toBe("2026-11-01T07:00:00.000Z"); // summer time (-7) at midnight
    expect(startOfDay("2026-11-02", "America/Los_Angeles")).toBe("2026-11-02T08:00:00.000Z"); // winter time (-8) at the next
  });

  it("is right where the clocks change between midnight UTC and the zone's own midnight (zones ahead of UTC)", () => {
    // Sydney: clocks go forward on 4 October 2026 at 2 a.m. (16:00 UTC on the 3rd). The guess made with the offset at midnight UTC (+11, after the change) is an hour out;
    // only the correction with the offset that holds AT the guess (+10, before it) finds the real midnight, 14:00 UTC.
    expect(startOfDay("2026-10-04", "Australia/Sydney")).toBe("2026-10-03T14:00:00.000Z");
    // Auckland: forward on 27 September 2026 at 2 a.m. (14:00 UTC on the 26th), the same shape with +13 and +12.
    expect(startOfDay("2026-09-27", "Pacific/Auckland")).toBe("2026-09-26T12:00:00.000Z");
    // Sydney the other way: back on 5 April 2026 at 3 a.m. (16:00 UTC on the 4th); midnight is still summer time (+11).
    expect(startOfDay("2026-04-05", "Australia/Sydney")).toBe("2026-04-04T13:00:00.000Z");
  });

  it("is null for a date that is not one", () => {
    expect(startOfDay("2026-02-30", "UTC")).toBeNull();
    expect(startOfDay("", "UTC")).toBeNull();
  });
});

describe("a range of two days (§45.5)", () => {
  it("begins at the start of the first day and ends where the day after the last begins, so the last day is whole", () => {
    const zone = "America/Los_Angeles";
    expect(sinceOf("2026-10-31", zone)).toBe("2026-10-31T07:00:00.000Z");
    expect(untilOf("2026-10-31", zone)).toBe("2026-11-01T07:00:00.000Z"); // 24 hours: a normal day
    // the day the clocks go back is 25 hours long, and the range covers all of it (a "plus 24 hours" would stop an hour early)
    expect(sinceOf("2026-11-01", zone)).toBe("2026-11-01T07:00:00.000Z");
    expect(untilOf("2026-11-01", zone)).toBe("2026-11-02T08:00:00.000Z");
    expect((Date.parse(untilOf("2026-11-01", zone) as string) - Date.parse(sinceOf("2026-11-01", zone) as string)) / 3_600_000).toBe(25);
    // and the day they go forward is 23
    expect((Date.parse(untilOf("2026-03-08", zone) as string) - Date.parse(sinceOf("2026-03-08", zone) as string)) / 3_600_000).toBe(23);
  });

  it("puts a run made late in the evening, local time, on the day it was made, and not on the next day of Greenwich", () => {
    // 11 p.m. on 7 November in Los Angeles is 7 a.m. on 8 November UTC: it belongs to the 7th for the person
    const madeAt = Date.parse("2026-11-08T07:00:00.000Z");
    const zone = "America/Los_Angeles";
    const inDay = (day: string) => madeAt >= Date.parse(sinceOf(day, zone) as string) && madeAt < Date.parse(untilOf(day, zone) as string);
    expect([inDay("2026-11-07"), inDay("2026-11-08")]).toEqual([true, false]);
  });

  it("is two instants in the studio's own form, which the server reads", () => {
    expect(sinceOf("2026-10-02", "UTC")).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  });
});

describe("what a date box shows for an instant", () => {
  it("is the day the instant falls on, in the zone", () => {
    expect(dateOf("2026-11-08T07:00:00.000Z", "America/Los_Angeles")).toBe("2026-11-07");
    expect(dateOf("2026-11-08T07:00:00.000Z", "UTC")).toBe("2026-11-08");
    expect(dateOf("nonsense", "UTC")).toBe("");
  });

  it("round-trips what a box was set to: From shows the day its instant begins, To the last day that is in the range", () => {
    for (const zone of ["UTC", "America/Los_Angeles", "Pacific/Auckland", "Asia/Kolkata", "Europe/Dublin"]) {
      for (const day of ["2026-03-08", "2026-10-31", "2026-11-01", "2026-12-31"]) {
        expect(fromDate(sinceOf(day, zone), zone), `${zone} ${day} from`).toBe(day);
        expect(toDate(untilOf(day, zone), zone), `${zone} ${day} to`).toBe(day);
      }
    }
    expect(fromDate(null)).toBe("");
    expect(toDate(null)).toBe("");
  });
});

describe("why two dates cannot be a range", () => {
  it("is said in words, and an empty end is fine", () => {
    expect(rangeProblem("", "")).toBeNull();
    expect(rangeProblem("2026-11-01", "")).toBeNull();
    expect(rangeProblem("", "2026-11-01")).toBeNull();
    expect(rangeProblem("2026-11-01", "2026-11-01")).toBeNull(); // one whole day
    expect(rangeProblem("2026-11-02", "2026-11-01")).toMatch(/after/);
    expect(rangeProblem("2026-02-30", "")).toMatch(/not a date/);
    expect(rangeProblem("", "junk")).toMatch(/not a date/);
  });
});

describe("the browser's zone", () => {
  it("is a name, UTC where the browser will not say", () => {
    expect(typeof browserZone()).toBe("string");
    expect(browserZone().length).toBeGreaterThan(0);
  });
});
