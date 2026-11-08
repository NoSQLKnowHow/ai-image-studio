import { createContext, useContext } from "react";
import { refLine } from "./builder";
import type { ImageInfo, ImageRun, Run } from "./types";

// What a run's card can do about the saved prompt it was made from (DESIGN.md §37.2 "On the Images tab", §37.3 rule 3): say where it came from and open that prompt, and refine
// a picture in the Prompt builder. The page provides these; a card shown without them (in a test, or with the builder not set up) still says where the run came from.

export interface PromptLinks {
  open: (promptId: string) => void;
  refine: ((run: ImageRun, image: ImageInfo | null) => void) | null; // null: the builder is not set up, so there is nothing to refine in
}

export const PromptLinksContext = createContext<PromptLinks | null>(null);
export const usePromptLinks = (): PromptLinks | null => useContext(PromptLinksContext);

/** `From "Lighthouse" v3`, with `edited` when the words sent are not those of the version and `deleted` when the prompt is in the bin; opens the prompt. A prompt that has been
 *  deleted for good has no title and cannot be opened: the line still says so. */
export function RunFrom({ run }: { run: Run }) {
  const links = usePromptLinks();
  const ref = run.prompt_ref;
  if (!ref) return null;
  const line = refLine(ref);
  return (
    <p className="run-from" data-note="from-prompt">
      {links && ref.title !== null
        ? <button type="button" className="link-button" onClick={() => links.open(ref.id)} title="Open this prompt in the Prompt builder">{line}</button>
        : line}
    </p>
  );
}

/** Refine in Prompt builder, for a finished picture. Not shown when the builder is not set up. `compact` is for the viewer's row of buttons on a phone, where there is no room for the
 *  whole name: it shows "Refine" and keeps the whole name for a screen reader and the tooltip. */
export function RefineButton({ run, image, className = "button small ghost", compact = false }: { run: ImageRun; image: ImageInfo | null; className?: string; compact?: boolean }) {
  const links = usePromptLinks();
  if (!links?.refine || run.status !== "done" || !image) return null;
  const refine = links.refine;
  return (
    <button type="button" className={className} data-action="refine" onClick={() => refine(run, image)} aria-label={compact ? "Refine in Prompt builder" : undefined}
      title="Open a chat in the Prompt builder about this picture, to say what is wrong and get a better version of the prompt">
      {compact ? "Refine" : "Refine in Prompt builder"}
    </button>
  );
}
