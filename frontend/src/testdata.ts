// Fixtures for unit tests, shaped like the real API responses.
import type { BinnedImage, BinnedTrack, Capabilities, ImageInfo, ImageRun, MusicRun, Status, TrackInfo } from "./types";

export const CAPS: Capabilities = {
  pipeline: "fake",
  model: "fake-pipeline",
  modes: ["generate", "edit", "music"],
  supports: { negative_prompt: true, cfg_scale: true, step_progress: true, transparent: true, edit: true, multi_image: true },
  aspect_ratios: {
    "1:1": [2048, 2048], "4:3": [2400, 1792], "3:4": [1792, 2400], "3:2": [2528, 1696],
    "2:3": [1696, 2528], "16:9": [2752, 1536], "9:16": [1536, 2752],
  },
  defaults: { mode: "generate", aspect_ratio: "1:1", width: 2048, height: 2048, steps: 40, seed: null, num_images: 1, transparent: false },
  limits: {
    prompt_chars: 8000,
    steps: { min: 1, max: 100 },
    num_images: { min: 1, max: 8 },
    seed: { min: 0, max: 4294967295 },
    cfg_scale: { min: 0.1, max: 20 },
    size: { min: 256, max: 4096, multiple: 32, max_pixels: 4_500_000 },
    draft: { long_side: 512, steps: 12 },
    input_images: { min: 1, max: 4 },
    resolutions: [1024, 2048],
    upload_mb: 20,
    edit_warn_units: 8,
    bin_days: 30,
    project_name_max: 60,
    music: {
      duration: { min: 10, max: 300, default: 60 },
      tracks: { min: 1, max: 4 },
      steps: { min: 10, max: 60, default: 30 },
      description_chars: 2000,
      lyrics_chars: 6000,
      field_chars: 400,
    },
  },
  music: { available: true, state: "done", reason: null, hint: null, model: "fake-music" },
  upscaler: { available: true, model: "fake", reason: null, hint: null, max_enlargement: 4 },
  queue_cap: 10,
  device: { name: "fake (no GPU used)" },
};

/** One result picture as the server sends it (DESIGN.md §33.3): `idx` is its place in the run, `seed` what names it. Its id says both. */
export function makeImage(idx: number, overrides: Partial<ImageInfo> = {}): ImageInfo {
  const id = overrides.id ?? `image${idx}`;
  return {
    id, idx, seed: 100 + idx, width: 2048, height: 2048, has_alpha: false,
    url: `/api/images/${id}`, thumb_url: `/api/images/${id}/thumb`, download_url: `/api/images/${id}?download=1`,
    can_4k: false, four_k_size: null, can_enlarge: false, enlarge_size: null, four_k: null,
    ...overrides,
  };
}

/** A result picture that is in the bin on its own. */
export function makeBinnedImage(idx: number, overrides: Partial<BinnedImage> = {}): BinnedImage {
  return { ...makeImage(idx), deleted_at: "2026-10-14T10:00:00.000Z", purge_at: "2026-11-13T10:00:00.000Z", ...overrides };
}

/** One track of a music run, 30 seconds long. */
export function makeTrack(idx: number, overrides: Partial<TrackInfo> = {}): TrackInfo {
  const id = overrides.id ?? `track${idx}`;
  return { id, idx, seed: 200 + idx, seconds: 30, sample_rate: 24000, channels: 1, bytes: 1440044, url: `/api/audio/${id}`, download_url: `/api/audio/${id}?download=1`, ...overrides };
}

/** A track that is in the bin on its own. */
export function makeBinnedTrack(idx: number, overrides: Partial<BinnedTrack> = {}): BinnedTrack {
  return { ...makeTrack(idx), deleted_at: "2026-10-14T10:00:00.000Z", purge_at: "2026-11-13T10:00:00.000Z", ...overrides };
}

let counter = 0;
export function makeRun(overrides: Partial<ImageRun> = {}): ImageRun {
  counter += 1;
  return {
    id: overrides.id ?? `run${String(counter).padStart(4, "0")}`,
    status: "done",
    mode: "generate",
    prompt: "a lighthouse at dusk",
    effective_prompt: "a lighthouse at dusk",
    options: { width: 2048, height: 2048, steps: 40, seed: 42, seed_was_random: false, num_images: 1, negative_prompt: null, cfg_scale: null, transparent: false },
    model_id: "fake-pipeline",
    created_at: `2026-10-02T10:00:${String(counter % 60).padStart(2, "0")}.000Z`,
    started_at: null,
    finished_at: null,
    error: null,
    pinned: false,
    expires_at: null,
    deleted_at: null,
    purge_at: null,
    project_id: null,
    queue_position: null,
    progress: null,
    canceling: false,
    lyrics: null,
    inputs: [],
    images: [],
    binned_images: [],
    tracks: [],
    binned_tracks: [],
    ...overrides,
  };
}

let musicCounter = 0;
/** A music run as the server sends it (DESIGN.md §26.3, §26.5): an instrumental track unless `lyrics` is given. */
export function makeMusicRun(overrides: Partial<MusicRun> = {}): MusicRun {
  musicCounter += 1;
  return {
    id: overrides.id ?? `music${String(musicCounter).padStart(4, "0")}`,
    status: "done",
    mode: "music",
    prompt: "Global Metadata\nBasic Attributes: ambient.\nInstrumental, no vocals.",
    effective_prompt: "Global Metadata\nBasic Attributes: ambient.\nInstrumental, no vocals.",
    options: { duration: 60, steps: 30, seed: 7, seed_was_random: false, tracks: 1, instrumental: true, fields: { genre: "ambient" } },
    model_id: "fake-music",
    created_at: `2026-10-02T11:00:${String(musicCounter % 60).padStart(2, "0")}.000Z`,
    started_at: null,
    finished_at: null,
    error: null,
    pinned: false,
    expires_at: null,
    deleted_at: null,
    purge_at: null,
    project_id: null,
    queue_position: null,
    progress: null,
    canceling: false,
    lyrics: null,
    inputs: [],
    images: [],
    binned_images: [],
    tracks: [],
    binned_tracks: [],
    ...overrides,
  };
}

export const STATUS: Status = {
  version: "1.15",
  worker: { state: "ready", detail: null, hint: null, pipeline: "fake", model: "image", pid: 1, unload_at: null, idle_timeout_min: 30, device: null, probe: "done" },
  queue: { running: null, queued: 0, cap: 10, enlarge_waiting: [] },
  memory: { total_gb: 119, available_gb: 80, min_free_gb: null, music_min_free_gb: null, worker_rss_gb: null },
};
