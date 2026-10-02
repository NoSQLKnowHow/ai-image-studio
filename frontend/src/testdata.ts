// Fixtures for unit tests, shaped like the real API responses.
import type { Capabilities, Run, Status } from "./types";

export const CAPS: Capabilities = {
  pipeline: "fake",
  model: "fake-pipeline",
  modes: ["generate"],
  supports: { negative_prompt: true, cfg_scale: true, step_progress: true, transparent: true, edit: false },
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
  },
  queue_cap: 10,
  device: { name: "fake (no GPU used)" },
};

let counter = 0;
export function makeRun(overrides: Partial<Run> = {}): Run {
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
    queue_position: null,
    progress: null,
    canceling: false,
    images: [],
    ...overrides,
  };
}

export const STATUS: Status = {
  version: "1.4",
  worker: { state: "ready", detail: null, hint: null, pipeline: "fake", pid: 1, unload_at: null, device: null, probe: "done" },
  queue: { running: null, queued: 0, cap: 10 },
  memory: { total_gb: 119, available_gb: 80, min_free_gb: null, worker_rss_gb: null },
};
