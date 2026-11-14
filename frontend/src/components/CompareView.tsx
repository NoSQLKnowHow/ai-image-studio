import * as Dialog from "@radix-ui/react-dialog";
import { useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { useReturnFocus } from "../hooks";
import { clampPercent, percentAt, sliderKey, type ComparePicture } from "../variations";
import { CloseIcon, PinIcon } from "./icons";

// A/B compare (DESIGN.md section 43.5): two pictures from any runs, side by side or one over the other with a draggable divider. Fit shows each whole; 100% shows them at their own size and
// pans both together. Each side has Keep this one, which keeps the run the picture is in (Keep is per run) and changes nothing else.

type Mode = "side" | "slider";
type Zoom = "fit" | "actual";

/** One side's picture. In 100% the pane scrolls, and the other pane follows it: `onScroll` hands the position to the page's own sync. */
function Pane({ picture, zoom, paneRef, onScroll, caption }: { picture: ComparePicture; zoom: Zoom; paneRef: (node: HTMLDivElement | null) => void; onScroll: () => void; caption: string }) {
  return (
    <figure className="compare-pane" data-pane={caption}>
      <div className={`compare-scroll zoom-${zoom}${picture.hasAlpha ? " checker" : ""}`} ref={paneRef} onScroll={onScroll} tabIndex={zoom === "actual" ? 0 : undefined} aria-label={zoom === "actual" ? `${caption}: scroll to pan` : undefined}>
        <img src={picture.url} alt={picture.label} width={zoom === "actual" ? picture.width : undefined} height={zoom === "actual" ? picture.height : undefined} draggable={false} />
      </div>
      <figcaption>{picture.label}</figcaption>
    </figure>
  );
}

/** The slider view: the first picture over the second, shown up to the divider. The divider is the slider: it takes arrow keys, Home, End and Page Up and Down, and drags by mouse, pen and finger. */
function SliderView({ first, second, zoom }: { first: ComparePicture; second: ComparePicture; zoom: Zoom }) {
  const [value, setValue] = useState(50);
  const box = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const move = (event: ReactPointerEvent) => {
    const rect = box.current?.getBoundingClientRect();
    if (rect) setValue(percentAt(event.clientX, rect.left, rect.width));
  };
  const onKey = (event: ReactKeyboardEvent) => {
    const next = sliderKey(value, event.key);
    if (next === null) return; // any other key (Tab, Escape) is not the slider's
    event.preventDefault();
    setValue(next);
  };
  const shown = Math.round(value);
  return (
    <div className={`compare-slider zoom-${zoom}`} ref={box}
      onPointerDown={(event) => { dragging.current = true; (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId); move(event); }}
      onPointerMove={(event) => { if (dragging.current) move(event); }}
      onPointerUp={() => { dragging.current = false; }} onPointerCancel={() => { dragging.current = false; }}>
      <img className="compare-under" src={second.url} alt={second.label} draggable={false} />
      {/* the first picture, cut at the divider: only what is to the left of it is shown */}
      <img className="compare-over" src={first.url} alt={first.label} draggable={false} style={{ clipPath: `inset(0 ${100 - clampPercent(value)}% 0 0)` }} />
      <div className="compare-handle" style={{ left: `${clampPercent(value)}%` }} role="slider" tabIndex={0} aria-label="Divider between the two pictures" aria-orientation="horizontal"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={shown} aria-valuetext={`${shown}% of the first picture is shown`} data-slider onKeyDown={onKey} />
    </div>
  );
}

export function CompareView({ open, first, second, onSwap, onKeep, onClose }: {
  open: boolean;
  first: ComparePicture | null;
  second: ComparePicture | null;
  onSwap: () => void;
  onKeep: (runId: string) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<Mode>("side");
  const [zoom, setZoom] = useState<Zoom>("fit");
  const panes = useRef<[HTMLDivElement | null, HTMLDivElement | null]>([null, null]);
  const syncing = useRef(false);
  const { props: returnFocus } = useReturnFocus();

  // Panning moves both panes together: when one scrolls, the other is put at the same place. `syncing` stops the second pane's own scroll event from sending it back.
  const follow = (from: 0 | 1) => () => {
    if (syncing.current) { syncing.current = false; return; }
    const source = panes.current[from], target = panes.current[from === 0 ? 1 : 0];
    if (!source || !target) return;
    if (target.scrollLeft !== source.scrollLeft || target.scrollTop !== source.scrollTop) {
      syncing.current = true;
      target.scrollLeft = source.scrollLeft;
      target.scrollTop = source.scrollTop;
    }
  };

  const sides = first && second ? [first, second] : [];
  return (
    <Dialog.Root open={open && !!first && !!second} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay dark" />
        <Dialog.Content className="compare-view" aria-describedby={undefined} {...returnFocus}>
          <div className="compare-bar">
            <Dialog.Title className="compare-title">Compare</Dialog.Title>
            <div className="compare-controls">
              <div role="group" aria-label="Layout">
                <button type="button" className="button small" data-mode="side" aria-pressed={mode === "side"} onClick={() => setMode("side")}>Side by side</button>
                <button type="button" className="button small" data-mode="slider" aria-pressed={mode === "slider"} onClick={() => setMode("slider")}>Slider</button>
              </div>
              <div role="group" aria-label="Zoom">
                <button type="button" className="button small" data-zoom="fit" aria-pressed={zoom === "fit"} onClick={() => setZoom("fit")}>Fit</button>
                <button type="button" className="button small" data-zoom="actual" aria-pressed={zoom === "actual"} onClick={() => setZoom("actual")}
                  title="Show both pictures at their own size, and pan them together">100%</button>
              </div>
              <button type="button" className="button small" data-action="compare-swap" onClick={onSwap}>Swap sides</button>
              <Dialog.Close className="button small ghost icon-only" aria-label="Close"><CloseIcon /></Dialog.Close>
            </div>
          </div>

          {first && second && (mode === "side" ? (
            <div className="compare-sides">
              <Pane picture={first} zoom={zoom} caption="First picture" paneRef={(node) => { panes.current[0] = node; }} onScroll={follow(0)} />
              <Pane picture={second} zoom={zoom} caption="Second picture" paneRef={(node) => { panes.current[1] = node; }} onScroll={follow(1)} />
            </div>
          ) : (
            <>
              <SliderView first={first} second={second} zoom={zoom} />
              <p className="hint compare-slider-note">Left of the divider: {first.label}. Right of it: {second.label}.</p>
            </>
          ))}

          {/* Pick a winner: Keep is per run, so the button says which run it keeps; the other picture, its run and everything else stay as they are */}
          <div className="compare-keeps">
            {sides.map((picture, index) => (
              <button key={picture.id} type="button" className="button small" data-action="compare-keep" data-side={index === 0 ? "first" : "second"} aria-pressed={picture.pinned}
                disabled={picture.pinned} onClick={() => onKeep(picture.runId)}
                title={picture.pinned ? "This run is kept." : "Keep the run this picture is in: it will never be deleted automatically. Nothing else changes."}>
                <PinIcon /> {picture.pinned ? "Kept" : "Keep this one"} <span className="compare-keep-side">({index === 0 ? "first" : "second"})</span>
              </button>
            ))}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
