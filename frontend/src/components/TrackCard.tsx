import { useState } from "react";
import { binNote, duration, expiryText, keepLockedTitle, timeAgo, canceledText, workingNote, type ViewScope } from "../format";
import { musicMeta, musicPhases, musicProgressText, musicTitle, trackLabel } from "../music";
import type { MusicRun, TrackInfo, WorkerState } from "../types";
import { CopyIcon, DownloadIcon, FolderIcon, NoteIcon, PinIcon, RestoreIcon, ReuseIcon, StopIcon, TrashIcon } from "./icons";
import { ProjectMenu, projectOf, type ProjectControls } from "./ProjectMenu";

interface Props {
  run: MusicRun;
  now: number;
  workerState: WorkerState | null;
  transient: boolean; // in a filtered view only because it is working (DESIGN.md §29.3, §32)
  viewScope: ViewScope; // which filter that is, so the note says what keeps the card there
  projects: ProjectControls; // the project folders, and what the Project button does (DESIGN.md §32)
  onReuse: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onToggleKeep: () => void;
  onRestore: () => void; // take the run out of the bin (DESIGN.md §30)
  onDelete: () => void;
  onDeleteTrack: (track: TrackInfo, position: number, of: number) => void; // Delete track (DESIGN.md §33.1): which one, of how many
  onCopy: () => void;
}

function statusLabel(run: MusicRun): string {
  switch (run.status) {
    case "queued":
      return run.queue_position ? `Queued · #${run.queue_position}` : "Queued";
    case "running":
      return run.canceling ? "Stopping" : "Making music";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    case "canceled":
      return "Canceled";
  }
}

/** Two bars, one per phase (DESIGN.md §26.1): composing, then rendering. */
function MusicProgress({ run, workerState }: { run: MusicRun; workerState: WorkerState | null }) {
  const p = run.progress;
  if (!p) {
    const text = run.canceling
      ? "Stopping… (a model that is still loading finishes loading first)"
      : workerState === "loading" ? "Loading the music model… (the first load takes a while)" : "Starting…";
    return (
      <div className="progress" role="status">
        <div className="progress-track indeterminate"><span /></div>
        <p>{text}</p>
      </div>
    );
  }
  return (
    <div className="progress" role="status">
      <div className="phases">
        {musicPhases(p).map((phase) => (
          <div key={phase.key} className="phase" data-phase={phase.key}>
            <span className="phase-label">{phase.label}</span>
            <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={Math.round(phase.fraction * 100)} aria-label={`${phase.label} progress`}>
              <span style={{ width: `${(phase.fraction * 100).toFixed(1)}%` }} />
            </div>
          </div>
        ))}
      </div>
      <p>{musicProgressText(p, run.canceling)}</p>
    </div>
  );
}

export function TrackCard({ run, now, workerState, transient, viewScope, projects, onRestore, onReuse, onRetry, onCancel, onToggleKeep, onDelete, onDeleteTrack, onCopy }: Props) {
  const [expanded, setExpanded] = useState(false);
  const long = run.prompt.length > 240;
  const label = statusLabel(run);
  const active = run.status === "queued" || run.status === "running";
  const took = run.status === "done" ? duration(run.started_at, run.finished_at) : null;
  const expiry = run.pinned ? null : expiryText(run.expires_at, now);
  const inBin = run.deleted_at !== null; // in the bin (DESIGN.md §30)
  // The project it is filed in (DESIGN.md §32): a filed run is always kept, so its Keep button is pressed and locked
  const filed = run.project_id !== null;
  const project = projectOf(run, projects.projects);
  const tracks = [...run.tracks].sort((a, b) => a.idx - b.idx);
  // "Version 2 of 3": while the run is being made, of how many it was asked for; once it has finished, of how many there ARE, so that after one is deleted
  // the others are numbered again by position (DESIGN.md §33.1)
  const total = active ? run.options.tracks : tracks.length;
  // a track can be deleted on its own from a finished run that is not in the bin, while it has another: its last would send the run, which Delete does (§33.1)
  const canDeleteTrack = !active && !inBin && tracks.length > 1;

  return (
    <article className={`run-card music-card status-${run.status}`} data-run-id={run.id} aria-label={`${label}: music, ${musicTitle(run)}`}>
      <div className="run-body">
        <div className="run-head">
          <span className="music-mark" aria-hidden="true"><NoteIcon /></span>
          <span className={`badge badge-${run.status}`}>{label}</span>
          {run.pinned && <span className="badge badge-kept"><PinIcon /> Kept</span>}
          {/* the project it is filed in: shown in the bin too, where it cannot be pressed */}
          {filed && <span className="badge badge-project" data-project-chip><FolderIcon /> {project?.name ?? "Project"}</span>}
          {/* a run in the bin says so */}
          {inBin && <span className="badge badge-deleted"><TrashIcon /> Deleted</span>}
          <span className="badge">{run.options.instrumental ? "Instrumental" : "With lyrics"}</span>
          <time dateTime={run.created_at} title={new Date(run.created_at).toLocaleString()}>{timeAgo(run.created_at, now)}</time>
        </div>

        <p className={`run-prompt${long && !expanded ? " clamped" : ""}`}>{run.prompt}</p>
        {long && (
          <button type="button" className="link-button" onClick={() => setExpanded((e) => !e)} aria-expanded={expanded}>
            {expanded ? "Show less" : "Show full description"}
          </button>
        )}
        {run.lyrics && (
          <details className="run-lyrics">
            <summary>Lyrics</summary>
            <pre>{run.lyrics}</pre>
          </details>
        )}
        <p className="run-meta">{musicMeta(run, took).join(" · ")}</p>

        {run.status === "running" && <MusicProgress run={run} workerState={workerState} />}

        {tracks.length > 0 && (
          <ul className="tracks" aria-label="Tracks">
            {tracks.map((track, i) => {
              const name = trackLabel(i, total, track.seconds, run.options.duration);
              return (
                <li key={track.id} className="track" data-track-id={track.id}>
                  <div className="track-head">
                    <span className="track-name">{name}</span>
                    <span className="track-seed">seed {track.seed}</span>
                    <a className="button small ghost" href={track.download_url} download data-action="download" aria-label={`Download WAV, ${name}`}>
                      <DownloadIcon /> Download WAV
                    </a>
                    {canDeleteTrack && (
                      <button type="button" className="button small ghost danger" data-action="delete-track" aria-label={`Delete track: ${name}`}
                        onClick={() => onDeleteTrack(track, i + 1, tracks.length)} title="Delete this track. It goes to Deleted for a while, and can be restored from there.">
                        <TrashIcon /> Delete track
                      </button>
                    )}
                  </div>
                  <audio controls preload="metadata" src={track.url} aria-label={`Play: ${name}`} />
                </li>
              );
            })}
          </ul>
        )}

        {run.status === "canceled" && <p className="run-note">{canceledText(run)}</p>}

        {expiry && <p className="run-expiry">{expiry}. Press <strong>Keep</strong> to save it.</p>}

        {/* in a filtered view only because it is working: say so, so that the card being there is not a surprise */}
        {transient && <p className="run-note" data-note={`working-in-${viewScope}`}>{workingNote(viewScope)}</p>}

        {/* since when it is in the bin, and until when */}
        {inBin && <p className="run-note" data-note="in-bin">{binNote(run, now)}</p>}

        {run.status === "failed" && run.error && (
          <div className="run-error" role="alert">
            <p>{run.error.message}</p>
            {run.error.hint && <p className="hint">{run.error.hint}</p>}
            {/* no Retry in the bin: restore the run first */}
            {!inBin && <button type="button" className="button small" onClick={onRetry}><ReuseIcon /> Retry</button>}
          </div>
        )}

        {/* In the bin a card has only what is safe there: Restore, Reuse and Copy (they only read the run) and Delete forever. No Keep, Cancel */}
        {/* or Retry: nothing that changes the run. */}
        {inBin ? (
          <div className="run-actions">
            <button type="button" className="button small" data-action="restore" onClick={onRestore}
              title="Take this run out of the bin. A kept run is still kept; any other gets a fresh clock.">
              <RestoreIcon /> Restore
            </button>
            <button type="button" className="button small ghost" data-action="reuse" onClick={onReuse}
              title="Load this description, its lyrics and settings, with the seed locked">
              <ReuseIcon /> Reuse
            </button>
            <button type="button" className="button small ghost" onClick={onCopy}><CopyIcon /> Copy description</button>
            <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete}
              title="Delete this run and its tracks for good">
              <TrashIcon /> Delete forever
            </button>
          </div>
        ) : (
        <div className="run-actions">
          {active && (
            <button type="button" className="button small ghost danger" data-action="cancel"
              aria-disabled={run.canceling || undefined} onClick={() => !run.canceling && onCancel()}
              title={run.status === "queued" ? "Take this job out of the queue" : run.canceling ? "Stopping after the current step" : "Stop making this track (finished tracks are kept)"}>
              <StopIcon /> {run.canceling ? "Stopping…" : "Cancel"}
            </button>
          )}
          <button type="button" className="button small ghost" data-action="reuse" onClick={onReuse}
            title="Load this description, its lyrics and settings, with the seed locked">
            <ReuseIcon /> Reuse
          </button>
          <button type="button" className="button small ghost" onClick={onCopy}><CopyIcon /> Copy description</button>
          {/* Keep is offered on a card that is still working too: it is how a run that is shown only while it works gets kept */}
          {/* On a filed run Keep is pressed and locked (DESIGN.md §32.3): the button stays, so the tooltip can say why, but it does nothing */}
          <button type="button" className={`button small ghost keep${run.pinned ? " active" : ""}`} data-action="keep"
            aria-pressed={run.pinned} aria-disabled={filed || undefined} data-locked={filed || undefined} onClick={filed ? undefined : onToggleKeep}
            title={filed ? keepLockedTitle(project?.name ?? null) : run.pinned ? "Kept: this run is never deleted automatically. Click to stop keeping it." : "Keep this run: it will never be deleted automatically"}>
            <PinIcon /> Keep
          </button>
          <ProjectMenu run={run} controls={projects} />
          <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete} disabled={run.status === "running"}
            title={run.status === "running" ? "Can't delete while it's being made" : "Delete this run and its tracks"}>
            <TrashIcon /> Delete
          </button>
        </div>
        )}
      </div>
    </article>
  );
}
