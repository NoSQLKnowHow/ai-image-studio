import { useState } from "react";
import { binNote, canceledText, deleteRunTitle, duration, expiryText, keepLockedTitle, timeAgo, workingNote, type ViewScope } from "../format";
import type { VectorItem, VectorRun, WorkerState, WorkerStatus } from "../types";
import { bytesText, kindLabel, layersCount, placeLabel, vectorMeta, vectorPhases, vectorProgressText, vectorTitle } from "../vector";
import { RunFrom } from "../promptLinks";
import { RunAnnotations } from "./RunAnnotations";
import { AlertIcon, CopyIcon, DownloadIcon, FolderIcon, PinIcon, RestoreIcon, ReuseIcon, StopIcon, TrashIcon, VectorIcon } from "./icons";
import { ProjectMenu, projectOf, type ProjectControls } from "./ProjectMenu";
import { VersionFacts } from "./VersionFacts";
import { loadView } from "../loadProgress";
import { LoadProgressView } from "./LoadProgress";

// The card of a Vector run (DESIGN.md §31.12.8, §35.2): one compact card for the run, as an Images card is. At the left a grid of the versions' previews
// (up to four, each on white because white is the bare material, in the sheet's own shape, with the version's place in a corner and its number of layers
// under it); at the right what was asked, the progress while it works, the run's notes and ONE row of the run's buttons. A preview opens the viewer
// (VectorViewer), where a version's facts, downloads and Delete version are. A run with exactly one version also shows what the clean-up found for it, and
// has its three downloads on the card, as an Images card has Download for a single picture.
//
// The SVG itself is never put in the page: the preview is a PNG (`preview_url`) in an <img>, and "Copy SVG code" fetches the file as text to copy it. A file
// that came from a model, however clean, never runs as part of the page (§31.6 "Safety").

interface Props {
  run: VectorRun;
  now: number;
  workerState: WorkerState | null;
  worker?: WorkerStatus | null; // what the worker is loading and how far it has got (DESIGN.md §39), for the run that waits for it
  transient: boolean; // in a filtered view only because it is working (DESIGN.md §29.3, §32)
  viewScope: ViewScope; // which filter that is, so the note says what keeps the card there
  projects: ProjectControls; // the project folders, and what the Project button does (§32)
  onOpenVersion: (index: number) => void; // open the viewer at this version (its place among the run's versions that are not in the bin)
  onReuse: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onToggleKeep: () => void;
  onRestore: () => void; // take the run out of the bin (§30)
  onDelete: () => void;
  onCopySvg: (item: VectorItem) => void; // fetch the SVG's text and copy it; the page tells the person how it went (a run with one version has the button)
}

const MAX_THUMBS = 4;

function statusLabel(run: VectorRun): string {
  switch (run.status) {
    case "queued":
      return run.queue_position ? `Queued · #${run.queue_position}` : "Queued";
    case "running":
      return run.canceling ? "Stopping" : "Making vector";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    case "canceled":
      return "Canceled";
  }
}

/** The two stages of making a vector, each with its bar (§31.12.5): drawing by the model, then tracing by the clean-up. */
function VectorProgress({ run, workerState, worker }: { run: VectorRun; workerState: WorkerState | null; worker?: WorkerStatus | null }) {
  const p = run.progress;
  if (!p) {
    // waiting for the model, and the server can say how far it has got (DESIGN.md §39): say it, instead of the one line that does not move
    if (!run.canceling && loadView(worker)) { // loadView is nothing unless the worker is loading
      return <div className="progress" role="status"><LoadProgressView worker={worker} /></div>;
    }
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
  return (
    <div className="progress" role="status">
      <div className="phases">
        {vectorPhases(p).map((phase) => (
          <div key={phase.key} className="phase" data-phase={phase.key}>
            <span className="phase-label">{phase.label}</span>
            {/* a stage with no steps to count (the clean-up) shows a bar that moves instead of one that fills */}
            {phase.fraction === null ? (
              <div className="progress-track indeterminate" aria-label={`${phase.label} progress`}><span /></div>
            ) : (
              <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(phase.fraction * 100)} aria-label={`${phase.label} progress`}>
                <span style={{ width: `${(phase.fraction * 100).toFixed(1)}%` }} />
              </div>
            )}
          </div>
        ))}
      </div>
      <p>{vectorProgressText(p, run.canceling)}</p>
    </div>
  );
}

/** The grid of previews (§35.2): up to four, then a "+n". Each is a button that opens the viewer at that version. */
function Previews({ run, versions, onOpenVersion }: { run: VectorRun; versions: VectorItem[]; onOpenVersion: (index: number) => void }) {
  if (!versions.length) {
    return (
      <div className={`run-media empty status-${run.status}`} aria-hidden="true">
        {run.status === "failed" ? <AlertIcon /> : run.status === "canceled" ? <StopIcon /> : <span className="shimmer" />}
      </div>
    );
  }
  const shown = versions.slice(0, MAX_THUMBS);
  return (
    <div className={`run-media vector-media grid-${Math.min(versions.length, MAX_THUMBS)}`} data-vector-previews>
      {shown.map((item, i) => (
        <button key={item.id} type="button" className="thumb vector-thumb" data-vector-id={item.id} onClick={() => onOpenVersion(i)}
          aria-label={`Open ${versions.length > 1 ? placeLabel(i + 1, versions.length, item.seed).toLowerCase() : "the vector"} (seed ${item.seed}, ${layersCount(item.layers)})`}>
          {/* the sheet's own shape is reserved before the picture arrives, so the card does not jump; white behind it: white is the material (§31.7 answer 2) */}
          <img src={item.preview_url} alt="" style={{ aspectRatio: `${item.width_mm} / ${item.height_mm}` }} loading="lazy" />
          {versions.length > 1 && <span className="vt-number" aria-hidden="true">{i + 1}</span>}
          <span className="vt-layers" aria-hidden="true">{layersCount(item.layers)}</span>
          {i === MAX_THUMBS - 1 && versions.length > MAX_THUMBS && <span className="more">+{versions.length - MAX_THUMBS}</span>}
        </button>
      ))}
    </div>
  );
}

/** The run's ways to take a file away (§35.2): a run with exactly one version has its three downloads on the card, as an Images card has Download for a single
 *  picture; one with several has Download…, which opens the viewer at the first version, where each version's downloads are. None while nothing is made yet. */
function Downloads({ versions, lone, onOpenVersion, onCopySvg }: { versions: VectorItem[]; lone: VectorItem | null; onOpenVersion: (index: number) => void; onCopySvg: (item: VectorItem) => void }) {
  if (lone) {
    return (
      <>
        <a className="button small" href={lone.download_url} download data-action="download-svg" title={`The combined SVG, ${bytesText(lone.bytes)}, at its real size in millimetres`}>
          <DownloadIcon /> Download SVG
        </a>
        <a className="button small" href={lone.zip_url} download data-action="download-zip" title="One SVG for each layer, all at the same size and origin, so they register when stacked">
          <DownloadIcon /> Download layers (ZIP)
        </a>
        <button type="button" className="button small" data-action="copy-svg" onClick={() => onCopySvg(lone)} title="Copy the SVG's text, to paste into your laser software">
          <CopyIcon /> Copy SVG code
        </button>
      </>
    );
  }
  if (versions.length > 1) {
    return (
      <button type="button" className="button small" data-action="download-more" onClick={() => onOpenVersion(0)}
        title={`Open the first of the ${versions.length} versions: each one's downloads are there`}>
        <DownloadIcon /> Download…
      </button>
    );
  }
  return null;
}

export function VectorCard({ run, now, workerState, worker, transient, viewScope, projects, onOpenVersion, onRestore, onReuse, onRetry, onCancel, onToggleKeep, onDelete, onCopySvg }: Props) {
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
  const versions = [...run.vectors].sort((a, b) => a.idx - b.idx);
  const lone = versions.length === 1 ? versions[0] : null; // a run with exactly one version has its facts and downloads on the card (§35.2)

  return (
    <article className={`run-card vector-card status-${run.status}`} data-run-id={run.id} aria-label={`${label}: vector, ${vectorTitle(run)}`}>
      <div className="run-media-col">
        <Previews run={run} versions={versions} onOpenVersion={onOpenVersion} />
      </div>
      <div className="run-body">
        <div className="run-head">
          <span className="music-mark" aria-hidden="true"><VectorIcon /></span>
          <span className={`badge badge-${run.status}`}>{label}</span>
          {run.pinned && <span className="badge badge-kept"><PinIcon /> Kept</span>}
          {/* the project it is filed in: shown in the bin too, where it cannot be pressed */}
          {filed && <span className="badge badge-project" data-project-chip><FolderIcon /> {project?.name ?? "Project"}</span>}
          {/* a run in the bin says so */}
          {inBin && <span className="badge badge-deleted"><TrashIcon /> Deleted</span>}
          <span className="badge">{kindLabel(run.options.kind)}</span>
          <time dateTime={run.created_at} title={new Date(run.created_at).toLocaleString()}>{timeAgo(run.created_at, now)}</time>
        </div>

        <p className={`run-prompt${long && !expanded ? " clamped" : ""}`}>{run.prompt}</p>
        {long && (
          <button type="button" className="link-button" onClick={() => setExpanded((e) => !e)} aria-expanded={expanded}>
            {expanded ? "Show less" : "Show full prompt"}
          </button>
        )}
        <p className="run-meta">{vectorMeta(run, took).join(" · ")}</p>
        <RunFrom run={run} />
        <RunAnnotations run={run} />

        {run.status === "running" && <VectorProgress run={run} workerState={workerState} worker={worker} />}

        {/* what the clean-up found, for a run that has just the one version; with several, the viewer has them for each */}
        {lone && <div className="vector-lone" data-vector-id={lone.id}><VersionFacts run={run} item={lone} /></div>}

        {/* A run that kept at least one good version is done, and keeps the text that says why another version is missing (§31.12.5) */}
        {run.status === "done" && run.error && (
          <div className="run-note vector-partial" data-note="partial" role="status">
            <p>{run.error.message}</p>
            {run.error.hint && <p className="hint">{run.error.hint}</p>}
          </div>
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

        {/* In the bin a card has only what is safe there: Restore, Reuse (they only read the run) and Delete forever. No Keep, Cancel or Retry: nothing that */}
        {/* changes the run. The downloads above only read it, so they stay. */}
        {inBin ? (
          <div className="run-actions">
            <Downloads versions={versions} lone={lone} onOpenVersion={onOpenVersion} onCopySvg={onCopySvg} />
            <button type="button" className="button small" data-action="restore" onClick={onRestore}
              title="Take this run out of the bin. A kept run is still kept; any other gets a fresh clock.">
              <RestoreIcon /> Restore
            </button>
            <button type="button" className="button small ghost" data-action="reuse" onClick={onReuse}
              title="Load this prompt and its settings, with the seed locked">
              <ReuseIcon /> Reuse
            </button>
            <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete}
              title="Delete this run and its files for good">
              <TrashIcon /> Delete forever
            </button>
          </div>
        ) : (
          <div className="run-actions">
            {active && (
              <button type="button" className="button small ghost danger" data-action="cancel"
                aria-disabled={run.canceling || undefined} onClick={() => !run.canceling && onCancel()}
                title={run.status === "queued" ? "Take this job out of the queue" : run.canceling ? "Stopping after the current step" : "Stop making this vector (finished versions are kept)"}>
                <StopIcon /> {run.canceling ? "Stopping…" : "Cancel"}
              </button>
            )}
            <Downloads versions={versions} lone={lone} onOpenVersion={onOpenVersion} onCopySvg={onCopySvg} />
            <button type="button" className="button small ghost" data-action="reuse" onClick={onReuse}
              title="Load this prompt and its settings, with the seed locked">
              <ReuseIcon /> Reuse
            </button>
            {/* Keep is offered on a card that is still working too: it is how a run that is shown only while it works gets kept */}
            {/* On a filed run Keep is pressed and locked (DESIGN.md §32.3): the button stays, so the tooltip can say why, but it does nothing */}
            <button type="button" className={`button small ghost keep${run.pinned ? " active" : ""}`} data-action="keep"
              aria-pressed={run.pinned} aria-disabled={filed || undefined} data-locked={filed || undefined} onClick={filed ? undefined : onToggleKeep}
              title={filed ? keepLockedTitle(project?.name ?? null) : run.pinned ? "Kept: this run is never deleted automatically. Click to stop keeping it." : "Keep this run: it will never be deleted automatically"}>
              <PinIcon /> Keep
            </button>
            <ProjectMenu run={run} controls={projects} />
            <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete} disabled={run.status === "running"}
              title={run.status === "running" ? "Can't delete while it's being made" : deleteRunTitle(run)}>
              <TrashIcon /> Delete
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
