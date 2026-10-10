// Shared by the end-to-end specs: the built UI against the real backend (fake pipeline). See playwright.config.ts.
import { expect, test as base, type Locator, type Page } from "@playwright/test";

export { expect };
export type { Locator, Page };

// Every test fails on browser console errors: CSP violations and runtime errors surface there.
// "Failed to load resource" for a 4xx the test provoked on purpose is expected and ignored.
export const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error" && !/Failed to load resource: the server responded with a status of 4\d\d/.test(m.text())) errors.push(m.text());
      });
      page.on("pageerror", (e) => errors.push(String(e)));
      await use(errors);
      expect(errors, "browser console errors").toEqual([]);
    },
    { auto: true },
  ],
});

export const FAST = { mode: "generate", aspect: "custom", customWidth: 512, customHeight: 512, steps: 8, seedLocked: false, seed: 42,
  numImages: 1, negativePrompt: "", guidance: null, transparent: false };

export async function useOptions(page: Page, overrides: Record<string, unknown> = {}) {
  await page.addInitScript((opts) => localStorage.setItem("studio.options.v1", JSON.stringify(opts)), { ...FAST, ...overrides });
}

export const unique = (text: string) => `${text} ${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
export const card = (page: Page, prompt: string): Locator => page.locator("article.run-card", { hasText: prompt });
// exact: run cards carry aria-labels made from their prompts, which may contain the word "prompt"
export const promptBox = (page: Page): Locator => page.getByRole("textbox", { name: "Prompt", exact: true });

export async function generate(page: Page, prompt: string): Promise<Locator> {
  await promptBox(page).fill(prompt);
  await page.keyboard.press("Control+Enter");
  const c = card(page, prompt).first();
  await expect(c).toBeVisible();
  return c;
}

/** Delete every run over the API (waiting out one that's still generating), and empty the bin (DESIGN.md §30), which the list leaves out. */
export async function clearHistory(page: Page) {
  // the studio's API wants this header on every write: its guard against requests from other web pages
  const asked = { "X-Studio-Client": "1" };
  await expect
    .poll(async () => {
      const { runs } = (await (await page.request.get("/api/runs?limit=100")).json()) as { runs: { id: string; status: string }[] };
      for (const run of runs) {
        if (run.status !== "running") await page.request.delete(`/api/runs/${run.id}`, { headers: asked });
      }
      // the bin is not in the list above, so empty it too: a run left there would show in Deleted, and in the counts, of the next test
      await page.request.delete("/api/bin", { headers: asked });
      return runs.length;
    }, { timeout: 20_000 })
    .toBe(0);
}

