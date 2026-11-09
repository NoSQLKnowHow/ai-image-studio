import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { Emitter } from "./emitter";
import type { KeyValueStore } from "./options";
import { PlayerEngine, SAVE_EVERY_MS, type PlayerState } from "./player";
import { savePlayerPrefs, readPlayerPrefs, pieceMade, type PlayerPrefs } from "./speech";
import { speechApi } from "./speechApi";
import type { ReadingPieceEvent } from "./speechTypes";
import type { SpeechRun } from "./types";

// The player as the reading view uses it (DESIGN.md §38.7): the engine of player.ts for one reading, made when the view opens and let go of when it closes, the
// `reading.piece` events handed to it, and the preferences that are kept in this browser (speed, text size, line spacing, Follow along).

export interface PlayerActions {
  toggle: () => void;
  skip: (seconds: number) => void;
  seek: (seconds: number) => void;
  previous: () => void;
  next: () => void;
  playPiece: (piece: number) => void;
  openPart: (part: number) => void;
  reload: () => void;
  setPrefs: (change: Partial<PlayerPrefs>) => void;
}

export type Player = PlayerState & PlayerActions & { prefs: PlayerPrefs };

export interface PlayerHookEnv {
  run: SpeechRun; // the reading, as the page has it now (it changes while it is being made)
  audios: readonly [RefObject<HTMLAudioElement | null>, RefObject<HTMLAudioElement | null>];
  pieces: Pick<Emitter<ReadingPieceEvent>, "subscribe">;
  store: KeyValueStore;
  autoplay?: boolean; // start playing when opened (the Listen button was pressed)
  api?: Pick<typeof speechApi, "part" | "savePosition">;
  saveEveryMs?: number;
}

const BEFORE: PlayerState = { part: 0, detail: null, loading: true, problem: null, time: 0, length: 0, piece: null, playing: false, waiting: false, blocked: false, speed: 1 };

export function usePlayer({ run, audios, pieces, store, autoplay = true, api, saveEveryMs = SAVE_EVERY_MS }: PlayerHookEnv): Player {
  const [prefs, setPrefsState] = useState<PlayerPrefs>(() => readPlayerPrefs(store));
  const [state, setState] = useState<PlayerState>(() => ({ ...BEFORE, speed: prefs.speed }));
  const engine = useRef<PlayerEngine | null>(null);
  const latest = useRef(run); // what the engine asks for as it goes: the reading's parts, and whether it is still being made
  latest.current = run;
  const initial = useRef({ speed: prefs.speed, autoplay });

  useEffect(() => {
    const [first, second] = [audios[0].current, audios[1].current];
    if (!first || !second) return undefined;
    const made = new PlayerEngine({
      id: run.id, audios: [first, second], api, onChange: setState, speed: initial.current.speed, saveEveryMs,
      parts: () => latest.current.reading?.parts ?? [],
      making: () => latest.current.status === "queued" || latest.current.status === "running",
    });
    engine.current = made;
    if (latest.current.reading) made.start(latest.current.reading, initial.current.autoplay);
    const unsubscribe = pieces.subscribe((event) => made.onPiece(event));
    return () => {
      unsubscribe();
      made.dispose(); // reports the place and stops the sound
      engine.current = null;
    };
    // one engine for one reading: the others are read as they are when it asks (`latest`), not reasons to start again
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.id]);

  // A safety net for a `reading.piece` event that did not arrive (the connection dropped and came back): the reading's own progress says how many pieces of the open
  // part are made, and when that is more than the player knows, it asks again. Normally the event comes first and the two agree, so nothing is asked twice.
  const known = state.detail ? state.detail.pieces.filter(pieceMade).length : null;
  const made = run.reading?.parts[state.part]?.made;
  useEffect(() => {
    if (known !== null && made !== undefined && made > known) engine.current?.reload();
  }, [known, made]);

  const kept = useRef(prefs); // the preferences as they are, so that two changes in a row both count
  const setPrefs = useCallback((change: Partial<PlayerPrefs>) => {
    const next = { ...kept.current, ...change };
    kept.current = next;
    setPrefsState(next);
    savePlayerPrefs(next, store);
    if (change.speed !== undefined) engine.current?.setSpeed(change.speed);
  }, [store]);

  const actions = useMemo<PlayerActions>(() => ({
    toggle: () => engine.current?.toggle(),
    skip: (seconds) => engine.current?.skip(seconds),
    seek: (seconds) => engine.current?.seek(seconds),
    previous: () => engine.current?.previous(),
    next: () => engine.current?.next(),
    playPiece: (piece) => engine.current?.playPiece(piece),
    openPart: (part) => engine.current?.openPart(part),
    reload: () => engine.current?.reload(),
    setPrefs,
  }), [setPrefs]);

  return { ...state, ...actions, prefs };
}
