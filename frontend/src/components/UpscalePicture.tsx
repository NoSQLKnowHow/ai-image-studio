import { useRef, useState } from "react";
import { UPSCALE_MARK, UPSCALE_TITLE } from "../paste";
import { UpscaleIcon } from "./icons";

/** "Upscale a picture…": give it a picture and get a 4K copy back as a download (DESIGN.md §27.9, §36.2). Nothing is added to the history, so it sits above the
 *  runs and not in them. A picture comes three ways: chosen from this computer, dropped on the button (which lights up while one is over it), or pasted while
 *  the button has the focus (the page's one paste listener sends it here when the focus is on this button, `UPSCALE_MARK`). Clicking it focuses it, which is how a
 *  person gets a paste to land here on a browser that does not focus a button when it is clicked. All three end in `onFiles`, which uses the first picture. */
export function UpscalePicture({ busy, onFiles }: { busy: boolean; onFiles: (files: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [dropping, setDropping] = useState(false);
  const carriesFiles = (e: React.DragEvent) => e.dataTransfer.types.includes("Files");
  return (
    <div className="tools-row">
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1} aria-hidden="true"
        data-testid="upscale-file-input"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = ""; // so choosing the same file again is a change
          if (file) onFiles([file]);
        }} />
      <button type="button" className={`button small ghost${dropping ? " dropping" : ""}`} data-action="upscale-picture" {...{ [UPSCALE_MARK]: "" }}
        aria-disabled={busy || undefined}
        onClick={(e) => {
          e.currentTarget.focus(); // so that a paste lands here even where a click does not focus a button
          if (!busy) input.current?.click();
        }}
        onDragOver={(e) => {
          if (!carriesFiles(e)) return;
          e.preventDefault(); // allow the drop
          setDropping(true);
        }}
        onDragLeave={() => setDropping(false)}
        onDrop={(e) => {
          if (!carriesFiles(e)) return;
          e.preventDefault();
          e.stopPropagation(); // it is this button's: not the prompt card's, not the page's
          setDropping(false);
          onFiles(Array.from(e.dataTransfer.files));
        }}
        title={UPSCALE_TITLE}>
        <UpscaleIcon /> {busy ? "Upscaling…" : "Upscale a picture…"}
      </button>
    </div>
  );
}
