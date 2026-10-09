import * as Dialog from "@radix-ui/react-dialog";
import { useEffect } from "react";
import { useReturnFocus } from "../hooks";
import { largerTarget } from "../options";
import type { KnownImage } from "../tray";
import type { FourKTarget, ImageInfo, ImageRun, Run } from "../types";
import { viewerItems, viewerKnown, viewerTitle } from "../viewer";
import { FourKButton } from "./FourKButton";
import { ChevronLeft, ChevronRight, CloseIcon, DownloadIcon, EditIcon, EnlargeIcon } from "./icons";

/** What the viewer says about a request made from it. The page behind a modal, toasts included, is hidden from
 *  screen readers, so the answer is shown inside the viewer instead (DESIGN.md §24.2). */
export interface ViewerNotice {
  kind: "info" | "error";
  text: string;
}

export function Lightbox({ run, index, notice, canEdit, making4k, onIndex, onRegenerateLarger, onMake4K, onEditThis, onClose }: {
  run: ImageRun | null;
  index: number;
  notice: ViewerNotice | null;
  canEdit: boolean; // the studio can edit, so "Edit this" is offered
  making4k: ReadonlySet<string>; // ids of the images whose 4K copy is being made (DESIGN.md §27)
  onIndex: (index: number) => void;
  onRegenerateLarger: (image: ImageInfo) => void;
  onMake4K: (image: FourKTarget) => void;
  onEditThis: (image: KnownImage) => boolean; // whether it was added (the tray may be full)
  onClose: () => void;
}) {
  const target = run ? largerTarget(run) : null; // the same rule as the run's card
  const items = run ? viewerItems(run) : []; // an edit's sources, then its results
  const item = items[index];
  const count = items.length;
  const { props: returnFocus, redirectTo } = useReturnFocus();

  useEffect(() => {
    if (!run || count < 2) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") onIndex((index + 1) % count);
      if (e.key === "ArrowLeft") onIndex((index - 1 + count) % count);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [run, index, count, onIndex]);

  const source = item?.kind === "source" ? item.input : null;
  const result = item?.kind === "result" ? item.image : null;
  const shown = source ?? result;

  return (
    <Dialog.Root open={!!run && !!item} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay dark" />
        <Dialog.Content className="lightbox" aria-describedby={undefined} {...returnFocus}>
          {run && item && shown && (
            <>
              <div className="lightbox-bar">
                <Dialog.Title className="lightbox-title">{viewerTitle(item, run.mode === "edit")}</Dialog.Title>
                <div className="lightbox-actions">
                  {result && target && (
                    <button type="button" className="button small" data-action="regenerate-larger" onClick={() => onRegenerateLarger(result)}
                      aria-label={`Regenerate larger: ${target.width}×${target.height}, ${target.steps} steps`}
                      title={`Regenerate this image at ${target.width}×${target.height}, ${target.steps} steps. The same seed at a bigger size makes a different picture.`}>
                      <EnlargeIcon /> Regenerate larger
                    </button>
                  )}
                  {canEdit && (
                    <button type="button" className="button small" data-action="edit-this" onClick={() => {
                        if (onEditThis(viewerKnown(run, item))) redirectTo(document.getElementById("prompt")); // the viewer closes: go to the prompt, not back to the thumbnail
                      }}
                      title="Add this picture to the images you are editing">
                      <EditIcon /> Edit this
                    </button>
                  )}
                  {result && <a className="button small" href={result.download_url} download><DownloadIcon /> Download</a>}
                  {shown && <FourKButton image={shown} making={making4k.has(shown.id)} className="button small" onMake={() => onMake4K(shown)} />}
                  {result?.thumb_url && (
                    <a className="button small" href={`${result.thumb_url}?download=1`} download
                      title="A small copy of this image (WebP, 512 px on the long side)"><DownloadIcon /> Thumbnail</a>
                  )}
                  <Dialog.Close className="button small ghost icon-only" aria-label="Close"><CloseIcon /></Dialog.Close>
                </div>
              </div>
              <div className={`lightbox-stage${shown.has_alpha ? " checker" : ""}`}>
                <img src={shown.url} alt={source ? `Source image ${source.position}` : run.prompt} />
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
 *  one at the end of the list) on the same tab, else the first box of that tab's form once the list is empty. */
function focusAfterDelete(runId: string): HTMLElement | null {
  const own = document.querySelector<HTMLElement>(`article.run-card[data-run-id="${runId}"]`);
  const cards = [...(own?.closest(".timeline") ?? document).querySelectorAll<HTMLElement>("article.run-card")];
  const at = cards.findIndex((card) => card.dataset.runId === runId);
  for (const card of [cards[at + 1], cards[at - 1]]) {
    const button = card?.querySelector<HTMLButtonElement>('button[data-action="delete"]:not(:disabled)');
    if (button) return button;
  }
  return own?.closest(".tab-panel")?.querySelector<HTMLElement>("#prompt, .music-grid input") ?? document.getElementById("prompt");
}

/** The Reuse button of a run's card: where focus goes when a control that had it disappears. */
export function cardReuseButton(runId: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`article.run-card[data-run-id="${runId}"] button[data-action="reuse"]`);
}

export function ConfirmCancel({ run, onBack, onConfirm }: { run: Run | null; onBack: () => void; onConfirm: () => void }) {
  const music = run?.mode === "music";
  const thing = music ? "track" : "image";
  const done = (music ? run?.tracks.length : run?.images.length) ?? 0;
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
              ? `The ${done === 1 ? thing : `${done} ${thing}s`} already finished ${done === 1 ? "is" : "are"} kept. The one being made now is discarded.`
              : `Nothing has been finished yet, so nothing is kept. The ${thing} being made now is discarded.`}
          </Dialog.Description>
          <div className="confirm-actions">
            <Dialog.Close className="button">Keep going</Dialog.Close>
            <button type="button" className="button danger-solid" onClick={confirm}>{music ? "Stop making music" : "Stop generating"}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ConfirmDelete({ run, onCancel, onConfirm }: { run: Run | null; onCancel: () => void; onConfirm: () => void }) {
  const music = run?.mode === "music";
  const thing = music ? "track" : "image";
  const n = (music ? run?.tracks.length : run?.images.length) ?? 0;
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
            {n ? `Its ${n === 1 ? `${thing} is` : `${n} ${thing}s are`} removed from the Spark.` : "It is removed from the history."}
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
