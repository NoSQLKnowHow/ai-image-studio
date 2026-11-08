import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { info, makeApi, reply, result, sends } from "./builderTestSupport";
import { useBuilder } from "./useBuilder";

// The builder's state as a hook: pictures attached to a chat that does not exist yet, and a chat opened by Refine or Help me write (DESIGN.md §37.2; criteria 241, 243, 244).

const bodyOf = (api: ReturnType<typeof makeApi>, call = 0) => (api.send.mock.calls[call] as unknown as [string, Record<string, unknown>])[1];
const picture = (name: string) => ({ file: new Blob([name], { type: "image/png" }), name });

describe("pictures attached to a new chat", () => {
  it("make one chat for all of them, however many are attached in a row", async () => {
    const api = makeApi();
    const { result: hook } = renderHook(() => useBuilder(api));
    const attach = hook.current.attach; // the same function for all three: what a loop over several chosen files holds
    await act(async () => {
      await attach(picture("one.png"));
      await attach(picture("two.png"));
      await attach(picture("three.png"));
    });
    expect(api.createChat).toHaveBeenCalledTimes(1);
    expect(api.attachFile.mock.calls.map((c) => c[0])).toEqual(["c1", "c1", "c1"]);
    await waitFor(() => expect(hook.current.pending).toHaveLength(3));
  });

  it("go to the chat that is open, not to a new one", async () => {
    const api = makeApi({ getChat: vi.fn(async () => ({ chat: info({ id: "c9" }), messages: [], draft: null, pending: [], preferences: 0 })) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.open("c9"));
    await act(async () => { await hook.current.attach(picture("one.png")); });
    expect(api.createChat).not.toHaveBeenCalled();
    expect(api.attachFile.mock.calls[0][0]).toBe("c9");
  });

  it("from the history go the same way, by the picture's id", async () => {
    const api = makeApi({ attachImage: vi.fn(async () => ({ id: "a2", url: "/a2", width: 1, height: 1, name: null })) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => { await hook.current.attach({ imageId: "img7" }); });
    expect(api.attachImage).toHaveBeenCalledWith("c1", "img7");
  });
});

describe("a chat opened by Refine or Help me write", () => {
  it("sends its first plain message as that, with the run it is about, and the next as an ordinary message", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => hook.current.prime("refine", "run42"));
    await act(async () => hook.current.send({ text: "the hands look off, too dark" }));
    expect(bodyOf(api, 0)).toMatchObject({ intent: "refine", run_id: "run42", text: "the hands look off, too dark" });
    await act(async () => hook.current.send({ text: "lighter, please" }));
    expect(bodyOf(api, 1).intent).toBe("message");
    expect(bodyOf(api, 1)).not.toHaveProperty("run_id");
    expect(hook.current.primed).toBeNull();
  });

  it("uses Help me write the same way, with no run", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => hook.current.prime("help_edit"));
    await act(async () => hook.current.send({ text: "put the dog into the beach" }));
    expect(bodyOf(api)).toMatchObject({ intent: "help_edit" });
    expect(bodyOf(api)).not.toHaveProperty("run_id");
  });

  it("leaves an intent the person chose themselves alone, and keeps the priming for the message that follows", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => hook.current.prime("refine", "run42"));
    await act(async () => hook.current.send({ text: "just write it", intent: "write_now" }));
    expect(bodyOf(api).intent).toBe("write_now");
    expect(bodyOf(api).run_id).toBe("run42"); // the run is still what the chat is about
  });

  it("is forgotten when another chat is started or opened", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    act(() => hook.current.prime("refine", "run42"));
    act(() => hook.current.startNew());
    expect(hook.current.primed).toBeNull();
    act(() => hook.current.prime("help_edit"));
    await act(async () => hook.current.open("c1"));
    expect(hook.current.primed).toBeNull();
    await act(async () => hook.current.send({ text: "hello" }));
    expect(bodyOf(api).intent).toBe("message");
  });

  it("does not turn a retry into something else", async () => {
    const api = makeApi({ send: sends({ name: "start", seq: 1 }, result(reply(2))) });
    const { result: hook } = renderHook(() => useBuilder(api));
    await act(async () => hook.current.send({ text: "hello" }));
    act(() => hook.current.prime("refine", "run42"));
    await act(async () => hook.current.send({ retry: true }));
    expect(bodyOf(api, 1)).toEqual({ retry: true });
    expect(hook.current.primed).not.toBeNull(); // a retry sends nothing new, so the priming waits for the next message
  });
});
