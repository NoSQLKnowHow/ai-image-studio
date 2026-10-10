import { binNote, runMadeText } from "../format";
import { clock } from "../music";
import type { BinnedImage, BinnedTrack, Run } from "../types";
import { FolderIcon, RestoreIcon, TrashIcon } from "./icons";

// The card the Deleted view shows for a run that is still in the history but has pictures (or tracks) of its own in the bin (DESIGN.md §33.1): one card
// for the run, however many of its pictures were deleted, with each deleted picture listed under it. A run that is itself in the bin is an ordinary card
// in the bin (§30.1) and does not use this one: it lists the pictures it has, and not the ones deleted before it (§33.2 item 5).

/** What the card hands back to the page: open a deleted picture to look at it, put one back, or delete one for good. The page asks before the last. */
export interface BinnedItemHandlers {
  onOpen: (run: Run, index: number) => void; // the viewer, read-only, on the run's deleted pictures; `index` is the place in that list
  onRestore: (run: Run, item: BinnedImage | BinnedTrack) => void;
  onDeleteForever: (run: Run, item: BinnedImage | BinnedTrack) => void;
}

/** One deleted picture or track and how the card names it: its own place in the run when it was made (it goes back there), and its seed. */
function name(run: Run, item: BinnedImage | BinnedTrack): string {
  return `${run.mode === "music" ? "Version" : "Image"} ${item.idx + 1} · seed ${item.seed}`;
}

export function BinnedItemsCard({ run, now, projectName, handlers }: { run: Run; now: number; projectName: string | null; handlers: BinnedItemHandlers }) {
  const music = run.mode === "music";
  const items: (BinnedImage | BinnedTrack)[] = music ? run.binned_tracks : run.binned_images;
  const noun = music ? "track" : "picture";
  return (
    <article className="run-card binned-items" data-run-id={run.id} data-card="binned-items"
      aria-label={`Deleted ${noun}${items.length === 1 ? "" : "s"} of: ${run.prompt.slice(0, 80)}`}>
      <div className="run-body">
        <div className="run-head">
          <span className="badge badge-deleted"><TrashIcon /> {items.length} deleted {noun}{items.length === 1 ? "" : "s"}</span>
          {/* the project the run is filed in, as on its own card: the run stays in it with its other pictures */}
          {run.project_id !== null && <span className="badge badge-project" data-project-chip><FolderIcon /> {projectName ?? "Project"}</span>}
          <time dateTime={run.created_at} title={new Date(run.created_at).toLocaleString()}>{runMadeText(run)}</time>
        </div>
        <p className="run-prompt clamped">{run.prompt}</p>
        <ul className="binned-list" aria-label={`Deleted ${noun}s`}>
          {items.map((item, index) => {
            const label = name(run, item);
            return (
              <li key={item.id} className="binned-item" data-item-id={item.id}>
                {music ? (
                  // a track is listened to where it is: the file is still there, and a player needs nothing of the run
                  <audio controls preload="none" src={item.url} aria-label={`Play: ${label}`} />
                ) : (
                  // a picture's thumbnail opens it in the viewer, read-only (§33.1)
                  <button type="button" className="binned-thumb" data-action="open-deleted" aria-label={`Open ${label}`} onClick={() => handlers.onOpen(run, index)}>
                    <img src={(item as BinnedImage).thumb_url ?? item.url} alt="" loading="lazy" />
                  </button>
                )}
                <div className="binned-text">
                  <strong>{label}</strong>
                  <span>{music ? clock((item as BinnedTrack).seconds) : `${(item as BinnedImage).width}×${(item as BinnedImage).height}`}</span>
                  {/* since when it is in the bin, and until when */}
                  <p className="run-note" data-note="in-bin">{binNote(item, now)}</p>
                </div>
                <div className="binned-actions">
                  <button type="button" className="button small" data-action="restore-item" aria-label={`Restore ${label}`} onClick={() => handlers.onRestore(run, item)}
                    title={`Put this ${noun} back in its run, at its own place.`}>
                    <RestoreIcon /> Restore
                  </button>
                  <button type="button" className="button small ghost danger" data-action="delete-item-forever" aria-label={`Delete ${label} for good`}
                    onClick={() => handlers.onDeleteForever(run, item)} title={`Delete this ${noun} for good.`}>
                    <TrashIcon /> Delete forever
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </article>
  );
}
