import { forwardRef, useState, type DragEvent, type ReactNode } from "react";
import { SCALES, baseSize, effectiveScale, isAutoSize, scaleProblem, scaledSize, sizeLabel, summarize, type Options, type Scale } from "../options";
import type { Capabilities, ImageMode } from "../types";
import { DraftIcon, SlidersIcon, SparkleIcon } from "./icons";

interface Props {
  caps: Capabilities;
  options: Options;
  prompt: string;
  submitting: boolean;
  problem: string | null;
  tray: ReactNode; // the reference tray, shown in Edit mode (DESIGN.md §21.4)
  blockReason: string | null; // why Generate must wait, in Edit mode (an empty tray, uploads still going)
  starterAvailable: boolean; // the "Extract the subject" starter may replace the prompt (it is empty)
  onFiles: (files: File[]) => void; // pictures dropped on the card or pasted into the prompt
  onStarter: () => void;
  onPromptFocus: () => void; // the prompt has been focused, so its caret is real (the page inserts "image N" there)
  onPrompt: (text: string) => void;
  onMode: (mode: ImageMode) => void;
  onScale: (scale: Scale) => void;
  onOpenOptions: () => void;
  onSubmit: () => void;
  onDraft: () => void;
}

const MODES: { mode: ImageMode; label: string }[] = [
  { mode: "generate", label: "Generate" },
  { mode: "edit", label: "Edit" },
];

export const PromptBar = forwardRef<HTMLTextAreaElement, Props>(function PromptBar(
  { caps, options, prompt, submitting, problem, tray, blockReason, starterAvailable, onFiles, onStarter, onPromptFocus, onPrompt, onMode, onScale, onOpenOptions, onSubmit, onDraft },
  textareaRef,
) {
  const limit = caps.limits.prompt_chars;
  const nearLimit = prompt.length > limit * 0.8;
  const base = baseSize(options, caps);
  const scale = effectiveScale(options, caps);
  const editing = options.mode !== "generate";
  const auto = isAutoSize(options);
  const draft = caps.limits.draft;
  const [dropping, setDropping] = useState(false);
  const carriesFiles = (e: DragEvent) => e.dataTransfer.types.includes("Files");
  return (
    <section className={`prompt-card${dropping ? " dropping" : ""}`} aria-label="New image"
      onDragOver={(e) => {
        if (!carriesFiles(e)) return;
        e.preventDefault(); // a file may be dropped here, in either mode (Generate says why it can't use it)
        setDropping(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropping(false);
      }}
      onDrop={(e) => {
        if (!carriesFiles(e)) return;
        e.preventDefault();
        setDropping(false);
        onFiles(Array.from(e.dataTransfer.files));
      }}>
      <div className="prompt-top">
        <div className="segmented" role="radiogroup" aria-label="Mode">
          {MODES.map(({ mode, label }) => {
            const available = caps.modes.includes(mode);
            return (
              <button key={mode} type="button" role="radio" aria-checked={options.mode === mode} disabled={!available}
                title={available ? undefined : "Editing isn't available: the loaded pipeline can't edit images."}
                onClick={() => onMode(mode)}>
                {label}
              </button>
            );
          })}
        </div>
        <div className="scale-group">
          <span className="scale-label" id="scale-label">Scale</span>
          <div className="segmented" role="radiogroup" aria-labelledby="scale-label" aria-describedby="prompt-help">
            {SCALES.map((value) => {
              const problem = auto ? "With Size on Auto, the images and Resolution (in Options) decide the size." : scaleProblem(base, value, caps);
              const size = scaledSize(base, value, caps);
              return (
                <button key={value} type="button" role="radio" aria-checked={scale === value} disabled={!!problem}
                  title={problem ?? `${value}% of the selected size: ${size.width}×${size.height}`}
                  onClick={() => onScale(value)}>
                  {value}%
                </button>
              );
            })}
          </div>
          <span className="scale-size" aria-live="polite">{sizeLabel(options, caps)}</span>
        </div>
      </div>

      {editing && tray}

      <label htmlFor="prompt" className="sr-only">Prompt</label>
      <textarea
        id="prompt"
        ref={textareaRef}
        className="prompt-input"
        value={prompt}
        maxLength={limit}
        placeholder={editing ? "Refer to images by number, e.g. put the dog from image 1 into the scene from image 2." : "Describe the image you want…"}
        aria-describedby="prompt-help"
        onChange={(e) => onPrompt(e.target.value)}
        onFocus={onPromptFocus}
        onPaste={(e) => {
          const pictures = Array.from(e.clipboardData?.files ?? []).filter((file) => file.type.startsWith("image/"));
          if (!pictures.length) return; // ordinary text pastes as always
          e.preventDefault();
          onFiles(pictures);
        }}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
            e.preventDefault();
            if (e.shiftKey) {
              if (!editing) onDraft();
            } else {
              onSubmit();
            }
          }
        }}
      />

      <div className="prompt-row">
        <button type="button" className="button options-button" aria-haspopup="dialog" onClick={onOpenOptions}>
          <SlidersIcon />
          <span>Options</span>
          <span className="options-summary">{summarize(options, caps)}</span>
        </button>
        <div className="prompt-row-end">
          {nearLimit && <span className="counter">{prompt.length} / {limit}</span>}
          <button type="button" className="button" onClick={onDraft} aria-describedby="prompt-help"
            disabled={!prompt.trim() || submitting || editing}
            title={editing ? "Drafts are for Generate." : `A small, quick try of this prompt (${draft.long_side} px, at most ${draft.steps} steps) to check your wording. The full-size image will look different.`}>
            <DraftIcon />
            Draft
          </button>
          <button type="button" className="button primary" onClick={onSubmit} disabled={!prompt.trim() || submitting || !!blockReason}>
            <SparkleIcon />
            {submitting ? "Sending…" : "Generate"}
          </button>
        </div>
      </div>
      {editing && blockReason && <p className="prompt-block" role="status">{blockReason}</p>}
      {editing && (
        <p className="prompt-help">
          Try: <button type="button" className="link-button" disabled={!starterAvailable} onClick={onStarter}
            title={starterAvailable ? "Fills the prompt and turns Transparent on" : "Clear the prompt first: a starter replaces it."}>Extract the subject</button>
          {" "}(a cut-out of image 1 on a transparent background)
        </p>
      )}
      <p id="prompt-help" className="prompt-help">
        Ctrl + Enter (⌘ + Enter on a Mac) generates; add Shift for a draft, a small quick try ({draft.long_side} px, up to {draft.steps} steps)
        that will look different from the full-size image. A smaller scale is a different picture too, even with the same seed.
      </p>
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </section>
  );
});
