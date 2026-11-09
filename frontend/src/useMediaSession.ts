import { useEffect, useRef } from "react";
import { SKIP_SECONDS } from "./speech";
import type { Player } from "./usePlayer";

// The phone's or the computer's own controls (DESIGN.md §38.2, criterion 285): the lock screen, the headphones' buttons, the browser's media bar. Where the browser has
// none (`navigator.mediaSession` is not there) nothing is done. What the controls show is the reading, the part and the voice; what they do is what the buttons of the
// view do, so that there is one player and not two.

export interface SessionWords {
  title: string; // the part
  artist: string; // the voice
  album: string; // the reading
}

type Session = NonNullable<Navigator["mediaSession"]>;
const ACTIONS = ["play", "pause", "seekbackward", "seekforward", "previoustrack", "nexttrack", "seekto"] as const;

/** `session` is the browser's, and may be handed in (tests do): by default it is `navigator.mediaSession`. */
export function useMediaSession(player: Player, words: SessionWords, session?: Session | null): void {
  const current = useRef(player); // read as it is when a control is pressed, so that the handlers need not be set again for every tick of the clock
  current.current = player;
  const found: Session | null | undefined = session !== undefined ? session : typeof navigator !== "undefined" ? navigator.mediaSession : null;

  // what the controls say of what is playing
  const { title, artist, album } = words;
  useEffect(() => {
    if (!found || typeof MediaMetadata !== "function") return undefined;
    found.metadata = new MediaMetadata({ title, artist, album });
    return () => {
      found.metadata = null;
    };
  }, [found, title, artist, album]);

  // what the controls do
  useEffect(() => {
    if (!found) return undefined;
    const handlers: Partial<Record<(typeof ACTIONS)[number], MediaSessionActionHandler>> = {
      play: () => { if (!current.current.playing) current.current.toggle(); },
      pause: () => { if (current.current.playing) current.current.toggle(); },
      seekbackward: (details) => current.current.skip(-(details.seekOffset ?? SKIP_SECONDS)),
      seekforward: (details) => current.current.skip(details.seekOffset ?? SKIP_SECONDS),
      previoustrack: () => current.current.previous(),
      nexttrack: () => current.current.next(),
      seekto: (details) => { if (details.seekTime !== undefined) current.current.seek(details.seekTime); },
    };
    for (const action of ACTIONS) {
      try {
        found.setActionHandler(action, handlers[action] ?? null);
      } catch {
        // a browser that does not know an action (seekto, older ones) is no reason to give up the others
      }
    }
    return () => {
      for (const action of ACTIONS) {
        try {
          found.setActionHandler(action, null);
        } catch {
          // as above
        }
      }
    };
  }, [found]);

  // whether it is playing, and where it is
  const { playing, time, length, speed } = player;
  useEffect(() => {
    if (!found) return;
    found.playbackState = playing ? "playing" : "paused";
  }, [found, playing]);
  useEffect(() => {
    if (!found || typeof found.setPositionState !== "function" || !(length > 0)) return;
    try {
      found.setPositionState({ duration: length, playbackRate: speed, position: Math.min(Math.max(0, time), length) });
    } catch {
      // the browser refuses a position past the duration, or a duration it cannot use: the clock of the view is right either way
    }
  }, [found, time, length, speed]);
}
