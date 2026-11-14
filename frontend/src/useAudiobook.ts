import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { speechApi } from "./speechApi";
import type { AudiobookStatus } from "./speechTypes";

// One reading's audiobook (DESIGN.md §44.4): nothing is asked until the person presses the button (a page with a hundred readings must not ask a hundred times), then the
// build is started, followed until it ends, and the finished file's address is kept. A build goes on in the server whether or not the page is looking, so a page that is closed
// and opened again finds it ready: pressing the button then answers at once, with the same file.

export interface Problem {
  message: string;
  hint: string | null;
}

export interface AudiobookState {
  status: AudiobookStatus | null; // what the server last said; null before the first press
  busy: boolean; // the press is on its way to the server
  problem: Problem | null; // the server refused, or could not be reached
  make: () => void;
}

type Api = Pick<typeof speechApi, "makeAudiobook" | "audiobook">;

/** How often a build that is going is asked how far it has got. A second is quick enough to see a percentage move and cheap for the server (it reads the database, not the audio). */
export const POLL_MS = 1000;

const settled = (status: AudiobookStatus): boolean => status.state !== "waiting" && status.state !== "building";

const toProblem = (error: unknown): Problem =>
  error instanceof ApiError ? { message: error.message, hint: error.hint } : { message: "The audiobook file could not be asked for.", hint: null };

export function useAudiobook(runId: string, api: Api = speechApi, pollMs: number = POLL_MS): AudiobookState {
  const [status, setStatus] = useState<AudiobookStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const alive = useRef(true);
  // Whether a press is on its way, kept in a ref and not read from `busy`: two presses made before the page has drawn again both see the same `busy`, and both would ask.
  const asking = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  /** Ask how far the build has got, and again after a moment until it has ended. One asking at a time: the next is arranged when the answer comes. */
  const follow = useCallback(() => {
    timer.current = setTimeout(async () => {
      try {
        const next = await api.audiobook(runId);
        if (!alive.current) return;
        setStatus(next);
        if (!settled(next)) follow();
      } catch (error) {
        if (!alive.current) return;
        setProblem(toProblem(error)); // the build goes on in the server; pressing the button again asks again
      }
    }, pollMs);
  }, [api, runId, pollMs]);

  const make = useCallback(() => {
    if (asking.current || (status && !settled(status))) return;
    asking.current = true;
    setBusy(true);
    setProblem(null);
    api.makeAudiobook(runId).then(
      (answer) => {
        asking.current = false;
        if (!alive.current) return;
        setStatus(answer);
        setBusy(false);
        if (!settled(answer)) follow();
      },
      (error: unknown) => {
        asking.current = false;
        if (!alive.current) return;
        setBusy(false);
        setProblem(toProblem(error));
      },
    );
  }, [api, runId, status, follow]);

  return { status, busy, problem, make };
}
