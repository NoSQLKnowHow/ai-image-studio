// End-to-end: the Vector tab (DESIGN.md §31, §31.12.7 and §31.12.8; criteria 135-143), in a real browser against the real server with the fake pipeline
// (the image worker draws a stand-in picture and the clean-up turns it into an SVG).
//
// WRITTEN BEFORE THE THREE BUILDS WERE MERGED, against the contract (backend/tests/vector_contract.json) and not against a running server. The first run after
// the merge may find a place where a guess was wrong; an assertion that fails for that reason should be corrected to what the contract says, and one that
// fails because the server differs from the contract is a finding. What the tests do not assume: how many layers the clean-up finds in the stand-in picture
// (they check the rules that hold for any picture: at most colours - 1 layers, white never has one, every note shown), and how fast a run is.
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
  await toastWith(page, /^Deleted “/).getByRole("button", { name: "Undo" }).click();
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
