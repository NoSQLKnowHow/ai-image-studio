// End-to-end: the built UI against the real backend (fake pipeline). See playwright.config.ts.
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { card, clearHistory, expect, generate, promptBox, test, unique, useOptions, type Locator, type Page } from "./helpers";

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
  await expect(pill).toHaveText(/^(Model not loaded|Loading (image|music) model…|(Image|Music) model ready|Generating|Making music)/);
  await pill.click();
  const details = page.getByRole("region", { name: "Model details" });
  await expect(details).toContainText(/Test pipeline \(fake (images|music), no GPU\)/); // whichever model an earlier test left loaded
  await page.keyboard.press("Escape");
  await expect(details).toBeHidden();
  await expect(page.getByRole("radio", { name: /Edit/ })).toBeEnabled(); // the fake pipeline can edit (M5b)
});

test("the title and the tab show the version the server is running", async ({ page }) => {
  const { version } = (await (await page.request.get("/api/health")).json()) as { version: string };
  expect(version).toMatch(/^\d+\.\d+/);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "AI Image Studio" })).toHaveText(`AI Image Studio v${version}`);
  await expect(page).toHaveTitle(`AI Image Studio v${version}`);
  // index.html is revalidated on every load, so a rebuilt page can't hide behind the browser's cache
  const html = await page.request.get("/");
  expect(html.headers()["cache-control"]).toBe("no-cache");
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
  // the title and its version are never overlapped by the status pill (the pill gives way, with an ellipsis)
  await expect(page.getByRole("heading", { name: /^AI Image Studio v\d/ })).toBeVisible();
  const gap = await page.evaluate(() => {
    const title = document.querySelector(".brand h1")!.getBoundingClientRect(); // the text, not its (shrinkable) container
    const pill = document.querySelector(".pill-wrap")!.getBoundingClientRect();
    return pill.left - title.right;
  });
  expect(gap).toBeGreaterThanOrEqual(0);
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

const scaleRadio = (page: Page, percent: number): Locator => page.getByRole("radio", { name: `${percent}%`, exact: true });
const optionsSummary = (page: Page): Locator => page.getByRole("button", { name: /^Options/ });

test("the scale picker changes the size that is sent, shows it, and is remembered", async ({ page }) => {
  // No useOptions here: it would re-apply its settings on the reload below and hide whether the scale is remembered.
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await clearHistory(page);
  await expect(scaleRadio(page, 100)).toHaveAttribute("aria-checked", "true");
  await expect(optionsSummary(page)).toContainText("2048×2048 · 40 steps"); // the page's own defaults: 1:1 preset, 40 steps

  await scaleRadio(page, 25).click();
  await expect(optionsSummary(page)).toContainText("512×512 (25%) · 40 steps");
  const c = await generate(page, unique("a tiny lighthouse"));
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(c.locator(".run-meta")).toContainText("512×512");

  await page.reload();
  await expect(scaleRadio(page, 25)).toHaveAttribute("aria-checked", "true"); // remembered with the other options
  await expect(optionsSummary(page)).toContainText("512×512 (25%)");

  await scaleRadio(page, 50).click(); // and 50% of 2048 is 1024
  await expect(optionsSummary(page)).toContainText("1024×1024 (50%)");
});

test("a scale that would make the image too small is not offered", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, scale: 25 }); // FAST's custom 512 x 512
  await page.goto("/");
  await expect(scaleRadio(page, 25)).toBeDisabled();
  await expect(scaleRadio(page, 25)).toHaveAttribute("title", /would be 128×128; each side must be at least 256/);
  await expect(scaleRadio(page, 50)).toBeEnabled(); // exactly 256
  await expect(scaleRadio(page, 50)).toHaveAttribute("aria-checked", "true"); // the nearest larger one stands in for the saved 25%
  await expect(optionsSummary(page)).toContainText("256×256 (50%)");
});

test("Draft makes one small, quick run that is marked as a draft, and Reuse on it keeps your own settings", async ({ page }) => {
  await useOptions(page, { aspect: "16:9", steps: 40, numImages: 4, scale: 100 });
  await page.goto("/");
  await clearHistory(page);
  const draftButton = page.getByRole("button", { name: "Draft", exact: true });
  await expect(draftButton).toBeDisabled(); // nothing to try yet
  const prompt = unique("a lighthouse draft");
  await promptBox(page).fill(prompt);
  await draftButton.click();
  const c = card(page, prompt).first();
  await expect(c.locator(".badge-draft")).toHaveText("Draft");
  await expect(c.locator(".badge").nth(1)).toBeVisible();
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(c.locator(".run-meta")).toContainText("512×288 · 12 steps"); // the 16:9 shape, long side 512, at most 12 steps
  await expect(c.locator(".thumb")).toHaveCount(1); // one image, though 4 per click were chosen
  const { runs } = (await (await page.request.get("/api/runs?limit=5")).json()) as { runs: { options: Record<string, unknown>; prompt: string }[] };
  expect(runs.find((r) => r.prompt === prompt)!.options).toMatchObject({ draft: true, num_images: 1, steps: 12, width: 512, height: 288 });

  await c.getByRole("button", { name: "Reuse" }).click(); // brings the prompt back, not the draft's size and steps
  await expect(promptBox(page)).toHaveValue(prompt);
  await expect(optionsSummary(page)).toContainText("2752×1536 · 40 steps");
  await expect(optionsSummary(page)).toContainText("4 images");
  await expect(page.getByRole("status").filter({ hasText: "Your size, steps and seed are unchanged" })).toBeVisible();

  await page.getByRole("button", { name: "Generate", exact: true }).click(); // and Generate makes the full-size one
  const full = card(page, prompt).first();
  await expect(full.locator(".badge-draft")).toHaveCount(0);
  await expect(full.locator(".run-meta")).toContainText("2752×1536");
});

test("Ctrl+Shift+Enter makes a draft and Ctrl+Enter generates", async ({ page }) => {
  await useOptions(page, { aspect: "1:1", steps: 3 });
  await page.goto("/");
  await clearHistory(page);
  const quick = unique("shortcut draft");
  await promptBox(page).fill(quick);
  await page.keyboard.press("Control+Shift+Enter");
  await expect(card(page, quick).first().locator(".badge-draft")).toBeVisible();
  const normal = unique("shortcut full");
  await promptBox(page).fill(normal);
  await page.keyboard.press("Control+Enter");
  await expect(card(page, normal).first()).toBeVisible();
  await expect(card(page, normal).first().locator(".badge-draft")).toHaveCount(0);
});

test("a draft goes ahead of waiting full-size runs", async ({ page }) => {
  await useOptions(page, { steps: 100, numImages: 4 }); // each full run ~4 s: time for a queue to form
  await page.goto("/");
  await clearHistory(page);
  const first = await generate(page, unique("running now"));
  await expect(first.locator(".badge").first()).toHaveText("Generating", { timeout: 15_000 });
  const second = await generate(page, unique("waits"));
  await expect(second.locator(".badge").first()).toHaveText("Queued · #1");
  const quick = unique("quick try");
  await promptBox(page).fill(quick);
  await page.getByRole("button", { name: "Draft", exact: true }).click();
  const draft = card(page, quick).first();
  await expect(draft.locator(".badge").first()).toHaveText("Queued · #1"); // ahead of the full-size run ...
  await expect(second.locator(".badge").first()).toHaveText("Queued · #2"); // ... which moves down one place
  await expect(draft.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });
  await expect(second.locator(".badge").first()).not.toHaveText("Done"); // the draft finished first
  // tidy up so the server can stop promptly
  await second.getByRole("button", { name: "Cancel" }).click().catch(() => undefined);
});

test("every image has a thumbnail to download, on its card and in the viewer", async ({ page }) => {
  await useOptions(page, { numImages: 2, steps: 3 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("two thumbnails");
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(c.getByRole("link", { name: "Thumbnail" })).toHaveCount(0); // two images: the viewer lets you choose
  await c.getByRole("button", { name: /Open image 2 of 2/ }).click();
  const link = page.getByRole("dialog").getByRole("link", { name: "Thumbnail" });
  const href = (await link.getAttribute("href"))!;
  expect(href).toMatch(/^\/api\/images\/[0-9a-f]{32}\/thumb\?download=1$/);
  const response = await page.request.get(href);
  expect(response.headers()["content-type"]).toBe("image/webp");
  expect(response.headers()["content-disposition"]).toMatch(/filename="generate_two-thumbnails.*_thumb\.webp"/);
  await page.keyboard.press("Escape");

  const single = await generate(page, unique("one thumbnail"));
  await expect(single.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await useOptions(page, { numImages: 1, steps: 3 });
  await page.reload();
  const one = await generate(page, unique("single image"));
  await expect(one.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const cardLink = one.getByRole("link", { name: "Thumbnail" });
  await expect(cardLink).toHaveAttribute("href", /thumb\?download=1$/);
  await expect(cardLink).toHaveAttribute("download", "");
});

test("phone width: the scale buttons and the size they give fit, and Draft and Generate sit together", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await useOptions(page, { aspect: "1:1", scale: 50 });
  await page.goto("/");
  for (const percent of [100, 75, 50, 25]) await expect(scaleRadio(page, percent)).toBeVisible();
  await expect(page.locator(".scale-size")).toBeVisible(); // (toHaveText alone passes on a hidden element)
  await expect(page.locator(".scale-size")).toHaveText("1024×1024"); // the Options summary is hidden on a phone
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  const draft = await page.getByRole("button", { name: "Draft", exact: true }).boundingBox();
  const generateBox = await page.getByRole("button", { name: "Generate", exact: true }).boundingBox();
  expect(Math.abs(draft!.y - generateBox!.y)).toBeLessThan(4); // one row
  expect(generateBox!.x + generateBox!.width).toBeLessThanOrEqual(360);
});

// ------------------------------------------------------------------ regenerate larger (DESIGN.md §23)
type ApiRun = { id: string; status: string; options: Record<string, unknown> };
const newestRuns = async (page: Page): Promise<ApiRun[]> =>
  ((await (await page.request.get("/api/runs?limit=10")).json()) as { runs: ApiRun[] }).runs;
const regenerateButton = (c: Locator): Locator => c.getByRole("button", { name: /^Regenerate larger/ });

test("a run made at 50% offers Regenerate larger, which queues the same prompt and seed at the full size", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 1024, customHeight: 1024, steps: 8, scale: 50, seedLocked: true, seed: 424242 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("a lighthouse at half size");
  await generate(page, prompt);
  const small = card(page, prompt).last(); // once the bigger one is queued it is first; this stays the older card
  await expect(small.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(small.locator(".run-meta")).toContainText("512×512 · 8 steps");

  const again = small.getByRole("button", { name: "Regenerate larger: 1024×1024, 8 steps" });
  await expect(again).toBeVisible();
  await expect(again).toHaveText("Regenerate larger");
  await expect(again).toHaveAttribute("title", /different picture/); // honest about what it will make
  await expect(small.getByRole("button", { name: "Upscale" })).toHaveCount(0); // the 1.4 placeholder is gone (DESIGN.md §27.7)

  await again.click();
  await expect(page.getByRole("status").filter({ hasText: "Queued at 1024×1024" })).toBeVisible();
  await expect(card(page, prompt)).toHaveCount(2);
  const big = card(page, prompt).first();
  await expect(big.locator(".run-meta")).toContainText("1024×1024 · 8 steps");
  await expect(big.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(regenerateButton(big)).toHaveCount(0); // it is the full size: nothing bigger to go back to
  await expect(big.getByRole("button", { name: "Upscale" })).toHaveCount(0);
  await expect(regenerateButton(small)).toBeVisible(); // the small one keeps its button

  const [newest, older] = await newestRuns(page);
  expect(newest.options).toMatchObject({ seed: 424242, width: 1024, height: 1024, steps: 8, num_images: 1, draft: false, full: null });
  expect(older.options.full).toEqual({ width: 1024, height: 1024, steps: 8 });
});

test("a draft offers Regenerate larger, back to the size and steps that were selected, with the seed it picked", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 1024, customHeight: 576, steps: 20, numImages: 3, seedLocked: false });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("a quick lighthouse");
  await promptBox(page).fill(prompt);
  await page.getByRole("button", { name: "Draft", exact: true }).click();
  const draft = card(page, prompt).last();
  await expect(draft.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(draft.locator(".run-meta")).toContainText("512×288 · 12 steps");

  await regenerateButton(draft).click(); // named for what it will do: 1024×576 at the 20 steps chosen, not the draft's 12
  await expect(card(page, prompt)).toHaveCount(2);
  const big = card(page, prompt).first();
  await expect(big.locator(".run-meta")).toContainText("1024×576 · 20 steps");
  await expect(big.locator(".badge-draft")).toHaveCount(0);
  await expect(big.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(big.locator(".thumb")).toHaveCount(1); // the draft's one image, not the three that were selected

  const [newest, older] = await newestRuns(page);
  expect(older.options).toMatchObject({ draft: true, seed_was_random: true, full: { width: 1024, height: 576, steps: 20 } });
  expect(newest.options).toMatchObject({ seed: older.options.seed, seed_was_random: false, draft: false, full: null, num_images: 1 });
});

test("no Regenerate larger on a run at full size, a failed one, or one that doesn't remember a bigger size", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 4, scale: 100 });
  await page.goto("/");
  await clearHistory(page);
  const post = (prompt: string, options: Record<string, unknown>) =>
    page.request.post("/api/runs", { headers: { "X-Studio-Client": "1" }, data: { prompt, options: { steps: 3, ...options } } });

  const plain = unique("full size");
  await generate(page, plain);
  const failing = unique("fails [fake:error]");
  expect((await post(failing, { width: 256, height: 256, full: { width: 512, height: 512, steps: 30 } })).ok()).toBe(true);
  const older = unique("made by an older client");
  expect((await post(older, { width: 256, height: 256 })).ok()).toBe(true); // small, but says nothing about a full size
  const withOne = unique("small with a record");
  expect((await post(withOne, { width: 256, height: 256, full: { width: 512, height: 512, steps: 30 } })).ok()).toBe(true);

  await expect(card(page, withOne).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(regenerateButton(card(page, withOne))).toBeVisible(); // the same cards do have it when they should
  for (const text of [plain, older]) {
    await expect(card(page, text).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
    await expect(regenerateButton(card(page, text))).toHaveCount(0);
    await expect(card(page, text).getByRole("button", { name: "Upscale" })).toHaveCount(0);
  }
  await expect(card(page, failing).locator(".badge").first()).toHaveText("Failed", { timeout: 20_000 });
  await expect(card(page, failing).getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(regenerateButton(card(page, failing))).toHaveCount(0);
});

test("when the server refuses or the queue is full, Regenerate larger says so and the card stays as it was", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 4, scale: 50 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("refused");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });

  const answers = [
    { status: 422, body: { detail: [{ loc: ["body", "options", "full"], msg: "The full size must be larger than the run.", type: "value_error" }] } },
    { status: 429, body: { detail: "The queue is full (3 jobs waiting). Try again in a moment.", code: "queue_full" } },
  ];
  await page.route("**/api/runs", (route) => {
    const answer = route.request().method() === "POST" ? answers.shift() : undefined;
    return answer ? route.fulfill({ status: answer.status, json: answer.body }) : route.continue();
  });
  await regenerateButton(card(page, prompt)).click();
  await expect(page.getByRole("alert").filter({ hasText: "Couldn't regenerate: The full size must be larger than the run." })).toBeVisible();
  await regenerateButton(card(page, prompt)).click();
  const fullQueue = page.getByRole("alert").filter({ hasText: "The queue is full (3 jobs waiting)" });
  await expect(fullQueue).toBeVisible();
  await expect(fullQueue).not.toContainText("Couldn't regenerate"); // the server's own words, as for Generate
  await expect(card(page, prompt)).toHaveCount(1); // nothing was queued
  await expect(regenerateButton(card(page, prompt))).toBeEnabled();
});

test("phone width: the buttons wrap inside the card, with no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 4, scale: 50 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("phone");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const regenerate = regenerateButton(card(page, prompt));
  const download = card(page, prompt).getByRole("link", { name: "Download", exact: true });
  await expect(regenerate).toBeVisible();
  await expect(download).toBeVisible();
  for (const button of [regenerate, download]) {
    const box = (await button.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(360);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});

test("a double click on Regenerate larger queues one run, not two", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 4, scale: 50 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("double click");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  let posts = 0;
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() === "POST") {
      posts += 1;
      await new Promise((resolve) => setTimeout(resolve, 400)); // the second click lands while the first is still being answered
    }
    await route.continue();
  });
  await regenerateButton(card(page, prompt)).dblclick();
  await expect(card(page, prompt)).toHaveCount(2);
  await page.waitForTimeout(600);
  expect(posts).toBe(1);
  await expect(card(page, prompt)).toHaveCount(2);
});

// ------------------------------------------------------------------ regenerate larger in the viewer (DESIGN.md §24)
const viewerButton = (page: Page): Locator => page.getByRole("dialog").getByRole("button", { name: /^Regenerate larger/ });
const viewerNote = (page: Page): Locator => page.getByRole("dialog").getByRole("status");
const THREE_AT_HALF = { aspect: "custom", customWidth: 1024, customHeight: 1024, steps: 8, scale: 50, seedLocked: true, seed: 1000, numImages: 3 };

test("the viewer's Regenerate larger enlarges the image being viewed: one image, with its own seed", async ({ page }) => {
  await useOptions(page, THREE_AT_HALF);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("three lighthouses");
  await generate(page, prompt);
  const small = card(page, prompt).last();
  await expect(small.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await small.getByRole("button", { name: /Open image 1 of 3/ }).click();
  await page.keyboard.press("ArrowRight");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Image 2 of 3 · seed 1001");
  await expect(viewerButton(page)).toHaveAccessibleName("Regenerate larger: 1024×1024, 8 steps");
  await expect(viewerButton(page)).toHaveAttribute("title", /different picture/);

  await viewerButton(page).click();
  await expect(viewerNote(page)).toContainText("Queued this image at 1024×1024");
  await expect(viewerNote(page).locator(".lightbox-notice")).not.toHaveClass(/error/);
  await expect(page.locator(".toast")).toHaveCount(0); // the answer is in the viewer, not a toast behind it
  await expect(dialog).toBeVisible(); // and the viewer stays open, for the next image
  await page.keyboard.press("ArrowRight");
  await expect(dialog).toContainText("Image 3 of 3");
  await expect(dialog.locator(".lightbox-notice")).toHaveCount(0); // the note belonged to image 2
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  await expect(card(page, prompt)).toHaveCount(2);
  const big = card(page, prompt).first();
  await expect(big.locator(".run-meta")).toContainText("1024×1024 · 8 steps");
  await expect(big.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(big.locator(".thumb")).toHaveCount(1); // image 2 only, not all three
  const [newest, older] = await newestRuns(page);
  expect(newest.options).toMatchObject({ seed: 1001, num_images: 1, width: 1024, height: 1024, steps: 8, draft: false, full: null });
  expect(older.options).toMatchObject({ seed: 1000, num_images: 3 });
});

test("the viewer has no Regenerate larger for a run that has none on its card", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 4, scale: 100 });
  await page.goto("/");
  await clearHistory(page);
  const plain = unique("full size in the viewer");
  await generate(page, plain);
  const older = unique("older client in the viewer");
  expect((await page.request.post("/api/runs", { headers: { "X-Studio-Client": "1" }, data: { prompt: older, options: { width: 256, height: 256, steps: 3 } } })).ok()).toBe(true);
  for (const text of [plain, older]) {
    await expect(card(page, text).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
    await card(page, text).locator(".thumb").first().click();
    await expect(page.getByRole("dialog").getByRole("link", { name: "Download" })).toBeVisible();
    await expect(viewerButton(page)).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
  }
});

test("when the server refuses or the queue is full, the viewer says so inside itself and queues nothing", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 4, scale: 50 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("refused in the viewer");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const answers = [
    { status: 422, body: { detail: [{ loc: ["body", "options", "full"], msg: "The full size must be larger than the run.", type: "value_error" }] } },
    { status: 429, body: { detail: "The queue is full (3 jobs waiting). Try again in a moment.", code: "queue_full" } },
  ];
  await page.route("**/api/runs", (route) => {
    const answer = route.request().method() === "POST" ? answers.shift() : undefined;
    return answer ? route.fulfill({ status: answer.status, json: answer.body }) : route.continue();
  });
  await card(page, prompt).locator(".thumb").first().click();
  await viewerButton(page).click();
  await expect(viewerNote(page)).toContainText("Couldn't regenerate: The full size must be larger than the run.");
  await expect(viewerNote(page).locator(".lightbox-notice")).toHaveClass(/error/);
  await viewerButton(page).click();
  await expect(viewerNote(page)).toContainText("The queue is full (3 jobs waiting)");
  await expect(viewerNote(page)).not.toContainText("Couldn't regenerate");
  await expect(page.locator(".toast")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(card(page, prompt)).toHaveCount(1);
});

test("a double click on the viewer's Regenerate larger queues one run, not two", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 4, scale: 50 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("double click in the viewer");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await card(page, prompt).locator(".thumb").first().click();
  let posts = 0;
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() === "POST") {
      posts += 1;
      await new Promise((resolve) => setTimeout(resolve, 400)); // the second click lands while the first is still being answered
    }
    await route.continue();
  });
  await viewerButton(page).dblclick();
  await expect(viewerNote(page)).toContainText("Queued this image");
  await page.waitForTimeout(600);
  expect(posts).toBe(1);
  await page.keyboard.press("Escape");
  await expect(card(page, prompt)).toHaveCount(2);
});

test("phone width: the viewer's buttons and its note stay on screen, and the image still fits", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await useOptions(page, { ...THREE_AT_HALF, aspect: "9:16" });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("phone viewer");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await card(page, prompt).getByRole("button", { name: /Open image 1 of 3/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(viewerButton(page)).toBeVisible();
  await viewerButton(page).click();
  await expect(viewerNote(page)).toContainText("Queued this image");
  const onScreen = async (locator: Locator) => {
    const box = (await locator.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(360);
    expect(box.y + box.height).toBeLessThanOrEqual(740);
  };
  for (const control of [viewerButton(page), dialog.getByRole("link", { name: "Download" }), dialog.getByRole("link", { name: "Thumbnail" }),
    dialog.getByRole("button", { name: "Close" }), dialog.locator(".lightbox-notice"), dialog.locator(".lightbox-stage img")]) await onScreen(control);
  const bar = (await dialog.locator(".lightbox-bar").boundingBox())!;
  const image = (await dialog.locator(".lightbox-stage img").boundingBox())!;
  expect(image.y).toBeGreaterThanOrEqual(bar.y + bar.height - 1); // the wrapped bar does not cover the picture
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});

test("enlarging two images in a row queues both, each with its own seed", async ({ page }) => {
  await useOptions(page, THREE_AT_HALF);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("two of three");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await card(page, prompt).getByRole("button", { name: /Open image 1 of 3/ }).click();
  let posts = 0;
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() === "POST") {
      posts += 1;
      await new Promise((resolve) => setTimeout(resolve, 400)); // the first is still being answered when the second is pressed
    }
    await route.continue();
  });
  await viewerButton(page).click();
  await page.keyboard.press("ArrowRight");
  await viewerButton(page).click();
  await expect(viewerNote(page)).toContainText("Queued this image");
  await expect(async () => expect(posts).toBe(2)).toPass();
  await page.keyboard.press("Escape");
  await expect(card(page, prompt)).toHaveCount(3);
  const [second, first] = await newestRuns(page);
  expect([first.options.seed, second.options.seed]).toEqual([1000, 1001]);
  expect([first.options.num_images, second.options.num_images]).toEqual([1, 1]);
});

test("if the viewer is closed before the answer comes, the answer is a toast instead of being lost", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 4, scale: 50 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("closed too soon");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await card(page, prompt).locator(".thumb").first().click();
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() === "POST") await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });
  await viewerButton(page).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.locator(".toast")).toContainText("Queued this image at 512×512");
  await expect(card(page, prompt)).toHaveCount(2);
});
