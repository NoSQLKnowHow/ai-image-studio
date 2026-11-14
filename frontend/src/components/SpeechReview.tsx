import { useId, useRef, useState } from "react";
import { PART_TITLE_MAX, mergeProblem, partChars, partLengthText, reviewLine, skippedText, type DraftPart } from "../speech";
import type { VoiceOffer } from "../speakers";
import type { SpeechLimits } from "../speechTypes";
import type { SpeechSource } from "../useSpeechSource";
import { SpeechSpeakers } from "./SpeechSpeakers";

// The review of a text before it is read (DESIGN.md §38.2): what will be spoken, divided into parts. Each part can be included or left out, renamed, joined to the
// next, and opened to read and change its paragraphs. What the studio leaves out (code, tables) is shown greyed, with the reason. Nothing is made from here.

/** A part of a very long book can have hundreds of paragraphs; they are shown in pages so that opening one does not put thousands of boxes on the page. */
const PARAGRAPHS_AT_A_TIME = 100;

/** Which voices the speakers can be given: what the engine can use together with the main voice (`voicesFor`), and the main voice's name for the first choice of each list. */
export interface SpeakerVoices {
  offer: VoiceOffer;
  mainName: string;
}

const NO_VOICES: SpeakerVoices = { offer: { presets: [], saved: [], note: null }, mainName: "the main voice" };

interface RowProps {
  part: DraftPart;
  index: number;
  parts: readonly DraftPart[];
  limits: SpeechLimits;
  source: SpeechSource;
}

function PartRow({ part, index, parts, limits, source }: RowProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [shown, setShown] = useState(PARAGRAPHS_AT_A_TIME);
  const renameButton = useRef<HTMLButtonElement>(null);
  const number = index + 1;
  const name = part.title || `Part ${number}`;
  const cannotMerge = mergeProblem(parts, index);
  const read = partChars(part) > 0;

  const doneRenaming = () => {
    setRenaming(false);
    renameButton.current?.focus();
  };

  return (
    <li className={`speech-part${part.include ? "" : " left-out"}`}>
      <div className="speech-part-head">
        <label className="speech-include">
          <input type="checkbox" checked={part.include} aria-label={`Include part ${number}: ${name}`} onChange={(e) => source.include(index, e.target.checked)} />
          <span className="speech-include-text">Include</span>
        </label>
        {renaming ? (
          <input className="speech-part-rename" value={part.title} maxLength={PART_TITLE_MAX} aria-label={`Title of part ${number}`} autoFocus
            onChange={(e) => source.rename(index, e.target.value)} onBlur={() => setRenaming(false)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); doneRenaming(); } }} />
        ) : (
          <span className="speech-part-title">{name}</span>
        )}
        <span className="speech-part-meta">
          {part.include ? partLengthText(part, limits) : <span className="badge">Left out</span>}
          {part.note && <span className="badge">{part.note}</span>}
          {part.include && !read && <span className="badge">Nothing to read</span>}
        </span>
      </div>
      <div className="speech-part-actions">
        <button ref={renameButton} type="button" className="button small" onClick={() => setRenaming(true)}>Rename</button>
        <button type="button" className="button small" aria-expanded={open} aria-controls={`${id}-paragraphs`} onClick={() => setOpen(!open)}>
          {open ? "Hide text" : "Show text"}
        </button>
        {/* aria-disabled and not disabled: the button keeps its place in the tab order, so focus is not lost when the last join is made */}
        <button type="button" className="button small" aria-disabled={cannotMerge !== null ? "true" : undefined} aria-describedby={cannotMerge ? `${id}-merge` : undefined} title={cannotMerge ?? "Join this part and the next into one"}
          onClick={() => { if (cannotMerge === null) source.merge(index); }}>
          Merge with next
        </button>
        {cannotMerge && <span id={`${id}-merge`} className="sr-only">{cannotMerge}</span>}
      </div>
      {open && (
        <ol className="speech-paragraphs" id={`${id}-paragraphs`} aria-label={`Text of part ${number}`}>
          {part.paragraphs.slice(0, shown).map((paragraph, j) => (
            <li key={j} className={`speech-paragraph${paragraph.kind === "heading" ? " heading" : ""}${paragraph.skipped ? " skipped" : ""}`}>
              {paragraph.skipped ? (
                <>
                  <p className="speech-skipped-text">{paragraph.text}</p>
                  <span className="badge">{skippedText(paragraph.skipped)}</span>
                </>
              ) : (
                <>
                {/* a line of a speaker shows whose it is: the name is not in the words and is not read; a name turned off is read as written, so its line is shown as text */}
                {paragraph.speaker && (
                  <span className="badge speech-paragraph-speaker" data-speaker={paragraph.speaker}>
                    {source.cast[paragraph.speaker]?.on ?? true ? paragraph.speaker : `${paragraph.label ?? paragraph.speaker} read as text`}
                  </span>
                )}
                <textarea value={paragraph.text} rows={Math.min(12, Math.max(1, Math.ceil(paragraph.text.length / 70)))}
                  aria-label={`${paragraph.kind === "heading" ? "Heading" : "Paragraph"} ${j + 1} of part ${number}`} onChange={(e) => source.edit(index, j, e.target.value)} />
                </>
              )}
            </li>
          ))}
          {part.paragraphs.length > shown && (
            <li className="speech-more">
              <button type="button" className="button small" onClick={() => setShown(shown + PARAGRAPHS_AT_A_TIME)}>
                Show the next {Math.min(PARAGRAPHS_AT_A_TIME, part.paragraphs.length - shown)} of {part.paragraphs.length - shown} paragraphs
              </button>
            </li>
          )}
        </ol>
      )}
    </li>
  );
}

export function SpeechReview({ source, limits, voices = NO_VOICES }: { source: SpeechSource; limits: SpeechLimits; voices?: SpeakerVoices }) {
  const { prepared, parts } = source;
  if (!prepared) return null;
  return (
    <section className="speech-review" aria-labelledby="speech-review-title">
      <h2 id="speech-review-title">What will be read</h2>

      <div className="field">
        <label className="field-label" htmlFor="speech-title">Title of the reading</label>
        <input id="speech-title" type="text" value={source.title} maxLength={PART_TITLE_MAX} onChange={(e) => source.setTitle(e.target.value)} />
      </div>

      {prepared.warnings.length > 0 && (
        <ul className="speech-warnings" aria-label="Things to know about this text">
          {prepared.warnings.map((warning, i) => <li key={i}>{warning}</li>)}
        </ul>
      )}

      {source.switchesStale && (
        <p className="speech-stale" role="status">
          The switches above have changed since this was prepared.{" "}
          <button type="button" className="link-button" disabled={source.phase === "preparing"} onClick={source.prepareAgain}>Prepare again</button> to apply them.
        </p>
      )}

      <p className="speech-review-line" aria-live="polite">{reviewLine(parts, limits)}</p>

      {prepared.cleaning.length > 0 && (
        <details className="speech-cleaning">
          <summary>What the clean-up did ({prepared.cleaning.length})</summary>
          <ul>{prepared.cleaning.map((line, i) => <li key={i}>{line}</li>)}</ul>
        </details>
      )}

      <SpeechSpeakers speakers={prepared.speakers ?? []} cast={source.cast} offer={voices.offer} mainName={voices.mainName} max={limits.speakers_max} onChange={source.setChoice} />

      <ol className="speech-parts" aria-label="Parts">
        {parts.map((part, index) => <PartRow key={part.key} part={part} index={index} parts={parts} limits={limits} source={source} />)}
      </ol>
    </section>
  );
}
