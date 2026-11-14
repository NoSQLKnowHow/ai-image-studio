import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PronunciationDialog } from "./components/PronunciationDialog";
import { ApiError } from "./api";
import type { PronunciationEntry, PronunciationLimits } from "./speechTypes";
import { usePronunciations } from "./usePronunciations";

// The pronunciation list's dialog (DESIGN.md §44.3): adding, changing and deleting entries, Try it, and what a person can and cannot do. The list is the real hook over a table
// of stand-ins for the API, so what the dialog sends is looked at and what it shows is what the hook kept. Every control is found by its accessible name, which is the test
// that each one has one (criterion 369).

afterEach(cleanup);

const LIMITS: PronunciationLimits = { word_max: 60, words_max: 6, say_max: 120, entries_max: 1000, try_chars: 5000 };
const entry = (id: string, word: string, say = "x", match_case = false): PronunciationEntry => ({ id, word, say, match_case, created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z" });
const flush = () => act(async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
});

function makeApi(initial: PronunciationEntry[]) {
  const stored = [...initial];
  return {
    stored,
    pronunciations: vi.fn(async () => ({ entries: [...stored] })),
    addPronunciation: vi.fn(async (body: { word: string; say: string; match_case?: boolean }) => {
      const made = entry(`id${stored.length + 1}`, body.word.trim(), body.say.trim(), !!body.match_case);
      stored.push(made);
      return made;
    }),
    changePronunciation: vi.fn(async (id: string, change: { word?: string; say?: string; match_case?: boolean }) => {
      const at = stored.findIndex((e) => e.id === id);
      stored[at] = { ...stored[at], ...change };
      return stored[at];
    }),
    deletePronunciation: vi.fn(async (id: string) => {
      stored.splice(stored.findIndex((e) => e.id === id), 1);
    }),
    tryPronunciation: vi.fn(async (text: string) => ({ spoken: text.replace("Nguyen", "win"), changed: text.includes("Nguyen"), used: text.includes("Nguyen") ? [{ word: "Nguyen", say: "win", count: 1 }] : [] })),
  };
}

function Harness({ api, onOpenChange, limits }: { api: ReturnType<typeof makeApi>; onOpenChange: (open: boolean) => void; limits: PronunciationLimits }) {
  const list = usePronunciations(true, api);
  return <PronunciationDialog open onOpenChange={onOpenChange} list={list} limits={limits} />;
}

async function open(initial: PronunciationEntry[] = [], limits = LIMITS) {
  const api = makeApi(initial);
  const onOpenChange = vi.fn();
  render(<Harness api={api} onOpenChange={onOpenChange} limits={limits} />);
  await flush();
  return { api, onOpenChange };
}

const word = () => screen.getAllByRole("textbox", { name: "Word or phrase" })[0] as HTMLInputElement;
const said = () => screen.getAllByRole("textbox", { name: "Said like" })[0] as HTMLInputElement;
const type = (box: HTMLElement, value: string) => fireEvent.change(box, { target: { value } });
const press = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;
const list = () => screen.getByRole("list", { name: "Your pronunciation list" });

describe("the dialog", () => {
  it("says what the list is, and that an empty one has nothing yet", async () => {
    await open();
    expect(screen.getByRole("dialog", { name: "Pronunciation list" })).toBeTruthy();
    expect(screen.getByText(/one list for every engine and voice/)).toBeTruthy();
    expect(screen.getByText(/it changes only what is spoken and never what you read/)).toBeTruthy();
    expect(screen.getByText(/a reading keeps the list it was made with/)).toBeTruthy();
    expect(screen.getByText("(No entries yet)")).toBeTruthy();
    expect(screen.getByText(/Nothing here yet/)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Your pronunciation list" })).toBeNull();
  });

  it("lists the entries it is given, each with its words and what it is said like, and the capitals badge on the one that has it", async () => {
    await open([entry("1", "Nguyen", "win"), entry("2", "US", "you ess", true)]);
    const rows = within(list()).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Nguyen");
    expect(rows[0].textContent).toContain("win");
    expect(within(rows[0]).queryByText("Match capitals")).toBeNull();
    expect(within(rows[1]).getByText("Match capitals")).toBeTruthy();
    expect(screen.getByText("(2 entries)")).toBeTruthy();
  });

  it("has Close, which asks to close it", async () => {
    const t = await open();
    press("Close");
    expect(t.onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe("adding an entry", () => {
  it("is off until both boxes have something in them", async () => {
    await open();
    expect(button("Add").disabled).toBe(true);
    type(word(), "Nguyen");
    expect(button("Add").disabled).toBe(true);
    type(said(), "win");
    expect(button("Add").disabled).toBe(false);
    type(word(), "   ");
    expect(button("Add").disabled).toBe(true);
  });

  it("sends the entry, says it was added, shows it, empties the boxes and puts the cursor back in the first one for the next", async () => {
    const t = await open();
    type(word(), "Nguyen");
    type(said(), "win");
    fireEvent.click(screen.getByRole("checkbox", { name: "Match capitals exactly" }));
    press("Add");
    await flush();
    expect(t.api.addPronunciation).toHaveBeenCalledWith({ word: "Nguyen", say: "win", match_case: true });
    expect(screen.getByRole("status", { name: "" }).textContent).toBe("Added “Nguyen”.");
    expect(within(list()).getAllByRole("listitem")).toHaveLength(1);
    expect(word().value).toBe("");
    expect(said().value).toBe("");
    expect((screen.getByRole("checkbox", { name: "Match capitals exactly" }) as HTMLInputElement).checked).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(word()));
  });

  it("is sent by Enter in a box as well", async () => {
    const t = await open();
    type(word(), "Nguyen");
    type(said(), "win");
    fireEvent.submit(said().closest("form") as HTMLFormElement);
    await flush();
    expect(t.api.addPronunciation).toHaveBeenCalledTimes(1);
  });

  it("says, before asking, that the word is already on the list, and does not send it", async () => {
    const t = await open([entry("1", "Nguyen", "win")]);
    type(word(), "NGUYEN");
    type(said(), "wynn");
    expect(screen.getByText("“Nguyen” is already on the list. Change that entry instead.")).toBeTruthy();
    expect(word().getAttribute("aria-invalid")).toBe("true");
    expect(button("Add").disabled).toBe(true);
    expect(t.api.addPronunciation).not.toHaveBeenCalled();
  });

  it("says what is wrong with each box, in words, beside it", async () => {
    await open();
    type(word(), "x".repeat(61));
    expect(screen.getByText("A word on the list is at most 60 characters and 6 words.")).toBeTruthy();
    type(word(), "Nguyen");
    type(said(), "Nguyen");
    expect(screen.getByText(/^That is the word itself/)).toBeTruthy();
    expect(said().getAttribute("aria-invalid")).toBe("true");
    expect(button("Add").disabled).toBe(true);
  });

  it("shows the server's own refusal under the boxes and keeps what was typed", async () => {
    const t = await open();
    t.api.addPronunciation.mockRejectedValueOnce(new ApiError("The list is busy.", 503, null, [], "Try again in a moment."));
    type(word(), "Nguyen");
    type(said(), "win");
    press("Add");
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("The list is busy.");
    expect(screen.getByRole("alert").textContent).toContain("Try again in a moment.");
    expect(word().value).toBe("Nguyen");
    expect(within(screen.getByRole("dialog")).queryByRole("list", { name: "Your pronunciation list" })).toBeNull();
  });

  it("is not offered when the list is full, and says so", async () => {
    await open([entry("1", "a"), entry("2", "b")], { ...LIMITS, entries_max: 2 });
    expect(screen.getByText("The list holds 2 entries and is full. Delete one you no longer need.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
  });
});

describe("changing an entry", () => {
  it("opens the entry in boxes of its own, named for the entry, with what it has", async () => {
    await open([entry("1", "Nguyen", "win", true)]);
    press("Change Nguyen");
    expect((screen.getByRole("textbox", { name: "Nguyen: Word or phrase" }) as HTMLInputElement).value).toBe("Nguyen");
    expect((screen.getByRole("textbox", { name: "Nguyen: Said like" }) as HTMLInputElement).value).toBe("win");
    expect((screen.getByRole("checkbox", { name: "Nguyen: Match capitals exactly" }) as HTMLInputElement).checked).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Nguyen: Word or phrase" }));
  });

  it("sends the change, says so, shows the entry as changed and puts focus back on its Change button", async () => {
    const t = await open([entry("1", "Nguyen", "win")]);
    press("Change Nguyen");
    type(screen.getByRole("textbox", { name: "Nguyen: Said like" }), "wynn");
    press("Save");
    await flush();
    expect(t.api.changePronunciation).toHaveBeenCalledWith("1", { word: "Nguyen", say: "wynn", match_case: false });
    expect(screen.getByText("Changed “Nguyen”.")).toBeTruthy();
    expect(within(list()).getAllByRole("listitem")[0].textContent).toContain("wynn");
    await waitFor(() => expect(document.activeElement).toBe(button("Change Nguyen")));
  });

  it("leaves the entry alone and does not close the dialog on Escape, and gives focus back to Change", async () => {
    const t = await open([entry("1", "Nguyen", "win")]);
    press("Change Nguyen");
    const box = screen.getByRole("textbox", { name: "Nguyen: Said like" });
    type(box, "changed my mind");
    box.focus();
    fireEvent.keyDown(box, { key: "Escape" });
    await flush();
    expect(screen.queryByRole("textbox", { name: "Nguyen: Said like" })).toBeNull();
    expect(t.api.changePronunciation).not.toHaveBeenCalled();
    expect(t.onOpenChange).not.toHaveBeenCalledWith(false); // Escape in the entry's boxes leaves the entry, not the dialog
    await waitFor(() => expect(document.activeElement).toBe(button("Change Nguyen")));
  });

  it("is left by Cancel as well", async () => {
    await open([entry("1", "Nguyen", "win")]);
    press("Change Nguyen");
    press("Cancel");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("does not take another entry's word, and says so before asking", async () => {
    const t = await open([entry("1", "Hermes", "her meez"), entry("2", "Nguyen", "win")]);
    press("Change Nguyen");
    type(screen.getByRole("textbox", { name: "Nguyen: Word or phrase" }), "hermes");
    expect(screen.getByText("“Hermes” is already on the list. Change that entry instead.")).toBeTruthy();
    expect(button("Save").disabled).toBe(true);
    expect(t.api.changePronunciation).not.toHaveBeenCalled();
    type(screen.getByRole("textbox", { name: "Nguyen: Word or phrase" }), "NGUYEN"); // but its own word in other capitals is fine
    expect(button("Save").disabled).toBe(false);
  });

  it("shows the server's refusal in the entry and stays open", async () => {
    const t = await open([entry("1", "Nguyen", "win")]);
    t.api.changePronunciation.mockRejectedValueOnce(new ApiError("That entry is not on the list.", 404, "not_found"));
    press("Change Nguyen");
    type(screen.getByRole("textbox", { name: "Nguyen: Said like" }), "wynn");
    press("Save");
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("That entry is not on the list.");
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });
});

describe("deleting an entry", () => {
  it("removes it, says so, and puts the cursor in the Add box", async () => {
    const t = await open([entry("1", "Nguyen", "win"), entry("2", "US", "you ess")]);
    press("Delete Nguyen");
    await flush();
    expect(t.api.deletePronunciation).toHaveBeenCalledWith("1");
    expect(screen.getByText("Deleted “Nguyen”.")).toBeTruthy();
    expect(within(list()).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Delete Nguyen" })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(word()));
  });

  it("says why when it could not, and keeps the entry", async () => {
    const t = await open([entry("1", "Nguyen", "win")]);
    t.api.deletePronunciation.mockRejectedValueOnce(new ApiError("The database is busy.", 503));
    press("Delete Nguyen");
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("The database is busy.");
    expect(screen.getByRole("button", { name: "Delete Nguyen" })).toBeTruthy();
  });
});

describe("Try it", () => {
  const tryBox = () => screen.getByRole("textbox", { name: "A sentence to try" }) as HTMLTextAreaElement;

  it("is off until there is a sentence", async () => {
    await open();
    expect(button("Show how it will be said").disabled).toBe(true);
    type(tryBox(), "Nguyen");
    expect(button("Show how it will be said").disabled).toBe(false);
  });

  it("shows what would be spoken and which entries changed it", async () => {
    const t = await open([entry("1", "Nguyen", "win")]);
    type(tryBox(), "Nguyen's winter was long.");
    press("Show how it will be said");
    await flush();
    expect(t.api.tryPronunciation).toHaveBeenCalledWith("Nguyen's winter was long.");
    expect(screen.getByText("win's winter was long.")).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "The entries that changed it" })).getByText("Nguyen → win")).toBeTruthy();
  });

  it("says when nothing was changed", async () => {
    await open();
    type(tryBox(), "Nothing here.");
    press("Show how it will be said");
    await flush();
    expect(screen.getByText(/Nothing in that sentence is on the list/)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "The entries that changed it" })).toBeNull();
  });

  it("forgets an answer as soon as the sentence changes, so an old answer is never shown beside new words", async () => {
    await open();
    type(tryBox(), "Nguyen");
    press("Show how it will be said");
    await flush();
    expect(screen.getByText("Spoken as:")).toBeTruthy();
    type(tryBox(), "Nguyen again");
    expect(screen.queryByText("Spoken as:")).toBeNull();
  });

  it("is off for a text that is too long, and says by how much the limit is", async () => {
    await open([], { ...LIMITS, try_chars: 10 });
    type(tryBox(), "x".repeat(11));
    expect(screen.getByText("Try a sentence or a paragraph: at most 10 characters.")).toBeTruthy();
    expect(button("Show how it will be said").disabled).toBe(true);
  });

  it("shows the server's refusal", async () => {
    const t = await open();
    t.api.tryPronunciation.mockRejectedValueOnce(new ApiError("Too much text.", 422));
    type(tryBox(), "x");
    press("Show how it will be said");
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("Too much text.");
  });
});
