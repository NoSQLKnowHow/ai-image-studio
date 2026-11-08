import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { AnswerContent, ChatFull, ChatInfo, ChatListItem, ChatMessage, Draft, ReplyEvent } from "./builderTypes";
import { BuilderPanel, type BuilderPanelProps } from "./components/BuilderPanel";
import { CAPS } from "./testdata";
import type { Api } from "./useBuilder";

// Helpers for the Prompt builder's component tests: a fake API that plays the server's replies, and the messages a chat holds.

export const info = (over: Partial<ChatInfo> = {}): ChatInfo => ({ id: "c1", title: "A lighthouse", for: "images", project_id: null, detail: "normal", prompt_id: null, created_at: "t", updated_at: "t", deleted_at: null, ...over });
export const answerContent = (over: Partial<AnswerContent> = {}): AnswerContent => ({
  reply: "Lovely idea.", title: "Lighthouse", questions: [], draft: null, variations: [], remember: [], tags: [], done: false, notices: [], plain: false, ...over,
});
export const reply = (seq: number, over: Partial<AnswerContent> = {}): ChatMessage => ({ seq, role: "assistant", text: over.reply ?? "Lovely idea.", model: "m", created_at: "t", attachments: [], content: answerContent(over) });
export const person = (seq: number, text: string): ChatMessage => ({ seq, role: "user", text, model: null, created_at: "t", attachments: [], content: { answers: [], intent: "message", draft: null, think: null } });
export const draft = (prompt: string, over: Partial<Draft> = {}): Draft => ({ prompt, negative: null, settings: {}, note: "", ...over });
export const full = (messages: ChatMessage[], over: Partial<ChatFull> = {}): ChatFull => ({ chat: info(), messages, draft: null, pending: [], preferences: 0, ...over });
export const QUESTIONS = [{ id: "q1", text: "What time of day?", chips: ["Dusk", "Midnight", "Dawn"] }, { id: "q2", text: "What style?", chips: ["Photograph", "Watercolour", "Flat"] }];

export type Mock = { [K in keyof Api]: ReturnType<typeof vi.fn> };
export const STATUS = { configured: true, reachable: true, model: "qwen3.8:27b", vision: true, think: "on" as const, message: null, hint: null };

export function makeApi(over: Partial<Record<keyof Api, unknown>> = {}): Mock & Api {
  const api = {
    status: vi.fn(async () => STATUS),
    test: vi.fn(async () => ({ reachable: true, version: "0.99.0", models: ["qwen3.8:27b", "llama3:8b"], model: "qwen3.8:27b", model_present: true, vision: true, vision_model: "", vision_model_present: false, context_length: 262144, message: null, hint: null })),
    unload: vi.fn(async () => ({ ok: true, unloaded: ["qwen3.8:27b"] })),
    counts: vi.fn(async () => ({})),
    listChats: vi.fn(async () => ({ chats: [] as ChatListItem[] })),
    createChat: vi.fn(async (body: { title?: string }) => info({ title: body.title ?? "New chat" })),
    getChat: vi.fn(async () => full([])),
    patchChat: vi.fn(async (_id: string, body: object) => info(body as Partial<ChatInfo>)),
    binChat: vi.fn(async () => info()),
    restoreChat: vi.fn(), deleteChat: vi.fn(),
    stopReply: vi.fn(async () => ({ stopping: true })),
    send: vi.fn(async () => undefined),
    attachFile: vi.fn(async () => ({ id: "a1", url: "/a1", width: 10, height: 10, name: "x.png" })),
    attachImage: vi.fn(), removeAttachment: vi.fn(async () => ({ deleted: "a1" })),
    listPrompts: vi.fn(async () => ({ prompts: [] })),
    createPrompt: vi.fn(async (body: { title: string }) => ({ id: "p1", title: body.title })),
    getPrompt: vi.fn(), patchPrompt: vi.fn(),
    addVersion: vi.fn(async () => ({ created: true, message: "Saved as version 2.", version: 2, prompt: {} })),
    makeCurrent: vi.fn(), compare: vi.fn(), binPrompt: vi.fn(), restorePrompt: vi.fn(), deletePrompt: vi.fn(), fileRuns: vi.fn(), emptyBin: vi.fn(),
    listPreferences: vi.fn(async () => ({ preferences: [] })), addPreference: vi.fn(async (body: { text: string }) => ({ id: "f1", ...body })),
    patchPreference: vi.fn(), deletePreference: vi.fn(),
    ...over,
  };
  return api as unknown as Mock & Api;
}

/** An API whose `send` plays these events (the server's reply) when it is called. */
export const sends = (...events: ReplyEvent[]) => vi.fn(async (_id: string, _body: unknown, onEvent: (e: ReplyEvent) => void) => events.forEach(onEvent));
export const result = (message: ChatMessage, chat: ChatInfo = info()): ReplyEvent => ({ name: "result", message, chat });

export function setup(api: Mock & Api, props: Partial<BuilderPanelProps> = {}) {
  const push = vi.fn();
  const onWorking = vi.fn();
  const view = render(<BuilderPanel hidden={false} caps={CAPS} projects={[]} push={push} onWorking={onWorking} api={api} {...props} />);
  return { push, onWorking, ...view };
}
export const send = (text: string) => {
  fireEvent.change(screen.getByLabelText(/What's the idea\?|Your reply/), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /^Send$/ }));
};

