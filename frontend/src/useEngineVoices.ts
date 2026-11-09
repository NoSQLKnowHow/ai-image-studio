import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { speechApi } from "./speechApi";
import type { EngineVoices } from "./speechTypes";

// An engine's voices (DESIGN.md §38.4): its presets as its source states them, and the voices the person made for it. Asked for again when the engine changes, and
// when something changes them (a voice saved, renamed or deleted: `reload`).

export interface EngineVoicesState {
  voices: EngineVoices | null;
  loading: boolean;
  problem: { message: string; hint: string | null } | null;
  reload: () => void;
}

type Api = Pick<typeof speechApi, "voices">;

/** The voices of `engine`, or nothing while `engine` is null (no engine that can speak, or one that cannot yet). An answer for an engine that is no longer the one
 *  wanted is dropped: changing engines quickly never shows one engine's voices under another's name. */
export function useEngineVoices(engine: string | null, api: Api = speechApi): EngineVoicesState {
  const [voices, setVoices] = useState<EngineVoices | null>(null);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState<EngineVoicesState["problem"]>(null);
  const [again, setAgain] = useState(0);
  const wanted = useRef(engine);
  wanted.current = engine;

  useEffect(() => {
    setProblem(null);
    if (!engine) {
      setVoices(null);
      setLoading(false);
      return;
    }
    let current = true;
    setLoading(true);
    api.voices(engine).then(
      (answer) => {
        if (!current || wanted.current !== engine) return;
        setVoices(answer);
        setLoading(false);
      },
      (error: unknown) => {
        if (!current || wanted.current !== engine) return;
        setVoices(null);
        setLoading(false);
        setProblem(error instanceof ApiError ? { message: error.message, hint: error.hint } : { message: "The voices could not be read.", hint: null });
      },
    );
    return () => {
      current = false;
    };
  }, [engine, again, api]);

  const reload = useCallback(() => setAgain((n) => n + 1), []);
  return { voices: voices && voices.engine === engine ? voices : null, loading, problem, reload };
}
