import { afterEach, describe, expect, it, vi } from "vitest";
import {
  IDLE_REPLY, answersFor, canSend, changedIn, chatTitle, chipLine, chooseMode, isAnswer, lengthMeter, newestDraft, nextReplyState, openQuestions, overlayOptions, previousDraft,
  promptRun, quickTryProblem, refLine, replying, settingChips, toggleChip, usedText, wordDiff, type Picks,
} from "./builder";
import { builderApi } from "./builderApi";
import type { AnswerContent, ChatMessage, Draft, Question, ReplyEvent } from "./builderTypes";
import { AUTO, defaultOptions } from "./options";
import { SseParser, readStream } from "./sse";
import { CAPS } from "./testdata";

const q = (id: string, ...chips: string[]): Question => ({ id, text: `Question ${id}?`, chips: chips.length ? chips : ["a", "b", "c"] });
const draft = (prompt: string, extra: Partial<Draft> = {}): Draft => ({ prompt, negative: null, settings: {}, note: "", ...extra });
const answer = (seq: number, content: Partial<AnswerContent>): ChatMessage => ({
  seq, role: "assistant", text: content.reply ?? "", model: "m", created_at: "t", attachments: [],
  content: { reply: "", title: null, questions: [], draft: null, variations: [], remember: [], tags: [], done: false, notices: [], plain: false, ...content },
});
const said = (seq: number, draftValue: Draft | null = null): ChatMessage => ({
  seq, role: "user", text: "words", model: null, created_at: "t", attachments: [], content: { answers: [], intent: "message", draft: draftValue, think: null },
});

// ------------------------------------------------------------------ the stream
describe("the server-sent events parser", () => {
  it("reads events whatever size the pieces arrive in", () => {
    const text = 'event: delta\ndata: {"text":"Hel"}\n\nevent: result\ndata: {"a":1}\n\n';
    for (const size of [1, 2, 5, 17, text.length]) {
      const parser = new SseParser();
      const got = [];
      for (let i = 0; i < text.length; i += size) got.push(...parser.feed(text.slice(i, i + size)));
      expect(got).toEqual([{ name: "delta", data: { text: "Hel" } }, { name: "result", data: { a: 1 } }]);
    }
  });

  it("hands over nothing until an event is complete", () => {
    const parser = new SseParser();
    expect(parser.feed('event: delta\ndata: {"text":"Hel')).toEqual([]);
    expect(parser.feed('lo"}\n')).toEqual([]);
    expect(parser.feed("\n")).toEqual([{ name: "delta", data: { text: "Hello" } }]);
  });

  it("copes with CRLF, comments, a missing name, multi-line data and things that are not JSON", () => {
    const parser = new SseParser();
    expect(parser.feed(': keep-alive\r\n\r\nevent: a\r\ndata: {"x":\r\ndata: 1}\r\n\r\ndata: {"y":2}\n\nevent: b\ndata: nonsense\n\nevent: c\n\n')).toEqual([
      { name: "a", data: { x: 1 } }, { name: "message", data: { y: 2 } },
    ]);
  });

  it("keeps text with unicode and colons in it", () => {
    expect(new SseParser().feed('event: delta\ndata: {"text":"café: 😀 \\"quoted\\""}\n\n')).toEqual([{ name: "delta", data: { text: 'café: 😀 "quoted"' } }]);
  });
});

describe("reading a reply from the server", () => {
  afterEach(() => vi.unstubAllGlobals());

  const streamOf = (...chunks: string[]) => new ReadableStream<Uint8Array>({ start(controller) { chunks.forEach((c) => controller.enqueue(new TextEncoder().encode(c))); controller.close(); } });

  it("hands each event to the caller as it arrives, with the studio's header, and resolves at the end", async () => {
    const fetchMock = vi.fn(async () => new Response(streamOf('event: start\ndata: {"seq":1}\n\nevent: del', 'ta\ndata: {"text":"Hi","thought":false}\n\n'), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const got: ReplyEvent[] = [];
    await builderApi.send("c1", { text: "idea", think: false }, (e) => got.push(e));
    expect(got).toEqual([{ name: "start", seq: 1 }, { name: "delta", text: "Hi", thought: false }]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/builder/chats/c1/messages");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "X-Studio-Client": "1", "Content-Type": "application/json", Accept: "text/event-stream" });
    expect(JSON.parse(init.body as string)).toEqual({ text: "idea", think: false });
  });

  it("turns an error status into the studio's own error, with its words and its code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "A reply is already being written in this chat.", code: "reply_in_progress" }), { status: 409 })));
    await expect(readStream("/x", {}, () => undefined)).rejects.toMatchObject({ status: 409, code: "reply_in_progress", message: "A reply is already being written in this chat." });
  });

  it("says the server can't be reached when the connection fails or drops", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    await expect(readStream("/x", {}, () => undefined)).rejects.toMatchObject({ status: 0 });
    const broken = new ReadableStream<Uint8Array>({ pull(controller) { controller.error(new Error("reset")); } });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(broken, { status: 200 })));
    await expect(readStream("/x", {}, () => undefined)).rejects.toMatchObject({ status: 0 });
  });

  it("lets an abort through as an abort", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new DOMException("aborted", "AbortError"); }));
    await expect(readStream("/x", {}, () => undefined, new AbortController().signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("the calls", () => {
  afterEach(() => vi.unstubAllGlobals());
  const ok = (body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));

  it("name their paths and carry the header on every change but not on a read", async () => {
    const fetchMock = ok({});
    vi.stubGlobal("fetch", fetchMock);
    await builderApi.status();
    await builderApi.listPrompts({ q: "fox", tag: "sea", favourite: true, project: "none", deleted: false, sort: "title" });
    await builderApi.compare("p1", 1, 3);
    await builderApi.makeCurrent("p1", 2);
    await builderApi.patchPreference("f1", { enabled: false });
    await builderApi.deletePreference("f1");
    await builderApi.attachImage("c1", "i1");
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([url, init]) => `${init.method} ${url}`)).toEqual([
      "GET /api/builder/status", "GET /api/builder/prompts?q=fox&tag=sea&favourite=true&project=none&sort=title", "GET /api/builder/prompts/p1/compare?a=1&b=3",
      "POST /api/builder/prompts/p1/versions/2/restore", "PATCH /api/builder/preferences/f1", "DELETE /api/builder/preferences/f1", "POST /api/builder/chats/c1/attachments/from-image",
    ]);
    expect((calls[0][1].headers as Record<string, string>)["X-Studio-Client"]).toBeUndefined();
    expect(calls.slice(3).every(([, init]) => (init.headers as Record<string, string>)["X-Studio-Client"] === "1")).toBe(true);
    expect(JSON.parse(calls[6][1].body as string)).toEqual({ image_id: "i1" });
  });

  it("send a picture as the body itself, with its name", async () => {
    const fetchMock = ok({ id: "a1", url: "/u", width: 1, height: 1, name: "fox.png" });
    vi.stubGlobal("fetch", fetchMock);
    const file = new Blob(["x"], { type: "image/png" });
    await expect(builderApi.attachFile("c1", file, "fox.png")).resolves.toMatchObject({ id: "a1" });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/builder/chats/c1/attachments?name=fox.png");
    expect(init.body).toBe(file);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "That isn't a PNG, JPEG or WebP image.", code: "bad_picture" }), { status: 415 })));
    await expect(builderApi.attachFile("c1", file)).rejects.toMatchObject({ status: 415, message: "That isn't a PNG, JPEG or WebP image." });
  });
});

// ------------------------------------------------------------------ the reply, while it is written
describe("what a reply is doing", () => {
  it("goes from waiting through thinking to writing, and keeps what it has been told", () => {
    let s = IDLE_REPLY;
    expect(replying(s)).toBe(false);
    s = nextReplyState(s, { name: "waiting" });
    expect(s.phase).toBe("waiting");
    s = nextReplyState(s, { name: "busy", images: true });
    s = nextReplyState(s, { name: "start", seq: 1 });
    s = nextReplyState(s, { name: "context", preferences: 2, examples: [{ id: "p", title: "Fox" }], dropped: 1 });
    s = nextReplyState(s, { name: "thinking", text: "Hmm. ", seconds: 1.5 });
    s = nextReplyState(s, { name: "thinking", text: "Right.", seconds: 3 });
    expect(s).toMatchObject({ phase: "thinking", thinking: "Hmm. Right.", seconds: 3, busyImages: true, context: { preferences: 2, examples: [{ id: "p", title: "Fox" }], dropped: 1 } });
    s = nextReplyState(s, { name: "delta", text: "Lovely", thought: true });
    s = nextReplyState(s, { name: "delta", text: "Lovely idea.", thought: true });
    expect(s).toMatchObject({ phase: "writing", text: "Lovely idea." });
    expect(replying(s)).toBe(true);
  });

  it("starts the reply over when it is asked again", () => {
    const s = nextReplyState({ ...IDLE_REPLY, phase: "thinking", thinking: "x", text: "partial" }, { name: "retry" });
    expect(s).toMatchObject({ phase: "retrying", text: "", thinking: "" });
  });

  it("leaves the end of a reply to the caller", () => {
    const s = { ...IDLE_REPLY, phase: "writing" as const, text: "x" };
    expect(nextReplyState(s, { name: "error", code: "c", message: "m", hint: null, status: 502 })).toBe(s);
  });
});

// ------------------------------------------------------------------ choosing chips (criterion 224, 225)
describe("choosing chips", () => {
  it("selects a chip, takes it back when tapped again, and allows several", () => {
    let picks: Picks = {};
    picks = toggleChip(picks, "q1", "Dusk");
    picks = toggleChip(picks, "q1", "Dawn");
    expect(picks).toEqual({ q1: { mode: "chips", chips: ["Dusk", "Dawn"] } });
    picks = toggleChip(picks, "q1", "Dusk");
    expect(picks).toEqual({ q1: { mode: "chips", chips: ["Dawn"] } });
    expect(toggleChip(picks, "q1", "Dawn")).toEqual({});
  });

  it("keeps the questions apart", () => {
    const picks = toggleChip(toggleChip({}, "q1", "a"), "q2", "b");
    expect(picks).toEqual({ q1: { mode: "chips", chips: ["a"] }, q2: { mode: "chips", chips: ["b"] } });
    expect(toggleChip(picks, "q1", "a")).toEqual({ q2: { mode: "chips", chips: ["b"] } });
  });

  it("makes Surprise me and Skip replace the chips, and a second press undo them", () => {
    let picks = toggleChip({}, "q1", "Dusk");
    picks = chooseMode(picks, "q1", "surprise");
    expect(picks).toEqual({ q1: { mode: "surprise", chips: [] } });
    expect(chooseMode(picks, "q1", "skip")).toEqual({ q1: { mode: "skip", chips: [] } });
    expect(chooseMode(picks, "q1", "surprise")).toEqual({});
    expect(toggleChip(picks, "q1", "Dawn")).toEqual({ q1: { mode: "chips", chips: ["Dawn"] } }); // a chip takes back Surprise me
  });

  it("does not change what it was given", () => {
    const picks: Picks = { q1: { mode: "chips", chips: ["a"] } };
    const frozen = JSON.stringify(picks);
    toggleChip(picks, "q1", "b");
    chooseMode(picks, "q1", "skip");
    expect(JSON.stringify(picks)).toBe(frozen);
  });

  it("sends an answer for each question chosen for, in the order asked", () => {
    const questions = [q("q1"), q("q2"), q("q3")];
    const picks: Picks = { q3: { mode: "skip", chips: [] }, q1: { mode: "chips", chips: ["a", "b"] } };
    expect(answersFor(picks, questions)).toEqual([{ id: "q1", chips: ["a", "b"], mode: "chips" }, { id: "q3", mode: "skip" }]);
    expect(answersFor({ zz: { mode: "chips", chips: ["a"] } }, questions)).toEqual([]);
    expect(answersFor({}, questions)).toEqual([]);
  });

  it("can send with words, a chosen answer or a waiting picture, and never while a reply is being written", () => {
    const none = { text: "  ", picks: {}, pictures: 0, busy: false };
    expect(canSend(none)).toBe(false);
    expect(canSend({ ...none, text: "idea" })).toBe(true);
    expect(canSend({ ...none, picks: { q1: { mode: "skip", chips: [] } } })).toBe(true);
    expect(canSend({ ...none, pictures: 1 })).toBe(true);
    expect(canSend({ ...none, text: "idea", busy: true })).toBe(false);
  });
});

// ------------------------------------------------------------------ reading a chat
describe("reading a chat", () => {
  it("asks only the questions of the last message when it is the model's", () => {
    const asked = answer(2, { questions: [q("q1")] });
    expect(openQuestions([said(1), asked]).map((x) => x.id)).toEqual(["q1"]);
    expect(openQuestions([said(1), asked, said(3)])).toEqual([]); // answered by writing again
    expect(openQuestions([])).toEqual([]);
    expect(openQuestions([said(1), answer(2, { questions: [q("q1")], stopped: true })])).toEqual([]); // a stopped reply asks nothing
  });

  it("knows an answer from the person's own message", () => {
    expect(isAnswer(answer(1, {}))).toBe(true);
    expect(isAnswer(said(1))).toBe(false);
    expect(isAnswer({ ...answer(1, {}), content: null })).toBe(false);
  });

  it("finds the newest draft, whoever wrote it, and none before there is one", () => {
    expect(newestDraft([])).toBeNull();
    expect(newestDraft([said(1), answer(2, { draft: null })])).toBeNull();
    expect(newestDraft([said(1), answer(2, { draft: draft("model one") })])?.prompt).toBe("model one");
    expect(newestDraft([said(1), answer(2, { draft: draft("model one") }), said(3, draft("hand edited"))])?.prompt).toBe("hand edited");
    expect(newestDraft([answer(2, { draft: draft("kept") }), answer(4, { draft: null, plain: true })])?.prompt).toBe("kept"); // a plain reply changes none
    expect(newestDraft([answer(2, { draft: draft("   ") })])).toBeNull();
  });

  it("finds the draft before the last reply, to show what that reply changed", () => {
    expect(previousDraft([answer(2, { draft: draft("only") })])).toBeNull();
    expect(previousDraft([answer(2, { draft: draft("first") }), said(3), answer(4, { draft: draft("second") })])?.prompt).toBe("first");
    expect(previousDraft([answer(2, { draft: draft("a") }), answer(4, { draft: draft("b") }), answer(6, { draft: draft("c") })])?.prompt).toBe("b");
  });

  it("titles a chat by its name", () => {
    expect(chatTitle("  A fox ")).toBe("A fox");
    expect(chatTitle("   ")).toBe("New chat");
  });
});

// ------------------------------------------------------------------ the words that changed (criterion 226)
describe("the words that changed", () => {
  it("marks removed and added words in reading order", () => {
    expect(wordDiff("a lighthouse at dusk", "a lighthouse at dawn in fog")).toEqual([{ op: "same", text: "a lighthouse at" }, { op: "del", text: "dusk" }, { op: "add", text: "dawn in fog" }]);
    expect(wordDiff("one two three", "one three")).toEqual([{ op: "same", text: "one" }, { op: "del", text: "two" }, { op: "same", text: "three" }]);
    expect(wordDiff("one three", "one two three")).toEqual([{ op: "same", text: "one" }, { op: "add", text: "two" }, { op: "same", text: "three" }]);
  });

  it("treats white space as no change and handles nothing", () => {
    expect(wordDiff("same  words", "same\nwords")).toEqual([{ op: "same", text: "same words" }]);
    expect(wordDiff("", "")).toEqual([]);
    expect(wordDiff(null, undefined)).toEqual([]);
    expect(wordDiff(null, "new")).toEqual([{ op: "add", text: "new" }]);
    expect(wordDiff("old", "")).toEqual([{ op: "del", text: "old" }]);
    expect(changedIn(wordDiff("a", "a"))).toBe(false);
    expect(changedIn(wordDiff("a", "b"))).toBe(true);
  });

  it("rebuilds both texts from its runs", () => {
    const before = "a red fox in the deep snow at dawn with long shadows";
    const after = "a grey fox in deep snow at dusk with long blue shadows";
    const runs = wordDiff(before, after);
    expect(runs.filter((r) => r.op !== "add").map((r) => r.text).join(" ")).toBe(before);
    expect(runs.filter((r) => r.op !== "del").map((r) => r.text).join(" ")).toBe(after);
  });

  it("is fast enough for the longest prompt the studio takes", () => {
    const words = Array.from({ length: 1400 }, (_, i) => `w${i % 97}`);
    const started = performance.now();
    wordDiff(words.join(" "), [...words.slice(0, 700), "inserted", ...words.slice(700)].join(" "));
    expect(performance.now() - started).toBeLessThan(2000);
  });
});

describe("the length meter", () => {
  it("counts against the limit, warns near it and refuses over it", () => {
    expect(lengthMeter(10, 100)).toEqual({ text: "10 / 100", near: false, over: false });
    expect(lengthMeter(80, 100)).toEqual({ text: "80 / 100", near: false, over: false });
    expect(lengthMeter(81, 100)).toEqual({ text: "81 / 100", near: true, over: false });
    expect(lengthMeter(100, 100).over).toBe(false);
    expect(lengthMeter(101, 100)).toEqual({ text: "101 / 100", near: true, over: true });
  });
});

// ------------------------------------------------------------------ using a prompt (criteria 235, 236, 239)
describe("using a prompt in Images", () => {
  const options = { ...defaultOptions(CAPS), guidance: null };
  const full = { negative: "blurry", settings: { shape: "3:2", guidance: 4 } };

  it("offers the settings that fit as switches, and only valid ones", () => {
    expect(settingChips("images", full, CAPS)).toEqual([{ key: "shape", label: "Shape 3:2" }, { key: "guidance", label: "Guidance 4" }, { key: "negative", label: "Negative prompt" }]);
    expect(settingChips("images", { negative: null, settings: { shape: "21:9", guidance: 99 } }, CAPS)).toEqual([]);
    expect(settingChips("images", { negative: "  ", settings: { shape: "auto" } }, CAPS)).toEqual([]);
    expect(settingChips("edit", { negative: null, settings: { shape: AUTO } }, CAPS)).toEqual([{ key: "shape", label: "Size: Auto" }]);
    expect(settingChips("vector", full, CAPS)).toEqual([]);
    expect(settingChips("music", full, CAPS)).toEqual([]);
    expect(settingChips("images", full, { ...CAPS, supports: { ...CAPS.supports, cfg_scale: false, negative_prompt: false } })).toEqual([{ key: "shape", label: "Shape 3:2" }]);
  });

  it("lays the settings over the form, leaving out those switched off, and says what it set", () => {
    const got = overlayOptions(options, "images", full, new Set(), CAPS);
    expect(got.options).toMatchObject({ aspect: "3:2", guidance: 4, negativePrompt: "blurry" });
    expect(got.applied).toEqual(["3:2", "guidance 4", "a negative prompt"]);
    const less = overlayOptions(options, "images", full, new Set(["guidance", "negative"] as const), CAPS);
    expect(less.options).toMatchObject({ aspect: "3:2", guidance: null, negativePrompt: "" });
    expect(less.applied).toEqual(["3:2"]);
    expect(options.aspect).toBe(CAPS.defaults.aspect_ratio); // the form it was given is not changed
  });

  it("gives a negative prompt the guidance it needs to do anything", () => {
    const got = overlayOptions(options, "images", { negative: "blurry", settings: {} }, new Set(), CAPS);
    expect(got.options).toMatchObject({ negativePrompt: "blurry", guidance: 4 });
    expect(got.applied).toEqual(["a negative prompt", "guidance 4"]);
    expect(overlayOptions({ ...options, guidance: 1 }, "images", { negative: "x", settings: {} }, new Set(), CAPS).options.guidance).toBe(4);
    expect(overlayOptions({ ...options, guidance: 6 }, "images", { negative: "x", settings: {} }, new Set(), CAPS).options.guidance).toBe(6); // enough already
    expect(overlayOptions(options, "images", { negative: "x", settings: { guidance: 4 } }, new Set(), CAPS).applied).toEqual(["guidance 4", "a negative prompt"]); // not said twice
  });

  it("puts an edit's shape in the edit form's own place", () => {
    const got = overlayOptions(options, "edit", { negative: null, settings: { shape: "1:1" } }, new Set(), CAPS);
    expect(got.options).toMatchObject({ editAspect: "1:1", aspect: options.aspect });
    expect(overlayOptions(options, "edit", { negative: null, settings: { shape: AUTO } }, new Set(), CAPS).applied).toEqual(["Size on Auto"]);
  });

  it("tells the person what was set, in the words of criterion 235", () => {
    expect(usedText([])).toBe("Filled the prompt.");
    expect(usedText(["16:9"])).toBe("Filled the prompt, set 16:9.");
    expect(usedText(["16:9", "guidance 4"])).toBe("Filled the prompt, set 16:9 and guidance 4.");
    expect(usedText(["16:9", "guidance 4", "a negative prompt"])).toBe("Filled the prompt, set 16:9, guidance 4 and a negative prompt.");
  });
});

describe("making a run from a prompt", () => {
  const options = { ...defaultOptions(CAPS), steps: 30, seedLocked: true, seed: 7, numImages: 2, aspect: "3:2", guidance: 4, negativePrompt: "blurry" };
  const ref = { prompt_id: "p".repeat(32), version: 3 };

  it("uses the options the form has and the prompt, and names where it came from", () => {
    const body = promptRun("images", "  A lighthouse ", options, CAPS, ref);
    expect(body).toMatchObject({ mode: "generate", prompt: "A lighthouse", prompt_ref: ref, options: { steps: 30, seed: 7, num_images: 2, width: 2528, height: 1696, cfg_scale: 4, negative_prompt: "blurry" } });
    expect(body.options).not.toHaveProperty("draft");
  });

  it("makes a quick try a Draft within Draft's limits", () => {
    const body = promptRun("images", "A lighthouse", options, CAPS, ref, { quick: true });
    expect(body.options).toMatchObject({ draft: true, steps: CAPS.limits.draft.steps, num_images: 1 });
    expect(Math.max(body.options.width!, body.options.height!)).toBe(CAPS.limits.draft.long_side);
    expect(body.prompt_ref).toEqual(ref);
  });

  it("makes an edit from the tray's pictures in order", () => {
    const inputs = [{ upload_id: "u1" }, { upload_id: "u2" }];
    const body = promptRun("edit", "put image 1 in image 2", { ...options, editAspect: AUTO }, CAPS, ref, { inputs });
    expect(body).toMatchObject({ mode: "edit", input_images: inputs, prompt_ref: ref });
    expect(body.options.width).toBeNull();
  });

  it("says why Vector, Music and Edit prompts have no quick try", () => {
    expect(quickTryProblem("images")).toBeNull();
    expect(quickTryProblem("vector")).toBe("A quick try is a Draft, and drafts are for Images. Vector has none.");
    expect(quickTryProblem("music")).toBe("A quick try is a Draft, and drafts are for Images. Music has none.");
    expect(quickTryProblem("edit")).toBe("A quick try is a Draft, and drafts are for Images. Edits has none.");
  });
});

describe("where a run came from", () => {
  const ref = { id: "p", title: "Lighthouse", version: 3, deleted: false, edited: false };

  it("says the prompt and the version, and what is different about it", () => {
    expect(refLine(ref)).toBe('From "Lighthouse" v3');
    expect(refLine({ ...ref, edited: true })).toBe('From "Lighthouse" v3, edited');
    expect(refLine({ ...ref, deleted: true })).toBe('From "Lighthouse" v3, deleted');
    expect(refLine({ ...ref, edited: true, deleted: true })).toBe('From "Lighthouse" v3, edited, deleted');
    expect(refLine({ ...ref, title: null, deleted: true })).toBe("From a deleted prompt, v3");
  });

  it("names the chip beside the Images prompt box", () => {
    expect(chipLine({ title: "Lighthouse", version: 3 })).toBe('From Prompt builder: "Lighthouse" v3');
  });
});
