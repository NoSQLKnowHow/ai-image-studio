import * as Dialog from "@radix-ui/react-dialog";
import { useId, useState } from "react";
import { sizeText } from "../format";
import { useReturnFocus } from "../hooks";
import type { Capabilities, ImageInfo, ImageRun, VariationHow } from "../types";
import { MAX_TAKES, pictureChoice, takesChoice, takesLimit, type MoreLikeThisAsk } from "../variations";

// More like this (DESIGN.md section 43.3): a small dialog opened from the viewer (for the picture on the screen) or from a card (for the run). It asks which kind of variation, how many
// pictures, and the words, and hands the answer to the page, which builds an ordinary run from it. A refusal by the server is shown here, in its own words, and the dialog stays open.

/** What the dialog was opened for: the run, and the picture that was on the screen (null from a card). */
export interface MoreLikeThisTarget {
  run: ImageRun;
  image: ImageInfo | null;
}

/** The form itself. It is its own component, keyed by the run and picture, so that opening the dialog for another picture starts from that run's words and not the last one's. */
function MoreLikeThisForm({ target, caps, error, making, onMake }: { target: MoreLikeThisTarget; caps: Capabilities; error: string | null; making: boolean; onMake: (ask: MoreLikeThisAsk) => void }) {
  const { run, image } = target;
  const ids = { count: useId(), words: useId(), hint: useId() };
  const limit = takesLimit(caps);
  const picture = pictureChoice(caps, run, image);
  const [how, setHow] = useState<VariationHow>("seeds");
  const [count, setCount] = useState(Math.min(MAX_TAKES, limit)); // 4 by default (the most there is), or what the server allows
  const [words, setWords] = useState(run.prompt);
  const choice = how === "seeds" ? takesChoice : picture;
  const ready = words.trim().length > 0 && choice.enabled && !making;
  const noun = count === 1 ? "picture" : "pictures";
  // what will be made, in one line, so that the cost is seen before pressing Make: for more takes it is exactly this run's own size and steps
  const summary = how === "seeds"
    ? `${count} ${noun} at ${sizeText(run)}, ${run.options.steps} steps: the same as this run, with new seeds.`
    : `${count} ${noun}, each an edit of this picture. The result follows the picture's size; ${run.options.steps} steps.`;

  return (
    <form className="more-like-this" onSubmit={(event) => { event.preventDefault(); if (ready) onMake({ how, count, words, image }); }}>
      <fieldset className="mlt-choices">
        <legend>Make</legend>
        <label className="mlt-choice">
          <input type="radio" name="mlt-how" value="seeds" data-choice="seeds" checked={how === "seeds"} onChange={() => setHow("seeds")} />
          <span>
            <strong>More takes of the prompt</strong>
            <span className="hint">The same words and settings with new random seeds: different pictures, not small changes to this one.</span>
          </span>
        </label>
        {/* greyed, with the reason, until the machine has been told that the editing model does this well (section 43.3) */}
        <label className="mlt-choice" aria-disabled={!picture.enabled || undefined}>
          <input type="radio" name="mlt-how" value="picture" data-choice="picture" checked={how === "picture"} disabled={!picture.enabled} onChange={() => setHow("picture")}
            aria-describedby={picture.enabled ? undefined : ids.hint} />
          <span>
            <strong>Variations of this picture</strong>
            <span className="hint">{picture.enabled ? "This picture goes into the editing model, so the results look like it." : <span id={ids.hint}>{picture.reason}</span>}</span>
          </span>
        </label>
      </fieldset>

      <div className="mlt-row">
        <label htmlFor={ids.count}>Pictures</label>
        <select id={ids.count} data-field="count" value={count} onChange={(event) => setCount(Number(event.target.value))}>
          {Array.from({ length: limit }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>

      <div className="mlt-row mlt-words">
        <label htmlFor={ids.words}>Words</label>
        <textarea id={ids.words} data-field="words" rows={4} value={words} maxLength={caps.limits.prompt_chars} onChange={(event) => setWords(event.target.value)} />
      </div>

      <p className="hint" data-field="summary">{summary}</p>
      {error && <p className="form-error" role="alert" data-field="error">{error}</p>}

      <div className="confirm-actions">
        <Dialog.Close className="button" type="button">Cancel</Dialog.Close>
        <button type="submit" className="button primary" data-action="make-more" disabled={!ready}>{making ? "Making…" : "Make"}</button>
      </div>
    </form>
  );
}

/** The dialog. `target` null means closed. */
export function MoreLikeThisDialog({ target, caps, error, making, onMake, onClose }: {
  target: MoreLikeThisTarget | null;
  caps: Capabilities | null;
  error: string | null;
  making: boolean;
  onMake: (ask: MoreLikeThisAsk) => void;
  onClose: () => void;
}) {
  const { props: returnFocus } = useReturnFocus();
  return (
    <Dialog.Root open={!!target && !!caps} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm more-like-this-dialog" aria-describedby={undefined} {...returnFocus}>
          <Dialog.Title>More like this</Dialog.Title>
          {target && caps && <MoreLikeThisForm key={`${target.run.id}:${target.image?.id ?? ""}`} target={target} caps={caps} error={error} making={making} onMake={onMake} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
