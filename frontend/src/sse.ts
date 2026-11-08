import { toApiError } from "./api";

// Reading a reply as it is written (DESIGN.md §37.4): the server answers a message with server-sent events, and the page reads them from the response body as they
// arrive. (EventSource cannot be used: it only sends GET.) `SseParser` is the whole format and is tested on its own; `readStream` is the thin part that touches `fetch`.

export interface SseEvent {
  name: string;
  data: unknown;
}

/** Splits a stream of text into events. An event is `event: name` and `data: {json}` lines ended by a blank line; a line beginning with a colon is a comment; an event with
 *  data that is not JSON is dropped (a half-written one cannot arrive, because nothing is handed over before its blank line). */
export class SseParser {
  private buffer = "";

  feed(chunk: string): SseEvent[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const events: SseEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      let name = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length === 0) continue;
      try {
        events.push({ name, data: JSON.parse(data.join("\n")) });
      } catch {
        /* not JSON: not ours */
      }
    }
    return events;
  }
}

/** POST a JSON body and hand each event to `onEvent` as it arrives. Resolves when the stream ends; rejects with an `ApiError` when the server answers with an error status
 *  instead of a stream (not set up, chat in the bin, a reply already being written, …) and with an AbortError when `signal` aborts it. */
export async function readStream(path: string, body: unknown, onEvent: (event: SseEvent) => void, signal?: AbortSignal): Promise<void> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json", "X-Studio-Client": "1" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if ((error as { name?: string }).name === "AbortError") throw error;
    throw toApiError(0, null);
  }
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      /* not JSON */
    }
    throw toApiError(response.status, payload);
  }
  if (!response.body) throw toApiError(0, null);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true })).forEach(onEvent);
    }
    parser.feed(decoder.decode()).forEach(onEvent);
  } catch (error) {
    if ((error as { name?: string }).name === "AbortError") throw error;
    throw toApiError(0, null); // the connection dropped in the middle
  } finally {
    reader.releaseLock();
  }
}
