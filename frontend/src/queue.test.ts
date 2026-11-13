import { describe, expect, it } from "vitest";
import { applyMove, canStep, kindWords, moveForSlot, movedWords, slotAt, stepMove } from "./queue";
import type { QueueItem } from "./types";

// What the Queue panel works out before it asks the server anything (DESIGN.md section 40, criteria 306 to 313). All of it is pure, so it is tried exactly, and the moves are
// tried against a plain list, for every list up to six jobs and every job and place, since the classic mistake of dragging is one too many or too few.

const job = (id: string, over: Partial<QueueItem> = {}): QueueItem => ({ id, kind: "image", title: `job ${id}`, draft: false, movable: true, position: null, ...over });
const jobs = (n: number): QueueItem[] => Array.from({ length: n }, (_, i) => job(String.fromCharCode(97 + i), { position: i + 1 }));
const ids = (items: readonly QueueItem[]) => items.map((item) => item.id).join("");

describe("kindWords: the model a job needs, as the panel says it", () => {
  it("names each kind, and a drawing is a drawing although it is made with the image model", () => {
    expect([kindWords("image"), kindWords("music"), kindWords("vector"), kindWords("speech"), kindWords("voice")]).toEqual(["Image", "Music", "Drawing", "Speech", "Voice"]);
  });

  it("says job for a kind a newer server has", () => {
    expect(kindWords("hologram" as never)).toBe("Job");
  });
});

describe("slotAt: where a job dragged to a height would land", () => {
  const rows = [{ top: 0, bottom: 40 }, { top: 44, bottom: 84 }, { top: 88, bottom: 128 }];   // three rows, 4 px apart

  it.each([[-50, 0], [0, 0], [20, 0], [20.5, 1], [60, 1], [64.5, 2], [100, 2], [108.5, 3], [500, 3]])("at %s it is slot %s", (y, slot) => {
    expect(slotAt(rows, y)).toBe(slot);
  });

  it("is slot 0 where there are no rows", () => {
    expect(slotAt([], 50)).toBe(0);
  });

  it("changes half way through a row, which is where a hand expects the line to move", () => {
    expect(slotAt(rows, 63.9)).toBe(1);   // the middle of the second row is 64
    expect(slotAt(rows, 64.1)).toBe(2);
  });
});

describe("moveForSlot: the request that puts a job in a place", () => {
  it("is before the job now in that place, or after the last for the end", () => {
    const items = jobs(4);
    expect(moveForSlot(items, 3, 0)).toEqual({ id: "d", before: "a" });
    expect(moveForSlot(items, 0, 3)).toEqual({ id: "a", before: "d" });
    expect(moveForSlot(items, 0, 4)).toEqual({ id: "a", after: "d" });
    expect(moveForSlot(items, 1, 4)).toEqual({ id: "b", after: "d" });
  });

  it("is nothing where the job already is: in front of itself or after itself", () => {
    const items = jobs(4);
    for (let i = 0; i < 4; i++) {
      expect(moveForSlot(items, i, i)).toBeNull();
      expect(moveForSlot(items, i, i + 1)).toBeNull();
    }
  });

  it("is nothing for places and jobs that are not there", () => {
    const items = jobs(3);
    expect(moveForSlot(items, -1, 1)).toBeNull();
    expect(moveForSlot(items, 3, 0)).toBeNull();
    expect(moveForSlot(items, 0, -1)).toBeNull();
    expect(moveForSlot(items, 0, 4)).toBeNull();
    expect(moveForSlot(items, 0.5, 2)).toBeNull();
    expect(moveForSlot([], 0, 0)).toBeNull();
  });

  it("never names the job itself as the one to go next to", () => {
    for (let n = 1; n <= 6; n++) {
      const items = jobs(n);
      for (let from = 0; from < n; from++) {
        for (let slot = 0; slot <= n; slot++) {
          const move = moveForSlot(items, from, slot);
          if (move) expect(move.before ?? move.after).not.toBe(move.id);
        }
      }
    }
  });
});

describe("applyMove: the queue as it will be", () => {
  it("puts a job before or after another and numbers the places again", () => {
    const items = jobs(4);
    expect(ids(applyMove(items, { id: "d", before: "a" }))).toBe("dabc");
    expect(ids(applyMove(items, { id: "a", after: "c" }))).toBe("bcad");
    expect(applyMove(items, { id: "d", before: "a" }).map((item) => item.position)).toEqual([1, 2, 3, 4]);
  });

  it("changes nothing for a request that names a job that is not there, or the job itself, or nothing", () => {
    const items = jobs(3);
    for (const move of [{ id: "x", before: "a" }, { id: "a", before: "x" }, { id: "a", before: "a" }, { id: "a" }]) expect(ids(applyMove(items, move))).toBe("abc");
  });

  it("does not change the list it is given", () => {
    const items = jobs(3);
    applyMove(items, { id: "c", before: "a" });
    expect(ids(items)).toBe("abc");
  });

  it("keeps everything else about each job", () => {
    const items = [job("a", { kind: "music", draft: true }), job("b", { movable: false })];
    expect(applyMove(items, { id: "b", before: "a" })).toEqual([{ ...items[1], position: 1 }, { ...items[0], position: 2 }]);
  });
});

describe("a drop and a plain list agree, for every list up to six jobs and every job and place", () => {
  it("moves the job to where the line showed", () => {
    for (let n = 1; n <= 6; n++) {
      const items = jobs(n);
      for (let from = 0; from < n; from++) {
        for (let slot = 0; slot <= n; slot++) {
          const move = moveForSlot(items, from, slot);
          const rest = items.filter((_, i) => i !== from);
          const at = slot - (slot > from ? 1 : 0);                                       // the line was drawn before the job now in `slot`, and the job itself is no longer there
          const expected = [...rest.slice(0, at), items[from], ...rest.slice(at)];
          expect(ids(move ? applyMove(items, move) : items), `n=${n} from=${from} slot=${slot}`).toBe(ids(expected));
        }
      }
    }
  });
});

describe("the arrows", () => {
  it("put a job before the one above it, or after the one below it", () => {
    const items = jobs(3);
    expect(stepMove(items, 1, -1)).toEqual({ id: "b", before: "a" });
    expect(stepMove(items, 1, 1)).toEqual({ id: "b", after: "c" });
  });

  it("do nothing at the ends", () => {
    const items = jobs(3);
    expect(stepMove(items, 0, -1)).toBeNull();
    expect(stepMove(items, 2, 1)).toBeNull();
    expect(canStep(items, 0, -1)).toBe(false);
    expect(canStep(items, 2, 1)).toBe(false);
    expect(canStep(items, 0, 1) && canStep(items, 2, -1)).toBe(true);
  });

  it("do nothing for a job that has started, but another job can be moved past it", () => {
    const items = [job("a"), job("b", { movable: false }), job("c")];
    expect(canStep(items, 1, -1) || canStep(items, 1, 1)).toBe(false);
    expect(stepMove(items, 1, 1)).toBeNull();
    expect(stepMove(items, 0, 1)).toEqual({ id: "a", after: "b" });      // a goes after the started one
    expect(stepMove(items, 2, -1)).toEqual({ id: "c", before: "b" });    // and c before it
  });

  it("do nothing for a job that is not in the list", () => {
    expect(canStep(jobs(2), 5, -1)).toBe(false);
    expect(stepMove(jobs(2), 5, 1)).toBeNull();
  });
});

describe("movedWords: what a screen reader is told", () => {
  it("says the job's words and its new place", () => {
    const items = applyMove(jobs(3), { id: "c", before: "a" });
    expect(movedWords(items, "c")).toBe("Moved “job c” to place 1 of 3.");
    expect(movedWords(items, "b")).toBe("Moved “job b” to place 3 of 3.");
  });

  it("says nothing for a job that is not there", () => {
    expect(movedWords(jobs(2), "x")).toBeNull();
    expect(movedWords([], "a")).toBeNull();
  });
});
