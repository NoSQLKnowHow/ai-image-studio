import { useId, useState } from "react";
import { binNote, canceledText, duration, expiryText, keepLockedTitle, timeAgo, workingNote, type ViewScope } from "../format";
import type { VectorItem, VectorRun, WorkerState } from "../types";
import {
  DEFAULT_PNG_PX, bytesText, coloursText, kindLabel, lengthText, mmText, pngDownloadUrl, smallestFeatureText, vectorMeta, vectorPhases,
  vectorProgressText, vectorTitle, versionLabel,
} from "../vector";
import { AlertIcon, CopyIcon, DownloadIcon, FolderIcon, PinIcon, RestoreIcon, ReuseIcon, StopIcon, TrashIcon, VectorIcon } from "./icons";
import { ProjectMenu, projectOf, type ProjectControls } from "./ProjectMenu";
import { Swatch } from "./Swatch";

// The card of a Vector run (DESIGN.md §31.12.8): what was asked, the progress while it works, and for each finished version the preview, the colours found
// against the colours asked, the table of layers, the smallest feature, every note, and the four ways to take the file away (SVG, layers ZIP, PNG at a size,
// the SVG's code). A Vector run has no viewer and no Make 4K, Enlarge or Delete picture in 1.17: the preview is the whole picture, and the run is binned whole.
//
// The SVG itself is never put in the page: the preview is a PNG (`preview_url`) in an <img>, and "Copy SVG code" fetches the file as text to copy it. A file
// that came from a model, however clean, never runs as part of the page (§31.6 "Safety").

interface Props {
  run: VectorRun;
  now: number;
  workerState: WorkerState | null;
  transient: boolean; // in a filtered view only because it is working (DESIGN.md §29.3, §32)
  viewScope: ViewScope; // which filter that is, so the note says what keeps the card there
  projects: ProjectControls; // the project folders, and what the Project button does (§32)
  pngRange: { min: number; max: number }; // the PNG's long side, in pixels
  onReuse: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onToggleKeep: () => void;
  onRestore: () => void; // take the run out of the bin (§30)
  onDelete: () => void;
  onCopySvg: (item: VectorItem) => void; // fetch the SVG's text and copy it; the page tells the person how it went
}

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
function VectorProgress({ run, workerState }: { run: VectorRun; workerState: WorkerState | null }) {
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

/** The table of a version's layers: swatch and name, how many paths, and the cut length (a rough idea of the time). When the picture's own colour differs from
 *  the layer's (always, in shape mode, where a layer's colour is a label from a fixed list: §31.10 item 7) it is shown under the name. */
function LayerTable({ item }: { item: VectorItem }) {
  if (item.check.layers.length === 0) return null;
  return (
    <div className="vector-layers-wrap" role="region" aria-label="Layers" tabIndex={0}>
      <table className="vector-layers">
        <thead>
          <tr><th scope="col">Layer</th><th scope="col" className="num">Paths</th><th scope="col" className="num">Cut length</th></tr>
        </thead>
        <tbody>
          {item.check.layers.map((layer) => (
            <tr key={layer.id} data-layer-id={layer.id}>
              <th scope="row">
                <span className="layer-name"><Swatch colour={layer.colour} /> {layer.name}</span>
                <span className="layer-colour">
                  {layer.colour}
                  {layer.picture_colour !== layer.colour && <> · in the picture <Swatch colour={layer.picture_colour} small /> {layer.picture_colour}</>}
                </span>
              </th>
              <td className="num">{layer.paths}</td>
              <td className="num">{lengthText(layer.length_mm)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One finished version: its preview, what the check found, and its downloads. */
function VersionBlock({ run, item, pngText, pngInvalid, onPngText, onCopySvg }: {
  run: VectorRun; item: VectorItem; pngText: string; pngInvalid: string | null; onPngText: (text: string) => void; onCopySvg: (item: VectorItem) => void;
}) {
  const id = useId();
  const asked = run.options.num_images;
  const label = versionLabel(item, asked);
  const check = item.check;
  const [wide, tall] = check.size_mm;
  // with several versions the same button names would repeat down the card: name each one for its version (a lone version keeps the plain names)
  const named = (text: string) => (asked > 1 ? `${text}, ${label.toLowerCase()}` : text);
  return (
    <li className="vector-item" data-vector-id={item.id} aria-label={label}>
      <div className="vector-preview">
        {/* the sheet's own shape is reserved before the picture arrives, so the card does not jump. White behind it: white is the material (§31.7 answer 2) */}
        <img src={item.preview_url} alt={`Preview of ${label.toLowerCase()}: ${mmText(wide)} by ${mmText(tall)} mm, ${item.layers} ${item.layers === 1 ? "layer" : "layers"}`}
          style={{ aspectRatio: `${wide} / ${tall}` }} loading="lazy" />
      </div>
      <div className="vector-details">
        <p className="vector-summary">
          <strong>{label}</strong> · seed {item.seed} · {mmText(wide)} × {mmText(tall)} mm · {item.layers} {item.layers === 1 ? "layer" : "layers"} · {bytesText(item.bytes)}
        </p>
        <p className="vector-colours" data-vector-colours>{coloursText(check)}</p>
        <LayerTable item={item} />
        <p className="vector-feature" data-vector-feature>{smallestFeatureText(check.smallest_feature_mm, run.options.min_feature_mm)}</p>
        {check.notes.length > 0 && (
          <ul className="vector-notes" aria-label="Notes">
            {check.notes.map((note, i) => (
              <li key={`${note.code}-${i}`} data-note-code={note.code}><AlertIcon /> <span>{note.message}</span></li>
            ))}
          </ul>
        )}
        <div className="vector-downloads">
          <a className="button small" href={item.download_url} download data-action="download-svg" aria-label={named("Download SVG")} title={`The combined SVG, ${bytesText(item.bytes)}, at its real size in millimetres`}>
            <DownloadIcon /> Download SVG
          </a>
          <a className="button small" href={item.zip_url} download data-action="download-zip" aria-label={named("Download layers (ZIP)")} title="One SVG for each layer, all at the same size and origin, so they register when stacked">
            <DownloadIcon /> Download layers (ZIP)
          </a>
          <button type="button" className="button small" data-action="copy-svg" aria-label={named("Copy SVG code")} onClick={() => onCopySvg(item)}
            title="Copy the SVG's text, to paste into your laser software">
            <CopyIcon /> Copy SVG code
          </button>
        </div>
        {/* a PNG drawn from the SVG at the size typed here: its link is the server's own (`png_download_url`) with the size added */}
        <div className="vector-png">
          <div className="field">
            <label htmlFor={`${id}-png`}>{named("PNG size, long side")}</label>
            <span className="vector-png-row">
              <input id={`${id}-png`} type="number" inputMode="numeric" value={pngText} aria-invalid={pngInvalid !== null} aria-describedby={`${id}-png-help`}
                data-vector-png-size onChange={(e) => onPngText(e.target.value)} />
              <span aria-hidden="true">px</span>
              {pngInvalid === null ? (
                <a className="button small" href={pngDownloadUrl(item, Number(pngText))} download data-action="download-png" aria-label={named("Download PNG")}>
                  <DownloadIcon /> Download PNG
                </a>
              ) : (
                // a size the server would refuse: the link is switched off rather than left to fail
                <button type="button" className="button small" data-action="download-png" aria-disabled="true" aria-label={named("Download PNG")}
                  onClick={(e) => e.preventDefault()}>
                  <DownloadIcon /> Download PNG
                </button>
              )}
            </span>
            <p id={`${id}-png-help`} className={pngInvalid ? "field-error" : "field-hint"} role={pngInvalid ? "alert" : undefined}>
              {pngInvalid ?? "Drawn from the SVG, so it is sharp at any size."}
            </p>
          </div>
        </div>
      </div>
    </li>
  );
}

export function VectorCard({ run, now, workerState, transient, viewScope, projects, pngRange, onRestore, onReuse, onRetry, onCancel, onToggleKeep, onDelete, onCopySvg }: Props) {
  const [expanded, setExpanded] = useState(false);
  // the PNG size is one setting for the card: every version's box shows it, and it is kept as text so that a half-typed number is not lost
  const [pngText, setPngText] = useState(String(DEFAULT_PNG_PX));
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
  const sizeNumber = Number(pngText);
  const pngInvalid = pngText.trim() === "" || !Number.isInteger(sizeNumber) || sizeNumber < pngRange.min || sizeNumber > pngRange.max
    ? `Enter a whole number from ${pngRange.min} to ${pngRange.max}.`
    : null;

  return (
    <article className={`run-card vector-card status-${run.status}`} data-run-id={run.id} aria-label={`${label}: vector, ${vectorTitle(run)}`}>
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

        {run.status === "running" && <VectorProgress run={run} workerState={workerState} />}

        {versions.length > 0 && (
          <ul className="vectors" aria-label="Versions">
            {versions.map((item) => (
              <VersionBlock key={item.id} run={run} item={item} pngText={pngText} pngInvalid={pngInvalid} onPngText={setPngText} onCopySvg={onCopySvg} />
            ))}
          </ul>
        )}

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
              title={run.status === "running" ? "Can't delete while it's being made" : "Delete this run and its files"}>
              <TrashIcon /> Delete
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
