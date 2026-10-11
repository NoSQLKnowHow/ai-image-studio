// End-to-end: one picture or track in the bin (DESIGN.md §33; criteria 163-175), in a real browser against the real server with the fake pipeline.
import { card, clearHistory, expect, test, unique, type Locator, type Page } from "./helpers";

// the studio's API wants this header on every write: its guard against requests from other web pages
const ASK = { "X-Studio-Client": "1" };
type ApiImage = { id: string; idx: number; seed: number; thumb_url: string | null; url: string };
type ApiTrack = { id: string; idx: number; seed: number; url: string };
type ApiRun = {
  id: string; status: string; prompt: string; deleted_at: string | null; project_id: string | null;
  images: ApiImage[]; binned_images: (ApiImage & { deleted_at: string; purge_at: string | null })[];
  tracks: ApiTrack[]; binned_tracks: (ApiTrack & { deleted_at: string; purge_at: string | null })[];
};

// Locators for the parts of the page these tests use. `bar` is the filter bar that is on screen (the other tab's bar is hidden).
const bar = (page: Page): Locator => page.locator("[data-filter-bar]:visible");
const deletedOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Deleted/ });
const allOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^All/ });
const toastWith = (page: Page, text: string | RegExp): Locator => page.locator(".toast", { hasText: text });
const viewer = (page: Page): Locator => page.locator(".lightbox");
const question = (page: Page): Locator => page.getByRole("alertdialog");
const deletePictureButton = (page: Page): Locator => viewer(page).locator('[data-action="delete-picture"]');
const confirmButton = (page: Page): Locator => question(page).locator('[data-action="confirm-delete-picture"]');
const binnedCard = (page: Page, prompt: string): Locator => page.locator('[data-card="binned-items"]', { hasText: prompt });
const trackCard = (page: Page, text: string): Locator => page.locator("article.music-card", { hasText: text });
const musicTab = (page: Page): Locator => page.getByRole("tab", { name: "Music" });
const thumbs = (c: Locator): Locator => c.getByRole("button", { name: /^Open image \d+ of \d+/ });

// the run as the SERVER has it: the tests check the server's truth as well as what the page shows
async function runOf(page: Page, id: string): Promise<ApiRun> {
  return (await (await page.request.get(`/api/runs/${id}`)).json()) as ApiRun;
}

// a small run of `count` pictures made over the API, waited for
async function makeRun(page: Page, prompt: string, count: number): Promise<ApiRun> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { prompt, options: { steps: 2, width: 512, height: 512, seed: 3, num_images: count } } });
  expect(posted.ok(), await posted.text()).toBe(true);
  const { id } = (await posted.json()) as { id: string };
  await expect.poll(async () => (await runOf(page, id)).status, { timeout: 30_000 }).toBe("done");
  return runOf(page, id);
}

// the same for a music run of `count` versions
async function makeMusic(page: Page, prompt: string, count: number): Promise<ApiRun> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { mode: "music", prompt, options: { duration: 30, tracks: count } } });
  expect(posted.ok(), await posted.text()).toBe(true);
  const { id } = (await posted.json()) as { id: string };
  await expect.poll(async () => (await runOf(page, id)).status, { timeout: 45_000 }).toBe("done");
  return runOf(page, id);
}

// send a picture to the bin over the API, behind the page's back
async function binPicture(page: Page, pictureId: string): Promise<void> {
  const answer = await page.request.post(`/api/images/${pictureId}/bin`, { headers: ASK });
  expect(answer.ok(), await answer.text()).toBe(true);
}

// ------------------------------------------------------------------ in the viewer
// The whole round trip on one picture (criteria 163, 164): the question names it, the viewer moves on to the next picture, the card shows the ones
// that are left (numbered again, keeping their seeds), a toast offers Undo, and Undo puts the picture back at its own place.
test("Delete picture in the viewer: the question, the viewer moves on, Undo restores the picture at its place", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("three pictures");
  const run = await makeRun(page, prompt, 3);
  const [first, second, third] = run.images;
  await page.goto("/");
  const c = card(page, prompt);
  await c.getByRole("button", { name: /^Open image 2 of 3/ }).click();
  await expect(viewer(page).locator(".lightbox-title")).toContainText("Image 2 of 3");
  await deletePictureButton(page).click();
  await expect(question(page)).toContainText("Delete this picture?");
  await expect(question(page)).toContainText(`image 2 of 3, seed ${second.seed}`);
  await expect(question(page)).toContainText("It moves to Deleted and stays there for 30 days; you can restore it from there. Its 4K and Enlarge copies go with it.");
  await confirmButton(page).click();

  // the viewer stays open, now on what was the third picture, which is "Image 2 of 2"
  await expect(viewer(page).locator(".lightbox-title")).toContainText(`Image 2 of 2 · seed ${third.seed}`);
  await expect(toastWith(page, /Deleted image 2 of .* It stays in Deleted for 30 days\./)).toBeVisible();
  const after = await runOf(page, run.id);
  expect(after.images.map((p) => p.id)).toEqual([first.id, third.id]);
  expect(after.binned_images.map((p) => p.id)).toEqual([second.id]);
  await viewer(page).getByRole("button", { name: "Close" }).click();
  await expect(thumbs(c)).toHaveCount(2);
  await expect(c.getByRole("button", { name: `Open image 2 of 2 (seed ${third.seed})` })).toBeVisible(); // numbered again; the seed is the same

  await toastWith(page, /Deleted image 2 of/).getByRole("button", { name: "Undo" }).click();
  await expect(toastWith(page, /Restored image 2 of/)).toBeVisible();
  await expect(thumbs(c)).toHaveCount(3);
  expect((await runOf(page, run.id)).images.map((p) => p.id)).toEqual([first.id, second.id, third.id]); // at its own place
});

// Deleting the picture the viewer is on when it is the LAST one in the list: there is no next picture, so the viewer steps back to the one before (§33.1).
test("deleting the last picture in the list moves the viewer back to the one before it", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("at the end");
  const run = await makeRun(page, prompt, 3);
  await page.goto("/");
  await card(page, prompt).getByRole("button", { name: /^Open image 3 of 3/ }).click();
  await deletePictureButton(page).click();
  await confirmButton(page).click();
  await expect(viewer(page).locator(".lightbox-title")).toContainText(`Image 2 of 2 · seed ${run.images[1].seed}`);
  expect((await runOf(page, run.id)).binned_images.map((p) => p.id)).toEqual([run.images[2].id]);
});

// Criterion 165: the last picture sends the whole run. The question says so; the viewer closes, the card leaves the history, the toast is the run's
// own, and Undo brings the run back.
test("deleting the last picture moves the whole run to the bin; Undo brings it back", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("a single picture");
  const run = await makeRun(page, prompt, 1);
  await page.goto("/");
  const c = card(page, prompt);
  await c.getByRole("button", { name: /^Open image 1 of 1/ }).click();
  await deletePictureButton(page).click();
  await expect(question(page)).toContainText("This is the last picture of this run, so the whole run moves to Deleted");
  await confirmButton(page).click();
  await expect(viewer(page)).toHaveCount(0); // the run left the history: nothing left to look at
  await expect(c).toHaveCount(0);
  expect((await runOf(page, run.id)).deleted_at).not.toBeNull();
  await toastWith(page, /Deleted “.*”\. It stays in Deleted/).getByRole("button", { name: "Undo" }).click();
  await expect(c).toBeVisible();
  expect((await runOf(page, run.id)).deleted_at).toBeNull();
});

// A run filed in a project: the question says the run stays in it with its other pictures (§33.1), and the project is not changed by the deletion.
test("the question names the project the run is filed in", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("filed pictures");
  const run = await makeRun(page, prompt, 2);
  const project = (await (await page.request.post("/api/projects", { headers: ASK, data: { name: "Logo" } })).json()) as { id: string };
  expect((await page.request.put(`/api/runs/${run.id}/project`, { headers: ASK, data: { project_id: project.id } })).ok()).toBe(true);
  await page.goto("/");
  const c = card(page, prompt);
  await c.getByRole("button", { name: /^Open image 1 of 2/ }).click();
  await deletePictureButton(page).click();
  await expect(question(page)).toContainText("The run stays in the project “Logo” with its other pictures.");
  await confirmButton(page).click();
  await expect(toastWith(page, /Deleted image 1 of/)).toBeVisible();
  expect(await runOf(page, run.id)).toMatchObject({ project_id: project.id, deleted_at: null });
});

// ------------------------------------------------------------------ the Deleted view
// Criteria 167-169: a run in the history that has pictures in the bin is ONE card in Deleted, listing them with their own place, seed and dates; the
// number beside Deleted counts them; a thumbnail opens a picture read-only; Restore puts one back; Delete forever asks first and removes the files.
test("the Deleted view lists a run's deleted pictures on one card: open, Restore, Delete forever", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("some deleted pictures");
  const run = await makeRun(page, prompt, 3);
  const [first, second, third] = run.images;
  await binPicture(page, first.id);
  await binPicture(page, third.id);
  await page.goto("/");
  await expect(deletedOption(page)).toContainText("2"); // two things in the bin: counted as pictures, not as the run
  await deletedOption(page).click();
  const c = binnedCard(page, prompt);
  await expect(c).toBeVisible();
  await expect(c).toContainText("2 deleted pictures");
  await expect(c).toContainText("From a run made on");
  await expect(c.locator("li.binned-item")).toHaveCount(2);
  await expect(c.locator("li.binned-item").nth(0)).toContainText(`Image 1 · seed ${first.seed}`); // its own place in the run, where Restore puts it back
  await expect(c.locator("li.binned-item").nth(1)).toContainText(`Image 3 · seed ${third.seed}`);
  await expect(c.locator('[data-note="in-bin"]').first()).toContainText(/^In the bin since .+ It will be deleted for good around .+, in (29|30) days\.$/);

  // a thumbnail opens the picture in the viewer, which is read-only: it can be downloaded, and nothing in it changes the run
  await c.getByRole("button", { name: /^Open Image 1/ }).click();
  await expect(viewer(page).locator(".lightbox-title")).toContainText(`seed ${first.seed}`);
  await expect(viewer(page).getByRole("link", { name: "Download" })).toBeVisible();
  await expect(deletePictureButton(page)).toHaveCount(0);
  await expect(viewer(page).locator('[data-action="regenerate-larger"], [data-action="edit-this"]')).toHaveCount(0);
  await viewer(page).getByRole("button", { name: "Close" }).click();

  // Restore: it goes back to its place on the run's own card, and the deleted card has one picture left
  await c.getByRole("button", { name: /^Restore Image 1/ }).click();
  await expect(toastWith(page, /Restored image 1 of/)).toBeVisible();
  await expect(c).toContainText("1 deleted picture");
  await expect(deletedOption(page)).toContainText("1");
  expect((await runOf(page, run.id)).images.map((p) => p.id)).toEqual([first.id, second.id]);

  // Delete forever asks first, says what is removed, and then the picture and its thumbnail are gone from the Spark
  await c.getByRole("button", { name: /^Delete Image 3 · seed .* for good/ }).click();
  await expect(question(page)).toContainText("Delete this picture for good?");
  await expect(question(page)).toContainText("removed from the Spark. This can't be undone.");
  await question(page).getByRole("button", { name: "Cancel" }).click();
  expect((await runOf(page, run.id)).binned_images).toHaveLength(1); // asking can be backed out of
  await c.getByRole("button", { name: /^Delete Image 3 · seed .* for good/ }).click();
  await question(page).getByRole("button", { name: "Delete forever" }).click();
  await expect(toastWith(page, /Deleted a picture of .* for good\./)).toBeVisible();
  await expect(c).toHaveCount(0); // nothing of this run is in the bin now
  expect((await page.request.get(third.url)).status()).toBe(404);
  expect((await page.request.get(third.thumb_url as string)).status()).toBe(404);
  const end = await runOf(page, run.id);
  expect([end.images.length, end.binned_images.length, end.deleted_at]).toEqual([2, 0, null]);
});

// A run that is itself in the bin is an ordinary card of the bin, with the pictures it has: not the ones deleted before it (§33.2 item 5).
test("a run in the bin shows its own pictures, not the ones deleted before it", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("a deleted run");
  const run = await makeRun(page, prompt, 3);
  await binPicture(page, run.images[0].id);
  expect((await page.request.post(`/api/runs/${run.id}/bin`, { headers: ASK })).ok()).toBe(true);
  await page.goto("/");
  await deletedOption(page).click();
  const c = card(page, prompt);
  await expect(c.locator('[data-note="in-bin"]')).toBeVisible();
  await expect(thumbs(c)).toHaveCount(2);
  await expect(binnedCard(page, prompt)).toHaveCount(0); // one card, not two
  await expect(deletedOption(page)).toContainText("1"); // the run is one thing; its earlier deleted picture is not counted on its own
});

// ------------------------------------------------------------------ Empty bin
// Criterion 169: the question says how many runs and how many pictures; afterwards the runs are gone and the pictures with them, and what was not in the
// bin is untouched.
test("Empty bin says how many runs and pictures, and deletes both", async ({ page }) => {
  await clearHistory(page);
  const keep = unique("stays");
  const leave = unique("goes");
  const a = await makeRun(page, keep, 3);
  const b = await makeRun(page, leave, 1);
  await binPicture(page, a.images[0].id);
  await binPicture(page, a.images[1].id);
  expect((await page.request.post(`/api/runs/${b.id}/bin`, { headers: ASK })).ok()).toBe(true);
  const song = await makeMusic(page, unique("a song"), 2);
  expect((await page.request.post(`/api/tracks/${song.tracks[0].id}/bin`, { headers: ASK })).ok()).toBe(true); // and a track of the Music tab
  await page.goto("/");
  await expect(deletedOption(page)).toContainText("3"); // on this tab: two pictures and a run
  await deletedOption(page).click();
  await bar(page).locator('[data-action="empty-bin"]').click();
  // the whole bin, both tabs: it says which kind each is, in the total and tab by tab
  await expect(question(page)).toContainText(
    "Delete 1 run, 2 pictures and 1 track for good (1 run and 2 pictures on Images, 1 track on Music), with their files. This can't be undone.");
  await question(page).locator('[data-action="confirm-empty-bin"]').click();
  await expect(toastWith(page, "Emptied the bin: 1 run, 2 pictures and 1 track deleted for good.")).toBeVisible();
  expect((await runOf(page, song.id)).binned_tracks).toHaveLength(0);
  expect((await page.request.get(song.tracks[0].url)).status()).toBe(404);
  expect((await page.request.get(`/api/runs/${b.id}`)).status()).toBe(404);
  const left = await runOf(page, a.id);
  expect([left.images.map((p) => p.id), left.binned_images.length]).toEqual([[a.images[2].id], 0]);
  expect((await page.request.get(a.images[0].url)).status()).toBe(404);
  expect((await page.request.get(a.images[2].url)).status()).toBe(200);
  await expect(binnedCard(page, keep)).toHaveCount(0);
  await expect(deletedOption(page)).toContainText("0");
});

// ------------------------------------------------------------------ tracks
// Criterion 163 for a track: Delete track on a row; the others are numbered again; the Deleted view has a card with a player; Restore.
test("Delete track on a music run: the row goes, the others are numbered again, Restore brings it back", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("three versions");
  const run = await makeMusic(page, prompt, 3);
  const [one, two, three] = run.tracks;
  await page.goto("/");
  await musicTab(page).click();
  const c = trackCard(page, prompt);
  await expect(c.locator(".track")).toHaveCount(3);
  await c.getByRole("button", { name: /^Delete track: Version 2 of 3/ }).click();
  await expect(question(page)).toContainText("Delete this track?");
  await expect(question(page)).toContainText(`version 2 of 3, seed ${two.seed}`);
  await confirmButton(page).click();
  await expect(toastWith(page, /Deleted version 2 of .* It stays in Deleted for 30 days\./)).toBeVisible();
  await expect(c.locator(".track")).toHaveCount(2);
  await expect(c.locator(".track-name").nth(0)).toContainText("Version 1 of 2");
  await expect(c.locator(".track-name").nth(1)).toContainText("Version 2 of 2");
  expect((await runOf(page, run.id)).binned_tracks.map((t) => t.id)).toEqual([two.id]);

  await deletedOption(page).click();
  const d = binnedCard(page, prompt);
  await expect(d).toContainText("1 deleted track");
  await expect(d).toContainText(`Version 2 · seed ${two.seed}`);
  await expect(d.locator("audio")).toHaveAttribute("src", two.url);
  await d.getByRole("button", { name: /^Restore Version 2/ }).click();
  await expect(toastWith(page, /Restored version 2 of/)).toBeVisible();
  await expect(d).toHaveCount(0);
  await allOption(page).click();
  await expect(c.locator(".track")).toHaveCount(3);
  expect((await runOf(page, run.id)).tracks.map((t) => t.id)).toEqual([one.id, two.id, three.id]);

  // Delete forever on a deleted track asks first, and then its file is gone
  expect((await page.request.post(`/api/tracks/${two.id}/bin`, { headers: ASK })).ok()).toBe(true);
  await deletedOption(page).click();
  await d.getByRole("button", { name: /^Delete Version 2 · seed .* for good/ }).click();
  await expect(question(page)).toContainText("Delete this track for good?");
  await question(page).getByRole("button", { name: "Delete forever" }).click();
  await expect(toastWith(page, /Deleted a track of .* for good\./)).toBeVisible();
  await expect(d).toHaveCount(0);
  expect((await page.request.get(two.url)).status()).toBe(404);
  expect((await runOf(page, run.id)).tracks.map((t) => t.id)).toEqual([one.id, three.id]);
});

// A run with one track has no Delete track: its Delete is the run's.
test("a music run with a single track has no Delete track", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("one version");
  await makeMusic(page, prompt, 1);
  await page.goto("/");
  await musicTab(page).click();
  await expect(trackCard(page, prompt)).toBeVisible();
  await expect(trackCard(page, prompt).getByRole("button", { name: /^Delete track/ })).toHaveCount(0);
});

// A refusal from the server (here: Make 4K or Enlarge is working on the picture, 409 image_busy) is said as information, in the server's own words, and not as
// a failure; nothing changes. The refusal is answered the way the server answers it.
test("a refusal to delete a picture is said in the server's words, not as an error", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("a busy picture");
  const run = await makeRun(page, prompt, 2);
  await page.goto("/");
  const words = "Make 4K or Enlarge is working on this picture. Try again when it has finished.";
  await page.route(`**/api/images/${run.images[0].id}/bin`, (route) =>
    route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: words, code: "image_busy" }) }));
  await card(page, prompt).getByRole("button", { name: /^Open image 1 of 2/ }).click();
  await deletePictureButton(page).click();
  await confirmButton(page).click();
  await expect(toastWith(page, words)).toBeVisible();
  await expect(page.locator(".toast-error")).toHaveCount(0);
  expect((await runOf(page, run.id)).binned_images).toHaveLength(0);
});

// Restore from the Deleted view takes the picture, and with the last one the whole card, out of the list: the keyboard goes on from the filter bar, as it
// does after Restore on a run (§29.4), since the button that had the focus is gone.
test("restoring the last deleted picture of a card moves keyboard focus to the filter bar", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("focus after restore");
  const run = await makeRun(page, prompt, 2);
  await binPicture(page, run.images[0].id);
  await page.goto("/");
  await deletedOption(page).click();
  const c = binnedCard(page, prompt);
  await c.getByRole("button", { name: /^Restore Image 1/ }).focus();
  await page.keyboard.press("Enter");
  await expect(c).toHaveCount(0);
  await expect(deletedOption(page)).toBeFocused();
});

// ------------------------------------------------------------------ live, and a phone
// Criterion 174: another open page follows a deletion, a restore and the number beside Deleted with no reload.
test("another open page follows a deleted picture live", async ({ page, context }) => {
  await clearHistory(page);
  const prompt = unique("watched from two pages");
  const run = await makeRun(page, prompt, 3);
  await page.goto("/");
  const other = await context.newPage();
  await other.goto("/");
  await expect(thumbs(card(other, prompt))).toHaveCount(3);

  await binPicture(page, run.images[1].id); // from a script (or the first page)
  await expect(thumbs(card(other, prompt))).toHaveCount(2);
  await expect(deletedOption(other)).toContainText("1");
  expect((await page.request.post(`/api/images/${run.images[1].id}/restore`, { headers: ASK })).ok()).toBe(true);
  await expect(thumbs(card(other, prompt))).toHaveCount(3);
  await expect(deletedOption(other)).toContainText("0");
  await other.close();
});

// Criterion 175: on a phone the viewer's Delete picture, the question and the card of deleted pictures fit with no sideways scroll. 320 px, the narrowest.
test("on a phone Delete picture, its question and the deleted card fit the screen", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await clearHistory(page);
  const prompt = unique("phone pictures");
  const run = await makeRun(page, prompt, 2);
  await page.goto("/");
  const sideways = () => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  await card(page, prompt).getByRole("button", { name: /^Open image 1 of 2/ }).click();
  const button = await deletePictureButton(page).boundingBox();
  expect(button && button.x >= 0 && button.x + button.width <= 320, `Delete picture is inside the window: ${JSON.stringify(button)}`).toBe(true);
  await deletePictureButton(page).click();
  const box = await question(page).boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 320, `the question is inside the window: ${JSON.stringify(box)}`).toBe(true);
  await confirmButton(page).click();
  await expect(toastWith(page, /Deleted image 1 of/)).toBeVisible();
  await viewer(page).getByRole("button", { name: "Close" }).click();
  await deletedOption(page).click();
  const c = binnedCard(page, prompt);
  await expect(c).toBeVisible();
  const cardBox = await c.boundingBox();
  expect(cardBox && cardBox.x >= 0 && cardBox.x + cardBox.width <= 320, `the card is inside the window: ${JSON.stringify(cardBox)}`).toBe(true);
  expect(await sideways()).toBe(false);
  expect((await runOf(page, run.id)).binned_images).toHaveLength(1);
});
