import { useId } from "react";
import { linesText, pickAvailable, pickFrom, pickKey, type Cast, type VoiceOffer } from "../speakers";
import type { VoicePick } from "../speech";
import type { PreparedSpeaker, PresetVoice, SavedVoice } from "../speechTypes";

// The speakers of a text (DESIGN.md §44.5): the names the review found at the start of lines, each with how many lines begin with it, the first of them, a tick that says whether
// it is a speaker at all, and the voice that speaks for it. A name that is turned off is not a speaker: its lines are read as written, name and all, by the main voice. A name
// that is on and has no voice chosen is read by the main voice, without its name. The voices on offer are the ones the engine can use together with the main voice, and the
// sentence about it (one voice only; one kind of voice) is shown where it applies.

interface Props {
  speakers: readonly PreparedSpeaker[];
  cast: Cast;
  offer: VoiceOffer;
  mainName: string; // the name of the main voice, for the first choice of every list
  max: number; // the most names the server offers (the review says when there were more)
  onChange: (name: string, change: { on?: boolean; voice?: VoicePick | null }) => void;
}

const presetLabel = (v: PresetVoice): string => v.label;
const savedLabel = (v: SavedVoice): string => v.name;

export function SpeechSpeakers({ speakers, cast, offer, mainName, max, onChange }: Props) {
  const id = useId();
  if (speakers.length === 0) return null;
  return (
    <section className="speech-speakers" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>Speakers found ({speakers.length})</h3>
      <p className="hint">
        These names begin lines of the text. Give each a voice; a line with no name, and a name with no voice chosen, is read by the main voice ({mainName}). Turn a name off if it is
        not a speaker: its lines are then read as written, name and all.
      </p>
      {speakers.length >= max && <p className="hint">Only the {max} names that begin the most lines are listed.</p>}
      {offer.note && <p className="hint speech-speakers-note" role="note">{offer.note}</p>}
      <ul className="speech-speaker-list">
        {speakers.map((speaker, i) => {
          const choice = cast[speaker.name] ?? { on: true, voice: null };
          const row = `${id}-${i}`;
          return (
            <li key={speaker.name} className={`speech-speaker${choice.on ? "" : " off"}`}>
              <label className="speech-speaker-on">
                <input type="checkbox" checked={choice.on} onChange={(e) => onChange(speaker.name, { on: e.target.checked })} aria-describedby={`${row}-sample`} />
                <span>Read <strong>{speaker.name}</strong> as a speaker</span>
              </label>
              <span className="speech-speaker-lines">{linesText(speaker.lines)}</span>
              <p className="speech-speaker-sample hint" id={`${row}-sample`}>“{speaker.sample}”</p>
              <div className="speech-speaker-voice">
                <label htmlFor={`${row}-voice`}>Voice for {speaker.name}</label>
                <select id={`${row}-voice`} value={pickAvailable(choice.voice, offer) ? pickKey(choice.voice) : ""} disabled={!choice.on} onChange={(e) => onChange(speaker.name, { voice: pickFrom(e.target.value) })}>
                  <option value="">Main voice ({mainName})</option>
                  {offer.presets.length > 0 && (
                    <optgroup label="Voices">{offer.presets.map((v) => <option key={v.id} value={`preset:${v.id}`}>{presetLabel(v)}</option>)}</optgroup>
                  )}
                  {offer.saved.length > 0 && (
                    <optgroup label="My voices">{offer.saved.map((v) => <option key={v.id} value={`saved:${v.id}`}>{savedLabel(v)}</option>)}</optgroup>
                  )}
                </select>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
