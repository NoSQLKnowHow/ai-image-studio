// The Prompt builder's shapes (DESIGN.md §37.4), as the server sends them. Written out here so that the page and the tests say the same keys.

export type ForKind = "images" | "vector" | "music" | "edit";
export type Detail = "short" | "normal" | "rich";
export type VersionSource = "builder" | "hand" | "wording" | "revision";
export type Intent = "message" | "write_now" | "variations" | "flesh_out" | "refine" | "describe" | "help_edit";

/** `capabilities.builder`: what is already known, never a call. `reachable` and `vision` are null until something has looked. */
export interface BuilderCaps {
  available: boolean;
  configured: boolean;
  reachable: boolean | null;
  model: string;
  vision: boolean | null;
}

/** `capabilities.limits.builder` */
export interface BuilderLimits {
  questions_max: number;
  chips: { min: number; max: number };
  chip_chars: number;
  reply_chars: number;
  title_chars: number;
  note_chars: number;
  tags_max: number;
  tag_chars: number;
  preferences_max: number;
  preference_chars: number;
}

/** `GET /api/builder/status` */
export interface BuilderStatus {
  configured: boolean;
  reachable: boolean;
  model: string;
  vision: boolean;
  think: "on" | "off" | "model"; // the setting a message's Think switch starts from (STUDIO_OLLAMA_THINK)
  message: string | null;
  hint: string | null;
}

/** `POST /api/builder/test` (Test connection) */
export interface BuilderTest {
  reachable: boolean;
  version: string;
  models: string[];
  model: string;
  model_present: boolean;
  vision: boolean;
  vision_model: string;
  vision_model_present: boolean;
  context_length: number | null;
  message: string | null;
  hint: string | null;
}

export interface Question {
  id: string;
  text: string;
  chips: string[];
}

export interface Draft {
  prompt: string;
  negative: string | null;
  settings: Record<string, unknown>;
  note: string;
}

export interface Wording {
  label: string;
  prompt: string;
  negative: string | null;
}

/** What the model said, as it is stored and sent (the `content` of an assistant message). */
export interface AnswerContent {
  reply: string;
  title: string | null;
  questions: Question[];
  draft: Draft | null;
  variations: Wording[];
  remember: string[];
  tags: string[];
  done: boolean;
  notices: string[];
  plain: boolean;
  stopped?: boolean;
  context?: { preferences: number; examples: { id: string; title: string }[]; dropped: number };
}

export interface AnswerChoice {
  id: string;
  question: string;
  mode: "chips" | "surprise" | "skip";
  chips: string[];
}

/** What a person's message carried besides their words. */
export interface UserContent {
  answers: AnswerChoice[];
  intent: Intent;
  draft: Draft | null;
  think: boolean | null;
}

export interface Attachment {
  id: string;
  url: string;
  width: number;
  height: number;
  name: string | null;
}

export interface ChatMessage {
  seq: number;
  role: "user" | "assistant";
  text: string;
  content: (AnswerContent & Partial<UserContent>) | UserContent | null;
  model: string | null;
  created_at: string;
  attachments: Attachment[];
}

export interface ChatInfo {
  id: string;
  title: string;
  for: ForKind;
  project_id: string | null;
  detail: Detail;
  prompt_id: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface ChatListItem extends ChatInfo {
  last_text: string;
  message_count: number;
}

export interface ChatFull {
  chat: ChatInfo;
  messages: ChatMessage[];
  draft: Draft | null;
  pending: Attachment[];
  preferences: number;
}

export interface Thumb {
  id: string;
  url: string;
}

export interface PromptCard {
  id: string;
  title: string;
  for: ForKind;
  favourite: boolean;
  tags: string[];
  project_id: string | null;
  version: number;
  versions: number;
  text: string;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  purge_at: string | null;
  thumbs: Thumb[];
  pictures: number;
}

export interface VersionRun {
  run_id: string;
  mode: string;
  status: string;
  seed: number;
  created_at: string;
  draft: boolean;
  images: { id: string; url: string; thumb_url: string }[];
}

export interface PromptVersion {
  n: number;
  text: string;
  negative: string | null;
  settings: Record<string, unknown>;
  note: string | null;
  source: VersionSource;
  chat_id: string | null;
  message_seq: number | null;
  created_at: string;
  runs: VersionRun[];
}

/** `GET /api/builder/prompts/{id}`: the card's fields, and the versions as a list (newest first) in place of the card's count. */
export type PromptDetail = Omit<PromptCard, "versions"> & {
  negative: string | null;
  settings: Record<string, unknown>;
  versions: PromptVersion[];
  chats: { id: string; title: string; updated_at: string }[];
  unfiled_runs?: number;
};

export interface SaveAnswer {
  created: boolean;
  message: string;
  version: number;
  prompt: PromptDetail;
}

export interface Preference {
  id: string;
  text: string;
  project_id: string | null;
  enabled: boolean;
  source: "hand" | "suggestion";
  position: number;
  created_at: string;
  orphaned: boolean;
}

export interface BuilderCounts {
  prompts: { all: number; favourites: number; deleted: number; no_project: number; projects: Record<string, number>; tags: Record<string, number> };
  chats: { all: number; deleted: number; no_project: number; projects: Record<string, number> };
  preferences: { all: number; enabled: number; orphaned: number };
}

export type DiffRun = { op: "same" | "add" | "del"; text: string };

export interface Comparison {
  a: { n: number; note: string | null; source: VersionSource; created_at: string };
  b: { n: number; note: string | null; source: VersionSource; created_at: string };
  text: DiffRun[];
  negative: DiffRun[];
  settings: { key: string; before: unknown; after: unknown; status: "same" | "changed" | "added" | "removed" }[];
  changed: boolean;
}

/** What the page sends with a message. */
export interface MessageBody {
  text?: string;
  answers?: { id: string; chips?: string[]; mode?: "chips" | "surprise" | "skip" }[];
  intent?: Intent;
  draft?: { prompt: string; negative: string | null; settings: Record<string, unknown>; note?: string } | null;
  think?: boolean | null;
  attachments?: string[];
  similar?: boolean;
  exclude?: string[];
  retry?: boolean;
  redo?: boolean; // with retry: throw away the model's last reply and answer again
  run_id?: string | null;
}

/** The server-sent events of a reply, as the page gets them. */
export type ReplyEvent =
  | { name: "waiting" }
  | { name: "busy"; images: boolean }
  | { name: "start"; seq: number }
  | { name: "context"; preferences: number; examples: { id: string; title: string }[]; dropped: number }
  | { name: "thinking"; text: string; seconds: number }
  | { name: "delta"; text: string; thought: boolean }
  | { name: "retry" }
  | { name: "result"; message: ChatMessage; chat: ChatInfo }
  | { name: "stopped"; message: ChatMessage | null; chat?: ChatInfo }
  | { name: "error"; code: string; message: string; hint: string | null; status: number };
