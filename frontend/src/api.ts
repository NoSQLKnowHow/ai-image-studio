import type { Capabilities, CreateRunBody, Run, RunsPage, Status, UploadResult } from "./types";

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

export const api = {
  capabilities: () => request<Capabilities>("/api/capabilities"),
  status: () => request<Status>("/api/status"),
  listRuns: (before?: string | null, limit = 20) =>
    request<RunsPage>(`/api/runs?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`),
  createRun: (body: CreateRunBody) => request<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  cancelRun: (id: string) => request<Run>(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  keepRun: (id: string, pinned: boolean) =>
    request<Run>(`/api/runs/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ pinned }) }),
  deleteRun: (id: string) => request<void>(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  deleteUpload: (id: string) => request<void>(`/api/uploads/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
