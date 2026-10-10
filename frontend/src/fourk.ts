// Make 4K and Enlarge on the page (DESIGN.md §27.3, §27.9, §28): which controls a picture gets and the words around them. Whether
// either is offered at all, and what it would make, is the server's rule (`can_4k`, `four_k_size`, `can_enlarge`, `enlarge_size`
// on the picture, `upscaler` in the capabilities), so nothing here repeats it: the page only follows.

import type { EnlargeSize, FourKSize, FourKTarget, UpscalerStatus } from "./types";

export type FourKControl =
  | { kind: "make"; label: string; title: string }
  | { kind: "making"; label: string; title: string }
  | { kind: "download"; label: string; title: string; href: string };

export type EnlargeControl =
  | { kind: "enlarge"; label: string; title: string }
  | { kind: "waiting"; label: string; title: string } // asked for, but the GPU is busy with a picture: it starts by itself (DESIGN.md §28.3)
  | { kind: "enlarging"; label: string; title: string }
  | { kind: "unavailable"; label: string; title: string }; // offered, but the model is not installed: shown disabled, with the reason

export const megabytes = (bytes: number): string => `${(bytes / 1_000_000).toFixed(1)} MB`;

/** The tooltip of Make 4K: what it will make, and what it does not do (it enlarges, it does not sharpen). */
export function makeTitle(size: FourKSize | null): string {
  const frame = size && size.height > size.width ? "9:16" : "16:9";
  const what = !size
    ? "Make a 4K copy of this picture with a standard resize."
    : size.trimmed
      ? `Make a ${size.width}×${size.height} copy of this picture, trimmed to exactly ${frame}, with a standard resize.`
      : `Make a ${size.width}×${size.height} copy of this picture with a standard resize.`;
  return `${what} It makes the picture bigger, not sharper: no detail is added.`;
}

/** The control for one picture (a result or an edit's source), or null when it has none. A 4K copy that exists is always
 *  offered for download, whatever `can_4k` says now: the file is there. Otherwise Make 4K, while the server allows it, and
 *  "Making 4K…" while the request is under way. */
export function fourKControl(target: FourKTarget, making: boolean): FourKControl | null {
  const copy = target.four_k;
  if (copy) {
    const what = copy.method === "model" ? "The enlarged 4K copy" : "The 4K copy";
    const how = copy.method === "model" ? ", made with an upscaler model" : "";
    return { kind: "download", label: "Download 4K", href: copy.download_url, title: `${what}: ${copy.width}×${copy.height} PNG, ${megabytes(copy.bytes)}${how}` };
  }
  if (!target.can_4k) return null;
  if (making) return { kind: "making", label: "Making 4K…", title: "Making the 4K copy. It takes a few seconds." };
  return { kind: "make", label: "Make 4K", title: makeTitle(target.four_k_size) };
}

/** The tooltip of a waiting Enlarge: why, and that nothing has to be done about it. */
export const WAITING_TITLE = "Waiting for the picture that is being made to finish. Enlarge starts by itself, between pictures, and you can keep working.";

/** The tooltip of Enlarge: what it will make, and what it is (the model's guess at detail) and costs (time). */
export function enlargeTitle(size: EnlargeSize | null): string {
  const frame = size && size.height > size.width ? "9:16" : "16:9";
  const what = !size
    ? "Enlarge this picture to 4K with an upscaler model"
    : size.trimmed
      ? `Enlarge this picture to ${size.width}×${size.height} (trimmed to exactly ${frame}) with an upscaler model`
      : `Enlarge this picture to ${size.width}×${size.height} with an upscaler model`;
  return `${what}: the same picture, sharper than Make 4K, but it takes longer. The extra detail is the model's guess.`;
}

/** The Enlarge control for one picture, or null when it has none: the server does not offer it, or the picture already has an
 *  enlarged copy (the better one; Download 4K is then all there is). Without the model file it is shown disabled with the
 *  server's reason, so the person can see why. `upscaler` is null until the capabilities have arrived: then it is offered.
 *  `waiting` is the server's word that this picture's Enlarge is queued behind the studio's own work (it says so for a request from
 *  any page), and then it is "Waiting…" whether or not this page asked. */
export function enlargeControl(target: FourKTarget, enlarging: boolean, upscaler: UpscalerStatus | null, waiting = false): EnlargeControl | null {
  if (!target.can_enlarge || target.four_k?.method === "model") return null;
  if (waiting) return { kind: "waiting", label: "Waiting…", title: WAITING_TITLE };
  if (enlarging) return { kind: "enlarging", label: "Enlarging…", title: "Enlarging with the upscaler model. It can take a minute or more." };
  if (upscaler && !upscaler.available) {
    const why = [upscaler.reason, upscaler.hint].filter(Boolean).join(" ");
    return { kind: "unavailable", label: "Enlarge", title: why || "Enlarge is not available here." };
  }
  return { kind: "enlarge", label: "Enlarge", title: enlargeTitle(target.enlarge_size) };
}

/** What is said when the copy has been enlarged, and when it could not be: the server's own words and its hint, after a short lead. */
export function enlargedText(target: FourKTarget): string {
  const copy = target.four_k;
  return copy ? `Enlarged to ${copy.width}×${copy.height}, ${megabytes(copy.bytes)}. Use Download 4K.` : "The enlarged copy is ready. Use Download 4K.";
}

export function enlargeFailedText(status: number, message: string, hint: string | null): string {
  if (status === 404) return "That picture no longer exists, so there is nothing to enlarge.";
  return `Couldn't enlarge: ${message}${hint ? ` ${hint}` : ""}`;
}

/** What is said when the copy has been made. */
export function fourKMadeText(target: FourKTarget): string {
  const copy = target.four_k;
  return copy
    ? `The 4K copy is ready: ${copy.width}×${copy.height}, ${megabytes(copy.bytes)}. Use Download 4K.`
    : "The 4K copy is ready. Use Download 4K.";
}

/** What is said when it could not be made: the server's own words, after a short lead. */
export function fourKFailedText(status: number, message: string): string {
  if (status === 404) return "That picture no longer exists, so there is nothing to make 4K from.";
  return `Couldn't make 4K: ${message}`;
}

// ------------------------------------------------------------------ a picture from the computer (DESIGN.md §27.9)
/** The file name in a `Content-Disposition` header: the UTF-8 form (`filename*=UTF-8''…`) if there is one, else the plain one. */
export function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const utf8 = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      /* a malformed escape: fall back to the plain name */
    }
  }
  const plain = /filename\s*=\s*"([^"]+)"/i.exec(header) ?? /filename\s*=\s*([^;]+)/i.exec(header);
  return plain ? plain[1].trim() : null;
}

/** What is said when a picture has been upscaled, and when it could not be. */
export function upscaledText(name: string, picture: { width: number; height: number; blob: { size: number } }): string {
  return `Upscaled ${name} to ${picture.width}×${picture.height} (${megabytes(picture.blob.size)}). Your download has started.`;
}

export function upscaleFailedText(name: string, message: string): string {
  return `Couldn't upscale ${name}: ${message}`;
}

/** Save a blob under a name, as a click on a download link would. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000); // after the browser has taken it
}
