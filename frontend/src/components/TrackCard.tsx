import { useState } from "react";
import { WORKING_IN_KEPT_NOTE, binNote, duration, expiryText, timeAgo, canceledText } from "../format";
import { musicMeta, musicPhases, musicProgressText, musicTitle, trackLabel } from "../music";
import type { MusicRun, WorkerState } from "../types";
import { CopyIcon, DownloadIcon, NoteIcon, PinIcon, RestoreIcon, ReuseIcon, StopIcon, TrashIcon } from "./icons";

interface Props {
  run: MusicRun;
  now: number;
  workerState: WorkerState | null;
  transient: boolean; // in the Kept view only because it is working (DESIGN.md §29.3)
  onReuse: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onToggleKeep: () => void;
  onRestore: () => void; // take the run out of the bin (DESIGN.md §30)
  onDelete: () => void;
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

export function TrackCard({ run, now, workerState, transient, onRestore, onReuse, onRetry, onCancel, onToggleKeep, onDelete, onCopy }: Props) {
  const [expanded, setExpanded] = useState(false);
  const long = run.prompt.length > 240;
  const label = statusLabel(run);
  const active = run.status === "queued" || run.status === "running";
  const took = run.status === "done" ? duration(run.started_at, run.finished_at) : null;
  const expiry = run.pinned ? null : expiryText(run.expires_at, now);
  const inBin = run.deleted_at !== null; // in the bin (DESIGN.md §30)
  const tracks = [...run.tracks].sort((a, b) => a.idx - b.idx);

  return (
    <article className={`run-card music-card status-${run.status}`} data-run-id={run.id} aria-label={`${label}: music, ${musicTitle(run)}`}>
      <div className="run-body">
        <div className="run-head">
          <span className="music-mark" aria-hidden="true"><NoteIcon /></span>
          <span className={`badge badge-${run.status}`}>{label}</span>
          {run.pinned && <span className="badge badge-kept"><PinIcon /> Kept</span>}
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
              const name = trackLabel(i, run.options.tracks, track.seconds, run.options.duration);
              return (
                <li key={track.id} className="track" data-track-id={track.id}>
                  <div className="track-head">
                    <span className="track-name">{name}</span>
                    <span className="track-seed">seed {track.seed}</span>
                    <a className="button small ghost" href={track.download_url} download data-action="download" aria-label={`Download WAV, ${name}`}>
                      <DownloadIcon /> Download WAV
                    </a>
                  </div>
                  <audio controls preload="metadata" src={track.url} aria-label={`Play: ${name}`} />
                </li>
              );
            })}
          </ul>
        )}

        {run.status === "canceled" && <p className="run-note">{canceledText(run)}</p>}

        {expiry && <p className="run-expiry">{expiry}. Press <strong>Keep</strong> to save it.</p>}

        {transient && <p className="run-note" data-note="working-in-kept">{WORKING_IN_KEPT_NOTE}</p>}

        {inBin && <p className="run-note" data-note="in-bin">{binNote(run, now)}</p>}

        {run.status === "failed" && run.error && (
          <div className="run-error" role="alert">
            <p>{run.error.message}</p>
            {run.error.hint && <p className="hint">{run.error.hint}</p>}
            {!inBin && <button type="button" className="button small" onClick={onRetry}><ReuseIcon /> Retry</button>}
          </div>
        )}

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
          <button type="button" className={`button small ghost keep${run.pinned ? " active" : ""}`} data-action="keep"
            aria-pressed={run.pinned} onClick={onToggleKeep}
            title={run.pinned ? "Kept: this run is never deleted automatically. Click to stop keeping it." : "Keep this run: it will never be deleted automatically"}>
            <PinIcon /> Keep
          </button>
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
