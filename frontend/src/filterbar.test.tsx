import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EmptyHistory, FilterBar } from "./components/FilterBar";
import { NO_FILTER, NO_PROJECT, ONLY_DELETED, ONLY_KEPT, withProject, type HistoryFilter } from "./history";
import type { Project } from "./types";

// a callback that does nothing: these tests only look at the HTML that is drawn
const nothing = () => undefined;
// two project folders, as the server lists them (A to Z), with how many runs each holds on each tab
const LOGO: Project = { id: "a".repeat(32), name: "Logo", created_at: "2026-10-02T10:00:00.000Z", counts: { image: 4, music: 1 } };
const PITCH: Project = { id: "b".repeat(32), name: "Pitch deck", created_at: "2026-10-03T10:00:00.000Z", counts: { image: 0, music: 2 } };
// render the empty-list message for a tab and a filter (`binDays` is the STUDIO_BIN_DAYS that the server reported; `project` the folder chosen, if any)
const empty = (kind: "image" | "music", filter: HistoryFilter = ONLY_DELETED, binDays = 30, project: Project | null = null) =>
  renderToStaticMarkup(<EmptyHistory kind={kind} filter={filter} binDays={binDays} project={project} onShowAll={nothing} onShowAnyProject={nothing} />);
// render the filter bar for a filter and counts (a count is null while the server has not said); `projects` null is the list not having arrived
const bar = (
  filter: HistoryFilter = NO_FILTER,
  counts: { kept: number | null; deleted: number | null; total: number | null } = { kept: 2, deleted: 3, total: 5 },
  projects: Project[] | null = [LOGO, PITCH],
  tab: "image" | "music" = "image",
) =>
  renderToStaticMarkup(<FilterBar filter={filter} keptCount={counts.kept} deletedCount={counts.deleted} binTotal={counts.total} projects={projects} tab={tab}
    onChange={nothing} onEmptyBin={nothing} onManageProjects={nothing} />);

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

// ------------------------------------------------------------------ projects (DESIGN.md §32.1)
describe("the Project drop-down in the filter bar", () => {
  // the <option> elements of the Project drop-down, as [value, text] pairs, and which one is selected
  const options = (html: string) => [...html.matchAll(/<option value="([^"]*)"([^>]*)>([^<]*)<\/option>/g)].map((m) => ({ value: m[1], selected: m[2].includes("selected"), text: m[3] }));

  it("offers any project, no project and each project with its number for the tab being looked at", () => {
    expect(options(bar()).map((o) => [o.value, o.text])).toEqual([["", "Any project"], ["none", "No project"], [LOGO.id, "Logo (4)"], [PITCH.id, "Pitch deck (0)"]]);
    // the same list on the Music tab: the numbers are that tab's
    expect(options(bar(NO_FILTER, undefined, undefined, "music")).map((o) => o.text)).toEqual(["Any project", "No project", "Logo (1)", "Pitch deck (2)"]);
  });

  it("selects what the filter says: any, none, or the project", () => {
    expect(options(bar()).find((o) => o.selected)?.value).toBe("");
    expect(options(bar(withProject(NO_FILTER, NO_PROJECT))).find((o) => o.selected)?.value).toBe("none");
    expect(options(bar(withProject(ONLY_KEPT, LOGO.id))).find((o) => o.selected)?.value).toBe(LOGO.id);
  });

  it("shows the Show choice as it was while a project is chosen: Project is a second control, not a fourth option", () => {
    const checked = (filter: HistoryFilter) => [...bar(filter).matchAll(/data-filter="(\w+)"[^>]*aria-checked="(true|false)"/g)].filter((m) => m[2] === "true").map((m) => m[1]);
    expect(checked(withProject(ONLY_KEPT, LOGO.id))).toEqual(["kept"]);
    expect(checked(withProject(NO_FILTER, LOGO.id))).toEqual(["all"]);
    expect(checked(withProject(ONLY_DELETED, NO_PROJECT))).toEqual(["deleted"]);
  });

  it("has a Manage button, and works before the list has arrived", () => {
    expect(bar()).toContain('data-action="manage-projects"');
    expect(options(bar(NO_FILTER, undefined, null)).map((o) => o.value)).toEqual(["", "none"]);
  });

  it("does not claim another project while the one this browser remembers is not in the list: it shows the remembered one as it is", () => {
    const shown = options(bar(withProject(NO_FILTER, "c".repeat(32)), undefined, [LOGO]));
    expect(shown.find((o) => o.selected)).toMatchObject({ value: "c".repeat(32), text: "…" });
  });
});

describe("what an empty list says in a project (DESIGN.md §32.1)", () => {
  it("names the project and the tab, because the other tab may have runs in it, and says how to fill it", () => {
    const html = empty("image", withProject(NO_FILTER, LOGO.id), 30, LOGO);
    expect(html).toContain("Nothing in “Logo” on Images yet");
    expect(html).toContain("Use Add to project on a card to file one here.");
    expect(html).toContain("Show any project");
    expect(empty("music", withProject(NO_FILTER, LOGO.id), 30, LOGO)).toContain("Nothing in “Logo” on Music yet");
  });

  it("says so for the bin of a project, and still names it when the page does not know the project", () => {
    expect(empty("image", withProject(ONLY_DELETED, LOGO.id), 30, LOGO)).toContain("Nothing deleted from “Logo” on Images");
    expect(empty("image", withProject(NO_FILTER, "d".repeat(32)), 30, null)).toContain("Nothing in “this project” on Images yet");
  });

  it("says that every run is in a project, for No project", () => {
    expect(empty("image", withProject(NO_FILTER, NO_PROJECT))).toContain("Every run on this tab is in a project");
    expect(empty("image", withProject(NO_FILTER, NO_PROJECT))).toContain("Show any project");
  });

  it("does not offer Show any project when no project is chosen", () => {
    expect(empty("image", NO_FILTER)).not.toContain("Show any project");
    expect(empty("image", ONLY_KEPT)).not.toContain("Show any project");
  });
});
