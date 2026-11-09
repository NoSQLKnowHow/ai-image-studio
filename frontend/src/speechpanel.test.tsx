import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SpeechPanel } from "./components/SpeechPanel";
import type { Prepared, PreparedPart } from "./speechTypes";
import { CAPS, makePrepared } from "./testdata";
import type { Capabilities } from "./types";

// The Speech tab's first half (DESIGN.md §38.2): whether speech works here at all, the three ways to give a text, and the review of its parts. `fetch` is replaced by
// a table of answers, so what the page sends is looked at and nothing leaves the test.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

interface Call {
  url: string;
  init: RequestInit & { headers: Record<string, string> };
}
type Answer = { status?: number; body: unknown } | Promise<{ status?: number; body: unknown }>;

/** Answer every call with `answer(url, init)`; the calls are returned for the test to look at. */
function server(answer: (call: Call) => Answer): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: Call["init"]) => {
    const call = { url, init };
    calls.push(call);
    const { status = 200, body } = await answer(call);
    return new Response(JSON.stringify(body), { status });
  }));
  return calls;
}

const part = (title: string, paragraphs: [string, string, (string | null)?][], extra: Partial<PreparedPart> = {}): PreparedPart => ({
  title, chars: paragraphs.filter((p) => !p[2]).reduce((n, p) => n + p[1].length, 0), minutes: 1, include: true, note: null,
  paragraphs: paragraphs.map(([kind, text, skipped = null]) => ({ kind: kind as "heading" | "paragraph", text, skipped })), ...extra,
});
const prepared = (parts: PreparedPart[], extra: Partial<Prepared> = {}): Prepared => makePrepared({ title: "A short book", parts, warnings: [], cleaning: [], ...extra });
const BOOK = prepared([
  part("Chapter one", [["heading", "Chapter one"], ["paragraph", "It was a bright cold day in April."]]),
  part("Chapter two", [["paragraph", "The second chapter begins here, with some words."]]),
  part("Chapter three", [["paragraph", "And the third."]]),
]);

const show = (caps: Capabilities = CAPS, hidden = false) => render(<SpeechPanel hidden={hidden} caps={caps} />);
const typeText = (text: string) => fireEvent.change(screen.getByLabelText("Paste the text"), { target: { value: text } });
const press = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const parts = () => within(screen.getByRole("list", { name: "Parts" })).getAllByRole("listitem", { name: "" }).filter((li) => li.classList.contains("speech-part"));
const jsonBody = (call: Call) => JSON.parse(call.init.body as string);

// ------------------------------------------------------------------ the frame
describe("the tab", () => {
  it("is the panel of the Speech tab: labelled by it, and hidden while another tab is open", () => {
    const { container } = show(CAPS, true);
    const panel = container.querySelector("#panel-speech") as HTMLElement;
    expect(panel.getAttribute("role")).toBe("tabpanel");
    expect(panel.getAttribute("aria-labelledby")).toBe("tab-speech");
    expect(panel.hidden).toBe(true);
    cleanup();
    expect((show(CAPS, false).container.querySelector("#panel-speech") as HTMLElement).hidden).toBe(false);
  });

  it("says why speech is not available, and what to do, and that the other tabs work (criterion 259)", () => {
    const caps = { ...CAPS, speech: { ...CAPS.speech, available: false, reason: "No speech engine can run here.", hint: "Each engine's own reason is below." } };
    show(caps);
    expect(screen.getByRole("heading", { name: "Speech isn't available on this server" })).toBeTruthy();
    expect(screen.getByText("No speech engine can run here.")).toBeTruthy();
    expect(screen.getByText("Each engine's own reason is below.")).toBeTruthy();
    expect(screen.getByText("Images, music and vector work as usual.")).toBeTruthy();
    expect(screen.queryByLabelText("Paste the text")).toBeNull(); // nothing to prepare with
  });

  it("says the engines are being checked at start-up, and not that speech is unavailable", () => {
    const engines = CAPS.speech.engines.map((engine) => ({ ...engine, state: "checking" as const }));
    show({ ...CAPS, speech: { ...CAPS.speech, available: false, reason: "The speech engines are still being checked.", engines } });
    expect(screen.getByRole("heading", { name: "Checking the speech engines…" })).toBeTruthy();
    expect(screen.queryByText(/isn't available/)).toBeNull();
  });

  it("shows the source and no review before anything is prepared", () => {
    show();
    expect(screen.getByRole("heading", { name: "Text to read" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "What will be read" })).toBeNull();
  });
});

// ------------------------------------------------------------------ pasted text
describe("pasted text", () => {
  it("is the way chosen at first, with Prepare text off until there is something to prepare, and a count under the box", () => {
    show();
    expect(screen.getByRole("radio", { name: "Text" }).getAttribute("aria-checked")).toBe("true");
    const button = screen.getByRole("button", { name: "Prepare text" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    typeText("   ");
    expect(button.disabled).toBe(true); // spaces are not a text
    typeText("x".repeat(12345));
    expect(button.disabled).toBe(false);
    expect(screen.getByText("12,345 characters, about 15 min to speak")).toBeTruthy();
  });

  it("is sent as it stands, with the two switches, and the review it comes back as is shown", async () => {
    const calls = server(() => ({ body: BOOK }));
    show();
    typeText("Chapter one\n\nIt was a bright cold day in April.");
    press("Prepare text");
    expect(await screen.findByRole("heading", { name: "What will be read" })).toBeTruthy();
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/speech/prepare");
    expect(jsonBody(calls[0])).toEqual({ source: "text", text: "Chapter one\n\nIt was a bright cold day in April.", clean: true, read_extras: false });
    expect((screen.getByLabelText("Title of the reading") as HTMLInputElement).value).toBe("A short book");
    expect(parts().map((li) => within(li).getByText(/^Chapter/).textContent)).toEqual(["Chapter one", "Chapter two", "Chapter three"]);
  });

  it("says it is working while it waits, and the buttons and the box are off meanwhile", async () => {
    let release: (value: { body: unknown }) => void = () => undefined;
    server(() => new Promise((resolve) => { release = resolve; }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    expect((await screen.findByRole("status")).textContent).toMatch(/Preparing the text/);
    expect((screen.getByLabelText("Paste the text") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Prepare text" }) as HTMLButtonElement).disabled).toBe(true);
    release({ body: BOOK });
    expect(await screen.findByRole("heading", { name: "What will be read" })).toBeTruthy();
    expect(screen.queryByText(/Preparing the text/)).toBeNull();
    expect((screen.getByLabelText("Paste the text") as HTMLTextAreaElement).disabled).toBe(false);
  });

  it("sends the switches as they are set", async () => {
    const calls = server(() => ({ body: BOOK }));
    show();
    fireEvent.click(screen.getByRole("checkbox", { name: /Clean up the text/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Read code and tables too/ }));
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    expect(jsonBody(calls[0])).toMatchObject({ clean: false, read_extras: true });
  });

  it("shows the server's refusal in its own words, with what to do, and no review", async () => {
    server(() => ({ status: 422, body: { detail: "No text that can be read was found.", code: "no_text", hint: "Paste the text itself." } }));
    show();
    typeText("....");
    press("Prepare text");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No text that can be read was found.");
    expect(alert.textContent).toContain("Paste the text itself.");
    expect(screen.queryByRole("heading", { name: "What will be read" })).toBeNull();
  });

  it("says when the studio cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach the studio server.");
  });

  it("clears an old problem as soon as the next attempt starts", async () => {
    let first = true;
    server(() => (first ? ((first = false), { status: 422, body: { detail: "Not this time.", code: "x" } }) : { body: BOOK }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("alert");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// ------------------------------------------------------------------ the ways in
describe("the three ways in", () => {
  it("are Text, Web page and File, one radio group, and the arrow keys move between them", () => {
    show();
    const group = screen.getByRole("radiogroup", { name: "Where the text comes from" });
    expect(within(group).getAllByRole("radio").map((r) => r.textContent)).toEqual(["Text", "Web page", "File"]);
    const text = screen.getByRole("radio", { name: "Text" });
    fireEvent.keyDown(text, { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: "Web page" }).getAttribute("aria-checked")).toBe("true");
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: "Web page" }));
    fireEvent.keyDown(screen.getByRole("radio", { name: "Web page" }), { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: "File" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.keyDown(screen.getByRole("radio", { name: "File" }), { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: "Text" }).getAttribute("aria-checked")).toBe("true"); // wraps
    fireEvent.keyDown(screen.getByRole("radio", { name: "Text" }), { key: "ArrowLeft" });
    expect(screen.getByRole("radio", { name: "File" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.keyDown(screen.getByRole("radio", { name: "File" }), { key: "a" });
    expect(screen.getByRole("radio", { name: "File" }).getAttribute("aria-checked")).toBe("true");
  });

  it("only the chosen one is in the tab order, and shows only its own controls", () => {
    show();
    expect(screen.getByRole("radio", { name: "Text" }).tabIndex).toBe(0);
    expect(screen.getByRole("radio", { name: "Web page" }).tabIndex).toBe(-1);
    expect(screen.queryByLabelText("Address of the web page")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Web page" }));
    expect(screen.getByLabelText("Address of the web page")).toBeTruthy();
    expect(screen.queryByLabelText("Paste the text")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "File" }));
    expect(screen.getByLabelText("File")).toBeTruthy();
    expect(screen.queryByLabelText("Address of the web page")).toBeNull();
  });

  it("keep what was typed in each while another is shown", () => {
    show();
    typeText("my pasted words");
    fireEvent.click(screen.getByRole("radio", { name: "Web page" }));
    fireEvent.change(screen.getByLabelText("Address of the web page"), { target: { value: "example.org/a" } });
    fireEvent.click(screen.getByRole("radio", { name: "Text" }));
    expect((screen.getByLabelText("Paste the text") as HTMLTextAreaElement).value).toBe("my pasted words");
    fireEvent.click(screen.getByRole("radio", { name: "Web page" }));
    expect((screen.getByLabelText("Address of the web page") as HTMLInputElement).value).toBe("example.org/a");
  });
});

// ------------------------------------------------------------------ a web page
describe("a web page", () => {
  const openUrl = () => {
    show();
    fireEvent.click(screen.getByRole("radio", { name: "Web page" }));
  };
  const address = (value: string) => fireEvent.change(screen.getByLabelText("Address of the web page"), { target: { value } });

  it("is fetched from the address, with https:// put in front of one typed without it, and the address in the box is the one that was sent", async () => {
    const calls = server(() => ({ body: prepared([part("The page", [["paragraph", "Words of the page."]])], { source: { kind: "url", url: "https://example.org/page", host: "example.org" } }) }));
    openUrl();
    expect((screen.getByRole("button", { name: "Fetch" }) as HTMLButtonElement).disabled).toBe(true);
    address("  example.org/page ");
    press("Fetch");
    await screen.findByRole("heading", { name: "What will be read" });
    expect(jsonBody(calls[0])).toEqual({ source: "url", url: "https://example.org/page", clean: true, read_extras: false });
    expect((screen.getByLabelText("Address of the web page") as HTMLInputElement).value).toBe("https://example.org/page");
  });

  it("is fetched by Enter in the box as well", async () => {
    const calls = server(() => ({ body: BOOK }));
    openUrl();
    address("https://example.org/a");
    fireEvent.keyDown(screen.getByLabelText("Address of the web page"), { key: "Enter" });
    await screen.findByRole("heading", { name: "What will be read" });
    expect(calls).toHaveLength(1);
  });

  it.each([["ftp://example.org/file", "A web address starts with http:// or https://."], ["https://", "That is not a web address."]])(
    "does not send %s, and says why", (value, words) => {
      const calls = server(() => ({ body: BOOK }));
      openUrl();
      address(value);
      press("Fetch");
      expect(screen.getByRole("alert").textContent).toContain(words);
      expect(calls).toHaveLength(0);
    });

  it("shows the server's refusal of an address on a private network, with its hint", async () => {
    server(() => ({ status: 422, body: { detail: "That address is on a private network.", code: "blocked_address", hint: "Only public web pages can be read." } }));
    openUrl();
    address("http://192.168.1.5/");
    press("Fetch");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("That address is on a private network.");
    expect(alert.textContent).toContain("Only public web pages can be read.");
  });

  it("says what is allowed under the box, with the limit from the server", () => {
    openUrl();
    expect(screen.getByText(/Public web pages only .* up to 5 MB\)\. Addresses on your own network are refused\./)).toBeTruthy();
  });
});

// ------------------------------------------------------------------ a file
describe("a file", () => {
  const openFile = () => {
    show();
    fireEvent.click(screen.getByRole("radio", { name: "File" }));
  };
  const file = (name: string, size = 1000, type = "") => {
    const f = new File(["x"], name, { type });
    Object.defineProperty(f, "size", { value: size });
    return f;
  };
  const choose = (f: File) => fireEvent.change(screen.getByLabelText("File"), { target: { files: [f] } });

  it("is read as soon as it is chosen, sent as the body with its name and the switches in the query, and shown by name", async () => {
    const calls = server(() => ({ body: BOOK }));
    openFile();
    choose(file("my book & more.epub"));
    await screen.findByRole("heading", { name: "What will be read" });
    expect(calls[0].url).toBe("/api/speech/prepare/file?name=my+book+%26+more.epub&clean=true&read_extras=false");
    expect((calls[0].init.body as File).name).toBe("my book & more.epub");
    expect(screen.getByText("my book & more.epub")).toBeTruthy();
  });

  it("offers the kinds of file the server reads, and the size limit from the server", () => {
    openFile();
    expect((screen.getByLabelText("File") as HTMLInputElement).accept).toBe(".txt,.text,.md,.markdown,.pdf,.epub,.docx");
    expect(screen.getByText("A .txt, .md, .pdf, .epub or .docx file, up to 50 MB.")).toBeTruthy();
  });

  it.each([
    ["picture.png", 1000, /picture\.png is not a kind of file that can be read\. Choose a \.txt, \.md, \.pdf, \.epub or \.docx file\./],
    ["empty.txt", 0, /empty\.txt is empty\./],
    ["huge.pdf", 60 * 1024 * 1024, /huge\.pdf is 60 MB; a file can be at most 50 MB\./],
  ])("%s is refused here, without being uploaded", (name, size, words) => {
    const calls = server(() => ({ body: BOOK }));
    openFile();
    choose(file(name, size));
    expect(screen.getByRole("alert").textContent).toMatch(words);
    expect(calls).toHaveLength(0);
  });

  it("is taken when it is dropped on the box, and the page does not call it a picture", async () => {
    const calls = server(() => ({ body: BOOK }));
    const windowDrop = vi.fn();
    window.addEventListener("drop", windowDrop);
    try {
      openFile();
      const zone = document.querySelector("[data-speech-drop]") as HTMLElement;
      fireEvent.dragOver(zone, { dataTransfer: { files: [], types: ["Files"] } });
      expect(zone.classList.contains("dragging")).toBe(true);
      fireEvent.drop(zone, { dataTransfer: { files: [file("notes.txt")], types: ["Files"] } });
      await screen.findByRole("heading", { name: "What will be read" });
      expect(calls[0].url).toContain("name=notes.txt");
      expect(zone.classList.contains("dragging")).toBe(false);
      expect(windowDrop).not.toHaveBeenCalled(); // the box answered for it
    } finally {
      window.removeEventListener("drop", windowDrop);
    }
  });

  it("reads the first of several files dropped together", async () => {
    const calls = server(() => ({ body: BOOK }));
    openFile();
    fireEvent.drop(document.querySelector("[data-speech-drop]") as HTMLElement, { dataTransfer: { files: [file("first.txt"), file("second.txt")], types: ["Files"] } });
    await screen.findByRole("heading", { name: "What will be read" });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("name=first.txt");
  });

  it("can be chosen again after it was chosen, since the box is told to forget it (a browser does not repeat a choice that has not changed)", async () => {
    const calls = server(() => ({ body: BOOK }));
    openFile();
    const input = screen.getByLabelText("File") as HTMLInputElement;
    const assigned: string[] = [];
    Object.defineProperty(input, "value", { configurable: true, get: () => "", set: (value: string) => void assigned.push(value) }); // jsdom keeps no file name to look at: watch what is set
    fireEvent.change(input, { target: { files: [file("same.txt")] } });
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(assigned).toEqual([""]);
  });

  it("can be stopped while it is being read, and that is not reported as a failure", async () => {
    let signal: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      signal = init.signal;
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    openFile();
    choose(file("long.epub"));
    press("Stop");
    await waitFor(() => expect(signal?.aborted).toBe(true));
    await waitFor(() => expect(screen.queryByText(/Preparing the text/)).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("heading", { name: "What will be read" })).toBeNull();
  });
});

// ------------------------------------------------------------------ the review
describe("the review", () => {
  const prepare = async (book: Prepared = BOOK) => {
    server(() => ({ body: book }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
  };
  const row = (n: number) => parts()[n];

  it("shows each part with its title, how long it takes, and the line above says how many are included", async () => {
    await prepare();
    expect(screen.getByText("3 of 3 parts included · less than a minute of audio")).toBeTruthy();
    expect(within(row(0)).getByText("Chapter one")).toBeTruthy();
    expect(within(row(0)).getByText("less than a minute")).toBeTruthy();
  });

  it("leaves a part out with its checkbox, greys it, says so in words, and counts it out", async () => {
    await prepare();
    const box = within(row(1)).getByRole("checkbox", { name: "Include part 2: Chapter two" }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(box.checked).toBe(false);
    expect(row(1).classList.contains("left-out")).toBe(true);
    expect(within(row(1)).getByText("Left out")).toBeTruthy();
    expect(screen.getByText(/^2 of 3 parts included/)).toBeTruthy();
    fireEvent.click(box);
    expect(within(row(1)).queryByText("Left out")).toBeNull();
    expect(screen.getByText(/^3 of 3 parts included/)).toBeTruthy();
  });

  it("starts a part the server left out (front matter) unticked, with the server's note", async () => {
    await prepare(prepared([part("Cover", [["paragraph", "Cover words."]], { include: false, note: "Front matter" }), part("Chapter one", [["paragraph", "Words."]])]));
    expect((within(row(0)).getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
    expect(within(row(0)).getByText("Front matter")).toBeTruthy();
    expect(within(row(0)).getByText("Left out")).toBeTruthy();
    expect(screen.getByText(/^1 of 2 parts included/)).toBeTruthy();
  });

  it("renames a part: the box opens with the title, typing changes it, Enter closes it and returns to the button", async () => {
    await prepare();
    fireEvent.click(within(row(0)).getByRole("button", { name: "Rename" }));
    const box = within(row(0)).getByLabelText("Title of part 1") as HTMLInputElement;
    expect(box.value).toBe("Chapter one");
    expect(document.activeElement).toBe(box);
    fireEvent.change(box, { target: { value: "The beginning" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(within(row(0)).getByText("The beginning")).toBeTruthy();
    expect(within(row(0)).queryByLabelText("Title of part 1")).toBeNull();
    expect(document.activeElement).toBe(within(row(0)).getByRole("button", { name: "Rename" }));
    expect(within(row(0)).getByRole("checkbox", { name: "Include part 1: The beginning" })).toBeTruthy(); // its name follows
  });

  it("names a part 'Part n' when its title is made empty", async () => {
    await prepare();
    fireEvent.click(within(row(2)).getByRole("button", { name: "Rename" }));
    fireEvent.change(within(row(2)).getByLabelText("Title of part 3"), { target: { value: "" } });
    fireEvent.keyDown(within(row(2)).getByLabelText("Title of part 3"), { key: "Escape" });
    expect(within(row(2)).getByText("Part 3")).toBeTruthy();
    expect(within(row(2)).getByRole("checkbox", { name: "Include part 3: Part 3" })).toBeTruthy(); // the checkbox is named by what is shown, not by nothing
  });

  it("joins a part to the next: one part fewer, the first one's title, and all the paragraphs", async () => {
    await prepare();
    fireEvent.click(within(row(0)).getByRole("button", { name: "Merge with next" }));
    expect(parts()).toHaveLength(2);
    expect(within(row(0)).getByText("Chapter one")).toBeTruthy();
    fireEvent.click(within(row(0)).getByRole("button", { name: "Show text" }));
    expect(within(row(0)).getAllByRole("textbox")).toHaveLength(3); // the heading, the paragraph, and chapter two's
    expect(screen.getByText(/^2 of 2 parts included/)).toBeTruthy();
  });

  it("cannot join the last part, or an included part to one that is left out, says why, and does nothing when pressed", async () => {
    await prepare();
    const last = within(row(2)).getByRole("button", { name: "Merge with next" });
    expect(last.getAttribute("aria-disabled")).toBe("true");
    expect(within(row(2)).getByText("There is no part after this one.").classList.contains("sr-only")).toBe(true);
    fireEvent.click(last);
    expect(parts()).toHaveLength(3);
    // leave part two out: part one (included) and part two (left out) cannot be joined, nor can part two and part three (included)
    expect(within(row(0)).getByRole("button", { name: "Merge with next" }).getAttribute("aria-disabled")).toBeNull();
    fireEvent.click(within(row(1)).getByRole("checkbox"));
    for (const n of [0, 1]) {
      const mismatch = within(row(n)).getByRole("button", { name: "Merge with next" });
      expect(mismatch.getAttribute("aria-disabled"), `part ${n + 1}`).toBe("true");
      expect(within(row(n)).getByText("Include both parts, or leave both out, before joining them.")).toBeTruthy();
      fireEvent.click(mismatch);
      expect(parts()).toHaveLength(3); // nothing is joined
    }
    fireEvent.click(within(row(2)).getByRole("checkbox")); // and with parts two and three both left out they can be joined
    expect(within(row(1)).getByRole("button", { name: "Merge with next" }).getAttribute("aria-disabled")).toBeNull();
    fireEvent.click(within(row(1)).getByRole("button", { name: "Merge with next" }));
    expect(parts()).toHaveLength(2);
  });

  it("keeps the button where it was when the last join is made, so focus is not lost", async () => {
    await prepare(prepared([part("One", [["paragraph", "Words one."]]), part("Two", [["paragraph", "Words two."]])]));
    const button = within(row(0)).getByRole("button", { name: "Merge with next" });
    button.focus();
    fireEvent.click(button);
    expect(parts()).toHaveLength(1);
    const same = within(row(0)).getByRole("button", { name: "Merge with next" });
    expect(same).toBe(button);
    expect(same.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(button);
  });

  it("opens a part to show its paragraphs as boxes that can be edited, and closes it again", async () => {
    await prepare();
    const toggle = within(row(0)).getByRole("button", { name: "Show text" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.textContent).toBe("Hide text");
    const boxes = within(row(0)).getAllByRole("textbox") as HTMLTextAreaElement[];
    expect(boxes.map((b) => b.getAttribute("aria-label"))).toEqual(["Heading 1 of part 1", "Paragraph 2 of part 1"]);
    fireEvent.change(boxes[1], { target: { value: "It was a bright warm day in May." } });
    expect((within(row(0)).getAllByRole("textbox")[1] as HTMLTextAreaElement).value).toBe("It was a bright warm day in May.");
    fireEvent.click(toggle);
    expect(within(row(0)).queryAllByRole("textbox")).toHaveLength(0);
    fireEvent.click(toggle);
    expect((within(row(0)).getAllByRole("textbox")[1] as HTMLTextAreaElement).value).toBe("It was a bright warm day in May."); // the change is kept
  });

  it("shows what the studio leaves out greyed, with the reason, and not as a box to edit", async () => {
    await prepare(prepared([part("Code", [["paragraph", "Before the code."], ["paragraph", "let x = 1;", "code"], ["paragraph", "a | b", "table"]])]));
    fireEvent.click(within(row(0)).getByRole("button", { name: "Show text" }));
    expect(within(row(0)).getAllByRole("textbox")).toHaveLength(1);
    expect(within(row(0)).getByText("let x = 1;").closest("li")?.classList.contains("skipped")).toBe(true);
    expect(within(row(0)).getByText("Not read: code")).toBeTruthy();
    expect(within(row(0)).getByText("Not read: table")).toBeTruthy();
  });

  it("shows a long part a hundred paragraphs at a time", async () => {
    const many = Array.from({ length: 250 }, (_, i): [string, string] => ["paragraph", `Paragraph number ${i + 1}.`]);
    await prepare(prepared([part("Long", many)]));
    fireEvent.click(within(row(0)).getByRole("button", { name: "Show text" }));
    expect(within(row(0)).getAllByRole("textbox")).toHaveLength(100);
    fireEvent.click(within(row(0)).getByRole("button", { name: "Show the next 100 of 150 paragraphs" }));
    expect(within(row(0)).getAllByRole("textbox")).toHaveLength(200);
    fireEvent.click(within(row(0)).getByRole("button", { name: "Show the next 50 of 50 paragraphs" }));
    expect(within(row(0)).getAllByRole("textbox")).toHaveLength(250);
    expect(within(row(0)).queryByRole("button", { name: /Show the next/ })).toBeNull();
  });

  it("lists what the server warned of, and what the clean-up did, folded away", async () => {
    await prepare(prepared([part("One", [["paragraph", "Words."]])], { warnings: ["Some pages of the PDF had no text."], cleaning: ["Removed 3 footnote marks.", "Joined 2 words split by a hyphen."] }));
    expect(within(screen.getByRole("list", { name: "Things to know about this text" })).getByText("Some pages of the PDF had no text.")).toBeTruthy();
    const details = screen.getByText("What the clean-up did (2)").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(within(details).getByText("Removed 3 footnote marks.")).toBeTruthy();
  });

  it("has no warnings list or clean-up fold when there is nothing to say", async () => {
    await prepare();
    expect(screen.queryByRole("list", { name: "Things to know about this text" })).toBeNull();
    expect(screen.queryByText(/What the clean-up did/)).toBeNull();
  });

  it("lets the title of the reading be changed", async () => {
    await prepare();
    const title = screen.getByLabelText("Title of the reading") as HTMLInputElement;
    expect(title.value).toBe("A short book");
    fireEvent.change(title, { target: { value: "My own title" } });
    expect(title.value).toBe("My own title");
  });
});

// ------------------------------------------------------------------ preparing again
describe("preparing again", () => {
  it("says the switches have changed since the review was made, and Prepare again applies them", async () => {
    const calls = server(() => ({ body: BOOK }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    expect(screen.queryByText(/switches above have changed/)).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: /Read code and tables too/ }));
    expect(screen.getByText(/The switches above have changed since this was prepared\./)).toBeTruthy();
    press("Prepare again");
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(jsonBody(calls[1])).toMatchObject({ clean: true, read_extras: true });
    await waitFor(() => expect(screen.queryByText(/switches above have changed/)).toBeNull());
  });

  it("prepares again the same source it was made from: the web page is fetched again, the file is sent again", async () => {
    const calls = server(() => ({ body: BOOK }));
    show();
    fireEvent.click(screen.getByRole("radio", { name: "Web page" }));
    fireEvent.change(screen.getByLabelText("Address of the web page"), { target: { value: "https://example.org/a" } });
    press("Fetch");
    await screen.findByRole("heading", { name: "What will be read" });
    fireEvent.click(screen.getByRole("checkbox", { name: /Read code and tables too/ }));
    press("Prepare again");
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].url).toBe("/api/speech/prepare");
    expect(jsonBody(calls[1])).toEqual({ source: "url", url: "https://example.org/a", clean: true, read_extras: true });
    await waitFor(() => expect(screen.queryByText(/switches above have changed/)).toBeNull());
    cleanup();

    const fileCalls = server(() => ({ body: BOOK }));
    show();
    fireEvent.click(screen.getByRole("radio", { name: "File" }));
    const book = new File(["x"], "book.epub");
    fireEvent.change(screen.getByLabelText("File"), { target: { files: [book] } });
    await screen.findByRole("heading", { name: "What will be read" });
    fireEvent.click(screen.getByRole("checkbox", { name: /Clean up the text/ }));
    press("Prepare again");
    await waitFor(() => expect(fileCalls).toHaveLength(2));
    expect(fileCalls[1].url).toBe("/api/speech/prepare/file?name=book.epub&clean=false&read_extras=false");
    expect(fileCalls[1].init.body).toBe(book); // the same file again, from the page's own copy
  });

  it("changes nothing by itself when a switch is changed: the review stays as it is until asked", async () => {
    const calls = server(() => ({ body: BOOK }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    fireEvent.click(screen.getByRole("checkbox", { name: /Clean up the text/ }));
    expect(calls).toHaveLength(1);
    expect(parts()).toHaveLength(3);
  });

  it("asks before replacing a review the person has changed, and Keep leaves it and the request unsent", async () => {
    const calls = server(() => ({ body: BOOK }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    fireEvent.click(within(parts()[0]).getByRole("checkbox"));
    press("Prepare text");
    const question = screen.getByRole("group", { name: "Replace the review?" });
    expect(question.textContent).toContain("This replaces the changes you made to the review below.");
    expect(document.activeElement).toBe(within(question).getByRole("button", { name: "Keep my changes" }));
    fireEvent.click(within(question).getByRole("button", { name: "Keep my changes" }));
    expect(screen.queryByRole("group", { name: "Replace the review?" })).toBeNull();
    expect(calls).toHaveLength(1);
    expect((within(parts()[0]).getByRole("checkbox") as HTMLInputElement).checked).toBe(false); // still left out
  });

  it("replaces the review when the person says so", async () => {
    let n = 0;
    const calls = server(() => ({ body: n++ === 0 ? BOOK : prepared([part("Another", [["paragraph", "Other words."]])], { title: "Another text" }) }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    fireEvent.change(screen.getByLabelText("Title of the reading"), { target: { value: "Changed title" } });
    press("Prepare text");
    press("Replace the review");
    expect(screen.queryByRole("group", { name: "Replace the review?" })).toBeNull(); // the question is answered: it does not stay on the screen
    await waitFor(() => expect((screen.getByLabelText("Title of the reading") as HTMLInputElement).value).toBe("Another text"));
    expect(calls).toHaveLength(2);
    expect(parts()).toHaveLength(1);
    expect(screen.queryByRole("group", { name: "Replace the review?" })).toBeNull();
  });

  it("does not ask when nothing in the review was changed", async () => {
    const calls = server(() => ({ body: BOOK }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    press("Prepare text");
    expect(screen.queryByRole("group", { name: "Replace the review?" })).toBeNull();
    await waitFor(() => expect(calls).toHaveLength(2));
  });

  it("starts no second request while one is out: the box, the buttons, the file box and Prepare again are all off", async () => {
    const waiting: ((value: { body: unknown }) => void)[] = [];
    const calls = server(() => new Promise((resolve) => { waiting.push(resolve); }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await waitFor(() => expect(waiting).toHaveLength(1));
    fireEvent.click(screen.getByRole("radio", { name: "Web page" }));
    fireEvent.change(screen.getByLabelText("Address of the web page"), { target: { value: "https://example.org/a" } });
    expect((screen.getByRole("button", { name: "Fetch" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByLabelText("Address of the web page"), { key: "Enter" });
    fireEvent.click(screen.getByRole("radio", { name: "File" }));
    expect((screen.getByLabelText("File") as HTMLInputElement).disabled).toBe(true);
    const dropped = new File(["x"], "late.txt");
    fireEvent.drop(document.querySelector("[data-speech-drop]") as HTMLElement, { dataTransfer: { files: [dropped], types: ["Files"] } });
    expect(calls).toHaveLength(1);
    waiting[0]({ body: BOOK });
    await screen.findByRole("heading", { name: "What will be read" });
    fireEvent.click(screen.getByRole("checkbox", { name: /Clean up the text/ })); // a stale review offers Prepare again ...
    expect((screen.getByRole("button", { name: "Prepare again" }) as HTMLButtonElement).disabled).toBe(false);
    press("Prepare again");
    await waitFor(() => expect(waiting).toHaveLength(2));
    expect((screen.getByRole("button", { name: "Prepare again" }) as HTMLButtonElement).disabled).toBe(true); // ... and is off while it works
  });

  it("keeps the review it has when the next attempt fails, and says what went wrong", async () => {
    let n = 0;
    server(() => (n++ === 0 ? { body: BOOK } : { status: 422, body: { detail: "That address is on a private network.", code: "blocked_address" } }));
    show();
    typeText("Some words to read, enough to be a text.");
    press("Prepare text");
    await screen.findByRole("heading", { name: "What will be read" });
    fireEvent.click(screen.getByRole("radio", { name: "Web page" }));
    fireEvent.change(screen.getByLabelText("Address of the web page"), { target: { value: "http://192.168.1.5/" } });
    press("Fetch");
    expect((await screen.findByRole("alert")).textContent).toContain("That address is on a private network.");
    expect(parts()).toHaveLength(3); // the first review is still there
  });
});
