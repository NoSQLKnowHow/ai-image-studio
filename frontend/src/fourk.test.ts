import { describe, expect, it } from "vitest";
import { MAKE_4K_TITLE, fourKControl, fourKFailedText, fourKMadeText, megabytes } from "./fourk";
import { initialState, reducer } from "./store";
import { makeRun } from "./testdata";
import type { FourK, ImageInfo } from "./types";

const COPY: FourK = { width: 3840, height: 2160, bytes: 14_100_000, url: "/api/images/i1/4k", download_url: "/api/images/i1/4k?download=1" };
const image = (extra: Partial<ImageInfo> = {}): ImageInfo => ({
  id: "i1", idx: 0, seed: 7, width: 2752, height: 1536, has_alpha: false, url: "/api/images/i1", thumb_url: "/api/images/i1/thumb",
  download_url: "/api/images/i1?download=1", can_4k: true, four_k: null, ...extra,
});

describe("which control a picture gets (DESIGN.md §27.3)", () => {
  it("Make 4K when the server offers it and there is no copy yet", () => {
    expect(fourKControl(image(), false)).toEqual({ kind: "make", label: "Make 4K", title: MAKE_4K_TITLE });
  });

  it("Making 4K… while the request is under way", () => {
    const control = fourKControl(image(), true);
    expect(control).toMatchObject({ kind: "making", label: "Making 4K…" });
  });

  it("Download 4K once the copy exists, with its size in the tooltip", () => {
    expect(fourKControl(image({ four_k: COPY }), false)).toEqual({
      kind: "download", label: "Download 4K", href: "/api/images/i1/4k?download=1",
      title: "The 4K copy: 3840×2160 PNG, 14.1 MB",
    });
  });

  it("an existing copy is offered even when the server no longer offers Make 4K, and even while another is being made", () => {
    expect(fourKControl(image({ can_4k: false, four_k: COPY }), false)?.kind).toBe("download");
    expect(fourKControl(image({ four_k: COPY }), true)?.kind).toBe("download");
  });

  it("nothing when the server does not offer it: the page repeats no rule of its own", () => {
    expect(fourKControl(image({ can_4k: false }), false)).toBeNull();
    expect(fourKControl(image({ can_4k: false }), true)).toBeNull();
    // the shape is 16:9 but the server says no (too small, say): the page believes the server
    expect(fourKControl(image({ width: 1536, height: 864, can_4k: false }), false)).toBeNull();
    // and the reverse: a shape the page would never guess at is offered if the server says so
    expect(fourKControl(image({ width: 2000, height: 2000, can_4k: true }), false)?.kind).toBe("make");
  });
});

describe("the words", () => {
  it("the tooltip says what it does and what it does not do", () => {
    expect(MAKE_4K_TITLE).toContain("exactly 16:9");
    expect(MAKE_4K_TITLE).toContain("3840×2160");
    expect(MAKE_4K_TITLE).toContain("standard resize");
    expect(MAKE_4K_TITLE).toContain("bigger, not sharper");
    expect(MAKE_4K_TITLE).toContain("no detail is added");
  });

  it("megabytes are decimal, with one decimal place", () => {
    expect(megabytes(14_100_000)).toBe("14.1 MB");
    expect(megabytes(999_999)).toBe("1.0 MB");
    expect(megabytes(0)).toBe("0.0 MB");
    expect(megabytes(25_049_999)).toBe("25.0 MB");
  });

  it("what is said when the copy is ready names its size and where to find it", () => {
    expect(fourKMadeText(image({ four_k: COPY }))).toBe("The 4K copy is ready: 3840×2160, 14.1 MB. Use Download 4K.");
    expect(fourKMadeText(image())).toBe("The 4K copy is ready. Use Download 4K.");
  });

  it("a failure gives the server's own reason after a short lead; a vanished picture says so instead", () => {
    expect(fourKFailedText(422, "This picture is too small to enlarge to 4K without it looking blurry.")).toBe(
      "Couldn't make 4K: This picture is too small to enlarge to 4K without it looking blurry.");
    expect(fourKFailedText(507, "The 4K picture could not be saved: No space left on device")).toContain("No space left on device");
    expect(fourKFailedText(0, "Can't reach the studio server.")).toBe("Couldn't make 4K: Can't reach the studio server.");
    expect(fourKFailedText(404, "Image not found.")).toBe("That picture no longer exists, so there is nothing to make 4K from.");
  });
});

describe("the store takes the updated run", () => {
  it("a finished run answered with a 4K copy replaces the copy that had none, although its stage is the same", () => {
    const before = makeRun({ id: "r1", status: "done", images: [image()] });
    const after = makeRun({ id: "r1", status: "done", images: [image({ four_k: COPY })] });
    let state = reducer(initialState, { type: "runUpsert", run: before });
    state = reducer(state, { type: "runUpsert", run: after });
    expect((state.runs.r1 as typeof after).images[0].four_k).toEqual(COPY);
  });

  it("and an older picture of the run (a queued or running copy) never takes it away", () => {
    const done = makeRun({ id: "r2", status: "done", images: [image({ four_k: COPY })] });
    let state = reducer(initialState, { type: "runUpsert", run: done });
    state = reducer(state, { type: "runUpsert", run: makeRun({ id: "r2", status: "running" }) });
    expect((state.runs.r2 as typeof done).images[0].four_k).toEqual(COPY);
  });
});
