import { useEffect, useState } from "react";
import { loadAnnouncement, loadView } from "../loadProgress";
import type { WorkerStatus } from "../types";

/** How far the model that is loading has got (DESIGN.md section 39.7): a line saying what, with the numbers beside it, a bar that fills where a size is known and moves where
 *  it is not, and, where one model was unloaded for this one, a sentence saying so. Shows nothing when no load is under way, so a place can always have one.
 *
 *  The numbers change every second and are not live: a screen reader is told only what changes rarely (the phase), and only where `announce` says this is the place to do it. */
export function LoadProgressView({ worker, announce = false }: { worker: WorkerStatus | null | undefined; announce?: boolean }) {
  const view = loadView(worker);
  const headline = view?.headline ?? null;
  const [said, setSaid] = useState("");
  useEffect(() => {
    setSaid(announce && view ? loadAnnouncement(view) : "");
    // the headline stands for the phase and the model: only a change of either is announced
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [announce, headline]);
  if (!view) return null;
  const determinate = view.fraction !== null;
  return (
    <div className="load-progress" data-phase={view.phase}>
      <p className="load-progress-line">
        <span className="load-progress-headline">{view.headline}</span>
        {view.detail && <span className="load-progress-detail" aria-live="off"> · {view.detail}</span>}
      </p>
      <div className={`progress-track${determinate ? "" : " indeterminate"}`} role="progressbar" aria-label={view.headline} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={determinate ? Math.round((view.fraction as number) * 100) : undefined}>
        <span style={determinate ? { width: `${((view.fraction as number) * 100).toFixed(1)}%` } : undefined} />
      </div>
      {view.after && <p className="hint load-progress-after">{view.after}</p>}
      {announce && <span className="sr-only" role="status">{said}</span>}
    </div>
  );
}
