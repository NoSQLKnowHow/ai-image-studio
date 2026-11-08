import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";
import type { Using } from "./components/BuilderPanel";
import { defaultMusicForm, saveMusicForm } from "./music";
import { CUSTOM, defaultOptions, type KeyValueStore, type Options } from "./options";
import { CAPS, makeRun } from "./testdata";
import type { TrayItem } from "./tray";
import { usePromptUse, type FromPrompt } from "./usePromptUse";
import { defaultVectorForm, saveVectorForm } from "./vector";

// Using a prompt in the other tabs, from the Prompt builder (DESIGN.md §37.2; criteria 235, 236, 239, 242, 243): what Use in, Generate now and Quick try set, send and leave alone.

const FROM: FromPrompt = { promptId: "p1", version: 3, title: "Lighthouse" };
const using = (over: Omit<Partial<Using>, "draft"> & { draft?: Partial<Using["draft"]> } = {}): Using => ({
  kind: "images", from: FROM, off: new Set(), ...over,
  draft: { prompt: "a lighthouse in a storm", negative: null, settings: {}, note: "", ...over.draft },
});
const memoryStore = (): KeyValueStore & { data: Record<string, string> } => {
  const data: Record<string, string> = {};
  return { available: true, data, get: (k) => data[k] ?? null, set: (k, v) => { data[k] = v; } };
};
const readyItem = (n: number): TrayItem => ({
  key: `k${n}`, name: `pic${n}.png`, state: "ready", progress: 1, ref: { upload_id: `u${n}` }, thumbUrl: null, previewUrl: null, width: 10, height: 10, hasAlpha: false, missing: false,
});

function setup(over: { options?: Options; prompt?: string; from?: FromPrompt | null; items?: TrayItem[]; store?: ReturnType<typeof memoryStore> } = {}) {
  const calls = { prompt: vi.fn(), options: vi.fn(), from: vi.fn(), problem: vi.fn(), push: vi.fn(), tab: vi.fn(), upsert: vi.fn(), focus: vi.fn(), adopt: vi.fn(), addFiles: vi.fn() };
  const store = over.store ?? memoryStore();
  const options = over.options ?? defaultOptions(CAPS);
  const hook = renderHook(() => usePromptUse({
    from: over.from ?? null, setFrom: calls.from, caps: CAPS, options, setOptions: calls.options, prompt: over.prompt ?? "my own words", setPrompt: calls.prompt, clearProblem: calls.problem,
    tray: { items: over.items ?? [], shapeKey: null, adoptRun: calls.adopt, addFiles: calls.addFiles }, store, push: calls.push, showTab: calls.tab, upsert: calls.upsert, focusPrompt: calls.focus,
  }));
  return { ...calls, store, startOptions: options, hook };
}
const toast = (push: ReturnType<typeof vi.fn>) => push.mock.calls[push.mock.calls.length - 1] as [string, string, { label: string; run: () => void } | undefined];
const said = (push: ReturnType<typeof vi.fn>) => toast(push).slice(0, 2);

/** The hook as the page has it before the studio has answered (no capabilities or no options yet), or with a pipeline that cannot edit. */
function bare(over: { caps?: typeof CAPS | null; options?: Options | null; items?: TrayItem[] } = {}) {
  const calls = { prompt: vi.fn(), options: vi.fn(), push: vi.fn(), tab: vi.fn(), upsert: vi.fn(), addFiles: vi.fn() };
  const caps = "caps" in over ? over.caps ?? null : CAPS;
  const options = "options" in over ? over.options ?? null : defaultOptions(CAPS);
  const hook = renderHook(() => usePromptUse({
    from: null, setFrom: vi.fn(), caps, options, setOptions: calls.options, prompt: "", setPrompt: calls.prompt, clearProblem: vi.fn(),
    tray: { items: over.items ?? [], shapeKey: null, adoptRun: vi.fn(), addFiles: calls.addFiles }, store: memoryStore(), push: calls.push, showTab: calls.tab, upsert: calls.upsert, focusPrompt: vi.fn(),
  }));
  return { ...calls, hook };
}

let create: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  create = vi.spyOn(api, "createRun").mockResolvedValue(makeRun({ id: "made" }));
  vi.spyOn(api, "createVectorRun").mockResolvedValue(makeRun({ id: "vmade" }) as never);
  vi.spyOn(api, "createMusicRun").mockResolvedValue(makeRun({ id: "mmade" }) as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Use in Images (criterion 235)", () => {
  it("fills the prompt box, sets only the settings left on, goes to Images, and says what it set", () => {
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ draft: { settings: { shape: "3:2", guidance: 5 }, negative: "blur" } })));
    expect(s.prompt).toHaveBeenCalledWith("a lighthouse in a storm");
    const next = s.options.mock.calls[0][0] as Options;
    expect(next).toMatchObject({ mode: "generate", aspect: "3:2", guidance: 5, negativePrompt: "blur" });
    expect(s.from).toHaveBeenCalledWith(FROM);
    expect(s.tab).toHaveBeenCalledWith("images");
    expect(s.problem).toHaveBeenCalled();
    expect(s.focus).toHaveBeenCalled();
    expect(toast(s.push)[0]).toBe("success");
    expect(toast(s.push)[1]).toBe("Filled the prompt, set 3:2, guidance 5 and a negative prompt.");
    expect(toast(s.push)[2]?.label).toBe("Undo");
    expect(create).not.toHaveBeenCalled(); // using a prompt never queues a run (rule 11)
  });

  it("leaves out the settings that were switched off", () => {
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ off: new Set(["shape", "negative"] as const), draft: { settings: { shape: "3:2", guidance: 5 }, negative: "blur" } })));
    const next = s.options.mock.calls[0][0] as Options;
    expect(next.aspect).toBe(s.startOptions.aspect);
    expect(next.negativePrompt).toBe("");
    expect(next.guidance).toBe(5);
    expect(toast(s.push)[1]).toBe("Filled the prompt, set guidance 5.");
  });

  it("puts the form back with Undo: the words, the options and the chip it had", () => {
    const before = { ...defaultOptions(CAPS), steps: 12, negativePrompt: "mine" };
    const earlier: FromPrompt = { promptId: "p0", version: 1, title: "Earlier" };
    const s = setup({ options: before, prompt: "typed by hand", from: earlier });
    act(() => s.hook.result.current.useIn(using({ draft: { settings: { shape: "1:1" } } })));
    s.prompt.mockClear(); s.options.mockClear(); s.from.mockClear();
    act(() => toast(s.push)[2]!.run());
    expect(s.prompt).toHaveBeenCalledWith("typed by hand");
    expect(s.options).toHaveBeenCalledWith(before);
    expect(s.from).toHaveBeenCalledWith(earlier);
  });

  it("has no chip when the words are not from a saved prompt", () => {
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ from: null })));
    expect(s.from).toHaveBeenCalledWith(null);
  });

  it("for Edit, switches the form to Edit and sets the Edit size", () => {
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ kind: "edit", draft: { settings: { shape: "auto" } } })));
    expect(s.options.mock.calls[0][0]).toMatchObject({ mode: "edit", editAspect: "auto" });
    expect(toast(s.push)[1]).toBe("Filled the prompt, set Size on Auto.");
  });

  it("says Edit is not there, and changes nothing, when the pipeline cannot edit", () => {
    const hook = renderHook(() => usePromptUse({
      from: null, setFrom: vi.fn(), caps: { ...CAPS, modes: ["generate"] }, options: defaultOptions(CAPS), setOptions: vi.fn(), prompt: "", setPrompt: vi.fn(), clearProblem: vi.fn(),
      tray: { items: [], shapeKey: null, adoptRun: vi.fn(), addFiles: vi.fn() }, store: memoryStore(), push: vi.fn(), showTab: vi.fn(), upsert: vi.fn(), focusPrompt: vi.fn(),
    }));
    const push = vi.fn();
    const calls = { setPrompt: vi.fn(), showTab: vi.fn() };
    const again = renderHook(() => usePromptUse({
      from: null, setFrom: vi.fn(), caps: { ...CAPS, modes: ["generate"] }, options: defaultOptions(CAPS), setOptions: vi.fn(), prompt: "", setPrompt: calls.setPrompt, clearProblem: vi.fn(),
      tray: { items: [], shapeKey: null, adoptRun: vi.fn(), addFiles: vi.fn() }, store: memoryStore(), push, showTab: calls.showTab, upsert: vi.fn(), focusPrompt: vi.fn(),
    }));
    hook.unmount();
    act(() => again.result.current.useIn(using({ kind: "edit" })));
    expect(push).toHaveBeenCalledWith("error", "Edit isn't available: the loaded pipeline can't edit images.");
    expect(calls.setPrompt).not.toHaveBeenCalled();
    expect(calls.showTab).not.toHaveBeenCalled();
  });
});

describe("Use in Edit puts the chat's pictures in the tray (criterion 243)", () => {
  const pictures = [{ url: "/api/builder/attachments/a1", name: "dog.png" }, { url: "/api/builder/attachments/a2", name: null }];
  const stubFetch = (ok = true) => vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok, status: ok ? 200 : 404, blob: async () => new Blob([url], { type: "image/png" }) })));
  afterEach(() => vi.unstubAllGlobals());

  it("in the chat's order, into an empty tray, and says so", async () => {
    stubFetch();
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ kind: "edit", pictures, draft: { prompt: "put the dog from image 1 into the scene from image 2" } })));
    await waitFor(() => expect(s.addFiles).toHaveBeenCalledTimes(1));
    const files = s.addFiles.mock.calls[0][0] as File[];
    expect(files.map((f) => f.name)).toEqual(["dog.png", "picture 2"]);
    expect(files.map((f) => f.type)).toEqual(["image/png", "image/png"]);
    await waitFor(() => expect(toast(s.push)[1]).toBe("Put the chat's 2 pictures in the tray, in order."));
    expect(s.tab).toHaveBeenCalledWith("images");
    expect(s.options.mock.calls[0][0]).toMatchObject({ mode: "edit" });
  });

  it("gives a picture the server sent without a type the type of a picture, and says it in the singular for one", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, blob: async () => new Blob(["x"]) })));
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ kind: "edit", pictures: [{ url: "/api/builder/attachments/a1", name: null }] })));
    await waitFor(() => expect(s.addFiles).toHaveBeenCalledTimes(1));
    const [file] = s.addFiles.mock.calls[0][0] as File[];
    expect(file.type).toBe("image/png");
    expect(file.name).toBe("picture 1");
    await waitFor(() => expect(toast(s.push)[1]).toBe("Put the chat's picture in the tray, in order."));
  });

  it("leaves a tray that has pictures in it alone, and says why", () => {
    stubFetch();
    const s = setup({ items: [readyItem(1)] });
    act(() => s.hook.result.current.useIn(using({ kind: "edit", pictures })));
    expect(s.addFiles).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    expect(said(s.push)[1]).toMatch(/^The tray already has a picture, so it was left as it is\./);
    cleanup();
  });

  it("says when the pictures could not be fetched, and puts nothing in the tray", async () => {
    stubFetch(false);
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ kind: "edit", pictures })));
    await waitFor(() => expect(toast(s.push)[0]).toBe("error"));
    expect(toast(s.push)[1]).toBe("The chat's pictures could not be put in the tray. Add them by hand.");
    expect(s.addFiles).not.toHaveBeenCalled();
  });

  it("does nothing about a tray for an Edit prompt with no pictures, or for any other kind", () => {
    stubFetch();
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ kind: "edit" })));
    act(() => s.hook.result.current.useIn(using({ kind: "images", pictures })));
    expect(fetch).not.toHaveBeenCalled();
    expect(s.addFiles).not.toHaveBeenCalled();
  });
});

describe("Use in Vector and Music (criterion 242)", () => {
  it("asks the Vector panel to fill itself, afresh each time, and goes to Vector", () => {
    const s = setup();
    const u = using({ kind: "vector" });
    act(() => s.hook.result.current.useIn(u));
    expect(s.hook.result.current.vectorFill).toEqual({ seq: 1, using: u });
    expect(s.tab).toHaveBeenLastCalledWith("vector");
    act(() => s.hook.result.current.useIn(u));
    expect(s.hook.result.current.vectorFill?.seq).toBe(2);
    expect(s.hook.result.current.musicFill).toBeNull();
    expect(s.prompt).not.toHaveBeenCalled(); // the Images box is not touched
  });

  it("asks the Music panel the same", () => {
    const s = setup();
    act(() => s.hook.result.current.useIn(using({ kind: "music" })));
    expect(s.hook.result.current.musicFill?.seq).toBe(1);
    expect(s.tab).toHaveBeenLastCalledWith("music");
    expect(s.hook.result.current.vectorFill).toBeNull();
  });
});

describe("Generate now (criterion 236)", () => {
  it("queues a run from the prompt with the form's options and the prompt's settings over them, and carries the prompt it is from", async () => {
    const s = setup({ options: { ...defaultOptions(CAPS), steps: 12, numImages: 2 } });
    await act(() => s.hook.result.current.generate(using({ draft: { settings: { shape: "1:1", guidance: 5 }, negative: "blur" } }), false));
    expect(create).toHaveBeenCalledTimes(1);
    const body = create.mock.calls[0][0] as { prompt: string; prompt_ref: unknown; options: Record<string, unknown> };
    expect(body.prompt).toBe("a lighthouse in a storm");
    expect(body.prompt_ref).toEqual({ prompt_id: "p1", version: 3 });
    const [w, h] = CAPS.aspect_ratios["1:1"];
    expect(body.options).toMatchObject({ steps: 12, num_images: 2, cfg_scale: 5, negative_prompt: "blur", width: w, height: h });
    expect(body.options.draft).toBeUndefined();
    expect(s.upsert).toHaveBeenCalledTimes(1);
    expect(s.tab).toHaveBeenCalledWith("images");
    expect(toast(s.push)[1]).toBe("Queued a run from “Lighthouse” v3. Your Images form is unchanged.");
    // the form is untouched
    expect(s.prompt).not.toHaveBeenCalled();
    expect(s.options).not.toHaveBeenCalled();
    expect(s.from).not.toHaveBeenCalled();
  });

  it("carries no reference for words that are not a saved prompt", async () => {
    const s = setup();
    await act(() => s.hook.result.current.generate(using({ from: null }), false));
    expect(create.mock.calls[0][0]).not.toHaveProperty("prompt_ref");
    expect(toast(s.push)[1]).toBe("Queued a run. Your Images form is unchanged.");
  });

  it("will not queue an empty prompt, or one the options cannot take", async () => {
    const s = setup();
    await act(() => s.hook.result.current.generate(using({ draft: { prompt: "   " } }), false));
    expect(said(s.push)).toEqual(["error", "There is no prompt to make a picture from yet."]);
    const bad = setup({ options: { ...defaultOptions(CAPS), aspect: CUSTOM, customWidth: 33, customHeight: 33 } });
    await act(() => bad.hook.result.current.generate(using(), false));
    expect(toast(bad.push)[0]).toBe("error");
    expect(toast(bad.push)[1]).toMatch(/^Check the options on the Images tab: /);
    expect(create).not.toHaveBeenCalled();
  });

  it("says what the server said when it refuses, and leaves the form alone", async () => {
    create.mockRejectedValueOnce(new ApiError("That prompt is in Deleted.", 409, "prompt_in_bin"));
    const s = setup();
    await act(() => s.hook.result.current.generate(using(), false));
    expect(said(s.push)).toEqual(["error", "Couldn't start the run: That prompt is in Deleted."]);
    expect(s.upsert).not.toHaveBeenCalled();
    expect(s.tab).not.toHaveBeenCalled();
  });

  it("for Edit sends the tray's pictures in order, and the tray keeps them for the next try", async () => {
    const s = setup({ items: [readyItem(1), readyItem(2)] });
    await act(() => s.hook.result.current.generate(using({ kind: "edit", draft: { prompt: "put the dog from image 1 into image 2" } }), false));
    const body = create.mock.calls[0][0] as { mode: string; input_images: unknown[]; prompt_ref: unknown };
    expect(body.mode).toBe("edit");
    expect(body.input_images).toHaveLength(2);
    expect(body.prompt_ref).toEqual({ prompt_id: "p1", version: 3 });
    expect(s.adopt).toHaveBeenCalledTimes(1);
  });

  it("for Edit with no pictures ready, says why and sends nothing", async () => {
    const s = setup({ items: [] });
    await act(() => s.hook.result.current.generate(using({ kind: "edit" }), false));
    expect(create).not.toHaveBeenCalled();
    expect(toast(s.push)[0]).toBe("error");
  });
});

describe("before the studio has answered, and when it cannot edit", () => {
  it("does nothing, and says nothing, until the page has its capabilities and its options", async () => {
    for (const missing of [{ caps: null }, { options: null }] as const) {
      const s = bare(missing);
      act(() => s.hook.result.current.useIn(using()));
      act(() => s.hook.result.current.useIn(using({ kind: "vector" })));
      await act(() => s.hook.result.current.generate(using(), false));
      await act(() => s.hook.result.current.generate(using({ kind: "music" }), true));
      expect(create).not.toHaveBeenCalled();
      expect(s.prompt).not.toHaveBeenCalled();
      expect(s.options).not.toHaveBeenCalled();
      expect(s.tab).not.toHaveBeenCalled();
      expect(s.push).not.toHaveBeenCalled();
      expect(s.hook.result.current.vectorFill).toBeNull();
      cleanup();
    }
  });

  it("will not queue an edit when the pipeline cannot edit", async () => {
    const s = bare({ caps: { ...CAPS, modes: ["generate"] } });
    await act(() => s.hook.result.current.generate(using({ kind: "edit" }), false));
    expect(s.push).toHaveBeenCalledWith("error", "Edit isn't available: the loaded pipeline can't edit images.");
    expect(create).not.toHaveBeenCalled();
    await act(() => s.hook.result.current.generate(using({ kind: "images" }), false)); // an Images prompt does not need it
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("checks an edit's own size, not the Generate size", async () => {
    const s = bare({ options: { ...defaultOptions(CAPS), mode: "edit", editAspect: CUSTOM, customWidth: 33, customHeight: 33 }, items: [readyItem(1)] });
    await act(() => s.hook.result.current.generate(using({ kind: "edit" }), false));
    expect(s.push.mock.calls.at(-1)?.[1]).toMatch(/^Check the options on the Images tab: /);
    expect(create).not.toHaveBeenCalled();
  });
});

describe("what the server says when it refuses", () => {
  it("is shown as it is when the queue is full, and with what was being done when anything else went wrong", async () => {
    create.mockRejectedValueOnce(new ApiError("The queue is full: 3 runs are waiting.", 429, "queue_full"));
    const s = setup();
    await act(() => s.hook.result.current.generate(using(), false));
    expect(said(s.push)).toEqual(["error", "The queue is full: 3 runs are waiting."]);
    create.mockRejectedValueOnce(new Error("network down"));
    await act(() => s.hook.result.current.generate(using(), false));
    expect(said(s.push)).toEqual(["error", "Couldn't start the run."]);
  });
});

describe("Quick try (criterion 239)", () => {
  it("makes a Draft, small and quick, with one picture, and stays where it is", async () => {
    const s = setup({ options: { ...defaultOptions(CAPS), steps: 50, numImages: 4 } });
    await act(() => s.hook.result.current.generate(using(), true));
    const body = create.mock.calls[0][0] as { prompt_ref: unknown; options: Record<string, number | boolean> };
    expect(body.options).toMatchObject({ draft: true, num_images: 1 });
    expect(body.options.steps).toBeLessThanOrEqual(CAPS.limits.draft.steps);
    expect(Math.max(body.options.width as number, body.options.height as number)).toBeLessThanOrEqual(CAPS.limits.draft.long_side);
    expect(body.prompt_ref).toEqual({ prompt_id: "p1", version: 3 });
    expect(s.tab).not.toHaveBeenCalled(); // the person stays in the builder
    expect(toast(s.push)[1]).toBe("Quick try queued from “Lighthouse” v3. Its picture shows on the version when it is done.");
  });
});

describe("Generate now for Vector and Music (criterion 242)", () => {
  it("queues a Vector run from the saved Vector form with the prompt's idea and settings over it, and does not change the form", async () => {
    const store = memoryStore();
    saveVectorForm({ ...defaultVectorForm(CAPS), idea: "my own idea", numImages: 2 }, store);
    const saved = store.data["studio.vector.v1"];
    const s = setup({ store });
    await act(() => s.hook.result.current.generate(using({ kind: "vector", draft: { prompt: "a fox, flat", settings: { kind: "line", colours: 3 } } }), false));
    const body = (api.createVectorRun as ReturnType<typeof vi.fn>).mock.calls[0][0] as { prompt: string; prompt_ref: unknown; options: Record<string, unknown> };
    expect(body.prompt).toBe("a fox, flat");
    expect(body.options).toMatchObject({ kind: "line", colours: 3, num_images: 2 });
    expect(body.prompt_ref).toEqual({ prompt_id: "p1", version: 3 });
    expect(store.data["studio.vector.v1"]).toBe(saved);
    expect(s.tab).toHaveBeenCalledWith("vector");
    expect(toast(s.push)[1]).toBe("Queued a run from “Lighthouse” v3. Your Vector form is unchanged.");
    expect(create).not.toHaveBeenCalled();
  });

  it("queues a Music run from the saved fields with the prompt's description, lyrics and length over them", async () => {
    const store = memoryStore();
    saveMusicForm({ ...defaultMusicForm(CAPS), tracks: 2 }, store);
    const s = setup({ store });
    await act(() => s.hook.result.current.generate(using({ kind: "music", draft: { prompt: "A slow song about the sea", settings: { fields: { genre: "folk" }, lyrics: "Salt on the wind", seconds: 90 } } }), false));
    const body = (api.createMusicRun as ReturnType<typeof vi.fn>).mock.calls[0][0] as { prompt: string; lyrics: string; prompt_ref: unknown; options: Record<string, unknown> };
    expect(body).toMatchObject({ prompt: "A slow song about the sea", lyrics: "Salt on the wind", prompt_ref: { prompt_id: "p1", version: 3 } });
    expect(body.options).toMatchObject({ duration: 90, tracks: 2, fields: { genre: "folk" } });
    expect(s.tab).toHaveBeenCalledWith("music");
  });

  it("will not queue a Vector or Music run the form's own rules refuse", async () => {
    const s = setup();
    await act(() => s.hook.result.current.generate(using({ kind: "vector", draft: { prompt: "  " } }), false));
    expect(toast(s.push)[1]).toMatch(/^Check the Vector form: /);
    await act(() => s.hook.result.current.generate(using({ kind: "music", draft: { prompt: "" } }), false));
    expect(toast(s.push)[1]).toMatch(/^Check the Music fields: /);
    expect(api.createVectorRun).not.toHaveBeenCalled();
    expect(api.createMusicRun).not.toHaveBeenCalled();
  });
});
