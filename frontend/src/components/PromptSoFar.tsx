import { useMemo, useState } from "react";
import { describeSettings, lengthMeter, settingChips, wordDiff, changedIn, type SettingKey } from "../builder";
import type { Draft, ForKind } from "../builderTypes";
import type { Capabilities } from "../types";

/** "The prompt so far" (DESIGN.md §37.2, criterion 226): the draft after every reply, which can be edited by hand (the next reply starts from the edited words), with a length
 *  meter against the studio's limit, the words the last reply changed, the settings that fit as switches, and the buttons that do something with it. */
export interface PromptButtons {
  useLabel: string; // "Images", "Vector" or "Music": where Use in … goes
  onSave: () => void; // Save as prompt
  onSaveVersion?: () => void; // Save as new version (when the chat is about a saved prompt)
  onUse?: () => void;
  onGenerate?: () => void;
  onQuick?: () => void;
  quickProblem: string | null; // why there is no quick try, or null
  useProblem: string | null; // why Use in … cannot be pressed now
}

export function PromptSoFar({ kind, draft, edited, previous, caps, off, buttons, onEdit, onToggleSetting }: {
  kind: ForKind;
  draft: Draft | null; // the newest, from the chat
  edited: Draft | null; // what the person has changed by hand and not sent
  previous: Draft | null; // the one before the last reply, to show what it changed
  caps: Capabilities;
  off: ReadonlySet<SettingKey>;
  buttons: PromptButtons;
  onEdit: (draft: Draft | null) => void;
  onToggleSetting: (key: SettingKey) => void;
}) {
  const shown = edited ?? draft;
  const limit = caps.limits.prompt_chars;
  const meter = lengthMeter(shown?.prompt.length ?? 0, limit);
  const [negativeOpen, setNegativeOpen] = useState(false);
  const diff = useMemo(() => (previous && draft ? wordDiff(previous.prompt, draft.prompt) : []), [previous, draft]);
  const chips = shown ? settingChips(kind, shown, caps) : [];
  const described = shown ? describeSettings(kind, shown) : [];
  const hasNegative = kind === "images" || kind === "edit";

  const change = (patch: Partial<Draft>) => {
    const base: Draft = shown ?? { prompt: "", negative: null, settings: {}, note: "" };
    const next = { ...base, ...patch };
    const same = draft && next.prompt === draft.prompt && (next.negative ?? "") === (draft.negative ?? "");
    onEdit(same ? null : next);
  };
  const canSave = !!shown && shown.prompt.trim() !== "" && !meter.over;

  return (
    <section className="builder-prompt" aria-label="The prompt so far">
      <h2 className="builder-heading">The prompt so far</h2>
      {!shown && <p className="hint">The prompt appears here as the builder writes it. You can also type or paste one yourself.</p>}
      <label htmlFor="builder-draft" className="sr-only">The prompt so far</label>
      <textarea id="builder-draft" className="prompt-input builder-draft" value={shown?.prompt ?? ""} rows={8} onChange={(e) => change({ prompt: e.target.value })}
        aria-invalid={meter.over || undefined} placeholder="Nothing yet." />
      <p className={`counter builder-meter${meter.over ? " over" : ""}`} aria-live="polite">
        <span>{meter.text}</span>{meter.over && <span className="form-error"> Too long for the studio to use.</span>}
      </p>
      {shown?.note && <p className="hint builder-note">{shown.note}</p>}

      {hasNegative && (shown?.negative || negativeOpen) ? (
        <>
          <label htmlFor="builder-negative" className="builder-label">Negative prompt</label>
          <textarea id="builder-negative" className="prompt-input builder-negative" value={shown?.negative ?? ""} rows={2} onChange={(e) => change({ negative: e.target.value || null })} />
        </>
      ) : hasNegative ? (
        <button type="button" className="link-button" onClick={() => setNegativeOpen(true)}>Add a negative prompt</button>
      ) : null}

      {chips.length > 0 && (
        <div className="builder-settings" role="group" aria-label="Settings that fit">
          {chips.map((c) => (
            <button key={c.key} type="button" className="builder-chip" aria-pressed={!off.has(c.key)} onClick={() => onToggleSetting(c.key)}
              title={off.has(c.key) ? "Off: not set when you use this prompt" : "On: set when you use this prompt"}>{c.label}</button>
          ))}
        </div>
      )}
      {described.length > 0 && <ul className="builder-settings-list" aria-label="Settings">{described.map((d) => <li key={d}>{d}</li>)}</ul>}

      {changedIn(diff) && (
        <details className="builder-changes">
          <summary>What the last reply changed</summary>
          <p className="builder-text" aria-label="The changed words">
            {diff.map((run, i) => run.op === "same" ? <span key={i}>{run.text} </span> : run.op === "del"
              ? <del key={i} aria-label={`removed: ${run.text}`}>{run.text} </del> : <ins key={i} aria-label={`added: ${run.text}`}>{run.text} </ins>)}
          </p>
        </details>
      )}

      <div className="builder-prompt-buttons">
        <button type="button" className="button primary" onClick={buttons.onSave} disabled={!canSave}>Save as prompt</button>
        {buttons.onSaveVersion && <button type="button" className="button" onClick={buttons.onSaveVersion} disabled={!canSave}>Save as new version</button>}
        <button type="button" className="button" onClick={buttons.onUse} disabled={!canSave || !buttons.onUse || !!buttons.useProblem} title={buttons.useProblem ?? undefined}>Use in {buttons.useLabel}</button>
        <button type="button" className="button" onClick={buttons.onGenerate} disabled={!canSave || !buttons.onGenerate || !!buttons.useProblem} title={buttons.useProblem ?? undefined}>Generate now</button>
        <button type="button" className="button" onClick={buttons.onQuick} disabled={!canSave || !buttons.onQuick || !!buttons.quickProblem} title={buttons.quickProblem ?? "A small, quick try of this prompt (a Draft)"}>Quick try</button>
      </div>
    </section>
  );
}
