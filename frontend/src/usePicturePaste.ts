import { useEffect, useRef } from "react";
import { hasPlainText, pastedPictures, routePaste } from "./paste";

// The one listener for pasted pictures on the page (DESIGN.md §36.2, §36.4). The prompt box used to have its own; one listener for the whole page means one
// paste acts once (§36.3 item 2). It reads the page (what has the focus, which tab shows, whether a dialog is open) and hands the rest to `routePaste`.
//
// A paste that is not ours is left alone, so text goes where the browser puts it. A paste that is ours is cancelled (`preventDefault`) first, so the browser
// does not also do something with it, and then handed to the page.

export interface PasteHandlers {
  tab: "images" | "music" | "vector" | "builder" | "speech";
  ready: boolean; // the page knows what the server can do
  onTray: (files: File[]) => void; // pictures for the Images tab's prompt card (Edit's tray; Generate switches to Edit)
  onUpscale: (files: File[]) => void; // pictures for Upscale a picture…, which has the focus
  onBuilder?: (files: File[]) => void; // pictures for the Prompt builder's chat
  onWrongTab: () => void; // a picture was given to the Music, Vector or Speech tab
}

/** A dialog (the questions, the Options drawer, the viewers) is open: they are all Radix dialogs, which carry these roles. */
const modalIsOpen = (): boolean => document.querySelector('[role="dialog"], [role="alertdialog"]') !== null;

export function usePicturePaste(handlers: PasteHandlers): void {
  // the newest handlers, so that the listener is added once and always acts on the page as it is now
  const latest = useRef(handlers);
  latest.current = handlers;

  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      if (event.defaultPrevented) return; // something has already taken this paste
      const now = latest.current;
      const route = routePaste({
        files: pastedPictures(event.clipboardData), plainText: hasPlainText(event.clipboardData),
        target: event.target instanceof Element ? event.target : null, tab: now.tab, modalOpen: modalIsOpen(), ready: now.ready,
      });
      if (route.kind === "ignore") return;
      event.preventDefault();
      if (route.kind === "tray") now.onTray(route.files);
      else if (route.kind === "upscale") now.onUpscale(route.files);
      else if (route.kind === "builder") now.onBuilder?.(route.files);
      else now.onWrongTab();
    };
    // A picture dropped on the Music or Vector tab: the window already stops the browser from opening it; this says where pictures are used.
    // The Speech tab says nothing: a file dropped there is meant for its own file box (a .txt, .epub or .docx), which answers for itself, and a picture dropped
    // elsewhere on that tab is simply not taken.
    const onDrop = (event: DragEvent) => {
      const now = latest.current;
      if (now.tab === "images" || now.tab === "builder" || now.tab === "speech" || !now.ready || modalIsOpen()) return;
      if (event.dataTransfer?.types.includes("Files")) now.onWrongTab();
    };
    document.addEventListener("paste", onPaste);
    window.addEventListener("drop", onDrop);
    return () => {
      document.removeEventListener("paste", onPaste);
      window.removeEventListener("drop", onDrop);
    };
  }, []);
}
