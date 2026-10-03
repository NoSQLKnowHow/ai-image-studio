import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { AUTO, CUSTOM, DEFAULT_GUIDANCE, randomSeed, resolutionLabel, sizeKey, sizeProblem, type Options } from "../options";
import { editCost } from "../tray";
import type { Capabilities } from "../types";
import { useReturnFocus } from "../hooks";
import { NumberField } from "./NumberField";
import { CloseIcon, DiceIcon } from "./icons";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  caps: Capabilities;
  options: Options;
  trayCount: number; // images in the tray, for the cost of an edit
  hasAlphaInput: boolean; // one of them has transparency
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

export function OptionsDrawer({ open, onOpenChange, caps, options, trayCount, hasAlphaInput, onChange, onReset }: Props) {
  const set = <K extends keyof Options>(key: K, value: Options[K]) => onChange({ ...options, [key]: value });
  const { limits, supports } = caps;
  const { props: returnFocus } = useReturnFocus();
  const editing = options.mode === "edit";
  const key = sizeKey(options); // Edit has its own size choice (it can be Auto), Generate has its own
  const setSize = (name: string) => set(editing ? "editAspect" : "aspect", name);
  const customProblem = key === CUSTOM ? sizeProblem(options.customWidth, options.customHeight, caps) : null;
  const cost = editCost(trayCount, options.resolution, limits.edit_warn_units);

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
                {editing && (
                  <button type="button" role="radio" aria-checked={key === AUTO} className="preset" onClick={() => setSize(AUTO)}>
                    <span className="preset-shape auto" aria-hidden="true" />
                    <span className="preset-name">Auto</span>
                    <span className="preset-size">from your images</span>
                  </button>
                )}
                {Object.entries(caps.aspect_ratios).map(([name, [w, h]]) => (
                  <button key={name} type="button" role="radio" aria-checked={key === name}
                    aria-label={`${name}, ${w} by ${h}`} className="preset" onClick={() => setSize(name)}>
                    <span className="preset-shape" style={{ aspectRatio: `${w} / ${h}` }} aria-hidden="true" />
                    <span className="preset-name">{name}</span>
                    <span className="preset-size">{w}×{h}</span>
                  </button>
                ))}
                <button type="button" role="radio" aria-checked={key === CUSTOM} className="preset"
                  onClick={() => setSize(CUSTOM)}>
                  <span className="preset-shape custom" aria-hidden="true" />
                  <span className="preset-name">Custom</span>
                  <span className="preset-size">your size</span>
                </button>
              </div>
              {editing && key === AUTO && (
                <p className="field-hint">
                  Auto lets the model size the result from your images: about 1 megapixel at 1K, about 4 at 2K. With several images it
                  follows one of them (the tray says which). Any other size overrides the shape, not the resolution.
                </p>
              )}
              {key === CUSTOM && (
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

            {editing && (
              <Section title="Resolution">
                <div className="segmented" role="radiogroup" aria-label="Edit resolution">
                  {limits.resolutions.map((value) => (
                    <button key={value} type="button" role="radio" aria-checked={options.resolution === value}
                      onClick={() => set("resolution", value as Options["resolution"])}>
                      {resolutionLabel(value)}
                    </button>
                  ))}
                </div>
                <p className="field-hint">
                  1K is about 1 megapixel and quicker. 2K is about 4 and costs far more with several images. It sizes every image you add, not
                  only the result.
                </p>
                <p className={cost.heavy ? "field-error" : "field-hint"} data-testid="edit-cost" role={cost.heavy ? "alert" : undefined}>
                  Cost: {cost.label} ({trayCount} {trayCount === 1 ? "image" : "images"} at {resolutionLabel(options.resolution)}).
                  {cost.heavy ? " This edit is heavy: expect a long run, or running out of memory. Use 1K or fewer images." : ""}
                </p>
              </Section>
            )}

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
              {supports.transparent && (
                <div className="field">
                  <label className="choice switch">
                    <input type="checkbox" role="switch" checked={options.transparent}
                      onChange={(e) => set("transparent", e.target.checked)} />
                    Transparent background
                  </label>
                  <p className="field-hint">Uses the model card's RGBA prompt format and saves a PNG with an alpha channel.</p>
                  {editing && hasAlphaInput && (
                    <p className="field-hint" data-testid="alpha-hint">
                      One of your images has transparency, and it is kept as it is. Turn this on only if you want the result to be transparent too.
                    </p>
                  )}
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
