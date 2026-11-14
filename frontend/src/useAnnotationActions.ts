import { useMemo, useRef } from "react";
import { DEFAULT_LIMITS, type AnnotationActions } from "./annotations";
import { api, type ApiError } from "./api";
import { builderApi } from "./builderApi";
import { matches, type HistoryFilter } from "./history";
import type { Action } from "./store";
import type { Capabilities, Run, TagCount } from "./types";

// How the page reaches the server for tags and notes (DESIGN.md §45.8), made once in the App and given to the cards and the viewers through `AnnotationsContext`.
//
// Two things happen here besides the requests. The run that comes back is put in the store at once (the server's `run.updated` follows and changes nothing). And a run that was in
// the view a search or a filter makes, and no longer is because of what was just written (the tag the search was for was removed), leaves the list: the page says so, with Undo,
// and keyboard focus goes to the filter bar, as for Keep in the Kept view (§29.4).

/** How long the list of tags in use is remembered before the editor asks again (a few seconds: it is asked when the box is used, and after every change the editor itself makes). */
const KNOWN_TAGS_MS = 5000;
/** How long the answer about whether Ollama can be reached is remembered. The server remembers its own for 10 seconds (§37.4), so asking more often only repeats it. */
const REACH_MS = 10_000;

export interface AnnotationDeps {
  caps: Capabilities | null;
  filter: HistoryFilter;
  dispatch: (action: Action) => void;
  /** A toast, with an optional button (the page's own `push`). */
  push: (kind: "info" | "error", text: string, action?: { label: string; run: () => void }) => void;
  /** Set when a card has just left the view, so that focus goes to the filter bar once the list has been drawn without it. */
  refocus: { current: boolean };
}

/** A prompt cut to one line for a toast. */
const short = (text: string, max = 48): string => {
  const flat = text.trim().replace(/\s+/g, " ");
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
};

export function useAnnotationActions({ caps, filter, dispatch, push, refocus }: AnnotationDeps): AnnotationActions {
  // What the functions below need that changes between draws, read at the moment they run: the filter that is on now, not the one of the draw that made them
  const latest = useRef({ filter, push });
  latest.current = { filter, push };
  const known = useRef<{ at: number; list: TagCount[] } | null>(null);
  const reach = useRef<{ at: number; answer: Awaited<ReturnType<AnnotationActions["reachability"]>> } | null>(null);
  const suggestSetting = caps?.annotations?.suggest;
  const limits = caps?.limits.annotations ?? DEFAULT_LIMITS;

  return useMemo<AnnotationActions>(() => {
    // After a change: the run is put in the store; and one that has just stopped matching the view leaves it with a toast that can undo it
    const applied = (before: Run, after: Run, undo: () => Promise<unknown>): Run => {
      const view = latest.current.filter;
      if (matches(before, view) && !matches(after, view)) {
        refocus.current = true;
        latest.current.push("info", `“${short(after.prompt)}” no longer matches this view, so it is not in the list.`, { label: "Undo", run: () => void undo().catch(() => undefined) });
      }
      dispatch({ type: "runUpsert", run: after });
      return after;
    };
    const setTags = async (run: Run, tags: string[]): Promise<Run> => {
      const after = await api.putRunTags(run.id, tags);
      known.current = null; // the list of tags in use has changed
      return applied(run, after, () => setTags(after, run.tags));
    };
    const setNotes = async (run: Run, notes: string): Promise<Run> => {
      const after = await api.putRunNotes(run.id, notes);
      return applied(run, after, () => setNotes(after, run.notes));
    };
    return {
      limits,
      setTags,
      setNotes,
      knownTags: async () => {
        if (known.current && Date.now() - known.current.at < KNOWN_TAGS_MS) return known.current.list;
        const list = await api.listTags();
        known.current = { at: Date.now(), list };
        return list;
      },
      suggest: suggestSetting ?? { available: false, reason: "Suggest is not available on this server." },
      suggestTags: async (run: Run) => {
        try {
          return await api.suggestTags(run.id);
        } catch (error) {
          // the server's own words (set up? reachable? model? unreadable answer?) are what the editor shows; the status of Ollama is looked at again after a failure
          reach.current = null;
          throw error as ApiError;
        }
      },
      reachability: async () => {
        if (reach.current && Date.now() - reach.current.at < REACH_MS) return reach.current.answer;
        try {
          const status = await builderApi.status();
          // Only a server that is set up can be unreachable: "not set up" is the capabilities' reason (suggest.reason), not this one's
          const answer = status.configured ? { reachable: status.reachable, message: status.message, hint: status.hint } : null;
          reach.current = { at: Date.now(), answer };
          return answer;
        } catch {
          return null; // not asked: Suggest stays as it was, and a press says what happened
        }
      },
    };
  }, [dispatch, refocus, limits, suggestSetting]);
}
