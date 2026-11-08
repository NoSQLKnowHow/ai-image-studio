import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { previousDraft, chatTitle, type SettingKey, FOR_LABELS, WORDS, quickTryProblem } from "../builder";
import { builderApi } from "../builderApi";
import type { ChatListItem, Draft, ForKind, Wording } from "../builderTypes";
import { useNarrow, useReturnFocus, type ToastAction, type ToastKind } from "../hooks";
import type { Capabilities, Project } from "../types";
import { useBuilder, type Api } from "../useBuilder";
import { BuilderChat } from "./BuilderChat";
import { ConnectionBar } from "./BuilderConnection";
import { PromptSoFar, type PromptButtons } from "./PromptSoFar";
import { SavePromptDialog, type SaveFields } from "./SavePromptDialog";

/** What the tab asks the rest of the page to do with a prompt (DESIGN.md §37.2): put it in another tab's form, or queue a run from it. They are the page's to do, since the forms
 *  and the queue are; the panel only says which prompt (the words, the settings that are switched on) and where it came from. */
export interface Using {
  kind: ForKind;
  draft: Draft;
  off: ReadonlySet<SettingKey>;
  from: { promptId: string; version: number; title: string } | null; // set when the words are those of a saved version
}

export interface Integration {
  onUse?: (using: Using) => void;
  onGenerate?: (using: Using) => void;
  onQuick?: (using: Using) => void;
}

type Push = (kind: ToastKind, text: string, action?: ToastAction) => void;

export interface BuilderPanelProps {
  hidden: boolean;
  caps: Capabilities;
  projects: Project[];
  push: Push;
  onWorking: (working: boolean) => void;
  integration?: Integration;
  api?: Api;
  tick?: number; // changes when another page changed something (builder.changed)
  attachRef?: MutableRefObject<((files: File[]) => void) | null>; // set to the way pictures that are pasted anywhere on the page reach the chat
}

function ChatList({ chats, current, onOpen, onNew, onDelete }: { chats: ChatListItem[]; current: string | null; onOpen: (id: string) => void; onNew: () => void; onDelete: (c: ChatListItem) => void }) {
  return (
    <nav className="builder-list" aria-label="Chats">
      <button type="button" className="button primary" onClick={onNew}>New chat</button>
      {chats.length === 0 && <p className="hint">No chats yet. A chat is saved from its first message.</p>}
      <ul>
        {chats.map((c) => (
          <li key={c.id} className={c.id === current ? "current" : undefined}>
            <button type="button" className="builder-list-open" aria-current={c.id === current || undefined} onClick={() => onOpen(c.id)}>
              <strong>{chatTitle(c.title)}</strong>
              <span className="hint">{FOR_LABELS[c.for]}{c.last_text ? ` · ${c.last_text}` : ""}</span>
            </button>
            <button type="button" className="icon-button" aria-label={`Delete “${chatTitle(c.title)}”`} onClick={() => onDelete(c)}>✕</button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function ConfirmBin({ ask, binDays, onCancel, onConfirm }: { ask: ChatListItem | null; binDays: number; onCancel: () => void; onConfirm: () => void }) {
  const { props: returnFocus } = useReturnFocus();
  return (
    <Dialog.Root open={!!ask} onOpenChange={(open) => !open && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm" role="alertdialog" {...returnFocus}>
          <Dialog.Title>Delete this chat?</Dialog.Title>
          <Dialog.Description>
            {binDays > 0 ? `“${chatTitle(ask?.title ?? "")}” moves to Deleted and stays there for ${binDays} ${binDays === 1 ? "day" : "days"}; you can restore it from there. After that it is gone for good, with its pictures.`
              : `“${chatTitle(ask?.title ?? "")}” and its pictures are deleted for good. This can't be undone.`}
          </Dialog.Description>
          <div className="confirm-actions">
            <Dialog.Close className="button">Cancel</Dialog.Close>
            <button type="button" className="button danger-solid" onClick={onConfirm}>Delete</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function BuilderPanel({ hidden, caps, projects, push, onWorking, integration = {}, api = builderApi, tick = 0, attachRef }: BuilderPanelProps) {
  const [chats, setChats] = useState<ChatListItem[]>([]);
  const [chatsProblem, setChatsProblem] = useState<string | null>(null);
  const refreshChats = useCallback(async () => {
    try {
      setChats((await api.listChats()).chats);
      setChatsProblem(null);
    } catch (error) {
      setChatsProblem(error instanceof Error ? error.message : "The chats could not be read.");
    }
  }, [api]);
  const builder = useBuilder(api, () => void refreshChats());
  const { chat, messages, status } = builder;
  const narrow = useNarrow();
  const [view, setView] = useState<"chat" | "prompt" | "library">("chat");
  const [off, setOff] = useState<ReadonlySet<SettingKey>>(new Set());
  const [saving, setSaving] = useState<"prompt" | "version" | null>(null);
  const [saveWith, setSaveWith] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<ChatListItem | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const limits = caps.limits.builder;

  useEffect(() => {
    void refreshChats();
  }, [refreshChats, tick]);
  useEffect(() => onWorking(builder.writing), [builder.writing, onWorking]);
  // pictures pasted anywhere on the tab go to the chat (criterion 244): a model that cannot look at pictures is told so, once
  const { attach, status: connection } = builder;
  useEffect(() => {
    if (!attachRef) return;
    attachRef.current = (files) => {
      if (connection && !connection.vision) return push("info", WORDS.noVision);
      void (async () => {
        for (const file of files) {
          const problem = await attach({ file, name: file.name });
          if (problem) return push("error", problem);
        }
      })();
    };
    return () => { attachRef.current = null; };
  }, [attachRef, attach, connection, push]);

  const kind: ForKind = chat?.for ?? builder.blank.for;
  const draft = builder.draft;
  const shown = builder.editedDraft ?? draft;
  const lastReply = [...messages].reverse().find((m) => m.role === "assistant");
  const prior = useMemo(() => previousDraft(messages), [messages]);
  const useLabel = kind === "vector" ? "Vector" : kind === "music" ? "Music" : "Images";
  const linkedId = chat?.prompt_id ?? null;

  const using = (override?: Draft): Using | null => {
    const d = override ?? shown;
    return d ? { kind, draft: d, off, from: null } : null;
  };

  // ------------------------------------------------------------------ saving (criterion 229)
  const suggestion: SaveFields = useMemo(() => {
    const content = lastReply?.content as { title?: string | null; tags?: string[] } | null | undefined;
    return { title: content?.title || chat?.title || "", tags: content?.tags ?? [], project_id: chat?.project_id ?? null, for: kind, note: (saveWith ?? shown)?.note ?? "" };
  }, [lastReply, chat, kind, saveWith, shown]);

  const sourceOf = () => (builder.editedDraft ? "hand" : "builder") as "hand" | "builder";
  const save = async (fields: SaveFields): Promise<string | null> => {
    const d = saveWith ?? shown;
    if (!d) return "There is nothing to save yet.";
    try {
      if (saving === "version" && linkedId) {
        const done = await api.addVersion(linkedId, { text: d.prompt, negative: d.negative, settings: d.settings, note: fields.note || d.note || null, source: saveWith ? "wording" : sourceOf(), chat_id: chat?.id ?? null, message_seq: lastReply?.seq ?? null });
        push(done.created ? "success" : "info", done.message);
      } else {
        const made = await api.createPrompt({
          title: fields.title, text: d.prompt, negative: d.negative, settings: d.settings, note: fields.note || null, source: saveWith ? "wording" : sourceOf(), for: fields.for,
          project_id: fields.project_id, tags: fields.tags, chat_id: chat?.id ?? null, message_seq: lastReply?.seq ?? null,
        });
        await builder.change({ prompt_id: made.id });
        push("success", `Saved “${made.title}” as a prompt, version 1.`);
      }
      setSaving(null);
      setSaveWith(null);
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : "It could not be saved.";
    }
  };

  const buttons: PromptButtons = {
    useLabel,
    onSave: () => { setSaveWith(null); setSaving("prompt"); },
    onSaveVersion: linkedId ? () => { setSaveWith(null); setSaving("version"); } : undefined,
    onUse: integration.onUse ? () => { const u = using(); if (u) integration.onUse!(u); } : undefined,
    onGenerate: integration.onGenerate ? () => { const u = using(); if (u) integration.onGenerate!(u); } : undefined,
    onQuick: integration.onQuick ? () => { const u = using(); if (u) integration.onQuick!(u); } : undefined,
    quickProblem: quickTryProblem(kind),
    useProblem: null,
  };

  const remember = async (text: string) => {
    try {
      await api.addPreference({ text, source: "suggestion", project_id: null });
      push("success", `Added “${text}” to your preferences.`);
    } catch (error) {
      push("error", error instanceof Error ? error.message : "It could not be added.");
    }
  };

  const bin = async () => {
    const ask = deleting;
    setDeleting(null);
    if (!ask) return;
    try {
      await api.binChat(ask.id);
      if (chat?.id === ask.id) builder.startNew();
      await refreshChats();
    } catch (error) {
      push("error", error instanceof Error ? error.message : "It could not be deleted.");
    }
  };

  const configured = status === null || status.configured;
  const showTabs = narrow;

  return (
    <div className="tab-panel builder-panel" role="tabpanel" id="panel-builder" aria-labelledby="tab-builder" hidden={hidden}>
      <ConnectionBar status={status} problem={builder.statusProblem} test={builder.test} testing={builder.testing} unloading={builder.unloading}
        onTest={() => void builder.runTest()} onUnload={() => void builder.unload().then((r) => push(r.ok ? "info" : "error", r.message))} />
      {configured && (
        <>
          {showTabs && (
            <div className="segmented builder-views" role="radiogroup" aria-label="Show">
              {(["chat", "prompt", "library"] as const).map((v) => (
                <button key={v} type="button" role="radio" aria-checked={view === v} onClick={() => setView(v)}>{v === "chat" ? "Chat" : v === "prompt" ? "Prompt" : "Library"}</button>
              ))}
            </div>
          )}
          <div className={`builder-columns${narrow ? " narrow" : ""}`} data-view={narrow ? view : undefined}>
            <div className="builder-col builder-col-list" hidden={narrow && view !== "library"}>
              <ChatList chats={chats} current={chat?.id ?? null} onOpen={(id) => { void builder.open(id); setView("chat"); }} onNew={() => { builder.startNew(); setView("chat"); composer.current?.focus(); }} onDelete={setDeleting} />
              {chatsProblem && <p className="form-error" role="alert">{chatsProblem}</p>}
            </div>
            <div className="builder-col builder-col-chat" hidden={narrow && view !== "chat"}>
              {builder.openProblem && <p className="form-error" role="alert">{builder.openProblem}</p>}
              <BuilderChat builder={builder} status={status} composerRef={composer}
                actions={{
                  useLabel, onRemember: remember,
                  onKeepWording: (w: Wording) => { setSaveWith({ prompt: w.prompt, negative: w.negative, settings: shown?.settings ?? {}, note: `Wording: ${w.label}` }); setSaving(linkedId ? "version" : "prompt"); },
                  onUseWording: integration.onUse ? (w: Wording) => integration.onUse!({ kind, draft: { prompt: w.prompt, negative: w.negative, settings: shown?.settings ?? {}, note: "" }, off, from: null }) : undefined,
                }} />
            </div>
            <div className="builder-col builder-col-prompt" hidden={narrow && view !== "prompt"}>
              <PromptSoFar kind={kind} draft={draft} edited={builder.editedDraft} previous={prior} caps={caps} off={off} buttons={buttons}
                onEdit={builder.setEdited} onToggleSetting={(key) => setOff((s) => { const next = new Set(s); if (next.has(key)) next.delete(key); else next.add(key); return next; })} />
            </div>
          </div>
        </>
      )}
      <SavePromptDialog open={saving !== null} suggestion={suggestion} projects={projects} limits={limits} onSave={save} onClose={() => { setSaving(null); setSaveWith(null); }} />
      <ConfirmBin ask={deleting} binDays={caps.limits.bin_days} onCancel={() => setDeleting(null)} onConfirm={() => void bin()} />
    </div>
  );
}
