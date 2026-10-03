// End-to-end: Edit mode (DESIGN.md §21.4, criteria 19-27), against the real backend with the fake pipeline.
import { card, clearHistory, expect, promptBox, test, unique, useOptions, type Locator, type Page } from "./helpers";
import { BLUE, GOLD, GREEN, RED, png } from "./png";

// ------------------------------------------------------------------ helpers
type Pic = { name: string; mimeType: string; buffer: Buffer };
const pic = (name: string, width: number, height: number, color = RED, alpha = 255): Pic => ({ name, mimeType: "image/png", buffer: png(width, height, color, alpha) });
const red = () => pic("red.png", 300, 200, RED);
const green = () => pic("green.png", 200, 300, GREEN);
const blue = () => pic("blue.png", 240, 240, BLUE);
const gold = () => pic("gold.png", 220, 220, GOLD);

type ApiRun = {
  id: string; status: string; mode: string; prompt: string;
  options: Record<string, unknown>;
  inputs: { position: number; id: string; width: number; height: number; role: string; thumb_url: string | null }[];
  images: { id: string; width: number; height: number }[];
};
const newestRuns = async (page: Page): Promise<ApiRun[]> => ((await (await page.request.get("/api/runs?limit=10")).json()) as { runs: ApiRun[] }).runs;

const tiles = (page: Page): Locator => page.locator(".tray-tile");
const generateButton = (page: Page): Locator => page.getByRole("button", { name: "Generate", exact: true });
const addTile = (page: Page): Locator => page.getByRole("button", { name: /^Add images/ });
const fileInput = (page: Page): Locator => page.getByTestId("tray-file-input");
const optionsButton = (page: Page): Locator => page.getByRole("button", { name: /^Options/ });
const editRadio = (page: Page): Locator => page.getByRole("radio", { name: "Edit", exact: true });

/** Start in Edit mode with fast options, an empty history and the tray empty. */
async function openEdit(page: Page, options: Record<string, unknown> = {}) {
  await useOptions(page, { mode: "edit", ...options });
  await page.goto("/");
  await clearHistory(page);
}

/** Add pictures through the file picker and wait for their uploads to finish. */
async function addPictures(page: Page, pictures: Pic[], total = pictures.length) {
  await fileInput(page).setInputFiles(pictures);
  await expect(tiles(page)).toHaveCount(total);
  await expect(page.locator(".tray-progress")).toHaveCount(0, { timeout: 20_000 });
}

/** The order the tray shows, by the picture's width (red 300, green 200, blue 240, gold 220). */
async function trayOrder(page: Page): Promise<string[]> {
  const names: Record<string, string> = { "red.png": "red", "green.png": "green", "blue.png": "blue", "gold.png": "gold" };
  return page.locator(".tray-tile").evaluateAll((els, map) => els.map((el) => {
    const label = el.getAttribute("aria-label") ?? "";
    return map[label.replace(/^Image \d+: /, "").replace(/, .*$/, "")] ?? label;
  }), names);
}

async function dataTransfer(page: Page, pictures: Pic[]) {
  return page.evaluateHandle((list) => {
    const transfer = new DataTransfer();
    for (const file of list) transfer.items.add(new File([Uint8Array.from(atob(file.b64), (c) => c.charCodeAt(0))], file.name, { type: file.type }));
    return transfer;
  }, pictures.map((p) => ({ name: p.name, b64: p.buffer.toString("base64"), type: p.mimeType })));
}

async function send(page: Page, prompt: string): Promise<Locator> {
  await promptBox(page).fill(prompt);
  await generateButton(page).click();
  const c = card(page, prompt).first();
  await expect(c).toBeVisible();
  return c;
}

// ------------------------------------------------------------------ Edit mode and the tray
test("Edit mode shows an empty tray, and Generate says why it must wait", async ({ page }) => {
  await useOptions(page, { mode: "generate" });
  await page.goto("/");
  await expect(editRadio(page)).toBeEnabled();
  await expect(page.locator(".tray")).toHaveCount(0); // Generate has no tray
  await editRadio(page).click();
  await expect(page.getByRole("region", { name: "Images to edit" })).toBeVisible();
  await expect(addTile(page)).toContainText("0 of 4");
  await promptBox(page).fill("a prompt without a picture");
  await expect(generateButton(page)).toBeDisabled();
  await expect(page.getByText("Add at least one image to edit.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Draft", exact: true })).toBeDisabled();
  await expect(promptBox(page)).toHaveAttribute("placeholder", /Refer to images by number/);
  await page.getByRole("radio", { name: "Generate", exact: true }).click();
  await expect(page.locator(".tray")).toHaveCount(0);
});

test("pictures added from the picker are numbered in order, and uploaded as soon as they are added", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green(), blue()]);
  await expect(page.locator(".tray-badge")).toHaveText(["1", "2", "3"]);
  expect(await trayOrder(page)).toEqual(["red", "green", "blue"]);
  await expect(addTile(page)).toContainText("3 of 4");
  for (const src of await page.locator(".tray-tile img").evaluateAll((els) => els.map((el) => el.getAttribute("src")))) {
    expect(src).toMatch(/^\/api\/images\/[0-9a-f]{32}\/thumb$/); // the server's thumbnail, so each picture is staged
  }
  await promptBox(page).fill("put the red one on the green one");
  await expect(generateButton(page)).toBeEnabled();
});

test("at the cap further pictures are refused with a message, never dropped silently", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green(), blue(), gold(), pic("extra.png", 100, 100)], 4);
  await expect(page.getByText("1 image did not fit: one edit takes at most 4.")).toBeVisible();
  await expect(addTile(page)).toContainText("4 of 4");
  await expect(addTile(page)).toBeDisabled();
  expect(await trayOrder(page)).toEqual(["red", "green", "blue", "gold"]); // the first ones that fit
  await page.getByRole("button", { name: "Dismiss this message" }).click();
  await expect(page.getByText("did not fit")).toHaveCount(0);
});

test("a bad file in a multi-file add is rejected alone, with the reason, and the others are kept", async ({ page }) => {
  await openEdit(page);
  const notes: Pic = { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("this is not a picture") };
  await fileInput(page).setInputFiles([red(), notes, green()]);
  await expect(page.getByText(/notes\.txt:.*PNG, JPEG or WebP/)).toBeVisible({ timeout: 20_000 });
  await expect(tiles(page)).toHaveCount(2);
  await expect(page.locator(".tray-progress")).toHaveCount(0);
  expect(await trayOrder(page)).toEqual(["red", "green"]);
});

test("a picture over the size limit is refused before it is sent", async ({ page }) => {
  await openEdit(page);
  let uploads = 0;
  await page.route("**/api/uploads", (route) => {
    uploads += 1;
    return route.continue();
  });
  const huge: Pic = { name: "huge.png", mimeType: "image/png", buffer: Buffer.alloc(21 * 1024 * 1024, 1) };
  await fileInput(page).setInputFiles([huge, red()]);
  await expect(page.getByText("huge.png: the file is larger than 20 MB.")).toBeVisible();
  await expect(tiles(page)).toHaveCount(1);
  await expect(page.locator(".tray-progress")).toHaveCount(0, { timeout: 20_000 });
  expect(uploads).toBe(1); // only the red one was sent
});

test("pictures can be dropped on the prompt card and pasted into the prompt", async ({ page }) => {
  await openEdit(page);
  const dropped = await dataTransfer(page, [red(), green()]);
  await page.locator(".prompt-card").dispatchEvent("dragover", { dataTransfer: dropped });
  await expect(page.locator(".prompt-card.dropping")).toHaveCount(1);
  await page.locator(".prompt-card").dispatchEvent("drop", { dataTransfer: dropped });
  await expect(tiles(page)).toHaveCount(2);
  await expect(page.locator(".prompt-card.dropping")).toHaveCount(0);

  const pasted = await dataTransfer(page, [pic("pasted.png", 260, 260, GOLD)]);
  await promptBox(page).evaluate((el, transfer) => {
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, pasted);
  await expect(tiles(page)).toHaveCount(3);
  await expect(page.locator(".tray-progress")).toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator(".tray-badge")).toHaveText(["1", "2", "3"]);

  // ordinary text still pastes as text
  await promptBox(page).focus();
  await page.evaluate(() => {
    const transfer = new DataTransfer();
    transfer.setData("text/plain", "some words");
    document.getElementById("prompt")!.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
  });
  await expect(tiles(page)).toHaveCount(3);
});

test("a picture dropped in Generate mode says to switch to Edit, and one dropped outside the card does not leave the page", async ({ page }) => {
  await useOptions(page, { mode: "generate" });
  await page.goto("/");
  const dropped = await dataTransfer(page, [red()]);
  await page.locator(".prompt-card").dispatchEvent("drop", { dataTransfer: dropped });
  await expect(page.getByRole("status").filter({ hasText: "Switch to Edit to work with pictures." })).toBeVisible();
  await editRadio(page).click();
  await expect(tiles(page)).toHaveCount(0); // nothing was added behind the toast

  // the browser would open a file dropped on the bare page; the page must stop that
  const prevented = await page.evaluate(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["x"], "stray.png", { type: "image/png" }));
    const event = new DragEvent("drop", { dataTransfer: transfer, bubbles: true, cancelable: true });
    document.body.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(prevented).toBe(true);
});

test("clicking a badge puts 'image N' at the caret, replacing a selection, and returns focus to the prompt", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green()]);
  const box = promptBox(page);
  await box.fill("put the dog from");
  await page.getByRole("button", { name: /^Image 1:/ }).click();
  await expect(box).toHaveValue("put the dog from image 1");
  await expect(box).toBeFocused();

  await box.fill("a b");
  await box.evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(1, 1));
  await page.getByRole("button", { name: /^Image 2:/ }).click();
  await expect(box).toHaveValue("a image 2 b");

  await box.fill("make DOG bigger");
  await box.evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(5, 8));
  await page.getByRole("button", { name: /^Image 1:/ }).click();
  await expect(box).toHaveValue("make image 1 bigger"); // the selection was replaced
  expect(await box.evaluate((el: HTMLTextAreaElement) => el.selectionStart)).toBe("make image 1".length);

  await page.keyboard.type(" now");
  await expect(box).toHaveValue("make image 1 now bigger"); // typing continues at the caret
});

test("with a saved draft the prompt has never been focused, so 'image N' goes on the end", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("studio.prompt.v1", "put the dog from"));
  await openEdit(page);
  await addPictures(page, [red(), green()]);
  await page.getByRole("button", { name: /^Image 2:/ }).click();
  await expect(promptBox(page)).toHaveValue("put the dog from image 2");
});

test("the arrows reorder, keep focus on the moved picture, and announce it", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green(), blue()]);
  await expect(page.getByRole("button", { name: "Move image 1 earlier" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Move image 3 later" })).toBeDisabled();

  await page.getByRole("button", { name: "Move image 3 earlier" }).click();
  expect(await trayOrder(page)).toEqual(["red", "blue", "green"]);
  await expect(page.getByRole("status").filter({ hasText: "Moved to position 2." })).toBeAttached();
  await expect(page.getByRole("button", { name: "Move image 2 earlier" })).toBeFocused(); // the same picture, now number 2
  await page.keyboard.press("Enter");
  expect(await trayOrder(page)).toEqual(["blue", "red", "green"]);
  await expect(page.getByRole("button", { name: "Move image 1 earlier" })).toBeDisabled(); // it is first now
  await expect(page.locator(".tray-badge")).toHaveText(["1", "2", "3"]); // the numbers always follow the places
});

test("dragging a picture onto another puts it there", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green(), blue()]);
  await tiles(page).nth(2).dragTo(tiles(page).nth(0));
  expect(await trayOrder(page)).toEqual(["blue", "red", "green"]);
  await tiles(page).nth(0).dragTo(tiles(page).nth(2));
  expect(await trayOrder(page)).toEqual(["red", "green", "blue"]);
});

test("removing a picture renumbers the rest, and takes its staged upload off the server", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green(), blue()]);
  const second = (await tiles(page).nth(1).locator("img").getAttribute("src"))!.replace("/thumb", "");
  expect((await page.request.get(second)).status()).toBe(200); // staged
  await page.getByRole("button", { name: "Remove image 2" }).click();
  expect(await trayOrder(page)).toEqual(["red", "blue"]);
  await expect(page.locator(".tray-badge")).toHaveText(["1", "2"]);
  await expect(addTile(page)).toContainText("2 of 4");
  await expect.poll(async () => (await page.request.get(second)).status(), { timeout: 10_000 }).toBe(404); // and gone
  await expect(page.getByRole("status").filter({ hasText: "Image 2 removed." })).toBeAttached();
});

test("Generate waits for uploads that are still going, and says so", async ({ page }) => {
  await openEdit(page);
  await page.route("**/api/uploads", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 900)); // a slow upload
    await route.continue();
  });
  await promptBox(page).fill("something");
  await fileInput(page).setInputFiles([red(), green()]);
  await expect(page.getByRole("progressbar", { name: "Uploading red.png" })).toBeVisible();
  await expect(generateButton(page)).toBeDisabled();
  await expect(page.getByText("Waiting for 2 uploads to finish.")).toBeVisible();
  await expect(page.locator(".tray-progress")).toHaveCount(0, { timeout: 20_000 });
  await expect(generateButton(page)).toBeEnabled();
});

test("taking a picture out while it is still going up stops it, and nothing is left staged", async ({ page }) => {
  await openEdit(page);
  const received: string[] = [];
  await page.route("**/api/uploads", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.continue();
  });
  page.on("response", async (response) => {
    if (response.url().endsWith("/api/uploads") && response.status() === 201) received.push(((await response.json()) as { upload_id: string }).upload_id);
  });
  await fileInput(page).setInputFiles([red()]);
  await expect(tiles(page)).toHaveCount(1);
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await expect(tiles(page)).toHaveCount(0);
  await page.waitForTimeout(1500); // the slow request may still complete on the server; the page must clean it up
  for (const id of received) await expect.poll(async () => (await page.request.get(`/api/images/${id}`)).status(), { timeout: 10_000 }).toBe(404);
});


// ------------------------------------------------------------------ sending an edit
test("an edit on Auto sends the images in the order shown, and its card shows them numbered", async ({ page }) => {
  await openEdit(page, { numImages: 1, steps: 6 });
  await addPictures(page, [red(), green(), blue()]);
  await page.getByRole("button", { name: "Move image 3 earlier" }).click(); // red, blue, green
  const prompt = unique("put image 1 into image 2");
  const c = await send(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(c.locator(".badge", { hasText: /^Edit$/ })).toBeVisible();
  await expect(c.locator(".run-meta")).toContainText("3 images");
  await expect(c.locator(".run-meta")).toContainText("1K · 6 steps");
  await expect(c.getByRole("list", { name: "Source images" }).locator(".source-number")).toHaveText(["1", "2", "3"]);

  const [run] = await newestRuns(page);
  expect(run).toMatchObject({ mode: "edit", status: "done" });
  expect(run.inputs.map((i) => [i.position, i.width])).toEqual([[1, 300], [2, 240], [3, 200]]); // red, blue, green: the order shown
  expect(run.options).toMatchObject({ width: null, height: null, resolution: 1024, shape_from: null });
  expect(run.images).toHaveLength(1);

  // the tray stays usable for the next try, now pointing at the run's own copies
  await expect(tiles(page)).toHaveCount(3);
  const trayIds = await page.locator(".tray-tile img").evaluateAll((els) => els.map((el) => el.getAttribute("src")?.split("/")[3]));
  expect(trayIds).toEqual(run.inputs.map((i) => i.id));
  await expect(generateButton(page)).toBeEnabled();
});

test("the result follows the last image unless another is chosen, and the choice follows the picture, not the number", async ({ page }) => {
  await openEdit(page, { steps: 4 });
  await addPictures(page, [red(), green(), blue()]);
  const follows = page.getByLabel("Result follows image");
  await expect(follows).toHaveValue("3"); // the pipeline's own rule: the last
  await expect(page.getByText("Size is Auto.")).toBeVisible();

  await follows.selectOption("1"); // red: 300 x 200, landscape
  await page.getByRole("button", { name: "Move image 1 later" }).click(); // red is now number 2
  await expect(follows).toHaveValue("2"); // still red
  const first = await send(page, unique("follow red"));
  await expect(first.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  let [run] = await newestRuns(page);
  expect(run.options.shape_from).toBe(2);
  expect(run.images[0].width).toBeGreaterThan(run.images[0].height); // landscape, like red

  await follows.selectOption("3"); // the last again, which is the default and is not sent
  const second = await send(page, unique("follow the last"));
  await expect(second.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  [run] = await newestRuns(page);
  expect(run.options.shape_from).toBeNull();
});

test("a chosen shape that is removed falls back to the last image", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green(), blue()]);
  const follows = page.getByLabel("Result follows image");
  await follows.selectOption("1");
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await expect(follows).toHaveValue("2"); // two pictures left, so the last is number 2
});

// ------------------------------------------------------------------ Options in Edit mode
test("Options in Edit: Size starts on Auto, Resolution 1K/2K shows its cost, and a heavy edit is warned about", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green()]);
  await optionsButton(page).click();
  const drawer = page.getByRole("dialog", { name: "Options" });
  await expect(drawer.getByRole("radio", { name: /^Auto/ })).toHaveAttribute("aria-checked", "true");
  await expect(drawer.getByRole("radio", { name: "1K" })).toHaveAttribute("aria-checked", "true");
  const cost = drawer.getByTestId("edit-cost");
  await expect(cost).toContainText("Cost: 2 units (2 images at 1K).");
  await drawer.getByRole("radio", { name: "2K" }).click();
  await expect(cost).toContainText("Cost: 8 units (2 images at 2K).");
  await expect(cost).not.toContainText("heavy"); // 8 is at the threshold, not over it
  await drawer.getByRole("button", { name: "Done" }).click();
  await expect(optionsButton(page)).toContainText("Auto · 2K");

  await addPictures(page, [blue()], 3); // 12 units
  await expect(page.getByText("This edit is heavy (12 units)")).toBeVisible(); // under the tray too, not only in Options
  await optionsButton(page).click();
  await expect(drawer.getByTestId("edit-cost")).toContainText("This edit is heavy");
  await drawer.getByRole("radio", { name: "1K" }).click();
  await expect(drawer.getByTestId("edit-cost")).not.toContainText("heavy");
});

test("the 2K resolution is what is sent", async ({ page }) => {
  await openEdit(page, { steps: 3 });
  await addPictures(page, [red()]);
  await optionsButton(page).click();
  await page.getByRole("dialog", { name: "Options" }).getByRole("radio", { name: "2K" }).click();
  await page.getByRole("dialog", { name: "Options" }).getByRole("button", { name: "Done" }).click();
  const c = await send(page, unique("big inputs"));
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });
  await expect(c.locator(".run-meta")).toContainText("2K");
  const [run] = await newestRuns(page);
  expect(run.options.resolution).toBe(2048);
});

test("a fixed size overrides Auto, and the scale picker works on it but not on Auto", async ({ page }) => {
  await openEdit(page, { steps: 3 });
  await addPictures(page, [red(), green()]);
  for (const percent of [100, 75, 50, 25]) await expect(page.getByRole("radio", { name: `${percent}%`, exact: true })).toBeDisabled(); // Auto
  await expect(page.locator(".scale-size")).toHaveText("Auto");
  await expect(page.getByLabel("Result follows image")).toBeVisible();

  await optionsButton(page).click();
  const drawer = page.getByRole("dialog", { name: "Options" });
  await drawer.getByRole("radio", { name: /^Custom/ }).click();
  await drawer.getByRole("spinbutton", { name: "Width" }).fill("512");
  await drawer.getByRole("spinbutton", { name: "Height" }).fill("512");
  await drawer.getByRole("button", { name: "Done" }).click();
  await expect(page.getByLabel("Result follows image")).toHaveCount(0); // a size was chosen: nothing to follow
  await expect(page.getByRole("radio", { name: "50%", exact: true })).toBeEnabled();
  await page.getByRole("radio", { name: "50%", exact: true }).click();
  await expect(page.locator(".scale-size")).toHaveText("256×256");
  const c = await send(page, unique("small fixed"));
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const [run] = await newestRuns(page);
  expect(run.options).toMatchObject({ width: 256, height: 256, resolution: 1024 });
  expect(run.options.full).toBeNull(); // Regenerate larger is for Generate: an edit records no full size
  expect(run.images[0]).toMatchObject({ width: 256, height: 256 });
});

test("Generate's size and Edit's size are kept apart", async ({ page }) => {
  await openEdit(page, { aspect: "1:1" });
  await optionsButton(page).click();
  const drawer = page.getByRole("dialog", { name: "Options" });
  await drawer.getByRole("radio", { name: /^16:9/ }).click();
  await drawer.getByRole("button", { name: "Done" }).click();
  await page.getByRole("radio", { name: "Generate", exact: true }).click();
  await expect(page.locator(".scale-size")).toHaveText("2048×2048"); // Generate's own size, 1:1, untouched by the Edit choice
  await editRadio(page).click();
  await expect(page.locator(".scale-size")).toHaveText("2752×1536"); // Edit's choice, as left
});

test("Transparent is offered in Edit, with a hint when an image has transparency, and is sent", async ({ page }) => {
  await openEdit(page, { steps: 3 });
  await addPictures(page, [pic("ghost.png", 200, 200, BLUE, 128)]);
  await optionsButton(page).click();
  const drawer = page.getByRole("dialog", { name: "Options" });
  await expect(drawer.getByTestId("alpha-hint")).toContainText("has transparency");
  await drawer.getByRole("switch", { name: "Transparent background" }).check();
  await drawer.getByRole("button", { name: "Done" }).click();
  await expect(optionsButton(page)).toContainText("transparent");
  const c = await send(page, unique("keep it see-through"));
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(c.locator(".badge", { hasText: "Transparent" })).toBeVisible();
  const [run] = await newestRuns(page);
  expect(run.options.transparent).toBe(true);
});

test("no hint about transparency when no image has any", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red()]);
  await optionsButton(page).click();
  await expect(page.getByRole("dialog", { name: "Options" }).getByRole("switch", { name: "Transparent background" })).toBeVisible();
  await expect(page.getByTestId("alpha-hint")).toHaveCount(0);
});

test("the 'Extract the subject' starter fills an empty prompt and turns Transparent on; it never replaces your words", async ({ page }) => {
  await openEdit(page);
  const starter = page.getByRole("button", { name: "Extract the subject" });
  await starter.click();
  await expect(promptBox(page)).toHaveValue("Extract the main subject of image 1.");
  await expect(optionsButton(page)).toContainText("transparent");
  await expect(starter).toBeDisabled(); // the prompt is not empty now
  await promptBox(page).fill("");
  await expect(starter).toBeEnabled();
});

// ------------------------------------------------------------------ failure paths
test("an upload that expired before sending is named by its place, nothing is queued, and the tray is kept", async ({ page }) => {
  await openEdit(page);
  await addPictures(page, [red(), green()]);
  const second = (await tiles(page).nth(1).locator("img").getAttribute("src"))!.split("/")[3];
  expect((await page.request.delete(`/api/uploads/${second}`, { headers: { "X-Studio-Client": "1" } })).status()).toBe(204);
  await promptBox(page).fill(unique("will fail"));
  await generateButton(page).click();
  await expect(page.getByRole("alert").filter({ hasText: "Image 2: that upload was already used, or has expired" })).toBeVisible();
  expect(await newestRuns(page)).toHaveLength(0);
  await expect(tiles(page)).toHaveCount(2);
});

test("an image that has gone is named, blocks Generate, and goes when removed", async ({ page }) => {
  await openEdit(page, { steps: 3 });
  await addPictures(page, [red(), green()]);
  const done = await send(page, unique("a first edit"));
  await expect(done.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });

  await page.route("**/api/images/*/thumb", (route) => route.fulfill({ status: 404, json: { detail: "gone" } })); // the files are gone
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await done.getByRole("button", { name: "Reuse" }).click(); // brings the run's images back
  await expect(tiles(page)).toHaveCount(2);
  await expect(page.locator(".tray-gap", { hasText: "Missing" })).toHaveCount(2);
  await expect(page.getByText("Image 1 is no longer available. Remove it or add it again.")).toBeVisible();
  await expect(generateButton(page)).toBeDisabled();
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await expect(page.getByText("Add at least one image to edit.")).toBeVisible();
});

// ------------------------------------------------------------------ Edit this
test("Edit this on a result adds it to the tray and switches to Edit; more can be added the same way", async ({ page }) => {
  await useOptions(page, { mode: "generate", steps: 3 });
  await page.goto("/");
  await clearHistory(page);
  const first = unique("a first picture");
  await promptBox(page).fill(first);
  await page.keyboard.press("Control+Enter");
  const firstCard = card(page, first).first();
  await expect(firstCard.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const second = unique("a second picture");
  await promptBox(page).fill(second);
  await page.keyboard.press("Control+Enter");
  const secondCard = card(page, second).first();
  await expect(secondCard.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });

  await firstCard.getByRole("button", { name: "Edit this" }).click();
  await expect(editRadio(page)).toHaveAttribute("aria-checked", "true");
  await expect(tiles(page)).toHaveCount(1);
  await expect(page.getByRole("status").filter({ hasText: "Added as image 1." })).toBeVisible();
  await expect(promptBox(page)).toBeFocused();
  await secondCard.getByRole("button", { name: "Edit this" }).click();
  await expect(tiles(page)).toHaveCount(2);
  await expect(page.getByRole("status").filter({ hasText: "Added as image 2." })).toBeVisible();

  const [newest, older] = await newestRuns(page);
  const sources = await page.locator(".tray-tile img").evaluateAll((els) => els.map((el) => el.getAttribute("src")?.split("/")[3]));
  expect(sources).toEqual([older.images[0].id, newest.images[0].id]); // the two results, in the order they were added
  await expect(generateButton(page)).toBeEnabled(); // the box still holds the last prompt, and the tray has pictures: ready to send
});

test("Edit this from the viewer adds that one image of a batch, and a full tray is reported inside the viewer", async ({ page }) => {
  await useOptions(page, { mode: "generate", steps: 3, numImages: 2 });
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("a pair");
  await promptBox(page).fill(prompt);
  await page.keyboard.press("Control+Enter");
  const c = card(page, prompt).first();
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  expect(await c.getByRole("button", { name: "Edit this" }).count()).toBe(0); // a batch: pick the image in the viewer

  await c.getByRole("button", { name: /Open image 2 of 2/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Edit this" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(editRadio(page)).toHaveAttribute("aria-checked", "true");
  await expect(tiles(page)).toHaveCount(1);
  await expect(promptBox(page)).toBeFocused();
  const [run] = await newestRuns(page);
  expect(await tiles(page).locator("img").getAttribute("src")).toContain(run.images[1].id); // image 2, not 1

  // fill the tray, then try again from the viewer
  await addPictures(page, [green(), blue(), gold()], 4);
  await page.getByRole("radio", { name: "Generate", exact: true }).click(); // the tray is kept, though not shown, in Generate
  await c.getByRole("button", { name: /Open image 1 of 2/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Edit this" }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("One edit takes at most 4 images.");
  await expect(page.getByRole("dialog")).toBeVisible(); // still open: nothing was added
  await page.keyboard.press("Escape");
  await editRadio(page).click();
  await expect(tiles(page)).toHaveCount(4); // unchanged
});

// ------------------------------------------------------------------ an edit's card and the viewer
test("an edit's viewer pages through its sources and then its results, and offers Edit this on each", async ({ page }) => {
  await openEdit(page, { steps: 3 });
  await addPictures(page, [red(), green()]);
  const c = await send(page, unique("two in one out"));
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await c.getByRole("button", { name: "Open source image 2 of 2" }).click();
  const viewer = page.getByRole("dialog");
  await expect(viewer).toContainText("Source 2 of 2 · 200×300");
  await expect(viewer.getByRole("link", { name: "Download" })).toHaveCount(0); // a source is not a result
  await expect(viewer.getByRole("button", { name: "Edit this" })).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(viewer).toContainText("Source 1 of 2 · 300×200");
  await page.keyboard.press("ArrowLeft"); // wraps round to the last: the result
  await expect(viewer).toContainText(/Result 1 of 1 · seed \d+ ·/);
  await expect(viewer.getByRole("link", { name: "Download" })).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Edit this" })).toBeVisible();
  await expect(viewer.getByRole("button", { name: /^Regenerate larger/ })).toHaveCount(0); // that is for Generate
  await page.keyboard.press("Escape");
  await c.getByRole("button", { name: /Open image 1 of 1/ }).click(); // the result's own thumbnail opens the same viewer at the result
  await expect(page.getByRole("dialog")).toContainText("Result 1 of 1");
});

// ------------------------------------------------------------------ Reuse and Retry
test("Reuse brings an edit back whole: prompt, options, shape, seed locked, and its images in order", async ({ page }) => {
  await openEdit(page, { steps: 5 });
  await addPictures(page, [red(), green(), blue()]);
  await page.getByLabel("Result follows image").selectOption("1");
  const prompt = unique("combine them");
  const c = await send(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  const [run] = await newestRuns(page);

  // go somewhere else entirely
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await page.getByRole("button", { name: "Remove image 1" }).click();
  await page.getByRole("radio", { name: "Generate", exact: true }).click();
  await promptBox(page).fill("something else");

  await c.getByRole("button", { name: "Reuse" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Loaded the prompt, options and 3 images." })).toBeVisible();
  await expect(editRadio(page)).toHaveAttribute("aria-checked", "true");
  await expect(promptBox(page)).toHaveValue(prompt);
  await expect(tiles(page)).toHaveCount(3);
  await expect(page.getByLabel("Result follows image")).toHaveValue("1");
  await expect(optionsButton(page)).toContainText("Auto · 1K · 5 steps");
  await expect(optionsButton(page)).toContainText(`seed ${run.options.seed}`);
  const reused = await page.locator(".tray-tile img").evaluateAll((els) => els.map((el) => el.getAttribute("src")?.split("/")[3]));
  expect(reused).toEqual(run.inputs.map((i) => i.id)); // the same pictures, in the same order
  await expect(generateButton(page)).toBeEnabled();
});

test("Retry resends an edit with the same images, resolution and shape", async ({ page }) => {
  await openEdit(page, { steps: 3 });
  await addPictures(page, [red(), green()]);
  await page.getByLabel("Result follows image").selectOption("1");
  const prompt = unique("this will fail [fake:error]");
  const c = await send(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Failed", { timeout: 20_000 });
  const [failed] = await newestRuns(page);
  await c.getByRole("button", { name: "Retry" }).click();
  await expect(card(page, prompt)).toHaveCount(2);
  const [again] = await newestRuns(page);
  expect(again.id).not.toBe(failed.id);
  expect(again).toMatchObject({ mode: "edit", prompt });
  expect(again.inputs.map((i) => i.width)).toEqual(failed.inputs.map((i) => i.width));
  expect(again.options).toMatchObject({ resolution: 1024, shape_from: 1, seed: failed.options.seed });
  expect(again.inputs.map((i) => i.id)).not.toEqual(failed.inputs.map((i) => i.id)); // the new run owns its own copies
});

// ------------------------------------------------------------------ phone width
test("phone width: the tray is three across with no sideways scroll, and its controls stay on screen", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 900 });
  await openEdit(page);
  await addPictures(page, [red(), green(), blue(), gold()]);
  const boxes = await tiles(page).evaluateAll((els) => els.map((el) => {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width) };
  }));
  expect(new Set(boxes.slice(0, 3).map((b) => b.y)).size).toBe(1); // the first three share a row
  expect(boxes[3].y).toBeGreaterThan(boxes[0].y); // the fourth wraps
  for (const b of boxes) expect(b.x + b.w).toBeLessThanOrEqual(360);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  for (const name of ["Move image 2 earlier", "Move image 2 later", "Remove image 2"]) {
    const box = (await page.getByRole("button", { name }).boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(360);
    expect(box.width).toBeGreaterThanOrEqual(24); // big enough to press
  }
  const select = (await page.getByLabel("Result follows image").boundingBox())!;
  expect(select.x + select.width).toBeLessThanOrEqual(360);
});
