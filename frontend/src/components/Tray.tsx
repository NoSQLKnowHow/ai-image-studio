import { useEffect, useRef, useState } from "react";
import type { TrayItem } from "../tray";
import type { TrayNote } from "../useTray";
import { ChevronLeft, ChevronRight, CloseIcon, GripIcon, PlusIcon } from "./icons";

/** The drag type of a tile being reordered, so a file dropped from the desktop is told apart from one. */
const TRAY_TYPE = "application/x-studio-tray-item";

interface Props {
  items: TrayItem[];
  cap: number;
  notes: TrayNote[];
  announcement: string;
  showShape: boolean; // Size is Auto, so the result's shape follows one of the images
  followed: number; // the 1-based place of the image the result follows
  heavy: string | null; // the warning to show when this edit is expensive, else null
  onAdd: (files: File[]) => void;
  onRemove: (key: string) => void;
  onMoveBy: (key: string, delta: number) => void;
  onMoveTo: (key: string, index: number) => void;
  onInsert: (position: number) => void;
  onShape: (key: string) => void;
  onMissing: (key: string) => void;
  onDismissNote: (id: number) => void;
}

/** The reference tray of Edit mode (DESIGN.md §21.4): the images in the order the model sees them, each with a
 *  number badge that puts "image N" into the prompt. */
export function Tray({ items, cap, notes, announcement, showShape, followed, heavy, onAdd, onRemove, onMoveBy, onMoveTo, onInsert, onShape, onMissing, onDismissNote }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const refocus = useRef<{ key: string; action: "earlier" | "later" } | null>(null);
  const full = items.length >= cap;

  // Keep a keyboard user on the tile they moved: reordering moves its nodes, which can drop focus.
  useEffect(() => {
    const wanted = refocus.current;
    if (!wanted) return;
    refocus.current = null;
    const tile = document.querySelector<HTMLElement>(`.tray-tile[data-key="${wanted.key}"]`);
    const button = tile?.querySelector<HTMLButtonElement>(`button[data-action="move-${wanted.action}"]:not(:disabled)`);
    (button ?? tile?.querySelector<HTMLButtonElement>('button[data-action="insert"]'))?.focus({ preventScroll: true });
  }, [items]);

  return (
    <section className="tray" aria-label="Images to edit">
      <p className="tray-help">
        The order is what the model sees: “image 1” is the first. Click a number to put it in your prompt, and drag a picture
        (or use its arrows) to change the order.
      </p>
      <ul className="tray-grid" aria-label="Images to edit, in order">
        {items.map((item, i) => {
          const n = i + 1;
          const picture = item.state === "ready" ? item.thumbUrl : item.previewUrl; // the server's thumbnail, or a local preview while uploading
          return (
            <li key={item.key} className={`tray-tile${item.hasAlpha ? " checker" : ""}${dragging === item.key ? " dragging" : ""}${over === item.key ? " over" : ""}`}
              data-key={item.key} aria-label={`Image ${n}: ${item.name}${item.state === "uploading" ? ", uploading" : ""}${item.missing ? ", missing" : ""}`}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(TRAY_TYPE, item.key);
                e.dataTransfer.effectAllowed = "move";
                setDragging(item.key);
              }}
              onDragEnd={() => {
                setDragging(null);
                setOver(null);
              }}
              onDragOver={(e) => {
                if (!e.dataTransfer.types.includes(TRAY_TYPE)) return; // files are handled by the prompt card
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                setOver(item.key);
              }}
              onDragLeave={() => setOver((current) => (current === item.key ? null : current))}
              onDrop={(e) => {
                const key = e.dataTransfer.getData(TRAY_TYPE);
                if (!key) return;
                e.preventDefault();
                e.stopPropagation();
                setDragging(null);
                setOver(null);
                if (key !== item.key) onMoveTo(key, i);
              }}>
              {picture && !item.missing ? (
                <img src={picture} alt="" draggable={false} onError={() => item.state === "ready" && onMissing(item.key)} />
              ) : (
                <span className="tray-gap" aria-hidden="true">{item.missing ? "Missing" : "…"}</span>
              )}
              <span className="tray-grip" aria-hidden="true"><GripIcon /></span>
              <button type="button" className="tray-badge" data-action="insert" onClick={() => onInsert(n)}
                aria-label={`Image ${n}: put “image ${n}” in the prompt`} title={`Put “image ${n}” in the prompt`}>
                {n}
              </button>
              <button type="button" className="tray-remove" data-action="remove" onClick={() => onRemove(item.key)}
                aria-label={`Remove image ${n}`} title="Remove">
                <CloseIcon />
              </button>
              <div className="tray-move">
                <button type="button" data-action="move-earlier" disabled={i === 0} aria-label={`Move image ${n} earlier`} title="Move earlier"
                  onClick={() => {
                    refocus.current = { key: item.key, action: "earlier" };
                    onMoveBy(item.key, -1);
                  }}>
                  <ChevronLeft />
                </button>
                <button type="button" data-action="move-later" disabled={i === items.length - 1} aria-label={`Move image ${n} later`} title="Move later"
                  onClick={() => {
                    refocus.current = { key: item.key, action: "later" };
                    onMoveBy(item.key, 1);
                  }}>
                  <ChevronRight />
                </button>
              </div>
              {item.state === "uploading" && (
                <div className="tray-progress" role="progressbar" aria-label={`Uploading ${item.name}`} aria-valuemin={0} aria-valuemax={100}
                  aria-valuenow={Math.round(item.progress * 100)}>
                  <span style={{ width: `${Math.round(item.progress * 100)}%` }} />
                </div>
              )}
            </li>
          );
        })}
        <li className="tray-add-cell">
          <button type="button" className="tray-add" disabled={full} onClick={() => fileInput.current?.click()}
            aria-label={`Add images (${items.length} of ${cap})`}
            title={full ? `One edit takes at most ${cap} images.` : "Choose pictures to add, or drop or paste them here"}>
            <PlusIcon />
            <span>Add images</span>
            <small>{items.length} of {cap}</small>
          </button>
          <input ref={fileInput} type="file" accept="image/*" multiple hidden aria-hidden="true" tabIndex={-1} data-testid="tray-file-input"
            onChange={(e) => {
              onAdd(Array.from(e.target.files ?? []));
              e.target.value = ""; // the same file can be chosen again
            }} />
        </li>
      </ul>

      {showShape && items.length > 0 && (
        <div className="tray-shape">
          <label>
            Result follows image{" "}
            <select value={followed} onChange={(e) => onShape(items[Number(e.target.value) - 1].key)}>
              {items.map((item, i) => <option key={item.key} value={i + 1}>{i + 1}</option>)}
            </select>
          </label>
          <span className="field-hint">Size is Auto. Pick a size in Options to set it yourself.</span>
        </div>
      )}

      {heavy && <p className="tray-heavy" role="status">{heavy}</p>}

      {notes.length > 0 && (
        <ul className="tray-notes" aria-label="Problems with the files you added">
          {notes.map((note) => (
            <li key={note.id}>
              <span>{note.text}</span>
              <button type="button" className="button ghost small icon-only" aria-label="Dismiss this message" onClick={() => onDismissNote(note.id)}><CloseIcon /></button>
            </li>
          ))}
        </ul>
      )}

      <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
    </section>
  );
}
