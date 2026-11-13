import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Header } from "./components/Header";
import { QueueMenu } from "./components/QueuePanel";
import { server, type Answer, type Call } from "./speechTestSupport";
import { STATUS } from "./testdata";
import type { QueueItem, QueueListing, Status } from "./types";

// The Queue button and its panel (DESIGN.md section 40, criteria 306 to 313): what it lists, how a job is moved by the arrows and by dragging, what a screen reader is told, and
// what it does when the server says no. The wording and the arithmetic are in queue.test.ts; this is the part that puts them on the screen.

const item = (id: string, over: Partial<QueueItem> = {}): QueueItem => ({ id, kind: "image", title: `job ${id}`, draft: false, movable: true, position: null, ...over });
const GOING = item("run", { title: "going now", movable: false });
const listing = (ids: readonly string[], over: Partial<QueueListing> = {}, items: Record<string, Partial<QueueItem>> = {}): QueueListing => ({
  running: GOING, queued: ids.map((id, i) => item(id, { position: i + 1, ...items[id] })), cap: 10, ...over,
});
const statusWith = (order: readonly string[], running: string | null = "run"): Status => ({ ...STATUS, queue: { running, queued: order.length, cap: 10, enlarge_waiting: [], order: [...order] } });

const flush = () => act(async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
});

/** A stand-in for the server's queue: the listing, and a move that does what the route does (take the job out, put it before or after the other). */
function queueServer(start: readonly string[], options: { items?: Record<string, Partial<QueueItem>>; move?: (call: Call) => Answer | undefined } = {}) {
  let order = [...start];
  const calls = server((call) => {
    if (call.url === "/api/queue" && (call.init?.method ?? "GET") === "GET") return { body: listing(order, {}, options.items) };
    if (call.url === "/api/queue/move") {
      const refused = options.move?.(call);
      if (refused) return refused;
      const { id, before, after } = JSON.parse(call.init.body as string) as { id: string; before?: string; after?: string };
      order = order.filter((x) => x !== id);
      order.splice(order.indexOf((before ?? after) as string) + (before ? 0 : 1), 0, id);
      return { body: listing(order, {}, options.items) };
    }
    return { status: 404, body: { detail: "n/a", code: "not_found" } };
  });
  return { calls, now: () => [...order] };
}
const gets = (calls: Call[]) => calls.filter((c) => c.url === "/api/queue" && (c.init?.method ?? "GET") === "GET").length;
const moves = (calls: Call[]) => calls.filter((c) => c.url === "/api/queue/move").map((c) => JSON.parse(c.init.body as string));

async function open(order: readonly string[], options: Parameters<typeof queueServer>[1] = {}) {
  const stub = queueServer(order, options);
  const view = render(<QueueMenu status={statusWith(order)} />);
  fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
  await flush();
  return { ...stub, ...view };
}

const row = (id: string) => document.querySelector(`li[data-row="${id}"]`) as HTMLElement;
const titles = () => Array.from(document.querySelectorAll("li[data-row] .queue-title"), (el) => el.textContent);
const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const pointer = (type: string, target: Window | Document | Element, init: MouseEventInit = {}) => fireEvent(target, new MouseEvent(type, { bubbles: true, cancelable: true, ...init }));
const grip = (id: string) => within(row(id)).getByTitle("Drag to move");

/** Rows of 40 px, 4 px apart, from the top: what a browser's layout would say, which jsdom has none of. */
function layOutRows() {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const li = this.closest("li[data-row]");
    if (!li || li !== this) return new DOMRect(0, 0, 0, 0);
    const top = Array.from(li.parentElement?.children ?? []).indexOf(li) * 44;
    return { top, bottom: top + 40, left: 0, right: 300, width: 300, height: 40, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  server(() => ({ body: listing([]) }));
});

// ------------------------------------------------------------------ the button
describe("the Queue button", () => {
  it("is not there while nothing is going and nothing waits, and is there with a count while something does", () => {
    const { calls } = queueServer([]);
    const { rerender } = render(<QueueMenu status={STATUS} />);
    expect(screen.queryByRole("button", { name: /Queue/ })).toBeNull();
    rerender(<QueueMenu status={statusWith(["a", "b"])} />);
    expect(screen.getByRole("button", { name: "Queue: 2 waiting" }).textContent).toContain("2");
    rerender(<QueueMenu status={statusWith([], "run")} />);
    expect(screen.getByRole("button", { name: "Queue: nothing waiting" })).toBeTruthy();
    expect(gets(calls)).toBe(0);   // the panel is not read until it is opened
  });

  it("is not there without a status", () => {
    render(<QueueMenu status={null} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says whether it is open, and what it opens", () => {
    queueServer(["a"]);
    render(<QueueMenu status={statusWith(["a"])} />);
    const button = screen.getByRole("button", { name: /^Queue:/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.getAttribute("aria-controls")).toBe("queue-details");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById("queue-details")).not.toBeNull();
  });

  it("closes on Escape and on a press outside it, but not on a press inside", async () => {
    await open(["a", "b"]);
    pointer("pointerdown", row("a"));
    expect(screen.getByRole("region", { name: "Queue" })).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("region", { name: "Queue" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    pointer("pointerdown", document.body);
    expect(screen.queryByRole("region", { name: "Queue" })).toBeNull();
  });

  it("stays while it is open although the queue has emptied, and goes when it is shut", async () => {
    queueServer([]);
    const { rerender } = render(<QueueMenu status={statusWith(["a"])} />);
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    rerender(<QueueMenu status={{ ...STATUS }} />);
    expect(screen.getByRole("button", { name: /^Queue:/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    expect(screen.queryByRole("button", { name: /Queue/ })).toBeNull();
  });

  it("is in the header, beside the model pill", () => {
    queueServer(["a"]);
    render(<Header status={statusWith(["a"])} now={Date.now()} busy={false} tab="image" musicAvailable onModel={vi.fn(async () => true)} />);
    expect(screen.getByRole("button", { name: "Queue: 1 waiting" })).toBeTruthy();
  });
});

// ------------------------------------------------------------------ the panel
describe("the panel", () => {
  it("shows the job that is going, locked, and the waiting jobs in order with their place, kind and words", async () => {
    await open(["a", "b", "c"], { items: { a: { kind: "music", title: "ambient piano" }, b: { kind: "vector", draft: true }, c: { kind: "voice", title: "Sample: Anna" } } });
    const region = screen.getByRole("region", { name: "Queue" });
    expect(within(region).getByText("going now")).toBeTruthy();
    expect(within(region).getByText("Now")).toBeTruthy();
    expect(titles()).toEqual(["ambient piano", "job b", "Sample: Anna"]);
    expect(Array.from(document.querySelectorAll(".queue-place"), (el) => el.textContent)).toEqual(["1", "2", "3"]);
    expect(Array.from(document.querySelectorAll("li .queue-kind"), (el) => el.textContent)).toEqual(["Music", "Drawing", "Voice"]);
    expect(within(row("b")).getByText("Draft")).toBeTruthy();
    expect(within(row("a")).queryByText("Draft")).toBeNull();
  });

  it("gives a job that has started a lock and no grip and no arrows, and says why to a screen reader", async () => {
    await open(["a", "b", "c"], { items: { b: { movable: false } } });
    expect(within(row("b")).queryByTitle("Drag to move")).toBeNull();
    expect(within(row("b")).queryAllByRole("button")).toHaveLength(0);
    expect(within(row("b")).getByText("Started: it can no longer be moved.")).toBeTruthy();
    expect(within(row("a")).getAllByRole("button")).toHaveLength(2);
  });

  it("says so when nothing is going, or nothing waits", async () => {
    queueServer([]);
    const view = render(<QueueMenu status={statusWith([], "run")} />);
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    await flush();
    expect(screen.getByText("Nothing is waiting behind it.")).toBeTruthy();
    view.unmount();
    server(() => ({ body: { running: null, queued: [], cap: 10 } }));
    render(<QueueMenu status={statusWith(["z"], null)} />);
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    await flush();
    expect(screen.getByText("Nothing is going and nothing is waiting.")).toBeTruthy();
  });

  it("says it is reading, and then shows the words of a failure to read", async () => {
    server(() => ({ status: 500, body: { detail: "The queue could not be read.", code: "oops" } }));
    render(<QueueMenu status={statusWith(["a"])} />);
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    expect(screen.getByText("Reading the queue…")).toBeTruthy();
    await flush();
    expect(screen.getByRole("alert").textContent).toBe("The queue could not be read.");
  });

  it("reads again when the order, the count or the job that is going changes, and not when nothing has", async () => {
    const stub = queueServer(["a", "b"]);
    const { rerender } = render(<QueueMenu status={statusWith(["a", "b"])} />);
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    await flush();
    expect(gets(stub.calls)).toBe(1);
    rerender(<QueueMenu status={statusWith(["a", "b"])} />);
    await flush();
    expect(gets(stub.calls)).toBe(1);                                  // the same line: nothing to look at again
    rerender(<QueueMenu status={statusWith(["b", "a"])} />);
    await flush();
    expect(gets(stub.calls)).toBe(2);                                  // the order changed and nothing else did (another page moved a job)
    rerender(<QueueMenu status={statusWith(["b", "a", "c"])} />);
    await flush();
    expect(gets(stub.calls)).toBe(3);
    rerender(<QueueMenu status={statusWith(["b", "a", "c"], "next")} />);
    await flush();
    expect(gets(stub.calls)).toBe(4);
  });

  it("looks again by the count where an older server sends no order", async () => {
    const stub = queueServer(["a", "b"]);
    const old = (n: number): Status => ({ ...STATUS, queue: { running: "run", queued: n, cap: 10, enlarge_waiting: [] } });
    const { rerender } = render(<QueueMenu status={old(2)} />);
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    await flush();
    rerender(<QueueMenu status={old(2)} />);
    await flush();
    expect(gets(stub.calls)).toBe(1);
    rerender(<QueueMenu status={old(1)} />);
    await flush();
    expect(gets(stub.calls)).toBe(2);
  });
});

// ------------------------------------------------------------------ the arrows
describe("the arrows (criterion 307)", () => {
  it("send a job before the one above it, or after the one below it", async () => {
    const { calls } = await open(["a", "b", "c"]);
    press("Move “job b” up");
    await flush();
    expect(moves(calls)).toEqual([{ id: "b", before: "a" }]);
    expect(titles()).toEqual(["job b", "job a", "job c"]);
    press("Move “job a” down");
    await flush();
    expect(moves(calls)[1]).toEqual({ id: "a", after: "c" });
    expect(titles()).toEqual(["job b", "job c", "job a"]);
  });

  it("are off where there is nowhere to go: up on the first job, down on the last", async () => {
    await open(["a", "b", "c"]);
    expect((screen.getByRole("button", { name: "Move “job a” up" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Move “job c” down" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Move “job b” up" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Move “job b” down" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("can put a job past one that has started, which cannot itself be moved", async () => {
    const { calls } = await open(["a", "b", "c"], { items: { b: { movable: false } } });
    press("Move “job a” down");
    await flush();
    expect(moves(calls)).toEqual([{ id: "a", after: "b" }]);
    expect(titles()).toEqual(["job b", "job a", "job c"]);
  });

  it("show the move at once, and then what the server says, which wins", async () => {
    let answer: (value: { body: unknown }) => void = () => undefined;
    const slow = new Promise<{ body: unknown }>((resolve) => { answer = resolve; });
    await open(["a", "b", "c"], { move: () => slow });
    press("Move “job c” up");
    await flush();
    expect(titles()).toEqual(["job a", "job c", "job b"]);                         // shown before the server has answered
    await act(async () => answer({ body: listing(["c", "a", "b"]) }));             // the server's own order is what it was told, and wins
    expect(titles()).toEqual(["job c", "job a", "job b"]);
  });

  it("tell a screen reader where the job went", async () => {
    await open(["a", "b", "c"]);
    press("Move “job c” up");
    await flush();
    expect(screen.getByRole("status").textContent).toBe("Moved “job c” to place 2 of 3.");
  });

  it("keep focus on the arrow that was pressed, or on the other one where it has nowhere left to go", async () => {
    await open(["a", "b", "c", "d"]);
    press("Move “job b” down");
    await flush();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Move “job b” down" }));        // still has a place to go down
    press("Move “job b” down");
    await flush();
    expect(titles()).toEqual(["job a", "job c", "job d", "job b"]);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Move “job b” up" }));          // last now: the down arrow is off, so the other one has focus
  });
});

// ------------------------------------------------------------------ a refusal (criterion 311)
describe("when the server says no", () => {
  const refusal = (code: string, detail: string): ((call: Call) => Answer) => () => ({ status: 409, body: { detail, code } });

  it("says why in the server's words, and reads the queue again, which is what was true", async () => {
    const { calls } = await open(["a", "b"], { move: refusal("not_movable", "This job has already started or finished, so it can no longer be moved.") });
    expect(gets(calls)).toBe(1);
    press("Move “job b” up");
    await flush();
    expect(screen.getByRole("alert").textContent).toBe("This job has already started or finished, so it can no longer be moved.");
    expect(gets(calls)).toBe(2);
    expect(titles()).toEqual(["job a", "job b"]);                                   // as the server has it, not as it was shown
  });

  it("clears the words of the last refusal when the next move is tried", async () => {
    let refuse = true;
    await open(["a", "b"], { move: (call) => (refuse ? refusal("queue_changed", "The queue has changed; look again.")(call) : undefined) });
    press("Move “job b” up");
    await flush();
    expect(screen.getByRole("alert")).toBeTruthy();
    refuse = false;
    press("Move “job b” up");
    await flush();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// ------------------------------------------------------------------ dragging (criteria 306, 312)
describe("dragging a job by its grip (criterion 306)", () => {
  beforeEach(layOutRows);

  it("drops it after the last job when it is let go below the middle of the last row", async () => {
    const { calls } = await open(["a", "b", "c"]);
    pointer("pointerdown", grip("a"), { button: 0 });
    pointer("pointermove", window, { clientY: 120 });
    pointer("pointerup", window, { clientY: 120 });
    await flush();
    expect(moves(calls)).toEqual([{ id: "a", after: "c" }]);
    expect(titles()).toEqual(["job b", "job c", "job a"]);
  });

  it("drops it before the first when it is let go above the middle of the first row", async () => {
    const { calls } = await open(["a", "b", "c"]);
    pointer("pointerdown", grip("c"), { button: 0 });
    pointer("pointermove", window, { clientY: 5 });
    pointer("pointerup", window, { clientY: 5 });
    await flush();
    expect(moves(calls)).toEqual([{ id: "c", before: "a" }]);
  });

  it("drops it before the job whose upper half it is let go in", async () => {
    const { calls } = await open(["a", "b", "c", "d"]);
    pointer("pointerdown", grip("d"), { button: 0 });
    pointer("pointermove", window, { clientY: 50 });                                // the upper half of the second row (44 to 84)
    pointer("pointerup", window, { clientY: 50 });
    await flush();
    expect(moves(calls)).toEqual([{ id: "d", before: "b" }]);
  });

  it("shows where it will land while it is dragged, without moving anything", async () => {
    await open(["a", "b", "c"]);
    pointer("pointerdown", grip("a"), { button: 0 });
    expect(row("a").className).toContain("is-dragging");
    expect(document.querySelector("ol.queue-list")?.className).toContain("is-dragging");
    expect(document.querySelectorAll(".drop-before, .drop-after")).toHaveLength(0);   // where it is: no line
    pointer("pointermove", window, { clientY: 60 });                                   // above the middle of row b: just after a, which is where it is: no line
    expect(document.querySelectorAll(".drop-before, .drop-after")).toHaveLength(0);
    pointer("pointermove", window, { clientY: 90 });                                   // below the middle of row b, above the middle of row c: before c
    expect(row("c").className).toContain("drop-before");
    expect(titles()).toEqual(["job a", "job b", "job c"]);
    pointer("pointermove", window, { clientY: 125 });                                  // below the middle of the last row: after it
    expect(row("c").className).toContain("drop-after");
    expect(document.querySelectorAll(".drop-before")).toHaveLength(0);
    pointer("pointerup", window, { clientY: 125 });
    expect(document.querySelectorAll(".is-dragging, .drop-before, .drop-after")).toHaveLength(0);
  });

  it("sends nothing when it is let go where it was", async () => {
    const { calls } = await open(["a", "b", "c"]);
    pointer("pointerdown", grip("b"), { button: 0 });
    pointer("pointermove", window, { clientY: 60 });                                   // its own row
    pointer("pointerup", window, { clientY: 60 });
    await flush();
    expect(moves(calls)).toEqual([]);
    pointer("pointerdown", grip("b"), { button: 0 });
    pointer("pointerup", window, { clientY: 100 });                                    // just after itself: slot 2, which is where it is
    await flush();
    expect(moves(calls)).toEqual([]);
  });

  it("is put back by Escape, which does not also close the panel", async () => {
    const { calls } = await open(["a", "b", "c"]);
    pointer("pointerdown", grip("a"), { button: 0 });
    pointer("pointermove", window, { clientY: 125 });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.querySelector(".is-dragging")).toBeNull();
    pointer("pointerup", window, { clientY: 125 });
    await flush();
    expect(moves(calls)).toEqual([]);
    expect(screen.getByRole("region", { name: "Queue" })).toBeTruthy();
  });

  it("is put back when the pointer is taken away (a finger that turned into a scroll)", async () => {
    const { calls } = await open(["a", "b", "c"]);
    pointer("pointerdown", grip("a"), { button: 0 });
    pointer("pointercancel", window);
    pointer("pointerup", window, { clientY: 125 });
    await flush();
    expect(document.querySelector(".is-dragging")).toBeNull();
    expect(moves(calls)).toEqual([]);
  });

  it("is not started by the other buttons of a mouse", async () => {
    const { calls } = await open(["a", "b", "c"]);
    pointer("pointerdown", grip("a"), { button: 2 });
    expect(document.querySelector(".is-dragging")).toBeNull();
    pointer("pointerup", window, { clientY: 125 });
    await flush();
    expect(moves(calls)).toEqual([]);
  });

  it("cannot be done to a job that has started: it has no grip", async () => {
    await open(["a", "b", "c"], { items: { b: { movable: false } } });
    expect(within(row("b")).queryByTitle("Drag to move")).toBeNull();
    pointer("pointerdown", within(row("b")).getByTitle("This job has started, so it can no longer be moved"), { button: 0 });
    expect(document.querySelector(".is-dragging")).toBeNull();
  });

  it("can be dropped before or after a job that has started", async () => {
    const { calls } = await open(["a", "b", "c"], { items: { b: { movable: false } } });
    pointer("pointerdown", grip("c"), { button: 0 });
    pointer("pointerup", window, { clientY: 50 });                                     // the upper half of the started job's row
    await flush();
    expect(moves(calls)).toEqual([{ id: "c", before: "b" }]);
  });

  it("sends nothing when the job it was dragging has gone from the queue by the time it is let go", async () => {
    queueServer(["a", "b", "c"]);
    const { rerender } = render(<QueueMenu status={statusWith(["a", "b", "c"])} />);
    fireEvent.click(screen.getByRole("button", { name: /^Queue:/ }));
    await flush();
    pointer("pointerdown", grip("a"), { button: 0 });
    const later = server(() => ({ body: listing(["b", "c"]) }));                      // the queue once a has started: it is not waiting any more
    rerender(<QueueMenu status={statusWith(["b", "c"], "a")} />);
    await flush();
    expect(titles()).toEqual(["job b", "job c"]);
    pointer("pointerup", window, { clientY: 125 });
    await flush();
    expect(moves(later)).toEqual([]);
    expect(document.querySelector(".is-dragging")).toBeNull();
  });
});
