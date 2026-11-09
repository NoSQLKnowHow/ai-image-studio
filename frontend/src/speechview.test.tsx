import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { ReadingView, type ReadingViewProps } from "./components/ReadingView";
import { createEmitter } from "./emitter";
import { PLAYER_PREFS_KEY, SPEEDS, TEXT_SIZES } from "./speech";
import type { PartDetail, PartPiece, ReadingInfo, ReadingPart, ReadingPieceEvent } from "./speechTypes";
import { Call, memoryStore, server } from "./speechTestSupport";
import { SpeechPanel, type SpeechHistory } from "./components/SpeechPanel";
import { CAPS } from "./testdata";
import { makePartDetail, makeSpeechRun } from "./testdata";
import type { SpeechRun } from "./types";

// The reading view (DESIGN.md §38.2, §38.7) as a person meets it: the player's buttons and clock, the parts, the text paragraph by paragraph with the spoken one marked,
// the keys, the preferences, and what happens as a reading is being made. The sound is the player's (speechplayer.test.ts); here the audio elements are real ones whose
// play and pause are watched, and that are told by the tests, as a browser would tell them, that time has moved on.

const RATE = 1000; // a frame is a millisecond
const piece = (idx: number, start: number | null, part = 0, extra: Partial<PartPiece> = {}): PartPiece => ({
  idx, kind: "paragraph", text: `Paragraph ${idx + 1} of part ${part + 1}.`, continues: false, status: start === null ? "pending" : "done", frames: start === null ? null : 2000,
  start_frame: start, error: null, url: start === null ? null : `/p${part}/${idx}.wav`, ...extra,
});
const whole = (part = 0): PartDetail => makePartDetail({
  idx: part, title: `Chapter ${part + 1}`, complete: true, url: `/w/${part}.wav`, download_url: `/w/${part}.wav?download=1`, sample_rate: RATE, total_frames: 6000,
  pieces: [piece(0, 0, part), piece(1, 2000, part), piece(2, 4000, part)],
});
const partial = (made: number, part = 0): PartDetail => makePartDetail({
  idx: part, title: `Chapter ${part + 1}`, complete: false, url: null, download_url: null, sample_rate: RATE, total_frames: made * 2000,
  pieces: [0, 1, 2, 3].map((i) => piece(i, i < made ? i * 2000 : null, part, i === 3 ? { kind: "heading" as const } : {})),
});

const readingPart = (idx: number, state: ReadingPart["state"], made: number, seconds: number): ReadingPart => ({ idx, title: `Chapter ${idx + 1}`, chars: 100, pieces: 3, made, seconds, state });
function runOf(over: Partial<SpeechRun> = {}, info: Partial<ReadingInfo> = {}): SpeechRun {
  const base = makeSpeechRun({ id: "r1", status: "done", ...over });
  return { ...base, reading: { ...(base.reading as ReadingInfo), title: "A long book", parts: [readingPart(0, "done", 3, 6), readingPart(1, "done", 3, 6)], parts_total: 2, parts_done: 2,
    sample_rate: RATE, seconds_made: 12, position: null, ...info } };
}

let plays: ReturnType<typeof vi.spyOn>;
let pauses: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  plays = vi.spyOn(window.HTMLMediaElement.prototype, "play").mockImplementation(async () => undefined);
  pauses = vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function fakeApi(details: PartDetail[]) {
  return {
    part: vi.fn(async (_id: string, n: number) => {
      const found = details[n];
      if (!found) throw new ApiError("That part does not exist.", 404, "not_found");
      return found;
    }),
    savePosition: vi.fn(async () => undefined),
    editPiece: vi.fn(async (_id: string, _part: number, idx: number, text: string) => ({ idx, text })),
  };
}

const flush = () => act(async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
});

async function open(details: PartDetail[], over: Partial<ReadingViewProps> = {}, run: SpeechRun = runOf()) {
  const api = fakeApi(details);
  const pieces = createEmitter<ReadingPieceEvent>();
  const store = memoryStore();
  const onClose = vi.fn();
  const props: ReadingViewProps = { run, pieces, store, onClose, api, saveEveryMs: 10_000, mediaSession: null, ...over };
  const view = render(<ReadingView {...props} />);
  await flush();
  const audios = () => Array.from(view.container.querySelectorAll("audio")) as HTMLAudioElement[];
  const tell = (element: HTMLAudioElement, type: string) => act(() => void element.dispatchEvent(new Event(type)));
  const at = (seconds: number) => act(() => {
    audios()[0].currentTime = seconds;
    audios()[0].dispatchEvent(new Event("timeupdate"));
  });
  return { ...view, api, pieces, store, onClose, props, audios, tell, at, rerender: (next: SpeechRun) => view.rerender(<ReadingView {...props} run={next} />) };
}
const button = (name: string | RegExp) => screen.getByRole("button", { name });
const paragraph = (text: string | RegExp) => screen.getByText(text, { selector: "p" });

// ------------------------------------------------------------------ what is there
describe("the reading view", () => {
  it("has the reading's title and facts, and a button that closes it", async () => {
    const t = await open([whole(0), whole(1)]);
    expect(screen.getByRole("heading", { name: "A long book" })).toBeTruthy();
    expect(screen.getByText("Pasted · Fake speech · Anna")).toBeTruthy();
    fireEvent.click(button("Close player"));
    expect(t.onClose).toHaveBeenCalledTimes(1);
  });

  it("asks for the part the person stopped in and shows its text, paragraph by paragraph", async () => {
    const t = await open([whole(0), whole(1)], {}, runOf({}, { position: { part: 1, piece: 1, offset_ms: 2500 } }));
    expect(t.api.part).toHaveBeenCalledWith("r1", 1);
    const text = screen.getByRole("region", { name: "Text of this part" });
    expect(within(text).getAllByText(/^Paragraph \d of part 2\.$/)).toHaveLength(3);
  });

  it("starts playing from the stored place when opened by Listen, and says Pause", async () => {
    const t = await open([whole(0), whole(1)], {}, runOf({}, { position: { part: 1, piece: 1, offset_ms: 2500 } }));
    expect(plays).toHaveBeenCalled();
    expect(t.audios()[0].getAttribute("src")).toBe("/w/1.wav");
    expect(t.audios()[0].currentTime).toBe(2.5);
    expect(button("Pause")).toBeTruthy();
  });

  it("does not play when it is not told to, and says Play", async () => {
    await open([whole(0)], { autoplay: false });
    expect(plays).not.toHaveBeenCalled();
    expect(button("Play")).toBeTruthy();
  });

  it("marks the paragraph being spoken for a screen reader as well as by colour, and moves the mark as it plays", async () => {
    const t = await open([whole(0), whole(1)]);
    expect(paragraph("Paragraph 1 of part 1.").getAttribute("aria-current")).toBe("true");
    expect(paragraph("Paragraph 1 of part 1.").className).toContain("piece-current");
    await t.at(2.4);
    expect(paragraph("Paragraph 2 of part 1.").getAttribute("aria-current")).toBe("true");
    expect(paragraph("Paragraph 1 of part 1.").hasAttribute("aria-current")).toBe(false);
    expect(document.querySelectorAll('[aria-current="true"].piece')).toHaveLength(1);
  });

  it("dims a paragraph that is not made, says so to a screen reader, and does not play it when pressed", async () => {
    const t = await open([partial(2, 0), whole(1)], {}, runOf({ status: "running" }, { parts: [readingPart(0, "started", 2, 4), readingPart(1, "waiting", 0, 0)] }));
    const pending = paragraph(/^Paragraph 3 of part 1\./);
    expect(pending.className).toContain("piece-pending");
    expect(pending.textContent).toContain("(not made yet)");
    plays.mockClear();
    fireEvent.click(pending);
    expect(plays).not.toHaveBeenCalled();
    expect(t.audios()[0].getAttribute("src")).toBe("/p0/0.wav"); // still where it was
  });

  it("plays from a paragraph when it is pressed", async () => {
    const t = await open([whole(0), whole(1)], { autoplay: false });
    expect(plays).not.toHaveBeenCalled();
    fireEvent.click(paragraph("Paragraph 3 of part 1."));
    expect(t.audios()[0].currentTime).toBe(4);
    expect(plays).toHaveBeenCalled();
    expect(button("Pause")).toBeTruthy();
  });

  it("shows a heading paragraph as a heading, and pressing it plays from it too", async () => {
    const detail = partial(4, 0);
    const t = await open([detail], { autoplay: false }, runOf({}, { parts: [readingPart(0, "done", 4, 8)], parts_total: 1 }));
    const heading = screen.getByRole("heading", { level: 4, name: /^Paragraph 4 of part 1\./ });
    fireEvent.click(heading);
    expect(t.audios()[0].getAttribute("src")).toBe("/p0/3.wav");
  });
});

// ------------------------------------------------------------------ the player
describe("the player's controls", () => {
  it("has the buttons the design names, each with its own name", async () => {
    await open([whole(0), whole(1)]);
    const player = screen.getByRole("group", { name: "Player" });
    expect(within(player).getAllByRole("button").map((b) => b.textContent?.replace(/\s+/g, " ").trim())).toEqual(
      ["Previous paragraph", "−15 sBack 15 seconds", "Pause", "+15 sForward 15 seconds", "Next paragraph"], // (what a person sees is "Previous", "−15 s", "Pause", "+15 s", "Next")
    );
    expect(button("Back 15 seconds")).toBeTruthy();
    expect(button("Forward 15 seconds")).toBeTruthy();
    expect(button("Previous paragraph")).toBeTruthy();
    expect(button("Next paragraph")).toBeTruthy();
  });

  it("pauses and plays", async () => {
    await open([whole(0)]);
    fireEvent.click(button("Pause"));
    expect(button("Play")).toBeTruthy();
    expect(pauses).toHaveBeenCalled();
    fireEvent.click(button("Play"));
    expect(button("Pause")).toBeTruthy();
  });

  it("goes back and forward fifteen seconds, and stays inside the part", async () => {
    const t = await open([whole(0)], { autoplay: false });
    fireEvent.click(button("Forward 15 seconds"));
    expect(t.audios()[0].currentTime).toBe(6); // the part is six seconds
    fireEvent.click(button("Back 15 seconds"));
    expect(t.audios()[0].currentTime).toBe(0);
  });

  it("goes to the next paragraph and the one before", async () => {
    const t = await open([whole(0), whole(1)], { autoplay: false });
    fireEvent.click(button("Next paragraph"));
    expect(t.audios()[0].currentTime).toBe(2);
    fireEvent.click(button("Next paragraph"));
    expect(t.audios()[0].currentTime).toBe(4);
    fireEvent.click(button("Previous paragraph"));
    expect(t.audios()[0].currentTime).toBe(2);
  });

  it("has a slider for the place in the part, named, and saying the time in words", async () => {
    const t = await open([whole(0)], { autoplay: false });
    const slider = screen.getByRole("slider", { name: "Position in this part" }) as HTMLInputElement;
    expect(slider.max).toBe("6");
    expect(slider.getAttribute("aria-valuetext")).toBe("0:00 of 0:06");
    fireEvent.change(slider, { target: { value: "3.5" } });
    expect(t.audios()[0].currentTime).toBe(3.5);
    expect((screen.getByRole("slider", { name: "Position in this part" }) as HTMLInputElement).value).toBe("3.5");
    expect(screen.getByRole("slider", { name: "Position in this part" }).getAttribute("aria-valuetext")).toBe("0:03 of 0:06");
  });

  it("has the slider off until there is something to move in", async () => {
    await open([partial(0, 0)], { autoplay: false }, runOf({ status: "running" }, { parts: [readingPart(0, "waiting", 0, 0), readingPart(1, "waiting", 0, 0)] }));
    expect((screen.getByRole("slider", { name: "Position in this part" }) as HTMLInputElement).disabled).toBe(true);
  });

  it("says the time in the part, and in the whole reading", async () => {
    const t = await open([whole(0), whole(1)], {}, runOf({}, { position: { part: 1, piece: 0, offset_ms: 0 }, seconds_made: 3725 }));
    await t.at(65);
    expect(screen.getByText("1:05 / 0:06")).toBeTruthy(); // (the stand-in element does not stop at the end of the file)
    // the whole: what part 1 holds before this one (six seconds) and the time in this one
    expect(screen.getByText("In all 1:11 of 1:02:05")).toBeTruthy();
  });

  it("says the whole is made so far while the reading is not finished", async () => {
    await open([partial(2, 0)], {}, runOf({ status: "running" }, { parts: [readingPart(0, "started", 2, 4), readingPart(1, "waiting", 0, 0)], seconds_made: 4 }));
    expect(screen.getByText("In all 0:00 of 0:04 made so far")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ what is kept in this browser
describe("speed, text size, line spacing and following along", () => {
  it("has the speeds the design names, and applies the one chosen to what plays, and keeps it", async () => {
    const t = await open([whole(0)]);
    const select = screen.getByRole("combobox", { name: "Speed" }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(SPEEDS.map(String));
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["0.75×", "1×", "1.25×", "1.5×", "1.75×", "2×"]);
    expect(select.value).toBe("1");
    fireEvent.change(select, { target: { value: "1.5" } });
    expect(t.audios()[0].playbackRate).toBe(1.5);
    expect(JSON.parse(t.store.data[PLAYER_PREFS_KEY]).speed).toBe(1.5);
  });

  it("starts at the speed it was kept at", async () => {
    const store = memoryStore({ [PLAYER_PREFS_KEY]: JSON.stringify({ speed: 0.75, textSize: "large", spaced: true, follow: false }) });
    const t = await open([whole(0)], { store });
    expect((screen.getByRole("combobox", { name: "Speed" }) as HTMLSelectElement).value).toBe("0.75");
    expect(t.audios()[0].playbackRate).toBe(0.75);
    expect((screen.getByRole("combobox", { name: "Text size" }) as HTMLSelectElement).value).toBe("large");
    expect((screen.getByRole("checkbox", { name: "Wider line spacing" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Follow along" }) as HTMLInputElement).checked).toBe(false);
  });

  it("has four text sizes, which make the text larger by the amounts the design names, and are kept", async () => {
    const t = await open([whole(0)]);
    const select = screen.getByRole("combobox", { name: "Text size" }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["Small", "Medium", "Large", "Extra large"]);
    const text = () => screen.getByRole("region", { name: "Text of this part" });
    for (const size of TEXT_SIZES) {
      fireEvent.change(select, { target: { value: size.id } });
      expect(text().style.fontSize).toBe(`${size.scale}em`);
      expect(JSON.parse(t.store.data[PLAYER_PREFS_KEY]).textSize).toBe(size.id);
    }
  });

  it("has a switch for wider line spacing, kept", async () => {
    const t = await open([whole(0)]);
    const text = () => screen.getByRole("region", { name: "Text of this part" });
    expect(text().hasAttribute("data-spaced")).toBe(false);
    fireEvent.click(screen.getByRole("checkbox", { name: "Wider line spacing" }));
    expect(text().getAttribute("data-spaced")).toBe("true");
    expect(JSON.parse(t.store.data[PLAYER_PREFS_KEY]).spaced).toBe(true);
  });

  it("two changes in a row both count", async () => {
    const t = await open([whole(0)]);
    fireEvent.change(screen.getByRole("combobox", { name: "Speed" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Follow along" }));
    expect(JSON.parse(t.store.data[PLAYER_PREFS_KEY])).toEqual({ speed: 2, textSize: "medium", spaced: false, follow: false });
  });

  describe("following along", () => {
    let scrollTo: ReturnType<typeof vi.fn>;
    beforeEach(() => {
      scrollTo = vi.fn();
      Object.defineProperty(HTMLElement.prototype, "scrollTo", { value: scrollTo, configurable: true, writable: true });
    });
    afterEach(() => {
      delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollTo;
    });

    it("keeps the paragraph being spoken in view, in the box of the text and not the page, when it changes", async () => {
      const t = await open([whole(0)]);
      scrollTo.mockClear();
      await t.at(2.5); // paragraph 2
      expect(scrollTo).toHaveBeenCalledTimes(1);
      expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "smooth" }));
      await t.at(3); // still paragraph 2
      expect(scrollTo).toHaveBeenCalledTimes(1);
    });

    it("leaves the text where the person put it when Follow along is off", async () => {
      const store = memoryStore({ [PLAYER_PREFS_KEY]: JSON.stringify({ speed: 1, textSize: "medium", spaced: false, follow: false }) });
      const t = await open([whole(0)], { store });
      scrollTo.mockClear();
      await t.at(2.5);
      expect(scrollTo).not.toHaveBeenCalled();
    });
  });
});

// ------------------------------------------------------------------ the parts
describe("the parts of the reading", () => {
  const parts = () => within(screen.getByRole("navigation", { name: "Parts of this reading" })).getAllByRole("listitem");

  it("lists them, each named by its title, with how long it is, and marks the one that is open", async () => {
    await open([whole(0), whole(1)]);
    expect(parts()).toHaveLength(2);
    const first = within(parts()[0]).getByRole("button", { name: "Chapter 1" });
    expect(first.getAttribute("aria-current")).toBe("true");
    expect(first.getAttribute("aria-describedby")).toBeTruthy();
    expect(document.getElementById(first.getAttribute("aria-describedby") as string)?.textContent).toBe("0:06");
    expect(within(parts()[1]).getByRole("button", { name: "Chapter 2" }).hasAttribute("aria-current")).toBe(false);
  });

  it("opens a part from its start and plays it when it is pressed", async () => {
    const t = await open([whole(0), whole(1)]);
    fireEvent.click(within(parts()[1]).getByRole("button", { name: "Chapter 2" }));
    await flush();
    expect(t.api.part).toHaveBeenLastCalledWith("r1", 1);
    expect(t.audios()[0].getAttribute("src")).toBe("/w/1.wav");
    expect(within(parts()[1]).getByRole("button", { name: "Chapter 2" }).getAttribute("aria-current")).toBe("true");
    expect(paragraph("Paragraph 1 of part 2.")).toBeTruthy();
  });

  it("cannot open a part with nothing made, and says it is not made", async () => {
    await open([partial(2, 0)], {}, runOf({ status: "running" }, { parts: [readingPart(0, "started", 2, 4), readingPart(1, "waiting", 0, 0)] }));
    const second = within(parts()[1]).getByRole("button", { name: "Chapter 2" }) as HTMLButtonElement;
    expect(second.disabled).toBe(true);
    expect(document.getElementById(second.getAttribute("aria-describedby") as string)?.textContent).toBe("Not made yet");
    const first = within(parts()[0]).getByRole("button", { name: "Chapter 1" });
    expect(document.getElementById(first.getAttribute("aria-describedby") as string)?.textContent).toBe("0:04 made so far · 2 of 3 paragraphs");
  });

  it("offers each finished part as a download, and not one still being made", async () => {
    await open([whole(0)], {}, runOf({ status: "running" }, { parts: [readingPart(0, "done", 3, 6), readingPart(1, "started", 1, 2)] }));
    const link = within(parts()[0]).getByRole("link", { name: "Download part 1" });
    expect(link.getAttribute("href")).toBe("/api/speech/r1/parts/0.wav?download=1");
    expect(link.hasAttribute("download")).toBe(true);
    expect(within(parts()[1]).queryByRole("link")).toBeNull();
  });

  it("names a part that has no title by its number", async () => {
    await open([whole(0)], {}, runOf({}, { parts: [{ ...readingPart(0, "done", 3, 6), title: "" }, readingPart(1, "done", 3, 6)] }));
    expect(within(parts()[0]).getByRole("button", { name: "Part 1" })).toBeTruthy();
  });
});

// ------------------------------------------------------------------ the keys
describe("the keys", () => {
  const key = (target: Element, name: string, extra: Partial<KeyboardEventInit> = {}) => fireEvent.keyDown(target, { key: name, ...extra });

  it("Space plays and pauses when focus is in the view and not on a control", async () => {
    await open([whole(0)]);
    const text = screen.getByRole("region", { name: "Text of this part" });
    key(text, " ");
    expect(button("Play")).toBeTruthy();
    key(text, " ");
    expect(button("Pause")).toBeTruthy();
  });

  it("leaves Space to a button that has focus, which it presses: once, not twice", async () => {
    const t = await open([whole(0)], { autoplay: false });
    const next = button("Next paragraph");
    const pressed = fireEvent.keyDown(next, { key: " " }); // a browser then clicks the button
    expect(pressed).toBe(true); // not prevented
    expect(button("Play")).toBeTruthy(); // and the view did not start the sound itself
    expect(t.audios()[0].currentTime).toBe(0);
  });

  it("J and K go to the previous and next paragraph, the arrows by ten seconds", async () => {
    const t = await open([whole(0)], { autoplay: false });
    const text = screen.getByRole("region", { name: "Text of this part" });
    key(text, "k");
    expect(t.audios()[0].currentTime).toBe(2);
    key(text, "K");
    expect(t.audios()[0].currentTime).toBe(4);
    key(text, "j");
    expect(t.audios()[0].currentTime).toBe(2);
    key(text, "ArrowRight");
    expect(t.audios()[0].currentTime).toBe(6); // ten seconds on, as far as the part goes
    key(text, "ArrowLeft");
    expect(t.audios()[0].currentTime).toBe(0);
  });

  it("takes the key from the browser when it does something (so that Space does not scroll the page)", async () => {
    await open([whole(0)]);
    expect(key(screen.getByRole("region", { name: "Text of this part" }), " ")).toBe(false);
  });

  it("leaves every other key to the browser", async () => {
    await open([whole(0)]);
    const text = screen.getByRole("region", { name: "Text of this part" });
    expect(key(text, "Enter")).toBe(true);
    expect(key(text, "a")).toBe(true);
    expect(key(text, "ArrowDown")).toBe(true);
  });

  it("does nothing while something is being typed or chosen: a text box, the speed, the slider", async () => {
    const t = await open([partial(2, 0)], { autoplay: false }, runOf({ status: "running" }, { parts: [readingPart(0, "started", 2, 4), readingPart(1, "waiting", 0, 0)] }));
    fireEvent.click(screen.getByRole("button", { name: /Edit the text of paragraph 3/ }));
    const box = screen.getByRole("textbox", { name: "Text of paragraph 3" });
    const before = t.audios()[0].currentTime;
    expect(key(box, " ")).toBe(true);
    expect(key(box, "j")).toBe(true);
    expect(key(screen.getByRole("combobox", { name: "Speed" }), "ArrowRight")).toBe(true);
    expect(key(screen.getByRole("slider", { name: "Position in this part" }), "ArrowRight")).toBe(true);
    expect(t.audios()[0].currentTime).toBe(before);
    expect(button("Play")).toBeTruthy();
  });

  it("leaves a key with a modifier to the browser (Ctrl-K, Alt-Left)", async () => {
    await open([whole(0)]);
    const text = screen.getByRole("region", { name: "Text of this part" });
    expect(key(text, "k", { ctrlKey: true })).toBe(true);
    expect(key(text, "ArrowLeft", { altKey: true })).toBe(true);
    expect(key(text, " ", { metaKey: true })).toBe(true);
  });
});

// ------------------------------------------------------------------ what it says
describe("what the view says of what is happening", () => {
  it("says it is loading, and then not", async () => {
    const api = fakeApi([whole(0)]);
    let release: (d: PartDetail) => void = () => undefined;
    api.part.mockImplementationOnce(() => new Promise<PartDetail>((resolve) => (release = resolve)));
    render(<ReadingView run={runOf()} pieces={createEmitter()} store={memoryStore()} onClose={vi.fn()} api={api} mediaSession={null} />);
    expect(screen.getByText("Loading…")).toBeTruthy();
    release(whole(0));
    await flush();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("says why when the part could not be had", async () => {
    await open([], { autoplay: false });
    expect(screen.getByRole("alert").textContent).toBe("That part does not exist.");
  });

  it("says it is waiting for the next paragraph while playing at the end of what is made", async () => {
    const t = await open([partial(1, 0)], {}, runOf({ status: "running" }, { parts: [readingPart(0, "started", 1, 2), readingPart(1, "waiting", 0, 0)] }));
    await t.tell(t.audios()[0], "ended");
    expect(screen.getByText("Waiting for the next paragraph to be made…")).toBeTruthy();
    expect(button("Pause")).toBeTruthy(); // still playing: it goes on by itself
  });

  it("says when the browser wanted a press before it would play", async () => {
    plays.mockRejectedValueOnce(new DOMException("not allowed", "NotAllowedError"));
    await open([whole(0)]);
    expect(screen.getByText("Your browser wanted a press before it would play sound. Press Play.")).toBeTruthy();
    expect(button("Play")).toBeTruthy();
  });

  it("says when nothing of the part is made", async () => {
    await open([partial(0, 0)], { autoplay: false }, runOf({ status: "failed" }, { parts: [readingPart(0, "waiting", 0, 0), readingPart(1, "waiting", 0, 0)] }));
    expect(screen.getByText("Nothing of this part is made yet.")).toBeTruthy();
  });

  it("says when the sound could not be played", async () => {
    const t = await open([whole(0)]);
    await t.tell(t.audios()[0], "error");
    expect(screen.getByRole("alert").textContent).toBe("The sound could not be played.");
  });
});

// ------------------------------------------------------------------ editing what is not made
describe("editing the text of a paragraph that is not made", () => {
  const running = () => runOf({ status: "running" }, { parts: [readingPart(0, "started", 2, 4), readingPart(1, "waiting", 0, 0)] });
  const edit = () => fireEvent.click(screen.getByRole("button", { name: "Edit the text of paragraph 3" }));

  it("is offered for a paragraph not made, and not for one that is", async () => {
    await open([partial(2, 0)], { autoplay: false }, running());
    expect(screen.getAllByRole("button", { name: /^Edit the text of paragraph/ }).map((b) => b.getAttribute("aria-label"))).toEqual(
      ["Edit the text of paragraph 3", "Edit the text of paragraph 4"],
    );
  });

  it("saves the new text for that paragraph of that part, and asks for the part again", async () => {
    const t = await open([partial(2, 0)], { autoplay: false }, running());
    edit();
    const box = screen.getByRole("textbox", { name: "Text of paragraph 3" }) as HTMLTextAreaElement;
    expect(box.value).toBe("Paragraph 3 of part 1.");
    fireEvent.change(box, { target: { value: "  A better paragraph.  " } });
    t.api.part.mockClear();
    fireEvent.click(button("Save"));
    await flush();
    expect(t.api.editPiece).toHaveBeenCalledWith("r1", 0, 2, "A better paragraph.");
    expect(t.api.part).toHaveBeenCalledWith("r1", 0);
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows what the server says when it refuses, and keeps the box open with what was typed", async () => {
    const t = await open([partial(2, 0)], { autoplay: false }, running());
    t.api.editPiece.mockRejectedValueOnce(new ApiError("That paragraph is already made.", 409, "piece_made"));
    edit();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Changed." } });
    fireEvent.click(button("Save"));
    await flush();
    expect(screen.getByRole("alert").textContent).toBe("That paragraph is already made.");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Changed.");
  });

  it("says so for a failure that is not the server's words", async () => {
    const t = await open([partial(2, 0)], { autoplay: false }, running());
    t.api.editPiece.mockRejectedValueOnce(new Error("offline"));
    edit();
    fireEvent.click(button("Save"));
    await flush();
    expect(screen.getByRole("alert").textContent).toBe("The text could not be saved.");
  });

  it("cannot be saved empty, and Cancel puts the paragraph back as it was", async () => {
    const t = await open([partial(2, 0)], { autoplay: false }, running());
    edit();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
    expect((button("Save") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button("Cancel"));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(paragraph(/^Paragraph 3 of part 1\./)).toBeTruthy();
    expect(t.api.editPiece).not.toHaveBeenCalled();
  });

  it("shows why a paragraph failed, beside it", async () => {
    const detail = partial(2, 0);
    detail.pieces[2] = { ...detail.pieces[2], status: "failed", error: "The voice ran out of memory." };
    await open([detail], { autoplay: false }, running());
    expect(screen.getByRole("note").textContent).toBe("This paragraph could not be made: The voice ran out of memory.");
  });

  it("is closed when another part is opened", async () => {
    const t = await open([partial(2, 0), whole(1)], { autoplay: false }, runOf({ status: "running" }, { parts: [readingPart(0, "started", 2, 4), readingPart(1, "done", 3, 6)] }));
    edit();
    expect(screen.getByRole("textbox")).toBeTruthy();
    fireEvent.click(within(screen.getByRole("navigation", { name: "Parts of this reading" })).getByRole("button", { name: "Chapter 2" }));
    await flush();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(t.api.part).toHaveBeenLastCalledWith("r1", 1);
  });
});

// ------------------------------------------------------------------ a reading that is being made
describe("a reading that is being made", () => {
  const making = (made: number) => runOf({ status: "running" }, { parts: [readingPart(0, "started", made, made * 2), readingPart(1, "waiting", 0, 0)], seconds_made: made * 2 });

  it("shows a paragraph that is made when the event says so, and plays on into it", async () => {
    const t = await open([partial(1, 0)], {}, making(1));
    expect(paragraph(/^Paragraph 2 of part 1\./).className).toContain("piece-pending");
    t.api.part.mockResolvedValue(partial(2, 0));
    act(() => t.pieces.emit({ run_id: "r1", part: 0, idx: 1, frames: 2000 }));
    await flush();
    expect(paragraph("Paragraph 2 of part 1.").className).not.toContain("piece-pending");
    await t.tell(t.audios()[0], "ended"); // the first piece ends: into the second, which is ready
    expect(paragraph("Paragraph 2 of part 1.").getAttribute("aria-current")).toBe("true");
  });

  it("does not look again for another reading's piece", async () => {
    const t = await open([partial(1, 0)], {}, making(1));
    t.api.part.mockClear();
    act(() => t.pieces.emit({ run_id: "another", part: 0, idx: 1, frames: 2000 }));
    await flush();
    expect(t.api.part).not.toHaveBeenCalled();
  });

  it("looks again when the reading's own progress shows more made than it knows (an event that did not arrive)", async () => {
    const t = await open([partial(1, 0)], {}, making(1));
    t.api.part.mockClear();
    t.api.part.mockResolvedValue(partial(3, 0));
    t.rerender(making(3)); // the page's run has moved on; no event came
    await flush();
    expect(t.api.part).toHaveBeenCalledTimes(1);
    expect(paragraph("Paragraph 3 of part 1.").className).not.toContain("piece-pending");
    t.api.part.mockClear();
    t.rerender(making(3)); // and when they agree it does not ask again
    await flush();
    expect(t.api.part).not.toHaveBeenCalled();
  });

  it("goes on playing through a change to the reading it is told of", async () => {
    const t = await open([partial(2, 0)], {}, making(2));
    plays.mockClear();
    t.rerender({ ...making(2), queue_position: 1 });
    await flush();
    expect(plays).not.toHaveBeenCalled(); // nothing was started over again
    expect(button("Pause")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ the phone's own controls
describe("the media controls", () => {
  type Handler = (details: MediaSessionActionDetails) => void;
  function session() {
    const handlers = new Map<string, Handler | null>();
    return {
      handlers,
      metadata: null as MediaMetadata | null,
      playbackState: "none" as MediaSessionPlaybackState,
      setActionHandler: vi.fn((action: string, handler: Handler | null) => void handlers.set(action, handler)),
      setPositionState: vi.fn(),
    };
  }
  const metaSeen: Array<{ title: string; artist: string; album: string }> = [];
  beforeEach(() => {
    metaSeen.length = 0;
    vi.stubGlobal("MediaMetadata", class {
      title = "";
      artist = "";
      album = "";
      constructor(init: { title: string; artist: string; album: string }) {
        Object.assign(this, init);
        metaSeen.push(init);
      }
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("tells them the part, the voice and the reading", async () => {
    const s = session();
    await open([whole(0)], { mediaSession: s as unknown as ReadingViewProps["mediaSession"] });
    expect(metaSeen.at(-1)).toEqual({ title: "Chapter 1", artist: "Anna", album: "A long book" });
    expect(s.playbackState).toBe("playing");
  });

  it("does what the buttons do when they are pressed", async () => {
    const s = session();
    const t = await open([whole(0)], { mediaSession: s as unknown as ReadingViewProps["mediaSession"] });
    const call = (action: string, details: Partial<MediaSessionActionDetails> = {}) => act(() => void s.handlers.get(action)?.({ action, ...details } as MediaSessionActionDetails));
    call("pause");
    expect(button("Play")).toBeTruthy();
    expect(s.playbackState).toBe("paused");
    call("pause"); // already paused: nothing to do
    expect(button("Play")).toBeTruthy();
    call("play");
    expect(button("Pause")).toBeTruthy();
    call("play"); // already playing
    expect(button("Pause")).toBeTruthy();
    call("seekforward", {});
    expect(t.audios()[0].currentTime).toBe(6);
    call("seekbackward", { seekOffset: 4 });
    expect(t.audios()[0].currentTime).toBe(2);
    call("seekto", { seekTime: 3 });
    expect(t.audios()[0].currentTime).toBe(3);
    call("seekto", {}); // no time given
    expect(t.audios()[0].currentTime).toBe(3);
    call("nexttrack");
    expect(t.audios()[0].currentTime).toBe(4);
    call("previoustrack");
    expect(t.audios()[0].currentTime).toBe(2);
  });

  it("says where it is, and how long, for the lock screen's bar", async () => {
    const s = session();
    const t = await open([whole(0)], { mediaSession: s as unknown as ReadingViewProps["mediaSession"] });
    await t.at(2.5);
    expect(s.setPositionState).toHaveBeenLastCalledWith({ duration: 6, playbackRate: 1, position: 2.5 });
  });

  it("lets go of the controls when the view closes", async () => {
    const s = session();
    const t = await open([whole(0)], { mediaSession: s as unknown as ReadingViewProps["mediaSession"] });
    t.unmount();
    for (const action of ["play", "pause", "seekbackward", "seekforward", "previoustrack", "nexttrack", "seekto"]) expect(s.handlers.get(action)).toBeNull();
    expect(s.metadata).toBeNull();
  });

  it("goes on without the controls of a browser that has none, and without an action a browser does not know", async () => {
    await open([whole(0)], { mediaSession: null });
    cleanup();
    const s = session();
    s.setActionHandler.mockImplementation((action: string) => {
      if (action === "seekto") throw new TypeError("not supported");
    });
    s.setPositionState.mockImplementation(() => {
      throw new TypeError("position past the duration");
    });
    const t = await open([whole(0)], { mediaSession: s as unknown as ReadingViewProps["mediaSession"] });
    await t.at(1);
    expect(button("Pause")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ the place, and closing
describe("the place", () => {
  it("is reported every ten seconds while playing, and when the view is closed", async () => {
    vi.useFakeTimers();
    try {
      const t = await open([whole(0), whole(1)]);
      await t.at(2.5);
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(t.api.savePosition).toHaveBeenCalledWith("r1", { part: 0, piece: 1, offset_ms: 2500 });
      await t.at(3.5);
      t.unmount();
      expect(t.api.savePosition).toHaveBeenLastCalledWith("r1", { part: 0, piece: 1, offset_ms: 3500 });
      expect(pauses).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("is not reported for a reading that was only opened and not played: nothing is lost by looking", async () => {
    const t = await open([whole(0)], { autoplay: false });
    t.unmount();
    expect(t.api.savePosition).not.toHaveBeenCalled();
  });
});


// ------------------------------------------------------------------ in the Speech tab
describe("the reading view in the Speech tab", () => {
  const NOW = Date.parse("2026-11-02T12:05:00.000Z");
  const history = (runs: SpeechRun[]): SpeechHistory => ({
    status: null, runs, runsReady: true, filterBar: <div>filters</div>, loadProblem: null, empty: <p>Nothing here.</p>, isTransient: () => false, viewScope: "kept",
    projects: { projects: [], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null) }, more: false, loadingOlder: false, now: NOW,
    onLoadOlder: vi.fn(), onCancel: vi.fn(), onContinue: vi.fn(), onRestore: vi.fn(), onDelete: vi.fn(), onListen: vi.fn(),
  });
  const answers = (call: Call) => {
    const part = /\/api\/readings\/r1\/parts\/(\d+)$/.exec(call.url);
    if (part) return { body: whole(Number(part[1])) };
    return { status: 204, body: null };
  };
  const panel = (caps = CAPS, reader: Parameters<typeof SpeechPanel>[0]["reader"] | undefined = undefined, runs: SpeechRun[] = [runOf()]) =>
    <SpeechPanel hidden={false} caps={caps} store={memoryStore()} history={history(runs)} reader={reader} />;

  it("is above the readings, and is the reading that was opened", async () => {
    server(answers);
    const reader = { run: runOf(), turn: 1, pieces: createEmitter<ReadingPieceEvent>(), onClose: vi.fn() };
    render(panel(CAPS, reader));
    await flush();
    const view = screen.getByRole("region", { name: "A long book" });
    const list = screen.getByRole("region", { name: "Your readings" });
    expect(view.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(within(view).getByRole("button", { name: "Close player" }));
    expect(reader.onClose).toHaveBeenCalledTimes(1);
  });

  it("is there when speech cannot be made now: a reading made before an engine went away can still be listened to", async () => {
    server(answers);
    const caps = { ...CAPS, speech: { ...CAPS.speech, available: false, reason: "No speech engine can run here.", hint: null } };
    render(panel(caps, { run: runOf(), turn: 1, pieces: createEmitter<ReadingPieceEvent>(), onClose: vi.fn() }));
    await flush();
    expect(screen.getByRole("heading", { name: "Speech isn't available on this server" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "A long book" })).toBeTruthy();
  });

  it("is not there when no reading is open", async () => {
    server(answers);
    render(panel());
    await flush();
    expect(screen.queryByRole("region", { name: "A long book" })).toBeNull();
  });

  it("is begun again, at the place it was left, when Listen is pressed again on the reading that is open", async () => {
    const calls = server(answers);
    const pieces = createEmitter<ReadingPieceEvent>();
    const view = render(panel(CAPS, { run: runOf(), turn: 1, pieces, onClose: vi.fn() }));
    await flush();
    const asked = () => calls.filter((c) => c.url === "/api/readings/r1/parts/0").length;
    expect(asked()).toBe(1);
    view.rerender(panel(CAPS, { run: runOf(), turn: 1, pieces, onClose: vi.fn() })); // the same press: nothing starts over
    await flush();
    expect(asked()).toBe(1);
    view.rerender(panel(CAPS, { run: runOf(), turn: 2, pieces, onClose: vi.fn() })); // another press
    await flush();
    expect(asked()).toBe(2);
  });

  it("is another reading's player when another is opened", async () => {
    server((call) => {
      const part = /\/api\/readings\/(r\d)\/parts\/(\d+)$/.exec(call.url);
      return part ? { body: whole(Number(part[2])) } : { status: 204, body: null };
    });
    const pieces = createEmitter<ReadingPieceEvent>();
    const view = render(panel(CAPS, { run: runOf(), turn: 1, pieces, onClose: vi.fn() }));
    await flush();
    const other = runOf({ id: "r2" }, { title: "Another book" });
    view.rerender(panel(CAPS, { run: other, turn: 2, pieces, onClose: vi.fn() }, [other]));
    await flush();
    expect(screen.queryByRole("region", { name: "A long book" })).toBeNull();
    expect(screen.getByRole("region", { name: "Another book" })).toBeTruthy();
  });
});
