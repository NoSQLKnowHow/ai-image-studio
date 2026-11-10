import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api";
import { createEmitter, type Emitter } from "../emitter";
import type { ToastKind } from "../hooks";
import type { KeyValueStore } from "../options";
import {
  chooseEngine, engineUsable, findVoice, firstVoice, queuedText, readChoice, readingProblem, readingTotals, saveChoice, totalsLine, buildReadingBody, voiceName,
  type SpeechChoice, type VoicePick, type VoiceFilter,
} from "../speech";
import { speechApi } from "../speechApi";
import type { PresetVoice, ReadingPieceEvent, SavedVoice, SpeechJob } from "../speechTypes";
import { isSpeechRun, type Capabilities, type Run, type SpeechRun, type Status, type WorkerStatus } from "../types";
import type { ViewScope } from "../format";
import { useEngineVoices } from "../useEngineVoices";
import { usePassage } from "../usePassage";
import { useSampler } from "../useSampler";
import { useSpeechSource, type Problem } from "../useSpeechSource";
import { SpeechEngines } from "./SpeechEngines";
import { SpeechMake } from "./SpeechMake";
import { SpeechReview } from "./SpeechReview";
import { SpeechSourceBox } from "./SpeechSource";
import { SpeechStyle } from "./SpeechStyle";
import { SpeechVoices } from "./SpeechVoices";
import { LoadProgressView } from "./LoadProgress";
import { AddVoiceDialog, ConfirmDeleteVoice, DesignVoiceDialog } from "./VoiceDialogs";
import { QueueBar } from "./Feedback";
import type { ProjectControls } from "./ProjectMenu";
import { ReadingCard } from "./ReadingCard";
import { ReadingView } from "./ReadingView";
import { panelId, tabId } from "./Tabs";

// The Speech tab (DESIGN.md §38.2): text in, read aloud in a voice. This file is the tab's frame and what ties its parts together: whether speech can work here at all,
// the source of the text and the review made of it, the engine and the voice (remembered), and Make reading. The readings themselves, in the history, are added to it
// as they are built.

const NO_PRESETS: PresetVoice[] = []; // one array each, so that nothing re-renders for a new empty one
const NO_SAVED: SavedVoice[] = [];
const NO_JOBS: Emitter<SpeechJob> = createEmitter<SpeechJob>();

/** What the history of readings needs from the page, handed over together: the list (newest first), how to say that it is loading or empty, the filter bar that the
 *  tabs share (DESIGN.md §29), the projects (§32), and what each button of a card does. */
export interface SpeechHistory {
  status: Status | null;
  runs: SpeechRun[]; // newest first
  runsReady: boolean;
  filterBar: ReactNode;
  loadProblem: ReactNode | null; // shown in place of "Loading…" when the filtered list could not be loaded
  empty: ReactNode; // what the list says when there is nothing to show for the filter, or that it is still looking
  isTransient: (run: SpeechRun) => boolean; // in a filtered view only because it is working (§29.3, §32)
  viewScope: ViewScope;
  projects: ProjectControls;
  more: boolean; // older runs can be loaded
  loadingOlder: boolean;
  now: number;
  onLoadOlder: () => void;
  onCancel: (run: Run) => void;
  onContinue: (run: Run) => void;
  onRestore: (run: Run) => void;
  onDelete: (run: Run) => void;
  onListen?: (run: SpeechRun) => void; // open the reading view
}

/** The reading that is open in the player (§38.7), if one is: which, the `reading.piece` events it hears, and what closing it does. */
export interface SpeechReader {
  run: SpeechRun;
  turn: number; // the press of Listen that opened it: another press on the same reading opens it again, at its place, and plays
  pieces: Emitter<ReadingPieceEvent>;
  onClose: () => void;
}

export interface SpeechPanelProps {
  hidden: boolean;
  caps: Capabilities;
  store: KeyValueStore; // where the choice of engine, voice, style and language is remembered
  push?: (kind: ToastKind, text: string) => void;
  jobs?: Emitter<SpeechJob>; // the `speech.job` events: a sample or a design that has moved on
  onEngine?: (engine: string | null) => void; // which engine the tab shows, for the header's Load and Unload
  onQueued?: (run: SpeechRun) => void; // a reading was queued: it joins the page's runs
  history?: SpeechHistory; // the readings made so far, with the filter bar above them
  reader?: SpeechReader; // the reading being listened to: the player, above the history
  worker?: WorkerStatus | null; // what the worker is doing, to show how far a speech model that is loading has got (DESIGN.md §39)
}

/** The readings made so far (DESIGN.md §38.2): one card each, newest first, under the filter bar the tabs share. They are shown whether or not speech can be made now,
 *  so that a reading made before an engine went away can still be listened to and downloaded. */
function Readings({ history }: { history: SpeechHistory }) {
  const { runs } = history;
  return (
    <>
      <QueueBar status={history.status} />
      {history.filterBar}
      <section className="timeline" aria-label="Your readings">
        {!history.runsReady ? (
          history.loadProblem ?? <p className="loading" role="status">Loading your readings…</p>
        ) : runs.length === 0 ? (
          history.empty
        ) : (
          runs.map((run) => (
            <ReadingCard key={run.id} run={run} now={history.now} transient={history.isTransient(run)} viewScope={history.viewScope} projects={history.projects} onListen={history.onListen}
              onContinue={() => history.onContinue(run)} onCancel={() => history.onCancel(run)} onRestore={() => history.onRestore(run)} onDelete={() => history.onDelete(run)} />
          ))
        )}
        {history.more && (
          <button type="button" className="button load-more" onClick={history.onLoadOlder} disabled={history.loadingOlder}>
            {history.loadingOlder ? "Loading…" : "Load older runs"}
          </button>
        )}
      </section>
    </>
  );
}

export function SpeechPanel({ hidden, caps, store, push, jobs = NO_JOBS, onEngine, onQueued, history, reader, worker }: SpeechPanelProps) {
  const speechWorker = worker?.model === "speech" ? worker : null; // only a speech model's load is the Speech tab's business
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
  const { voices, loading, problem: voicesProblem, reload: reloadVoices } = useEngineVoices(usable ? engine.id : null);
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

  // The person's own voices (DESIGN.md §38.4): made in a dialog, renamed in the list, deleted after a question. What the dialogs need to know (the passage, and the limits
  // on a name and a description) is asked for the first time one is opened, and not before.
  const [maker, setMaker] = useState<"design" | "clone" | null>(null);
  const [deleting, setDeleting] = useState<SavedVoice | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteProblem, setDeleteProblem] = useState<Problem | null>(null);
  const passage = usePassage(maker !== null);
  const words = (error: unknown, fallback: string): Problem => (error instanceof ApiError ? { message: error.message, hint: error.hint } : { message: fallback, hint: null });
  const savedVoice = (voice: SavedVoice) => {
    setMaker(null);
    reloadVoices();
    update({ voice: { kind: "saved", id: voice.id } }); // the voice just made is the voice chosen
    push?.("success", `Saved the voice “${voice.name}”.`);
  };
  const renameVoice = async (voice: SavedVoice, name: string): Promise<Problem | null> => {
    try {
      await speechApi.renameVoice(voice.id, name.trim());
      reloadVoices();
      return null;
    } catch (error) {
      return words(error, "The voice could not be renamed.");
    }
  };
  const confirmDeleteVoice = async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    setDeleteProblem(null);
    try {
      await speechApi.deleteVoice(deleting.id);
      stop(); // a sample of it that was playing
      reloadVoices();
      push?.("success", `Deleted the voice “${deleting.name}”.`);
      setDeleting(null);
    } catch (error) {
      setDeleteProblem(words(error, "The voice could not be deleted."));
    } finally {
      setDeleteBusy(false);
    }
  };
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
            <LoadProgressView worker={speechWorker} />
            {engine && (
              <>
                <SpeechVoices engine={engine.id} presets={presets} saved={saved} chosen={pick} onChoose={chooseVoice} filter={choice.filter} onFilter={setFilter} sampler={sampler}
                  loading={loading} problem={voicesProblem} idle={idle} nameMax={passage.passage?.name_max}
                  tools={usable ? { design: engine.can_design ? () => setMaker("design") : null, clone: engine.can_clone ? () => setMaker("clone") : null } : undefined}
                  onRename={renameVoice} onDelete={(voice) => { setDeleteProblem(null); setDeleting(voice); }} />
                {usable && <SpeechStyle engine={engine} voice={voice} style={choice.style} onStyle={(style) => update({ style })} language={choice.language} onLanguage={(language) => update({ language })} />}
              </>
            )}
          </section>
          {prepared && <SpeechMake summary={summary} blocked={blocked} making={making} problem={makeProblem} queued={queued} onMake={() => void make()} />}
          <audio ref={audio} preload="none" />
          {engine && usable && (
            <>
              <DesignVoiceDialog open={maker === "design"} onOpenChange={(open) => !open && setMaker(null)} engine={engine} passage={passage} jobs={jobs} worker={speechWorker} onSaved={savedVoice} />
              <AddVoiceDialog open={maker === "clone"} onOpenChange={(open) => !open && setMaker(null)} engine={engine} limits={limits} passage={passage} jobs={jobs} worker={speechWorker} onSaved={savedVoice} />
            </>
          )}
          <ConfirmDeleteVoice voice={deleting} deleting={deleteBusy} problem={deleteProblem} onCancel={() => setDeleting(null)} onConfirm={() => void confirmDeleteVoice()} />
        </>
      )}
      {/* a reading made before an engine went away can still be listened to, so the player does not depend on speech being available */}
      {reader && <ReadingView key={`${reader.run.id}:${reader.turn}`} run={reader.run} pieces={reader.pieces} store={store} onClose={reader.onClose} />}
      {history && <Readings history={history} />}
    </div>
  );
}
