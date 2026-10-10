import { useState } from "react";
import { WORKING_IN_KEPT_NOTE, binNote, canceledText, duration, expiryText, seedText, sizeText, timeAgo } from "../format";
import { largerTarget, resolutionLabel } from "../options";
import type { FourKTarget, ImageRun, UpscalerStatus, WorkerState } from "../types";
import { FourKButton } from "./FourKButton";
import { AlertIcon, CopyIcon, DownloadIcon, EditIcon, EnlargeIcon, PinIcon, RestoreIcon, ReuseIcon, StopIcon, TrashIcon } from "./icons";

interface Props {
  run: ImageRun;
  now: number;
  workerState: WorkerState | null;
  canEdit: boolean; // the studio can edit, so "Edit this" is offered
  making4k: ReadonlySet<string>; // ids of the images whose 4K copy is being made (DESIGN.md §27)
  enlarging: ReadonlySet<string>; // ids of the images being enlarged with the upscaler model (DESIGN.md §28)
  enlargeWaiting: ReadonlySet<string>; // ids of the images whose Enlarge is waiting for the picture being made (§28.3)
  transient: boolean; // in the Kept view only because it is working (DESIGN.md §29.3)
  upscaler: UpscalerStatus | null; // whether Enlarge can run here (from the capabilities)
  onReuse: () => void;
  onRegenerateLarger: () => void;
  onMake4K: (image: FourKTarget) => void;
  onEnlarge: (image: FourKTarget) => void;
  onEditThis: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onToggleKeep: () => void;
  onRestore: () => void; // take the run out of the bin (DESIGN.md §30)
  onDelete: () => void;
  onCopy: () => void;
  onOpenImage: (index: number) => void;
}

const MAX_THUMBS = 4;

function statusLabel(run: ImageRun): string {
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

function ProgressBlock({ run, workerState }: { run: ImageRun; workerState: WorkerState | null }) {
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

/** An edit's source images, numbered as the prompt refers to them ("image 2"), before its results. Each opens the viewer. */
function Sources({ run, onOpenImage }: { run: ImageRun; onOpenImage: (index: number) => void }) {
  const inputs = [...run.inputs].sort((a, b) => a.position - b.position);
  return (
    <ol className="run-sources" aria-label="Source images">
      {inputs.map((input, i) => (
        <li key={input.id}>
          <button type="button" className={`source-thumb${input.has_alpha ? " checker" : ""}`} onClick={() => onOpenImage(i)}
            aria-label={`Open source image ${input.position} of ${inputs.length}`} title={`Source image ${input.position}`}>
            <img src={input.thumb_url ?? input.url} alt="" loading="lazy" />
            <span className="source-number" aria-hidden="true">{input.position}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

/** The results. `offset` is how many sources come before them in the viewer, which pages through both. */
function Media({ run, offset, onOpenImage }: { run: ImageRun; offset: number; onOpenImage: (index: number) => void }) {
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
          onClick={() => onOpenImage(offset + i)} aria-label={`Open image ${i + 1} of ${images.length} (seed ${img.seed})`}>
          <img src={img.thumb_url ?? img.url} alt="" loading="lazy" width={img.width} height={img.height} />
          {i === MAX_THUMBS - 1 && images.length > MAX_THUMBS && <span className="more">+{images.length - MAX_THUMBS}</span>}
        </button>
      ))}
    </div>
  );
}

export function RunCard({ run, now, workerState, canEdit, making4k, enlarging, enlargeWaiting, transient, upscaler, onReuse, onRegenerateLarger, onMake4K, onEnlarge, onEditThis, onRetry, onCancel, onToggleKeep, onRestore, onDelete, onCopy, onOpenImage }: Props) {
  const [expanded, setExpanded] = useState(false);
  const long = run.prompt.length > 240;
  const edit = run.mode === "edit";
  const meta = edit
    ? [`${run.inputs.length} ${run.inputs.length === 1 ? "image" : "images"}`, sizeText(run), resolutionLabel(run.options.resolution ?? 1024), `${run.options.steps} steps`, seedText(run)]
    : [sizeText(run), `${run.options.steps} steps`, seedText(run)];
  if (run.options.cfg_scale !== null) meta.push(`guidance ${run.options.cfg_scale}`);
  const took = run.status === "done" ? duration(run.started_at, run.finished_at) : null;
  if (took) meta.push(took);
  const label = statusLabel(run);
  const active = run.status === "queued" || run.status === "running";
  const target = largerTarget(run); // the bigger size to regenerate at, when this run was made smaller than selected
  const expiry = run.pinned ? null : expiryText(run.expires_at, now);
  const inBin = run.deleted_at !== null; // in the bin (DESIGN.md §30): look at it, restore it, copy it, or delete it for good

  return (
    <article className={`run-card status-${run.status}`} data-run-id={run.id} aria-label={`${label}: ${run.prompt.slice(0, 80)}`}>
      <div className="run-media-col">
        {run.inputs.length > 0 && <Sources run={run} onOpenImage={onOpenImage} />}
        <Media run={run} offset={run.inputs.length} onOpenImage={onOpenImage} />
      </div>
      <div className="run-body">
        <div className="run-head">
          <span className={`badge badge-${run.status}`}>{label}</span>
          {run.pinned && <span className="badge badge-kept"><PinIcon /> Kept</span>}
          {inBin && <span className="badge badge-deleted"><TrashIcon /> Deleted</span>}
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

        {/* in the Kept view only because it is working: say so, so that the card being there is not a surprise */}
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
              title="Load this prompt and its options, with the seed locked">
              <ReuseIcon /> Reuse
            </button>
            <button type="button" className="button small ghost" onClick={onCopy}><CopyIcon /> Copy prompt</button>
            <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete}
              title="Delete this run and its images for good">
              <TrashIcon /> Delete forever
            </button>
          </div>
        ) : (
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
          {target && (
            <button type="button" className="button small ghost" data-action="regenerate-larger" onClick={onRegenerateLarger}
              aria-label={`Regenerate larger: ${target.width}×${target.height}, ${target.steps} steps`}
              title={`Regenerate at ${target.width}×${target.height}, ${target.steps} steps. The same seed at a bigger size makes a different picture.`}>
              <EnlargeIcon /> Regenerate larger
            </button>
          )}
          {run.images.length === 1 && (
            <a className="button small ghost" href={run.images[0].download_url} download>
              <DownloadIcon /> Download
            </a>
          )}
          {run.images.length === 1 && (
            <FourKButton image={run.images[0]} making={making4k.has(run.images[0].id)} enlarging={enlarging.has(run.images[0].id)} waiting={enlargeWaiting.has(run.images[0].id)}
              upscaler={upscaler} className="button small ghost" onMake={() => onMake4K(run.images[0])}
              onEnlarge={() => onEnlarge(run.images[0])} />
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
          {/* Keep is offered on a card that is still working too: it is how a run that is shown only while it works gets kept */}
          <button type="button" className={`button small ghost keep${run.pinned ? " active" : ""}`} data-action="keep"
            aria-pressed={run.pinned} onClick={onToggleKeep}
            title={run.pinned ? "Kept: this run is never deleted automatically. Click to stop keeping it." : "Keep this run: it will never be deleted automatically"}>
            <PinIcon /> Keep
          </button>
          {canEdit && run.status === "done" && run.images.length === 1 && (
            <button type="button" className="button small ghost" data-action="edit-this" onClick={onEditThis}
              title="Add this picture to the images you are editing">
              <EditIcon /> Edit this
            </button>
          )}
          <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete} disabled={run.status === "running"}
            title={run.status === "running" ? "Can't delete while it's generating" : "Delete this run and its images"}>
            <TrashIcon /> Delete
          </button>
        </div>
        )}
      </div>
    </article>
  );
}
