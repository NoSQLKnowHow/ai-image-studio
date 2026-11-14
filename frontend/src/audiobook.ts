import { audioSizeText, lengthText } from "./speech";
import type { AudiobookCaps, AudiobookStatus } from "./speechTypes";

// The words of the audiobook button (DESIGN.md §44.4): what it offers, how far a build has got, and what the finished file is. No React in it, so each sentence can be
// tested on its own. The server decides what can be made and says why when it cannot; these functions only turn its answer into the words a person reads.

const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

/** "3 of 12 parts so far" for a reading that is still being made, or null when every part is finished (there is then nothing to add). */
export function partsSoFar(status: Pick<AudiobookStatus, "parts_made" | "parts_total" | "complete"> | null): string | null {
  if (!status || status.complete || status.parts_made === 0) return null;
  return `${status.parts_made} of ${plural(status.parts_total, "part")} so far`;
}

/** The share of the samples fed in, as a whole percent: never 100 until the file is ready (the build is not finished when the last sample has gone in). */
export function percentText(progress: number | null): string {
  const share = Math.min(99, Math.max(0, Math.floor((progress ?? 0) * 100)));
  return `${share}%`;
}

/** What the button says while nothing is happening: "Make audiobook (M4B)", and for a reading still being made which parts it will hold. */
export function makeLabel(status: Pick<AudiobookStatus, "parts_made" | "parts_total" | "complete"> | null): string {
  const so = partsSoFar(status);
  return so ? `Make audiobook (M4B) of ${so}` : "Make audiobook (M4B)";
}

/** The sentence under the button while a file is being made: "Making the audiobook… 37%, part 3 of 12." or that it waits its turn. */
export function buildingText(status: Pick<AudiobookStatus, "state" | "progress" | "part" | "parts_made">): string {
  if (status.state === "waiting") return "Waiting for another audiobook to be made first…";
  const where = status.part ? `, part ${status.part} of ${status.parts_made}` : "";
  return `Making the audiobook… ${percentText(status.progress)}${where}.`;
}

/** The link to the finished file: "Download audiobook (M4B, 312 MB)". */
export function downloadLabel(status: Pick<AudiobookStatus, "bytes">): string {
  return status.bytes ? `Download audiobook (M4B, ${audioSizeText(status.bytes)})` : "Download audiobook (M4B)";
}

/** What the file holds, said once it is ready: its length and how many chapters (one for each part), and that the parts are what is in it so far when the reading is not finished. */
export function holdsText(status: Pick<AudiobookStatus, "seconds" | "chapters" | "parts_made" | "parts_total" | "complete">): string {
  const so = partsSoFar(status);
  return `${lengthText(status.seconds)}, ${plural(status.chapters, "chapter")}, one for each part${so ? ` (${so})` : ""}.`;
}

/** Why the button is off, in words, with the hint, or null when it is on. The reason comes from the server, which looked at ffmpeg; the capability says it before a press and the
 *  status says it after one. */
export function unavailableText(caps: Pick<AudiobookCaps, "available" | "reason" | "hint"> | undefined | null): string | null {
  if (!caps || caps.available) return null;
  return [caps.reason ?? "The audiobook file cannot be made on this server.", caps.hint].filter(Boolean).join(" ");
}
