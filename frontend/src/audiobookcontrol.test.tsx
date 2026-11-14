import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AudiobookControl } from "./components/AudiobookControl";
import { ReadingCard } from "./components/ReadingCard";
import type { ProjectControls } from "./components/ProjectMenu";
import type { AudiobookCaps, AudiobookStatus, ReadingInfo } from "./speechTypes";
import { makeSpeechRun } from "./testdata";
import type { AudiobookState } from "./useAudiobook";

// The audiobook button (DESIGN.md §44.4, criteria 360 and 368): what it says before a press, while a file is made and when it is ready, why it is off, and where it is (a
// reading's card, but not in the bin and not before something is finished). The control is driven from outside with a state of the tests' own, so each state is looked at
// without a server; the hook that makes the real state is tested in useaudiobook.test.tsx.

afterEach(cleanup);

const status = (changes: Partial<AudiobookStatus> = {}): AudiobookStatus => ({
  available: true, reason: null, hint: null, state: "idle", parts_made: 2, parts_total: 2, complete: true, seconds: 600, chapters: 2, kbps: 64, estimated_bytes: 4_800_000,
  progress: null, part: null, bytes: null, url: null, error: null, ...changes,
});
const OK: AudiobookCaps = { available: true, reason: null, hint: null };
const NO_AAC: AudiobookCaps = { available: false, reason: "The audiobook file needs ffmpeg with an AAC encoder, and this server's ffmpeg has none.", hint: "The ZIP still has every part and its chapter lists." };
const reading = (done: number, total: number): Pick<ReadingInfo, "parts_done" | "parts_total"> => ({ parts_done: done, parts_total: total });

function show(state: Partial<AudiobookState>, caps: AudiobookCaps | undefined = OK, parts = reading(2, 2)) {
  const make = vi.fn();
  render(<AudiobookControl runId={"r".repeat(32)} reading={parts} audiobook={caps} state={{ status: null, busy: false, problem: null, make, ...state }} />);
  return make;
}
const button = () => screen.getByRole("button", { name: /audiobook|Making|Waiting|Starting/i }) as HTMLButtonElement;

describe("before it is pressed", () => {
  it("offers to make the file, and is pressed to do so", () => {
    const make = show({});
    expect(button().textContent).toBe("Make audiobook (M4B)");
    expect(button().getAttribute("aria-disabled")).toBeNull();
    fireEvent.click(button());
    expect(make).toHaveBeenCalledTimes(1);
  });

  it("says which parts it will hold when the reading is still being made", () => {
    show({}, OK, reading(3, 12));
    expect(button().textContent).toBe("Make audiobook (M4B) of 3 of 12 parts so far");
    expect(screen.getByText("The file will hold the parts finished so far.")).toBeTruthy();
  });

  it("has a live region only when there is something to say: a card with nothing going on is silent", () => {
    show({});
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("where the file cannot be made (criterion 360)", () => {
  it("says why in words beside the button, which stays in the tab order, and a press does nothing", () => {
    const make = show({}, NO_AAC);
    expect(screen.getByText(/needs ffmpeg with an AAC encoder, and this server's ffmpeg has none\. The ZIP still has every part and its chapter lists\./)).toBeTruthy();
    expect(button().getAttribute("aria-disabled")).toBe("true");
    expect(button().disabled).toBe(false); // aria-disabled and not disabled: a screen reader reaches it and hears the reason
    expect(button().getAttribute("aria-describedby")).toBeTruthy();
    expect(document.getElementById(button().getAttribute("aria-describedby") as string)?.textContent).toContain("AAC encoder");
    fireEvent.click(button());
    expect(make).not.toHaveBeenCalled();
  });

  it("goes by what the server last said of ffmpeg once there is an answer, which is newer than what the page was given", () => {
    show({ status: status({ available: false, reason: "ffmpeg is not found.", hint: null }) }, OK);
    expect(screen.getByText("ffmpeg is not found.")).toBeTruthy();
    expect(button().getAttribute("aria-disabled")).toBe("true");
  });
});

describe("while it is made", () => {
  it("says how far it has got and which part, and cannot be pressed again", () => {
    const make = show({ status: status({ state: "building", progress: 0.37, part: 1 }) });
    expect(button().textContent).toBe("Making the audiobook…");
    expect(button().getAttribute("aria-disabled")).toBe("true");
    expect(button().getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("status").textContent).toBe("Making the audiobook… 37%, part 1 of 2.");
    fireEvent.click(button());
    expect(make).not.toHaveBeenCalled();
  });

  it("says when it waits for another to be made", () => {
    show({ status: status({ state: "waiting" }) });
    expect(button().textContent).toBe("Waiting its turn…");
    expect(screen.getByRole("status").textContent).toBe("Waiting for another audiobook to be made first…");
  });

  it("says it is starting while the press is on its way", () => {
    show({ busy: true });
    expect(button().textContent).toBe("Starting…");
    expect(button().getAttribute("aria-disabled")).toBe("true");
  });
});

describe("when it is ready", () => {
  it("is a link to the file, with its size, that downloads, and says what it holds", () => {
    show({ status: status({ state: "ready", url: "/api/speech/x/audiobook.m4b", bytes: 312_000_000 }) });
    const link = screen.getByRole("link", { name: /Download audiobook \(M4B, 312 MB\)/ });
    expect(link.getAttribute("href")).toBe("/api/speech/x/audiobook.m4b");
    expect(link.hasAttribute("download")).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("10 min, 2 chapters, one for each part.");
    expect(screen.queryByRole("button")).toBeNull(); // the button is gone: there is nothing left to make
  });

  it("says which parts it holds when the reading was not finished", () => {
    show({ status: status({ state: "ready", url: "/u", bytes: 1000, parts_made: 1, parts_total: 3, complete: false, chapters: 1 }) });
    expect(screen.getByRole("status").textContent).toBe("10 min, 1 chapter, one for each part (1 of 3 parts so far).");
  });
});

describe("when it fails", () => {
  it("says what the server said, with its hint, and the button is there to try again", () => {
    const make = show({ status: status({ state: "failed", error: { message: "ffmpeg took in no audio for 300 seconds, so it was stopped.", hint: "Try again." } }) });
    expect(screen.getByRole("alert").textContent).toContain("ffmpeg took in no audio for 300 seconds, so it was stopped.");
    expect(screen.getByRole("alert").textContent).toContain("Try again.");
    fireEvent.click(button());
    expect(make).toHaveBeenCalledTimes(1);
  });

  it("says what the server refused with, when it refused the press", () => {
    show({ problem: { message: "The audiobook file needs about 1.0 GB of disk and 0.1 GB is free.", hint: "Free disk space." } });
    expect(screen.getByRole("alert").textContent).toContain("needs about 1.0 GB of disk");
  });
});

// ------------------------------------------------------------------ where it is
const controls = (): ProjectControls => ({ projects: [], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null) });
const NOW = Date.parse("2026-11-02T12:05:00.000Z");

function card(run = makeSpeechRun(), caps: AudiobookCaps | undefined = OK) {
  render(<ReadingCard run={run} now={NOW} transient={false} viewScope="kept" projects={controls()} audiobook={caps} onContinue={vi.fn()} onCancel={vi.fn()} onRestore={vi.fn()} onDelete={vi.fn()} />);
}
const withParts = (done: number) => {
  const run = makeSpeechRun();
  return makeSpeechRun({ reading: { ...(run.reading as ReadingInfo), parts_done: done } });
};

describe("on a reading's card", () => {
  it("is there once a part is finished, beside the Download of the ZIP", () => {
    card(withParts(2));
    expect(screen.getByRole("button", { name: "Make audiobook (M4B)" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download" })).toBeTruthy(); // the ZIP is still there, as it was
    expect(screen.getByRole("link", { name: "Download" }).getAttribute("title")).toContain("chapter lists");
  });

  it("is not there before any part is finished: there is nothing to make a file of", () => {
    card(withParts(0));
    expect(screen.queryByRole("button", { name: /audiobook/i })).toBeNull();
  });

  it("is not there in the bin, which only reads", () => {
    card(makeSpeechRun({ deleted_at: "2026-11-02T10:00:00.000Z", purge_at: "2026-12-02T10:00:00.000Z" }));
    expect(screen.queryByRole("button", { name: /audiobook/i })).toBeNull();
  });

  it("is off, with the reason, where the server's ffmpeg cannot make it, and the ZIP's Download is still offered", () => {
    card(withParts(2), NO_AAC);
    expect(screen.getByRole("button", { name: "Make audiobook (M4B)" }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByText(/AAC encoder/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download" })).toBeTruthy();
  });

  it("asks nothing of the server until it is pressed", async () => {
    const fetched = vi.fn();
    vi.stubGlobal("fetch", fetched);
    card(withParts(2));
    await act(async () => { await Promise.resolve(); });
    expect(fetched).not.toHaveBeenCalled(); // a page with a hundred readings must not ask a hundred times
    vi.unstubAllGlobals();
  });
});
