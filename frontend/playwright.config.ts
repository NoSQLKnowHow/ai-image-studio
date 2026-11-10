import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// End-to-end tests drive the real backend (fake pipeline, no GPU) serving the built UI.
// Run `npm run build` first. STUDIO_PYTHON points at a Python with the backend's requirements.
const port = Number(process.env.STUDIO_E2E_PORT ?? 8099);
const python = process.env.STUDIO_PYTHON ?? resolve("../backend/.venv/bin/python");
// This file is evaluated again in every worker process. The first evaluation (the runner, which also starts the
// server) makes the directory and records it in the environment; workers inherit that and must reuse it, so the
// tests that age a run behind the server's back open the server's own database.
const dataDir = process.env.STUDIO_E2E_DATA_DIR ?? mkdtempSync(join(tmpdir(), "studio-e2e-"));
process.env.STUDIO_E2E_DATA_DIR = dataDir;
// The load-progress tests (e2e/progress.spec.ts, DESIGN.md §39) have a server of their own: it pretends to download a model while it loads, which would make every
// other test's load longer and show a number the others do not look for. It has its own port, its own data and its own Hugging Face cache folder.
const progressPort = port + 1;
const progressDir = process.env.STUDIO_E2E_PROGRESS_DIR ?? mkdtempSync(join(tmpdir(), "studio-e2e-progress-"));
process.env.STUDIO_E2E_PROGRESS_DIR = progressDir;

/** A fake-pipeline server on `listen`, keeping its data in `data`; `extra` is what that server is for. */
const studio = (listen: number, data: string, extra: Record<string, string> = {}) => ({
  command: `"${python}" -m studio`,
  cwd: resolve("../backend"),
  url: `http://127.0.0.1:${listen}/api/health`,
  timeout: 60_000,
  reuseExistingServer: false,
  env: {
    STUDIO_PIPELINE: "fake",
    STUDIO_DATA_DIR: data,
    STUDIO_HOST: "127.0.0.1",
    STUDIO_PORT: String(listen),
    STUDIO_STATIC_DIR: resolve("dist"),
    STUDIO_FAKE_STEP_DELAY_MS: "10",
    STUDIO_FAKE_LOAD_DELAY_MS: "1200", // long enough for a test to see "Loading model…" (§25)
    STUDIO_QUEUE_CAP: "3",
    STUDIO_BUILDER_LLM: "fake", // the Prompt builder talks to a scripted model, not an Ollama server (DESIGN.md §37.4)
    ...extra,
  },
});

export default defineConfig({
  testDir: "e2e",
  timeout: 45_000,
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [
    { name: "chromium", testIgnore: "progress.spec.ts", use: { ...devices["Desktop Chrome"] } },
    { name: "progress", testMatch: "progress.spec.ts", use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${progressPort}` } },
  ],
  webServer: [
    studio(port, dataDir),
    studio(progressPort, progressDir, {
      STUDIO_FAKE_DOWNLOAD_MB: "400", // each fake load pretends to download this much: a sparse file that grows in the cache folder below, for 70% of the load
      STUDIO_FAKE_LOAD_DELAY_MS: "3000", // so that there is time to see both halves, the download and the loading into memory, in a slow browser
      STUDIO_LOAD_PROGRESS_MS: "150", // the worker reports how far it has got this often (a second, by default)
      HF_HOME: join(progressDir, "hf"),
    }),
  ],
});
