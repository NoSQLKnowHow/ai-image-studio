// The Speech tab (DESIGN.md §38, criteria 258-294), in a real browser against the real server with the fake speech engines (a tone for a voice, a piece made much
// faster than real time). Everything the unit tests could only try against stand-ins for the audio elements is tried here against a real one: that a reading
// plays, that the spoken paragraph moves, that a reading still being made is played as it comes, and that a place stopped at survives a reload.
import { clearHistory, expect, test, unique, type Locator, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
const speechTab = (page: Page) => page.getByRole("tab", { name: "Speech" });
const readingCard = (page: Page, title: string): Locator => page.locator("article.reading-card", { hasText: title });
const player = (page: Page, title: string): Locator => page.locator("section.reading-view", { has: page.getByRole("heading", { name: title }) });

// three paragraphs, each long enough to be a piece of its own for the fake engine (it joins any under 40 characters)
const THREE = [
  "The first paragraph of this test reading is long enough to stand on its own.",
  "The second paragraph follows it, and is also long enough to be a piece of its own.",
  "The third paragraph ends the reading, and is long enough as well for this test.",
];

async function openSpeech(page: Page) {
  await page.goto("/");
  await speechTab(page).click();
  await expect(page.getByRole("heading", { name: "Voice", exact: true })).toBeVisible();
}

/** Paste the text, prepare it, give the reading a title and press Make reading; the card it makes. */
async function makeReading(page: Page, title: string, paragraphs: string[] = THREE): Promise<Locator> {
  await page.getByLabel("Paste the text").fill(paragraphs.join("\n\n"));
  await page.getByRole("button", { name: "Prepare text" }).click();
  await page.getByLabel("Title of the reading").fill(title);
  await expect(page.getByRole("button", { name: "Make reading" })).toBeEnabled({ timeout: 15_000 });
  await page.getByRole("button", { name: "Make reading" }).click();
  const card = readingCard(page, title).first();
  await expect(card).toBeVisible();
  return card;
}

const done = (card: Locator) => expect(card.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });

/** What the audio elements of a reading's player are doing: the first that has something loaded. */
const sound = (page: Page) => page.evaluate(() => {
  const loaded = [...document.querySelectorAll<HTMLAudioElement>(".reading-view audio")].filter((a) => a.getAttribute("src"));
  return loaded.map((a) => ({ src: a.getAttribute("src"), time: a.currentTime, paused: a.paused }));
});

/** The voices the person made, deleted over the API: they live in the server's database, so one left by a test would be in the next test's list. */
async function clearVoices(page: Page) {
  const { saved } = (await (await page.request.get("/api/speech/engines/fake/voices")).json()) as { saved: { id: string }[] };
  for (const voice of saved) await page.request.delete(`/api/speech/voices/${voice.id}`, { headers: ASK });
}

/** A mono 16-bit WAV of a quiet tone: the recording a person would upload (long enough, not silent, not clipped). */
function recording(seconds: number, rate = 24000): Buffer {
  const count = Math.floor(seconds * rate);
  const data = Buffer.alloc(count * 2);
  for (let i = 0; i < count; i++) data.writeInt16LE(Math.round(9000 * Math.sin((2 * Math.PI * 220 * i) / rate) * (0.6 + 0.4 * Math.sin((2 * Math.PI * 3 * i) / rate))), i * 2);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0); header.writeUInt32LE(36 + data.length, 4); header.write("WAVE", 8); header.write("fmt ", 12);
  header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20); header.writeUInt16LE(1, 22); header.writeUInt32LE(rate, 24); header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32); header.writeUInt16LE(16, 34); header.write("data", 36); header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await clearVoices(page);
});

// ------------------------------------------------------------------ the tab, and a reading made and played
test("the Speech tab is the fifth and last, is remembered, and has the three ways of giving it a text", async ({ page }) => {
  await page.goto("/");
  const tabs = page.getByRole("tablist").getByRole("tab");
  await expect(tabs).toHaveText(["Images", "Music", "Vector", "Prompt builder", "Speech"]);
  await speechTab(page).click();
  await expect(page.getByRole("radiogroup", { name: "Where the text comes from" })).toBeVisible();
  await page.reload();
  await expect(speechTab(page)).toHaveAttribute("aria-selected", "true"); // remembered
});

test("a reading is made from pasted text, plays from its card, and the spoken paragraph moves as it plays", async ({ page }) => {
  await openSpeech(page);
  const title = unique("Lighthouse");
  const card = await makeReading(page, title);
  await done(card);
  await expect(card.getByText(/^All 1 part · paragraph 3 of 3 · /)).toBeVisible();

  await card.getByRole("button", { name: "Listen" }).click();
  const view = player(page, title);
  await expect(view).toBeVisible();
  await expect(view.getByRole("button", { name: "Pause" })).toBeVisible(); // Listen starts it: the press is the gesture a browser wants

  // a real audio element is playing the whole part, and the clock moves
  await expect.poll(async () => (await sound(page)).some((s) => !s.paused && s.time > 0.3 && /\/parts\/0\.wav/.test(s.src ?? "")), { timeout: 15_000 }).toBe(true);
  const first = view.getByText(THREE[0], { exact: true });
  await expect(first).toHaveAttribute("aria-current", "true");
  // and the paragraph being spoken moves on by itself
  await expect(view.getByText(THREE[1], { exact: true })).toHaveAttribute("aria-current", "true", { timeout: 15_000 });
  await expect(first).not.toHaveAttribute("aria-current", "true");
});

test("pressing a paragraph plays from there, and the slider and the buttons move about in the part", async ({ page }) => {
  await openSpeech(page);
  const title = unique("Harbour");
  const card = await makeReading(page, title);
  await done(card);
  await card.getByRole("button", { name: "Listen" }).click();
  const view = player(page, title);
  await expect(view.getByRole("button", { name: "Pause" })).toBeVisible();
  await view.getByText(THREE[2], { exact: true }).click();
  await expect(view.getByText(THREE[2], { exact: true })).toHaveAttribute("aria-current", "true");
  await view.getByRole("button", { name: "Back 15 seconds" }).click();
  await expect(view.getByText(THREE[0], { exact: true })).toHaveAttribute("aria-current", "true");
  const slider = view.getByRole("slider", { name: "Position in this part" });
  await expect(slider).toBeEnabled();
  const length = Number(await slider.getAttribute("max"));
  expect(length).toBeGreaterThan(2);
  await slider.fill(String(length / 2));
  await expect.poll(async () => (await sound(page))[0]?.time ?? 0).toBeGreaterThan(length / 2 - 0.5);
});

test("the keys: Space plays and pauses, K and J go by paragraph", async ({ page }) => {
  await openSpeech(page);
  const title = unique("Keys");
  const card = await makeReading(page, title);
  await done(card);
  await card.getByRole("button", { name: "Listen" }).click();
  const view = player(page, title);
  await expect(view.getByRole("button", { name: "Pause" })).toBeVisible();
  await view.getByRole("region", { name: "Text of this part" }).focus();
  await page.keyboard.press("Space");
  await expect(view.getByRole("button", { name: "Play", exact: true })).toBeVisible();
  await expect.poll(async () => (await sound(page)).every((s) => s.paused)).toBe(true);
  await page.keyboard.press("k");
  await expect(view.getByText(THREE[1], { exact: true })).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("k");
  await expect(view.getByText(THREE[2], { exact: true })).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("j");
  await expect(view.getByText(THREE[1], { exact: true })).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("Space");
  await expect(view.getByRole("button", { name: "Pause" })).toBeVisible();
});

test("a place stopped at is kept on the server: after a reload the card offers to continue from it", async ({ page }) => {
  await openSpeech(page);
  const title = unique("Place");
  const card = await makeReading(page, title);
  await done(card);
  await card.getByRole("button", { name: "Listen" }).click();
  const view = player(page, title);
  await view.getByText(THREE[1], { exact: true }).click(); // into the second paragraph, which starts well after a second
  await expect.poll(async () => (await sound(page))[0]?.time ?? 0, { timeout: 15_000 }).toBeGreaterThan(1.5);
  const reported = page.waitForResponse((r) => /\/api\/readings\/[^/]+\/position$/.test(r.url()) && r.request().method() === "PUT");
  await view.getByRole("button", { name: "Pause" }).click();
  expect((await reported).status()).toBeLessThan(300);
  await page.reload();
  const again = readingCard(page, title).first();
  await expect(again.getByRole("button", { name: "Continue listening" })).toBeVisible();
  await expect(again.getByText(/^Continue listening from \d+:\d\d in part 1$/)).toBeVisible();
});

test("a reading that is still being made is played as it comes, waits for the next paragraph, and goes on by itself", async ({ page }) => {
  await openSpeech(page);
  const title = unique("Slow");
  // the paragraphs after the first each take two seconds to make: the first is there at once, and the listener catches up with the maker, more than once
  const slow = [`${THREE[1]} [fake:slow]`, `${THREE[2]} [fake:slow]`, "A fourth paragraph, long enough for a piece of its own, and slow to make as well. [fake:slow]"];
  const card = await makeReading(page, title, [THREE[0], ...slow]);
  await expect(card.getByRole("button", { name: "Listen" })).toBeVisible({ timeout: 15_000 }); // something is made
  await card.getByRole("button", { name: "Listen" }).click();
  const view = player(page, title);
  await expect(view.getByText(THREE[0], { exact: true })).toHaveAttribute("aria-current", "true", { timeout: 10_000 });
  // it plays the first piece on its own (the part is not complete), and reaches the end of what is made before the next is: the wait is a moment, so the page is
  // watched on every frame and not at the intervals a locator backs off to
  await page.waitForFunction(() => document.querySelector(".reading-view-status")?.textContent?.includes("Waiting for the next paragraph to be made…"), undefined, { polling: "raf", timeout: 15_000 });
  await expect(view.getByRole("button", { name: "Pause" })).toBeVisible(); // still "playing"
  // and goes on without being touched, into the paragraph that was made meanwhile
  await expect(view.getByText(new RegExp(`^${THREE[1]}`))).toHaveAttribute("aria-current", "true", { timeout: 15_000 });
  await done(card);
  // when the part is complete it carries on in the whole file
  await expect.poll(async () => (await sound(page)).some((s) => /\/parts\/0\.wav/.test(s.src ?? "")), { timeout: 20_000 }).toBe(true);
});

// ------------------------------------------------------------------ voices of one's own
test("a voice is designed: three samples are made, one is kept under a name, chosen, renamed and deleted", async ({ page }) => {
  await openSpeech(page);
  const name = unique("Warm");
  await page.getByRole("button", { name: "Design a voice" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Design a voice" })).toBeVisible();
  await dialog.getByLabel("Describe the voice").fill("a warm Irish woman in her forties, unhurried and clear");
  await dialog.getByRole("button", { name: "Make three samples" }).click();
  await expect(dialog.getByRole("radio", { name: "Sample 3" })).toBeVisible({ timeout: 30_000 });
  await dialog.getByRole("button", { name: "Play sample 2" }).click();
  await expect(dialog.getByRole("button", { name: "Stop sample 2" })).toBeVisible();
  await dialog.getByRole("button", { name: "Stop sample 2" }).click();
  await expect(dialog.getByRole("button", { name: "Save this voice" })).toBeDisabled(); // nothing chosen, no name
  await dialog.getByRole("radio", { name: "Sample 2" }).check();
  await dialog.getByLabel("Name this voice").fill(name);
  await dialog.getByRole("button", { name: "Save this voice" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("radio", { name })).toBeChecked(); // the voice just made is the voice chosen

  const renamed = `${name} again`;
  await page.getByRole("button", { name: `Rename ${name}` }).click();
  await page.getByRole("textbox", { name: `New name for ${name}` }).fill(renamed);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("radio", { name: renamed })).toBeVisible();
  await expect(page.getByRole("button", { name: `Rename ${renamed}` })).toBeFocused(); // back where it was

  await page.getByRole("button", { name: `Delete ${renamed}` }).click();
  await expect(page.getByRole("alertdialog")).toContainText(`Delete the voice “${renamed}”?`);
  await page.getByRole("button", { name: "Delete voice" }).click();
  await expect(page.getByRole("radio", { name: renamed })).toHaveCount(0);
});

test("a voice is added from a recording: checked by the server, heard back, and kept only with the statement and a name", async ({ page }) => {
  await openSpeech(page);
  const name = unique("Mine");
  await page.getByRole("button", { name: "Add my voice" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(/On a quiet morning the town wakes slowly/)).toBeVisible(); // the passage to read aloud
  const file = dialog.locator('input[type="file"]');

  // a file of the wrong kind is refused before anything is sent
  await file.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("not a recording") });
  await expect(dialog.getByRole("alert")).toContainText("is not a kind of recording that can be read");

  // one that is too short is refused by the server, in its words
  await file.setInputFiles({ name: "short.wav", mimeType: "audio/wav", buffer: recording(3) });
  await expect(dialog.getByRole("alert")).toContainText(/short|seconds/i, { timeout: 20_000 });

  // a good one is kept, can be heard back, and the studio can be heard speaking in it
  await file.setInputFiles({ name: "me.wav", mimeType: "audio/wav", buffer: recording(10) });
  await expect(dialog.getByText(/^1\d s long|^10 s long/)).toBeVisible({ timeout: 20_000 });
  await dialog.getByRole("button", { name: "Hear your recording" }).click();
  await expect(dialog.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Stop", exact: true }).click();
  const asked = page.waitForRequest((r) => r.url().endsWith("/api/speech/samples") && r.method() === "POST");
  await dialog.getByRole("button", { name: "Play sample of the new voice" }).click();
  expect(JSON.parse((await asked).postData() ?? "{}")).toMatchObject({ engine: "fake" });
  await expect.poll(() => page.evaluate(() => [...document.querySelectorAll<HTMLAudioElement>(".voice-dialog audio")].some((a) => (a.getAttribute("src") ?? "").includes("/api/speech/samples/"))), { timeout: 30_000 }).toBe(true);

  // nothing is saved without the statement, nor without a name
  const save = dialog.getByRole("button", { name: "Save this voice" });
  await dialog.getByLabel("Name this voice").fill(name);
  await expect(save).toBeDisabled();
  const statement = dialog.getByRole("checkbox");
  await expect(statement).not.toBeChecked();
  await statement.check();
  await expect(save).toBeEnabled();
  await save.click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("radio", { name })).toBeChecked();
  await expect(page.getByText("Cloned from a recording", { exact: false }).first()).toBeVisible();
});

// ------------------------------------------------------------------ the history, the download and the bin
test("a reading can be downloaded as a ZIP, deleted to the bin and restored", async ({ page }) => {
  await openSpeech(page);
  const title = unique("Bin");
  const card = await makeReading(page, title);
  await done(card);

  const link = card.getByRole("link", { name: /Download/ });
  const zip = await page.request.get((await link.getAttribute("href")) as string);
  expect(zip.status()).toBe(200);
  expect(zip.headers()["content-type"]).toContain("zip");
  expect((await zip.body()).subarray(0, 2).toString()).toBe("PK"); // a real ZIP

  await card.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: /^Delete/ }).last().click();
  await expect(readingCard(page, title)).toHaveCount(0);
  await page.getByRole("radio", { name: /^Deleted/ }).first().click();
  const binned = readingCard(page, title).first();
  await expect(binned).toBeVisible();
  await binned.getByRole("button", { name: "Restore" }).click();
  await page.getByRole("radio", { name: /^All/ }).first().click();
  await expect(readingCard(page, title).first()).toBeVisible();
});

// ------------------------------------------------------------------ a phone
test("on a 360 px phone the tab, the voices, the cards and the player fit without a sideways scroll, and the player's buttons stay on screen", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await openSpeech(page);
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(await overflow()).toBeLessThanOrEqual(0);

  const title = unique("Phone");
  const card = await makeReading(page, title);
  await done(card);
  expect(await overflow()).toBeLessThanOrEqual(0);

  await card.getByRole("button", { name: "Listen" }).click();
  const view = player(page, title);
  await expect(view).toBeVisible();
  for (const name of ["Previous paragraph", "Back 15 seconds", "Pause", "Forward 15 seconds", "Next paragraph"]) {
    await expect(view.getByRole("button", { name })).toBeInViewport();
  }
  expect(await overflow()).toBeLessThanOrEqual(0);

  await page.getByRole("button", { name: "Design a voice" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(await overflow()).toBeLessThanOrEqual(0);
});
