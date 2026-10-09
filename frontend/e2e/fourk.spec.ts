// End-to-end: Make 4K (DESIGN.md §27, §27.9; criteria 78-93), in a real browser against the real server with the fake pipeline.
import { readFileSync } from "node:fs";
import { card, clearHistory, expect, generate, test, unique, useOptions, type Locator, type Page } from "./helpers";
import { BLUE, GOLD, png } from "./png";

// A 16:9 picture the studio can make: exactly 16:9, 3.7 MP (inside the 4.5 MP limit), wide enough for Make 4K (1920+).
const WIDE = { aspect: "custom", customWidth: 2560, customHeight: 1440, steps: 2 };

type ApiImage = { id: string; width: number; height: number; can_4k: boolean; four_k: { width: number; height: number; bytes: number; url: string; download_url: string } | null };
type ApiRun = { id: string; status: string; prompt: string; images: ApiImage[] };
const runs = async (page: Page): Promise<ApiRun[]> => ((await (await page.request.get("/api/runs?limit=20")).json()) as { runs: ApiRun[] }).runs;

/** Width and height from a PNG's header (IHDR). */
const pngSize = (bytes: Buffer): [number, number] => [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];

const make4k = (c: Locator): Locator => c.getByRole("button", { name: /^(Make|Making) 4K/ });
const download4k = (c: Locator): Locator => c.getByRole("link", { name: "Download 4K" });
const viewer = (page: Page): Locator => page.locator(".lightbox");

async function done(page: Page, prompt: string): Promise<Locator> {
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  return c;
}

test("a 16:9 picture gets Make 4K on its card, which makes a 3840×2160 copy and turns into Download 4K", async ({ page }) => {
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("a harbour at dawn");
  const c = await done(page, prompt);

  const button = make4k(c);
  await expect(button).toHaveText("Make 4K");
  // 2560×1440 is exactly 16:9: nothing to trim, and the tooltip says what it will make
  await expect(button).toHaveAttribute("title", "Make a 3840×2160 copy of this picture with a standard resize. It makes the picture bigger, not sharper: no detail is added.");
  await expect(download4k(c)).toHaveCount(0);
  await button.click();

  await expect(page.getByRole("status").filter({ hasText: "The 4K copy is ready: 3840×2160" })).toBeVisible();
  await expect(make4k(c)).toHaveCount(0);
  const link = download4k(c);
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", /\/4k\?download=1$/);
  await expect(link).toHaveAttribute("title", /^The 4K copy: 3840×2160 PNG, \d+\.\d MB$/);
  await expect(c.getByRole("link", { name: "Download", exact: true })).toBeVisible(); // the original is still there

  const file = await page.request.get((await link.getAttribute("href"))!);
  expect(file.ok()).toBe(true);
  expect(file.headers()["content-type"]).toBe("image/png");
  expect(file.headers()["content-disposition"]).toMatch(/3840x2160.*\.png/);
  expect(pngSize(await file.body())).toEqual([3840, 2160]);
  const original = (await runs(page))[0].images[0];
  expect([original.width, original.height]).toEqual([2560, 1440]); // the picture itself did not change
});

test("the model's own 16:9 size is trimmed to exactly the frame, and the tooltip says so", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 2752, customHeight: 1536, steps: 2 });
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("the model's own 16:9"));
  await expect(make4k(c)).toHaveAttribute("title", /^Make a 3840×2160 copy of this picture, trimmed to exactly 16:9, with a standard resize\./);
  await make4k(c).click();
  const link = download4k(c);
  await expect(link).toBeVisible();
  expect(pngSize(await (await page.request.get((await link.getAttribute("href"))!)).body())).toEqual([3840, 2160]);
});

test("pictures that would need more than a doubling get no Make 4K", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 512, customHeight: 512, steps: 2 });
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("a small square one"));
  await expect(make4k(c)).toHaveCount(0);
  await expect(download4k(c)).toHaveCount(0);
});

test("a square picture, your default shape, gets Make 4K at 3840×3840: nothing is cut off", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 2048, customHeight: 2048, steps: 2 });
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("a square harbour"));
  await expect(make4k(c)).toHaveAttribute("title", "Make a 3840×3840 copy of this picture with a standard resize. It makes the picture bigger, not sharper: no detail is added.");
  await make4k(c).click();
  await expect(page.getByRole("status").filter({ hasText: "The 4K copy is ready: 3840×3840" })).toBeVisible();
  const link = download4k(c);
  await expect(link).toHaveAttribute("title", /^The 4K copy: 3840×3840 PNG, /);
  const file = await page.request.get((await link.getAttribute("href"))!);
  expect(file.headers()["content-disposition"]).toMatch(/3840x3840/);
  expect(pngSize(await file.body())).toEqual([3840, 3840]);
});

test("a small 16:9 draft gets no Make 4K either: the server's rule, not the page's", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 1536, customHeight: 864, steps: 2 });
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("small and wide"));
  await expect(c.locator(".run-meta")).toContainText("1536×864");
  await expect(make4k(c)).toHaveCount(0);
});

test("the copy survives a reload", async ({ page }) => {
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("still here after a reload");
  await make4k(await done(page, prompt)).click();
  await expect(download4k(card(page, prompt))).toBeVisible();
  await page.reload();
  await expect(download4k(card(page, prompt))).toBeVisible();
  await expect(make4k(card(page, prompt))).toHaveCount(0);
});

test("a run with several pictures makes 4K in the viewer, one picture at a time, with the answer inside the viewer", async ({ page }) => {
  await useOptions(page, { ...WIDE, numImages: 2 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("two harbours");
  const c = await done(page, prompt);
  await expect(make4k(c)).toHaveCount(0); // not on a card with several pictures: its Download… opens the viewer
  await c.locator(".thumb").first().click();

  const v = viewer(page);
  await expect(v).toBeVisible();
  await expect(v.getByRole("button", { name: "Upscale" })).toHaveCount(0);
  await make4k(v).click();
  await expect(v.getByRole("status")).toContainText("The 4K copy is ready: 3840×2160"); // inside the viewer, not a toast behind it
  await expect(download4k(v)).toBeVisible();
  await expect(make4k(v)).toHaveCount(0);

  await page.keyboard.press("ArrowRight"); // the next picture has none yet
  await expect(make4k(v)).toHaveText("Make 4K");
  await expect(download4k(v)).toHaveCount(0);
  await page.keyboard.press("ArrowLeft");
  await expect(download4k(v)).toBeVisible();

  const [newest] = await runs(page);
  expect(newest.images.map((image) => image.four_k !== null)).toEqual([true, false]);
});

test("while it is being made the button says so and a second click does nothing", async ({ page }) => {
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("slow to enlarge");
  const c = await done(page, prompt);
  let posts = 0;
  await page.route("**/api/images/*/4k", async (route) => {
    if (route.request().method() === "POST") {
      posts += 1;
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
    await route.continue();
  });
  const button = make4k(c);
  await button.click();
  await expect(button).toHaveText("Making 4K…");
  await expect(button).toHaveAttribute("aria-disabled", "true");
  await button.click({ force: true });
  await button.click({ force: true });
  await expect(download4k(c)).toBeVisible({ timeout: 15_000 });
  expect(posts).toBe(1);
});

test("a refusal or a failure is said in words and the button stays", async ({ page, consoleErrors }) => {
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("refused");
  const c = await done(page, prompt);
  const answers = [
    { status: 422, body: { detail: "This picture is too small to enlarge to 4K without it looking blurry.", code: "not_4k_eligible" } },
    { status: 507, body: { detail: "The 4K picture could not be saved: No space left on device", code: "storage_full" } },
    { status: 404, body: { detail: "Image not found.", code: "not_found" } },
  ];
  await page.route("**/api/images/*/4k", (route) => {
    const answer = route.request().method() === "POST" ? answers.shift() : undefined;
    return answer ? route.fulfill({ status: answer.status, json: answer.body }) : route.continue();
  });
  await make4k(c).click();
  await expect(page.getByRole("alert").filter({ hasText: "Couldn't make 4K: This picture is too small" })).toBeVisible();
  await expect(make4k(c)).toHaveText("Make 4K");
  await expect(make4k(c)).not.toHaveAttribute("aria-disabled", "true");
  await make4k(c).click();
  await expect(page.getByRole("alert").filter({ hasText: "No space left on device" })).toBeVisible();
  // The browser logs a 5xx as a console error, which the harness would fail the test for. This one was provoked on purpose:
  // check it is the only one, and take it off the list the harness checks at the end.
  expect(consoleErrors).toEqual([expect.stringMatching(/status of 507/)]);
  consoleErrors.length = 0;
  await make4k(c).click();
  await expect(page.getByRole("alert").filter({ hasText: "no longer exists" })).toBeVisible();
  await expect(download4k(c)).toHaveCount(0);
});

test("a failure from the viewer is shown inside the viewer", async ({ page }) => {
  await useOptions(page, { ...WIDE, numImages: 2 });
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("refused in the viewer"));
  await page.route("**/api/images/*/4k", (route) =>
    route.request().method() === "POST" ? route.fulfill({ status: 422, json: { detail: "Make 4K is for 16:9 pictures; this one is 10×10.", code: "not_4k_eligible" } }) : route.continue());
  await c.locator(".thumb").first().click();
  await make4k(viewer(page)).click();
  await expect(viewer(page).getByRole("status")).toContainText("Couldn't make 4K: Make 4K is for 16:9 pictures");
  await expect(viewer(page).locator(".lightbox-notice.error")).toBeVisible();
});

test("another open page learns of the copy without reloading", async ({ page, context }) => {
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("seen from two pages");
  const here = await done(page, prompt);
  const other = await context.newPage();
  await other.goto("/");
  await expect(make4k(card(other, prompt))).toBeVisible();
  await make4k(here).click();
  await expect(download4k(card(other, prompt))).toBeVisible({ timeout: 10_000 }); // by the run.updated event
  await other.close();
});

test("deleting the run removes its 4K copy", async ({ page }) => {
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("gone with its run");
  await make4k(await done(page, prompt)).click();
  const link = download4k(card(page, prompt));
  await expect(link).toBeVisible();
  const url = (await link.getAttribute("href"))!.replace("?download=1", "");
  expect((await page.request.get(url)).status()).toBe(200);
  await card(page, prompt).getByRole("button", { name: "Delete" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
  await expect(card(page, prompt)).toHaveCount(0);
  expect((await page.request.get(url)).status()).toBe(404);
});

test("phone width: the card's buttons and the viewer's bar, with Make 4K in them, stay on the screen", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("on a phone");
  const c = await done(page, prompt);
  await make4k(c).click();
  await expect(download4k(c)).toBeVisible();
  const within = async (locator: Locator) => {
    const box = (await locator.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(360);
  };
  await within(download4k(c));

  await c.locator(".thumb").first().click();
  const v = viewer(page);
  await expect(v).toBeVisible();
  for (const button of await v.locator(".lightbox-actions").getByRole("button").all()) await within(button);
  for (const link of await v.locator(".lightbox-actions").getByRole("link").all()) await within(link);
  await expect(download4k(v)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});

// ------------------------------------------------------------------ keyboard focus: the button is replaced by a link
test("a keyboard user who pressed Make 4K on a card lands on Download 4K", async ({ page }) => {
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("focus on the card"));
  await make4k(c).focus();
  await page.keyboard.press("Enter");
  await expect(download4k(c)).toBeFocused();
});

test("and in the viewer, where the page behind is out of reach", async ({ page }) => {
  await useOptions(page, { ...WIDE, numImages: 2 });
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("focus in the viewer"));
  await c.locator(".thumb").first().click();
  await make4k(viewer(page)).focus();
  await page.keyboard.press("Enter");
  await expect(download4k(viewer(page))).toBeFocused();
});

test("a person who moved on while it was being made keeps their place", async ({ page }) => {
  await useOptions(page, WIDE);
  await page.goto("/");
  await clearHistory(page);
  const c = await done(page, unique("moved on"));
  await page.route("**/api/images/*/4k", async (route) => {
    if (route.request().method() === "POST") await new Promise((resolve) => setTimeout(resolve, 700));
    await route.continue();
  });
  await make4k(c).focus();
  await page.keyboard.press("Enter");
  await expect(make4k(c)).toHaveText("Making 4K…");
  await expect(make4k(c)).toBeFocused(); // still the same button while it works: focus is kept
  const keep = c.getByRole("button", { name: "Keep" });
  await keep.focus();
  await expect(download4k(c)).toBeVisible({ timeout: 10_000 });
  await expect(keep).toBeFocused();
});

// ------------------------------------------------------------------ an edit's source images (DESIGN.md §27.9)
const CLIENT = { "X-Studio-Client": "1" };

/** An edit run made through the API from one picture of the given size, so the test is about the viewer, not about editing. */
async function editRun(page: Page, prompt: string, size: [number, number]): Promise<void> {
  const upload = await page.request.post("/api/uploads", { headers: CLIENT, data: png(size[0], size[1], BLUE) });
  expect(upload.ok()).toBe(true);
  const { upload_id } = (await upload.json()) as { upload_id: string };
  const run = await page.request.post("/api/runs", {
    headers: CLIENT, data: { mode: "edit", prompt, input_images: [{ upload_id }], options: { steps: 3, seed: 7, width: 2560, height: 1440 } },
  });
  expect(run.ok()).toBe(true);
}

test("an edit's source image can be made 4K in the viewer, and downloaded", async ({ page }) => {
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("put the dog on a beach");
  await editRun(page, prompt, [2048, 2048]);
  const c = card(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await c.getByRole("button", { name: "Open source image 1 of 1" }).click();
  const v = viewer(page);
  await expect(v).toBeVisible();
  await expect(v.locator(".lightbox-title")).toContainText("Source 1 of 1");
  await expect(make4k(v)).toHaveAttribute("title", /^Make a 3840×3840 copy of this picture/);
  await make4k(v).click();
  await expect(v.getByRole("status")).toContainText("The 4K copy is ready: 3840×3840");
  const link = download4k(v);
  await expect(link).toBeVisible();
  const file = await page.request.get((await link.getAttribute("href"))!);
  expect(file.headers()["content-disposition"]).toMatch(/source-1_put-dog-beach-[a-z0-9]+_3840x3840_/);
  expect(pngSize(await file.body())).toEqual([3840, 3840]);
  await page.keyboard.press("ArrowRight"); // the result is its own picture: no copy yet
  await expect(v.locator(".lightbox-title")).toContainText("Result 1 of 1");
  await expect(download4k(v)).toHaveCount(0);
  await page.keyboard.press("ArrowLeft");
  await expect(download4k(v)).toBeVisible(); // and the source keeps its copy
});

test("a small source image has no Make 4K", async ({ page }) => {
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("from a tiny picture");
  await editRun(page, prompt, [200, 200]);
  const c = card(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await c.getByRole("button", { name: "Open source image 1 of 1" }).click();
  await expect(viewer(page)).toBeVisible();
  await expect(make4k(viewer(page))).toHaveCount(0);
});

// ------------------------------------------------------------------ a picture from the computer (DESIGN.md §27.9)
const upscaleButton = (page: Page): Locator => page.getByRole("button", { name: /^(Upscale a picture…|Upscaling…)/ });
const fileInput = (page: Page): Locator => page.getByTestId("upscale-file-input");
const pic = (name: string, width: number, height: number) => ({ name, mimeType: "image/png", buffer: png(width, height, GOLD) });

test("Upscale a picture… takes a file from the computer and downloads the 4K PNG, adding nothing to the history", async ({ page }) => {
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("a run that is already here");
  await generate(page, prompt);
  await expect(card(page, prompt).locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const cards = await page.locator("article.run-card").count();

  await expect(upscaleButton(page)).toHaveText("Upscale a picture…");
  await expect(upscaleButton(page)).toHaveAttribute("title", /Nothing is added to your history.*bigger, not sharper/);
  await expect(fileInput(page)).toHaveAttribute("accept", "image/png,image/jpeg,image/webp");
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), upscaleButton(page).click()]); // the real click path
  expect(chooser.isMultiple()).toBe(false);
  const downloading = page.waitForEvent("download");
  await chooser.setFiles(pic("my holiday photo.png", 1920, 1080));
  const download = await downloading;

  expect(download.suggestedFilename()).toMatch(/^upscale_my-holiday-photo_3840x2160_\d{8}-\d{6}\.png$/);
  const path = await download.path();
  expect(pngSize(readFileSync(path))).toEqual([3840, 2160]);
  await expect(page.getByRole("status").filter({ hasText: "Upscaled my holiday photo.png to 3840×2160" })).toBeVisible();
  await expect(upscaleButton(page)).toHaveText("Upscale a picture…"); // ready for the next one
  expect(await page.locator("article.run-card").count()).toBe(cards); // no card: nothing was added
  expect(((await (await page.request.get("/api/runs?limit=50")).json()) as { runs: unknown[] }).runs).toHaveLength(cards);
});

test("a square picture from the computer comes back 3840×3840", async ({ page }) => {
  await page.goto("/");
  const downloading = page.waitForEvent("download");
  await fileInput(page).setInputFiles(pic("square.png", 2048, 2048));
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^upscale_square_3840x3840_/);
  expect(pngSize(readFileSync(await download.path()))).toEqual([3840, 3840]);
});

test("a picture that cannot be upscaled says why, downloads nothing and leaves the button ready", async ({ page }) => {
  await page.goto("/");
  let downloads = 0;
  page.on("download", () => (downloads += 1));
  await fileInput(page).setInputFiles(pic("tiny.png", 256, 256));
  await expect(page.getByRole("alert").filter({ hasText: "Couldn't upscale tiny.png: This picture (256×256) is too small" })).toBeVisible();
  await expect(upscaleButton(page)).toHaveText("Upscale a picture…");
  await fileInput(page).setInputFiles(pic("already.png", 3840, 2160));
  await expect(page.getByRole("alert").filter({ hasText: "already 4K or bigger" })).toBeVisible();
  expect(downloads).toBe(0);
});

test("a file that is not a picture is refused in words", async ({ page }) => {
  await page.goto("/");
  await fileInput(page).setInputFiles({ name: "notes.png", mimeType: "image/png", buffer: Buffer.from("these are notes, not a picture") });
  await expect(page.getByRole("alert").filter({ hasText: "Couldn't upscale notes.png: That isn't a PNG, JPEG or WebP image." })).toBeVisible();
});

test("while it works the button says so and a second file is ignored", async ({ page }) => {
  await page.goto("/");
  let posts = 0;
  await page.route("**/api/upscale*", async (route) => {
    posts += 1;
    await new Promise((resolve) => setTimeout(resolve, 900));
    await route.continue();
  });
  const downloading = page.waitForEvent("download");
  await fileInput(page).setInputFiles(pic("first.png", 1920, 1080));
  await expect(upscaleButton(page)).toHaveText("Upscaling…");
  await expect(upscaleButton(page)).toHaveAttribute("aria-disabled", "true");
  await fileInput(page).setInputFiles(pic("second.png", 1920, 1080)); // while the first is still being made
  await downloading;
  await expect(upscaleButton(page)).toHaveText("Upscale a picture…");
  expect(posts).toBe(1);
});

test("phone width: the button fits and there is no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/");
  const box = (await upscaleButton(page).boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(360);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});
