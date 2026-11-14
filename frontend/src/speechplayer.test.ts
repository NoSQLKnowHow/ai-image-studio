import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { PlayerEngine, SAVE_EVERY_MS, type PlayerState } from "./player";
import type { PartDetail, PartPiece, ReadingInfo, ReadingPart } from "./speechTypes";
import { makePartDetail, makeSpeechRun } from "./testdata";

// What plays a reading (DESIGN.md §38.7), with stand-ins for the two audio elements: what each is given, what it is told to do, and what the player says of it as the
// elements report, as a browser's would, that time has moved on or that a file has ended. A frame is a millisecond here (rate 1000) so that a place can be read off.

/** An audio element with the parts of one that the player uses. `srcLog` keeps every file it was given, so that "not loaded again" can be seen. */
class StandIn extends EventTarget {
  currentTime = 0;
  playbackRate = 1;
  preservesPitch = false;
  preload = "none";
  srcLog: string[] = [];
  private held: string | null = null;
  /** The files it was given, not counting the times it was emptied. */
  get loads(): string[] {
    return this.srcLog.filter((entry) => entry !== "(removed)");
  }
  play = vi.fn(async () => undefined);
  pause = vi.fn();
  set src(value: string) {
    this.srcLog.push(value);
    this.held = value;
    // loading a file puts an element back at its start, at its default speed and (a play that was under way having been given up) paused: the player must say all
    // of that again, which is why it does
    this.playbackRate = 1;
    this.currentTime = 0;
  }
  get src(): string {
    return this.held ?? "";
  }
  removeAttribute(name: string): void {
    if (name === "src") {
      this.held = null;
      this.srcLog.push("(removed)");
    }
  }
  tell(type: string): void {
    this.dispatchEvent(new Event(type));
  }
  at(seconds: number): void {
    this.currentTime = seconds;
    this.tell("timeupdate");
  }
}

const RATE = 1000;
const piece = (idx: number, start: number | null, part = 0, frames = 2000): PartPiece => ({
  idx, kind: "paragraph", text: `Paragraph ${idx}`, continues: false, speaker: null, status: start === null ? "pending" : "done", frames: start === null ? null : frames,
  start_frame: start, error: null, url: start === null ? null : `/p${part}/${idx}.wav`,
});
/** A part of five pieces, two seconds each, of which `made` are made, and none of it whole yet. */
const partial = (made: number, part = 0): PartDetail => makePartDetail({
  idx: part, complete: false, url: null, download_url: null, sample_rate: RATE, total_frames: made * 2000,
  pieces: [0, 1, 2, 3, 4].map((i) => piece(i, i < made ? i * 2000 : null, part)),
});
/** A part that is complete: three pieces, six seconds, one file. */
const whole = (part = 0): PartDetail => makePartDetail({
  idx: part, complete: true, url: `/w/${part}.wav`, sample_rate: RATE, total_frames: 6000, pieces: [piece(0, 0, part), piece(1, 2000, part), piece(2, 4000, part)],
});
const nothing = (part = 0): PartDetail => makePartDetail({ idx: part, complete: false, url: null, sample_rate: null, total_frames: 0, pieces: [0, 1, 2].map((i) => piece(i, null, part)) });

const settle = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};

function setup(details: PartDetail[], { making = false, speed = 1, parts = details.length }: { making?: boolean; speed?: number; parts?: number } = {}) {
  const a = new StandIn();
  const b = new StandIn();
  const flags = { making };
  const answers = [...details];
  const api = {
    part: vi.fn(async (_id: string, n: number) => {
      const found = answers[n];
      if (!found) throw new ApiError("That part does not exist.", 404, "not_found");
      return found;
    }),
    savePosition: vi.fn(async () => undefined),
  };
  const states: PlayerState[] = [];
  const list: ReadingPart[] = Array.from({ length: parts }, (_, i) => ({ idx: i, title: `Part ${i + 1}`, chars: 10, pieces: 3, made: 3, seconds: 6, state: "done" as const }));
  const engine = new PlayerEngine({
    id: "r1", audios: [a as unknown as HTMLAudioElement, b as unknown as HTMLAudioElement], api, onChange: (s) => states.push(s), parts: () => list, making: () => flags.making,
    speed, // (the period of the reports is left to the engine's own default: it is one of the things tried)
  });
  const reading = (position: ReadingInfo["position"] = null): ReadingInfo => ({ ...(makeSpeechRun().reading as ReadingInfo), parts: list, position });
  return { engine, a, b, api, states, flags, answers, list, reading, state: () => engine.snapshot };
}

/** Open the reading from its start and let the part's pieces arrive. */
async function opened(details: PartDetail[], play = true, options: Parameters<typeof setup>[1] = {}) {
  const t = setup(details, options);
  t.engine.start(t.reading(), play);
  await settle();
  return t;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

// ------------------------------------------------------------------ opening
describe("opening a reading", () => {
  it("asks for the part the person stopped in and plays it from that place", async () => {
    const t = setup([whole(0), whole(1)]);
    t.engine.start(t.reading({ part: 1, piece: 0, offset_ms: 3500 }), true);
    expect(t.state()).toMatchObject({ part: 1, loading: true, playing: true });
    await settle();
    expect(t.api.part).toHaveBeenCalledWith("r1", 1);
    expect(t.a.src).toBe("/w/1.wav");
    expect(t.a.currentTime).toBe(3.5);
    expect(t.a.play).toHaveBeenCalledTimes(1);
    expect(t.state()).toMatchObject({ part: 1, loading: false, playing: true, time: 3.5, piece: 1, length: 6, problem: null });
  });

  it("starts at the beginning of the first part when there is no place stored", async () => {
    const t = await opened([whole(0)]);
    expect(t.api.part).toHaveBeenCalledWith("r1", 0);
    expect(t.a.currentTime).toBe(0);
    expect(t.state()).toMatchObject({ time: 0, piece: 0 });
  });

  it("does not take a stored place for another part than the one it opens at", async () => {
    const t = setup([whole(0), whole(1)]);
    t.engine.start(t.reading({ part: 7, piece: 0, offset_ms: 9000 }), true); // a part that is not there: the first
    await settle();
    expect(t.api.part).toHaveBeenCalledWith("r1", 0);
    expect(t.a.currentTime).toBe(0);
  });

  it("does not go past what is made when the stored place is further on than the part is", async () => {
    const t = setup([whole(0)]);
    t.engine.start(t.reading({ part: 0, piece: 2, offset_ms: 99_000 }), true);
    await settle();
    expect(t.a.currentTime).toBe(6);
  });

  it("can open without playing: the file is ready, no sound is made, and Play starts it", async () => {
    const t = await opened([whole(0)], false);
    expect(t.a.src).toBe("/w/0.wav");
    expect(t.a.play).not.toHaveBeenCalled();
    expect(t.state().playing).toBe(false);
    t.engine.toggle();
    expect(t.a.play).toHaveBeenCalledTimes(1);
    expect(t.state().playing).toBe(true);
  });

  it("says why when the part cannot be had, and does not pretend to play", async () => {
    const t = setup([]);
    t.engine.start(t.reading(), true);
    await settle();
    expect(t.state()).toMatchObject({ loading: false, playing: false, problem: "That part does not exist.", detail: null });
    t.api.part.mockRejectedValueOnce(new Error("boom"));
    t.engine.open(0, 0, true);
    await settle();
    expect(t.state().problem).toBe("The part could not be loaded.");
  });

  it("ignores the answer for a part that is no longer the one open", async () => {
    const t = setup([whole(0), whole(1)]);
    let release: (d: PartDetail) => void = () => undefined;
    t.api.part.mockImplementationOnce(() => new Promise<PartDetail>((resolve) => (release = resolve)));
    t.engine.open(0, 0, true); // its answer is held
    t.engine.open(1, 0, true); // another part is wanted before it comes
    await settle();
    expect(t.a.src).toBe("/w/1.wav");
    release(whole(0)); // the late answer for part 0
    await settle();
    expect(t.a.src).toBe("/w/1.wav");
    expect(t.state().part).toBe(1);
  });
});

// ------------------------------------------------------------------ the clock
describe("where the player says it is", () => {
  it("follows the element for a whole part, with the paragraph being spoken", async () => {
    const t = await opened([whole(0)]);
    t.a.at(2.5);
    expect(t.state()).toMatchObject({ time: 2.5, piece: 1 });
    t.a.at(5.9);
    expect(t.state()).toMatchObject({ time: 5.9, piece: 2 });
  });

  it("is the piece's start later for a piece, because its own file starts at nought", async () => {
    const t = await opened([partial(3)]);
    t.a.at(0.5);
    expect(t.state().time).toBe(0.5);
    t.a.tell("ended"); // on to piece 1, which starts 2 s in
    t.b.at(1.25);
    expect(t.state()).toMatchObject({ time: 3.25, piece: 1 });
  });

  it("does not listen to the element that is not playing", async () => {
    const t = await opened([partial(3)]);
    t.b.at(1.5); // the one with the next piece ready
    expect(t.state().time).toBe(0);
  });
});

// ------------------------------------------------------------------ piece by piece
describe("a part that is still being made", () => {
  it("plays the piece the place is in, and has the next one ready in the other element", async () => {
    const t = await opened([partial(3)]);
    expect(t.a.src).toBe("/p0/0.wav");
    expect(t.b.src).toBe("/p0/1.wav");
    expect(t.b.preload).toBe("auto");
    expect(t.a.play).toHaveBeenCalledTimes(1);
    expect(t.b.play).not.toHaveBeenCalled();
    expect(t.state()).toMatchObject({ length: 6, piece: 0, playing: true });
  });

  it("goes on into the next piece when one ends, without loading it again, and gets the one after ready", async () => {
    const t = await opened([partial(3)]);
    t.a.pause.mockClear();
    t.b.currentTime = 1.5; // (an element that was loaded early and left somewhere: the next piece is to start at its start)
    t.a.tell("ended");
    expect(t.a.pause).toHaveBeenCalledTimes(1); // the piece that ended is not left in the way
    expect(t.b.play).toHaveBeenCalledTimes(1);
    expect(t.b.currentTime).toBe(0);
    expect(t.b.loads).toEqual(["/p0/1.wav"]); // loaded once, while the first played
    expect(t.a.srcLog.at(-1)).toBe("/p0/2.wav"); // and the first element is given the one after
    expect(t.state()).toMatchObject({ time: 2, piece: 1, playing: true, waiting: false });
    t.b.tell("ended");
    expect(t.a.play).toHaveBeenCalledTimes(2);
    expect(t.state()).toMatchObject({ time: 4, piece: 2 });
  });

  it("waits, still playing, at the end of what is made while the reading is being made, and goes on when the next piece is", async () => {
    const t = await opened([partial(1)], true, { making: true });
    t.a.tell("ended");
    expect(t.state()).toMatchObject({ playing: true, waiting: true, piece: 0 });
    t.answers[0] = partial(2); // the next piece was made
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 1, frames: 2000 });
    await settle();
    expect(t.state()).toMatchObject({ playing: true, waiting: false, piece: 1, time: 2 });
    expect(t.b.src).toBe("/p0/1.wav"); // it was not ready (not made when the first began): it is given now
    expect(t.b.play).toHaveBeenCalledTimes(1);
  });

  it("stops at the end of what is made when nothing more is going to be", async () => {
    const t = await opened([partial(1)], true, { making: false });
    t.a.tell("ended");
    expect(t.state()).toMatchObject({ playing: false, waiting: false });
  });

  it("looks again when a piece of this part is made, and not for another reading's or another part's", async () => {
    const t = await opened([partial(2)]);
    t.api.part.mockClear();
    t.engine.onPiece({ run_id: "other", part: 0, idx: 2, frames: 1 });
    t.engine.onPiece({ run_id: "r1", part: 1, idx: 0, frames: 1 });
    await settle();
    expect(t.api.part).not.toHaveBeenCalled();
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 2, frames: 1 });
    await settle();
    expect(t.api.part).toHaveBeenCalledTimes(1);
    expect(t.api.part).toHaveBeenCalledWith("r1", 0);
  });

  it("has the next piece ready as soon as it is made, if it was not when this one began", async () => {
    const t = await opened([partial(1)], true, { making: true });
    expect(t.b.src).toBe(""); // nothing after the first piece yet
    t.answers[0] = partial(2);
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 1, frames: 2000 });
    await settle();
    expect(t.b.src).toBe("/p0/1.wav");
    t.a.tell("ended");
    expect(t.b.loads).toEqual(["/p0/1.wav"]); // so that it goes on with nothing between
  });

  it("switches to the whole file when the part is complete, at the same place, and keeps playing", async () => {
    const t = await opened([partial(3)]);
    t.a.tell("ended"); // into piece 1
    t.b.at(1.5); // 3.5 s into the part
    t.answers[0] = whole(0);
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 2, frames: 2000 });
    await settle();
    expect(t.b.src).toBe("/w/0.wav"); // the element that was playing
    expect(t.b.currentTime).toBe(3.5);
    expect(t.state()).toMatchObject({ playing: true, time: 3.5, piece: 1 });
    expect(t.a.srcLog.at(-1)).toBe("(removed)"); // the one that held the next piece lets go of it
    t.b.at(4.25);
    expect(t.state().time).toBe(4.25); // and from here the clock is the file's own
  });

  it("switches while paused too, and stays paused", async () => {
    const t = await opened([partial(3)], false);
    t.answers[0] = whole(0);
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 2, frames: 2000 });
    await settle();
    expect(t.a.src).toBe("/w/0.wav");
    expect(t.state().playing).toBe(false);
    expect(t.a.play).not.toHaveBeenCalled();
  });

  it("carries on from the end of the last piece when the part completes while it was waiting", async () => {
    const t = await opened([partial(1)], true, { making: true });
    t.a.tell("ended");
    expect(t.state().waiting).toBe(true);
    t.answers[0] = whole(0);
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 2, frames: 2000 });
    await settle();
    expect(t.state()).toMatchObject({ playing: true, waiting: false, time: 2 });
    expect(t.a.src).toBe("/w/0.wav");
    expect(t.a.currentTime).toBe(2);
  });
});

// ------------------------------------------------------------------ nothing made yet
describe("a part with nothing made yet", () => {
  it("waits to play while the reading is being made, and plays the first piece when it is made", async () => {
    const t = await opened([nothing(0)], true, { making: true });
    expect(t.state()).toMatchObject({ playing: true, waiting: true, detail: expect.anything() });
    expect(t.a.src).toBe("");
    t.answers[0] = partial(1);
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 0, frames: 2000 });
    await settle();
    expect(t.a.src).toBe("/p0/0.wav");
    expect(t.state()).toMatchObject({ playing: true, waiting: false, piece: 0 });
  });

  it("does not wait for what will never be made: a reading that is over has nothing to wait for", async () => {
    const t = await opened([nothing(0)], true, { making: false });
    expect(t.state()).toMatchObject({ playing: false, waiting: false });
  });

  it("opened without playing, stays still when the first piece is made, and plays when asked", async () => {
    const t = await opened([nothing(0)], false, { making: true });
    expect(t.state()).toMatchObject({ playing: false, waiting: false });
    t.answers[0] = partial(1);
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 0, frames: 2000 });
    await settle();
    expect(t.a.src).toBe("/p0/0.wav");
    expect(t.a.play).not.toHaveBeenCalled();
    t.engine.toggle();
    expect(t.a.play).toHaveBeenCalledTimes(1);
  });

  it("Play with nothing to play says it is waiting while the reading is being made", async () => {
    const t = await opened([nothing(0)], false, { making: true });
    t.engine.toggle();
    expect(t.state()).toMatchObject({ playing: true, waiting: true });
    t.engine.toggle();
    expect(t.state()).toMatchObject({ playing: false, waiting: false });
  });
});

// ------------------------------------------------------------------ the end of a part
describe("the end of a part", () => {
  it("goes on into the next part, which the page asks for and plays from its start", async () => {
    const t = await opened([whole(0), whole(1)]);
    t.a.tell("ended");
    await settle();
    expect(t.api.part).toHaveBeenLastCalledWith("r1", 1);
    expect(t.state()).toMatchObject({ part: 1, playing: true, time: 0 });
    expect(t.a.src).toBe("/w/1.wav");
    expect(t.a.currentTime).toBe(0);
  });

  it("stops at the end of the last part, and reports where it ended", async () => {
    const t = await opened([whole(0)]);
    t.a.at(5.99);
    t.a.tell("ended");
    expect(t.state().playing).toBe(false);
    expect(t.api.savePosition).toHaveBeenCalledWith("r1", { part: 0, piece: 2, offset_ms: 5990 });
  });

  it("waits for the next part to be made when the reading is being made, and plays it when it is", async () => {
    const t = await opened([whole(0), nothing(1)], true, { making: true });
    t.a.tell("ended");
    await settle();
    expect(t.state()).toMatchObject({ part: 1, playing: true, waiting: true });
    t.answers[1] = partial(1, 1);
    t.engine.onPiece({ run_id: "r1", part: 1, idx: 0, frames: 2000 });
    await settle();
    expect(t.a.src).toBe("/p1/0.wav");
    expect(t.state()).toMatchObject({ part: 1, playing: true, waiting: false });
  });

  it("stops at the end of a part whose next part will not be made", async () => {
    const t = await opened([whole(0), nothing(1)], true, { making: false });
    t.a.tell("ended");
    await settle();
    expect(t.state()).toMatchObject({ part: 1, playing: false, waiting: false });
  });
});

// ------------------------------------------------------------------ the controls
describe("play, pause and moving about", () => {
  it("pauses, and plays again", async () => {
    const t = await opened([whole(0)]);
    t.a.pause.mockClear();
    t.engine.toggle();
    expect(t.a.pause).toHaveBeenCalledTimes(1);
    expect(t.state().playing).toBe(false);
    t.engine.toggle();
    expect(t.a.play).toHaveBeenCalledTimes(2);
    expect(t.state().playing).toBe(true);
  });

  it("does nothing before the part's pieces are known", () => {
    const t = setup([whole(0)]);
    t.engine.toggle();
    t.engine.skip(15);
    t.engine.seek(3);
    t.engine.next();
    t.engine.previous();
    t.engine.playPiece(1);
    expect(t.a.play).not.toHaveBeenCalled();
    expect(t.state().detail).toBeNull();
  });

  it("moves about in a whole part by moving the element, without loading the file again, and plays or stays paused as it was", async () => {
    const t = await opened([whole(0)]);
    t.engine.seek(4.5);
    expect(t.a.currentTime).toBe(4.5);
    expect(t.a.loads).toEqual(["/w/0.wav"]);
    expect(t.state()).toMatchObject({ time: 4.5, piece: 2, playing: true });
    t.engine.toggle(); // paused
    const plays = t.a.play.mock.calls.length;
    t.engine.seek(1);
    expect(t.a.currentTime).toBe(1);
    expect(t.state()).toMatchObject({ time: 1, playing: false });
    expect(t.a.play.mock.calls.length).toBe(plays); // moving about while paused makes no sound
  });

  it("asks the element to play again after moving to another file while playing, since loading a file stops an element (and not when paused)", async () => {
    const t = await opened([partial(3)]);
    expect(t.a.play).toHaveBeenCalledTimes(1);
    t.engine.seek(4.5); // another piece: another file
    expect(t.a.play).toHaveBeenCalledTimes(2);
  });

  it("moves about in a part still being made by playing the piece the place is in", async () => {
    const t = await opened([partial(3)]);
    t.engine.seek(4.5);
    expect(t.a.src).toBe("/p0/2.wav");
    expect(t.a.currentTime).toBe(0.5);
    expect(t.state()).toMatchObject({ time: 4.5, piece: 2 });
    t.engine.seek(1);
    expect(t.a.src).toBe("/p0/0.wav");
    expect(t.a.currentTime).toBe(1);
  });

  it("goes only as far as what is made", async () => {
    const t = await opened([partial(3)]);
    t.engine.seek(99);
    expect(t.a.src).toBe("/p0/2.wav");
    expect(t.a.currentTime).toBe(2); // the end of the last piece made
    expect(t.state().time).toBe(6);
  });

  it("skips forward and back by the seconds asked, and stays inside the part", async () => {
    const t = await opened([whole(0)]);
    t.engine.skip(15);
    expect(t.state().time).toBe(6);
    t.engine.skip(-4);
    expect(t.state().time).toBe(2);
    t.engine.skip(-15);
    expect(t.state().time).toBe(0);
  });

  it("plays a paragraph from its start when it is clicked, even if paused, and does nothing for one that is not made", async () => {
    const t = await opened([partial(3)], false);
    t.engine.playPiece(2);
    expect(t.a.src).toBe("/p0/2.wav");
    expect(t.a.currentTime).toBe(0);
    expect(t.a.play).toHaveBeenCalledTimes(1);
    expect(t.state()).toMatchObject({ playing: true, time: 4, piece: 2 });
    t.a.play.mockClear();
    t.engine.playPiece(3); // not made
    t.engine.playPiece(99); // not there
    expect(t.a.play).not.toHaveBeenCalled();
    expect(t.state().time).toBe(4);
  });

  it("plays a paragraph of a whole part from its start by moving the element", async () => {
    const t = await opened([whole(0)]);
    t.engine.playPiece(1);
    expect(t.a.currentTime).toBe(2);
    expect(t.a.loads).toEqual(["/w/0.wav"]);
  });

  it("goes to the next and the previous paragraph, as the keys J and K do", async () => {
    const t = await opened([whole(0)]);
    t.engine.next();
    expect(t.state()).toMatchObject({ time: 2, piece: 1 });
    t.engine.next();
    expect(t.state()).toMatchObject({ time: 4, piece: 2 });
    t.engine.previous(); // from the very start of paragraph 2: the one before
    expect(t.state()).toMatchObject({ time: 2, piece: 1 });
  });

  it("goes from the last paragraph to the next part, and from the first to the part before, keeping playing or paused as it was", async () => {
    const t = await opened([whole(0), whole(1)], true);
    t.engine.seek(5);
    t.engine.next();
    await settle();
    expect(t.state()).toMatchObject({ part: 1, time: 0, playing: true });
    t.engine.toggle(); // paused
    t.engine.previous(); // at the very start of part 1
    await settle();
    expect(t.state()).toMatchObject({ part: 0, time: 0, playing: false });
    t.engine.previous(); // the start of the first part: nowhere to go
    await settle();
    expect(t.state().part).toBe(0);
  });

  it("does not go past the last part", async () => {
    const t = await opened([whole(0)]);
    t.engine.seek(5);
    t.engine.next();
    await settle();
    expect(t.state().part).toBe(0);
    expect(t.api.part).toHaveBeenCalledTimes(1);
  });

  it("opens another part from its start and plays it", async () => {
    const t = await opened([whole(0), whole(1)], false);
    t.engine.openPart(1);
    await settle();
    expect(t.state()).toMatchObject({ part: 1, playing: true, time: 0 });
    expect(t.a.src).toBe("/w/1.wav");
  });

  it("changes the speed of both elements, and keeps it for every file loaded after (loading a file puts an element back at the default speed)", async () => {
    const t = await opened([partial(3)], true, { speed: 1.5 });
    expect(t.a.playbackRate).toBe(1.5); // a file loaded into the playing element is played at the speed asked for
    t.engine.setSpeed(0.75);
    expect(t.a.playbackRate).toBe(0.75);
    expect(t.b.playbackRate).toBe(0.75);
    expect(t.state().speed).toBe(0.75);
    t.engine.seek(2.5); // another piece is loaded into the playing element, and the one after it into the other: both put back at the default
    expect(t.a.playbackRate).toBe(0.75);
    t.a.tell("ended"); // on into the piece that was loaded early
    expect(t.b.playbackRate).toBe(0.75);
    expect(t.a.preservesPitch).toBe(true);
  });
});

// ------------------------------------------------------------------ the place, kept on the server
describe("reporting where the person is", () => {
  it("reports every ten seconds while playing, with the part, the paragraph and the time in the part", async () => {
    const t = await opened([whole(0)]);
    t.a.at(2.5);
    await vi.advanceTimersByTimeAsync(9_999);
    expect(t.api.savePosition).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(t.api.savePosition).toHaveBeenCalledTimes(1);
    expect(t.api.savePosition).toHaveBeenLastCalledWith("r1", { part: 0, piece: 1, offset_ms: 2500 });
    t.a.at(4.25);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.api.savePosition).toHaveBeenCalledTimes(2);
    expect(t.api.savePosition).toHaveBeenLastCalledWith("r1", { part: 0, piece: 2, offset_ms: 4250 });
  });

  it("reports once when paused, and not again while it stays paused", async () => {
    const t = await opened([whole(0)]);
    t.a.at(3.1);
    t.engine.toggle();
    expect(t.api.savePosition).toHaveBeenCalledTimes(1);
    expect(t.api.savePosition).toHaveBeenLastCalledWith("r1", { part: 0, piece: 1, offset_ms: 3100 });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(t.api.savePosition).toHaveBeenCalledTimes(1);
  });

  it("does not report a place before anything was played or moved: opening a reading must not lose the place it has", async () => {
    const t = await opened([whole(0)], false);
    await vi.advanceTimersByTimeAsync(20_000);
    t.engine.dispose();
    expect(t.api.savePosition).not.toHaveBeenCalled();
  });

  it("reports the place of a person who moved while paused, when the view is closed", async () => {
    const t = await opened([whole(0)], false);
    t.engine.seek(3.5);
    expect(t.api.savePosition).not.toHaveBeenCalled(); // not while paused
    t.engine.dispose();
    expect(t.api.savePosition).toHaveBeenCalledTimes(1);
    expect(t.api.savePosition).toHaveBeenCalledWith("r1", { part: 0, piece: 1, offset_ms: 3500 });
  });

  it("reports once, not twice, when the view is closed while playing", async () => {
    const t = await opened([whole(0)]);
    t.a.at(1);
    t.engine.dispose();
    expect(t.api.savePosition).toHaveBeenCalledTimes(1);
  });

  it("goes on playing when a report fails: the next one will do", async () => {
    const t = await opened([whole(0)]);
    // (a plain function, not a mock: a mock attaches its own handlers to what it returns, so a rejection from one is never left unhandled, whatever the code does)
    let reports = 0;
    (t.api as { savePosition: unknown }).savePosition = () => {
      reports += 1;
      return Promise.reject(new Error("offline"));
    };
    const unhandled: unknown[] = [];
    const note = (reason: unknown) => void unhandled.push(reason);
    process.on("unhandledRejection", note);
    try {
      await vi.advanceTimersByTimeAsync(10_000); // the report is made, and refused
      await settle();
      vi.useRealTimers();
      await new Promise((resolve) => setTimeout(resolve, 0)); // a rejection nobody handled is only noticed once the current turn of the event loop is over
    } finally {
      process.off("unhandledRejection", note);
    }
    expect(reports).toBe(1);
    expect(unhandled).toEqual([]); // a report that failed is dealt with here: it is no one else's to catch
    expect(t.state().playing).toBe(true);
    expect(t.state().problem).toBeNull();
  });

  it("reports every ten seconds and not more often (the engine's own period)", () => {
    expect(SAVE_EVERY_MS).toBe(10_000);
  });

  it("opening a part to play it counts as playing: the place is kept", async () => {
    const t = await opened([whole(0), whole(1)], false);
    t.engine.openPart(1);
    await settle();
    t.engine.toggle();
    expect(t.api.savePosition).toHaveBeenLastCalledWith("r1", { part: 1, piece: 0, offset_ms: 0 });
  });
});

// ------------------------------------------------------------------ when the sound does not come
describe("when the sound does not come", () => {
  it("says the browser wants a press when it will not start sound by itself, and plays at the next press", async () => {
    const t = setup([whole(0)]);
    t.a.play.mockRejectedValueOnce(new DOMException("not allowed", "NotAllowedError"));
    t.engine.start(t.reading(), true);
    await settle();
    expect(t.state()).toMatchObject({ playing: false, blocked: true, problem: null });
    t.engine.toggle();
    expect(t.state()).toMatchObject({ playing: true, blocked: false });
  });

  it("does not take the interruption of one file by the next for a failure", async () => {
    const t = setup([whole(0)]);
    t.a.play.mockRejectedValueOnce(new DOMException("interrupted", "AbortError"));
    t.engine.start(t.reading(), true);
    await settle();
    expect(t.state()).toMatchObject({ playing: true, blocked: false, problem: null });
  });

  it("says so for any other refusal", async () => {
    const t = setup([whole(0)]);
    t.a.play.mockRejectedValueOnce(new DOMException("no", "NotSupportedError"));
    t.engine.start(t.reading(), true);
    await settle();
    expect(t.state()).toMatchObject({ playing: false, blocked: false, problem: "The sound could not be played." });
  });

  it("says so when the file fails to load or play", async () => {
    const t = await opened([whole(0)]);
    t.a.tell("error");
    expect(t.state()).toMatchObject({ playing: false, problem: "The sound could not be played." });
  });

  it("puts the player where it was asked to be when the browser ignored the place it was given before the file had loaded", async () => {
    const t = await opened([whole(0)]);
    t.engine.seek(4);
    t.a.currentTime = 0; // a browser that dropped the early seek
    t.a.tell("loadedmetadata");
    expect(t.a.currentTime).toBe(4);
    t.a.currentTime = 4.1; // and one that did it right is left alone
    t.a.tell("loadedmetadata");
    expect(t.a.currentTime).toBe(4.1);
  });
});

// ------------------------------------------------------------------ letting go
describe("closing", () => {
  it("stops the sound, lets go of the files, and does not listen to the elements or to late answers any more", async () => {
    const t = await opened([partial(3)]);
    t.engine.dispose();
    const told = t.states.length; // what the page was told up to and including the closing
    expect(t.a.pause).toHaveBeenCalled();
    expect(t.b.pause).toHaveBeenCalled();
    expect(t.a.src).toBe("");
    expect(t.b.src).toBe("");
    t.a.at(1);
    t.a.tell("ended");
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 3, frames: 1 });
    await settle();
    expect(t.states.length).toBe(told); // nothing is told to the page once it is closed
    t.engine.dispose(); // twice is no harm
    expect(t.api.savePosition).toHaveBeenCalledTimes(1);
  });

  it("is not told of an answer that comes after it was closed", async () => {
    const t = setup([whole(0)]);
    t.engine.start(t.reading(), true);
    t.engine.dispose();
    await settle();
    expect(t.a.src).toBe("");
    expect(t.a.play).not.toHaveBeenCalled();
  });
});


// ------------------------------------------------------------------ what arrives late, or from the wrong element
describe("what arrives late or from the element that is not playing", () => {
  it("is not told of the other element's time passing", async () => {
    const t = await opened([partial(3)]);
    const told = t.states.length;
    t.b.at(1.5); // the one with the next piece ready
    expect(t.states.length).toBe(told);
  });

  it("does not take a late 'ended' from the element that played before for another end of a piece", async () => {
    const t = await opened([partial(3)]);
    t.a.tell("ended"); // on into piece 1, in the other element
    expect(t.state().piece).toBe(1);
    t.a.tell("ended"); // the first one reports its end again, late
    expect(t.state()).toMatchObject({ piece: 1, time: 2, playing: true });
    expect(t.b.play).toHaveBeenCalledTimes(1);
  });

  it("is not stopped by a file that fails to load in the element that is only getting the next piece ready", async () => {
    const t = await opened([partial(3)]);
    t.b.tell("error"); // the next piece's file could not be had
    expect(t.state()).toMatchObject({ playing: true, problem: null });
  });

  it("does not load the next piece into the other element again when nothing about it changed", async () => {
    const t = await opened([partial(3)]);
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 2, frames: 2000 });
    await settle();
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 2, frames: 2000 });
    await settle();
    expect(t.b.loads).toEqual(["/p0/1.wav"]);
  });

  it("does not take the refusal of a play that was begun in the element it has since left", async () => {
    const t = setup([partial(3)]);
    let refuse: (error: unknown) => void = () => undefined;
    t.a.play.mockImplementationOnce(() => new Promise<undefined>((_, reject) => (refuse = reject)));
    t.engine.start(t.reading(), true);
    await settle();
    t.a.tell("ended"); // on into the other element
    refuse(new DOMException("not allowed", "NotAllowedError")); // the first one's play is refused, long after
    await settle();
    expect(t.state()).toMatchObject({ playing: true, blocked: false, problem: null });
  });

  it("goes on into the next part keeping paused when it was paused (K from the last paragraph)", async () => {
    const t = await opened([whole(0), whole(1)], true);
    t.engine.toggle(); // paused
    t.engine.seek(5);
    t.engine.next();
    await settle();
    expect(t.state()).toMatchObject({ part: 1, playing: false });
    expect(t.a.src).toBe("/w/1.wav");
  });

  it("stops the sound of the part it leaves at once, before the pieces of the next have arrived", async () => {
    const t = await opened([whole(0), whole(1)]);
    let release: (d: PartDetail) => void = () => undefined;
    t.api.part.mockImplementationOnce(() => new Promise<PartDetail>((resolve) => (release = resolve)));
    t.a.pause.mockClear();
    t.engine.openPart(1);
    expect(t.a.pause).toHaveBeenCalled();
    expect(t.a.src).toBe(""); // nothing of the old part is left in the element
    expect(t.state()).toMatchObject({ part: 1, loading: true });
    release(whole(1));
    await settle();
    expect(t.a.src).toBe("/w/1.wav");
  });

  it("takes only the answer for the part that is open when the answers come in the wrong order", async () => {
    const t = setup([whole(0), whole(1)]);
    const held: Array<(d: PartDetail) => void> = [];
    t.api.part.mockImplementation(() => new Promise<PartDetail>((resolve) => void held.push(resolve)));
    t.engine.open(0, 0, true); // asks for part 0
    t.engine.open(1, 0, true); // then, before any answer, for part 1
    held[0](whole(0)); // the answer for part 0 comes first
    await settle();
    expect(t.state()).toMatchObject({ part: 1, detail: null, loading: true });
    held[1](whole(1));
    await settle();
    expect(t.state().detail?.idx).toBe(1);
    expect(t.a.src).toBe("/w/1.wav");
  });

  it("takes the newer answer for a part, and not an older one that comes after it", async () => {
    const t = setup([partial(1)], { making: true });
    const held: Array<(d: PartDetail) => void> = [];
    t.api.part.mockImplementation(() => new Promise<PartDetail>((resolve) => void held.push(resolve)));
    t.engine.start(t.reading(), true); // asks
    t.engine.onPiece({ run_id: "r1", part: 0, idx: 1, frames: 2000 }); // asks again: a piece was made meanwhile
    held[1](partial(2)); // the newer answer comes first
    await settle();
    expect(t.state().detail?.pieces.filter((p) => p.start_frame !== null)).toHaveLength(2);
    held[0](partial(1)); // the older one comes after
    await settle();
    expect(t.state().detail?.pieces.filter((p) => p.start_frame !== null)).toHaveLength(2);
  });
});
