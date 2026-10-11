// End-to-end: 16:9 is the default shape of a picture (DESIGN.md §34, version 1.16; acceptance criteria 177 to 183), in a real browser against the
// real backend (fake pipeline). The pieces are covered by the unit tests (options.test.ts, test_default_shape.py); what only a browser shows is
// that a fresh page starts there, that the one-time move really happens on a reload and only once, and that the note is shown and goes away.
import { MOVED_KEY, card, clearHistory, expect, generate, promptBox, test, unique, useOptions, type Locator, type Page } from "./helpers";

const OPTIONS_KEY = "studio.options.v1";
const NOTE = "The default size is now 16:9, and your saved 1:1 was changed to it. Choose 1:1 in Options to go back.";

const optionsButton = (page: Page): Locator => page.getByRole("button", { name: /^Options/ });
const drawerOf = (page: Page): Locator => page.getByRole("dialog", { name: "Options" });
const note = (page: Page): Locator => page.locator(".toast", { hasText: NOTE });
const radio = (page: Page, name: string | RegExp): Locator => drawerOf(page).getByRole("radio", { name });
const SIXTEEN_NINE = "16:9, 2752 by 1536";
const SQUARE = "1:1, 2048 by 2048";

/** What a browser that last ran version 1.15 has saved: every option, Size on the 1:1 preset (the old default) and the Custom fields at its size. */
const saved1_15 = (over: Record<string, unknown> = {}) => ({
  mode: "generate", aspect: "1:1", editAspect: "auto", resolution: 1024, customWidth: 2048, customHeight: 2048, scale: 100, steps: 40,
  seedLocked: false, seed: 42, numImages: 1, negativePrompt: "", guidance: null, transparent: false, ...over,
});

/** Save these options in the browser, the way an earlier version left them and WITHOUT the mark that says the browser has been moved, on the
 *  first load only (a marker in sessionStorage says it has been done). Unlike `useOptions` this must not run again on a reload: the tests
 *  below reload to see that the move happens once, and a script that saved the 1:1 again would move it again. */
async function leaveSaved(page: Page, options: Record<string, unknown>) {
  await page.addInitScript(
    ({ key, value }) => {
      if (sessionStorage.getItem("seeded")) return;
      sessionStorage.setItem("seeded", "1");
      localStorage.setItem(key, JSON.stringify(value));
    },
    { key: OPTIONS_KEY, value: options },
  );
}

/** Press a preset in Options, then close the drawer. */
async function choose(page: Page, name: string | RegExp) {
  await optionsButton(page).click();
  await radio(page, name).click();
  await page.keyboard.press("Escape");
  await expect(drawerOf(page)).toBeHidden();
}

// ------------------------------------------------------------------ criterion 177: nothing saved
test("a browser with nothing saved starts at 16:9, 2752×1536, with Custom starting there too, and makes that size", async ({ page }) => {
  await page.goto("/");
  await clearHistory(page);
  await expect(optionsButton(page)).toContainText("2752×1536 · 40 steps · random seed");
  await optionsButton(page).click();
  await expect(radio(page, SIXTEEN_NINE)).toHaveAttribute("aria-checked", "true");
  await radio(page, "Custom").click();
  await expect(drawerOf(page).getByRole("spinbutton", { name: "Width" })).toHaveValue("2752"); // Custom starts at the default size
  await expect(drawerOf(page).getByRole("spinbutton", { name: "Height" })).toHaveValue("1536");
  await radio(page, SIXTEEN_NINE).click();
  await page.keyboard.press("Escape");
  await expect(note(page)).toHaveCount(0); // a browser that starts empty is never told about a move

  // The whole path: the page sends no choice of its own, and the run, its picture and its download name all say 2752×1536.
  const prompt = unique("a harbour at the default size");
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });
  await expect(c.locator(".run-meta")).toContainText("2752×1536");
  const { runs } = (await (await page.request.get("/api/runs?limit=5")).json()) as {
    runs: { prompt: string; options: Record<string, unknown>; images: { width: number; height: number; download_url: string }[] }[];
  };
  const run = runs.find((r) => r.prompt === prompt)!;
  expect(run.options).toMatchObject({ width: 2752, height: 1536 });
  expect([run.images[0].width, run.images[0].height]).toEqual([2752, 1536]);
  expect((await page.request.get(run.images[0].download_url)).headers()["content-disposition"]).toContain("_2752x1536_");
});

test("Reset to defaults goes back to 16:9 and to Custom starting at 2752×1536", async ({ page }) => {
  await useOptions(page, { aspect: "custom", customWidth: 1024, customHeight: 768, scale: 50 });
  await page.goto("/");
  await expect(optionsButton(page)).toContainText("512×384 (50%)"); // the saved Custom 1024×768 at 50%
  await optionsButton(page).click();
  await drawerOf(page).getByRole("button", { name: "Reset to defaults" }).click();
  await expect(radio(page, SIXTEEN_NINE)).toHaveAttribute("aria-checked", "true");
  await radio(page, "Custom").click();
  await expect(drawerOf(page).getByRole("spinbutton", { name: "Width" })).toHaveValue("2752");
  await expect(drawerOf(page).getByRole("spinbutton", { name: "Height" })).toHaveValue("1536");
  await radio(page, SIXTEEN_NINE).click();
  await page.keyboard.press("Escape");
  await expect(optionsButton(page)).toContainText("2752×1536 · 40 steps · random seed"); // the scale went back to 100% as well
});

test("the page and the API agree on the default", async ({ page }) => {
  const caps = (await (await page.request.get("/api/capabilities")).json()) as { defaults: Record<string, unknown> };
  expect(caps.defaults).toMatchObject({ aspect_ratio: "16:9", width: 2752, height: 1536 });
  await page.goto("/");
  await expect(optionsButton(page)).toContainText("2752×1536");
});

// ------------------------------------------------------------------ criteria 178 and 179: the one-time move
test("the first load moves a saved 1:1 to 16:9 and says so once; a 1:1 chosen afterwards stays", async ({ page }) => {
  await leaveSaved(page, saved1_15({ scale: 50, steps: 12, editAspect: "3:4" }));
  await page.goto("/");
  await expect(note(page)).toBeVisible(); // the one-time note, as a toast
  await expect(optionsButton(page)).toContainText("1376×768 (50%) · 12 steps"); // 16:9 at the saved 50%: only the shape moved
  expect(await page.evaluate((k) => localStorage.getItem(k), MOVED_KEY)).not.toBeNull();

  // Edit's saved size is not touched by the move.
  await page.getByRole("radio", { name: "Edit", exact: true }).click();
  await expect(page.locator(".scale-size")).toHaveText("896×1216"); // Edit's own 3:4 (1792×2400) at the 50% scale, as saved (1200 rounds up to 1216)
  await page.getByRole("radio", { name: "Generate", exact: true }).click();

  // A second load: no note, and still 16:9.
  await page.reload();
  await expect(optionsButton(page)).toContainText("1376×768 (50%) · 12 steps");
  await expect(note(page)).toHaveCount(0);

  // Now the person chooses 1:1 on purpose, and reloads: it stays, with no note (criterion 179).
  await choose(page, SQUARE);
  await expect(optionsButton(page)).toContainText("1024×1024 (50%)");
  await page.reload();
  await expect(optionsButton(page)).toContainText("1024×1024 (50%) · 12 steps");
  await expect(note(page)).toHaveCount(0);
  await optionsButton(page).click();
  await expect(radio(page, SQUARE)).toHaveAttribute("aria-checked", "true");
});

test("the note goes away by itself, and the move is already saved when it does", async ({ page }) => {
  await leaveSaved(page, saved1_15());
  await page.goto("/");
  await expect(note(page)).toBeVisible();
  expect(JSON.parse((await page.evaluate((k) => localStorage.getItem(k), OPTIONS_KEY))!).aspect).toBe("16:9"); // saved at once, not on the next change
  await expect(note(page)).toBeHidden({ timeout: 10_000 }); // a plain toast, like the others (§34.5 choice 4)
});

for (const [name, options, summary] of [
  ["another preset", saved1_15({ aspect: "4:3" }), "2400×1792 · 40 steps"],
  ["a Custom size", saved1_15({ aspect: "custom", customWidth: 1024, customHeight: 768 }), "1024×768 · 40 steps"],
  ["a Custom of 2048×2048", saved1_15({ aspect: "custom" }), "2048×2048 · 40 steps"],
] as const) {
  test(`${name} is left as it is on the first load, with no note, and a later 1:1 stays`, async ({ page }) => {
    await leaveSaved(page, options);
    await page.goto("/");
    await expect(optionsButton(page)).toContainText(summary);
    await expect(note(page)).toHaveCount(0);
    expect(await page.evaluate((k) => localStorage.getItem(k), MOVED_KEY)).not.toBeNull(); // the move is marked as done even though nothing moved

    await choose(page, SQUARE); // chosen afterwards, on purpose
    await page.reload();
    await expect(optionsButton(page)).toContainText("2048×2048 · 40 steps");
    await expect(note(page)).toHaveCount(0);
  });
}

test("a saved Edit size of 1:1 is not moved, and the Generate size next to it is", async ({ page }) => {
  await leaveSaved(page, saved1_15({ mode: "edit", editAspect: "1:1" }));
  await page.goto("/");
  await expect(note(page)).toBeVisible();
  await expect(optionsButton(page)).toContainText("2048×2048 · 1K"); // Edit mode, Edit's own 1:1
  await page.getByRole("radio", { name: "Generate", exact: true }).click();
  await expect(optionsButton(page)).toContainText("2752×1536"); // Generate's moved
});

// ------------------------------------------------------------------ storage blocked
test("with storage blocked the page works, starts at 16:9, says storage is blocked and says nothing about a move", async ({ page }) => {
  await page.addInitScript(() => {
    // Every use of localStorage throws, as in a private window or with site data blocked.
    Object.defineProperty(window, "localStorage", { get() { throw new DOMException("blocked", "SecurityError"); } });
  });
  await page.goto("/");
  await expect(page.locator(".toast", { hasText: "This browser is blocking storage" })).toBeVisible();
  await expect(optionsButton(page)).toContainText("2752×1536 · 40 steps");
  await expect(note(page)).toHaveCount(0);
  await choose(page, SQUARE); // it still works in memory
  await expect(optionsButton(page)).toContainText("2048×2048 · 40 steps");
});

// ------------------------------------------------------------------ criterion 180: Edit stays on Auto
test("Edit starts on Auto, and Reset to defaults leaves it there", async ({ page }) => {
  await page.goto("/");
  await clearHistory(page);
  await page.getByRole("radio", { name: "Edit", exact: true }).click();
  await expect(optionsButton(page)).toContainText("Auto · 1K · 40 steps"); // not 16:9: Edit follows its pictures
  await optionsButton(page).click();
  await expect(radio(page, "Auto from your images")).toHaveAttribute("aria-checked", "true");
  await radio(page, "4:3, 2400 by 1792").click(); // choose a size for the edit ...
  await expect(radio(page, "4:3, 2400 by 1792")).toHaveAttribute("aria-checked", "true");
  await drawerOf(page).getByRole("button", { name: "Reset to defaults" }).click(); // ... and Reset
  await page.keyboard.press("Escape");
  await page.getByRole("radio", { name: "Edit", exact: true }).click(); // (Reset also returns to Generate, as it always has)
  await expect(optionsButton(page)).toContainText("Auto · 1K · 40 steps");
});

// ------------------------------------------------------------------ criterion 183: runs that exist keep their size
test("Reuse of a square run brings back its own 1:1, not the new default", async ({ page }) => {
  await useOptions(page, { aspect: "1:1", scale: 25, steps: 3 }); // a 1:1 chosen on purpose, at 25% so that the picture is small: 512×512
  await page.goto("/");
  await clearHistory(page);
  const prompt = unique("a square lighthouse");
  const c = await generate(page, prompt);
  await expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 20_000 });
  await expect(c.locator(".run-meta")).toContainText("512×512");

  // Go back to the default shape and the full scale (whatever the server says that is: this test is about Reuse, not about the default),
  // then Reuse the square run.
  const { defaults } = (await (await page.request.get("/api/capabilities")).json()) as { defaults: { width: number; height: number } };
  await optionsButton(page).click();
  await drawerOf(page).getByRole("button", { name: "Reset to defaults" }).click();
  await page.keyboard.press("Escape");
  await expect(optionsButton(page)).toContainText(`${defaults.width}×${defaults.height} · 40 steps`);
  await card(page, prompt).getByRole("button", { name: "Reuse" }).click();
  await expect(promptBox(page)).toHaveValue(prompt);
  await expect(optionsButton(page)).toContainText("512×512 (25%)"); // the run's own size, as 1:1 at 25%
  await optionsButton(page).click();
  await expect(radio(page, SQUARE)).toHaveAttribute("aria-checked", "true");
});
