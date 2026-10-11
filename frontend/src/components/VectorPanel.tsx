import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../api";
import { copyText, type ToastKind } from "../hooks";
import type { ViewScope } from "../format";
import type { KeyValueStore } from "../options";
import type { Capabilities, Run, Status, VectorItem, VectorRun } from "../types";
import { recipeKey, useVectorRecipe } from "../useVectorRecipe";
import {
  ARRANGEMENT_LABELS, KIND_HINTS, KIND_LABELS, LAYERS_BY_LABELS, WHITE, WHITE_LABEL, addInk, bleedApplies, buildVectorRequest, editRecipe, effectiveArrangement,
  inkCount, layersLine, loadVectorForm, pngRange, presetMatches, rebuildRecipe, reconcileReusedRecipe, recipeRequest, removeInk, saveVectorForm, setInk,
  unavailableText, vectorFormFromRun, vectorProblem, vectorRetryRequest, vectorUsable, withColours, type VectorForm,
} from "../vector";
import { QueueBar } from "./Feedback";
import { NumberField } from "./NumberField";
import { panelId, tabId } from "./Tabs";
import { type ProjectControls } from "./ProjectMenu";
import { VectorIcon } from "./icons";
import { Swatch } from "./Swatch";
import { VectorCard } from "./VectorCard";

// The Vector tab (DESIGN.md §31, §31.12.8): the form that makes a laser-ready SVG from an idea, and the vectors made so far. It follows the Music tab's shape
// (a form that remembers its values, one shared filter bar, a list of cards) so that a person who knows one knows the other.

interface Props {
  hidden: boolean;
  caps: Capabilities;
  status: Status | null;
  store: KeyValueStore;
  runs: VectorRun[]; // newest first
  runsReady: boolean;
  filterBar: ReactNode; // the filter bar, shared with the other tabs (DESIGN.md §29)
  loadProblem: ReactNode | null; // shown in place of "Loading…" when the filtered list could not be loaded
  empty: ReactNode; // what the list says when there is nothing to show for the filter, or that it is still looking
  isTransient: (run: VectorRun) => boolean; // in a filtered view only because it is working (§29.3, §32)
  viewScope: ViewScope; // which filter that is
  projects: ProjectControls; // the project folders and what the Project button does (§32)
  more: boolean; // older runs can be loaded
  loadingOlder: boolean;
  now: number;
  push: (kind: ToastKind, text: string) => void;
  onRun: (run: VectorRun) => void; // a run was made (or retried): the page adds it to its list
  onLoadOlder: () => void;
  onCancel: (run: Run) => void;
  onToggleKeep: (run: Run) => void;
  onRestore: (run: Run) => void;
  onDelete: (run: Run) => void;
}

export function VectorPanel({
  hidden, caps, status, store, runs, runsReady, filterBar, loadProblem, empty, isTransient, viewScope, projects, more, loadingOlder, now, push, onRun, onLoadOlder,
  onCancel, onToggleKeep, onRestore, onDelete,
}: Props) {
  const ids = useId();
  const idOf = (name: string) => `${ids}-${name}`;
  const ideaBox = useRef<HTMLTextAreaElement>(null);
  const limits = caps.limits.vector;
  const [form, setForm] = useState<VectorForm>(() => loadVectorForm(caps, store).form);
  const [problem, setProblem] = useState<string | null>(null);
  const [inkNote, setInkNote] = useState<string | null>(null); // why a colour was refused (white, or one an ink has already)
  const [submitting, setSubmitting] = useState(false);
  const copying = useRef(new Set<string>()); // versions whose SVG is being fetched to copy: one request at a time for each
  // The recipe the run being reused used, and the request it belongs to: when the server's words for that request arrive they are compared with it
  // (`reconcileReusedRecipe`). Any change the person makes meanwhile cancels the comparison.
  const reused = useRef<{ key: string | null; text: string } | null>(null);

  useEffect(() => saveVectorForm(form, store), [form, store]);

  // What the studio sends: asked of the server whenever the idea, kind, colours or palette change (after a pause), never written here. Nothing is asked
  // while the tab is not on show, or when Vector cannot run, so a remembered prompt does not make a request on every other tab's page load.
  const usable = vectorUsable(caps);
  const request = recipeRequest(form);
  const recipe = useVectorRecipe(hidden || !usable ? null : request);
  const negative = recipe.answered?.negative ?? null;

  // Reuse: once the server's words for the restored form are known (they have arrived, or could not be had), decide whether the run's own words were edited by
  // hand. `settled` is that decision for a form, or null while the words are still on their way.
  const settled = (waiting: { key: string | null; text: string }, current: VectorForm): VectorForm | null => {
    const answered = recipe.answered?.key === waiting.key ? recipe.answered : null;
    if (waiting.key !== null && !answered && recipe.failed?.key !== waiting.key) return null;
    return reconcileReusedRecipe(current, waiting.text, answered?.positive ?? null);
  };
  useEffect(() => {
    const waiting = reused.current;
    if (!waiting || settled(waiting, form) === null) return;
    reused.current = null;
    setForm((current) => settled(waiting, current) ?? current);
  }, [recipe]);

  /** Change the form. The "check the form" message and the note about a refused colour are stale as soon as it changes, and so is a pending Reuse
   *  comparison: it must never put the run's words over something the person has just done. A function of the current form, so that two changes in
   *  one event cannot undo each other. */
  const change = (fn: (current: VectorForm) => VectorForm) => {
    reused.current = null;
    setForm(fn);
    setProblem(null);
    setInkNote(null);
  };
  const update = (patch: Partial<VectorForm>) => change((current) => ({ ...current, ...patch }));

  const ready = form.idea.trim() !== ""; // an empty idea is nothing to make
  const arrangement = effectiveArrangement(form);
  const byShape = form.layersBy === "shape";
  const range = pngRange(limits);
  const seedHint = form.seedLocked ? "The same prompt, settings and seed make the same drawing, and the same SVG." : "A new random seed for every run. Lock it to repeat a drawing.";
  // The box shows the hand-written words once there are some, else the server's last answer (kept while a newer one is on its way, so that it does not
  // flicker empty on every key), and nothing while there is no prompt to write words around.
  const recipeShown = form.recipeEdited ? form.recipeText : request === null ? "" : recipe.answered?.positive ?? "";

  const submit = async () => {
    if (submitting || !ready) return;
    const wrong = vectorProblem(form, caps);
    if (wrong) {
      setProblem(wrong);
      return;
    }
    setSubmitting(true);
    setProblem(null);
    try {
      onRun(await api.createVectorRun(buildVectorRequest(form, caps)));
    } catch (error) {
      const err = error as ApiError;
      if (err.status === 422) setProblem(err.message);
      else push("error", err.status === 429 ? err.message : `Couldn't start the run: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  // Reuse: the prompt and every setting come back to the form, with the seed locked. The run's own recipe is compared with the server's once it arrives.
  const reuse = (run: VectorRun) => {
    const restored = vectorFormFromRun(run, caps, form);
    const waiting = { key: recipeKey(recipeRequest(restored)), text: run.options.recipe };
    // the words for the restored form may be known already (the same prompt, kind and colours were on the form): then there is nothing to wait for
    const next = settled(waiting, restored);
    reused.current = next === null ? waiting : null;
    setForm(next ?? restored);
    setProblem(null);
    setInkNote(null);
    push("info", `Loaded the prompt and settings. Seed locked to ${run.options.seed}.`);
    window.scrollTo({ top: 0, behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    ideaBox.current?.focus({ preventScroll: true });
  };

  const retry = async (run: VectorRun) => {
    try {
      onRun(await api.createVectorRun(vectorRetryRequest(run)));
    } catch (error) {
      push("error", `Couldn't retry: ${(error as Error).message}`);
    }
  };

  // Copy SVG code: the file is fetched as TEXT and copied. It is never put in the page as markup (§31.12.8).
  const copySvg = async (item: VectorItem) => {
    if (copying.current.has(item.id)) return;
    copying.current.add(item.id);
    try {
      const text = await api.fetchText(item.url);
      const ok = await copyText(text);
      push(ok ? "success" : "error", ok ? "SVG code copied." : "Couldn't copy. Use Download SVG instead.");
    } catch (error) {
      push("error", `Couldn't get the SVG: ${(error as Error).message}`);
    } finally {
      copying.current.delete(item.id);
    }
  };

  /** One choice among a few, as the Images tab's segmented buttons are: a radio group named by the label above it (`name` is that label's id suffix), the
   *  chosen one checked. `disabled` greys the whole group (Arrangement, by shape). */
  const choice = <T extends string>(name: string, values: readonly T[], names: Record<T, string>, current: T, set: (value: T) => void, disabled = false) => (
    <div className="segmented" role="radiogroup" aria-labelledby={idOf(name)} aria-disabled={disabled || undefined}>
      {values.map((value) => (
        <button key={value} type="button" role="radio" aria-checked={current === value} disabled={disabled} data-value={value} onClick={() => set(value)}>
          {names[value] ?? value}
        </button>
      ))}
    </div>
  );

  const body: ReactNode = (() => {
    if (!usable) {
      const { reason, hint } = unavailableText(caps);
      return (
        <section className="vector-form vector-unavailable" aria-label="Vector is not available">
          <h2><VectorIcon /> Vector isn't available on this server</h2>
          <p role="status">{reason}</p>
          {hint && <p className="hint">{hint}</p>}
          <p className="hint">Images work as usual.</p>
        </section>
      );
    }
    return (
      <section className="vector-form" aria-label="New vector"
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
            e.preventDefault();
            void submit();
          }
        }}>
        <div className="vector-title">
          <h2><VectorIcon /> New vector</h2>
          <p className="hint">
            An SVG a laser can use: flat colour shapes, closed, one layer for each colour, at a real size in millimetres. The studio draws the picture, then
            turns it into shapes itself, so the colours and the layers are what you ask for.
          </p>
        </div>

        {/* the idea */}
        <div className="field">
          <label htmlFor={idOf("idea")}>Prompt</label>
          <textarea id={idOf("idea")} ref={ideaBox} className="prompt-input" data-first-field rows={3} value={form.idea} maxLength={caps.limits.prompt_chars}
            placeholder="a fox head, facing left" aria-describedby={idOf("idea-hint")} onChange={(e) => update({ idea: e.target.value })} />
          <p id={idOf("idea-hint")} className="field-hint">Say what to make. The words around it that the studio adds are shown below, and you can change them.</p>
        </div>

        {/* the kind of artwork changes the wording the studio adds (§31.3) */}
        <div className="field">
          <span className="field-label" id={idOf("kind")}>Kind of artwork</span>
          {choice("kind", limits.kinds, KIND_LABELS, form.kind, (kind) => update({ kind }))}
          <p className="field-hint">{KIND_HINTS[form.kind]}</p>
        </div>

        {/* Colours: a stepper, white always there, one colour input per ink, and the line that says how many layers there will be */}
        <fieldset className="field vector-colours-field">
          <legend>Colours</legend>
          <div className="stepper" role="group" aria-label="Number of colours">
            <button type="button" className="button small" aria-label="Fewer colours" disabled={form.colours <= limits.colours.min}
              onClick={() => change((current) => withColours(current, current.colours - 1))}>−</button>
            <output aria-live="polite" data-vector-colours-value>{form.colours}</output>
            <button type="button" className="button small" aria-label="More colours" disabled={form.colours >= limits.colours.max}
              onClick={() => change((current) => withColours(current, current.colours + 1))}>+</button>
          </div>
          <p className="field-hint">
            {form.colours} colours: white and {inkCount(form.colours)} {inkCount(form.colours) === 1 ? "ink" : "inks"}. White counts, because it is the material you cut or engrave.
          </p>
          <ul className="inks" aria-label="Colours of the layers">
            <li className="ink ink-white" data-ink="white">
              <Swatch colour={WHITE} />
              <span>{WHITE_LABEL}</span>
            </li>
            {Array.from({ length: inkCount(form.colours) }, (_, i) => {
              const colour = form.palette[i];
              if (colour) {
                return (
                  <li className="ink" key={`ink-${i}`} data-ink={i + 1}>
                    {/* the native colour input: it gives the colour in lower case and the form keeps it in capitals, as the server wants it */}
                    <input type="color" aria-label={`Ink ${i + 1} colour`} value={colour.toLowerCase()}
                      onChange={(e) => {
                        const next = setInk(form.palette, i, e.target.value);
                        if (next.refused) setInkNote(next.refused); // white, or a colour an ink has already: the palette stays as it was
                        else update({ palette: next.palette });
                      }} />
                    <code>{colour}</code>
                    <button type="button" className="button small ghost" aria-label={`Let the picture choose ink ${i + 1}`}
                      onClick={() => change((current) => ({ ...current, palette: removeInk(current.palette, i) }))}>
                      Let the picture choose
                    </button>
                  </li>
                );
              }
              return (
                <li className="ink ink-open" key={`ink-${i}`} data-ink={i + 1}>
                  <Swatch unset />
                  <span>Let the picture choose</span>
                  {/* adding always goes on the end of the palette, so only the first open ink offers it: the colour then appears where the person pressed */}
                  {i === form.palette.length && (
                    <button type="button" className="button small" aria-label={`Choose a colour for ink ${i + 1}`} onClick={() => change((current) => ({ ...current, palette: addInk(current.palette, current.colours) }))}>
                      Choose a colour
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
          {inkNote && <p className="field-error" role="status">{inkNote}</p>}
          <p className="vector-layers-line" role="status" data-vector-layers-line>{layersLine(form, limits)}</p>
        </fieldset>

        {/* Size, in millimetres: the SVG is made at exactly this size */}
        <fieldset className="field">
          <legend>Size</legend>
          <div className="vector-size">
            <NumberField label="Width (mm)" value={form.widthMm} min={limits.size_mm.min} max={limits.size_mm.max} integer={false} step={1}
              onChange={(widthMm) => update({ widthMm })} />
            <NumberField label="Height (mm)" value={form.heightMm} min={limits.size_mm.min} max={limits.size_mm.max} integer={false} step={1}
              onChange={(heightMm) => update({ heightMm })} />
          </div>
          <div className="chips" role="group" aria-label="Sheet sizes of your machines">
            {limits.presets.map((preset) => (
              <button key={preset.id} type="button" className="chip" aria-pressed={presetMatches(form, preset)} data-preset={preset.id}
                onClick={() => update({ widthMm: preset.width_mm, heightMm: preset.height_mm })}>
                {preset.name} · {preset.width_mm} × {preset.height_mm} mm
              </button>
            ))}
          </div>
          <p className="field-hint">The SVG is made at exactly this size, in millimetres, so the laser cuts the size you chose.</p>
        </fieldset>

        {/* what the layers are by, and how they are arranged (§31.3): by shape the pieces are separate already, so the arrangement is greyed */}
        <div className="vector-settings">
          <div className="field">
            <span className="field-label" id={idOf("layersby")}>Layers by</span>
            {choice("layersby", limits.layers_by, LAYERS_BY_LABELS, form.layersBy, (layersBy) => update({ layersBy }))}
            <p className="field-hint">
              {byShape ? `Each separate piece is its own layer, up to ${limits.shape_layer_cap}.` : "One layer for each ink."}
            </p>
          </div>
          <div className="field">
            <span className="field-label" id={idOf("arrangement")}>Arrangement</span>
            {choice("arrangement", limits.arrangements, ARRANGEMENT_LABELS, arrangement, (value) => update({ arrangement: value }), byShape)}
            <p className="field-hint">
              {byShape ? "Layers by shape are always side by side." : arrangement === "stacked"
                ? "Each layer also holds everything above it, a little larger, so sheets cut from the layers glue up with no gaps."
                : "Each layer holds only its own colour. For inlay and for engraving."}
            </p>
          </div>
        </div>

        {/* what the studio sends: the server's words, editable; Rebuild takes the edit away; the negative text is fixed */}
        <div className="field vector-recipe">
          <label htmlFor={idOf("recipe")}>What the studio sends</label>
          <textarea id={idOf("recipe")} className="prompt-input" rows={6} value={recipeShown} maxLength={limits.recipe_max_chars} aria-busy={recipe.loading}
            aria-describedby={idOf("recipe-hint")} placeholder="Write your idea above, and the words the studio sends to the model appear here."
            onChange={(e) => change((current) => editRecipe(current, e.target.value))} />
          <div className="vector-recipe-row" id={idOf("recipe-hint")}>
            <p className="field-hint">
              {form.recipeEdited
                ? "You changed this by hand, so the prompt, the kind and the colours no longer rewrite it."
                : recipe.failed ? `Couldn't write these words just now (${recipe.failed.message}). The studio writes them again when you send.`
                : recipe.loading ? "Writing these words…"
                : "Written by the studio from your prompt, the kind and the colours. Change it by hand if you like."}
              {recipeShown.length > limits.recipe_max_chars * 0.8 ? ` ${recipeShown.length} / ${limits.recipe_max_chars}` : ""}
            </p>
            <button type="button" className="button small" disabled={!form.recipeEdited} onClick={() => change(rebuildRecipe)}
              title="Have the studio write these words again, replacing your changes">
              Rebuild
            </button>
          </div>
          <label htmlFor={idOf("negative")} className="vector-negative-label">What the studio keeps out (fixed)</label>
          <textarea id={idOf("negative")} className="prompt-input vector-negative" rows={2} readOnly value={negative ?? ""} placeholder="Shown once the studio has written the words." />
        </div>

        {/* versions and seed, as on the Images tab */}
        <div className="vector-settings">
          <div className="field">
            <span className="field-label" id={idOf("versions")}>Versions</span>
            <div className="segmented" role="radiogroup" aria-labelledby={idOf("versions")}>
              {Array.from({ length: limits.versions.max - limits.versions.min + 1 }, (_, i) => limits.versions.min + i).map((n) => (
                <button key={n} type="button" role="radio" aria-checked={form.numImages === n} onClick={() => update({ numImages: n })}>{n}</button>
              ))}
            </div>
            <p className="field-hint">Different drawings of the same prompt, made one after another.</p>
          </div>
          <div className="field">
            <label className="choice">
              <input type="checkbox" checked={form.seedLocked} onChange={(e) => update({ seedLocked: e.target.checked })} />
              Lock seed
            </label>
            <NumberField label="Seed" value={form.seed} min={caps.limits.seed.min} max={caps.limits.seed.max} disabled={!form.seedLocked} hint={seedHint}
              onChange={(seed) => update({ seed })} />
          </div>
        </div>

        {/* the settings that rarely change */}
        <details className="vector-more">
          <summary>More</summary>
          <div className="vector-more-body">
            <NumberField label="Smallest feature (mm)" value={form.minFeatureMm} min={limits.min_feature_mm.min} max={limits.min_feature_mm.max} integer={false} step={0.05}
              hint="Anything smaller, a speck or a gap or a thin neck, is removed or flagged, because it will not survive being cut." onChange={(minFeatureMm) => update({ minFeatureMm })} />
            <NumberField label="Kerf (mm)" value={form.kerfMm} min={limits.kerf_mm.min} max={limits.kerf_mm.max} integer={false} step={0.05}
              hint="0 leaves the shapes as drawn. Above 0 every layer is offset by half of it, to make up for the width of the cut." onChange={(kerfMm) => update({ kerfMm })} />
            <NumberField label="Bleed (mm)" value={form.bleedMm} min={limits.bleed_mm.min} max={limits.bleed_mm.max} integer={false} step={0.1} disabled={!bleedApplies(form)}
              hint={bleedApplies(form) ? "How much each stacked layer grows over the one above it." : "Used only when the layers are stacked."} onChange={(bleedMm) => update({ bleedMm })} />
          </div>
        </details>

        <div className="prompt-row">
          <p className="prompt-help">Ctrl + Enter (⌘ + Enter on a Mac) makes the vector.</p>
          <div className="prompt-row-end">
            <button type="button" className="button primary" data-action="make" onClick={() => void submit()} disabled={!ready || submitting}>
              <VectorIcon />
              {submitting ? "Sending…" : "Make vector"}
            </button>
          </div>
        </div>
        {problem && <p className="form-error" role="alert">{problem}</p>}
      </section>
    );
  })();

  return (
    <div className="tab-panel" role="tabpanel" id={panelId("vector")} aria-labelledby={tabId("vector")} hidden={hidden}>
      {body}
      {usable && <QueueBar status={status} />}
      {filterBar}
      <section className="timeline" aria-label="Your vectors">
        {!runsReady ? (
          loadProblem ?? <p className="loading" role="status">Loading your vectors…</p>
        ) : runs.length === 0 ? (
          empty
        ) : (
          // A Vector run is binned and restored whole: the Deleted view never shows a card of deleted versions for it (§31.12.6), so every run is an ordinary card
          runs.map((run) => (
            <VectorCard key={run.id} run={run} now={now} workerState={status?.worker.state ?? null} transient={isTransient(run)} viewScope={viewScope} projects={projects}
              pngRange={range} onReuse={() => reuse(run)} onRetry={() => void retry(run)} onCancel={() => onCancel(run)} onToggleKeep={() => onToggleKeep(run)}
              onRestore={() => onRestore(run)} onDelete={() => onDelete(run)} onCopySvg={(item) => void copySvg(item)} />
          ))
        )}
        {more && (
          <button type="button" className="button load-more" onClick={onLoadOlder} disabled={loadingOlder}>
            {loadingOlder ? "Loading…" : "Load older runs"}
          </button>
        )}
      </section>
    </div>
  );
}
