import { useEffect, useState } from "react";
import { api } from "./api";
import type { VectorRecipeRequest } from "./types";

// "What the studio sends" on the Vector tab (DESIGN.md §31.12.8): the words the server will send to the image model for the idea, colours and kind that are on
// the form. They come from `POST /api/vector/recipe` (pure text: no model, no database), asked after a short pause so that typing does not make a request for
// every key. The page never writes these words itself; this hook only fetches them.

/** What the hook holds. `answered` is the last answer and the request it was for (`key`), kept while a newer request is under way so that the box does not
 *  flicker empty on every key; `failed` is the last failure, with its request. */
export interface RecipeState {
  loading: boolean; // a request is waiting for its pause, or on its way
  answered: { key: string; positive: string; negative: string } | null;
  failed: { key: string; message: string } | null;
}

export const RECIPE_PAUSE_MS = 350;

/** The identity of a request: the same idea, kind, colours and palette are the same words, so a change of anything else on the form asks nothing. */
export const recipeKey = (request: VectorRecipeRequest | null): string | null => (request === null ? null : JSON.stringify(request));

/** Ask for the recipe of `request` (null: there is no idea yet, so nothing is asked and nothing is shown). Only the newest request counts: an answer that
 *  arrives after the form has moved on is dropped, so the box can never show the words for an older idea. */
export function useVectorRecipe(request: VectorRecipeRequest | null, pauseMs = RECIPE_PAUSE_MS): RecipeState {
  const key = recipeKey(request);
  const [state, setState] = useState<RecipeState>({ loading: false, answered: null, failed: null });
  useEffect(() => {
    if (key === null) {
      setState((current) => ({ ...current, loading: false, failed: null }));
      return;
    }
    let stale = false; // the form moved on (or the page closed): this request's answer is no longer wanted
    setState((current) => ({ ...current, loading: true, failed: null }));
    const timer = setTimeout(() => {
      api.vectorRecipe(JSON.parse(key) as VectorRecipeRequest).then(
        (answer) => {
          if (!stale) setState({ loading: false, answered: { key, positive: answer.positive, negative: answer.negative }, failed: null });
        },
        (error: Error) => {
          if (!stale) setState((current) => ({ ...current, loading: false, failed: { key, message: error.message } }));
        },
      );
    }, pauseMs);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [key, pauseMs]);
  return state;
}
