import { request, toApiError } from "./api";
import type { Run, Status } from "./types";
import type {
  EngineVoices, Passage, PartDetail, PositionBody, Prepared, PrepareTextBody, ReadingBody, SampleAnswer, SampleBody, SaveVoiceBody, SavedVoice, SpeechJob,
  StagedRecording,
} from "./speechTypes";

// The Speech tab's calls (DESIGN.md §38.9). Everything the server sends comes through `request` (or `sendFile`, which reads errors the same way), so a refusal is an
// `ApiError` with its words, its hint and, where the server names one, the `field` of the request it is about.

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });
const post = (body?: unknown): RequestInit => ({ method: "POST", ...(body === undefined ? {} : json(body)) });
const patch = (body: unknown): RequestInit => ({ method: "PATCH", ...json(body) });
const del = (): RequestInit => ({ method: "DELETE" });
const put = (body: unknown): RequestInit => ({ method: "PUT", ...json(body) });

/** A file as the request body, which is how the studio takes files (DESIGN.md §21.6): its name goes in the query, and no `Content-Type` is sent (the server
 *  looks at the name and at the bytes, never at what the browser guessed). */
async function sendFile<T>(path: string, file: Blob, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { method: "POST", body: file, headers: { "X-Studio-Client": "1", Accept: "application/json" }, signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error; // the person stopped it: not a failure to explain
    throw toApiError(0, null);
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) throw toApiError(response.status, body);
  return body as T;
}

export interface PrepareFileOptions {
  clean: boolean;
  read_extras: boolean;
}

export const speechApi = {
  // preparing a source (§38.5): nothing is stored until a reading is made from what comes back
  prepare: (body: PrepareTextBody) => request<Prepared>("/api/speech/prepare", post(body)),
  prepareFile: (file: File, options: PrepareFileOptions, signal?: AbortSignal) => {
    const query = new URLSearchParams({ name: file.name, clean: String(options.clean), read_extras: String(options.read_extras) });
    return sendFile<Prepared>(`/api/speech/prepare/file?${query}`, file, signal);
  },

  // readings (§38.6, §38.9)
  makeReading: (body: ReadingBody) => request<Run>("/api/readings", post(body)),
  reading: (id: string) => request<Run>(`/api/readings/${id}`),
  part: (id: string, part: number) => request<PartDetail>(`/api/readings/${id}/parts/${part}`),
  continueReading: (id: string) => request<Run>(`/api/readings/${id}/continue`, post()),
  editPiece: (id: string, part: number, piece: number, text: string) =>
    request<{ idx: number; text: string }>(`/api/readings/${id}/parts/${part}/pieces/${piece}`, patch({ text })),
  savePosition: (id: string, position: PositionBody) => request<void>(`/api/readings/${id}/position`, put(position)),

  // the engines and their voices (§38.4)
  voices: (engine: string) => request<EngineVoices>(`/api/speech/engines/${encodeURIComponent(engine)}/voices`),
  passage: () => request<Passage>("/api/speech/passage"),
  sample: (body: SampleBody) => request<SampleAnswer>("/api/speech/samples", post(body)),
  job: (id: string) => request<SpeechJob>(`/api/speech/jobs/${id}`),
  design: (description: string, engine?: string) => request<SpeechJob>("/api/speech/voices/design", post(engine ? { description, engine } : { description })),
  uploadRecording: (file: Blob, signal?: AbortSignal) => sendFile<StagedRecording>("/api/speech/recordings", file, signal),
  saveVoice: (body: SaveVoiceBody) => request<SavedVoice>("/api/speech/voices", post(body)),
  renameVoice: (id: string, name: string) => request<SavedVoice>(`/api/speech/voices/${id}`, patch({ name })),
  deleteVoice: (id: string) => request<void>(`/api/speech/voices/${id}`, del()),

  // loading and unloading an engine's model: the same routes as the other two models, which say which engine
  loadEngine: (engine: string, variant?: string) => request<Status>("/api/model/load", post({ model: "speech", engine, ...(variant ? { variant } : {}) })),
  unloadEngine: () => request<Status>("/api/model/unload", post({ model: "speech" })),
};

// Addresses the page puts in <audio> and <a download>. They are made here so that the page never builds a path by hand.
export const partAudioUrl = (id: string, part: number): string => `/api/speech/${id}/parts/${part}.wav`;
export const partDownloadUrl = (id: string, part: number): string => `/api/speech/${id}/parts/${part}.wav?download=1`;
export const pieceAudioUrl = (id: string, part: number, piece: number): string => `/api/speech/${id}/parts/${part}/pieces/${piece}.wav`;
export const zipUrl = (id: string): string => `/api/speech/${id}/download.zip`;
