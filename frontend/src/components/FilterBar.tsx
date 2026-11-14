import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDownIcon, CloseIcon, FolderIcon, SearchIcon, TrashIcon } from "./icons";
import { fromDate, rangeProblem, sinceOf, toDate, untilOf } from "../dates";
import { NO_FILTER, NO_PROJECT, ONLY_DELETED, ONLY_KEPT, filtersOn, hasSearchOrFilters, searchText, withFamily, withProject, withoutSearch, type HistoryFilter, type TabKind } from "../history";
import type { Facets, Project } from "../types";

// The filter bar above the history (DESIGN.md §29.1): one choice for both tabs. More controls join it as more filters are added
// (§29.5); each is a field of `HistoryFilter` and one more control in this row. Release 1.26 (§45.3) adds the search box on the row and, behind a Filters button, a second row with
// Model, Size (Images only), From and To.

/** How long the search box waits after the last key before it searches (DESIGN.md §45.3): a quarter of a second, or Enter at once. */
export const SEARCH_DELAY_MS = 250;

const OPTIONS: { id: "all" | "kept" | "deleted"; label: string; filter: HistoryFilter }[] = [
  { id: "all", label: "All", filter: NO_FILTER },
  { id: "kept", label: "Kept", filter: ONLY_KEPT },
  { id: "deleted", label: "Deleted", filter: ONLY_DELETED },
];

/** What a size is called in the Size drop-down: `2752×1536`, and the studio's own name for the shape when the size is exactly one of the shapes it offers (`2752×1536 · 16:9`), so
 *  that "the 16:9 ones" can be picked by eye. A shape across sizes is not a filter (DESIGN.md §45.10 choice 11). */
export function sizeLabel(width: number, height: number, shapes: Record<string, [number, number]> | undefined): string {
  const name = Object.entries(shapes ?? {}).find(([, [w, h]]) => w === width && h === height)?.[0];
  return `${width}×${height}${name ? ` · ${name}` : ""}`;
}

/** The search box's own state. What is typed is kept here, and becomes the filter's `q` after a short pause or on Enter; a `q` that the page changes from outside (Clear, Show all, a
 *  family view) is copied into the box. `committed` is the last value this box gave the filter, so that a change it made itself is not mistaken for one made from outside, which would
 *  overwrite what the person has typed since. */
function useSearchBox(filter: HistoryFilter, onChange: (filter: HistoryFilter) => void) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(filter.q ?? "");
  const committed = useRef<string | null>(filter.q);
  const latest = useRef({ filter, onChange });
  latest.current = { filter, onChange };
  const commit = (raw: string) => {
    const next = searchText(raw);
    if (next === committed.current) return;
    committed.current = next;
    latest.current.onChange({ ...latest.current.filter, q: next });
  };
  // Typing: search a moment after the last key
  useEffect(() => {
    const timer = setTimeout(() => commit(text), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text]);
  // Changed from outside: show it
  useEffect(() => {
    if (filter.q !== committed.current) {
      committed.current = filter.q;
      setText(filter.q ?? "");
    }
  }, [filter.q]);
  return {
    id, input, text, setText,
    commitNow: () => commit(text),
    clear: () => { setText(""); commit(""); input.current?.focus(); },
  };
}

/** The second row of the bar (DESIGN.md §45.3): Model, Size (the Images tab only), From and To, and Clear filters. */
function FiltersPanel({ filter, tab, facets, aspectNames, panelId, hidden, onChange }: {
  filter: HistoryFilter; tab: TabKind; facets: Facets | null; aspectNames?: Record<string, [number, number]>; panelId: string; hidden: boolean; onChange: (filter: HistoryFilter) => void;
}) {
  const modelId = useId();
  const sizeId = useId();
  const fromId = useId();
  const toId = useId();
  // The two date boxes hold what was typed; the filter holds the two instants made from them. A pair that cannot be a range is said under the boxes and sent nowhere.
  const [from, setFrom] = useState(fromDate(filter.since));
  const [to, setTo] = useState(toDate(filter.until));
  const problem = rangeProblem(from, to);
  useEffect(() => {
    setFrom(fromDate(filter.since));
    setTo(toDate(filter.until));
  }, [filter.since, filter.until]);
  const setRange = (nextFrom: string, nextTo: string) => {
    setFrom(nextFrom);
    setTo(nextTo);
    if (rangeProblem(nextFrom, nextTo) !== null) return;
    onChange({ ...filter, since: nextFrom === "" ? null : sinceOf(nextFrom), until: nextTo === "" ? null : untilOf(nextTo) });
  };
  const models = facets?.models ?? [];
  const sizes = facets?.sizes ?? [];
  // A value the filter holds that the list does not (the list is a moment behind, or the model has no run left): shown as it is, so that the control never says another value
  const knownModel = filter.model === null || models.some((model) => model.value === filter.model);
  const knownSize = filter.size === null || sizes.some((size) => `${size.width}x${size.height}` === filter.size);
  return (
    <div className="filter-more" id={panelId} data-filters-panel hidden={hidden}>
      <div className="filter-field">
        <label className="filter-label" htmlFor={modelId}>Model</label>
        <select id={modelId} className="filter-select" data-filter-model value={filter.model ?? ""} onChange={(event) => onChange({ ...filter, model: event.target.value === "" ? null : event.target.value })}>
          <option value="">Any model</option>
          {models.map((model) => <option key={model.value} value={model.value}>{`${model.value} (${model.count})`}</option>)}
          {!knownModel && <option value={filter.model ?? ""}>{filter.model}</option>}
        </select>
      </div>
      {/* a size is a picture's: only the Images tab has one (DESIGN.md §45.4 rule 6) */}
      {tab === "image" && (
        <div className="filter-field">
          <label className="filter-label" htmlFor={sizeId}>Size</label>
          <select id={sizeId} className="filter-select" data-filter-size value={filter.size ?? ""} onChange={(event) => onChange({ ...filter, size: event.target.value === "" ? null : event.target.value })}>
            <option value="">Any size</option>
            {sizes.map((size) => <option key={`${size.width}x${size.height}`} value={`${size.width}x${size.height}`}>{`${sizeLabel(size.width, size.height, aspectNames)} (${size.count})`}</option>)}
            {!knownSize && <option value={filter.size ?? ""}>{filter.size}</option>}
          </select>
        </div>
      )}
      <div className="filter-field">
        <label className="filter-label" htmlFor={fromId}>From</label>
        <input id={fromId} type="date" className="filter-select filter-date" data-filter-from value={from} max={to || undefined} aria-invalid={problem !== null || undefined}
          onChange={(event) => setRange(event.target.value, to)} />
      </div>
      <div className="filter-field">
        <label className="filter-label" htmlFor={toId}>To</label>
        <input id={toId} type="date" className="filter-select filter-date" data-filter-to value={to} min={from || undefined} aria-invalid={problem !== null || undefined}
          onChange={(event) => setRange(from, event.target.value)} />
      </div>
      <button type="button" className="button small ghost" data-action="clear-filters" disabled={!hasSearchOrFilters(filter)} onClick={() => onChange(withoutSearch(filter))}
        title="Clear the search and these filters">Clear filters</button>
      {problem && <p className="filter-problem" role="alert" data-filter-problem>{problem}</p>}
    </div>
  );
}

/** `keptCount` and `deletedCount` are how many kept runs and runs in the bin the tab being looked at holds (from the server), or null
 *  while that is not known; with a project chosen they are the project's (DESIGN.md §32.5), and with a search or filters on, theirs (§45.7). `binTotal` is the whole bin, both tabs, whatever
 *  project is chosen: **Empty bin** (shown while Deleted is chosen) empties all of it. `projects` are the project folders, with the numbers
 *  for the tab being looked at (`tab`), in the Project drop-down; null until the list has arrived. `facets` are the models and sizes of this tab's history for the Filters row, and
 *  `resultCount` is the number the live region reads out. */
export function FilterBar({ filter, keptCount, deletedCount, binTotal, projects, tab, onChange, onEmptyBin, onManageProjects, familyLabel, facets, resultCount, aspectNames, searchMax }: {
  filter: HistoryFilter;
  familyLabel?: string | null; // the words of the run a family view is named for (DESIGN.md §43.4), for its pill; null when they are not known
  facets?: Facets | null; // what the Model and Size drop-downs offer for this tab (DESIGN.md §45.7); null until it has arrived
  resultCount?: number | null; // how many runs the chosen Show option finds with the search and filters on, from the server's counts; null while it is not known
  aspectNames?: Record<string, [number, number]>; // the studio's shapes (`capabilities.aspect_ratios`), to name a size that is exactly one of them
  searchMax?: number; // the longest search the server takes
  keptCount: number | null;
  deletedCount: number | null;
  binTotal: number | null;
  projects: Project[] | null;
  tab: TabKind;
  onChange: (filter: HistoryFilter) => void;
  onEmptyBin: () => void;
  onManageProjects: () => void;
}) {
  const labelId = useId();
  const projectId = useId();
  const panelId = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const search = useSearchBox(filter, onChange);
  // The Filters row is closed until it is asked for, and open from the start when a filter is already on (a model chosen on this tab earlier): a hidden filter would be a surprise
  const [open, setOpen] = useState(() => filtersOn(filter) > 0);
  const on = filtersOn(filter);
  // The Show choice (All, Kept, Deleted) is the filter without its project: the project is a separate control and is kept as it was
  const selected = OPTIONS.findIndex((option) => option.filter.kept === filter.kept && option.filter.deleted === filter.deleted);
  const choose = (option: HistoryFilter) => onChange({ ...withProject(option, filter.project), q: filter.q, model: filter.model, size: filter.size, since: filter.since, until: filter.until });
  const go = (index: number) => {
    const next = (index + OPTIONS.length) % OPTIONS.length;
    choose(OPTIONS[next].filter);
    buttons.current[next]?.focus();
  };
  // The Project drop-down's value: "" is any project, "none" is no project, anything else is a project's id
  const projectValue = filter.project ?? "";
  const known = filter.project === null || filter.project === NO_PROJECT || !!projects?.some((project) => project.id === filter.project);
  return (
    <div className="filter-bar" data-filter-bar>
      <span className="filter-label" id={labelId}>Show</span>
      <div className="segmented" role="radiogroup" aria-labelledby={labelId}>
        {OPTIONS.map((option, index) => (
          <button
            key={option.id}
            ref={(element) => { buttons.current[index] = element; }}
            type="button"
            role="radio"
            data-filter={option.id}
            aria-checked={index === selected}
            tabIndex={index === selected || (selected < 0 && index === 0) ? 0 : -1}
            onClick={() => choose(option.filter)}
            onKeyDown={(event) => {
              const to = event.key === "ArrowRight" || event.key === "ArrowDown" ? index + 1
                : event.key === "ArrowLeft" || event.key === "ArrowUp" ? index - 1
                : event.key === "Home" ? 0 : event.key === "End" ? OPTIONS.length - 1 : null;
              if (to === null) return;
              event.preventDefault();
              go(to);
            }}
          >
            {option.label}
            {option.id === "kept" && keptCount !== null && <span className="filter-count"> {keptCount}</span>}
            {option.id === "deleted" && deletedCount !== null && <span className="filter-count"> {deletedCount}</span>}
          </button>
        ))}
      </div>
      {/* The project: any, none, or one of the folders (with how many runs it holds on this tab), and the dialog that manages them */}
      <div className="filter-project">
        <label className="filter-label" htmlFor={projectId}>Project</label>
        <select id={projectId} className="filter-select" data-filter-project value={projectValue}
          onChange={(event) => onChange(withProject(filter, event.target.value === "" ? null : event.target.value))}>
          <option value="">Any project</option>
          <option value={NO_PROJECT}>No project</option>
          {/* the number is the folder's runs on this tab; on the Vector tab the server's list may not count them (§31.12), and then no number is shown, never a 0 */}
          {projects?.map((project) => {
            const count = project.counts[tab];
            return <option key={project.id} value={project.id}>{count === undefined ? project.name : `${project.name} (${count})`}</option>;
          })}
          {/* a project this page remembers but has not found in the list (yet): shown as it is, so the control never claims another value */}
          {!known && <option value={projectValue}>…</option>}
        </select>
        <button type="button" className="button small ghost" data-action="manage-projects" onClick={onManageProjects} title="Make, rename and delete projects">
          <FolderIcon /> Manage
        </button>
      </div>
      {/* The search (DESIGN.md §45.3): a box with a label for a screen reader, a button that clears it, and a live region that says how many runs were found. */}
      <div className="filter-search" role="search">
        <label className="sr-only" htmlFor={search.id}>Search history</label>
        <span className="filter-search-box">
          <SearchIcon />
          <input id={search.id} ref={search.input} type="search" className="filter-search-input" data-filter-search placeholder="Search words, notes and tags" autoComplete="off" spellCheck={false}
            maxLength={searchMax ?? 200} value={search.text} onChange={(event) => search.setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") { event.preventDefault(); search.commitNow(); }
              if (event.key === "Escape" && search.text !== "") { event.preventDefault(); search.clear(); }
            }} />
          {search.text !== "" && (
            <button type="button" className="filter-search-clear" data-action="clear-search" aria-label="Clear the search" onClick={search.clear}><CloseIcon /></button>
          )}
        </span>
        {/* Polite, so it does not interrupt typing: the number is the server's count for the chosen Show option, said once it has arrived. */}
        <span className="filter-result" role="status" aria-live="polite" data-filter-result>
          {hasSearchOrFilters(filter) && resultCount !== null && resultCount !== undefined ? (resultCount === 0 ? "No results" : `${resultCount} ${resultCount === 1 ? "result" : "results"}`) : ""}
        </span>
      </div>
      <button type="button" className="button small ghost filter-toggle" data-action="toggle-filters" aria-expanded={open} aria-controls={open ? panelId : undefined} onClick={() => setOpen(!open)}>
        Filters{on > 0 ? ` · ${on}` : ""} <span className={`filter-chevron${open ? " open" : ""}`}><ChevronDownIcon /></span>
      </button>
      {/* A family (§43.4): chosen from a Variations chip, not from this bar, so all that is here is what it is and the way out. The Show buttons also leave it. */}
      {filter.family !== null && (
        <span className="filter-family" data-filter-family>
          <span className="filter-family-text">Variations of {familyLabel ? `“${familyLabel}”` : "a run"}</span>
          <button type="button" className="button small ghost icon-only" data-action="leave-family" aria-label="Leave this view and show the whole history"
            onClick={() => onChange(withFamily(filter, null))}><CloseIcon /></button>
        </span>
      )}
      {/* Empty bin is only offered while the bin is on show, and is disabled when the whole bin (both tabs) is already empty */}
      {filter.deleted === true && (
        <button type="button" className="button small danger" data-action="empty-bin" disabled={binTotal === 0} onClick={onEmptyBin}
          title={binTotal === 0 ? "The bin is empty" : "Delete everything in the bin for good"}>
          <TrashIcon /> Empty bin
        </button>
      )}
      {/* in the page only while it is open (a closed one would add selects and a disabled button to a bar whose other controls are looked for by kind); the button names it only then */}
      {open && <FiltersPanel filter={filter} tab={tab} facets={facets ?? null} aspectNames={aspectNames} panelId={panelId} hidden={false} onChange={onChange} />}
    </div>
  );
}

/** Put keyboard focus on the chosen option of the filter bar that is showing (the other tab's bar is hidden). Used when the card that
 *  had focus has just left the list (§29.4). */
export function focusFilterBar(): void {
  for (const option of document.querySelectorAll<HTMLElement>('[data-filter-bar] [role="radio"][aria-checked="true"]')) {
    if (option.offsetParent !== null) {
      option.focus();
      return;
    }
  }
}

const EMPTY_ALL: Record<TabKind, { title: string; body: string }> = {
  image: { title: "No images yet", body: "Describe something above and press Generate. Every run lands here with its prompt and settings." },
  music: { title: "No music yet", body: "Describe the music above and press Make music. Every track lands here with its description and settings." },
  vector: { title: "No vectors yet", body: "Describe your idea above and press Make vector. Every SVG lands here with its settings and its layers." },
  speech: { title: "No readings yet", body: "Give it a text above, choose a voice and press Make reading. Every reading lands here, and you can listen to it while it is being made." },
};
// what each tab calls its runs, in the empty states of the Deleted and Kept views, and what the tab is called when a project's view is empty
const EMPTY_DELETED: Record<TabKind, string> = { image: "No deleted images", music: "No deleted music", vector: "No deleted vectors", speech: "No deleted readings" };
const EMPTY_KEPT: Record<TabKind, string> = { image: "No kept images yet", music: "No kept music yet", vector: "No kept vectors yet", speech: "No readings yet" };
const TAB_NAME: Record<TabKind, string> = { image: "Images", music: "Music", vector: "Vector", speech: "Speech" };

/** What is on, in words, for the empty list of a search (DESIGN.md §45.3): `search “fox”, model m, made from 1 Nov to 8 Nov`, then where it was looked for (Kept, Deleted, a project). */
export function narrowedText(filter: HistoryFilter, project: Project | null): string {
  const parts: string[] = [];
  if (filter.q !== null) parts.push(`search “${filter.q}”`);
  if (filter.model !== null) parts.push(`model ${filter.model}`);
  if (filter.size !== null) parts.push(`size ${filter.size.replace("x", "×")}`);
  if (filter.since !== null || filter.until !== null) {
    const from = fromDate(filter.since);
    const to = toDate(filter.until);
    parts.push(from && to ? `made from ${from} to ${to}` : from ? `made from ${from}` : `made up to ${to}`);
  }
  const where: string[] = [];
  if (filter.kept === true) where.push("only runs you kept");
  if (filter.deleted === true) where.push("in Deleted");
  if (filter.project === NO_PROJECT) where.push("in no project");
  else if (filter.project !== null) where.push(`in the project “${project?.name ?? "this project"}”`);
  return `Looked for: ${parts.join(", ")}${where.length ? `; ${where.join(", ")}` : ""}.`;
}

/** What the list says when the tab has nothing to show for the filter (§29.1, §32.1). `project` is the folder chosen, when the filter names one
 *  (null for "no project" or for a project the page does not know). */
export function EmptyHistory({ kind, filter, binDays, project, onShowAll, onShowAnyProject, onClearSearch }: {
  kind: TabKind;
  filter: HistoryFilter;
  binDays: number;
  project: Project | null;
  onShowAll: () => void;
  onShowAnyProject: () => void;
  onClearSearch?: () => void; // clears the search and the filters of the Filters row (DESIGN.md §45.3)
}): ReactNode {
  // A search or a filter is on: nothing matches it, and saying what is on is more use than the tab's usual empty text, which would be untrue here (the tab is not empty)
  if (hasSearchOrFilters(filter) && onClearSearch) {
    return (
      <div className="empty-state" data-empty="narrowed">
        <p className="empty-title">No runs match</p>
        <p>{narrowedText(filter, project)}</p>
        <button type="button" className="button small" data-action="clear-search-and-filters" onClick={onClearSearch}>Clear search and filters</button>
      </div>
    );
  }
  // A project is chosen: say that nothing is in it on this tab (naming the tab, because the other tab may have runs in it), and how to fill it
  if (filter.project !== null && filter.project !== NO_PROJECT) {
    const where = TAB_NAME[kind];
    const name = project?.name ?? "this project";
    return (
      <div className="empty-state">
        <p className="empty-title">{filter.deleted === true ? `Nothing deleted from “${name}” on ${where}` : `Nothing in “${name}” on ${where} yet`}</p>
        <p>{filter.deleted === true ? "Runs deleted from this project stay in Deleted for a while, and come back to it when restored." : "Use Add to project on a card to file one here."}</p>
        <button type="button" className="button small" onClick={onShowAnyProject}>Show any project</button>
      </div>
    );
  }
  // "No project" chosen and every run on the tab is in one
  if (filter.project === NO_PROJECT) {
    return (
      <div className="empty-state">
        <p className="empty-title">Every run on this tab is in a project</p>
        <button type="button" className="button small" onClick={onShowAnyProject}>Show any project</button>
      </div>
    );
  }
  if (filter.deleted === true) {
    return (
      <div className="empty-state">
        <p className="empty-title">{EMPTY_DELETED[kind]}</p>
        <p>{binDays > 0 ? `Runs you delete stay here for ${binDays} ${binDays === 1 ? "day" : "days"} before they are gone for good.` : "There is no bin: a deleted run is deleted for good."}</p>
        <button type="button" className="button small" onClick={onShowAll}>Show all</button>
      </div>
    );
  }
  if (filter.kept === true) {
    return (
      <div className="empty-state">
        <p className="empty-title">{EMPTY_KEPT[kind]}</p>
        {kind === "speech"
          ? <p>Readings are always kept: they are never deleted automatically, and there is nothing to keep by hand.</p>
          : <p>Press <strong>Keep</strong> on a run to keep it here. Kept runs are never deleted automatically.</p>}
        <button type="button" className="button small" onClick={onShowAll}>Show all</button>
      </div>
    );
  }
  if (filter.kept === false) {
    return (
      <div className="empty-state">
        <p className="empty-title">Everything here is kept</p>
        <button type="button" className="button small" onClick={onShowAll}>Show all</button>
      </div>
    );
  }
  return (
    <div className="empty-state">
      {/* no filter: the tab is simply empty, so say how to fill it */}
      <p className="empty-title">{EMPTY_ALL[kind].title}</p>
      <p>{EMPTY_ALL[kind].body}</p>
    </div>
  );
}
