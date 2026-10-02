import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { CUSTOM, DEFAULT_GUIDANCE, randomSeed, sizeProblem, type Options } from "../options";
import type { Capabilities } from "../types";
import { useReturnFocus } from "../hooks";
import { NumberField } from "./NumberField";
import { CloseIcon, DiceIcon } from "./icons";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  caps: Capabilities;
  options: Options;
  onChange: (options: Options) => void;
  onReset: () => void;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="drawer-section" aria-label={title}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

export function OptionsDrawer({ open, onOpenChange, caps, options, onChange, onReset }: Props) {
  const set = <K extends keyof Options>(key: K, value: Options[K]) => onChange({ ...options, [key]: value });
  const { limits, supports } = caps;
  const { props: returnFocus } = useReturnFocus();
  const customProblem = options.aspect === CUSTOM ? sizeProblem(options.customWidth, options.customHeight, caps) : null;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="drawer" aria-describedby={undefined} {...returnFocus}>
          <div className="drawer-header">
            <Dialog.Title>Options</Dialog.Title>
            <Dialog.Close className="button ghost icon-only" aria-label="Close options"><CloseIcon /></Dialog.Close>
          </div>

          <div className="drawer-body">
            <Section title="Size">
              <div className="preset-grid" role="radiogroup" aria-label="Image size">
                {Object.entries(caps.aspect_ratios).map(([name, [w, h]]) => (
                  <button key={name} type="button" role="radio" aria-checked={options.aspect === name}
                    aria-label={`${name}, ${w} by ${h}`} className="preset" onClick={() => set("aspect", name)}>
                    <span className="preset-shape" style={{ aspectRatio: `${w} / ${h}` }} aria-hidden="true" />
                    <span className="preset-name">{name}</span>
                    <span className="preset-size">{w}×{h}</span>
                  </button>
                ))}
                <button type="button" role="radio" aria-checked={options.aspect === CUSTOM} className="preset"
                  onClick={() => set("aspect", CUSTOM)}>
                  <span className="preset-shape custom" aria-hidden="true" />
                  <span className="preset-name">Custom</span>
                  <span className="preset-size">your size</span>
                </button>
              </div>
              {options.aspect === CUSTOM && (
                <>
                  <div className="field-row">
                    <NumberField label="Width" value={options.customWidth} min={limits.size.min} max={limits.size.max}
                      step={limits.size.multiple} onChange={(v) => set("customWidth", v)} />
                    <NumberField label="Height" value={options.customHeight} min={limits.size.min} max={limits.size.max}
                      step={limits.size.multiple} onChange={(v) => set("customHeight", v)} />
                  </div>
                  <p className={customProblem ? "field-error" : "field-hint"} role={customProblem ? "alert" : undefined}>
                    {customProblem ?? `Multiples of ${limits.size.multiple}, up to ${(limits.size.max_pixels / 1e6).toFixed(1)} MP. The presets are the model's native sizes.`}
                  </p>
                </>
              )}
            </Section>

            <Section title="Sampling">
              <div className="field">
                <label htmlFor="steps">Steps <output htmlFor="steps">{options.steps}</output></label>
                <input id="steps" type="range" min={limits.steps.min} max={limits.steps.max} value={options.steps}
                  onChange={(e) => set("steps", Number(e.target.value))} aria-describedby="steps-help" />
                <p id="steps-help" className="field-hint">Qwen recommends 40. Fewer steps are faster but rougher.</p>
              </div>

              <fieldset className="field">
                <legend>Seed</legend>
                <label className="choice">
                  <input type="radio" name="seed-mode" checked={!options.seedLocked} onChange={() => set("seedLocked", false)} />
                  New random seed each run
                </label>
                <label className="choice">
                  <input type="radio" name="seed-mode" checked={options.seedLocked} onChange={() => set("seedLocked", true)} />
                  Fixed seed (repeatable results)
                </label>
                {options.seedLocked && (
                  <div className="field-row align-end">
                    <NumberField label="Seed value" value={options.seed} min={limits.seed.min} max={limits.seed.max}
                      onChange={(v) => set("seed", v)} />
                    <button type="button" className="button" onClick={() => set("seed", randomSeed(limits.seed.max))}>
                      <DiceIcon /> New seed
                    </button>
                  </div>
                )}
              </fieldset>

              {supports.cfg_scale && (
                <div className="field">
                  <label className="choice">
                    <input type="checkbox" checked={options.guidance !== null}
                      onChange={(e) => set("guidance", e.target.checked ? DEFAULT_GUIDANCE : null)} />
                    Use guidance (CFG)
                  </label>
                  {options.guidance !== null && (
                    <NumberField label="Guidance strength" value={options.guidance} min={Math.max(limits.cfg_scale.min, 1)}
                      max={limits.cfg_scale.max} step={0.5} integer={false} onChange={(v) => set("guidance", v)} />
                  )}
                  <p className="field-hint">
                    Qwen-Image-2.1 is meant to run without guidance. Above 1 it uses the negative prompt and roughly doubles the time per image.
                  </p>
                </div>
              )}
            </Section>

            <Section title="Output">
              <NumberField label="Images per click" value={options.numImages} min={limits.num_images.min}
                max={limits.num_images.max} hint="Each extra image uses the next seed." onChange={(v) => set("numImages", v)} />
              {options.mode === "generate" && supports.transparent && (
                <div className="field">
                  <label className="choice switch">
                    <input type="checkbox" role="switch" checked={options.transparent}
                      onChange={(e) => set("transparent", e.target.checked)} />
                    Transparent background
                  </label>
                  <p className="field-hint">Uses the model card's RGBA prompt format and saves a PNG with an alpha channel.</p>
                </div>
              )}
            </Section>

            {supports.negative_prompt && (
              <Section title="Negative prompt">
                <div className="field">
                  <label htmlFor="negative" className="sr-only">Negative prompt</label>
                  <textarea id="negative" rows={3} value={options.negativePrompt} maxLength={limits.prompt_chars}
                    placeholder="Things to steer away from…" onChange={(e) => set("negativePrompt", e.target.value)}
                    aria-describedby="negative-help" />
                  <p id="negative-help" className="field-hint">Only used when guidance is on.</p>
                </div>
              </Section>
            )}
          </div>

          <div className="drawer-footer">
            <button type="button" className="button" onClick={onReset}>Reset to defaults</button>
            <Dialog.Close className="button primary">Done</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
