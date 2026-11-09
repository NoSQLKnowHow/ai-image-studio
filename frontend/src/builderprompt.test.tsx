import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { ChatListItem, ReplyEvent } from "./builderTypes";
import { QUESTIONS, draft, full, info, makeApi, person, reply, result, send, sends, setup } from "./builderTestSupport";

afterEach(cleanup);

const withChat = (messages: ReturnType<typeof full>["messages"], over: Parameters<typeof makeApi>[0] = {}, chat = info()) =>
  makeApi({
    getChat: vi.fn(async () => full(messages, { chat })), listChats: vi.fn(async () => ({ chats: [{ ...chat, last_text: "", message_count: messages.length } as ChatListItem] })), ...over,
  });
const open = async () => fireEvent.click(await screen.findByRole("button", { name: /^A lighthouse/ }));
const box = () => screen.getByRole("textbox", { name: "The prompt so far" }) as HTMLTextAreaElement;

// ------------------------------------------------------------------ the prompt so far (criterion 226)
describe("the prompt so far", () => {
  it("says what it is until there is one, and has nothing to save", async () => {
    setup(makeApi());
    await screen.findByText(/Ollama is ready/);
    expect(screen.getByText(/The prompt appears here as the builder writes it/)).toBeTruthy();
    expect(box().value).toBe("");
    expect((screen.getByRole("button", { name: "Save as prompt" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the newest draft with a length meter against the studio's limit", async () => {
    setup(withChat([person(1, "idea"), reply(2, { draft: draft("a lighthouse in a storm") })]));
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    expect(screen.getByText("23 / 8000")).toBeTruthy();
  });

  it("can be edited by hand, and the next message starts from the edited words", async () => {
    const api = withChat([person(1, "idea"), reply(2, { draft: draft("a lighthouse", { settings: { shape: "3:2" } }) })], { send: sends({ name: "start", seq: 3 }, result(reply(4))) });
    setup(api);
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse"));
    fireEvent.change(box(), { target: { value: "MY OWN lighthouse words" } });
    expect(box().value).toBe("MY OWN lighthouse words");
    send("make it snowy");
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect((api.send.mock.calls[0] as unknown as [string, { draft: unknown }])[1].draft).toMatchObject({ prompt: "MY OWN lighthouse words", settings: { shape: "3:2" } });
  });

  it("sends no draft when the words were not changed", async () => {
    const api = withChat([person(1, "idea"), reply(2, { draft: draft("a lighthouse") })], { send: sends({ name: "start", seq: 3 }, result(reply(4))) });
    setup(api);
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse"));
    fireEvent.change(box(), { target: { value: "changed" } });
    fireEvent.change(box(), { target: { value: "a lighthouse" } }); // put back
    send("more");
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect((api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1]).not.toHaveProperty("draft");
  });

  it("warns near the limit and refuses past it: the prompt cannot be saved or used", async () => {
    setup(withChat([person(1, "idea"), reply(2, { draft: draft("short") })]));
    await open();
    await waitFor(() => expect(box().value).toBe("short"));
    fireEvent.change(box(), { target: { value: "x".repeat(8001) } });
    expect(screen.getByText("8001 / 8000")).toBeTruthy();
    expect(screen.getByText("Too long for the studio to use.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save as prompt" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(box(), { target: { value: "x".repeat(8000) } });
    expect(screen.queryByText("Too long for the studio to use.")).toBeNull();
    expect((screen.getByRole("button", { name: "Save as prompt" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("marks the words the last reply changed, in text as well as in colour", async () => {
    setup(withChat([person(1, "idea"), reply(2, { draft: draft("a lighthouse at dusk") }), person(3, "dawn"), reply(4, { draft: draft("a lighthouse at dawn in fog") })]));
    await open();
    fireEvent.click(await screen.findByText("What the last reply changed"));
    const changed = screen.getByLabelText("The changed words");
    expect(within(changed).getByLabelText("removed: dusk").tagName).toBe("DEL");
    expect(within(changed).getByLabelText("added: dawn in fog").tagName).toBe("INS");
    expect(within(changed).getByText("a lighthouse at")).toBeTruthy();
  });

  it("has no changes to show after the first reply", async () => {
    setup(withChat([person(1, "idea"), reply(2, { draft: draft("a lighthouse") })]));
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse"));
    expect(screen.queryByText("What the last reply changed")).toBeNull();
  });

  it("offers the settings that fit as switches that can be turned off", async () => {
    setup(withChat([person(1, "idea"), reply(2, { draft: draft("a lighthouse", { negative: "blurry", settings: { shape: "3:2", guidance: 4 } }) })]));
    await open();
    const group = await screen.findByRole("group", { name: "Settings that fit" });
    const shape = within(group).getByRole("button", { name: "Shape 3:2" });
    expect(shape.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(shape);
    expect(shape.getAttribute("aria-pressed")).toBe("false");
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Shape 3:2", "Guidance 4", "Negative prompt"]);
    expect((screen.getByRole("textbox", { name: "Negative prompt" }) as HTMLTextAreaElement).value).toBe("blurry");
  });

  it("lists what a Vector or Music prompt would set, without switches", async () => {
    setup(withChat([person(1, "idea"), reply(2, { draft: draft("a fox", { settings: { kind: "flat", colours: 3, palette: ["#FF0000"], width_mm: 100, height_mm: 80 } }) })], {}, info({ for: "vector" })));
    await open();
    const list = await screen.findByRole("list", { name: "Settings" });
    expect(within(list).getAllByRole("listitem").map((l) => l.textContent)).toEqual(["Kind flat", "3 colours", "Inks #FF0000", "100 × 80 mm"]);
    expect(screen.queryByRole("textbox", { name: "Negative prompt" })).toBeNull();
    expect(screen.queryByText("Add a negative prompt")).toBeNull();
  });

  it("offers a negative prompt box for Images when there is none yet", async () => {
    setup(withChat([person(1, "idea"), reply(2, { draft: draft("a fox") })]));
    await open();
    fireEvent.click(await screen.findByText("Add a negative prompt"));
    expect(screen.getByRole("textbox", { name: "Negative prompt" })).toBeTruthy();
  });

  it("says where Use in goes for what the chat is for, and has the buttons the spec names", async () => {
    setup(withChat([person(1, "idea"), reply(2, { draft: draft("a fox") })], {}, info({ for: "music" })));
    await open();
    for (const name of ["Save as prompt", "Use in Music", "Generate now", "Quick try"]) expect(await screen.findByRole("button", { name })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Quick try" }) as HTMLButtonElement).title).toBe("A quick try is a Draft, and drafts are for Images. Music has none.");
  });
});

// ------------------------------------------------------------------ saving (criterion 229)
describe("saving", () => {
  const chatWithDraft = (over: Parameters<typeof makeApi>[0] = {}, chat = info()) =>
    withChat([person(1, "a lighthouse"), reply(2, { title: "Lighthouse in a storm", tags: ["sea", "storm"], draft: draft("a lighthouse in a storm", { negative: "blurry", settings: { shape: "3:2" }, note: "added the storm" }) })], over, chat);

  it("saves a prompt with the title and tags the builder suggested, which can be edited", async () => {
    const api = chatWithDraft();
    const { push } = setup(api);
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    fireEvent.click(screen.getByRole("button", { name: "Save as prompt" }));
    const dialog = await screen.findByRole("dialog");
    expect((within(dialog).getByLabelText("Title") as HTMLInputElement).value).toBe("Lighthouse in a storm");
    expect((within(dialog).getByLabelText(/Tags/) as HTMLInputElement).value).toBe("sea, storm");
    expect((within(dialog).getByLabelText("Note") as HTMLInputElement).value).toBe("added the storm");
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "My lighthouse" } });
    fireEvent.change(within(dialog).getByLabelText(/Tags/), { target: { value: "sea, dusk , " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.createPrompt).toHaveBeenCalled());
    expect(api.createPrompt.mock.calls[0][0]).toEqual({
      title: "My lighthouse", text: "a lighthouse in a storm", negative: "blurry", settings: { shape: "3:2" }, note: "added the storm", source: "builder", for: "images",
      project_id: null, tags: ["sea", "dusk"], chat_id: "c1", message_seq: 2,
    });
    expect(api.patchChat).toHaveBeenCalledWith("c1", { prompt_id: "p1" }); // the chat is about it from now on
    await waitFor(() => expect(push).toHaveBeenCalledWith("success", "Saved “My lighthouse” as a prompt, version 1."));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("files the prompt in a project chosen, and says it came from hand editing when it did", async () => {
    const api = chatWithDraft();
    setup(api, { projects: [{ id: "a".repeat(32), name: "Logo", created_at: "t", counts: { image: 0, music: 0 } }] });
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    fireEvent.change(box(), { target: { value: "edited by hand" } });
    fireEvent.click(screen.getByRole("button", { name: "Save as prompt" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Project"), { target: { value: "a".repeat(32) } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.createPrompt).toHaveBeenCalled());
    expect(api.createPrompt.mock.calls[0][0]).toMatchObject({ text: "edited by hand", source: "hand", project_id: "a".repeat(32) });
  });

  it("starts the project at the one the chat is in", async () => {
    const project = "a".repeat(32);
    setup(chatWithDraft({}, info({ project_id: project })), { projects: [{ id: project, name: "Logo", created_at: "t", counts: { image: 0, music: 0 } }] });
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    fireEvent.click(screen.getByRole("button", { name: "Save as prompt" }));
    expect((within(await screen.findByRole("dialog")).getByLabelText("Project") as HTMLSelectElement).value).toBe(project);
  });

  it("says why it could not be saved, in the dialog, and keeps the dialog open", async () => {
    const api = chatWithDraft({ createPrompt: vi.fn(async () => { throw new ApiError("The builder suggested guidance 40; the studio allows 0.1 to 20, so it was left out.", 422, "bad_settings"); }) });
    setup(api);
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    fireEvent.click(screen.getByRole("button", { name: "Save as prompt" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toBeTruthy();
    expect(within(dialog).getByText(/guidance 40/)).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("cannot save without a title", async () => {
    setup(chatWithDraft());
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    fireEvent.click(screen.getByRole("button", { name: "Save as prompt" }));
    const dialog = await screen.findByRole("dialog");
    const save = within(dialog).getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "   " } });
    expect(save.disabled).toBe(true);
  });

  it("offers Save as new version only when the chat is about a saved prompt, and says Nothing changed when nothing did", async () => {
    const plain = chatWithDraft();
    setup(plain);
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    expect(screen.queryByRole("button", { name: "Save as new version" })).toBeNull();
    cleanup();
    const linked = chatWithDraft({ addVersion: vi.fn(async () => ({ created: false, message: "Nothing changed.", version: 1, prompt: {} })) }, info({ prompt_id: "p1" }));
    const { push } = setup(linked);
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(linked.addVersion).toHaveBeenCalled());
    expect(linked.addVersion.mock.calls[0]).toEqual(["p1", { text: "a lighthouse in a storm", negative: "blurry", settings: { shape: "3:2" }, note: "added the storm", source: "builder", chat_id: "c1", message_seq: 2 }]);
    await waitFor(() => expect(push).toHaveBeenCalledWith("info", "Nothing changed."));
    expect(linked.createPrompt).not.toHaveBeenCalled();
  });

  it("says Saved as version 2 when it made one", async () => {
    const api = chatWithDraft({}, info({ prompt_id: "p1" }));
    const { push } = setup(api);
    await open();
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("success", "Saved as version 2."));
  });

  it("keeps a wording as a version of the chat's prompt, or starts a prompt from it", async () => {
    const wordings = [{ label: "simpler", prompt: "A lighthouse.", negative: null }];
    const messages = [person(1, "idea"), reply(2, { draft: draft("a lighthouse", { settings: { shape: "3:2" } }) }), reply(4, { variations: wordings, reply: "Takes." })];
    const linked = withChat(messages, {}, info({ prompt_id: "p1" }));
    setup(linked);
    await open();
    fireEvent.click(await screen.findByRole("button", { name: "Keep as version" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(linked.addVersion).toHaveBeenCalled());
    expect(linked.addVersion.mock.calls[0][1]).toMatchObject({ text: "A lighthouse.", source: "wording", note: "Wording: simpler", settings: { shape: "3:2" } });
  });
});

// ------------------------------------------------------------------ things to remember
describe("suggestions to remember", () => {
  it("are added only when Add is pressed, and can be turned down", async () => {
    const api = withChat([person(1, "idea"), reply(2, { remember: ["always 16:9", "no text in the picture"] })]);
    const { push } = setup(api);
    await open();
    expect(await screen.findByText(/Remember “always 16:9”\?/)).toBeTruthy();
    expect(api.addPreference).not.toHaveBeenCalled(); // nothing is added by the reply
    const first = screen.getByText(/Remember “always 16:9”\?/).closest("p")!;
    fireEvent.click(within(first).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(api.addPreference).toHaveBeenCalledWith({ text: "always 16:9", source: "suggestion", project_id: null }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("success", "Added “always 16:9” to your preferences."));
    await waitFor(() => expect(screen.queryByText(/Remember “always 16:9”\?/)).toBeNull());
    const second = screen.getByText(/Remember “no text in the picture”\?/).closest("p")!;
    fireEvent.click(within(second).getByRole("button", { name: "No" }));
    expect(screen.queryByText(/Remember “no text/)).toBeNull();
    expect(api.addPreference).toHaveBeenCalledTimes(1);
  });

  it("are only offered under the reply that made them, while it is the last", async () => {
    setup(withChat([person(1, "idea"), reply(2, { remember: ["old one"] }), person(3, "more"), reply(4, {})]));
    await open();
    await screen.findByText("Lovely idea.", {}, { timeout: 2000 }).catch(() => undefined);
    expect(screen.queryByText(/Remember “old one”\?/)).toBeNull();
  });
});

// ------------------------------------------------------------------ what it looked at (criteria 233, 234)
describe("what the chat says it was told", () => {
  it("says how many preferences and which saved prompts the first reply was given, and asks again without one that is left out", async () => {
    const context = { preferences: 2, examples: [{ id: "p1", title: "Harbour at dusk" }, { id: "p2", title: "Lighthouse" }], dropped: 0 };
    const api = withChat([person(1, "idea"), reply(2, { context })], { send: sends({ name: "start", seq: 1 }, result(reply(2, { context: { preferences: 2, examples: [{ id: "p2", title: "Lighthouse" }], dropped: 0 } }))) });
    setup(api);
    await open();
    expect(await screen.findByText("Using 2 preferences.")).toBeTruthy();
    expect(screen.getByText(/Looked at 2 of your prompts:/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Leave out “Harbour at dusk” and answer again" }));
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect((api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1]).toEqual({ retry: true, redo: true, exclude: ["p1"] });
    expect(await screen.findByText(/Looked at 1 of your prompt:/)).toBeTruthy();
  });

  it("says nothing about it when there was nothing", async () => {
    setup(withChat([person(1, "idea"), reply(2, { context: { preferences: 0, examples: [], dropped: 0 } })]));
    await open();
    await screen.findByText("Lovely idea.");
    expect(screen.queryByText(/Using \d/)).toBeNull();
    expect(screen.queryByText(/Looked at/)).toBeNull();
  });

  it("lets the person turn off looking at their saved prompts for a new chat", async () => {
    const api = makeApi({ send: sends(result(reply(2))) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    fireEvent.click(screen.getByLabelText("Look at my saved prompts"));
    send("idea");
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect((api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1]).toMatchObject({ similar: false });
  });
});

// ------------------------------------------------------------------ think and the chat's own settings (criterion 253)
describe("the Think switch and the chat's settings", () => {
  it("starts at the setting and sends the message's own choice", async () => {
    const api = makeApi({ send: sends(result(reply(2))) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    const think = screen.getByRole("combobox", { name: "Think" }) as HTMLSelectElement;
    expect(think.selectedOptions[0].textContent).toBe("Think: On (the setting)");
    fireEvent.change(think, { target: { value: "off" } });
    send("quick one");
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect((api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1]).toMatchObject({ think: false });
  });

  it("names the setting when it is off or the model's own choice", async () => {
    setup(makeApi({ status: vi.fn(async () => ({ ...(await import("./builderTestSupport")).STATUS, think: "model" as const })) }));
    await screen.findByText(/Ollama is ready/);
    expect((screen.getByRole("combobox", { name: "Think" }) as HTMLSelectElement).selectedOptions[0].textContent).toBe("Think: the model's own choice (the setting)");
  });

  it("keeps the kind and detail it was started with, and can change them in a chat that has begun", async () => {
    const api = withChat([person(1, "idea"), reply(2)], { patchChat: vi.fn(async (_i: string, body: object) => info({ ...body })) });
    setup(api);
    await open();
    await screen.findByText("Lovely idea."); // the chat has loaded
    fireEvent.change(await screen.findByRole("combobox", { name: "What the prompt is for" }), { target: { value: "vector" } });
    await waitFor(() => expect(api.patchChat).toHaveBeenCalledWith("c1", { for: "vector" }));
    fireEvent.change(screen.getByRole("combobox", { name: "How detailed the prompt should be" }), { target: { value: "rich" } });
    await waitFor(() => expect(api.patchChat).toHaveBeenCalledWith("c1", { detail: "rich" }));
  });

  it("starts a new chat for what was chosen before the first message", async () => {
    const api = makeApi({ send: sends(result(reply(2))) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    fireEvent.change(screen.getByRole("combobox", { name: "What the prompt is for" }), { target: { value: "music" } });
    fireEvent.change(screen.getByRole("combobox", { name: "How detailed the prompt should be" }), { target: { value: "short" } });
    send("a calm tune");
    await waitFor(() => expect(api.createChat).toHaveBeenCalled());
    expect(api.createChat).toHaveBeenCalledWith({ title: "a calm tune", for: "music", detail: "short", project_id: null, prompt_id: null });
  });
});

// ------------------------------------------------------------------ the list of chats
describe("the list of chats", () => {
  it("opens a chat with its whole conversation, and starts a new one", async () => {
    const api = withChat([person(1, "a lighthouse"), reply(2, { reply: "Lovely idea.", questions: QUESTIONS })]);
    setup(api);
    await open();
    expect(await screen.findByText("Lovely idea.")).toBeTruthy();
    expect(api.getChat).toHaveBeenCalledWith("c1");
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText("Lovely idea.")).toBeNull();
    expect(screen.getAllByText("What's the idea?").length).toBeGreaterThan(0);
    expect(screen.getByRole("textbox", { name: "What's the idea?" })).toBeTruthy();
  });

  it("says when a chat could not be opened", async () => {
    const api = withChat([], { getChat: vi.fn(async () => { throw new ApiError("That chat doesn't exist.", 404); }) });
    setup(api);
    await open();
    expect(await screen.findByText("That chat doesn't exist.")).toBeTruthy();
  });

  it("asks before it deletes, says where it goes, and only then moves it to the bin", async () => {
    const api = withChat([person(1, "idea")]);
    setup(api);
    fireEvent.click(await screen.findByRole("button", { name: "Delete “A lighthouse”" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/moves to Deleted and stays there for 30 days; you can restore it from there/)).toBeTruthy();
    expect(api.binChat).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api.binChat).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Delete “A lighthouse”" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.binChat).toHaveBeenCalledWith("c1"));
  });

  it("goes back to a new chat when the open one is deleted", async () => {
    const api = withChat([person(1, "idea"), reply(2, { reply: "Lovely idea." })]);
    setup(api);
    await open();
    await screen.findByText("Lovely idea.");
    fireEvent.click(screen.getByRole("button", { name: "Delete “A lighthouse”" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Lovely idea.")).toBeNull());
  });

  it("reads the list again when another page changed something", async () => {
    const api = withChat([]);
    const { rerender } = setup(api, { tick: 0 });
    await screen.findByText(/Ollama is ready/);
    const before = api.listChats.mock.calls.length;
    const { BuilderPanel } = await import("./components/BuilderPanel");
    const { CAPS } = await import("./testdata");
    rerender(<BuilderPanel hidden={false} caps={CAPS} projects={[]} push={vi.fn()} onWorking={vi.fn()} api={api} tick={1} />);
    await waitFor(() => expect(api.listChats.mock.calls.length).toBeGreaterThan(before));
  });
});

// ------------------------------------------------------------------ pictures (criterion 244, 254)
describe("pictures", () => {
  const file = () => new File([new Uint8Array([1, 2, 3])], "fox.png", { type: "image/png" });

  it("are attached from the button, shown waiting, numbered, and sent with the message", async () => {
    const api = makeApi({ send: sends(result(reply(2))) });
    setup(api);
    await screen.findByText(/Ollama is ready/);
    fireEvent.change(screen.getByTestId("builder-file-input"), { target: { files: [file()] } });
    const pictures = await screen.findByRole("list", { name: "Pictures" });
    expect(within(pictures).getByAltText("x.png")).toBeTruthy();
    expect(api.attachFile).toHaveBeenCalledTimes(1);
    expect(api.createChat).toHaveBeenCalledTimes(1); // the chat is made for the picture
    send("describe this");
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect((api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1]).toMatchObject({ attachments: ["a1"] });
  });

  it("can be sent with no words at all, and can be taken back", async () => {
    const api = makeApi();
    setup(api);
    await screen.findByText(/Ollama is ready/);
    expect((screen.getByRole("button", { name: /^Send$/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("builder-file-input"), { target: { files: [file()] } });
    await screen.findByRole("list", { name: "Pictures" });
    expect((screen.getByRole("button", { name: /^Send$/ }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Take picture 1 out" }));
    await waitFor(() => expect(api.removeAttachment).toHaveBeenCalledWith("a1"));
    expect(screen.queryByRole("list", { name: "Pictures" })).toBeNull();
  });

  it("are refused in the server's own words", async () => {
    setup(makeApi({ attachFile: vi.fn(async () => { throw new ApiError("That isn't a PNG, JPEG or WebP image.", 415, "bad_picture"); }) }));
    await screen.findByText(/Ollama is ready/);
    fireEvent.change(screen.getByTestId("builder-file-input"), { target: { files: [file()] } });
    expect(await screen.findByText("That isn't a PNG, JPEG or WebP image.")).toBeTruthy();
  });

  it("cannot be attached when the model can't look at pictures, and the button says why", async () => {
    setup(makeApi({ status: vi.fn(async () => ({ ...(await import("./builderTestSupport")).STATUS, vision: false })) }));
    await screen.findByText(/Ollama is ready/);
    const attach = screen.getByRole("button", { name: "Attach a picture" }) as HTMLButtonElement;
    await waitFor(() => expect(attach.disabled).toBe(true));
    expect(attach.title).toBe("The model can't look at pictures, so pictures can't be attached.");
  });

  it("reach the chat when pasted anywhere on the page", async () => {
    const attachRef: { current: ((files: File[]) => void) | null } = { current: null };
    const api = makeApi();
    setup(api, { attachRef });
    await screen.findByText(/Ollama is ready/);
    await waitFor(() => expect(attachRef.current).not.toBeNull());
    attachRef.current!([file()]);
    await waitFor(() => expect(api.attachFile).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("list", { name: "Pictures" })).toBeTruthy();
  });

  it("are shown with the message they went with", async () => {
    const withPicture = { ...person(1, "like this"), attachments: [{ id: "a1", url: "/api/builder/attachments/a1", width: 10, height: 10, name: "fox.png" }] };
    setup(withChat([withPicture, reply(2)]));
    await open();
    expect((await screen.findByAltText("fox.png") as HTMLImageElement).getAttribute("src")).toBe("/api/builder/attachments/a1");
  });
});

// ------------------------------------------------------------------ the tab
describe("the panel", () => {
  it("is hidden when another tab is open, and keeps the chat", async () => {
    const api = withChat([person(1, "idea"), reply(2, { reply: "Lovely idea." })]);
    const { container, rerender } = setup(api);
    await open();
    await screen.findByText("Lovely idea.");
    const { BuilderPanel } = await import("./components/BuilderPanel");
    const { CAPS } = await import("./testdata");
    rerender(<BuilderPanel hidden caps={CAPS} projects={[]} push={vi.fn()} onWorking={vi.fn()} api={api} />);
    expect(container.querySelector("#panel-builder")?.hasAttribute("hidden")).toBe(true);
    expect(container.textContent).toContain("Lovely idea.");
  });
});

export type { ReplyEvent };
