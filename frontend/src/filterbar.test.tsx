import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EmptyHistory, FilterBar } from "./components/FilterBar";
import { NO_FILTER, ONLY_DELETED, ONLY_KEPT } from "./history";

// a callback that does nothing: these tests only look at the HTML that is drawn
const nothing = () => undefined;
// render the empty-list message for a tab and a filter (`binDays` is the STUDIO_BIN_DAYS that the server reported)
const empty = (kind: "image" | "music", filter = ONLY_DELETED, binDays = 30) =>
  renderToStaticMarkup(<EmptyHistory kind={kind} filter={filter} binDays={binDays} onShowAll={nothing} />);
// render the filter bar for a filter and counts (a count is null while the server has not said)
const bar = (filter = NO_FILTER, counts: { kept: number | null; deleted: number | null; total: number | null } = { kept: 2, deleted: 3, total: 5 }) =>
  renderToStaticMarkup(<FilterBar filter={filter} keptCount={counts.kept} deletedCount={counts.deleted} binTotal={counts.total} onChange={nothing} onEmptyBin={nothing} />);

describe("what an empty list says (DESIGN.md §29.1, §30.1)", () => {
  it("in the bin: how long deleted runs stay, as many days as the setting says", () => {
    expect(empty("image")).toContain("No deleted images");
    expect(empty("image", ONLY_DELETED, 7)).toContain("Runs you delete stay here for 7 days before they are gone for good.");
    expect(empty("image", ONLY_DELETED, 1)).toContain("stay here for 1 day before");
  });

  it("in the bin of the Music tab, and with no bin", () => {
    expect(empty("music")).toContain("No deleted music");
    expect(empty("image", ONLY_DELETED, 0)).toContain("There is no bin: a deleted run is deleted for good.");
  });

  it("in Kept, how to keep a run; in All, how to make one", () => {
    expect(empty("music", ONLY_KEPT)).toContain("No kept music yet");
    expect(empty("image", ONLY_KEPT)).toContain("No kept images yet");
    expect(empty("image", NO_FILTER)).toContain("No images yet");
    expect(empty("music", NO_FILTER)).toContain("No music yet");
  });

  it("offers Show all everywhere but in All", () => {
    expect(empty("image", ONLY_DELETED)).toContain("Show all");
    expect(empty("image", ONLY_KEPT)).toContain("Show all");
    expect(empty("image", NO_FILTER)).not.toContain("Show all");
  });
});

describe("the filter bar (DESIGN.md §29.1, §30.1)", () => {
  it("has All, Kept and Deleted, and counts on the last two", () => {
    const html = bar();
    for (const name of ["All", "Kept", "Deleted"]) expect(html).toContain(`>${name}`);
    expect(html).toContain("Kept<span class=\"filter-count\"> 2</span>");
    expect(html).toContain("Deleted<span class=\"filter-count\"> 3</span>");
  });

  it("leaves a count out while it is not known", () => {
    const html = bar(NO_FILTER, { kept: null, deleted: null, total: null });
    expect(html).not.toContain("filter-count");
  });

  it("checks the option that is chosen, and only that", () => {
    // read the `data-filter` and `aria-checked` of every option out of the HTML: which ones are checked
    const checked = (filter: typeof NO_FILTER) => [...bar(filter).matchAll(/data-filter="(\w+)"[^>]*aria-checked="(true|false)"/g)].filter((m) => m[2] === "true").map((m) => m[1]);
    expect(checked(NO_FILTER)).toEqual(["all"]);
    expect(checked(ONLY_KEPT)).toEqual(["kept"]);
    expect(checked(ONLY_DELETED)).toEqual(["deleted"]);
  });

  it("shows Empty bin only while Deleted is chosen, and off when the whole bin is empty", () => {
    expect(bar(NO_FILTER)).not.toContain("Empty bin");
    expect(bar(ONLY_KEPT)).not.toContain("Empty bin");
    expect(bar(ONLY_DELETED)).toContain("Empty bin");
    expect(bar(ONLY_DELETED)).not.toMatch(/data-action="empty-bin"[^>]*disabled/);
    expect(bar(ONLY_DELETED, { kept: 0, deleted: 0, total: 0 })).toMatch(/disabled=""[^>]*data-action="empty-bin"|data-action="empty-bin"[^>]*disabled=""/);
    expect(bar(ONLY_DELETED, { kept: 0, deleted: 0, total: 4 })).not.toMatch(/disabled=""/); // the other tab has some
  });
});
