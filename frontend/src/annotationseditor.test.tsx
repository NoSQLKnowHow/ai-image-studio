import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { DEFAULT_LIMITS, AnnotationsContext, type AnnotationActions } from "./annotations";
import { AnnotationsEditor, AnnotationsView, RunAnnotations, ViewerAnnotationsButton, ViewerAnnotationsPanel, panelEscape } from "./components/RunAnnotations";
import { makeMusicRun, makeRun } from "./testdata";
import type { Run, TagCount } from "./types";

// The tags and the note of a run (DESIGN.md §45.3): what a card shows, and the editor that opens in it and in the viewer. The editor is drawn with a stand-in for the server that does what
// the server does (replaces the tags, answers the run), inside a small harness that holds the run as the page's store does, so that what the editor shows after a change is what it would show.

afterEach(cleanup);

const KNOWN: TagCount[] = [{ tag: "fox", count: 5 }, { tag: "winter", count: 3 }, { tag: "red fox", count: 2 }];

/** The actions the page gives a card, with spies, answering as the server does. `run` is held by the harness, so a change is drawn. */
function actions(over: Partial<AnnotationActions> = {}) {
  const setTags = vi.fn(async (run: Run, tags: string[]) => ({ ...run, tags: [...tags].sort() }) as Run);
  const setNotes = vi.fn(async (run: Run, notes: string) => ({ ...run, notes }) as Run);
  const knownTags = vi.fn(async () => KNOWN);
  const value: AnnotationActions = {
    limits: DEFAULT_LIMITS, setTags, setNotes, knownTags, suggest: { available: false, reason: "not in this test" }, suggestTags: vi.fn(async () => []), reachability: vi.fn(async () => null), ...over,
  };
  return { value, setTags, setNotes, knownTags };
}

/** Draw `node(run)` with the actions, holding the run in state: the actions' answers replace it, as the page's store replaces a run. */
function Harness({ first, value, children }: { first: Run; value: AnnotationActions | null; children: (run: Run) => React.ReactNode }) {
  const [run, setRun] = useState(first);
  const wrapped: AnnotationActions | null = value && {
    ...value,
    setTags: async (r, tags) => { const answer = await value.setTags(r, tags); setRun(answer); return answer; },
    setNotes: async (r, notes) => { const answer = await value.setNotes(r, notes); setRun(answer); return answer; },
  };
  return <AnnotationsContext.Provider value={wrapped}>{children(run)}</AnnotationsContext.Provider>;
}
const card = (run: Run, value: AnnotationActions | null) => render(<Harness first={run} value={value}>{(r) => <RunAnnotations run={r} />}</Harness>);
const editor = (run: Run, value: AnnotationActions | null) => render(<Harness first={run} value={value}>{(r) => <AnnotationsEditor run={r} />}</Harness>);
const tagBox = () => screen.getByRole("combobox", { name: "Add a tag" }) as HTMLInputElement;
const noteBox = () => screen.getByLabelText("Note") as HTMLTextAreaElement;
const status = () => document.querySelector("[data-annotation-status]")?.textContent;
const chips = () => [...document.querySelectorAll(".annotations-editor [data-tag]")].map((li) => li.getAttribute("data-tag"));
const open = () => fireEvent.click(screen.getByRole("button", { name: /Add tags or a note|Tags and notes/ }));

describe("what a card shows (§45.3)", () => {
  it("the tags as chips and the note, wherever the run is: with no editor at all (a card shown without the page's actions) it only reads", () => {
    const run = makeRun({ tags: ["fox", "winter"], notes: "chosen for the poster" });
    card(run, null);
    expect(screen.getByRole("list", { name: "Tags" }).textContent).toBe("foxwinter");
    expect(document.querySelector("[data-annotation=note]")?.textContent).toBe("chosen for the poster");
    expect(screen.queryByRole("button", { name: /tags/i })).toBeNull(); // no way to change them without the page's actions
  });

  it("nothing at all for a run with no tags and no note, but the button that adds some", () => {
    const { container } = card(makeRun(), actions().value);
    expect(container.querySelector("[data-annotations-view]")).toBeNull();
    expect(screen.getByRole("button", { name: "Add tags or a note" })).toBeTruthy();
  });

  it("the button says Tags and notes once there is something", () => {
    card(makeRun({ notes: "x" }), actions().value);
    expect(screen.getByRole("button", { name: "Tags and notes" })).toBeTruthy();
  });

  it("a run in the bin shows its tags and note and has no way to change them", () => {
    card(makeRun({ tags: ["fox"], notes: "kept for later", deleted_at: "2026-10-02T00:00:00.000Z" }), actions().value);
    expect(screen.getByRole("list", { name: "Tags" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /tags|note/i })).toBeNull();
  });

  it("every kind of card has them: a music run's are shown the same way", () => {
    card(makeMusicRun({ tags: ["calm"], notes: "for the intro" }), actions().value);
    expect(screen.getByRole("list", { name: "Tags" }).textContent).toBe("calm");
  });

  it("a long note is cut and Show all opens it, Show less closes it", () => {
    const long = `${"word ".repeat(80)}end`;
    render(<AnnotationsView run={makeRun({ notes: long })} />);
    const text = () => document.querySelector("[data-annotation=note]")?.textContent ?? "";
    expect(text().endsWith("…")).toBe(true);
    const more = screen.getByRole("button", { name: "Show all" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(text()).toBe(long);
    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    expect(text().endsWith("…")).toBe(true);
  });

  it("opens the editor in place and closes it, hiding the chips while the editor shows them", () => {
    const { container } = card(makeRun({ tags: ["fox"] }), actions().value);
    expect(container.querySelector("[data-annotations-view]")).not.toBeNull();
    open();
    expect(screen.getByRole("group", { name: "Tags and note" })).toBeTruthy();
    expect(container.querySelector("[data-annotations-view]")).toBeNull(); // the editor lists them, with their ✕
    expect(screen.getByRole("button", { name: "Close tags and notes" }).getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Close tags and notes" }));
    expect(screen.queryByRole("group", { name: "Tags and note" })).toBeNull();
  });
});

describe("adding tags (§45.3)", () => {
  it("takes what is typed on Enter, split at commas, and says what it added", async () => {
    const { setTags } = (() => { const a = actions(); editor(makeRun({ tags: ["winter"] }), a.value); return a; })();
    fireEvent.change(tagBox(), { target: { value: "Fox, snow" } });
    // a comma ends a tag: what is before it is added at once and what follows stays in the box
    await waitFor(() => expect(setTags).toHaveBeenCalledTimes(1));
    expect(setTags.mock.calls[0][1]).toEqual(["fox", "winter"]);
    await waitFor(() => expect(status()).toBe("Added the tag “fox”."));
    expect(tagBox().value).toBe(" snow");
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(setTags).toHaveBeenCalledTimes(2));
    expect(setTags.mock.calls[1][1]).toEqual(["fox", "snow", "winter"]);
    await waitFor(() => expect(chips()).toEqual(["fox", "snow", "winter"]));
    expect(tagBox().value).toBe("");
  });

  it("adds several at once from a paste with commas, in one request", async () => {
    const a = actions();
    editor(makeRun(), a.value);
    fireEvent.change(tagBox(), { target: { value: "one, two, three" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(a.setTags).toHaveBeenCalled());
    // the first two are added at the commas, the third on Enter; between them each answer is the run, so the last request has all three
    await waitFor(() => expect(chips()).toEqual(["one", "three", "two"]));
  });

  it("with the Add button as well", async () => {
    const a = actions();
    editor(makeRun(), a.value);
    const add = document.querySelector('[data-action="add-tag"]') as HTMLButtonElement;
    expect(add.disabled).toBe(true); // nothing typed
    fireEvent.change(tagBox(), { target: { value: "fox" } });
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    await waitFor(() => expect(a.setTags).toHaveBeenCalledWith(expect.anything(), ["fox"]));
  });

  it("says a tag the run has is already one, and sends nothing", async () => {
    const a = actions();
    editor(makeRun({ tags: ["fox"] }), a.value);
    fireEvent.change(tagBox(), { target: { value: "FOX" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(status()).toBe("“fox” is already a tag."));
    expect(a.setTags).not.toHaveBeenCalled();
    expect(tagBox().value).toBe("");
  });

  it("ignores text with no tag in it", () => {
    const a = actions();
    editor(makeRun(), a.value);
    fireEvent.change(tagBox(), { target: { value: " , ," } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    expect(a.setTags).not.toHaveBeenCalled();
  });

  it("refuses a thirteenth tag in words and sends nothing", () => {
    const twelve = Array.from({ length: 12 }, (_, i) => `t${String(i).padStart(2, "0")}`);
    const a = actions();
    editor(makeRun({ tags: twelve }), a.value);
    fireEvent.change(tagBox(), { target: { value: "one more" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    expect(document.querySelector("[data-annotation-problem]")?.textContent).toBe("A run can have 12 tags. Remove one to add another.");
    expect(a.setTags).not.toHaveBeenCalled();
    expect(tagBox().value).toBe("one more"); // what was typed is not lost
  });

  it("uses the server's limits, not its own", () => {
    const a = actions({ limits: { ...DEFAULT_LIMITS, tags_max: 2 } });
    editor(makeRun({ tags: ["a", "b"] }), a.value);
    fireEvent.change(tagBox(), { target: { value: "c" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    expect(document.querySelector("[data-annotation-problem]")?.textContent).toContain("A run can have 2 tags");
  });

  it("shows the server's refusal in words and keeps what was there", async () => {
    const a = actions({ setTags: vi.fn(async () => { throw new ApiError("This run is in the bin, which only reads.", 409, "run_in_bin"); }) });
    editor(makeRun({ tags: ["fox"] }), a.value);
    fireEvent.change(tagBox(), { target: { value: "snow" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(document.querySelector("[data-annotation-problem]")?.textContent).toBe("Couldn't save the tags: This run is in the bin, which only reads."));
    expect(chips()).toEqual(["fox"]);
    expect(status()).toBe(""); // a refusal replaces the last confirmation
  });

  it("is the right size for a phone: the box takes the limit with room for several tags typed at once", () => {
    editor(makeRun(), actions().value);
    expect(tagBox().maxLength).toBe(DEFAULT_LIMITS.tag_chars * 4);
  });
});

describe("removing a tag", () => {
  it("is the ✕ on its chip, named for the tag, and puts the cursor back in the box", async () => {
    const a = actions();
    editor(makeRun({ tags: ["fox", "winter"] }), a.value);
    fireEvent.click(screen.getByRole("button", { name: "Remove the tag “fox”" }));
    await waitFor(() => expect(a.setTags).toHaveBeenCalledWith(expect.anything(), ["winter"]));
    await waitFor(() => expect(chips()).toEqual(["winter"]));
    expect(status()).toBe("Removed the tag “fox”.");
    expect(document.activeElement).toBe(tagBox());
  });
});

describe("the tags you already use are offered (§45.3)", () => {
  it("when the box is used, most used first, with how many runs have each, and without the tags the run has", async () => {
    const a = actions();
    editor(makeRun({ tags: ["winter"] }), a.value);
    expect(a.knownTags).toHaveBeenCalledTimes(1); // the box takes focus when the editor opens, which asks
    await waitFor(() => expect(screen.getByRole("listbox", { name: "Tags you use" })).toBeTruthy());
    expect([...document.querySelectorAll("[role=option]")].map((o) => o.textContent)).toEqual(["fox 5", "red fox 2"]);
  });

  it("narrows as you type, by the start of the tag or of a word in it", async () => {
    editor(makeRun(), actions().value);
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    fireEvent.change(tagBox(), { target: { value: "fox" } });
    expect([...document.querySelectorAll("[role=option]")].map((o) => o.getAttribute("data-tag-option"))).toEqual(["fox", "red fox"]);
  });

  it("is chosen with the arrow keys and Enter, and the box says which is chosen", async () => {
    const a = actions();
    editor(makeRun(), a.value);
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    expect(tagBox().getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(tagBox(), { key: "ArrowDown" });
    fireEvent.keyDown(tagBox(), { key: "ArrowDown" });
    const chosen = document.querySelector("[role=option][aria-selected=true]") as HTMLElement;
    expect(chosen.getAttribute("data-tag-option")).toBe("winter");
    expect(tagBox().getAttribute("aria-activedescendant")).toBe(chosen.id);
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(a.setTags).toHaveBeenCalledWith(expect.anything(), ["winter"]));
  });

  it("wraps round at either end", async () => {
    editor(makeRun(), actions().value);
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    fireEvent.keyDown(tagBox(), { key: "ArrowUp" }); // from nothing chosen, up goes to the last
    expect(document.querySelector("[role=option][aria-selected=true]")?.getAttribute("data-tag-option")).toBe("red fox");
    fireEvent.keyDown(tagBox(), { key: "ArrowDown" }); // and down from the last goes to the first
    expect(document.querySelector("[role=option][aria-selected=true]")?.getAttribute("data-tag-option")).toBe("fox");
  });

  it("is chosen by a press on it", async () => {
    const a = actions();
    editor(makeRun(), a.value);
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    fireEvent.click(document.querySelector('[data-tag-option="red fox"]') as HTMLElement);
    await waitFor(() => expect(a.setTags).toHaveBeenCalledWith(expect.anything(), ["red fox"]));
  });

  it("closes on Escape, and the key does not reach the viewer or the page", async () => {
    editor(makeRun(), actions().value);
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    const reached = vi.fn();
    document.addEventListener("keydown", reached);
    fireEvent.keyDown(tagBox(), { key: "Escape" });
    document.removeEventListener("keydown", reached);
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(tagBox().getAttribute("aria-expanded")).toBe("false");
    expect(reached).not.toHaveBeenCalled();
  });

  it("asks again after a change, since the list of tags in use has changed", async () => {
    const a = actions();
    editor(makeRun(), a.value);
    fireEvent.change(tagBox(), { target: { value: "newtag" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(a.knownTags.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("is not a problem when the list cannot be read: tags can still be typed", async () => {
    const a = actions({ knownTags: vi.fn(async () => { throw new Error("down"); }) });
    editor(makeRun(), a.value);
    fireEvent.change(tagBox(), { target: { value: "fox" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(a.setTags).toHaveBeenCalled());
  });
});

describe("the note (§45.3)", () => {
  it("is saved with Save, which is off until it is changed, and says so", async () => {
    const a = actions();
    editor(makeRun({ notes: "first" }), a.value);
    const save = document.querySelector('[data-action="save-note"]') as HTMLButtonElement;
    expect(noteBox().value).toBe("first");
    expect(save.disabled).toBe(true);
    fireEvent.change(noteBox(), { target: { value: "second" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(a.setNotes).toHaveBeenCalledWith(expect.anything(), "second"));
    await waitFor(() => expect(status()).toBe("Note saved."));
    expect(save.disabled).toBe(true); // saved: nothing to save
  });

  it("is saved with Ctrl+Enter", async () => {
    const a = actions();
    editor(makeRun(), a.value);
    fireEvent.change(noteBox(), { target: { value: "why" } });
    fireEvent.keyDown(noteBox(), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(a.setNotes).toHaveBeenCalledWith(expect.anything(), "why"));
    fireEvent.keyDown(noteBox(), { key: "Enter" }); // a plain Enter is a new line, not a save
    expect(a.setNotes).toHaveBeenCalledTimes(1);
  });

  it("is cleared with Clear, which saves at once", async () => {
    const a = actions();
    editor(makeRun({ notes: "something" }), a.value);
    fireEvent.click(document.querySelector('[data-action="clear-note"]') as HTMLElement);
    await waitFor(() => expect(a.setNotes).toHaveBeenCalledWith(expect.anything(), ""));
    await waitFor(() => expect(status()).toBe("Note cleared."));
    expect(noteBox().value).toBe("");
    expect((document.querySelector('[data-action="clear-note"]') as HTMLButtonElement).disabled).toBe(true); // nothing left to clear
  });

  it("counts its length against the server's limit, and the box stops at it", () => {
    editor(makeRun({ notes: "abc" }), actions().value);
    expect(screen.getByText("3 of 2000")).toBeTruthy();
    expect(noteBox().maxLength).toBe(2000);
    fireEvent.change(noteBox(), { target: { value: "abcd" } });
    expect(screen.getByText("4 of 2000")).toBeTruthy();
  });

  it("shows the server's refusal in words", async () => {
    const a = actions({ setNotes: vi.fn(async () => { throw new ApiError("A note is at most 2000 characters; this one is 2001.", 422, "notes_too_long"); }) });
    editor(makeRun(), a.value);
    fireEvent.change(noteBox(), { target: { value: "x" } });
    fireEvent.click(document.querySelector('[data-action="save-note"]') as HTMLElement);
    await waitFor(() => expect(document.querySelector("[data-annotation-problem]")?.textContent).toBe("Couldn't save the note: A note is at most 2000 characters; this one is 2001."));
  });

  it("follows a change made on another page, unless it is being edited", () => {
    const a = actions();
    const view = render(<AnnotationsContext.Provider value={a.value}><AnnotationsEditor run={makeRun({ id: "r1", notes: "one" })} /></AnnotationsContext.Provider>);
    const again = (notes: string) => view.rerender(<AnnotationsContext.Provider value={a.value}><AnnotationsEditor run={makeRun({ id: "r1", notes })} /></AnnotationsContext.Provider>);
    again("two");
    expect(noteBox().value).toBe("two"); // not touched here, so the other page's words are shown
    fireEvent.change(noteBox(), { target: { value: "two, and my own words" } });
    again("three");
    expect(noteBox().value).toBe("two, and my own words"); // being edited: not overwritten
  });
});

describe("the editor as a screen reader meets it (§45.3)", () => {
  it("is a labelled group with a labelled combobox, listbox and note, and says what happened politely", async () => {
    editor(makeRun({ tags: ["fox"] }), actions().value);
    expect(screen.getByRole("group", { name: "Tags and note" })).toBeTruthy();
    expect(tagBox().getAttribute("aria-autocomplete")).toBe("list");
    const live = document.querySelector("[data-annotation-status]") as HTMLElement;
    expect(live.getAttribute("role")).toBe("status");
    expect(live.getAttribute("aria-live")).toBe("polite");
    fireEvent.change(tagBox(), { target: { value: "snow" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(live.textContent).toBe("Added the tag “snow”."));
    fireEvent.change(tagBox(), { target: { value: "a,b,c,d,e,f,g,h,i,j,k,l,m" } });
    await act(async () => undefined);
  });

  it("puts the cursor in the tag box when it opens", () => {
    editor(makeRun(), actions().value);
    expect(document.activeElement).toBe(tagBox());
  });
});

describe("in the viewer (§45.3)", () => {
  it("has a button that counts the tags, and opens a panel named Tags and notes with the editor in it", () => {
    const run = makeRun({ tags: ["fox", "winter"] });
    const a = actions();
    const onToggle = vi.fn();
    const { rerender } = render(<AnnotationsContext.Provider value={a.value}><ViewerAnnotationsButton run={run} open={false} panelId="p1" onToggle={onToggle} /></AnnotationsContext.Provider>);
    const button = screen.getByRole("button", { name: "Tags and notes · 2" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.getAttribute("aria-controls")).toBe("p1");
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledTimes(1);
    rerender(<AnnotationsContext.Provider value={a.value}><ViewerAnnotationsPanel run={run} panelId="p1" /></AnnotationsContext.Provider>);
    expect(screen.getByRole("region", { name: "Tags and notes" }).id).toBe("p1");
    expect(screen.getByRole("group", { name: "Tags and note" })).toBeTruthy();
  });

  it("has no button without the page's actions, and a read-only panel for a run in the bin", () => {
    const { container } = render(<ViewerAnnotationsButton run={makeRun()} open={false} panelId="p" onToggle={() => undefined} />);
    expect(container.textContent).toBe("");
    cleanup();
    render(<AnnotationsContext.Provider value={actions().value}><ViewerAnnotationsPanel run={makeRun({ deleted_at: "2026-10-02T00:00:00.000Z" })} panelId="p" /></AnnotationsContext.Provider>);
    expect(screen.queryByRole("group", { name: "Tags and note" })).toBeNull(); // no editor
    expect(screen.getByText(/A run in the bin only reads/)).toBeTruthy();
  });

  it("takes Escape for the panel and not for the viewer, unless the tag list is the one that is open", () => {
    const close = vi.fn();
    const keyed = (expanded: boolean | null) => {
      const target = document.createElement("input");
      if (expanded !== null) target.setAttribute("aria-expanded", String(expanded));
      const event = { target, preventDefault: vi.fn() };
      return { event, took: panelEscape(event, true, close) };
    };
    const closed = keyed(null); // a text box with no list: the panel closes
    expect(closed.took).toBe(true);
    expect(closed.event.preventDefault).toHaveBeenCalled(); // the viewer stays
    expect(close).toHaveBeenCalledTimes(1);
    const withList = keyed(true); // the list is open: the box closes it, the panel stays
    expect(withList.took).toBe(true);
    expect(withList.event.preventDefault).toHaveBeenCalled();
    expect(close).toHaveBeenCalledTimes(1);
    const listClosed = keyed(false);
    expect(listClosed.took).toBe(true);
    expect(close).toHaveBeenCalledTimes(2);
    // with the panel closed it is not the panel's key: the viewer closes as it always did
    const shut = { target: document.body, preventDefault: vi.fn() };
    expect(panelEscape(shut, false, close)).toBe(false);
    expect(shut.preventDefault).not.toHaveBeenCalled();
  });
});

describe("Suggest (§45.3, §45.6)", () => {
  const on = { available: true, reason: null };
  const suggesting = (over: Partial<AnnotationActions> = {}) => actions({ suggest: on, ...over });
  const button = () => document.querySelector('[data-action="suggest"]') as HTMLButtonElement;

  it("is greyed with the reason beside it when the capabilities say it is not available, and typing tags still works", async () => {
    const a = actions({ suggest: { available: false, reason: "Suggest needs the Prompt builder's Ollama." } });
    editor(makeRun(), a.value);
    expect(button().disabled).toBe(true);
    expect(document.querySelector("[data-suggest-why]")?.textContent).toBe("Suggest needs the Prompt builder's Ollama.");
    expect(button().getAttribute("aria-describedby")).toBe(document.querySelector("[data-suggest-why]")?.id);
    expect(a.value.reachability).not.toHaveBeenCalled(); // not set up: nothing to look at
    fireEvent.change(tagBox(), { target: { value: "fox" } });
    fireEvent.keyDown(tagBox(), { key: "Enter" });
    await waitFor(() => expect(a.setTags).toHaveBeenCalled());
  });

  it("is greyed with the status's words when Ollama is set up but cannot be reached", async () => {
    const a = suggesting({ reachability: vi.fn(async () => ({ reachable: false, message: "Can't reach the Ollama server.", hint: "Check that Ollama is running." })) });
    editor(makeRun(), a.value);
    await waitFor(() => expect(button().disabled).toBe(true));
    expect(document.querySelector("[data-suggest-why]")?.textContent).toBe("Can't reach the Ollama server. Check that Ollama is running.");
  });

  it("can be pressed when it is available, and shows what the model suggested without adding any of it", async () => {
    const a = suggesting({ suggestTags: vi.fn(async () => ["lighthouse", "dusk"]) });
    editor(makeRun({ tags: ["sea"] }), a.value);
    expect(button().disabled).toBe(false);
    fireEvent.click(button());
    await waitFor(() => expect(screen.getByRole("list", { name: "Suggested tags" })).toBeTruthy());
    expect([...document.querySelectorAll("[data-suggestion]")].map((b) => b.getAttribute("data-suggestion"))).toEqual(["lighthouse", "dusk"]);
    expect(status()).toBe("2 tags suggested. Press one to add it.");
    expect(a.setTags).not.toHaveBeenCalled(); // suggested, not applied
    expect(chips()).toEqual(["sea"]);
  });

  it("says Asking… while it works and keeps the button off", async () => {
    let finish: (tags: string[]) => void = () => undefined;
    const a = suggesting({ suggestTags: vi.fn(() => new Promise<string[]>((resolve) => { finish = resolve; })) });
    editor(makeRun(), a.value);
    fireEvent.click(button());
    expect(button().textContent).toBe("Asking…");
    expect(button().disabled).toBe(true);
    await act(async () => finish(["x"]));
    expect(button().textContent).toBe("Suggest");
  });

  it("adds one suggestion when it is pressed, and it leaves the row", async () => {
    const a = suggesting({ suggestTags: vi.fn(async () => ["lighthouse", "dusk"]) });
    editor(makeRun(), a.value);
    fireEvent.click(button());
    await waitFor(() => expect(document.querySelector('[data-suggestion="dusk"]')).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Add “dusk”" }));
    await waitFor(() => expect(a.setTags).toHaveBeenCalledWith(expect.anything(), ["dusk"]));
    await waitFor(() => expect(chips()).toEqual(["dusk"]));
    expect([...document.querySelectorAll("[data-suggestion]")].map((b) => b.getAttribute("data-suggestion"))).toEqual(["lighthouse"]); // one at a time
  });

  it("is dismissed with Dismiss suggestions, and does not offer a tag the run has", async () => {
    const a = suggesting({ suggestTags: vi.fn(async () => ["sea", "dusk"]) });
    editor(makeRun({ tags: ["sea"] }), a.value);
    fireEvent.click(button());
    await waitFor(() => expect(document.querySelector("[data-suggestions]")).not.toBeNull());
    expect(document.querySelector('[data-suggestion="sea"]')).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss suggestions" }));
    expect(document.querySelector("[data-suggestions]")).toBeNull();
  });

  it("says when there is nothing new, and shows a refusal in the server's words with its hint", async () => {
    const a = suggesting({ suggestTags: vi.fn(async () => []) });
    editor(makeRun(), a.value);
    fireEvent.click(button());
    await waitFor(() => expect(status()).toBe("Nothing new to suggest."));
    cleanup();
    const b = suggesting({ suggestTags: vi.fn(async () => { throw new ApiError("Can't reach the Ollama server.", 503, "unreachable", [], "Check that Ollama is running."); }) });
    editor(makeRun(), b.value);
    fireEvent.click(button());
    await waitFor(() => expect(document.querySelector("[data-annotation-problem]")?.textContent).toBe("Couldn't get suggestions: Can't reach the Ollama server. Check that Ollama is running."));
    expect(button().disabled).toBe(false); // and it can be tried again
  });
});
