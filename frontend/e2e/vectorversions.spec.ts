// End-to-end: Vector versions as one group (DESIGN.md §35; criteria 185 to 210), in a real browser against the real server with the fake pipeline: Delete
// version in the viewer and everything that follows from it (the numbering, the toast and Undo, the last version sending the run, the Deleted view's card of
// versions with its read-only viewer, Restore and Delete forever), and Stacked (depth): the Back sheet, the Height column and the stack view.
//
// The sheet is small and the colours few so that a run is as quick as a Vector run can be. What the tests do not assume: how many layers the clean-up finds in
// the stand-in picture (they check what holds for any number of at least one).
import { clearHistory, expect, test, unique, useOptions, type Locator, type Page } from "./helpers";

test.describe.configure({ timeout: 120_000 });

const ASK = { "X-Studio-Client": "1" }; // the studio's guard against requests from other web pages

type ApiVector = {
  id: string; idx: number; seed: number; layers: number; url: string; zip_url: string; png_url: string; preview_url: string; drawing_url: string; layer_urls: string[];
  check: { arrangement: string; stack_order: string | null; layers: { index: number; height: number }[] };
};
type ApiRun = {
  id: string; status: string; prompt: string; deleted_at: string | null; project_id: string | null;
  vectors: ApiVector[]; binned_vectors: (ApiVector & { deleted_at: string; purge_at: string | null })[]; options: Record<string, unknown>;
};

// ------------------------------------------------------------------ locators and helpers
const vectorTab = (page: Page): Locator => page.getByRole("tab", { name: "Vector" });
const imagesTab = (page: Page): Locator => page.getByRole("tab", { name: "Images" });
const form = (page: Page): Locator => page.getByRole("region", { name: "New vector" });
const bar = (page: Page): Locator => page.locator("[data-filter-bar]:visible");
const deletedOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Deleted/ });
const toastWith = (page: Page, text: string | RegExp): Locator => page.locator(".toast", { hasText: text });
const vectorCard = (page: Page, text: string): Locator => page.locator("article.vector-card", { hasText: text });
const binnedCard = (page: Page, prompt: string): Locator => page.locator('[data-card="binned-items"]', { hasText: prompt });
const viewer = (page: Page): Locator => page.locator(".lightbox.vector-viewer");
const title = (page: Page): Locator => viewer(page).locator("[data-viewer-title]");
const question = (page: Page): Locator => page.getByRole("alertdialog");
const deleteVersion = (page: Page): Locator => viewer(page).locator('[data-action="delete-version"]');
const confirmButton = (page: Page): Locator => question(page).locator('[data-action="confirm-delete-picture"]');
const thumbs = (c: Locator): Locator => c.locator("button.vector-thumb");
const radio = (page: Page, group: string, name: string): Locator => form(page).getByRole("radiogroup", { name: group }).getByRole("radio", { name });

async function runOf(page: Page, id: string): Promise<ApiRun> {
  return (await (await page.request.get(`/api/runs/${id}`)).json()) as ApiRun;
}

/** A Vector run of `count` versions made over the API and waited for. */
async function makeRun(page: Page, prompt: string, count: number, options: Record<string, unknown> = {}): Promise<ApiRun> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { mode: "vector", prompt, options: { colours: 3, width_mm: 40, height_mm: 40, seed: 11, num_images: count, ...options } } });
  expect(posted.status(), await posted.text()).toBe(201);
  const { id } = (await posted.json()) as { id: string };
  await expect.poll(async () => (await runOf(page, id)).status, { timeout: 100_000 }).toBe("done");
  const run = await runOf(page, id);
  expect(run.vectors).toHaveLength(count);
  return run;
}

/** Send a version to the bin over the API, behind the page's back. */
async function binVersion(page: Page, versionId: string): Promise<void> {
  const answer = await page.request.post(`/api/vectors/${versionId}/bin`, { headers: ASK });
  expect(answer.ok(), await answer.text()).toBe(true);
}

async function openVector(page: Page) {
  await page.goto("/");
  await vectorTab(page).click();
  await expect(form(page)).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await useOptions(page);
});

// ------------------------------------------------------------------ Delete version, in the viewer (criteria 189 to 192)
test("Delete version in the viewer: the question, the viewer moves on, the others are numbered again, Undo restores it at its place", async ({ page }) => {
  const prompt = unique("three versions");
  const run = await makeRun(page, prompt, 3);
  const [first, second, third] = run.vectors;
  await openVector(page);
  const c = vectorCard(page, prompt).first();
  await thumbs(c).nth(1).click();
  await expect(title(page)).toContainText(`Version 2 of 3 · seed ${second.seed}`);
  await deleteVersion(page).click();
  await expect(question(page)).toContainText("Delete this version?");
  await expect(question(page)).toContainText(`version 2 of 3, seed ${second.seed}`);
  await expect(question(page)).toContainText("It moves to Deleted and stays there for 30 days; you can restore it from there.");
  await question(page).getByRole("button", { name: "Cancel" }).click();
  expect((await runOf(page, run.id)).binned_vectors).toHaveLength(0); // asking can be backed out of
  await deleteVersion(page).click();
  await confirmButton(page).click();

  // the viewer stays open, now on what was the third version, which is "Version 2 of 2"
  await expect(title(page)).toContainText(`Version 2 of 2 · seed ${third.seed}`);
  await expect(toastWith(page, /Deleted version 2 of .* It stays in Deleted for 30 days\./)).toBeVisible();
  const after = await runOf(page, run.id);
  expect(after.vectors.map((v) => v.id)).toEqual([first.id, third.id]);
  expect(after.binned_vectors.map((v) => v.id)).toEqual([second.id]);
  await page.keyboard.press("Escape");
  await expect(thumbs(c)).toHaveCount(2);
  await expect(c.getByRole("button", { name: `Open version 2 of 2 (seed ${third.seed}, ${third.layers} ${third.layers === 1 ? "layer" : "layers"})` })).toBeVisible(); // numbered again; the seed is the same
  await expect(c.locator(".run-meta")).toContainText(`seed`);

  // its files wait in the bin; Undo puts it back where it was
  for (const url of [second.url, second.preview_url, second.drawing_url, second.layer_urls[0]]) expect((await page.request.get(url)).status(), url).toBe(200);
  await toastWith(page, /Deleted version 2 of/).getByRole("button", { name: "Undo" }).click();
  await expect(toastWith(page, /Restored version 2 of/)).toBeVisible();
  await expect(thumbs(c)).toHaveCount(3);
  expect((await runOf(page, run.id)).vectors.map((v) => v.id)).toEqual([first.id, second.id, third.id]); // at its own place
});

test("deleting the last version in the list steps the viewer back to the one before it", async ({ page }) => {
  const prompt = unique("at the end");
  const run = await makeRun(page, prompt, 2);
  await openVector(page);
  await thumbs(vectorCard(page, prompt).first()).nth(1).click();
  await deleteVersion(page).click();
  await confirmButton(page).click();
  await expect(title(page)).toContainText(`Seed ${run.vectors[0].seed}`); // one version is left: it is named by its seed alone
  expect((await runOf(page, run.id)).binned_vectors.map((v) => v.id)).toEqual([run.vectors[1].id]);
});

test("deleting the last version sends the whole run to the bin, says so first, and Undo brings the run back", async ({ page }) => {
  const prompt = unique("a single version");
  const run = await makeRun(page, prompt, 1);
  await openVector(page);
  const c = vectorCard(page, prompt).first();
  await thumbs(c).first().click();
  await deleteVersion(page).click();
  await expect(question(page)).toContainText("This is the last version of this run, so the whole run moves to Deleted");
  await confirmButton(page).click();
  await expect(viewer(page)).toHaveCount(0); // the run left the history: nothing left to look at
  await expect(c).toHaveCount(0);
  expect((await runOf(page, run.id)).deleted_at).not.toBeNull();
  await toastWith(page, /Deleted “.*”\. It stays in Deleted/).getByRole("button", { name: "Undo" }).click();
  await expect(c).toBeVisible();
  expect((await runOf(page, run.id)).deleted_at).toBeNull();
});

// ------------------------------------------------------------------ the Deleted view (criteria 193 to 197)
test("the Deleted view lists a run's deleted versions on one card: open them read-only, Restore, Delete forever", async ({ page }) => {
  const prompt = unique("some deleted versions");
  const run = await makeRun(page, prompt, 3);
  const [first, second, third] = run.vectors;
  await binVersion(page, first.id);
  await binVersion(page, third.id);
  await openVector(page);
  await expect(deletedOption(page)).toContainText("2"); // two things in the bin: counted as versions, not as the run
  await deletedOption(page).click();
  const c = binnedCard(page, prompt);
  await expect(c).toBeVisible();
  await expect(c).toContainText("2 deleted versions");
  await expect(c).toContainText("From a run made on");
  await expect(c.locator("li.binned-item")).toHaveCount(2);
  await expect(c.locator("li.binned-item").nth(0)).toContainText(`Version 1 · seed ${first.seed}`); // its own place in the run, where Restore puts it back
  await expect(c.locator("li.binned-item").nth(1)).toContainText(`Version 3 · seed ${third.seed}`);
  await expect(c.locator('[data-note="in-bin"]').first()).toContainText(/^In the bin since .+ It will be deleted for good around .+, in (29|30) days\.$/);

  // a preview opens the version in the viewer, which is read-only: the drawing and the vector are there, downloads work, and nothing in it changes the run
  await c.getByRole("button", { name: /^Open Version 1/ }).click();
  await expect(title(page)).toContainText(`Version 1 · seed ${first.seed} · deleted`);
  await expect(viewer(page).locator('[data-panel="drawing"] img')).toHaveAttribute("src", first.drawing_url);
  await expect(viewer(page).locator('[data-panel="vector"] img')).toHaveAttribute("src", first.url);
  await expect(viewer(page).getByRole("link", { name: "Download SVG" })).toBeVisible();
  await expect(deleteVersion(page)).toHaveCount(0);
  await page.keyboard.press("ArrowRight"); // it pages between the deleted versions only
  await expect(title(page)).toContainText(`Version 3 · seed ${third.seed} · deleted`);
  await page.keyboard.press("Escape");

  // Restore: it goes back to its place on the run's own card, and the deleted card has one version left
  await c.getByRole("button", { name: /^Restore Version 1/ }).click();
  await expect(toastWith(page, /Restored version 1 of/)).toBeVisible();
  await expect(c).toContainText("1 deleted version");
  await expect(deletedOption(page)).toContainText("1");
  expect((await runOf(page, run.id)).vectors.map((v) => v.id)).toEqual([first.id, second.id]);

  // Delete forever asks first, says what is removed, and then the version's files are gone from the Spark
  await c.getByRole("button", { name: /^Delete Version 3 · seed .* for good/ }).click();
  await expect(question(page)).toContainText("Delete this version for good?");
  await expect(question(page)).toContainText("Its SVG, its layers, its PNG and the drawing the model made are removed from the Spark. This can't be undone.");
  await question(page).getByRole("button", { name: "Cancel" }).click();
  expect((await runOf(page, run.id)).binned_vectors).toHaveLength(1); // asking can be backed out of
  await c.getByRole("button", { name: /^Delete Version 3 · seed .* for good/ }).click();
  await question(page).getByRole("button", { name: "Delete forever" }).click();
  await expect(toastWith(page, /Deleted a version of .* for good\./)).toBeVisible();
  await expect(c).toHaveCount(0); // nothing of this run is in the bin now
  for (const url of [third.url, third.preview_url, third.drawing_url, third.layer_urls[0], third.zip_url]) expect((await page.request.get(url)).status(), url).toBe(404);
  const end = await runOf(page, run.id);
  expect([end.vectors.length, end.binned_vectors.length, end.deleted_at]).toEqual([2, 0, null]);
  expect((await page.request.get(second.url)).status()).toBe(200); // the others are untouched
});

test("a run in the bin shows its own versions, not the ones deleted before it", async ({ page }) => {
  const prompt = unique("binned whole");
  const run = await makeRun(page, prompt, 3);
  await binVersion(page, run.vectors[1].id);
  expect((await page.request.post(`/api/runs/${run.id}/bin`, { headers: ASK })).ok()).toBe(true);
  await openVector(page);
  await deletedOption(page).click();
  const c = vectorCard(page, prompt).first();
  await expect(c).toBeVisible();
  await expect(c.locator(".badge-deleted")).toHaveText("Deleted");
  await expect(thumbs(c)).toHaveCount(2); // the two it has, not the one that was deleted before the run
  await expect(binnedCard(page, prompt)).toHaveCount(0);
  await thumbs(c).first().click();
  await expect(deleteVersion(page)).toHaveCount(0); // a run in the bin is read-only
  await expect(viewer(page).locator('[data-panel="drawing"] img')).toBeVisible();
});

test("another open page follows a deleted and a restored version live", async ({ page, context }) => {
  const prompt = unique("watched from two pages");
  const run = await makeRun(page, prompt, 3);
  await openVector(page);
  const other = await context.newPage();
  await other.goto("/");
  await vectorTab(other).click();
  await expect(thumbs(vectorCard(other, prompt).first())).toHaveCount(3);

  await binVersion(page, run.vectors[1].id); // from a script (or the first page)
  await expect(thumbs(vectorCard(other, prompt).first())).toHaveCount(2);
  await expect(deletedOption(other)).toContainText("1");
  expect((await page.request.post(`/api/vectors/${run.vectors[1].id}/restore`, { headers: ASK })).ok()).toBe(true);
  await expect(thumbs(vectorCard(other, prompt).first())).toHaveCount(3);
  await expect(deletedOption(other)).toContainText("0");
  await other.close();
});

test("a refusal to delete a version is said in the server's words, and the viewer is not left half way", async ({ page }) => {
  const prompt = unique("refused");
  const run = await makeRun(page, prompt, 2);
  await openVector(page);
  await thumbs(vectorCard(page, prompt).first()).first().click();
  await deleteVersion(page).click();
  await page.route(`**/api/vectors/${run.vectors[0].id}/bin`, (route) => route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "This run is in the bin. Restore the run first.", code: "run_in_bin" }) }));
  await confirmButton(page).click();
  await expect(question(page)).toHaveCount(0);
  await expect(viewer(page)).toBeVisible(); // the viewer is still open, on the same version
  await expect(title(page)).toContainText(`Version 1 of 2 · seed ${run.vectors[0].seed}`);
  expect((await runOf(page, run.id)).binned_vectors).toHaveLength(0);
});

// ------------------------------------------------------------------ Copy SVG code from the viewer (criterion 186)
// The page behind a modal, toasts included, is hidden from screen readers (§24.2), so what a request made in the viewer answers is said INSIDE the viewer.
test("Copy SVG code in the viewer answers inside the viewer, in words, and not as a toast behind it", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const prompt = unique("copy from the viewer");
  const run = await makeRun(page, prompt, 2);
  await openVector(page);
  await thumbs(vectorCard(page, prompt).first()).nth(1).click();
  await viewer(page).getByRole("button", { name: "Copy SVG code" }).click();
  await expect(viewer(page).locator(".lightbox-notice")).toHaveText("SVG code copied.");
  await expect(viewer(page).locator(".lightbox-notice")).not.toHaveClass(/error/);
  await expect(toastWith(page, "SVG code copied.")).toHaveCount(0);
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe(await (await page.request.get(run.vectors[1].url)).text()); // the version that is shown, not another
  expect(copied).toMatch(/<svg[\s>]/);
  await viewer(page).getByRole("button", { name: "Next version" }).click();
  await expect(viewer(page).locator(".lightbox-notice")).toHaveCount(0); // moving on clears what was said about the last one
  await page.route("**/api/vectors/*/svg", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "That vector no longer exists.", code: "not_found" }) }));
  await viewer(page).getByRole("button", { name: "Copy SVG code" }).click();
  await expect(viewer(page).locator(".lightbox-notice.error")).toContainText("Couldn't get the SVG:");
  await expect(page.getByRole("alert").filter({ hasText: "Couldn't get the SVG" })).toHaveCount(0);
});

// ------------------------------------------------------------------ Stacked (depth) (criteria 198 to 210)
test("the Back sheet is greyed until the run is stacked by colour, is sent, is remembered, and Reuse brings it back", async ({ page }) => {
  await openVector(page);
  const select = form(page).locator("[data-back-sheet] select");
  await expect(select).toBeDisabled();
  await expect(form(page).locator("[data-back-sheet] .field-hint")).toHaveText("Used only when the layers are Stacked (depth).");
  await radio(page, "Arrangement", "Stacked (depth)").click();
  await expect(select).toBeEnabled();
  await expect(select.locator("option")).toHaveText(["The colour with the most ink", "The colour with the least ink", "The first ink in my list"]);
  await select.selectOption("area_asc");
  await radio(page, "Layers by", "Shape").click();
  await expect(select).toBeDisabled(); // layers by shape are always side by side
  await expect(form(page).locator("[data-back-sheet] .field-hint")).toContainText("no back sheet");
  await radio(page, "Layers by", "Colour").click();
  await expect(select).toHaveValue("area_asc"); // the choice the person made is still there

  const idea = unique("a fox head");
  await form(page).getByLabel("Prompt", { exact: true }).fill(idea);
  await form(page).getByRole("button", { name: "More colours" }).click();
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/runs") && r.method() === "POST");
  await form(page).getByRole("button", { name: "Make vector" }).click();
  const body = (await sent).postDataJSON();
  expect(body.options).toMatchObject({ arrangement: "stacked", stack_order: "area_asc", layers_by: "colour" });

  const c = vectorCard(page, idea).first();
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 100_000 });
  expect((await runOf(page, (await c.getAttribute("data-run-id"))!)).options).toMatchObject({ arrangement: "stacked", stack_order: "area_asc" });
  await page.reload();
  await vectorTab(page).click();
  await expect(form(page).locator("[data-back-sheet] select")).toHaveValue("area_asc"); // remembered across a reload
  await form(page).locator("[data-back-sheet] select").selectOption("palette");
  await vectorCard(page, idea).first().locator('[data-action="reuse"]').click();
  await expect(form(page).locator("[data-back-sheet] select")).toHaveValue("area_asc"); // Reuse puts the run's own choice back
});

test("a stacked version shows the Height column and a stack view whose slider peels the sheets from the front", async ({ page }) => {
  const prompt = unique("stacked depth");
  const run = await makeRun(page, prompt, 1, { arrangement: "stacked", stack_order: "area_desc", bleed_mm: 0 });
  const version = run.vectors[0];
  expect(version.check.arrangement).toBe("stacked");
  expect(version.check.stack_order).toBe("area_desc");
  const count = version.layers;
  expect(version.check.layers.map((l) => l.height)).toEqual(Array.from({ length: count }, (_, i) => i + 1)); // 1 at the back, as high as its number
  await openVector(page);
  const c = vectorCard(page, prompt).first();
  await thumbs(c).first().click();
  const v = viewer(page);
  await expect(v.getByRole("columnheader", { name: "Height" })).toBeVisible();
  await expect(v.locator("td[data-height]")).toHaveText(Array.from({ length: count }, (_, i) => String(i + 1)));

  const stack = v.locator("[data-stack-view]");
  await expect(stack).toBeVisible();
  await expect(stack.locator(".stack-sheet")).toHaveCount(count);
  await expect(stack.locator(".stack-sheet img").first()).toHaveAttribute("src", version.layer_urls[0]); // the back sheet is the first layer
  for (const sheet of await stack.locator(".stack-sheet img").all()) {
    await expect.poll(() => sheet.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true); // each layer's own SVG loads, in an <img>
  }
  await expect(stack.locator("[data-sheet-caption]").first()).toHaveText(/^\s*Layer 1, .*, 1 high \(back\)$/);
  await expect(stack.locator("[data-sheet-caption]").last()).toContainText(count > 1 ? `Layer ${count}` : "Layer 1");
  const slider = stack.locator("[data-stack-shown]"); // (the label also matches the frame's own "2 of 2 sheets shown")
  await expect(stack.getByLabel("Sheets shown", { exact: true })).toHaveCount(1);
  await expect(slider).toHaveValue(String(count));
  await expect(stack.locator("[data-stack-shown-text]")).toHaveText(`${count} of ${count}`);
  if (count > 1) {
    await slider.fill(String(count - 1)); // the front sheet goes
    await expect(stack.locator(".stack-sheet")).toHaveCount(count - 1);
    await expect(stack.locator("[data-stack-shown-text]")).toHaveText(`${count - 1} of ${count}`);
    await expect(stack.locator("li.hidden-sheet")).toHaveCount(1);
    await slider.fill("1");
    await expect(stack.locator(".stack-sheet")).toHaveCount(1);
    await slider.fill(String(count));
    await expect(stack.locator(".stack-sheet")).toHaveCount(count);
  }
  await expect(v.locator("object, embed, iframe")).toHaveCount(0);
});

test("a version that is not stacked has neither the Height column nor a stack view", async ({ page }) => {
  const prompt = unique("side by side");
  await makeRun(page, prompt, 1);
  await openVector(page);
  await thumbs(vectorCard(page, prompt).first()).first().click();
  await expect(viewer(page).getByRole("columnheader", { name: "Layer" })).toBeVisible();
  await expect(viewer(page).getByRole("columnheader", { name: "Height" })).toHaveCount(0);
  await expect(viewer(page).locator("[data-stack-view]")).toHaveCount(0);
});

// ------------------------------------------------------------------ a phone (criteria 185, 186)
test("on a phone the viewer's stack view, Delete version and the deleted card fit the screen", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const prompt = unique("phone versions");
  const run = await makeRun(page, prompt, 2, { arrangement: "stacked", bleed_mm: 0 });
  await binVersion(page, run.vectors[1].id);
  await openVector(page);
  const fits = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth && document.body.scrollWidth <= window.innerWidth);
  const c = vectorCard(page, prompt).first();
  await thumbs(c).first().click();
  const stack = viewer(page).locator("[data-stack-view]");
  await stack.scrollIntoViewIfNeeded();
  const box = (await stack.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(320);
  expect(await fits()).toBe(true);
  await page.keyboard.press("Escape");
  await deletedOption(page).click();
  const deleted = binnedCard(page, prompt);
  await expect(deleted).toBeVisible();
  const cardBox = (await deleted.boundingBox())!;
  expect(cardBox.x + cardBox.width).toBeLessThanOrEqual(320);
  for (const button of await deleted.getByRole("button").all()) {
    const b = (await button.boundingBox())!;
    expect(b.x + b.width).toBeLessThanOrEqual(320);
  }
  expect(await fits()).toBe(true);
  await imagesTab(page).click(); // (the other tabs are untouched by all this)
});
