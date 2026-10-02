import { forwardRef } from "react";
import { summarize, type Options } from "../options";
import type { Capabilities, Mode } from "../types";
import { SlidersIcon, SparkleIcon } from "./icons";

interface Props {
  caps: Capabilities;
  options: Options;
  prompt: string;
  submitting: boolean;
  problem: string | null;
  onPrompt: (text: string) => void;
  onMode: (mode: Mode) => void;
  onOpenOptions: () => void;
  onSubmit: () => void;
}

const MODES: { mode: Mode; label: string }[] = [
  { mode: "generate", label: "Generate" },
  { mode: "edit", label: "Edit" },
];

export const PromptBar = forwardRef<HTMLTextAreaElement, Props>(function PromptBar(
  { caps, options, prompt, submitting, problem, onPrompt, onMode, onOpenOptions, onSubmit },
  textareaRef,
) {
  const limit = caps.limits.prompt_chars;
  const nearLimit = prompt.length > limit * 0.8;
  return (
    <section className="prompt-card" aria-label="New image">
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
            onSubmit();
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
          <button type="button" className="button primary" onClick={onSubmit} disabled={!prompt.trim() || submitting}>
            <SparkleIcon />
            {submitting ? "Sending…" : "Generate"}
          </button>
        </div>
      </div>
      <p id="prompt-help" className="prompt-help">Ctrl + Enter (⌘ + Enter on a Mac) generates.</p>
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </section>
  );
});
