import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "./api";
import { IDLE_REPLY, answersFor, newestDraft, nextReplyState, replying, type Picks, type ReplyState } from "./builder";
import { builderApi } from "./builderApi";
import type { Attachment, BuilderStatus, BuilderTest, ChatInfo, ChatMessage, Detail, Draft, ForKind, Intent, MessageBody, Question, ReplyEvent } from "./builderTypes";

// The state of the Prompt builder's chat (DESIGN.md §37.2): the connection, the chat that is open, the reply being written, and what the page has not sent yet. It lives in
// the panel, which stays mounted when another tab is shown, so a reply goes on being written while you look at Images.

export type Api = typeof builderApi;

export interface ReplyProblem {
  message: string;
  hint: string | null;
  code: string | null;
}

export interface Blank {
  for: ForKind;
  detail: Detail;
  project_id: string | null;
  prompt_id: string | null;
}

export const BLANK: Blank = { for: "images", detail: "normal", project_id: null, prompt_id: null };

export interface SendInput {
  text?: string;
  picks?: Picks;
  questions?: Question[];
  intent?: Intent;
  draft?: Draft | null; // the draft as the person has it now, when they edited it
  think?: boolean | null;
  similar?: boolean;
  exclude?: string[];
  runId?: string | null;
  retry?: boolean;
  redo?: boolean;
}

const problemOf = (error: unknown): ReplyProblem => {
  if (error instanceof ApiError) return { message: error.message, hint: error.hint, code: error.code };
  return { message: "Something went wrong.", hint: null, code: null };
};

const firstWords = (text: string): string => text.split(/\s+/).filter(Boolean).slice(0, 12).join(" ").slice(0, 60);

export function useBuilder(api: Api = builderApi, onChanged: () => void = () => undefined) {
  const [status, setStatus] = useState<BuilderStatus | null>(null);
  const [statusProblem, setStatusProblem] = useState<string | null>(null);
  const [test, setTest] = useState<BuilderTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [unloading, setUnloading] = useState(false);

  const [chat, setChat] = useState<ChatInfo | null>(null); // null: a new chat that has not been made yet (it is made with its first message)
  const [blank, setBlank] = useState<Blank>(BLANK);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<Attachment[]>([]);
  const [preferences, setPreferences] = useState(0);
  const [opening, setOpening] = useState(false);
  const [openProblem, setOpenProblem] = useState<string | null>(null);

  const [reply, setReply] = useState<ReplyState>(IDLE_REPLY);
  const [replyChat, setReplyChat] = useState<string | null>(null); // the chat the reply is being written in
  const [problem, setProblem] = useState<ReplyProblem | null>(null);
  const [picking, setPicking] = useState(false); // a picture is on its way up
  const [edited, setEdited] = useState<Draft | null>(null); // the prompt so far, as the person has changed it by hand and not yet sent

  const viewing = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;

  // ------------------------------------------------------------------ the connection
  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.status());
      setStatusProblem(null);
    } catch (error) {
      setStatusProblem(problemOf(error).message);
    }
  }, [api]);

  const runTest = useCallback(async () => {
    setTesting(true);
    try {
      setTest(await api.test());
      await refreshStatus();
    } catch (error) {
      setStatusProblem(problemOf(error).message);
    } finally {
      setTesting(false);
    }
  }, [api, refreshStatus]);

  const unload = useCallback(async (): Promise<{ ok: boolean; message: string }> => {
    setUnloading(true);
    try {
      const done = await api.unload();
      return { ok: true, message: done.unloaded.length ? `Asked Ollama to unload ${done.unloaded.join(" and ")} from memory.` : "Nothing was loaded." };
    } catch (error) {
      return { ok: false, message: problemOf(error).message };
    } finally {
      setUnloading(false);
    }
  }, [api]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  // ------------------------------------------------------------------ opening and starting chats
  const startNew = useCallback((over: Partial<Blank> = {}) => {
    viewing.current = null;
    setChat(null);
    setBlank({ ...BLANK, ...over });
    setMessages([]);
    setPending([]);
    setPreferences(0);
    setOpenProblem(null);
    setProblem(null);
    setEdited(null);
  }, []);

  const open = useCallback(async (id: string) => {
    setOpening(true);
    setOpenProblem(null);
    try {
      const full = await api.getChat(id);
      viewing.current = id;
      setChat(full.chat);
      setMessages(full.messages);
      setPending(full.pending);
      setPreferences(full.preferences);
      setProblem(null);
      setEdited(null);
    } catch (error) {
      setOpenProblem(problemOf(error).message);
    } finally {
      setOpening(false);
    }
  }, [api]);

  /** The chat to write in: the open one, or a new one made now from what the blank chat has chosen. */
  const ensureChat = useCallback(async (title: string): Promise<ChatInfo> => {
    if (chat) return chat;
    const made = await api.createChat({ title, for: blank.for, detail: blank.detail, project_id: blank.project_id, prompt_id: blank.prompt_id });
    viewing.current = made.id;
    setChat(made);
    changed.current();
    return made;
  }, [api, blank, chat]);

  // ------------------------------------------------------------------ a reply
  const send = useCallback(async (input: SendInput = {}) => {
    const text = (input.text ?? "").trim();
    let target: ChatInfo;
    try {
      target = await ensureChat(firstWords(text) || "New chat");
    } catch (error) {
      setProblem(problemOf(error));
      return;
    }
    const questions = input.questions ?? [];
    const answers = answersFor(input.picks ?? {}, questions);
    const body: MessageBody = input.retry
      ? { retry: true, ...(input.redo ? { redo: true } : {}), ...(input.exclude ? { exclude: input.exclude } : {}) }
      : {
          text, answers, intent: input.intent ?? "message", think: input.think ?? null, attachments: pending.map((a) => a.id), similar: input.similar ?? true,
          exclude: input.exclude ?? [], ...(input.draft ? { draft: input.draft } : {}), ...(input.runId ? { run_id: input.runId } : {}),
        };
    setEdited(null); // what was edited goes with this message, and the model's answer is the new draft
    if (input.retry) {
      setProblem(null);
      if (input.redo) setMessages((all) => (all[all.length - 1]?.role === "assistant" ? all.slice(0, -1) : all));
    } else {
      // the person's words are on the screen at once, with the answers they chose; the server confirms them with `start`
      const questionOf = new Map(questions.map((q) => [q.id, q.text]));
      const shown: ChatMessage = {
        seq: -1, role: "user", text, model: null, created_at: new Date().toISOString(), attachments: pending,
        content: { answers: answers.map((a) => ({ id: a.id, question: questionOf.get(a.id) ?? a.id, mode: a.mode, chips: a.chips ?? [] })), intent: input.intent ?? "message", draft: input.draft ?? null, think: input.think ?? null },
      };
      setMessages((all) => [...all, shown]);
      setPending([]);
      setProblem(null);
    }
    const controller = new AbortController();
    abort.current = controller;
    setReplyChat(target.id);
    setReply({ ...IDLE_REPLY, phase: "working" });
    let finished = false;
    const here = () => viewing.current === target.id;
    const onEvent = (event: ReplyEvent) => {
      if (event.name === "result" || event.name === "stopped") {
        finished = true;
        if (here()) {
          const message = event.message;
          if (message) setMessages((all) => [...all.filter((m) => m.seq !== message.seq), message]);
          if (event.chat) setChat(event.chat);
        }
        changed.current();
      } else if (event.name === "error") {
        finished = true;
        if (here()) setProblem({ message: event.message, hint: event.hint, code: event.code });
      } else if (event.name === "start") {
        if (here()) setMessages((all) => all.map((m) => (m.seq === -1 ? { ...m, seq: event.seq } : m)));
      } else if (here()) {
        setReply((state) => nextReplyState(state, event));
      }
    };
    try {
      await api.send(target.id, body, onEvent, controller.signal);
      if (!finished && here() && !controller.signal.aborted) {
        // the stream ended without an ending: ask the server what it has
        await open(target.id);
        setProblem({ message: "The connection was lost before the reply finished.", hint: "Retry sends your message again.", code: "connection_lost" });
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError") && here()) {
        setProblem(problemOf(error));
        if (error instanceof ApiError && error.status === 0) await open(target.id);
      }
    } finally {
      if (abort.current === controller) abort.current = null;
      setReply(IDLE_REPLY);
      setReplyChat(null);
    }
  }, [api, ensureChat, open, pending]);

  const retry = useCallback(() => send({ retry: true }), [send]);

  const stop = useCallback(async () => {
    if (!chat) return;
    try {
      await api.stopReply(chat.id);
    } catch {
      /* nothing was being written any more */
    }
  }, [api, chat]);

  // a page that is closed (or an app that goes away) closes the connection, which stops the reply and keeps none of it
  useEffect(() => () => abort.current?.abort(), []);

  // ------------------------------------------------------------------ pictures (criterion 244)
  const attach = useCallback(async (source: { file: Blob; name?: string } | { imageId: string }) => {
    setPicking(true);
    try {
      const target = await ensureChat("New chat");
      const added = "file" in source ? await api.attachFile(target.id, source.file, source.name) : await api.attachImage(target.id, source.imageId);
      setPending((all) => [...all, added]);
      return null;
    } catch (error) {
      return problemOf(error).message;
    } finally {
      setPicking(false);
    }
  }, [api, ensureChat]);

  const unattach = useCallback(async (id: string) => {
    setPending((all) => all.filter((a) => a.id !== id));
    try {
      await api.removeAttachment(id);
    } catch {
      /* it was gone already */
    }
  }, [api]);

  // ------------------------------------------------------------------ the chat's own settings
  const change = useCallback(async (fields: { title?: string; for?: ForKind; detail?: Detail; project_id?: string | null; prompt_id?: string | null }) => {
    if (!chat) {
      setBlank((b) => ({ ...b, ...(fields.for ? { for: fields.for } : {}), ...(fields.detail ? { detail: fields.detail } : {}), ...("project_id" in fields ? { project_id: fields.project_id ?? null } : {}),
        ...("prompt_id" in fields ? { prompt_id: fields.prompt_id ?? null } : {}) }));
      return null;
    }
    try {
      const updated = await api.patchChat(chat.id, fields);
      setChat(updated);
      changed.current();
      return null;
    } catch (error) {
      return problemOf(error).message;
    }
  }, [api, chat]);

  const draft = useMemo(() => newestDraft(messages), [messages]);
  const replyHere = chat && replyChat === chat.id ? reply : IDLE_REPLY;

  return {
    status, statusProblem, refreshStatus, test, testing, runTest, unloading, unload,
    chat, blank, messages, pending, preferences, opening, openProblem, draft,
    reply: replyHere, replying: replying(replyHere), writing: replyChat !== null, problem, picking,
    editedDraft: edited, setEdited, startNew, open, send, retry, stop, attach, unattach, change, setProblem,
  };
}

export type BuilderState = ReturnType<typeof useBuilder>;
