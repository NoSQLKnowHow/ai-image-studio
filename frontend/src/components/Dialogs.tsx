import * as Dialog from "@radix-ui/react-dialog";
import { useEffect } from "react";
import { deleteItemQuestion, deleteItemTitle, emptyBinQuestion, type BinContents, type ItemRef } from "../format";
import { useReturnFocus } from "../hooks";
import { largerTarget } from "../options";
import type { KnownImage } from "../tray";
import type { FourKTarget, ImageInfo, ImageRun, Run, UpscalerStatus } from "../types";
import { viewerItems, viewerKnown, viewerTitle } from "../viewer";
import { FourKButton } from "./FourKButton";
import { ChevronLeft, ChevronRight, CloseIcon, DownloadIcon, EditIcon, EnlargeIcon, TrashIcon } from "./icons";

/** What the viewer says about a request made from it. The page behind a modal, toasts included, is hidden from
 *  screen readers, so the answer is shown inside the viewer instead (DESIGN.md §24.2). */
export interface ViewerNotice {
  kind: "info" | "error";
  text: string;
}

export function Lightbox({ run, index, notice, canEdit, readOnly, making4k, enlarging, enlargeWaiting, upscaler, onIndex, onRegenerateLarger, onMake4K, onEnlarge, onEditThis, onDeletePicture, onClose }: {
  run: ImageRun | null;
  index: number;
  notice: ViewerNotice | null;
  canEdit: boolean; // the studio can edit, so "Edit this" is offered
  readOnly: boolean; // the run is in the bin: look at it and download it, but nothing that changes it (DESIGN.md §30.3)
  making4k: ReadonlySet<string>; // ids of the images whose 4K copy is being made (DESIGN.md §27)
  enlarging: ReadonlySet<string>; // ids of the images being enlarged with the upscaler model (DESIGN.md §28)
  enlargeWaiting: ReadonlySet<string>; // ids of the images whose Enlarge is waiting for the picture being made (§28.3)
  upscaler: UpscalerStatus | null; // whether Enlarge can run here (from the capabilities)
  onIndex: (index: number) => void;
  onRegenerateLarger: (image: ImageInfo) => void;
  onMake4K: (image: FourKTarget) => void;
  onEnlarge: (image: FourKTarget) => void;
  onEditThis: (image: KnownImage) => boolean; // whether it was added (the tray may be full)
  onDeletePicture: (image: ImageInfo, position: number, of: number) => void; // Delete picture (DESIGN.md §33.1): which result of how many
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
                  {!readOnly && result && target && (
                    <button type="button" className="button small" data-action="regenerate-larger" onClick={() => onRegenerateLarger(result)}
                      aria-label={`Regenerate larger: ${target.width}×${target.height}, ${target.steps} steps`}
                      title={`Regenerate this image at ${target.width}×${target.height}, ${target.steps} steps. The same seed at a bigger size makes a different picture.`}>
                      <EnlargeIcon /> Regenerate larger
                    </button>
                  )}
                  {canEdit && !readOnly && (
                    <button type="button" className="button small" data-action="edit-this" onClick={() => {
                        if (onEditThis(viewerKnown(run, item))) redirectTo(document.getElementById("prompt")); // the viewer closes: go to the prompt, not back to the thumbnail
                      }}
                      title="Add this picture to the images you are editing">
                      <EditIcon /> Edit this
                    </button>
                  )}
                  {result && <a className="button small" href={result.download_url} download><DownloadIcon /> Download</a>}
                  {/* Delete picture (DESIGN.md §33.1): on a RESULT of a finished run that is not in the bin; never on an edit's source, which is the run's
                      input, and never while Make 4K or Enlarge is working on this picture (the copy being written would be left behind) */}
                  {result && !readOnly && run.status !== "queued" && run.status !== "running" && item?.kind === "result" && (
                    <button type="button" className="button small danger" data-action="delete-picture"
                      disabled={making4k.has(result.id) || enlarging.has(result.id) || enlargeWaiting.has(result.id)}
                      title={making4k.has(result.id) || enlarging.has(result.id) || enlargeWaiting.has(result.id)
                        ? "Make 4K or Enlarge is working on this picture. Delete it when that has finished."
                        : "Delete this picture. It goes to Deleted for a while, and can be restored from there."}
                      onClick={() => onDeletePicture(result, item.number, item.of)}>
                      <TrashIcon /> Delete picture
                    </button>
                  )}
                  {shown && !readOnly && (
                    <FourKButton image={shown} making={making4k.has(shown.id)} enlarging={enlarging.has(shown.id)} waiting={enlargeWaiting.has(shown.id)} upscaler={upscaler}
                      className="button small" onMake={() => onMake4K(shown)} onEnlarge={() => onEnlarge(shown)} />
                  )}
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

/** Delete, in its three kinds (DESIGN.md §30): to the bin (a finished run, with a bin), for good out of the bin, and for good without a bin
 *  or for a run that has not made anything. For a run that is filed in a project the question names the project (§32.3 item 5): it is not a
 *  second hurdle, only the one fact that matters. `projectName` is that project's name, or null if the page does not know it. */
export function ConfirmDelete({ run, binDays, projectName, onCancel, onConfirm }: { run: Run | null; binDays: number; projectName: string | null; onCancel: () => void; onConfirm: () => void }) {
  const music = run?.mode === "music";
  const thing = music ? "track" : "image";
  const n = (music ? run?.tracks.length : run?.images.length) ?? 0;
  // One dialog, three cases. A finished run, with a bin: Delete moves it to Deleted (nothing is lost yet). A run already in the bin:
  // "Delete forever". Anything else (a run that is only waiting, or any run when there is no bin): deleted for good, with no way back.
  const inBin = !!run?.deleted_at;
  // it moves to the bin only if there is a bin, it is not already in it, and it has finished (a run still waiting made nothing worth keeping)
  const toBin = !!run && !inBin && binDays > 0 && run.status !== "queued" && run.status !== "running";
  // what "for good" means: the pictures or tracks are removed from the Spark, not just hidden
  const gone = n ? `Its ${n === 1 ? `${thing} is` : `${n} ${thing}s are`} removed from the Spark.` : "It is removed from the history.";
  // a run filed in a project: the project is named, and what happens to the run there is said (a run in the bin keeps its project, and Restore puts it back)
  const filed = !!run && run.project_id !== null;
  const project = projectName === null ? "a project" : `the project “${projectName}”`;
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
          <Dialog.Title>{inBin ? "Delete this run for good?" : "Delete this run?"}</Dialog.Title>
          <Dialog.Description>
            {toBin
              ? `${filed ? `It is in ${project}. ` : ""}It moves to Deleted and stays there for ${binDays} ${binDays === 1 ? "day" : "days"}; ${filed ? "restoring it puts it back in the project" : "you can restore it from there"}. After that it is gone for good.`
              : `${gone} ${filed ? `It is in ${project}, which loses it. ` : ""}This can't be undone.`}
            {run?.pinned && !inBin && !filed ? " You marked it Keep." : ""}
          </Dialog.Description>
          <div className="confirm-actions">
            <Dialog.Close className="button">Cancel</Dialog.Close>
            <button type="button" className="button danger-solid" data-action="confirm-delete" onClick={confirm}>{inBin ? "Delete forever" : "Delete"}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** What the question about deleting one picture or track needs (DESIGN.md §33.1): which run and which item (its place, `of` how many, its seed, and its
 *  id for the request), whether it is the run's last, and whether it is in the bin already (Delete forever). */
export interface ItemAsk {
  run: Run;
  item: ItemRef & { id: string };
  last: boolean;
  forever: boolean;
}

/** The question before a picture or track is deleted (§33.1): to the bin, the whole run to the bin when it is the last, for good with no bin, or for
 *  good out of the bin. The answer is Delete or Delete forever; Cancel and Escape leave everything as it was. */
export function ConfirmDeleteItem({ ask, binDays, projectName, onCancel, onConfirm }: { ask: ItemAsk | null; binDays: number; projectName: string | null; onCancel: () => void; onConfirm: () => void }) {
  const { props: returnFocus } = useReturnFocus();
  return (
    <Dialog.Root open={!!ask} onOpenChange={(open) => !open && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm" role="alertdialog" {...returnFocus}>
          {ask && (
            <>
              <Dialog.Title>{deleteItemTitle(ask.item, ask.forever)}</Dialog.Title>
              <Dialog.Description>
                {deleteItemQuestion({ item: ask.item, binDays, last: ask.last, forever: ask.forever, filed: ask.run.project_id !== null, projectName })}
              </Dialog.Description>
              <div className="confirm-actions">
                <Dialog.Close className="button">Cancel</Dialog.Close>
                <button type="button" className="button danger-solid" data-action="confirm-delete-picture" onClick={onConfirm}>{ask.forever ? "Delete forever" : "Delete"}</button>
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Empty bin (DESIGN.md §30.1, §33.1): everything in the bin, both tabs, for good; the question says how many runs and how many pictures or tracks. */
export function ConfirmEmptyBin({ open, bin, wholeBin, onCancel, onConfirm }: { open: boolean; bin: BinContents; wholeBin: boolean; onCancel: () => void; onConfirm: () => void }) {
  const { props: returnFocus } = useReturnFocus();
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm" role="alertdialog" {...returnFocus}>
          <Dialog.Title>Empty the bin?</Dialog.Title>
          {/* what goes (with the whole-bin sentence when a project is chosen: the bar on screen shows only that project's part of the bin) */}
          <Dialog.Description>{emptyBinQuestion(bin, wholeBin)}</Dialog.Description>
          <div className="confirm-actions">
            <Dialog.Close className="button">Cancel</Dialog.Close>
            <button type="button" className="button danger-solid" data-action="confirm-empty-bin" onClick={onConfirm}>Empty bin</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
