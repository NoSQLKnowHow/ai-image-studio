import { filenameFromDisposition } from "./fourk";
import type { Capabilities, CreateMusicBody, CreateRunBody, ImageRun, ModelName, MusicRun, Run, RunsPage, Status, UploadResult } from "./types";

export interface FieldError {
  field: string; // e.g. "options.width" or "prompt"
  message: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
    readonly fieldErrors: FieldError[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Turn any error body the server can send into one readable message plus per-field errors. */
export function toApiError(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  const code = (body as { code?: string } | null)?.code ?? null;
  if (Array.isArray(detail)) {
    const fieldErrors = detail.map((d: { loc?: unknown[]; msg?: string }) => ({
      field: (d.loc ?? []).filter((p) => p !== "body").join("."),
      message: d.msg ?? "Invalid value.",
    }));
    return new ApiError(fieldErrors.map((f) => f.message).join(" ") || "The request was rejected.", status, code, fieldErrors);
  }
  if (typeof detail === "string") return new ApiError(detail, status, code);
  if (status === 0) return new ApiError("Can't reach the studio server.", status, code);
  return new ApiError(`The server answered ${status}.`, status, code);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  // Required on every mutation: browsers can't send it cross-site without a preflight,
  // which the server refuses, so other web pages can't act on the studio (DESIGN.md §11).
  if (method !== "GET" && method !== "HEAD") headers["X-Studio-Client"] = "1";

  let response: Response;
  try {
    response = await fetch(path, { ...init, method, headers });
  } catch {
    throw toApiError(0, null);
  }
  if (response.status === 204) return undefined as T;
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) throw toApiError(response.status, body);
  return body as T;
}

/** Send one image to be staged for an edit (DESIGN.md §21.6): the file itself is the request body. This uses
 *  XMLHttpRequest rather than fetch because only it reports how much has been sent, and the tray shows that on the
 *  picture. `abort` stops it; the promise then rejects with an AbortError. */
export function uploadImage(file: Blob, onProgress?: (fraction: number) => void): { promise: Promise<UploadResult>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<UploadResult>((resolve, reject) => {
    xhr.open("POST", "/api/uploads");
    xhr.setRequestHeader("X-Studio-Client", "1"); // as on every mutation (§11)
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.total ? event.loaded / event.total : 0);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* empty or non-JSON body */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as UploadResult);
      else reject(toApiError(xhr.status, body));
    };
    xhr.onerror = () => reject(toApiError(0, null));
    xhr.onabort = () => reject(new DOMException("The upload was canceled.", "AbortError"));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}

/** A picture from the person's computer, made 4K and sent back (DESIGN.md §27.9). Nothing is stored on the server. */
export interface UpscaledPicture {
  blob: Blob;
  filename: string; // as the server named it: upscale_<name>_<size>_<time>.png
  width: number;
  height: number;
}

async function upscalePicture(file: File): Promise<UpscaledPicture> {
  let response: Response;
  try {
    response = await fetch(`/api/upscale?name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      body: file, // the file itself is the body, as for an upload
      headers: { "X-Studio-Client": "1", Accept: "image/png, application/json" },
    });
  } catch {
    throw toApiError(0, null);
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      /* empty or non-JSON body */
    }
    throw toApiError(response.status, body);
  }
  const size = /^(\d+)x(\d+)$/.exec(response.headers.get("X-Output-Size") ?? "");
  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get("Content-Disposition")) ?? "upscaled.png",
    width: size ? Number(size[1]) : 0,
    height: size ? Number(size[2]) : 0,
  };
}

export const api = {
  capabilities: () => request<Capabilities>("/api/capabilities"),
  status: () => request<Status>("/api/status"),
  // The model is named, so a button on one tab never acts on the other tab's model (DESIGN.md §26.3).
  loadModel: (model: ModelName) => request<Status>("/api/model/load", { method: "POST", body: JSON.stringify({ model }) }),
  unloadModel: (model: ModelName) => request<Status>("/api/model/unload", { method: "POST", body: JSON.stringify({ model }) }),
  listRuns: (before?: string | null, limit = 20) =>
    request<RunsPage>(`/api/runs?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`),
  createRun: (body: CreateRunBody) => request<ImageRun>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  createMusicRun: (body: CreateMusicBody) => request<MusicRun>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  cancelRun: (id: string) => request<Run>(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  keepRun: (id: string, pinned: boolean) =>
    request<Run>(`/api/runs/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ pinned }) }),
  deleteRun: (id: string) => request<void>(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  // Make the 4K copy of a result image (DESIGN.md §27). The answer is the whole run, as for Keep: its image now has `four_k`.
  upscalePicture,
  makeFourK: (imageId: string) => request<ImageRun>(`/api/images/${encodeURIComponent(imageId)}/4k`, { method: "POST" }),
  deleteUpload: (id: string) => request<void>(`/api/uploads/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
