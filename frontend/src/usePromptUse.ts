import { useCallback, useState } from "react";
import { ApiError, api } from "./api";
import { overlayOptions, usedText, promptRun, chipLine } from "./builder";
import type { Using } from "./components/BuilderPanel";
import type { ToastAction, ToastKind } from "./hooks";
import { buildMusicRequest, loadMusicForm, musicProblem, type TabId } from "./music";
import { optionsProblem, type KeyValueStore, type Options } from "./options";
import { fillMusic, fillVector } from "./promptUse";
import { inputRefs, shapeFromForRequest, submitBlock, type TrayItem } from "./tray";
import type { Capabilities, ImageRun, PromptRunRef, Run } from "./types";
import { buildVectorRequest, loadVectorForm, vectorProblem } from "./vector";

// Using a prompt from the Prompt builder in the other tabs (DESIGN.md §37.2, §37.3 rule 3; criteria 235, 236, 239, 242): fill the Images prompt box (with an Undo), hand the Vector and
// Music forms what to fill, queue a run at once from a prompt (Generate now), or make a quick try (a Draft). It also holds the "From Prompt builder" chip that makes the next
// Generate carry the prompt it came from. Only a button the person presses queues a run (rule 11): nothing here runs by itself.

export interface FromPrompt {
  promptId: string;
  version: number;
  title: string;
}

/** What a Vector or Music panel is asked to fill: a new `seq` each time, so asking twice with the same prompt fills twice. */
export interface FillRequest {
  seq: number;
  using: Using;
}

export const refOf = (from: FromPrompt | null): PromptRunRef | null => (from ? { prompt_id: from.promptId, version: from.version } : null);
export { chipLine };

interface Deps {
  from: FromPrompt | null; // the chip beside the Images prompt box; the page keeps it, because Generate and Reuse read it too
  setFrom: (from: FromPrompt | null) => void;
  caps: Capabilities | null;
  options: Options | null;
  setOptions: (options: Options) => void;
  prompt: string;
  setPrompt: (text: string) => void;
  clearProblem: () => void;
  tray: { items: TrayItem[]; shapeKey: string | null; adoptRun: (run: ImageRun) => void };
  store: KeyValueStore;
  push: (kind: ToastKind, text: string, action?: ToastAction) => void;
  showTab: (tab: TabId) => void;
  upsert: (run: Run) => void;
  focusPrompt: () => void;
}

const failure = (error: unknown, what: string): string => (error instanceof ApiError ? (error.status === 429 ? error.message : `${what}: ${error.message}`) : `${what}.`);

export function usePromptUse(deps: Deps) {
  const { from, setFrom, caps, options, setOptions, prompt, setPrompt, clearProblem, tray, store, push, showTab, upsert, focusPrompt } = deps;
  const [vectorFill, setVectorFill] = useState<FillRequest | null>(null);
  const [musicFill, setMusicFill] = useState<FillRequest | null>(null);

  /** Use in …: put the prompt in the other tab's form. Nothing is queued. */
  const useIn = useCallback((using: Using) => {
    if (!caps || !options) return;
    if (using.kind === "vector") {
      setVectorFill((n) => ({ seq: (n?.seq ?? 0) + 1, using }));
      showTab("vector");
      return;
    }
    if (using.kind === "music") {
      setMusicFill((n) => ({ seq: (n?.seq ?? 0) + 1, using }));
      showTab("music");
      return;
    }
    if (using.kind === "edit" && !caps.modes.includes("edit")) {
      push("error", "Edit isn't available: the loaded pipeline can't edit images.");
      return;
    }
    const before = { prompt, options, from };
    const { options: next, applied } = overlayOptions({ ...options, mode: using.kind === "edit" ? "edit" : "generate" }, using.kind, using.draft, using.off, caps);
    setPrompt(using.draft.prompt);
    setOptions(next);
    clearProblem();
    setFrom(using.from);
    showTab("images");
    push("success", usedText(applied), { label: "Undo", run: () => { setPrompt(before.prompt); setOptions(before.options); setFrom(before.from); clearProblem(); } });
    focusPrompt();
  }, [caps, options, prompt, from, setFrom, setPrompt, setOptions, clearProblem, showTab, push, focusPrompt]);

  /** Generate now and Quick try: queue a run from the prompt at once, with the tab's own options and the prompt's settings over them, without changing the form. */
  const generate = useCallback(async (using: Using, quick: boolean) => {
    if (!caps || !options) return;
    const ref = refOf(using.from);
    const label = using.from ? ` from “${using.from.title}” v${using.from.version}` : "";
    try {
      if (using.kind === "images" || using.kind === "edit") {
        if (using.kind === "edit" && !caps.modes.includes("edit")) return push("error", "Edit isn't available: the loaded pipeline can't edit images.");
        if (!using.draft.prompt.trim()) return push("error", "There is no prompt to make a picture from yet.");
        const { options: laid } = overlayOptions({ ...options, mode: using.kind === "edit" ? "edit" : "generate" }, using.kind, using.draft, using.off, caps);
        const problem = optionsProblem(laid, caps);
        if (problem) return push("error", `Check the options on the Images tab: ${problem}`);
        const extra: Parameters<typeof promptRun>[5] = { quick };
        if (using.kind === "edit") {
          const block = submitBlock(tray.items);
          if (block) return push("error", block);
          extra.inputs = inputRefs(tray.items);
          extra.shapeFrom = shapeFromForRequest(tray.items, tray.shapeKey);
        }
        const run = await api.createRun(promptRun(using.kind, using.draft.prompt, laid, caps, ref, extra));
        upsert(run);
        if (using.kind === "edit") tray.adoptRun(run);
        if (quick) push("info", `Quick try queued${label}. Its picture shows on the version when it is done.`);
        else { showTab("images"); push("info", `Queued a run${label}. Your Images form is unchanged.`); }
      } else if (using.kind === "vector") {
        const { form } = fillVector(loadVectorForm(caps, store).form, using.draft, caps);
        const problem = vectorProblem(form, caps);
        if (problem) return push("error", `Check the Vector form: ${problem}`);
        upsert(await api.createVectorRun({ ...buildVectorRequest(form, caps), ...(ref ? { prompt_ref: ref } : {}) }));
        showTab("vector");
        push("info", `Queued a run${label}. Your Vector form is unchanged.`);
      } else {
        const { form } = fillMusic(loadMusicForm(caps, store).form, using.draft, caps);
        const problem = musicProblem(form, caps);
        if (problem) return push("error", `Check the Music fields: ${problem}`);
        upsert(await api.createMusicRun({ ...buildMusicRequest(form), ...(ref ? { prompt_ref: ref } : {}) }));
        showTab("music");
        push("info", `Queued a run${label}. Your Music fields are unchanged.`);
      }
    } catch (error) {
      push("error", failure(error, "Couldn't start the run"));
    }
  }, [caps, options, tray, store, push, showTab, upsert]);

  return { vectorFill, musicFill, useIn, generate };
}
