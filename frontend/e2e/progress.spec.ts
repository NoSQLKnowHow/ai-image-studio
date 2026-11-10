// How far a model's load has got (DESIGN.md §39, criteria 300 to 305), in a real browser against the real server. This spec runs against a server of its own (see
// playwright.config.ts, the project "progress"): its fake pipelines pretend to download 400 MB into a cache folder while they load, over 3 seconds, and the worker says every
// 150 ms how far it has got. Everything between the worker's folder-watching and the words on the screen is the real thing; what is pretended is only that the files
// come from the network.
import { clearHistory, expect, test, unique, useOptions, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
const pill = (page: Page) => page.locator('button[aria-controls="model-details"]');
const panel = (page: Page) => page.getByRole("region", { name: "Model details" });
const SIZE = /\d+(\.\d+)? (MB|GB) of about 400 MB/; // what has arrived, of what is expected (detail line)

/** Unload over the API, waiting out a run that an earlier test left going: every test starts from a model that is not in memory, so that its first use loads it. */
async function unloaded(page: Page) {
  await expect.poll(async () => (await page.request.post("/api/model/unload", { headers: ASK })).status(), { timeout: 30_000 }).toBe(200);
}

const workerNow = async (page: Page) => ((await (await page.request.get("/api/status")).json()) as { worker: { state: string; model: string } }).worker;

async function postImage(page: Page, prompt: string): Promise<string> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { prompt, options: { steps: 2, width: 512, height: 512, seed: 3 } } });
  expect(posted.status()).toBe(201);
  return ((await posted.json()) as { id: string }).id;
}

/** Every headline of a load that the page shows, in the order it shows them. The last half of a load is only half a second long, and Playwright looks every second once it
 *  has looked for a while, so a state that short is not watched for: the page is told to write it down, and the test reads what was written. Call it before the load starts. */
async function watchHeadlines(page: Page) {
  await page.evaluate(() => {
    const seen: string[] = [];
    (window as unknown as { __headlines: string[] }).__headlines = seen;
    const note = () => {
      for (const el of document.querySelectorAll(".load-progress-headline")) {
        const text = el.textContent ?? "";
        if (text && seen[seen.length - 1] !== text) seen.push(text);
      }
    };
    new MutationObserver(note).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
}
const headlines = (page: Page) => page.evaluate(() => (window as unknown as { __headlines: string[] }).__headlines);

/** What was written down says the download came first and loading into memory last (the load can also say "into memory" at the very start, before the first byte arrives). */
function expectDownloadThenMemory(seen: string[], model: string) {
  const downloading = seen.indexOf(`Downloading ${model}`);
  expect(downloading, `"Downloading ${model}" among ${JSON.stringify(seen)}`).toBeGreaterThanOrEqual(0);
  expect(seen.lastIndexOf(`Loading ${model} into memory`), `"Loading ${model} into memory" after it, among ${JSON.stringify(seen)}`).toBeGreaterThan(downloading);
}

const done = (card: ReturnType<Page["locator"]>) => expect(card.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await useOptions(page);
});

test("an image waits for the model, and the card, the pill and the panel say how far the download has got", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  await watchHeadlines(page);
  await postImage(page, unique("a lighthouse"));
  const card = page.locator("article.run-card").first();

  // downloading: what, how much of what, as a bar that fills
  await expect(card.getByText("Downloading the image model")).toBeVisible();
  await expect(card.getByText(SIZE)).toBeVisible();
  const bar = card.getByRole("progressbar", { name: "Downloading the image model" });
  await expect(bar).toBeVisible();
  await expect(bar).toHaveAttribute("aria-valuenow", /^\d+$/);
  await expect.poll(() => bar.locator("span").evaluate((el) => el.getBoundingClientRect().width), { intervals: [50] }).toBeGreaterThan(0); // painted: the real CSS reads the width
  await expect(card.getByText(/the first load takes a while/)).toHaveCount(0); // not the line that never moved

  // the short form is in the pill, and the whole view in the panel it opens
  await expect(pill(page)).toContainText(/Loading image model…\s*· \d+(\.\d+)? (MB|GB) of 400 MB/);
  await pill(page).click();
  await expect(panel(page).getByText("Downloading the image model")).toBeVisible();
  await expect(panel(page).getByRole("progressbar")).toBeVisible();

  // then, once the files are all there, into memory (written down by the page, below), and then the run's own progress, and nothing of the load is left on the screen
  await done(card);
  expectDownloadThenMemory(await headlines(page), "the image model");
  await expect(page.getByText(/Downloading the image model|into memory/)).toHaveCount(0);
  await expect(pill(page)).not.toContainText(/MB of/);
  await expect(pill(page)).toContainText("Image model ready");
});

test("a track waits for the music model, and says which model was unloaded to make room", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  await watchHeadlines(page);
  await postImage(page, unique("a lighthouse")); // the image model is the one in memory...
  await expect.poll(async () => (await workerNow(page)).model + ":" + (await workerNow(page)).state, { timeout: 30_000 }).toBe("image:ready");

  const genre = unique("ambient");
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { mode: "music", prompt: genre, options: { duration: 30 } } }); // ...and the music model needs its room
  expect(posted.status()).toBe(201);
  await page.getByRole("tab", { name: "Music" }).click();
  const card = page.locator("article.music-card", { hasText: genre }).first();
  await expect(card.getByText("Downloading the music model")).toBeVisible();
  await expect(card.getByText(SIZE)).toBeVisible();
  await expect(card.getByText("The image model was unloaded to make room.")).toBeVisible();
  await done(card);
  expectDownloadThenMemory(await headlines(page), "the music model");
  await expect(page.getByText(/unloaded to make room|Downloading the music model/)).toHaveCount(0);
});

test("a drawing waits for the model it draws with, and the card says how far the download has got", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  const idea = unique("a paper boat");
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { mode: "vector", prompt: idea, options: { colours: 2, width_mm: 40, height_mm: 40, seed: 7 } } });
  expect(posted.status()).toBe(201);
  await page.getByRole("tab", { name: "Vector" }).click();
  const card = page.locator("article.vector-card", { hasText: idea }).first();
  await expect(card.getByText("Downloading the image model")).toBeVisible();
  await expect(card.getByText(SIZE)).toBeVisible();
  await done(card);
  await expect(page.getByText(/Downloading the image model/)).toHaveCount(0);
});

test("Design a voice says how far the speech model has got while the three samples wait for it", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "Speech" }).click();
  await expect(page.getByRole("heading", { name: "Voice", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Design a voice" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Design a voice" })).toBeVisible();
  await expect(dialog.getByText(/Downloading Fake speech/)).toHaveCount(0); // nothing is waited for yet
  await dialog.getByLabel("Describe the voice").fill("a warm Irish woman in her forties, unhurried and clear");
  await dialog.getByRole("button", { name: "Make three samples" }).click();

  await expect(dialog.getByText(/Making three samples…/)).toBeVisible();
  // the engine's name is the server's (capabilities.speech.engines), and the kind of model is its variant word: the page writes neither
  await expect(dialog.getByText("Downloading Fake speech for designing a voice", { exact: true })).toBeVisible(); // not the screen reader's sentence, which has a full stop
  await expect(dialog.getByText(SIZE)).toBeVisible();
  await expect(page.locator("header")).toContainText(/Loading speech model…\s*· \d+(\.\d+)? (MB|GB) of 400 MB/);

  await expect(dialog.getByRole("radio", { name: "Sample 3" })).toBeVisible({ timeout: 30_000 });
  await expect(dialog.getByText(/Downloading Fake speech|into memory/)).toHaveCount(0);
});

test("on a phone the card and the pill's panel do not push the page sideways while a model loads", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await unloaded(page);
  await page.goto("/");
  await postImage(page, unique("a lighthouse"));
  const card = page.locator("article.run-card").first();
  await expect(card.getByText(SIZE)).toBeVisible();
  await pill(page).click();
  await expect(panel(page).getByText("Downloading the image model")).toBeVisible();
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(await overflow()).toBeLessThanOrEqual(0);
  await done(card);
});
