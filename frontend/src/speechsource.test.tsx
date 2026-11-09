import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Prepared } from "./speechTypes";
import { makePrepared } from "./testdata";
import { useSpeechSource } from "./useSpeechSource";

// The state behind the Speech tab's source and review (DESIGN.md §38.2), without the page: what happens when answers come back in an order other than the order the
// questions were asked in, and when a request is stopped. The page itself does not let a second request start while one is out, so these are what protects it if
// that ever changes, and the order the answers come in is the one thing a browser does not promise.

afterEach(() => vi.unstubAllGlobals());

const limits = { upload_mb: 50 };
const review = (title: string): Prepared => makePrepared({ title, parts: [{ ...makePrepared().parts[0], title }] });

/** A fetch whose answers the test hands out, one at a time and in any order. */
function held() {
  const waiting: { resolve: (body: unknown) => void; reject: (error: unknown) => void; signal: AbortSignal | null | undefined }[] = [];
  vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise<Response>((resolve, reject) => {
    waiting.push({ resolve: (body) => resolve(new Response(JSON.stringify(body), { status: 200 })), reject, signal: init.signal });
    init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  })));
  return waiting;
}

describe("the source, asked twice", () => {
  it("takes the answer to the newest question, even when the older one comes back after it", async () => {
    const waiting = held();
    const { result } = renderHook(() => useSpeechSource(limits));
    act(() => result.current.setText("First text, long enough."));
    act(() => result.current.prepareText());
    act(() => result.current.setText("Second text, long enough."));
    act(() => result.current.prepareText());
    expect(waiting).toHaveLength(2);
    await act(async () => waiting[1].resolve(review("Second")));
    expect(result.current.title).toBe("Second");
    await act(async () => waiting[0].resolve(review("First")));
    expect(result.current.title).toBe("Second"); // the late answer to the first question is not wanted
    expect(result.current.phase).toBe("ready");
  });

  it("ignores a failure of an older question too, rather than show it over the newer answer", async () => {
    const waiting = held();
    const { result } = renderHook(() => useSpeechSource(limits));
    act(() => result.current.setText("First text, long enough."));
    act(() => result.current.prepareText());
    act(() => result.current.prepareText());
    await act(async () => waiting[1].resolve(review("Second")));
    await act(async () => waiting[0].reject(new TypeError("network")));
    expect(result.current.problem).toBeNull();
    expect(result.current.title).toBe("Second");
  });

  it("stops a file that is being read, goes back to what it was, and says nothing of it", async () => {
    const waiting = held();
    const { result } = renderHook(() => useSpeechSource(limits));
    act(() => result.current.chooseFile(new File(["x"], "book.epub")));
    expect(result.current.phase).toBe("preparing");
    act(() => result.current.stop());
    await act(async () => undefined);
    expect(waiting[0].signal?.aborted).toBe(true);
    expect(result.current.phase).toBe("idle");
    expect(result.current.problem).toBeNull();
    expect(result.current.prepared).toBeNull();
  });

  it("goes back to the review it had when a file that replaced it is stopped", async () => {
    const waiting = held();
    const { result } = renderHook(() => useSpeechSource(limits));
    act(() => result.current.setText("First text, long enough."));
    act(() => result.current.prepareText());
    await act(async () => waiting[0].resolve(review("First")));
    act(() => result.current.chooseFile(new File(["x"], "book.epub")));
    act(() => result.current.stop());
    await act(async () => undefined);
    expect(result.current.phase).toBe("ready");
    expect(result.current.title).toBe("First");
    expect(result.current.problem).toBeNull();
  });

  it("stops the file that is being read when a newer question is asked, so that it is not uploaded for nothing", async () => {
    const waiting = held();
    const { result } = renderHook(() => useSpeechSource(limits));
    act(() => result.current.chooseFile(new File(["x"], "old.epub")));
    expect(waiting[0].signal?.aborted).toBe(false);
    act(() => result.current.setText("A newer question, long enough."));
    act(() => result.current.prepareText());
    expect(waiting[0].signal?.aborted).toBe(true); // the older upload is told to stop ...
    await act(async () => waiting[1].resolve(review("Newer")));
    expect(result.current.title).toBe("Newer"); // ... and the newer answer is the one taken
    expect(result.current.problem).toBeNull(); // (the stopped one is not a failure)
  });

  it("does not ask the server for text that is empty, a file of the wrong kind, or an address that is not one", async () => {
    const waiting = held();
    const { result } = renderHook(() => useSpeechSource(limits));
    act(() => result.current.prepareText());
    expect(result.current.problem?.message).toBe("Paste the text to read.");
    act(() => result.current.chooseFile(new File(["x"], "picture.png")));
    expect(result.current.problem?.message).toMatch(/not a kind of file/);
    act(() => result.current.prepareUrl());
    expect(result.current.problem?.message).toBe("Give the web address of the page to read.");
    expect(waiting).toHaveLength(0);
    expect(result.current.phase).toBe("idle");
  });

  it("uses the upload limit it is given, and 50 MB before the server has said", () => {
    held();
    const big = new File(["x"], "big.txt");
    Object.defineProperty(big, "size", { value: 20 * 1024 * 1024 });
    const { result } = renderHook(() => useSpeechSource({ upload_mb: 10 }));
    act(() => result.current.chooseFile(big));
    expect(result.current.problem?.message).toBe("big.txt is 20 MB; a file can be at most 10 MB.");
    const unknown = renderHook(() => useSpeechSource(undefined));
    act(() => unknown.result.current.chooseFile(big));
    expect(unknown.result.current.problem).toBeNull(); // 20 MB is under the 50 MB it assumes
  });
});
