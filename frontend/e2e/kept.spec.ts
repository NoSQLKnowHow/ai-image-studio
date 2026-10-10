// End-to-end: the Kept view (DESIGN.md §29; criteria 110-120), in a real browser against the real server with the fake pipeline.
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { card, clearHistory, expect, promptBox, test, unique, useOptions, type Locator, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
type ApiRun = { id: string; status: string; pinned: boolean; prompt: string; created_at: string };

const bar = (page: Page): Locator => page.locator("[data-filter-bar]:visible");
const allOption = (page: Page): Locator => bar(page).getByRole("radio", { name: "All" });
const keptOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Kept/ });
const toastWith = (page: Page, text: string | RegExp): Locator => page.locator(".toast", { hasText: text });
const keepButton = (c: Locator): Locator => c.getByRole("button", { name: "Keep" });
const trackCard = (page: Page, text: string): Locator => page.locator("article.music-card", { hasText: text });
const musicTab = (page: Page): Locator => page.getByRole("tab", { name: "Music" });
const imagesTab = (page: Page): Locator => page.getByRole("tab", { name: "Images" });

async function status(page: Page, id: string): Promise<ApiRun> {
  return (await (await page.request.get(`/api/runs/${id}`)).json()) as ApiRun;
}

/** Make a small picture over the API and wait for it. */
async function makeRun(page: Page, prompt: string, extra: Record<string, unknown> = {}): Promise<string> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { prompt, options: { steps: 2, width: 512, height: 512, seed: 3, ...extra } } });
  expect(posted.ok(), await posted.text()).toBe(true);
  const { id } = (await posted.json()) as { id: string };
  await expect.poll(async () => (await status(page, id)).status, { timeout: 30_000 }).toBe("done");
  return id;
}

async function makeTrack(page: Page, prompt: string): Promise<string> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { mode: "music", prompt, options: { duration: 30 } } });
  expect(posted.ok(), await posted.text()).toBe(true);
  const { id } = (await posted.json()) as { id: string };
  await expect.poll(async () => (await status(page, id)).status, { timeout: 45_000 }).toBe("done");
  return id;
}

async function keep(page: Page, id: string, pinned = true): Promise<void> {
  const response = await page.request.patch(`/api/runs/${id}`, { headers: ASK, data: { pinned } });
  expect(response.ok(), await response.text()).toBe(true);
}

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

test("Kept shows only the kept runs, All shows them all, and the choice survives a reload", async ({ page }) => {
  await clearHistory(page);
  const [a, b, c] = [unique("harbour"), unique("mountain"), unique("forest")];
  const ids = [await makeRun(page, a), await makeRun(page, b), await makeRun(page, c)];
  await keep(page, ids[1]);

  await page.goto("/");
  await expect(allOption(page)).toBeChecked();
  for (const prompt of [a, b, c]) await expect(card(page, prompt)).toBeVisible();
  await expect(keptOption(page)).toHaveText("Kept 1");

  await keptOption(page).click();
  await expect(keptOption(page)).toBeChecked();
  await expect(card(page, b)).toBeVisible();
  await expect(card(page, a)).toHaveCount(0);
  await expect(card(page, c)).toHaveCount(0);

  await allOption(page).click();
  for (const prompt of [a, b, c]) await expect(card(page, prompt)).toBeVisible();

  await keptOption(page).click();
  await page.reload();
  await expect(keptOption(page)).toBeChecked();
  await expect(card(page, b)).toBeVisible();
  await expect(card(page, a)).toHaveCount(0);
});

test("the filter bar is a radio group: arrow keys, Home and End move between the options", async ({ page }) => {
  await clearHistory(page);
  await page.goto("/");
  const deleted = bar(page).getByRole("radio", { name: /^Deleted/ });
  await allOption(page).focus();
  await page.keyboard.press("ArrowRight");
  await expect(keptOption(page)).toBeChecked();
  await expect(keptOption(page)).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(deleted).toBeChecked();
  await page.keyboard.press("ArrowRight"); // wraps
  await expect(allOption(page)).toBeChecked();
  await page.keyboard.press("ArrowLeft"); // wraps the other way
  await expect(deleted).toBeChecked();
  await page.keyboard.press("ArrowLeft");
  await expect(keptOption(page)).toBeChecked();
  await page.keyboard.press("Home");
  await expect(allOption(page)).toBeChecked();
  await page.keyboard.press("End");
  await expect(deleted).toBeChecked();
  // only the chosen option is in the tab order
  await expect(allOption(page)).toHaveAttribute("tabindex", "-1");
  await expect(keptOption(page)).toHaveAttribute("tabindex", "-1");
  await expect(deleted).toHaveAttribute("tabindex", "0");
});

test("the choice applies to both tabs, and each tab counts its own kept runs", async ({ page }) => {
  await clearHistory(page);
  const picture = unique("a kept picture");
  const other = unique("an unkept picture");
  const tune = unique("Genre: ambient. kept tune");
  const lost = unique("Genre: techno. unkept tune");
  const ids = [await makeRun(page, picture), await makeRun(page, other), await makeTrack(page, tune), await makeTrack(page, lost)];
  await keep(page, ids[0]);
  await keep(page, ids[2]);
  await page.goto("/");
  await keptOption(page).click();
  await expect(card(page, picture)).toBeVisible();
  await expect(card(page, other)).toHaveCount(0);
  await expect(keptOption(page)).toHaveText("Kept 1");

  await musicTab(page).click();
  await expect(keptOption(page)).toBeChecked(); // the same choice, in the Music tab's own bar
  await expect(trackCard(page, tune)).toBeVisible();
  await expect(trackCard(page, lost)).toHaveCount(0);
  await expect(keptOption(page)).toHaveText("Kept 1");

  await allOption(page).click();
  await expect(trackCard(page, lost)).toBeVisible();
  await imagesTab(page).click();
  await expect(allOption(page)).toBeChecked();
  await expect(card(page, other)).toBeVisible();
});

test("the count follows Keep without a reload", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("count me");
  await makeRun(page, prompt);
  await page.goto("/");
  await expect(keptOption(page)).toHaveText("Kept 0");
  await keepButton(card(page, prompt)).click();
  await expect(keptOption(page)).toHaveText("Kept 1");
  await keepButton(card(page, prompt)).click();
  await expect(keptOption(page)).toHaveText("Kept 0");
  await expect(card(page, prompt)).toBeVisible(); // in All nothing leaves, so nothing is said
  await expect(toastWith(page, "No longer kept")).toHaveCount(0);
});

test("a kept run beyond the newest twenty is reached with Load older runs, and All does not show it until it has paged down that far", async ({ page }) => {
  test.setTimeout(120_000);
  await clearHistory(page);
  const prompts: string[] = [];
  const ids: string[] = [];
  for (let n = 0; n < 22; n += 1) {
    prompts.push(`${unique("paging")} number-${String(n).padStart(2, "0")}-end`);
    ids.push(await makeRun(page, prompts[n]));
  }
  for (const id of ids.slice(0, 21)) await keep(page, id); // all but the newest
  const cards = page.locator("article.run-card");
  const more = page.getByRole("button", { name: "Load older runs" });

  await page.goto("/");
  await expect(cards).toHaveCount(20); // All: the newest twenty
  await keptOption(page).click();
  await expect(cards).toHaveCount(20); // Kept: the newest twenty kept (not the newest run, which is not kept)
  await expect(card(page, prompts[21])).toHaveCount(0);
  await expect(card(page, prompts[1])).toBeVisible();
  await expect(card(page, prompts[0])).toHaveCount(0);

  await more.click();
  await expect(cards).toHaveCount(21);
  await expect(card(page, prompts[0])).toBeVisible();
  await expect(more).toHaveCount(0);

  await allOption(page).click(); // the oldest two are in the cache now, but All has not paged down to them
  await expect(cards).toHaveCount(20);
  await expect(card(page, prompts[0])).toHaveCount(0);
  await expect(card(page, prompts[1])).toHaveCount(0);
  await more.click();
  await expect(cards).toHaveCount(22);
});

test("generating with Kept chosen: the job stays at the top with a note, leaves when done unless kept, and a toast says so", async ({ page }) => {
  await useOptions(page, { steps: 100, numImages: 3 }); // about three seconds of work
  await clearHistory(page);
  const old = unique("an old kept one");
  await keep(page, await makeRun(page, old));
  await page.goto("/");
  await keptOption(page).click();
  await expect(card(page, old)).toBeVisible();

  const prompt = unique("working in the kept view");
  await promptBox(page).fill(prompt);
  await page.keyboard.press("Control+Enter");
  const c = card(page, prompt);
  await expect(c).toBeVisible();
  await expect(keptOption(page)).toBeChecked(); // starting a run does not change the choice
  await expect(page.locator("article.run-card").first()).toContainText(prompt); // at the top
  await expect(c.locator("[data-note=working-in-kept]")).toHaveText("Shown while it works. It stays in this view only if you Keep it.");

  const toast = toastWith(page, "is done. It is not kept, so it is not in this view.");
  await expect(toast).toBeVisible({ timeout: 30_000 });
  await expect(toast).toContainText(prompt);
  await expect(c).toHaveCount(0);
  await expect(card(page, old)).toBeVisible();
  await toast.getByRole("button", { name: "Show all" }).click();
  await expect(allOption(page)).toBeChecked();
  await expect(card(page, prompt)).toBeVisible();
  await expect(toast).toHaveCount(0);
});

test("a job kept while it works stays in the Kept view when it is done, with no toast", async ({ page }) => {
  await useOptions(page, { steps: 100, numImages: 4 }); // about four seconds of work
  await clearHistory(page);
  await page.goto("/");
  await keptOption(page).click();
  const prompt = unique("kept while working");
  await promptBox(page).fill(prompt);
  await page.keyboard.press("Control+Enter");
  const c = card(page, prompt);
  await expect(c.locator("[data-note=working-in-kept]")).toBeVisible();
  await keepButton(c).click(); // Keep is offered on a card that is still working
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "true");
  await expect(c.locator("[data-note=working-in-kept]")).toHaveCount(0, { timeout: 1500 }); // kept: it belongs here now, at once
  await expect(c.locator(".badge").first()).toHaveText("Generating"); // ... while it is still working, not only after it is done
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });
  await expect(c).toBeVisible();
  await expect(toastWith(page, "is not kept")).toHaveCount(0);
  expect((await status(page, (await c.getAttribute("data-run-id"))!)).pinned).toBe(true);
});

test("stopping to keep a run in the Kept view: the card goes, a toast gives the date and Undo, focus moves to the bar, Undo brings it back", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("a paper boat");
  const id = await makeRun(page, prompt);
  await keep(page, id);
  await page.goto("/");
  await keptOption(page).click();
  const c = card(page, prompt);
  await expect(c).toBeVisible();

  await keepButton(c).focus();
  await page.keyboard.press("Enter");
  await expect(c).toHaveCount(0);
  const toast = toastWith(page, "No longer kept");
  await expect(toast).toContainText(`“${prompt}”`);
  await expect(toast).toContainText(/It will be deleted around (\d{1,2} [A-Za-z]{3}|[A-Za-z]{3} \d{1,2}), in (29|30) days, unless you Keep it again\./);
  await expect(keptOption(page)).toBeFocused(); // the card with the focus on it is gone; the bar is where the keyboard continues
  expect((await status(page, id)).pinned).toBe(false);

  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(c).toBeVisible(); // back in its place
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "true");
  await expect(toast).toHaveCount(0);
  expect((await status(page, id)).pinned).toBe(true);
});

test("a run that is past its time says it goes at the next clean-up", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("an old one");
  const id = await makeRun(page, prompt);
  await keep(page, id);
  ageRun(id, 40);
  await page.goto("/");
  await keptOption(page).click();
  await keepButton(card(page, prompt)).click();
  await expect(toastWith(page, "No longer kept")).toContainText("It is past its time, so it will be deleted at the next daily clean-up, unless you Keep it again.");
});

test("Undo for a run that was deleted meanwhile says so", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("deleted under the toast");
  const id = await makeRun(page, prompt);
  await keep(page, id);
  await page.goto("/");
  await keptOption(page).click();
  await keepButton(card(page, prompt)).click();
  const toast = toastWith(page, "No longer kept");
  await expect(toast).toBeVisible();
  expect((await page.request.delete(`/api/runs/${id}`, { headers: ASK })).ok()).toBe(true);
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(toastWith(page, "no longer exists, so it cannot be kept again")).toBeVisible();
});

test("a change made on another open page shows live in the Kept view, without a toast", async ({ page, context }) => {
  await clearHistory(page);
  const prompt = unique("kept elsewhere");
  await makeRun(page, prompt);
  await page.goto("/");
  await keptOption(page).click();
  await expect(card(page, prompt)).toHaveCount(0);

  const other = await context.newPage();
  await other.goto("/");
  await allOption(other).click(); // the two pages share the remembered choice; this one looks at All
  await keepButton(card(other, prompt)).click();
  await expect(card(page, prompt)).toBeVisible(); // it joined the Kept view, in place

  await keepButton(card(other, prompt)).click(); // stop keeping, over there
  await expect(card(page, prompt)).toHaveCount(0);
  await expect(toastWith(page, "No longer kept")).toHaveCount(0); // whoever did it was told; this page was not the one
});

test("nothing kept: the tab says so and how to keep a run, with Show all", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("not kept");
  await makeRun(page, prompt);
  await page.goto("/");
  await keptOption(page).click();
  const empty = page.locator(".empty-state:visible");
  await expect(empty).toContainText("No kept images yet");
  await expect(empty).toContainText("Press Keep on a run to keep it here. Kept runs are never deleted automatically.");
  await musicTab(page).click();
  await expect(page.locator(".empty-state:visible")).toContainText("No kept music yet");
  await imagesTab(page).click();
  await page.locator(".empty-state:visible").getByRole("button", { name: "Show all" }).click();
  await expect(allOption(page)).toBeChecked();
  await expect(card(page, prompt)).toBeVisible();
});

test("a tab whose kept runs are all further down is not told it has none: the next page is read", async ({ page }) => {
  await clearHistory(page);
  const picture = unique("a kept picture far down");
  const pictureId = await makeRun(page, picture);
  const trackId = await makeTrack(page, unique("Genre: ambient. a kept tune"));
  await keep(page, pictureId);
  await keep(page, trackId);
  const real = (await (await page.request.get("/api/runs?kept=true&limit=10")).json()) as { runs: (ApiRun & { mode: string })[] };
  const track = real.runs.find((run) => run.mode === "music")!;
  const image = real.runs.find((run) => run.mode !== "music")!;
  // The first page of the Kept list is twenty tracks (newer than the picture); the picture is on the second.
  const clones = Array.from({ length: 20 }, (_, n) => ({
    ...track, id: (0xa000 + n).toString(16).padStart(32, "0"), created_at: new Date(Date.parse(track.created_at) + (20 - n) * 1000).toISOString(),
  }));
  let reads = 0;
  await page.route("**/api/runs?*kept=true*", async (route) => {
    const url = new URL(route.request().url());
    reads += 1;
    const body = url.searchParams.has("before") ? { runs: [image], next_before: null } : { runs: clones, next_before: clones[clones.length - 1].id };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  await keptOption(page).click();
  await expect(card(page, picture)).toBeVisible({ timeout: 15_000 }); // found without anyone pressing Load older runs
  expect(reads).toBe(2);
  await expect(page.getByText("No kept images yet")).toHaveCount(0);
});

test("when the Kept list cannot be read the page says so and Try again reads it", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("kept but unreachable");
  await keep(page, await makeRun(page, prompt));
  await page.goto("/");
  let fail = true;
  await page.route("**/api/runs?*kept=true*", async (route) => {
    if (fail) await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: "the list is not available just now" }) });
    else await route.continue();
  });
  await keptOption(page).click();
  const problem = page.getByRole("alert").filter({ hasText: "Couldn't load your kept runs" });
  await expect(problem).toBeVisible();
  fail = false;
  await problem.getByRole("button", { name: "Try again" }).click();
  await expect(card(page, prompt)).toBeVisible();
  await expect(problem).toHaveCount(0);
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the filter bar and the toast with Undo fit the screen and Undo can be pressed", async ({ page }) => {
    await clearHistory(page);
    const prompt = unique("phone");
    const id = await makeRun(page, prompt);
    await keep(page, id);
    await page.goto("/");
    const noSideways = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    await expect(bar(page)).toBeVisible();
    const box = (await bar(page).boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(375);
    await noSideways();

    await keptOption(page).click();
    await keepButton(card(page, prompt)).click();
    const undo = toastWith(page, "No longer kept").getByRole("button", { name: "Undo" });
    await expect(undo).toBeVisible();
    const undoBox = (await undo.boundingBox())!;
    expect(undoBox.x).toBeGreaterThanOrEqual(0);
    expect(undoBox.x + undoBox.width).toBeLessThanOrEqual(375);
    await noSideways();
    await undo.click();
    await expect(card(page, prompt)).toBeVisible();
    expect((await status(page, id)).pinned).toBe(true);
  });
});
