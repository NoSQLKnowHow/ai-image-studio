// Load model and Unload model beside the pill (DESIGN.md §25, acceptance 51-60), in a real browser against the real
// server with the fake pipeline. The server is shared by every test, so each one first puts the model in the state it
// needs by asking the API.
import { clearHistory, expect, generate, test, unique, useOptions, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
const pill = (page: Page) => page.locator('button[aria-controls="model-details"]');
const load = (page: Page) => page.getByRole("button", { name: "Load model", exact: true });
const unloadButton = (page: Page) => page.getByRole("button", { name: "Unload model", exact: true });
const announcement = (page: Page) => page.locator("header p[role=status]");

/** Unload over the API, waiting out a run that an earlier test left going. */
async function unloaded(page: Page) {
  await expect.poll(async () => (await page.request.post("/api/model/unload", { headers: ASK })).status(), { timeout: 30_000 }).toBe(200);
}

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await useOptions(page);
});

test("Load model loads the model without a run, and Unload model gives it back", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  await expect(pill(page)).toHaveText(/^Model not loaded/);
  await expect(unloadButton(page)).toHaveCount(0); // nothing to unload
  await expect(load(page)).toHaveAttribute("title", /ready when you press Generate/);

  await load(page).click();
  await expect(pill(page)).toHaveText(/^Loading image model…/);
  await expect(pill(page)).toBeFocused(); // the button went away: focus is not lost with it
  await expect(announcement(page)).toHaveText("Loading the image model…");
  await expect(load(page)).toHaveCount(0); // and neither button is offered while it loads
  await expect(unloadButton(page)).toHaveCount(0);

  await expect(pill(page)).toHaveText(/^Image model ready\s*· unloads in/);
  await expect(announcement(page)).toHaveText("Image model ready.");
  await expect(unloadButton(page)).toBeVisible();
  await expect(load(page)).toHaveCount(0);
  await expect(page.locator("article.run-card")).toHaveCount(0); // no run was made

  // Unload waits for the worker to stop; slow it down to see what the page does meanwhile
  await page.route("**/api/model/unload", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    await route.continue();
  });
  await unloadButton(page).click();
  await expect(announcement(page)).toHaveText("Unloading the image model…");
  await expect(unloadButton(page)).toBeDisabled(); // not twice while it is on its way
  await expect(pill(page)).toHaveText(/^Model not loaded/);
  await expect(pill(page)).toBeFocused();
  await expect(announcement(page)).toHaveText("Image model unloaded.");
  await expect(load(page)).toBeVisible();
});

test("only what you asked for is announced: a later load that a run causes is not", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  await load(page).click();
  await expect(announcement(page)).toHaveText("Image model ready.");
  expect((await page.request.post("/api/model/unload", { headers: ASK })).status()).toBe(200); // not through the page
  await expect(pill(page)).toHaveText(/^Model not loaded/);
  const c = await generate(page, unique("a lantern on a hill")); // this run loads the model again
  await expect(pill(page)).toHaveText(/^Loading image model…/);
  await page.waitForTimeout(400); // long enough for a stale request to have spoken
  expect(await announcement(page).textContent()).toBe("Image model ready."); // still what it said before (read once: a retry could wait out a stale "Loading")
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
});

test("a run sent while the model loads waits for it and then runs", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  await load(page).click();
  await expect(pill(page)).toHaveText(/^Loading image model…/);
  const prompt = unique("a fox in a snowy wood");
  const c = await generate(page, prompt); // sent while the load is going on
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(pill(page)).toHaveText(/^Image model ready/);
});

test("a refused request is reported, and Load model stays so it can be tried again", async ({ page }) => {
  await unloaded(page);
  await page.route("**/api/model/load", (route) =>
    route.fulfill({ status: 409, contentType: "application/json",
      body: JSON.stringify({ detail: "Not enough free memory to load the model: 12.5 GB available, 40 GB required (STUDIO_MIN_FREE_GB).", code: "not_enough_memory" }) }));
  await page.goto("/");
  await load(page).click();
  await expect(page.getByText(/Couldn't load the model: Not enough free memory/)).toBeVisible();
  await expect(load(page)).toBeEnabled();
  await expect(announcement(page)).toHaveText(""); // nothing was started, so nothing is announced as loading
  await page.unroute("**/api/model/load");
  await load(page).click(); // the same button works once the cause is gone
  await expect(pill(page)).toHaveText(/^Image model ready/);
});

test("the buttons are never doubled: pressing Load twice quickly loads once", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  const posts: string[] = [];
  page.on("request", (r) => r.method() === "POST" && r.url().endsWith("/api/model/load") && posts.push(r.url()));
  await load(page).dblclick();
  await expect(pill(page)).toHaveText(/^Image model ready/);
  expect(posts.length).toBe(1);
});

test("phone width: the header still fits, with the button as an icon that keeps its name", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await unloaded(page);
  await page.goto("/");
  const button = load(page);
  await expect(button).toBeVisible();
  await expect(button.locator(".model-action-label")).toBeHidden(); // icon only on a phone
  // the pill says its state in short words that fit whole: the button took some of its room
  await expect(pill(page)).toHaveText("Unloaded");
  expect(await pill(page).locator(".pill-label").evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  const box = (await button.boundingBox())!;
  expect(box.x + box.width).toBeLessThanOrEqual(360);
  const gap = await page.evaluate(() => {
    const title = document.querySelector(".brand h1")!.getBoundingClientRect();
    const right = document.querySelector(".header-right")!.getBoundingClientRect();
    return right.left - title.right;
  });
  expect(gap).toBeGreaterThanOrEqual(0); // the title is not overlapped; the pill gives way
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  await button.click();
  await expect(pill(page)).toHaveText(/^Ready/); // the countdown beside it is hidden on a phone
  expect(await pill(page).locator(".pill-label").evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  await expect(unloadButton(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});

test("after Unload model the next run loads the model again", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  await load(page).click();
  await expect(unloadButton(page)).toBeVisible();
  await unloadButton(page).click();
  await expect(pill(page)).toHaveText(/^Model not loaded/);
  const c = await generate(page, unique("a quiet harbour"));
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(pill(page)).toHaveText(/^Image model ready/);
});
