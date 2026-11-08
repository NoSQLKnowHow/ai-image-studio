import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { ChatListItem, Comparison, Preference, PromptCard, PromptDetail, PromptVersion } from "./builderTypes";
import { ZERO_COUNTS, info, makeApi, setup } from "./builderTestSupport";
import type { Project } from "./types";

afterEach(cleanup);

// The library: the chat list and its bin, the prompts list with search and filters, a prompt opened with its versions, Compare, Flesh out, and the preferences
// (DESIGN.md §37.2, criteria 229-233, 245-247).

const PROJECTS = [{ id: "pr1", name: "Harbour" }, { id: "pr2", name: "Forest" }] as unknown as Project[];
const card = (over: Partial<PromptCard> = {}): PromptCard => ({
  id: "p1", title: "Lighthouse", for: "images", favourite: false, tags: ["sea"], project_id: null, version: 2, versions: 2, text: "a lighthouse at dawn in fog",
  created_at: "2026-01-01T10:00:00", updated_at: "2026-01-02T10:00:00", deleted_at: null, purge_at: null, thumbs: [], pictures: 0, ...over,
});
const version = (n: number, text: string, over: Partial<PromptVersion> = {}): PromptVersion => ({
  n, text, negative: null, settings: {}, note: null, source: "builder", chat_id: null, message_seq: null, created_at: `2026-01-0${n}T10:00:00`, runs: [], ...over,
});
const detail = (over: Partial<PromptDetail> = {}): PromptDetail => {
  const { versions: _versions, ...rest } = card();
  return { ...rest, negative: null, settings: {}, versions: [version(2, "a lighthouse at dawn in fog", { settings: { shape: "3:2" } }), version(1, "a lighthouse at dusk", { settings: { shape: "1:1" } })], chats: [], ...over };
};
const chatRow = (over: Partial<ChatListItem> = {}): ChatListItem => ({ ...info(), last_text: "", message_count: 2, ...over }) as ChatListItem;
const pref = (over: Partial<Preference> = {}): Preference => ({ id: "f1", text: "photographic, natural light", project_id: null, enabled: true, source: "hand", position: 0, created_at: "t", orphaned: false, ...over });
const counts = (over: Partial<typeof ZERO_COUNTS> = {}) => ({ ...ZERO_COUNTS, ...over });

const radio = (name: RegExp | string) => screen.getByRole("radio", { name });
const toPrompts = async () => fireEvent.click(await screen.findByRole("radio", { name: "Prompts" }));
const openCard = async (title = "Lighthouse") => fireEvent.click(await screen.findByRole("button", { name: new RegExp(`^${title}`) }));
const lastCall = (fn: { mock: { calls: unknown[][] } }) => fn.mock.calls[fn.mock.calls.length - 1];

// ------------------------------------------------------------------ chats and the bin (criterion 246)
describe("the chat list", () => {
  it("asks before deleting a chat, says how long it stays, and does nothing when cancelled", async () => {
    const api = makeApi({ listChats: vi.fn(async () => ({ chats: [chatRow()] })), binChat: vi.fn(async () => info()) });
    setup(api);
    fireEvent.click(await screen.findByRole("button", { name: "Delete “A lighthouse”" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/stays there for 30 days/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(api.binChat).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete “A lighthouse”" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.binChat).toHaveBeenCalledWith("c1"));
  });

  it("reads the bin when asked, and restores or deletes for good from it", async () => {
    const api = makeApi({
      counts: vi.fn(async () => counts({ chats: { all: 1, deleted: 2, no_project: 0, projects: {} } })),
      listChats: vi.fn(async (p?: { deleted?: boolean }) => ({ chats: p?.deleted ? [chatRow({ id: "c9", title: "Old storm", deleted_at: "t" })] : [chatRow()] })),
      restoreChat: vi.fn(async () => info()), deleteChat: vi.fn(async () => ({ deleted: "c9" })), emptyBin: vi.fn(async () => ({ deleted: 3 })),
    });
    const { push } = setup(api);
    fireEvent.click(await screen.findByRole("radio", { name: "Deleted (2)" }));
    await screen.findByText("Old storm");
    expect(lastCall(api.listChats)[0]).toEqual({ deleted: true });
    expect(screen.queryByRole("button", { name: "New chat" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Restore “Old storm”" }));
    await waitFor(() => expect(api.restoreChat).toHaveBeenCalledWith("c9"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("info", "Restored “Old storm”."));
    fireEvent.click(screen.getByRole("button", { name: "Delete “Old storm” forever" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete forever" }));
    await waitFor(() => expect(api.deleteChat).toHaveBeenCalledWith("c9"));
    fireEvent.click(screen.getByRole("button", { name: "Empty bin" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Empty it" }));
    await waitFor(() => expect(api.emptyBin).toHaveBeenCalled());
    await waitFor(() => expect(push).toHaveBeenCalledWith("info", "Deleted 3 for good."));
  });

  it("will not empty an empty bin", async () => {
    setup(makeApi());
    fireEvent.click(await screen.findByRole("radio", { name: /^Deleted/ }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Empty bin" }) as HTMLButtonElement).disabled).toBe(true));
  });

  it("says plainly when the bin is empty and when there are no chats", async () => {
    setup(makeApi());
    expect(await screen.findByText(/No chats yet/)).toBeTruthy();
    fireEvent.click(radio(/^Deleted/));
    expect(await screen.findByText("Nothing in Deleted.")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ the prompts list (criterion 245)
describe("the prompts list", () => {
  it("shows each prompt with its newest words, tags, version and project, and the counts beside the filters", async () => {
    const api = makeApi({
      listPrompts: vi.fn(async () => ({ prompts: [card({ project_id: "pr1", thumbs: [{ id: "i1", url: "/i1" }] }), card({ id: "p2", title: "Forest path", tags: [], version: 1, text: "a path in a forest" })] })),
      counts: vi.fn(async () => counts({ prompts: { all: 2, favourites: 1, deleted: 3, no_project: 1, projects: { pr1: 1 }, tags: { sea: 1 } } })),
    });
    setup(api, { projects: PROJECTS });
    await toPrompts();
    expect(await screen.findByText("a lighthouse at dawn in fog")).toBeTruthy();
    expect(screen.getByText(/Images · v2 · Harbour/)).toBeTruthy();
    expect(screen.getByText("sea", { selector: ".badge" })).toBeTruthy();
    expect(screen.getByText(/Images · v1$/)).toBeTruthy();
    expect(radio("All (2)")).toBeTruthy();
    expect(radio("★ (1)")).toBeTruthy();
    expect(radio("Deleted (3)")).toBeTruthy();
    expect(screen.getByRole("option", { name: "No project (1)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Harbour (1)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Forest (0)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "sea (1)" })).toBeTruthy();
  });

  it("asks the server for what is typed, once the typing pauses, and for each filter and the sort", async () => {
    const api = makeApi({ listPrompts: vi.fn(async () => ({ prompts: [card()] })), counts: vi.fn(async () => counts({ prompts: { ...ZERO_COUNTS.prompts, tags: { sea: 1 } } })) });
    setup(api, { projects: PROJECTS });
    await toPrompts();
    await screen.findByText("Lighthouse");
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "  light " } });
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "  lighthouse " } });
    await waitFor(() => expect(lastCall(api.listPrompts)[0]).toMatchObject({ q: "lighthouse" }));
    expect(api.listPrompts.mock.calls.filter((c) => (c[0] as { q?: string }).q === "light")).toHaveLength(0); // the half-typed word was never asked
    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "sea" } });
    await waitFor(() => expect(lastCall(api.listPrompts)[0]).toMatchObject({ q: "lighthouse", tag: "sea" }));
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "pr2" } });
    await waitFor(() => expect(lastCall(api.listPrompts)[0]).toMatchObject({ project: "pr2" }));
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "title" } });
    await waitFor(() => expect(lastCall(api.listPrompts)[0]).toMatchObject({ sort: "title" }));
    fireEvent.click(radio(/^★/));
    await waitFor(() => expect(lastCall(api.listPrompts)[0]).toMatchObject({ favourite: true, deleted: false }));
    fireEvent.click(radio(/^Deleted/));
    await waitFor(() => expect(lastCall(api.listPrompts)[0]).toMatchObject({ favourite: false, deleted: true }));
  });

  it("stars and unstars a prompt without opening it", async () => {
    const api = makeApi({ listPrompts: vi.fn(async () => ({ prompts: [card()] })), patchPrompt: vi.fn(async () => detail({ favourite: true })) });
    setup(api);
    await toPrompts();
    fireEvent.click(await screen.findByRole("button", { name: "Star “Lighthouse”" }));
    await waitFor(() => expect(api.patchPrompt).toHaveBeenCalledWith("p1", { favourite: true }));
    expect(api.getPrompt).not.toHaveBeenCalled();
  });

  it("says why the list is empty: nothing saved, nothing matching, nothing deleted", async () => {
    const api = makeApi();
    setup(api);
    await toPrompts();
    expect(await screen.findByText("No saved prompts yet. Save one from a chat.")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });
    expect(await screen.findByText("No prompt matches.")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    fireEvent.click(radio(/^Deleted/));
    expect(await screen.findByText("Nothing in Deleted.")).toBeTruthy();
  });

  it("shows a prompt in the bin with when it goes, and cannot star it", async () => {
    setup(makeApi({ listPrompts: vi.fn(async () => ({ prompts: [card({ deleted_at: "2026-02-01T00:00:00", purge_at: "2026-03-03T00:00:00" })] })) }));
    await toPrompts();
    expect(await screen.findByText("In Deleted, gone for good 2026-03-03.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Star “Lighthouse”" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says when the list could not be read", async () => {
    setup(makeApi({ listPrompts: vi.fn(async () => { throw new ApiError("The library could not be read.", 500, "server_error"); }) }));
    await toPrompts();
    expect((await screen.findByRole("alert")).textContent).toContain("The library could not be read.");
  });
});

// ------------------------------------------------------------------ a prompt opened (criteria 229-231)
const openedApi = (over: Parameters<typeof makeApi>[0] = {}) =>
  makeApi({ listPrompts: vi.fn(async () => ({ prompts: [card()] })), getPrompt: vi.fn(async () => detail()), ...over });

describe("a prompt opened", () => {
  it("lists every version newest first with the current one marked, and its settings", async () => {
    const api = openedApi();
    setup(api);
    await toPrompts();
    await openCard();
    const list = await screen.findByRole("list", { name: "Versions" });
    const heads = within(list).getAllByText(/^Version \d$/).map((e) => e.textContent);
    expect(heads).toEqual(["Version 2", "Version 1"]);
    expect(within(list).getAllByText("Current")).toHaveLength(1);
    expect(within(list).getByText("shape: 3:2")).toBeTruthy();
    expect(within(list).getByText("shape: 1:1")).toBeTruthy();
    expect(screen.getByText("2 versions. Saving a change adds the next one; nothing is written over. Choose two to compare.")).toBeTruthy();
    expect(api.getPrompt).toHaveBeenCalledWith("p1");
  });

  it("makes an older version the current one by asking for a copy, and says what happened", async () => {
    const api = openedApi({ makeCurrent: vi.fn(async () => ({ created: true, message: "Version 1 is now the current one, as version 3.", version: 3, prompt: detail() })) });
    const { push } = setup(api);
    await toPrompts();
    await openCard();
    await screen.findByRole("list", { name: "Versions" });
    expect(screen.getAllByRole("button", { name: "Make this the current one" })).toHaveLength(1); // the newest has no such button
    fireEvent.click(screen.getByRole("button", { name: "Make this the current one" }));
    await waitFor(() => expect(api.makeCurrent).toHaveBeenCalledWith("p1", 1));
    await waitFor(() => expect(push).toHaveBeenCalledWith("success", "Version 1 is now the current one, as version 3."));
  });

  it("compares two versions, oldest first however they were ticked, with the changed words and settings marked in text", async () => {
    const comparison: Comparison = {
      a: { n: 1, note: null, source: "builder", created_at: "t" }, b: { n: 2, note: null, source: "builder", created_at: "t" }, changed: true,
      text: [{ op: "same", text: "a lighthouse" }, { op: "del", text: "at dusk" }, { op: "add", text: "at dawn in fog" }], negative: [],
      settings: [{ key: "shape", before: "1:1", after: "3:2", status: "changed" }, { key: "steps", before: 4, after: 4, status: "same" }, { key: "seed", before: null, after: 7, status: "added" }],
    };
    const api = openedApi({ compare: vi.fn(async () => comparison) });
    setup(api);
    await toPrompts();
    await openCard();
    await screen.findByRole("list", { name: "Versions" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare version 2" }));
    expect(api.compare).not.toHaveBeenCalled(); // one is not a comparison
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare version 1" }));
    await waitFor(() => expect(api.compare).toHaveBeenCalledWith("p1", 1, 2));
    const view = await screen.findByRole("region", { name: "Version 1 compared with version 2" });
    fireEvent.click(screen.getByRole("radio", { name: "Prompts" })); // drawing the panel again is not a reason to ask again
    expect(api.compare).toHaveBeenCalledTimes(1);
    expect(within(view).getByLabelText("removed: at dusk").tagName).toBe("DEL");
    expect(within(view).getByLabelText("added: at dawn in fog").tagName).toBe("INS");
    const table = within(view).getByRole("table", { name: "Settings compared" });
    expect(within(table).getByRole("row", { name: /shape 1:1 3:2 changed/ })).toBeTruthy();
    expect(within(table).getByRole("row", { name: /steps 4 4 same/ })).toBeTruthy();
    expect(within(table).getByRole("row", { name: /seed — 7 added/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare version 1" })); // unticking one ends the comparison
    await waitFor(() => expect(screen.queryByRole("region", { name: /compared with/ })).toBeNull());
  });

  it("says when two versions are the same, and when they could not be compared", async () => {
    const same: Comparison = { a: { n: 1, note: null, source: "builder", created_at: "t" }, b: { n: 2, note: null, source: "builder", created_at: "t" }, changed: false, text: [{ op: "same", text: "x" }], negative: [], settings: [] };
    const api = openedApi({ compare: vi.fn().mockResolvedValueOnce(same).mockRejectedValueOnce(new ApiError("That version is gone.", 404, "not_found")) });
    setup(api);
    await toPrompts();
    await openCard();
    await screen.findByRole("list", { name: "Versions" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare version 1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare version 2" }));
    expect(await screen.findByText("No differences.")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare version 2" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare version 2" }));
    expect((await screen.findByRole("alert")).textContent).toContain("That version is gone.");
  });

  it("renames, stars, retags, refiles and changes what a prompt is for, each as one change", async () => {
    const api = openedApi({ patchPrompt: vi.fn(async (_id: string, body: object) => detail(body as Partial<PromptDetail>)) });
    setup(api, { projects: PROJECTS });
    await toPrompts();
    await openCard();
    const title = await screen.findByRole("textbox", { name: "Title" });
    fireEvent.change(title, { target: { value: "Old lighthouse" } });
    fireEvent.blur(title);
    await waitFor(() => expect(api.patchPrompt).toHaveBeenLastCalledWith("p1", { title: "Old lighthouse" }));
    fireEvent.click(screen.getByRole("button", { name: "Star this prompt" }));
    await waitFor(() => expect(api.patchPrompt).toHaveBeenLastCalledWith("p1", { favourite: true }));
    const tags = screen.getByRole("textbox", { name: "Tags" });
    fireEvent.change(tags, { target: { value: "sea, storm ,," } });
    fireEvent.blur(tags);
    await waitFor(() => expect(api.patchPrompt).toHaveBeenLastCalledWith("p1", { tags: ["sea", "storm"] }));
    fireEvent.change(screen.getByLabelText("Project of this prompt"), { target: { value: "pr1" } });
    await waitFor(() => expect(api.patchPrompt).toHaveBeenLastCalledWith("p1", { project_id: "pr1" }));
    fireEvent.change(screen.getByLabelText("For"), { target: { value: "music" } });
    await waitFor(() => expect(api.patchPrompt).toHaveBeenLastCalledWith("p1", { for: "music" }));
    expect(api.patchPrompt).toHaveBeenCalledTimes(5);
  });

  it("does not save a title that was left as it was or emptied", async () => {
    const api = openedApi({ patchPrompt: vi.fn(async () => detail()) });
    setup(api);
    await toPrompts();
    await openCard();
    const title = (await screen.findByRole("textbox", { name: "Title" })) as HTMLInputElement;
    fireEvent.blur(title);
    fireEvent.change(title, { target: { value: "   " } });
    fireEvent.blur(title);
    expect(title.value).toBe("Lighthouse");
    expect(api.patchPrompt).not.toHaveBeenCalled();
  });

  it("offers to file the runs of earlier pictures after a project is chosen, and does so only when asked", async () => {
    const api = openedApi({ patchPrompt: vi.fn(async () => detail({ project_id: "pr1", unfiled_runs: 3 })), fileRuns: vi.fn(async () => ({ filed: ["r1", "r2", "r3"] })) });
    const { push } = setup(api, { projects: PROJECTS });
    await toPrompts();
    await openCard();
    await screen.findByRole("list", { name: "Versions" });
    fireEvent.change(screen.getByLabelText("Project of this prompt"), { target: { value: "pr1" } });
    await waitFor(() => expect(push).toHaveBeenCalled());
    const [kind, text, action] = lastCall(push) as ["info", string, { label: string; run: () => void }];
    expect(kind).toBe("info");
    expect(text).toBe("Filed in a project. 3 earlier pictures' runs are where they were.");
    expect(action.label).toBe("Also file its 3 pictures' runs");
    expect(api.fileRuns).not.toHaveBeenCalled();
    action.run();
    await waitFor(() => expect(api.fileRuns).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(push).toHaveBeenLastCalledWith("success", "Filed 3 runs in the project."));
  });

  it("says it in the singular for one earlier run", async () => {
    const api = openedApi({ patchPrompt: vi.fn(async () => detail({ project_id: "pr1", unfiled_runs: 1 })) });
    const { push } = setup(api, { projects: PROJECTS });
    await toPrompts();
    await openCard();
    await screen.findByRole("list", { name: "Versions" });
    fireEvent.change(screen.getByLabelText("Project of this prompt"), { target: { value: "pr1" } });
    await waitFor(() => expect(push).toHaveBeenCalled());
    const [, text, action] = lastCall(push) as ["info", string, { label: string }];
    expect(text).toBe("Filed in a project. 1 earlier picture's run is where it was.");
    expect(action.label).toBe("Also file its 1 picture's run");
  });

  it("says why a change could not be made, beside the prompt", async () => {
    const api = openedApi({ patchPrompt: vi.fn(async () => { throw new ApiError("That title is too long.", 422, "bad"); }) });
    setup(api);
    await toPrompts();
    await openCard();
    fireEvent.click(await screen.findByRole("button", { name: "Star this prompt" }));
    expect((await screen.findByRole("alert")).textContent).toContain("That title is too long.");
  });

  it("asks before moving a prompt to Deleted, then closes it", async () => {
    const api = openedApi({ binPrompt: vi.fn(async () => detail({ deleted_at: "t" })) });
    setup(api);
    await toPrompts();
    await openCard();
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/stay there for 30 days.*pictures made from it are not touched/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.binPrompt).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(screen.queryByRole("region", { name: /^Prompt “/ })).toBeNull());
  });

  it("shows a deleted prompt as read-only, with Restore and Delete forever", async () => {
    const binned = detail({ deleted_at: "2026-02-01T00:00:00", purge_at: "2026-03-03T00:00:00" });
    const api = openedApi({ getPrompt: vi.fn(async () => binned), restorePrompt: vi.fn(async () => detail()), deletePrompt: vi.fn(async () => ({ deleted: "p1" })) });
    const { push } = setup(api);
    await toPrompts();
    await openCard();
    expect(await screen.findByText("In Deleted, gone for good 2026-03-03. Its pictures are untouched.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Flesh out" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Make this the current one" })).toBeNull();
    expect((screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(api.restorePrompt).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("info", "Restored."));
    fireEvent.click(screen.getByRole("button", { name: "Delete forever" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete forever" }));
    await waitFor(() => expect(api.deletePrompt).toHaveBeenCalledWith("p1"));
  });

  it("lists the chats about it and opens one", async () => {
    const api = openedApi({ getPrompt: vi.fn(async () => detail({ chats: [{ id: "c7", title: "Fog talk", updated_at: "t" }] })), getChat: vi.fn(async () => ({ chat: info({ id: "c7", title: "Fog talk" }), messages: [], draft: null, pending: [], preferences: 0 })) });
    setup(api);
    await toPrompts();
    await openCard();
    fireEvent.click(await screen.findByRole("button", { name: "Fog talk" }));
    await waitFor(() => expect(api.getChat).toHaveBeenCalledWith("c7"));
    await waitFor(() => expect(screen.queryByRole("region", { name: /^Prompt “/ })).toBeNull());
    expect(radio("Chats").getAttribute("aria-checked")).toBe("true");
  });

  it("goes back to the chat", async () => {
    setup(openedApi());
    await toPrompts();
    await openCard();
    fireEvent.click(await screen.findByRole("button", { name: "← Back to the chat" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: /^Prompt “/ })).toBeNull());
  });

  it("says when it could not be opened", async () => {
    setup(openedApi({ getPrompt: vi.fn(async () => { throw new ApiError("That prompt is gone.", 404, "prompt_not_found"); }) }));
    await toPrompts();
    await openCard();
    expect((await screen.findByRole("alert")).textContent).toContain("That prompt is gone.");
  });

  it("shows the pictures made from a version, marks quick tries as drafts, and says when a run has none yet", async () => {
    const runs = [
      { run_id: "r1", mode: "generate", status: "done", seed: 1, created_at: "t", draft: false, images: [{ id: "i1", url: "/i1", thumb_url: "/t1" }] },
      { run_id: "r2", mode: "generate", status: "done", seed: 2, created_at: "t", draft: true, images: [{ id: "i2", url: "/i2", thumb_url: "/t2" }] },
      { run_id: "r3", mode: "generate", status: "running", seed: 3, created_at: "t", draft: false, images: [] },
    ];
    const withRuns = detail({ versions: [version(2, "new", { runs }), version(1, "old")] });
    setup(openedApi({ getPrompt: vi.fn(async () => withRuns) }));
    await toPrompts();
    await openCard();
    const list = await screen.findByRole("list", { name: "Pictures made from this version" });
    expect(within(list).getByAltText("Made from this version")).toBeTruthy();
    expect(within(list).getByAltText("Made from this version (quick try)")).toBeTruthy();
    expect(within(list).getByText("generate · running")).toBeTruthy();
    expect(within(list).getAllByText("Draft")).toHaveLength(1);
  });
});

// ------------------------------------------------------------------ Flesh out (criterion 231)
describe("Flesh out", () => {
  it("starts a chat about the prompt, in its project and for its kind, and sends its newest version as the draft", async () => {
    const forest = detail({ for: "vector", project_id: "pr2", negative: "blur", versions: [version(3, "a forest path", { negative: "blur", settings: { shape: "3:2" } }), version(1, "a path")] });
    const api = openedApi({ getPrompt: vi.fn(async () => forest), createChat: vi.fn(async () => info({ id: "c5", title: "New chat", for: "vector", project_id: "pr2", prompt_id: "p1" })) });
    setup(api, { projects: PROJECTS });
    await toPrompts();
    await openCard();
    fireEvent.click(await screen.findByRole("button", { name: "Flesh out" }));
    await waitFor(() => expect(api.createChat).toHaveBeenCalled());
    expect(api.createChat.mock.calls[0][0]).toMatchObject({ for: "vector", project_id: "pr2", prompt_id: "p1" });
    await waitFor(() => expect(api.send).toHaveBeenCalled());
    const body = (api.send.mock.calls[0] as unknown as [string, Record<string, unknown>])[1];
    expect(body).toMatchObject({ intent: "flesh_out", draft: { prompt: "a forest path", negative: "blur", settings: { shape: "3:2" } } });
    expect(api.createChat).toHaveBeenCalledTimes(1);
    expect(api.send).toHaveBeenCalledTimes(1);
    expect(radio("Chats").getAttribute("aria-checked")).toBe("true");
  });
});

// ------------------------------------------------------------------ preferences (criterion 233)
describe("preferences", () => {
  const toPrefs = async () => fireEvent.click(await screen.findByRole("radio", { name: "Preferences" }));

  it("lists the notes, with a switch, where each applies, and says how many are on", async () => {
    const api = makeApi({
      listPreferences: vi.fn(async () => ({ preferences: [pref(), pref({ id: "f2", text: "no people", project_id: "pr1", enabled: false })] })),
      counts: vi.fn(async () => counts({ preferences: { all: 2, enabled: 1, orphaned: 0 } })),
    });
    setup(api, { projects: PROJECTS });
    await toPrefs();
    expect(await screen.findByText("photographic, natural light")).toBeTruthy();
    expect(screen.getByText(/1 on of 2/)).toBeTruthy();
    expect((screen.getByRole("checkbox", { name: "Turn off: photographic, natural light" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Turn on: no people" }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText("Where “no people” applies") as HTMLSelectElement).value).toBe("pr1");
    expect((screen.getByLabelText("Where “photographic, natural light” applies") as HTMLSelectElement).value).toBe("everywhere");
  });

  it("adds a note, everywhere or in one project, and clears the box; an empty note cannot be added", async () => {
    const api = makeApi();
    setup(api, { projects: PROJECTS });
    await toPrefs();
    const box = (await screen.findByRole("textbox", { name: "New note" })) as HTMLInputElement;
    expect((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(box, { target: { value: "  soft light " } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(api.addPreference).toHaveBeenCalledWith({ text: "soft light", project_id: null }));
    await waitFor(() => expect(box.value).toBe(""));
    fireEvent.change(box, { target: { value: "moss" } });
    fireEvent.change(screen.getByLabelText("Where the new note applies"), { target: { value: "pr2" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(api.addPreference).toHaveBeenLastCalledWith({ text: "moss", project_id: "pr2" }));
  });

  it("keeps what was typed and says why when a note cannot be added", async () => {
    const api = makeApi({ addPreference: vi.fn(async () => { throw new ApiError("You have 50 notes already.", 409, "limit"); }) });
    setup(api);
    await toPrefs();
    const box = (await screen.findByRole("textbox", { name: "New note" })) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "one more" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect((await screen.findByRole("alert")).textContent).toContain("You have 50 notes already.");
    expect(box.value).toBe("one more");
  });

  it("switches a note on and off, edits it, moves it to a project, and deletes it", async () => {
    const api = makeApi({ listPreferences: vi.fn(async () => ({ preferences: [pref()] })), patchPreference: vi.fn(async () => pref()), deletePreference: vi.fn(async () => ({ deleted: "f1" })) });
    setup(api, { projects: PROJECTS });
    await toPrefs();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Turn off: photographic, natural light" }));
    await waitFor(() => expect(api.patchPreference).toHaveBeenLastCalledWith("f1", { enabled: false }));
    fireEvent.click(screen.getByRole("button", { name: "Edit “photographic, natural light”" }));
    const edit = screen.getByRole("textbox", { name: "Edit the note" });
    fireEvent.change(edit, { target: { value: "photographic, golden hour" } });
    fireEvent.blur(edit);
    await waitFor(() => expect(api.patchPreference).toHaveBeenLastCalledWith("f1", { text: "photographic, golden hour" }));
    fireEvent.change(screen.getByLabelText("Where “photographic, natural light” applies"), { target: { value: "pr1" } });
    await waitFor(() => expect(api.patchPreference).toHaveBeenLastCalledWith("f1", { project_id: "pr1" }));
    fireEvent.change(screen.getByLabelText("Where “photographic, natural light” applies"), { target: { value: "everywhere" } });
    await waitFor(() => expect(api.patchPreference).toHaveBeenLastCalledWith("f1", { project_id: null }));
    fireEvent.click(screen.getByRole("button", { name: "Delete “photographic, natural light”" }));
    await waitFor(() => expect(api.deletePreference).toHaveBeenCalledWith("f1"));
  });

  it("leaves a note alone when its edit is cancelled with Escape or changes nothing", async () => {
    const api = makeApi({ listPreferences: vi.fn(async () => ({ preferences: [pref()] })), patchPreference: vi.fn(async () => pref()) });
    setup(api);
    await toPrefs();
    fireEvent.click(await screen.findByRole("button", { name: "Edit “photographic, natural light”" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Edit the note" }), { target: { value: "something else" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Edit the note" }), { key: "Escape" });
    expect(screen.getByText("photographic, natural light")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit “photographic, natural light”" }));
    fireEvent.blur(screen.getByRole("textbox", { name: "Edit the note" }));
    expect(api.patchPreference).not.toHaveBeenCalled();
  });

  it("lists notes of a deleted project apart, off, with nowhere to apply until they are given a place", async () => {
    const api = makeApi({
      listPreferences: vi.fn(async () => ({ preferences: [pref(), pref({ id: "f3", text: "harbour mood", project_id: null, enabled: false, orphaned: true })] })),
      counts: vi.fn(async () => counts({ preferences: { all: 2, enabled: 1, orphaned: 1 } })),
    });
    setup(api, { projects: PROJECTS });
    await toPrefs();
    const apart = await screen.findByRole("list", { name: "Notes no longer in a project" });
    expect(within(apart).getByText("harbour mood")).toBeTruthy();
    const where = within(apart).getByLabelText("Where “harbour mood” applies") as HTMLSelectElement;
    expect(where.value).toBe("");
    expect(within(where).getByRole("option", { name: "No longer in a project" })).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Notes" })).queryByText("harbour mood")).toBeNull();
    expect(screen.getByText(/1 no longer in a project/)).toBeTruthy();
  });

  it("marks a note the builder suggested", async () => {
    setup(makeApi({ listPreferences: vi.fn(async () => ({ preferences: [pref({ source: "suggestion" })] })) }));
    await toPrefs();
    expect(await screen.findByText("(suggested)")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ a chat's name and project
describe("a chat's name and project", () => {
  const withChat = (over: Parameters<typeof makeApi>[0] = {}) => makeApi({
    listChats: vi.fn(async () => ({ chats: [chatRow()] })), getChat: vi.fn(async () => ({ chat: info(), messages: [], draft: null, pending: [], preferences: 0 })), ...over,
  });
  const openIt = async () => fireEvent.click(await screen.findByRole("button", { name: /^A lighthouse/ }));

  it("renames a chat when the field is left, and not when it is left as it was", async () => {
    const api = withChat({ patchChat: vi.fn(async (_id: string, body: object) => info(body as object)) });
    setup(api);
    await openIt();
    const name = (await screen.findByRole("textbox", { name: "Chat name" })) as HTMLInputElement;
    fireEvent.blur(name);
    expect(api.patchChat).not.toHaveBeenCalled();
    fireEvent.change(name, { target: { value: "Harbour light" } });
    fireEvent.blur(name);
    await waitFor(() => expect(api.patchChat).toHaveBeenCalledWith("c1", { title: "Harbour light" }));
  });

  it("files a chat in a project, or takes it out", async () => {
    const api = withChat({ patchChat: vi.fn(async (_id: string, body: object) => info(body as object)) });
    setup(api, { projects: PROJECTS });
    await openIt();
    fireEvent.change(await screen.findByLabelText("Project of this chat"), { target: { value: "pr1" } });
    await waitFor(() => expect(api.patchChat).toHaveBeenLastCalledWith("c1", { project_id: "pr1" }));
    fireEvent.change(screen.getByLabelText("Project of this chat"), { target: { value: "" } });
    await waitFor(() => expect(api.patchChat).toHaveBeenLastCalledWith("c1", { project_id: null }));
  });
});

