import type { Emitter } from "./emitter";
import { jobSettled } from "./speech";
import type { speechApi } from "./speechApi";
import type { SpeechJob } from "./speechTypes";

// Waiting for a short speech job (a sample, a voice design: DESIGN.md §38.4) to finish. The page is told by the `speech.job` event, and asks every few seconds as well,
// in case an event was missed (the connection dropped and came back). Shared by everything that waits for one, so that they all do it the same way.

export interface JobWait {
  /** The finished job (done, failed or canceled), or null if the wait was given up. */
  done: Promise<SpeechJob | null>;
  /** Give up: stop listening and stop asking. Resolves `done` with null. Safe to call more than once, and after the job has finished. */
  cancel: () => void;
}

export interface JobWaitEnv {
  jobs: Pick<Emitter<SpeechJob>, "subscribe">;
  api: Pick<typeof speechApi, "job">;
  pollMs: number;
  /** Asked at each poll: false once what the wait was for has been given up (another press, the page closed), which ends the wait. */
  wanted?: () => boolean;
}

export function waitForJob(first: SpeechJob, env: JobWaitEnv): JobWait {
  let finish: (job: SpeechJob | null) => void = () => undefined;
  const done = new Promise<SpeechJob | null>((resolve) => {
    let over = false;
    finish = (job) => {
      if (over) return;
      over = true;
      unsubscribe();
      clearInterval(timer);
      resolve(job);
    };
    const unsubscribe = env.jobs.subscribe((job) => {
      if (job.id === first.id && jobSettled(job)) finish(job);
    });
    const timer = setInterval(() => {
      if (env.wanted && !env.wanted()) return finish(null);
      env.api.job(first.id).then((job) => { if (jobSettled(job)) finish(job); }, () => undefined); // a poll that fails is tried again at the next one
    }, env.pollMs);
    if (jobSettled(first)) finish(first);
  });
  return { done, cancel: () => finish(null) };
}
