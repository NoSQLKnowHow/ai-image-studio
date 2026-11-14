// More like this and A/B compare (DESIGN.md section 43, criteria 332 to 346), in a real browser against the real server with the fake pipeline: the dialog from a card and from the viewer, the
// linked runs it makes and the chips and family view that show them, a refusal said inside the dialog, the picture-based choice greyed with its reason, and the compare basket and compare view
// (side by side and the slider, by keyboard, the full basket's offer, Keep this one) at desktop and phone widths. What the unit tests cannot see is here: real dialogs, real focus, real layout.
import { clearHistory, expect, test, unique, useOptions, type Locator, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
const runCard = (page: Page, id: string): Locator => page.locator(`article.run-card[data-run-id="${id}"]`);
const dialog = (page: Page): Locator => page.getByRole("dialog", { name: "More like this" });
const basketBar = (page: Page): Locator => page.getByRole("region", { name: "Compare basket" });
const compareView = (page: Page): Locator => page.getByRole("dialog", { name: "Compare" });
// The one filter bar is mounted in every tab's panel and the panels that are not shown are hidden, so a family's pill is in the page once per panel: the shown one is the one meant.
const familyPill = (page: Page): Locator => page.locator("[data-filter-family]:visible");

interface RunJson { id: string; status: string; prompt: string; pinned: boolean; images: { id: string; seed: number }[]; variation_of: null | { run_id: string; image_id: string | null; how: string; source: { state: string } };
  family_id: string | null; variation_count: number; options: { seed: number; seed_was_random: boolean; num_images: number } }

const get = async (page: Page, id: string): Promise<RunJson> => (await (await page.request.get(`/api/runs/${id}`)).json()) as RunJson;

/** A finished picture run, made through the API (a small picture, two steps, fast) and waited for. */
async function made(page: Page, prompt: string, options: Record<string, unknown> = {}): Promise<string> {
  const response = await page.request.post("/api/runs", { headers: ASK, data: { prompt, options: { steps: 2, width: 512, height: 512, seed: 5, ...options } } });
  expect(response.status()).toBe(201);
  const id = ((await response.json()) as { id: string }).id;
  await expect.poll(async () => (await get(page, id)).status, { timeout: 20_000 }).toBe("done");
  return id;
}

/** Every run in the history that is a variation of `sourceId`, newest first. */
async function variationsOf(page: Page, sourceId: string): Promise<RunJson[]> {
  const { runs } = (await (await page.request.get(`/api/runs?family=${sourceId}`)).json()) as { runs: RunJson[] };
  return runs.filter((run) => run.id !== sourceId);
}

/** Open a card's n-th picture in the viewer. */
const openPicture = async (page: Page, id: string, n = 1) => {
  await runCard(page, id).getByRole("button", { name: new RegExp(`^Open image ${n} of`) }).click();
  await expect(page.getByRole("dialog").filter({ has: page.locator(".lightbox-bar") })).toBeVisible();
};

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await useOptions(page);
});

// ---- More like this from a card (criteria 332, 333, 336, 337) ------------------------------------------------------------------------------------------------
test("More like this on a card makes more takes with fresh seeds, linked to the run, and the chips show the family", async ({ page }) => {
  const words = unique("a fox in a snowy wood");
  const id = await made(page, words);
  await page.goto("/");
  await runCard(page, id).getByRole("button", { name: /More like this/ }).click();

  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).locator('[data-field="count"]')).toHaveValue("4"); // 4 by default
  await expect(dialog(page).locator('[data-field="words"]')).toHaveValue(words); // the run's own words, to be edited
  await dialog(page).locator('[data-field="count"]').selectOption("2");
  await expect(dialog(page).locator('[data-field="summary"]')).toContainText("2 pictures at");
  await dialog(page).getByRole("button", { name: "Make", exact: true }).click();
  await expect(dialog(page)).toBeHidden();

  // the new run is linked, has the number asked for, and a seed that is not the source's (the same seed would make the same picture)
  await expect.poll(async () => (await variationsOf(page, id)).length).toBe(1);
  const [variation] = await variationsOf(page, id);
  expect(variation.variation_of).toMatchObject({ run_id: id, image_id: null, how: "seeds", source: { state: "present" } });
  expect(variation.family_id).toBe(id);
  expect(variation.options.num_images).toBe(2);
  expect(variation.options.seed_was_random).toBe(true);
  expect(variation.options.seed).not.toBe(5);
  await expect.poll(async () => (await get(page, variation.id)).status).toBe("done");
  expect((await get(page, variation.id)).images.map((image) => image.seed)).not.toContain(5);

  // the cards say so: the variation where it came from, the source how many it has
  await expect(runCard(page, variation.id).locator('[data-chip="variation-of"]')).toContainText(`Variation of “${words}”`);
  await expect(runCard(page, id).locator('[data-chip="variations"]')).toHaveText("Variations (1)");

  // the chip shows the family: the first run and what was made from it, with a pill that leaves it
  await runCard(page, id).locator('[data-chip="variations"]').click();
  await expect(familyPill(page)).toContainText(`Variations of “${words}”`);
  await expect(page.locator("article.run-card")).toHaveCount(2);
  await page.getByRole("button", { name: "Leave this view and show the whole history" }).click();
  await expect(familyPill(page)).toHaveCount(0);
});

test("the family is not remembered: a reload leaves it", async ({ page }) => {
  const id = await made(page, unique("a lighthouse"));
  await page.goto("/");
  await runCard(page, id).getByRole("button", { name: /More like this/ }).click();
  await dialog(page).getByRole("button", { name: "Make", exact: true }).click();
  await expect.poll(async () => (await variationsOf(page, id)).length).toBe(1);
  await runCard(page, id).locator('[data-chip="variations"]').click();
  await expect(familyPill(page)).toBeVisible();
  await page.reload();
  await expect(page.locator("[data-filter-family]")).toHaveCount(0); // not merely hidden: after a reload no panel has the pill at all
});

// ---- from the viewer (criterion 334) ---------------------------------------------------------------------------------------------------------------------------
test("More like this in the viewer is the same request and records the picture that was on the screen", async ({ page }) => {
  const id = await made(page, unique("a red door"), { num_images: 2 });
  const source = await get(page, id);
  await page.goto("/");
  await openPicture(page, id, 2);
  await page.getByRole("button", { name: /More like this/ }).click();
  await expect(dialog(page)).toBeVisible();
  await dialog(page).getByRole("button", { name: "Make", exact: true }).click();
  await expect.poll(async () => (await variationsOf(page, id)).length).toBe(1);
  const [variation] = await variationsOf(page, id);
  expect(variation.variation_of).toMatchObject({ run_id: id, image_id: source.images[1].id, how: "seeds" }); // the second picture, which was the one on the screen
  await expect(page.getByRole("dialog").filter({ has: page.locator(".lightbox-bar") })).toBeHidden(); // the viewer closes: the new run's card is on the page
});

// ---- Variations of this picture: off until switched on (criterion 335) ------------------------------------------------------------------------------------------
test("Variations of this picture is greyed, with the reason, and cannot be chosen", async ({ page }) => {
  const id = await made(page, unique("a quiet harbour"));
  await page.goto("/");
  await runCard(page, id).getByRole("button", { name: /More like this/ }).click();
  const picture = dialog(page).locator('[data-choice="picture"]');
  await expect(picture).toBeDisabled();
  await expect(dialog(page)).toContainText("Not tried on this machine yet.");
  await expect(dialog(page).locator('[data-choice="seeds"]')).toBeChecked();
  // and the server would refuse it if it were asked for: the same words
  const refused = await page.request.post("/api/runs", { headers: ASK, data: {
    mode: "edit", prompt: "x", input_images: [{ image_id: (await get(page, id)).images[0].id }], options: { steps: 2 }, variation_of: { run_id: id, image_id: null, how: "picture" } } });
  expect(refused.status()).toBe(422);
});

// ---- a refusal is said inside the dialog ----------------------------------------------------------------------------------------------------------------------
test("a refusal by the server is said in its words inside the dialog, which stays open", async ({ page }) => {
  const id = await made(page, unique("a paper boat"));
  await page.goto("/");
  await runCard(page, id).getByRole("button", { name: /More like this/ }).click();
  await expect(dialog(page)).toBeVisible();
  expect((await page.request.delete(`/api/runs/${id}`, { headers: ASK })).status()).toBe(204); // gone, while the dialog is open (another page, say)
  await dialog(page).getByRole("button", { name: "Make", exact: true }).click();
  await expect(dialog(page).locator('[data-field="error"]')).toContainText("not there any more");
  await expect(dialog(page)).toBeVisible();
});

// ---- A/B compare (criteria 341 to 344) -----------------------------------------------------------------------------------------------------------------------
test("two pictures from two runs: the basket, the compare view, the slider by keyboard, and Keep this one", async ({ page }) => {
  const [a, b] = [unique("first picture"), unique("second picture")];
  const [idA, idB] = [await made(page, a), await made(page, b)];
  await page.goto("/");

  await openPicture(page, idA);
  await page.getByRole("button", { name: "Add to compare" }).click();
  await expect(page.getByRole("button", { name: "In compare" })).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
  await expect(basketBar(page)).toContainText("Compare · 1 chosen");
  await expect(basketBar(page).getByRole("button", { name: "Compare", exact: true })).toBeDisabled(); // one is not enough

  await openPicture(page, idB);
  await page.getByRole("button", { name: "Add to compare" }).click();
  await page.keyboard.press("Escape");
  await expect(basketBar(page)).toContainText("Compare · 2 chosen");
  await basketBar(page).getByRole("button", { name: "Compare", exact: true }).click();

  // side by side: two panes, each labelled with its words, its seed and its size
  await expect(compareView(page)).toBeVisible();
  await expect(compareView(page).locator(".compare-pane")).toHaveCount(2);
  await expect(compareView(page).locator(".compare-pane").first()).toContainText(a);
  await expect(compareView(page).locator(".compare-pane").nth(1)).toContainText(b);

  // swap puts them the other way round
  await compareView(page).getByRole("button", { name: "Swap sides" }).click();
  await expect(compareView(page).locator(".compare-pane").first()).toContainText(b);
  await compareView(page).getByRole("button", { name: "Swap sides" }).click();

  // the slider: a real slider, moved by the keyboard, announcing its value
  await compareView(page).getByRole("button", { name: "Slider" }).click();
  const slider = compareView(page).getByRole("slider", { name: "Divider between the two pictures" });
  await slider.focus();
  await expect(slider).toHaveAttribute("aria-valuenow", "50");
  await page.keyboard.press("ArrowRight");
  await expect(slider).toHaveAttribute("aria-valuenow", "55");
  await page.keyboard.press("PageDown");
  await expect(slider).toHaveAttribute("aria-valuenow", "45");
  await page.keyboard.press("Home");
  await expect(slider).toHaveAttribute("aria-valuenow", "0");
  await page.keyboard.press("End");
  await expect(slider).toHaveAttribute("aria-valuenow", "100");
  await page.keyboard.press("ArrowRight"); // never past the end
  await expect(slider).toHaveAttribute("aria-valuenow", "100");

  // and by mouse (the divider takes pointer events, so a pen and a finger go the same way): a press moves it to the place pressed, dragging carries it, and after the release it stays
  const area = (await compareView(page).locator(".compare-slider").boundingBox())!;
  const at = (fraction: number) => [area.x + area.width * fraction, area.y + area.height / 2] as const;
  const value = async () => Number(await slider.getAttribute("aria-valuenow"));
  await page.mouse.move(...at(0.25));
  await page.mouse.down();
  await expect.poll(value).toBeGreaterThanOrEqual(24);
  await expect.poll(value).toBeLessThanOrEqual(26);
  await page.mouse.move(...at(0.75), { steps: 5 });
  await expect.poll(value).toBeGreaterThanOrEqual(74);
  await expect.poll(value).toBeLessThanOrEqual(76);
  await page.mouse.up();
  await page.mouse.move(...at(0.1), { steps: 3 }); // the button is up: the divider does not follow
  expect(await value()).toBeGreaterThanOrEqual(74);
  expect(await value()).toBeLessThanOrEqual(76);

  // Keep this one keeps the run of the first picture, and changes nothing else
  expect((await get(page, idA)).pinned).toBe(false);
  await compareView(page).locator('[data-action="compare-keep"][data-side="first"]').click();
  await expect.poll(async () => (await get(page, idA)).pinned).toBe(true);
  expect((await get(page, idB)).pinned).toBe(false);
  await expect(compareView(page).locator('[data-action="compare-keep"][data-side="first"]')).toContainText("Kept");
  await expect(compareView(page).locator('[data-action="compare-keep"][data-side="second"]')).toContainText("Keep this one");
});

test("a third picture is refused in words and offers to replace the first", async ({ page }) => {
  const [a, b, c] = [unique("alpha picture"), unique("bravo picture"), unique("charlie picture")];
  const [idA, idB, idC] = [await made(page, a), await made(page, b), await made(page, c)];
  await page.goto("/");
  for (const id of [idA, idB]) {
    await openPicture(page, id);
    await page.getByRole("button", { name: "Add to compare" }).click();
    await page.keyboard.press("Escape");
  }
  await openPicture(page, idC);
  await page.getByRole("button", { name: "Add to compare" }).click();
  await expect(page.locator(".lightbox-notice")).toContainText("The compare basket holds two pictures");
  // the viewer is a modal, so the page behind it is hidden from the accessibility tree: the bar is found by its attribute here, not by its role
  await expect(page.locator("[data-compare-basket]")).toContainText("Compare · 2 chosen"); // nothing was added
  await page.getByRole("button", { name: "Replace the first" }).click();
  await page.keyboard.press("Escape");
  await basketBar(page).getByRole("button", { name: "Compare", exact: true }).click();
  // the first picture went out, the second became the first, and the new one is second
  await expect(compareView(page).locator(".compare-pane").first()).toContainText(b);
  await expect(compareView(page).locator(".compare-pane").nth(1)).toContainText(c);
  await page.keyboard.press("Escape");
  await basketBar(page).getByRole("button", { name: "Clear" }).click();
  await expect(basketBar(page)).toHaveCount(0);
});

// ---- on a phone (criterion 345) ---------------------------------------------------------------------------------------------------------------------------------
test.describe("on a phone", () => {
  test.use({ viewport: { width: 360, height: 800 }, hasTouch: true });

  const insideTheWindow = async (locator: Locator, what: string) => {
    const box = await locator.boundingBox();
    expect(box, `${what} is on the screen`).toBeTruthy();
    expect(box!.x, `${what} starts inside the window`).toBeGreaterThanOrEqual(-0.5);
    expect(box!.x + box!.width, `${what} ends inside the window`).toBeLessThanOrEqual(360.5);
  };

  test("the dialog, the basket and the compare view fit, and side by side stacks", async ({ page }) => {
    const long = (tag: string) => `${tag} a cinematic photorealistic film still set on a dusty old-western railway platform at golden hour, long shadows, a lone traveller waiting beside a battered trunk`;
    const [idA, idB] = [await made(page, long(unique("first"))), await made(page, long(unique("second")))];
    await page.goto("/");

    await runCard(page, idA).getByRole("button", { name: /More like this/ }).click();
    await insideTheWindow(dialog(page), "the dialog");
    for (const part of ['[data-field="words"]', '[data-field="count"]', '[data-action="make-more"]']) await insideTheWindow(dialog(page).locator(part), `the dialog's ${part}`);
    await page.keyboard.press("Escape");

    for (const id of [idA, idB]) {
      await openPicture(page, id);
      await page.getByRole("button", { name: "Add to compare" }).click();
      await page.keyboard.press("Escape");
    }
    await insideTheWindow(basketBar(page), "the basket bar");
    await basketBar(page).getByRole("button", { name: "Compare", exact: true }).click();
    await insideTheWindow(compareView(page), "the compare view");
    // side by side stacks: the second pane is below the first, not beside it
    const [first, second] = [await compareView(page).locator(".compare-pane").first().boundingBox(), await compareView(page).locator(".compare-pane").nth(1).boundingBox()];
    expect(second!.y).toBeGreaterThan(first!.y + first!.height - 1);
    for (const button of await compareView(page).getByRole("button").all()) await insideTheWindow(button, "a compare button");
  });
});
