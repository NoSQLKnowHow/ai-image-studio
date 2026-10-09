// Make 4K on the page (DESIGN.md §27.3): which control a picture gets and the words around it. Whether Make 4K is
// offered at all is the server's rule (`can_4k` on the image), so nothing here repeats it: the page only follows.

import type { ImageInfo } from "./types";

/** What the tooltip says, and what it does not: it enlarges, it does not sharpen. */
export const MAKE_4K_TITLE =
  "Trims the picture to exactly 16:9 and enlarges it to 3840×2160 with a standard resize. It makes the picture bigger, not sharper: no detail is added.";

export type FourKControl =
  | { kind: "make"; label: string; title: string }
  | { kind: "making"; label: string; title: string }
  | { kind: "download"; label: string; title: string; href: string };

export const megabytes = (bytes: number): string => `${(bytes / 1_000_000).toFixed(1)} MB`;

/** The control for one result image, or null when it has none. A 4K copy that exists is always offered for download,
 *  whatever `can_4k` says now: the file is there. Otherwise Make 4K, while the server allows it, and "Making 4K…" while
 *  the request is under way. */
export function fourKControl(image: ImageInfo, making: boolean): FourKControl | null {
  const copy = image.four_k;
  if (copy) {
    return { kind: "download", label: "Download 4K", href: copy.download_url, title: `The 4K copy: ${copy.width}×${copy.height} PNG, ${megabytes(copy.bytes)}` };
  }
  if (!image.can_4k) return null;
  if (making) return { kind: "making", label: "Making 4K…", title: "Making the 4K copy. It takes a few seconds." };
  return { kind: "make", label: "Make 4K", title: MAKE_4K_TITLE };
}

/** What is said when the copy has been made. */
export function fourKMadeText(image: ImageInfo): string {
  const copy = image.four_k;
  return copy
    ? `The 4K copy is ready: ${copy.width}×${copy.height}, ${megabytes(copy.bytes)}. Use Download 4K.`
    : "The 4K copy is ready. Use Download 4K.";
}

/** What is said when it could not be made: the server's own words, after a short lead. */
export function fourKFailedText(status: number, message: string): string {
  if (status === 404) return "That picture no longer exists, so there is nothing to make 4K from.";
  return `Couldn't make 4K: ${message}`;
}
