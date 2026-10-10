import { useEffect, useRef } from "react";
import { enlargeControl, fourKControl } from "../fourk";
import type { FourKTarget, UpscalerStatus } from "../types";
import { DownloadIcon, UpscaleIcon, ZoomInIcon } from "./icons";

/** Make 4K and Enlarge, then Download 4K, for one picture the studio holds: a result or an edit's source (DESIGN.md §27.3, §27.9,
 *  §28.2). Nothing at all when the server offers neither, and no copy yet.
 *  The same controls sit on a card (`ghost`) and in the viewer's bar.
 *
 *  When a copy is made the button that made it is replaced (by a link, or by nothing), so a keyboard user who pressed it would lose
 *  their place. Focus then goes to the Download 4K link, but only if it was lost (on the page, or on the viewer itself): a person who
 *  has moved on to something else while it was being made keeps their place.
 *
 *  Enlarge without its model file is shown dimmed, with the reason as its tooltip; pressing it asks the server anyway, so that the
 *  reason and what to do about it appear as a message (a phone has no tooltips). */
export function FourKButton({ image, making, enlarging, upscaler, className, onMake, onEnlarge }: {
  image: FourKTarget;
  making: boolean;
  enlarging: boolean;
  upscaler: UpscalerStatus | null;
  className: string;
  onMake: () => void;
  onEnlarge: () => void;
}) {
  const control = fourKControl(image, making);
  const enlarge = enlargeControl(image, enlarging, upscaler);
  const kind = control?.kind;
  const enlargeKind = enlarge?.kind;
  const link = useRef<HTMLAnchorElement>(null);
  const pressed = useRef(false); // Make 4K or Enlarge was pressed here and has not been answered yet

  useEffect(() => {
    if (!pressed.current) return;
    if (kind === "making" || enlargeKind === "enlarging") return; // still being made
    pressed.current = false; // answered: made (a link, or no Enlarge button any more) or failed (the buttons are back)
    if (kind !== "download") return;
    const active = document.activeElement;
    if (!active || active === document.body || active.getAttribute("role") === "dialog") link.current?.focus();
  }, [kind, enlargeKind]);

  if (!control && !enlarge) return null;
  return (
    <>
      {control?.kind === "download" && (
        <a ref={link} className={className} href={control.href} download data-action="download-4k" title={control.title}>
          <DownloadIcon /> {control.label}
        </a>
      )}
      {control && control.kind !== "download" && (
        <button type="button" className={className} data-action="make-4k" title={control.title}
          aria-disabled={control.kind === "making" || undefined}
          onClick={() => {
            if (control.kind !== "make") return;
            pressed.current = true;
            onMake();
          }}>
          <UpscaleIcon /> {control.label}
        </button>
      )}
      {enlarge && (
        <button type="button" className={className} data-action="enlarge" title={enlarge.title}
          aria-disabled={enlarge.kind !== "enlarge" || undefined} data-unavailable={enlarge.kind === "unavailable" || undefined}
          onClick={() => {
            if (enlarge.kind === "enlarging") return;
            pressed.current = true;
            onEnlarge();
          }}>
          <ZoomInIcon /> {enlarge.label}
        </button>
      )}
    </>
  );
}
