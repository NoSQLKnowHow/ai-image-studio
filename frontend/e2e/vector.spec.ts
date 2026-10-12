// End-to-end: the Vector tab (DESIGN.md §31, §31.12.7 and §31.12.8; criteria 135-143), in a real browser against the real server with the fake pipeline
// (the image worker draws a stand-in picture and the clean-up turns it into an SVG).
//
// Written against the contract (backend/tests/vector_contract.json) before the three builds were merged; since the merge it runs against the real server. What
// the tests do not assume: how many layers the clean-up finds in the stand-in picture (they check the rules that hold for any picture: at most colours - 1
// layers, white never has one, every note shown), and how fast a run is. The scenarios under "the browser-level mutation round of 1.17", at the end of the
// file, were added when deliberate bugs in the page's wiring got through the first 21.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { clearHistory, expect, generate, test, unique, useOptions, type Locator, type Page } from "./helpers";

// A run of a few versions takes a while with the real clean-up behind a stand-in picture: give every test here more than the default 45 s.
test.describe.configure({ timeout: 120_000 });

// the studio's API wants this header on every write: its guard against requests from other web pages
const ASK = { "X-Studio-Client": "1" };
// The contract file the server's tests and the page's tests are checked against. Playwright runs from `frontend/`, as playwright.config.ts relies on.
const CONTRACT = JSON.parse(readFileSync(resolve("../backend/tests/vector_contract.json"), "utf8"));
const CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"; // §31.12.7: what an SVG is served with

type ApiVector = {
  id: string; idx: number; seed: number; layers: number; check: { layers: { id: string; colour: string }[]; colours_asked: number; colours_found: number };
  url: string; download_url: string; zip_url: string; png_url: string; png_download_url: string; preview_url: string;
};
type ApiRun = {
  id: string; status: string; mode: string; pinned: boolean; prompt: string; project_id: string | null; deleted_at: string | null; error: unknown;
  options: Record<string, unknown>; vectors: ApiVector[];
};

// ------------------------------------------------------------------ locators and helpers
const vectorTab = (page: Page): Locator => page.getByRole("tab", { name: "Vector" });
const musicTab = (page: Page): Locator => page.getByRole("tab", { name: "Music" });
const imagesTab = (page: Page): Locator => page.getByRole("tab", { name: "Images" });
const form = (page: Page): Locator => page.getByRole("region", { name: "New vector" });
const ideaBox = (page: Page): Locator => form(page).getByLabel("Prompt", { exact: true });
const recipeBox = (page: Page): Locator => form(page).getByLabel("What the studio sends");
const negativeBox = (page: Page): Locator => form(page).getByLabel("What the studio keeps out (fixed)");
const make = (page: Page): Locator => form(page).getByRole("button", { name: "Make vector" });
const vectorCard = (page: Page, text: string): Locator => page.locator("article.vector-card", { hasText: text });
const bar = (page: Page): Locator => page.locator("[data-filter-bar]:visible");
const allOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^All/ });
const keptOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Kept/ });
const deletedOption = (page: Page): Locator => bar(page).getByRole("radio", { name: /^Deleted/ });
const toastWith = (page: Page, text: string | RegExp): Locator => page.locator(".toast", { hasText: text });
const dialog = (page: Page, name: string | RegExp): Locator => page.getByRole("alertdialog", { name });
const radio = (page: Page, group: string, name: string): Locator => form(page).getByRole("radiogroup", { name: group }).getByRole("radio", { name });
const pill = (page: Page): Locator => page.locator('button[aria-controls="model-details"]');
const done = (c: Locator) => expect(c.locator(".badge").first()).toHaveText("Done", { timeout: 90_000 });

async function openVector(page: Page) {
  await page.goto("/");
  await vectorTab(page).click();
  await expect(form(page)).toBeVisible();
}

/** Write an idea and press Make vector; the card it makes. */
async function makeVector(page: Page, idea: string): Promise<Locator> {
  await ideaBox(page).fill(idea);
  await make(page).click();
  const c = vectorCard(page, idea).first();
  await expect(c).toBeVisible();
  return c;
}

// the run as the SERVER has it: the tests check the server's truth as well as what the page shows
async function status(page: Page, id: string): Promise<ApiRun> {
  return (await (await page.request.get(`/api/runs/${id}`)).json()) as ApiRun;
}

/** Make a vector run over the API and wait for it. The sheet is small and the colours few so that it is as quick as a Vector run can be. */
async function makeRun(page: Page, prompt: string, options: Record<string, unknown> = {}): Promise<string> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { mode: "vector", prompt, options: { colours: 2, width_mm: 40, height_mm: 40, seed: 7, ...options } } });
  expect(posted.status(), await posted.text()).toBe(201);
  const { id } = (await posted.json()) as { id: string };
  await expect.poll(async () => (await status(page, id)).status, { timeout: 90_000 }).toBe("done");
  return id;
}

/** The PNG's width and height, read from its header (the first chunk is always IHDR). */
function pngSize(bytes: Buffer): [number, number] {
  expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a"); // the PNG signature
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
}

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await useOptions(page);
});

// ------------------------------------------------------------------ the tab, and the form's limits
test("Vector is the third tab: the arrow keys reach it, only its panel is shown, and the choice is remembered", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("tab")).toHaveText(["Images", "Music", "Vector"]);
  await musicTab(page).click();
  await page.keyboard.press("ArrowRight");
  await expect(vectorTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(vectorTab(page)).toBeFocused();
  await expect(form(page)).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveCount(1); // the Images prompt is hidden: only this tab's is on screen
  await expect(page.getByRole("tabpanel", { name: "Vector" })).toBeVisible();
  await page.reload();
  await expect(vectorTab(page)).toHaveAttribute("aria-selected", "true"); // remembered
  await expect(form(page)).toBeVisible();
  // the tab list is one stop for the Tab key, on the tab that is selected; the arrow keys reach the others (roving tabindex)
  await expect(vectorTab(page)).toHaveAttribute("tabindex", "0");
  await expect(imagesTab(page)).toHaveAttribute("tabindex", "-1");
  await expect(musicTab(page)).toHaveAttribute("tabindex", "-1");
  await vectorTab(page).focus();
  await page.keyboard.press("ArrowRight");
  await expect(imagesTab(page)).toHaveAttribute("aria-selected", "true"); // wraps round
});

test("the form's defaults, limits and presets are the server's (capabilities), not the page's", async ({ page }) => {
  const caps = (await (await page.request.get("/api/capabilities")).json()) as { modes: string[]; vector: Record<string, unknown>; limits: { vector: Record<string, any> } };
  expect(caps.modes).toContain("vector");
  expect(caps.vector).toMatchObject({ available: true });
  const v = caps.limits.vector;
  await openVector(page);
  await expect(form(page).locator("[data-vector-colours-value]")).toHaveText(String(v.colours.default));
  await expect(form(page).getByLabel("Width (mm)")).toHaveValue(String(v.size_mm.default[0]));
  await expect(form(page).getByLabel("Height (mm)")).toHaveValue(String(v.size_mm.default[1]));
  for (const label of ["Width (mm)", "Height (mm)"]) {
    await expect(form(page).getByLabel(label)).toHaveAttribute("min", String(v.size_mm.min));
    await expect(form(page).getByLabel(label)).toHaveAttribute("max", String(v.size_mm.max));
  }
  await expect(form(page).getByLabel("Smallest feature (mm)")).toHaveValue(String(v.min_feature_mm.default));
  await expect(form(page).getByLabel("Kerf (mm)")).toHaveValue(String(v.kerf_mm.default));
  await expect(form(page).getByLabel("Bleed (mm)")).toHaveValue(String(v.bleed_mm.default));
  await expect(form(page).getByRole("radiogroup", { name: "Versions" }).getByRole("radio")).toHaveCount(v.versions.max - v.versions.min + 1);
  // each preset the server lists is a button that sets both sides
  for (const preset of v.presets as { name: string; width_mm: number; height_mm: number }[]) {
    await form(page).getByRole("button", { name: `${preset.name} · ${preset.width_mm} × ${preset.height_mm} mm` }).click();
    await expect(form(page).getByLabel("Width (mm)")).toHaveValue(String(preset.width_mm));
    await expect(form(page).getByLabel("Height (mm)")).toHaveValue(String(preset.height_mm));
  }
  // the stepper stops at the server's own ends
  const more = form(page).getByRole("button", { name: "More colours" });
  for (let n = v.colours.default; n < v.colours.max; n++) await more.click();
  await expect(more).toBeDisabled();
  await expect(form(page).locator("[data-vector-colours-value]")).toHaveText(String(v.colours.max));
  await expect(form(page).locator("[data-vector-layers-line]")).toContainText(`Up to ${v.colours.max - 1} layers`);
  await radio(page, "Layers by", "Shape").click();
  await expect(form(page).locator("[data-vector-layers-line]")).toContainText(`Up to ${v.shape_layer_cap} pieces`);
});

test("the server's answers have exactly the keys the contract lists, so the page can read them", async ({ page }) => {
  const id = await makeRun(page, unique("a contract check"), { colours: 3 });
  const run = (await status(page, id)) as ApiRun & Record<string, unknown>;
  expect(Object.keys(run).sort()).toEqual(Object.keys(CONTRACT.run).sort());
  expect(Object.keys(run.options).sort()).toEqual(Object.keys(CONTRACT.run.options).sort());
  expect(run.vectors.length).toBeGreaterThan(0);
  const item = run.vectors[0] as unknown as Record<string, unknown>;
  expect(Object.keys(item).sort()).toEqual(Object.keys(CONTRACT.run.vectors[0]).sort());
  expect(Object.keys(item.check as object).sort()).toEqual(Object.keys(CONTRACT.run.vectors[0].check).sort());
  for (const layer of (item.check as { layers: object[] }).layers) expect(Object.keys(layer).sort()).toEqual(Object.keys(CONTRACT.run.vectors[0].check.layers[0]).sort());
  const caps = (await (await page.request.get("/api/capabilities")).json()) as { vector: object; limits: { vector: object } };
  expect(Object.keys(caps.vector).sort()).toEqual(Object.keys(CONTRACT.capabilities.vector).sort());
  expect(Object.keys(caps.limits.vector).sort()).toEqual(expect.arrayContaining(Object.keys(CONTRACT.capabilities.limits_vector).sort())); // the page's optional PNG range may be there too
  // the counts have a third tab, each with the same keys as before
  const counts = (await (await page.request.get("/api/runs/counts")).json()) as Record<string, Record<string, number>>;
  expect(Object.keys(counts).sort()).toEqual(["image", "music", "vector"]);
  for (const tab of ["all", "kept", "deleted"]) expect(counts.vector[tab]).toEqual(expect.any(Number));
  // the history can be asked for the Vector tab's runs alone
  const listed = (await (await page.request.get("/api/runs?kind=vector")).json()) as { runs: ApiRun[] };
  expect(listed.runs.map((r) => r.id)).toContain(id);
  expect(listed.runs.every((r) => r.mode === "vector")).toBe(true);
});

// ------------------------------------------------------------------ making a vector, start to finish
test("making a vector: the request, both stages, and the card with everything the check found", async ({ page }) => {
  await openVector(page);
  const idea = unique("a fox head");
  await ideaBox(page).fill(idea);
  await expect(make(page)).toBeEnabled();

  // the box "What the studio sends" is written by the server from the prompt; the negative text is fixed and read-only
  const token = idea.split(" ").at(-1)!; // the unique part of the idea
  await expect(recipeBox(page)).toHaveValue(new RegExp(token), { timeout: 10_000 }); // the idea is in the words (the studio adds its own around it)
  const written = await recipeBox(page).inputValue();
  expect(written.length).toBeGreaterThan(idea.length);
  await expect(negativeBox(page)).not.toHaveValue("");
  await expect(negativeBox(page)).toHaveAttribute("readonly", "");

  // three colours (white and two inks), the first ink red; two versions so that there is time to see both stages
  const more = form(page).getByRole("button", { name: "More colours" });
  await more.click();
  await expect(form(page).locator("[data-vector-layers-line]")).toHaveText("Up to 2 layers, one for each ink. White, the bare material, gets none.");
  await expect(form(page).locator('[data-ink="white"]')).toHaveText("White, the bare material, no layer");
  await form(page).getByRole("button", { name: "Choose a colour for ink 1" }).click();
  await form(page).getByLabel("Ink 1 colour").fill("#ff0000");
  await expect(form(page).locator('[data-ink="1"] code')).toHaveText("#FF0000");
  await radio(page, "Versions", "2").click();
  await radio(page, "Kind of artwork", "Flat colour art").click();
  await form(page).getByLabel("Lock seed").check();
  await form(page).getByLabel("Seed", { exact: true }).fill("4242");

  const sent = page.waitForRequest((r) => r.url().endsWith("/api/runs") && r.method() === "POST");
  await ideaBox(page).press("Control+Enter");
  const body = (await sent).postDataJSON();
  expect(body.mode).toBe("vector");
  expect(body.prompt).toBe(idea);
  // exactly the keys of the contract's request, with the values the form had
  expect(Object.keys(body.options).sort()).toEqual(Object.keys(CONTRACT.create_request.options).sort());
  expect(body.options).toMatchObject({ colours: 3, palette: ["#FF0000"], kind: "flat", layers_by: "colour", arrangement: "side_by_side", seed: 4242, num_images: 2, recipe: null });
  expect(body.options.palette).not.toContain("#FFFFFF"); // white is never listed

  const c = vectorCard(page, idea).first();
  await expect(c).toBeVisible();
  // both stages are on the card while it works (drawing by the model, then tracing)
  await expect(c.locator('[data-phase="draw"] .phase-label')).toHaveText("Drawing");
  await expect(c.locator('[data-phase="trace"] .phase-label')).toHaveText("Tracing");
  await expect(c.locator(".progress")).toContainText(/Version \d of 2/);
  await done(c);
  await expect(c.locator("[data-phase]")).toHaveCount(0); // the bars go when it is done

  // the card: preview in an <img>, what was asked and found, the layers, the smallest feature, the notes
  const run = await status(page, (await c.getAttribute("data-run-id"))!);
  expect(run.status).toBe("done");
  expect(run.vectors.length).toBeGreaterThan(0);
  const versions = c.locator(".vector-item");
  await expect(versions).toHaveCount(run.vectors.length);
  const first = versions.first();
  const preview = first.locator("img");
  await expect(preview).toHaveAttribute("src", run.vectors[0].preview_url);
  await expect.poll(() => preview.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
  await expect(first.locator("[data-vector-colours]")).toContainText(/^\d+ colours? found, 3 asked\./);
  await expect(first.locator("[data-vector-feature]")).toContainText(/^Smallest feature: .* \(your limit is 0\.5 mm\)\.$/);
  const rows = first.locator("table.vector-layers tbody tr");
  const layerCount = await rows.count();
  expect(layerCount).toBeGreaterThan(0);
  expect(layerCount).toBeLessThanOrEqual(2); // at most colours - 1: white gets no layer
  for (let i = 0; i < layerCount; i++) {
    await expect(rows.nth(i).locator(".layer-name")).toHaveText(new RegExp(`^\\s*Layer ${i + 1} \\(`));
    await expect(rows.nth(i).locator("td").first()).toHaveText(/^\d+$/);
    await expect(rows.nth(i).locator("td").last()).toHaveText(/^[\d.]+ (mm|m)$/);
  }
  expect(run.vectors[0].check.layers.map((l) => l.colour)).not.toContain("#FFFFFF"); // white has no layer
  // every note the check made is on the card
  const noted = (run.vectors[0].check as unknown as { notes: { code: string; message: string }[] }).notes;
  await expect(first.locator(".vector-notes li")).toHaveCount(noted.length);
  for (const note of noted) await expect(first.locator(`[data-note-code="${note.code}"]`)).toContainText(note.message);
  // the page shows the SVG only as a picture of it (an <img>), never as markup of its own
  await expect(c.locator("object, embed, iframe")).toHaveCount(0);
  await expect(c.locator("svg:not([aria-hidden='true'])")).toHaveCount(0);
  expect(run.options).toMatchObject({ colours: 3, palette: ["#FF0000"], seed: 4242, num_images: 2 });
});

test("the card's downloads: the SVG, the layers ZIP, and a PNG at the size typed, with the headers the contract names", async ({ page }) => {
  const idea = unique("downloads");
  const id = await makeRun(page, idea, { colours: 3 });
  const run = await status(page, id);
  const item = run.vectors[0];
  await openVector(page);
  const c = vectorCard(page, idea).first();
  await expect(c).toBeVisible();

  // Download SVG: the server's own link, an attachment named for the idea, the size, the colours, the seed and the time
  const svgLink = c.getByRole("link", { name: "Download SVG" });
  await expect(svgLink).toHaveAttribute("href", item.download_url);
  const svg = await page.request.get(item.download_url);
  expect(svg.status()).toBe(200);
  expect(svg.headers()["content-type"]).toContain("image/svg+xml");
  expect(svg.headers()["content-security-policy"]).toBe(CSP);
  expect(svg.headers()["x-content-type-options"]).toBe("nosniff");
  expect(svg.headers()["content-disposition"]).toMatch(/^attachment; filename="?vector_.*_40x40mm_\d+c_s7_\d{8}-\d{6}\.svg"?$/);
  const text = await svg.text();
  expect(text).toMatch(/<svg[\s>]/);
  expect(text).toMatch(/width="40(\.0+)?mm"/); // a real size in millimetres
  expect(text).toMatch(/height="40(\.0+)?mm"/);
  expect(text).toMatch(/viewBox="0 0 40(\.\d+)? 40(\.\d+)?"/);
  expect(text).not.toMatch(/<script|<image|<foreignObject|\son\w+=|xlink:href|href=/i); // nothing a program could run or follow
  // the same file, shown (not downloaded), carries the same protection
  const shown = await page.request.get(item.url);
  expect(shown.headers()["content-security-policy"]).toBe(CSP);
  expect(shown.headers()["content-disposition"] ?? "").not.toContain("attachment");

  // Download layers (ZIP)
  await expect(c.getByRole("link", { name: "Download layers (ZIP)" })).toHaveAttribute("href", item.zip_url);
  const zip = await page.request.get(item.zip_url);
  expect(zip.status()).toBe(200);
  expect(zip.headers()["content-disposition"]).toMatch(/^attachment; filename="?vector_.*-layers\.zip"?$/);
  expect((await zip.body()).subarray(0, 2).toString()).toBe("PK");

  // Download PNG: the size box starts at 2048, the link is the server's with the size added, and the file is that size on its long side
  const size = c.getByLabel("PNG size, long side");
  await expect(size).toHaveValue("2048");
  const png = c.getByRole("link", { name: "Download PNG" });
  await expect(png).toHaveAttribute("href", `${item.png_download_url}&size=2048`);
  await size.fill("96");
  await expect(png).toHaveAttribute("href", `${item.png_download_url}&size=96`);
  const small = await page.request.get(`${item.png_download_url}&size=96`);
  expect(small.status()).toBe(200);
  expect(small.headers()["content-type"]).toBe("image/png");
  expect(small.headers()["content-disposition"]).toMatch(/^attachment; filename="?vector_.*\.png"?$/);
  expect(Math.max(...pngSize(await small.body()))).toBe(96);
  // the stored preview and a size of the route's own range
  expect((await page.request.get(item.png_url)).status()).toBe(200);
  expect((await page.request.get(`${item.png_url}?size=16`)).status()).toBe(200);
  // a size outside the server's range is refused by it, and the page switches its link off before it comes to that
  expect((await page.request.get(`${item.png_url}?size=15`)).status()).toBe(422);
  expect((await page.request.get(`${item.png_url}?size=8193`)).status()).toBe(422);
  for (const bad of ["15", "8193", "1.5", ""]) {
    await size.fill(bad);
    const off = c.locator('[data-action="download-png"]');
    await expect(off).not.toHaveAttribute("href", /.*/);
    await expect(off).toHaveAttribute("aria-disabled", "true");
    await expect(c.getByRole("alert")).toHaveText("Enter a whole number from 16 to 8192.");
  }
  await size.fill("2048");
  await expect(c.getByRole("alert")).toHaveCount(0);

  // an id that does not exist is a 404 that says so, as for the other kinds
  const missing = await page.request.get("/api/vectors/00000000000000000000000000000000/svg");
  expect(missing.status()).toBe(404);
});

test("Copy SVG code puts the file's text on the clipboard and says so; the page does not show the file", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const idea = unique("copy me");
  const id = await makeRun(page, idea);
  const item = (await status(page, id)).vectors[0];
  await openVector(page);
  const c = vectorCard(page, idea).first();
  await c.getByRole("button", { name: "Copy SVG code" }).click();
  await expect(toastWith(page, "SVG code copied.")).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  const file = await (await page.request.get(item.url)).text();
  expect(copied).toBe(file);
  expect(copied).toMatch(/<svg[\s>]/);
  // the text of the file is nowhere on the page
  await expect(page.locator("body")).not.toContainText(`viewBox="0 0 40`);
});

// ------------------------------------------------------------------ Reuse, Retry, Keep, projects
test("Reuse puts the prompt and every setting back in the form, with the seed locked", async ({ page }) => {
  const idea = unique("reuse me");
  await makeRun(page, idea, { colours: 4, palette: ["#0000FF", "#FFA500"], kind: "line", width_mm: 123.5, height_mm: 45, layers_by: "colour", arrangement: "stacked", bleed_mm: 1.5, min_feature_mm: 0.8, kerf_mm: 0.2, seed: 555, num_images: 1 });
  await openVector(page);
  // change everything first, so that a field Reuse forgot would show
  await ideaBox(page).fill("something else");
  await form(page).getByRole("button", { name: "More colours" }).click();
  await radio(page, "Kind of artwork", "Silhouette or stencil").click();
  await form(page).getByLabel("Width (mm)").fill("77");
  await radio(page, "Layers by", "Shape").click();
  await vectorCard(page, idea).first().getByRole("button", { name: "Reuse" }).click();
  await expect(ideaBox(page)).toHaveValue(idea);
  await expect(form(page).locator("[data-vector-colours-value]")).toHaveText("4");
  await expect(form(page).getByLabel("Ink 1 colour")).toHaveValue("#0000ff");
  await expect(form(page).getByLabel("Ink 2 colour")).toHaveValue("#ffa500");
  await expect(radio(page, "Kind of artwork", "Line art")).toHaveAttribute("aria-checked", "true");
  await expect(form(page).getByLabel("Width (mm)")).toHaveValue("123.5");
  await expect(form(page).getByLabel("Height (mm)")).toHaveValue("45");
  await expect(radio(page, "Layers by", "Colour")).toHaveAttribute("aria-checked", "true");
  await expect(radio(page, "Arrangement", "Stacked")).toHaveAttribute("aria-checked", "true");
  await expect(form(page).getByLabel("Bleed (mm)")).toHaveValue("1.5");
  await expect(form(page).getByLabel("Smallest feature (mm)")).toHaveValue("0.8");
  await expect(form(page).getByLabel("Kerf (mm)")).toHaveValue("0.2");
  await expect(form(page).getByLabel("Lock seed")).toBeChecked();
  await expect(form(page).getByLabel("Seed", { exact: true })).toHaveValue("555");
  await expect(toastWith(page, "Loaded the prompt and settings. Seed locked to 555.")).toBeVisible();
  await expect(ideaBox(page)).toBeFocused();
  // the box of words is the server's again for the restored idea (the run used the server's own, so it is not an edit)
  await expect(recipeBox(page)).not.toHaveValue("");
  await expect(form(page)).not.toContainText("You changed this by hand");
});

test("a hand-written recipe is sent as `recipe`, comes back with Reuse as an edit, and Rebuild gives the studio's words back", async ({ page }) => {
  await openVector(page);
  const idea = unique("my own words");
  await ideaBox(page).fill(idea);
  await expect(recipeBox(page)).not.toHaveValue("", { timeout: 10_000 });
  const written = await recipeBox(page).inputValue();
  const mine = `${written} (edited by hand: ${idea})`;
  await recipeBox(page).fill(mine);
  await expect(form(page)).toContainText("You changed this by hand");
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/runs") && r.method() === "POST");
  await make(page).click();
  expect((await sent).postDataJSON().options.recipe).toBe(mine);
  const c = vectorCard(page, idea).first();
  await expect(c).toBeVisible();
  await done(c);
  // the server keeps what was sent (the run's effective prompt), and Reuse brings it back as an edit
  const run = await status(page, (await c.getAttribute("data-run-id"))!);
  expect(run.options.recipe).toBe(mine);
  await ideaBox(page).fill("another idea");
  await c.getByRole("button", { name: "Reuse" }).click();
  await expect(recipeBox(page)).toHaveValue(mine);
  await expect(form(page)).toContainText("You changed this by hand");
  await form(page).getByRole("button", { name: "Rebuild" }).click();
  await expect(recipeBox(page)).toHaveValue(written);
  await expect(form(page)).not.toContainText("You changed this by hand");
});

test("Keep, and the counts of the Vector tab: a kept run is counted and shown in Kept, and un-keeping says when it will go", async ({ page }) => {
  const idea = unique("keep me");
  await makeRun(page, idea);
  await openVector(page);
  const c = vectorCard(page, idea).first();
  await expect(keptOption(page)).toHaveText("Kept 0");
  await expect(deletedOption(page)).toHaveText("Deleted 0");
  const keep = c.getByRole("button", { name: "Keep" });
  await keep.click();
  await expect(keep).toHaveAttribute("aria-pressed", "true");
  await expect(c.locator(".badge-kept")).toBeVisible();
  await expect(keptOption(page)).toHaveText("Kept 1");
  await keptOption(page).click();
  await expect(c).toBeVisible();
  await keep.click(); // un-keep in the Kept view: the card goes, with a toast and Undo
  await expect(c).toHaveCount(0);
  await expect(toastWith(page, /No longer kept: “.*”\. It will be deleted/)).toBeVisible();
  await toastWith(page, /No longer kept/).getByRole("button", { name: "Undo" }).click();
  await expect(c).toBeVisible();
  await expect(keptOption(page)).toHaveText("Kept 1");
});

test("a Vector run can be filed in a project: the chip, the locked Keep, and the folder's count", async ({ page }) => {
  const idea = unique("file me");
  const id = await makeRun(page, idea);
  await page.request.post("/api/projects", { headers: ASK, data: { name: "Laser jobs" } });
  await openVector(page);
  const c = vectorCard(page, idea).first();
  await c.locator('[data-action="project"]').click();
  await page.getByRole("menu", { name: "Projects" }).getByRole("menuitemradio", { name: "Laser jobs" }).click();
  await expect(toastWith(page, /Filed “.*” in Laser jobs\. It is kept\./)).toBeVisible();
  await expect(c.locator("[data-project-chip]")).toContainText("Laser jobs");
  await expect(c.getByRole("button", { name: "Keep" })).toHaveAttribute("aria-disabled", "true");
  expect(await status(page, id)).toMatchObject({ project_id: expect.any(String), pinned: true });
  // the Project drop-down in the filter bar: the folder, with the number of this tab's runs in it when the server counts them
  await expect(bar(page).locator("[data-filter-project] option", { hasText: "Laser jobs" })).toHaveText(/^Laser jobs( \(1\))?$/);
  // Escape closes the menu and gives focus back to its button (it works the same on this card as on the others)
  await c.locator('[data-action="project"]').click();
  await expect(page.getByRole("menu", { name: "Projects" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu", { name: "Projects" })).toHaveCount(0);
  await expect(c.locator('[data-action="project"]')).toBeFocused();
});

// ------------------------------------------------------------------ Delete, the bin, Restore, Empty bin
test("Delete moves a Vector run to the bin whole: no per-item delete, the files wait, Restore and Undo bring it back", async ({ page }) => {
  const idea = unique("bin me");
  const id = await makeRun(page, idea, { num_images: 2 });
  const item = (await status(page, id)).vectors[0];
  await openVector(page);
  const c = vectorCard(page, idea).first();
  await expect(c.getByRole("button", { name: /Delete picture|Delete track|Delete version/ })).toHaveCount(0); // a Vector run is binned whole
  await c.getByRole("button", { name: "Delete", exact: true }).click();
  const question = dialog(page, "Delete this run?");
  await expect(question).toContainText("It moves to Deleted and stays there for 30 days; you can restore it from there. After that it is gone for good.");
  await question.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(c).toHaveCount(0);
  await expect(toastWith(page, `Deleted “${idea}”. It stays in Deleted for 30 days.`)).toBeVisible();
  await expect(deletedOption(page)).toHaveText("Deleted 1");
  expect((await status(page, id)).deleted_at).not.toBeNull();
  // the run's files stay with it in the bin (§31.12.6)
  for (const url of [item.preview_url, item.url, item.zip_url, item.png_url]) expect((await page.request.get(url)).status(), url).toBe(200);

  // the Deleted view: the same card, with only what is safe in the bin
  await deletedOption(page).click();
  await expect(c).toBeVisible();
  await expect(c.locator(".badge-deleted")).toHaveText("Deleted");
  await expect(c.locator("[data-note=in-bin]")).toContainText(/^In the bin since .*\. It will be deleted for good around .*, in (29|30) days\.$/);
  for (const name of ["Restore", "Reuse", "Delete forever"]) await expect(c.getByRole("button", { name })).toBeVisible();
  for (const name of ["Keep", "Retry", "Cancel"]) await expect(c.getByRole("button", { name })).toHaveCount(0);
  await expect(c.locator("img").first()).toBeVisible();
  await expect(c.getByRole("link", { name: /Download SVG/ }).first()).toBeVisible(); // the downloads only read the run

  // Restore: it goes back to the history, and focus lands on the filter bar (the card that had it is gone)
  await c.getByRole("button", { name: "Restore" }).click();
  await expect(c).toHaveCount(0);
  await expect(toastWith(page, /^Restored “.*”\. It has a fresh (29|30) days\./)).toBeVisible();
  await expect(deletedOption(page)).toBeFocused();
  await allOption(page).click();
  await expect(c).toBeVisible();
  expect((await status(page, id)).deleted_at).toBeNull();

  // Undo on the toast of a Delete does the same
  await c.getByRole("button", { name: "Delete", exact: true }).click();
  await dialog(page, "Delete this run?").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(c).toHaveCount(0);
  await toastWith(page, /^Deleted “/).last().getByRole("button", { name: "Undo" }).click(); // (the first Delete's toast may still be on screen: the newest is the last)
  await expect(c).toBeVisible();
});

test("Empty bin counts the Vector tab's runs and says so; emptied, their files are gone for good", async ({ page }) => {
  const vectorIdea = unique("vector in the bin");
  const pictureIdea = unique("picture in the bin");
  const vectorId = await makeRun(page, vectorIdea);
  const item = (await status(page, vectorId)).vectors[0];
  const picture = await page.request.post("/api/runs", { headers: ASK, data: { prompt: pictureIdea, options: { steps: 2, width: 512, height: 512, seed: 3 } } });
  const pictureId = ((await picture.json()) as { id: string }).id;
  await expect.poll(async () => (await status(page, pictureId)).status, { timeout: 30_000 }).toBe("done");
  for (const id of [vectorId, pictureId]) expect((await page.request.post(`/api/runs/${id}/bin`, { headers: ASK })).ok()).toBe(true);

  await openVector(page);
  await expect(deletedOption(page)).toHaveText("Deleted 1"); // the Vector tab counts its own bin
  await deletedOption(page).click();
  await expect(vectorCard(page, vectorIdea)).toBeVisible();
  await expect(vectorCard(page, pictureIdea)).toHaveCount(0); // the picture's card is on the Images tab
  await bar(page).getByRole("button", { name: "Empty bin" }).click();
  const question = dialog(page, "Empty the bin?");
  await expect(question).toContainText("Delete 2 runs for good (1 on Images, 1 on Vector), with their files. This can't be undone.");
  await question.getByRole("button", { name: "Empty bin" }).click();
  await expect(toastWith(page, "Emptied the bin: 2 runs deleted for good.")).toBeVisible();
  await expect(vectorCard(page, vectorIdea)).toHaveCount(0);
  await expect(page.getByText("No deleted vectors")).toBeVisible();
  for (const url of [item.url, item.zip_url, item.preview_url]) expect((await page.request.get(url)).status(), url).toBe(404);
  expect((await page.request.get(`/api/runs/${vectorId}`)).status()).toBe(404);
});

test("Delete forever on a Vector run in the bin asks first, names its versions, and removes them", async ({ page }) => {
  const idea = unique("forever");
  const id = await makeRun(page, idea);
  const item = (await status(page, id)).vectors[0];
  expect((await page.request.post(`/api/runs/${id}/bin`, { headers: ASK })).ok()).toBe(true);
  await openVector(page);
  await deletedOption(page).click();
  const c = vectorCard(page, idea).first();
  await c.getByRole("button", { name: "Delete forever" }).click();
  const forGood = dialog(page, "Delete this run for good?");
  await expect(forGood).toContainText("Its version is removed from the Spark.");
  await forGood.getByRole("button", { name: "Delete forever" }).click();
  await expect(c).toHaveCount(0);
  expect((await page.request.get(item.url)).status()).toBe(404);
});

// ------------------------------------------------------------------ the tabs apart, the model, the queue
test("each tab shows only its own runs and counts its own", async ({ page }) => {
  await page.goto("/");
  const picture = await generate(page, unique("a red kite"));
  await expect(picture.locator(".badge").first()).toHaveText("Done", { timeout: 30_000 });
  await vectorTab(page).click();
  await expect(page.getByText("No vectors yet")).toBeVisible();
  const c = await makeVector(page, unique("a blue whale"));
  await done(c);
  await expect(page.locator("article.run-card:visible")).toHaveCount(1); // only the vector on this tab
  await expect(page.locator("article.vector-card:visible")).toHaveCount(1);
  await keptOption(page).click();
  await expect(page.getByText("No kept vectors yet")).toBeVisible();
  await allOption(page).click();
  await imagesTab(page).click();
  await expect(page.locator("article.run-card:visible")).toHaveCount(1);
  await expect(page.locator("article.vector-card:visible")).toHaveCount(0);
  await expect(page.locator("article.run-card", { hasText: "a red kite" })).toBeVisible();
  await musicTab(page).click();
  await expect(page.locator("article.run-card:visible")).toHaveCount(0);
});

test("on the Vector tab the model pill and the Load / Unload button are the image model's", async ({ page }) => {
  await expect.poll(async () => (await page.request.post("/api/model/unload", { headers: ASK })).status(), { timeout: 30_000 }).toBe(200);
  await openVector(page);
  await expect(pill(page)).toHaveText(/^Model not loaded/);
  await expect(page.getByRole("button", { name: "Load music model", exact: true })).toHaveCount(0);
  const load = page.getByRole("button", { name: "Load model", exact: true });
  await expect(load).toBeVisible();
  await expect(load).toHaveAttribute("data-model", "image");
  await load.click();
  await expect(pill(page)).toHaveText(/^Image model ready/, { timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Unload model", exact: true })).toHaveAttribute("data-model", "image");
});

test("a run that is waiting can be canceled from its card, and says what was kept", async ({ page }) => {
  await openVector(page);
  // the first run keeps the worker busy (four versions), so the second waits in the queue
  const first = await page.request.post("/api/runs", { headers: ASK, data: { mode: "vector", prompt: unique("busy first"), options: { num_images: 4, seed: 1 } } });
  expect(first.status()).toBe(201);
  const idea = unique("waiting second");
  const second = await page.request.post("/api/runs", { headers: ASK, data: { mode: "vector", prompt: idea, options: { seed: 2 } } });
  expect(second.status()).toBe(201);
  const c = vectorCard(page, idea).first();
  await expect(c.locator(".badge").first()).toHaveText(/^Queued/);
  await c.getByRole("button", { name: "Cancel" }).click(); // a waiting run leaves the queue at once, with no question
  await expect(c.locator(".badge").first()).toHaveText("Canceled");
  await expect(c.locator(".run-note")).toHaveText("Canceled before any version was finished.");
  await expect(c.getByRole("button", { name: "Cancel" })).toHaveCount(0);
});

test("when Vector cannot run here the tab says why, in the server's words, and the other tabs work as usual", async ({ page }) => {
  // the server's capabilities, with Vector turned off by what the server would say in that case
  await page.route("**/api/capabilities", async (route) => {
    const real = await route.fetch();
    const caps = await real.json();
    caps.modes = caps.modes.filter((mode: string) => mode !== "vector");
    caps.vector = { available: false, reason: "The Vector libraries are not installed here (vtracer).", hint: "They are in backend/requirements.txt: rebuild the image." };
    await route.fulfill({ response: real, json: caps });
  });
  await page.goto("/");
  await vectorTab(page).click();
  const notice = page.getByRole("region", { name: "Vector is not available" });
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("The Vector libraries are not installed here (vtracer).");
  await expect(notice).toContainText("They are in backend/requirements.txt: rebuild the image.");
  await expect(form(page)).toHaveCount(0);
  await expect(bar(page)).toBeVisible(); // the history stays
  await imagesTab(page).click();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toBeVisible();
});

// ------------------------------------------------------------------ a phone, and the keyboard
test("at a phone's width (320 px) nothing runs off the page: the tabs, the form and the card all fit", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const idea = unique("phone");
  await makeRun(page, idea, { colours: 3, num_images: 2 });
  await openVector(page);
  const fits = () => page.evaluate(() => ({ page: document.documentElement.scrollWidth <= window.innerWidth, body: document.body.scrollWidth <= window.innerWidth }));
  expect(await fits()).toEqual({ page: true, body: true });
  // all three tabs are on screen, and the buttons of the form can be reached without scrolling sideways
  for (const tab of [imagesTab(page), musicTab(page), vectorTab(page)]) {
    const box = (await tab.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(320);
  }
  await form(page).getByRole("button", { name: "More colours" }).click();
  await form(page).getByRole("button", { name: "Choose a colour for ink 1" }).click();
  await ideaBox(page).fill("a phone-sized idea");
  for (const control of [make(page), form(page).getByRole("button", { name: "xTool P2 bed · 600 × 307 mm" }), form(page).getByRole("button", { name: "Rebuild" }), form(page).getByLabel("Ink 1 colour")]) {
    await control.scrollIntoViewIfNeeded();
    const box = (await control.boundingBox())!;
    expect(box.x, "left edge").toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, "right edge").toBeLessThanOrEqual(320);
  }
  expect(await fits()).toEqual({ page: true, body: true });
  // the card: the previews stack above the details, and the table scrolls inside its own box if it must
  const c = vectorCard(page, idea).first();
  await c.scrollIntoViewIfNeeded();
  const cardBox = (await c.boundingBox())!;
  expect(cardBox.x + cardBox.width).toBeLessThanOrEqual(320);
  for (const image of await c.locator(".vector-preview img").all()) {
    const box = (await image.boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(320);
  }
  const first = c.locator(".vector-item").first();
  const preview = (await first.locator(".vector-preview").boundingBox())!;
  const details = (await first.locator(".vector-details").boundingBox())!;
  expect(details.y).toBeGreaterThanOrEqual(preview.y + preview.height - 1); // stacked, not side by side
  for (const button of await c.locator(".run-actions button:visible").all()) {
    const box = (await button.boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(320);
  }
  expect(await fits()).toEqual({ page: true, body: true });
});

test("the form can be worked with the keyboard alone: labelled controls in reading order, Ctrl+Enter makes, Escape leaves the menu", async ({ page }) => {
  await openVector(page);
  await ideaBox(page).focus();
  // Tab walks the form in the order it is read: the prompt, the kind, the colours stepper, ...
  await page.keyboard.press("Tab");
  await expect(form(page).getByRole("radio", { name: "Silhouette or stencil" })).toBeFocused();
  for (let i = 0; i < 3; i++) await page.keyboard.press("Tab"); // the three kinds
  await expect(form(page).getByRole("button", { name: "More colours" })).toBeFocused(); // (Fewer colours is disabled at the least, so it is skipped)
  await page.keyboard.press("Enter");
  await expect(form(page).locator("[data-vector-colours-value]")).toHaveText("3");
  // every control of the form has a name a screen reader can say
  const unnamed = await form(page).locator("input, textarea, select, button").evaluateAll((elements) =>
    elements.filter((el) => {
      const e = el as HTMLElement;
      const labelled = (e as HTMLInputElement).labels?.length || e.getAttribute("aria-label") || e.getAttribute("aria-labelledby") || (e.textContent ?? "").trim();
      return !labelled;
    }).map((el) => el.outerHTML.slice(0, 80)));
  expect(unnamed).toEqual([]);
  // Ctrl + Enter makes it, from the prompt
  const idea = unique("keyboard only");
  await ideaBox(page).fill(idea);
  await ideaBox(page).press("Control+Enter");
  await expect(vectorCard(page, idea).first()).toBeVisible();
});

test("the form is remembered across a reload: the prompt, the colours, an ink, the size and the choices", async ({ page }) => {
  await openVector(page);
  await ideaBox(page).fill("an owl");
  await radio(page, "Kind of artwork", "Line art").click();
  await form(page).getByRole("button", { name: "More colours" }).click();
  await form(page).getByRole("button", { name: "Choose a colour for ink 1" }).click();
  await form(page).getByLabel("Ink 1 colour").fill("#00aa00");
  await form(page).getByLabel("Width (mm)").fill("321.5");
  await radio(page, "Layers by", "Shape").click();
  await form(page).getByRole("button", { name: "xTool P2 bed · 600 × 307 mm" }).click();
  await page.reload();
  await expect(ideaBox(page)).toHaveValue("an owl");
  await expect(radio(page, "Kind of artwork", "Line art")).toHaveAttribute("aria-checked", "true");
  await expect(form(page).locator("[data-vector-colours-value]")).toHaveText("3");
  await expect(form(page).getByLabel("Ink 1 colour")).toHaveValue("#00aa00");
  await expect(form(page).getByLabel("Width (mm)")).toHaveValue("600");
  await expect(radio(page, "Layers by", "Shape")).toHaveAttribute("aria-checked", "true");
  await expect(radio(page, "Arrangement", "Side by side")).toBeDisabled(); // greyed by shape, still after a reload
});

test("white never becomes an ink, and the arrangement and bleed are greyed as the rules say", async ({ page }) => {
  await openVector(page);
  await form(page).getByRole("button", { name: "More colours" }).click();
  await form(page).getByRole("button", { name: "Choose a colour for ink 1" }).click();
  await form(page).getByLabel("Ink 1 colour").fill("#ff0000");
  await form(page).getByLabel("Ink 1 colour").fill("#ffffff");
  await expect(form(page)).toContainText("White is the bare material and has no layer. Pick another colour.");
  await expect(form(page).getByLabel("Ink 1 colour")).toHaveValue("#ff0000"); // the ink keeps its colour
  await expect(form(page).locator('[data-ink="white"] input')).toHaveCount(0);

  await expect(form(page).getByLabel("Bleed (mm)")).toBeDisabled(); // side by side
  await radio(page, "Arrangement", "Stacked").click();
  await expect(form(page).getByLabel("Bleed (mm)")).toBeEnabled();
  await radio(page, "Layers by", "Shape").click();
  await expect(radio(page, "Arrangement", "Stacked")).toBeDisabled();
  await expect(radio(page, "Arrangement", "Side by side")).toHaveAttribute("aria-checked", "true");
  await expect(form(page).getByLabel("Bleed (mm)")).toBeDisabled();
  await expect(form(page)).toContainText("Layers by shape are always side by side.");
});

// ================================================================== the browser-level mutation round of 1.17
// Everything below closes a gap that a deliberate bug in the page's wiring got through: a one-line change ("mutant") to App.tsx, VectorPanel.tsx,
// VectorCard.tsx or one of the small files that none of the scenarios above failed on. The comment on each test says which bug it would catch, so that a
// reader can see why it is there. They check what the person sees and what the page sends, never how the page is written.

/** Start a Vector run over the API and do NOT wait for it, for the tests that look at a run while it works. Four versions of 100 steps (the most the server allows)
 *  keep the worker busy for about seven seconds (a version is drawn at 10 ms a step and then traced), which is long enough to do a dozen things; a test that
 *  does not want to leave the worker busy for the next one stops the run with `stopRun`. */
async function startRun(page: Page, prompt: string, options: Record<string, unknown> = {}): Promise<string> {
  const posted = await page.request.post("/api/runs", { headers: ASK, data: { mode: "vector", prompt, options: { colours: 2, width_mm: 40, height_mm: 40, seed: 11, num_images: 4, steps: 100, ...options } } });
  expect(posted.status(), await posted.text()).toBe(201);
  return ((await posted.json()) as { id: string }).id;
}

/** Cancel a run over the API (behind the page's back), so that a test that has seen what it came for does not make the next one wait for the worker. */
async function stopRun(page: Page, id: string) {
  expect((await page.request.post(`/api/runs/${id}/cancel`, { headers: ASK })).ok()).toBe(true);
}

/** Make a run look `days` old, behind the server's back (it computes the time left before deletion from the stored time): the same helper as kept.spec.ts. */
function ageRun(runId: string, days: number) {
  const python = process.env.STUDIO_PYTHON ?? resolve("../backend/.venv/bin/python");
  const script = [
    "import sqlite3, sys, datetime as d",
    "t = (d.datetime.now(d.timezone.utc) - d.timedelta(days=float(sys.argv[3]))).isoformat(timespec='milliseconds').replace('+00:00', 'Z')",
    "c = sqlite3.connect(sys.argv[1], timeout=10); c.execute('UPDATE runs SET created_at=? WHERE id=?', (t, sys.argv[2])); c.commit()",
  ].join("\n");
  execFileSync(python, ["-c", script, `${process.env.STUDIO_E2E_DATA_DIR}/studio.sqlite`, runId, String(days)]);
}

// ------------------------------------------------------------------ the tabs while a run works
// Catches: Tabs told that Vector never works (`vector: false`), a dot put on the Music tab for Vector's work, and a dot on the tab that is open.
test("a Vector run that works puts the dot on the Vector tab, and on no other, while another tab is open", async ({ page }) => {
  await page.goto("/");
  const id = await startRun(page, unique("dot while working"));
  const dot = (tab: Locator) => tab.locator(".tab-dot");
  await expect(dot(vectorTab(page))).toHaveCount(1);
  await expect(vectorTab(page)).toContainText("(working)"); // a screen reader is told as well
  await expect(dot(imagesTab(page))).toHaveCount(0);
  await expect(dot(musicTab(page))).toHaveCount(0);
  await musicTab(page).click();
  await expect(dot(vectorTab(page))).toHaveCount(1); // still working, still on Vector, and never on Music
  await expect(dot(musicTab(page))).toHaveCount(0);
  await vectorTab(page).click();
  await expect(dot(vectorTab(page))).toHaveCount(0); // the tab being looked at shows the work itself, on its cards
  await imagesTab(page).click();
  await expect(dot(vectorTab(page))).toHaveCount(1);
  await stopRun(page, id);
  await expect(dot(vectorTab(page))).toHaveCount(0, { timeout: 30_000 }); // and the dot goes when the run is no longer working
});

// ------------------------------------------------------------------ a run that fails, and Retry
// Catches: Retry that sends the form as it is now instead of the failed run's own settings, and a Retry offered on a card in the bin.
test("a failed Vector run says why, Retry sends the run's own settings again, and a failed run in the bin has no Retry", async ({ page }) => {
  const idea = unique("doomed [fake:error]"); // the fake image pipeline fails a run whose words hold this marker
  const posted = await page.request.post("/api/runs", {
    headers: ASK,
    data: { mode: "vector", prompt: idea, options: { colours: 4, palette: ["#0000FF"], kind: "line", width_mm: 77, height_mm: 33, layers_by: "colour", arrangement: "stacked",
      bleed_mm: 1.2, min_feature_mm: 0.7, kerf_mm: 0.1, seed: 9, num_images: 1 } },
  });
  expect(posted.status(), await posted.text()).toBe(201);
  const { id } = (await posted.json()) as { id: string };
  await openVector(page); // the form is on its defaults: nothing like the run above
  const c = vectorCard(page, idea).first();
  await expect(c.locator(".badge").first()).toHaveText("Failed", { timeout: 30_000 });
  await expect(c.getByRole("alert")).toContainText("Simulated pipeline failure");
  const run = await status(page, id);

  const sent = page.waitForRequest((r) => r.url().endsWith("/api/runs") && r.method() === "POST");
  await c.getByRole("button", { name: "Retry" }).click();
  const body = (await sent).postDataJSON();
  expect(body.mode).toBe("vector");
  expect(body.prompt).toBe(idea);
  expect(body.options).toMatchObject({ colours: 4, palette: ["#0000FF"], kind: "line", width_mm: 77, height_mm: 33, layers_by: "colour", arrangement: "stacked", bleed_mm: 1.2,
    min_feature_mm: 0.7, kerf_mm: 0.1, seed: 9, num_images: 1, recipe: run.options.recipe });
  await expect(vectorCard(page, idea)).toHaveCount(2); // the retry is a new run, next to the first

  // a failed run in the bin keeps its reason but may only be restored, reused or deleted for good
  expect((await page.request.post(`/api/runs/${id}/bin`, { headers: ASK })).ok()).toBe(true);
  await deletedOption(page).click();
  await expect(c).toBeVisible();
  await expect(c.getByRole("alert")).toContainText("Simulated pipeline failure");
  await expect(c.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(c.getByRole("button", { name: "Restore" })).toBeVisible();
});

// ------------------------------------------------------------------ a run that is working: Cancel, Delete, the queue, the views
// Catches: Cancel on a running run that does not ask first, the question calling versions "images" or not counting them, the button not saying what it stops,
// and a Delete that works on a run that is being made.
test("Cancel on a Vector run that is working asks first, counts the versions kept, and Delete waits", async ({ page }) => {
  const idea = unique("cancel while working");
  await startRun(page, idea);
  await openVector(page);
  const c = vectorCard(page, idea).first();
  await expect(c.locator(".badge").first()).toHaveText("Making vector", { timeout: 30_000 });
  await expect(c.getByRole("button", { name: "Delete", exact: true })).toBeDisabled(); // not while it is being made
  // wait for a first finished version, so that the question has something to say is kept
  await expect(c.locator(".vector-item").first()).toBeVisible({ timeout: 60_000 });
  await c.getByRole("button", { name: "Cancel" }).click();
  const question = dialog(page, "Stop this run?");
  // (by the time the button is pressed one or two versions are finished: "The version ... is kept" or "The 2 versions ... are kept"; never "Nothing has been finished")
  await expect(question).toContainText(/The (version|\d versions) already finished (is|are) kept\. The one being made now is discarded\./);
  await expect(question.getByRole("button", { name: "Stop making the vector" })).toBeVisible();
  await question.getByRole("button", { name: "Keep going" }).click();
  await expect(question).toHaveCount(0);
  await expect(c.locator(".badge").first()).toHaveText("Making vector"); // Keep going changed nothing
  await c.getByRole("button", { name: "Cancel" }).click();
  await dialog(page, "Stop this run?").getByRole("button", { name: "Stop making the vector" }).click();
  await expect(c.getByRole("button", { name: "Reuse" })).toBeFocused(); // the Cancel button that had focus is going: it moves to the card's Reuse
  await expect(c.locator(".badge").first()).toHaveText("Canceled", { timeout: 60_000 });
  await expect(c.locator(".run-note")).toHaveText(/^Canceled\. \d of 4 versions finished and kept\.$/);
});

// Catches: no queue line on the Vector tab (the panel given no status), a waiting run sent to the bin instead of deleted, a working run listed in the Deleted
// view, and the note on a working run saying "Kept" in a project's view.
test("while a Vector run works the tab shows the queue, a waiting run is deleted for good, and the filtered views show only what they should", async ({ page }) => {
  const busy = unique("queue busy");
  const waiting = unique("queue waiting");
  const projectId = ((await (await page.request.post("/api/projects", { headers: ASK, data: { name: "Queue test" } })).json()) as { id: string }).id;
  const busyId = await startRun(page, busy);
  const waitingId = await startRun(page, waiting, { num_images: 1 });
  await openVector(page);
  await expect(page.locator(".queue-bar:visible")).toContainText("Generating 1 run · 1 waiting");

  // a run that only waits made nothing worth keeping: Delete removes it for good, with no days in the bin
  const w = vectorCard(page, waiting).first();
  await expect(w.locator(".badge").first()).toHaveText("Queued · #1"); // its place in the queue is on the card
  await w.getByRole("button", { name: "Delete", exact: true }).click();
  const question = dialog(page, "Delete this run?");
  await expect(question).toContainText("It is removed from the history. This can't be undone.");
  await expect(question).not.toContainText("days");
  await question.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(w).toHaveCount(0);
  expect((await page.request.get(`/api/runs/${waitingId}`)).status()).toBe(404);

  // the working run is not in the bin's view; it is in the others while it works, and says why
  const b = vectorCard(page, busy).first();
  await expect(b.locator(".badge").first()).toHaveText(/Making vector|Queued/);
  await deletedOption(page).click();
  await expect(page.getByText("No deleted vectors")).toBeVisible();
  await expect(b).toHaveCount(0);
  await keptOption(page).click();
  await expect(b.locator("[data-note=working-in-kept]")).toHaveText("Shown while it works. It stays in this view only if you Keep it.");
  await allOption(page).click();
  await bar(page).locator("[data-filter-project]").selectOption(projectId);
  await expect(b.locator("[data-note=working-in-project]")).toBeVisible(); // a project's view says "project", not "Kept"
  await expect(b.locator("[data-note=working-in-kept]")).toHaveCount(0);
  await stopRun(page, busyId);
});

// ------------------------------------------------------------------ the form: every control is wired to its own setting
// Catches: Fewer colours that adds one, "Let the picture choose" that keeps the colour, the height box setting the width, the smallest feature, kerf and
// bleed boxes setting each other's settings, a preset that never shows as pressed, a Rebuild or seed box that is always on, Make vector on with no idea, the
// idea and the words without the server's length limits, and the words left on screen after the idea is cleared. The request is stopped at the page's door
// (answered 422) so that no run is made and what the page sent can be read exactly.
test("the form's controls are wired to their own settings, and what is sent is what was set", async ({ page }) => {
  const caps = (await (await page.request.get("/api/capabilities")).json()) as { limits: { prompt_chars: number; vector: { recipe_max_chars: number } } };
  const posts: { options: Record<string, unknown> }[] = [];
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posts.push(route.request().postDataJSON());
    await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "Stub: refused so that no run is made." }) });
  });
  await openVector(page);

  // Make vector waits for an idea, and the text boxes are limited as the server limits them
  await expect(make(page)).toBeDisabled();
  await ideaBox(page).fill("x");
  await expect(make(page)).toBeEnabled();
  await ideaBox(page).fill("");
  await expect(make(page)).toBeDisabled();
  await expect(ideaBox(page)).toHaveAttribute("maxlength", String(caps.limits.prompt_chars));
  await expect(recipeBox(page)).toHaveAttribute("maxlength", String(caps.limits.vector.recipe_max_chars));

  // the studio's words appear for an idea and go when the idea is cleared (they are not left behind for a prompt that is no longer there)
  await ideaBox(page).fill("an owl");
  await expect(recipeBox(page)).not.toHaveValue("", { timeout: 10_000 });
  await ideaBox(page).fill("");
  await expect(recipeBox(page)).toHaveValue("");
  await ideaBox(page).fill("an owl on a branch");

  // Rebuild is for a hand-written text only, and the seed box for a locked seed only
  const rebuild = form(page).getByRole("button", { name: "Rebuild" });
  await expect(rebuild).toBeDisabled();
  await expect(form(page).getByLabel("Seed", { exact: true })).toBeDisabled();
  await form(page).getByLabel("Lock seed").check();
  await expect(form(page).getByLabel("Seed", { exact: true })).toBeEnabled();
  await recipeBox(page).fill("owl, by hand");
  await expect(rebuild).toBeEnabled();
  await rebuild.click();
  await expect(rebuild).toBeDisabled();

  // colours: three inks, the first given back, then one colour fewer
  const more = form(page).getByRole("button", { name: "More colours" });
  for (let n = 0; n < 3; n++) await more.click(); // 5 colours: white and four inks
  for (const ink of [1, 2, 3]) await form(page).getByRole("button", { name: `Choose a colour for ink ${ink}` }).click();
  await expect(form(page).getByLabel("Ink 1 colour")).toHaveValue("#000000");
  await expect(form(page).getByLabel("Ink 2 colour")).toHaveValue("#ff0000");
  await form(page).getByRole("button", { name: "Let the picture choose ink 1" }).click();
  await expect(form(page).getByLabel("Ink 1 colour")).toHaveValue("#ff0000"); // the others move up
  await expect(form(page).getByLabel("Ink 2 colour")).toHaveValue("#00aa00");
  await expect(form(page).locator('[data-ink="3"]')).toHaveClass(/ink-open/);
  await form(page).getByRole("button", { name: "Fewer colours" }).click();
  await expect(form(page).locator("[data-vector-colours-value]")).toHaveText("4");
  await expect(form(page).locator("[data-vector-layers-line]")).toContainText("Up to 3 layers");

  // the sheet: a preset shows as pressed while it is the size, and stops when either side is changed
  const preset = form(page).getByRole("button", { name: "xTool P2 bed · 600 × 307 mm" });
  await preset.click();
  await expect(preset).toHaveAttribute("aria-pressed", "true");
  await form(page).getByLabel("Height (mm)").fill("55.5");
  await expect(preset).toHaveAttribute("aria-pressed", "false");
  await form(page).getByLabel("Width (mm)").fill("88");

  // the boxes under More, and the arrangement that makes the bleed count
  await form(page).locator("summary", { hasText: "More" }).click();
  await form(page).getByLabel("Smallest feature (mm)").fill("0.8");
  await form(page).getByLabel("Kerf (mm)").fill("0.3");
  await radio(page, "Arrangement", "Stacked").click();
  await form(page).getByLabel("Bleed (mm)").fill("1.2");

  // sent: every setting where it belongs; the server's refusal is shown in its own words (a 422 is for the form)
  await make(page).click();
  await expect(form(page).getByRole("alert")).toHaveText("Stub: refused so that no run is made.");
  expect(posts).toHaveLength(1);
  expect(posts[0].options).toMatchObject({ colours: 4, palette: ["#FF0000", "#00AA00"], width_mm: 88, height_mm: 55.5, min_feature_mm: 0.8, kerf_mm: 0.3,
    arrangement: "stacked", bleed_mm: 1.2, seed: 42, recipe: null });
  // the message belongs to the form as it was: it goes with the next change
  await ideaBox(page).fill("an owl on a branch, in flight");
  await expect(form(page).getByRole("alert")).toHaveCount(0);
});

// Catches: a form that is not right sent all the same (the page's own check not stopping it), the queue-full answer told as a failure to start, and the
// shortcut for Mac (Cmd + Enter) that does not make.
test("a form that is not right is not sent, a full queue is told in the server's words, and Cmd + Enter makes", async ({ page }) => {
  let posts = 0;
  let answer: { status: number; detail: string } | null = { status: 429, detail: "The queue is full (3 waiting). Try again when one has finished." };
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posts += 1;
    if (answer) await route.fulfill({ status: answer.status, contentType: "application/json", body: JSON.stringify({ detail: answer.detail }) });
    else await route.continue();
  });
  await openVector(page);
  // hand-written words that are only spaces are no words: the page says so and sends nothing
  await ideaBox(page).fill(unique("not right"));
  await expect(recipeBox(page)).not.toHaveValue("", { timeout: 10_000 });
  await recipeBox(page).fill("   ");
  await make(page).click();
  await expect(form(page).getByRole("alert")).toHaveText("The words the studio sends are empty: write them, or press Rebuild to have them written again.");
  expect(posts).toBe(0);
  await form(page).getByRole("button", { name: "Rebuild" }).click();
  await expect(form(page).getByRole("alert")).toHaveCount(0);

  // a full queue is the server's sentence, as it is, in a toast (not "Couldn't start the run: ...")
  await make(page).click();
  await expect(page.locator(".toast", { hasText: "The queue is full" })).toContainText("The queue is full (3 waiting). Try again when one has finished.");
  await expect(page.locator(".toast", { hasText: "Couldn't start the run" })).toHaveCount(0);
  expect(posts).toBe(1);

  // Cmd + Enter (the Mac's shortcut, the page says so under the form) makes, as Ctrl + Enter does
  answer = null;
  const idea = unique("command enter");
  await ideaBox(page).fill(idea);
  await ideaBox(page).press("Meta+Enter");
  await expect(vectorCard(page, idea).first()).toBeVisible();
  expect(posts).toBe(2);
});

// ------------------------------------------------------------------ the studio's words: when they are asked for, and which answer counts
/** Every request for the studio's words, as the text of its body, from now on. */
function watchRecipeRequests(page: Page): string[] {
  const asked: string[] = [];
  page.on("request", (r) => {
    if (r.url().endsWith("/api/vector/recipe")) asked.push(r.postData() ?? "");
  });
  return asked;
}

// Catches: the words asked for while the Vector tab is hidden (a remembered idea would make a request on every page load of the other tabs), and asked for
// when Vector cannot run here.
test("the studio's words are asked for only when the Vector tab is on show and can make something", async ({ page }) => {
  // an idea left in the form on an earlier visit
  await page.addInitScript(() => localStorage.setItem("studio.vector.v1", JSON.stringify({ idea: "a remembered idea" })));
  const asked = watchRecipeRequests(page);
  await page.goto("/");
  await expect(imagesTab(page)).toHaveAttribute("aria-selected", "true");
  // (a pause longer than the form's own 350 ms before it asks, and than an answer takes: if nothing was asked by then, nothing will be for a tab nobody sees)
  await page.waitForTimeout(1500);
  expect(asked).toEqual([]);
  await vectorTab(page).click();
  await expect(recipeBox(page)).not.toHaveValue("", { timeout: 10_000 });
  expect(asked).toHaveLength(1);
  expect(JSON.parse(asked[0]).prompt).toBe("a remembered idea");

  // Vector cannot run here (the same answer as in the test of the unavailable tab): the notice is shown, and no words are asked for
  await page.route("**/api/capabilities", async (route) => {
    const real = await route.fetch();
    const caps = await real.json();
    caps.modes = caps.modes.filter((mode: string) => mode !== "vector");
    caps.vector = { available: false, reason: "The Vector libraries are not installed here (vtracer).", hint: null };
    await route.fulfill({ response: real, json: caps });
  });
  asked.length = 0;
  await page.reload(); // the Vector tab is remembered, so it is on show
  await expect(page.getByRole("region", { name: "Vector is not available" })).toBeVisible();
  await page.waitForTimeout(1500);
  expect(asked).toEqual([]);
});

// Catches: a request for the words at every key (the pause removed), the request that leaves the kind out, and an answer for an older idea that arrives late and
// replaces the words for the newer one.
test("the studio's words are asked for once after typing, with the idea, kind, colours and inks, and only the newest answer is shown", async ({ page }) => {
  const asked = watchRecipeRequests(page);
  await openVector(page);
  await ideaBox(page).pressSequentially("owl", { delay: 80 }); // three keys, each well inside the 350 ms pause before the form asks
  await expect(recipeBox(page)).toHaveValue(/owl/i, { timeout: 10_000 });
  expect(asked.map((body) => JSON.parse(body))).toEqual([{ prompt: "owl", kind: "silhouette", colours: 2, palette: [] }]);
  await radio(page, "Kind of artwork", "Line art").click();
  await form(page).getByRole("button", { name: "More colours" }).click();
  await form(page).getByRole("button", { name: "Choose a colour for ink 1" }).click();
  await expect.poll(() => JSON.parse(asked.at(-1)!)).toEqual({ prompt: "owl", kind: "line", colours: 3, palette: ["#000000"] });

  // the first answer is held back until the second is on screen: it is for an idea that is gone, and must not take the box over
  let seen = 0;
  let held = false;
  await page.route("**/api/vector/recipe", async (route) => {
    seen += 1;
    if (seen === 1) {
      const real = await route.fetch();
      await new Promise((done) => setTimeout(done, 2000));
      await route.fulfill({ response: real });
      held = true;
    } else {
      await route.continue();
    }
  });
  await ideaBox(page).fill("first idea");
  await expect.poll(() => seen).toBe(1); // the request for the first idea is on its way and held
  await ideaBox(page).fill("second idea");
  await expect(recipeBox(page)).toHaveValue(/second idea/i, { timeout: 10_000 });
  await expect.poll(() => held, { timeout: 10_000 }).toBe(true); // the late answer has been handed to the page
  await page.waitForTimeout(500); // ... and the page has had time to take it in, or not
  await expect(recipeBox(page)).toHaveValue(/second idea/i);
  await expect(recipeBox(page)).not.toHaveValue(/first idea/i);
});

// Catches: a Reuse that is still waiting for the studio's words and puts the run's own words over what the person has typed in the box meanwhile (any change to
// the form must cancel the comparison that Reuse leaves waiting).
test("Reuse waits for the studio's words, but never puts the run's words over something typed meanwhile", async ({ page }) => {
  const idea = unique("typed over");
  const mine = "the run's own hand-written words";
  await makeRun(page, idea, { recipe: mine }); // the words sent were not the studio's: Reuse will bring them back as an edit, if nothing else happens first
  await openVector(page);
  let held = false;
  await page.route("**/api/vector/recipe", async (route) => {
    const real = await route.fetch();
    await new Promise((done) => setTimeout(done, 1500)); // the studio's words for the restored idea arrive late
    await route.fulfill({ response: real });
    held = true;
  });
  await vectorCard(page, idea).first().getByRole("button", { name: "Reuse" }).click();
  await expect(ideaBox(page)).toHaveValue(idea);
  await recipeBox(page).fill("typed after Reuse"); // before the studio's words are in
  await expect.poll(() => held, { timeout: 10_000 }).toBe(true);
  await page.waitForTimeout(500); // the page has had time to compare the words with the run's
  await expect(recipeBox(page)).toHaveValue("typed after Reuse");
  await expect(form(page)).toContainText("You changed this by hand");
});

// ------------------------------------------------------------------ keyboard focus, and the model that is still loading
// Catches: Delete that leaves keyboard focus nowhere instead of on the next card's Delete button, and Cancel of a waiting run that does not keep focus on the card
// (both find the card's buttons by their `data-action`).
test("keyboard focus stays on the list: Delete moves it to the next card's Delete, and canceling a waiting run keeps it on that card's Reuse", async ({ page }) => {
  const [older, newer] = [unique("focus older"), unique("focus newer")];
  await makeRun(page, older);
  await makeRun(page, newer);
  await openVector(page);
  const top = vectorCard(page, newer).first();
  await top.getByRole("button", { name: "Delete", exact: true }).click();
  await dialog(page, "Delete this run?").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(top).toHaveCount(0);
  await expect(vectorCard(page, older).first().getByRole("button", { name: "Delete", exact: true })).toBeFocused();

  const busyId = await startRun(page, unique("focus busy"));
  const waiting = unique("focus waiting");
  await startRun(page, waiting, { num_images: 1 });
  const w = vectorCard(page, waiting).first();
  await expect(w.locator(".badge").first()).toHaveText(/^Queued/);
  await w.getByRole("button", { name: "Cancel" }).click();
  await expect(w.locator(".badge").first()).toHaveText("Canceled");
  await expect(w.getByRole("button", { name: "Reuse" })).toBeFocused();
  await stopRun(page, busyId);
});

// Catches: a card that says only "Starting…" while the model is still loading (the card not told the worker's state).
test("a Vector run that waits for the model to load says so on its card", async ({ page }) => {
  await expect.poll(async () => (await page.request.post("/api/model/unload", { headers: ASK })).status(), { timeout: 30_000 }).toBe(200);
  await openVector(page);
  const c = await makeVector(page, unique("model loading"));
  await expect(c.locator(".progress")).toContainText("Loading the model… (the first load takes a while)"); // the fake model takes about a second to load
});

// ------------------------------------------------------------------ Copy SVG code when something goes wrong
// Catches: the file read as the download variant, a second press while the first is on its way making a second request, the server's error page copied as if it
// were the file, and a copy the browser refuses told as a success.
test("Copy SVG code reads the file as shown, once at a time, and says so when the file or the clipboard fails", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const idea = unique("copy trouble");
  const item = (await status(page, await makeRun(page, idea))).vectors[0];
  await openVector(page);
  const copy = vectorCard(page, idea).first().getByRole("button", { name: "Copy SVG code" });

  // pressed twice while the file is on its way: one request, for the file as it is shown (not "?download=1"), and one toast
  const reads: string[] = [];
  const fileUrl = (url: URL) => url.pathname === item.url;
  await page.route(fileUrl, async (route) => {
    reads.push(route.request().url());
    await new Promise((done) => setTimeout(done, 600));
    await route.continue();
  });
  await copy.click();
  await copy.click();
  await expect(toastWith(page, "SVG code copied.")).toBeVisible();
  expect(reads).toEqual([new URL(item.url, page.url()).href]);
  await expect(toastWith(page, "SVG code copied.")).toHaveCount(1);
  await page.unroute(fileUrl);

  // the file is gone: the server's sentence is shown, and nothing is copied
  await page.evaluate(() => navigator.clipboard.writeText("untouched"));
  await page.route(fileUrl, (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "That vector is gone." }) }));
  await copy.click();
  await expect(toastWith(page, "Couldn't get the SVG: That vector is gone.")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("untouched");
  await page.unroute(fileUrl);

  // the browser refuses to copy (no clipboard, and the old way fails too): the card says to download instead
  await page.evaluate(() => {
    navigator.clipboard.writeText = () => Promise.reject(new Error("blocked"));
    document.execCommand = () => false;
  });
  await copy.click();
  await expect(toastWith(page, "Couldn't copy. Use Download SVG instead.")).toBeVisible();
});

// ------------------------------------------------------------------ the bin, when only Vector is in it
// Catches: a bin total that leaves Vector out (Empty bin switched off while a Vector run is the only thing in the bin), the counts not read again after the
// bin is emptied or a run is deleted for good, and keyboard focus lost when the last Vector card is deleted.
test("with only a Vector run in the bin Empty bin is on, and the counts and focus follow the run that goes", async ({ page }) => {
  const idea = unique("alone in the bin");
  const id = await makeRun(page, idea);
  expect((await page.request.post(`/api/runs/${id}/bin`, { headers: ASK })).ok()).toBe(true);
  await openVector(page);
  await expect(deletedOption(page)).toHaveText("Deleted 1");
  await deletedOption(page).click();
  const empty = bar(page).getByRole("button", { name: "Empty bin" });
  await expect(empty).toBeEnabled(); // the whole bin is one Vector run: that is not "empty"
  await empty.click();
  await expect(dialog(page, "Empty the bin?")).toContainText("Delete 1 run for good (1 on Vector), with their files. This can't be undone.");
  await dialog(page, "Empty the bin?").getByRole("button", { name: "Empty bin" }).click();
  await expect(toastWith(page, "Emptied the bin: 1 run deleted for good.")).toBeVisible();
  await expect(deletedOption(page)).toHaveText("Deleted 0"); // the numbers are read again
  await expect(empty).toBeDisabled(); // and the bin is empty now

  // Delete forever of a single run: the count follows it, and so does keyboard focus (to the form, as no card is left)
  const other = unique("deleted for good");
  const otherId = await makeRun(page, other);
  expect((await page.request.post(`/api/runs/${otherId}/bin`, { headers: ASK })).ok()).toBe(true);
  await expect(deletedOption(page)).toHaveText("Deleted 1");
  const c = vectorCard(page, other).first();
  await c.getByRole("button", { name: "Delete forever" }).click();
  await dialog(page, "Delete this run for good?").getByRole("button", { name: "Delete forever" }).click();
  await expect(c).toHaveCount(0);
  await expect(deletedOption(page)).toHaveText("Deleted 0");
  await expect(ideaBox(page)).toBeFocused();
});

// ------------------------------------------------------------------ the list: loading, trouble, an empty project, older runs
// Catches: a Vector list that never says it is loading, a failed read of the Kept list left as "Loading", and an empty project's view that names the wrong tab.
test("the Vector list says it is loading, says when it could not be read and offers Try again, and an empty project names the Vector tab", async ({ page }) => {
  const projectId = ((await (await page.request.post("/api/projects", { headers: ASK, data: { name: "Empty on Vector" } })).json()) as { id: string }).id;
  await openVector(page);
  // the Kept list is read slowly, then not at all, then properly
  let reads = 0;
  await page.route("**/api/runs?*kept=true*", async (route) => {
    reads += 1;
    if (reads === 1) {
      await new Promise((done) => setTimeout(done, 1200));
      await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: "Stub: the list cannot be read just now." }) });
    } else {
      await route.continue();
    }
  });
  await keptOption(page).click();
  await expect(page.getByText("Loading your vectors…")).toBeVisible(); // (while the first read is on its way)
  const trouble = page.locator("[role=alert]:visible", { hasText: "Couldn't load your kept runs" });
  await expect(trouble).toContainText("Stub: the list cannot be read just now.");
  await trouble.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText("No kept vectors yet")).toBeVisible();
  expect(reads).toBe(2);

  // a project with nothing of Vector's in it: the view says so for this tab
  await allOption(page).click();
  await bar(page).locator("[data-filter-project]").selectOption(projectId);
  const emptyView = page.locator(".empty-state:visible");
  await expect(emptyView).toContainText("Nothing in “Empty on Vector” on Vector yet");
  await emptyView.getByRole("button", { name: "Show any project" }).click();
  await expect(page.getByText("No vectors yet")).toBeVisible();
});

// Catches: a Vector tab that says "none yet" while its runs are on a later page (it must read on by itself), a Vector tab that never offers Load older runs, and a
// Load older runs on it that does nothing. The Kept list is scripted (as in kept.spec.ts) so that twenty newer runs need not be made: a real picture and a real
// vector, kept, are copied into the pages the server would send.
test("older runs: the Vector tab reads on by itself when its first page holds only pictures, and Load older runs works on it", async ({ page }) => {
  const idea = unique("the old vector");
  const vectorId = await makeRun(page, idea);
  const picture = await page.request.post("/api/runs", { headers: ASK, data: { prompt: unique("a picture"), options: { steps: 2, width: 512, height: 512, seed: 3 } } });
  const pictureId = ((await picture.json()) as { id: string }).id;
  await expect.poll(async () => (await status(page, pictureId)).status, { timeout: 30_000 }).toBe("done");
  for (const id of [vectorId, pictureId]) expect((await page.request.patch(`/api/runs/${id}`, { headers: ASK, data: { pinned: true } })).ok()).toBe(true);
  type Listed = ApiRun & { created_at: string };
  const real = (await (await page.request.get("/api/runs?kept=true&limit=10")).json()) as { runs: Listed[] };
  const vector = real.runs.find((run) => run.mode === "vector")!;
  const image = real.runs.find((run) => run.mode !== "vector")!;
  // a copy of a run, `n` seconds newer than the vector, with an id and (for a vector) a prompt of its own
  const newer = (run: Listed, n: number, prompt?: string): Listed => ({
    ...run, id: (0xb000 + n).toString(16).padStart(32, "0"), created_at: new Date(Date.parse(vector.created_at) + n * 1000).toISOString(), ...(prompt ? { prompt } : {}),
  });
  const pictures = (count: number) => Array.from({ length: count }, (_, n) => newer(image, 100 + n));

  // first, the newest twenty kept runs are all pictures and the vector is on the second page
  const newestVector = unique("the newer vector");
  let pages: "pictures first" | "vector first" = "pictures first";
  let reads = 0;
  await page.route("**/api/runs?*kept=true*", async (route) => {
    reads += 1;
    const asksForOlder = new URL(route.request().url()).searchParams.has("before");
    const first = pages === "pictures first" ? pictures(20) : [newer(vector, 200, newestVector), ...pictures(19)];
    const body = asksForOlder ? { runs: [vector], next_before: null } : { runs: first, next_before: first[first.length - 1].id };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await openVector(page);
  await keptOption(page).click();
  await expect(vectorCard(page, idea).first()).toBeVisible({ timeout: 15_000 }); // found without anyone pressing Load older runs
  expect(reads).toBe(2);
  await expect(page.getByText("No kept vectors yet")).toHaveCount(0);

  // then the first page holds a newer vector, and the older one is a button away
  pages = "vector first";
  reads = 0;
  await page.reload();
  await keptOption(page).click();
  await expect(vectorCard(page, newestVector).first()).toBeVisible();
  await expect(vectorCard(page, idea)).toHaveCount(0);
  const older = page.locator("button.load-more:visible", { hasText: "Load older runs" });
  await expect(older).toBeVisible();
  await older.click();
  await expect(vectorCard(page, idea).first()).toBeVisible();
  await expect(older).toHaveCount(0);
});

// ------------------------------------------------------------------ the card: the details a person reads
// Catches: a kind badge that shows the raw word, versions listed last first, the buttons of several versions that share one name, a long prompt that cannot be
// opened, a preview with no description or in the wrong shape, a card that does not say how long it took, and no warning that an unkept run is about to go.
test("the card's details: the kind, the versions in order and named, a long prompt that opens, the preview's description and shape, the time taken, the warning", async ({ page }) => {
  const idea = `${unique("details")} ${"a very long wish ".repeat(20)}`.trim(); // well over the 240 characters after which a prompt is shortened
  const id = await makeRun(page, idea, { colours: 3, kind: "line", width_mm: 60, height_mm: 40, num_images: 2 });
  ageRun(id, 25); // 30 days is how long an unkept run lives: five are left
  await openVector(page);
  const c = vectorCard(page, idea.slice(0, 30)).first();
  await expect(c).toBeVisible();
  await expect(c.locator(".badge", { hasText: "Line art" })).toBeVisible();
  await expect(c.locator(".run-expiry")).toContainText(/Will be deleted in \d+ days?\. Press Keep to save it\./);
  await c.getByRole("button", { name: "Keep" }).click();
  await expect(c.locator(".run-expiry")).toHaveCount(0); // kept: it will not go
  await expect(c.locator(".run-meta")).toHaveText(/^60 × 40 mm · 3 colours · line art · layers by colour, side by side · seeds 7–8 · (\d+m )?\d+s$/);

  // two versions, first first, and every button and link of a version says which version it is for
  expect(await c.locator(".vector-item").evaluateAll((items) => items.map((item) => item.getAttribute("aria-label")))).toEqual(["Version 1 of 2", "Version 2 of 2"]);
  for (const n of [1, 2]) {
    const named = (text: string) => `${text}, version ${n} of 2`;
    await expect(c.getByRole("link", { name: named("Download SVG") })).toHaveCount(1);
    await expect(c.getByRole("link", { name: named("Download layers (ZIP)") })).toHaveCount(1);
    await expect(c.getByRole("link", { name: named("Download PNG") })).toHaveCount(1);
    await expect(c.getByRole("button", { name: named("Copy SVG code") })).toHaveCount(1);
    await expect(c.getByLabel(named("PNG size, long side"))).toHaveCount(1);
  }

  // the preview: described for a screen reader, and in the sheet's own shape before the picture arrives
  const preview = c.locator(".vector-item").first().locator("img");
  await expect(preview).toHaveAttribute("alt", /^Preview of version 1 of 2: 60 by 40 mm, \d+ layers?$/);
  await expect(preview).toHaveCSS("aspect-ratio", "60 / 40");

  // a long prompt is shortened, and opens and closes
  const prompt = c.locator(".run-prompt");
  const opener = c.getByRole("button", { name: "Show full prompt" });
  await expect(prompt).toHaveClass(/clamped/);
  await expect(opener).toHaveAttribute("aria-expanded", "false");
  await opener.click();
  await expect(prompt).not.toHaveClass(/clamped/);
  await expect(c.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
});

// Catches: the two ends of the PNG range refused (16 and 8192 are allowed), and a link to the download variant of the SVG without the `download` attribute, which
// would open the file in the browser instead of saving it.
test("the PNG size accepts both ends of the server's range, and the SVG link saves rather than opens", async ({ page }) => {
  const idea = unique("range");
  const item = (await status(page, await makeRun(page, idea))).vectors[0];
  await openVector(page);
  const c = vectorCard(page, idea).first();
  const size = c.getByLabel("PNG size, long side");
  const png = c.getByRole("link", { name: "Download PNG" });
  for (const edge of ["16", "8192"]) {
    await size.fill(edge);
    await expect(png).toHaveAttribute("href", `${item.png_download_url}&size=${edge}`);
    await expect(c.getByRole("alert")).toHaveCount(0);
  }
  for (const name of ["Download SVG", "Download layers (ZIP)", "Download PNG"]) await expect(c.getByRole("link", { name })).toHaveAttribute("download", "");
});

// ------------------------------------------------------------------ Keep, locked
// Catches: Keep on a run that is filed in a project (locked) that still sends its request when pressed.
test("Keep on a run filed in a project is locked: pressing it asks the server nothing", async ({ page }) => {
  const idea = unique("locked keep");
  await makeRun(page, idea);
  await page.request.post("/api/projects", { headers: ASK, data: { name: "Locked jobs" } });
  await openVector(page);
  const c = vectorCard(page, idea).first();
  await c.locator('[data-action="project"]').click();
  await page.getByRole("menu", { name: "Projects" }).getByRole("menuitemradio", { name: "Locked jobs" }).click();
  await expect(c.locator("[data-project-chip]")).toContainText("Locked jobs");
  const writes: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "PATCH") writes.push(r.url());
  });
  await c.getByRole("button", { name: "Keep" }).click({ force: true }); // (marked aria-disabled, so Playwright would wait for it: a person can still press it)
  await page.waitForTimeout(500); // (a request made by the click would be on its way within milliseconds: nothing is, so there is nothing to wait for)
  expect(writes).toEqual([]);
  await expect(c.getByRole("button", { name: "Keep" })).toHaveAttribute("aria-pressed", "true");
});

// ------------------------------------------------------------------ what the check found, when it is more than the plain case
// Catches: a layer's own colour in the picture never shown (by shape every layer's colour is a label, so the picture's is the useful one), and the note on a run
// that kept some versions (done, with the reason another is missing) shown only for a failed run.
test("by shape each layer shows the colour it had in the picture, and a run that kept some versions says why another is missing", async ({ page }) => {
  const idea = unique("by shape");
  const shapeId = await makeRun(page, idea, { colours: 3, layers_by: "shape" });
  const first = (await status(page, shapeId)).vectors[0];
  const partialIdea = unique("partial");
  const partialId = await makeRun(page, partialIdea, { num_images: 2 });
  // the fake pipeline cannot make one version fail and another not, so the server's answer for the Kept list is changed on its way: the run has an error text
  expect((await page.request.patch(`/api/runs/${partialId}`, { headers: ASK, data: { pinned: true } })).ok()).toBe(true);
  expect((await page.request.patch(`/api/runs/${shapeId}`, { headers: ASK, data: { pinned: true } })).ok()).toBe(true);
  await page.route("**/api/runs?*kept=true*", async (route) => {
    const real = await route.fetch();
    const body = (await real.json()) as { runs: ApiRun[] };
    body.runs = body.runs.map((run) => (run.id === partialId ? { ...run, error: { message: "Version 2 could not be made: the clean-up found nothing to trace.", hint: "Try another prompt." } } : run));
    await route.fulfill({ response: real, json: body });
  });
  await openVector(page);
  await keptOption(page).click();

  const c = vectorCard(page, idea).first();
  await expect(c).toBeVisible();
  expect(first.check.layers.length).toBeGreaterThan(0);
  const rows = c.locator("table.vector-layers tbody tr");
  await expect(rows).toHaveCount(first.check.layers.length);
  for (const layer of first.check.layers) {
    const row = c.locator(`[data-layer-id="${layer.id}"]`);
    await expect(row.locator(".layer-colour")).toContainText("in the picture"); // by shape a layer's colour is a label, and the picture's own is shown beside it
  }

  const p = vectorCard(page, partialIdea).first();
  await expect(p.locator(".badge").first()).toHaveText("Done");
  const note = p.locator("[data-note=partial]");
  await expect(note).toContainText("Version 2 could not be made: the clean-up found nothing to trace.");
  await expect(note).toContainText("Try another prompt.");
  await expect(c.locator("[data-note=partial]")).toHaveCount(0);
});
