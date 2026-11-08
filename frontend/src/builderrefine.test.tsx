import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { PromptDetail, PromptVersion } from "./builderTypes";
import { STATUS, info, makeApi, reply, result, sends, type Mock } from "./builderTestSupport";
import { BuilderPanel, type BuilderCommand, type BuilderPanelProps } from "./components/BuilderPanel";
import { CAPS } from "./testdata";
import type { Api } from "./useBuilder";

afterEach(cleanup);

// Refine in Prompt builder, Help me write this, a prompt from a picture, and pictures from the history (DESIGN.md §37.2; criteria 241, 243, 244).

const attachments = () => ({ attachImage: vi.fn(async (_chat: string, id: string) => ({ id: `a-${id}`, url: `/a/${id}`, width: 10, height: 10, name: null })) });
const target = (over: Partial<Extract<BuilderCommand, { kind: "refine" }>["target"]> = {}) => ({
  runId: "run1", kind: "images" as const, text: "a lighthouse in a storm", negative: "blur", imageId: "img1", prompt: { id: "p1", project_id: "pr1" }, ...over,
});

function mount(api: Mock & Api, props: Partial<BuilderPanelProps> = {}) {
  const push = vi.fn();
  const base: BuilderPanelProps = { hidden: false, caps: CAPS, projects: [], push, onWorking: vi.fn(), api, ...props };
  const view = render(<BuilderPanel {...base} />);
  const command = (next: BuilderCommand) => view.rerender(<BuilderPanel {...base} command={next} />);
  return { push, command, ...view };
}
const box = () => screen.getByRole("textbox", { name: "The prompt so far" }) as HTMLTextAreaElement;
const typeAndSend = (text: string) => {
  fireEvent.change(screen.getByLabelText(/What's the idea\?|Your reply/), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /^Send$/ }));
};
const sentBody = (api: Mock & Api, n = 0) => (api.send.mock.calls[n] as unknown as [string, Record<string, unknown>])[1];

describe("a command from another part of the page", () => {
  it("opens a saved prompt in the Prompts view", async () => {
    const detail = { id: "p1", title: "Lighthouse", for: "images", favourite: false, tags: [], project_id: null, version: 1, text: "x", created_at: "2026-01-01T00:00:00", updated_at: "2026-01-01T00:00:00", deleted_at: null, purge_at: null,
      thumbs: [], pictures: 0, negative: null, settings: {}, versions: [] as PromptVersion[], chats: [] } as unknown as PromptDetail;
    const api = makeApi({ getPrompt: vi.fn(async () => detail), listPrompts: vi.fn(async () => ({ prompts: [] })) });
    const { command } = mount(api);
    await screen.findByText(/Ollama is ready/);
    command({ seq: 1, kind: "open-prompt", id: "p1" });
    expect(await screen.findByRole("region", { name: "Prompt “Lighthouse”" })).toBeTruthy();
    expect(api.getPrompt).toHaveBeenCalledWith("p1");
    expect(screen.getByRole("radio", { name: "Prompts" }).getAttribute("aria-checked")).toBe("true");
  });

  it("does the same command once, however often the page is drawn", async () => {
    const api = makeApi({ getPrompt: vi.fn(async () => { throw new ApiError("gone", 404, "prompt_not_found"); }) });
    const { command, rerender } = mount(api);
    await screen.findByText(/Ollama is ready/);
    const same: BuilderCommand = { seq: 1, kind: "open-prompt", id: "p1" };
    command(same);
    command(same);
    rerender(<BuilderPanel hidden={false} caps={CAPS} projects={[]} push={vi.fn()} onWorking={vi.fn()} api={api} command={same} />);
    await waitFor(() => expect(api.getPrompt).toHaveBeenCalledTimes(1));
  });
});

describe("Refine in Prompt builder (criterion 241)", () => {
  it("opens a chat for the run's words, linked to its prompt and in its project, with the picture attached, and sends the next message as a refinement of that run", async () => {
    const api = makeApi({ ...attachments(), createChat: vi.fn(async () => info({ id: "c5", title: "New chat", prompt_id: "p1", project_id: "pr1" })), send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { command } = mount(api);
    await screen.findByText(/Ollama is ready/);
    command({ seq: 1, kind: "refine", target: target() });
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    await waitFor(() => expect(api.attachImage).toHaveBeenCalledWith("c5", "img1"));
    expect(api.createChat.mock.calls[0][0]).toMatchObject({ for: "images", prompt_id: "p1", project_id: "pr1" });
    expect(screen.getByText(/Say what is wrong with this picture/)).toBeTruthy();
    expect(await screen.findByRole("list", { name: "Pictures" })).toBeTruthy();
    typeAndSend("the hands look off, too dark");
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect(sentBody(api)).toMatchObject({ intent: "refine", run_id: "run1", text: "the hands look off, too dark", draft: { prompt: "a lighthouse in a storm", negative: "blur" } });
    expect(sentBody(api).attachments).toEqual(["a-img1"]);
    expect(api.createChat).toHaveBeenCalledTimes(1);
  });

  it("for a picture that was not made from a saved prompt, opens a chat linked to nothing and saves nothing", async () => {
    const api = makeApi({ ...attachments(), createChat: vi.fn(async () => info({ id: "c6" })) });
    const { command } = mount(api);
    await screen.findByText(/Ollama is ready/);
    command({ seq: 1, kind: "refine", target: target({ prompt: null }) });
    await waitFor(() => expect(api.attachImage).toHaveBeenCalled());
    expect(api.createChat.mock.calls[0][0]).toMatchObject({ prompt_id: null, project_id: null });
    expect(api.createPrompt).not.toHaveBeenCalled();
    expect(api.addVersion).not.toHaveBeenCalled();
  });

  it("does not attach the picture when the model cannot look at pictures, but still carries the words", async () => {
    const api = makeApi({ ...attachments(), status: vi.fn(async () => ({ ...STATUS, vision: false })) });
    const { command } = mount(api);
    await screen.findByText(/Ollama is ready/);
    command({ seq: 1, kind: "refine", target: target() });
    await waitFor(() => expect(box().value).toBe("a lighthouse in a storm"));
    expect(api.attachImage).not.toHaveBeenCalled();
    expect(api.createChat).not.toHaveBeenCalled(); // the chat is made with the first message
  });

  it("says why the picture could not be attached, and keeps the chat", async () => {
    const api = makeApi({ attachImage: vi.fn(async () => { throw new ApiError("That picture's file is missing.", 404, "file_missing"); }), createChat: vi.fn(async () => info({ id: "c7" })) });
    const { command, push } = mount(api);
    await screen.findByText(/Ollama is ready/);
    command({ seq: 1, kind: "refine", target: target() });
    await waitFor(() => expect(push).toHaveBeenCalledWith("info", "That picture's file is missing."));
    expect(box().value).toBe("a lighthouse in a storm");
  });

  it("leaves the chat that was open and starts a new one", async () => {
    const api = makeApi({
      ...attachments(), listChats: vi.fn(async () => ({ chats: [{ ...info({ id: "old", title: "Old chat" }), last_text: "", message_count: 2 }] })),
      getChat: vi.fn(async () => ({ chat: info({ id: "old", title: "Old chat" }), messages: [], draft: null, pending: [], preferences: 0 })), createChat: vi.fn(async () => info({ id: "fresh" })),
    });
    const { command } = mount(api);
    fireEvent.click(await screen.findByRole("button", { name: /^Old chat/ }));
    await screen.findByRole("textbox", { name: "Chat name" });
    command({ seq: 1, kind: "refine", target: target() });
    await waitFor(() => expect(api.attachImage).toHaveBeenCalledWith("fresh", "img1"));
    expect(api.attachImage).not.toHaveBeenCalledWith("old", expect.anything());
  });

  it("is offered on a picture in a prompt's detail", async () => {
    const run = { run_id: "runZ", mode: "generate", status: "done", seed: 5, created_at: "t", draft: false, images: [{ id: "imgZ", url: "/z", thumb_url: "/zt" }] };
    const version = (n: number, text: string, runs: typeof run[]): PromptVersion => ({ n, text, negative: n === 2 ? "blur" : null, settings: {}, note: null, source: "builder", chat_id: null, message_seq: null, created_at: "2026-01-01T00:00:00", runs });
    const detail = { id: "p1", title: "Lighthouse", for: "images", favourite: false, tags: [], project_id: "pr1", version: 2, text: "new", created_at: "2026-01-01T00:00:00", updated_at: "2026-01-01T00:00:00", deleted_at: null, purge_at: null,
      thumbs: [], pictures: 1, negative: "blur", settings: {}, versions: [version(2, "new words", []), version(1, "old words", [run])], chats: [] } as unknown as PromptDetail;
    const api = makeApi({ ...attachments(), listPrompts: vi.fn(async () => ({ prompts: [{ ...detail, version: 2, versions: 2 }] })), getPrompt: vi.fn(async () => detail), createChat: vi.fn(async () => info({ id: "c8" })) });
    mount(api);
    fireEvent.click(await screen.findByRole("radio", { name: "Prompts" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Lighthouse/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Refine in Prompt builder" }));
    await waitFor(() => expect(api.attachImage).toHaveBeenCalledWith("c8", "imgZ"));
    expect(box().value).toBe("old words"); // the words of the version the picture was made from
    expect(api.createChat.mock.calls[0][0]).toMatchObject({ prompt_id: "p1", project_id: "pr1" });
    expect(screen.getByRole("radio", { name: "Chats" }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("Help me write this (criterion 243)", () => {
  it("opens a chat For Edit with the tray's pictures attached in order, in one chat, and sends the next message as help with an edit", async () => {
    const api = makeApi({ ...attachments(), createChat: vi.fn(async () => info({ id: "c9", for: "edit" })), send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { command } = mount(api);
    await screen.findByText(/Ollama is ready/);
    command({ seq: 1, kind: "help-edit", pictures: ["t1", "t2", "t3"] });
    await waitFor(() => expect(api.attachImage).toHaveBeenCalledTimes(3));
    expect(api.attachImage.mock.calls.map((c) => c[1])).toEqual(["t1", "t2", "t3"]);
    expect(api.attachImage.mock.calls.map((c) => c[0])).toEqual(["c9", "c9", "c9"]);
    expect(api.createChat).toHaveBeenCalledTimes(1);
    expect(api.createChat.mock.calls[0][0]).toMatchObject({ for: "edit", prompt_id: null });
    expect(screen.getByText(/They are numbered in order: image 1, image 2/)).toBeTruthy();
    typeAndSend("put the dog from image 1 into the scene from image 2");
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect(sentBody(api)).toMatchObject({ intent: "help_edit" });
    expect(sentBody(api).attachments).toEqual(["a-t1", "a-t2", "a-t3"]);
  });

  it("attaches the pictures even when the model cannot look at them, so that it can work from their count", async () => {
    const api = makeApi({ ...attachments(), status: vi.fn(async () => ({ ...STATUS, vision: false })), createChat: vi.fn(async () => info({ id: "c10", for: "edit" })) });
    const { command } = mount(api);
    await screen.findByText(/Ollama is ready/);
    command({ seq: 1, kind: "help-edit", pictures: ["t1", "t2"] });
    await waitFor(() => expect(api.attachImage).toHaveBeenCalledTimes(2));
  });
});

describe("pictures from the history (criterion 244)", () => {
  const history = [{ id: "h1", thumbUrl: "/t/h1", label: "Picture 1, a lighthouse" }, { id: "h2", thumbUrl: "/t/h2", label: "Picture 2, a harbour" }];

  it("are offered in a picker, and the one chosen is attached to the chat", async () => {
    const api = makeApi({ ...attachments(), createChat: vi.fn(async () => info({ id: "c11" })) });
    mount(api, { history });
    fireEvent.click(await screen.findByRole("button", { name: "Pick from my history" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByRole("button", { name: /^Picture \d/ })).toHaveLength(2);
    fireEvent.click(within(dialog).getByRole("button", { name: "Picture 2, a harbour" }));
    await waitFor(() => expect(api.attachImage).toHaveBeenCalledWith("c11", "h2"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByRole("list", { name: "Pictures" })).toBeTruthy();
  });

  it("are off with the reason when there is no history, or when the model cannot look at pictures", async () => {
    mount(makeApi());
    const button = (await screen.findByRole("button", { name: "Pick from my history" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe("There are no pictures in the history yet.");
    cleanup();
    mount(makeApi({ status: vi.fn(async () => ({ ...STATUS, vision: false })) }), { history });
    await waitFor(() => expect((screen.getByRole("button", { name: "Pick from my history" }) as HTMLButtonElement).disabled).toBe(true));
  });

  it("say why one could not be attached, in the chat", async () => {
    const api = makeApi({ attachImage: vi.fn(async () => { throw new ApiError("That picture's file is missing.", 404, "file_missing"); }), createChat: vi.fn(async () => info({ id: "c12" })) });
    mount(api, { history });
    fireEvent.click(await screen.findByRole("button", { name: "Pick from my history" }));
    fireEvent.click(await screen.findByRole("button", { name: "Picture 1, a lighthouse" }));
    expect((await screen.findByText("That picture's file is missing.")).getAttribute("role")).toBe("alert");
  });
});

describe("a prompt from a picture (criterion 244)", () => {
  it("is asked for with Describe as a prompt, once a picture is attached", async () => {
    const api = makeApi({ ...attachments(), createChat: vi.fn(async () => info({ id: "c13" })), send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    mount(api, { history: [{ id: "h1", thumbUrl: "/t/h1", label: "Picture 1" }] });
    expect(screen.queryByRole("button", { name: "Describe as a prompt" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Pick from my history" }));
    fireEvent.click(await screen.findByRole("button", { name: "Picture 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "Describe as a prompt" }));
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    expect(sentBody(api)).toMatchObject({ intent: "describe", attachments: ["a-h1"] });
  });
});
