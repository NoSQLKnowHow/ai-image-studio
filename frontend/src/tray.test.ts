import { describe, expect, it } from "vitest";
import {
  addItems,
  editCost,
  followedPosition,
  inputRefs,
  insertReference,
  moveBy,
  moveItem,
  positionOf,
  removeItem,
  room,
  shapeFromForRequest,
  submitBlock,
  type TrayItem,
} from "./tray";

function item(key: string, over: Partial<TrayItem> = {}): TrayItem {
  return {
    key, name: `${key}.png`, state: "ready", progress: 1, ref: { upload_id: `up-${key}` }, thumbUrl: `/t/${key}`,
    previewUrl: null, width: 100, height: 80, hasAlpha: false, missing: false, ...over,
  };
}
const keys = (items: readonly TrayItem[]) => items.map((i) => i.key).join("");
const tray = (letters: string) => [...letters].map((k) => item(k));

describe("the cap", () => {
  it("room is what is left, never negative", () => {
    expect(room(tray("ab"), 4)).toBe(2);
    expect(room(tray("abcd"), 4)).toBe(0);
    expect(room(tray("abcdef"), 4)).toBe(0);
  });

  it("adding keeps the first images that fit and counts the rest as skipped", () => {
    const result = addItems(tray("ab"), tray("cdefg"), 4);
    expect(keys(result.items)).toBe("abcd");
    expect(result.skipped).toBe(3);
  });

  it("adding to a full tray adds nothing, and nothing is skipped silently", () => {
    const result = addItems(tray("abcd"), tray("e"), 4);
    expect(keys(result.items)).toBe("abcd");
    expect(result.skipped).toBe(1);
  });

  it("adding within the cap skips nothing", () => {
    expect(addItems(tray("a"), tray("bc"), 4)).toMatchObject({ skipped: 0 });
  });

  it("adding never changes the lists it was given", () => {
    const first = tray("ab");
    addItems(first, tray("c"), 4);
    expect(keys(first)).toBe("ab");
  });
});

describe("order", () => {
  it("removing keeps the others in order, and a missing key changes nothing", () => {
    expect(keys(removeItem(tray("abc"), "b"))).toBe("ac");
    expect(keys(removeItem(tray("abc"), "z"))).toBe("abc");
  });

  it("moving puts the item at a place and shifts the rest", () => {
    expect(keys(moveItem(tray("abcd"), "d", 0))).toBe("dabc");
    expect(keys(moveItem(tray("abcd"), "a", 2))).toBe("bcad");
    expect(keys(moveItem(tray("abcd"), "b", 1))).toBe("abcd");
  });

  it("moving clamps to the ends and ignores a key that isn't there", () => {
    expect(keys(moveItem(tray("abc"), "a", 99))).toBe("bca");
    expect(keys(moveItem(tray("abc"), "c", -5))).toBe("cab");
    expect(keys(moveItem(tray("abc"), "z", 0))).toBe("abc");
  });

  it("Move earlier and Move later step by one and stop at the ends", () => {
    expect(keys(moveBy(tray("abc"), "b", -1))).toBe("bac");
    expect(keys(moveBy(tray("abc"), "b", 1))).toBe("acb");
    expect(keys(moveBy(tray("abc"), "a", -1))).toBe("abc");
    expect(keys(moveBy(tray("abc"), "c", 1))).toBe("abc");
    expect(keys(moveBy(tray("abc"), "z", 1))).toBe("abc");
  });

  it("positions count from 1, which is what 'image 1' means", () => {
    const items = tray("abc");
    expect([positionOf(items, "a"), positionOf(items, "c"), positionOf(items, "z")]).toEqual([1, 3, 0]);
  });

  it("the references sent are the ready images in the order shown", () => {
    const items = moveItem(tray("abc"), "c", 0);
    expect(inputRefs(items)).toEqual([{ upload_id: "up-c" }, { upload_id: "up-a" }, { upload_id: "up-b" }]);
  });

  it("images still uploading, or with no reference, are not sent", () => {
    const items = [item("a"), item("b", { state: "uploading", ref: null }), item("c", { ref: null })];
    expect(inputRefs(items)).toEqual([{ upload_id: "up-a" }]);
  });

  it("an image from an earlier run is sent by its id", () => {
    expect(inputRefs([item("a", { ref: { image_id: "img1" } })])).toEqual([{ image_id: "img1" }]);
  });
});

describe("what blocks Generate", () => {
  it("an empty tray", () => {
    expect(submitBlock([])).toBe("Add at least one image to edit.");
  });

  it("uploads that are still going, singular and plural", () => {
    expect(submitBlock([item("a"), item("b", { state: "uploading" })])).toBe("Waiting for 1 upload to finish.");
    expect(submitBlock([item("a", { state: "uploading" }), item("b", { state: "uploading" })])).toBe("Waiting for 2 uploads to finish.");
  });

  it("an image whose file is gone, named by its place", () => {
    expect(submitBlock([item("a"), item("b", { missing: true })])).toBe("Image 2 is no longer available. Remove it or add it again.");
  });

  it("uploading is reported before a missing image", () => {
    expect(submitBlock([item("a", { missing: true }), item("b", { state: "uploading" })])).toContain("Waiting for 1 upload");
  });

  it("nothing, when every image is ready", () => {
    expect(submitBlock(tray("ab"))).toBeNull();
  });
});

describe("the result's shape (decision #30)", () => {
  it("follows the last image unless one was chosen", () => {
    expect(followedPosition(tray("abc"), null)).toBe(3);
    expect(followedPosition(tray("abc"), "a")).toBe(1);
  });

  it("a chosen image is followed by identity, so reordering does not change which one", () => {
    const items = moveItem(tray("abc"), "a", 2);
    expect(followedPosition(items, "a")).toBe(3); // 'a' is now last, and still the one chosen
    expect(followedPosition(moveItem(tray("abc"), "c", 0), "a")).toBe(2);
  });

  it("a chosen image that was removed falls back to the last", () => {
    expect(followedPosition(tray("ab"), "z")).toBe(2);
  });

  it("an empty tray follows nothing", () => {
    expect(followedPosition([], null)).toBe(0);
  });

  it("shape_from is sent only for an image other than the default", () => {
    expect(shapeFromForRequest(tray("abc"), null)).toBeNull();
    expect(shapeFromForRequest(tray("abc"), "a")).toBe(1);
    expect(shapeFromForRequest(tray("abc"), "b")).toBe(2);
    expect(shapeFromForRequest(tray("abc"), "c")).toBeNull(); // the last is what the pipeline does anyway
    expect(shapeFromForRequest(tray("abc"), "z")).toBeNull();
  });
});

describe("inserting 'image N' into the prompt", () => {
  it("into an empty prompt", () => {
    expect(insertReference("", 0, 0, 2)).toEqual({ text: "image 2", caret: 7 });
  });

  it("at the end of a sentence, with a space before it", () => {
    expect(insertReference("put the dog from", 16, 16, 1)).toEqual({ text: "put the dog from image 1", caret: 24 });
  });

  it("at the start, with a space after it", () => {
    expect(insertReference("into the scene", 0, 0, 3)).toEqual({ text: "image 3 into the scene", caret: 8 });
  });

  it("in the middle of a word, with a space either side", () => {
    expect(insertReference("abcd", 2, 2, 1)).toEqual({ text: "ab image 1 cd", caret: 11 });
  });

  it("replaces the selected text", () => {
    expect(insertReference("put it in THAT place", 10, 14, 2)).toEqual({ text: "put it in image 2 place", caret: 17 });
  });

  it("an end before the start is the same selection", () => {
    expect(insertReference("put it in THAT place", 14, 10, 2)).toEqual(insertReference("put it in THAT place", 10, 14, 2));
  });

  it("adds no space after whitespace, an opening bracket or a quote", () => {
    expect(insertReference("a ", 2, 2, 1)?.text).toBe("a image 1");
    expect(insertReference("a\n", 2, 2, 1)?.text).toBe("a\nimage 1");
    expect(insertReference("(", 1, 1, 1)?.text).toBe("(image 1");
    expect(insertReference('say "', 5, 5, 1)?.text).toBe('say "image 1');
  });

  it("adds no space before punctuation or whitespace that follows", () => {
    expect(insertReference("a ,b", 2, 2, 1)?.text).toBe("a image 1,b");
    expect(insertReference("a.", 1, 1, 1)?.text).toBe("a image 1.");
    expect(insertReference("a )", 2, 2, 1)?.text).toBe("a image 1)");
    expect(insertReference("a b", 2, 2, 1)?.text).toBe("a image 1 b"); // a word follows: a space is added after
  });

  it("puts the caret right after the token when a space already follows", () => {
    const result = insertReference("hello world", 5, 5, 1)!;
    expect(result.text).toBe("hello image 1 world");
    expect(result.text.slice(0, result.caret)).toBe("hello image 1");
  });

  it("puts the caret after a space it had to add, ready for the next word", () => {
    const result = insertReference("helloworld", 5, 5, 1)!;
    expect(result.text).toBe("hello image 1 world");
    expect(result.text.slice(0, result.caret)).toBe("hello image 1 ");
  });

  it("clamps an out-of-range caret", () => {
    expect(insertReference("abc", 99, 99, 1)?.text).toBe("abc image 1");
    expect(insertReference("abc", -4, -4, 1)?.text).toBe("image 1 abc");
  });

  it("refuses to make the prompt longer than its limit", () => {
    expect(insertReference("abc", 3, 3, 1, 8)).toBeNull(); // "abc image 1" is 11
    expect(insertReference("abc", 3, 3, 1, 11)?.text).toBe("abc image 1");
  });
});

describe("the cost hint", () => {
  it("is images × (resolution ÷ 1024)²", () => {
    expect(editCost(1, 1024, 8).units).toBe(1);
    expect(editCost(2, 1024, 8).units).toBe(2);
    expect(editCost(1, 2048, 8).units).toBe(4);
    expect(editCost(4, 2048, 8).units).toBe(16);
  });

  it("is worded in units, singular for one", () => {
    expect(editCost(1, 1024, 8).label).toBe("1 unit");
    expect(editCost(3, 1024, 8).label).toBe("3 units");
    expect(editCost(4, 2048, 8).label).toBe("16 units");
  });

  it("warns only above the threshold, not at it", () => {
    expect(editCost(2, 2048, 8).heavy).toBe(false); // 8 units: at the threshold
    expect(editCost(3, 2048, 8).heavy).toBe(true); // 12
    expect(editCost(4, 1024, 8).heavy).toBe(false);
  });

  it("never warns when the threshold is 0", () => {
    expect(editCost(10, 2048, 0).heavy).toBe(false);
  });

  it("an empty tray costs nothing", () => {
    expect(editCost(0, 2048, 8)).toMatchObject({ units: 0, heavy: false });
  });
});
