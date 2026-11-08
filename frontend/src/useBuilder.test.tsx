import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { ReplyEvent } from "./builderTypes";
import { info, makeApi, person, reply, result, sends } from "./builderTestSupport";
import { STOP_RETRY_MS, STOP_TRIES, useBuilder } from "./useBuilder";

// The builder's state as a hook: pictures attached to a chat that does not exist yet, and a chat opened by Refine or Help me write (DESIGN.md §37.2; criteria 241, 243, 244).

const bodyOf = (api: ReturnType<typeof makeApi>, call = 0) => (api.send.mock.calls[call] as unknown as [string, Record<string, unknown>])[1];
const picture = (name: string) => ({ file: new Blob([name], { type: "image/png" }), name });

describe("pictures attached to a new chat", () => {
  it("make one chat for all of them, however many are attached in a row", async () => {
    const api = makeApi();
    const { result: hook } = renderHook(() => useBuilder(api));
    const attach = hook.current.attach; // the same function for all three: what a loop over several chosen files holds
    await act(async () => {
      await attach(picture("one.png"));
      await attach(picture("two.png"));
      await attach(picture("three.png"));
    });
    expect(api.createChat).toHaveBeenCalledTimes(1);
    expect(api.attachFile.mock.calls.map((c) => c[0])).toEqual(["c1", "c1", "c1"]);
    await waitFor(() => expect(hook.current.pending).toHaveLength(3));
  });

  it("go to the chat that is open, not to a new one", async () => {
    const api = makeApi({ getChat: vi.fn(async () => ({ chat: info({ id: "c9" }), messages: [], draft: null, pending: [], preferences: 0 })) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.open("c9"));
    await act(async () => { await hook.current.attach(picture("one.png")); });
    expect(api.createChat).not.toHaveBeenCalled();
    expect(api.attachFile.mock.calls[0][0]).toBe("c9");
  });

  it("from the history go the same way, by the picture's id", async () => {
    const api = makeApi({ attachImage: vi.fn(async () => ({ id: "a2", url: "/a2", width: 1, height: 1, name: null })) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => { await hook.current.attach({ imageId: "img7" }); });
    expect(api.attachImage).toHaveBeenCalledWith("c1", "img7");
  });
});

describe("a chat opened by Refine or Help me write", () => {
  it("sends its first plain message as that, with the run it is about, and the next as an ordinary message", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => hook.current.prime("refine", "run42"));
    await act(async () => hook.current.send({ text: "the hands look off, too dark" }));
    expect(bodyOf(api, 0)).toMatchObject({ intent: "refine", run_id: "run42", text: "the hands look off, too dark" });
    await act(async () => hook.current.send({ text: "lighter, please" }));
    expect(bodyOf(api, 1).intent).toBe("message");
    expect(bodyOf(api, 1)).not.toHaveProperty("run_id");
    expect(hook.current.primed).toBeNull();
  });

  it("uses Help me write the same way, with no run", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => hook.current.prime("help_edit"));
    await act(async () => hook.current.send({ text: "put the dog into the beach" }));
    expect(bodyOf(api)).toMatchObject({ intent: "help_edit" });
    expect(bodyOf(api)).not.toHaveProperty("run_id");
  });

  it("leaves an intent the person chose themselves alone, and keeps the priming for the message that follows", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => hook.current.prime("refine", "run42"));
    await act(async () => hook.current.send({ text: "just write it", intent: "write_now" }));
    expect(bodyOf(api).intent).toBe("write_now");
    expect(bodyOf(api).run_id).toBe("run42"); // the run is still what the chat is about
  });

  it("is forgotten when another chat is started or opened", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => hook.current.prime("refine", "run42"));
    act(() => hook.current.startNew());
    expect(hook.current.primed).toBeNull();
    act(() => hook.current.prime("help_edit"));
    await act(async () => hook.current.open("c1"));
    expect(hook.current.primed).toBeNull();
    await act(async () => hook.current.send({ text: "hello" }));
    expect(bodyOf(api).intent).toBe("message");
  });

  it("does not turn a retry into something else", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.send({ text: "hello" }));
    act(() => hook.current.prime("refine", "run42"));
    await act(async () => hook.current.send({ retry: true }));
    expect(bodyOf(api, 1)).toEqual({ retry: true });
    expect(hook.current.primed).not.toBeNull(); // a retry sends nothing new, so the priming waits for the next message
  });
});

describe("Stop (criterion 224)", () => {
  const early = () => new ApiError("No reply is being written in this chat.", 409, "no_reply");
  /** A reply that is still being waited for until `release()`. */
  const held = () => {
    const state = { release: () => undefined as void };
    const send = vi.fn((_c: string, _b: unknown, _on: unknown) => new Promise<void>((done) => { state.release = done; }));
    return { state, send };
  };

  it("asks once when the reply is being written", async () => {
    const { state, send } = held();
    const api = makeApi({ send, stopReply: vi.fn(async () => ({ stopping: true })) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => { void hook.current.send({ text: "idea" }); });
    await waitFor(() => expect(send).toHaveBeenCalled());
    await act(async () => hook.current.stop());
    expect(api.stopReply).toHaveBeenCalledTimes(1);
    state.release();
  });

  it("tries again when the server does not have the reply on its list yet, and stops it once it does", async () => {
    const { state, send } = held();
    const api = makeApi({ send, stopReply: vi.fn().mockRejectedValueOnce(early()).mockRejectedValueOnce(early()).mockResolvedValue({ stopping: true }) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => { void hook.current.send({ text: "idea" }); });
    await waitFor(() => expect(send).toHaveBeenCalled());
    await act(async () => hook.current.stop());
    expect(api.stopReply).toHaveBeenCalledTimes(3);
    state.release();
  });

  it("gives up after a second or so rather than for ever", async () => {
    const { state, send } = held();
    const api = makeApi({ send, stopReply: vi.fn(async () => { throw early(); }) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => { void hook.current.send({ text: "idea" }); });
    await waitFor(() => expect(send).toHaveBeenCalled());
    await act(async () => hook.current.stop());
    expect(api.stopReply).toHaveBeenCalledTimes(STOP_TRIES);
    expect(STOP_TRIES * STOP_RETRY_MS).toBeLessThanOrEqual(1500);
    state.release();
  });

  it("does not keep asking when the reply is over, or the server refuses for another reason", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))), stopReply: vi.fn(async () => { throw early(); }) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.send({ text: "idea" })); // finished
    await act(async () => hook.current.stop());
    expect(api.stopReply).toHaveBeenCalledTimes(1);
    const other = makeApi({ stopReply: vi.fn(async () => { throw new ApiError("Nope.", 500, "server_error"); }) });
    const { result: second } = renderHook(() => useBuilder(other));
    await act(async () => { await second.current.attach({ file: new Blob(["x"]), name: "x.png" }); }); // a chat now exists
    await act(async () => second.current.stop());
    expect(other.stopReply).toHaveBeenCalledTimes(1);
  });
});

describe("Stop before anything was written", () => {
  it("is said, once, and forgotten when the next message is sent or another chat is opened", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, { name: "stopped", message: null, chat: info() }), getChat: vi.fn(async () => ({ chat: info({ id: "c9" }), messages: [], draft: null, pending: [], preferences: 0 })) });
    const { result: hook } = renderHook(() => useBuilder(api));
    expect(hook.current.stoppedEmpty).toBe(false);
    await act(async () => hook.current.send({ text: "idea" }));
    expect(hook.current.stoppedEmpty).toBe(true);
    await act(async () => hook.current.open("c9"));
    expect(hook.current.stoppedEmpty).toBe(false);
    await act(async () => hook.current.send({ text: "idea again" }));
    expect(hook.current.stoppedEmpty).toBe(true);
    api.send.mockImplementationOnce(sends({ name: "start", seq: 3 }, result(reply(4))));
    await act(async () => hook.current.send({ text: "and again" }));
    expect(hook.current.stoppedEmpty).toBe(false);
  });

  it("is not said when what was written is kept", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, { name: "stopped", message: reply(2, { reply: "Lovely" }), chat: info() }) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.send({ text: "idea" }));
    expect(hook.current.stoppedEmpty).toBe(false);
  });
});

// ------------------------------------------------------------------ what mutation checks showed the tests above did not pin down
/** A promise the test settles when it chooses, to look at the hook while something is on its way. */
function gate<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (error: unknown) => void = () => undefined;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("what the hook says while something is on its way", () => {
  it("is testing the connection from the press until the answer, and says what it found", async () => {
    const answer = gate<Awaited<ReturnType<ReturnType<typeof makeApi>["test"]>>>();
    const api = makeApi({ test: vi.fn(() => answer.promise) });
    const { result: hook } = renderHook(() => useBuilder(api));
    expect(hook.current.testing).toBe(false);
    act(() => { void hook.current.runTest(); });
    expect(hook.current.testing).toBe(true);
    await act(async () => answer.resolve({ reachable: true, version: "0.99", models: [], model: "m", model_present: true, vision: true, vision_model: "", vision_model_present: false, context_length: null, message: null, hint: null } as never));
    await waitFor(() => expect(hook.current.testing).toBe(false));
    expect(hook.current.test).toMatchObject({ version: "0.99" });
  });

  it("is unloading from the press until the answer, and the answer is in words", async () => {
    const answer = gate<{ ok: boolean; unloaded: string[] }>();
    const api = makeApi({ unload: vi.fn(() => answer.promise) });
    const { result: hook } = renderHook(() => useBuilder(api));
    let said: Promise<{ ok: boolean; message: string }> = Promise.resolve({ ok: false, message: "" });
    act(() => { said = hook.current.unload(); });
    expect(hook.current.unloading).toBe(true);
    await act(async () => answer.resolve({ ok: true, unloaded: ["qwen3.8:27b", "llava"] }));
    expect(await said).toEqual({ ok: true, message: "Asked Ollama to unload qwen3.8:27b and llava from memory." });
    expect(hook.current.unloading).toBe(false);
    const nothing = makeApi({ unload: vi.fn(async () => ({ ok: true, unloaded: [] })) });
    const { result: second } = renderHook(() => useBuilder(nothing));
    expect(await second.current.unload()).toEqual({ ok: true, message: "Nothing was loaded." });
    const failing = makeApi({ unload: vi.fn(async () => { throw new ApiError("Can't reach the Ollama server.", 502, "ollama_unreachable"); }) });
    const { result: third } = renderHook(() => useBuilder(failing));
    expect(await third.current.unload()).toEqual({ ok: false, message: "Can't reach the Ollama server." });
    expect(third.current.unloading).toBe(false);
  });

  it("is opening a chat from the request until it has it, and says why it could not", async () => {
    const chatGate = gate<Awaited<ReturnType<ReturnType<typeof makeApi>["getChat"]>>>();
    const api = makeApi({ getChat: vi.fn(() => chatGate.promise) });
    const { result: hook } = renderHook(() => useBuilder(api));
    expect(hook.current.opening).toBe(false);
    act(() => { void hook.current.open("c1"); });
    expect(hook.current.opening).toBe(true);
    await act(async () => chatGate.resolve({ chat: info({ id: "c1" }), messages: [], draft: null, pending: [], preferences: 2 }));
    await waitFor(() => expect(hook.current.opening).toBe(false));
    expect(hook.current.chat?.id).toBe("c1");
    expect(hook.current.preferences).toBe(2);
    const gone = makeApi({ getChat: vi.fn(async () => { throw new ApiError("That chat doesn't exist.", 404, "not_found"); }) });
    const { result: lost } = renderHook(() => useBuilder(gone));
    await act(async () => lost.current.open("c9"));
    expect(lost.current.openProblem).toBe("That chat doesn't exist.");
    expect(lost.current.opening).toBe(false);
  });

  it("is attaching a picture from the moment it is chosen until it is up", async () => {
    const up = gate<{ id: string; url: string; width: number; height: number; name: string }>();
    const api = makeApi({ attachFile: vi.fn(() => up.promise) });
    const { result: hook } = renderHook(() => useBuilder(api));
    expect(hook.current.picking).toBe(false);
    let done: Promise<string | null> = Promise.resolve(null);
    act(() => { done = hook.current.attach({ file: new Blob(["x"]), name: "x.png" }); });
    await waitFor(() => expect(hook.current.picking).toBe(true));
    await act(async () => up.resolve({ id: "a1", url: "/a1", width: 1, height: 1, name: "x.png" }));
    expect(await done).toBeNull();
    expect(hook.current.picking).toBe(false);
    expect(hook.current.pending.map((p) => p.id)).toEqual(["a1"]);
  });
});

describe("what a message looks like on the page before the server has answered", () => {
  /** A `send` that the test answers by hand: it keeps the function that carries the server's events, and finishes when told. */
  function held() {
    const state: { emit: (e: ReplyEvent) => void; finish: () => void; body: Record<string, unknown> | null } = { emit: () => undefined, finish: () => undefined, body: null };
    const send = vi.fn((_chat: string, body: unknown, onEvent: (e: ReplyEvent) => void) => new Promise<void>((done) => { state.emit = onEvent; state.finish = done; state.body = body as Record<string, unknown>; }));
    return { state, send };
  }
  const questions = [{ id: "q1", text: "What time of day?", chips: ["Dusk", "Dawn", "Noon"] }];

  it("shows the person's words with the answers they chose, the draft they edited and whether they asked it to think; the server confirms its place", async () => {
    const { state, send } = held();
    const api = makeApi({ send });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => { void hook.current.send({ text: "  at the harbour ", questions, picks: { q1: { mode: "chips", chips: ["Dusk"] } }, draft: { prompt: "MY WORDS", negative: null, settings: {}, note: "" }, think: true }); });
    await waitFor(() => expect(hook.current.messages).toHaveLength(1));
    const shown = hook.current.messages[0];
    expect(shown).toMatchObject({ seq: -1, role: "user", text: "at the harbour" });
    expect(shown.content).toMatchObject({ answers: [{ id: "q1", question: "What time of day?", mode: "chips", chips: ["Dusk"] }], intent: "message", draft: { prompt: "MY WORDS" }, think: true });
    expect(state.body).toMatchObject({ similar: true, exclude: [], think: true, intent: "message" });   // a first look at saved prompts is the default
    act(() => state.emit({ name: "start", seq: 7 }));
    expect(hook.current.messages[0].seq).toBe(7);                                                          // the server's number for it
    act(() => state.emit(result(reply(8), info({ title: "At the harbour" }))));
    expect(hook.current.chat?.title).toBe("At the harbour");                                              // and the chat as the reply left it
    expect(hook.current.messages.map((m) => m.seq)).toEqual([7, 8]);
    await act(async () => state.finish());
  });

  it("passes on what the page chose about saved prompts and leaves out, and a message with no choices says none", async () => {
    const { state, send } = held();
    const api = makeApi({ send });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => { void hook.current.send({ text: "x", similar: false, exclude: ["p1"] }); });
    await waitFor(() => expect(state.body).not.toBeNull());
    expect(state.body).toMatchObject({ similar: false, exclude: ["p1"], answers: [] });
    expect(state.body).not.toHaveProperty("draft");
    await act(async () => state.finish());
  });

  it("takes away the model's last reply at once when a retry asks for it to be redone, and keeps it for a plain retry", async () => {
    const { state, send } = held();
    // (a stream that ends with no ending makes the hook ask the server what it has, so the chat it has is the same every time)
    const api = makeApi({ send, getChat: vi.fn(async () => ({ chat: info(), messages: [person(1, "idea"), reply(2)], draft: null, pending: [], preferences: 0 })) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.open("c1"));
    expect(hook.current.messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    act(() => { void hook.current.send({ retry: true }); });
    await waitFor(() => expect(state.body).not.toBeNull());
    expect(hook.current.messages.map((m) => m.role)).toEqual(["user", "assistant"]);     // a plain retry leaves the reply on the screen
    await act(async () => state.finish());
    const second = held();
    api.send.mockImplementation(second.send);
    act(() => { void hook.current.send({ retry: true, redo: true }); });
    await waitFor(() => expect(second.state.body).not.toBeNull());
    expect(hook.current.messages.map((m) => m.role)).toEqual(["user"]);                  // a redo shows it gone
    await act(async () => second.state.finish());
  });

  it("does not say anything is wrong when the person closes the page, and does when something else goes wrong", async () => {
    const aborted = makeApi({ send: vi.fn(async () => { throw new DOMException("closed", "AbortError"); }) });
    const { result: closed } = renderHook(() => useBuilder(aborted));
    await act(async () => closed.current.send({ text: "x" }));
    expect(closed.current.problem).toBeNull();
    const broken = makeApi({ send: vi.fn(async () => { throw new Error("boom"); }) });
    const { result: other } = renderHook(() => useBuilder(broken));
    await act(async () => other.current.send({ text: "x" }));
    expect(other.current.problem).toEqual({ message: "Something went wrong.", hint: null, code: null });
    const refused = makeApi({ send: vi.fn(async () => { throw new ApiError("A reply is already being written in this chat.", 409, "reply_in_progress", [], "Wait for it."); }) });
    const { result: busy } = renderHook(() => useBuilder(refused));
    await act(async () => busy.current.send({ text: "x" }));
    expect(busy.current.problem).toEqual({ message: "A reply is already being written in this chat.", hint: "Wait for it.", code: "reply_in_progress" });
  });
});

describe("the choices of a chat that does not exist yet", () => {
  it("are kept until it is made, one at a time, and a choice of nothing takes the project and the prompt away", async () => {
    const api = makeApi();
    const { result: hook } = renderHook(() => useBuilder(api));
    expect(hook.current.blank).toMatchObject({ for: "images", detail: "normal", project_id: null, prompt_id: null });
    await act(async () => { await hook.current.change({ for: "music" }); });
    await act(async () => { await hook.current.change({ detail: "rich" }); });
    await act(async () => { await hook.current.change({ project_id: "pr1", prompt_id: "p1" }); });
    expect(hook.current.blank).toEqual({ for: "music", detail: "rich", project_id: "pr1", prompt_id: "p1" });
    await act(async () => { await hook.current.change({ title: "ignored here" }); });
    expect(hook.current.blank).toEqual({ for: "music", detail: "rich", project_id: "pr1", prompt_id: "p1" });    // a name is made with the chat
    await act(async () => { await hook.current.change({ project_id: null, prompt_id: null }); });
    expect(hook.current.blank).toMatchObject({ project_id: null, prompt_id: null });
    expect(api.patchChat).not.toHaveBeenCalled();
    await act(async () => { await hook.current.send({ text: "an idea" }); });
    expect(api.createChat.mock.calls[0][0]).toMatchObject({ for: "music", detail: "rich", project_id: null, prompt_id: null });
  });
});

describe("the last small things the hook does", () => {
  it("forgets that Stop kept nothing when another chat is started", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, { name: "stopped", message: null, chat: info() }) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.send({ text: "idea" }));
    expect(hook.current.stoppedEmpty).toBe(true);
    act(() => hook.current.startNew());
    expect(hook.current.stoppedEmpty).toBe(false);
  });

  it("shows a message with no draft, no choices and no thinking choice as exactly that, and an answer to a question it does not know by its id", async () => {
    const send = vi.fn((_c: string, _b: unknown, _on: unknown) => new Promise<void>(() => undefined));
    const api = makeApi({ send });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => { void hook.current.send({ text: "plain", questions: [], picks: { q9: { mode: "skip", chips: [] } } }); });
    await waitFor(() => expect(hook.current.messages).toHaveLength(1));
    // (an answer to a question that was not asked is not sent: the server would ignore it, and the page does not show it)
    expect(hook.current.messages[0].content).toEqual({ answers: [], intent: "message", draft: null, think: null });
  });

  it("asks the server what it has when the stream ends with no ending, and says the connection was lost", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }), getChat: vi.fn(async () => ({ chat: info(), messages: [person(1, "idea")], draft: null, pending: [], preferences: 0 })) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.send({ text: "idea" }));
    expect(api.getChat).toHaveBeenCalled();
    expect(hook.current.problem).toMatchObject({ code: "connection_lost" });
    expect(hook.current.writing).toBe(false);
  });

  it("says something went wrong for a network failure that is not the page being closed", async () => {
    const api = makeApi({ send: vi.fn(async () => { throw new DOMException("the network went away", "NetworkError"); }) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.send({ text: "idea" }));
    expect(hook.current.problem).toEqual({ message: "Something went wrong.", hint: null, code: null });
  });

  it("does not ask again for a Stop the server refuses for a reason of its own", async () => {
    const wait = gate<void>();
    const api = makeApi({ send: vi.fn(() => wait.promise), stopReply: vi.fn(async () => { throw new ApiError("Nope.", 500, "server_error"); }) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => { void hook.current.send({ text: "idea" }); });
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    await act(async () => hook.current.stop());
    expect(api.stopReply).toHaveBeenCalledTimes(1);
    await act(async () => wait.resolve());
  });
});
