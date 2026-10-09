import { useRef } from "react";
import { UpscaleIcon } from "./icons";

/** "Upscale a picture…": choose a file from this computer and get a 4K copy back as a download (DESIGN.md §27.9). Nothing is
 *  added to the history, so it sits above the runs and not in them. */
export function UpscalePicture({ busy, onPick }: { busy: boolean; onPick: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="tools-row">
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1} aria-hidden="true"
        data-testid="upscale-file-input"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = ""; // so choosing the same file again is a change
          if (file) onPick(file);
        }} />
      <button type="button" className="button small ghost" data-action="upscale-picture" aria-disabled={busy || undefined}
        onClick={() => !busy && input.current?.click()}
        title="Choose a picture from this computer and get a 4K copy of it back as a download. Nothing is added to your history. It makes the picture bigger, not sharper: no detail is added.">
        <UpscaleIcon /> {busy ? "Upscaling…" : "Upscale a picture…"}
      </button>
    </div>
  );
}
