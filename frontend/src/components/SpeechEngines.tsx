import { useId } from "react";
import { ENGINE_STATES, licenceBadge } from "../speech";
import type { SpeechEngine } from "../speechTypes";

// The engines (DESIGN.md §38.2, §38.3): one choice among those this server offers, each with how it is right now and, when it is not ready, why and what to do. The
// engine's licence is shown beside it, and its notice (the non-commercial note, or a licence that has not been checked) under it, as criterion 289 asks.

interface Props {
  engines: SpeechEngine[];
  chosen: string | null;
  onChoose: (id: string) => void;
}

export function SpeechEngines({ engines, chosen, onChoose }: Props) {
  const base = useId();
  return (
    <fieldset className="speech-engines">
      <legend>Engine</legend>
      <ul>
        {engines.map((engine) => {
          const state = ENGINE_STATES[engine.state];
          const why = engine.state === "ready" || engine.state === "checking" ? null : engine.reason;
          const ids = `${base}-${engine.id}`;
          return (
            <li key={engine.id} className={`speech-engine${chosen === engine.id ? " chosen" : ""}`}>
              <div className="speech-engine-head">
                {/* the radio is named by the engine's title alone; how it is and its licence are its description, so a screen reader says them apart and not run together */}
                <label className="speech-engine-label">
                  <input type="radio" name="speech-engine" checked={chosen === engine.id} aria-describedby={`${ids}-state ${ids}-licence`} onChange={() => onChoose(engine.id)} />
                  <span className="speech-engine-title">{engine.title}</span>
                </label>
                <span id={`${ids}-state`} className={`engine-pill ${state.tone}`}>{state.label}</span>
                <span id={`${ids}-licence`} className="badge">{licenceBadge(engine)}</span>
              </div>
              {engine.notes && <p className="field-hint">{engine.notes}</p>}
              {why && (
                <p className="speech-engine-reason">
                  {why}
                  {engine.hint && <span className="hint"> {engine.hint}</span>}
                </p>
              )}
              {engine.licence.notice && <p className="field-hint">{engine.licence.notice}</p>}
            </li>
          );
        })}
      </ul>
    </fieldset>
  );
}
