// End-to-end: pictures by paste, anywhere on the page, from Generate, into Upscale a picture… (DESIGN.md §36, criteria 211 to 219), in a real browser against the
// real server with the fake pipeline. A paste is made the way a browser makes one: a ClipboardEvent with a DataTransfer, aimed at an element; the page says it
// took the paste by cancelling it, which is what `paste()` returns.
import { readFileSync } from "node:fs";
import { clearHistory, expect, promptBox, test, useOptions, type Locator, type Page } from "./helpers";
import { GOLD, GREEN, RED, png } from "./png";

type Pic = { name: string; mimeType: string; buffer: Buffer };
const pic = (name: string, width: number, height: number, color = RED): Pic => ({ name, mimeType: "image/png", buffer: png(width, height, color) });
const red = () => pic("red.png", 300, 200, RED);
const green = () => pic("green.png", 200, 300, GREEN);
const gold = () => pic("gold.png", 220, 220, GOLD);
/** Width and height from a PNG's header (IHDR). */
const pngSize = (bytes: Buffer): [number, number] => [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];

const tiles = (page: Page): Locator => page.locator(".tray-tile");
const modeRadio = (page: Page, name: "Generate" | "Edit"): Locator => page.getByRole("radio", { name, exact: true });
const toast = (page: Page, text: string): Locator => page.getByRole("status").filter({ hasText: text });
const upscaleButton = (page: Page): Locator => page.getByRole("button", { name: /^(Upscale a picture…|Upscaling…)/ });
const uploadsDone = (page: Page) => expect(page.locator(".tray-progress")).toHaveCount(0, { timeout: 20_000 });

/** Paste `pictures` (and `text`, if given) at the element `selector` (the page body if null). Returns whether the page cancelled the paste, which is how it takes one. */
async function paste(page: Page, selector: string | null, pictures: Pic[], text?: string): Promise<boolean> {
  return page.evaluate(({ selector, files, text }) => {
    const transfer = new DataTransfer();
    for (const file of files) transfer.items.add(new File([Uint8Array.from(atob(file.b64), (c) => c.charCodeAt(0))], file.name, { type: file.type }));
    if (text !== undefined) transfer.setData("text/plain", text);
    const target = selector ? document.querySelector(selector)! : document.body;
    const event = new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true });
    target.dispatchEvent(event);
    return event.defaultPrevented;
  }, { selector, files: pictures.map((p) => ({ name: p.name, b64: p.buffer.toString("base64"), type: p.mimeType })), text });
}

async function dropFiles(page: Page, selector: string, pictures: Pic[]) {
  const transfer = await page.evaluateHandle((list) => {
    const data = new DataTransfer();
    for (const file of list) data.items.add(new File([Uint8Array.from(atob(file.b64), (c) => c.charCodeAt(0))], file.name, { type: file.type }));
    return data;
  }, pictures.map((p) => ({ name: p.name, b64: p.buffer.toString("base64"), type: p.mimeType })));
  await page.locator(selector).dispatchEvent("dragover", { dataTransfer: transfer });
  await page.locator(selector).dispatchEvent("drop", { dataTransfer: transfer });
}

async function open(page: Page, mode: "generate" | "edit") {
  await useOptions(page, { mode });
  await page.goto("/");
  await clearHistory(page);
  await expect(promptBox(page)).toBeVisible(); // the page has what it needs from the server: until then it takes no paste, by design
}

// ------------------------------------------------------------------ the Images tab (criteria 211, 212)
test("a picture pasted with the cursor on the bare page or on a button goes to the tray, in order", async ({ page }) => {
  await open(page, "edit");
  expect(await paste(page, null, [red(), green()])).toBe(true);
  await expect(tiles(page)).toHaveCount(2);
  await uploadsDone(page);
  expect(await paste(page, 'button[aria-haspopup="dialog"]', [gold()])).toBe(true); // the Options button
  await expect(tiles(page)).toHaveCount(3);
  await uploadsDone(page);
  await expect(page.locator(".tray-badge")).toHaveText(["1", "2", "3"]);
});

test("one paste in the prompt box adds each picture once, and a picture wins over the text beside it", async ({ page }) => {
  await open(page, "edit");
  expect(await paste(page, "#prompt", [red(), green()], "A1\tB1")).toBe(true);
  await expect(tiles(page)).toHaveCount(2);
  await uploadsDone(page);
  await expect(tiles(page)).toHaveCount(2); // not 4: the prompt box and the page did not both act
  await expect(promptBox(page)).toHaveValue(""); // the text was not pasted: the picture won
});

test("a paste of text alone is never taken, in the prompt box or anywhere", async ({ page }) => {
  await open(page, "edit");
  await promptBox(page).focus();
  expect(await paste(page, "#prompt", [], "some words")).toBe(false);
  expect(await paste(page, null, [], "some words")).toBe(false);
  await expect(tiles(page)).toHaveCount(0);
});

test("a paste while the Options drawer is open adds nothing; closed again, it works", async ({ page }) => {
  await open(page, "edit");
  await page.getByRole("button", { name: /^Options/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(await paste(page, null, [red()])).toBe(false);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(tiles(page)).toHaveCount(0);
  expect(await paste(page, null, [red()])).toBe(true);
  await expect(tiles(page)).toHaveCount(1);
});

// ------------------------------------------------------------------ Generate (criteria 213, 214)
test("in Generate a paste switches the card to Edit with the prompt kept, counts the pictures, and Undo goes back", async ({ page }) => {
  await open(page, "generate");
  await promptBox(page).fill("a lighthouse in fog");
  await expect(page.locator("[data-generate-pictures-help]")).toHaveText("Paste or drop pictures to work from them: the card switches to Edit.");
  expect(await paste(page, null, [red(), green(), gold()])).toBe(true);
  await expect(toast(page, "Switched to Edit: 3 pictures added. Refer to them as image 1, 2, 3.")).toBeVisible();
  await expect(modeRadio(page, "Edit")).toHaveAttribute("aria-checked", "true");
  await expect(tiles(page)).toHaveCount(3);
  await expect(promptBox(page)).toHaveValue("a lighthouse in fog");
  await expect(page.locator("[data-generate-pictures-help]")).toHaveCount(0); // Edit shows the tray instead

  await toast(page, "Switched to Edit").getByRole("button", { name: "Undo" }).click();
  await expect(modeRadio(page, "Generate")).toHaveAttribute("aria-checked", "true");
  await expect(page.locator(".tray")).toHaveCount(0);
  await expect(promptBox(page)).toHaveValue("a lighthouse in fog");
  await modeRadio(page, "Edit").click();
  await expect(tiles(page)).toHaveCount(0); // exactly those pictures were taken out
});

test("Undo takes out only the pictures that paste added, and leaves the ones already in the tray", async ({ page }) => {
  await open(page, "edit");
  expect(await paste(page, null, [red()])).toBe(true);
  await expect(tiles(page)).toHaveCount(1);
  await uploadsDone(page);
  await modeRadio(page, "Generate").click();
  expect(await paste(page, null, [green(), gold()])).toBe(true);
  await expect(toast(page, "Switched to Edit: 2 pictures added. Refer to them as image 2, 3.")).toBeVisible(); // numbered after the one that was there
  await expect(tiles(page)).toHaveCount(3);
  await toast(page, "Switched to Edit").getByRole("button", { name: "Undo" }).click();
  await modeRadio(page, "Edit").click();
  await expect(tiles(page)).toHaveCount(1);
  await expect(page.locator(".tray-badge")).toHaveText(["1"]);
});

test("in Generate a dropped file that is not a picture changes nothing and is named", async ({ page }) => {
  await open(page, "generate");
  await dropFiles(page, ".prompt-card", [{ name: "notes.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 not a picture") }]);
  await expect(page.getByRole("alert").filter({ hasText: "notes.pdf: That isn't a PNG, JPEG or WebP image." })).toBeVisible();
  await expect(modeRadio(page, "Generate")).toHaveAttribute("aria-checked", "true");
  await modeRadio(page, "Edit").click();
  await expect(tiles(page)).toHaveCount(0);
  await expect(page.locator(".tray-note")).toHaveCount(0); // not left behind in a tray nobody was looking at
});

// ------------------------------------------------------------------ Music and Vector (criterion 216)
test("a picture pasted or dropped on the Vector tab is not taken, and a note says where pictures are used", async ({ page }) => {
  await open(page, "edit");
  await page.getByRole("tab", { name: "Vector" }).click();
  expect(await paste(page, null, [red()])).toBe(true);
  await expect(toast(page, "Pictures are used on the Images tab.")).toBeVisible();
  await dropFiles(page, "body", [red()]);
  await expect(toast(page, "Pictures are used on the Images tab.")).toHaveCount(2);
  await page.getByRole("tab", { name: "Images" }).click();
  await expect(tiles(page)).toHaveCount(0);
});

// ------------------------------------------------------------------ Upscale a picture… (criterion 218, 219)
test("Upscale a picture… takes a picture pasted while it has the focus, and a paste anywhere else never upscales", async ({ page }) => {
  await open(page, "edit");
  await expect(upscaleButton(page)).toHaveAttribute("title", /or drop or paste a picture here\./);
  let downloads = 0;
  page.on("download", () => (downloads += 1));
  expect(await paste(page, null, [pic("elsewhere.png", 1920, 1080)])).toBe(true); // the tray's
  await expect(tiles(page)).toHaveCount(1);
  expect(downloads).toBe(0);

  await upscaleButton(page).focus();
  const downloading = page.waitForEvent("download");
  expect(await paste(page, '[data-action="upscale-picture"]', [pic("shot.png", 1920, 1080)])).toBe(true);
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^upscale_shot_3840x2160_/);
  expect(pngSize(readFileSync(await download.path()))).toEqual([3840, 2160]);
  await expect(tiles(page)).toHaveCount(1); // the tray did not take it
});

test("Upscale a picture… takes a dropped picture, lighting up while one is over it, and uses only the first of several", async ({ page }) => {
  await open(page, "edit");
  const first = pic("first.png", 1920, 1080);
  const second = pic("second.png", 1920, 1080);
  const transfer = await page.evaluateHandle((list) => {
    const data = new DataTransfer();
    for (const file of list) data.items.add(new File([Uint8Array.from(atob(file.b64), (c) => c.charCodeAt(0))], file.name, { type: "image/png" }));
    return data;
  }, [first, second].map((p) => ({ name: p.name, b64: p.buffer.toString("base64") })));
  await upscaleButton(page).dispatchEvent("dragover", { dataTransfer: transfer });
  await expect(upscaleButton(page)).toHaveClass(/dropping/);
  let downloads = 0;
  page.on("download", () => (downloads += 1));
  const downloading = page.waitForEvent("download");
  await upscaleButton(page).dispatchEvent("drop", { dataTransfer: transfer });
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^upscale_first_/);
  await expect(toast(page, "Upscaling the first picture only; the other 1 was left out.")).toBeVisible();
  await expect(upscaleButton(page)).not.toHaveClass(/dropping/);
  await expect(upscaleButton(page)).toHaveText("Upscale a picture…");
  expect(downloads).toBe(1);
  await expect(tiles(page)).toHaveCount(0); // a drop on the control is not the tray's
});

test("a paste while Upscale is still working on the last picture is ignored, in words", async ({ page }) => {
  await open(page, "edit");
  let posts = 0;
  await page.route("**/api/upscale*", async (route) => {
    posts += 1;
    await new Promise((resolve) => setTimeout(resolve, 900));
    await route.continue();
  });
  await upscaleButton(page).focus();
  const downloading = page.waitForEvent("download");
  await paste(page, '[data-action="upscale-picture"]', [pic("one.png", 1920, 1080)]);
  await expect(upscaleButton(page)).toHaveText("Upscaling…");
  await paste(page, '[data-action="upscale-picture"]', [pic("two.png", 1920, 1080)]);
  await expect(toast(page, "Still upscaling the last picture.")).toBeVisible();
  await downloading;
  expect(posts).toBe(1);
});

// ------------------------------------------------------------------ a phone (the prompt card and the tray with several pictures)
test("on a phone the Generate help line and a tray of several pictures fit with no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await open(page, "generate");
  await expect(page.locator("[data-generate-pictures-help]")).toBeVisible();
  expect(await paste(page, null, [red(), green(), gold(), red(), green()])).toBe(true);
  await expect(tiles(page)).toHaveCount(4); // the shipped limit is 4: the fifth is skipped and counted
  await expect(page.getByText("1 image did not fit: one edit takes at most 4.")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});
