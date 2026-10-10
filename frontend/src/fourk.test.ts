import { afterEach, describe, expect, it, vi } from "vitest";
import {
  enlargeControl, enlargeFailedText, enlargeTitle, enlargedText, filenameFromDisposition, fourKControl, fourKFailedText, fourKMadeText,
  makeTitle, megabytes, saveBlob, upscaleFailedText, upscaledText, WAITING_TITLE,
} from "./fourk";
import { initialState, reducer } from "./store";
import { makeRun } from "./testdata";
import type { EnlargeSize, FourK, FourKSize, FourKTarget, ImageInfo, UpscalerStatus } from "./types";

const FRAME: FourKSize = { width: 3840, height: 2160, trimmed: true };
const SQUARE: FourKSize = { width: 3840, height: 3840, trimmed: false };
const COPY: FourK = { width: 3840, height: 2160, bytes: 14_100_000, method: "resize", url: "/api/images/i1/4k", download_url: "/api/images/i1/4k?download=1" };
const MODEL_COPY: FourK = { ...COPY, method: "model", url: "/api/images/i1/4k?method=model", download_url: "/api/images/i1/4k?method=model&download=1" };
const ENLARGE: EnlargeSize = { width: 3840, height: 2160, trimmed: true, passes: 1 };
const READY: UpscalerStatus = { available: true, model: "RealESRGAN_x2plus.pth", reason: null, hint: null, max_enlargement: 4 };
const MISSING: UpscalerStatus = {
  available: false, model: "RealESRGAN_x2plus.pth", max_enlargement: 4,
  reason: "The upscaler model file is not there: /models/upscalers/RealESRGAN_x2plus.pth", hint: "Download it once.",
};
const image = (extra: Partial<ImageInfo> = {}): ImageInfo => ({
  id: "i1", idx: 0, seed: 7, width: 2752, height: 1536, has_alpha: false, url: "/api/images/i1", thumb_url: "/api/images/i1/thumb",
  download_url: "/api/images/i1?download=1", can_4k: true, four_k_size: FRAME, can_enlarge: true, enlarge_size: ENLARGE, four_k: null, ...extra,
});

describe("which control a picture gets (DESIGN.md §27.3)", () => {
  it("Make 4K when the server offers it and there is no copy yet, with a tooltip that says what it will make", () => {
    expect(fourKControl(image(), false)).toEqual({ kind: "make", label: "Make 4K", title: makeTitle(FRAME) });
  });

  it("Making 4K… while the request is under way", () => {
    expect(fourKControl(image(), true)).toMatchObject({ kind: "making", label: "Making 4K…" });
  });

  it("Download 4K once the copy exists, with its size in the tooltip", () => {
    expect(fourKControl(image({ four_k: COPY }), false)).toEqual({
      kind: "download", label: "Download 4K", href: "/api/images/i1/4k?download=1",
      title: "The 4K copy: 3840×2160 PNG, 14.1 MB",
    });
  });

  it("an existing copy is offered even when the server no longer offers Make 4K, and even while another is being made", () => {
    expect(fourKControl(image({ can_4k: false, four_k_size: null, four_k: COPY }), false)?.kind).toBe("download");
    expect(fourKControl(image({ four_k: COPY }), true)?.kind).toBe("download");
  });

  it("nothing when the server does not offer it: the page repeats no rule of its own", () => {
    expect(fourKControl(image({ can_4k: false, four_k_size: null }), false)).toBeNull();
    expect(fourKControl(image({ can_4k: false, four_k_size: null }), true)).toBeNull();
    // 16:9, but the server says no (too small, say): the page believes the server
    expect(fourKControl(image({ width: 1536, height: 864, can_4k: false, four_k_size: null }), false)).toBeNull();
    // and the reverse: a shape the page would never guess at is offered if the server says so
    expect(fourKControl(image({ width: 2048, height: 2048, can_4k: true, four_k_size: SQUARE }), false)?.kind).toBe("make");
  });

  it("an edit's source is a picture like any other: the same control, from the same three fields", () => {
    const source: FourKTarget = { id: "in1", can_4k: true, four_k_size: SQUARE, can_enlarge: true, enlarge_size: { ...ENLARGE, width: 3840, height: 3840, trimmed: false }, four_k: null };
    expect(fourKControl(source, false)).toMatchObject({ kind: "make", title: makeTitle(SQUARE) });
    expect(fourKControl({ ...source, four_k: { ...COPY, width: 3840, height: 3840, download_url: "/api/images/in1/4k?download=1" } }, false))
      .toMatchObject({ kind: "download", href: "/api/images/in1/4k?download=1", title: "The 4K copy: 3840×3840 PNG, 14.1 MB" });
  });
});

describe("the words", () => {
  it("the tooltip names the size it will make and, for a 16:9 picture, the trim", () => {
    expect(makeTitle(FRAME)).toBe("Make a 3840×2160 copy of this picture, trimmed to exactly 16:9, with a standard resize. It makes the picture bigger, not sharper: no detail is added.");
    expect(makeTitle(SQUARE)).toBe("Make a 3840×3840 copy of this picture with a standard resize. It makes the picture bigger, not sharper: no detail is added.");
    expect(makeTitle({ width: 3840, height: 2160, trimmed: false })).not.toContain("trimmed");
  });

  it("an upright 9:16 picture is trimmed to 9:16, not 16:9", () => {
    expect(makeTitle({ width: 2160, height: 3840, trimmed: true })).toContain("trimmed to exactly 9:16");
  });

  it("with no size to name it still says what it does and does not do", () => {
    expect(makeTitle(null)).toBe("Make a 4K copy of this picture with a standard resize. It makes the picture bigger, not sharper: no detail is added.");
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

describe("a picture from the computer (DESIGN.md §27.9)", () => {
  it("the file name comes from the UTF-8 form of the header when there is one", () => {
    const real = `attachment; filename="upscale_dog_3840x2160_20261009-181500.png"; filename*=UTF-8''upscale_dog_3840x2160_20261009-181500.png`;
    expect(filenameFromDisposition(real)).toBe("upscale_dog_3840x2160_20261009-181500.png");
    expect(filenameFromDisposition(`attachment; filename="upscale__3840x2160_x.png"; filename*=UTF-8''upscale_%E5%86%99%E7%9C%9F_3840x2160_x.png`))
      .toBe("upscale_写真_3840x2160_x.png");
  });

  it("and from the plain form when that is all there is, or when the escapes are broken", () => {
    expect(filenameFromDisposition('attachment; filename="plain name.png"')).toBe("plain name.png");
    expect(filenameFromDisposition("attachment; filename=unquoted.png")).toBe("unquoted.png");
    expect(filenameFromDisposition(`attachment; filename="fallback.png"; filename*=UTF-8''bad%E0%A4%A.png`)).toBe("fallback.png");
  });

  it("no header, or none with a name, gives nothing, so the page can use its own", () => {
    expect(filenameFromDisposition(null)).toBeNull();
    expect(filenameFromDisposition("")).toBeNull();
    expect(filenameFromDisposition("attachment")).toBeNull();
  });

  it("what is said when it worked, and when it did not", () => {
    expect(upscaledText("dog.jpg", { width: 3840, height: 2160, blob: { size: 12_345_678 } }))
      .toBe("Upscaled dog.jpg to 3840×2160 (12.3 MB). Your download has started.");
    expect(upscaleFailedText("dog.jpg", "This picture is already 4K or bigger.")).toBe("Couldn't upscale dog.jpg: This picture is already 4K or bigger.");
  });

  describe("saving the blob", () => {
    afterEach(() => vi.restoreAllMocks());

    it("clicks a link with the name and the blob's address, and lets the address go afterwards", () => {
      vi.useFakeTimers();
      const created = vi.fn(() => "blob:studio-1");
      const revoked = vi.fn();
      vi.stubGlobal("URL", { ...URL, createObjectURL: created, revokeObjectURL: revoked });
      const seen: { href: string; download: string }[] = [];
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        seen.push({ href: this.href, download: this.download });
      });
      saveBlob(new Blob(["x"], { type: "image/png" }), "upscale_dog_3840x2160_x.png");
      expect(seen).toEqual([{ href: "blob:studio-1", download: "upscale_dog_3840x2160_x.png" }]);
      expect(document.querySelector("a[download]")).toBeNull(); // the temporary link is gone
      expect(revoked).not.toHaveBeenCalled(); // not before the browser has taken it
      vi.advanceTimersByTime(10_001);
      expect(revoked).toHaveBeenCalledWith("blob:studio-1");
      vi.useRealTimers();
      vi.unstubAllGlobals();
    });
  });
});

describe("Enlarge (DESIGN.md §28)", () => {
  // Waiting… (DESIGN.md §28.9): the Enlarge control while the server has this picture's enlargement queued behind the picture being made
  it("Waiting… when the server has it queued behind the picture being made, and the title says nothing has to be done", () => {
    expect(enlargeControl(image(), true, READY, true)).toEqual({ kind: "waiting", label: "Waiting…", title: WAITING_TITLE });
    expect(WAITING_TITLE).toMatch(/starts by itself/);
  });

  it("Waiting… for a request another page made: the server's word is enough, this page need not have asked", () => {
    expect(enlargeControl(image(), false, READY, true)?.kind).toBe("waiting");
  });

  it("waiting is the server's word only while there is something to wait for: no Enlarge, no Waiting…", () => {
    expect(enlargeControl(image({ can_enlarge: false, enlarge_size: null }), true, READY, true)).toBeNull();
    expect(enlargeControl(image({ four_k: MODEL_COPY }), true, READY, true)).toBeNull();
  });

  it("when its turn comes it becomes Enlarging…, and not waiting is the default", () => {
    expect(enlargeControl(image(), true, READY, false)?.kind).toBe("enlarging");
    expect(enlargeControl(image(), true, READY)?.kind).toBe("enlarging");
  });

  it("is offered beside Make 4K when the server offers it and the model is there, with a tooltip that says what it will make", () => {
    expect(enlargeControl(image(), false, READY)).toEqual({ kind: "enlarge", label: "Enlarge", title: enlargeTitle(ENLARGE) });
    expect(fourKControl(image(), false)?.kind).toBe("make"); // and Make 4K is still there
  });

  it("is offered while the capabilities have not arrived yet: the server will answer", () => {
    expect(enlargeControl(image(), false, null)?.kind).toBe("enlarge");
  });

  it("says Enlarging… while the request is under way, and that is not pressable again", () => {
    expect(enlargeControl(image(), true, READY)).toMatchObject({ kind: "enlarging", label: "Enlarging…" });
  });

  it("an enlargement under way stays an enlargement under way even if the model has just gone away", () => {
    expect(enlargeControl(image(), true, MISSING)?.kind).toBe("enlarging");
  });

  it("without the model file it is shown, dimmed, with the reason and what to do as its tooltip", () => {
    expect(enlargeControl(image(), false, MISSING)).toEqual({
      kind: "unavailable", label: "Enlarge",
      title: "The upscaler model file is not there: /models/upscalers/RealESRGAN_x2plus.pth Download it once.",
    });
    expect(enlargeControl(image(), false, { ...MISSING, reason: null, hint: null })).toMatchObject({ kind: "unavailable", title: "Enlarge is not available here." });
    expect(enlargeControl(image(), false, { ...MISSING, hint: null })?.title).toBe("The upscaler model file is not there: /models/upscalers/RealESRGAN_x2plus.pth");
  });

  it("nothing when the server does not offer it for this picture: the page repeats no rule of its own", () => {
    expect(enlargeControl(image({ can_enlarge: false, enlarge_size: null }), false, READY)).toBeNull();
    expect(enlargeControl(image({ can_enlarge: false, enlarge_size: null }), true, MISSING)).toBeNull();
    // a picture Make 4K cannot do but Enlarge can: offered by Enlarge alone
    const half = image({ can_4k: false, four_k_size: null });
    expect(fourKControl(half, false)).toBeNull();
    expect(enlargeControl(half, false, READY)?.kind).toBe("enlarge");
  });

  it("nothing once the picture has an enlarged copy: Download 4K is all there is", () => {
    const done = image({ four_k: MODEL_COPY });
    expect(enlargeControl(done, false, READY)).toBeNull();
    expect(fourKControl(done, false)?.kind).toBe("download");
  });

  it("still offered after a plain Make 4K copy, which it replaces: both Download 4K and Enlarge show", () => {
    const plain = image({ four_k: COPY });
    expect(fourKControl(plain, false)?.kind).toBe("download");
    expect(enlargeControl(plain, false, READY)?.kind).toBe("enlarge");
  });

  it("an edit's source is a picture like any other", () => {
    const source: FourKTarget = { id: "in1", can_4k: false, four_k_size: null, can_enlarge: true, enlarge_size: { ...ENLARGE, trimmed: false }, four_k: null };
    expect(enlargeControl(source, false, READY)).toMatchObject({ kind: "enlarge", title: enlargeTitle({ ...ENLARGE, trimmed: false }) });
  });

  it("the Download 4K tooltip says which kind of copy it is", () => {
    expect(fourKControl(image({ four_k: COPY }), false)).toMatchObject({ title: "The 4K copy: 3840×2160 PNG, 14.1 MB" });
    expect(fourKControl(image({ four_k: MODEL_COPY }), false)).toEqual({
      kind: "download", label: "Download 4K", href: "/api/images/i1/4k?method=model&download=1",
      title: "The enlarged 4K copy: 3840×2160 PNG, 14.1 MB, made with an upscaler model",
    });
  });

  it("the tooltip names the size, the trim, and what the model's detail is", () => {
    expect(enlargeTitle(ENLARGE)).toBe("Enlarge this picture to 3840×2160 (trimmed to exactly 16:9) with an upscaler model: the same picture, sharper than Make 4K, but it takes longer. The extra detail is the model's guess.");
    expect(enlargeTitle({ ...ENLARGE, trimmed: false, width: 3840, height: 3840 })).toBe("Enlarge this picture to 3840×3840 with an upscaler model: the same picture, sharper than Make 4K, but it takes longer. The extra detail is the model's guess.");
    expect(enlargeTitle({ width: 2160, height: 3840, trimmed: true, passes: 1 })).toContain("trimmed to exactly 9:16");
    expect(enlargeTitle(null)).toBe("Enlarge this picture to 4K with an upscaler model: the same picture, sharper than Make 4K, but it takes longer. The extra detail is the model's guess.");
  });

  it("what is said when it is done names the size and where to find it", () => {
    expect(enlargedText(image({ four_k: MODEL_COPY }))).toBe("Enlarged to 3840×2160, 14.1 MB. Use Download 4K.");
    expect(enlargedText(image())).toBe("The enlarged copy is ready. Use Download 4K.");
  });

  it("a failure gives the server's reason and its hint after a short lead; a vanished picture says so instead", () => {
    expect(enlargeFailedText(503, "The upscaler model file is not there: /m/x.pth", "Download it once.")).toBe(
      "Couldn't enlarge: The upscaler model file is not there: /m/x.pth Download it once.");
    expect(enlargeFailedText(500, "Out of memory while enlarging.", null)).toBe("Couldn't enlarge: Out of memory while enlarging.");
    expect(enlargeFailedText(504, "Enlarging took longer than 30 minutes and was stopped.", "Use the GPU.")).toContain("Use the GPU.");
    expect(enlargeFailedText(0, "Can't reach the studio server.", null)).toBe("Couldn't enlarge: Can't reach the studio server.");
    expect(enlargeFailedText(404, "Image not found.", null)).toBe("That picture no longer exists, so there is nothing to enlarge.");
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
