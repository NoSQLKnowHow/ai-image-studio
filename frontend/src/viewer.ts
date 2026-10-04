// What the viewer pages through (DESIGN.md §21.4): an edit's source images, in order, and then its results, so the
// "image 2" of the prompt can be matched to a picture. A Generate run has results only.

import type { KnownImage } from "./tray";
import type { ImageInfo, ImageRun, RunInput } from "./types";

export type ViewerItem =
  | { kind: "source"; number: number; of: number; input: RunInput }
  | { kind: "result"; number: number; of: number; image: ImageInfo };

export function viewerItems(run: ImageRun): ViewerItem[] {
  const inputs = [...run.inputs].sort((a, b) => a.position - b.position);
  return [
    ...inputs.map<ViewerItem>((input, i) => ({ kind: "source", number: i + 1, of: inputs.length, input })),
    ...run.images.map<ViewerItem>((image, i) => ({ kind: "result", number: i + 1, of: run.images.length, image })),
  ];
}

/** The viewer's title. A Generate run's results read "Image 2 of 3" as they always have; in an edit, which has sources
 *  too, they read "Result 1 of 2" so the two cannot be confused. */
export function viewerTitle(item: ViewerItem, edit: boolean): string {
  if (item.kind === "source") return `Source ${item.number} of ${item.of} · ${item.input.width}×${item.input.height}`;
  const { image } = item;
  const lead = edit ? `Result ${item.number} of ${item.of} · ` : item.of > 1 ? `Image ${item.number} of ${item.of} · ` : "";
  return `${lead}seed ${image.seed} · ${image.width}×${image.height}`;
}

/** The picture being viewed, as the tray takes it ("Edit this"). */
export function viewerKnown(run: ImageRun, item: ViewerItem): KnownImage {
  const label = run.prompt.length > 40 ? `${run.prompt.slice(0, 40)}…` : run.prompt;
  if (item.kind === "source") {
    const { input } = item;
    return { imageId: input.id, name: `Source ${item.number}: ${label}`, thumbUrl: input.thumb_url ?? input.url, width: input.width, height: input.height, hasAlpha: input.has_alpha };
  }
  const { image } = item;
  return { imageId: image.id, name: `${label} (${image.seed})`, thumbUrl: image.thumb_url ?? image.url, width: image.width, height: image.height, hasAlpha: image.has_alpha };
}
