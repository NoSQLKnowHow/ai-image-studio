import { fourKControl } from "../fourk";
import type { ImageInfo } from "../types";
import { DownloadIcon, UpscaleIcon } from "./icons";

/** Make 4K, then Download 4K, for one result image (DESIGN.md §27.3). Nothing at all when the server does not offer it.
 *  The same control sits on a card (`ghost`) and in the viewer's bar. */
export function FourKButton({ image, making, className, onMake }: {
  image: ImageInfo;
  making: boolean;
  className: string;
  onMake: () => void;
}) {
  const control = fourKControl(image, making);
  if (!control) return null;
  if (control.kind === "download") {
    return (
      <a className={className} href={control.href} download data-action="download-4k" title={control.title}>
        <DownloadIcon /> {control.label}
      </a>
    );
  }
  return (
    <button type="button" className={className} data-action="make-4k" title={control.title}
      aria-disabled={control.kind === "making" || undefined} onClick={() => control.kind === "make" && onMake()}>
      <UpscaleIcon /> {control.label}
    </button>
  );
}
