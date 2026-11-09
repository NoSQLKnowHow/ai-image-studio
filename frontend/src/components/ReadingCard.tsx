import { binNote, timeAgo, workingNote, type ViewScope } from "../format";
import { canListen, canceledLine, continueText, listenLabel, progressLine, readingFacts, readingTitle, toGoText } from "../speech";
import { zipUrl } from "../speechApi";
import type { SpeechRun } from "../types";
import { DownloadIcon, FolderIcon, PlayIcon, RestoreIcon, ReuseIcon, SpeakerIcon, StopIcon, TrashIcon } from "./icons";
import { ProjectMenu, projectOf, type ProjectControls } from "./ProjectMenu";

// The card of a reading (DESIGN.md §38.2): one card for the whole reading however many parts it has, with its title, where the text came from, the engine and the
// voice, a bar of how far it has got, and the buttons it needs: Listen, Cancel while it is waiting or being made, Continue after a Cancel, Retry after a failure,
// Download, Project and Delete. There is no Keep: a reading never expires (§38.8), so it is kept from the start, and no Reuse, since a reading is made from a text
// and not from a prompt. A reading in the bin has Listen, Download, Restore and Delete forever.

interface Props {
  run: SpeechRun;
  now: number;
  transient: boolean; // in a filtered view only because it is working (DESIGN.md §29.3, §32)
  viewScope: ViewScope; // which filter that is, so the note says what keeps the card there
  projects: ProjectControls; // the project folders, and what the Project button does (§32)
  onListen?: (run: SpeechRun) => void; // open the reading view; absent where there is no player
  onContinue: () => void; // Continue after a Cancel, Retry after a failure: both make the rest, from the first piece not made
  onCancel: () => void;
  onRestore: () => void; // take the reading out of the bin (§30)
  onDelete: () => void;
}

/** The words of the badge: what the reading is doing. A reading that is queued between two parts, with something made, says it is waiting for its turn. */
export function readingStatusLabel(run: SpeechRun): string {
  const waiting = !!run.reading?.waiting;
  switch (run.status) {
    case "queued":
      return waiting ? (run.queue_position ? `Waiting its turn · #${run.queue_position}` : "Waiting its turn") : run.queue_position ? `Queued · #${run.queue_position}` : "Queued";
    case "running":
      return run.canceling ? "Stopping" : "Making reading";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    case "canceled":
      return "Canceled";
  }
}

export function ReadingCard({ run, now, transient, viewScope, projects, onListen, onContinue, onCancel, onRestore, onDelete }: Props) {
  const reading = run.reading;
  const label = readingStatusLabel(run);
  const title = readingTitle(run);
  const active = run.status === "queued" || run.status === "running";
  const inBin = run.deleted_at !== null; // in the bin (DESIGN.md §30)
  const filed = run.project_id !== null; // filed in a project (DESIGN.md §32)
  const project = projectOf(run, projects.projects);
  const made = reading?.pieces_done ?? 0;
  const total = reading?.pieces_total ?? 0;
  const percent = total ? Math.round((made / total) * 100) : 0;
  const toGo = active && reading ? toGoText(reading) : null;
  const place = continueText(reading);
  const listenable = !!onListen && canListen(reading);

  return (
    <article className={`run-card reading-card status-${run.status}`} data-run-id={run.id} aria-label={`${label}: reading, ${title}`}>
      <div className="run-media-col">
        <div className={`run-media empty reading-media status-${run.status}`} aria-hidden="true"><SpeakerIcon /></div>
      </div>
      <div className="run-body">
        <div className="run-head">
          <span className="music-mark" aria-hidden="true"><SpeakerIcon /></span>
          <span className={`badge badge-${run.status === "queued" ? "queued" : run.status}`}>{label}</span>
          {filed && <span className="badge badge-project" data-project-chip><FolderIcon /> {project?.name ?? "Project"}</span>}
          {inBin && <span className="badge badge-deleted"><TrashIcon /> Deleted</span>}
          {/* an engine with a restricted licence says so on the tab (§38.4, criterion 289); the card of what it made repeats it, for the day someone else looks at it */}
          {reading?.licence?.noncommercial && <span className="badge" title={reading.licence.notice}>Non-commercial use only</span>}
          <time dateTime={run.created_at} title={new Date(run.created_at).toLocaleString()}>{timeAgo(run.created_at, now)}</time>
        </div>

        <p className="run-prompt reading-title">{title}</p>
        <p className="run-meta">{readingFacts(run)}</p>

        {/* how far it has got: the part, the paragraph and how much is made (§38.2). Nothing to show until a piece is made, and then it stays: a finished reading
            says how long it is */}
        {reading && total > 0 && (
          <div className="progress reading-progress" role={run.status === "running" ? "status" : undefined}>
            {made > 0 && (
              <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label="How much of the reading is made">
                <span style={{ width: `${percent}%` }} />
              </div>
            )}
            <p>{run.canceling ? "Stopping… what is made stays made." : progressLine(reading)}{toGo ? ` · ${toGo}` : ""}</p>
          </div>
        )}

        {run.status === "canceled" && reading && <p className="run-note">{canceledLine(reading)}. What was made is kept; Continue makes the rest.</p>}

        {/* where the listener stopped, on a reading with something to listen to */}
        {place && !inBin && <p className="run-note reading-place" data-note="place">{place}</p>}

        {/* in a filtered view only because it is working: say so, so that the card being there is not a surprise */}
        {transient && <p className="run-note" data-note={`working-in-${viewScope}`}>{workingNote(viewScope)}</p>}

        {inBin && <p className="run-note" data-note="in-bin">{binNote(run, now)}</p>}

        {run.status === "failed" && run.error && (
          <div className="run-error" role="alert">
            <p>{run.error.message}</p>
            {run.error.hint && <p className="hint">{run.error.hint}</p>}
            {!inBin && <button type="button" className="button small" data-action="continue" onClick={onContinue}><ReuseIcon /> Retry</button>}
          </div>
        )}

        <div className="run-actions">
          {listenable && (
            <button type="button" className="button small" data-action="listen" onClick={() => onListen?.(run)}
              title={place ?? "Listen to it, a piece at a time as it is made if it is still being made"}>
              <PlayIcon /> {listenLabel(reading)}
            </button>
          )}
          {!inBin && active && (
            <button type="button" className="button small ghost danger" data-action="cancel" aria-disabled={run.canceling || undefined} onClick={() => !run.canceling && onCancel()}
              title={run.status === "queued" ? "Take this reading out of the queue (what is made stays)" : run.canceling ? "Stopping after the piece being made" : "Stop making this reading (what is made stays)"}>
              <StopIcon /> {run.canceling ? "Stopping…" : "Cancel"}
            </button>
          )}
          {!inBin && run.status === "canceled" && (
            <button type="button" className="button small" data-action="continue" onClick={onContinue} title="Make the rest, from the first piece that is not made">
              <ReuseIcon /> Continue
            </button>
          )}
          {(reading?.parts_done ?? 0) > 0 && (
            <a className="button small" href={zipUrl(run.id)} download data-action="download" title="The finished parts, one WAV each, with a playlist for a phone's player">
              <DownloadIcon /> Download
            </a>
          )}
          {inBin ? (
            <>
              <button type="button" className="button small" data-action="restore" onClick={onRestore} title="Take this reading out of the bin">
                <RestoreIcon /> Restore
              </button>
              <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete} title="Delete this reading and its audio for good">
                <TrashIcon /> Delete forever
              </button>
            </>
          ) : (
            <>
              <ProjectMenu run={run} controls={projects} />
              <button type="button" className="button small ghost danger" data-action="delete" onClick={onDelete} disabled={run.status === "running"}
                title={run.status === "running" ? "Can't delete while it's being made" : "Delete this reading and its audio"}>
                <TrashIcon /> Delete
              </button>
            </>
          )}
        </div>
      </div>
    </article>
  );
}
