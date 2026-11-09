import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { ApiError } from "./api";
import type { Emitter } from "./emitter";
import type { SamplePhase } from "./speech";
import { speechApi } from "./speechApi";
import { waitForJob } from "./speechJobs";
import type { SampleBody, SpeechJob } from "./speechTypes";

// "Play sample" (DESIGN.md §38.4, criterion 262): ask for the sample of a voice, wait for it if it has to be made, and play it. A kept sample comes back at once; one that
// has to be made is a short job that the server puts ahead of waiting readings, and the page hears of it through the `speech.job` event (with a poll as a safety net, since
// an event can be missed). One sample plays at a time: pressing another stops the first, and pressing the one that is playing stops it.

export interface SamplerProblem {
  key: string;
  message: string;
  hint: string | null;
}

export interface Sampler {
  phase: (key: string) => SamplePhase;
  problem: SamplerProblem | null;
  play: (key: string, body: SampleBody) => void;
  stop: () => void;
}

interface Env {
  audio: RefObject<HTMLAudioElement | null>;
  jobs: Pick<Emitter<SpeechJob>, "subscribe">;
  api?: Pick<typeof speechApi, "sample" | "job">;
  pollMs?: number;
}

const words = (error: unknown): { message: string; hint: string | null } =>
  error instanceof ApiError ? { message: error.message, hint: error.hint } : { message: "The sample could not be made.", hint: null };

export function useSampler({ audio, jobs, api = speechApi, pollMs = 2000 }: Env): Sampler {
  const [current, setCurrent] = useState<{ key: string; phase: SamplePhase } | null>(null);
  const [problem, setProblem] = useState<SamplerProblem | null>(null);
  const turn = useRef(0); // which press is the one being answered: an answer to an earlier press is not wanted
  const ready = useRef(new Map<string, string>()); // key -> a sample that is made but that the browser would not start by itself
  const cleanup = useRef<(() => void) | null>(null); // stops listening and polling for the job being waited on

  const halt = useCallback(() => {
    turn.current += 1;
    cleanup.current?.();
    cleanup.current = null;
    const element = audio.current;
    if (element) {
      element.pause();
      element.removeAttribute("src");
    }
  }, [audio]);

  const stop = useCallback(() => {
    halt();
    setCurrent(null);
  }, [halt]);

  /** Start playing a made sample. A browser may refuse to start sound that was asked for a while after the press (Safari, mostly); then it waits for a press. */
  const start = useCallback(async (key: string, url: string, mine: number) => {
    const element = audio.current;
    if (!element) {
      setCurrent(null);
      setProblem({ key, message: "The sample could not be played.", hint: null });
      return;
    }
    element.src = url;
    try {
      await element.play();
      if (mine === turn.current) setCurrent({ key, phase: "playing" });
    } catch (error) {
      if (mine !== turn.current) return;
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        ready.current.set(key, url);
        setCurrent({ key, phase: "ready" });
      } else {
        setCurrent(null);
        setProblem({ key, message: "The sample could not be played.", hint: null });
      }
    }
  }, [audio]);

  /** Wait until a job has finished (speechJobs.ts). Resolves with the finished job, or null if this press was given up. */
  const wait = useCallback((first: SpeechJob, mine: number) => {
    const waiting = waitForJob(first, { jobs, api, pollMs, wanted: () => mine === turn.current });
    cleanup.current = waiting.cancel;
    return waiting.done;
  }, [api, jobs, pollMs]);

  const play = useCallback((key: string, body: SampleBody) => {
    if (current?.key === key && (current.phase === "playing" || current.phase === "waiting")) return stop();
    const url = ready.current.get(key);
    halt();
    setProblem(null);
    const mine = turn.current;
    if (url && current?.key === key && current.phase === "ready") {
      ready.current.delete(key);
      setCurrent({ key, phase: "waiting" });
      void start(key, url, mine);
      return;
    }
    setCurrent({ key, phase: "waiting" });
    void (async () => {
      try {
        const answer = await api.sample(body);
        if (mine !== turn.current) return;
        let sampleUrl = answer.ready ? answer.url : null;
        if (!sampleUrl && answer.job) {
          const job = await wait(answer.job, mine);
          if (mine !== turn.current || !job) return;
          if (job.status !== "done") {
            setCurrent(null);
            const failed = job.error;
            setProblem({ key, message: failed?.message ?? "The sample could not be made.", hint: failed?.hint ?? null });
            return;
          }
          sampleUrl = job.files[0]?.url ?? null;
        }
        if (!sampleUrl) {
          setCurrent(null);
          setProblem({ key, message: "The sample could not be made.", hint: null });
          return;
        }
        await start(key, sampleUrl, mine);
      } catch (error) {
        if (mine !== turn.current) return;
        setCurrent(null);
        setProblem({ key, ...words(error) });
      }
    })();
  }, [api, current, halt, start, stop, wait]);

  // the sound ending, or the file failing, is the end of this sample
  useEffect(() => {
    const element = audio.current;
    if (!element) return;
    const ended = () => setCurrent(null);
    const failed = () => {
      if (!element.getAttribute("src")) return; // taking the source away to stop is not a failure
      setCurrent((now) => {
        if (now) setProblem({ key: now.key, message: "The sample could not be played.", hint: null });
        return null;
      });
    };
    element.addEventListener("ended", ended);
    element.addEventListener("error", failed);
    return () => {
      element.removeEventListener("ended", ended);
      element.removeEventListener("error", failed);
    };
  }, [audio]);

  useEffect(() => () => { // leaving the page: no sound, no polling
    turn.current += 1;
    cleanup.current?.();
  }, []);

  return { phase: (key) => (current?.key === key ? current.phase : "idle"), problem, play, stop };
}
