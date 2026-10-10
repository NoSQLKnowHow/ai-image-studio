// End-to-end: project folders (DESIGN.md §32; criteria 146-162), in a real browser against the real server with the fake pipeline.
import { card, clearHistory, expect, promptBox, test, unique, useOptions, type Locator, type Page } from "./helpers";

// the studio's API wants this header on every write: its guard against requests from other web pages
const ASK = { "X-Studio-Client": "1" };
type ApiRun = { id: string; status: string; pinned: boolean; prompt: string; project_id: string | null; deleted_at: string | null };
type ApiProject = { id: string; name: string; counts: { image: number; music: number } };

// Locators for the parts of the page these tests use. `bar` is the filter bar that is on screen (the other tab's bar is hidden).
const bar = (page: Page): Locator => page.locator("[data-filter-bar]:visible");
const projectSelect = (page: Page): Locator => bar(page).locator("[data-filter-project]");
const allOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^All/ });
const keptOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Kept/ });
const deletedOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Deleted/ });
const toastWith = (page: Page, text: string | RegExp): Locator => page.locator(".toast", { hasText: text });
const projectButton = (c: Locator): Locator => c.locator('[data-action="project"]');
const menu = (page: Page): Locator => page.getByRole("menu", { name: "Projects" });
const menuItem = (page: Page, name: string | RegExp): Locator => menu(page).getByRole("menuitemradio", { name });
const keepButton = (c: Locator): Locator => c.getByRole("button", { name: "Keep" });
const chip = (c: Locator): Locator => c.locator("[data-project-chip]");
const trackCard = (page: Page, text: string): Locator => page.locator("article.music-card", { hasText: text });
const musicTab = (page: Page): Locator => page.getByRole("tab", { name: "Music" });
const dialog = (page: Page, name: string | RegExp): Locator => page.getByRole("alertdialog", { name });

// the run and the project as the SERVER has them: the tests check the server's truth as well as what the page shows
async function status(page: Page, id: string): Promise<ApiRun> {
  return (await (await page.request.get(`/api/runs/${id}`)).json()) as ApiRun;
}
async function projects(page: Page): Promise<ApiProject[]> {
  return ((await (await page.request.get("/api/projects")).json()) as { projects: ApiProject[] }).projects;
}

// make a project over the API
async function makeProject(page: Page, name: string): Promise<ApiProject> {
  const made = await page.request.post("/api/projects", { headers: ASK, data: { name } });
  expect(made.status(), await made.text()).toBe(201);
  return (await made.json()) as ApiProject;
}

// make a small picture over the API and wait for it
async function makeRun(page: Page, prompt: string, extra: Record<string, unknown> = {}): Promise<string> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { prompt, options: { steps: 2, width: 512, height: 512, seed: 3, ...extra } } });
  expect(posted.ok(), await posted.text()).toBe(true);
  const { id } = (await posted.json()) as { id: string };
  await expect.poll(async () => (await status(page, id)).status, { timeout: 30_000 }).toBe("done");
  return id;
}

// the same for a short music track
async function makeTrack(page: Page, prompt: string): Promise<string> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { mode: "music", prompt, options: { duration: 30 } } });
  expect(posted.ok(), await posted.text()).toBe(true);
  const { id } = (await posted.json()) as { id: string };
  await expect.poll(async () => (await status(page, id)).status, { timeout: 45_000 }).toBe("done");
  return id;
}

// file a run in a project over the API, behind the page's back
async function file(page: Page, runId: string, projectId: string): Promise<void> {
  const filed = await page.request.put(`/api/runs/${runId}/project`, { headers: ASK, data: { project_id: projectId } });
  expect(filed.ok(), await filed.text()).toBe(true);
}

// open a card's Project drop-down and choose a project in it
async function fileFromCard(page: Page, c: Locator, project: string): Promise<void> {
  await projectButton(c).click();
  await menuItem(page, project).click();
}

// ------------------------------------------------------------------ filing, from a card
// The whole round trip on one card (criteria 146, 147, 150): the run is filed and kept in one step; the card shows the project, and its Keep is
// pressed and locked; Undo puts the run back exactly as it was (not filed, and not kept, because it was not kept before).
test("filing a run from its card keeps it and locks Keep; Undo puts it back as it was", async ({ page }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const prompt = unique("a red fox in the snow");
  const id = await makeRun(page, prompt);
  await page.goto("/");
  const c = card(page, prompt);
  await expect(projectButton(c)).toHaveText(/Add to project/);
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "false");

  await fileFromCard(page, c, "Logo");
  await expect(toastWith(page, /Filed “.*” in Logo\. It is kept\./)).toBeVisible();
  await expect(chip(c)).toContainText("Logo");
  await expect(projectButton(c)).toContainText("Logo");
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "true");
  await expect(keepButton(c)).toHaveAttribute("aria-disabled", "true");
  await expect(keepButton(c)).toHaveAttribute("title", /Kept because it is in the project “Logo”\. Take it out of the project first\./);
  expect(await status(page, id)).toMatchObject({ project_id: logo.id, pinned: true });

  // pressing the locked Keep changes nothing, on the page or on the server (it is marked aria-disabled, which Playwright will not click, but
  // a person can press it: force the click)
  await keepButton(c).click({ force: true });
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "true");
  expect(await status(page, id)).toMatchObject({ project_id: logo.id, pinned: true });

  // Undo: out of the project, and not kept again, which is what the run was before
  await toastWith(page, /Filed/).getByRole("button", { name: "Undo" }).click();
  await expect(chip(c)).toHaveCount(0);
  await expect(projectButton(c)).toHaveText(/Add to project/);
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "false");
  expect(await status(page, id)).toMatchObject({ project_id: null, pinned: false });
});

// A run that was kept before keeps being kept after Undo of its filing: Undo restores what it was, not "not kept".
test("Undo of a filing leaves a run that was kept before kept", async ({ page }) => {
  await clearHistory(page);
  await makeProject(page, "Logo");
  const prompt = unique("already kept");
  const id = await makeRun(page, prompt);
  expect((await page.request.patch(`/api/runs/${id}`, { headers: ASK, data: { pinned: true } })).ok()).toBe(true);
  await page.goto("/");
  const c = card(page, prompt);
  await fileFromCard(page, c, "Logo");
  await expect(chip(c)).toBeVisible();
  await toastWith(page, /Filed/).getByRole("button", { name: "Undo" }).click();
  await expect(chip(c)).toHaveCount(0);
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "true");
  expect(await status(page, id)).toMatchObject({ project_id: null, pinned: true });
});

// Moving a run to another project, and taking it out (criterion 149): each says so with an Undo; taking out leaves the run kept.
test("moving a run to another project and taking it out: toasts with Undo, and the run stays kept", async ({ page }) => {
  await clearHistory(page);
  const [logo, pitch] = [await makeProject(page, "Logo"), await makeProject(page, "Pitch deck")];
  const prompt = unique("a poster");
  const id = await makeRun(page, prompt);
  await file(page, id, logo.id);
  await page.goto("/");
  const c = card(page, prompt);
  await expect(chip(c)).toContainText("Logo");

  await fileFromCard(page, c, "Pitch deck");
  await expect(toastWith(page, /Moved “.*” from Logo to Pitch deck\./)).toBeVisible();
  await expect(chip(c)).toContainText("Pitch deck");
  expect((await status(page, id)).project_id).toBe(pitch.id);

  // the drop-down of a filed card ticks its project, and offers Take out of project
  await projectButton(c).click();
  await expect(menuItem(page, "Pitch deck")).toHaveAttribute("aria-checked", "true");
  await menu(page).getByRole("menuitem", { name: "Take out of project" }).click();
  await expect(toastWith(page, "Taken out of Pitch deck. It is still kept.")).toBeVisible();
  await expect(chip(c)).toHaveCount(0);
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "true");
  await expect(keepButton(c)).not.toHaveAttribute("aria-disabled", "true"); // out of the project, Keep is free again
  expect(await status(page, id)).toMatchObject({ project_id: null, pinned: true });

  await toastWith(page, /Taken out/).getByRole("button", { name: "Undo" }).click();
  await expect(chip(c)).toContainText("Pitch deck");
  expect(await status(page, id)).toMatchObject({ project_id: pitch.id, pinned: true });
});

// ------------------------------------------------------------------ a new project, right from the card
// Criterion 148: the drop-down turns into a field; Create makes the project and files the run in it; a name that cannot be used stays in the
// field with the reason, and nothing is made.
test("New project… on a card makes the project and files the run in it, and refuses bad names with the reason", async ({ page }) => {
  await clearHistory(page);
  await makeProject(page, "Logo");
  const prompt = unique("a lighthouse");
  const id = await makeRun(page, prompt);
  await page.goto("/");
  const c = card(page, prompt);

  await projectButton(c).click();
  await menu(page).getByRole("menuitem", { name: /New project/ }).click();
  const field = page.getByLabel("Name of the new project");
  await expect(field).toBeFocused();

  await field.fill("");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Give the project a name." })).toBeVisible();

  await field.fill("x".repeat(61));
  await field.press("Enter");
  await expect(page.getByRole("alert").filter({ hasText: "at most 60 characters" })).toBeVisible();

  await field.fill("logo"); // the same name as Logo, ignoring case
  await field.press("Enter");
  await expect(page.getByRole("alert").filter({ hasText: "There is a project with that name already." })).toBeVisible();
  expect((await projects(page)).map((p) => p.name)).toEqual(["Logo"]); // none of it made anything
  expect((await status(page, id)).project_id).toBeNull();

  await field.fill("  Brand   work ");
  await field.press("Enter");
  await expect(toastWith(page, /Filed “.*” in Brand work\. It is kept\./)).toBeVisible();
  await expect(chip(c)).toContainText("Brand work");
  const made = (await projects(page)).find((p) => p.name === "Brand work"); // trimmed, and its spaces made one
  expect(made).toBeTruthy();
  expect(await status(page, id)).toMatchObject({ project_id: made?.id, pinned: true });
});

// the keyboard works the drop-down: Enter opens it, the arrow keys move, Escape closes it and gives focus back to its button
test("the card's Project drop-down works from the keyboard", async ({ page }) => {
  await clearHistory(page);
  await makeProject(page, "Logo");
  await makeProject(page, "Pitch deck");
  const prompt = unique("keyboard");
  const id = await makeRun(page, prompt);
  await page.goto("/");
  const c = card(page, prompt);
  await expect(projectSelect(page).locator("option")).toHaveCount(4); // the projects have arrived: the menu takes its first focus when it opens
  await projectButton(c).focus();
  await page.keyboard.press("Enter");
  await expect(menu(page)).toBeVisible();
  await expect(menuItem(page, "Logo")).toBeFocused(); // a run in no project: the first choice
  await page.keyboard.press("ArrowDown");
  await expect(menuItem(page, "Pitch deck")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu(page)).toHaveCount(0);
  await expect(projectButton(c)).toBeFocused();
  expect((await status(page, id)).project_id).toBeNull(); // Escape filed nothing

  await page.keyboard.press("Enter");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter"); // chooses Pitch deck
  await expect(chip(c)).toContainText("Pitch deck");
  await expect(projectButton(c)).toBeFocused();
});

// ------------------------------------------------------------------ the Project drop-down in the filter bar
// Criteria 152, 153: the drop-down narrows the list, its numbers are the tab's, the counts beside Kept and Deleted follow the project, it
// combines with Kept, and the choice is remembered by the browser.
test("the Project drop-down narrows the history and the counts, combines with Kept, and is remembered", async ({ page }) => {
  await clearHistory(page);
  const [logo, pitch] = [await makeProject(page, "Logo"), await makeProject(page, "Pitch deck")];
  const [a, b, c3, d] = [unique("in logo one"), unique("in logo two"), unique("in pitch"), unique("in none")];
  const ids = [await makeRun(page, a), await makeRun(page, b), await makeRun(page, c3), await makeRun(page, d)];
  await file(page, ids[0], logo.id);
  await file(page, ids[1], logo.id);
  await file(page, ids[2], pitch.id);
  await page.goto("/");
  for (const prompt of [a, b, c3, d]) await expect(card(page, prompt)).toBeVisible(); // Any project: all of them

  // the drop-down lists the projects with how many runs each holds on this tab
  await expect(projectSelect(page).locator("option")).toHaveText(["Any project", "No project", "Logo (2)", "Pitch deck (1)"]);

  await projectSelect(page).selectOption({ label: "Logo (2)" });
  await expect(card(page, a)).toBeVisible();
  await expect(card(page, b)).toBeVisible();
  await expect(card(page, c3)).toHaveCount(0);
  await expect(card(page, d)).toHaveCount(0);
  await expect(keptOption(page)).toContainText("2"); // Kept 2: the project's own count (every filed run is kept)

  await projectSelect(page).selectOption({ label: "No project" });
  await expect(card(page, d)).toBeVisible();
  await expect(card(page, a)).toHaveCount(0);
  await expect(keptOption(page)).toContainText("0");

  // it combines with Kept (Show is a separate control): the project stays chosen
  await projectSelect(page).selectOption({ label: "Pitch deck (1)" });
  await keptOption(page).click();
  await expect(card(page, c3)).toBeVisible();
  await expect(card(page, a)).toHaveCount(0);
  await expect(projectSelect(page)).toHaveValue(pitch.id);

  // remembered across a reload, with Kept
  await page.reload();
  await expect(projectSelect(page)).toHaveValue(pitch.id);
  await expect(keptOption(page)).toBeChecked();
  await expect(card(page, c3)).toBeVisible();
  await expect(card(page, a)).toHaveCount(0);
});

// The tab's own numbers (criterion 152, 153) and the one list serving both tabs: a track filed in a project shows on the Music tab, under the
// same choice, and the drop-down's numbers there are the Music tab's.
test("one list of projects serves both tabs: a track filed in a project shows on Music under the same choice", async ({ page }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const picture = unique("a filed picture");
  const track = unique("filed track");
  await file(page, await makeRun(page, picture), logo.id);
  await file(page, await makeTrack(page, `Genre: ${track}.`), logo.id);
  await page.goto("/");
  await projectSelect(page).selectOption({ value: logo.id });
  await expect(card(page, picture)).toBeVisible();
  await musicTab(page).click();
  await expect(trackCard(page, track)).toBeVisible();
  await expect(projectSelect(page)).toHaveValue(logo.id); // the choice is for both tabs
  await expect(projectSelect(page).locator("option").nth(2)).toHaveText("Logo (1)"); // the Music tab's number
  await expect(trackCard(page, track).locator("[data-project-chip]")).toContainText("Logo");
});

// Criteria 153: a project with nothing on this tab says so, naming the tab, with a way back; No project says every run is in one.
test("a project with nothing in it says so, naming the tab, and Show any project goes back", async ({ page }) => {
  await clearHistory(page);
  const empty = await makeProject(page, "Empty one");
  const filed = await makeProject(page, "Everything");
  await file(page, await makeRun(page, unique("only run")), filed.id);
  await page.goto("/");
  await projectSelect(page).selectOption({ value: empty.id });
  await expect(page.locator(".empty-state").first()).toContainText("Nothing in “Empty one” on Images yet");
  await expect(page.locator(".empty-state").first()).toContainText("Use Add to project on a card to file one here.");
  await page.locator(".empty-state").first().getByRole("button", { name: "Show any project" }).click();
  await expect(projectSelect(page)).toHaveValue("");
  await projectSelect(page).selectOption({ label: "No project" });
  await expect(page.locator(".empty-state").first()).toContainText("Every run on this tab is in a project");
});

// While a project is chosen, a job you start stays visible with a note (it is in no project yet) and leaves when done, with a toast and a way
// back: the same rule as the Kept view (§29.3), with words that say what keeps it here.
test("generating with a project chosen: the job stays at the top with a note, and leaves when done unless filed", async ({ page }) => {
  await useOptions(page, { steps: 100, numImages: 3 }); // about three seconds of work
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const old = unique("an old filed one");
  await file(page, await makeRun(page, old), logo.id);
  await page.goto("/");
  await projectSelect(page).selectOption({ value: logo.id });
  await expect(card(page, old)).toBeVisible();

  const prompt = unique("working in a project");
  await promptBox(page).fill(prompt);
  await page.keyboard.press("Control+Enter");
  const c = card(page, prompt);
  await expect(c).toBeVisible();
  await expect(projectSelect(page)).toHaveValue(logo.id); // starting a run does not change the choice
  await expect(page.locator("article.run-card").first()).toContainText(prompt); // at the top
  await expect(c.locator("[data-note=working-in-project]")).toHaveText("Shown while it works. It stays in this view only if you add it to this project.");
  await expect(toastWith(page, /It is not in this project, so it is not in this view\./)).toBeVisible({ timeout: 30_000 });
  await expect(c).toHaveCount(0); // it is done, it is not filed: it has left the view
  await toastWith(page, /not in this project/).getByRole("button", { name: "Show all" }).click();
  await expect(projectSelect(page)).toHaveValue("");
  await expect(c).toBeVisible();
});

// ------------------------------------------------------------------ the Manage dialog
// Criterion 154 and 155: make, rename (a taken name is refused) and delete (it asks first and says the runs are kept), and deleting a project
// deletes, un-keeps and moves no run.
test("Manage: make, rename and delete projects; deleting one keeps its runs, kept and out of it", async ({ page }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const prompt = unique("a filed run");
  const id = await makeRun(page, prompt);
  await file(page, id, logo.id);
  await page.goto("/");
  await expect(chip(card(page, prompt))).toContainText("Logo");

  await bar(page).getByRole("button", { name: /Manage/ }).click();
  const manage = page.getByRole("dialog", { name: "Projects" });
  await expect(manage).toBeVisible();
  await expect(manage.locator('[data-project-row]', { hasText: "Logo" })).toContainText("1 picture");

  // make one
  await manage.getByLabel("New project").fill("Poster");
  await manage.getByRole("button", { name: "Create" }).click();
  await expect(manage.locator("[data-project-row]", { hasText: "Poster" })).toContainText("no runs");
  // a taken name, ignoring case, is refused with the reason and the name stays in the field
  await manage.getByLabel("New project").fill("LOGO");
  await manage.getByRole("button", { name: "Create" }).click();
  await expect(manage.getByRole("alert")).toHaveText("There is a project with that name already.");
  await manage.getByLabel("New project").fill("");

  // rename in place: Escape puts the old name back, Enter saves
  const logoRow = manage.locator("[data-project-row]", { hasText: "Logo" });
  await logoRow.getByRole("button", { name: "Rename Logo" }).click();
  await manage.getByLabel("New name for Logo").fill("Brand");
  await page.keyboard.press("Escape");
  await expect(manage).toBeVisible(); // Escape ended the rename, not the dialog
  await expect(logoRow).toContainText("Logo");
  await logoRow.getByRole("button", { name: "Rename Logo" }).click();
  await manage.getByLabel("New name for Logo").fill("Brand");
  await page.keyboard.press("Enter");
  await expect(manage.locator("[data-project-row]", { hasText: "Brand" })).toBeVisible();
  expect((await projects(page)).map((p) => p.name).sort()).toEqual(["Brand", "Poster"]);

  // delete: it asks, says the run is kept, and nothing is deleted until it is confirmed
  const brandRow = manage.locator("[data-project-row]", { hasText: "Brand" });
  await brandRow.getByRole("button", { name: "Delete Brand" }).click();
  await expect(brandRow).toContainText("Its 1 run (1 picture) is not deleted: it stays kept and is no longer in a project.");
  expect((await projects(page)).length).toBe(2);
  await brandRow.getByRole("button", { name: "Delete project" }).click();
  await expect(manage.locator("[data-project-row]", { hasText: "Brand" })).toHaveCount(0);
  await expect(toastWith(page, "Deleted the project “Brand”. Its runs are kept.")).toBeVisible(); // said, since the runs it held are not gone
  expect(await status(page, id)).toMatchObject({ project_id: null, pinned: true, status: "done", deleted_at: null });
  await manage.getByRole("button", { name: "Close" }).click();
  await expect(chip(card(page, prompt))).toHaveCount(0); // the card follows
  await expect(keepButton(card(page, prompt))).toHaveAttribute("aria-pressed", "true");
});

// A project this browser remembers is deleted somewhere else: the filter goes back to any project, and says so (criterion 152).
test("a remembered project that no longer exists falls back to Any project with a note", async ({ page }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const prompt = unique("a run");
  await file(page, await makeRun(page, prompt), logo.id);
  await page.goto("/");
  await projectSelect(page).selectOption({ value: logo.id });
  await expect(card(page, prompt)).toBeVisible();
  const gone = await page.request.delete(`/api/projects/${logo.id}`, { headers: ASK }); // another page, or a script
  expect(gone.ok()).toBe(true);
  await expect(toastWith(page, "That project no longer exists.")).toBeVisible();
  await expect(projectSelect(page)).toHaveValue("");
  await expect(card(page, prompt)).toBeVisible(); // the run is still there, unfiled and kept
  // and a reload does not bring the dead project back
  await page.reload();
  await expect(projectSelect(page)).toHaveValue("");
});

// ------------------------------------------------------------------ deleting a filed run, and the bin
// Criteria 156, 157: the question names the project; in Deleted the card shows the project and cannot be filed; Restore puts it back in its
// project, kept.
test("deleting a filed run: the question names the project, and Restore puts it back in it", async ({ page }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const prompt = unique("a doomed filed run");
  const id = await makeRun(page, prompt);
  await file(page, id, logo.id);
  await page.goto("/");
  await card(page, prompt).getByRole("button", { name: "Delete" }).click();
  const question = dialog(page, "Delete this run?");
  await expect(question).toContainText("It is in the project “Logo”. It moves to Deleted and stays there for 30 days; restoring it puts it back in the project.");
  await question.getByRole("button", { name: "Delete" }).click();
  await expect(card(page, prompt)).toHaveCount(0);

  await deletedOption(page).click();
  const binned = card(page, prompt);
  await expect(chip(binned)).toContainText("Logo");
  await expect(projectButton(binned)).toHaveCount(0); // a run in the bin cannot be filed
  expect(await status(page, id)).toMatchObject({ project_id: logo.id, pinned: true });
  const refused = await page.request.put(`/api/runs/${id}/project`, { headers: ASK, data: { project_id: logo.id } });
  expect(refused.status()).toBe(409);

  await binned.getByRole("button", { name: "Restore" }).click();
  await expect(toastWith(page, /It is back in the project “Logo”, and still kept\./)).toBeVisible();
  expect(await status(page, id)).toMatchObject({ project_id: logo.id, pinned: true, deleted_at: null });
});

// Empty bin deletes the whole bin, and says so while a project is chosen, using the whole bin's numbers and not the project's.
test("Empty bin while a project is chosen says it empties the whole bin", async ({ page }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const [a, b] = [await makeRun(page, unique("filed and binned")), await makeRun(page, unique("plain and binned"))];
  await file(page, a, logo.id);
  for (const id of [a, b]) expect((await page.request.post(`/api/runs/${id}/bin`, { headers: ASK })).ok()).toBe(true);
  await page.goto("/");
  await projectSelect(page).selectOption({ value: logo.id });
  await deletedOption(page).click();
  await expect(deletedOption(page)).toContainText("1"); // the project's part of the bin
  await bar(page).getByRole("button", { name: "Empty bin" }).click();
  const question = dialog(page, "Empty the bin?");
  await expect(question).toContainText("Delete 2 runs for good (2 on Images)"); // the whole bin
  await expect(question).toContainText("This is the whole bin, not only the project you are looking at.");
  await question.getByRole("button", { name: "Cancel" }).click();
  expect((await status(page, b)).deleted_at).not.toBeNull(); // nothing was deleted
});

// ------------------------------------------------------------------ what mutation checking found missing
// Each of these was a change to the page that all the scenarios above let through.

// Filing a run out of the view it is looked at in takes its card away, and the keyboard goes on from the filter bar, as it does when Keep takes a
// card out of Kept (§29.4): the focus was on the card's button, which is gone. Both ways out of a view: filing from No project, and taking out of a
// project.
test("filing a run out of No project, or taking it out of a project, moves keyboard focus to the filter bar", async ({ page }) => {
  await clearHistory(page);
  await makeProject(page, "Logo");
  const prompt = unique("leaving a view");
  await makeRun(page, prompt);
  await page.goto("/");
  await projectSelect(page).selectOption({ label: "No project" });
  const c = card(page, prompt);
  await expect(c).toBeVisible();
  await projectButton(c).focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter"); // the first choice: Logo
  await expect(c).toHaveCount(0); // it left No project
  await expect(allOption(page)).toBeFocused();

  await projectSelect(page).selectOption({ label: "Logo (1)" });
  await expect(c).toBeVisible();
  await projectButton(c).focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("End"); // the last choice: Take out of project
  await page.keyboard.press("Enter");
  await expect(c).toHaveCount(0); // it left Logo
  await expect(allOption(page)).toBeFocused();
});

// A project made, renamed or deleted on another page (or by a script) is in this page's drop-down with no reload. No run changes, so the run events
// that keep the list fresh in the scenario above do not fire: this is the `projects.changed` event on its own.
test("a project made, renamed or deleted elsewhere is in the drop-down with no reload", async ({ page }) => {
  await clearHistory(page);
  await page.goto("/");
  await expect(projectSelect(page).locator("option")).toHaveText(["Any project", "No project"]);
  const made = await makeProject(page, "Made elsewhere");
  await expect(projectSelect(page).locator("option")).toHaveText(["Any project", "No project", "Made elsewhere (0)"]);
  const renamed = await page.request.patch(`/api/projects/${made.id}`, { headers: ASK, data: { name: "Renamed elsewhere" } });
  expect(renamed.ok()).toBe(true);
  await expect(projectSelect(page).locator("option")).toHaveText(["Any project", "No project", "Renamed elsewhere (0)"]);
  expect((await page.request.delete(`/api/projects/${made.id}`, { headers: ASK })).ok()).toBe(true);
  await expect(projectSelect(page).locator("option")).toHaveText(["Any project", "No project"]);
});

// A project that was deleted on another page while the Manage dialog was open: renaming it is refused with a 404, and the dialog says so in
// its own words (the page's, not the server's "Project not found."), quietly.
test("renaming a project that is already gone says so in the dialog", async ({ page }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  await page.goto("/");
  await bar(page).getByRole("button", { name: /Manage/ }).click();
  const manage = page.getByRole("dialog", { name: "Projects" });
  await expect(manage).toBeVisible();
  await page.route(`**/api/projects/${logo.id}`, (route) =>
    route.request().method() === "PATCH"
      ? route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Project not found.", code: "not_found" }) })
      : route.continue());
  await manage.locator("[data-project-row]", { hasText: "Logo" }).getByRole("button", { name: "Rename Logo" }).click();
  await manage.getByLabel("New name for Logo").fill("Brand");
  await page.keyboard.press("Enter");
  await expect(manage.getByRole("alert")).toHaveText("That project no longer exists.");
});

// What the person has just done shows at once; the page's list of projects is read again from the server a moment after every change, and that
// answer is made slow here (6 s) to tell the two apart: a page that only waited for the server's list would still be showing the old one. The card
// names a project that was made from its own drop-down, the Manage dialog lists a new project in its place A to Z (it is made second, and
// sorts first), and a deleted project is out of the dialog.
test("a project just made or deleted is in the page's list at once, in order, without waiting for the server's list", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("at once");
  await makeRun(page, prompt);
  await page.goto("/");
  await expect(card(page, prompt)).toBeVisible();
  await page.route("**/api/projects", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await new Promise((resolve) => setTimeout(resolve, 6000));
    await route.continue().catch(() => undefined); // the test may be over by then
  });

  await projectButton(card(page, prompt)).click();
  await menu(page).getByRole("menuitem", { name: /New project/ }).click();
  await page.getByLabel("Name of the new project").fill("Zebra");
  await page.keyboard.press("Enter");
  await expect(chip(card(page, prompt))).toContainText("Zebra", { timeout: 2500 });

  await bar(page).getByRole("button", { name: /Manage/ }).click();
  const manage = page.getByRole("dialog", { name: "Projects" });
  await manage.getByLabel("New project").fill("Apple");
  await manage.getByRole("button", { name: "Create" }).click();
  await expect(manage.locator(".project-row-name")).toHaveText(["Apple", "Zebra"], { timeout: 2500 });

  const apple = manage.locator("[data-project-row]", { hasText: "Apple" });
  await apple.getByRole("button", { name: "Delete Apple" }).click();
  await apple.getByRole("button", { name: "Delete project" }).click();
  await expect(manage.locator(".project-row-name")).toHaveText(["Zebra"], { timeout: 2500 });
});

// Choosing a project: the numbers beside Kept and Deleted are the project's, and until the project's numbers have arrived the bar shows none, rather
// than the numbers of the view that was left (which would be taken for the project's, and could even say "none" for a project that has runs). The
// project's numbers are made slow (4 s) to see the wait.
test("while a project's numbers are on their way the filter bar shows no numbers, then the project's", async ({ page }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const [a, b] = [unique("filed"), unique("loose")];
  const [idA] = [await makeRun(page, a), await makeRun(page, b)];
  await file(page, idA, logo.id);
  await page.goto("/");
  await expect(bar(page).locator(".filter-count")).toHaveCount(2); // the whole history's, beside Kept and Deleted
  await page.route(/\/api\/runs\/counts\?project=/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 4000));
    await route.continue().catch(() => undefined);
  });
  await projectSelect(page).selectOption({ label: "Logo (1)" });
  await expect(bar(page).locator(".filter-count")).toHaveCount(0, { timeout: 2000 });
  await expect(bar(page).locator(".filter-count")).toHaveCount(2, { timeout: 10_000 }); // then the project's
  await expect(keptOption(page)).toContainText("1");
});

// ------------------------------------------------------------------ live, and a phone
// Criterion 160: another open page follows live (the chip, the lock, the drop-down's number) with no reload.
test("another open page follows a filing live", async ({ page, context }) => {
  await clearHistory(page);
  const logo = await makeProject(page, "Logo");
  const prompt = unique("watched from two pages");
  const id = await makeRun(page, prompt);
  await page.goto("/");
  const other = await context.newPage();
  await other.goto("/");
  await expect(card(other, prompt)).toBeVisible();
  await expect(projectSelect(other).locator("option").nth(2)).toHaveText("Logo (0)");

  await fileFromCard(page, card(page, prompt), "Logo"); // on the first page
  await expect(chip(card(other, prompt))).toContainText("Logo"); // the second page shows it
  await expect(keepButton(card(other, prompt))).toHaveAttribute("aria-disabled", "true");
  await expect(projectSelect(other).locator("option").nth(2)).toHaveText("Logo (1)");
  expect(await status(page, id)).toMatchObject({ project_id: logo.id });
  await other.close();
});

// A run filed on another page while this page's card still shows it as kept and not filed: pressing Keep on that stale card asks the server to
// stop keeping a filed run, and the server refuses (409 run_in_project). The page says why, in the server's words, instead of failing
// (criterion 150). The stale card is made the way it happens, with the server's own refusal: the request is answered as the server answers it.
test("a refusal to stop keeping a filed run is shown as the server words it, not as an error", async ({ page }) => {
  await clearHistory(page);
  const prompt = unique("a stale card");
  const id = await makeRun(page, prompt);
  expect((await page.request.patch(`/api/runs/${id}`, { headers: ASK, data: { pinned: true } })).ok()).toBe(true);
  await page.goto("/");
  const c = card(page, prompt);
  await expect(keepButton(c)).toHaveAttribute("aria-pressed", "true");
  const words = "This run is in a project, so it stays kept. Take it out of the project first.";
  await page.route(`**/api/runs/${id}`, (route) =>
    route.request().method() === "PATCH"
      ? route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: words, code: "run_in_project" }) })
      : route.continue());
  await keepButton(c).click(); // the card still thinks the run is in no project, so the button works and asks to stop keeping it
  await expect(toastWith(page, words)).toBeVisible();
  await expect(page.locator(".toast-error")).toHaveCount(0); // it is said as information, not as a failure
});

// Criterion 161: on a phone the drop-down, the filter bar with its two controls and the Manage dialog fit with no sideways scroll. The width is 320,
// the narrowest phone: at 375 the Project button sits far enough left for the menu to fit without being moved (found by mutation checking: with
// the code that keeps the menu inside the window taken out, this test passed at 375 and the menu ran 3 px off the right of a 320 screen).
test("on a phone the Project drop-down, the filter bar and the Manage dialog fit the screen", async ({ page }) => {
  const width = 320;
  await page.setViewportSize({ width, height: 700 });
  await clearHistory(page);
  await makeProject(page, "A project with a rather long name to see how it wraps");
  const prompt = unique("phone");
  await makeRun(page, prompt);
  await page.goto("/");
  const sideways = () => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(await sideways()).toBe(false);

  await projectButton(card(page, prompt)).click();
  await expect(menu(page)).toBeVisible();
  const box = await menu(page).boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= width, `the menu is inside the window: ${JSON.stringify(box)}`).toBe(true);
  expect(await sideways()).toBe(false);
  await menu(page).getByRole("menuitem", { name: /New project/ }).click();
  const field = await page.getByLabel("Name of the new project").boundingBox();
  expect(field && field.x >= 0 && field.x + field.width <= width, `the field is inside the window: ${JSON.stringify(field)}`).toBe(true);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");

  await bar(page).getByRole("button", { name: /Manage/ }).click();
  const manage = page.getByRole("dialog", { name: "Projects" });
  await expect(manage).toBeVisible();
  const dialogBox = await manage.boundingBox();
  expect(dialogBox && dialogBox.x >= 0 && dialogBox.x + dialogBox.width <= width, `the dialog is inside the window: ${JSON.stringify(dialogBox)}`).toBe(true);
  expect(await sideways()).toBe(false);
});
