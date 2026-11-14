import { useId } from "react";
import { buildingText, downloadLabel, holdsText, makeLabel, partsSoFar, unavailableText } from "../audiobook";
import type { AudiobookCaps, ReadingInfo } from "../speechTypes";
import { useAudiobook, type AudiobookState } from "../useAudiobook";
import { DownloadIcon } from "./icons";

// The audiobook button of a reading (DESIGN.md §44.4): Make audiobook (M4B), the progress while it is made, and a link to take the finished file. On a reading's card and in its
// player. Nothing is asked of the server until the button is pressed. Where this server's ffmpeg cannot make the file, the button is off and says why in words beside it, and
// the ZIP, which has the parts and the chapter lists, is unaffected.

interface Props {
  runId: string;
  reading: Pick<ReadingInfo, "parts_done" | "parts_total">;
  audiobook?: AudiobookCaps; // `capabilities.speech.audiobook`: what ffmpeg can do here, known before a press
  state?: AudiobookState; // for a test that drives the control from outside; the control makes its own otherwise
}

/** The control, with the hook that follows its build. */
export function AudiobookControl(props: Props) {
  const own = useAudiobook(props.runId);
  return <Control {...props} state={props.state ?? own} />;
}

function Control({ reading, audiobook, state }: Props & { state: AudiobookState }) {
  const id = useId();
  const { status, busy, problem } = state;
  // what the server last said of ffmpeg is newer than what the page was given at the start, so it wins once there is an answer
  const why = unavailableText(status ? status : audiobook);
  const working = busy || status?.state === "building" || status?.state === "waiting";
  const known = status ?? { parts_made: reading.parts_done, parts_total: reading.parts_total, complete: reading.parts_done === reading.parts_total && reading.parts_total > 0 };

  // the finished file: a real link, so that the browser's own download does the work (a click made by a script after a long wait is what a browser refuses)
  if (status?.state === "ready" && status.url) {
    return (
      <div className="audiobook" data-audiobook="ready">
        <a className="button small" href={status.url} download data-action="audiobook-download" aria-describedby={`${id}-note`}>
          <DownloadIcon /> {downloadLabel(status)}
        </a>
        <p className="audiobook-note hint" id={`${id}-note`} role="status">{holdsText(status)}</p>
      </div>
    );
  }

  const label = status?.state === "building" ? "Making the audiobook…" : status?.state === "waiting" ? "Waiting its turn…" : busy ? "Starting…" : makeLabel(known);
  const note = status && (status.state === "building" || status.state === "waiting") ? buildingText(status) : null;
  const failed = status?.state === "failed" ? status.error : null;
  const so = partsSoFar(known);
  return (
    <div className="audiobook" data-audiobook={status?.state ?? (why ? "unavailable" : "idle")}>
      {/* aria-disabled and not disabled: the button keeps its place in the tab order, so that a person using a screen reader reaches it and hears why it is off */}
      <button type="button" className="button small" data-action="audiobook" aria-disabled={why || working ? "true" : undefined} aria-busy={working || undefined}
        aria-describedby={why ? `${id}-why` : note ? `${id}-note` : undefined}
        title={why ?? "Build one audiobook file with a chapter for each part, to download. The ZIP of the parts has chapter lists too."}
        onClick={() => { if (!why && !working) state.make(); }}>
        {label}
      </button>
      {why && <p className="audiobook-note hint" id={`${id}-why`}>{why}</p>}
      {!why && !working && !failed && !problem && so && <p className="audiobook-note hint">The file will hold the parts finished so far.</p>}
      {note && <p className="audiobook-note hint" id={`${id}-note`} role="status">{note}</p>}
      {failed && (
        <p className="audiobook-note form-error" role="alert">
          {failed.message}
          {failed.hint && <span className="hint"> {failed.hint}</span>}
        </p>
      )}
      {problem && (
        <p className="audiobook-note form-error" role="alert">
          {problem.message}
          {problem.hint && <span className="hint"> {problem.hint}</span>}
        </p>
      )}
    </div>
  );
}
