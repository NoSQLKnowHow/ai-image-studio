import { ApiError, request, toApiError } from "./api";
import { readStream } from "./sse";
import type {
  Attachment, BuilderCounts, BuilderStatus, BuilderTest, ChatFull, ChatInfo, ChatListItem, Comparison, ForKind, MessageBody, Preference, PromptCard, PromptDetail,
  ReplyEvent, SaveAnswer, VersionSource, Detail,
} from "./builderTypes";

// The Prompt builder's calls (DESIGN.md §37.4). Everything the server sends comes through `request`, so an error is an `ApiError` with its words and its hint.

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });
const post = (body?: unknown): RequestInit => ({ method: "POST", ...(body === undefined ? {} : json(body)) });
const patch = (body: unknown): RequestInit => ({ method: "PATCH", ...json(body) });
const query = (params: Record<string, string | boolean | undefined | null>): string => {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== null && value !== false && value !== "") q.set(key, String(value));
  const text = q.toString();
  return text ? `?${text}` : "";
};

export interface PromptQuery {
  q?: string;
  tag?: string;
  favourite?: boolean;
  project?: string; // an id, or "none"
  deleted?: boolean;
  sort?: "updated" | "newest" | "title";
}

export interface NewPrompt {
  title: string;
  text: string;
  negative?: string | null;
  settings?: Record<string, unknown>;
  note?: string | null;
  source?: VersionSource;
  for?: ForKind;
  project_id?: string | null;
  tags?: string[];
  favourite?: boolean;
  chat_id?: string | null;
  message_seq?: number | null;
}

export interface NewVersion {
  text: string;
  negative?: string | null;
  settings?: Record<string, unknown>;
  note?: string | null;
  source?: VersionSource;
  chat_id?: string | null;
  message_seq?: number | null;
}

export const builderApi = {
  // the connection
  status: () => request<BuilderStatus>("/api/builder/status"),
  test: () => request<BuilderTest>("/api/builder/test", post()),
  unload: () => request<{ ok: boolean; unloaded: string[] }>("/api/builder/unload", post()),
  counts: () => request<BuilderCounts>("/api/builder/counts"),

  // chats
  listChats: (params: { project?: string; deleted?: boolean } = {}) => request<{ chats: ChatListItem[] }>(`/api/builder/chats${query(params)}`),
  createChat: (body: { title?: string; for?: ForKind; project_id?: string | null; detail?: Detail; prompt_id?: string | null }) => request<ChatInfo>("/api/builder/chats", post(body)),
  getChat: (id: string) => request<ChatFull>(`/api/builder/chats/${id}`),
  patchChat: (id: string, body: { title?: string; for?: ForKind; project_id?: string | null; detail?: Detail }) => request<ChatInfo>(`/api/builder/chats/${id}`, patch(body)),
  binChat: (id: string) => request<ChatInfo | { deleted: string }>(`/api/builder/chats/${id}/bin`, post()),
  restoreChat: (id: string) => request<ChatInfo>(`/api/builder/chats/${id}/restore`, post()),
  deleteChat: (id: string) => request<{ deleted: string }>(`/api/builder/chats/${id}`, { method: "DELETE" }),
  stopReply: (id: string) => request<{ stopping: boolean }>(`/api/builder/chats/${id}/stop`, post()),

  /** Send a message and read its reply as it is written; `onEvent` gets each event. Aborting `signal` closes the connection, which stops the reply and keeps none of it. */
  send: (id: string, body: MessageBody, onEvent: (event: ReplyEvent) => void, signal?: AbortSignal) =>
    readStream(`/api/builder/chats/${id}/messages`, body, (e) => onEvent({ name: e.name, ...(e.data as object) } as ReplyEvent), signal),

  // a chat's pictures: the file itself is the body, as for an edit's upload
  attachFile: async (chatId: string, file: Blob, name?: string): Promise<Attachment> => {
    let response: Response;
    try {
      response = await fetch(`/api/builder/chats/${chatId}/attachments${query({ name })}`, { method: "POST", headers: { "X-Studio-Client": "1", Accept: "application/json" }, body: file });
    } catch {
      throw toApiError(0, null);
    }
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      /* empty */
    }
    if (!response.ok) throw toApiError(response.status, body);
    return body as Attachment;
  },
  attachImage: (chatId: string, imageId: string) => request<Attachment>(`/api/builder/chats/${chatId}/attachments/from-image`, post({ image_id: imageId })),
  removeAttachment: (id: string) => request<{ deleted: string }>(`/api/builder/attachments/${id}`, { method: "DELETE" }),

  // prompts
  listPrompts: (params: PromptQuery = {}) => request<{ prompts: PromptCard[] }>(`/api/builder/prompts${query({ ...params })}`),
  createPrompt: (body: NewPrompt) => request<PromptDetail>("/api/builder/prompts", post(body)),
  getPrompt: (id: string) => request<PromptDetail>(`/api/builder/prompts/${id}`),
  patchPrompt: (id: string, body: { title?: string; for?: ForKind; favourite?: boolean; tags?: string[]; project_id?: string | null }) => request<PromptDetail>(`/api/builder/prompts/${id}`, patch(body)),
  addVersion: (id: string, body: NewVersion) => request<SaveAnswer>(`/api/builder/prompts/${id}/versions`, post(body)),
  makeCurrent: (id: string, n: number) => request<SaveAnswer>(`/api/builder/prompts/${id}/versions/${n}/restore`, post()),
  compare: (id: string, a: number, b: number) => request<Comparison>(`/api/builder/prompts/${id}/compare${query({ a: String(a), b: String(b) })}`),
  binPrompt: (id: string) => request<PromptDetail | { deleted: string }>(`/api/builder/prompts/${id}/bin`, post()),
  restorePrompt: (id: string) => request<PromptDetail>(`/api/builder/prompts/${id}/restore`, post()),
  deletePrompt: (id: string) => request<{ deleted: string }>(`/api/builder/prompts/${id}`, { method: "DELETE" }),
  fileRuns: (id: string, projectId?: string) => request<{ filed: string[]; project_id: string }>(`/api/builder/prompts/${id}/file-runs`, post(projectId ? { project_id: projectId } : {})),
  emptyBin: () => request<{ deleted: number }>("/api/builder/bin/empty", post()),

  // preferences
  listPreferences: () => request<{ preferences: Preference[] }>("/api/builder/preferences"),
  addPreference: (body: { text: string; project_id?: string | null; source?: "hand" | "suggestion" }) => request<Preference>("/api/builder/preferences", post(body)),
  patchPreference: (id: string, body: { text?: string; enabled?: boolean; project_id?: string | null }) => request<Preference>(`/api/builder/preferences/${id}`, patch(body)),
  deletePreference: (id: string) => request<{ deleted: string }>(`/api/builder/preferences/${id}`, { method: "DELETE" }),
};

export { ApiError };
