import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import type { Emitter } from "./emitter";
import { candidateClips } from "./speech";
import { speechApi } from "./speechApi";
import { waitForJob } from "./speechJobs";
import type { SavedVoice, SpeechJob } from "./speechTypes";

// Designing a voice from a description (DESIGN.md §38.4): the description is sent, three clips of the same passage are made (a short job, followed as a sample is), the
// person plays them and saves the one they like under a name. Nothing is kept until it is saved.

export type DesignPhase = "idle" | "working" | "done" | "failed";
type Problem = { message: string; hint: string | null };

export interface VoiceDesign {
  phase: DesignPhase;
  clips: { index: number; url: string }[]; // the three candidates, once made
  problem: Problem | null; // why they could not be made
  saving: boolean;
  saveProblem: Problem | null; // why the voice could not be saved
  start: (description: string) => void;
  /** Save the candidate `index` under `name`. The saved voice, or null (and `saveProblem` says why). */
  save: (name: string, index: number) => Promise<SavedVoice | null>;
  reset: () => void;
}

interface Env {
  engine: string; // the engine the voice is for
  jobs: Pick<Emitter<SpeechJob>, "subscribe">;
  api?: Pick<typeof speechApi, "design" | "job" | "saveVoice">;
  pollMs?: number;
}

const words = (error: unknown, fallback: string): Problem => (error instanceof ApiError ? { message: error.message, hint: error.hint } : { message: fallback, hint: null });

export function useVoiceDesign({ engine, jobs, api = speechApi, pollMs = 2000 }: Env): VoiceDesign {
  const [phase, setPhase] = useState<DesignPhase>("idle");
  const [clips, setClips] = useState<VoiceDesign["clips"]>([]);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveProblem, setSaveProblem] = useState<Problem | null>(null);
  const job = useRef<SpeechJob | null>(null); // the finished design the clips are from: a candidate is saved by it
  const turn = useRef(0); // which description is the one being waited for: an answer to an earlier one is not wanted
  const cancel = useRef<(() => void) | null>(null);

  const halt = useCallback(() => {
    turn.current += 1;
    cancel.current?.();
    cancel.current = null;
  }, []);

  const reset = useCallback(() => {
    halt();
    job.current = null;
    setPhase("idle");
    setClips([]);
    setProblem(null);
    setSaving(false);
    setSaveProblem(null);
  }, [halt]);

  const start = useCallback((description: string) => {
    halt();
    const mine = turn.current;
    job.current = null;
    setPhase("working");
    setClips([]);
    setProblem(null);
    setSaveProblem(null);
    api.design(description.trim(), engine).then(
      async (first) => {
        if (mine !== turn.current) return;
        const waiting = waitForJob(first, { jobs, api, pollMs, wanted: () => mine === turn.current });
        cancel.current = waiting.cancel;
        const finished = await waiting.done;
        if (mine !== turn.current || !finished) return;
        const made = finished.status === "done" ? candidateClips(finished) : [];
        if (made.length === 0) {
          setProblem({ message: finished.error?.message ?? "The samples could not be made.", hint: finished.error?.hint ?? null });
          setPhase("failed");
          return;
        }
        job.current = finished;
        setClips(made);
        setPhase("done");
      },
      (error: unknown) => {
        if (mine !== turn.current) return;
        setProblem(words(error, "The samples could not be asked for."));
        setPhase("failed");
      },
    );
  }, [api, engine, halt, jobs, pollMs]);

  const save = useCallback(async (name: string, index: number): Promise<SavedVoice | null> => {
    const made = job.current;
    if (!made) return null;
    setSaving(true);
    setSaveProblem(null);
    try {
      return await api.saveVoice({ kind: "designed", name: name.trim(), candidate: { job: made.id, index } });
    } catch (error) {
      setSaveProblem(words(error, "The voice could not be saved."));
      return null;
    } finally {
      setSaving(false);
    }
  }, [api]);

  useEffect(() => halt, [halt]); // leaving: no more waiting

  return { phase, clips, problem, saving, saveProblem, start, save, reset };
}
