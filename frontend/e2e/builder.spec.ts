// End-to-end: the Prompt builder (DESIGN.md §37, criteria 221 to 257), in a real browser against the real server with the scripted model (STUDIO_BUILDER_LLM=fake) and the fake
// pipeline. The scripted model asks two questions of an idea, one of the next message, and is finished after a third or at "Write it now" (backend/studio/builder/fake.py).
import { clearBuilder, clearHistory, expect, promptBox, test, unique, useOptions, type Locator, type Page } from "./helpers";
import { RED, png } from "./png";

const builderTab = (page: Page): Locator => page.getByRole("tab", { name: "Prompt builder" });
const imagesTab = (page: Page): Locator => page.getByRole("tab", { name: "Images" });
const idea = (page: Page): Locator => page.getByRole("textbox", { name: /What's the idea\?|Your reply/ });
const soFar = (page: Page): Locator => page.getByRole("textbox", { name: "The prompt so far" });
const toast = (page: Page, text: string | RegExp): Locator => page.getByRole("status").filter({ hasText: text });
const log = (page: Page): Locator => page.getByRole("list", { name: "Messages" });
const library = (page: Page): Locator => page.getByRole("navigation", { name: "Library" });

/**
 * Wait until a reply of the model is shown ONCE. At the hand-over from the bubble that is being written to the saved message the same words are in the list
 * twice for a moment (found in 1.20 by a whole run under load, in the page of 1.19, which was not changed). A locator that must match one element fails
 * at once when it matches two, and does not wait for the second to go; so the count is waited for first. A reply that stayed twice would still fail here.
 */
async function replied(page: Page, text: string): Promise<void> {
  const reply = log(page).getByText(text);
  await expect(reply).toHaveCount(1);
  await expect(reply).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await clearBuilder(page);
  await useOptions(page);
  await page.goto("/");
  await builderTab(page).click();
});

/** Say an idea, answer its questions with the first chip of each, and ask for the prompt now. Leaves the finished draft in "The prompt so far". */
async function finishedPrompt(page: Page, words: string) {
  await idea(page).fill(words);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await replied(page, "Lovely idea. A few questions.");
  await page.getByRole("button", { name: "Dusk", exact: true }).click();
  await page.getByRole("button", { name: "Photograph", exact: true }).click();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await replied(page, "Thank you. One more thing.");
  await page.getByRole("button", { name: "Write it now" }).click();
  await replied(page, "Here is the finished prompt.");
  await expect(soFar(page)).toHaveValue(new RegExp(words));
}

async function savePrompt(page: Page, title: string) {
  await page.getByRole("button", { name: "Save as prompt" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Title").fill(title);
  await dialog.getByRole("button", { name: /^Save/ }).click();
  await expect(toast(page, `Saved “${title}” as a prompt, version 1.`)).toBeVisible();
}

test("the tab is after Vector, is remembered, and says the builder is ready (criteria 221, 222)", async ({ page }) => {
  const names = await page.getByRole("tab").allTextContents();
  expect(names.map((n) => n.replace(/\s+/g, " ").trim().replace(/ ?●$/, ""))).toEqual(["Images", "Music", "Vector", "Prompt builder", "Speech"]);
  await expect(page.getByRole("tabpanel", { name: "Prompt builder" })).toBeVisible();
  await page.reload();
  await expect(builderTab(page)).toHaveAttribute("aria-selected", "true");
});

test("an idea becomes a saved chat with a reply, questions with chips, and a prompt that is written from the answers (criteria 224, 225, 226)", async ({ page }) => {
  const words = unique("a lighthouse in a storm");
  await finishedPrompt(page, words);
  await expect(soFar(page)).toHaveValue(new RegExp(`${words}, Dusk, Photograph`));
  await expect(page.getByText(/^\d+ \/ 8000$/)).toBeVisible(); // the length meter against the studio's limit
  // the chat is saved: it is in the list, and reopening it brings the conversation back
  await page.getByRole("radio", { name: "Chats" }).click();
  const chat = library(page).getByRole("button", { name: new RegExp(`^${words.slice(0, 20)}`, "i") });
  await expect(chat).toBeVisible();
  await page.reload();
  await builderTab(page).click();
  await library(page).getByRole("button", { name: new RegExp(`^${words.slice(0, 20)}`, "i") }).click();
  await replied(page, "Here is the finished prompt.");
});

test("a finished prompt is saved with a title, found in Prompts, and a second version is added, never over the first (criteria 229, 245)", async ({ page }) => {
  const words = unique("a harbour at dawn");
  const title = unique("Harbour");
  await finishedPrompt(page, words);
  await savePrompt(page, title);
  await library(page).getByRole("radio", { name: "Prompts" }).click();
  await expect(library(page).getByRole("button", { name: new RegExp(`^${title}`) })).toBeVisible();
  // a further message changes the words; saving them as a new version adds version 2
  await idea(page).fill("make it moodier");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(soFar(page)).toHaveValue(/make it moodier/);
  await page.getByRole("button", { name: "Save as new version" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /^Save/ }).click();
  await expect(toast(page, "Saved as version 2.")).toBeVisible();
  // saving the same words again makes none, and says so
  await page.getByRole("button", { name: "Save as new version" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /^Save/ }).click();
  await expect(toast(page, "Nothing changed.")).toBeVisible();
  await library(page).getByRole("button", { name: new RegExp(`^${title}`) }).click();
  const versions = page.getByRole("list", { name: "Versions" });
  await expect(versions.getByText(/^Version \d$/)).toHaveText(["Version 2", "Version 1"]);
  // Compare marks the changed words in text as well as colour
  await versions.getByRole("checkbox", { name: "Compare version 1" }).check();
  await versions.getByRole("checkbox", { name: "Compare version 2" }).check();
  const compared = page.getByRole("region", { name: "Version 1 compared with version 2" });
  await expect(compared.locator("ins").first()).toBeVisible();
  await expect(compared.locator("ins").first()).toHaveAttribute("aria-label", /^added: /);
  // Make this the current one copies version 1 as version 3
  await versions.getByRole("button", { name: "Make this the current one" }).last().click();
  await expect(toast(page, /as version 3/)).toBeVisible();
  await expect(versions.getByText(/^Version \d$/)).toHaveText(["Version 3", "Version 2", "Version 1"]);
});

test("Use in Images fills the prompt box and the settings that fit, shows the chip, and Undo puts the form back (criterion 235)", async ({ page }) => {
  const words = unique("a lantern on a pier");
  await finishedPrompt(page, words);
  await page.getByRole("button", { name: "Use in Images" }).click();
  await expect(imagesTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(promptBox(page)).toHaveValue(new RegExp(words));
  await expect(toast(page, "Filled the prompt, set 16:9, guidance 4 and a negative prompt.")).toBeVisible();
  await toast(page, "Filled the prompt").getByRole("button", { name: "Undo" }).click();
  await expect(promptBox(page)).toHaveValue("");
});

test("Generate now queues a run that says where it came from, and the line opens the prompt (criteria 236, 237)", async ({ page }) => {
  const words = unique("a kite over the dunes");
  const title = unique("Kite");
  await finishedPrompt(page, words);
  await savePrompt(page, title);
  await library(page).getByRole("radio", { name: "Prompts" }).click();
  await library(page).getByRole("button", { name: new RegExp(`^${title}`) }).click();
  await page.getByRole("button", { name: "Generate now" }).click();
  await expect(imagesTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(promptBox(page)).toHaveValue(""); // the form is not touched
  const run = page.locator("article.run-card", { hasText: words }).first();
  await expect(run).toBeVisible();
  const from = run.getByRole("button", { name: `From "${title}" v1` });
  await expect(from).toBeVisible();
  await from.click();
  await expect(builderTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: `Prompt “${title}”` })).toBeVisible();
  // the version shows the picture made from it
  await expect(page.getByRole("list", { name: "Pictures made from this version" }).getByRole("img").first()).toBeVisible({ timeout: 20_000 });
});

test("Quick try makes a Draft linked to the prompt, not kept or filed, and its picture shows on the version without leaving the tab (criteria 238, 239)", async ({ page }) => {
  const words = unique("a fox in the snow");
  const title = unique("Fox");
  await finishedPrompt(page, words);
  await savePrompt(page, title);
  await library(page).getByRole("radio", { name: "Prompts" }).click();
  await library(page).getByRole("button", { name: new RegExp(`^${title}`) }).click();
  await page.getByRole("button", { name: "Quick try" }).click();
  await expect(builderTab(page)).toHaveAttribute("aria-selected", "true");
  const pictures = page.getByRole("list", { name: "Pictures made from this version" });
  await expect(pictures.getByRole("img", { name: "Made from this version (quick try)" })).toBeVisible({ timeout: 20_000 });
  await expect(pictures.getByText("Draft")).toBeVisible();
  // it is a real run in the history, a Draft linked to the prompt, and it is not kept or filed (rule 4)
  const { runs } = (await (await page.request.get("/api/runs?limit=20")).json()) as { runs: { options: { draft?: boolean }; pinned: boolean; project_id: string | null; prompt_ref: { title: string } | null }[] };
  const quick = runs.filter((r) => r.options.draft && r.prompt_ref?.title === title);
  expect(quick).toHaveLength(1);
  expect(quick[0].pinned).toBe(false);
  expect(quick[0].project_id).toBeNull();
});

test("a run from a prompt in a project is filed there and kept; a quick try is not; filing later offers the earlier runs (criterion 238)", async ({ page }) => {
  const asked = { "X-Studio-Client": "1" };
  const project = (await (await page.request.post("/api/projects", { headers: asked, data: { name: unique("Harbour") } })).json()) as { id: string; name: string };
  const title = unique("Pier");
  const made = await page.request.post("/api/builder/prompts", { headers: asked, data: { title, text: "a pier at dusk", negative: null, settings: {}, note: null, source: "hand", for: "images", project_id: project.id, tags: [] } });
  expect(made.status()).toBe(201);
  const unfiled = unique("Mole");
  const loose = (await (await page.request.post("/api/builder/prompts", { headers: asked, data: { title: unfiled, text: "a mole at dawn", negative: null, settings: {}, note: null, source: "hand", for: "images", project_id: null, tags: [] } })).json()) as { id: string };
  await page.reload();
  await builderTab(page).click();
  await library(page).getByRole("radio", { name: "Prompts" }).click();
  // Generate now: filed in the prompt's project, and kept
  await library(page).getByRole("button", { name: new RegExp(`^${title}`, "i") }).click();
  await page.getByRole("button", { name: "Generate now" }).click();
  await expect(page.locator("article.run-card", { hasText: "a pier at dusk" }).first()).toBeVisible();
  // Quick try: linked, but neither filed nor kept
  await builderTab(page).click();
  await page.getByRole("button", { name: "Quick try" }).click();
  await expect.poll(async () => {
    const { runs } = (await (await page.request.get("/api/runs?limit=20")).json()) as { runs: { prompt: string }[] };
    return runs.filter((r) => r.prompt === "a pier at dusk").length;
  }).toBe(2);
  const { runs } = (await (await page.request.get("/api/runs?limit=20")).json()) as { runs: { options: { draft?: boolean }; pinned: boolean; project_id: string | null; prompt: string }[] };
  const full = runs.find((r) => r.prompt === "a pier at dusk" && !r.options.draft)!;
  const draft = runs.find((r) => r.prompt === "a pier at dusk" && r.options.draft)!;
  expect(full.project_id).toBe(project.id);
  expect(full.pinned).toBe(true);
  expect(draft.project_id).toBeNull();
  expect(draft.pinned).toBe(false);
  // a prompt filed later: its earlier runs stay where they are until the person asks
  await builderTab(page).click();
  await library(page).getByRole("button", { name: new RegExp(`^${unfiled}`, "i") }).click();
  await page.getByRole("button", { name: "Generate now" }).click();
  await expect(page.locator("article.run-card", { hasText: "a mole at dawn" }).first()).toBeVisible();
  await builderTab(page).click();
  await page.getByLabel("Project of this prompt").selectOption(project.id);
  const offer = toast(page, "Filed in a project. 1 earlier picture's run is where it was.");
  await expect(offer).toBeVisible();
  const { runs: before } = (await (await page.request.get("/api/runs?limit=20")).json()) as { runs: { project_id: string | null; prompt: string }[] };
  expect(before.find((r) => r.prompt === "a mole at dawn")!.project_id).toBeNull(); // nothing moved without the tap
  await offer.getByRole("button", { name: "Also file its 1 picture's run" }).click();
  await expect.poll(async () => {
    const { runs: after } = (await (await page.request.get("/api/runs?limit=20")).json()) as { runs: { project_id: string | null; prompt: string }[] };
    return after.find((r) => r.prompt === "a mole at dawn")!.project_id;
  }).toBe(project.id);
  expect(loose.id).toBeTruthy();
});

test("Refine in Prompt builder opens a chat about a picture with its words, ready for what is wrong (criterion 241)", async ({ page }) => {
  const words = unique("a red barn");
  await imagesTab(page).click();
  await promptBox(page).fill(words);
  await page.keyboard.press("Control+Enter");
  const run = page.locator("article.run-card", { hasText: words }).first();
  await expect(run.getByRole("button", { name: "Refine in Prompt builder" })).toBeVisible({ timeout: 20_000 });
  await run.getByRole("button", { name: "Refine in Prompt builder" }).click();
  await expect(builderTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(soFar(page)).toHaveValue(words);
  await expect(page.getByText(/Say what is wrong with this picture/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Pictures" })).toBeVisible(); // the picture, for a model that can look at pictures
  await idea(page).fill("the roof looks wrong");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await replied(page, "Lovely idea. A few questions.");
});

test("Preferences are added, told to the model (the chat says how many), switched off and deleted (criterion 233)", async ({ page }) => {
  const note = unique("natural light");
  await library(page).getByRole("radio", { name: "Preferences" }).click();
  await page.getByRole("textbox", { name: "New note" }).fill(note);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText(note, { exact: true })).toBeVisible();
  // a reply is told the notes that are on, and the chat says how many
  await library(page).getByRole("radio", { name: "Chats" }).click();
  await idea(page).fill(unique("a pond"));
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await replied(page, "Using 1 preference.");
  await library(page).getByRole("radio", { name: "Preferences" }).click();
  await page.getByRole("checkbox", { name: `Turn off: ${note}` }).click(); // (the switch moves once the server has answered, so it is clicked, not "unchecked")
  await expect(page.getByRole("checkbox", { name: `Turn on: ${note}` })).not.toBeChecked();
  await page.getByRole("button", { name: `Delete “${note}”` }).click();
  await expect(page.getByText(note, { exact: true })).toHaveCount(0);
});

test("a chat is deleted to the bin, restored from it, and deleted for good (criterion 246)", async ({ page }) => {
  const words = unique("a sleeping cat");
  await idea(page).fill(words);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await replied(page, "Lovely idea. A few questions.");
  const title = words.split(/\s+/).slice(0, 12).join(" ").slice(0, 60);
  await page.getByRole("button", { name: `Delete “${title}”` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await library(page).getByRole("radio", { name: /^Deleted/ }).click();
  await expect(library(page).getByRole("button", { name: new RegExp(`^${title}`, "i") })).toBeVisible();
  await page.getByRole("button", { name: `Restore “${title}”` }).click();
  await expect(library(page).getByRole("button", { name: new RegExp(`^${title}`, "i") })).toHaveCount(0);
  await library(page).getByRole("radio", { name: /^All/ }).click();
  await expect(library(page).getByRole("button", { name: new RegExp(`^${title}`, "i") })).toBeVisible();
  // and for good: from the bin, after asking
  await page.getByRole("button", { name: `Delete “${title}”` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await library(page).getByRole("radio", { name: /^Deleted/ }).click();
  await page.getByRole("button", { name: `Delete “${title}” forever` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete forever" }).click();
  await expect(library(page).getByRole("button", { name: new RegExp(`^${title}`, "i") })).toHaveCount(0);
  await expect(library(page).getByText("Nothing in Deleted.")).toBeVisible();
});

test("a failed reply keeps the chat and the message and offers Retry; Stop keeps what was written (criteria 228, 248)", async ({ page }) => {
  const words = unique("a quiet lake");
  await idea(page).fill(`${words} [[error:unreachable]]`);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Can't reach the Ollama server." })).toBeVisible();
  await expect(log(page).getByText(words)).toBeVisible(); // the message stays
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "New chat" }).click();
  const slow = unique("a slow river");
  await idea(page).fill(`${slow} [[slow]]`);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".builder-message.working")).toContainText("Lovely"); // some of the reply is written: that much is kept
  await page.getByRole("button", { name: /Stop/ }).click();
  await expect(log(page).locator(".badge", { hasText: "stopped" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
});

test("the model's text is shown as text, never as markup (criterion 250)", async ({ page }) => {
  await idea(page).fill(`${unique("a pier")} [[markup]]`);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const reply = log(page).getByText("<b>bold</b>");
  await expect(reply).toBeVisible();
  expect(await log(page).locator("b, script, a").count()).toBe(0);
});

test("Help me write this opens the builder For Edit with the tray's pictures, in order (criterion 243)", async ({ page }) => {
  await imagesTab(page).click();
  await page.getByRole("radio", { name: "Edit", exact: true }).click();
  await page.locator('input[type="file"]').first().setInputFiles([
    { name: "one.png", mimeType: "image/png", buffer: png(120, 90, RED) }, { name: "two.png", mimeType: "image/png", buffer: png(90, 120, RED) },
  ]);
  await expect(page.locator(".tray-progress")).toHaveCount(0, { timeout: 20_000 });
  await page.getByRole("button", { name: "Help me write this" }).click();
  await expect(builderTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("list", { name: "Pictures" }).getByRole("listitem")).toHaveCount(2);
  await expect(page.getByText(/numbered in order: image 1, image 2/)).toBeVisible();
  await expect(page.getByRole("combobox", { name: "What the prompt is for" })).toHaveValue("edit");
});

test("on a 360 px phone the tab has no sideways scroll and the three views are one column (criterion 221)", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.reload();
  await builderTab(page).click();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const views = page.getByRole("radiogroup", { name: "Show" }).first();
  await expect(views.getByRole("radio", { name: "Chat" })).toBeVisible();
  await views.getByRole("radio", { name: "Library" }).click();
  await expect(library(page)).toBeVisible();
  await views.getByRole("radio", { name: "Prompt" }).click();
  await expect(soFar(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
