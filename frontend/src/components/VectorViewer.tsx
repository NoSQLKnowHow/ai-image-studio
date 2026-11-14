import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useId, useRef, useState } from "react";
import { useReturnFocus } from "../hooks";
import { ViewerAnnotationsButton, ViewerAnnotationsPanel, panelEscape } from "./RunAnnotations";
import type { BinnedVectorItem, VectorItem, VectorRun } from "../types";
import { DEFAULT_PNG_PX, binnedVersionName, bytesText, drawingCaption, isStacked, mmText, pngDownloadUrl, viewerSummary } from "../vector";
import type { ViewerNotice } from "./Dialogs";
import { ChevronLeft, ChevronRight, CloseIcon, CopyIcon, DownloadIcon, TrashIcon } from "./icons";
import { StackView } from "./StackView";
import { VersionFacts } from "./VersionFacts";

// The viewer of a Vector run's versions (DESIGN.md §35.2): the large view that opens when a version's preview on the card is pressed. It works like the Images
// viewer: it covers the page, Escape closes it, the arrow keys and the buttons move between the run's versions, nothing behind it can be reached, and focus
// goes back to the preview that opened it. It shows the model's drawing beside the vector it was traced into, then the facts of the version, its downloads,
// the PNG size box (one for the viewer, not one for each version), Delete version, and for a version made Stacked (depth) the stack view.
//
// The SVG and the layers' SVGs are only ever shown in <img>s, where a script in them could never run (§31.6 "Safety"); "Copy SVG code" fetches the file as
// text to copy it, through the page's own handler.

export function VectorViewer({ run, index, readOnly, pngRange, notice, onIndex, onCopySvg, onDeleteVersion, onClose }: {
  run: VectorRun | null;
  index: number; // the place in the list below
  readOnly: boolean; // looking at a version in the bin, or at a run in the bin: downloads work, nothing that changes it (DESIGN.md §30.3, §35.2)
  pngRange: { min: number; max: number }; // the PNG's long side, in pixels
  notice: ViewerNotice | null; // what a request made from here answered: the page behind a modal is hidden from screen readers, so it is said inside
  onIndex: (index: number) => void;
  onCopySvg: (item: VectorItem) => void;
  onDeleteVersion: (item: VectorItem, position: number, of: number) => void; // which version, its place among those the run has now, and how many
  onClose: () => void;
}) {
  const id = useId();
  const { props: returnFocus } = useReturnFocus();
  // The tags and notes panel (DESIGN.md §45.3), closed until its button opens it and closed again for another run
  const [notesOpen, setNotesOpen] = useState(false);
  const notesButton = useRef<HTMLButtonElement>(null);
  const notesPanel = useId();
  useEffect(() => {
    setNotesOpen(false);
  }, [run?.id]);
  // the versions to page through: the run's, by place; or, for a look at the Deleted view, the ones that are in the bin
  const versions: (VectorItem | BinnedVectorItem)[] = run ? (readOnly && run.deleted_at === null ? run.binned_vectors : [...run.vectors].sort((a, b) => a.idx - b.idx)) : [];
  const count = versions.length;
  const item = versions[index];
  const binned = !!run && readOnly && run.deleted_at === null; // the versions listed are the deleted ones
  const [pngText, setPngText] = useState(String(DEFAULT_PNG_PX));
  const [missingDrawing, setMissingDrawing] = useState<string | null>(null); // the id of a version whose drawing could not be loaded

  useEffect(() => {
    if (!run || count < 2) return;
    const onKey = (e: KeyboardEvent) => {
      // a range or number box in the viewer uses the arrows itself (the stack view's control, the PNG size), and so does a text box (the tag box, the note: DESIGN.md §45.3)
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowRight") onIndex((index + 1) % count);
      if (e.key === "ArrowLeft") onIndex((index - 1 + count) % count);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [run, index, count, onIndex]);

  const sizeNumber = Number(pngText);
  const pngInvalid = pngText.trim() === "" || !Number.isInteger(sizeNumber) || sizeNumber < pngRange.min || sizeNumber > pngRange.max
    ? `Enter a whole number from ${pngRange.min} to ${pngRange.max}.`
    : null;
  const working = !!run && (run.status === "queued" || run.status === "running");
  const position = index + 1;

  return (
    <Dialog.Root open={!!run && !!item} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay dark" />
        <Dialog.Content className="lightbox vector-viewer" aria-describedby={undefined} {...returnFocus}
          onEscapeKeyDown={(event) => { panelEscape(event, notesOpen, () => { setNotesOpen(false); notesButton.current?.focus(); }); }}>
          {run && item && (
            <>
              <div className="lightbox-bar">
                <Dialog.Title className="lightbox-title" data-viewer-title>
                  {binned ? `${binnedVersionName(item)} · deleted` : viewerSummary(item, position, count)}
                </Dialog.Title>
                <div className="lightbox-actions">
                  <ViewerAnnotationsButton run={run} open={notesOpen} panelId={notesPanel} buttonRef={notesButton} onToggle={() => setNotesOpen(!notesOpen)} />
                  {/* Delete version: only on a version that is in the run now, of a run that has stopped working and is in the history (§35.3 item 1) */}
                  {!readOnly && !working && (
                    <button type="button" className="button small danger" data-action="delete-version" onClick={() => onDeleteVersion(item, position, count)}
                      title="Delete this version. It goes to Deleted for a while, and can be restored from there.">
                      <TrashIcon /> Delete version
                    </button>
                  )}
                  <Dialog.Close className="button small ghost icon-only" aria-label="Close"><CloseIcon /></Dialog.Close>
                </div>
              </div>
              <div className="vv-scroll" key={item.id}>
                <div className="vv-body">
                  {/* the drawing and the vector it was traced into, the same size, so that the trace can be judged against what it was traced from */}
                  <div className="vv-pictures">
                    <figure className="vv-panel" data-panel="drawing">
                      <figcaption className="vv-heading">The drawing</figcaption>
                      <div className="vv-frame" style={{ aspectRatio: `${run.options.draw_width} / ${run.options.draw_height}` }}>
                        {missingDrawing === item.id ? (
                          <p className="vv-missing" role="status">The drawing is no longer on the disk.</p>
                        ) : (
                          <img src={item.drawing_url} alt="What the image model drew, which was traced into the vector" onError={() => setMissingDrawing(item.id)} />
                        )}
                      </div>
                      <p className="vv-caption">{drawingCaption(run)}</p>
                      {missingDrawing !== item.id && (
                        <a className="button small" href={item.drawing_download_url} download data-action="download-drawing" title="The picture the model drew, as a PNG">
                          <DownloadIcon /> Download drawing
                        </a>
                      )}
                    </figure>
                    <figure className="vv-panel" data-panel="vector">
                      <figcaption className="vv-heading">The vector</figcaption>
                      {/* white behind it whatever the theme: white is the material, and black shapes would vanish on a dark page */}
                      <div className="vv-frame white" style={{ aspectRatio: `${item.width_mm} / ${item.height_mm}` }}>
                        <img src={item.url} alt={`The vector: ${mmText(item.width_mm)} by ${mmText(item.height_mm)} mm, ${item.layers} ${item.layers === 1 ? "layer" : "layers"}`} data-vector-image />
                      </div>
                      <p className="vv-caption">{mmText(item.width_mm)} × {mmText(item.height_mm)} mm, {bytesText(item.bytes)}</p>
                    </figure>
                  </div>

                  <div className="vv-details">
                    <VersionFacts run={run} item={item} />
                    <div className="vector-downloads">
                      <a className="button small" href={item.download_url} download data-action="download-svg" title={`The combined SVG, ${bytesText(item.bytes)}, at its real size in millimetres`}>
                        <DownloadIcon /> Download SVG
                      </a>
                      <a className="button small" href={item.zip_url} download data-action="download-zip" title="One SVG for each layer, all at the same size and origin, so they register when stacked">
                        <DownloadIcon /> Download layers (ZIP)
                      </a>
                      <button type="button" className="button small" data-action="copy-svg" onClick={() => onCopySvg(item)} title="Copy the SVG's text, to paste into your laser software">
                        <CopyIcon /> Copy SVG code
                      </button>
                    </div>
                    {/* one PNG size for the viewer: a PNG drawn from the SVG at the size typed here (its link is the server's own with the size added) */}
                    <div className="vector-png">
                      <div className="field">
                        <label htmlFor={`${id}-png`}>PNG size, long side</label>
                        <span className="vector-png-row">
                          <input id={`${id}-png`} type="number" inputMode="numeric" value={pngText} aria-invalid={pngInvalid !== null} aria-describedby={`${id}-png-help`}
                            data-vector-png-size onChange={(e) => setPngText(e.target.value)} />
                          <span aria-hidden="true">px</span>
                          {pngInvalid === null ? (
                            <a className="button small" href={pngDownloadUrl(item, sizeNumber)} download data-action="download-png"><DownloadIcon /> Download PNG</a>
                          ) : (
                            // a size the server would refuse: the link is switched off rather than left to fail
                            <button type="button" className="button small" data-action="download-png" aria-disabled="true" onClick={(e) => e.preventDefault()}>
                              <DownloadIcon /> Download PNG
                            </button>
                          )}
                        </span>
                        <p id={`${id}-png-help`} className={pngInvalid ? "field-error" : "field-hint"} role={pngInvalid ? "alert" : undefined}>
                          {pngInvalid ?? "Drawn from the SVG, so it is sharp at any size."}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* a version made Stacked (depth) shows how the sheets stack; the keyed wrapper starts each version with all its sheets */}
                  {isStacked(item.check) && item.layer_urls.length > 0 && <StackView key={item.id} item={item} />}
                </div>
              </div>
              {notesOpen && <ViewerAnnotationsPanel run={run} panelId={notesPanel} />}
              <div className="lightbox-notice-region" role="status">
                {notice && <p className={`lightbox-notice${notice.kind === "error" ? " error" : ""}`}>{notice.text}</p>}
              </div>
              {count > 1 && (
                <>
                  <button type="button" className="lightbox-nav prev" aria-label="Previous version" onClick={() => onIndex((index - 1 + count) % count)}><ChevronLeft /></button>
                  <button type="button" className="lightbox-nav next" aria-label="Next version" onClick={() => onIndex((index + 1) % count)}><ChevronRight /></button>
                </>
              )}
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
