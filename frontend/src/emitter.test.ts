import { describe, expect, it, vi } from "vitest";
import { createEmitter } from "./emitter";

describe("an emitter", () => {
  it("tells every listener what was emitted, in the order they began to listen", () => {
    const emitter = createEmitter<number>();
    const heard: string[] = [];
    emitter.subscribe((n) => heard.push(`a${n}`));
    emitter.subscribe((n) => heard.push(`b${n}`));
    emitter.emit(1);
    expect(heard).toEqual(["a1", "b1"]);
  });

  it("stops telling a listener that has stopped listening, and the others go on hearing", () => {
    const emitter = createEmitter<string>();
    const first = vi.fn();
    const second = vi.fn();
    const stop = emitter.subscribe(first);
    emitter.subscribe(second);
    stop();
    emitter.emit("x");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith("x");
  });

  it("is quiet when nobody listens, and when the same stop is used twice", () => {
    const emitter = createEmitter<string>();
    expect(() => emitter.emit("x")).not.toThrow();
    const stop = emitter.subscribe(() => undefined);
    stop();
    expect(() => stop()).not.toThrow();
  });

  it("does not let a listener that fails keep the rest from being told", () => {
    const emitter = createEmitter<string>();
    const reported = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const after = vi.fn();
    emitter.subscribe(() => { throw new Error("broken"); });
    emitter.subscribe(after);
    emitter.emit("x");
    expect(after).toHaveBeenCalledWith("x");
    expect(reported).toHaveBeenCalledTimes(1);
    reported.mockRestore();
  });

  it("lets a listener stop listening while it is being told, without the others missing it", () => {
    const emitter = createEmitter<string>();
    const heard: string[] = [];
    const stop = emitter.subscribe(() => { heard.push("first"); stop(); });
    emitter.subscribe(() => heard.push("second"));
    emitter.emit("x");
    emitter.emit("y");
    expect(heard).toEqual(["first", "second", "second"]);
  });
});
