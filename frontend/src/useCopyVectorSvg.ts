import { useRef } from "react";
import { api } from "./api";
import { copyText } from "./hooks";
import type { VectorItem } from "./types";

// Copy SVG code (DESIGN.md §31.12.8, §35.2): the file is fetched as TEXT and copied, never put in the page as markup, so a file that came from a model
// never runs as part of the page (§31.6 "Safety"). It is a hook of its own, not part of the page's component, so that the three things that matter can be
// tested without the whole page: the text is only on the clipboard, one request at a time is made for a version however often the button is pressed, and
// what happened is told in words that say what to do instead.
//
// `tell` says how it went. The page chooses where: from a card the answer is a toast; from the viewer it is a note inside the viewer, because the page
// behind a modal, toasts included, is hidden from screen readers (§24.2). The latest `tell` is always the one used, even for a request already under way.

export type CopyTell = (kind: "success" | "error", text: string) => void;

export function useCopyVectorSvg(tell: CopyTell): (item: VectorItem) => Promise<void> {
  const copying = useRef(new Set<string>()); // versions whose SVG is being fetched to copy: one request at a time for each
  const latest = useRef(tell);
  latest.current = tell;
  return async (item) => {
    if (copying.current.has(item.id)) return;
    copying.current.add(item.id);
    try {
      const text = await api.fetchText(item.url);
      const ok = await copyText(text);
      latest.current(ok ? "success" : "error", ok ? "SVG code copied." : "Couldn't copy. Use Download SVG instead.");
    } catch (error) {
      latest.current("error", `Couldn't get the SVG: ${(error as Error).message}`);
    } finally {
      copying.current.delete(item.id);
    }
  };
}
