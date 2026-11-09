import * as Dialog from "@radix-ui/react-dialog";
import { useId, useRef, useState, type ReactNode } from "react";
import type { Emitter } from "../emitter";
import {
  RECORDING_ACCEPT, deleteVoiceQuestion, descriptionProblem, recordingFacts, sampleLabel, sampleWords, voiceNameProblem,
} from "../speech";
import type { Passage, SavedVoice, SpeechEngine, SpeechJob, SpeechLimits } from "../speechTypes";
import type { PassageState } from "../usePassage";
import { useClipPlayer } from "../useClipPlayer";
import { useRecordedVoice } from "../useRecordedVoice";
import { useSampler } from "../useSampler";
import { useVoiceDesign } from "../useVoiceDesign";

// Making a voice of one's own (DESIGN.md §38.4): Design a voice (a description, three samples, keep one) and Add my voice (a passage read aloud and uploaded, a hearing
// back, a preview in the new voice, a statement that the voice is the person's own or that they agreed, a name). And the question before one is deleted.

interface Problem {
  message: string;
  hint: string | null;
}

function ProblemText({ problem }: { problem: Problem }) {
  return (
    <p className="form-error" role="alert">
      {problem.message}
      {problem.hint && <span className="hint"> {problem.hint}</span>}
    </p>
  );
}

/** What the person calls the voice, and the button that keeps it. The same in both dialogs. */
function SaveRow({ name, onName, max, ready, saving, onSave, problem, children }: {
  name: string; onName: (name: string) => void; max: number; ready: boolean; saving: boolean; onSave: () => void; problem: Problem | null; children?: ReactNode;
}) {
  const id = useId();
  const wrong = name.trim() ? voiceNameProblem(name, max) : null; // an empty name is not nagged about: Save is simply off
  return (
    <div className="voice-save">
      <label htmlFor={`${id}-name`}>Name this voice</label>
      <input id={`${id}-name`} type="text" value={name} onChange={(event) => onName(event.target.value)} disabled={saving} aria-invalid={wrong ? true : undefined}
        aria-describedby={wrong ? `${id}-wrong` : undefined} autoComplete="off" />
      {wrong && <p id={`${id}-wrong`} className="form-error">{wrong}</p>}
      {children}
      {problem && <ProblemText problem={problem} />}
      <div className="speech-actions">
        <button type="button" className="button primary" onClick={onSave} disabled={!ready || saving}>{saving ? "Saving…" : "Save this voice"}</button>
      </div>
    </div>
  );
}

function Frame({ open, onOpenChange, title, description, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; children: ReactNode }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="voice-dialog">
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description className="hint">{description}</Dialog.Description>
          {children}
          <div className="confirm-actions">
            <Dialog.Close className="button">Close</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function PassageProblem({ passage }: { passage: PassageState }) {
  if (passage.loading && !passage.passage) return <p role="status">Loading…</p>;
  if (!passage.problem) return null;
  return (
    <div>
      <ProblemText problem={passage.problem} />
      <button type="button" className="button small" onClick={passage.retry}>Try again</button>
    </div>
  );
}

// ------------------------------------------------------------------ Design a voice
interface DesignProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  engine: Pick<SpeechEngine, "id" | "title">;
  passage: PassageState;
  jobs: Pick<Emitter<SpeechJob>, "subscribe">;
  onSaved: (voice: SavedVoice) => void;
}

/** The dialog is mounted while open: closing it takes what was made with it, so a voice that was not saved is not kept anywhere. */
export function DesignVoiceDialog({ open, onOpenChange, engine, passage, jobs, onSaved }: DesignProps) {
  return (
    <Frame open={open} onOpenChange={onOpenChange} title="Design a voice"
      description={`Describe the voice you would like. ${engine.title} makes three short clips of the same passage in it; play them and keep the one you like. Whether a particular accent is held is not promised: listen before you save.`}>
      {open && <DesignBody engine={engine} passage={passage} jobs={jobs} onSaved={onSaved} />}
    </Frame>
  );
}

function DesignBody({ engine, passage, jobs, onSaved }: Pick<DesignProps, "engine" | "passage" | "jobs" | "onSaved">) {
  const id = useId();
  const audio = useRef<HTMLAudioElement>(null);
  const clipsPlayer = useClipPlayer(audio);
  const design = useVoiceDesign({ engine: engine.id, jobs });
  const [description, setDescription] = useState("");
  const [chosen, setChosen] = useState<number | null>(null);
  const [name, setName] = useState("");
  const limits = passage.passage;
  const descriptionWrong = limits && description.trim() ? descriptionProblem(description, limits.description_max) : null;
  const canAsk = !!limits && !descriptionProblem(description, limits.description_max) && design.phase !== "working";
  const canSave = chosen !== null && !!limits && !voiceNameProblem(name, limits.name_max);

  const make = () => {
    clipsPlayer.stop();
    setChosen(null);
    design.start(description);
  };
  const save = async () => {
    if (chosen === null) return;
    clipsPlayer.stop();
    const voice = await design.save(name, chosen);
    if (voice) onSaved(voice);
  };

  return (
    <div className="voice-body">
      <PassageProblem passage={passage} />
      <label htmlFor={`${id}-description`}>Describe the voice</label>
      <textarea id={`${id}-description`} rows={3} value={description} onChange={(event) => setDescription(event.target.value)} disabled={design.phase === "working"}
        placeholder="warm Irish woman in her forties, unhurried, clear" aria-invalid={descriptionWrong ? true : undefined} aria-describedby={descriptionWrong ? `${id}-wrong` : undefined} />
      {descriptionWrong && <p id={`${id}-wrong`} className="form-error">{descriptionWrong}</p>}
      <div className="speech-actions">
        <button type="button" className="button primary" onClick={make} disabled={!canAsk}>{design.phase === "done" || design.phase === "failed" ? "Make three new samples" : "Make three samples"}</button>
      </div>

      {design.phase === "working" && <p role="status" className="speech-working">Making three samples… this takes a little while, longer if {engine.title} has to be loaded first.</p>}
      {design.phase === "failed" && design.problem && <ProblemText problem={design.problem} />}

      {design.phase === "done" && (
        <fieldset className="voice-clips">
          <legend>The samples</legend>
          <ul>
            {design.clips.map((clip, i) => (
              <li key={clip.index}>
                <label>
                  <input type="radio" name={`${id}-clip`} checked={chosen === clip.index} onChange={() => setChosen(clip.index)} />
                  <span>Sample {i + 1}</span>
                </label>
                <button type="button" className="button small" aria-label={`${clipsPlayer.playing === `clip-${clip.index}` ? "Stop" : "Play"} sample ${i + 1}`}
                  onClick={() => clipsPlayer.play(`clip-${clip.index}`, clip.url)}>
                  {clipsPlayer.playing === `clip-${clip.index}` ? "Stop" : "Play"}
                </button>
              </li>
            ))}
          </ul>
          {clipsPlayer.problem && <p className="form-error" role="alert">{clipsPlayer.problem}</p>}
          <SaveRow name={name} onName={setName} max={limits?.name_max ?? 60} ready={canSave} saving={design.saving} onSave={() => void save()} problem={design.saveProblem}>
            {chosen === null && <p className="hint">Choose the sample you want to keep.</p>}
          </SaveRow>
        </fieldset>
      )}
      <audio ref={audio} preload="none" />
    </div>
  );
}

// ------------------------------------------------------------------ Add my voice
interface AddProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  engine: Pick<SpeechEngine, "id" | "title">;
  limits: Pick<SpeechLimits, "recording_mb" | "voice_seconds">;
  passage: PassageState;
  jobs: Pick<Emitter<SpeechJob>, "subscribe">;
  onSaved: (voice: SavedVoice) => void;
}

export function AddVoiceDialog({ open, onOpenChange, engine, limits, passage, jobs, onSaved }: AddProps) {
  return (
    <Frame open={open} onOpenChange={onOpenChange} title="Add my voice"
      description="Read the passage aloud, record it on your phone or your computer, and choose the file. The studio checks it, lets you hear it back and hear it speak, and keeps it only when you save it.">
      {open && <AddBody engine={engine} limits={limits} passage={passage} jobs={jobs} onSaved={onSaved} />}
    </Frame>
  );
}

function AddBody({ engine, limits, passage, jobs, onSaved }: Omit<AddProps, "open" | "onOpenChange">) {
  const id = useId();
  const clipAudio = useRef<HTMLAudioElement>(null);
  const previewAudio = useRef<HTMLAudioElement>(null);
  const chooser = useRef<HTMLInputElement>(null);
  const clip = useClipPlayer(clipAudio);
  const sampler = useSampler({ audio: previewAudio, jobs });
  const recorded = useRecordedVoice({ engine: engine.id, limits });
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const text: Passage | null = passage.passage;
  const staged = recorded.staged;
  const previewProblem = sampler.problem?.key === "preview" ? sampler.problem : null;
  const phase = sampler.phase("preview");
  const canSave = !!staged && consent && !!text && !voiceNameProblem(name, text.name_max);

  const choose = (file: File | undefined) => {
    if (!file) return;
    clip.stop();
    sampler.stop();
    recorded.choose(file);
  };
  const another = () => {
    clip.stop();
    sampler.stop();
    recorded.discard();
    setConsent(false);
    chooser.current?.focus();
  };
  const save = async () => {
    clip.stop();
    sampler.stop();
    const voice = await recorded.save(name, consent);
    if (voice) onSaved(voice);
  };

  return (
    <div className="voice-body">
      <PassageProblem passage={passage} />
      {text && (
        <>
          <h3 className="speech-voices-heading">Read this aloud</h3>
          <blockquote className="voice-passage">{text.text}</blockquote>
          <p className="hint">
            A recording of {limits.voice_seconds.min} to {limits.voice_seconds.max} seconds, in a quiet room, as a WAV, MP3, M4A, AAC, OGG, FLAC or WebM file, up to {limits.recording_mb} MB.
            Recording is done outside this page: a browser only lets a page use the microphone on https or on this computer, and the studio is on plain http.
          </p>
        </>
      )}

      <div className="speech-actions">
        <label className="button" htmlFor={`${id}-file`}>{staged ? "Choose another recording" : "Choose a recording"}</label>
        <input ref={chooser} id={`${id}-file`} type="file" className="sr-only" accept={RECORDING_ACCEPT} disabled={recorded.checking}
          onChange={(event) => { choose(event.target.files?.[0]); event.target.value = ""; }} />
        {recorded.checking && <button type="button" className="button small" onClick={recorded.stopChecking}>Stop</button>}
      </div>
      {recorded.checking && <p role="status" className="speech-working">Checking the recording…</p>}
      {recorded.problem && <ProblemText problem={recorded.problem} />}

      {staged && (
        <div className="voice-staged">
          <p className="run-meta">{recordingFacts(staged)}</p>
          <div className="speech-actions">
            <button type="button" className="button small" onClick={() => clip.play("recording", staged.url)}>{clip.playing === "recording" ? "Stop" : "Hear your recording"}</button>
            <button type="button" className="button small" aria-label={sampleLabel("the new voice", phase)} aria-busy={phase === "waiting"}
              onClick={() => sampler.play("preview", { engine: engine.id, recording: staged.id })}>
              {phase === "idle" ? "Hear it speak" : sampleWords(phase)}
            </button>
            <button type="button" className="button small ghost" onClick={another}>Put it away</button>
          </div>
          {text && <p className="hint">It speaks: “{text.sample_sentence}”</p>}
          {clip.problem && <p className="form-error" role="alert">{clip.problem}</p>}
          {previewProblem && <ProblemText problem={previewProblem} />}

          <SaveRow name={name} onName={setName} max={text?.name_max ?? 60} ready={canSave} saving={recorded.saving} onSave={() => void save()} problem={recorded.saveProblem}>
            <label className="choice voice-consent">
              <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} disabled={recorded.saving} />
              <span>{text?.consent ?? "This is my own voice, or the person has agreed that I may use it."}</span>
            </label>
          </SaveRow>
        </div>
      )}
      <audio ref={clipAudio} preload="none" />
      <audio ref={previewAudio} preload="none" />
    </div>
  );
}

// ------------------------------------------------------------------ Delete a voice
export function ConfirmDeleteVoice({ voice, deleting, problem, onCancel, onConfirm }: {
  voice: SavedVoice | null; deleting: boolean; problem: Problem | null; onCancel: () => void; onConfirm: () => void;
}) {
  return (
    <Dialog.Root open={!!voice} onOpenChange={(open) => !open && !deleting && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm" role="alertdialog">
          <Dialog.Title>Delete this voice?</Dialog.Title>
          <Dialog.Description>{voice ? deleteVoiceQuestion(voice.name) : ""}</Dialog.Description>
          {problem && <ProblemText problem={problem} />}
          <div className="confirm-actions">
            <Dialog.Close className="button" disabled={deleting}>Keep it</Dialog.Close>
            <button type="button" className="button danger-solid" onClick={onConfirm} disabled={deleting}>{deleting ? "Deleting…" : "Delete voice"}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
