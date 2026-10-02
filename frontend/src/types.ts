// JSON shapes returned by the backend (backend/studio/serialize.py, api.py, jobs.py).

export type Mode = "generate" | "edit";
export type RunStatus = "queued" | "running" | "done" | "failed" | "canceled";
export type WorkerState = "unloaded" | "loading" | "ready" | "busy" | "error" | "unavailable";

export interface RunOptionsSnapshot {
  width: number | null;
  height: number | null;
  steps: number;
  seed: number;
  seed_was_random: boolean;
  num_images: number;
  negative_prompt: string | null;
  cfg_scale: number | null;
  transparent: boolean;
}

export interface ImageInfo {
  id: string;
  idx: number;
  seed: number;
  width: number;
  height: number;
  has_alpha: boolean;
  url: string;
  thumb_url: string | null;
  download_url: string;
}

export interface Progress {
  image: number;
  of: number;
  step: number;
  steps: number;
}

export interface Run {
  id: string;
  status: RunStatus;
  mode: Mode;
  prompt: string;
  effective_prompt: string;
  options: RunOptionsSnapshot;
  model_id: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: { message: string; hint: string | null } | null;
  pinned: boolean; // "Keep": never expires
  expires_at: string | null; // when it will be deleted automatically; null if kept, pending, or expiry is off
  queue_position: number | null;
  progress: Progress | null;
  canceling: boolean; // running, and the user has asked it to stop (it stops at the next step)
  images: ImageInfo[];
}

export interface WorkerStatus {
  state: WorkerState;
  detail: string | null;
  hint: string | null;
  pipeline: string;
  pid: number | null;
  unload_at: string | null;
  device: { name?: string; capability?: string; torch?: string; cuda?: string | null } | null;
  probe: string | null;
}

export interface Status {
  version: string;
  worker: WorkerStatus;
  queue: { running: string | null; queued: number; cap: number };
  memory: { total_gb?: number; available_gb?: number; min_free_gb: number | null; worker_rss_gb: number | null };
}

export interface Range {
  min: number;
  max: number;
}

export interface Capabilities {
  pipeline: string;
  model: string;
  modes: Mode[];
  supports: { negative_prompt?: boolean; cfg_scale?: boolean; step_progress?: boolean; transparent?: boolean; edit?: boolean };
  aspect_ratios: Record<string, [number, number]>;
  defaults: {
    mode: Mode;
    aspect_ratio: string;
    width: number;
    height: number;
    steps: number;
    seed: number | null;
    num_images: number;
    transparent: boolean;
  };
  limits: {
    prompt_chars: number;
    steps: Range;
    num_images: Range;
    seed: Range;
    cfg_scale: Range;
    size: Range & { multiple: number; max_pixels: number };
  };
  queue_cap: number;
  device: WorkerStatus["device"];
}

export interface CreateRunBody {
  mode: Mode;
  prompt: string;
  options: {
    width: number | null;
    height: number | null;
    steps: number;
    seed: number | null;
    num_images: number;
    negative_prompt: string | null;
    cfg_scale: number | null;
    transparent: boolean;
  };
}

export interface RunsPage {
  runs: Run[];
  next_before: string | null;
}

/** First event on every /api/events connection: a consistent starting point for the live updates. */
export interface Hello {
  status: Status;
  runs: RunsPage;
}
