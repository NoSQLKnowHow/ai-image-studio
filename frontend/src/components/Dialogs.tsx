import * as Dialog from "@radix-ui/react-dialog";
import { useEffect } from "react";
import { useReturnFocus } from "../hooks";
import { largerTarget } from "../options";
import type { ImageInfo, Run } from "../types";
import { ChevronLeft, ChevronRight, CloseIcon, DownloadIcon, EnlargeIcon } from "./icons";

/** What the viewer says about a request made from it. The page behind a modal, toasts included, is hidden from
 *  screen readers, so the answer is shown inside the viewer instead (DESIGN.md §24.2). */
export interface ViewerNotice {
  kind: "info" | "error";
  text: string;
}

export function Lightbox({ run, index, notice, onIndex, onRegenerateLarger, onClose }: {
  run: Run | null;
  index: number;
  notice: ViewerNotice | null;
  onIndex: (index: number) => void;
  onRegenerateLarger: (image: ImageInfo) => void;
  onClose: () => void;
}) {
  const target = run ? largerTarget(run) : null; // the same rule as the run's card
  const images = run?.images ?? [];
  const image = images[index];
  const count = images.length;
  const { props: returnFocus } = useReturnFocus();

  useEffect(() => {
    if (!run || count < 2) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") onIndex((index + 1) % count);
      if (e.key === "ArrowLeft") onIndex((index - 1 + count) % count);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [run, index, count, onIndex]);

  return (
    <Dialog.Root open={!!run && !!image} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay dark" />
        <Dialog.Content className="lightbox" aria-describedby={undefined} {...returnFocus}>
          {run && image && (
            <>
              <div className="lightbox-bar">
                <Dialog.Title className="lightbox-title">
                  {count > 1 ? `Image ${index + 1} of ${count} · ` : ""}seed {image.seed} · {image.width}×{image.height}
                </Dialog.Title>
                <div className="lightbox-actions">
                  {target && (
                    <button type="button" className="button small" data-action="regenerate-larger" onClick={() => onRegenerateLarger(image)}
                      aria-label={`Regenerate larger: ${target.width}×${target.height}, ${target.steps} steps`}
                      title={`Regenerate this image at ${target.width}×${target.height}, ${target.steps} steps. The same seed at a bigger size makes a different picture.`}>
                      <EnlargeIcon /> Regenerate larger
                    </button>
                  )}
                  <a className="button small" href={image.download_url} download><DownloadIcon /> Download</a>
                  {image.thumb_url && (
                    <a className="button small" href={`${image.thumb_url}?download=1`} download
                      title="A small copy of this image (WebP, 512 px on the long side)"><DownloadIcon /> Thumbnail</a>
                  )}
                  <Dialog.Close className="button small ghost icon-only" aria-label="Close"><CloseIcon /></Dialog.Close>
                </div>
              </div>
              <div className={`lightbox-stage${image.has_alpha ? " checker" : ""}`}>
                <img src={image.url} alt={run.prompt} />
              </div>
              <div className="lightbox-notice-region" role="status">
                {notice && <p className={`lightbox-notice${notice.kind === "error" ? " error" : ""}`}>{notice.text}</p>}
              </div>
              {count > 1 && (
                <>
                  <button type="button" className="lightbox-nav prev" aria-label="Previous image"
                    onClick={() => onIndex((index - 1 + count) % count)}><ChevronLeft /></button>
                  <button type="button" className="lightbox-nav next" aria-label="Next image"
                    onClick={() => onIndex((index + 1) % count)}><ChevronRight /></button>
                </>
              )}
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** After a delete, keep a keyboard user's place: the Delete button of the next card (or the previous
 *  one at the end of the list), else the prompt box once the list is empty. */
function focusAfterDelete(runId: string): HTMLElement | null {
  const cards = [...document.querySelectorAll<HTMLElement>("article.run-card")];
  const at = cards.findIndex((card) => card.dataset.runId === runId);
  for (const card of [cards[at + 1], cards[at - 1]]) {
    const button = card?.querySelector<HTMLButtonElement>('button[data-action="delete"]:not(:disabled)');
    if (button) return button;
  }
  return document.getElementById("prompt");
}

/** The Reuse button of a run's card: where focus goes when a control that had it disappears. */
export function cardReuseButton(runId: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`article.run-card[data-run-id="${runId}"] button[data-action="reuse"]`);
}

export function ConfirmCancel({ run, onBack, onConfirm }: { run: Run | null; onBack: () => void; onConfirm: () => void }) {
  const done = run?.images.length ?? 0;
  const { props: returnFocus, redirectTo } = useReturnFocus();
  const confirm = () => {
    if (run) redirectTo(cardReuseButton(run.id)); // the Cancel button turns into "Stopping…" and then goes away
    onConfirm();
  };
  return (
    <Dialog.Root open={!!run} onOpenChange={(open) => !open && onBack()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm" role="alertdialog" {...returnFocus}>
          <Dialog.Title>Stop this run?</Dialog.Title>
          <Dialog.Description>
            {done
              ? `The ${done === 1 ? "image" : `${done} images`} already finished ${done === 1 ? "is" : "are"} kept. The one being made now is discarded.`
              : "Nothing has been finished yet, so nothing is kept. The image being made now is discarded."}
          </Dialog.Description>
          <div className="confirm-actions">
            <Dialog.Close className="button">Keep going</Dialog.Close>
            <button type="button" className="button danger-solid" onClick={confirm}>Stop generating</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ConfirmDelete({ run, onCancel, onConfirm }: { run: Run | null; onCancel: () => void; onConfirm: () => void }) {
  const n = run?.images.length ?? 0;
  const { props: returnFocus, redirectTo } = useReturnFocus();
  const confirm = () => {
    if (run) redirectTo(focusAfterDelete(run.id));
    onConfirm();
  };
  return (
    <Dialog.Root open={!!run} onOpenChange={(open) => !open && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm" role="alertdialog" {...returnFocus}>
          <Dialog.Title>Delete this run?</Dialog.Title>
          <Dialog.Description>
            {n ? `Its ${n === 1 ? "image is" : `${n} images are`} removed from the Spark.` : "It is removed from the history."}
            {run?.pinned ? " You marked it Keep." : ""} This can't be undone.
          </Dialog.Description>
          <div className="confirm-actions">
            <Dialog.Close className="button">Cancel</Dialog.Close>
            <button type="button" className="button danger-solid" onClick={confirm}>Delete</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
