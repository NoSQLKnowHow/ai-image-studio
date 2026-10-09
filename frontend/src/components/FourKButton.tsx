import { useEffect, useRef } from "react";
import { fourKControl } from "../fourk";
import type { ImageInfo } from "../types";
import { DownloadIcon, UpscaleIcon } from "./icons";

/** Make 4K, then Download 4K, for one result image (DESIGN.md §27.3). Nothing at all when the server does not offer it.
 *  The same control sits on a card (`ghost`) and in the viewer's bar.
 *
 *  When the copy is made the button is replaced by a link, so a keyboard user who pressed it would lose their place.
 *  Focus then goes to the new link, but only if it was lost (on the page, or on the viewer itself): a person who has
 *  moved on to something else while it was being made keeps their place. */
export function FourKButton({ image, making, className, onMake }: {
  image: ImageInfo;
  making: boolean;
  className: string;
  onMake: () => void;
}) {
  const control = fourKControl(image, making);
  const kind = control?.kind;
  const link = useRef<HTMLAnchorElement>(null);
  const pressed = useRef(false); // Make 4K was pressed here and has not been answered yet

  useEffect(() => {
    if (kind === "make") pressed.current = false; // it failed (or never started): nothing to hand over
    if (kind !== "download" || !pressed.current) return;
    pressed.current = false;
    const active = document.activeElement;
    if (!active || active === document.body || active.getAttribute("role") === "dialog") link.current?.focus();
  }, [kind]);

  if (!control) return null;
  if (control.kind === "download") {
    return (
      <a ref={link} className={className} href={control.href} download data-action="download-4k" title={control.title}>
        <DownloadIcon /> {control.label}
      </a>
    );
  }
  return (
    <button type="button" className={className} data-action="make-4k" title={control.title}
      aria-disabled={control.kind === "making" || undefined}
      onClick={() => {
        if (control.kind !== "make") return;
        pressed.current = true;
        onMake();
      }}>
      <UpscaleIcon /> {control.label}
    </button>
  );
}
