import { useId } from "react";
import {
  NO_VOICE_FILTER, filterVoices, isSaved, sampleBody, sampleKey, sampleLabel, sampleWords, savedVoiceFacts, voiceFacts, voiceName, type GenderFilter, type VoiceFilter, type VoicePick,
} from "../speech";
import type { PresetVoice, SavedVoice } from "../speechTypes";
import type { Sampler } from "../useSampler";

// The voices of the engine shown (DESIGN.md §38.2, §38.4): the person's own first, then the presets, each with a Play sample button and what its source states of it
// (gender, language, accent, and "not stated" where it states nothing). The chips filter the presets; the voice that is chosen always stays in the list.

interface Props {
  engine: string; // the engine whose voices these are: a sample is asked for by it
  presets: PresetVoice[];
  saved: SavedVoice[];
  chosen: VoicePick | null;
  onChoose: (pick: VoicePick) => void;
  filter: VoiceFilter;
  onFilter: (filter: VoiceFilter) => void;
  sampler: Sampler;
  loading: boolean;
  problem: { message: string; hint: string | null } | null;
  idle: string | null; // why there is no list (the engine cannot run), or null
}

function Chip({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return <button type="button" className="chip" aria-pressed={on} onClick={onClick}>{label}</button>;
}

function Row({ engine, voice, chosen, onChoose, sampler }: { engine: string; voice: PresetVoice | SavedVoice; chosen: boolean; onChoose: () => void; sampler: Sampler }) {
  const id = useId();
  const key = sampleKey(voice);
  const phase = sampler.phase(key);
  const name = voiceName(voice);
  const problem = sampler.problem?.key === key ? sampler.problem : null;
  return (
    <li className={`speech-voice${chosen ? " chosen" : ""}`}>
      {/* the radio is named by the voice's name alone; what its source states of it is its description, so a screen reader says them apart and not run together */}
      <div className="speech-voice-label">
        <label>
          <input type="radio" name="speech-voice" checked={chosen} aria-describedby={`${id}-facts${!isSaved(voice) && voice.description ? ` ${id}-about` : ""}`} onChange={onChoose} />
          <span className="speech-voice-name">{name}</span>
        </label>
        <span id={`${id}-facts`} className="speech-voice-facts">{isSaved(voice) ? savedVoiceFacts(voice) : voiceFacts(voice)}</span>
        {!isSaved(voice) && voice.description && <span id={`${id}-about`} className="field-hint">{voice.description}</span>}
      </div>
      <button type="button" className="button small speech-sample" aria-label={sampleLabel(name, phase)} aria-busy={phase === "waiting"}
        title={phase === "waiting" ? "Press again to stop waiting for it" : undefined} onClick={() => sampler.play(key, sampleBody({ id: engine }, voice))}>
        {sampleWords(phase)}
      </button>
      {problem && (
        <p className="form-error speech-sample-problem" role="alert">
          {problem.message}
          {problem.hint && <span className="hint"> {problem.hint}</span>}
        </p>
      )}
    </li>
  );
}

export function SpeechVoices({ engine, presets, saved, chosen, onChoose, filter, onFilter, sampler, loading, problem, idle }: Props) {
  const isChosen = (voice: PresetVoice | SavedVoice) => !!chosen && chosen.kind === (isSaved(voice) ? "saved" : "preset") && chosen.id === voice.id;
  const shown = presets.filter((v) => filterVoices([v], filter).length > 0 || isChosen(v));
  const set = (gender: GenderFilter) => onFilter({ ...filter, gender: filter.gender === gender ? "any" : gender });
  const filtering = filter.gender !== "any" || filter.english;

  return (
    <fieldset className="speech-voices">
      <legend>Voice</legend>
      {idle ? (
        <p className="hint">{idle}</p>
      ) : (
        <>
          <div className="speech-chips" role="group" aria-label="Show only">
            <Chip label="Female" on={filter.gender === "female"} onClick={() => set("female")} />
            <Chip label="Male" on={filter.gender === "male"} onClick={() => set("male")} />
            <Chip label="English" on={filter.english} onClick={() => onFilter({ ...filter, english: !filter.english })} />
          </div>
          {loading && <p role="status" className="hint">Loading the voices…</p>}
          {problem && (
            <p className="form-error" role="alert">
              {problem.message}
              {problem.hint && <span className="hint"> {problem.hint}</span>}
            </p>
          )}
          {saved.length > 0 && (
            <>
              <h3 className="speech-voices-heading">My voices</h3>
              <ul className="speech-voice-list" aria-label="My voices">
                {saved.map((voice) => <Row key={voice.id} engine={engine} voice={voice} chosen={isChosen(voice)} onChoose={() => onChoose({ kind: "saved", id: voice.id })} sampler={sampler} />)}
              </ul>
            </>
          )}
          {presets.length > 0 && (
            <>
              {saved.length > 0 && <h3 className="speech-voices-heading">Voices</h3>}
              <ul className="speech-voice-list" aria-label="Voices">
                {shown.map((voice) => <Row key={voice.id} engine={engine} voice={voice} chosen={isChosen(voice)} onChoose={() => onChoose({ kind: "preset", id: voice.id })} sampler={sampler} />)}
              </ul>
              {filtering && shown.length === 0 && <p className="hint">No voice matches those choices.</p>}
              {filtering && (
                <button type="button" className="link-button" onClick={() => onFilter(NO_VOICE_FILTER)}>Show every voice</button>
              )}
            </>
          )}
        </>
      )}
    </fieldset>
  );
}
