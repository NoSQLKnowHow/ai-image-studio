import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../api";
import {
  MUSIC_MODEL_NAME, MUSIC_MODEL_URL, SECTION_TAGS, buildMusicRequest, chipsFor, descriptionOf, durationText, hasDescription,
  insertTag, loadMusicForm, musicFormFromRun, musicModelNote, musicProblem, musicRetryRequest, saveMusicForm, type FieldName, type MusicForm,
} from "../music";
import type { KeyValueStore } from "../options";
import type { Capabilities, MusicRun, Status } from "../types";
import { QueueBar } from "./Feedback";
import { NumberField } from "./NumberField";
import { panelId, tabId } from "./Tabs";
import { TrackCard } from "./TrackCard";
import { NoteIcon } from "./icons";
import type { ToastKind } from "../hooks";

interface Props {
  hidden: boolean;
  caps: Capabilities;
  status: Status | null;
  store: KeyValueStore;
  runs: MusicRun[]; // newest first
  runsReady: boolean;
  more: boolean; // older runs can be loaded
  loadingOlder: boolean;
  now: number;
  push: (kind: ToastKind, text: string) => void;
  onRun: (run: MusicRun) => void; // a run was made (or retried): the page adds it to its list
  onLoadOlder: () => void;
  onCancel: (run: MusicRun) => void;
  onToggleKeep: (run: MusicRun) => void;
  onDelete: (run: MusicRun) => void;
  onCopy: (run: MusicRun) => void;
}

interface TextProps {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  maxLength: number;
  hint?: string;
  multiline?: boolean;
  onChange: (value: string) => void;
}

function TextField({ id, label, value, placeholder, maxLength, hint, multiline, onChange }: TextProps) {
  const describedBy = hint ? `${id}-hint` : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {multiline ? (
        <textarea id={id} rows={2} value={value} maxLength={maxLength} placeholder={placeholder} aria-describedby={describedBy}
          onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input id={id} type="text" value={value} maxLength={maxLength} placeholder={placeholder} aria-describedby={describedBy}
          onChange={(e) => onChange(e.target.value)} />
      )}
      {hint && <p id={`${id}-hint`} className="field-hint">{hint}</p>}
    </div>
  );
}

const LABELS: Record<FieldName, { label: string; placeholder: string; hint?: string }> = {
  genre: { label: "Genre", placeholder: "acoustic pop" },
  mood: { label: "Mood", placeholder: "warm and intimate, building gently", hint: "How it feels, and how that develops over the piece." },
  bpm: { label: "Tempo (BPM)", placeholder: "96" },
  key: { label: "Key and scale", placeholder: "C major" },
  instruments: {
    label: "Instruments and arrangement", placeholder: "fingerpicked guitar and soft piano; brushed drums and upright bass enter in the second half",
  },
  voice: { label: "Voice", placeholder: "soft female lead, close and breathy" },
};

/** The Music tab (DESIGN.md §26): the form that builds the description, and the tracks made so far. */
export function MusicPanel({ hidden, caps, status, store, runs, runsReady, more, loadingOlder, now, push, onRun, onLoadOlder, onCancel, onToggleKeep, onDelete, onCopy }: Props) {
  const ids = useId();
  const idOf = (name: string) => `${ids}-${name}`;
  const root = useRef<HTMLDivElement>(null);
  const lyricsBox = useRef<HTMLTextAreaElement>(null);
  const caretKnown = useRef(false); // the lyrics box has been focused, so its caret means something
  const firstField = useRef<HTMLInputElement | null>(null);
  const limits = caps.limits.music;
  const [form, setForm] = useState<MusicForm>(() => loadMusicForm(caps, store).form);
  const [problem, setProblem] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => saveMusicForm(form, store), [form, store]);

  // Only one track plays at a time: starting one stops the others (the "play" event does not bubble, so listen at the top).
  useEffect(() => {
    const onPlay = (e: Event) => {
      const target = e.target;
      if (!(target instanceof HTMLAudioElement) || !root.current?.contains(target)) return;
      root.current.querySelectorAll("audio").forEach((audio) => {
        if (audio !== target && !audio.paused) audio.pause();
      });
    };
    document.addEventListener("play", onPlay, true);
    return () => document.removeEventListener("play", onPlay, true);
  }, []);

  const update = useCallback((patch: Partial<MusicForm>) => {
    setForm((current) => ({ ...current, ...patch }));
    setProblem(null);
  }, []);
  const setField = (name: FieldName, value: string) => update({ fields: { ...form.fields, [name]: value } });

  const description = descriptionOf(form);
  const ready = hasDescription(form);
  const chips = chipsFor(limits.duration);
  const worker = status?.worker ?? null;
  const note = worker ? musicModelNote(worker) : null;
  const seedHint = form.seedLocked ? "The same description, lyrics, length and seed make the same track." : "A new random seed for every run. Lock it to repeat a track.";

  const submit = async () => {
    if (submitting || !ready) return;
    const wrong = musicProblem(form, caps);
    if (wrong) {
      setProblem(wrong);
      return;
    }
    setSubmitting(true);
    setProblem(null);
    try {
      onRun(await api.createMusicRun(buildMusicRequest(form)));
    } catch (error) {
      const err = error as ApiError;
      if (err.status === 422) setProblem(err.message);
      else push("error", err.status === 429 ? err.message : `Couldn't start the run: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const reuse = (run: MusicRun) => {
    setForm((current) => musicFormFromRun(run, caps, current));
    setProblem(null);
    push("info", `Loaded the description${run.lyrics ? ", lyrics" : ""} and settings. Seed locked to ${run.options.seed}.`);
    window.scrollTo({ top: 0, behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    firstField.current?.focus({ preventScroll: true });
  };

  const retry = async (run: MusicRun) => {
    try {
      onRun(await api.createMusicRun(musicRetryRequest(run)));
    } catch (error) {
      push("error", `Couldn't retry: ${(error as Error).message}`);
    }
  };

  const addTag = (tag: string) => {
    const box = lyricsBox.current;
    const from = caretKnown.current && box ? box.selectionStart : form.lyrics.length;
    const to = caretKnown.current && box ? box.selectionEnd : form.lyrics.length;
    const result = insertTag(form.lyrics, from, to, tag, limits.lyrics_chars);
    if (!result) {
      push("error", "The lyrics are too long to add that.");
      return;
    }
    update({ lyrics: result.text });
    requestAnimationFrame(() => {
      box?.focus({ preventScroll: true });
      box?.setSelectionRange(result.caret, result.caret);
    });
  };

  const body: ReactNode = (() => {
    if (!caps.modes.includes("music")) {
      const checking = caps.music.state === "pending" || caps.music.state === "running";
      return (
        <section className="music-form music-unavailable" aria-label="Music is not available">
          <h2><NoteIcon /> {checking ? "Checking the music model…" : "Music isn't available on this server"}</h2>
          {checking ? (
            <p>The studio is checking whether the music model can run here. This tab fills in by itself when it knows.</p>
          ) : (
            <>
              <p role="status">{caps.music.reason ?? `The music model (${MUSIC_MODEL_NAME}) can't run on this server as it is set up.`}</p>
              {caps.music.hint && <p className="hint">{caps.music.hint}</p>}
            </>
          )}
          <p className="hint">Images work as usual.</p>
        </section>
      );
    }
    return (
      <>
        <section className="music-form" aria-label="New music"
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
              e.preventDefault();
              void submit();
            }
          }}>
          <div className="music-title">
            <h2><NoteIcon /> New music</h2>
            <p className="hint">Describe it with the boxes. {MUSIC_MODEL_NAME} reads the description shown below them, exactly as written.</p>
          </div>

          <div className="music-grid">
            {(["genre", "mood", "bpm", "key"] as const).map((name, i) => (
              <div className="field" key={name}>
                <label htmlFor={idOf(name)}>{LABELS[name].label}</label>
                <input id={idOf(name)} type="text" value={form.fields[name]} maxLength={limits.field_chars} placeholder={LABELS[name].placeholder}
                  aria-describedby={LABELS[name].hint ? `${idOf(name)}-hint` : undefined}
                  ref={i === 0 ? (el) => { firstField.current = el; } : undefined}
                  onChange={(e) => setField(name, e.target.value)} />
                {LABELS[name].hint && <p id={`${idOf(name)}-hint`} className="field-hint">{LABELS[name].hint}</p>}
              </div>
            ))}
            <div className="music-wide">
              <TextField id={idOf("instruments")} label={LABELS.instruments.label} placeholder={LABELS.instruments.placeholder} multiline
                maxLength={limits.field_chars} value={form.fields.instruments} onChange={(v) => setField("instruments", v)} />
            </div>
          </div>

          <div className="music-lyrics">
            <label className="choice">
              <input type="checkbox" role="switch" checked={form.lyricsOn} onChange={(e) => update({ lyricsOn: e.target.checked })} />
              Add lyrics
            </label>
            <p className="field-hint" id={idOf("lyrics-note")}>
              {form.lyricsOn ? "The track will have vocals." : "Without lyrics the track is instrumental: the studio sends the [Instrumental] tag and says “no vocals” in the description."}
            </p>
            {form.lyricsOn && (
              <>
                <TextField id={idOf("voice")} label={LABELS.voice.label} placeholder={LABELS.voice.placeholder} maxLength={limits.field_chars}
                  value={form.fields.voice} onChange={(v) => setField("voice", v)} />
                <div className="field">
                  <label htmlFor={idOf("lyrics")}>Lyrics</label>
                  <div className="tag-row" role="group" aria-label="Insert a section tag">
                    {SECTION_TAGS.map((tag) => (
                      <button key={tag} type="button" className="button small" onMouseDown={(e) => e.preventDefault()} onClick={() => addTag(tag)}
                        title={`Put [${tag}] on a line of its own`}>
                        [{tag}]
                      </button>
                    ))}
                  </div>
                  <textarea id={idOf("lyrics")} ref={lyricsBox} className="music-lyrics-box" rows={8} value={form.lyrics} maxLength={limits.lyrics_chars}
                    aria-describedby={`${idOf("lyrics")}-hint`} placeholder={"[Verse]\nThe words, a line at a time.\n[Chorus]\n…"}
                    onFocus={() => { caretKnown.current = true; }} onChange={(e) => update({ lyrics: e.target.value })} />
                  <p id={`${idOf("lyrics")}-hint`} className="field-hint">
                    Put each tag on its own line: the model drops any text written on the same line as a tag.
                    {form.lyrics.length > limits.lyrics_chars * 0.8 ? ` ${form.lyrics.length} / ${limits.lyrics_chars}` : ""}
                  </p>
                </div>
              </>
            )}
          </div>

          <div className="field music-description">
            <label htmlFor={idOf("description")}>Description sent to the model</label>
            <textarea id={idOf("description")} className="prompt-input" rows={7} value={description} maxLength={limits.description_chars}
              aria-describedby={`${idOf("description")}-hint`} onChange={(e) => update({ edited: true, text: e.target.value })} />
            <div className="music-description-row" id={`${idOf("description")}-hint`}>
              <p className="field-hint">
                {form.edited
                  ? "You changed this by hand, so the boxes no longer rewrite it."
                  : "Built from the boxes above as you type. Change it by hand if you like."}
                {description.length > limits.description_chars * 0.8 ? ` ${description.length} / ${limits.description_chars}` : ""}
              </p>
              <button type="button" className="button small" disabled={!form.edited} onClick={() => update({ edited: false, text: "" })}
                title="Write the description from the boxes again, replacing your changes">
                Rebuild from fields
              </button>
            </div>
          </div>

          <div className="music-settings">
            <div className="field">
              <span className="field-label" id={idOf("chips")}>Length (at most)</span>
              <div className="chips" role="group" aria-labelledby={idOf("chips")}>
                {chips.map((seconds) => (
                  <button key={seconds} type="button" className="chip" aria-pressed={form.duration === seconds} onClick={() => update({ duration: seconds })}>
                    {durationText(seconds)}
                  </button>
                ))}
              </div>
              <NumberField label="Seconds" value={form.duration} min={limits.duration.min} max={limits.duration.max}
                hint={`${durationText(form.duration)}. The model may end a piece sooner. Making music takes much longer than playing it: try 15 s first.`}
                onChange={(duration) => update({ duration })} />
            </div>
            <div className="field">
              <span className="field-label" id={idOf("versions")}>Versions</span>
              <div className="segmented" role="radiogroup" aria-labelledby={idOf("versions")}>
                {Array.from({ length: limits.tracks.max - limits.tracks.min + 1 }, (_, i) => limits.tracks.min + i).map((n) => (
                  <button key={n} type="button" role="radio" aria-checked={form.tracks === n} onClick={() => update({ tracks: n })}>{n}</button>
                ))}
              </div>
              <p className="field-hint">Different tracks from the same description, made one after another.</p>
            </div>
          </div>

          <details className="music-advanced">
            <summary>Advanced{form.seedLocked ? ` · seed locked to ${form.seed}` : ""}</summary>
            <div className="music-advanced-body">
              <NumberField label="Rendering steps" value={form.steps} min={limits.steps.min} max={limits.steps.max}
                hint={`More steps can sound cleaner and take longer. Default ${limits.steps.default}.`} onChange={(steps) => update({ steps })} />
              <div className="field">
                <label className="choice">
                  <input type="checkbox" checked={form.seedLocked} onChange={(e) => update({ seedLocked: e.target.checked })} />
                  Lock seed
                </label>
                <NumberField label="Seed" value={form.seed} min={caps.limits.seed.min} max={caps.limits.seed.max} disabled={!form.seedLocked}
                  hint={seedHint} onChange={(seed) => update({ seed })} />
              </div>
            </div>
          </details>

          <div className="prompt-row">
            <p className="prompt-help">Ctrl + Enter (⌘ + Enter on a Mac) makes the music.</p>
            <div className="prompt-row-end">
              <button type="button" className="button primary" onClick={() => void submit()} disabled={!ready || submitting}>
                <NoteIcon />
                {submitting ? "Sending…" : "Make music"}
              </button>
            </div>
          </div>
          {problem && <p className="form-error" role="alert">{problem}</p>}
          {note && <p className="music-note" role="status">{note}</p>}
          <p className="music-licence">
            Made with <a href={MUSIC_MODEL_URL} target="_blank" rel="noreferrer noopener">{MUSIC_MODEL_NAME}</a>. Its licence asks you to say
            the music is machine-generated when you share it publicly. Every WAV you download says so in its file information.
          </p>
        </section>
      </>
    );
  })();

  return (
    <div className="tab-panel" role="tabpanel" id={panelId("music")} aria-labelledby={tabId("music")} hidden={hidden} ref={root}>
      {body}
      {caps.modes.includes("music") && <QueueBar status={status} />}
      <section className="timeline" aria-label="Your tracks">
        {!runsReady ? (
          <p className="loading" role="status">Loading your tracks…</p>
        ) : runs.length === 0 ? (
          <div className="empty-state">
            <p className="empty-title">No music yet</p>
            <p>Describe the music above and press Make music. Every track lands here with its description and settings.</p>
          </div>
        ) : (
          runs.map((run) => (
            <TrackCard key={run.id} run={run} now={now} workerState={status?.worker.state ?? null}
              onReuse={() => reuse(run)} onRetry={() => void retry(run)} onCancel={() => onCancel(run)}
              onToggleKeep={() => onToggleKeep(run)} onDelete={() => onDelete(run)} onCopy={() => onCopy(run)} />
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
