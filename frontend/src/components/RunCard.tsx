import { useState } from "react";
import { canceledText, duration, expiryText, seedText, sizeText, timeAgo } from "../format";
import type { Run, WorkerState } from "../types";
import { AlertIcon, CopyIcon, DownloadIcon, EditIcon, PinIcon, ReuseIcon, StopIcon, TrashIcon } from "./icons";

interface Props {
  run: Run;
  now: number;
  workerState: WorkerState | null;
  onReuse: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onToggleKeep: () => void;
  onDelete: () => void;
  onCopy: () => void;
  onOpenImage: (index: number) => void;
}

const MAX_THUMBS = 4;

function statusLabel(run: Run): string {
  switch (run.status) {
    case "queued":
      return run.queue_position ? `Queued · #${run.queue_position}` : "Queued";
    case "running":
      return run.canceling ? "Stopping" : "Generating";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    case "canceled":
      return "Canceled";
  }
}

function ProgressBlock({ run, workerState }: { run: Run; workerState: WorkerState | null }) {
  const p = run.progress;
  if (!p) {
    const text = run.canceling
      ? "Stopping… (a model that is still loading finishes loading first)"
      : workerState === "loading" ? "Loading the model… (the first load takes a while)" : "Starting…";
    return (
      <div className="progress" role="status">
        <div className="progress-track indeterminate"><span /></div>
        <p>{text}</p>
      </div>
    );
  }
  const fraction = Math.min(1, (p.image - 1 + p.step / Math.max(1, p.steps)) / Math.max(1, p.of));
  return (
    <div className="progress" role="status">
      <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(fraction * 100)} aria-label="Generation progress">
        <span style={{ width: `${(fraction * 100).toFixed(1)}%` }} />
      </div>
      <p>{p.of > 1 ? `Image ${p.image} of ${p.of} · ` : ""}step {p.step} of {p.steps}{run.canceling ? " · stopping after this step…" : ""}</p>
    </div>
  );
}

function Media({ run, onOpenImage }: { run: Run; onOpenImage: (index: number) => void }) {
  const images = run.images;
  if (!images.length) {
    return (
      <div className={`run-media empty status-${run.status}`} aria-hidden="true">
        {run.status === "failed" ? <AlertIcon /> : run.status === "canceled" ? <StopIcon /> : <span className="shimmer" />}
      </div>
    );
  }
  const shown = images.slice(0, MAX_THUMBS);
  return (
    <div className={`run-media grid-${Math.min(images.length, MAX_THUMBS)}`}>
      {shown.map((img, i) => (
        <button key={img.id} type="button" className={`thumb${img.has_alpha ? " checker" : ""}`}
          onClick={() => onOpenImage(i)} aria-label={`Open image ${i + 1} of ${images.length} (seed ${img.seed})`}>
          <img src={img.thumb_url ?? img.url} alt="" loading="lazy" width={img.width} height={img.height} />
          {i === MAX_THUMBS - 1 && images.length > MAX_THUMBS && <span className="more">+{images.length - MAX_THUMBS}</span>}
        </button>
      ))}
    </div>
  );
}

export function RunCard({ run, now, workerState, onReuse, onRetry, onCancel, onToggleKeep, onDelete, onCopy, onOpenImage }: Props) {
  const [expanded, setExpanded] = useState(false);
  const long = run.prompt.length > 240;
  const meta = [sizeText(run), `${run.options.steps} steps`, seedText(run)];
  if (run.options.cfg_scale !== null) meta.push(`guidance ${run.options.cfg_scale}`);
  const took = run.status === "done" ? duration(run.started_at, run.finished_at) : null;
  if (took) meta.push(took);
  const label = statusLabel(run);
  const active = run.status === "queued" || run.status === "running";
  const expiry = run.pinned ? null : expiryText(run.expires_at, now);

  return (
    <article className={`run-card status-${run.status}`} data-run-id={run.id} aria-label={`${label}: ${run.prompt.slice(0, 80)}`}>
      <Media run={run} onOpenImage={onOpenImage} />
      <div className="run-body">
        <div className="run-head">
          <span className={`badge badge-${run.status}`}>{label}</span>
          {run.pinned && <span className="badge badge-kept"><PinIcon /> Kept</span>}
          {run.options.draft && <span className="badge badge-draft" title="A small, quick try. The full-size image will look different.">Draft</span>}
          {run.mode === "edit" && <span className="badge">Edit</span>}
          {run.options.transparent && <span className="badge">Transparent</span>}
          <time dateTime={run.created_at} title={new Date(run.created_at).toLocaleString()}>{timeAgo(run.created_at, now)}</time>
        </div>

        <p className={`run-prompt${long && !expanded ? " clamped" : ""}`}>{run.prompt}</p>
        {long && (
          <button type="button" className="link-button" onClick={() => setExpanded((e) => !e)} aria-expanded={expanded}>
            {expanded ? "Show less" : "Show full prompt"}
          </button>
        )}
        {run.options.negative_prompt && (
          <p className="run-negative"><span>Negative:</span> {run.options.negative_prompt}</p>
        )}
        <p className="run-meta">{meta.join(" · ")}</p>

        {run.status === "running" && <ProgressBlock run={run} workerState={workerState} />}

        {run.status === "canceled" && <p className="run-note">{canceledText(run)}</p>}

        {expiry && <p className="run-expiry">{expiry}. Press <strong>Keep</strong> to save it.</p>}

        {run.status === "failed" && run.error && (
          <div className="run-error" role="alert">
            <p>{run.error.message}</p>
            {run.error.hint && <p className="hint">{run.error.hint}</p>}
            <button type="button" className="button small" onClick={onRetry}><ReuseIcon /> Retry</button>
          </div>
        )}

        <div className="run-actions">
          {active && (
            <button type="button" className="button small ghost danger" data-action="cancel"
              aria-disabled={run.canceling || undefined} onClick={() => !run.canceling && onCancel()}
              title={run.status === "queued" ? "Take this job out of the queue" : run.canceling ? "Stopping after the current step" : "Stop generating (finished images are kept)"}>
              <StopIcon /> {run.canceling ? "Stopping…" : "Cancel"}
            </button>
          )}
          <button type="button" className="button small ghost" data-action="reuse" onClick={onReuse}
            title="Load this prompt and its options, with the seed locked">
            <ReuseIcon /> Reuse
          </button>
          {run.images.length === 1 && (
            <a className="button small ghost" href={run.images[0].download_url} download>
              <DownloadIcon /> Download
            </a>
          )}
          {run.images.length === 1 && run.images[0].thumb_url && (
            <a className="button small ghost" href={`${run.images[0].thumb_url}?download=1`} download
              title="A small copy of this image (WebP, 512 px on the long side)">
              <DownloadIcon /> Thumbnail
            </a>
          )}
          {run.images.length > 1 && (
            <button type="button" className="button small ghost" onClick={() => onOpenImage(0)}>
              <DownloadIcon /> Download…
            </button>
          )}
          <button type="button" className="button small ghost" onClick={onCopy}><CopyIcon /> Copy prompt</button>
          {!active && (
            <button type="button" className={`button small ghost keep${run.pinned ? " active" : ""}`} data-action="keep"
              aria-pressed={run.pinned} onClick={onToggleKeep}
              title={run.pinned ? "Kept: this run is never deleted automatically. Click to stop keeping it." : "Keep this run: it will never be deleted automatically"}>
              <PinIcon /> Keep
            </button>
          )}
          <button type="button" className="button small ghost" disabled title="Edit mode arrives in a later update">
            <EditIcon /> Edit this
          </button>
          <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete} disabled={run.status === "running"}
            title={run.status === "running" ? "Can't delete while it's generating" : "Delete this run and its images"}>
            <TrashIcon /> Delete
          </button>
        </div>
      </div>
    </article>
  );
}
