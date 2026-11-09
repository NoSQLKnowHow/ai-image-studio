import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { speechApi } from "./speechApi";
import type { Passage } from "./speechTypes";

// The passage a voice is recorded from, and the limits that go with it (DESIGN.md §38.4): the words to read aloud, the statement to agree to, how long a recording may be,
// how long a name or a description may be. Asked for the first time it is wanted, and kept: it does not change while the studio runs.

export interface PassageState {
  passage: Passage | null;
  loading: boolean;
  problem: { message: string; hint: string | null } | null;
  retry: () => void;
}

export function usePassage(wanted: boolean, api: Pick<typeof speechApi, "passage"> = speechApi): PassageState {
  const [passage, setPassage] = useState<Passage | null>(null);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState<PassageState["problem"]>(null);
  const [again, setAgain] = useState(0);

  useEffect(() => {
    if (!wanted || passage) return undefined;
    let current = true;
    setLoading(true);
    setProblem(null);
    api.passage().then(
      (answer) => {
        if (!current) return;
        setPassage(answer);
        setLoading(false);
      },
      (error: unknown) => {
        if (!current) return;
        setLoading(false);
        setProblem(error instanceof ApiError ? { message: error.message, hint: error.hint } : { message: "The passage could not be read.", hint: null });
      },
    );
    return () => {
      current = false;
    };
    // `passage` is what stops a second asking, not a reason for one
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, again, api]);

  const retry = useCallback(() => setAgain((n) => n + 1), []);
  return { passage, loading, problem, retry };
}
