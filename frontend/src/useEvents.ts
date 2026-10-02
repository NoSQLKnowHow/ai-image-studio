import { useEffect, useRef } from "react";
import type { Action } from "./store";
import type { Hello, Progress, Run, WorkerStatus } from "./types";

interface Handlers {
  onCapabilitiesChanged: () => void;
}

/** Live updates over server-sent events, reconnecting after any interruption. Every connection
 *  opens with a `hello` snapshot (status + newest runs) that the server reads after subscribing, so
 *  snapshot-then-events never has a gap, on the first connection or after a reconnect. */
export function useEventStream(dispatch: (action: Action) => void, handlers: Handlers): void {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let connectedBefore = false;
    let disposed = false;

    const parse = (event: Event) => JSON.parse((event as MessageEvent<string>).data);

    const connect = () => {
      if (disposed) return;
      source = new EventSource("/api/events");
      source.onopen = () => dispatch({ type: "connection", value: "open" });
      source.onerror = () => {
        dispatch({ type: "connection", value: "lost" });
        // EventSource retries by itself while CONNECTING; if it gave up, start over.
        if (source?.readyState === EventSource.CLOSED) {
          source.close();
          retryTimer = setTimeout(connect, 3000);
        }
      };
      source.addEventListener("hello", (e) => {
        const hello = parse(e) as Hello;
        dispatch({ type: "status", status: hello.status });
        dispatch({ type: "runsLoaded", page: hello.runs, append: false });
        // capabilities.updated events may have been missed (a server restart can change the pipeline)
        if (connectedBefore) handlersRef.current.onCapabilitiesChanged();
        connectedBefore = true;
      });
      source.addEventListener("run.created", (e) => dispatch({ type: "runUpsert", run: parse(e) as Run }));
      source.addEventListener("run.updated", (e) => dispatch({ type: "runUpsert", run: parse(e) as Run }));
      source.addEventListener("run.progress", (e) => {
        const data = parse(e) as { id: string; progress: Progress };
        dispatch({ type: "runProgress", id: data.id, progress: data.progress });
      });
      source.addEventListener("run.deleted", (e) => dispatch({ type: "runDeleted", id: (parse(e) as { id: string }).id }));
      source.addEventListener("queue.updated", (e) => {
        const data = parse(e) as { running: string | null; positions: Record<string, number> };
        dispatch({ type: "queue", running: data.running, positions: data.positions });
      });
      source.addEventListener("worker.state", (e) => dispatch({ type: "worker", worker: parse(e) as WorkerStatus }));
      source.addEventListener("capabilities.updated", () => handlersRef.current.onCapabilitiesChanged());
      source.addEventListener("shutdown", () => dispatch({ type: "serverStopping" }));
      source.addEventListener("overflow", () => {
        // We fell behind; a fresh stream starts with a fresh snapshot, so nothing is missed.
        source?.close();
        retryTimer = setTimeout(connect, 0);
      });
    };

    connect();
    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      source?.close();
    };
  }, [dispatch]);
}
