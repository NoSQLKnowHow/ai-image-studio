import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { recordingProblem } from "./speech";
import { speechApi } from "./speechApi";
import type { SavedVoice, SpeechLimits, StagedRecording } from "./speechTypes";

// Adding a voice from a recording (DESIGN.md §38.4): the file is checked here for what is cheap to check, sent, and checked by the server (long enough, not silent, not
// clipped); what it kept can be heard back, and is saved as a voice under a name once the person has said the voice is theirs or its owner agreed.

type Problem = { message: string; hint: string | null };

export interface RecordedVoice {
  staged: StagedRecording | null; // the recording the server accepted
  checking: boolean; // it is being sent and checked
  problem: Problem | null; // why the last recording was not taken
  saving: boolean;
  saveProblem: Problem | null;
  choose: (file: File) => void;
  /** Give up on the recording that is being sent. */
  stopChecking: () => void;
  /** Put the recording away to choose another. */
  discard: () => void;
  /** Save it as a voice. Without the statement there is no voice: the answer is null and nothing is sent. */
  save: (name: string, consent: boolean) => Promise<SavedVoice | null>;
}

interface Env {
  engine: string;
  limits: Pick<SpeechLimits, "recording_mb">;
  api?: Pick<typeof speechApi, "uploadRecording" | "saveVoice">;
}

const words = (error: unknown, fallback: string): Problem => (error instanceof ApiError ? { message: error.message, hint: error.hint } : { message: fallback, hint: null });

export function useRecordedVoice({ engine, limits, api = speechApi }: Env): RecordedVoice {
  const [staged, setStaged] = useState<StagedRecording | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveProblem, setSaveProblem] = useState<Problem | null>(null);
  const turn = useRef(0); // which file is the one being checked
  const abort = useRef<AbortController | null>(null);

  const halt = useCallback(() => {
    turn.current += 1;
    abort.current?.abort();
    abort.current = null;
  }, []);

  const choose = useCallback((file: File) => {
    halt();
    const mine = turn.current;
    setStaged(null);
    setSaveProblem(null);
    const wrong = recordingProblem(file, limits);
    if (wrong) {
      setChecking(false);
      setProblem({ message: wrong, hint: null });
      return;
    }
    setProblem(null);
    setChecking(true);
    const controller = new AbortController();
    abort.current = controller;
    api.uploadRecording(file, controller.signal).then(
      (recording) => {
        if (mine !== turn.current) return;
        setStaged(recording);
        setChecking(false);
      },
      (error: unknown) => {
        if (mine !== turn.current) return;
        setChecking(false);
        if (error instanceof DOMException && error.name === "AbortError") return; // given up: not a failure
        setProblem(words(error, "The recording could not be sent."));
      },
    );
  }, [api, halt, limits]);

  const stopChecking = useCallback(() => {
    halt();
    setChecking(false);
  }, [halt]);

  const discard = useCallback(() => {
    halt();
    setStaged(null);
    setChecking(false);
    setProblem(null);
    setSaveProblem(null);
  }, [halt]);

  const save = useCallback(async (name: string, consent: boolean): Promise<SavedVoice | null> => {
    if (!staged || !consent) return null;
    setSaving(true);
    setSaveProblem(null);
    try {
      return await api.saveVoice({ kind: "cloned", name: name.trim(), recording: staged.id, engine, consent: true });
    } catch (error) {
      setSaveProblem(words(error, "The voice could not be saved."));
      return null;
    } finally {
      setSaving(false);
    }
  }, [api, engine, staged]);

  useEffect(() => halt, [halt]); // leaving: nothing more is sent

  return { staged, checking, problem, saving, saveProblem, choose, stopChecking, discard, save };
}
