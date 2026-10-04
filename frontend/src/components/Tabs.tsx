import { useRef } from "react";
import { TABS, tabAfterKey, type TabId } from "../music";

interface Props {
  tab: TabId;
  onTab: (tab: TabId) => void;
  busy: Record<TabId, boolean>; // something is queued or running for that tab
}

export const tabId = (tab: TabId) => `tab-${tab}`;
export const panelId = (tab: TabId) => `panel-${tab}`;

/** Images | Music (DESIGN.md §26.1): ARIA tabs with the arrow keys, Home and End, and a dot on a tab that has work going
 *  on while another tab is open. Selecting follows focus, since both panels are already on the page. */
export function Tabs({ tab, onTab, busy }: Props) {
  const refs = useRef<Partial<Record<TabId, HTMLButtonElement | null>>>({});
  return (
    <div className="tabs" role="tablist" aria-label="What to make"
      onKeyDown={(e) => {
        const next = tabAfterKey(tab, e.key);
        if (!next) return;
        e.preventDefault();
        onTab(next);
        refs.current[next]?.focus();
      }}>
      {TABS.map(({ id, label }) => (
        <button key={id} type="button" role="tab" id={tabId(id)} ref={(el) => { refs.current[id] = el; }}
          aria-selected={tab === id} aria-controls={panelId(id)} tabIndex={tab === id ? 0 : -1}
          className={`tab${busy[id] && tab !== id ? " working" : ""}`} onClick={() => onTab(id)}>
          {label}
          {busy[id] && tab !== id && (
            <>
              <span className="tab-dot" aria-hidden="true" />
              <span className="sr-only"> (working)</span>
            </>
          )}
        </button>
      ))}
    </div>
  );
}
