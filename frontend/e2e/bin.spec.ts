// End-to-end: the bin, a Deleted view that keeps a deleted run for 30 days (DESIGN.md §30; criteria 121-133), in a real browser against the
// real server with the fake pipeline.
import { card, clearHistory, expect, test, unique, useOptions, type Locator, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
type ApiRun = { id: string; status: string; pinned: boolean; prompt: string; deleted_at: string | null; purge_at: string | null; images: { url: string }[] };

const bar = (page: Page): Locator => page.locator("[data-filter-bar]:visible");
const allOption = (page: Page): Locator => bar(page).getByRole("radio", { name: "All" });
const keptOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Kept/ });
const deletedOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Deleted/ });
const emptyBinButton = (page: Page): Locator => bar(page).getByRole("button", { name: "Empty bin" });
const toastWith = (page: Page, text: string | RegExp): Locator => page.locator(".toast", { hasText: text });
const dialog = (page: Page, name: string | RegExp): Locator => page.getByRole("alertdialog", { name });
const musicTab = (page: Page): Locator => page.getByRole("tab", { name: "Music" });
const imagesTab = (page: Page): Locator => page.getByRole("tab", { name: "Images" });
const trackCard = (page: Page, text: string): Locator => page.locator("article.music-card", { hasText: text });
const viewer = (page: Page): Locator => page.locator(".lightbox");

async function status(page: Page, id: string): Promise<ApiRun> {
  return (await (await page.request.get(`/api/runs/${id}`)).json()) as ApiRun;
}

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

async function bin(page: Page, id: string): Promise<void> {
  const response = await page.request.post(`/api/runs/${id}/bin`, { headers: ASK });
  expect(response.ok(), await response.text()).toBe(true);
}

async function keep(page: Page, id: string): Promise<void> {
  expect((await page.request.patch(`/api/runs/${id}`, { headers: ASK, data: { pinned: true } })).ok()).toBe(true);
}

/** Press Delete on a card and confirm the question. */
async function deleteViaPage(page: Page, c: Locator, confirmName = "Delete"): Promise<void> {
  await c.getByRole("button", { name: "Delete" }).click();
  await dialog(page, /^Delete this run/).getByRole("button", { name: confirmName, exact: true }).click();
}

test("Delete moves a run to the bin: the card goes with a toast and Undo, and the run waits in Deleted with its files", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("a harbour at dawn");
  const id = await makeRun(page, prompt);
  await page.goto("/");
  const c = card(page, prompt);
  await c.getByRole("button", { name: "Delete" }).click();
  const question = dialog(page, "Delete this run?");
  await expect(question).toContainText("It moves to Deleted and stays there for 30 days; you can restore it from there. After that it is gone for good.");
  await question.getByRole("button", { name: "Delete", exact: true }).click();

  await expect(c).toHaveCount(0);
  await expect(toastWith(page, `Deleted “${prompt}”. It stays in Deleted for 30 days.`)).toBeVisible();
  await expect(deletedOption(page)).toHaveText("Deleted 1");
  const run = await status(page, id);
  expect(run.deleted_at).not.toBeNull();
  expect((await page.request.get(run.images[0].url)).status()).toBe(200); // its files are still there

  await deletedOption(page).click();
  await expect(deletedOption(page)).toBeChecked();
  await expect(c).toBeVisible();
  await expect(c.locator(".badge-deleted")).toHaveText("Deleted");
  await expect(c.locator("[data-note=in-bin]")).toContainText(/^In the bin since .*\. It will be deleted for good around .*, in (29|30) days\.$/);
  for (const name of ["Restore", "Reuse", "Copy prompt", "Delete forever"]) await expect(c.getByRole("button", { name })).toBeVisible();
  for (const name of ["Keep", "Retry", "Edit this", "Make 4K", "Enlarge", "Regenerate larger"]) await expect(c.getByRole("button", { name })).toHaveCount(0);
  await allOption(page).click();
  await expect(c).toHaveCount(0);
  await expect(emptyBinButton(page)).toHaveCount(0); // Empty bin is for the Deleted view only
});

test("Undo on the toast restores the run to where it was", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("undo the delete");
  const id = await makeRun(page, prompt);
  await page.goto("/");
  await deleteViaPage(page, card(page, prompt));
  const toast = toastWith(page, "It stays in Deleted for 30 days.");
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(card(page, prompt)).toBeVisible();
  await expect(toastWith(page, /^Restored .*It has a fresh (29|30) days\./)).toBeVisible();
  await expect(deletedOption(page)).toHaveText("Deleted 0");
  expect((await status(page, id)).deleted_at).toBeNull();
});

test("Restore on a card in the bin: the card leaves Deleted, a kept run is still kept, focus lands on the filter bar", async ({ page }) => {
  await clearHistory(page);
  const plain = unique("restore me");
  const kept = unique("restore me, kept");
  const plainId = await makeRun(page, plain);
  const keptId = await makeRun(page, kept);
  await keep(page, keptId);
  await bin(page, plainId);
  await bin(page, keptId);
  await page.goto("/");
  await deletedOption(page).click();
  await expect(card(page, plain)).toBeVisible();
  await expect(card(page, kept)).toBeVisible();

  await card(page, kept).getByRole("button", { name: "Restore" }).click();
  await expect(card(page, kept)).toHaveCount(0);
  await expect(toastWith(page, `Restored “${kept}”. It is still kept.`)).toBeVisible();
  expect(await status(page, keptId)).toMatchObject({ pinned: true, deleted_at: null });

  const button = card(page, plain).getByRole("button", { name: "Restore" });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(card(page, plain)).toHaveCount(0);
  await expect(toastWith(page, /^Restored .*It has a fresh (29|30) days\./)).toBeVisible();
  await expect(deletedOption(page)).toBeFocused(); // the card with the focus on it is gone; the bar is where the keyboard continues
  await keptOption(page).click();
  await expect(card(page, kept)).toBeVisible();
  await expect(card(page, plain)).toHaveCount(0); // not kept
});

test("Delete forever asks first, and then the run and its files are gone for good", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("for good");
  const id = await makeRun(page, prompt);
  const url = (await status(page, id)).images[0].url;
  await bin(page, id);
  await page.goto("/");
  await deletedOption(page).click();
  const c = card(page, prompt);
  await c.getByRole("button", { name: "Delete forever" }).click();
  const question = dialog(page, "Delete this run for good?");
  await expect(question).toContainText("This can't be undone.");
  await question.getByRole("button", { name: "Cancel" }).click();
  await expect(c).toBeVisible(); // asked, and said no

  await c.getByRole("button", { name: "Delete forever" }).click();
  await dialog(page, "Delete this run for good?").getByRole("button", { name: "Delete forever" }).click();
  await expect(c).toHaveCount(0);
  expect((await page.request.get(`/api/runs/${id}`)).status()).toBe(404);
  expect((await page.request.get(url)).status()).toBe(404);
  await expect(deletedOption(page)).toHaveText("Deleted 0");
});

test("Empty bin asks how many, then deletes everything in the bin on both tabs and nothing else", async ({ page }) => {
  await clearHistory(page);
  const stays = unique("stays in the history");
  const gone = [unique("bin one"), unique("bin two")];
  const tune = unique("Genre: ambient. bin tune");
  const ids = [await makeRun(page, gone[0]), await makeRun(page, gone[1]), await makeTrack(page, tune)];
  const stayId = await makeRun(page, stays);
  for (const id of ids) await bin(page, id);
  await page.goto("/");
  await deletedOption(page).click();
  await expect(deletedOption(page)).toHaveText("Deleted 2");
  await expect(emptyBinButton(page)).toBeEnabled();

  await emptyBinButton(page).click();
  const question = dialog(page, "Empty the bin?");
  await expect(question).toContainText("Delete 3 runs for good (2 on Images, 1 on Music), with their files. This can't be undone.");
  await question.getByRole("button", { name: "Cancel" }).click();
  await expect(card(page, gone[0])).toBeVisible();

  await emptyBinButton(page).click();
  await dialog(page, "Empty the bin?").getByRole("button", { name: "Empty bin" }).click();
  await expect(toastWith(page, "Emptied the bin: 3 runs deleted for good.")).toBeVisible();
  for (const id of ids) expect((await page.request.get(`/api/runs/${id}`)).status()).toBe(404);
  expect((await status(page, stayId)).deleted_at).toBeNull();
  await expect(page.locator(".empty-state:visible")).toContainText("No deleted images");
  await expect(emptyBinButton(page)).toBeDisabled();
  await musicTab(page).click();
  await expect(page.locator(".empty-state:visible")).toContainText("No deleted music");
  await allOption(page).click();
  await imagesTab(page).click();
  await expect(card(page, stays)).toBeVisible();
});

test("Deleted applies to both tabs, each with its own count, and is remembered", async ({ page }) => {
  await clearHistory(page);
  const picture = unique("a deleted picture");
  const tune = unique("Genre: ambient. a deleted tune");
  await bin(page, await makeRun(page, picture));
  await bin(page, await makeTrack(page, tune));
  await bin(page, await makeTrack(page, unique("Genre: techno. another deleted tune")));
  await page.goto("/");
  await expect(deletedOption(page)).toHaveText("Deleted 1");
  await deletedOption(page).click();
  await expect(card(page, picture)).toBeVisible();
  await musicTab(page).click();
  await expect(deletedOption(page)).toBeChecked();
  await expect(deletedOption(page)).toHaveText("Deleted 2");
  await expect(trackCard(page, tune).locator(".badge-deleted")).toBeVisible();
  await expect(trackCard(page, tune).getByRole("button", { name: "Restore" })).toBeVisible();
  await expect(trackCard(page, tune).getByRole("button", { name: "Keep" })).toHaveCount(0);
  await page.reload();
  await expect(deletedOption(page)).toBeChecked();
});

test("a deleted track can be restored and deleted for good from the Music tab", async ({ page }) => {
  await clearHistory(page);
  const tune = unique("Genre: ambient. delete this tune");
  const id = await makeTrack(page, tune);
  await page.goto("/");
  await musicTab(page).click();
  await trackCard(page, tune).getByRole("button", { name: "Delete" }).click();
  await dialog(page, "Delete this run?").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(trackCard(page, tune)).toHaveCount(0);
  expect((await status(page, id)).deleted_at).not.toBeNull();
  await deletedOption(page).click();
  await trackCard(page, tune).getByRole("button", { name: "Restore" }).click();
  await expect(trackCard(page, tune)).toHaveCount(0);
  expect((await status(page, id)).deleted_at).toBeNull();
});

test("Empty bin is on whenever the bin holds anything on either tab, not only on the tab you are looking at", async ({ page }) => {
  await clearHistory(page);
  const tune = unique("Genre: ambient. only the bin has music");
  await bin(page, await makeTrack(page, tune));
  await page.goto("/");
  await deletedOption(page).click();
  await expect(deletedOption(page)).toHaveText("Deleted 0"); // nothing deleted on Images ...
  await expect(emptyBinButton(page)).toBeEnabled(); // ... but the bin is not empty
  await emptyBinButton(page).click();
  await expect(dialog(page, "Empty the bin?")).toContainText("Delete 1 run for good (1 on Music), with their files.");
  await dialog(page, "Empty the bin?").getByRole("button", { name: "Empty bin" }).click();
  await expect(toastWith(page, "Emptied the bin: 1 run deleted for good.")).toBeVisible();
});

test("the viewer on a picture in the bin lets you look and download, and offers nothing that changes it", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 1920, customHeight: 1088, steps: 2 }); // a size Make 4K takes
  await clearHistory(page);
  const prompt = unique("looked at in the bin");
  const id = await makeRun(page, prompt, { width: 1920, height: 1088 });
  await page.goto("/");
  await card(page, prompt).locator(".thumb").first().click();
  await expect(viewer(page).getByRole("button", { name: /Make 4K/ })).toBeVisible(); // a run that is not in the bin has it ...
  await page.keyboard.press("Escape");
  await bin(page, id);
  await page.reload();
  await deletedOption(page).click();
  await card(page, prompt).locator(".thumb").first().click();
  await expect(viewer(page).getByRole("link", { name: "Download", exact: true })).toBeVisible();
  for (const name of [/Make 4K/, /Enlarge/, /Regenerate larger/, /Edit this/]) await expect(viewer(page).getByRole("button", { name })).toHaveCount(0); // ... this one does not
});

test("a run that is only waiting is deleted for good, not put in the bin", async ({ page }) => {
  await useOptions(page, { steps: 100, numImages: 4 }); // about four seconds, so the next job waits behind it
  await clearHistory(page);
  await page.goto("/");
  const first = unique("the long one");
  const second = unique("waits, then deleted");
  for (const prompt of [first, second]) {
    await page.getByRole("textbox", { name: "Prompt", exact: true }).fill(prompt);
    await page.keyboard.press("Control+Enter");
    await expect(card(page, prompt).first()).toBeVisible();
  }
  const waiting = card(page, second);
  await expect(waiting.locator(".badge").first()).toHaveText(/^Queued/);
  const id = (await waiting.getAttribute("data-run-id"))!;
  await waiting.getByRole("button", { name: "Delete" }).click();
  const question = dialog(page, "Delete this run?");
  await expect(question).toContainText("This can't be undone.");
  await expect(question).not.toContainText("Deleted and stays");
  await question.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(waiting).toHaveCount(0);
  expect((await page.request.get(`/api/runs/${id}`)).status()).toBe(404);
  await expect(deletedOption(page)).toHaveText("Deleted 0");
  await expect(card(page, first).locator(".badge").first()).toHaveText("Done", { timeout: 40_000 });
});

test("another open page follows a delete and a restore live", async ({ page, context }) => {
  await clearHistory(page);
  const prompt = unique("followed live");
  await makeRun(page, prompt);
  await page.goto("/");
  const other = await context.newPage();
  await other.goto("/");
  await expect(card(other, prompt)).toBeVisible();

  await deleteViaPage(page, card(page, prompt));
  await expect(card(other, prompt)).toHaveCount(0); // it left the history over there too
  await expect(deletedOption(other)).toHaveText("Deleted 1");
  await deletedOption(other).click(); // the two pages share the remembered choice; this one looks at Deleted now
  await expect(card(other, prompt)).toBeVisible();

  await card(other, prompt).getByRole("button", { name: "Restore" }).click();
  await expect(card(other, prompt)).toHaveCount(0);
  await allOption(page).click();
  await expect(card(page, prompt)).toBeVisible(); // back in the history over here
  await expect(deletedOption(page)).toHaveText("Deleted 0");
});

test("a deleted run is not in Kept and is not counted there", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("kept and deleted");
  const id = await makeRun(page, prompt);
  await keep(page, id);
  await page.goto("/");
  await expect(keptOption(page)).toHaveText("Kept 1");
  await keptOption(page).click();
  await deleteViaPage(page, card(page, prompt));
  await expect(card(page, prompt)).toHaveCount(0);
  await expect(keptOption(page)).toHaveText("Kept 0");
  await expect(deletedOption(page)).toHaveText("Deleted 1");
  expect((await status(page, id)).pinned).toBe(true); // still marked Keep: it comes back kept
  await deletedOption(page).click();
  await expect(card(page, prompt).locator(".badge-kept")).toBeVisible();
});

test("the empty bin says how long deleted runs stay", async ({ page }) => {
  await clearHistory(page);
  await page.goto("/");
  await deletedOption(page).click();
  await expect(page.locator(".empty-state:visible")).toContainText("Runs you delete stay here for 30 days before they are gone for good.");
  await expect(emptyBinButton(page)).toBeDisabled();
  await page.locator(".empty-state:visible").getByRole("button", { name: "Show all" }).click();
  await expect(allOption(page)).toBeChecked();
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the filter bar with three options and Empty bin, a card in the bin and the questions fit the screen", async ({ page }) => {
    await clearHistory(page);
    const prompt = unique("phone bin");
    await bin(page, await makeRun(page, prompt));
    await page.goto("/");
    const noSideways = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    await deletedOption(page).click();
    await expect(emptyBinButton(page)).toBeVisible();
    for (const control of [bar(page), emptyBinButton(page), card(page, prompt).getByRole("button", { name: "Delete forever" })]) {
      const box = (await control.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(375);
    }
    await noSideways();
    await emptyBinButton(page).click();
    const question = dialog(page, "Empty the bin?");
    await expect(question).toBeVisible();
    const box = (await question.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(375);
    await noSideways();
    await question.getByRole("button", { name: "Cancel" }).click();
    await card(page, prompt).getByRole("button", { name: "Restore" }).click();
    await expect(card(page, prompt)).toHaveCount(0);
  });
});
