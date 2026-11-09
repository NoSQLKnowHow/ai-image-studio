import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

// Playing a short clip the page already has the address of (a candidate of a voice design, a recording to hear back): one at a time through one audio element, a press on
// the clip that is playing stops it, a press on another moves to that one. The sample of a voice (which has to be made first) is the sampler's.

export interface ClipPlayer {
  playing: string | null; // the key of the clip that plays
  problem: string | null; // the clip that could not be played, with the words
  play: (key: string, url: string) => void;
  stop: () => void;
}

export function useClipPlayer(audio: RefObject<HTMLAudioElement | null>): ClipPlayer {
  const [playing, setPlaying] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const turn = useRef(0); // which press is the one being answered

  const stop = useCallback(() => {
    turn.current += 1;
    const element = audio.current;
    if (element) {
      element.pause();
      element.removeAttribute("src");
    }
    setPlaying(null);
  }, [audio]);

  const play = useCallback((key: string, url: string) => {
    const element = audio.current;
    if (!element) return;
    if (playing === key) return stop();
    turn.current += 1;
    const mine = turn.current;
    setProblem(null);
    element.src = url;
    setPlaying(key);
    Promise.resolve(element.play()).catch((error: unknown) => {
      if (mine !== turn.current || (error instanceof DOMException && error.name === "AbortError")) return;
      setPlaying(null);
      setProblem("The clip could not be played.");
    });
  }, [audio, playing, stop]);

  // the clip ending is the end of it; the file failing is a problem (taking the source away to stop is not)
  useEffect(() => {
    const element = audio.current;
    if (!element) return undefined;
    const ended = () => setPlaying(null);
    const failed = () => {
      if (!element.getAttribute("src")) return;
      setPlaying(null);
      setProblem("The clip could not be played.");
    };
    element.addEventListener("ended", ended);
    element.addEventListener("error", failed);
    return () => {
      element.removeEventListener("ended", ended);
      element.removeEventListener("error", failed);
    };
  }, [audio]);

  useEffect(() => () => { // leaving: no sound
    turn.current += 1;
    audio.current?.pause();
  }, [audio]);

  return { playing, problem, play, stop };
}
