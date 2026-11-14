// Release 1.25, "Audiobook polish for Speech" (DESIGN.md §44), in a real browser against the real server with the fake speech engines: the pronunciation list, the audiobook
// (M4B) button, and several voices in one text. The unit tests try each rule against stand-ins; here the whole path is walked as a person walks it, at a desktop width and at
// 360 px, and each screen is checked not to run off the side of the page.
//
// Set STUDIO_SHOTS_DIR to have a screenshot of each screen written there (the build's own check does not need them).
import { clearHistory, expect, test, unique, type Locator, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
const speechTab = (page: Page) => page.getByRole("tab", { name: "Speech" });
const readingCard = (page: Page, title: string): Locator => page.locator("article.reading-card", { hasText: title });
const player = (page: Page, title: string): Locator => page.locator("section.reading-view", { has: page.getByRole("heading", { name: title }) });
const shots = process.env.STUDIO_SHOTS_DIR;

const LONG = "Nguyen walked all the way home from the harbour, and Nguyen's dog ran on ahead of him.";
// A script: a heading, a line of narration, two names with two lines each (the fake engine joins anything under 40 characters, so each line is longer)
const NARRATION = "The sea went on all night, and nobody on the quay spoke of it until morning.";
const SCRIPT = [
  "# The Gate",
  "",
  NARRATION,
  "",
  "Alice: Did you hear the sea last night? It was loud enough to wake the whole house.",
  "Bob: I did, and I did not sleep at all, not for a single minute of the night.",
  "Alice: Then we should go down to the quay and look at what it has left behind.",
  "Bob: Yes, and quickly, before the tide comes back in again.",
].join("\n");

async function openSpeech(page: Page) {
  await page.goto("/");
  await speechTab(page).click();
  await expect(page.getByRole("heading", { name: "Voice", exact: true })).toBeVisible();
}

/** Nothing runs off the side of the page: what a phone's person would have to scroll sideways for. */
async function noSidewaysScroll(page: Page, what: string) {
  const wide = await page.evaluate(() => [document.documentElement.scrollWidth - window.innerWidth, ...[...document.querySelectorAll<HTMLElement>("[role=dialog]")].map((d) => d.scrollWidth - d.clientWidth)]);
  expect(Math.max(...wide), `${what} is wider than the screen`).toBeLessThanOrEqual(0);
}

async function shot(page: Page, name: string) {
  if (shots) await page.screenshot({ path: `${shots}/${name}-${page.viewportSize()?.width}.png`, fullPage: true });
}

/** Paste a text, prepare it, title it and press Make reading; the card it makes. */
async function makeReading(page: Page, text: string, title: string, beforeMake?: () => Promise<void>): Promise<Locator> {
  await page.getByLabel("Paste the text").fill(text);
  await page.getByRole("button", { name: "Prepare text" }).click();
  await page.getByLabel("Title of the reading").fill(title);
  await beforeMake?.();
  await expect(page.getByRole("button", { name: "Make reading" })).toBeEnabled({ timeout: 15_000 });
  await page.getByRole("button", { name: "Make reading" }).click();
  const card = readingCard(page, title).first();
  await expect(card).toBeVisible();
  return card;
}

const done = (card: Locator) => expect(card.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });

/** The list and the voices the tests make live in the server's database: one left by a test would be in the next test's way. */
async function clearLists(page: Page) {
  const { entries } = (await (await page.request.get("/api/speech/pronunciations")).json()) as { entries: { id: string }[] };
  for (const entry of entries) await page.request.delete(`/api/speech/pronunciations/${entry.id}`, { headers: ASK });
  const { saved } = (await (await page.request.get("/api/speech/engines/fake/voices")).json()) as { saved: { id: string }[] };
  for (const voice of saved) await page.request.delete(`/api/speech/voices/${voice.id}`, { headers: ASK });
}

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await clearLists(page);
});
test.afterEach(async ({ page }) => {
  await clearLists(page);
});

// ------------------------------------------------------------------ the pronunciation list (§44.3)
for (const [width, height] of [[1280, 900], [360, 780]] as const) {
  test(`the pronunciation list is made in a dialog, tried, used by a reading and said on its card (${width} px)`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openSpeech(page);
    await page.getByRole("button", { name: "Pronunciation list", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Pronunciation list" });
    await expect(dialog).toBeVisible();

    await dialog.getByLabel("Word or phrase").fill("Nguyen");
    await dialog.getByLabel("Said like").fill("win");
    await dialog.getByRole("button", { name: "Add", exact: true }).click();
    await expect(dialog.getByText("Added “Nguyen”.")).toBeVisible();
    await expect(dialog.getByRole("list", { name: "Your pronunciation list" }).getByText("Nguyen")).toBeVisible();

    // Try it shows what would be spoken and which entry changed it, and changes nothing that is stored
    await dialog.getByLabel("A sentence to try").fill("Nguyen walked home.");
    await dialog.getByRole("button", { name: "Show how it will be said" }).click();
    await expect(dialog.locator(".pron-spoken")).toHaveText("win walked home.");
    await expect(dialog.getByRole("list", { name: "The entries that changed it" })).toContainText("Nguyen → win");
    await noSidewaysScroll(page, "the pronunciation dialog");
    await shot(page, "pronunciation-dialog");
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("button", { name: "Pronunciation list (1)" })).toBeVisible();

    // a reading made with the list says so on its card, and shows the words as they were written
    const title = unique("Harbour");
    const card = await makeReading(page, LONG, title);
    await done(card);
    await expect(card.getByText("Pronunciation list: 1 entry, 2 words said your way.")).toBeVisible();
    await card.getByRole("button", { name: "Listen" }).click();
    const view = player(page, title);
    await expect(view.getByText(LONG, { exact: true })).toBeVisible(); // the read-along shows "Nguyen", not "win"
    await noSidewaysScroll(page, "the card and the player");
    await shot(page, "pronunciation-card");
  });
}

// ------------------------------------------------------------------ the audiobook (§44.4)
for (const [width, height] of [[1280, 900], [360, 780]] as const) {
  test(`the audiobook button builds one file with the chapters, or says why it cannot (${width} px)`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openSpeech(page);
    const title = unique("Audiobook");
    const card = await makeReading(page, `${NARRATION}\n\n${LONG}`, title);
    await done(card);
    const caps = (await (await page.request.get("/api/capabilities")).json()) as { speech: { audiobook: { available: boolean; reason: string | null } } };
    const button = card.getByRole("button", { name: /^Make audiobook \(M4B\)/ });
    await expect(button).toBeVisible();

    if (!caps.speech.audiobook.available) {
      // this server's ffmpeg cannot make the file: the button is off, says why in words, and the ZIP (parts and chapter lists) is unaffected
      await expect(button).toHaveAttribute("aria-disabled", "true");
      await expect(card.getByText(caps.speech.audiobook.reason ?? "cannot be made")).toBeVisible();
      await expect(card.getByRole("link", { name: /ZIP|All parts/ }).first()).toBeVisible();
      await shot(page, "audiobook-unavailable");
      return;
    }
    await button.click();
    const link = card.getByRole("link", { name: /^Download audiobook \(M4B/ });
    await expect(link).toBeVisible({ timeout: 60_000 });
    await expect(card.getByText(/1 chapter, one for each part/)).toBeVisible();
    await noSidewaysScroll(page, "the card with the audiobook");
    await shot(page, "audiobook-ready");

    // the link is a real file: an MP4 audiobook (its type box says so) that carries a chapter list, sent as a download
    const href = (await link.getAttribute("href")) as string;
    const file = await page.request.get(href);
    expect(file.status()).toBe(200);
    expect(file.headers()["content-disposition"]).toMatch(/attachment/);
    const bytes = await file.body();
    expect(bytes.subarray(4, 12).toString("latin1")).toBe("ftypM4B ");
    expect(bytes.includes(Buffer.from("chpl"))).toBe(true);

    // the player has the same button; nothing is asked of the server until it is pressed, and then the file that is already made is offered at once (no second build)
    await card.getByRole("button", { name: "Listen" }).click();
    const view = player(page, title);
    await view.getByRole("button", { name: /^Make audiobook \(M4B\)/ }).click();
    await expect(view.getByRole("link", { name: /^Download audiobook \(M4B/ })).toBeVisible({ timeout: 10_000 });
    expect(await view.getByRole("link", { name: /^Download audiobook \(M4B/ }).getAttribute("href")).toBe(href); // the same file
  });
}

// ------------------------------------------------------------------ several voices in one text (§44.5)
for (const [width, height] of [[1280, 900], [360, 780]] as const) {
  test(`a script is read in several voices: the names are found, each is given a voice, and the card and the player say who speaks (${width} px)`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openSpeech(page);
    await expect(page.getByRole("checkbox", { name: /Find speakers/ })).toBeChecked();
    await page.getByLabel("Paste the text").fill(SCRIPT);
    await page.getByRole("button", { name: "Prepare text" }).click();
    const speakers = page.getByRole("region", { name: /Speakers found/ });
    await expect(speakers.getByRole("heading", { name: "Speakers found (2)" })).toBeVisible();
    await expect(speakers.getByText("2 lines")).toHaveCount(2);
    await expect(speakers.getByText(/Did you hear the sea last night/)).toBeVisible(); // the first line of Alice, as a sample

    // in the text of the part the speaker's paragraph shows the name beside it, without the name in the words
    await page.getByRole("button", { name: "Show text" }).first().click();
    await expect(page.locator(".speech-paragraph-speaker", { hasText: "Alice" }).first()).toBeVisible();
    await expect(page.getByRole("textbox", { name: /^Paragraph \d+ of part 1$/ }).filter({ hasText: /^Did you hear the sea last night/ })).toHaveCount(1);

    // each name is given a voice; the voices on offer are the ones the engine can use together with the main voice
    await page.getByRole("radio", { name: /^Anna$/ }).check();
    await speakers.getByLabel("Voice for Alice").selectOption({ label: "Bea" });
    await expect(speakers.getByLabel("Voice for Bob")).toHaveValue(""); // no voice chosen: the main voice
    await noSidewaysScroll(page, "the list of speakers");
    await shot(page, "speakers-review");

    const title = unique("The Gate");
    await page.getByLabel("Title of the reading").fill(title);
    await expect(page.getByRole("button", { name: "Make reading" })).toBeEnabled({ timeout: 15_000 });
    await page.getByRole("button", { name: "Make reading" }).click();
    const card = readingCard(page, title).first();
    await done(card);
    await expect(card.getByText("Speakers: Alice in Bea, Bob in the main voice.")).toBeVisible();

    await card.getByRole("button", { name: "Listen" }).click();
    const view = player(page, title);
    await expect(view.locator(".piece-speaker", { hasText: "Alice" })).toHaveCount(2);
    await expect(view.locator(".piece-speaker", { hasText: "Bob" })).toHaveCount(2);
    await expect(view.getByText(NARRATION, { exact: true })).toBeVisible(); // the narrator's paragraph has no name
    await noSidewaysScroll(page, "the player with the speakers");
    await shot(page, "speakers-player");
  });
}

test("a name that is turned off is read as written, and a text with no speakers shows no list", async ({ page }) => {
  await openSpeech(page);
  await page.getByLabel("Paste the text").fill(SCRIPT);
  await page.getByRole("button", { name: "Prepare text" }).click();
  const speakers = page.getByRole("region", { name: /Speakers found/ });
  await speakers.getByRole("checkbox", { name: /Read Bob as a speaker/ }).uncheck();
  await expect(speakers.getByLabel("Voice for Bob")).toBeDisabled();
  const title = unique("Off");
  await page.getByLabel("Title of the reading").fill(title);
  await page.getByRole("button", { name: "Make reading" }).click();
  const card = readingCard(page, title).first();
  await done(card);
  await expect(card.getByText("Speakers: Alice in the main voice.")).toBeVisible();
  await card.getByRole("button", { name: "Listen" }).click();
  const view = player(page, title);
  await expect(view.getByText(/^Bob: I did, and I did not sleep at all/)).toBeVisible(); // as written, name and all
  await expect(view.locator(".piece-speaker", { hasText: "Bob" })).toHaveCount(0);

  // the switch off: the same text is prepared with no speakers, and the lines are not split apart
  await page.getByRole("checkbox", { name: /Find speakers/ }).uncheck();
  await page.getByRole("button", { name: "Prepare text" }).click();
  await page.getByRole("button", { name: "Replace the review" }).click(); // the choice just made is the person's work: asked before it is thrown away
  await expect(page.getByRole("region", { name: /Speakers found/ })).toHaveCount(0);
});
