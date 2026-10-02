import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// End-to-end tests drive the real backend (fake pipeline, no GPU) serving the built UI.
// Run `npm run build` first. STUDIO_PYTHON points at a Python with the backend's requirements.
const port = Number(process.env.STUDIO_E2E_PORT ?? 8099);
const python = process.env.STUDIO_PYTHON ?? resolve("../backend/.venv/bin/python");
const dataDir = mkdtempSync(join(tmpdir(), "studio-e2e-"));

export default defineConfig({
  testDir: "e2e",
  timeout: 45_000,
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `"${python}" -m studio`,
    cwd: resolve("../backend"),
    url: `http://127.0.0.1:${port}/api/health`,
    timeout: 60_000,
    reuseExistingServer: false,
    env: {
      STUDIO_PIPELINE: "fake",
      STUDIO_DATA_DIR: dataDir,
      STUDIO_HOST: "127.0.0.1",
      STUDIO_PORT: String(port),
      STUDIO_STATIC_DIR: resolve("dist"),
      STUDIO_FAKE_STEP_DELAY_MS: "10",
      STUDIO_QUEUE_CAP: "3",
    },
  },
});
