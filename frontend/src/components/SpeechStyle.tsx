import { languageChoices, styleControl } from "../speech";
import type { PresetVoice, SavedVoice, SpeechEngine } from "../speechTypes";

// How it should sound, and in what language (DESIGN.md §38.2, criterion 264). The style is one box for the whole reading; for a voice that takes none, a line says so
// instead of the box disappearing. The language list is there only where the engine takes one.

interface Props {
  engine: SpeechEngine;
  voice: PresetVoice | SavedVoice | null;
  style: string;
  onStyle: (style: string) => void;
  language: string;
  onLanguage: (language: string) => void;
}

export function SpeechStyle({ engine, voice, style, onStyle, language, onLanguage }: Props) {
  const control = styleControl(engine, voice);
  const languages = languageChoices(engine);
  return (
    <div className="speech-style">
      {control.kind === "box" ? (
        <div className="field">
          <label className="field-label" htmlFor="speech-style">{control.label}</label>
          <textarea id="speech-style" rows={2} maxLength={500} value={style} placeholder="calm and warm, like an audiobook narrator" aria-describedby="speech-style-examples"
            onChange={(e) => onStyle(e.target.value)} />
          <div className="speech-chips" id="speech-style-examples" role="group" aria-label="Examples">
            {control.examples.map((example) => <button key={example} type="button" className="chip" onClick={() => onStyle(example)}>{example}</button>)}
          </div>
        </div>
      ) : (
        <p className="field-hint speech-style-line">{control.text}</p>
      )}
      {languages && (
        <div className="field">
          <label className="field-label" htmlFor="speech-language">Language</label>
          <select id="speech-language" value={languages.includes(language) ? language : "Auto"} onChange={(e) => onLanguage(e.target.value)}>
            {languages.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
      )}
    </div>
  );
}
