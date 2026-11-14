import { describe, expect, it } from "vitest";
import { buildingText, downloadLabel, holdsText, makeLabel, partsSoFar, percentText, unavailableText } from "./audiobook";

// The words of the audiobook button (DESIGN.md §44.4). The server decides what can be made; these tests keep the page's sentences true to what it says.

const so = (parts_made: number, parts_total: number, complete = parts_made === parts_total) => ({ parts_made, parts_total, complete });

describe("how many parts the file will hold", () => {
  it("says nothing when every part is finished, or none is", () => {
    expect(partsSoFar(so(12, 12))).toBeNull();
    expect(partsSoFar(so(0, 12))).toBeNull();
    expect(partsSoFar(null)).toBeNull();
  });

  it("says how many of how many for a reading that is still being made", () => {
    expect(partsSoFar(so(3, 12))).toBe("3 of 12 parts so far");
    expect(partsSoFar(so(1, 1, false))).toBe("1 of 1 part so far");
  });
});

describe("the share made", () => {
  it("is a whole percent, rounded down, and never says 100 before the file is ready", () => {
    expect(percentText(0)).toBe("0%");
    expect(percentText(null)).toBe("0%");
    expect(percentText(0.376)).toBe("37%");
    expect(percentText(0.999)).toBe("99%");
    expect(percentText(1)).toBe("99%"); // the last sample has gone in; ffmpeg has still to finish the file
    expect(percentText(-1)).toBe("0%");
  });
});

describe("the words on the button and under it", () => {
  it("offers to make the file, and says which parts when the reading is not finished", () => {
    expect(makeLabel(so(12, 12))).toBe("Make audiobook (M4B)");
    expect(makeLabel(null)).toBe("Make audiobook (M4B)");
    expect(makeLabel(so(3, 12))).toBe("Make audiobook (M4B) of 3 of 12 parts so far");
  });

  it("says how far a build has got and which part the samples are in", () => {
    expect(buildingText({ state: "building", progress: 0.37, part: 3, parts_made: 12 })).toBe("Making the audiobook… 37%, part 3 of 12.");
    expect(buildingText({ state: "building", progress: null, part: null, parts_made: 12 })).toBe("Making the audiobook… 0%.");
  });

  it("says a build that waits for another is waiting", () => {
    expect(buildingText({ state: "waiting", progress: 0, part: null, parts_made: 2 })).toBe("Waiting for another audiobook to be made first…");
  });

  it("names the file to take, with its size", () => {
    expect(downloadLabel({ bytes: 312_000_000 })).toBe("Download audiobook (M4B, 312 MB)");
    expect(downloadLabel({ bytes: null })).toBe("Download audiobook (M4B)");
  });

  it("says what the file holds: its length and a chapter for each part, and which parts if the reading is not finished", () => {
    expect(holdsText({ seconds: 4200, chapters: 12, parts_made: 12, parts_total: 12, complete: true })).toBe("1 h 10 min, 12 chapters, one for each part.");
    expect(holdsText({ seconds: 600, chapters: 1, parts_made: 1, parts_total: 3, complete: false })).toBe("10 min, 1 chapter, one for each part (1 of 3 parts so far).");
  });
});

describe("why the button is off", () => {
  it("is nothing while the file can be made, or when nothing is known", () => {
    expect(unavailableText({ available: true, reason: null, hint: null })).toBeNull();
    expect(unavailableText(undefined)).toBeNull();
    expect(unavailableText(null)).toBeNull();
  });

  it("is the server's reason and its hint, in words", () => {
    expect(unavailableText({ available: false, reason: "The audiobook file needs ffmpeg with an AAC encoder, and this server's ffmpeg has none.", hint: "The ZIP still has every part and its chapter lists." }))
      .toBe("The audiobook file needs ffmpeg with an AAC encoder, and this server's ffmpeg has none. The ZIP still has every part and its chapter lists.");
    expect(unavailableText({ available: false, reason: "No ffmpeg.", hint: null })).toBe("No ffmpeg.");
  });

  it("still says something plain when the server gave no reason", () => {
    expect(unavailableText({ available: false, reason: null, hint: null })).toBe("The audiobook file cannot be made on this server.");
  });
});
