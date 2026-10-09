import { describe, expect, it } from "vitest";
import { makeRun } from "./testdata";
import type { ImageInfo, ImageRun, RunInput } from "./types";
import { viewerItems, viewerKnown, viewerTitle } from "./viewer";

const image = (idx: number, seed: number): ImageInfo => ({
  id: `img${idx}`, idx, seed, width: 1024, height: 768, has_alpha: idx === 1, url: `/api/images/img${idx}`, thumb_url: `/api/images/img${idx}/thumb`,
  download_url: `/api/images/img${idx}?download=1`, can_4k: false, four_k: null,
});
const input = (position: number): RunInput => ({
  position, role: "reference", id: `in${position}`, width: 600, height: 400, has_alpha: false, url: `/api/images/in${position}`, thumb_url: `/api/images/in${position}/thumb`,
});
const generateRun = (n: number): ImageRun => makeRun({ images: Array.from({ length: n }, (_, i) => image(i, 100 + i)) });
const editRun = (sources: number, results: number): ImageRun =>
  makeRun({ mode: "edit", prompt: "put image 1 into image 2", inputs: Array.from({ length: sources }, (_, i) => input(i + 1)), images: Array.from({ length: results }, (_, i) => image(i, 500 + i)) });

describe("what the viewer pages through", () => {
  it("a Generate run: its results only", () => {
    const items = viewerItems(generateRun(3));
    expect(items.map((i) => i.kind)).toEqual(["result", "result", "result"]);
    expect(items.map((i) => i.number)).toEqual([1, 2, 3]);
  });

  it("an Edit run: the sources in order, then the results", () => {
    const items = viewerItems(editRun(3, 2));
    expect(items.map((i) => `${i.kind}${i.number}`)).toEqual(["source1", "source2", "source3", "result1", "result2"]);
    expect(items.every((i) => i.of === (i.kind === "source" ? 3 : 2))).toBe(true);
  });

  it("sources are ordered by position even if they arrive shuffled", () => {
    const run = editRun(3, 1);
    run.inputs = [run.inputs[2], run.inputs[0], run.inputs[1]];
    expect(viewerItems(run).slice(0, 3).map((i) => (i.kind === "source" ? i.input.position : 0))).toEqual([1, 2, 3]);
  });

  it("a run with nothing to show has no items", () => {
    expect(viewerItems(makeRun())).toEqual([]);
  });
});

describe("the title", () => {
  it("a Generate batch reads Image N of M with the seed and size, as it always has", () => {
    const items = viewerItems(generateRun(3));
    expect(viewerTitle(items[1], false)).toBe("Image 2 of 3 · seed 101 · 1024×768");
  });

  it("a single Generate image has no counter", () => {
    expect(viewerTitle(viewerItems(generateRun(1))[0], false)).toBe("seed 100 · 1024×768");
  });

  it("an Edit run's results say Result, and its sources say Source, so they cannot be confused", () => {
    const items = viewerItems(editRun(2, 2));
    expect(viewerTitle(items[0], true)).toBe("Source 1 of 2 · 600×400");
    expect(viewerTitle(items[2], true)).toBe("Result 1 of 2 · seed 500 · 1024×768");
  });

  it("an Edit run's single result still says Result", () => {
    expect(viewerTitle(viewerItems(editRun(1, 1))[1], true)).toBe("Result 1 of 1 · seed 500 · 1024×768");
  });
});

describe("the picture as the tray takes it", () => {
  it("a result is taken by its image id, with its thumbnail, size and transparency", () => {
    const run = generateRun(2);
    expect(viewerKnown(run, viewerItems(run)[1])).toMatchObject({ imageId: "img1", thumbUrl: "/api/images/img1/thumb", width: 1024, height: 768, hasAlpha: true });
  });

  it("a source is taken by the id of the run's own copy", () => {
    const run = editRun(2, 1);
    expect(viewerKnown(run, viewerItems(run)[1])).toMatchObject({ imageId: "in2", width: 600, height: 400, hasAlpha: false });
  });

  it("without a thumbnail it falls back to the full picture", () => {
    const run = generateRun(1);
    run.images[0].thumb_url = null;
    expect(viewerKnown(run, viewerItems(run)[0]).thumbUrl).toBe("/api/images/img0");
  });

  it("the name carries the start of the prompt, shortened if it is long", () => {
    const run = generateRun(1);
    run.prompt = "x".repeat(80);
    expect(viewerKnown(run, viewerItems(run)[0]).name).toBe(`${"x".repeat(40)}… (100)`);
  });
});
