import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { ApiError } from "../api";
import type { Emitter } from "../emitter";
import type { KeyValueStore } from "../options";
import {
  ARROW_SECONDS, SKIP_SECONDS, SPEEDS, TEXT_SIZES, clockText, partListText, partPlayable, pieceMade, playerAction, readingFacts, readingTitle, speedText, wholeTime,
} from "../speech";
import { partDownloadUrl, speechApi } from "../speechApi";
import type { AudiobookCaps, PartPiece, ReadingInfo, ReadingPieceEvent } from "../speechTypes";
import type { SpeechRun } from "../types";
import { useMediaSession } from "../useMediaSession";
import { usePlayer, type Player } from "../usePlayer";
import { AudiobookControl } from "./AudiobookControl";
import { DownloadIcon, PauseIcon, PlayIcon } from "./icons";

// The reading view (DESIGN.md §38.2, §38.7): what opens when Listen is pressed on a reading's card. A player (play and pause, back and forward fifteen seconds,
// previous and next paragraph, a slider, the time in the part and in the whole, the speed), the parts of the reading (press one to hear it), and the text of the part
// that is open, paragraph by paragraph, with the paragraph being spoken marked and kept in view. Press a paragraph to hear from there. The sound itself is player.ts's.

export interface ReadingViewProps {
  run: SpeechRun; // the reading as the page has it now: it changes while the reading is being made
  pieces: Pick<Emitter<ReadingPieceEvent>, "subscribe">; // the `reading.piece` events
  store: KeyValueStore; // where the speed, the text size, the line spacing and Follow along are kept
  onClose: () => void;
  audiobook?: AudiobookCaps; // whether this server can make the M4B file, and why not if it cannot (§44.4)
  autoplay?: boolean; // start playing when opened: Listen was pressed, which is the press a browser wants before it lets a page make sound
  api?: Pick<typeof speechApi, "part" | "savePosition" | "editPiece">;
  saveEveryMs?: number;
  mediaSession?: Parameters<typeof useMediaSession>[2];
}

// ------------------------------------------------------------------ the player's buttons and clock
function PlayerBar({ player, reading, run }: { player: Player; reading: ReadingInfo; run: SpeechRun }) {
  const { playing, time, length } = player;
  const whole = wholeTime(reading, player.part, time);
  return (
    <div className="player" role="group" aria-label="Player">
      <div className="player-buttons">
        <button type="button" className="button small" onClick={player.previous} title="Previous paragraph (J)">
          Previous{" "}<span className="sr-only">paragraph</span>
        </button>
        <button type="button" className="button small" onClick={() => player.skip(-SKIP_SECONDS)} title="Back 15 seconds (left arrow: 10)">
          <span aria-hidden="true">−15 s</span><span className="sr-only">Back 15 seconds</span>
        </button>
        <button type="button" className="button primary player-play" onClick={player.toggle} disabled={player.detail === null} title="Play or pause (Space)">
          {playing ? <PauseIcon /> : <PlayIcon />} {playing ? "Pause" : "Play"}
        </button>
        <button type="button" className="button small" onClick={() => player.skip(SKIP_SECONDS)} title="Forward 15 seconds (right arrow: 10)">
          <span aria-hidden="true">+15 s</span><span className="sr-only">Forward 15 seconds</span>
        </button>
        <button type="button" className="button small" onClick={player.next} title="Next paragraph (K)">
          Next{" "}<span className="sr-only">paragraph</span>
        </button>
      </div>
      <input
        type="range" className="player-seek" min={0} max={length} step="any" value={Math.min(time, length)} disabled={!(length > 0)}
        aria-label="Position in this part" aria-valuetext={`${clockText(time)} of ${clockText(length)}`}
        onChange={(event) => player.seek(Number(event.target.value))}
      />
      <p className="player-times">
        <span className="player-clock">{clockText(time)} / {clockText(length)}</span>
        <span className="player-whole">In all {clockText(whole)} of {clockText(reading.seconds_made)}{run.status === "done" ? "" : " made so far"}</span>
      </p>
      <label className="player-speed">
        Speed{" "}
        <select value={player.prefs.speed} onChange={(event) => player.setPrefs({ speed: Number(event.target.value) })}>
          {SPEEDS.map((speed) => <option key={speed} value={speed}>{speedText(speed)}</option>)}
        </select>
      </label>
    </div>
  );
}

// ------------------------------------------------------------------ the parts
function PartsList({ player, run, reading }: { player: Player; run: SpeechRun; reading: ReadingInfo }) {
  const ids = useId();
  return (
    <nav className="reading-parts" aria-label="Parts of this reading">
      <ol>
        {reading.parts.map((part, i) => {
          const open = i === player.part;
          return (
            <li key={part.idx} className={open ? "open" : undefined}>
              <button
                type="button" className="part-open" aria-current={open ? "true" : undefined} disabled={!partPlayable(part)} onClick={() => player.openPart(i)}
                aria-labelledby={`${ids}-t${i}`} aria-describedby={`${ids}-s${i}`}
              >
                <span id={`${ids}-t${i}`} className="part-title">{part.title || `Part ${i + 1}`}</span>
                <span id={`${ids}-s${i}`} className="part-state">{partListText(part)}</span>
              </button>
              {part.state === "done" && (
                <a className="button small ghost" href={partDownloadUrl(run.id, i)} download aria-label={`Download part ${i + 1}`} title="Download this part as a WAV file">
                  <DownloadIcon /> Download
                </a>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// ------------------------------------------------------------------ the text, paragraph by paragraph
/** Where to scroll a box so that `element` is in the middle of it. The box scrolls and not the page, so that following along never takes the controls off the screen. */
export function centre(container: Pick<HTMLElement, "clientHeight" | "scrollTo" | "scrollTop">, element: Pick<HTMLElement, "offsetTop" | "clientHeight">): void {
  const top = Math.max(0, element.offsetTop - container.clientHeight / 2 + element.clientHeight / 2);
  if (typeof container.scrollTo === "function") container.scrollTo({ top, behavior: "smooth" });
  else container.scrollTop = top;
}

function PieceEditor({ piece, saving, problem, onSave, onCancel }: { piece: PartPiece; saving: boolean; problem: string | null; onSave: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(piece.text);
  return (
    <div className="piece-editor">
      <textarea value={text} onChange={(event) => setText(event.target.value)} aria-label={`Text of paragraph ${piece.idx + 1}`} rows={4} disabled={saving} />
      {problem && <p className="run-error" role="alert">{problem}</p>}
      <div className="speech-actions">
        <button type="button" className="button small primary" onClick={() => onSave(text)} disabled={saving || !text.trim()}>{saving ? "Saving…" : "Save"}</button>
        <button type="button" className="button small ghost" onClick={onCancel} disabled={saving}>Cancel</button>
      </div>
    </div>
  );
}

function ReadAlong({ player, run, editApi }: { player: Player; run: SpeechRun; editApi: Pick<typeof speechApi, "editPiece"> }) {
  const box = useRef<HTMLDivElement>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const { detail, piece: spoken, prefs } = player;
  const size = TEXT_SIZES.find((t) => t.id === prefs.textSize) ?? TEXT_SIZES[1];

  // keep the paragraph being spoken in view, while Follow along is on
  useEffect(() => {
    const element = spoken === null ? null : box.current?.querySelector<HTMLElement>(`[data-piece="${spoken}"]`);
    if (prefs.follow && element && box.current) centre(box.current, element);
  }, [spoken, player.part, prefs.follow]);

  // an edit that was open belongs to the part it was opened in
  useEffect(() => setEditing(null), [player.part]);

  const save = async (piece: PartPiece, text: string) => {
    setSaving(true);
    setProblem(null);
    try {
      await editApi.editPiece(run.id, player.part, piece.idx, text.trim());
      setEditing(null);
      player.reload();
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "The text could not be saved.");
    } finally {
      setSaving(false);
    }
  };

  if (!detail) return <div className="read-along empty" aria-label="Text of this part" />;
  return (
    <div className="read-along" ref={box} role="region" aria-label="Text of this part" tabIndex={0} data-spaced={prefs.spaced ? "true" : undefined}
      style={{ fontSize: `${size.scale}em` }}>
      {detail.pieces.map((piece) => {
        const made = pieceMade(piece);
        const current = piece.idx === spoken;
        const heading = piece.kind === "heading";
        return (
          <div className="piece-row" key={piece.idx}>
            {editing === piece.idx ? (
              <PieceEditor piece={piece} saving={saving} problem={problem} onSave={(text) => void save(piece, text)} onCancel={() => setEditing(null)} />
            ) : (
              <p
                className={`piece${heading ? " piece-heading" : ""}${current ? " piece-current" : ""}${made ? "" : " piece-pending"}`} data-piece={piece.idx}
                role={heading ? "heading" : undefined} aria-level={heading ? 4 : undefined} aria-current={current ? "true" : undefined}
                title={made ? "Hear from here" : undefined} onClick={made ? () => player.playPiece(piece.idx) : undefined}
              >
                {piece.speaker && <span className="piece-speaker">{piece.speaker}<span className="sr-only">:</span> </span>}
                {piece.text}
                {!made && <span className="sr-only"> (not made yet)</span>}
              </p>
            )}
            {!made && editing !== piece.idx && (
              <button type="button" className="button small ghost piece-edit" onClick={() => { setEditing(piece.idx); setProblem(null); }} aria-label={`Edit the text of paragraph ${piece.idx + 1}`}>
                Edit text
              </button>
            )}
            {piece.error && editing !== piece.idx && <p className="piece-error" role="note">This paragraph could not be made: {piece.error}</p>}
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ the view
export function ReadingView({ run, pieces, store, onClose, audiobook, autoplay = true, api, saveEveryMs, mediaSession }: ReadingViewProps) {
  const first = useRef<HTMLAudioElement>(null);
  const second = useRef<HTMLAudioElement>(null);
  const root = useRef<HTMLElement>(null);
  const headingId = useId();
  const reading = run.reading as ReadingInfo; // the view is opened for a reading, which has one
  const player = usePlayer({ run, audios: [first, second], pieces, store, autoplay, api, saveEveryMs });
  const title = readingTitle(run);
  const part = reading.parts[player.part];

  // Opened from a card further down the page: bring it into view, and put focus in it, so that its keys work at once and a screen reader says where it is
  useEffect(() => {
    root.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    root.current?.focus({ preventScroll: true });
  }, []);
  useMediaSession(player, { title: part?.title || `Part ${player.part + 1}`, artist: run.options.voice.label, album: title }, mediaSession);

  // The keys of the player (§38.7), when focus is in the view: Space plays and pauses, J and K go by paragraph, the arrows by ten seconds. Not while something is being typed
  // or chosen (a text box, the speed, the slider, which use their own keys), and Space is left to a button that has focus, which it presses.
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const target = event.target as HTMLElement;
    if (event.altKey || event.ctrlKey || event.metaKey || target.closest("input, select, textarea")) return;
    const action = playerAction(event.key);
    if (!action || (action === "toggle" && target.closest("button, a"))) return;
    event.preventDefault();
    if (action === "toggle") player.toggle();
    else if (action === "previous") player.previous();
    else if (action === "next") player.next();
    else player.skip(action === "back" ? -ARROW_SECONDS : ARROW_SECONDS);
  };

  const nothingMade = player.detail !== null && !player.waiting && !player.detail.pieces.some(pieceMade);

  return (
    <section className="reading-view" ref={root} aria-labelledby={headingId} tabIndex={-1} onKeyDown={onKeyDown}>
      <header className="reading-view-head">
        <div>
          <h3 id={headingId}>{title}</h3>
          <p className="run-meta">{readingFacts(run)}</p>
        </div>
        <button type="button" className="button small" onClick={onClose}>Close player</button>
      </header>

      <PlayerBar player={player} reading={reading} run={run} />

      {/* the audiobook file of what is finished (DESIGN.md §44.4), beside the player: the same control as on the reading's card */}
      {reading.parts_done > 0 && <AudiobookControl runId={run.id} reading={reading} audiobook={audiobook} />}

      <div className="reading-view-status" role="status">
        {player.loading && <span>Loading…</span>}
        {player.waiting && <span>Waiting for the next paragraph to be made…</span>}
        {player.blocked && <span>Your browser wanted a press before it would play sound. Press Play.</span>}
        {nothingMade && !player.loading && <span>Nothing of this part is made yet.</span>}
      </div>
      {player.problem && <p className="run-error" role="alert">{player.problem}</p>}

      <div className="reading-view-body">
        <PartsList player={player} run={run} reading={reading} />
        <div className="reading-text">
          <div className="reading-text-head">
            <p className="reading-text-title">{part?.title || `Part ${player.part + 1}`}</p>
            <div className="player-options" role="group" aria-label="How the text is shown">
              <label>
                Text size{" "}
                <select value={player.prefs.textSize} onChange={(event) => player.setPrefs({ textSize: event.target.value as Player["prefs"]["textSize"] })}>
                  {TEXT_SIZES.map((size) => <option key={size.id} value={size.id}>{size.label}</option>)}
                </select>
              </label>
              <label className="choice"><input type="checkbox" checked={player.prefs.spaced} onChange={(event) => player.setPrefs({ spaced: event.target.checked })} /> Wider line spacing</label>
              <label className="choice"><input type="checkbox" checked={player.prefs.follow} onChange={(event) => player.setPrefs({ follow: event.target.checked })} /> Follow along</label>
            </div>
          </div>
          <ReadAlong player={player} run={run} editApi={api ?? speechApi} />
          <p className="hint reading-keys">Keys: Space plays and pauses, J and K go to the previous and next paragraph, the left and right arrows move ten seconds.</p>
        </div>
      </div>

      <audio ref={first} preload="metadata" />
      <audio ref={second} preload="metadata" />
    </section>
  );
}
