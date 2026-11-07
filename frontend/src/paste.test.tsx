import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { useState, type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { PromptBar } from "./components/PromptBar";
import { RunCard } from "./components/RunCard";
import { Tray } from "./components/Tray";
import { UpscalePicture } from "./components/UpscalePicture";
import { defaultOptions } from "./options";
import {
  EDITING_UNAVAILABLE, GENERATE_PICTURES_HELP, NOT_A_PICTURE, PICTURES_ON_IMAGES_TAB, UPSCALE_TITLE, hasPlainText, isTextField, mayBePicture, pastedPictures, routePaste,
  switchedToEditText, takePictures, type IntakeEnv, type PasteFacts,
} from "./paste";
import { CAPS, makeImage, makeRun } from "./testdata";
import type { TrayItem } from "./tray";
import type { Capabilities } from "./types";
import { useTray, type AddOutcome } from "./useTray";
import { usePicturePaste, type PasteHandlers } from "./usePicturePaste";

// Pictures by paste and drop (DESIGN.md §36, criteria 211 to 219), in the browser stand-in: the routing rules as a table, what Generate does with pictures, the
// one page listener, the Upscale control's three ways in, and the tray at a limit of 8. The whole page is in the e2e (e2e/paste.spec.ts).

afterEach(() => cleanup());

// the tray's uploads never finish in these tests: what is checked is what the tray takes, in which order, and what it refuses
vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  uploadImage: vi.fn(() => ({ promise: new Promise(() => undefined), abort: vi.fn() })),
}));

const png = (name = "image.png", type = "image/png", size = 10) => new File([new Uint8Array(size)], name, { type });
const clip = (over: { files?: File[]; text?: string; items?: { kind: string; type: string; getAsFile: () => File | null }[] } = {}) => ({
  files: (over.files ?? []) as unknown as FileList,
  items: (over.items ?? []) as unknown as DataTransferItemList,
  getData: (kind: string) => (kind === "text/plain" ? over.text ?? "" : ""),
});

// ---------------------------------------------------------------- what is on the clipboard
describe("the pictures on a clipboard", () => {
  it("are the image files, in the order the clipboard holds them, and nothing that is not a picture", () => {
    const a = png("a.png");
    const b = png("b.jpg", "image/jpeg");
    const text = new File(["x"], "notes.txt", { type: "text/plain" });
    expect(pastedPictures(clip({ files: [a, text, b] }))).toEqual([a, b]);
    expect(pastedPictures(clip())).toEqual([]);
    expect(pastedPictures(null)).toEqual([]);
  });

  it("are read from the items when a browser lists them only there, and never from both (one picture counts once)", () => {
    const viaItem = png("from-item.png");
    const items = [{ kind: "file", type: "image/png", getAsFile: () => viaItem }, { kind: "string", type: "text/plain", getAsFile: () => null }, { kind: "file", type: "application/pdf", getAsFile: () => png("x.pdf", "application/pdf") }];
    expect(pastedPictures(clip({ items }))).toEqual([viaItem]);
    const both = png("both.png");
    expect(pastedPictures(clip({ files: [both], items: [{ kind: "file", type: "image/png", getAsFile: () => both }] }))).toEqual([both]);
  });

  it("have plain text beside them only when the clipboard says so", () => {
    expect(hasPlainText(clip({ text: "A1\tB1" }))).toBe(true);
    expect(hasPlainText(clip())).toBe(false);
    expect(hasPlainText(null)).toBe(false);
    expect(hasPlainText({ getData: () => { throw new Error("not allowed"); } })).toBe(false);
  });

  it("may be a picture when the type says so or says nothing (the server decides the second)", () => {
    expect(mayBePicture(png())).toBe(true);
    expect(mayBePicture(png("x.webp", ""))).toBe(true);
    expect(mayBePicture(png("x.pdf", "application/pdf"))).toBe(false);
  });
});

describe("where text can be pasted", () => {
  it("is a text box, a text area or an editable element, and not a button, a checkbox, a range or a file input", () => {
    const make = (tag: string, type?: string) => { const e = document.createElement(tag); if (type) (e as HTMLInputElement).type = type; return e; };
    for (const type of ["text", "search", "email", "url", "number", "password"]) expect(isTextField(make("input", type)), type).toBe(true);
    expect(isTextField(make("textarea"))).toBe(true);
    for (const type of ["button", "checkbox", "radio", "range", "file", "submit", "color"]) expect(isTextField(make("input", type)), type).toBe(false);
    expect(isTextField(make("button"))).toBe(false);
    expect(isTextField(make("div"))).toBe(false);
    expect(isTextField(null)).toBe(false);
    const editable = make("div");
    Object.defineProperty(editable, "isContentEditable", { value: true });
    expect(isTextField(editable)).toBe(true);
  });
});

// ---------------------------------------------------------------- where a paste goes
describe("where a pasted picture goes (DESIGN.md §36.2, §36.3; criteria 211, 212, 216, 217, 218)", () => {
  const prompt = Object.assign(document.createElement("textarea"), { id: "prompt" });
  const negative = Object.assign(document.createElement("input"), { id: "negative", type: "text" });
  const button = document.createElement("button");
  const upscale = Object.assign(document.createElement("button"), { id: "upscale" });
  upscale.setAttribute("data-upscale-drop", "");
  const checkbox = Object.assign(document.createElement("input"), { type: "checkbox" });
  const files = [png()];
  const facts = (over: Partial<PasteFacts> = {}): PasteFacts => ({ files, plainText: false, target: document.body, tab: "images", modalOpen: false, ready: true, ...over });
  const kind = (over: Partial<PasteFacts>) => routePaste(facts(over)).kind;

  it("never touches a paste with no picture, wherever it is: the browser pastes the text", () => {
    for (const target of [prompt, negative, button, upscale, document.body]) for (const tab of ["images", "music", "vector"] as const) {
      expect(kind({ files: [], plainText: true, target, tab })).toBe("ignore");
    }
  });

  it("takes a picture in the prompt box even when the clipboard has text too (a picture wins there, as it always has)", () => {
    expect(routePaste(facts({ target: prompt }))).toEqual({ kind: "tray", files });
    expect(kind({ target: prompt, plainText: true })).toBe("tray");
  });

  it("takes a picture on the bare page and on a button, with or without text on the clipboard", () => {
    for (const target of [document.body, button, checkbox]) for (const plainText of [false, true]) expect(kind({ target, plainText }), `${target.tagName} ${plainText}`).toBe("tray");
  });

  it("takes a picture in another text field only when the clipboard has no plain text, so a mixed clipboard pastes its text there", () => {
    expect(kind({ target: negative, plainText: false })).toBe("tray");
    expect(kind({ target: negative, plainText: true })).toBe("ignore");
  });

  it("sends the picture to Upscale a picture… only when that has the focus, and to the tray from anywhere else", () => {
    expect(routePaste(facts({ target: upscale }))).toEqual({ kind: "upscale", files });
    expect(kind({ target: button })).toBe("tray");
    expect(kind({ target: upscale, plainText: true })).toBe("upscale"); // a button takes no text
  });

  it("takes nothing while a dialog, the Options drawer or a viewer is open, or before the page knows what the server can do", () => {
    for (const target of [prompt, negative, button, upscale, document.body]) expect(kind({ target, modalOpen: true }), target.tagName).toBe("ignore");
    expect(kind({ target: prompt, ready: false })).toBe("ignore");
  });

  it("does not take a picture on the Music or Vector tab, and says where pictures are used; text beside it goes into a text field without a word", () => {
    for (const tab of ["music", "vector"] as const) {
      expect(kind({ tab, target: document.body })).toBe("wrong-tab");
      expect(kind({ tab, target: button })).toBe("wrong-tab");
      expect(kind({ tab, target: prompt })).toBe("wrong-tab"); // (a picture alone in a text box: nothing to paste but the note)
      expect(kind({ tab, target: negative, plainText: true })).toBe("ignore");
      expect(kind({ tab, target: negative, plainText: false })).toBe("wrong-tab");
    }
  });
});

describe("the toast after Generate turned into Edit", () => {
  it("counts the pictures and says the numbers to refer to them by", () => {
    expect(switchedToEditText(3, 1)).toBe("Switched to Edit: 3 pictures added. Refer to them as image 1, 2, 3.");
    expect(switchedToEditText(1, 1)).toBe("Switched to Edit: 1 picture added. Refer to it as image 1.");
    expect(switchedToEditText(2, 3)).toBe("Switched to Edit: 2 pictures added. Refer to them as image 3, 4."); // two were in the tray already
    expect(switchedToEditText(8, 1)).toBe("Switched to Edit: 8 pictures added. Refer to them as image 1, 2, 3, 4, 5, 6, 7, 8.");
  });
});

// ---------------------------------------------------------------- what Generate does with pictures
describe("pictures given to the prompt card (criteria 213, 214)", () => {
  type Spied = IntakeEnv & { addFiles: Mock; note: Mock; remove: Mock; setMode: Mock; push: Mock; trayCount: Mock };
  function setup(over: Partial<IntakeEnv> = {}, outcome: Partial<AddOutcome> = {}): Spied {
    return {
      canEdit: true, mode: "generate", trayCount: vi.fn(() => 0),
      addFiles: vi.fn((_files: File[], _quiet: boolean): AddOutcome => ({ added: 3, skipped: 0, keys: ["k1", "k2", "k3"], problems: [], ...outcome })),
      note: vi.fn(), remove: vi.fn(), setMode: vi.fn(), push: vi.fn(), ...over,
    } as Spied;
  }
  const three = [png("a.png"), png("b.png"), png("c.png")];

  it("in Edit adds them to the tray as ever, with nothing said and no switch", () => {
    const env = setup({ mode: "edit" });
    takePictures(three, env);
    expect(env.addFiles).toHaveBeenCalledWith(three, false);
    expect(env.setMode).not.toHaveBeenCalled();
    expect(env.push).not.toHaveBeenCalled();
  });

  it("in Generate adds them in order, makes the card Edit, and says how many with an Undo", () => {
    const env = setup();
    takePictures(three, env);
    expect(env.addFiles).toHaveBeenCalledWith(three, true); // quiet: the tray is not on screen yet
    expect(env.setMode).toHaveBeenCalledWith("edit");
    expect(env.push).toHaveBeenCalledTimes(1);
    const [kind, text, action] = env.push.mock.calls[0];
    expect([kind, text, action.label]).toEqual(["info", "Switched to Edit: 3 pictures added. Refer to them as image 1, 2, 3.", "Undo"]);
  });

  it("numbers the pictures by their place in the tray when it already held some", () => {
    const env = setup({ trayCount: vi.fn(() => 2) }, { added: 2, keys: ["k3", "k4"] });
    takePictures(three.slice(0, 2), env);
    expect(env.push.mock.calls[0][1]).toBe("Switched to Edit: 2 pictures added. Refer to them as image 3, 4.");
  });

  it("undoes exactly the pictures this paste added, and the switch: earlier pictures stay", () => {
    const env = setup({ trayCount: vi.fn(() => 1) }, { keys: ["new1", "new2", "new3"] });
    takePictures(three, env);
    env.setMode.mockClear();
    env.push.mock.calls[0][2].run();
    expect(env.remove.mock.calls).toEqual([["new1"], ["new2"], ["new3"]]); // each with just its key
    expect(env.setMode).toHaveBeenCalledWith("generate");
  });

  it("names, in the tray, the pictures that were refused when others were accepted", () => {
    const env = setup({}, { added: 2, keys: ["k1", "k2"], problems: ["huge.png: the file is larger than 20 MB."] });
    takePictures(three, env);
    expect(env.note).toHaveBeenCalledWith("huge.png: the file is larger than 20 MB.");
    expect(env.setMode).toHaveBeenCalledWith("edit");
  });

  it("stays in Generate when no picture is accepted, and names each one that was refused", () => {
    const env = setup({}, { added: 0, keys: [], problems: ["huge.png: the file is larger than 20 MB.", "notes.pdf: " + NOT_A_PICTURE] });
    takePictures(three, env);
    expect(env.setMode).not.toHaveBeenCalled();
    expect(env.note).not.toHaveBeenCalled(); // not into a tray nobody is looking at
    expect(env.push).toHaveBeenCalledWith("error", "huge.png: the file is larger than 20 MB. notes.pdf: That isn't a PNG, JPEG or WebP image.");
  });

  it("adds nothing and says so when the loaded pipeline cannot edit, in either mode", () => {
    for (const mode of ["generate", "edit"] as const) {
      const env = setup({ canEdit: false, mode });
      takePictures(three, env);
      expect(env.addFiles).not.toHaveBeenCalled();
      expect(env.push).toHaveBeenCalledWith("info", EDITING_UNAVAILABLE);
      expect(env.setMode).not.toHaveBeenCalled();
    }
  });

  it("does nothing for no files", () => {
    const env = setup();
    takePictures([], env);
    expect(env.addFiles).not.toHaveBeenCalled();
    expect(env.push).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------- the tray's own checks and its limit
describe("adding pictures to the tray (criteria 215)", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:preview");
    URL.revokeObjectURL = vi.fn();
  });
  const capsWith = (max: number): Capabilities => ({ ...CAPS, limits: { ...CAPS.limits, input_images: { min: 1, max } } });

  it("takes eight pictures when the server's limit is eight, skips the ninth and counts it; every picture goes up", async () => {
    const { result } = renderHook(() => useTray(capsWith(8)));
    let outcome!: AddOutcome;
    act(() => { outcome = result.current.addFiles(Array.from({ length: 9 }, (_, i) => png(`p${i}.png`))); });
    expect(outcome.added).toBe(8);
    expect(outcome.skipped).toBe(1);
    expect(outcome.problems).toEqual(["1 image did not fit: one edit takes at most 8."]);
    expect(result.current.items.map((item) => item.name)).toEqual(Array.from({ length: 8 }, (_, i) => `p${i}.png`)); // in the order they came
    expect(result.current.notes.map((n) => n.text)).toEqual(outcome.problems);
    expect(result.current.room).toBe(0);
    const { uploadImage } = await import("./api");
    expect(uploadImage).toHaveBeenCalledTimes(8);
  });

  it("keeps the shipped limit of 4 until the server says otherwise", () => {
    const { result } = renderHook(() => useTray(CAPS));
    act(() => { result.current.addFiles(Array.from({ length: 6 }, (_, i) => png(`p${i}.png`))); });
    expect(result.current.items).toHaveLength(4);
    expect(CAPS.limits.input_images.max).toBe(4);
  });

  it("refuses a file that is not a picture, and one over the size limit, each by name, and keeps the others", () => {
    const { result } = renderHook(() => useTray(CAPS));
    let outcome!: AddOutcome;
    const big = new File([new Uint8Array(1)], "huge.png", { type: "image/png" });
    Object.defineProperty(big, "size", { value: 21 * 1024 * 1024 });
    act(() => { outcome = result.current.addFiles([png("ok.png"), png("report.pdf", "application/pdf"), big]); });
    expect(outcome.added).toBe(1);
    expect(outcome.problems).toEqual([`report.pdf: ${NOT_A_PICTURE}`, "huge.png: the file is larger than 20 MB."]);
    expect(result.current.items.map((i) => i.name)).toEqual(["ok.png"]);
  });

  it("takes a file of exactly the size limit and refuses one a byte over", () => {
    const sized = (name: string, bytes: number) => Object.defineProperty(png(name), "size", { value: bytes });
    const { result } = renderHook(() => useTray(CAPS));
    let outcome!: AddOutcome;
    act(() => { outcome = result.current.addFiles([sized("exact.png", 20 * 1024 * 1024), sized("over.png", 20 * 1024 * 1024 + 1)]); });
    expect(outcome.added).toBe(1);
    expect(outcome.problems).toEqual(["over.png: the file is larger than 20 MB."]);
    expect(result.current.items.map((i) => i.name)).toEqual(["exact.png"]);
  });

  it("announces what was added, for a screen reader: the picture's number for one, the count for several", () => {
    const { result } = renderHook(() => useTray(CAPS));
    act(() => { result.current.addFiles([png("a.png")]); });
    expect(result.current.announcement).toBe("Image 1 added.");
    act(() => { result.current.addFiles([png("b.png"), png("c.png")]); });
    expect(result.current.announcement).toBe("2 images added.");
    act(() => { result.current.addFiles([png("d.png")]); });
    expect(result.current.announcement).toBe("Image 4 added."); // by its place in the tray
    act(() => { result.current.addFiles([png("report.pdf", "application/pdf")]); });
    expect(result.current.announcement).toBe("Image 4 added."); // nothing was added: nothing new is said
  });

  it("returns the reasons without posting them in the tray when asked to be quiet (Generate's tray is not on screen)", () => {
    const { result } = renderHook(() => useTray(CAPS));
    let outcome!: AddOutcome;
    act(() => { outcome = result.current.addFiles([png("report.pdf", "application/pdf")], true); });
    expect(outcome.added).toBe(0);
    expect(outcome.problems).toEqual([`report.pdf: ${NOT_A_PICTURE}`]);
    expect(result.current.notes).toEqual([]);
    act(() => result.current.note("said later"));
    expect(result.current.notes.map((n) => n.text)).toEqual(["said later"]);
  });

  it("names a pasted file that has no name by what it is", () => {
    const { result } = renderHook(() => useTray(CAPS));
    act(() => { result.current.addFiles([new File([new Uint8Array(1)], "", { type: "image/png" })]); });
    expect(result.current.items[0].name).toBe("pasted image");
  });
});

describe("the tray at a limit of 8", () => {
  const item = (n: number): TrayItem => ({ key: `k${n}`, name: `p${n}.png`, state: "ready", progress: 1, ref: { image_id: `i${n}` }, thumbUrl: `/t${n}`, previewUrl: null, width: 1024, height: 1024, hasAlpha: false, missing: false });
  const trayProps = (count: number, cap: number): ComponentProps<typeof Tray> => ({
    items: Array.from({ length: count }, (_, i) => item(i + 1)), cap, notes: [], announcement: "", showShape: false, followed: 1, heavy: null,
    onAdd: vi.fn(), onRemove: vi.fn(), onMoveBy: vi.fn(), onMoveTo: vi.fn(), onInsert: vi.fn(), onShape: vi.fn(), onMissing: vi.fn(), onDismissNote: vi.fn(),
  });

  it("reads 3 of 8 with room left, and 8 of 8 and is switched off when full", () => {
    const { unmount } = render(<Tray {...trayProps(3, 8)} />);
    const add = () => screen.getByRole("button", { name: /^Add images/ }) as HTMLButtonElement;
    expect(add().getAttribute("aria-label")).toBe("Add images (3 of 8)");
    expect(add().textContent).toContain("3 of 8");
    expect(add().disabled).toBe(false);
    unmount();
    render(<Tray {...trayProps(8, 8)} />);
    expect(add().textContent).toContain("8 of 8");
    expect(add().disabled).toBe(true);
    expect(add().title).toBe("One edit takes at most 8 images.");
    expect(document.querySelectorAll(".tray-tile")).toHaveLength(8);
  });
});

describe("an edit of eight pictures is an ordinary run (criterion 220)", () => {
  const eight = makeRun({
    id: "edit-eight", mode: "edit", prompt: "put them all together",
    inputs: Array.from({ length: 8 }, (_, i) => ({ ...makeImage(i), position: i + 1, role: "reference", id: `in${i}`.padEnd(32, "0") })) as never,
  });
  const props = (): ComponentProps<typeof RunCard> => ({
    run: eight, now: Date.parse("2026-10-05T12:00:00.000Z"), workerState: "ready", canEdit: true, making4k: new Set<string>(), enlarging: new Set<string>(),
    enlargeWaiting: new Set<string>(), transient: false, viewScope: "kept", upscaler: null, onReuse: vi.fn(), onRegenerateLarger: vi.fn(), onMake4K: vi.fn(), onEnlarge: vi.fn(),
    onEditThis: vi.fn(), onRetry: vi.fn(), onCancel: vi.fn(), onToggleKeep: vi.fn(), onRestore: vi.fn(), onDelete: vi.fn(), onCopy: vi.fn(), onOpenImage: vi.fn(),
    projects: { projects: [], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null) },
  });

  it("shows every source picture on its card, numbered in order, each opening the viewer at its place, and says how many it was given", () => {
    const p = props();
    render(<RunCard {...p} />);
    const sources = Array.from(document.querySelectorAll(".run-sources button.source-thumb")) as HTMLElement[];
    expect(sources).toHaveLength(8); // not cut off at the four a result grid shows
    expect(sources.map((b) => b.querySelector(".source-number")?.textContent)).toEqual(["1", "2", "3", "4", "5", "6", "7", "8"]);
    expect(sources[7].getAttribute("aria-label")).toBe("Open source image 8 of 8");
    fireEvent.click(sources[7]);
    expect(p.onOpenImage).toHaveBeenCalledWith(7);
    expect(document.querySelector(".run-meta")?.textContent).toContain("8 images");
  });
});

// ---------------------------------------------------------------- the one listener
describe("the page's one paste listener (criteria 211, 212, 216, 217)", () => {
  function Harness(props: Partial<PasteHandlers> & { children?: React.ReactNode }) {
    const handlers: PasteHandlers = { tab: "images", ready: true, onTray: vi.fn(), onUpscale: vi.fn(), onWrongTab: vi.fn(), ...props };
    usePicturePaste(handlers);
    return (
      <div>
        <textarea id="prompt" aria-label="prompt" />
        <input id="negative" type="text" aria-label="negative" />
        <button type="button" id="plain">a button</button>
        <button type="button" data-upscale-drop>upscale</button>
        {props.children}
      </div>
    );
  }
  /** Dispatch a paste at `target` the way a browser would, and say whether the page took it (cancelled it). */
  const paste = (target: Element, data: ReturnType<typeof clip>) => {
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", { value: data });
    target.dispatchEvent(event);
    return event.defaultPrevented;
  };
  const el = (selector: string) => document.querySelector(selector) as HTMLElement;

  it("takes a picture pasted in the prompt box once: the page's own listener, and no other, acts on it", () => {
    const onTray = vi.fn();
    render(<Harness onTray={onTray} />);
    const pictures = [png("a.png"), png("b.png")];
    expect(paste(el("#prompt"), clip({ files: pictures, text: "also text" }))).toBe(true);
    expect(onTray).toHaveBeenCalledTimes(1);
    expect(onTray).toHaveBeenCalledWith(pictures);
  });

  it("takes a picture pasted on the bare page and on a button", () => {
    const onTray = vi.fn();
    render(<Harness onTray={onTray} />);
    expect(paste(document.body, clip({ files: [png()] }))).toBe(true);
    expect(paste(el("#plain"), clip({ files: [png()] }))).toBe(true);
    expect(onTray).toHaveBeenCalledTimes(2);
  });

  it("never cancels a paste of text alone, anywhere, so that the browser pastes it", () => {
    const handlers = { onTray: vi.fn(), onUpscale: vi.fn(), onWrongTab: vi.fn() };
    render(<Harness {...handlers} />);
    for (const target of [el("#prompt"), el("#negative"), el("#plain"), document.body]) expect(paste(target, clip({ text: "just words" })), target.tagName).toBe(false);
    expect(handlers.onTray).not.toHaveBeenCalled();
    expect(handlers.onWrongTab).not.toHaveBeenCalled();
  });

  it("leaves a mixed clipboard's text to another text field, and takes a picture-only clipboard there", () => {
    const onTray = vi.fn();
    render(<Harness onTray={onTray} />);
    expect(paste(el("#negative"), clip({ files: [png()], text: "A1\tB1" }))).toBe(false);
    expect(onTray).not.toHaveBeenCalled();
    expect(paste(el("#negative"), clip({ files: [png()] }))).toBe(true);
    expect(onTray).toHaveBeenCalledTimes(1);
  });

  it("sends a paste to Upscale a picture… when that has the focus, and not to the tray", () => {
    const handlers = { onTray: vi.fn(), onUpscale: vi.fn(), onWrongTab: vi.fn() };
    render(<Harness {...handlers} />);
    const upscale = el("[data-upscale-drop]");
    expect(paste(upscale, clip({ files: [png("shot.png")] }))).toBe(true);
    expect(handlers.onUpscale).toHaveBeenCalledTimes(1);
    expect(handlers.onTray).not.toHaveBeenCalled();
  });

  it("takes nothing while a dialog is open", () => {
    const onTray = vi.fn();
    render(<Harness onTray={onTray}><div role="dialog" aria-label="Options" /></Harness>);
    expect(paste(el("#prompt"), clip({ files: [png()] }))).toBe(false);
    expect(paste(document.body, clip({ files: [png()] }))).toBe(false);
    expect(onTray).not.toHaveBeenCalled();
    cleanup();
    render(<Harness onTray={onTray}><div role="alertdialog" aria-label="Delete this run?" /></Harness>);
    expect(paste(document.body, clip({ files: [png()] }))).toBe(false);
    expect(onTray).not.toHaveBeenCalled();
  });

  it("says where pictures are used when one is pasted or dropped on the Music or Vector tab, and takes nothing", () => {
    for (const tab of ["music", "vector"] as const) {
      const handlers = { onTray: vi.fn(), onUpscale: vi.fn(), onWrongTab: vi.fn() };
      const { unmount } = render(<Harness tab={tab} {...handlers} />);
      expect(paste(document.body, clip({ files: [png()] }))).toBe(true);
      expect(handlers.onWrongTab).toHaveBeenCalledTimes(1);
      const drop = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(drop, "dataTransfer", { value: { types: ["Files"] } });
      window.dispatchEvent(drop);
      expect(handlers.onWrongTab).toHaveBeenCalledTimes(2);
      const textDrop = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(textDrop, "dataTransfer", { value: { types: ["text/plain"] } });
      window.dispatchEvent(textDrop);
      expect(handlers.onWrongTab).toHaveBeenCalledTimes(2); // a dragged selection is not a picture
      expect(handlers.onTray).not.toHaveBeenCalled();
      unmount();
    }
    expect(PICTURES_ON_IMAGES_TAB).toBe("Pictures are used on the Images tab.");
  });

  it("says nothing about a drop on the Music or Vector tab while a dialog is open, or before the page is ready", () => {
    const drop = () => {
      const event = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "dataTransfer", { value: { types: ["Files"] } });
      window.dispatchEvent(event);
    };
    const onWrongTab = vi.fn();
    render(<Harness tab="music" onWrongTab={onWrongTab}><div role="dialog" aria-label="Options" /></Harness>);
    drop();
    expect(onWrongTab).not.toHaveBeenCalled(); // a dialog has the focus: nothing is said behind it
    cleanup();
    render(<Harness tab="music" ready={false} onWrongTab={onWrongTab} />);
    drop();
    expect(onWrongTab).not.toHaveBeenCalled(); // the page does not yet know what it can do
    cleanup();
    render(<Harness tab="music" onWrongTab={onWrongTab} />);
    drop();
    expect(onWrongTab).toHaveBeenCalledTimes(1); // and the same drop, with neither, is told
  });

  it("does not say it for a drop on the Images tab, where pictures are used", () => {
    const onWrongTab = vi.fn();
    render(<Harness onWrongTab={onWrongTab} />);
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", { value: { types: ["Files"] } });
    window.dispatchEvent(drop);
    expect(onWrongTab).not.toHaveBeenCalled();
  });

  it("acts on the page as it is now: a tab change after the listener was added is seen, and one listener is never added twice", () => {
    const add = vi.spyOn(document, "addEventListener");
    const onTray = vi.fn();
    const onWrongTab = vi.fn();
    function Switcher() {
      const [tab, setTab] = useState<"images" | "music">("images");
      return (<><button type="button" id="go" onClick={() => setTab("music")}>go</button><Harness tab={tab} onTray={onTray} onWrongTab={onWrongTab} /></>);
    }
    render(<Switcher />);
    paste(document.body, clip({ files: [png()] }));
    fireEvent.click(el("#go"));
    paste(document.body, clip({ files: [png()] }));
    expect(onTray).toHaveBeenCalledTimes(1);
    expect(onWrongTab).toHaveBeenCalledTimes(1);
    expect(add.mock.calls.filter(([type]) => type === "paste")).toHaveLength(1);
    add.mockRestore();
  });

  it("is removed when the page goes away", () => {
    const onTray = vi.fn();
    const { unmount } = render(<Harness onTray={onTray} />);
    unmount();
    paste(document.body, clip({ files: [png()] }));
    expect(onTray).not.toHaveBeenCalled();
  });

  it("leaves a paste that something else has already taken", () => {
    const onTray = vi.fn();
    render(<Harness onTray={onTray} />);
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", { value: clip({ files: [png()] }) });
    document.body.addEventListener("paste", (e) => e.preventDefault(), { once: true, capture: true });
    document.body.dispatchEvent(event);
    expect(onTray).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------- the prompt card
describe("the prompt card (criteria 213, 219)", () => {
  function barProps(over: Partial<ComponentProps<typeof PromptBar>> = {}, caps: Capabilities = CAPS): ComponentProps<typeof PromptBar> {
    return {
      caps, options: defaultOptions(caps), prompt: "a lighthouse", submitting: false, problem: null, tray: <div data-testid="tray" />, blockReason: null, starterAvailable: false,
      onFiles: vi.fn(), onStarter: vi.fn(), onPromptFocus: vi.fn(), onPrompt: vi.fn(), onMode: vi.fn(), onScale: vi.fn(), onOpenOptions: vi.fn(), onSubmit: vi.fn(), onDraft: vi.fn(), ...over,
    };
  }
  const editing = (caps: Capabilities = CAPS) => ({ ...defaultOptions(caps), mode: "edit" as const });

  it("says in Generate's help line that pictures can be pasted or dropped, and that the card switches to Edit", () => {
    render(<PromptBar {...barProps()} />);
    expect(document.querySelector("#prompt-help")?.textContent).toContain("Paste or drop pictures to work from them: the card switches to Edit.");
    expect(document.querySelector("[data-generate-pictures-help]")?.textContent?.trim()).toBe(GENERATE_PICTURES_HELP);
  });

  it("does not say it in Edit, where the tray is on screen, nor when the pipeline cannot edit", () => {
    render(<PromptBar {...barProps({ options: editing() })} />);
    expect(document.querySelector("[data-generate-pictures-help]")).toBeNull();
    cleanup();
    const noEdit: Capabilities = { ...CAPS, modes: CAPS.modes.filter((mode) => mode !== "edit") };
    render(<PromptBar {...barProps({}, noEdit)} />);
    expect(document.querySelector("[data-generate-pictures-help]")).toBeNull();
  });

  it("hands pictures dropped on the card to the page in either mode, and lights up while one is over it", () => {
    for (const options of [defaultOptions(CAPS), editing()]) {
      const props = barProps({ options });
      render(<PromptBar {...props} />);
      const card = screen.getByRole("region", { name: "New image" });
      const file = png("dropped.png");
      fireEvent.dragOver(card, { dataTransfer: { types: ["Files"] } });
      expect(card.className).toContain("dropping");
      fireEvent.drop(card, { dataTransfer: { types: ["Files"], files: [file] } });
      expect(card.className).not.toContain("dropping");
      expect(props.onFiles).toHaveBeenCalledWith([file]);
      cleanup();
    }
  });

  it("has no paste handler of its own: the page's listener is the only one, so a picture pasted in the prompt box counts once", () => {
    const props = barProps();
    render(<PromptBar {...props} />);
    const box = screen.getByLabelText("Prompt");
    fireEvent.paste(box, { clipboardData: { files: [png()], items: [], getData: () => "" } });
    expect(props.onFiles).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------- Upscale a picture…
describe("Upscale a picture… (criterion 218)", () => {
  const button = () => screen.getByRole("button", { name: /Upscale a picture/ });

  it("says in its tooltip that a picture can also be dropped or pasted on it", () => {
    render(<UpscalePicture busy={false} onFiles={vi.fn()} />);
    expect(button().getAttribute("title")).toBe(UPSCALE_TITLE);
    expect(button().getAttribute("title")).toContain("or drop or paste a picture here.");
    expect(button().hasAttribute("data-upscale-drop")).toBe(true); // the mark the page's listener looks for
    expect(screen.getByTestId("upscale-file-input").getAttribute("aria-hidden")).toBe("true"); // the hidden file input is not read out
  });

  it("takes a chosen file as a list of one", () => {
    const onFiles = vi.fn();
    render(<UpscalePicture busy={false} onFiles={onFiles} />);
    const file = png("chosen.png");
    fireEvent.change(screen.getByTestId("upscale-file-input"), { target: { files: [file] } });
    expect(onFiles).toHaveBeenCalledWith([file]);
  });

  it("takes dropped pictures, all of them (the page uses the first), and lights up only while a picture is over it", () => {
    const onFiles = vi.fn();
    render(<UpscalePicture busy={false} onFiles={onFiles} />);
    fireEvent.dragOver(button(), { dataTransfer: { types: ["text/plain"] } });
    expect(button().className).not.toContain("dropping"); // a dragged selection is not a picture
    fireEvent.dragOver(button(), { dataTransfer: { types: ["Files"] } });
    expect(button().className).toContain("dropping");
    fireEvent.dragLeave(button());
    expect(button().className).not.toContain("dropping");
    fireEvent.dragOver(button(), { dataTransfer: { types: ["Files"] } });
    const files = [png("one.png"), png("two.png")];
    fireEvent.drop(button(), { dataTransfer: { types: ["Files"], files } });
    expect(onFiles).toHaveBeenCalledWith(files);
    expect(button().className).not.toContain("dropping");
  });

  it("keeps a drop to itself: the prompt card and the page do not also take it", () => {
    const outer = vi.fn();
    render(<div onDrop={outer}><UpscalePicture busy={false} onFiles={vi.fn()} /></div>);
    fireEvent.drop(button(), { dataTransfer: { types: ["Files"], files: [png()] } });
    expect(outer).not.toHaveBeenCalled();
  });

  it("is focused by a click, so that a paste lands on it (a browser that does not focus a button when it is clicked), and opens the chooser", () => {
    render(<UpscalePicture busy={false} onFiles={vi.fn()} />);
    const chooser = vi.spyOn(screen.getByTestId("upscale-file-input") as HTMLInputElement, "click");
    fireEvent.click(button());
    expect(document.activeElement).toBe(button());
    expect(chooser).toHaveBeenCalledTimes(1);
  });

  it("does not open the chooser while it is busy, and says it is upscaling", () => {
    render(<UpscalePicture busy onFiles={vi.fn()} />);
    const working = document.querySelector('[data-action="upscale-picture"]') as HTMLElement; // (its name is "Upscaling…" now)
    const chooser = vi.spyOn(screen.getByTestId("upscale-file-input") as HTMLInputElement, "click");
    fireEvent.click(working);
    expect(chooser).not.toHaveBeenCalled();
    expect(working.getAttribute("aria-disabled")).toBe("true");
    expect(working.textContent).toContain("Upscaling…");
  });
});
