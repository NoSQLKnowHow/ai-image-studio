// JSON shapes returned by the backend (backend/studio/serialize.py, api.py, jobs.py).

/** Pictures are made in one of two modes; music (DESIGN.md §26) is a third kind of run, on its own tab. */
export type ImageMode = "generate" | "edit";
export type Mode = ImageMode | "music";
/** The two models the studio can hold, one at a time (DESIGN.md §26.4). */
export type ModelName = "image" | "music";
export type RunStatus = "queued" | "running" | "done" | "failed" | "canceled";
export type WorkerState = "unloaded" | "loading" | "ready" | "busy" | "error" | "unavailable";

/** What an input image is for. Only "reference" is used until local edits (DESIGN.md §21.5). */
export type InputRole = "reference" | "marked" | "mask";

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
  draft?: boolean; // a small, quick try (DESIGN.md §22.2); absent on runs made before version 1.3
  full?: FullSize | null; // the size and steps this run stands in for (DESIGN.md §23.1); absent before 1.4, null at full size
  resolution?: number | null; // Edit: 1024 or 2048; null for Generate
  shape_from?: number | null; // Edit with Size on Auto: the 1-based image the result follows, when one was chosen
  roles?: InputRole[]; // the inputs' roles, in order (Edit)
}

export interface FullSize {
  width: number;
  height: number;
  steps: number;
}

/** How a 4K copy was made: `resize` is Make 4K (a standard resize), `model` is Enlarge (an upscaler model, DESIGN.md §28). */
export type FourKMethod = "resize" | "model";

/** The 4K copy of an image, once made (DESIGN.md §27). It is a file beside the image, so it is just a size and a link. */
export interface FourK {
  width: number;
  height: number;
  bytes: number;
  method: FourKMethod;
  url: string;
  download_url: string;
}

/** What Make 4K would make of a picture (DESIGN.md §27.3): its size, and whether a 16:9 (or 9:16) picture is trimmed to the frame. */
export interface FourKSize {
  width: number;
  height: number;
  trimmed: boolean;
}

/** What Enlarge would make of a picture (DESIGN.md §28.2): the size, and how many x2 passes of the model it takes (1 or 2). */
export interface EnlargeSize extends FourKSize {
  passes: number;
}

/** What a picture the studio holds says about Make 4K and Enlarge: a result (ImageInfo) or an edit's source (RunInput) alike. */
export interface FourKTarget {
  id: string;
  can_4k: boolean; // the server's rule for whether Make 4K is offered for this picture
  four_k_size: FourKSize | null; // what it would make; null when it is not offered
  can_enlarge: boolean; // the server's rule for whether Enlarge is offered (whether or not the model is installed)
  enlarge_size: EnlargeSize | null; // what it would make; null when it is not offered
  four_k: FourK | null; // its 4K copy, once made, by either
}

export interface ImageInfo extends FourKTarget {
  idx: number;
  seed: number;
  width: number;
  height: number;
  has_alpha: boolean;
  url: string;
  thumb_url: string | null;
  download_url: string;
}

/** An image an edit was given: its place in the order the model sees them (1 = "image 1"). */
export interface RunInput extends FourKTarget {
  position: number;
  role: InputRole;
  id: string;
  width: number;
  height: number;
  has_alpha: boolean;
  url: string;
  thumb_url: string | null;
}

export type MusicStage = "compose" | "render" | "finish";

export interface Progress {
  image: number; // the picture (or the track) being made, 1-based
  of: number;
  step: number;
  steps: number;
  stage?: MusicStage; // music only: composing (frame by frame), rendering (steps), finishing
}

/** What every run has, pictures or music. */
interface RunBase {
  id: string;
  status: RunStatus;
  prompt: string;
  effective_prompt: string;
  model_id: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: { message: string; hint: string | null } | null;
  pinned: boolean; // "Keep": never expires
  expires_at: string | null; // when it will be deleted automatically; null if kept, pending, in the bin, or expiry is off
  deleted_at: string | null; // when it went to the bin (DESIGN.md §30); null if it is not there
  purge_at: string | null; // when it will be deleted for good; null if it is not in the bin
  queue_position: number | null;
  progress: Progress | null;
  canceling: boolean; // running, and the user has asked it to stop (it stops at the next step)
}

export interface ImageRun extends RunBase {
  mode: ImageMode;
  options: RunOptionsSnapshot;
  lyrics: null;
  inputs: RunInput[]; // an edit's images, in order; empty for Generate
  images: ImageInfo[];
  tracks: [];
}

/** One finished track of a music run (DESIGN.md §26.3). `seconds` is what the model really made. */
export interface TrackInfo {
  id: string;
  idx: number;
  seed: number;
  seconds: number;
  sample_rate: number;
  channels: number;
  bytes: number;
  url: string;
  download_url: string;
}

/** What a music run used (DESIGN.md §26.5). `fields` is what the page's boxes said, kept for Reuse. */
export interface MusicOptionsSnapshot {
  duration: number;
  steps: number;
  seed: number;
  seed_was_random: boolean;
  tracks: number;
  instrumental: boolean;
  fields: Record<string, string>;
}

export interface MusicRun extends RunBase {
  mode: "music";
  options: MusicOptionsSnapshot;
  lyrics: string | null; // null for an instrumental track
  inputs: [];
  images: [];
  tracks: TrackInfo[];
}

export type Run = ImageRun | MusicRun;

export const isMusicRun = (run: Run): run is MusicRun => run.mode === "music";
export const isImageRun = (run: Run): run is ImageRun => run.mode !== "music";

export interface WorkerStatus {
  state: WorkerState;
  detail: string | null;
  hint: string | null;
  pipeline: string;
  model: ModelName; // the model the worker holds, or last tried to load (DESIGN.md §26.3)
  pid: number | null;
  unload_at: string | null;
  idle_timeout_min: number; // 0 = unload as soon as the queue is empty, so loading ahead of time is not offered (§25)
  device: { name?: string; capability?: string; torch?: string; cuda?: string | null } | null;
  probe: string | null;
}

export interface Status {
  version: string;
  worker: WorkerStatus;
  queue: { running: string | null; queued: number; cap: number; enlarge_waiting: string[] }; // enlarge_waiting: images whose Enlarge is waiting for the GPU (DESIGN.md §28.3)
  memory: { total_gb?: number; available_gb?: number; min_free_gb: number | null; music_min_free_gb: number | null; worker_rss_gb: number | null };
}

export interface Range {
  min: number;
  max: number;
}

/** What the music model needs from the page (DESIGN.md §26.3): the limits the server enforces. */
export interface MusicLimits {
  duration: Range & { default: number }; // seconds, an upper bound: the model may end a piece sooner
  tracks: Range; // versions of one description
  steps: Range & { default: number }; // rendering steps
  description_chars: number;
  lyrics_chars: number;
  field_chars: number;
}

/** Whether the music model can run here, and if not, why (the Music tab explains it). */
export interface MusicAvailability {
  available: boolean;
  state: string | null;
  reason: string | null;
  hint: string | null;
  model: string;
}

/** Whether Enlarge can run here and, if not, why and what to do (DESIGN.md §28.3). */
export interface UpscalerStatus {
  available: boolean;
  model: string | null;
  reason: string | null;
  hint: string | null;
  max_enlargement: number;
}

export interface Capabilities {
  pipeline: string;
  model: string;
  modes: Mode[];
  supports: { negative_prompt?: boolean; cfg_scale?: boolean; step_progress?: boolean; transparent?: boolean; edit?: boolean; multi_image?: boolean };
  aspect_ratios: Record<string, [number, number]>;
  defaults: {
    mode: ImageMode;
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
    draft: { long_side: number; steps: number }; // what a draft may be: its long side in pixels, and its most steps
    input_images: Range; // how many images one edit may use
    resolutions: number[]; // Edit's 1K / 2K choices (1024, 2048)
    upload_mb: number; // the largest single upload
    edit_warn_units: number; // an edit costing more units than this gets a warning; 0 = never (DESIGN.md §21.4)
    bin_days: number; // how long a deleted run stays in the bin; 0 = there is no bin and Delete is for good (DESIGN.md §30)
    music: MusicLimits;
  };
  music: MusicAvailability;
  upscaler: UpscalerStatus;
  queue_cap: number;
  device: WorkerStatus["device"];
}

/** How an edit names one of its images: an upload that is waiting, or an image from an earlier run. */
export type InputRef = ({ upload_id: string } | { image_id: string }) & { role?: InputRole };

/** What POST /api/uploads answers (DESIGN.md §21.6). */
export interface UploadResult {
  upload_id: string;
  width: number;
  height: number;
  has_alpha: boolean;
  bytes: number;
  url: string;
  thumb_url: string | null;
}

export interface CreateRunBody {
  mode: ImageMode;
  prompt: string;
  input_images?: InputRef[]; // Edit only, in the order the model sees them
  options: {
    width: number | null;
    height: number | null;
    steps: number;
    seed: number | null;
    num_images: number;
    negative_prompt: string | null;
    cfg_scale: number | null;
    transparent: boolean;
    draft?: boolean;
    full?: FullSize;
    resolution?: number; // Edit only
    shape_from?: number; // Edit only, with Size on Auto
  };
}

/** A music run (DESIGN.md §26.3). No lyrics means an instrumental track: the server sends the [Instrumental] tag. */
export interface CreateMusicBody {
  mode: "music";
  prompt: string; // the finished description
  lyrics?: string;
  options: {
    duration: number;
    tracks: number;
    steps: number;
    seed: number | null;
    fields: Record<string, string>;
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
