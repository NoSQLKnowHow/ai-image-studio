import { useId } from "react";
import type { Problem } from "../useSpeechSource";

// Make reading (DESIGN.md §38.2): says what it will do before it does it, and does it. Nothing is made until this is pressed.

interface Props {
  summary: string; // "12 parts, about 11 h 20 min of audio, about 2 GB. Kokoro-82M, Heart."
  blocked: string | null; // why it cannot be pressed yet, or null
  making: boolean; // the request is on its way
  problem: Problem | null; // what the server refused it for
  queued: string | null; // what was said of the last one that was queued
  onMake: () => void;
}

export function SpeechMake({ summary, blocked, making, problem, queued, onMake }: Props) {
  const id = useId();
  return (
    <section className="speech-make" aria-label="Make the reading">
      <p className="speech-summary" id={`${id}-summary`} aria-live="polite">{summary}</p>
      {blocked && <p className="hint" id={`${id}-why`}>{blocked}</p>}
      <div className="speech-actions">
        <button type="button" className="button primary" disabled={!!blocked || making} aria-describedby={blocked ? `${id}-why` : `${id}-summary`} onClick={onMake}>
          {making ? "Queuing…" : "Make reading"}
        </button>
      </div>
      {problem && (
        <div className="form-error speech-problem" role="alert">
          <p>{problem.message}</p>
          {problem.hint && <p className="hint">{problem.hint}</p>}
        </div>
      )}
      {queued && <p className="speech-queued" role="status">{queued}</p>}
    </section>
  );
}
