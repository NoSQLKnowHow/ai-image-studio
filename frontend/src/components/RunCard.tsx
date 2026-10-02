import { useState } from "react";
import { duration, seedText, sizeText, timeAgo } from "../format";
import type { Run, WorkerState } from "../types";
import { AlertIcon, CopyIcon, DownloadIcon, EditIcon, ReuseIcon, TrashIcon } from "./icons";

interface Props {
  run: Run;
  now: number;
  workerState: WorkerState | null;
  onReuse: () => void;
  onRetry: () => void;
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
      return "Generating";
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
    const text = workerState === "loading" ? "Loading the model… (the first load takes a while)" : "Starting…";
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
      <p>{p.of > 1 ? `Image ${p.image} of ${p.of} · ` : ""}step {p.step} of {p.steps}</p>
    </div>
  );
}

function Media({ run, onOpenImage }: { run: Run; onOpenImage: (index: number) => void }) {
  const images = run.images;
  if (!images.length) {
    return (
      <div className={`run-media empty status-${run.status}`} aria-hidden="true">
        {run.status === "failed" ? <AlertIcon /> : <span className="shimmer" />}
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

export function RunCard({ run, now, workerState, onReuse, onRetry, onDelete, onCopy, onOpenImage }: Props) {
  const [expanded, setExpanded] = useState(false);
  const long = run.prompt.length > 240;
  const meta = [sizeText(run), `${run.options.steps} steps`, seedText(run)];
  if (run.options.cfg_scale !== null) meta.push(`guidance ${run.options.cfg_scale}`);
  const took = run.status === "done" ? duration(run.started_at, run.finished_at) : null;
  if (took) meta.push(took);
  const label = statusLabel(run);

  return (
    <article className={`run-card status-${run.status}`} data-run-id={run.id} aria-label={`${label}: ${run.prompt.slice(0, 80)}`}>
      <Media run={run} onOpenImage={onOpenImage} />
      <div className="run-body">
        <div className="run-head">
          <span className={`badge badge-${run.status}`}>{label}</span>
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

        {run.status === "failed" && run.error && (
          <div className="run-error" role="alert">
            <p>{run.error.message}</p>
            {run.error.hint && <p className="hint">{run.error.hint}</p>}
            <button type="button" className="button small" onClick={onRetry}><ReuseIcon /> Retry</button>
          </div>
        )}

        <div className="run-actions">
          <button type="button" className="button small ghost" onClick={onReuse}
            title="Load this prompt and its options, with the seed locked">
            <ReuseIcon /> Reuse
          </button>
          {run.images.length === 1 && (
            <a className="button small ghost" href={run.images[0].download_url} download>
              <DownloadIcon /> Download
            </a>
          )}
          {run.images.length > 1 && (
            <button type="button" className="button small ghost" onClick={() => onOpenImage(0)}>
              <DownloadIcon /> Download…
            </button>
          )}
          <button type="button" className="button small ghost" onClick={onCopy}><CopyIcon /> Copy prompt</button>
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
