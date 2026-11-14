import { useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { FILE_ACCEPT, pastedTextLine } from "../speech";
import type { SpeechLimits } from "../speechTypes";
import type { SpeechSource, Way } from "../useSpeechSource";

// Where the text to read comes from (DESIGN.md §38.2): pasted, fetched from a web page, or read from a file. Three ways in and one result: the review below. Nothing is
// generated here; this only gets the text and shows what became of it.

const WAYS: { way: Way; label: string }[] = [
  { way: "text", label: "Text" },
  { way: "url", label: "Web page" },
  { way: "file", label: "File" },
];

interface Props {
  source: SpeechSource;
  limits: SpeechLimits;
}

/** The three ways as a radio group with the arrow keys, the way a native one would work (the buttons are drawn like the other tabs' segmented controls). */
function WayPicker({ way, onWay }: { way: Way; onWay: (way: Way) => void }) {
  const refs = useRef<Partial<Record<Way, HTMLButtonElement | null>>>({});
  const move = (e: KeyboardEvent) => {
    const at = WAYS.findIndex((w) => w.way === way);
    const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? (at + 1) % WAYS.length : e.key === "ArrowLeft" || e.key === "ArrowUp" ? (at - 1 + WAYS.length) % WAYS.length : -1;
    if (next === -1) return;
    e.preventDefault();
    onWay(WAYS[next].way);
    refs.current[WAYS[next].way]?.focus();
  };
  return (
    <div className="segmented" role="radiogroup" aria-label="Where the text comes from" onKeyDown={move}>
      {WAYS.map(({ way: id, label }) => (
        <button key={id} type="button" role="radio" aria-checked={way === id} tabIndex={way === id ? 0 : -1} ref={(el) => { refs.current[id] = el; }} onClick={() => onWay(id)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function SpeechSourceBox({ source, limits }: Props) {
  const busy = source.phase === "preparing";
  const [dragging, setDragging] = useState(false);
  const chooser = useRef<HTMLInputElement>(null);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation(); // this box answers for a file dropped on it
    setDragging(false);
    if (busy) return; // one text is being prepared at a time; the buttons and the boxes are off, and so is this
    const first = e.dataTransfer.files[0]; // one file is read; if several are dropped the first is, and the rest are left alone
    if (first) source.chooseFile(first);
  };

  return (
    <section className="speech-source" aria-labelledby="speech-source-title">
      <h2 id="speech-source-title">Text to read</h2>
      <WayPicker way={source.way} onWay={source.setWay} />

      {source.way === "text" && (
        <div className="field">
          <label className="field-label" htmlFor="speech-text">Paste the text</label>
          <textarea id="speech-text" className="speech-text" rows={8} value={source.text} disabled={busy} placeholder="Paste the text to read here. A whole book is fine."
            aria-describedby="speech-text-count" onChange={(e) => source.setText(e.target.value)} />
          <p className="field-hint" id="speech-text-count" aria-live="polite">{pastedTextLine(source.text, limits)}</p>
          <div className="speech-actions">
            <button type="button" className="button primary" disabled={busy || !source.text.trim()} onClick={source.prepareText}>Prepare text</button>
          </div>
        </div>
      )}

      {source.way === "url" && (
        <div className="field">
          <label className="field-label" htmlFor="speech-url">Address of the web page</label>
          <div className="speech-url-row">
            <input id="speech-url" type="url" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://…" value={source.url} disabled={busy}
              aria-describedby="speech-url-hint" onChange={(e) => source.setUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !busy) { e.preventDefault(); source.prepareUrl(); } }} />
            <button type="button" className="button primary" disabled={busy || !source.url.trim()} onClick={source.prepareUrl}>Fetch</button>
          </div>
          <p className="field-hint" id="speech-url-hint">Public web pages only (and text or PDF files at a web address, up to {limits.fetch_mb} MB). Addresses on your own network are refused.</p>
        </div>
      )}

      {source.way === "file" && (
        <div className="field">
          <span className="field-label" id="speech-file-label">File</span>
          <div className={`speech-drop${dragging ? " dragging" : ""}`} data-speech-drop onDragEnter={(e) => { e.preventDefault(); setDragging(true); }}
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
            <input ref={chooser} id="speech-file" type="file" className="sr-only" accept={FILE_ACCEPT} disabled={busy} aria-labelledby="speech-file-label" aria-describedby="speech-file-hint"
              onChange={(e) => { const chosen = e.target.files?.[0]; e.target.value = ""; if (chosen) source.chooseFile(chosen); }} />
            <label htmlFor="speech-file" className={`button${busy ? " disabled" : ""}`}>Choose a file</label>
            <span className="speech-drop-text">or drop one here</span>
            {source.file && <span className="speech-file-name">{source.file.name}</span>}
          </div>
          <p className="field-hint" id="speech-file-hint">A .txt, .md, .pdf, .epub or .docx file, up to {limits.upload_mb} MB.</p>
        </div>
      )}

      <div className="speech-switches">
        <label className="choice">
          <input type="checkbox" checked={source.clean} disabled={busy} onChange={(e) => source.setClean(e.target.checked)} />
          <span>Clean up the text <span className="field-hint">(removes markup, footnote marks and soft hyphens; reads a link as its text)</span></span>
        </label>
        <label className="choice">
          <input type="checkbox" checked={source.readExtras} disabled={busy} onChange={(e) => source.setReadExtras(e.target.checked)} />
          <span>Read code and tables too <span className="field-hint">(they are left out otherwise)</span></span>
        </label>
        <label className="choice">
          <input type="checkbox" checked={source.speakers} disabled={busy} onChange={(e) => source.setSpeakers(e.target.checked)} />
          <span>Find speakers <span className="field-hint">(lines that begin with a name and a colon, as in a script, can be read in different voices)</span></span>
        </label>
      </div>

      {busy && (
        <p className="speech-working" role="status">
          Preparing the text… a whole book can take a minute.
          {source.way === "file" && <button type="button" className="link-button" onClick={source.stop}>Stop</button>}
        </p>
      )}

      {source.problem && (
        <div className="form-error speech-problem" role="alert">
          <p>{source.problem.message}</p>
          {source.problem.hint && <p className="hint">{source.problem.hint}</p>}
        </div>
      )}

      {source.replacing && (
        <div className="speech-replace" role="group" aria-label="Replace the review?">
          <p>This replaces the changes you made to the review below.</p>
          <div className="speech-actions">
            <button type="button" className="button primary" onClick={source.confirmReplace}>Replace the review</button>
            <button type="button" className="button" autoFocus onClick={source.keepReview}>Keep my changes</button>
          </div>
        </div>
      )}
    </section>
  );
}
