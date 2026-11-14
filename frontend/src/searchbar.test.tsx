import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EmptyHistory, FilterBar, SEARCH_DELAY_MS, narrowedText, sizeLabel } from "./components/FilterBar";
import { sinceOf, untilOf } from "./dates";
import { NO_FILTER, ONLY_KEPT, withoutSearch, type HistoryFilter } from "./history";
import type { Facets, Project } from "./types";

// The search box and the Filters row of the filter bar (DESIGN.md §45.3): what each control sends to the page, how the box waits and how it follows a change made from outside, the words
// the live region says, the labels a screen reader reads, and the empty list of a search. The page behind (App) is not drawn: a small harness holds the filter, as the page does.

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
beforeEach(() => vi.useFakeTimers());

const LOGO: Project = { id: "a".repeat(32), name: "Logo", created_at: "2026-10-02T10:00:00.000Z", counts: { image: 4, music: 1 } };
const FACETS: Facets = {
  models: [{ value: "model-a", count: 7 }, { value: "model-b", count: 2 }],
  sizes: [{ width: 2752, height: 1536, count: 6 }, { width: 640, height: 480, count: 3 }],
};
const SHAPES: Record<string, [number, number]> = { "16:9": [2752, 1536], "1:1": [2048, 2048] };
const typeInto = (value: string) => fireEvent.change(screen.getByRole("searchbox", { name: "Search history" }), { target: { value } });
const wait = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

/** The bar with the filter held as the page holds it: a change the bar asks for becomes the filter it is drawn with next. `spy` sees every change. */
function Harness({ initial, spy, tab = "image", facets = FACETS, resultCount = null }: { initial: HistoryFilter; spy: (filter: HistoryFilter) => void; tab?: "image" | "music"; facets?: Facets | null; resultCount?: number | null }) {
  const [filter, setFilter] = useState(initial);
  return (
    <FilterBar filter={filter} keptCount={2} deletedCount={1} binTotal={1} projects={[LOGO]} tab={tab} facets={facets} resultCount={resultCount} aspectNames={SHAPES}
      onChange={(next) => { spy(next); setFilter(next); }} onEmptyBin={() => undefined} onManageProjects={() => undefined} />
  );
}
const draw = (initial: HistoryFilter = NO_FILTER, extra: { tab?: "image" | "music"; facets?: Facets | null; resultCount?: number | null } = {}) => {
  const spy = vi.fn();
  const view = render(<Harness initial={initial} spy={spy} {...extra} />);
  return { spy, ...view };
};

describe("the search box", () => {
  it("waits a quarter of a second after the last key, then searches for the words, trimmed and with white space made single", () => {
    const { spy } = draw();
    expect(SEARCH_DELAY_MS).toBe(250);
    typeInto("  red   fox ");
    wait(SEARCH_DELAY_MS - 1);
    expect(spy).not.toHaveBeenCalled(); // not yet: the person may still be typing
    wait(1);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, q: "red fox" });
  });

  it("starts the wait again at each key, so a word typed slowly is one search", () => {
    const { spy } = draw();
    typeInto("f");
    wait(200);
    typeInto("fo");
    wait(200);
    typeInto("fox");
    wait(200);
    expect(spy).not.toHaveBeenCalled();
    wait(50);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, q: "fox" });
  });

  it("searches at once on Enter", () => {
    const { spy } = draw();
    typeInto("fox");
    fireEvent.keyDown(screen.getByRole("searchbox", { name: "Search history" }), { key: "Enter" });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, q: "fox" });
    wait(500);
    expect(spy).toHaveBeenCalledTimes(1); // and the pause that was running does not search again
  });

  it("searches for nothing when no word is left: punctuation alone is no search", () => {
    const { spy } = draw();
    typeInto('"*-:');
    wait(500);
    expect(spy).not.toHaveBeenCalled(); // the filter had no search and still has none
    // a search that is then reduced to punctuation is taken off
    typeInto("fox");
    wait(250);
    typeInto("-");
    wait(250);
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, q: null });
  });

  it("clears with its button, at once, and puts the cursor back in the box", () => {
    const { spy } = draw({ ...NO_FILTER, q: "fox" });
    const box = screen.getByRole("searchbox", { name: "Search history" }) as HTMLInputElement;
    expect(box.value).toBe("fox"); // a search that is already on is in the box
    fireEvent.click(screen.getByRole("button", { name: "Clear the search" }));
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, q: null });
    expect(box.value).toBe("");
    expect(document.activeElement).toBe(box);
    expect(screen.queryByRole("button", { name: "Clear the search" })).toBeNull(); // nothing to clear now
  });

  it("clears with Escape when there is something to clear", () => {
    const { spy } = draw();
    const box = screen.getByRole("searchbox", { name: "Search history" }) as HTMLInputElement;
    typeInto("fox");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(box.value).toBe("");
    wait(500);
    expect(spy).not.toHaveBeenCalled(); // it was never searched for
  });

  it("shows a search that the page changes from outside, and never overwrites what is being typed", () => {
    const spy = vi.fn();
    const view = render(<FilterBar filter={{ ...NO_FILTER, q: "fox" }} keptCount={0} deletedCount={0} binTotal={0} projects={[]} tab="image" onChange={spy} onEmptyBin={() => undefined} onManageProjects={() => undefined} />);
    const box = () => screen.getByRole("searchbox", { name: "Search history" }) as HTMLInputElement;
    expect(box().value).toBe("fox");
    const again = (filter: HistoryFilter) => view.rerender(<FilterBar filter={filter} keptCount={0} deletedCount={0} binTotal={0} projects={[]} tab="image" onChange={spy} onEmptyBin={() => undefined} onManageProjects={() => undefined} />);
    // cleared from outside (Show all, Clear filters): the box is emptied
    again({ ...NO_FILTER, q: null });
    expect(box().value).toBe("");
    // the person types and the page has not changed its filter yet: what they typed stays, whatever else re-draws the bar
    typeInto("hen");
    again({ ...NO_FILTER, q: null, kept: true });
    expect(box().value).toBe("hen");
    // a search the box itself made, drawn back to it a moment later, is not "from outside": typing on after it is not undone
    wait(250);
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, kept: true, q: "hen" }); // it searches with the filter as the page has it NOW, Kept included
    typeInto("hens");
    again({ ...NO_FILTER, kept: true, q: "hen" });
    expect(box().value).toBe("hens");
  });

  it("is labelled for a screen reader, and takes at most the number of characters the server does", () => {
    draw();
    const box = screen.getByRole("searchbox", { name: "Search history" }) as HTMLInputElement;
    expect(box.maxLength).toBe(200);
    expect(box.placeholder).toBe("Search words, notes and tags");
    expect(screen.getByRole("search")).toBeTruthy(); // a landmark
    const longer = render(<FilterBar filter={NO_FILTER} keptCount={null} deletedCount={null} binTotal={null} projects={[]} tab="image" searchMax={120} onChange={() => undefined} onEmptyBin={() => undefined} onManageProjects={() => undefined} />);
    expect((longer.container.querySelector("[data-filter-search]") as HTMLInputElement).maxLength).toBe(120);
  });

  it("keeps the search when Show is chosen, and Show and Project when the search changes", () => {
    const { spy } = draw({ ...NO_FILTER, q: "fox", project: LOGO.id });
    fireEvent.click(screen.getByRole("radio", { name: /^Kept/ }));
    expect(spy).toHaveBeenLastCalledWith({ ...ONLY_KEPT, q: "fox", project: LOGO.id });
    typeInto("hen");
    wait(250);
    expect(spy).toHaveBeenLastCalledWith({ ...ONLY_KEPT, q: "hen", project: LOGO.id });
  });
});

describe("the live region (§45.3)", () => {
  const said = (container: HTMLElement) => container.querySelector("[data-filter-result]")?.textContent;

  it("is polite, and says how many runs were found once the server has counted", () => {
    const { container } = draw({ ...NO_FILTER, q: "fox" }, { resultCount: 12 });
    const region = container.querySelector("[data-filter-result]") as HTMLElement;
    expect(region.getAttribute("role")).toBe("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(said(container)).toBe("12 results");
  });

  it("says 1 result, and No results, and nothing while the number is not known", () => {
    expect(said(draw({ ...NO_FILTER, q: "fox" }, { resultCount: 1 }).container)).toBe("1 result");
    cleanup();
    expect(said(draw({ ...NO_FILTER, q: "fox" }, { resultCount: 0 }).container)).toBe("No results");
    cleanup();
    expect(said(draw({ ...NO_FILTER, q: "fox" }, { resultCount: null }).container)).toBe("");
  });

  it("says nothing when no search or filter is on: the number beside All is not a search result", () => {
    expect(said(draw(NO_FILTER, { resultCount: 40 }).container)).toBe("");
    cleanup();
    expect(said(draw(ONLY_KEPT, { resultCount: 4 }).container)).toBe("");
  });

  it("speaks for a filter as well as for a search", () => {
    expect(said(draw({ ...NO_FILTER, model: "model-a" }, { resultCount: 7 }).container)).toBe("7 results");
  });
});

describe("the Filters row (§45.3)", () => {
  const toggle = () => screen.getByRole("button", { name: /^Filters/ });

  it("is closed until the button opens it, and the button says so", () => {
    const { container } = draw();
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector("[data-filters-panel]")).toBeNull();
    expect(toggle().getAttribute("aria-controls")).toBeNull(); // nothing to name while it is closed
    fireEvent.click(toggle());
    const panel = container.querySelector("[data-filters-panel]") as HTMLElement;
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(toggle().getAttribute("aria-controls")).toBe(panel.id); // and when it is open, the button names what it opened
    fireEvent.click(toggle());
    expect(container.querySelector("[data-filters-panel]")).toBeNull();
  });

  it("is open from the start when a filter is on, and the button counts the filters that are", () => {
    const { container } = draw({ ...NO_FILTER, model: "model-a", size: "2752x1536" });
    expect(container.querySelector("[data-filters-panel]")).not.toBeNull();
    expect(toggle().textContent).toContain("Filters · 2");
    cleanup();
    draw({ ...NO_FILTER, q: "fox" }); // a search alone is not one of the row's filters
    expect(toggle().textContent?.trim()).toBe("Filters");
  });

  it("has a labelled control for each filter", () => {
    draw(NO_FILTER);
    fireEvent.click(toggle());
    for (const name of ["Model", "Size", "From", "To"]) expect(screen.getByLabelText(name), name).toBeTruthy();
  });

  it("offers the models of the tab with their counts, and chooses one or any", () => {
    const { spy } = draw();
    fireEvent.click(toggle());
    const select = screen.getByLabelText("Model") as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent)).toEqual(["Any model", "model-a (7)", "model-b (2)"]);
    fireEvent.change(select, { target: { value: "model-b" } });
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, model: "model-b" });
    fireEvent.change(select, { target: { value: "" } });
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, model: null }); // Any model is no model at all, not an empty name
  });

  it("shows a model the list does not hold, rather than saying another", () => {
    draw({ ...NO_FILTER, model: "gone" });
    const select = screen.getByLabelText("Model") as HTMLSelectElement;
    expect(select.value).toBe("gone");
    expect([...select.options].map((option) => option.textContent)).toContain("gone");
  });

  it("offers sizes on the Images tab only, labelled with the studio's shape where it is exactly one", () => {
    draw(NO_FILTER, { tab: "image" });
    fireEvent.click(toggle());
    const select = screen.getByLabelText("Size") as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent)).toEqual(["Any size", "2752×1536 · 16:9 (6)", "640×480 (3)"]);
    expect([...select.options].map((option) => option.value)).toEqual(["", "2752x1536", "640x480"]);
    cleanup();
    draw(NO_FILTER, { tab: "music" });
    fireEvent.click(toggle());
    expect(screen.queryByLabelText("Size")).toBeNull(); // a track has no size
    expect(screen.getByLabelText("Model")).toBeTruthy();
  });

  it("chooses a size", () => {
    const { spy } = draw();
    fireEvent.click(toggle());
    fireEvent.change(screen.getByLabelText("Size"), { target: { value: "2752x1536" } });
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, size: "2752x1536" });
  });

  it("works while the lists have not arrived", () => {
    draw(NO_FILTER, { facets: null });
    fireEvent.click(toggle());
    expect((screen.getByLabelText("Model") as HTMLSelectElement).options).toHaveLength(1); // Any model, and nothing else
  });

  it("turns the two days into the two instants the server takes: the start of the first and the start of the day after the last, in the browser's zone", () => {
    const { spy } = draw();
    fireEvent.click(toggle());
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-01" } });
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, since: sinceOf("2026-11-01"), until: null });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-11-08" } });
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, since: sinceOf("2026-11-01"), until: untilOf("2026-11-08") });
    // the last day is in the range: the range ends where the day after it begins
    expect(Date.parse(untilOf("2026-11-08") as string)).toBeGreaterThan(Date.parse(sinceOf("2026-11-08") as string));
  });

  it("takes an end away when its box is emptied, and shows the dates of a range that is on", () => {
    const { spy } = draw({ ...NO_FILTER, since: sinceOf("2026-11-01"), until: untilOf("2026-11-08") });
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("2026-11-01");
    expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("2026-11-08");
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "" } });
    expect(spy).toHaveBeenLastCalledWith({ ...NO_FILTER, since: sinceOf("2026-11-01"), until: null });
  });

  it("refuses a range that ends before it begins, in words, and sends nothing", () => {
    const { spy, container } = draw();
    fireEvent.click(toggle());
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-08" } });
    spy.mockClear();
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-11-01" } });
    expect(spy).not.toHaveBeenCalled();
    const problem = container.querySelector("[data-filter-problem]") as HTMLElement;
    expect(problem.textContent).toMatch(/“From” is after “To”/);
    expect(problem.getAttribute("role")).toBe("alert");
    expect((screen.getByLabelText("To") as HTMLInputElement).getAttribute("aria-invalid")).toBe("true");
    // put right, it is sent and the words go
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-11-09" } });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(container.querySelector("[data-filter-problem]")).toBeNull();
  });

  it("clears the search and the filters, and is off when there is nothing to clear", () => {
    const clear = () => document.querySelector('[data-action="clear-filters"]') as HTMLButtonElement;
    const idle = draw();
    fireEvent.click(toggle());
    expect(clear().disabled).toBe(true);
    idle.unmount();
    const { spy } = draw({ ...ONLY_KEPT, project: LOGO.id, q: "fox", model: "model-a", size: "640x480", since: "2026-11-01T00:00:00.000Z", until: "2026-11-02T00:00:00.000Z" });
    expect(clear().disabled).toBe(false);
    fireEvent.click(clear());
    expect(spy).toHaveBeenLastCalledWith({ ...ONLY_KEPT, project: LOGO.id }); // Show and Project stay
  });
});

describe("the empty list of a search (§45.3)", () => {
  // `onClear` null is a list that was not handed a way to clear (an older caller)
  const empty = (filter: HistoryFilter, project: Project | null = null, onClear: (() => void) | null = () => undefined) =>
    renderToStaticMarkup(<EmptyHistory kind="image" filter={filter} binDays={30} project={project} onShowAll={() => undefined} onShowAnyProject={() => undefined} onClearSearch={onClear ?? undefined} />);

  it("says nothing matches, what was looked for, and how to clear it", () => {
    const html = empty({ ...NO_FILTER, q: "fox" });
    expect(html).toContain("No runs match");
    expect(html).toContain("Looked for: search “fox”.");
    expect(html).toContain("Clear search and filters");
    expect(html).not.toContain("No images yet"); // the tab's own empty text would be untrue: the tab is not empty
  });

  it("says it for a filter alone, and for Kept, the bin and a project", () => {
    expect(empty({ ...NO_FILTER, model: "model-a" })).toContain("Looked for: model model-a.");
    expect(empty({ ...ONLY_KEPT, q: "fox" })).toContain("search “fox”; only runs you kept.");
    expect(empty({ ...NO_FILTER, deleted: true, q: "fox" })).toContain("in Deleted");
    expect(empty({ ...NO_FILTER, q: "fox", project: LOGO.id }, LOGO)).toContain("in the project “Logo”");
  });

  it("keeps the tab's usual empty text when nothing is searched, and when it was not handed a way to clear", () => {
    expect(empty(NO_FILTER)).toContain("No images yet");
    expect(empty({ ...NO_FILTER, q: "fox" }, null, null)).toContain("No images yet");
  });

  it("presses Clear search and filters", () => {
    const onClear = vi.fn();
    render(<EmptyHistory kind="image" filter={{ ...NO_FILTER, q: "fox" }} binDays={30} project={null} onShowAll={() => undefined} onShowAnyProject={() => undefined} onClearSearch={onClear} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear search and filters" }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});

describe("words for what is on (§45.3)", () => {
  it("lists the search, the model, the size and the range, then where it was looked for", () => {
    const since = sinceOf("2026-11-01");
    const until = untilOf("2026-11-08");
    expect(narrowedText({ ...NO_FILTER, q: "red fox", model: "m", size: "2752x1536", since, until }, null))
      .toBe("Looked for: search “red fox”, model m, size 2752×1536, made from 2026-11-01 to 2026-11-08.");
    expect(narrowedText({ ...NO_FILTER, since }, null)).toBe("Looked for: made from 2026-11-01.");
    expect(narrowedText({ ...NO_FILTER, until }, null)).toBe("Looked for: made up to 2026-11-08.");
    expect(narrowedText({ ...NO_FILTER, q: "x", project: "none" }, null)).toContain("in no project");
    expect(narrowedText(withoutSearch({ ...NO_FILTER, q: "x" }), null)).toBe("Looked for: .");
  });

  it("names a size with the studio's shape when it is exactly one", () => {
    expect(sizeLabel(2752, 1536, SHAPES)).toBe("2752×1536 · 16:9");
    expect(sizeLabel(2752, 1535, SHAPES)).toBe("2752×1535");
    expect(sizeLabel(10, 10, undefined)).toBe("10×10");
  });
});
