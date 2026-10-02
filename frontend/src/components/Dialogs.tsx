import * as Dialog from "@radix-ui/react-dialog";
import { useEffect } from "react";
import { useReturnFocus } from "../hooks";
import type { Run } from "../types";
import { ChevronLeft, ChevronRight, CloseIcon, DownloadIcon } from "./icons";

export function Lightbox({ run, index, onIndex, onClose }: {
  run: Run | null;
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
}) {
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
                  <a className="button small" href={image.download_url} download><DownloadIcon /> Download</a>
                  <Dialog.Close className="button small ghost icon-only" aria-label="Close"><CloseIcon /></Dialog.Close>
                </div>
              </div>
              <div className={`lightbox-stage${image.has_alpha ? " checker" : ""}`}>
                <img src={image.url} alt={run.prompt} />
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
            {n ? `Its ${n === 1 ? "image is" : `${n} images are`} removed from the Spark.` : "It is removed from the history."} This can't be undone.
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
