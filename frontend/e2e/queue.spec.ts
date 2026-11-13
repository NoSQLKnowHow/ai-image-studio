// The Queue panel (DESIGN.md section 40, criteria 306 to 315), in a real browser against the real server with the fake pipeline: a waiting job dragged to a new place with the mouse
// and with a finger, the arrows from the keyboard, a move made on one page appearing on another, and the job that is going being locked. What the unit tests cannot see is
// here: real rows with real heights under a real pointer, the server's events reaching a second page, and the order the jobs are in fact worked on.
import { clearHistory, expect, test, unique, useOptions, type Locator, type Page } from "./helpers";

const ASK = { "X-Studio-Client": "1" };
const queueButton = (page: Page): Locator => page.getByRole("button", { name: /^Queue:/ });
const panel = (page: Page): Locator => page.getByRole("region", { name: "Queue" });
const rowOf = (page: Page, words: string): Locator => panel(page).locator("li[data-row]", { hasText: words });
const titles = (page: Page): Locator => panel(page).locator("li[data-row] .queue-title");

async function post(page: Page, prompt: string, options: Record<string, unknown> = {}): Promise<string> {
  const made = await page.request.post("/api/runs", { headers: ASK, data: { prompt, options: { steps: 2, width: 512, height: 512, seed: 3, ...options } } });
  expect(made.status()).toBe(201);
  return ((await made.json()) as { id: string }).id;
}

/** A run that keeps the worker busy for several seconds (the fake pipeline takes 10 ms a step: 100 steps of 8 pictures), so that what is made after it waits. */
async function busy(page: Page, words?: string): Promise<{ id: string; prompt: string }> {
  const prompt = words ?? unique("busy");
  const id = await post(page, prompt, { steps: 100, num_images: 8 });
  await expect.poll(async () => ((await (await page.request.get(`/api/runs/${id}`)).json()) as { status: string }).status, { timeout: 20_000 }).toBe("running");
  return { id, prompt };
}

const queueNow = async (page: Page) => (await (await page.request.get("/api/queue")).json()) as { running: { id: string } | null; queued: { id: string; title: string }[] };

/** Stop everything that is going or waiting, so that the next test starts with an idle worker and an empty queue. */
async function drain(page: Page) {
  await expect.poll(async () => {
    const queue = await queueNow(page);
    for (const run of [...(queue.running ? [queue.running] : []), ...queue.queued]) await page.request.post(`/api/runs/${run.id}/cancel`, { headers: ASK });
    return queue.running === null && queue.queued.length === 0;
  }, { timeout: 30_000 }).toBe(true);
}

/** What a person really writes: a prompt much longer than the panel is wide, which the server shortens to 80 characters and the panel must shorten again, to what fits. */
const longWords = (tag: string) => `${tag} a cinematic photorealistic film still set on a dusty old-western railway platform at golden hour, long shadows, a lone traveller waiting beside a battered trunk`;

/** Every row of the open panel, the job going and the waiting ones, ends inside the panel, and the panel is inside the window. A row wider than the panel is what a long title does to a
 *  grid whose column is allowed to grow to fit it: the page of someone with real prompts saw it, and tests with short words could not. */
async function expectRowsInsidePanel(page: Page) {
  const outer = await panel(page).boundingBox();
  const window = page.viewportSize();
  expect(outer && window).toBeTruthy();
  expect(outer!.x).toBeGreaterThanOrEqual(0);
  expect(outer!.x + outer!.width).toBeLessThanOrEqual(window!.width);
  const rows = await panel(page).locator("li[data-row], .queue-now").all();
  expect(rows.length).toBeGreaterThanOrEqual(3);
  for (const row of rows) {
    const box = await row.boundingBox();
    expect(box!.x, "a row starts inside the panel").toBeGreaterThanOrEqual(outer!.x - 0.5);
    expect(box!.x + box!.width, `a row ends inside the panel (${box!.x + box!.width} against ${outer!.x + outer!.width})`).toBeLessThanOrEqual(outer!.x + outer!.width + 0.5);
  }
  // the words that do not fit are cut with an ellipsis, not pushed out
  const cut = await panel(page).locator(".queue-title").evaluateAll((els) => els.every((el) => el.scrollWidth <= el.clientWidth || getComputedStyle(el).textOverflow === "ellipsis"));
  expect(cut).toBe(true);
}

const startedAt = async (page: Page, id: string) => ((await (await page.request.get(`/api/runs/${id}`)).json()) as { started_at: string | null }).started_at;

test.beforeEach(async ({ page }) => {
  await clearHistory(page);
  await useOptions(page);
});

test.afterEach(async ({ page }) => {
  await drain(page);
});

test("a waiting job is dragged to a new place in the Queue panel, and the jobs are worked on in that order", async ({ page }) => {
  await page.goto("/");
  const going = await busy(page);
  const [a, b, c] = [unique("first"), unique("second"), unique("third")];
  const ids = [await post(page, a), await post(page, b), await post(page, c)];

  await queueButton(page).click();
  await expect(panel(page).locator(".queue-now")).toContainText(going.prompt);
  await expect(titles(page)).toHaveText([a, b, c]);

  // the third job's grip, dropped on the upper half of the first job's row
  await rowOf(page, c).locator(".queue-grip").dragTo(rowOf(page, a), { targetPosition: { x: 60, y: 4 } });
  await expect(titles(page)).toHaveText([c, a, b]);
  await expect(panel(page).getByRole("status")).toHaveText(`Moved “${c}” to place 1 of 3.`);
  await expect.poll(async () => (await queueNow(page)).queued.map((q) => q.id)).toEqual([ids[2], ids[0], ids[1]]);   // the server has it, not only the page
  await expect(page.locator("article.run-card", { hasText: c }).locator(".badge").first()).toHaveText("Queued · #1");   // and the cards say the same

  // let the busy run go and see what is worked on next
  await page.request.post(`/api/runs/${going.id}/cancel`, { headers: ASK });
  for (const id of ids) {
    await expect.poll(async () => ((await (await page.request.get(`/api/runs/${id}`)).json()) as { status: string }).status, { timeout: 30_000 }).toBe("done");
  }
  const started = await Promise.all(ids.map((id) => startedAt(page, id)));
  expect([...ids].sort((x, y) => String(started[ids.indexOf(x)]).localeCompare(String(started[ids.indexOf(y)])))).toEqual([ids[2], ids[0], ids[1]]);
});

test("the arrows move a job from the keyboard, say where it went, and keep focus where a person can carry on", async ({ page }) => {
  await page.goto("/");
  await busy(page);
  const [a, b] = [unique("first"), unique("second")];
  await post(page, a);
  await post(page, b);
  await queueButton(page).click();
  await expect(titles(page)).toHaveText([a, b]);

  const up = panel(page).getByRole("button", { name: `Move “${b}” up` });
  await up.focus();
  await page.keyboard.press("Enter");
  await expect(titles(page)).toHaveText([b, a]);
  await expect(panel(page).getByRole("status")).toHaveText(`Moved “${b}” to place 1 of 2.`);
  await expect(panel(page).getByRole("button", { name: `Move “${b}” down` })).toBeFocused();   // it is first now, so its up arrow is off and focus went to the other
  await expect(panel(page).getByRole("button", { name: `Move “${b}” up` })).toBeDisabled();
});

test("a move made on one page appears on another that has the panel open, without it doing anything", async ({ page }) => {
  await page.goto("/");
  await busy(page);
  const [a, b] = [unique("first"), unique("second")];
  await post(page, a);
  await post(page, b);
  await queueButton(page).click();

  const other = await page.context().newPage();
  await other.goto("/");
  await queueButton(other).click();
  await expect(titles(other)).toHaveText([a, b]);

  await panel(page).getByRole("button", { name: `Move “${b}” up` }).click();
  await expect(titles(page)).toHaveText([b, a]);
  await expect(titles(other)).toHaveText([b, a]);
  await other.close();
});

test("the job that is going is locked: no grip, no arrows, and the server refuses to move it", async ({ page }) => {
  await page.goto("/");
  const going = await busy(page);
  const a = unique("waiting");
  const id = await post(page, a);
  await queueButton(page).click();
  const now = panel(page).locator(".queue-now");
  await expect(now).toContainText(going.prompt);
  await expect(now.locator(".queue-lock")).toBeVisible();
  await expect(now.getByRole("button")).toHaveCount(0);
  await expect(rowOf(page, a).getByRole("button", { name: `Move “${a}” up` })).toBeDisabled();   // nothing can go before the job that is going
  const refused = await page.request.post("/api/queue/move", { headers: ASK, data: { id: going.id, after: id } });
  expect(refused.status()).toBe(409);
  expect(((await refused.json()) as { code: string }).code).toBe("not_movable");
});

test("long prompts are cut to fit the panel, and no row runs out of it", async ({ page }) => {
  await page.goto("/");
  await busy(page, longWords("going"));
  for (const tag of ["first", "second"]) await post(page, longWords(unique(tag)));
  await queueButton(page).click();
  await expect(titles(page)).toHaveCount(2);
  await expectRowsInsidePanel(page);
  await expect(panel(page).getByRole("button", { name: /^Move .* up$/ }).first()).toBeInViewport();    // and the arrows are still in reach at the right-hand end of each row
  await expect(panel(page).getByRole("button", { name: /^Move .* down$/ }).first()).toBeInViewport();
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 360, height: 800 }, hasTouch: true });

  test("the panel fits, its arrows can be reached, and a thumb can drag a job (criteria 312, 315)", async ({ page }) => {
    await page.goto("/");
    await busy(page, longWords("going"));
    const [a, b, c] = [unique("first"), unique("second"), unique("third")];
    for (const words of [a, b, c]) await post(page, longWords(words));
    await queueButton(page).click();
    await expect(titles(page)).toHaveCount(3);
    await expectRowsInsidePanel(page);
    const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(await overflow()).toBeLessThanOrEqual(0);
    for (const words of [a, b, c]) {
      await expect(panel(page).getByRole("button", { name: new RegExp(`^Move “${words}.*” down$`) })).toBeInViewport();   // named by the job's words, which are cut at 80 characters
    }

    // a finger, by the protocol: touch down on the third job's grip, drag up over the first job's row, lift
    const grip = await rowOf(page, c).locator(".queue-grip").boundingBox();
    const first = await rowOf(page, a).boundingBox();
    expect(grip && first).toBeTruthy();
    const from = { x: grip!.x + grip!.width / 2, y: grip!.y + grip!.height / 2 };
    const to = { x: from.x, y: first!.y + 4 };
    const touch = await page.context().newCDPSession(page);
    await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [from] });
    for (let step = 1; step <= 8; step++) {
      await touch.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: from.x, y: from.y + ((to.y - from.y) * step) / 8 }] });
    }
    await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(titles(page)).toHaveText([c, a, b].map((tag) => new RegExp(`^${tag}`)));   // the titles are the long words, which begin with the tag
    expect(await overflow()).toBeLessThanOrEqual(0);
  });
});
