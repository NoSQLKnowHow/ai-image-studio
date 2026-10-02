import { forwardRef } from "react";
import { SCALES, baseSize, effectiveScale, resolveSize, scaleProblem, scaledSize, summarize, type Options, type Scale } from "../options";
import type { Capabilities, Mode } from "../types";
import { DraftIcon, SlidersIcon, SparkleIcon } from "./icons";

interface Props {
  caps: Capabilities;
  options: Options;
  prompt: string;
  submitting: boolean;
  problem: string | null;
  onPrompt: (text: string) => void;
  onMode: (mode: Mode) => void;
  onScale: (scale: Scale) => void;
  onOpenOptions: () => void;
  onSubmit: () => void;
  onDraft: () => void;
}

const MODES: { mode: Mode; label: string }[] = [
  { mode: "generate", label: "Generate" },
  { mode: "edit", label: "Edit" },
];

export const PromptBar = forwardRef<HTMLTextAreaElement, Props>(function PromptBar(
  { caps, options, prompt, submitting, problem, onPrompt, onMode, onScale, onOpenOptions, onSubmit, onDraft },
  textareaRef,
) {
  const limit = caps.limits.prompt_chars;
  const nearLimit = prompt.length > limit * 0.8;
  const base = baseSize(options, caps);
  const scale = effectiveScale(options, caps);
  const used = resolveSize(options, caps);
  const editing = options.mode !== "generate";
  const draft = caps.limits.draft;
  return (
    <section className="prompt-card" aria-label="New image">
      <div className="prompt-top">
        <div className="segmented" role="radiogroup" aria-label="Mode">
          {MODES.map(({ mode, label }) => {
            const available = caps.modes.includes(mode);
            return (
              <button key={mode} type="button" role="radio" aria-checked={options.mode === mode} disabled={!available}
                title={available ? undefined : "Edit mode arrives in a later update"}
                onClick={() => onMode(mode)}>
                {label}
                {!available && <span className="soon">soon</span>}
              </button>
            );
          })}
        </div>
        <div className="scale-group">
          <span className="scale-label" id="scale-label">Scale</span>
          <div className="segmented" role="radiogroup" aria-labelledby="scale-label" aria-describedby="prompt-help">
            {SCALES.map((value) => {
              const problem = editing ? "Edit mode sizes the result with Resolution." : scaleProblem(base, value, caps);
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
          <span className="scale-size" aria-live="polite">{used.width}×{used.height}</span>
        </div>
      </div>

      <label htmlFor="prompt" className="sr-only">Prompt</label>
      <textarea
        id="prompt"
        ref={textareaRef}
        className="prompt-input"
        value={prompt}
        maxLength={limit}
        placeholder="Describe the image you want…"
        aria-describedby="prompt-help"
        onChange={(e) => onPrompt(e.target.value)}
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
          <button type="button" className="button primary" onClick={onSubmit} disabled={!prompt.trim() || submitting}>
            <SparkleIcon />
            {submitting ? "Sending…" : "Generate"}
          </button>
        </div>
      </div>
      <p id="prompt-help" className="prompt-help">
        Ctrl + Enter (⌘ + Enter on a Mac) generates; add Shift for a draft, a small quick try ({draft.long_side} px, up to {draft.steps} steps)
        that will look different from the full-size image. A smaller scale is a different picture too, even with the same seed.
      </p>
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </section>
  );
});
