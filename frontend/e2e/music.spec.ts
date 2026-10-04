// The Music tab (DESIGN.md §26, acceptance 61-75), in a real browser against the real server with the fake music
// pipeline (a short tune of sine tones, whatever length is asked for). The server is shared by every test, so a test
// that cares about the model first puts it in the state it needs by asking the API.
import { clearHistory, expect, generate, test, unique, useOptions, type Locator, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
const pill = (page: Page) => page.locator('button[aria-controls="model-details"]');
const musicTab = (page: Page) => page.getByRole("tab", { name: "Music" });
const imagesTab = (page: Page) => page.getByRole("tab", { name: "Images" });
const form = (page: Page) => page.getByRole("region", { name: "New music" });
const make = (page: Page) => page.getByRole("button", { name: "Make music", exact: true });
const description = (page: Page) => form(page).getByLabel("Description sent to the model");
const trackCard = (page: Page, text: string): Locator => page.locator("article.music-card", { hasText: text });
const announcement = (page: Page) => page.locator("header p[role=status]");
const loadMusic = (page: Page) => page.getByRole("button", { name: "Load music model", exact: true });
const loadImage = (page: Page) => page.getByRole("button", { name: "Load model", exact: true });
const unloadButton = (page: Page) => page.getByRole("button", { name: "Unload model", exact: true });

/** Unload whatever is loaded over the API, waiting out a run that an earlier test left going. */
async function unloaded(page: Page) {
  await expect.poll(async () => (await page.request.post("/api/model/unload", { headers: ASK })).status(), { timeout: 30_000 }).toBe(200);
}

async function openMusic(page: Page) {
  await page.goto("/");
  await musicTab(page).click();
  await expect(form(page)).toBeVisible();
}

/** Describe something by its genre and press Make music; the card it makes. */
async function makeMusic(page: Page, genre: string): Promise<Locator> {
  await form(page).getByLabel("Genre").fill(genre);
  await make(page).click();
  const c = trackCard(page, genre).first();
  await expect(c).toBeVisible();
  return c;
}

const done = (c: Locator) => expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await useOptions(page);
});

test("Images and Music tabs: the arrow keys switch, only the open one is shown, and the choice is remembered", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("tablist", { name: "What to make" })).toBeVisible();
  await expect(imagesTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(musicTab(page)).toHaveAttribute("tabindex", "-1"); // one tab stop for the whole bar
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toBeVisible();
  await expect(form(page)).toBeHidden();

  await imagesTab(page).focus();
  await page.keyboard.press("ArrowRight");
  await expect(musicTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(musicTab(page)).toBeFocused();
  await expect(form(page)).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toBeHidden();
  await expect(page.getByRole("tabpanel", { name: "Music" })).toBeVisible();

  await page.reload();
  await expect(musicTab(page)).toHaveAttribute("aria-selected", "true"); // remembered
  await expect(form(page)).toBeVisible();

  await musicTab(page).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(imagesTab(page)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  await expect(musicTab(page)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Home");
  await expect(imagesTab(page)).toHaveAttribute("aria-selected", "true");
});

test("each tab shows only its own runs", async ({ page }) => {
  await page.goto("/");
  const picture = await generate(page, unique("a red kite"));
  await expect(picture.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await musicTab(page).click();
  const genre = unique("ambient");
  const track = await makeMusic(page, genre);
  await done(track);
  await expect(page.locator("article.run-card:visible")).toHaveCount(1); // only the track on this tab
  await expect(page.locator("article.music-card:visible")).toHaveCount(1);
  await imagesTab(page).click();
  await expect(page.locator("article.run-card:visible")).toHaveCount(1);
  await expect(page.locator("article.music-card:visible")).toHaveCount(0);
  await expect(card(page, "a red kite")).toBeVisible();
});
const card = (page: Page, text: string): Locator => page.locator("article.run-card", { hasText: text }).first();

test("an instrumental track: the fields write the description, no lyrics are sent, and the card has a player that seeks and a download", async ({ page }) => {
  await openMusic(page);
  await expect(page.getByRole("switch", { name: "Add lyrics" })).not.toBeChecked(); // instrumental is the default
  const genre = unique("acoustic pop");
  await form(page).getByLabel("Genre").fill(genre);
  await form(page).getByLabel("Mood").fill("warm and intimate");
  await form(page).getByLabel("Tempo (BPM)").fill("96");
  await form(page).getByLabel("Key and scale").fill("C major");
  await form(page).getByLabel("Instruments and arrangement").fill("fingerpicked guitar");
  const text = await description(page).inputValue();
  expect(text).toBe(
    `Global Metadata\nBasic Attributes: bpm is 96. key is C, and scale is major. ${genre}.\n` +
    "Global Emotional Progression: warm and intimate.\nInstrumental, no vocals.\nArrangement\nInstrument Lifecycle Description: fingerpicked guitar.",
  );

  const sent = page.waitForRequest((r) => r.url().endsWith("/api/runs") && r.method() === "POST");
  await make(page).click();
  const body = (await sent).postDataJSON();
  expect(body.mode).toBe("music");
  expect(body.prompt).toBe(text);
  expect("lyrics" in body).toBe(false); // the server sends [Instrumental]
  expect(body.options.fields).toEqual({ genre, mood: "warm and intimate", bpm: "96", key: "C major", instruments: "fingerpicked guitar" });
  expect(body.options.seed).toBeNull();

  const c = trackCard(page, genre).first();
  await done(c);
  await expect(c.getByText("Instrumental", { exact: true })).toBeVisible();
  await expect(c.locator(".run-lyrics")).toHaveCount(0);
  const audio = c.locator("audio");
  await expect(audio).toHaveCount(1);
  const src = (await audio.getAttribute("src"))!;
  expect(src).toMatch(/^\/api\/audio\/[0-9a-f]{32}$/);
  await expect(c.locator(".track-name")).toHaveText(/^Track · 0:0[5-6] \(you allowed up to 1:00\)$/); // the fake makes ~6 s, not the 60 allowed

  // the player can seek: the server answers range requests and the browser uses them
  const partial = await page.request.get(src, { headers: { Range: "bytes=0-99" } });
  expect(partial.status()).toBe(206);
  const [duration, at, seekable] = await audio.evaluate(async (a: HTMLAudioElement) => {
    a.preload = "auto";
    a.load();
    await new Promise<void>((resolve) => a.addEventListener("loadedmetadata", () => resolve(), { once: true }));
    a.currentTime = 2;
    await new Promise<void>((resolve) => a.addEventListener("seeked", () => resolve(), { once: true }));
    return [a.duration, a.currentTime, a.seekable.length];
  });
  expect(duration).toBeGreaterThan(5);
  expect(duration).toBeLessThan(7);
  expect(at).toBeGreaterThan(1.9);
  expect(seekable).toBeGreaterThan(0);

  // Download WAV: a meaningful name, a real WAV, and the note that it is machine-generated (DESIGN.md §26.8)
  const link = c.getByRole("link", { name: /Download WAV/ });
  const href = (await link.getAttribute("href"))!;
  expect(href).toBe(`${src}?download=1`);
  const file = await page.request.get(href);
  expect(file.headers()["content-disposition"]).toMatch(/filename="?music_.*_6s_s\d+_\d{8}-\d{6}\.wav"?/); // named for the length the model made (the fake makes ~6 s), not the 60 s allowed
  const bytes = await file.body();
  expect(bytes.subarray(0, 4).toString()).toBe("RIFF");
  expect(bytes.toString("latin1")).toContain("machine-generated");
});

test("Add lyrics shows Voice and the lyrics box; the tag buttons put each tag on its own line; the lyrics are sent as written", async ({ page }) => {
  await openMusic(page);
  await expect(form(page).getByLabel("Voice")).toHaveCount(0); // only with lyrics
  await expect(form(page).getByRole("button", { name: "[Verse]" })).toHaveCount(0);
  await form(page).getByLabel("Add lyrics").check();
  await expect(form(page).getByLabel("Voice")).toBeVisible();
  const lyrics = form(page).getByLabel("Lyrics", { exact: true });
  await expect(lyrics).toBeVisible();

  await form(page).getByRole("button", { name: "[Verse]" }).click();
  await expect(lyrics).toHaveValue("[Verse]\n");
  await expect(lyrics).toBeFocused();
  await page.keyboard.type("la la la");
  await form(page).getByRole("button", { name: "[Chorus]" }).click();
  await expect(lyrics).toHaveValue("[Verse]\nla la la\n[Chorus]\n"); // a line break before the tag, so it is not on the words' line
  await page.keyboard.type("oh oh");

  const genre = unique("indie folk");
  await form(page).getByLabel("Genre").fill(genre);
  await form(page).getByLabel("Voice").fill("soft female lead");
  const text = await description(page).inputValue();
  expect(text).toContain("Vocal Details\nVocal Gender & Timbre: soft female lead.");
  expect(text).not.toContain("Instrumental, no vocals.");

  const sent = page.waitForRequest((r) => r.url().endsWith("/api/runs") && r.method() === "POST");
  await make(page).click();
  const body = (await sent).postDataJSON();
  expect(body.lyrics).toBe("[Verse]\nla la la\n[Chorus]\noh oh");
  expect(body.options.fields.voice).toBe("soft female lead");

  const c = trackCard(page, genre).first();
  await done(c);
  await expect(c.getByText("With lyrics", { exact: true })).toBeVisible();
  await c.locator(".run-lyrics summary").click();
  await expect(c.locator(".run-lyrics pre")).toHaveText("[Verse]\nla la la\n[Chorus]\noh oh");
});

test("lyrics turned on but left empty are not sent as an instrumental track: the page says to write them", async ({ page }) => {
  await openMusic(page);
  await form(page).getByLabel("Genre").fill(unique("pop"));
  await form(page).getByLabel("Add lyrics").check();
  let posts = 0;
  page.on("request", (r) => r.method() === "POST" && r.url().endsWith("/api/runs") && posts++);
  await make(page).click();
  await expect(form(page).getByRole("alert")).toHaveText(/Write the lyrics, or turn Add lyrics off/);
  expect(posts).toBe(0);
  await form(page).getByLabel("Add lyrics").uncheck();
  await expect(form(page).getByRole("alert")).toHaveCount(0); // the message goes as soon as the form changes
});

test("nothing to describe: Make music waits; the description is editable and Rebuild from fields puts it back", async ({ page }) => {
  await openMusic(page);
  await expect(make(page)).toBeDisabled(); // an empty form is not a description
  const genre = form(page).getByLabel("Genre");
  await genre.fill("jazz");
  await expect(make(page)).toBeEnabled();
  await expect(description(page)).toHaveValue(/Basic Attributes: jazz\./);
  await expect(page.getByRole("button", { name: "Rebuild from fields" })).toBeDisabled(); // nothing to rebuild yet

  await description(page).fill("My own words, exactly.");
  await expect(form(page)).toContainText("You changed this by hand");
  await genre.fill("blues"); // the fields no longer rewrite it
  await expect(description(page)).toHaveValue("My own words, exactly.");
  await page.getByRole("button", { name: "Rebuild from fields" }).click();
  await expect(description(page)).toHaveValue(/Basic Attributes: blues\./);
  await expect(page.getByRole("button", { name: "Rebuild from fields" })).toBeDisabled();

  // a hand-written description is what is sent
  await description(page).fill("calm piano, nothing else");
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/runs") && r.method() === "POST");
  await page.keyboard.press("Control+Enter"); // from the description box, as in the prompt box on the Images tab
  expect((await sent).postDataJSON().prompt).toBe("calm piano, nothing else");
  await expect(page.locator("article.music-card", { hasText: "calm piano, nothing else" }).first()).toBeVisible();
});

test("the form is remembered across a reload", async ({ page }) => {
  await openMusic(page);
  await form(page).getByLabel("Genre").fill("dub");
  await form(page).getByLabel("Add lyrics").check();
  await form(page).getByLabel("Lyrics", { exact: true }).fill("[Verse]\nhello");
  await form(page).getByRole("button", { name: "30 s" }).click();
  await page.reload();
  await expect(form(page).getByLabel("Genre")).toHaveValue("dub");
  await expect(form(page).getByLabel("Add lyrics")).toBeChecked();
  await expect(form(page).getByLabel("Lyrics", { exact: true })).toHaveValue("[Verse]\nhello");
  await expect(form(page).getByRole("button", { name: "30 s" })).toHaveAttribute("aria-pressed", "true");
});

test("length: the chips set it, the box checks it, and what is sent is what is shown", async ({ page }) => {
  await openMusic(page);
  await form(page).getByLabel("Genre").fill(unique("ambient"));
  const chips = form(page).getByRole("group", { name: "Length (at most)" }).getByRole("button");
  await expect(chips).toHaveText(["15 s", "30 s", "1 min", "2 min", "3 min", "5 min"]);
  await expect(form(page).getByRole("button", { name: "1 min", exact: true })).toHaveAttribute("aria-pressed", "true"); // the default
  await form(page).getByRole("button", { name: "15 s" }).click();
  await expect(form(page).getByLabel("Seconds")).toHaveValue("15");
  await expect(form(page)).toContainText("15 s. The model may end a piece sooner");

  const seconds = form(page).getByLabel("Seconds");
  await seconds.fill("400"); // over the server's 300
  await expect(form(page)).toContainText("Enter a whole number from 10 to 300.");
  await expect(form(page).getByRole("button", { name: "15 s" })).toHaveAttribute("aria-pressed", "true"); // not committed
  await seconds.fill("90");
  await expect(form(page)).toContainText("1 min 30 s.");

  const sent = page.waitForRequest((r) => r.url().endsWith("/api/runs") && r.method() === "POST");
  await make(page).click();
  expect((await sent).postDataJSON().options.duration).toBe(90);
});

test("versions: two tracks, each with its player, on consecutive seeds", async ({ page }) => {
  await openMusic(page);
  const genre = unique("ambient");
  await form(page).getByRole("radio", { name: "2" }).click();
  const c = await makeMusic(page, genre);
  await done(c);
  await expect(c.locator(".track")).toHaveCount(2);
  await expect(c.locator("audio")).toHaveCount(2);
  await expect(c.locator(".track-name")).toHaveText([/^Version 1 of 2 · /, /^Version 2 of 2 · /]);
  const seeds = (await c.locator(".track-seed").allTextContents()).map((t) => Number(t.replace("seed ", "")));
  expect(seeds[1]).toBe(seeds[0] + 1);
  await expect(c.locator(".run-meta")).toContainText(`2 versions`);
  await expect(c.locator(".run-meta")).toContainText(`seeds ${seeds[0]}–${seeds[1]}`);
});

test("only one track plays at a time", async ({ page }) => {
  await openMusic(page);
  await form(page).getByRole("radio", { name: "2" }).click();
  const c = await makeMusic(page, unique("ambient"));
  await done(c);
  await page.mouse.click(5, 5); // a click on the page: browsers only let it play sound after one
  const [first, second] = [c.locator("audio").nth(0), c.locator("audio").nth(1)];
  await first.evaluate((a: HTMLAudioElement) => a.play());
  await expect.poll(() => first.evaluate((a: HTMLAudioElement) => !a.paused)).toBe(true);
  await second.evaluate((a: HTMLAudioElement) => a.play());
  await expect.poll(() => first.evaluate((a: HTMLAudioElement) => a.paused)).toBe(true); // starting one stopped the other
  expect(await second.evaluate((a: HTMLAudioElement) => !a.paused)).toBe(true);
});

test("progress has two phases, composing and rendering, each with its own bar", async ({ page }) => {
  await unloaded(page);
  await openMusic(page);
  await form(page).getByRole("radio", { name: "4" }).click(); // four tracks keep it running long enough to be seen
  const c = await makeMusic(page, unique("ambient"));
  await expect(c.getByRole("progressbar", { name: "Composing progress" })).toBeVisible({ timeout: 15_000 });
  await expect(c.getByRole("progressbar", { name: "Rendering progress" })).toBeVisible();
  await expect(c.locator(".phase-label")).toHaveText(["Composing", "Rendering"]);
  await done(c);
  await expect(c.getByRole("progressbar")).toHaveCount(0);
  await expect(c.locator(".track")).toHaveCount(4);
});

test("a failed track shows the reason and Retry makes another run", async ({ page }) => {
  await openMusic(page);
  const c = await makeMusic(page, unique("[fake:error] doomed"));
  await expect(c.locator(".badge").first()).toHaveText("Failed", { timeout: 20_000 });
  await expect(c.getByRole("alert")).toContainText("Simulated music generation failure");
  await c.getByRole("button", { name: "Retry" }).click();
  await expect(page.locator("article.music-card")).toHaveCount(2);
});

test("canceling a track that is still loading its model asks first, and the next track works", async ({ page }) => {
  await unloaded(page);
  await openMusic(page);
  const c = await makeMusic(page, unique("ambient"));
  await expect(c.getByRole("status")).toContainText(/Loading the music model|Starting/);
  const cancel = c.getByRole("button", { name: "Cancel" });
  await cancel.click();
  const confirm = page.getByRole("alertdialog", { name: "Stop this run?" });
  await expect(confirm).toContainText("Nothing has been finished yet");
  await expect(confirm).toContainText("The track being made now is discarded.");
  await confirm.getByRole("button", { name: "Keep going" }).click();
  await expect(c.locator(".badge").first()).toHaveText("Making music");
  await cancel.click();
  await confirm.getByRole("button", { name: "Stop making music" }).click();
  await expect(c.locator(".badge").first()).toHaveText("Canceled", { timeout: 20_000 });
  await expect(c.locator(".run-note")).toHaveText("Canceled before any track was finished.");
  await expect(c.getByRole("button", { name: "Reuse" })).toBeFocused();

  const next = await makeMusic(page, unique("piano"));
  await done(next);
});

test("Reuse brings back the fields, the lyrics, the description and the length, and locks the seed", async ({ page }) => {
  await openMusic(page);
  const genre = unique("folk");
  await form(page).getByLabel("Add lyrics").check();
  await form(page).getByLabel("Lyrics", { exact: true }).fill("[Verse]\nthe words");
  await form(page).getByLabel("Voice").fill("warm alto");
  await form(page).getByLabel("Mood").fill("wistful");
  await form(page).getByRole("button", { name: "30 s" }).click();
  const c = await makeMusic(page, genre);
  await done(c);
  const sentText = await description(page).inputValue();

  // change everything, then Reuse
  await form(page).getByLabel("Genre").fill("something else");
  await form(page).getByLabel("Add lyrics").uncheck();
  await form(page).getByRole("button", { name: "2 min" }).click();
  await c.getByRole("button", { name: "Reuse" }).click();
  await expect(form(page).getByLabel("Genre")).toHaveValue(genre);
  await expect(form(page).getByLabel("Mood")).toHaveValue("wistful");
  await expect(form(page).getByLabel("Add lyrics")).toBeChecked();
  await expect(form(page).getByLabel("Voice")).toHaveValue("warm alto");
  await expect(form(page).getByLabel("Lyrics", { exact: true })).toHaveValue("[Verse]\nthe words");
  await expect(form(page).getByRole("button", { name: "30 s" })).toHaveAttribute("aria-pressed", "true");
  await expect(description(page)).toHaveValue(sentText);
  await expect(form(page).locator("summary")).toContainText(/seed locked to \d+/);
  await expect(page.getByText(/Loaded the description, lyrics and settings\. Seed locked to \d+\./)).toBeVisible();
});

test("Keep and Delete work on a track, and delete takes its audio off the server", async ({ page }) => {
  await openMusic(page);
  const c = await makeMusic(page, unique("ambient"));
  await done(c);
  const src = (await c.locator("audio").getAttribute("src"))!;
  expect((await page.request.get(src)).status()).toBe(200);

  const keep = c.getByRole("button", { name: "Keep" });
  await keep.click();
  await expect(keep).toHaveAttribute("aria-pressed", "true");
  await expect(c.getByText("Kept", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator("article.music-card").first().getByText("Kept", { exact: true })).toBeVisible(); // survives a reload

  const again = page.locator("article.music-card").first();
  await again.getByRole("button", { name: "Delete" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Delete this run?" });
  await expect(confirm).toContainText("Its track is removed from the Spark.");
  await expect(confirm).toContainText("You marked it Keep.");
  await confirm.getByRole("button", { name: "Delete" }).click();
  await expect(page.locator("article.music-card")).toHaveCount(0);
  await expect(page.getByText("No music yet")).toBeVisible();
  expect((await page.request.get(src)).status()).toBe(404);
  await expect(form(page).getByLabel("Genre")).toBeFocused(); // the list is empty, so focus goes to the form
});

test("the Music tab names the model and reminds you of the licence", async ({ page }) => {
  await openMusic(page);
  const note = form(page).locator(".music-licence");
  await expect(note).toContainText("MiniMax-Music3");
  await expect(note).toContainText("machine-generated");
  const link = note.getByRole("link", { name: "MiniMax-Music3" });
  await expect(link).toHaveAttribute("href", "https://huggingface.co/MiniMaxAI/MiniMax-Music3");
  await expect(link).toHaveAttribute("rel", /noopener/);
});

test("Load music model and Unload model act on the music model; the pill and the Images tab follow", async ({ page }) => {
  await unloaded(page);
  await openMusic(page);
  await expect(pill(page)).toHaveText(/^Model not loaded/);
  await expect(unloadButton(page)).toHaveCount(0);
  await expect(loadImage(page)).toHaveCount(0); // on this tab the button is the music one
  await expect(loadMusic(page)).toHaveAttribute("title", /press Make music/);

  await loadMusic(page).click();
  await expect(pill(page)).toHaveText(/^Loading music model…/);
  await expect(announcement(page)).toHaveText("Loading the music model…");
  await expect(pill(page)).toHaveText(/^Music model ready\s*· unloads in/);
  await expect(announcement(page)).toHaveText("Music model ready.");
  await expect(unloadButton(page)).toBeVisible();
  expect(((await (await page.request.get("/api/status")).json()) as { worker: { model: string } }).worker.model).toBe("music");

  // the Images tab: the music model is loaded, so its button loads the image model (which replaces it)
  await imagesTab(page).click();
  await expect(unloadButton(page)).toHaveCount(0);
  await expect(loadImage(page)).toBeVisible();
  await expect(loadImage(page)).toHaveAttribute("title", /music model is unloaded first/);
  await loadImage(page).click();
  await expect(pill(page)).toHaveText(/^Image model ready\s*· unloads in/, { timeout: 20_000 });
  await expect(announcement(page)).toHaveText("Image model ready.");

  // and back: the Music tab offers to swap again
  await musicTab(page).click();
  await expect(loadMusic(page)).toHaveAttribute("title", /image model is unloaded first/);
  await loadMusic(page).click();
  await expect(pill(page)).toHaveText(/^Music model ready/, { timeout: 20_000 });
  await unloadButton(page).click();
  await expect(pill(page)).toHaveText(/^Model not loaded/);
  await expect(announcement(page)).toHaveText("Music model unloaded.");
});

test("making music swaps the models by itself, and a picture swaps them back", async ({ page }) => {
  await unloaded(page);
  await page.goto("/");
  const picture = await generate(page, unique("a quiet pond"));
  await expect(picture.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(pill(page)).toHaveText(/^Image model ready/);

  await musicTab(page).click();
  const track = await makeMusic(page, unique("ambient"));
  await done(track);
  await expect(pill(page)).toHaveText(/^Music model ready/);
  const status = async () => ((await (await page.request.get("/api/status")).json()) as { worker: { model: string; state: string } }).worker;
  expect((await status()).model).toBe("music");

  await imagesTab(page).click();
  const next = await generate(page, unique("a second pond"));
  await expect(next.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });
  await expect(pill(page)).toHaveText(/^Image model ready/);
  expect((await status()).model).toBe("image");
});

test("the other tab shows a dot while its run is going", async ({ page }) => {
  await unloaded(page);
  await openMusic(page);
  await form(page).getByRole("radio", { name: "4" }).click();
  const c = await makeMusic(page, unique("ambient"));
  await imagesTab(page).click();
  await expect(musicTab(page)).toContainText("(working)"); // the sr-only words that go with the dot
  await expect(musicTab(page).locator(".tab-dot")).toBeVisible();
  await musicTab(page).click();
  await expect(musicTab(page)).not.toContainText("(working)"); // no dot on the tab that is open
  await done(c);
  await imagesTab(page).click();
  await expect(musicTab(page)).not.toContainText("(working)"); // and none once it is finished
});

test("when the music model cannot run here, the tab says why, with the hint, and offers no Load button", async ({ page }) => {
  await page.route("**/api/capabilities", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.modes = body.modes.filter((mode: string) => mode !== "music");
    body.music = { available: false, state: "failed", reason: "The diffusers package is not installed.", hint: "Rebuild the image (docker compose build).", model: "MiniMaxAI/MiniMax-Music3" };
    await route.fulfill({ response, json: body });
  });
  await unloaded(page);
  await page.goto("/");
  await musicTab(page).click(); // the tab is still there, and says why
  const why = page.getByRole("region", { name: "Music is not available" });
  await expect(why).toContainText("Music isn't available on this server");
  await expect(why).toContainText("The diffusers package is not installed.");
  await expect(why).toContainText("Rebuild the image (docker compose build).");
  await expect(form(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Make music" })).toHaveCount(0);
  await expect(loadMusic(page)).toHaveCount(0);
  await imagesTab(page).click();
  await expect(loadImage(page)).toBeVisible(); // pictures are not affected
});

test("while the server is still checking whether music can run, the tab says so", async ({ page }) => {
  await page.route("**/api/capabilities", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.modes = body.modes.filter((mode: string) => mode !== "music");
    body.music = { available: false, state: "running", reason: null, hint: null, model: "x" };
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  await musicTab(page).click();
  await expect(page.getByRole("region", { name: "Music is not available" })).toContainText("Checking the music model…");
});

test("a server that refuses the request is heard: the field error shows on the form", async ({ page }) => {
  await openMusic(page);
  await page.route("**/api/runs", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: [{ loc: ["body", "options", "duration"], msg: "Must be between 10 and 120 seconds.", type: "value_error" }] }) })
      : route.continue());
  await form(page).getByLabel("Genre").fill("jazz");
  await make(page).click();
  await expect(form(page).getByRole("alert")).toHaveText("Must be between 10 and 120 seconds.");
  await expect(page.locator("article.music-card")).toHaveCount(0);
});

test("phone width: the tabs, the form and a finished track fit, with the player and its buttons on screen", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await unloaded(page);
  await openMusic(page);
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth);
  expect(await overflow()).toBeLessThanOrEqual(360);
  const bar = (await page.getByRole("tablist").boundingBox())!;
  expect(bar.x).toBeGreaterThanOrEqual(0);
  expect(bar.x + bar.width).toBeLessThanOrEqual(360);
  await form(page).getByLabel("Add lyrics").check();
  await form(page).getByLabel("Lyrics", { exact: true }).fill("[Verse]\nla");
  expect(await overflow()).toBeLessThanOrEqual(360); // the tag buttons wrap
  for (const tag of ["Intro", "Outro"]) {
    const box = (await form(page).getByRole("button", { name: `[${tag}]` }).boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(360);
  }
  const genre = unique("ambient");
  await form(page).getByLabel("Genre").fill(genre);
  await expect(pill(page)).toHaveText("Unloaded"); // short words that fit, beside the button
  expect(await pill(page).locator(".pill-label").evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  await make(page).click();
  const c = trackCard(page, genre).first();
  await done(c);
  expect(await overflow()).toBeLessThanOrEqual(360);
  const player = (await c.locator("audio").boundingBox())!;
  expect(player.x).toBeGreaterThanOrEqual(0);
  expect(player.x + player.width).toBeLessThanOrEqual(360);
  for (const button of await c.getByRole("button").all()) {
    const box = (await button.boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(360);
  }
  const download = (await c.getByRole("link", { name: /Download WAV/ }).boundingBox())!;
  expect(download.x + download.width).toBeLessThanOrEqual(360);
  await page.screenshot({ path: "test-results/music-phone.png", fullPage: true });
});
