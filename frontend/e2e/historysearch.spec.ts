// Search and tags across history (DESIGN.md section 45, criteria 404 to 407, 409 to 418, 420, 423 to 427), in a real browser against the real server with the fake pipeline and the Prompt
// builder's scripted model (STUDIO_BUILDER_LLM=fake, which answers a Suggest request in the right shape): the tag and note editors on a card and in the viewer, the search box and the
// Filters row on every tab, the result count that is announced, what is and is not remembered over a reload, Suggest, and a 360 px phone. What the unit tests cannot see is here: real focus,
// real keys, real layout.
import { clearHistory, expect, test, unique, useOptions, type Locator, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
const runCard = (page: Page, id: string): Locator => page.locator(`article.run-card[data-run-id="${id}"]`);
// The one filter bar is mounted in every tab's panel, and the panels that are not shown are hidden: the shown one is the one meant.
const searchBox = (page: Page): Locator => page.getByRole("searchbox", { name: "Search history" });
const result = (page: Page): Locator => page.locator("[data-filter-result]:visible");
const tabs = (page: Page, name: string): Locator => page.getByRole("tab", { name });

/** A finished picture run, made through the API (small, two steps, fast) and waited for. */
async function made(page: Page, prompt: string, extra: Record<string, unknown> = {}): Promise<string> {
  const response = await page.request.post("/api/runs", { headers: ASK, data: { prompt, options: { steps: 2, width: 512, height: 512, seed: 5, ...extra } } });
  expect(response.status()).toBe(201);
  const id = ((await response.json()) as { id: string }).id;
  await expect.poll(async () => ((await (await page.request.get(`/api/runs/${id}`)).json()) as { status: string }).status, { timeout: 20_000 }).toBe("done");
  return id;
}
const putTags = (page: Page, id: string, tags: string[]) => page.request.put(`/api/runs/${id}/tags`, { headers: ASK, data: { tags } });
const putNotes = (page: Page, id: string, notes: string) => page.request.put(`/api/runs/${id}/notes`, { headers: ASK, data: { notes } });
const get = async (page: Page, id: string) => (await (await page.request.get(`/api/runs/${id}`)).json()) as { tags: string[]; notes: string };

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await useOptions(page);
});

// ---- the editor on a card (criteria 404, 405, 407) -------------------------------------------------------------------------------------------------------
test("tags are typed on a card with commas, offered from the tags in use, removed, and a note is saved; all of it survives a reload", async ({ page }) => {
  const other = await made(page, unique("harbour at dawn"));
  await putTags(page, other, ["harbour", "winter"]);
  const words = unique("a fox in a snowy wood");
  const id = await made(page, words);
  await page.goto("/");
  const card = runCard(page, id);
  await card.getByRole("button", { name: "Add tags or a note" }).click();
  const box = card.getByRole("combobox", { name: "Add a tag" });
  await expect(box).toBeFocused();
  // the tags in use are offered as the box is used, most used first
  await expect(card.getByRole("option", { name: /^winter/ })).toBeVisible();
  await box.fill("fox, snow");
  await expect(card.locator("[data-annotation-status]")).toHaveText("Added the tag “fox”.");
  await box.press("Enter");
  await expect(card.locator(".annotations-editor [data-tag]")).toHaveText(["fox", "snow"]);
  expect((await get(page, id)).tags).toEqual(["fox", "snow"]);
  // chosen from the list by keyboard
  await box.fill("win");
  await box.press("ArrowDown");
  await box.press("Enter");
  await expect(card.locator(".annotations-editor [data-tag]")).toHaveText(["fox", "snow", "winter"]);
  // removed
  await card.getByRole("button", { name: "Remove the tag “snow”" }).click();
  await expect(card.locator(".annotations-editor [data-tag]")).toHaveText(["fox", "winter"]);
  await expect(box).toBeFocused();
  // the note
  await card.locator("[data-note-input]").fill("chosen for the poster");
  await card.getByRole("button", { name: "Save" }).click();
  await expect(card.locator("[data-annotation-status]")).toHaveText("Note saved.");
  await page.reload();
  const again = runCard(page, id);
  await expect(again.locator("[data-annotations-view] [data-tag]")).toHaveText(["fox", "winter"]);
  await expect(again.locator("[data-annotation=note]")).toHaveText("chosen for the poster");
  // too many: a thirteenth is refused in words and nothing is sent
  await putTags(page, id, Array.from({ length: 12 }, (_, i) => `t${String(i).padStart(2, "0")}`));
  await expect(again.locator("[data-annotations-view] [data-tag]")).toHaveCount(12);
  await again.getByRole("button", { name: "Tags and notes" }).click();
  await again.getByRole("combobox", { name: "Add a tag" }).fill("one more");
  await again.getByRole("combobox", { name: "Add a tag" }).press("Enter");
  await expect(again.locator("[data-annotation-problem]")).toHaveText("A run can have 12 tags. Remove one to add another.");
});

test("a card in the bin shows its tags and note and offers no way to change them", async ({ page }) => {
  const id = await made(page, unique("binned words"));
  await putTags(page, id, ["fox"]);
  await putNotes(page, id, "keep this thought");
  await page.request.post(`/api/runs/${id}/bin`, { headers: ASK });
  await page.goto("/");
  await page.locator("[data-filter-bar]:visible").getByRole("radio", { name: /^Deleted/ }).click();
  const card = runCard(page, id);
  await expect(card.locator("[data-annotations-view] [data-tag]")).toHaveText(["fox"]);
  await expect(card.locator("[data-annotation=note]")).toHaveText("keep this thought");
  await expect(card.getByRole("button", { name: /tags|note/i })).toHaveCount(0);
});

// ---- the editor in the viewer (criterion 406) ------------------------------------------------------------------------------------------------------------
test("the viewer has the same editor; Esc closes the panel first and the viewer on the second press", async ({ page }) => {
  const id = await made(page, unique("a lighthouse"));
  await page.goto("/");
  await runCard(page, id).getByRole("button", { name: /^Open image 1 of/ }).click();
  const viewer = page.locator(".lightbox");
  await expect(viewer).toBeVisible();
  // the page behind the modal is hidden from the accessibility tree, so the viewer's own controls are found by CSS and by role inside it
  await viewer.locator('[data-action="annotations"]').click();
  const panel = viewer.locator("[data-annotations-panel]");
  await expect(panel).toBeVisible();
  const box = panel.getByRole("combobox", { name: "Add a tag" });
  await box.fill("beacon");
  await box.press("Enter");
  await expect(panel.locator(".annotations-editor [data-tag]")).toHaveText(["beacon"]);
  await expect(viewer.locator('[data-action="annotations"]')).toContainText("· 1");
  // arrow keys in the box move its caret and do not turn the viewer's page
  await box.press("ArrowLeft");
  await expect(viewer).toBeVisible();
  await page.keyboard.press("Escape"); // the tag list is closed (a tag was just added), so this one closes the panel and not the viewer
  await expect(panel).toHaveCount(0);
  await expect(viewer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  expect((await get(page, id)).tags).toEqual(["beacon"]);
});

// ---- search (criteria 409 to 414, 418, 423, 424) -----------------------------------------------------------------------------------------------------------
test("the search narrows the history by prompt, tag and note, the count is announced, and Clear, Kept and a reload behave", async ({ page }) => {
  const fox = await made(page, unique("a red fox in the snow"));
  const lighthouse = await made(page, unique("a lighthouse at dusk"));
  const harbour = await made(page, unique("a quiet harbour"));
  await putTags(page, lighthouse, ["beacon"]);
  await putNotes(page, harbour, "chosen for the poster");
  await page.request.patch(`/api/runs/${fox}`, { headers: ASK, data: { pinned: true } });
  await page.goto("/");
  await expect(page.locator("article.run-card:visible")).toHaveCount(3);
  await searchBox(page).fill("fox");
  await expect(page.locator("article.run-card:visible")).toHaveCount(1);
  await expect(runCard(page, fox)).toBeVisible();
  await expect(result(page)).toHaveText("1 result");
  // a tag and a note are found, and the count follows
  await searchBox(page).fill("beacon");
  await expect(runCard(page, lighthouse)).toBeVisible();
  await expect(page.locator("article.run-card:visible")).toHaveCount(1);
  await searchBox(page).fill("poster");
  await expect(runCard(page, harbour)).toBeVisible();
  // the start of a word is enough; case is ignored; words are all required
  await searchBox(page).fill("LIGHT dus");
  await expect(runCard(page, lighthouse)).toBeVisible();
  await searchBox(page).fill("light snow");
  await expect(page.locator('[data-empty="narrowed"]:visible')).toBeVisible();
  await expect(result(page)).toHaveText("No results");
  // combined with Show: the number beside Kept follows the search
  await searchBox(page).fill("fox");
  await page.locator("[data-filter-bar]:visible").getByRole("radio", { name: /^Kept/ }).click();
  await expect(runCard(page, fox)).toBeVisible();
  await page.locator("[data-filter-bar]:visible").getByRole("radio", { name: /^All/ }).click();
  // what a person can type is never an error
  for (const odd of ['"', "fox*", "fox AND hen", "-fox", "a:b", "(", "' OR 1=1 --", "\\"]) {
    await searchBox(page).fill(odd);
    await page.waitForTimeout(400);
    await expect(page.locator("[data-filter-bar]:visible")).toBeVisible();
    await expect(page.getByText("Couldn't search your history")).toHaveCount(0);
  }
  // Clear
  await searchBox(page).fill("fox");
  await expect(page.locator("article.run-card:visible")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear the search" }).click();
  await expect(page.locator("article.run-card:visible")).toHaveCount(3);
  await expect(searchBox(page)).toBeFocused();
  // not remembered over a reload, while Show is
  await searchBox(page).fill("fox");
  await page.locator("[data-filter-bar]:visible").getByRole("radio", { name: /^Kept/ }).click();
  await page.reload();
  await expect(searchBox(page)).toHaveValue("");
  await expect(page.locator("[data-filter-bar]:visible").getByRole("radio", { name: /^Kept/ })).toHaveAttribute("aria-checked", "true");
});

test("a search finds music lyrics, and every tab has the box; a Model and a date range narrow, and a reversed range is refused in words", async ({ page }) => {
  const picture = await made(page, unique("a river bend"));
  const music = await page.request.post("/api/runs", { headers: ASK, data: { mode: "music", prompt: unique("slow piano"), lyrics: "twinkle twinkle little star", options: { duration: 30 } } });
  expect(music.status()).toBe(201);
  const musicId = ((await music.json()) as { id: string }).id;
  await expect.poll(async () => ((await (await page.request.get(`/api/runs/${musicId}`)).json()) as { status: string }).status, { timeout: 30_000 }).toBe("done");
  await page.goto("/");
  for (const name of ["Images", "Music", "Vector", "Speech"]) {
    await tabs(page, name).click();
    await expect(searchBox(page)).toBeVisible();
  }
  await tabs(page, "Music").click();
  await searchBox(page).fill("twinkle");
  await expect(page.locator("article.run-card:visible")).toHaveCount(1);
  await expect(result(page)).toHaveText("1 result");
  await searchBox(page).fill("");
  // Filters: Model on the Music tab, no Size there
  const bar = page.locator("[data-filter-bar]:visible");
  await bar.getByRole("button", { name: /^Filters/ }).click();
  await expect(bar.getByLabel("Model")).toBeVisible();
  await expect(bar.getByLabel("Size")).toHaveCount(0);
  await tabs(page, "Images").click();
  // each tab's bar opens its Filters row for itself: this tab's is shut
  await bar.getByRole("button", { name: /^Filters/ }).click();
  await expect(bar.getByLabel("Size")).toBeVisible();
  await expect(bar.getByLabel("Model")).toContainText("fake-pipeline");
  await bar.getByLabel("Model").selectOption("fake-pipeline");
  await expect(runCard(page, picture)).toBeVisible();
  await expect(bar.getByRole("button", { name: /^Filters/ })).toContainText("· 1");
  await bar.getByLabel("Size").selectOption("512x512");
  await expect(runCard(page, picture)).toBeVisible();
  // the Model of the Images tab is not the Music tab's: nothing carried over, and it is still there when we come back
  await tabs(page, "Music").click();
  await expect(bar.getByLabel("Model")).toHaveValue("");
  await expect(page.locator("article.run-card:visible")).toHaveCount(1);
  await tabs(page, "Images").click();
  await expect(bar.getByLabel("Model")).toHaveValue("fake-pipeline");
  // a date range: today (the browser's own day) holds the run; a past one does not; a reversed one is refused in words
  const today = await page.evaluate(() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; });
  await bar.getByLabel("From", { exact: true }).fill(today);
  await bar.getByLabel("To", { exact: true }).fill(today);
  await expect(runCard(page, picture)).toBeVisible();
  await bar.getByLabel("From", { exact: true }).fill("2020-01-01");
  await bar.getByLabel("To", { exact: true }).fill("2020-01-31");
  await expect(page.locator('[data-empty="narrowed"]:visible')).toContainText("No runs match");
  await expect(page.locator('[data-empty="narrowed"]:visible')).toContainText("made from 2020-01-01 to 2020-01-31");
  await bar.getByLabel("From", { exact: true }).fill("2020-02-01");
  await expect(bar.locator("[data-filter-problem]")).toContainText("“From” is after “To”");
  await page.getByRole("button", { name: "Clear search and filters" }).click();
  await expect(runCard(page, picture)).toBeVisible();
  await expect(bar.getByLabel("Model")).toHaveValue("");
});

test("a run edited so that it no longer matches leaves the search with a toast, and Undo brings it back", async ({ page }) => {
  const id = await made(page, unique("a lighthouse at dusk"));
  await putTags(page, id, ["beacon"]);
  await page.goto("/");
  await searchBox(page).fill("beacon");
  const card = runCard(page, id);
  await expect(result(page)).toHaveText("1 result"); // the search has been made (the box waits a quarter of a second), so the run is in a view that holds it only because of its tag
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Tags and notes" }).click();
  await card.getByRole("button", { name: "Remove the tag “beacon”" }).click();
  await expect(card).toHaveCount(0);
  const toast = page.getByText(/no longer matches this view/);
  await expect(toast).toBeVisible();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(runCard(page, id)).toBeVisible();
  expect((await get(page, id)).tags).toEqual(["beacon"]);
});

// ---- Suggest (criteria 426 to 429) -----------------------------------------------------------------------------------------------------------------------
test("Suggest shows tags from the scripted model and applies none until one is pressed; a failure is said in words", async ({ page, consoleErrors }) => {
  const id = await made(page, unique("a lighthouse beacon storm"));
  await page.goto("/");
  const card = runCard(page, id);
  await card.getByRole("button", { name: "Add tags or a note" }).click();
  const suggest = card.locator('[data-action="suggest"]');
  await expect(suggest).toBeEnabled();
  await suggest.click();
  const row = card.locator("[data-suggestions]");
  await expect(row).toBeVisible();
  await expect(row.locator("[data-suggestion]").first()).toBeVisible();
  expect((await get(page, id)).tags).toEqual([]); // shown, not applied
  await expect(card.locator(".annotations-editor [data-tag]")).toHaveCount(0);
  const first = (await row.locator("[data-suggestion]").first().getAttribute("data-suggestion")) as string;
  await row.locator(`[data-suggestion="${first}"]`).click();
  await expect(card.locator(".annotations-editor [data-tag]")).toHaveText([first]);
  await expect(row.locator(`[data-suggestion="${first}"]`)).toHaveCount(0); // one at a time: it left the row
  expect((await get(page, id)).tags).toEqual([first]);
  await card.getByRole("button", { name: "Dismiss suggestions" }).click();
  await expect(row).toHaveCount(0);
  // a failure is said in words and the button is there for another try (the scripted model fails on a marker in the words)
  const bad = await made(page, unique("a failing [[error:unreachable]] case"));
  await page.reload();
  const badCard = runCard(page, bad);
  await badCard.getByRole("button", { name: "Add tags or a note" }).click();
  await badCard.locator('[data-action="suggest"]').click();
  await expect(badCard.locator("[data-annotation-problem]")).toContainText("Couldn't get suggestions: Can't reach the Ollama server.");
  consoleErrors.splice(0); // the browser logs the 503 that was provoked on purpose; it is the failure being tested, not a fault of the page
  await expect(badCard.locator('[data-action="suggest"]')).toBeEnabled();
});

// ---- a phone (criterion 425) -----------------------------------------------------------------------------------------------------------------------------
test("on a 360 px phone the search box, the Filters row, the editor, the suggestions and the viewer panel fit", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  const id = await made(page, unique("a long prompt about a lighthouse at dusk with gulls over a grey sea"));
  await putTags(page, id, ["lighthouse", "dusk", "gulls", "a longer tag with several words"]);
  await putNotes(page, id, "A longer note that runs over a few lines so that the card has to wrap it on a narrow screen and not push the page sideways.");
  await page.goto("/");
  const fits = async (what: string) => {
    const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(wide, `${what}: horizontal overflow`).toBeLessThanOrEqual(0);
  };
  await fits("the page");
  await searchBox(page).fill("lighthouse");
  await page.locator("[data-filter-bar]:visible").getByRole("button", { name: /^Filters/ }).click();
  await expect(page.locator("[data-filters-panel]")).toBeVisible();
  await page.locator("[data-filter-bar]:visible").getByLabel("From").fill("2026-01-01");
  await fits("the Filters row");
  // a change of filter reads the list again, and the cards are drawn anew when it arrives: wait for it before using one
  await expect(result(page)).toHaveText("1 result");
  const card = runCard(page, id);
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Tags and notes" }).click();
  await card.locator('[data-action="suggest"]').click();
  await expect(card.locator("[data-suggestions]")).toBeVisible();
  await fits("the editor and the suggestions");
  await card.getByRole("button", { name: "Close tags and notes" }).click();
  await card.getByRole("button", { name: /^Open image 1 of/ }).click();
  const viewer = page.locator(".lightbox");
  await viewer.locator('[data-action="annotations"]').click();
  const panel = viewer.locator("[data-annotations-panel]");
  await expect(panel).toBeVisible();
  const box = await panel.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(360);
  const view = page.viewportSize()!;
  expect(box!.y + box!.height).toBeLessThanOrEqual(view.height);
  // the picture is not made smaller by the panel: it is over it, with a scroll of its own
  await fits("the viewer");
});
