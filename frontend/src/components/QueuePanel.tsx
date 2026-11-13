import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { ApiError, api } from "../api";
import { useNarrow } from "../hooks";
import { applyMove, canStep, kindWords, moveForSlot, movedWords, slotAt, stepMove } from "../queue";
import type { QueueItem, QueueListing, QueueMove, Status } from "../types";
import { ChevronDownIcon, ChevronUpIcon, GripIcon, LockIcon, QueueIcon } from "./icons";

// The Queue (DESIGN.md section 40): one panel, opened from the header, that shows the job that is going and every job that is waiting, from every tab, in the order they will
// be worked on, and lets a person change that order. The queue is one line for all of them, since one model is in memory at a time, so it is seen as one line.
//
// A waiting job that has never started is moved by dragging its grip, or by the arrows beside it. A job that has started (the one that is going, and a reading that has made a
// part and waits for its next turn) shows a lock and no controls; other jobs can still be put before or after it.

/** What the panel says of the queue, and what it does when a job is moved: the listing as the server gave it, a move shown at once and then replaced by the server's answer,
 *  and the words of a refusal. It asks again whenever `signature` changes: the page is told by the server's events when the order, the count or the job that is going changes
 *  (`status.queue`), and a listing of waiting jobs from other tabs, with their words, is the server's to give. */
function useQueue(signature: string) {
  const [listing, setListing] = useState<QueueListing | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const turn = useRef(0); // every ask and every move is a turn; the answer to one that a later turn has overtaken is not shown, since it is older than what is on the screen

  const refresh = useCallback(async () => {
    const mine = ++turn.current;
    try {
      const next = await api.queue();
      if (mine === turn.current) setListing(next);
    } catch (error) {
      if (mine === turn.current) setProblem(error instanceof ApiError ? error.message : "Could not read the queue.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [signature, refresh]);

  const move = useCallback(async (request: QueueMove) => {
    const mine = ++turn.current;
    setProblem(null);
    setListing((current) => (current ? { ...current, queued: applyMove(current.queued, request) } : current)); // at once; the server's answer replaces it
    try {
      const answer = await api.moveInQueue(request);
      setSaid(movedWords(answer.queued, request.id) ?? "");
      if (mine === turn.current) setListing(answer);
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "Could not move the job.");
      if (mine === turn.current) void refresh(); // put back what the server has, which is not what was just shown
    }
  }, [refresh]);

  return { listing, problem, said, move };
}

/** Dragging a job by its grip. It listens on the window while a drag is under way, so that it follows the pointer wherever it goes, and works the same for a mouse, a pen and a
 *  finger (the grip has `touch-action: none`, so a finger drags it instead of scrolling). Escape, or the pointer being taken away, puts the job back. `target` is the place
 *  the job would take if dropped now (for the line that shows it), or null where that is where it already is. */
function useRowDrag(list: RefObject<HTMLOListElement | null>, items: readonly QueueItem[], onMove: (request: QueueMove) => void) {
  const [drag, setDrag] = useState<{ id: string; slot: number } | null>(null);
  const latest = useRef({ items, onMove });
  latest.current = { items, onMove };
  const dragging = drag?.id ?? null;

  const start = (id: string) => (event: ReactPointerEvent) => {
    if (event.button !== 0) return; // the main button only (a finger or a pen touching reports button 0 too)
    event.preventDefault();
    setDrag({ id, slot: items.findIndex((item) => item.id === id) });
  };

  useEffect(() => {
    if (!dragging) return;
    const slotFor = (y: number) => slotAt(Array.from(list.current?.querySelectorAll("li[data-row]") ?? [], (row) => row.getBoundingClientRect()), y);
    const onPointerMove = (event: PointerEvent) => setDrag((current) => (current ? { ...current, slot: slotFor(event.clientY) } : current));
    const onPointerUp = (event: PointerEvent) => {
      const { items: now, onMove: send } = latest.current;
      const from = now.findIndex((item) => item.id === dragging); // by id: the queue may have changed under the pointer, and the job may be gone
      setDrag(null);
      const request = from < 0 ? null : moveForSlot(now, from, slotFor(event.clientY));
      if (request) send(request);
    };
    const cancel = () => setDrag(null);
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation(); // this Escape ends the drag; it does not also close the panel
      setDrag(null);
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [dragging, list]);

  const from = drag ? items.findIndex((item) => item.id === drag.id) : -1;
  const target = drag && from >= 0 && moveForSlot(items, from, drag.slot) ? drag.slot : null;
  return { start, dragging, target };
}

function QueuePanel({ signature }: { signature: string }) {
  const { listing, problem, said, move } = useQueue(signature);
  const list = useRef<HTMLOListElement>(null);
  const items = listing?.queued ?? [];
  const drag = useRowDrag(list, items, (request) => void move(request));
  const focusAfter = useRef<string | null>(null);

  // A job moved by an arrow keeps the person's place: the row is moved in the page, which can drop focus, so it is put back, on the same arrow or, if that arrow has nowhere left
  // to go (the job is now first or last), on the other one.
  useLayoutEffect(() => {
    const wanted = focusAfter.current;
    if (!wanted) return;
    focusAfter.current = null;
    const [id, way] = wanted.split(":");
    const row = list.current?.querySelector(`li[data-row="${id}"]`);
    const same = row?.querySelector<HTMLButtonElement>(`button[data-step="${way}"]`);
    const other = row?.querySelector<HTMLButtonElement>(`button[data-step="${way === "up" ? "down" : "up"}"]`);
    (same && !same.disabled ? same : other)?.focus();
  }, [items]);

  const step = (index: number, direction: -1 | 1) => {
    const request = stepMove(items, index, direction);
    if (!request) return;
    focusAfter.current = `${request.id}:${direction < 0 ? "up" : "down"}`;
    void move(request);
  };

  const running = listing?.running ?? null;
  return (
    <div id="queue-details" className="pill-panel queue-panel" role="region" aria-label="Queue">
      <p className="pill-panel-title">Queue</p>
      {running && (
        <div className="queue-now">
          <span className="queue-grip queue-lock" title="This job is going, so it cannot be moved"><LockIcon /></span>
          <span className="queue-kind">{kindWords(running.kind)}</span>
          <span className="queue-title" title={running.title}>{running.title}</span>
          <span className="badge badge-running">Now</span>
        </div>
      )}
      {listing && items.length === 0 && <p className="hint">{running ? "Nothing is waiting behind it." : "Nothing is going and nothing is waiting."}</p>}
      {!listing && !problem && <p className="hint">Reading the queue…</p>}
      {items.length > 0 && (
        <>
          <ol ref={list} className={`queue-list${drag.dragging ? " is-dragging" : ""}`} aria-label="Waiting jobs">
            {items.map((item, index) => {
              const line = drag.target === index ? " drop-before" : drag.target === items.length && index === items.length - 1 ? " drop-after" : "";
              return (
                <li key={item.id} data-row={item.id} className={`queue-row${drag.dragging === item.id ? " is-dragging" : ""}${line}`}>
                  {item.movable ? (
                    <span className="queue-grip" aria-hidden="true" title="Drag to move" onPointerDown={drag.start(item.id)}><GripIcon /></span>
                  ) : (
                    <span className="queue-grip queue-lock" title="This job has started, so it can no longer be moved">
                      <LockIcon /><span className="sr-only">Started: it can no longer be moved.</span>
                    </span>
                  )}
                  <span className="queue-place">{item.position}</span>
                  <span className="queue-kind">{kindWords(item.kind)}</span>
                  <span className="queue-title" title={item.title}>{item.title}</span>
                  {item.draft && <span className="badge queue-draft">Draft</span>}
                  {item.movable && (
                    <span className="queue-steps">
                      <button type="button" className="queue-step" data-step="up" aria-label={`Move “${item.title}” up`} disabled={!canStep(items, index, -1)}
                        onClick={() => step(index, -1)}><ChevronUpIcon /></button>
                      <button type="button" className="queue-step" data-step="down" aria-label={`Move “${item.title}” down`} disabled={!canStep(items, index, 1)}
                        onClick={() => step(index, 1)}><ChevronDownIcon /></button>
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
          <p className="hint">Drag the grip, or use the arrows, to change the order. A job that has started cannot be moved.</p>
        </>
      )}
      {problem && <p className="form-error" role="alert">{problem}</p>}
      <p className="sr-only" role="status">{said}</p>
    </div>
  );
}

/** The Queue button beside the model pill, and the panel it opens (DESIGN.md section 40.4). It is there while something is going or waiting, and while it is open. */
export function QueueMenu({ status }: { status: Status | null }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const narrow = useNarrow();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onPointer = (e: PointerEvent) => wrap.current && !wrap.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const queue = status?.queue;
  const waiting = queue?.queued ?? 0;
  if (!queue || (!queue.running && waiting === 0 && !open)) return null;
  // what tells the panel to look again: the job that is going, and the waiting jobs in order (their count where an older server does not send the order)
  const signature = `${queue.running ?? ""}|${queue.order ? queue.order.join(",") : waiting}`;
  return (
    <div className="queue-wrap" ref={wrap}>
      <button type="button" className="button queue-button" aria-expanded={open} aria-controls="queue-details"
        aria-label={`Queue: ${waiting === 0 ? "nothing waiting" : `${waiting} waiting`}`} onClick={() => setOpen((o) => !o)}>
        <QueueIcon />
        {!narrow && <span className="queue-button-label">Queue</span>}
        {waiting > 0 && <span className="queue-count" aria-hidden="true">{waiting}</span>}
      </button>
      {open && <QueuePanel signature={signature} />}
    </div>
  );
}
