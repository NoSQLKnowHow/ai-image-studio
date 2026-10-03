// The tray's state and its uploads (DESIGN.md §21.4). The rules about the list itself are in tray.ts; this holds the
// list, starts an upload as soon as a file is added (so Generate is quick), and tidies up after itself: an image taken
// out of the tray while it is still going up is stopped, and one that had already been staged is deleted.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, uploadImage } from "./api";
import { addItems, moveBy as moveByInList, moveItem, positionOf, removeItem, room, type KnownImage, type TrayItem } from "./tray";
import type { Capabilities, Run } from "./types";

export interface TrayNote {
  id: number;
  text: string;
}

export interface AddOutcome {
  added: number;
  skipped: number;
  keys: string[]; // the keys of the images that were added, in order
}

export function useTray(caps: Capabilities | null) {
  const cap = caps?.limits.input_images.max ?? 0;
  const uploadLimitMb = caps?.limits.upload_mb ?? 20;
  const [items, setItems] = useState<TrayItem[]>([]);
  const [shapeKey, setShapeKey] = useState<string | null>(null);
  const [notes, setNotes] = useState<TrayNote[]>([]);
  const [announcement, setAnnouncement] = useState("");
  const latest = useRef<TrayItem[]>([]); // the list as of now, for callbacks that run later
  const aborts = useRef(new Map<string, () => void>()); // uploads under way, by item key
  const counter = useRef(0);
  const noteId = useRef(0);

  const commit = useCallback((next: TrayItem[]) => {
    latest.current = next;
    setItems(next);
  }, []);
  const patch = useCallback((key: string, change: Partial<TrayItem>) => {
    commit(latest.current.map((item) => (item.key === key ? { ...item, ...change } : item)));
  }, [commit]);
  const note = useCallback((text: string) => setNotes((all) => [...all, { id: ++noteId.current, text }]), []);
  const dismissNote = useCallback((id: number) => setNotes((all) => all.filter((n) => n.id !== id)), []);
  const newKey = () => `tray-${++counter.current}`;

  /** Let go of what an item holds: stop its upload, or delete the staged file nobody has claimed, and free its preview. */
  const release = useCallback((item: TrayItem) => {
    aborts.current.get(item.key)?.();
    aborts.current.delete(item.key);
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    if (item.state === "ready" && item.ref && "upload_id" in item.ref) void api.deleteUpload(item.ref.upload_id).catch(() => undefined);
  }, []);

  const startUpload = useCallback((key: string, file: File) => {
    const { promise, abort } = uploadImage(file, (progress) => patch(key, { progress }));
    aborts.current.set(key, abort);
    promise.then(
      (result) => {
        aborts.current.delete(key);
        const item = latest.current.find((i) => i.key === key);
        if (!item) {
          void api.deleteUpload(result.upload_id).catch(() => undefined); // it was taken out while going up
          return;
        }
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
        patch(key, {
          state: "ready", progress: 1, ref: { upload_id: result.upload_id }, thumbUrl: result.thumb_url ?? result.url,
          previewUrl: null, width: result.width, height: result.height, hasAlpha: result.has_alpha,
        });
      },
      (error: unknown) => {
        aborts.current.delete(key);
        if (error instanceof DOMException && error.name === "AbortError") return;
        const item = latest.current.find((i) => i.key === key);
        if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
        commit(removeItem(latest.current, key));
        note(`${file.name || "A file"}: ${error instanceof Error ? error.message : "it could not be uploaded."}`); // rejected on its own
      },
    );
  }, [commit, note, patch]);

  /** Add files from the picker, a drop or a paste. Each is checked on its own; the first that fit are kept. */
  const addFiles = useCallback((files: File[]): AddOutcome => {
    const limit = uploadLimitMb * 1024 * 1024;
    const usable = files.filter((file) => {
      if (file.size > limit) {
        note(`${file.name || "A file"}: the file is larger than ${uploadLimitMb} MB.`);
        return false;
      }
      return true;
    });
    const candidates = usable.map<TrayItem>((file) => ({
      key: newKey(), name: file.name || "pasted image", state: "uploading", progress: 0, ref: null, thumbUrl: null,
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : null, width: null, height: null, hasAlpha: false, missing: false,
    }));
    const { items: next, skipped } = addItems(latest.current, candidates, cap);
    const accepted = next.slice(latest.current.length);
    candidates.slice(accepted.length).forEach((item) => item.previewUrl && URL.revokeObjectURL(item.previewUrl)); // did not fit
    commit(next);
    accepted.forEach((item, i) => startUpload(item.key, usable[i]));
    if (skipped) note(`${skipped} ${skipped === 1 ? "image" : "images"} did not fit: one edit takes at most ${cap}.`);
    if (accepted.length) setAnnouncement(accepted.length === 1 ? `Image ${next.length} added.` : `${accepted.length} images added.`);
    return { added: accepted.length, skipped, keys: accepted.map((item) => item.key) };
  }, [cap, commit, note, startUpload, uploadLimitMb]);

  /** Add images the server already has (Edit this on a result). */
  const addKnown = useCallback((images: KnownImage[]): AddOutcome => {
    const candidates = images.map<TrayItem>((image) => ({
      key: newKey(), name: image.name, state: "ready", progress: 1, ref: { image_id: image.imageId }, thumbUrl: image.thumbUrl,
      previewUrl: null, width: image.width, height: image.height, hasAlpha: image.hasAlpha, missing: false,
    }));
    const { items: next, skipped } = addItems(latest.current, candidates, cap);
    commit(next);
    const added = images.length - skipped;
    if (added) setAnnouncement(added === 1 ? `Image ${next.length} added.` : `${added} images added.`);
    return { added, skipped, keys: candidates.slice(0, added).map((item) => item.key) };
  }, [cap, commit]);

  /** Replace the whole tray (Reuse): what it held is let go first. */
  const replaceWith = useCallback((images: KnownImage[]): AddOutcome => {
    latest.current.forEach(release);
    commit([]);
    setShapeKey(null);
    return addKnown(images);
  }, [addKnown, commit, release]);

  const remove = useCallback((key: string) => {
    const item = latest.current.find((i) => i.key === key);
    if (!item) return;
    const position = positionOf(latest.current, key);
    release(item);
    commit(removeItem(latest.current, key));
    setShapeKey((current) => (current === key ? null : current));
    setAnnouncement(`Image ${position} removed.`);
  }, [commit, release]);

  const moveTo = useCallback((key: string, toIndex: number) => {
    const next = moveItem(latest.current, key, toIndex);
    commit(next);
    setAnnouncement(`Moved to position ${positionOf(next, key)}.`);
  }, [commit]);

  const moveBy = useCallback((key: string, delta: number) => {
    const next = moveByInList(latest.current, key, delta);
    commit(next);
    setAnnouncement(`Moved to position ${positionOf(next, key)}.`);
  }, [commit]);

  const markMissing = useCallback((key: string) => patch(key, { missing: true }), [patch]);

  /** After an edit was sent the server owns copies of the images (and has used up the staged uploads), so the tray
   *  now points at those copies and stays usable for the next try. Each tile keeps its place and its key. */
  const adoptRun = useCallback((run: Run) => {
    if (run.inputs.length !== latest.current.length) return;
    commit(latest.current.map((item, i) => {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      const input = run.inputs[i];
      return { ...item, state: "ready", progress: 1, ref: { image_id: input.id }, thumbUrl: input.thumb_url ?? input.url, previewUrl: null,
        width: input.width, height: input.height, hasAlpha: input.has_alpha, missing: false };
    }));
  }, [commit]);

  // Leaving the page: stop what is still going up and free the previews.
  useEffect(() => () => {
    latest.current.forEach((item) => {
      aborts.current.get(item.key)?.();
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
  }, []);

  return {
    items, cap, shapeKey, setShapeKey, notes, announcement, room: room(items, cap),
    addFiles, addKnown, replaceWith, remove, moveTo, moveBy, markMissing, adoptRun, dismissNote,
  };
}
