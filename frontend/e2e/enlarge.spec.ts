// End-to-end: Enlarge (DESIGN.md §28; criteria 94-104), in a real browser against the real server with the fake pipeline, whose stand-in
// upscaler enlarges with a plain resize and says so in the file. What a real model does to a picture is a Spark question.
import { card, clearHistory, expect, generate, test, unique, useOptions, type Locator, type Page } from "./helpers";
import { BLUE, png } from "./png";

// Pictures the studio can make that Enlarge takes to 3840×2160 cheaply (the fake upscaler resizes them in a second or two).
const NEAR = { aspect: "custom", customWidth: 1920, customHeight: 1088, steps: 2 }; // 2.0x, trimmed from 16:9 by 0.7%
const HALF = { aspect: "custom", customWidth: 1376, customHeight: 768, steps: 2 }; // "50%" of the model's size: 2.8x, too much for Make 4K
const TINY = { aspect: "custom", customWidth: 512, customHeight: 512, steps: 2 }; // 7.5x: neither

type ApiCopy = { width: number; height: number; bytes: number; method: string; url: string; download_url: string };
type ApiRun = { id: string; images: { id: string; can_4k: boolean; can_enlarge: boolean; four_k: ApiCopy | null }[] };
const latest = async (page: Page): Promise<ApiRun> => ((await (await page.request.get("/api/runs?limit=1")).json()) as { runs: ApiRun[] }).runs[0];
const pngSize = (bytes: Buffer): [number, number] => [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];

const enlarge = (c: Locator): Locator => c.getByRole("button", { name: /^(Enlarge|Enlarging)/ });
const make4k = (c: Locator): Locator => c.getByRole("button", { name: /^(Make|Making) 4K/ });
const download4k = (c: Locator): Locator => c.getByRole("link", { name: "Download 4K" });
const viewer = (page: Page): Locator => page.locator(".lightbox");
const CLIENT = { "X-Studio-Client": "1" };

async function done(page: Page, prompt: string): Promise<Locator> {
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  return c;
}

/** Slow the enlargement down, so that what the page shows while it works can be looked at. Counts the requests. */
async function slow(page: Page, ms = 900): Promise<{ posts: () => number }> {
  let posts = 0;
  await page.route("**/api/images/*/enlarge", async (route) => {
    if (route.request().method() === "POST") {
      posts += 1;
      await new Promise((resolve) => setTimeout(resolve, ms));
    }
    await route.continue();
  });
  return { posts: () => posts };
}

test("a picture gets Enlarge beside Make 4K; it enlarges, says so, and leaves only Download 4K", async ({ page }) => {
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("a harbour at dawn"));

  await expect(make4k(c)).toBeVisible();
  const button = enlarge(c);
  await expect(button).toHaveText("Enlarge");
  await expect(button).toHaveAttribute("title", /^Enlarge this picture to 3840×2160 \(trimmed to exactly 16:9\) with an upscaler model: the same picture, sharper than Make 4K, but it takes longer\./);
  await expect(button).not.toHaveAttribute("aria-disabled", "true");
  await button.click();

  await expect(page.getByRole("status").filter({ hasText: /^Enlarged to 3840×2160, \d+\.\d MB\. Use Download 4K\.$/ })).toBeVisible({ timeout: 30_000 });
  await expect(enlarge(c)).toHaveCount(0); // it has the better copy now
  await expect(make4k(c)).toHaveCount(0);
  const link = download4k(c);
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", /\/4k\?method=model&download=1$/);
  await expect(link).toHaveAttribute("title", /^The enlarged 4K copy: 3840×2160 PNG, \d+\.\d MB, made with an upscaler model$/);

  const file = await page.request.get((await link.getAttribute("href"))!);
  expect(file.ok()).toBe(true);
  expect(file.headers()["content-type"]).toBe("image/png");
  expect(file.headers()["content-disposition"]).toMatch(/3840x2160.*\.png/);
  const bytes = await file.body();
  expect(pngSize(bytes)).toEqual([3840, 2160]);
  expect(bytes.toString("latin1")).toContain("upscaler model the fake upscaler"); // made by the upscaler path, not by the plain resize
  expect((await latest(page)).images[0].four_k?.method).toBe("model");
});

test("while it works the button says so, a second click does nothing, and only one request is made", async ({ page }) => {
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("slow to enlarge"));
  const requests = await slow(page);
  const button = enlarge(c);
  await button.click();
  await expect(button).toHaveText("Enlarging…");
  await expect(button).toHaveAttribute("aria-disabled", "true");
  await expect(button).toHaveAttribute("title", /Enlarging with the upscaler model\. It can take a minute or more\./);
  await button.click({ force: true });
  await button.click({ force: true });
  await expect(download4k(c)).toBeVisible({ timeout: 30_000 });
  expect(requests.posts()).toBe(1);
});

test("after a Make 4K copy both Download 4K and Enlarge show, and Enlarge replaces the copy", async ({ page }) => {
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("plain first"));
  await make4k(c).click();
  await expect(download4k(c)).toBeVisible({ timeout: 30_000 });
  await expect(download4k(c)).toHaveAttribute("href", /\/4k\?download=1$/); // the plain copy's address
  await expect(enlarge(c)).toBeVisible();
  await enlarge(c).click();
  await expect(enlarge(c)).toHaveCount(0, { timeout: 30_000 });
  await expect(download4k(c)).toHaveAttribute("href", /\/4k\?method=model&download=1$/); // a new address: nothing cached is reused
  await expect(make4k(c)).toHaveCount(0);
  const run = await latest(page);
  expect(run.images[0].four_k?.method).toBe("model");
});

test("a picture Make 4K cannot do (it needs 2.8×) is enlarged by Enlarge", async ({ page }) => {
  await useOptions(page, HALF);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("half the size"));
  await expect(make4k(c)).toHaveCount(0);
  await expect(enlarge(c)).toHaveAttribute("title", /^Enlarge this picture to 3840×2160 \(trimmed to exactly 16:9\)/);
  await enlarge(c).click();
  const link = download4k(c);
  await expect(link).toBeVisible({ timeout: 30_000 });
  expect(pngSize(await (await page.request.get((await link.getAttribute("href"))!)).body())).toEqual([3840, 2160]);
});

test("a picture that needs more than 4× gets neither button: the server's rule, not the page's", async ({ page }) => {
  await useOptions(page, TINY);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("too small"));
  await expect(enlarge(c)).toHaveCount(0);
  await expect(make4k(c)).toHaveCount(0);
  const image = (await latest(page)).images[0];
  expect([image.can_4k, image.can_enlarge]).toEqual([false, false]);
});

test("the copy survives a reload", async ({ page }) => {
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("still here");
  const c = await done(page, prompt);
  await enlarge(c).click();
  await expect(download4k(c)).toBeVisible({ timeout: 30_000 });
  await page.reload();
  const again = card(page, prompt).first();
  await expect(download4k(again)).toHaveAttribute("href", /method=model/);
  await expect(enlarge(again)).toHaveCount(0);
});

// ------------------------------------------------------------------ the model is not there, and other failures
test("without the model file Enlarge is shown dimmed with the reason, and pressing it says why in words", async ({ page, consoleErrors }) => {
  const reason = "The upscaler model file is not there: /models/upscalers/RealESRGAN_x2plus.pth";
  const hint = "Download it once, on the machine that runs the studio.";
  await page.route("**/api/capabilities", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.upscaler = { available: false, model: "RealESRGAN_x2plus.pth", reason, hint, max_enlargement: 4 };
    await route.fulfill({ response, json: body });
  });
  await page.route("**/api/images/*/enlarge", (route) =>
    route.request().method() === "POST" ? route.fulfill({ status: 503, json: { detail: reason, code: "upscaler_unavailable", hint } }) : route.continue());
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("no model"));
  const button = enlarge(c);
  await expect(button).toHaveText("Enlarge");
  await expect(button).toHaveAttribute("aria-disabled", "true");
  await expect(button).toHaveAttribute("data-unavailable", "true");
  await expect(button).toHaveAttribute("title", `${reason} ${hint}`);
  await expect(make4k(c)).toBeVisible(); // Make 4K needs no model: still there

  await button.click({ force: true }); // dimmed, but pressable (Playwright treats aria-disabled as not enabled): a phone has no tooltips, so pressing it says the same
  await expect(page.getByRole("alert").filter({ hasText: `Couldn't enlarge: ${reason} ${hint}` })).toBeVisible();
  expect(consoleErrors).toEqual([expect.stringMatching(/status of 503/)]); // provoked on purpose: the browser logs a 5xx as an error
  consoleErrors.length = 0;
  await expect(button).toHaveText("Enlarge"); // and nothing was made
  await expect(download4k(c)).toHaveCount(0);
});

test("a refusal or a failure is said in words and the button stays", async ({ page, consoleErrors }) => {
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("refused"));
  const answers = [
    { status: 422, body: { detail: "This picture (10×10) is too small to enlarge to 4K without it looking blurry.", code: "not_enlarge_eligible" } },
    { status: 500, body: { detail: "Out of memory while enlarging.", code: "upscale_failed", hint: "Try again when the image model is not generating." } },
    { status: 404, body: { detail: "Image not found.", code: "not_found" } },
  ];
  await page.route("**/api/images/*/enlarge", (route) => {
    const answer = route.request().method() === "POST" ? answers.shift() : undefined;
    return answer ? route.fulfill({ status: answer.status, json: answer.body }) : route.continue();
  });
  await enlarge(c).click();
  await expect(page.getByRole("alert").filter({ hasText: "Couldn't enlarge: This picture (10×10) is too small" })).toBeVisible();
  await expect(enlarge(c)).toHaveText("Enlarge");
  await expect(enlarge(c)).not.toHaveAttribute("aria-disabled", "true");
  await enlarge(c).click();
  await expect(page.getByRole("alert").filter({ hasText: "Couldn't enlarge: Out of memory while enlarging. Try again when the image model is not generating." })).toBeVisible();
  expect(consoleErrors).toEqual([expect.stringMatching(/status of 500/)]);
  consoleErrors.length = 0;
  await enlarge(c).click();
  await expect(page.getByRole("alert").filter({ hasText: "no longer exists, so there is nothing to enlarge" })).toBeVisible();
  await expect(download4k(c)).toHaveCount(0);
});

// ------------------------------------------------------------------ the viewer, focus, other pages, a phone
test("in the viewer the answer is shown inside the viewer, and keyboard focus lands on Download 4K", async ({ page }) => {
  await useOptions(page, { ...NEAR, numImages: 2 });
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("in the viewer"));
  await c.locator(".thumb").first().click();
  const v = viewer(page);
  await expect(enlarge(v)).toBeVisible();
  await enlarge(v).focus();
  await page.keyboard.press("Enter");
  await expect(v.getByRole("status")).toContainText(/Enlarged to 3840×2160/, { timeout: 30_000 });
  await expect(download4k(v)).toBeFocused();
  await expect(enlarge(v)).toHaveCount(0);
  await page.keyboard.press("ArrowRight"); // the other picture of the run has its own state
  await expect(enlarge(v)).toBeVisible();
  await expect(download4k(v)).toHaveCount(0);
});

test("in the viewer the button says Enlarging… while it works, and a second press does nothing", async ({ page }) => {
  await useOptions(page, { ...NEAR, numImages: 2 });
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("slow in the viewer"));
  await c.locator(".thumb").first().click();
  const v = viewer(page);
  const requests = await slow(page, 800);
  await enlarge(v).click();
  await expect(enlarge(v)).toHaveText("Enlarging…");
  await expect(enlarge(v)).toHaveAttribute("aria-disabled", "true");
  await enlarge(v).click({ force: true });
  await expect(download4k(v)).toBeVisible({ timeout: 30_000 });
  expect(requests.posts()).toBe(1);
});

test("a keyboard user on a card lands on Download 4K, whether or not a plain copy was there before", async ({ page }) => {
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("focus on the card"));
  await enlarge(c).focus();
  await page.keyboard.press("Enter");
  await expect(download4k(c)).toBeFocused({ timeout: 30_000 });

  const d = await done(page, unique("focus after a plain copy"));
  await make4k(d).click();
  await expect(download4k(d)).toBeVisible({ timeout: 30_000 });
  await enlarge(d).focus();
  await page.keyboard.press("Enter");
  await expect(enlarge(d)).toHaveCount(0, { timeout: 30_000 });
  await expect(download4k(d)).toBeFocused(); // the button that was pressed is gone: focus went to the link
});

test("a person who moved on while it was being made keeps their place", async ({ page }) => {
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("moved on"));
  await slow(page, 800);
  await enlarge(c).focus();
  await page.keyboard.press("Enter");
  await expect(enlarge(c)).toHaveText("Enlarging…");
  await expect(enlarge(c)).toBeFocused();
  const keep = c.getByRole("button", { name: "Keep" });
  await keep.focus();
  await expect(download4k(c)).toBeVisible({ timeout: 30_000 });
  await expect(keep).toBeFocused();
});

test("another open page learns of the enlarged copy without reloading", async ({ page, context }) => {
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("two pages");
  const c = await done(page, prompt);
  const other = await context.newPage();
  await other.goto("/");
  const there = card(other, prompt).first();
  await expect(enlarge(there)).toBeVisible();
  await enlarge(c).click();
  await expect(download4k(there)).toHaveAttribute("href", /method=model/, { timeout: 30_000 });
  await expect(enlarge(there)).toHaveCount(0);
});

test("phone width: the card's buttons and the viewer's bar, with Enlarge in them, stay on the screen", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await useOptions(page, NEAR);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("on a phone"));
  const within = async (locator: Locator) => {
    const box = (await locator.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(360);
  };
  await within(enlarge(c));
  await within(make4k(c));
  await make4k(c).click(); // the busiest card: Download 4K and Enlarge side by side
  await expect(download4k(c)).toBeVisible({ timeout: 30_000 });
  await within(download4k(c));
  await within(enlarge(c));
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);

  await c.locator(".thumb").first().click();
  const v = viewer(page);
  await expect(v).toBeVisible();
  for (const button of await v.locator(".lightbox-actions").getByRole("button").all()) await within(button);
  for (const link of await v.locator(".lightbox-actions").getByRole("link").all()) await within(link);
  await expect(enlarge(v)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});

// ------------------------------------------------------------------ an edit's source image
test("an edit's source image can be enlarged in the viewer when Make 4K cannot do it", async ({ page }) => {
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("put the dog on a beach");
  const upload = await page.request.post("/api/uploads", { headers: CLIENT, data: png(1280, 720, BLUE) });
  expect(upload.ok()).toBe(true);
  const { upload_id } = (await upload.json()) as { upload_id: string };
  const run = await page.request.post("/api/runs", {
    headers: CLIENT, data: { mode: "edit", prompt, input_images: [{ upload_id }], options: { steps: 3, seed: 7, width: 1920, height: 1088 } },
  });
  expect(run.ok()).toBe(true);
  const c = card(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await c.getByRole("button", { name: "Open source image 1 of 1" }).click();
  const v = viewer(page);
  await expect(v.locator(".lightbox-title")).toContainText("Source 1 of 1");
  await expect(make4k(v)).toHaveCount(0); // 3× is more than a plain resize goes to
  await expect(enlarge(v)).toHaveAttribute("title", /^Enlarge this picture to 3840×2160 with an upscaler model/);
  await enlarge(v).click();
  await expect(v.getByRole("status")).toContainText("Enlarged to 3840×2160", { timeout: 30_000 });
  const link = download4k(v);
  await expect(link).toBeVisible();
  const file = await page.request.get((await link.getAttribute("href"))!);
  expect(file.headers()["content-disposition"]).toMatch(/source-1_put-dog-beach-[a-z0-9]+_3840x2160_/);
  expect(pngSize(await file.body())).toEqual([3840, 2160]);
});
