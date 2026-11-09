import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import { createEmitter, type Emitter } from "../emitter";
import type { ToastKind } from "../hooks";
import type { KeyValueStore } from "../options";
import {
  chooseEngine, engineUsable, findVoice, firstVoice, queuedText, readChoice, readingProblem, readingTotals, saveChoice, totalsLine, buildReadingBody, voiceName,
  type SpeechChoice, type VoicePick, type VoiceFilter,
} from "../speech";
import { speechApi } from "../speechApi";
import type { PresetVoice, SavedVoice, SpeechJob } from "../speechTypes";
import { isSpeechRun, type Capabilities, type SpeechRun } from "../types";
import { useEngineVoices } from "../useEngineVoices";
import { useSampler } from "../useSampler";
import { useSpeechSource, type Problem } from "../useSpeechSource";
import { SpeechEngines } from "./SpeechEngines";
import { SpeechMake } from "./SpeechMake";
import { SpeechReview } from "./SpeechReview";
import { SpeechSourceBox } from "./SpeechSource";
import { SpeechStyle } from "./SpeechStyle";
import { SpeechVoices } from "./SpeechVoices";
import { panelId, tabId } from "./Tabs";

// The Speech tab (DESIGN.md §38.2): text in, read aloud in a voice. This file is the tab's frame and what ties its parts together: whether speech can work here at all,
// the source of the text and the review made of it, the engine and the voice (remembered), and Make reading. The readings themselves, in the history, are added to it
// as they are built.

const NO_PRESETS: PresetVoice[] = []; // one array each, so that nothing re-renders for a new empty one
const NO_SAVED: SavedVoice[] = [];
const NO_JOBS: Emitter<SpeechJob> = createEmitter<SpeechJob>();

export interface SpeechPanelProps {
  hidden: boolean;
  caps: Capabilities;
  store: KeyValueStore; // where the choice of engine, voice, style and language is remembered
  push?: (kind: ToastKind, text: string) => void;
  jobs?: Emitter<SpeechJob>; // the `speech.job` events: a sample or a design that has moved on
  onEngine?: (engine: string | null) => void; // which engine the tab shows, for the header's Load and Unload
  onQueued?: (run: SpeechRun) => void; // a reading was queued: it joins the page's runs
}

export function SpeechPanel({ hidden, caps, store, push, jobs = NO_JOBS, onEngine, onQueued }: SpeechPanelProps) {
  const speech = caps.speech;
  const limits = caps.limits.speech;
  const source = useSpeechSource(limits);
  const [choice, setChoice] = useState<SpeechChoice>(() => readChoice(store));
  const [making, setMaking] = useState(false);
  const [makeProblem, setMakeProblem] = useState<Problem | null>(null);
  const [queued, setQueued] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const sampler = useSampler({ audio, jobs });
  const { stop } = sampler;

  // the choice is remembered as it changes (DESIGN.md §38.2: "the last choice is remembered")
  useEffect(() => saveChoice(choice, store), [choice, store]);
  const update = (changes: Partial<SpeechChoice>) => setChoice((now) => ({ ...now, ...changes }));

  // the engine shown: the one remembered if it can be used, else the first that can; and its voices, once it is ready
  const engine = chooseEngine(speech, choice.engine);
  const usable = !!engine && engineUsable(engine);
  const { voices, loading, problem: voicesProblem } = useEngineVoices(usable ? engine.id : null);
  const presets = voices?.presets ?? NO_PRESETS;
  const saved = voices?.saved ?? NO_SAVED;
  const remembered = choice.voice && findVoice(choice.voice, presets, saved) ? choice.voice : null;
  const pick: VoicePick | null = remembered ?? firstVoice(presets, saved, choice.filter);
  const voice = findVoice(pick, presets, saved);

  const engineId = engine?.id ?? null;
  useEffect(() => onEngine?.(engineId), [engineId, onEngine]); // the header's button acts on the engine shown
  useEffect(() => { if (hidden) stop(); }, [hidden, stop]); // no sound from a tab that is not on the screen

  const chooseEngineById = (id: string) => {
    stop();
    update({ engine: id });
  };
  const chooseVoice = (next: VoicePick) => update({ voice: next });
  const setFilter = (filter: VoiceFilter) => update({ filter });

  const prepared = source.prepared;
  const totals = engine ? readingTotals(source.parts, limits, engine.sample_rate) : null;
  const blocked = prepared ? readingProblem(source.parts, engine, voice) : null;
  const summary = engine && totals ? totalsLine(totals, engine.title, voice ? voiceName(voice) : "") : "";

  const make = async () => {
    if (!prepared || !engine || !voice || blocked) return;
    setMaking(true);
    setMakeProblem(null);
    setQueued(null);
    try {
      const run = await speechApi.makeReading(buildReadingBody(source.title, prepared.source, source.parts, engine, voice, choice.style, choice.language));
      if (isSpeechRun(run)) {
        const text = queuedText(run);
        onQueued?.(run);
        setQueued(text);
        push?.("success", text);
      }
    } catch (error) {
      setMakeProblem(error instanceof ApiError ? { message: error.message, hint: error.hint } : { message: "The reading could not be queued.", hint: null });
    } finally {
      setMaking(false);
    }
  };

  const checking = !speech.available && speech.engines.some((e) => e.state === "checking");
  const idle = engine && !usable ? (engine.state === "checking" ? `${engine.title} is still being checked.` : "Choose an engine that is ready to see its voices.") : null;

  return (
    <div className="tab-panel speech-panel" role="tabpanel" id={panelId("speech")} aria-labelledby={tabId("speech")} hidden={hidden}>
      {!speech.available ? (
        <section className="speech-unavailable" aria-label="Speech is not available">
          <h2>{checking ? "Checking the speech engines…" : "Speech isn't available on this server"}</h2>
          {checking ? (
            <p role="status">Each engine is being checked. This takes a moment after the server starts.</p>
          ) : (
            <>
              <p role="status">{speech.reason ?? "No speech engine can run on this server as it is set up."}</p>
              {speech.hint && <p className="hint">{speech.hint}</p>}
            </>
          )}
          <p className="hint">Images, music and vector work as usual.</p>
        </section>
      ) : (
        <>
          <SpeechSourceBox source={source} limits={limits} />
          <SpeechReview source={source} limits={limits} />
          <section className="speech-voice-section" aria-labelledby="speech-voice-title">
            <h2 id="speech-voice-title">Voice</h2>
            <SpeechEngines engines={speech.engines} chosen={engineId} onChoose={chooseEngineById} />
            {engine && (
              <>
                <SpeechVoices engine={engine.id} presets={presets} saved={saved} chosen={pick} onChoose={chooseVoice} filter={choice.filter} onFilter={setFilter} sampler={sampler}
                  loading={loading} problem={voicesProblem} idle={idle} />
                {usable && <SpeechStyle engine={engine} voice={voice} style={choice.style} onStyle={(style) => update({ style })} language={choice.language} onLanguage={(language) => update({ language })} />}
              </>
            )}
          </section>
          {prepared && <SpeechMake summary={summary} blocked={blocked} making={making} problem={makeProblem} queued={queued} onMake={() => void make()} />}
          <audio ref={audio} preload="none" />
        </>
      )}
    </div>
  );
}
