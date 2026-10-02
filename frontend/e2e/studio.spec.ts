// End-to-end: the built UI against the real backend (fake pipeline). See playwright.config.ts.
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, test as base, type Locator, type Page } from "@playwright/test";

// Every test fails on browser console errors: CSP violations and runtime errors surface there.
// "Failed to load resource" for a 4xx the test provoked on purpose is expected and ignored.
const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error" && !/Failed to load resource: the server responded with a status of 4\d\d/.test(m.text())) errors.push(m.text());
      });
      page.on("pageerror", (e) => errors.push(String(e)));
      await use(errors);
      expect(errors, "browser console errors").toEqual([]);
    },
    { auto: true },
  ],
});

const FAST = { mode: "generate", aspect: "custom", customWidth: 512, customHeight: 512, steps: 8, seedLocked: false, seed: 42,
  numImages: 1, negativePrompt: "", guidance: null, transparent: false };

async function useOptions(page: Page, overrides: Record<string, unknown> = {}) {
  await page.addInitScript((opts) => localStorage.setItem("studio.options.v1", JSON.stringify(opts)), { ...FAST, ...overrides });
}

const unique = (text: string) => `${text} ${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const card = (page: Page, prompt: string): Locator => page.locator("article.run-card", { hasText: prompt });
// exact: run cards carry aria-labels made from their prompts, which may contain the word "prompt"
const promptBox = (page: Page): Locator => page.getByRole("textbox", { name: "Prompt", exact: true });

async function generate(page: Page, prompt: string): Promise<Locator> {
  await promptBox(page).fill(prompt);
  await page.keyboard.press("Control+Enter");
  const c = card(page, prompt).first();
  await expect(c).toBeVisible();
  return c;
}

/** Delete every run over the API (waiting out one that's still generating). */
async function clearHistory(page: Page) {
  await expect
    .poll(async () => {
      const { runs } = (await (await page.request.get("/api/runs?limit=100")).json()) as { runs: { id: string; status: string }[] };
      for (const run of runs) {
        if (run.status !== "running") await page.request.delete(`/api/runs/${run.id}`, { headers: { "X-Studio-Client": "1" } });
      }
      return runs.length;
    }, { timeout: 20_000 })
    .toBe(0);
}

async function setRange(locator: Locator, value: number) {
  await locator.evaluate((el, v) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(el, String(v));
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, value);
}

test("loads cleanly and explains the model state", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "AI Image Studio" })).toBeVisible();
  // whatever state the model is in (a run from an earlier test may still be going)
  const pill = page.locator('button[aria-controls="model-details"]');
  await expect(pill).toHaveText(/^(Model not loaded|Loading model…|Model ready|Generating)/);
  await pill.click();
  const details = page.getByRole("region", { name: "Model details" });
  await expect(details).toContainText("Test pipeline (fake images, no GPU)");
  await page.keyboard.press("Escape");
  await expect(details).toBeHidden();
  await expect(page.getByRole("radio", { name: /Edit/ })).toBeDisabled(); // arrives in M5
});

test("theme follows the system by default, cycles, and persists without a flash", async ({ page }) => {
  await page.goto("/");
  const toggle = page.getByRole("button", { name: /^Theme:/ });
  await expect(toggle).toHaveAccessibleName(/^Theme: System/);
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload({ waitUntil: "domcontentloaded" });
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark"); // set before the app ran
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(15, 15, 19)");
  await page.getByRole("button", { name: /^Theme: Dark/ }).click();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.*/);
});

test("options drawer is accessible, persists across reloads, and validates", async ({ page }) => {
  await page.goto("/");
  const optionsButton = page.getByRole("button", { name: /^Options/ });
  await optionsButton.click();
  const drawer = page.getByRole("dialog", { name: "Options" });
  await expect(drawer).toBeVisible();
  expect(await drawer.evaluate((el) => el.contains(document.activeElement))).toBe(true); // focus moved inside

  await drawer.getByRole("radio", { name: "16:9, 2752 by 1536" }).click();
  await setRange(drawer.getByLabel(/^Steps/), 12);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(optionsButton).toBeFocused(); // focus returns to where it was
  await expect(optionsButton).toContainText("2752×1536 · 12 steps · random seed");

  await page.reload();
  await expect(page.getByRole("button", { name: /^Options/ })).toContainText("2752×1536 · 12 steps");
  await page.getByRole("button", { name: /^Options/ }).click();
  await expect(drawer.getByRole("radio", { name: "16:9, 2752 by 1536" })).toHaveAttribute("aria-checked", "true");

  await drawer.getByRole("radio", { name: "Custom" }).click();
  await drawer.getByLabel("Width").fill("1040");
  await expect(drawer.getByText("Each side must be a multiple of 32.")).toBeVisible();
  await page.keyboard.press("Escape");
  await promptBox(page).fill("should not be sent");
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(drawer).toBeVisible(); // reopened to fix it, with the problem shown in place
  await expect(drawer.getByText("Each side must be a multiple of 32.")).toBeVisible();
  // the prompt bar says why too (behind the modal, so hidden from the accessibility tree until it closes)
  await expect(page.getByText("Check the options: Each side must be a multiple of 32.")).toBeVisible();
  await drawer.getByRole("button", { name: "Reset to defaults" }).click();
  await page.keyboard.press("Escape");
  await expect(optionsButton).toContainText("2048×2048 · 40 steps · random seed");
  await expect(page.getByText("Check the options")).toHaveCount(0); // fixed, so the message went away
});

test("generate shows live progress, then the finished image with a meaningful download", async ({ page }) => {
  await useOptions(page, { steps: 100 }); // ~1 s of fake work, so the progress bar is observable
  await page.goto("/");
  const prompt = unique("a lighthouse at dusk");
  const c = await generate(page, prompt);
  await expect(c.getByRole("progressbar")).toBeVisible();
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const thumb = c.locator(".thumb img");
  await expect(thumb).toBeVisible();
  expect(await thumb.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await expect(c.locator(".run-meta")).toContainText("512×512 · 100 steps · seed");

  const href = await c.getByRole("link", { name: "Download" }).getAttribute("href");
  expect(href).toMatch(/^\/api\/images\/[0-9a-f]{32}\?download=1$/);
  const response = await page.request.get(href!);
  expect(response.headers()["content-disposition"]).toMatch(/filename="generate_lighthouse-dusk/);
});

test("reuse restores the prompt and locks the seed", async ({ page }) => {
  await useOptions(page);
  await page.goto("/");
  const prompt = unique("a red barn in snow");
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const seed = (await c.locator(".run-meta").textContent())!.match(/seed (\d+)/)![1];

  await promptBox(page).fill("something else entirely");
  await c.getByRole("button", { name: "Reuse" }).click();
  await expect(promptBox(page)).toHaveValue(prompt);
  await expect(page.getByRole("button", { name: /^Options/ })).toContainText(`seed ${seed}`);
  await expect(page.getByRole("status").filter({ hasText: `Seed locked to ${seed}` })).toBeVisible();
});

test("a failure shows the reason, and Retry runs it again", async ({ page }) => {
  await useOptions(page);
  await page.goto("/");
  const prompt = unique("broken on purpose [fake:error]");
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Failed", { timeout: 20_000 });
  await expect(c.getByRole("alert")).toContainText("Simulated pipeline failure");
  await c.getByRole("button", { name: "Retry" }).click();
  await expect(card(page, prompt)).toHaveCount(2);
});

test("delete asks first, removes the run, and keeps your place", async ({ page }) => {
  await useOptions(page);
  await page.goto("/");
  await clearHistory(page);
  await expect(page.getByText("No images yet")).toBeVisible(); // the deletions arrived as live events
  const older = await generate(page, unique("a teapot"));
  await expect(older.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const newerPrompt = unique("a kettle");
  const newer = await generate(page, newerPrompt);
  await expect(newer.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });

  await newer.getByRole("button", { name: "Delete" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Delete this run?" });
  await expect(confirm.getByRole("button", { name: "Cancel" })).toBeFocused(); // the safe choice has focus
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(card(page, newerPrompt)).toHaveCount(1);
  await expect(newer.getByRole("button", { name: "Delete" })).toBeFocused(); // back where you were

  await newer.getByRole("button", { name: "Delete" }).click();
  await confirm.getByRole("button", { name: "Delete" }).click();
  await expect(card(page, newerPrompt)).toHaveCount(0);
  await expect(older.getByRole("button", { name: "Delete" })).toBeFocused(); // the next card's Delete

  await page.keyboard.press("Enter");
  await confirm.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No images yet")).toBeVisible();
  await expect(promptBox(page)).toBeFocused(); // nothing left to land on, so the prompt box
});

test("the lightbox shows full-size images and pages through a batch", async ({ page }) => {
  await useOptions(page, { numImages: 2 });
  await page.goto("/");
  const prompt = unique("two foxes");
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const thumbnail = c.getByRole("button", { name: /Open image 1 of 2/ });
  await thumbnail.click();
  const box = page.getByRole("dialog");
  await expect(box).toContainText("Image 1 of 2");
  await page.keyboard.press("ArrowRight");
  await expect(box).toContainText("Image 2 of 2");
  expect(await box.locator("img").getAttribute("src")).toMatch(/^\/api\/images\/[0-9a-f]{32}$/);
  await page.keyboard.press("Escape");
  await expect(box).toBeHidden();
  await expect(thumbnail).toBeFocused();
});

test("phone width: no sideways scrolling, and Options is a bottom sheet", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await useOptions(page);
  await page.goto("/");
  const c = await generate(page, unique("a very long prompt " + "with many words ".repeat(30)));
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  await page.getByRole("button", { name: /^Options/ }).click();
  const rect = await page.getByRole("dialog", { name: "Options" }).boundingBox();
  expect(rect!.x).toBeLessThanOrEqual(1);
  expect(Math.round(rect!.width)).toBeGreaterThanOrEqual(359);
  expect(Math.round(rect!.y + rect!.height)).toBeGreaterThanOrEqual(739);
});

test("a full queue gets a clear message", async ({ page }) => {
  await useOptions(page, { steps: 100, numImages: 8 }); // ~8 s per run, so the queue fills up
  await page.goto("/");
  for (let i = 1; i <= 4; i++) await generate(page, unique(`queue filler ${i}`));
  await promptBox(page).fill(unique("one too many"));
  await page.keyboard.press("Control+Enter");
  await expect(page.getByRole("alert").filter({ hasText: "The queue is full (3 jobs waiting)" })).toBeVisible();
  // tidy up: delete what's still waiting so the server can stop promptly
  const { runs } = await (await page.request.get("/api/runs?limit=50")).json();
  for (const run of runs.filter((r: { status: string }) => r.status === "queued")) {
    await page.request.delete(`/api/runs/${run.id}`, { headers: { "X-Studio-Client": "1" } });
  }
});

test("a queued job is canceled at once, never runs, and focus stays on its card", async ({ page }) => {
  await useOptions(page, { steps: 100, numImages: 4 }); // ~4 s, so the next job has to wait behind it
  await page.goto("/");
  await clearHistory(page);
  const first = await generate(page, unique("the long one"));
  const waiting = await generate(page, unique("waits in the queue"));
  await expect(waiting.locator(".badge").first()).toHaveText(/^Queued/);

  await waiting.getByRole("button", { name: "Cancel" }).click(); // nothing is lost, so it doesn't ask
  await expect(waiting.locator(".badge").first()).toHaveText("Canceled");
  await expect(waiting.getByText("Canceled before any image was finished.")).toBeVisible();
  await expect(waiting.getByRole("button", { name: "Reuse" })).toBeFocused(); // the Cancel button went away
  await expect(waiting.getByRole("button", { name: "Cancel" })).toHaveCount(0);
  await expect(waiting.getByRole("button", { name: "Keep" })).toBeVisible(); // a finished card can be kept

  await expect(first.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 }); // the one ahead is unaffected
  await expect(waiting.locator(".badge").first()).toHaveText("Canceled"); // and the canceled one never started
  await expect(waiting.locator(".thumb")).toHaveCount(0);
});

test("canceling a running job asks first, keeps what was finished, and the worker carries on", async ({ page }) => {
  await useOptions(page, { steps: 100, numImages: 8 }); // ~8 s: time to stop it part way
  await page.goto("/");
  await clearHistory(page);
  const c = await generate(page, unique("stop me part way"));
  await expect(c.getByRole("status")).toContainText(/Image [2-8] of 8/, { timeout: 20_000 }); // at least one image is done
  const pid = ((await (await page.request.get("/api/status")).json()) as { worker: { pid: number } }).worker.pid;

  const cancel = c.getByRole("button", { name: "Cancel" });
  await cancel.click();
  const confirm = page.getByRole("alertdialog", { name: "Stop this run?" });
  await expect(confirm).toContainText(/already finished (is|are) kept/);
  await expect(confirm.getByRole("button", { name: "Keep going" })).toBeFocused(); // the safe choice has focus
  await confirm.getByRole("button", { name: "Keep going" }).click();
  await expect(cancel).toBeFocused(); // back where you were, and nothing changed
  await expect(c.locator(".badge").first()).toHaveText("Generating");

  await cancel.click();
  await confirm.getByRole("button", { name: "Stop generating" }).click();
  await expect(c.locator(".badge").first()).toHaveText(/^(Stopping|Canceled)$/);
  await expect(c.locator(".badge").first()).toHaveText("Canceled", { timeout: 15_000 });
  await expect(c.locator(".run-note")).toHaveText(/^Canceled\. [1-7] of 8 images finished and kept\.$/);
  await expect(c.locator(".thumb").first()).toBeVisible(); // what was finished is there to open
  await expect(c.getByRole("progressbar")).toHaveCount(0);
  await expect(c.getByRole("button", { name: "Reuse" })).toBeFocused(); // focus was moved off the vanishing Cancel button

  const next = await generate(page, unique("after the stop"));
  await expect(next.locator(".badge").first()).toHaveText("Done", { timeout: 40_000 });
  const after = ((await (await page.request.get("/api/status")).json()) as { worker: { pid: number } }).worker.pid;
  expect(after, "canceling must not restart the worker, which would reload the model").toBe(pid);
});

/** Make a run look `days` old, behind the server's back (it computes expiry from the stored time). */
function ageRun(runId: string, days: number) {
  const python = process.env.STUDIO_PYTHON ?? resolve("../backend/.venv/bin/python");
  const script = [
    "import sqlite3, sys, datetime as d",
    "t = (d.datetime.now(d.timezone.utc) - d.timedelta(days=float(sys.argv[3]))).isoformat(timespec='milliseconds').replace('+00:00', 'Z')",
    "c = sqlite3.connect(sys.argv[1], timeout=10); c.execute('UPDATE runs SET created_at=? WHERE id=?', (t, sys.argv[2])); c.commit()",
  ].join("\n");
  execFileSync(python, ["-c", script, `${process.env.STUDIO_E2E_DATA_DIR}/studio.sqlite`, runId, String(days)]);
}

test("a run close to expiring says so, and Keep removes the warning and survives a reload", async ({ page }) => {
  await useOptions(page);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("a paper boat");
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(c.locator(".run-expiry")).toHaveCount(0); // brand new: 30 days left, no nagging
  const runId = (await c.getAttribute("data-run-id"))!;

  ageRun(runId, 27); // 3 days left of the default 30
  await page.reload();
  const aged = card(page, prompt);
  await expect(aged.locator(".run-expiry")).toHaveText(/^Will be deleted in [23] days\. Press Keep to save it\.$/);

  const keep = aged.getByRole("button", { name: "Keep" });
  await expect(keep).toHaveAttribute("aria-pressed", "false");
  await keep.click();
  await expect(keep).toHaveAttribute("aria-pressed", "true");
  await expect(aged.locator(".badge-kept")).toHaveText("Kept");
  await expect(aged.locator(".run-expiry")).toHaveCount(0);
  await expect(keep).toBeFocused(); // the button stayed put, so did focus

  await page.reload();
  await expect(card(page, prompt).locator(".badge-kept")).toBeVisible(); // stored on the server, not just on screen
  await expect(card(page, prompt).locator(".run-expiry")).toHaveCount(0);

  // the delete confirmation mentions it, since that is the one way a kept run goes
  await card(page, prompt).getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("alertdialog", { name: "Delete this run?" })).toContainText("You marked it Keep.");
  await page.keyboard.press("Escape");

  await card(page, prompt).getByRole("button", { name: "Keep" }).click(); // un-keep: the warning is back
  await expect(card(page, prompt).locator(".run-expiry")).toBeVisible();
  await expect(card(page, prompt).locator(".badge-kept")).toHaveCount(0);
});
