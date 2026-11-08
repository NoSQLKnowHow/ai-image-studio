import { useEffect, useRef, useState, type ReactNode } from "react";
import { DETAILS, DETAIL_LABELS, FOR_KINDS, FOR_LABELS, WORDS, canSend, chooseMode, isAnswer, openQuestions, toggleChip, type Picks } from "../builder";
import type { AnswerContent, Attachment, BuilderStatus, ChatInfo, ChatMessage, Detail, ForKind, Question, Wording } from "../builderTypes";
import type { BuilderState } from "../useBuilder";
import { CloseIcon, DiceIcon, SparkleIcon, StopIcon } from "./icons";

/** The conversation (DESIGN.md §37.2): the messages, the questions with their chips, what the model is doing, and the box to write in. Text from the model is shown as text and
 *  nothing else: it is never given to the browser as markup (criterion 250). */

export type ThinkChoice = "setting" | "on" | "off";

export const thinkValue = (choice: ThinkChoice): boolean | null => (choice === "on" ? true : choice === "off" ? false : null);

export function thinkLabel(choice: ThinkChoice, setting: BuilderStatus["think"] | undefined): string {
  const named = (on: boolean | null) => (on === null ? "the model's own choice" : on ? "On" : "Off");
  if (choice === "on") return "Think: On";
  if (choice === "off") return "Think: Off";
  return `Think: ${setting === "on" ? "On" : setting === "off" ? "Off" : named(null)} (the setting)`;
}

function Pictures({ pictures, onRemove }: { pictures: Attachment[]; onRemove?: (id: string) => void }) {
  if (!pictures.length) return null;
  return (
    <ul className="builder-pictures" aria-label="Pictures">
      {pictures.map((p, i) => (
        <li key={p.id}>
          <img src={p.url} alt={p.name ?? `Picture ${i + 1}`} width={64} height={64} loading="lazy" />
          <span className="tray-badge" aria-hidden="true">{i + 1}</span>
          {onRemove && <button type="button" className="tray-remove" aria-label={`Take picture ${i + 1} out`} onClick={() => onRemove(p.id)}><CloseIcon /></button>}
        </li>
      ))}
    </ul>
  );
}

function PersonMessage({ message }: { message: ChatMessage }) {
  const answers = (message.content as { answers?: { id: string; question: string; mode: string; chips: string[] }[] } | null)?.answers ?? [];
  return (
    <li className="builder-message person" data-role="user">
      {message.text && <p className="builder-text">{message.text}</p>}
      {answers.length > 0 && (
        <ul className="builder-answered" aria-label="Answers">
          {answers.map((a) => (
            <li key={a.id}><span className="hint">{a.question}</span> {a.mode === "surprise" ? "Surprise me" : a.mode === "skip" ? "Skip" : a.chips.join(", ")}</li>
          ))}
        </ul>
      )}
      <Pictures pictures={message.attachments} />
    </li>
  );
}

function Looked({ content, onLeaveOut }: { content: AnswerContent; onLeaveOut?: (id: string) => void }) {
  const context = content.context;
  if (!context || (!context.preferences && !context.examples.length)) return null;
  return (
    <p className="builder-looked hint">
      {context.preferences > 0 && <span>Using {context.preferences} {context.preferences === 1 ? "preference" : "preferences"}. </span>}
      {context.examples.length > 0 && (
        <span>
          Looked at {context.examples.length} of your {context.examples.length === 1 ? "prompt" : "prompts"}:{" "}
          {context.examples.map((e, i) => (
            <span key={e.id} className="builder-example">
              {i > 0 ? ", " : ""}“{e.title}”
              {onLeaveOut && <button type="button" className="link-button" aria-label={`Leave out “${e.title}” and answer again`} onClick={() => onLeaveOut(e.id)}>✕</button>}
            </span>
          ))}
        </span>
      )}
    </p>
  );
}

function Wordings({ wordings, useLabel, onKeep, onUse }: { wordings: Wording[]; useLabel: string; onKeep?: (w: Wording) => void; onUse?: (w: Wording) => void }) {
  const [dismissed, setDismissed] = useState<ReadonlySet<number>>(new Set());
  const shown = wordings.map((w, i) => ({ w, i })).filter(({ i }) => !dismissed.has(i));
  if (!shown.length) return null;
  return (
    <ul className="builder-wordings" aria-label="Three wordings">
      {shown.map(({ w, i }) => (
        <li key={i} className="builder-wording">
          <strong>{w.label}</strong>
          <p className="builder-text">{w.prompt}</p>
          <div className="builder-wording-buttons">
            {onKeep && <button type="button" className="button small" onClick={() => onKeep(w)}>Keep as version</button>}
            {onUse && <button type="button" className="button small" onClick={() => onUse(w)}>Use in {useLabel}</button>}
            <button type="button" className="button small ghost" onClick={() => setDismissed((all) => new Set([...all, i]))}>Dismiss</button>
          </div>
        </li>
      ))}
    </ul>
  );
}

function QuestionCard({ question, picks, disabled, onChip, onMode }: {
  question: Question; picks: Picks; disabled: boolean; onChip: (chip: string) => void; onMode: (mode: "surprise" | "skip") => void;
}) {
  const pick = picks[question.id];
  return (
    <fieldset className="builder-question" disabled={disabled}>
      <legend>{question.text}</legend>
      <div className="builder-chips">
        {question.chips.map((chip) => (
          <button key={chip} type="button" className="builder-chip" aria-pressed={pick?.mode === "chips" && pick.chips.includes(chip)} onClick={() => onChip(chip)}>{chip}</button>
        ))}
      </div>
      <div className="builder-question-extra">
        <button type="button" className="button small ghost" aria-pressed={pick?.mode === "surprise"} onClick={() => onMode("surprise")}><DiceIcon /> Surprise me</button>
        <button type="button" className="button small ghost" aria-pressed={pick?.mode === "skip"} onClick={() => onMode("skip")}>Skip</button>
      </div>
    </fieldset>
  );
}

function ModelMessage({ message, last, picks, busy, useLabel, onChip, onMode, onRemember, onKeep, onUse, onLeaveOut }: {
  message: ChatMessage & { content: AnswerContent }; last: boolean; picks: Picks; busy: boolean; useLabel: string;
  onChip: (id: string, chip: string) => void; onMode: (id: string, mode: "surprise" | "skip") => void;
  onRemember?: (text: string) => Promise<void>; onKeep?: (w: Wording) => void; onUse?: (w: Wording) => void; onLeaveOut?: (id: string) => void;
}) {
  const c = message.content;
  const [remembered, setRemembered] = useState<ReadonlySet<string>>(new Set());
  return (
    <li className="builder-message model" data-role="assistant">
      <p className="builder-text">{c.reply}{c.stopped && <span className="badge"> {WORDS.stopped}</span>}</p>
      {c.notices.length > 0 && <ul className="builder-notices" aria-label="Left out">{c.notices.map((n) => <li key={n}>{n}</li>)}</ul>}
      {c.questions.length > 0 && last && c.questions.map((q) => (
        <QuestionCard key={q.id} question={q} picks={picks} disabled={busy} onChip={(chip) => onChip(q.id, chip)} onMode={(mode) => onMode(q.id, mode)} />
      ))}
      {c.questions.length > 0 && !last && <p className="hint">Asked: {c.questions.map((q) => q.text).join(" ")}</p>}
      {c.variations.length > 0 && <Wordings wordings={c.variations} useLabel={useLabel} onKeep={onKeep} onUse={onUse} />}
      {last && c.remember.filter((r) => !remembered.has(r)).map((text) => (
        <p key={text} className="builder-remember">
          Remember “{text}”?{" "}
          <button type="button" className="button small" onClick={() => onRemember?.(text).then(() => setRemembered((s) => new Set([...s, text])))}>Add</button>{" "}
          <button type="button" className="button small ghost" onClick={() => setRemembered((s) => new Set([...s, text]))}>No</button>
        </p>
      ))}
      {c.context && <Looked content={c} onLeaveOut={last ? onLeaveOut : undefined} />}
    </li>
  );
}

/** What the model is doing, while a reply is written (criterion 224, 248, 255): waiting its turn, thinking with the seconds and a fold for the reasoning, writing. */
function Progress({ state, onStop }: { state: BuilderState["reply"]; onStop: () => void }) {
  const [shown, setShown] = useState(false);
  return (
    <li className="builder-message model working" aria-live="polite" data-role="progress">
      {state.phase === "waiting" && <p className="hint">{WORDS.waiting}</p>}
      {state.busyImages && <p className="hint">{WORDS.busyImages}</p>}
      {state.phase === "retrying" && <p className="hint">{WORDS.retrying}</p>}
      {(state.phase === "thinking" || (state.thinking && state.phase === "writing")) && (
        <details open={shown} onToggle={(e) => setShown((e.target as HTMLDetailsElement).open)} className="builder-thinking">
          <summary>{state.phase === "thinking" ? `${WORDS.thinking} ${Math.round(state.seconds)} s` : "Show thinking"}</summary>
          <p className="builder-text hint">{state.thinking}</p>
        </details>
      )}
      {state.text && <p className="builder-text">{state.text}</p>}
      {(state.phase === "working" && !state.text) && <p className="hint">Sending…</p>}
      <button type="button" className="button small" onClick={onStop}><StopIcon /> Stop</button>
    </li>
  );
}

export interface ChatActions {
  useLabel: string; // where "Use in …" goes for what this chat is for: Images, Vector or Music
  onKeepWording?: (w: Wording) => void;
  onUseWording?: (w: Wording) => void;
  onRemember: (text: string) => Promise<void>;
}

export function BuilderChat({ builder, status, actions, composerRef, extra }: {
  builder: BuilderState;
  status: BuilderStatus | null;
  actions: ChatActions;
  composerRef?: React.RefObject<HTMLTextAreaElement | null>;
  extra?: ReactNode;
}) {
  const { chat, messages, pending, reply, problem, blank } = builder;
  const [text, setText] = useState("");
  const [picks, setPicks] = useState<Picks>({});
  const [think, setThink] = useState<ThinkChoice>("setting");
  const [exclude, setExclude] = useState<string[]>([]);
  const [similar, setSimilar] = useState(true);
  const [note, setNote] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const log = useRef<HTMLOListElement>(null);
  const questions = openQuestions(messages);
  const kind = chat?.for ?? blank.for;
  const detail = chat?.detail ?? blank.detail;
  const busy = builder.replying || builder.writing;
  const first = messages.length === 0;
  const canLook = status?.vision !== false;

  // a new chat or another chat starts from nothing
  useEffect(() => {
    setText("");
    setPicks({});
    setExclude([]);
    setNote(null);
  }, [chat?.id]);

  // the newest words are in view
  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, reply.text, reply.phase]);

  const send = async (intent: "message" | "write_now" | "variations" = "message") => {
    const body = { text, picks, questions, intent, think: thinkValue(think), similar, exclude, draft: builder.editedDraft };
    setText("");
    setPicks({});
    await builder.send(body);
  };
  const ready = canSend({ text, picks, pictures: pending.length, busy });

  const attach = async (files: FileList | File[] | null) => {
    if (!files) return;
    for (const file of Array.from(files)) {
      const problemText = await builder.attach({ file, name: file.name });
      if (problemText) {
        setNote(problemText);
        break;
      }
    }
  };

  return (
    <section className="builder-chat" aria-label="Chat"
      onDragOver={(e) => { if (canLook && e.dataTransfer.types.includes("Files")) e.preventDefault(); }}
      onDrop={(e) => { if (!canLook || !e.dataTransfer.types.includes("Files")) return; e.preventDefault(); void attach(Array.from(e.dataTransfer.files)); }}>
      {extra}
      <ol className="builder-log" ref={log} aria-label="Messages" aria-relevant="additions text">
        {first && !busy && (
          <li className="builder-welcome">
            <p className="empty-title">What's the idea?</p>
            <p className="hint">Type a rough idea. The builder asks a few questions, then writes the prompt. Everything is saved as you go.</p>
          </li>
        )}
        {messages.map((m, i) => (isAnswer(m)
          ? <ModelMessage key={m.seq} message={m} last={i === messages.length - 1} picks={picks} busy={busy} useLabel={actions.useLabel}
              onChip={(id, chip) => setPicks((p) => toggleChip(p, id, chip))} onMode={(id, mode) => setPicks((p) => chooseMode(p, id, mode))}
              onRemember={actions.onRemember} onKeep={actions.onKeepWording} onUse={actions.onUseWording}
              onLeaveOut={(id) => { setExclude((x) => [...x, id]); void builder.send({ retry: true, redo: true, exclude: [...exclude, id], think: thinkValue(think) }); }} />
          : <PersonMessage key={`${m.seq}-${i}`} message={m} />))}
        {busy && <Progress state={reply} onStop={() => void builder.stop()} />}
      </ol>
      {problem && (
        <div className="builder-problem" role="alert">
          <p className="form-error">{problem.message}</p>
          {problem.hint && <p className="hint">{problem.hint}</p>}
          {problem.code !== "reply_in_progress" && messages[messages.length - 1]?.role === "user" && (
            <button type="button" className="button small" onClick={() => void builder.retry()}>Retry</button>
          )}
        </div>
      )}
      <form className="builder-composer" onSubmit={(e) => { e.preventDefault(); if (ready) void send(); }}>
        {first && (
          <div className="builder-start">
            <label>For <select value={kind} onChange={(e) => void builder.change({ for: e.target.value as ForKind })} aria-label="What the prompt is for">
              {FOR_KINDS.map((k) => <option key={k} value={k}>{FOR_LABELS[k]}</option>)}</select></label>
            <label>Detail <select value={detail} onChange={(e) => void builder.change({ detail: e.target.value as Detail })} aria-label="How detailed the prompt should be">
              {DETAILS.map((d) => <option key={d} value={d}>{DETAIL_LABELS[d]}</option>)}</select></label>
            <label className="builder-switch"><input type="checkbox" checked={similar} onChange={(e) => setSimilar(e.target.checked)} /> Look at my saved prompts</label>
          </div>
        )}
        {!first && chat && (
          <div className="builder-start">
            <label>For <select value={kind} onChange={(e) => void builder.change({ for: e.target.value as ForKind })} aria-label="What the prompt is for">
              {FOR_KINDS.map((k) => <option key={k} value={k}>{FOR_LABELS[k]}</option>)}</select></label>
            <label>Detail <select value={detail} onChange={(e) => void builder.change({ detail: e.target.value as Detail })} aria-label="How detailed the prompt should be">
              {DETAILS.map((d) => <option key={d} value={d}>{DETAIL_LABELS[d]}</option>)}</select></label>
          </div>
        )}
        <Pictures pictures={pending} onRemove={(id) => void builder.unattach(id)} />
        {note && <p className="form-error" role="alert">{note}</p>}
        <label htmlFor="builder-input" className="sr-only">{first ? "What's the idea?" : "Your reply"}</label>
        <textarea id="builder-input" ref={composerRef} className="prompt-input builder-input" value={text} maxLength={4000} 
          placeholder={first ? "What's the idea? A rough one is fine…" : questions.length ? "Choose answers above, add words, then Send…" : "Write back…"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); if (ready) void send(); } }} />
        <div className="builder-composer-row">
          <button type="button" className="button small ghost" onClick={() => fileInput.current?.click()} disabled={!canLook || builder.picking} aria-label="Attach a picture"
            title={canLook ? "Attach a picture (or paste or drop one)" : WORDS.noVision}>{builder.picking ? "Attaching…" : "Attach"}</button>
          <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple className="sr-only" tabIndex={-1} aria-hidden="true" data-testid="builder-file-input"
            onChange={(e) => { void attach(e.target.files); e.target.value = ""; }} />
          <select value={think} onChange={(e) => setThink(e.target.value as ThinkChoice)} aria-label="Think">
            <option value="setting">{thinkLabel("setting", status?.think)}</option>
            <option value="on">{thinkLabel("on", status?.think)}</option>
            <option value="off">{thinkLabel("off", status?.think)}</option>
          </select>
          <span className="builder-composer-end">
            {!first && builder.draft && (
              <button type="button" className="button small" onClick={() => void send("variations")} disabled={busy}>Try 3 wordings</button>
            )}
            {!first && (
              <button type="button" className="button small" onClick={() => void send("write_now")} disabled={busy}>Write it now</button>
            )}
            <button type="submit" className="button primary" disabled={!ready}><SparkleIcon /> Send</button>
          </span>
        </div>
      </form>
    </section>
  );
}

export type { ChatInfo };
