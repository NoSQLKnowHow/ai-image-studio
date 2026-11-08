import type { AnswerContent, ChatMessage, Detail, DiffRun, Draft, ForKind, Question, ReplyEvent } from "./builderTypes";
import { AUTO, DEFAULT_GUIDANCE, buildEditRequest, buildRequest, draftRequest, type Options } from "./options";
import type { Capabilities, CreateRunBody, InputRef, PromptRef, PromptRunRef, WithPromptRef } from "./types";

// The Prompt builder's rules (DESIGN.md §37), as functions of plain values so that they can be tested without a browser: how chips are chosen, what a reply is doing, which
// words changed, and what using a prompt in another tab sets. The components only draw what these say.

export const FOR_LABELS: Record<ForKind, string> = { images: "Images", vector: "Vector", music: "Music", edit: "Edit" };
export const FOR_KINDS: ForKind[] = ["images", "vector", "music", "edit"];
export const DETAIL_LABELS: Record<Detail, string> = { short: "Short", normal: "Normal", rich: "Rich" };
export const DETAILS: Detail[] = ["short", "normal", "rich"];

/** The words the page, the tests and the browser tests all use. */
export const WORDS = {
  notSetUp: "The Prompt builder needs an Ollama server. Set STUDIO_OLLAMA_URL and STUDIO_OLLAMA_MODEL in .env, then restart.",
  nothingChanged: "Nothing changed.",
  waiting: "Waiting for another reply…",
  busyImages: "An image is being made, so replies may be slower.",
  retrying: "Asking again…",
  thinking: "Thinking…",
  stopped: "stopped",
  noVision: "The model can't look at pictures, so pictures can't be attached.",
};

// ------------------------------------------------------------------ what a reply is doing
export type ReplyPhase = "idle" | "waiting" | "working" | "thinking" | "writing" | "retrying";

export interface ReplyState {
  phase: ReplyPhase;
  busyImages: boolean; // an image is being made, so this may be slower
  thinking: string; // the reasoning so far: shown, never kept
  seconds: number; // since the reply began
  text: string; // the reply's own words so far
  context: { preferences: number; examples: { id: string; title: string }[]; dropped: number } | null;
}

export const IDLE_REPLY: ReplyState = { phase: "idle", busyImages: false, thinking: "", seconds: 0, text: "", context: null };

/** What a reply's events do to what the page shows while it is written. `result`, `stopped` and `error` end it: the caller replaces this with `IDLE_REPLY`. */
export function nextReplyState(state: ReplyState, event: ReplyEvent): ReplyState {
  switch (event.name) {
    case "waiting":
      return { ...state, phase: "waiting" };
    case "busy":
      return { ...state, busyImages: event.images };
    case "start":
      return { ...state, phase: "working" };
    case "context":
      return { ...state, context: { preferences: event.preferences, examples: event.examples, dropped: event.dropped } };
    case "thinking":
      return { ...state, phase: "thinking", thinking: state.thinking + event.text, seconds: event.seconds };
    case "delta":
      return { ...state, phase: "writing", text: event.text };
    case "retry":
      return { ...state, phase: "retrying", text: "", thinking: "" };
    default:
      return state;
  }
}

export const replying = (state: ReplyState): boolean => state.phase !== "idle";

// ------------------------------------------------------------------ answering the questions (criterion 224, 225)
export type PickMode = "chips" | "surprise" | "skip";
export type Picks = Record<string, { mode: PickMode; chips: string[] }>;

/** Tap a chip to choose it, tap it again to undo. Several are fine. Choosing a chip takes back "Surprise me" or "Skip" on that question. */
export function toggleChip(picks: Picks, id: string, chip: string): Picks {
  const current = picks[id];
  const chips = current?.mode === "chips" ? current.chips : [];
  const next = chips.includes(chip) ? chips.filter((c) => c !== chip) : [...chips, chip];
  const { [id]: _gone, ...rest } = picks;
  return next.length ? { ...rest, [id]: { mode: "chips", chips: next } } : rest;
}

/** "Surprise me" and "Skip" replace the chips of that question; pressing the same one again takes it back. */
export function chooseMode(picks: Picks, id: string, mode: "surprise" | "skip"): Picks {
  const { [id]: current, ...rest } = picks;
  return current?.mode === mode ? rest : { ...rest, [id]: { mode, chips: [] } };
}

/** The answers to send: one for each question the person has chosen something for, in the order asked. */
export function answersFor(picks: Picks, questions: Question[]): { id: string; chips?: string[]; mode: PickMode }[] {
  return questions.flatMap((q) => {
    const pick = picks[q.id];
    if (!pick) return [];
    return [pick.mode === "chips" ? { id: q.id, chips: pick.chips, mode: pick.mode } : { id: q.id, mode: pick.mode }];
  });
}

/** Whether Send can be pressed: words, a chosen answer or a waiting picture, and no reply already being written. */
export function canSend(input: { text: string; picks: Picks; pictures: number; busy: boolean }): boolean {
  return !input.busy && (input.text.trim() !== "" || Object.keys(input.picks).length > 0 || input.pictures > 0);
}

// ------------------------------------------------------------------ reading a chat
export const isAnswer = (message: ChatMessage): message is ChatMessage & { content: AnswerContent } => message.role === "assistant" && message.content !== null;

/** The questions still waiting for an answer: those of the model's last message, if it is the last message of the chat (once the person writes again they are answered). */
export function openQuestions(messages: ChatMessage[]): Question[] {
  const last = messages[messages.length - 1];
  return last && isAnswer(last) && !last.content.stopped ? last.content.questions : [];
}

/** The newest draft in the chat, whether the model wrote it or the person edited it by hand (their message carries it). */
export function newestDraft(messages: ChatMessage[]): Draft | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const draft = (messages[i].content as { draft?: Draft | null } | null)?.draft;
    if (draft && draft.prompt.trim()) return draft;
  }
  return null;
}

/** The draft before the last reply, to show what that reply changed. */
export function previousDraft(messages: ChatMessage[]): Draft | null {
  let seen = 0;
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    const draft = (message.content as { draft?: Draft | null } | null)?.draft;
    if (message.role === "assistant" && draft && draft.prompt.trim() && ++seen === 2) return draft;
  }
  return null;
}

// ------------------------------------------------------------------ the words that changed (criterion 226)
/** The change from one text to another as runs of words: the words kept, removed and added, in reading order (the same rule as the server's compare). */
export function wordDiff(before: string | null | undefined, after: string | null | undefined): DiffRun[] {
  const a = (before ?? "").split(/\s+/).filter(Boolean);
  const b = (after ?? "").split(/\s+/).filter(Boolean);
  const n = a.length;
  const m = b.length;
  const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const runs: DiffRun[] = [];
  let del: string[] = [];
  let add: string[] = [];
  const flush = () => {
    if (del.length) runs.push({ op: "del", text: del.join(" ") });
    if (add.length) runs.push({ op: "add", text: add.join(" ") });
    del = [];
    add = [];
  };
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      flush();
      const last = runs[runs.length - 1];
      if (last?.op === "same") last.text += " " + a[i];
      else runs.push({ op: "same", text: a[i] });
      i++;
      j++;
    } else if (j < m && (i === n || lcs[i][j + 1] >= lcs[i + 1][j])) add.push(b[j++]);
    else del.push(a[i++]);
  }
  flush();
  return runs;
}

export const changedIn = (runs: DiffRun[]): boolean => runs.some((r) => r.op !== "same");

/** The length meter against the studio's limit: the words, whether it is near, and whether it is over (the prompt cannot be used then). */
export function lengthMeter(length: number, limit: number): { text: string; near: boolean; over: boolean } {
  return { text: `${length} / ${limit}`, near: length > limit * 0.8, over: length > limit };
}

// ------------------------------------------------------------------ using a prompt (criteria 235, 236, 239)
export type SettingKey = "shape" | "guidance" | "negative";

/** The settings a prompt has that fit the Images form, as small switches the person can turn off: the shape, the guidance, and the negative prompt. */
export function settingChips(kind: ForKind, draft: Pick<Draft, "negative" | "settings">, caps: Capabilities): { key: SettingKey; label: string }[] {
  if (kind !== "images" && kind !== "edit") return [];
  const chips: { key: SettingKey; label: string }[] = [];
  const shape = draft.settings.shape;
  if (typeof shape === "string" && (shape in caps.aspect_ratios || (kind === "edit" && shape === AUTO))) chips.push({ key: "shape", label: shape === AUTO ? "Size: Auto" : `Shape ${shape}` });
  const guidance = draft.settings.guidance;
  const { min, max } = caps.limits.cfg_scale;
  if (typeof guidance === "number" && guidance >= min && guidance <= max && caps.supports.cfg_scale) chips.push({ key: "guidance", label: `Guidance ${guidance}` });
  if (draft.negative?.trim() && caps.supports.negative_prompt) chips.push({ key: "negative", label: "Negative prompt" });
  return chips;
}

/** The Images options with a prompt's settings laid over them, except those switched off. Returns the options and a word for each setting that was set (for the toast).
 *  A negative prompt does nothing at guidance 1 or below, so setting one also sets the studio's usual guidance when the form has none (and says so). */
export function overlayOptions(
  options: Options, kind: ForKind, draft: Pick<Draft, "negative" | "settings">, off: ReadonlySet<SettingKey>, caps: Capabilities,
): { options: Options; applied: string[] } {
  const next = { ...options };
  const applied: string[] = [];
  for (const chip of settingChips(kind, draft, caps)) {
    if (off.has(chip.key)) continue;
    if (chip.key === "shape") {
      const shape = draft.settings.shape as string;
      if (kind === "edit") next.editAspect = shape;
      else next.aspect = shape;
      applied.push(shape === AUTO ? "Size on Auto" : shape);
    } else if (chip.key === "guidance") {
      next.guidance = draft.settings.guidance as number;
      applied.push(`guidance ${next.guidance}`);
    } else {
      next.negativePrompt = draft.negative!.trim();
      applied.push("a negative prompt");
      if (next.guidance === null || next.guidance <= 1) {
        next.guidance = DEFAULT_GUIDANCE;
        if (!applied.includes(`guidance ${DEFAULT_GUIDANCE}`)) applied.push(`guidance ${DEFAULT_GUIDANCE}`);
      }
    }
  }
  return { options: next, applied };
}

/** The toast after Use in Images (criterion 235): what was filled and set. Undo is the toast's button. */
export function usedText(applied: string[]): string {
  if (applied.length === 0) return "Filled the prompt.";
  const list = applied.length === 1 ? applied[0] : `${applied.slice(0, -1).join(", ")} and ${applied[applied.length - 1]}`;
  return `Filled the prompt, set ${list}.`;
}

/** The request for Generate now and Quick try (criterion 236, 239): the Images tab's current options with the prompt's settings over them, nothing in the form changed.
 *  A quick try is a Draft, within Draft's limits (the studio's own `draftRequest`). Both carry the prompt and version they are made from. For Edit, `inputs` are the tray's pictures. */
export function promptRun(
  kind: "images" | "edit", text: string, options: Options, caps: Capabilities, ref: PromptRunRef,
  extra: { quick?: boolean; inputs?: InputRef[]; shapeFrom?: number | null } = {},
): WithPromptRef<CreateRunBody> {
  const body = kind === "edit" ? buildEditRequest(text, options, caps, extra.inputs ?? [], extra.shapeFrom ?? null)
    : extra.quick ? draftRequest(text, options, caps) : buildRequest(text, options, caps);
  return { ...body, prompt_ref: ref };
}

/** Why a prompt has no quick try (criterion 239), or null when it has: only an Images prompt does, and only if the studio can draft. */
export function quickTryProblem(kind: ForKind): string | null {
  return kind === "images" ? null : `A quick try is a Draft, and drafts are for Images. ${kind === "edit" ? "Edits" : kind === "vector" ? "Vector" : "Music"} has none.`;
}

// ------------------------------------------------------------------ where a run came from (criterion 237)
/** The line on a run made from a prompt: `From "Lighthouse" v3`, with `edited` when the words sent differ, and `deleted` when the prompt is in the bin or gone. */
export function refLine(ref: PromptRef): string {
  const name = ref.title === null ? "a deleted prompt" : `"${ref.title}"`;
  const tail = [ref.edited ? "edited" : "", ref.deleted && ref.title !== null ? "deleted" : ""].filter(Boolean);
  return `From ${name}${ref.title === null ? "," : ""} v${ref.version}${tail.length ? `, ${tail.join(", ")}` : ""}`;
}

/** The chip beside the Images prompt box (criterion 235, rule 3). */
export function chipLine(ref: { title: string; version: number }): string {
  return `From Prompt builder: "${ref.title}" v${ref.version}`;
}

/** A chat's title for the list: its name, or the first words. */
export const chatTitle = (title: string): string => title.trim() || "New chat";
