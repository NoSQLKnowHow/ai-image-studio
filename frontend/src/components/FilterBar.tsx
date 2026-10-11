import { useId, useRef, type ReactNode } from "react";
import { FolderIcon, TrashIcon } from "./icons";
import { NO_FILTER, NO_PROJECT, ONLY_DELETED, ONLY_KEPT, withProject, type HistoryFilter } from "../history";
import type { Project } from "../types";

// The filter bar above the history (DESIGN.md §29.1): one choice for both tabs. More controls join it as more filters are added
// (§29.5); each is a field of `HistoryFilter` and one more control in this row.

const OPTIONS: { id: "all" | "kept" | "deleted"; label: string; filter: HistoryFilter }[] = [
  { id: "all", label: "All", filter: NO_FILTER },
  { id: "kept", label: "Kept", filter: ONLY_KEPT },
  { id: "deleted", label: "Deleted", filter: ONLY_DELETED },
];

/** `keptCount` and `deletedCount` are how many kept runs and runs in the bin the tab being looked at holds (from the server), or null
 *  while that is not known; with a project chosen they are the project's (DESIGN.md §32.5). `binTotal` is the whole bin, both tabs, whatever
 *  project is chosen: **Empty bin** (shown while Deleted is chosen) empties all of it. `projects` are the project folders, with the numbers
 *  for the tab being looked at (`tab`), in the Project drop-down; null until the list has arrived. */
export function FilterBar({ filter, keptCount, deletedCount, binTotal, projects, tab, onChange, onEmptyBin, onManageProjects }: {
  filter: HistoryFilter;
  keptCount: number | null;
  deletedCount: number | null;
  binTotal: number | null;
  projects: Project[] | null;
  tab: "image" | "music";
  onChange: (filter: HistoryFilter) => void;
  onEmptyBin: () => void;
  onManageProjects: () => void;
}) {
  const labelId = useId();
  const projectId = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  // The Show choice (All, Kept, Deleted) is the filter without its project: the project is a separate control and is kept as it was
  const selected = OPTIONS.findIndex((option) => option.filter.kept === filter.kept && option.filter.deleted === filter.deleted);
  const choose = (option: HistoryFilter) => onChange(withProject(option, filter.project));
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
          {projects?.map((project) => <option key={project.id} value={project.id}>{project.name} ({project.counts[tab]})</option>)}
          {/* a project this page remembers but has not found in the list (yet): shown as it is, so the control never claims another value */}
          {!known && <option value={projectValue}>…</option>}
        </select>
        <button type="button" className="button small ghost" data-action="manage-projects" onClick={onManageProjects} title="Make, rename and delete projects">
          <FolderIcon /> Manage
        </button>
      </div>
      {/* Empty bin is only offered while the bin is on show, and is disabled when the whole bin (both tabs) is already empty */}
      {filter.deleted === true && (
        <button type="button" className="button small danger" data-action="empty-bin" disabled={binTotal === 0} onClick={onEmptyBin}
          title={binTotal === 0 ? "The bin is empty" : "Delete everything in the bin for good"}>
          <TrashIcon /> Empty bin
        </button>
      )}
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

const EMPTY_ALL = {
  image: { title: "No images yet", body: "Describe something above and press Generate. Every run lands here with its prompt and settings." },
  music: { title: "No music yet", body: "Describe the music above and press Make music. Every track lands here with its description and settings." },
};

/** What the list says when the tab has nothing to show for the filter (§29.1, §32.1). `project` is the folder chosen, when the filter names one
 *  (null for "no project" or for a project the page does not know). */
export function EmptyHistory({ kind, filter, binDays, project, onShowAll, onShowAnyProject }: {
  kind: "image" | "music";
  filter: HistoryFilter;
  binDays: number;
  project: Project | null;
  onShowAll: () => void;
  onShowAnyProject: () => void;
}): ReactNode {
  // A project is chosen: say that nothing is in it on this tab (naming the tab, because the other tab may have runs in it), and how to fill it
  if (filter.project !== null && filter.project !== NO_PROJECT) {
    const where = kind === "music" ? "Music" : "Images";
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
        <p className="empty-title">{kind === "music" ? "No deleted music" : "No deleted images"}</p>
        <p>{binDays > 0 ? `Runs you delete stay here for ${binDays} ${binDays === 1 ? "day" : "days"} before they are gone for good.` : "There is no bin: a deleted run is deleted for good."}</p>
        <button type="button" className="button small" onClick={onShowAll}>Show all</button>
      </div>
    );
  }
  if (filter.kept === true) {
    return (
      <div className="empty-state">
        <p className="empty-title">{kind === "music" ? "No kept music yet" : "No kept images yet"}</p>
        <p>Press <strong>Keep</strong> on a run to keep it here. Kept runs are never deleted automatically.</p>
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
