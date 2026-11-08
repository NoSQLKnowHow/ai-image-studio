import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { WORDS, previousDraft, quickTryProblem, type SettingKey } from "../builder";
import { builderApi } from "../builderApi";
import type { ChatListItem, Draft, ForKind, Intent, PromptCard, PromptDetail, PromptVersion, Wording } from "../builderTypes";
import { useNarrow, useReturnFocus, type ToastAction, type ToastKind } from "../hooks";
import type { Capabilities, Project } from "../types";
import { useBuilder, type Api } from "../useBuilder";
import { useLibrary, type ListView } from "../useLibrary";
import { BuilderChat, type HistoryPicture } from "./BuilderChat";
import { ConnectionBar } from "./BuilderConnection";
import { BuilderLibrary } from "./BuilderLibrary";
import { ChatHeader } from "./ChatHeader";
import { PreferencesView } from "./PreferencesView";
import { PromptDetailView } from "./PromptDetailView";
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

/** A picture that was made, and what to say about it, for a chat that is to refine it (DESIGN.md §37.2, "Revise from a result"). */
export interface RefineTarget {
  runId: string;
  kind: ForKind;
  text: string; // the words the run used
  negative: string | null;
  imageId: string | null; // the picture, attached when the model can look at pictures
  prompt: { id: string; project_id: string | null } | null; // the saved prompt it was made from, if there still is one: the chat is linked to it
}

/** What another part of the page asks the Prompt builder to do: open a saved prompt, refine a result, or help write an edit of these pictures. A new `seq` each time. */
export type BuilderCommand =
  | { seq: number; kind: "open-prompt"; id: string }
  | { seq: number; kind: "refine"; target: RefineTarget }
  | { seq: number; kind: "help-edit"; pictures: string[] }; // the ids of the pictures in the Edit tray, in order

export type { HistoryPicture };

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
  command?: BuilderCommand | null;
  history?: HistoryPicture[]; // pictures the studio already has, to attach
}

interface Ask {
  title: string;
  body: ReactNode;
  confirm: string;
  onConfirm: () => void;
}

function Confirm({ ask, onCancel }: { ask: Ask | null; onCancel: () => void }) {
  const { props: returnFocus } = useReturnFocus();
  return (
    <Dialog.Root open={!!ask} onOpenChange={(open) => !open && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm" role="alertdialog" {...returnFocus}>
          <Dialog.Title>{ask?.title}</Dialog.Title>
          <Dialog.Description>{ask?.body}</Dialog.Description>
          <div className="confirm-actions">
            <Dialog.Close className="button">Cancel</Dialog.Close>
            <button type="button" className="button danger-solid" onClick={() => { const run = ask?.onConfirm; onCancel(); run?.(); }}>{ask?.confirm}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const daysWord = (n: number) => `${n} ${n === 1 ? "day" : "days"}`;
const whyNot = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

export function BuilderPanel({ hidden, caps, projects, push, onWorking, integration = {}, api = builderApi, tick = 0, attachRef, command = null, history = [] }: BuilderPanelProps) {
  const [listView, setListView] = useState<ListView>("chats");
  const lib = useLibrary(api, tick, listView);
  const builder = useBuilder(api, lib.everything);
  const { chat, messages, status } = builder;
  const narrow = useNarrow("(max-width: 900px)");
  const [view, setView] = useState<"chat" | "prompt" | "library">("chat");
  const [off, setOff] = useState<ReadonlySet<SettingKey>>(new Set());
  const [saving, setSaving] = useState<"prompt" | "version" | null>(null);
  const [saveWith, setSaveWith] = useState<Draft | null>(null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [flesh, setFlesh] = useState<PromptDetail | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const limits = caps.limits.builder;
  const binDays = caps.limits.bin_days;

  useEffect(() => onWorking(builder.writing), [builder.writing, onWorking]);
  // pictures pasted anywhere on the tab go to the chat (criterion 244): a model that cannot look at pictures is told so
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

  // a stable function, so that the open prompt's comparison is asked for once and not again whenever this panel is drawn
  const openId = lib.detail?.id ?? null;
  const compareVersions = useCallback((a: number, b: number) => api.compare(openId ?? "", a, b), [api, openId]);

  const kind: ForKind = chat?.for ?? builder.blank.for;
  const draft = builder.draft;
  const shown = builder.editedDraft ?? draft;
  const lastReply = [...messages].reverse().find((m) => m.role === "assistant");
  const prior = useMemo(() => previousDraft(messages), [messages]);
  const useLabel = kind === "vector" ? "Vector" : kind === "music" ? "Music" : "Images";
  const linkedId = chat?.prompt_id ?? null;
  const center: "chat" | "prompt" | "preferences" = listView === "preferences" ? "preferences" : listView === "prompts" && (lib.detail || lib.detailProblem) ? "prompt" : "chat";

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
      lib.everything();
      return null;
    } catch (error) {
      return whyNot(error, "It could not be saved.");
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
      lib.everything();
    } catch (error) {
      push("error", whyNot(error, "It could not be added."));
    }
  };

  // ------------------------------------------------------------------ chats: the list, the bin
  const openChat = (id: string) => { setListView("chats"); lib.closePrompt(); void builder.open(id); setView("chat"); };
  const newChat = () => { lib.closePrompt(); builder.startNew(); setView("chat"); composer.current?.focus(); };
  const binChat = (c: ChatListItem) => setAsk({
    title: "Delete this chat?", confirm: "Delete",
    body: binDays > 0 ? `“${c.title}” moves to Deleted and stays there for ${daysWord(binDays)}; you can restore it from there. After that it is gone for good, with its pictures.` : `“${c.title}” and its pictures are deleted for good. This can't be undone.`,
    onConfirm: async () => {
      try {
        await api.binChat(c.id);
        if (chat?.id === c.id) builder.startNew();
        lib.everything();
      } catch (error) { push("error", whyNot(error, "It could not be deleted.")); }
    },
  });
  const restoreChat = async (c: ChatListItem) => {
    try { await api.restoreChat(c.id); lib.everything(); push("info", `Restored “${c.title}”.`); } catch (error) { push("error", whyNot(error, "It could not be restored.")); }
  };
  const foreverChat = (c: ChatListItem) => setAsk({
    title: "Delete this chat for good?", confirm: "Delete forever", body: `“${c.title}” and its pictures are removed from the Spark. This can't be undone.`,
    onConfirm: async () => { try { await api.deleteChat(c.id); lib.everything(); } catch (error) { push("error", whyNot(error, "It could not be deleted.")); } },
  });
  const emptyBin = () => setAsk({
    title: "Empty Deleted?", confirm: "Empty it",
    body: `Every prompt and chat in Deleted is removed for good, with the chats' pictures. The pictures made from those prompts stay. This can't be undone.`,
    onConfirm: async () => { try { const r = await api.emptyBin(); lib.everything(); push("info", r.deleted ? `Deleted ${r.deleted} for good.` : "There was nothing in Deleted."); } catch (error) { push("error", whyNot(error, "It could not be emptied.")); } },
  });

  // ------------------------------------------------------------------ prompts (criteria 229-231, 245, 246)
  const openPrompt = (id: string) => { void lib.openPrompt(id); setView("chat"); };
  const star = async (card: PromptCard) => {
    try { await api.patchPrompt(card.id, { favourite: !card.favourite }); lib.everything(); } catch (error) { push("error", whyNot(error, "It could not be changed.")); }
  };
  const changePrompt = async (fields: Parameters<Api["patchPrompt"]>[1]): Promise<string | null> => {
    const open = lib.detail;
    if (!open) return null;
    try {
      const updated = await api.patchPrompt(open.id, fields);
      lib.setDetail(updated);
      lib.everything();
      if (updated.unfiled_runs && updated.unfiled_runs > 0) {
        push("info", `Filed in a project. ${updated.unfiled_runs} earlier ${updated.unfiled_runs === 1 ? "picture's run is" : "pictures' runs are"} where they were.`, {
          label: `Also file its ${updated.unfiled_runs} ${updated.unfiled_runs === 1 ? "picture's run" : "pictures' runs"}`,
          run: () => void api.fileRuns(open.id).then((r) => { push("success", `Filed ${r.filed.length} ${r.filed.length === 1 ? "run" : "runs"} in the project.`); lib.everything(); }).catch((e) => push("error", whyNot(e, "They could not be filed."))),
        });
      }
      return null;
    } catch (error) {
      return whyNot(error, "It could not be changed.");
    }
  };
  const makeCurrent = async (n: number) => {
    if (!lib.detail) return;
    try {
      const done = await api.makeCurrent(lib.detail.id, n);
      push(done.created ? "success" : "info", done.message);
      lib.everything();
    } catch (error) { push("error", whyNot(error, "It could not be done.")); }
  };
  const binPrompt = () => lib.detail && setAsk({
    title: "Delete this prompt?", confirm: "Delete",
    body: binDays > 0 ? `“${lib.detail.title}” and its versions move to Deleted and stay there for ${daysWord(binDays)}; you can restore it from there. The pictures made from it are not touched.` : `“${lib.detail.title}” and its versions are deleted for good. The pictures made from it are not touched. This can't be undone.`,
    onConfirm: async () => { try { await api.binPrompt(lib.detail!.id); lib.closePrompt(); lib.everything(); } catch (error) { push("error", whyNot(error, "It could not be deleted.")); } },
  });
  const restorePrompt = async () => { if (!lib.detail) return; try { lib.setDetail(await api.restorePrompt(lib.detail.id)); lib.everything(); push("info", "Restored."); } catch (error) { push("error", whyNot(error, "It could not be restored.")); } };
  const foreverPrompt = () => lib.detail && setAsk({
    title: "Delete this prompt for good?", confirm: "Delete forever", body: `“${lib.detail.title}” and all its versions are removed. The pictures made from it stay and say it was deleted. This can't be undone.`,
    onConfirm: async () => { try { await api.deletePrompt(lib.detail!.id); lib.closePrompt(); lib.everything(); } catch (error) { push("error", whyNot(error, "It could not be deleted.")); } },
  });

  // Flesh out (criterion 231): a chat about this prompt whose first reply says what it has and asks about what is missing
  const startFlesh = (prompt: PromptDetail) => {
    lib.closePrompt();
    setListView("chats");
    builder.startNew({ for: prompt.for, prompt_id: prompt.id, project_id: prompt.project_id });
    setFlesh(prompt);
    setView("chat");
  };
  useEffect(() => {
    if (!flesh || chat !== null || builder.blank.prompt_id !== flesh.id) return;
    const newest = flesh.versions[0];
    setFlesh(null);
    void builder.send({ intent: "flesh_out", text: "", draft: { prompt: newest.text, negative: newest.negative, settings: newest.settings, note: "" } });
  }, [flesh, chat, builder]);

  // A chat that starts from something else (DESIGN.md §37.2): Refine a result, or Help me write an edit. The chat is made when its first message is sent; what it starts with is put in
  // the prompt box, the next message is told what it is, and the pictures are attached once the new chat is the one on screen.
  const [after, setAfter] = useState<{ pictures: string[] } | null>(null);
  const startFrom = (start: { kind: ForKind; prompt: { id: string; project_id: string | null } | null; draft: Draft | null; intent: Intent; runId: string | null; pictures: string[] }) => {
    lib.closePrompt();
    setListView("chats");
    setView("chat");
    builder.startNew({ for: start.kind, prompt_id: start.prompt?.id ?? null, project_id: start.prompt?.project_id ?? null });
    builder.setEdited(start.draft);
    builder.prime(start.intent, start.runId);
    setAfter({ pictures: start.pictures });
  };
  const { attach: attachPicture } = builder;
  useEffect(() => {
    if (!after || chat !== null) return;
    const { pictures } = after;
    setAfter(null);
    void (async () => {
      for (const id of pictures) {
        const problem = await attachPicture({ imageId: id });
        if (problem) { push("info", problem); break; }
      }
      composer.current?.focus();
    })();
  }, [after, chat, attachPicture, push]);

  const refine = (target: RefineTarget) => startFrom({
    kind: target.kind, prompt: target.prompt, draft: { prompt: target.text, negative: target.negative, settings: {}, note: "" }, intent: "refine", runId: target.runId,
    pictures: target.imageId && status?.vision ? [target.imageId] : [],
  });

  // another part of the page asked for something
  const lastCommand = useRef(command?.seq ?? 0);
  useEffect(() => {
    if (!command || command.seq === lastCommand.current) return;
    lastCommand.current = command.seq;
    if (command.kind === "open-prompt") { setListView("prompts"); void lib.openPrompt(command.id); setView("chat"); }
    else if (command.kind === "refine") refine(command.target);
    else startFrom({ kind: "edit", prompt: null, draft: null, intent: "help_edit", runId: null, pictures: command.pictures });
  }, [command?.seq]);

  const versionUsing = (prompt: PromptDetail, v: PromptVersion): Using => ({
    kind: prompt.for, draft: { prompt: v.text, negative: v.negative, settings: v.settings, note: "" }, off: new Set(), from: { promptId: prompt.id, version: v.n, title: prompt.title },
  });

  // ------------------------------------------------------------------ preferences (criterion 233)
  const addPref = async (text: string, projectId: string | null): Promise<string | null> => {
    try { await api.addPreference({ text, project_id: projectId }); lib.everything(); return null; } catch (error) { return whyNot(error, "It could not be added."); }
  };
  const changePref = async (id: string, fields: Parameters<Api["patchPreference"]>[1]): Promise<string | null> => {
    try { await api.patchPreference(id, fields); lib.everything(); return null; } catch (error) { return whyNot(error, "It could not be changed."); }
  };
  const deletePref = async (id: string) => { try { await api.deletePreference(id); lib.everything(); } catch (error) { push("error", whyNot(error, "It could not be deleted.")); } };

  const configured = status === null || status.configured;
  const columnsClass = `builder-columns${narrow ? " narrow" : ""}${center !== "chat" ? " wide-center" : ""}`;

  const library = (
    <BuilderLibrary lib={lib} view={listView} onView={(v) => { setListView(v); if (v !== "prompts") lib.closePrompt(); }} projects={projects} currentChat={chat?.id ?? null} currentPrompt={lib.detail?.id ?? null}
      onOpenChat={openChat} onNewChat={newChat} onDeleteChat={binChat} onRestoreChat={(c) => void restoreChat(c)} onForeverChat={foreverChat} onEmptyBin={emptyBin} onOpenPrompt={openPrompt} onStar={(c) => void star(c)} />
  );

  return (
    <div className="tab-panel builder-panel" role="tabpanel" id="panel-builder" aria-labelledby="tab-builder" hidden={hidden}>
      <ConnectionBar status={status} problem={builder.statusProblem} test={builder.test} testing={builder.testing} unloading={builder.unloading}
        onTest={() => void builder.runTest()} onUnload={() => void builder.unload().then((r) => push(r.ok ? "info" : "error", r.message))} />
      {narrow && (
        <div className="segmented builder-views" role="radiogroup" aria-label="Show">
          {(["chat", "prompt", "library"] as const).filter((v) => configured || v === "library").map((v) => (
            <button key={v} type="button" role="radio" aria-checked={view === v} onClick={() => setView(v)}>{v === "chat" ? "Chat" : v === "prompt" ? "Prompt" : "Library"}</button>
          ))}
        </div>
      )}
      <div className={columnsClass} data-view={narrow ? view : undefined}>
        <div className="builder-col builder-col-list" hidden={narrow && view !== "library"}>{library}</div>
        <div className="builder-col builder-col-chat" hidden={narrow && view !== "chat"}>
          {center === "preferences" && <PreferencesView preferences={lib.preferences} projects={projects} limits={limits} onAdd={addPref} onChange={changePref} onDelete={(id) => void deletePref(id)} />}
          {center === "prompt" && lib.detail && (
            <PromptDetailView prompt={lib.detail} projects={projects} binDays={binDays} limits={limits} problem={lib.detailProblem} compare={compareVersions}
              onClose={lib.closePrompt} onChange={changePrompt} onMakeCurrent={makeCurrent} onFlesh={() => startFlesh(lib.detail!)} onBin={binPrompt} onRestore={() => void restorePrompt()} onForever={foreverPrompt}
              onOpenChat={openChat} quickProblem={quickTryProblem(lib.detail.for)}
              onUse={integration.onUse ? (v) => integration.onUse!(versionUsing(lib.detail!, v)) : undefined}
              onGenerate={integration.onGenerate ? (v) => integration.onGenerate!(versionUsing(lib.detail!, v)) : undefined}
              onQuick={integration.onQuick ? (v) => integration.onQuick!(versionUsing(lib.detail!, v)) : undefined}
              onRefine={(run, v) => refine({
                runId: run.run_id, kind: lib.detail!.for, text: v.text, negative: v.negative, imageId: run.images[0]?.id ?? null, prompt: { id: lib.detail!.id, project_id: lib.detail!.project_id },
              })} />
          )}
          {center === "prompt" && !lib.detail && lib.detailProblem && <p className="form-error" role="alert">{lib.detailProblem}</p>}
          {center === "chat" && (
            !configured ? (
              <p className="hint">The chat needs the builder to be set up. Saved prompts, versions, comparing, projects and preferences work without it.</p>
            ) : (
              <>
                {builder.openProblem && <p className="form-error" role="alert">{builder.openProblem}</p>}
                <BuilderChat builder={builder} status={status} composerRef={composer} history={history}
                  extra={chat ? <ChatHeader chat={chat} projects={projects} onChange={builder.change} /> : null}
                  actions={{
                    useLabel, onRemember: remember,
                    onKeepWording: (w: Wording) => { setSaveWith({ prompt: w.prompt, negative: w.negative, settings: shown?.settings ?? {}, note: `Wording: ${w.label}` }); setSaving(linkedId ? "version" : "prompt"); },
                    onUseWording: integration.onUse ? (w: Wording) => integration.onUse!({ kind, draft: { prompt: w.prompt, negative: w.negative, settings: shown?.settings ?? {}, note: "" }, off, from: null }) : undefined,
                  }} />
              </>
            )
          )}
        </div>
        {center === "chat" && configured && (
          <div className="builder-col builder-col-prompt" hidden={narrow && view !== "prompt"}>
            <PromptSoFar kind={kind} draft={draft} edited={builder.editedDraft} previous={prior} caps={caps} off={off} buttons={buttons}
              onEdit={builder.setEdited} onToggleSetting={(key) => setOff((s) => { const next = new Set(s); if (next.has(key)) next.delete(key); else next.add(key); return next; })} />
          </div>
        )}
      </div>
      <SavePromptDialog open={saving !== null} suggestion={suggestion} projects={projects} limits={limits} onSave={save} onClose={() => { setSaving(null); setSaveWith(null); }} />
      <Confirm ask={ask} onCancel={() => setAsk(null)} />
    </div>
  );
}

