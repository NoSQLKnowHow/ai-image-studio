import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { ReplyEvent } from "./builderTypes";
import { QUESTIONS, STATUS, draft, full, info, makeApi, person, reply, result, send, sends, setup } from "./builderTestSupport";

afterEach(cleanup);

// ------------------------------------------------------------------ the connection (criteria 222, 223, 249)
describe("the connection", () => {
  it("says how to set it up when there is no address, and shows no chat", async () => {
    setup(makeApi({ status: vi.fn(async () => ({ ...STATUS, configured: false, reachable: false, model: "", message: "The Prompt builder needs an Ollama server. Set STUDIO_OLLAMA_URL and STUDIO_OLLAMA_MODEL in .env, then restart." })) }));
    expect(await screen.findByText(/needs an Ollama server\. Set STUDIO_OLLAMA_URL and STUDIO_OLLAMA_MODEL in \.env, then restart\./)).toBeTruthy();
    expect(screen.queryByLabelText("Chat")).toBeNull();
    expect(screen.queryByRole("button", { name: "Test connection" })).toBeNull();
  });

  it("says it is ready, with the model's name and no address", async () => {
    const { container } = setup(makeApi());
    expect(await screen.findByText("Ollama is ready: qwen3.8:27b")).toBeTruthy();
    expect(container.textContent).not.toMatch(/https?:\/\/|:11434/);
  });

  it.each([
    ["an Ollama that cannot be reached", { ...STATUS, reachable: false, message: "Can't reach the Ollama server.", hint: "Check that Ollama is running." }, "Can't reach the Ollama server.", "Check that Ollama is running."],
    ["a model Ollama doesn't have", { ...STATUS, message: "Ollama doesn't have the model qwen3.8:27b.", hint: "Pull it on the Ollama machine: ollama pull qwen3.8:27b" }, "Ollama doesn't have the model qwen3.8:27b.", "ollama pull qwen3.8:27b"],
  ])("says which it is for %s, with what to do, and still offers Test connection", async (_name, status, message, hint) => {
    setup(makeApi({ status: vi.fn(async () => status) }));
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.getByText(new RegExp(hint.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeTruthy();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeTruthy();
  });

  it("says when the studio server itself can't be asked", async () => {
    setup(makeApi({ status: vi.fn(async () => { throw new ApiError("Can't reach the studio server.", 0); }) }));
    expect(await screen.findByText("Can't reach the studio server.")).toBeTruthy();
  });

  it("shows what Test connection found: the version, the models, whether yours is there, and pictures", async () => {
    const api = makeApi();
    setup(api);
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const found = await screen.findByLabelText("What Test connection found");
    expect(within(found).getByText("version 0.99.0")).toBeTruthy();
    expect(within(found).getByText("qwen3.8:27b, llama3:8b")).toBeTruthy();
    expect(within(found).getByText("is there")).toBeTruthy();
    expect(within(found).getByText(/the model can look at pictures; it allows 262,144 tokens/)).toBeTruthy();
    expect(api.test).toHaveBeenCalledTimes(1);
  });

  it("says when the model is missing and what to do, and when the model cannot see", async () => {
    const test = { reachable: true, version: "0.99.0", models: ["other:7b"], model: "qwen3.8:27b", model_present: false, vision: false, vision_model: "", vision_model_present: false, context_length: null, message: "Ollama doesn't have the model qwen3.8:27b.", hint: "Pull it on the Ollama machine: ollama pull qwen3.8:27b" };
    setup(makeApi({ test: vi.fn(async () => test) }));
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const found = await screen.findByLabelText("What Test connection found");
    expect(within(found).getByText(/is not there — Pull it on the Ollama machine/)).toBeTruthy();
    expect(within(found).getByText("the model can't look at pictures")).toBeTruthy();
  });

  it("asks Ollama to unload only when the button is pressed, and says what it asked", async () => {
    const api = makeApi();
    const { push } = setup(api);
    await screen.findByText(/Ollama is ready/);
    expect(api.unload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Unload from memory/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("info", "Asked Ollama to unload qwen3.8:27b from memory."));
  });

  it("says when unloading failed", async () => {
    const { push } = setup(makeApi({ unload: vi.fn(async () => { throw new ApiError("Can't reach the Ollama server.", 502, "ollama_unreachable"); }) }));
    fireEvent.click(await screen.findByRole("button", { name: /Unload from memory/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("error", "Can't reach the Ollama server."));
  });
});

// ------------------------------------------------------------------ a chat (criteria 224, 225)
describe("a chat", () => {
  it("makes the chat with the first message and shows the reply with its questions and chips", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2, { questions: QUESTIONS, draft: draft("a lighthouse in a storm") }))) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    send("a lighthouse in a storm");
    expect(await screen.findByText("What time of day?")).toBeTruthy();
    expect(api.createChat).toHaveBeenCalledWith({ title: "a lighthouse in a storm", for: "images", detail: "normal", project_id: null, prompt_id: null });
    expect(api.send).toHaveBeenCalledTimes(1);
    const [chatId, body] = api.send.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(chatId).toBe("c1");
    expect(body).toMatchObject({ text: "a lighthouse in a storm", answers: [], intent: "message", think: null, attachments: [], similar: true });
    expect(screen.getAllByRole("button", { name: /Dusk|Midnight|Dawn/ })).toHaveLength(3);
    expect((screen.getByRole("textbox", { name: "The prompt so far" }) as HTMLTextAreaElement).value).toBe("a lighthouse in a storm");
  });

  it("shows the person's words at once and keeps them while the reply is written", async () => {
    let release: () => void = () => undefined;
    const api = makeApi({ send: vi.fn(async (_i: string, _b: unknown, onEvent: (e: ReplyEvent) => void) => { onEvent({ name: "start", seq: 1 }); await new Promise<void>((r) => { release = r; }); }) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    send("my idea");
    expect(await screen.findByText("my idea")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Stop/ })).toBeTruthy();
    release();
  });

  it("selects a chip when tapped, takes it back when tapped again, and sends the answers with the words", async () => {
    const api = makeApi({ getChat: vi.fn(async () => full([person(1, "idea"), reply(2, { questions: QUESTIONS })])), listChats: vi.fn(async () => ({ chats: [{ ...info(), last_text: "", message_count: 2 }] })),
      send: sends({ name: "start", seq: 3 }, result(reply(4, { reply: "Thank you." }))) });
    setup(api);
    fireEvent.click(await screen.findByRole("button", { name: /^A lighthouse/ }));
    const dusk = await screen.findByRole("button", { name: "Dusk" });
    fireEvent.click(dusk);
    expect(dusk.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Dawn" }));
    fireEvent.click(dusk);
    expect(dusk.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(within(screen.getByText("What style?").closest("fieldset")!).getByRole("button", { name: /Surprise me/ }));
    send("with a small boat");
    await screen.findByText("Thank you.");
    const body = (api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1];
    expect(body.text).toBe("with a small boat");
    expect(body.answers).toEqual([{ id: "q1", chips: ["Dawn"], mode: "chips" }, { id: "q2", mode: "surprise" }]);
  });

  it("lets Surprise me and Skip take back each other and the chips", async () => {
    const api = makeApi({ getChat: vi.fn(async () => full([person(1, "idea"), reply(2, { questions: QUESTIONS })])), listChats: vi.fn(async () => ({ chats: [{ ...info(), last_text: "", message_count: 2 }] })) });
    setup(api);
    fireEvent.click(await screen.findByRole("button", { name: /^A lighthouse/ }));
    const question = (await screen.findByText("What time of day?")).closest("fieldset")!;
    const surprise = within(question).getByRole("button", { name: /Surprise me/ });
    const skip = within(question).getByRole("button", { name: "Skip" });
    fireEvent.click(within(question).getByRole("button", { name: "Dusk" }));
    fireEvent.click(surprise);
    expect(surprise.getAttribute("aria-pressed")).toBe("true");
    expect(within(question).getByRole("button", { name: "Dusk" }).getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(skip);
    expect(skip.getAttribute("aria-pressed")).toBe("true");
    expect(surprise.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(skip);
    expect(skip.getAttribute("aria-pressed")).toBe("false");
  });

  it("cannot send an empty message, can send with only an answer chosen, and has Write it now once there is a reply", async () => {
    const api = makeApi({ getChat: vi.fn(async () => full([person(1, "idea"), reply(2, { questions: QUESTIONS, draft: draft("x") })])), listChats: vi.fn(async () => ({ chats: [{ ...info(), last_text: "", message_count: 2 }] })),
      send: sends({ name: "start", seq: 3 }, result(reply(4, { done: true, reply: "Here it is." }))) });
    setup(api);
    expect((screen.getByRole("button", { name: /^Send$/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Write it now" })).toBeNull(); // nothing to write from yet
    fireEvent.click(await screen.findByRole("button", { name: /^A lighthouse/ }));
    expect((await screen.findByRole("button", { name: /^Send$/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(await screen.findByRole("button", { name: "Dusk" }));
    expect((screen.getByRole("button", { name: /^Send$/ }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Write it now" }));
    await screen.findByText("Here it is.");
    expect((api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1]).toMatchObject({ intent: "write_now" });
  });

  it("sends with Ctrl+Enter", async () => {
    const api = makeApi({ send: sends(result(reply(2))) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    const box = screen.getByLabelText("What's the idea?");
    fireEvent.change(box, { target: { value: "idea" } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(api.send).toHaveBeenCalledTimes(1));
  });

  it("asks for three wordings when there is a draft, and shows them with what can be done with each; nothing is saved until Keep as version (criterion 240)", async () => {
    const wordings = [{ label: "simpler", prompt: "A lighthouse.", negative: null }, { label: "more cinematic", prompt: "A lighthouse, dramatic light.", negative: null }, { label: "more detailed", prompt: "A tall lighthouse on a cliff.", negative: null }];
    const api = makeApi({ getChat: vi.fn(async () => full([person(1, "idea"), reply(2, { draft: draft("a lighthouse") })])), listChats: vi.fn(async () => ({ chats: [{ ...info(), last_text: "", message_count: 2 }] })),
      send: sends({ name: "start", seq: 3 }, result(reply(4, { reply: "Three takes.", variations: wordings }))) });
    setup(api);
    fireEvent.click(await screen.findByRole("button", { name: /^A lighthouse/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Try 3 wordings" }));
    const list = await screen.findByLabelText("Three wordings");
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(within(list).getByText("more cinematic")).toBeTruthy();
    expect((api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1]).toMatchObject({ intent: "variations" });
    fireEvent.click(within(list).getAllByRole("button", { name: "Dismiss" })[0]);
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).queryByText("simpler")).toBeNull();
    expect(api.createPrompt).not.toHaveBeenCalled(); // nothing is saved until Keep as version
  });
});

// ------------------------------------------------------------------ while it is written (criteria 224, 248, 255)
describe("a reply being written", () => {
  const hold = () => {
    const state: { emit: (e: ReplyEvent) => void; finish: () => void } = { emit: () => undefined, finish: () => undefined };
    const send = vi.fn((_i: string, _b: unknown, onEvent: (e: ReplyEvent) => void) => new Promise<void>((done) => { state.emit = onEvent; state.finish = done; }));
    return { state, send };
  };

  it("shows waiting its turn, then that an image is being made, then thinking with the seconds and a fold, then the words as they grow", async () => {
    const { state, send: sendMock } = hold();
    const { onWorking } = setup(makeApi({ send: sendMock }));
    await screen.findByText(/Ollama is ready/);
    send("idea");
    await waitFor(() => expect(sendMock).toHaveBeenCalled());
    state.emit({ name: "waiting" });
    expect(await screen.findByText("Waiting for another reply…")).toBeTruthy();
    state.emit({ name: "busy", images: true });
    expect(await screen.findByText("An image is being made, so replies may be slower.")).toBeTruthy();
    state.emit({ name: "start", seq: 1 });
    state.emit({ name: "thinking", text: "Considering. ", seconds: 3.2 });
    expect(await screen.findByText("Thinking… 3 s")).toBeTruthy();
    const fold = screen.getByText("Thinking… 3 s").closest("details")!;
    expect(within(fold).getByText("Considering.")).toBeTruthy();
    state.emit({ name: "delta", text: "Lovely", thought: true });
    expect(await screen.findByText("Lovely")).toBeTruthy();
    state.emit({ name: "delta", text: "Lovely idea.", thought: true });
    expect(await screen.findByText("Lovely idea.")).toBeTruthy();
    expect(screen.getByText("Show thinking")).toBeTruthy();
    expect(onWorking).toHaveBeenLastCalledWith(true);
    state.emit(result(reply(2, { reply: "Lovely idea." })));
    state.finish();
    await waitFor(() => expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull());
    await waitFor(() => expect(onWorking).toHaveBeenLastCalledWith(false)); // (told from an effect, a beat after the card changes)
    expect(screen.queryByText("Show thinking")).toBeNull(); // the reasoning is not kept
  });

  it("says it is asking again when the answer was not in the structure asked for", async () => {
    const { state, send: sendMock } = hold();
    setup(makeApi({ send: sendMock }));
    await screen.findByText(/Ollama is ready/);
    send("idea");
    await waitFor(() => expect(sendMock).toHaveBeenCalled());
    state.emit({ name: "retry" });
    expect(await screen.findByText("Asking again…")).toBeTruthy();
    state.emit(result(reply(2)));
    state.finish();
  });

  it("stops the reply when Stop is pressed and shows what was kept, marked stopped", async () => {
    const { state, send: sendMock } = hold();
    const api = makeApi({ send: sendMock });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    send("idea");
    await waitFor(() => expect(sendMock).toHaveBeenCalled());
    state.emit({ name: "start", seq: 1 });
    state.emit({ name: "delta", text: "Thank you for", thought: false });
    fireEvent.click(await screen.findByRole("button", { name: /Stop/ }));
    await waitFor(() => expect(api.stopReply).toHaveBeenCalledWith("c1"));
    state.emit({ name: "stopped", message: reply(2, { reply: "Thank you for", stopped: true }), chat: info() });
    state.finish();
    expect(await screen.findByText("stopped")).toBeTruthy();
    expect(screen.getByText(/Thank you for/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Dusk" })).toBeNull(); // a stopped reply asks nothing
  });

  it("shows nothing and keeps nothing when the reply was stopped before any words", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, { name: "stopped", message: null }) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    send("idea");
    await waitFor(() => expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull());
    expect(screen.getByText("idea")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ what goes wrong (criterion 228)
describe("a reply that fails", () => {
  it("keeps the person's words, says what happened and what to try, and offers Retry", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, { name: "error", code: "ollama_timeout", message: "Ollama stopped answering.", hint: "Try again, or raise STUDIO_OLLAMA_TIMEOUT_S.", status: 504 }) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    send("a fox");
    const problem = await screen.findByRole("alert");
    expect(within(problem).getByText("Ollama stopped answering.")).toBeTruthy();
    expect(within(problem).getByText("Try again, or raise STUDIO_OLLAMA_TIMEOUT_S.")).toBeTruthy();
    expect(screen.getByText("a fox")).toBeTruthy();
    api.send.mockImplementationOnce(async (_i: string, _b: unknown, onEvent: (e: ReplyEvent) => void) => { onEvent(result(reply(2, { reply: "Back again." }))); });
    fireEvent.click(within(problem).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Back again.")).toBeTruthy();
    expect((api.send.mock.calls[1] as unknown as [string, Record<string, unknown>])[1]).toEqual({ retry: true });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getAllByText("a fox")).toHaveLength(1); // not added twice
  });

  it("says when the studio itself is not reachable, and when another reply is already being written", async () => {
    const api = makeApi({ send: vi.fn(async () => { throw new ApiError("A reply is already being written in this chat.", 409, "reply_in_progress"); }) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    send("idea");
    const problem = await screen.findByRole("alert");
    expect(within(problem).getByText("A reply is already being written in this chat.")).toBeTruthy();
    expect(within(problem).queryByRole("button", { name: "Retry" })).toBeNull(); // sending again would not help
  });

  it("reloads the chat when the connection dropped, so that nothing the server kept is lost from the page", async () => {
    const api = makeApi({
      send: vi.fn(async () => { throw new ApiError("Can't reach the studio server.", 0); }),
      getChat: vi.fn(async () => full([person(1, "idea"), reply(2, { reply: "It got through." })])),
    });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    send("idea");
    expect(await screen.findByText("It got through.")).toBeTruthy();
    expect(api.getChat).toHaveBeenCalledWith("c1");
  });

  it("shows a reply that could not be read as plain text with no chips", async () => {
    setup(makeApi({ send: sends(result(reply(2, { plain: true, reply: "I think you want something lovely." }))) }));
    await screen.findByText(/Ollama is ready/);
    send("idea");
    expect(await screen.findByText("I think you want something lovely.")).toBeTruthy();
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("lists what was left out because it broke a rule, in the model's reply", async () => {
    setup(makeApi({ send: sends(result(reply(2, { notices: ["The builder suggested guidance 40; the studio allows 0.1 to 20, so it was left out."] }))) }));
    await screen.findByText(/Ollama is ready/);
    send("idea");
    const left = await screen.findByLabelText("Left out");
    expect(within(left).getByText(/guidance 40; the studio allows 0\.1 to 20, so it was left out\./)).toBeTruthy();
  });
});

// ------------------------------------------------------------------ text is only ever text (criterion 250)
describe("what the model writes", () => {
  it("is shown as text and never as markup", async () => {
    const hostile = '<img src=x onerror="window.__owned=1"> <script>window.__owned=2</script> **bold** [link](http://example.com)';
    const { container } = setup(makeApi({ send: sends(result(reply(2, { reply: hostile, draft: draft(hostile), questions: [{ id: "q1", text: "<b>Q</b>?", chips: ["<i>a</i>", "b", "c"] }], notices: ["<u>n</u>"] }))) }));
    await screen.findByText(/Ollama is ready/);
    send("idea");
    const messages = await screen.findByRole("list", { name: "Messages" });
    expect(within(messages).getByText(hostile)).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "The prompt so far" }) as HTMLTextAreaElement).value).toBe(hostile);
    expect(screen.getByText("<b>Q</b>?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "<i>a</i>" })).toBeTruthy();
    expect(container.querySelector("script, img[onerror], b, i, u, a[href]")).toBeNull();
    expect((window as unknown as { __owned?: number }).__owned).toBeUndefined();
  });
});
