import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfirmCancel, ConfirmDelete, cardReuseButton } from "./components/Dialogs";
import { EmptyHistory } from "./components/FilterBar";
import { ReadingCard, readingStatusLabel } from "./components/ReadingCard";
import type { ProjectControls } from "./components/ProjectMenu";
import { NO_FILTER, ONLY_DELETED, ONLY_KEPT, withProject } from "./history";
import { binContents, deleteProjectQuestion, emptyBinQuestion, projectContents } from "./format";
import { SpeechPanel, type SpeechHistory } from "./components/SpeechPanel";
import type { ReadingInfo } from "./speechTypes";
import { memoryStore, server } from "./speechTestSupport";
import { CAPS, STATUS, makeSpeechRun } from "./testdata";
import type { Project, SpeechRun } from "./types";

// A reading in the history (DESIGN.md §38.2): its card in each state it can be in, what each button asks for, the words the shared dialogs and filter bar say about it,
// and the history under the tab. Everything else of the history (filters, projects, the bin) is the other tabs' and is tested with them; what is tested here is that a
// reading takes part in it as they do.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NOW = Date.parse("2026-11-02T12:05:00.000Z");
const reading = (run: SpeechRun, changes: Partial<ReadingInfo>): ReadingInfo => ({ ...(run.reading as ReadingInfo), ...changes });
const PROJECT: Project = { id: "a".repeat(32), name: "Audiobooks", created_at: "2026-10-01T00:00:00.000Z", counts: { image: 0, music: 0, speech: 1 } };
const noop = () => undefined;

function controls(over: Partial<ProjectControls> = {}): ProjectControls {
  return { projects: [PROJECT], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null), ...over };
}

function card(run: SpeechRun, props: Partial<Parameters<typeof ReadingCard>[0]> = {}) {
  const handlers = { onContinue: vi.fn(), onCancel: vi.fn(), onRestore: vi.fn(), onDelete: vi.fn() };
  render(<ReadingCard run={run} now={NOW} transient={false} viewScope="kept" projects={controls()} {...handlers} {...props} />);
  return handlers;
}
const button = (name: string | RegExp) => screen.queryByRole("button", { name });

// a run in each state, from the contract's finished one (two parts, three pieces, kept)
const done = () => makeSpeechRun();
const made = (partsDone: number, piecesDone: number, extra: Partial<SpeechRun> = {}) => {
  const run = done();
  const parts = (run.reading as ReadingInfo).parts.map((p, i) => ({ ...p, state: i < partsDone ? ("done" as const) : i === partsDone && piecesDone > 0 ? ("started" as const) : ("waiting" as const) }));
  return makeSpeechRun({ ...extra, reading: reading(run, { parts, parts_done: partsDone, pieces_done: piecesDone, seconds_made: piecesDone * 1.6, seconds_to_go: null, position: null }) });
};

// ------------------------------------------------------------------ the badge
describe("what the badge says", () => {
  it("is the state of the reading, in words, with its place in the queue", () => {
    expect(readingStatusLabel(done())).toBe("Done");
    expect(readingStatusLabel(makeSpeechRun({ status: "queued", queue_position: 3 }))).toBe("Queued · #3");
    expect(readingStatusLabel(makeSpeechRun({ status: "queued", queue_position: null }))).toBe("Queued");
    expect(readingStatusLabel(makeSpeechRun({ status: "running" }))).toBe("Making reading");
    expect(readingStatusLabel(makeSpeechRun({ status: "running", canceling: true }))).toBe("Stopping");
    expect(readingStatusLabel(makeSpeechRun({ status: "failed" }))).toBe("Failed");
    expect(readingStatusLabel(makeSpeechRun({ status: "canceled" }))).toBe("Canceled");
  });

  it("says a reading that is queued between two parts is waiting for its turn, and not that it has not started", () => {
    const between = makeSpeechRun({ status: "queued", queue_position: 2, reading: reading(done(), { waiting: true }) });
    expect(readingStatusLabel(between)).toBe("Waiting its turn · #2");
    expect(readingStatusLabel({ ...between, queue_position: null })).toBe("Waiting its turn");
  });
});

// ------------------------------------------------------------------ the card in each state
describe("a reading's card", () => {
  it("has its title, where the text came from, the engine and the voice, and how long it is", () => {
    card(done());
    const article = screen.getByRole("article", { name: "Done: reading, A test reading" });
    expect(within(article).getByText("A test reading")).toBeTruthy();
    expect(within(article).getByText("Pasted · Fake speech · Anna")).toBeTruthy();
    expect(within(article).getByText(/^All 2 parts · paragraph 3 of 3 · /)).toBeTruthy();
    expect(article.getAttribute("data-run-id")).toBeTruthy();
    expect(article.className).toContain("run-card");
  });

  it("says the host a text came from, and the name of a file", () => {
    const web = makeSpeechRun({ reading: reading(done(), { source: { kind: "url", url: "https://example.org/a", host: "example.org" } }) });
    card(web);
    expect(screen.getByText("example.org · Fake speech · Anna")).toBeTruthy();
    cleanup();
    card(makeSpeechRun({ reading: reading(done(), { source: { kind: "file", name: "book.epub" } }) }));
    expect(screen.getByText("book.epub · Fake speech · Anna")).toBeTruthy();
  });

  it("has no Keep, no Reuse and no expiry: a reading never expires and is not made from a prompt (§38.2)", () => {
    card(done());
    expect(button(/Keep/)).toBeNull();
    expect(button(/Reuse/)).toBeNull();
    expect(screen.queryByText(/Kept/)).toBeNull(); // not even the badge: every reading is kept, so it says nothing
    expect(screen.queryByText(/expire|deleted automatically/i)).toBeNull();
  });

  it("has a bar of how far it has got while it is being made, with the part, the paragraph, how much is made and how long is left", () => {
    const run = makeSpeechRun({ status: "running", reading: reading(made(1, 2), { seconds_to_go: 840, parts_done: 1, pieces_done: 2, pieces_total: 5 }) });
    card(run);
    expect(screen.getByRole("progressbar", { name: "How much of the reading is made" }).getAttribute("aria-valuenow")).toBe("40");
    expect(screen.getByText(/^Part 2 of 2 · paragraph 2 of 5 · less than a minute made · about 14 min to go$/)).toBeTruthy();
    cleanup();
    card(makeSpeechRun({ status: "running", reading: reading(made(1, 2), { pieces_done: 2, pieces_total: 3 }) }));
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("67"); // two thirds is 67 per cent: rounded, not cut off at 66
  });

  it("says how long is left only while it is being made: a canceled, failed or finished reading has no time to go, whatever the server last estimated", () => {
    for (const status of ["canceled", "failed", "done"] as const) {
      cleanup();
      card(makeSpeechRun({ status, reading: reading(made(1, 2), { seconds_to_go: 600 }) }));
      expect(screen.getByText(/ made$/), status).toBeTruthy(); // the line about how far it got is there...
      expect(screen.queryByText(/to go/), status).toBeNull(); // ...without a time still to go
    }
  });

  it("says nothing about how far it has got when it has nothing in it to make (no pieces at all)", () => {
    card(makeSpeechRun({ status: "queued", queue_position: 1, reading: reading(made(0, 0), { parts_total: 0, pieces_total: 0 }) }));
    expect(document.querySelector(".reading-progress")).toBeNull();
    expect(screen.queryByText(/none made yet/)).toBeNull();
  });

  it("announces its progress only while it is being made: a live region on every finished card would have a screen reader read them all out", () => {
    card(makeSpeechRun({ status: "running", reading: reading(made(1, 2), { pieces_done: 2 }) }));
    expect(document.querySelector(".reading-progress")?.getAttribute("role")).toBe("status");
    for (const status of ["queued", "done", "canceled"] as const) {
      cleanup();
      card(makeSpeechRun({ status, queue_position: status === "queued" ? 1 : null }));
      expect(document.querySelector(".reading-progress")?.hasAttribute("role"), status).toBe(false);
    }
  });

  it("has no bar before anything is made, and says so", () => {
    card(makeSpeechRun({ status: "queued", queue_position: 1, reading: reading(made(0, 0), { parts_total: 2, pieces_total: 3 }) }));
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByText("2 parts · 3 paragraphs · none made yet")).toBeTruthy();
  });

  it("says Stopping while it stops, and that what is made stays made", () => {
    card(makeSpeechRun({ status: "running", canceling: true }));
    expect(screen.getByText("Stopping… what is made stays made.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Stopping…" }).getAttribute("aria-disabled")).toBe("true");
  });

  it("says Canceled with how many parts are done, and that Continue makes the rest", () => {
    card(makeSpeechRun({ status: "canceled", reading: reading(made(1, 2), { parts_done: 1, parts_total: 2 }) }));
    expect(screen.getByText("Canceled · 1 of 2 parts. What was made is kept; Continue makes the rest.")).toBeTruthy();
    expect(button("Continue")).toBeTruthy();
    expect(button("Cancel")).toBeNull();
  });

  it("says why it failed, with the place and what to do, and offers Retry", () => {
    const failed = makeSpeechRun({ status: "failed", error: { message: "The voice failed at part 1, paragraph 2.", hint: "Edit that paragraph and retry." } });
    const handlers = card(failed);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("The voice failed at part 1, paragraph 2.");
    expect(alert.textContent).toContain("Edit that paragraph and retry.");
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(handlers.onContinue).toHaveBeenCalledTimes(1);
  });

  it("shows the project it is filed in, and the badge of an engine that is for non-commercial use only", () => {
    const run = makeSpeechRun({ project_id: PROJECT.id, reading: reading(done(), { licence: { name: "CC-BY-NC", noncommercial: true, notice: "Not for commercial use." } }) });
    card(run);
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Audiobooks"); // (the Project button names it too, so look at the badge)
    expect(screen.getByText("Non-commercial use only").getAttribute("title")).toBe("Not for commercial use.");
    cleanup();
    card(done());
    expect(screen.queryByText("Non-commercial use only")).toBeNull();
  });

  it("says it is in a project even when the page does not know that project's name (yet)", () => {
    card(makeSpeechRun({ project_id: "f".repeat(32) }));
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Project");
    cleanup();
    card(done());
    expect(document.querySelector("[data-project-chip]")).toBeNull(); // and nothing of the kind when it is in none
  });

  it("shows an error only on a reading that has failed: a stale one on a reading that is being made again is not shown, nor is Retry offered", () => {
    card(makeSpeechRun({ status: "running", error: { message: "The voice failed at part 1.", hint: "Retry." } }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(button("Retry")).toBeNull();
    expect(screen.queryByText("The voice failed at part 1.")).toBeNull();
  });

  it("says why a working reading is in a filtered view that would not show it", () => {
    card(makeSpeechRun({ status: "running" }), { transient: true, viewScope: "project" });
    expect(document.querySelector('[data-note="working-in-project"]')).toBeTruthy();
  });
});

// ------------------------------------------------------------------ its buttons
describe("a reading's buttons", () => {
  it("Cancel is offered while it waits or is made, asks the page to cancel, and is not offered once it is over", () => {
    const handlers = card(makeSpeechRun({ status: "running" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(handlers.onCancel).toHaveBeenCalledTimes(1);
    cleanup();
    card(makeSpeechRun({ status: "queued", queue_position: 1 }));
    expect(button("Cancel")).toBeTruthy();
    for (const status of ["done", "failed", "canceled"] as const) {
      cleanup();
      card(makeSpeechRun({ status }));
      expect(button("Cancel"), status).toBeNull();
    }
  });

  it("Cancel does nothing a second time while it is stopping", () => {
    const handlers = card(makeSpeechRun({ status: "running", canceling: true }));
    fireEvent.click(screen.getByRole("button", { name: "Stopping…" }));
    expect(handlers.onCancel).not.toHaveBeenCalled();
  });

  it("Continue is offered only after a Cancel, and asks for the rest; Retry only after a failure", () => {
    const handlers = card(makeSpeechRun({ status: "canceled" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(handlers.onContinue).toHaveBeenCalledTimes(1);
    for (const status of ["done", "queued", "running"] as const) {
      cleanup();
      card(makeSpeechRun({ status, queue_position: 1 }));
      expect(button("Continue"), status).toBeNull();
      expect(button("Retry"), status).toBeNull();
    }
  });

  it("Download is a link to the ZIP of the finished parts, there once a part is finished and not before", () => {
    card(makeSpeechRun({ id: "r1abc" }));
    const link = screen.getByRole("link", { name: /Download/ });
    expect(link.getAttribute("href")).toBe("/api/speech/r1abc/download.zip");
    expect(link.hasAttribute("download")).toBe(true);
    cleanup();
    card(made(0, 1, { status: "running" }));
    expect(screen.queryByRole("link", { name: /Download/ })).toBeNull(); // a piece is made but no part: nothing to put in a ZIP yet
  });

  it("Delete asks the page, and cannot be pressed while the reading is being made", () => {
    const handlers = card(done());
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(handlers.onDelete).toHaveBeenCalledTimes(1);
    cleanup();
    card(makeSpeechRun({ status: "running" }));
    const running = screen.getByRole("button", { name: "Delete" }) as HTMLButtonElement;
    expect(running.disabled).toBe(true);
    expect(running.getAttribute("title")).toBe("Can't delete while it's being made");
    cleanup();
    card(makeSpeechRun({ status: "queued", queue_position: 2 })); // one that only waits can be deleted: it has made nothing worth keeping
    const waiting = screen.getByRole("button", { name: "Delete" }) as HTMLButtonElement;
    expect(waiting.disabled).toBe(false);
    expect(waiting.getAttribute("title")).toBe("Delete this reading and its audio");
  });

  it("has the Project button, as the other cards have", () => {
    card(done());
    expect(document.querySelector('[data-run-id] button[aria-haspopup]')).toBeTruthy();
  });

  it("offers Listen only where there is a player, and something made to listen to; it says Continue listening where the person stopped", () => {
    const onListen = vi.fn();
    const stopped = makeSpeechRun({ reading: reading(done(), { position: { part: 1, piece: 0, offset_ms: (2 * 3600 + 14 * 60 + 7) * 1000 } }) });
    card(stopped, { onListen });
    const listen = screen.getByRole("button", { name: "Continue listening" });
    fireEvent.click(listen);
    expect(onListen).toHaveBeenCalledTimes(1);
    expect((onListen.mock.calls[0][0] as SpeechRun).id).toBe(stopped.id);
    expect(screen.getByText("Continue listening from 2:14:07 in part 2")).toBeTruthy(); // where they stopped, as the design words it
    expect(listen.getAttribute("title")).toBe("Continue listening from 2:14:07 in part 2"); // and the button says so too
    cleanup();
    card(done(), { onListen });
    expect(screen.getByRole("button", { name: "Listen" }).getAttribute("title")).toMatch(/^Listen to it/); // the contract's reading was stopped half a second in: that is where anyone starts
    expect(document.querySelector('[data-note="place"]')).toBeNull();
    cleanup();
    card(made(1, 2), { onListen });
    expect(screen.getByRole("button", { name: "Listen" })).toBeTruthy(); // no place stored: it starts at the beginning
    cleanup();
    card(made(0, 0, { status: "queued", queue_position: 1 }), { onListen });
    expect(button(/Listen/)).toBeNull(); // nothing is made yet
    cleanup();
    card(done());
    expect(button(/Listen/)).toBeNull(); // no player on this page
  });
});

describe("a reading in the bin", () => {
  const binned = () => makeSpeechRun({ deleted_at: "2026-11-03T10:00:00.000Z", purge_at: "2026-12-03T10:00:00.000Z" });

  it("has Restore and Delete forever, and can still be downloaded and listened to; nothing that would change it", () => {
    const onListen = vi.fn();
    const handlers = card(binned(), { onListen });
    expect(screen.getByText("Deleted")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(handlers.onRestore).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Delete forever" }));
    expect(handlers.onDelete).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: /Download/ })).toBeTruthy();
    expect(button(/Listen/)).toBeTruthy();
    for (const name of ["Delete", "Cancel", "Continue", "Retry"]) expect(button(new RegExp(`^${name}$`)), name).toBeNull();
    expect(document.querySelector("[data-project-chip]")).toBeNull();
  });

  it("says since when it is in the bin and until when", () => {
    card(binned());
    expect(document.querySelector('[data-note="in-bin"]')?.textContent).toMatch(/^In the bin since (Nov 3|3 Nov)\. It will be deleted for good around /); // (the order of day and month is the browser's locale's)
  });

  it("has no Retry after a failure, and no Continue after a Cancel: Restore it first", () => {
    card({ ...binned(), status: "failed", error: { message: "It failed.", hint: null } });
    expect(button("Retry")).toBeNull();
    cleanup();
    card({ ...binned(), status: "canceled" });
    expect(button("Continue")).toBeNull();
  });

  it("does not offer the place to continue from, which is for a reading that is not in the bin", () => {
    const stopped = makeSpeechRun({ deleted_at: "2026-11-03T10:00:00.000Z", purge_at: "2026-12-03T10:00:00.000Z", reading: reading(done(), { position: { part: 1, piece: 0, offset_ms: 134000 } }) });
    card(stopped, { onListen: noop });
    expect(screen.getByRole("button", { name: "Continue listening" })).toBeTruthy(); // the button still knows it (a reading in the bin can be listened to)...
    expect(document.querySelector('[data-note="place"]')).toBeNull(); // ...but the card does not write the place out
  });

  it("has no Cancel, whatever its state says: a reading in the bin has finished, and the page does not rely on that", () => {
    card(makeSpeechRun({ status: "queued", queue_position: 1, deleted_at: "2026-11-03T10:00:00.000Z", purge_at: "2026-12-03T10:00:00.000Z" }));
    expect(button("Cancel")).toBeNull();
  });
});

// ------------------------------------------------------------------ the history under the tab
describe("the history under the Speech tab", () => {
  const history = (runs: SpeechRun[], over: Partial<SpeechHistory> = {}): SpeechHistory => ({
    status: null, runs, runsReady: true, filterBar: <div data-testid="filters">filters</div>, loadProblem: null, empty: <p>Nothing here.</p>, isTransient: () => false, viewScope: "kept",
    projects: controls(), more: false, loadingOlder: false, now: NOW, onLoadOlder: vi.fn(), onCancel: vi.fn(), onContinue: vi.fn(), onRestore: vi.fn(), onDelete: vi.fn(), ...over,
  });
  const show = (h: SpeechHistory, caps = CAPS) => {
    server(() => ({ status: 404, body: { detail: "n/a", code: "not_found" } }));
    return render(<SpeechPanel hidden={false} caps={caps} store={memoryStore()} history={h} />);
  };
  const a = () => makeSpeechRun({ id: "a1", reading: reading(done(), { title: "First book" }) });
  const b = () => makeSpeechRun({ id: "b2", reading: reading(done(), { title: "Second book" }) });

  it("is one card for each reading, in the order given (newest first), under the filter bar", () => {
    show(history([b(), a()]));
    const list = screen.getByRole("region", { name: "Your readings" });
    expect(within(list).getAllByRole("article").map((c) => c.getAttribute("data-run-id"))).toEqual(["b2", "a1"]);
    expect(screen.getByTestId("filters")).toBeTruthy();
    expect(screen.getByTestId("filters").compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says it is loading, or why it could not, or that there is nothing, as the other tabs do", () => {
    show(history([], { runsReady: false }));
    expect(screen.getByText("Loading your readings…")).toBeTruthy();
    cleanup();
    show(history([], { runsReady: false, loadProblem: <p role="alert">Couldn't load.</p> }));
    expect(screen.getByText("Couldn't load.")).toBeTruthy();
    expect(screen.queryByText("Loading your readings…")).toBeNull();
    cleanup();
    show(history([]));
    expect(screen.getByText("Nothing here.")).toBeTruthy();
  });

  it("loads older readings by a button that says so while it works", () => {
    const onLoadOlder = vi.fn();
    show(history([a()], { more: true, onLoadOlder }));
    fireEvent.click(screen.getByRole("button", { name: "Load older runs" }));
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
    cleanup();
    show(history([a()], { more: true, loadingOlder: true }));
    expect((screen.getByRole("button", { name: "Loading…" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    show(history([a()]));
    expect(screen.queryByRole("button", { name: /Load older/ })).toBeNull();
  });

  it("hands each card's buttons the reading they are on, and each button to its own handler", () => {
    const working = makeSpeechRun({ id: "w1", status: "running", reading: reading(done(), { title: "Working book" }) });
    const stopped = makeSpeechRun({ id: "c3", status: "canceled", reading: reading(made(1, 2), { title: "Stopped book", parts_done: 1 }) });
    const binned = makeSpeechRun({ id: "d4", deleted_at: "2026-11-03T10:00:00.000Z", purge_at: "2026-12-03T10:00:00.000Z", reading: reading(done(), { title: "Binned book" }) });
    const spies = { cancel: vi.fn(), continue: vi.fn(), restore: vi.fn(), delete: vi.fn(), listen: vi.fn() };
    show(history([working, a(), stopped, b(), binned], { onCancel: spies.cancel, onContinue: spies.continue, onRestore: spies.restore, onDelete: spies.delete, onListen: spies.listen }));
    const card = (id: string) => screen.getAllByRole("article").find((c) => c.getAttribute("data-run-id") === id) as HTMLElement;
    const press = (id: string, name: string) => fireEvent.click(within(card(id)).getByRole("button", { name }));
    const calledWith = (which: keyof typeof spies) => spies[which].mock.calls.map((call) => (call[0] as SpeechRun).id);
    press("w1", "Cancel");
    press("c3", "Continue");
    press("d4", "Restore");
    press("b2", "Delete");
    press("a1", "Listen");
    // each press reached its own handler, once, with the reading it was pressed on, and no other handler heard of it
    expect(calledWith("cancel")).toEqual(["w1"]);
    expect(calledWith("continue")).toEqual(["c3"]);
    expect(calledWith("restore")).toEqual(["d4"]);
    expect(calledWith("delete")).toEqual(["b2"]);
    expect(calledWith("listen")).toEqual(["a1"]);
    press("d4", "Delete forever");
    expect(calledWith("delete")).toEqual(["b2", "d4"]);
  });

  it("tells each card whether it is only in the view because it is working, and which view that is", () => {
    const working = makeSpeechRun({ id: "w1", status: "running", reading: reading(done(), { title: "Working book" }) });
    show(history([working, a()], { isTransient: (run) => run.id === "w1", viewScope: "project" }));
    const notes = (id: string) => screen.getAllByRole("article").find((c) => c.getAttribute("data-run-id") === id)?.querySelector("[data-note^='working-in-']")?.getAttribute("data-note");
    expect(notes("w1")).toBe("working-in-project");
    expect(notes("a1")).toBeUndefined();
  });

  it("shows the queue above the readings, as the other tabs do, only when there is one", () => {
    show(history([a()], { status: { ...STATUS, queue: { running: "w1", queued: 2, cap: 10, enlarge_waiting: [] } } }));
    expect(screen.getByText(/^Generating 1 run · 2 waiting \(room for 8 more\)$/)).toBeTruthy();
    cleanup();
    show(history([a()], { status: STATUS }));
    expect(document.querySelector(".queue-bar")).toBeNull();
  });

  it("is shown when speech cannot be made now, so that a reading made before an engine went away can still be downloaded", () => {
    const caps = { ...CAPS, speech: { ...CAPS.speech, available: false, reason: "No speech engine can run here.", hint: null } };
    show(history([a()]), caps);
    expect(screen.getByRole("heading", { name: "Speech isn't available on this server" })).toBeTruthy();
    expect(screen.getByRole("article", { name: /reading, First book/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Download/ })).toBeTruthy();
  });

  it("is not there at all when the page gives no history (the panel on its own)", () => {
    server(() => ({ status: 404, body: { detail: "n/a", code: "not_found" } }));
    render(<SpeechPanel hidden={false} caps={CAPS} store={memoryStore()} />);
    expect(screen.queryByRole("region", { name: "Your readings" })).toBeNull();
  });
});

// ------------------------------------------------------------------ the shared dialogs and words
describe("what the shared dialogs say of a reading", () => {
  it("Stop this reading? says that what is made is kept, with how many parts are finished, and that Continue makes the rest", () => {
    const run = makeSpeechRun({ status: "running", reading: reading(done(), { parts_done: 1, pieces_done: 2 }) });
    render(<ConfirmCancel run={run} onBack={noop} onConfirm={noop} />);
    expect(screen.getByRole("heading", { name: "Stop this reading?" })).toBeTruthy();
    expect(screen.getByText("Everything already made is kept (1 part is finished), and so is what was made of the part being worked on. Continue makes the rest.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Stop making the reading" })).toBeTruthy();
  });

  it("keeps what was made of the first part even when no part is finished yet, and does not say '0 parts are finished'", () => {
    render(<ConfirmCancel run={makeSpeechRun({ status: "running", reading: reading(done(), { parts_done: 0, pieces_done: 2 }) })} onBack={noop} onConfirm={noop} />);
    expect(screen.getByText("Everything already made is kept, and so is what was made of the part being worked on. Continue makes the rest.")).toBeTruthy();
  });

  it("says so plainly when nothing is made yet, and when several parts are finished", () => {
    render(<ConfirmCancel run={makeSpeechRun({ status: "running", reading: reading(done(), { parts_done: 0, pieces_done: 0 }) })} onBack={noop} onConfirm={noop} />);
    expect(screen.getByText("Nothing has been made yet, so nothing is kept. Continue makes it from the start.")).toBeTruthy();
    cleanup();
    render(<ConfirmCancel run={makeSpeechRun({ status: "running", reading: reading(done(), { parts_done: 3, pieces_done: 9 }) })} onBack={noop} onConfirm={noop} />);
    expect(screen.getByText(/\(3 parts are finished\)/)).toBeTruthy();
  });

  it("says nothing of the picture kind for a reading", () => {
    render(<ConfirmCancel run={makeSpeechRun({ status: "running" })} onBack={noop} onConfirm={noop} />);
    expect(screen.queryByText(/image|Stop generating/)).toBeNull();
  });

  it("Delete this reading? moves it to the bin for the days there are, and never says 'You marked it Keep' (every reading is kept)", () => {
    render(<ConfirmDelete run={done()} binDays={30} projectName={null} onCancel={noop} onConfirm={noop} />);
    expect(screen.getByRole("heading", { name: "Delete this reading?" })).toBeTruthy();
    expect(screen.getByText(/It moves to Deleted and stays there for 30 days/)).toBeTruthy();
    expect(screen.queryByText(/You marked it Keep/)).toBeNull();
  });

  it("says that for good its audio is removed from the Spark, in the bin or without one", () => {
    render(<ConfirmDelete run={makeSpeechRun({ deleted_at: "2026-11-03T10:00:00.000Z" })} binDays={30} projectName={null} onCancel={noop} onConfirm={noop} />);
    expect(screen.getByRole("heading", { name: "Delete this reading for good?" })).toBeTruthy();
    expect(screen.getByText(/Its audio is removed from the Spark\. This can't be undone\./)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete forever" })).toBeTruthy();
    cleanup();
    render(<ConfirmDelete run={done()} binDays={0} projectName={null} onCancel={noop} onConfirm={noop} />);
    expect(screen.getByText(/Its audio is removed from the Spark\./)).toBeTruthy();
  });

  it("names the project a filed reading is in, as for the other kinds, and the other kinds still say You marked it Keep", () => {
    render(<ConfirmDelete run={makeSpeechRun({ project_id: PROJECT.id })} binDays={30} projectName="Audiobooks" onCancel={noop} onConfirm={noop} />);
    expect(screen.getByText(/It is in the project “Audiobooks”\./)).toBeTruthy();
    cleanup();
    render(<ConfirmDelete run={makeSpeechRun({ mode: "generate" } as never)} binDays={30} projectName={null} onCancel={noop} onConfirm={noop} />);
    expect(screen.getByText(/You marked it Keep\./)).toBeTruthy(); // (a picture run that is kept: unchanged)
  });

  it("gives focus back, after Cancel, to what a reading's card keeps: Listen, else Download", () => {
    card(makeSpeechRun({ id: "r1abc" }), { onListen: noop });
    expect(cardReuseButton("r1abc")?.getAttribute("data-action")).toBe("listen");
    expect(cardReuseButton("another")).toBeNull(); // another run's id: nothing
    cleanup();
    card(makeSpeechRun({ id: "r1abc" })); // no player on the page: the Download link is what stays
    expect(cardReuseButton("r1abc")?.getAttribute("data-action")).toBe("download");
  });
});

describe("what the filter bar and the projects say of readings", () => {
  const empty = (filter = NO_FILTER, project: Project | null = null) =>
    render(<EmptyHistory kind="speech" filter={filter} binDays={30} project={project} onShowAll={noop} onShowAnyProject={noop} />);

  it("says how to fill an empty Speech tab, and that readings are always kept in the Kept view", () => {
    empty();
    expect(screen.getByText("No readings yet")).toBeTruthy();
    expect(screen.getByText(/press Make reading/)).toBeTruthy();
    cleanup();
    empty(ONLY_KEPT);
    expect(screen.getByText("No readings yet")).toBeTruthy();
    expect(screen.getByText("Readings are always kept: they are never deleted automatically, and there is nothing to keep by hand.")).toBeTruthy();
    expect(screen.queryByText(/Press/)).toBeNull(); // not "Press Keep on a run"
  });

  it("says no readings are deleted, and names the tab when a project's view is empty", () => {
    empty(ONLY_DELETED);
    expect(screen.getByText("No deleted readings")).toBeTruthy();
    cleanup();
    empty(withProject(NO_FILTER, PROJECT.id), PROJECT);
    expect(screen.getByText("Nothing in “Audiobooks” on Speech yet")).toBeTruthy();
  });

  it("counts readings among what a project holds, and in what deleting the project leaves alone", () => {
    expect(projectContents({ image: 0, music: 0, speech: 1 })).toBe("1 reading");
    expect(projectContents({ image: 2, music: 1, vector: 3, speech: 4, prompts: 2 })).toBe("2 pictures, 1 track, 3 vectors, 4 readings, 2 prompts");
    expect(projectContents({ image: 0, music: 0 })).toBe("no runs");
    const question = deleteProjectQuestion({ name: "Audiobooks", counts: { image: 0, music: 0, speech: 2 } });
    expect(question).toContain("Its 2 runs (2 readings) are not deleted");
    expect(deleteProjectQuestion({ name: "Empty", counts: { image: 0, music: 0, speech: 0 } })).toBe("Delete the project “Empty”? It has no runs.");
  });

  it("counts a reading in the bin as a run, and names the Speech tab in what Empty bin deletes", () => {
    const bin = binContents({ image: { deleted: 0, deleted_items: 0 }, music: { deleted: 0, deleted_items: 0 }, vector: { deleted: 0 }, speech: { deleted: 3, deleted_items: 0 } });
    expect(bin.speech).toEqual({ runs: 3, items: 0 });
    expect(emptyBinQuestion(bin, false)).toBe("Delete 3 runs for good (3 on Speech), with their files. This can't be undone.");
    const mixed = binContents({ image: { deleted: 2, deleted_items: 0 }, music: { deleted: 0, deleted_items: 0 }, vector: { deleted: 0 }, speech: { deleted: 1, deleted_items: 0 } });
    expect(emptyBinQuestion(mixed, false)).toBe("Delete 3 runs for good (2 on Images, 1 on Speech), with their files. This can't be undone.");
  });
});
