import type { Capabilities, CreateRunBody, Run, RunsPage, Status } from "./types";

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

export const api = {
  capabilities: () => request<Capabilities>("/api/capabilities"),
  status: () => request<Status>("/api/status"),
  listRuns: (before?: string | null, limit = 20) =>
    request<RunsPage>(`/api/runs?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`),
  createRun: (body: CreateRunBody) => request<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  deleteRun: (id: string) => request<void>(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
