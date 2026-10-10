import { useId, useRef, type ReactNode } from "react";
import { NO_FILTER, ONLY_KEPT, type HistoryFilter } from "../history";

// The filter bar above the history (DESIGN.md §29.1): one choice for both tabs. More controls join it as more filters are added
// (§29.5); each is a field of `HistoryFilter` and one more control in this row.

const OPTIONS: { id: "all" | "kept"; label: string; filter: HistoryFilter }[] = [
  { id: "all", label: "All", filter: NO_FILTER },
  { id: "kept", label: "Kept", filter: ONLY_KEPT },
];

/** `keptCount` is how many kept runs the tab being looked at holds (from the server), or null while that is not known. */
export function FilterBar({ filter, keptCount, onChange }: { filter: HistoryFilter; keptCount: number | null; onChange: (filter: HistoryFilter) => void }) {
  const labelId = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const selected = OPTIONS.findIndex((option) => option.filter.kept === filter.kept);
  const go = (index: number) => {
    const next = (index + OPTIONS.length) % OPTIONS.length;
    onChange(OPTIONS[next].filter);
    buttons.current[next]?.focus();
  };
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
            onClick={() => onChange(option.filter)}
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
          </button>
        ))}
      </div>
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

/** What the list says when the tab has nothing to show for the filter (§29.1). */
export function EmptyHistory({ kind, filter, onShowAll }: { kind: "image" | "music"; filter: HistoryFilter; onShowAll: () => void }): ReactNode {
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
      <p className="empty-title">{EMPTY_ALL[kind].title}</p>
      <p>{EMPTY_ALL[kind].body}</p>
    </div>
  );
}
