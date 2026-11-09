// What plays a reading (DESIGN.md §38.7), with no React in it, so that its timing can be tried on stand-in audio elements: a class that owns two <audio> elements, one that
// is playing and one that has the next piece ready, and tells whoever listens what the player is doing.
//
// Three ways of playing, one place in the part:
//   * A part that is complete is ONE file; seeking in it is the browser's.
//   * A part still being made has only its pieces, each its own file. The player plays them one after another, loading the next into the other element while the
//     current one plays, so that there is no gap to hear. When a piece ends and the next is not made yet it waits (still "playing"), and goes on when it is.
//   * When the part completes while a piece plays, it switches to the whole file at the same place (criterion 281).
// The place is always seconds into the PART (`time`): what an element reports is converted by `partTime`, so the clock, the highlight and the saved position do not care
// which way the sound is coming.

import { ApiError } from "./api";
import { speechApi } from "./speechApi";
import {
  nextPieceSource, nextTime, partSeconds, partTime, pieceAt, positionFor, previousTime, resumeTime, seekedTime, sourceFor, startingPart,
  type PlaySource,
} from "./speech";
import type { PartDetail, ReadingInfo, ReadingPart, ReadingPieceEvent, ReadingPosition } from "./speechTypes";

export const SAVE_EVERY_MS = 10_000; // §38.7: the position is reported this often while playing, and when playing stops

/** Everything the view shows of the player. A new object is made for every change, so it can be handed to React as it is. */
export interface PlayerState {
  part: number; // the part that is open
  detail: PartDetail | null; // its pieces, once they have been asked for
  loading: boolean; // they are being asked for
  problem: string | null; // they could not be, or the sound could not be played
  time: number; // seconds into the part
  length: number; // seconds the part has so far (all of it once it is complete)
  piece: number | null; // the piece being spoken (or paused in)
  playing: boolean; // sound is meant to be playing
  waiting: boolean; // ... but what comes next is not made yet
  blocked: boolean; // the browser would not start the sound by itself: pressing Play will
  speed: number;
}

export interface PlayerEnv {
  id: string; // the reading
  audios: readonly [HTMLAudioElement, HTMLAudioElement];
  api?: Pick<typeof speechApi, "part" | "savePosition">;
  onChange: (state: PlayerState) => void;
  parts: () => readonly ReadingPart[]; // the reading's parts as they are now (it is being made, so they change)
  making: () => boolean; // the reading is queued or running: what is not made yet will be
  speed: number;
  saveEveryMs?: number;
}

type Pending = { time: number; want: boolean; resume: ReadingPosition | null };

const words = (error: unknown): string => (error instanceof ApiError ? error.message : "The part could not be loaded.");

export class PlayerEngine {
  private state: PlayerState;
  private readonly api: Pick<typeof speechApi, "part" | "savePosition">;
  private active: 0 | 1 = 0; // which element is the one playing
  private source: PlaySource | null = null; // what it has been given
  private loaded: [string | null, string | null] = [null, null]; // the file each element holds
  private seekTo: number | null = null; // where the active element is to be when it has loaded enough to be moved
  private pending: Pending | null = null; // wanting to play from a time in the part, once something of it is made
  private touched = false; // the person has played or moved: only then is a position worth saving
  private timer: ReturnType<typeof setInterval> | null = null;
  private asked = 0; // the last request for a part's pieces: an answer to an earlier one is not wanted
  private applied = 0; // the newest answer taken
  private disposed = false;
  private readonly listeners: Array<{ element: HTMLAudioElement; type: string; handler: () => void }> = [];

  constructor(private readonly env: PlayerEnv) {
    this.api = env.api ?? speechApi;
    this.state = { part: 0, detail: null, loading: false, problem: null, time: 0, length: 0, piece: null, playing: false, waiting: false, blocked: false, speed: env.speed };
    env.audios.forEach((element, index) => {
      element.playbackRate = env.speed;
      element.preservesPitch = true; // a faster reading is not a higher voice (§38.7); a browser's default, said so that it does not depend on that
      this.listen(element, "timeupdate", () => this.onTime(index as 0 | 1));
      this.listen(element, "ended", () => this.onEnded(index as 0 | 1));
      this.listen(element, "loadedmetadata", () => this.onLoaded(index as 0 | 1));
      this.listen(element, "error", () => this.onError(index as 0 | 1));
    });
  }

  // ------------------------------------------------------------------ what is shown
  get snapshot(): PlayerState {
    return this.state;
  }

  private set(change: Partial<PlayerState>): void {
    if (this.disposed) return;
    this.state = { ...this.state, ...change };
    this.env.onChange(this.state);
  }

  private listen(element: HTMLAudioElement, type: string, handler: () => void): void {
    element.addEventListener(type, handler);
    this.listeners.push({ element, type, handler });
  }

  private get element(): HTMLAudioElement {
    return this.env.audios[this.active];
  }

  private get standby(): HTMLAudioElement {
    return this.env.audios[this.active === 0 ? 1 : 0];
  }

  // ------------------------------------------------------------------ opening a part
  /** Open the reading where it was left: the part the stored place is in, at that place. `play` is whether to start (the Listen button was pressed, which is the press a
   *  browser wants before it lets a page make sound). */
  start(reading: ReadingInfo, play: boolean): void {
    const part = startingPart(reading);
    const resume = reading.position && reading.position.part === part ? reading.position : null;
    this.open(part, 0, play, resume);
  }

  /** Open a part and play it from `at` seconds in (or from where `resume` says, once the part's pieces are known). */
  open(part: number, at: number, play: boolean, resume: ReadingPosition | null = null): void {
    this.halt();
    this.pending = { time: at, want: play, resume };
    this.set({ part, detail: null, loading: true, problem: null, time: at, length: 0, piece: null, playing: play, waiting: false, blocked: false });
    this.fetch();
  }

  /** Ask for the open part's pieces. Asked again whenever a piece of it is made (`piece`), and when something changed that the answer may show. */
  reload(): void {
    this.fetch();
  }

  private fetch(): void {
    const part = this.state.part;
    const mine = ++this.asked;
    this.api.part(this.env.id, part).then(
      (detail) => {
        if (this.disposed || part !== this.state.part || mine < this.applied) return;
        this.applied = mine;
        this.take(detail);
      },
      (error) => {
        if (this.disposed || part !== this.state.part || mine < this.applied) return;
        this.set({ loading: false, problem: words(error), playing: false, waiting: false });
      },
    );
  }

  /** What to do with a part's pieces as they now are, whatever is happening. */
  private take(detail: PartDetail): void {
    const length = partSeconds(detail);
    this.set({ detail, loading: false, problem: null, length });
    if (this.pending) {
      const { want, resume } = this.pending;
      const time = resume ? resumeTime(resume, detail) : this.pending.time;
      const source = sourceFor(detail, time);
      if (!source) {
        // nothing is made yet: the wish to play stays, and is met by the first piece
        this.pending = { time, want, resume: null };
        this.set({ time, waiting: want && this.env.making(), playing: want && this.env.making() });
        return;
      }
      this.pending = null;
      this.begin(source, want);
      return;
    }
    const source = this.source;
    if (source && source.kind === "piece") {
      if (detail.complete && detail.url) this.switchToWhole(detail);
      else if (this.state.waiting) this.goOn(detail, source.piece);
      else this.preload(detail, source.piece);
    }
    this.set({ piece: pieceAt(detail.pieces, this.state.time, detail.sample_rate ?? 0) });
  }

  /** A piece of the open part was made (the `reading.piece` event): look again. */
  onPiece(event: ReadingPieceEvent): void {
    if (event.run_id === this.env.id && event.part === this.state.part) this.reload();
  }

  // ------------------------------------------------------------------ giving the elements something to play
  /** Put `source` in the playing element, at its place, and play it if `want`. The same file is not loaded again: only moved. */
  private begin(source: PlaySource, want: boolean): void {
    const element = this.element;
    this.source = source;
    if (this.loaded[this.active] !== source.url) {
      this.loaded[this.active] = source.url;
      element.src = source.url;
    }
    this.seekTo = source.at;
    element.currentTime = source.at; // before the file has loaded this is where it will start; onLoaded does it again for a browser that ignored that
    element.playbackRate = this.state.speed;
    const time = partTime(source, source.at);
    const detail = this.state.detail;
    this.set({ time, piece: detail ? pieceAt(detail.pieces, time, detail.sample_rate ?? 0) : null, waiting: false });
    if (want) this.play();
    else this.setPlaying(false);
    if (detail && source.kind === "piece") this.preload(detail, source.piece);
  }

  /** Have the piece after `piece` ready in the other element, so that the one before it ends into it with nothing between. */
  private preload(detail: PartDetail, piece: number): void {
    const next = nextPieceSource(detail, piece);
    const other = this.active === 0 ? 1 : 0;
    if (!next || this.loaded[other] === next.url) return;
    this.loaded[other] = next.url;
    this.standby.preload = "auto";
    this.standby.src = next.url;
  }

  /** The part has become a whole file while a piece of it plays: carry on in the file, at the same place. */
  private switchToWhole(detail: PartDetail): void {
    const source = sourceFor(detail, this.state.time);
    if (!source) return;
    const other = this.active === 0 ? 1 : 0;
    this.standby.removeAttribute("src");
    this.loaded[other] = null;
    this.begin(source, this.state.playing);
  }

  /** What was waited for may be made now: play it. */
  private goOn(detail: PartDetail, piece: number): void {
    const next = nextPieceSource(detail, piece);
    if (next) this.advance(next);
  }

  /** Move on to the next piece, which the other element holds (or is given now), and make that the playing one. */
  private advance(next: Extract<PlaySource, { kind: "piece" }>): void {
    const finished = this.element;
    finished.pause();
    this.active = this.active === 0 ? 1 : 0;
    const element = this.element;
    if (this.loaded[this.active] !== next.url) {
      this.loaded[this.active] = next.url;
      element.src = next.url;
    }
    this.source = next;
    this.seekTo = 0;
    element.currentTime = 0;
    element.playbackRate = this.state.speed;
    this.set({ time: next.start, piece: next.piece, waiting: false });
    this.play();
    if (this.state.detail) this.preload(this.state.detail, next.piece);
  }

  // ------------------------------------------------------------------ playing and stopping
  private play(): void {
    this.touched = true; // sound that was asked for makes the place the person's: it is worth keeping from here
    this.setPlaying(true);
    const result = this.element.play();
    if (result && typeof result.catch === "function") {
      const element = this.element;
      result.catch((error: unknown) => {
        if (this.disposed || element !== this.element) return;
        if (error instanceof DOMException && error.name === "AbortError") return; // a new file was loaded over it: not a failure
        if (error instanceof DOMException && error.name === "NotAllowedError") {
          this.setPlaying(false);
          this.set({ blocked: true });
        } else {
          this.setPlaying(false);
          this.set({ problem: "The sound could not be played." });
        }
      });
    }
  }

  /** Playing or not, and the position reported every few seconds while it is, and once when it stops (§38.7). */
  private setPlaying(playing: boolean): void {
    const was = this.state.playing;
    this.set({ playing, blocked: playing ? false : this.state.blocked });
    if (playing && !this.timer) this.timer = setInterval(() => this.save(), this.env.saveEveryMs ?? SAVE_EVERY_MS);
    if (!playing && this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    if (was && !playing) this.save();
  }

  /** Report where the person is. Not before they have played or moved (the place a reading has is theirs, and opening one must not lose it), and a failure to report is
   *  not worth interrupting anyone for: the next report will do. */
  save(): void {
    const detail = this.state.detail;
    if (!this.touched || !detail) return;
    this.api.savePosition(this.env.id, positionFor(this.state.part, detail.pieces, this.state.time, detail.sample_rate ?? 0)).catch(() => undefined);
  }

  /** Stop the sound and forget what was loaded: another part is to be opened, or the view is closing. */
  private halt(): void {
    this.setPlaying(false);
    for (const element of this.env.audios) {
      element.pause();
      element.removeAttribute("src");
    }
    this.loaded = [null, null];
    this.source = null;
    this.seekTo = null;
    this.pending = null;
  }

  // ------------------------------------------------------------------ what the elements tell
  private onTime(index: 0 | 1): void {
    if (index !== this.active || !this.source) return;
    const time = partTime(this.source, this.element.currentTime);
    const detail = this.state.detail;
    this.set({ time, piece: detail ? pieceAt(detail.pieces, time, detail.sample_rate ?? 0) : null });
  }

  private onLoaded(index: 0 | 1): void {
    if (index !== this.active || this.seekTo === null) return;
    const wanted = this.seekTo;
    this.seekTo = null;
    if (Math.abs(this.element.currentTime - wanted) > 0.25) this.element.currentTime = wanted;
  }

  private onError(index: 0 | 1): void {
    if (index !== this.active || !this.source) return;
    this.setPlaying(false);
    this.set({ problem: "The sound could not be played.", waiting: false });
  }

  private onEnded(index: 0 | 1): void {
    const detail = this.state.detail;
    if (index !== this.active || !this.source || !detail) return;
    if (this.source.kind === "piece") {
      const source = this.source;
      const next = nextPieceSource(detail, source.piece);
      if (next) return this.advance(next);
      // the clock is where the sound ended, not where it was last reported (a fraction of a second earlier): what comes next starts from here
      const played = detail.pieces.find((p) => p.idx === source.piece);
      if (played?.frames && detail.sample_rate) this.set({ time: source.start + played.frames / detail.sample_rate });
      if (!(detail.complete && detail.url)) {
        // the end of what is made: wait for the rest if it is being made, else that is the end
        if (this.env.making()) this.set({ waiting: true });
        else this.setPlaying(false);
        return;
      }
    }
    this.endOfPart();
  }

  /** The part has been played to its end: the next one, which may not be made yet, or the end of the reading. */
  private endOfPart(): void {
    const next = this.state.part + 1;
    if (next < this.env.parts().length) this.open(next, 0, true);
    else {
      this.setPlaying(false);
      this.save();
    }
  }

  // ------------------------------------------------------------------ what the person does
  toggle(): void {
    const detail = this.state.detail;
    if (!detail) return;
    this.touched = true;
    if (this.state.playing) {
      this.element.pause();
      this.set({ waiting: false });
      this.setPlaying(false);
      return;
    }
    if (!this.source) {
      const source = sourceFor(detail, this.state.time);
      if (source) this.begin(source, true);
      else {
        this.pending = { time: this.state.time, want: true, resume: null };
        this.set({ playing: this.env.making(), waiting: this.env.making() });
      }
      return;
    }
    this.play();
  }

  /** Move to `seconds` into the part and carry on as it was (playing or paused). */
  seek(seconds: number): void {
    const detail = this.state.detail;
    if (!detail) return;
    this.touched = true;
    const source = sourceFor(detail, seconds);
    if (source) this.begin(source, this.state.playing);
  }

  skip(delta: number): void {
    this.seek(seekedTime(this.state.time, delta, this.state.length));
  }

  /** Play the piece from its start: "click any paragraph to hear from there". A piece not made has nothing to hear. */
  playPiece(piece: number): void {
    const detail = this.state.detail;
    const found = detail?.pieces.find((p) => p.idx === piece);
    if (!detail || !found || found.start_frame === null || !detail.sample_rate) return;
    this.touched = true;
    const source = sourceFor(detail, found.start_frame / detail.sample_rate);
    if (source) this.begin(source, true);
  }

  /** J: the paragraph before (or this one again, once well into it); from the start of a part, the part before. */
  previous(): void {
    const detail = this.state.detail;
    if (!detail) return;
    const to = previousTime(detail.pieces, this.state.time, detail.sample_rate ?? 0);
    if (to !== null) this.seek(to);
    else if (this.state.part > 0) {
      this.touched = true;
      this.open(this.state.part - 1, 0, this.state.playing);
    }
  }

  /** K: the next paragraph that is made; from the last one, the next part if there is one. */
  next(): void {
    const detail = this.state.detail;
    if (!detail) return;
    const to = nextTime(detail.pieces, this.state.time, detail.sample_rate ?? 0);
    if (to !== null) this.seek(to);
    else if (this.state.part + 1 < this.env.parts().length) {
      this.touched = true;
      this.open(this.state.part + 1, 0, this.state.playing);
    }
  }

  /** Open a part from its start and play it. */
  openPart(part: number): void {
    this.touched = true;
    this.open(part, 0, true);
  }

  setSpeed(speed: number): void {
    for (const element of this.env.audios) element.playbackRate = speed;
    this.set({ speed });
  }

  /** Stop, report the place, and let go of the elements. */
  dispose(): void {
    if (this.disposed) return;
    if (!this.state.playing) this.save(); // (while playing, stopping reports it)
    this.halt();
    for (const { element, type, handler } of this.listeners) element.removeEventListener(type, handler);
    this.listeners.length = 0;
    this.disposed = true;
  }
}
