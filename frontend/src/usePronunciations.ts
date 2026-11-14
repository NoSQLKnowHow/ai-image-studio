import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { wordKey } from "./pronunciation";
import { speechApi } from "./speechApi";
import type { PronunciationBody, PronunciationChange, PronunciationEntry, PronunciationTry } from "./speechTypes";

// The pronunciation list the page holds (DESIGN.md §44.3): read once when the Speech tab can be used, and kept in step with what the person does to it. The server keeps
// the list; this keeps a copy to show, and each change is made by the server first and shown after, so the page never shows an entry the server does not have.

export interface Problem {
  message: string;
  hint: string | null;
  field: string | null; // the box of the entry the words are about, where the server names one
}

export interface PronunciationList {
  entries: PronunciationEntry[]; // by word, ignoring case, as the server lists them; empty until read
  loaded: boolean;
  problem: Problem | null; // the list could not be read
  add: (body: PronunciationBody) => Promise<Problem | null>;
  change: (id: string, change: PronunciationChange) => Promise<Problem | null>;
  remove: (id: string) => Promise<Problem | null>;
  tryIt: (text: string) => Promise<PronunciationTry | Problem>;
  reload: () => void;
}

type Api = Pick<typeof speechApi, "pronunciations" | "addPronunciation" | "changePronunciation" | "deletePronunciation" | "tryPronunciation">;

const byWord = (entries: PronunciationEntry[]): PronunciationEntry[] =>
  [...entries].sort((a, b) => (wordKey(a.word) < wordKey(b.word) ? -1 : wordKey(a.word) > wordKey(b.word) ? 1 : a.id < b.id ? -1 : 1));

export const toProblem = (error: unknown, fallback: string): Problem =>
  error instanceof ApiError ? { message: error.message, hint: error.hint, field: error.fieldErrors[0]?.field ?? null } : { message: fallback, hint: null, field: null };

/** Whether a `tryIt` answer is the server's refusal and not the sentence's spoken form. */
export const isProblem = (answer: PronunciationTry | Problem): answer is Problem => "message" in answer;

export function usePronunciations(enabled: boolean, api: Api = speechApi): PronunciationList {
  const [entries, setEntries] = useState<PronunciationEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [again, setAgain] = useState(0);

  useEffect(() => {
    if (!enabled) return undefined;
    let current = true;
    api.pronunciations().then(
      (answer) => {
        if (!current) return;
        setEntries(byWord(answer.entries));
        setLoaded(true);
        setProblem(null);
      },
      (error: unknown) => {
        if (current) setProblem(toProblem(error, "The pronunciation list could not be read."));
      },
    );
    return () => {
      current = false;
    };
  }, [enabled, again, api]);

  const add = useCallback(async (body: PronunciationBody): Promise<Problem | null> => {
    try {
      const made = await api.addPronunciation(body);
      setEntries((now) => byWord([...now, made]));
      return null;
    } catch (error) {
      return toProblem(error, "The entry could not be added.");
    }
  }, [api]);

  const change = useCallback(async (id: string, changes: PronunciationChange): Promise<Problem | null> => {
    try {
      const changed = await api.changePronunciation(id, changes);
      setEntries((now) => byWord(now.map((e) => (e.id === id ? changed : e))));
      return null;
    } catch (error) {
      return toProblem(error, "The entry could not be changed.");
    }
  }, [api]);

  const remove = useCallback(async (id: string): Promise<Problem | null> => {
    try {
      await api.deletePronunciation(id);
      setEntries((now) => now.filter((e) => e.id !== id));
      return null;
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setEntries((now) => now.filter((e) => e.id !== id)); // it was already gone (deleted on another screen): the list should not show it either
        return null;
      }
      return toProblem(error, "The entry could not be deleted.");
    }
  }, [api]);

  const tryIt = useCallback(async (text: string): Promise<PronunciationTry | Problem> => {
    try {
      return await api.tryPronunciation(text);
    } catch (error) {
      return toProblem(error, "That could not be tried.");
    }
  }, [api]);

  const reload = useCallback(() => setAgain((n) => n + 1), []);
  return { entries, loaded, problem, add, change, remove, tryIt, reload };
}
