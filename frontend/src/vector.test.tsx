import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";
import { BinnedItemsCard } from "./components/BinnedItemsCard";
import { ConfirmCancel, ConfirmDelete } from "./components/Dialogs";
import { EmptyHistory, FilterBar } from "./components/FilterBar";
import { Tabs } from "./components/Tabs";
import type { ProjectControls } from "./components/ProjectMenu";
import { StackView } from "./components/StackView";
import { AlertIcon, StopIcon } from "./components/icons";
import { VectorCard } from "./components/VectorCard";
import { VectorPanel } from "./components/VectorPanel";
import { VectorViewer } from "./components/VectorViewer";
import { NO_FILTER, ONLY_DELETED, ONLY_KEPT, withProject } from "./history";
import { browserStore, type KeyValueStore } from "./options";
import { CAPS, STATUS, VECTOR_CONTRACT, makeBinnedVector, makeMusicRun, makeRun, makeVectorItem, makeVectorRun } from "./testdata";
import type { Capabilities, Project, VectorLimits, VectorRecipeRequest, VectorRun } from "./types";
import { BLEED_HINT, STACK_ORDER_HINT, STACK_ORDER_LABELS, VECTOR_FORM_KEY, defaultVectorForm, saveVectorForm } from "./vector";
import { useCopyVectorSvg } from "./useCopyVectorSvg";
import { workingNote } from "./format";

// The Vector tab on the page (DESIGN.md §31.12.8), in the browser stand-in: the form (its defaults and limits from the capabilities, the colours and their
// swatches, the layers line, what is greyed, the box "What the studio sends", the request, Reuse, what is remembered), the card (everything the fixture run
// has, the downloads, the buttons for each state), and the bits of the shared parts that know the third kind (tabs, filter bar, empty states, questions).

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  window.scrollTo = vi.fn() as never; // jsdom has none; Reuse scrolls to the form
});

const memoryStore = (initial: Record<string, string> = {}): KeyValueStore & { data: Record<string, string> } => {
  const data = { ...initial };
  return { available: true, data, get: (key) => data[key] ?? null, set: (key, value) => { data[key] = value; } };
};
const LOGO: Project = { id: "a".repeat(32), name: "Logo", created_at: "2026-10-02T10:00:00.000Z", counts: { image: 3, music: 1, vector: 2 } };
const controls = (): ProjectControls => ({ projects: [LOGO], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null) });
const nothing = () => undefined;

/** The words the studio would send for a request: made from it, so a test can tell which request they answer. */
const wordsFor = (body: VectorRecipeRequest) => `WORDS[${body.prompt}|${body.kind}|${body.colours}|${body.palette.join(",")}]`;
function fakeRecipe() {
  return vi.spyOn(api, "vectorRecipe").mockImplementation(async (body) => ({ positive: wordsFor(body), negative: "NEGATIVE-FIXED" }));
}

// ------------------------------------------------------------------ the card
function cardProps(run: VectorRun, over: Partial<ComponentProps<typeof VectorCard>> = {}): ComponentProps<typeof VectorCard> {
  return {
    run, now: Date.parse("2026-10-11T02:00:00.000Z"), workerState: "ready", transient: false, viewScope: "kept", projects: controls(), onOpenVersion: vi.fn(),
    onReuse: vi.fn(), onRetry: vi.fn(), onCancel: vi.fn(), onToggleKeep: vi.fn(), onRestore: vi.fn(), onDelete: vi.fn(), onCopySvg: vi.fn(), ...over,
  };
}
const card = () => document.querySelector("article.run-card") as HTMLElement;
const byAction = (action: string) => document.querySelector(`[data-action="${action}"]`) as HTMLElement | null;

describe("the card of a finished Vector run (the contract's run)", () => {
  const run = makeVectorRun({ id: "v-card" });
  const item = run.vectors[0];

  it("is an ordinary card of the history: a run-card with its run's id, a status, the prompt and the settings", () => {
    render(<VectorCard {...cardProps(run)} />);
    expect(card().className).toContain("run-card");
    expect(card().getAttribute("data-run-id")).toBe("v-card");
    expect(card().getAttribute("aria-label")).toBe("Done: vector, a fox head");
    expect(card().querySelector(".badge-done")?.textContent).toBe("Done");
    expect(card().querySelector(".run-prompt")?.textContent).toBe("a fox head");
    expect(card().querySelector(".run-meta")?.textContent).toBe("100 × 100 mm · 4 colours · flat colour art · layers by colour, side by side · seed 1234 · 8s");
    expect(card().textContent).toContain("Flat colour art"); // the kind, as a badge
  });

  it("shows the preview in an <img> and nowhere else: the page has no other place for the SVG", () => {
    const { container } = render(<VectorCard {...cardProps(run)} />);
    const images = Array.from(container.querySelectorAll("img"));
    expect(images).toHaveLength(1);
    expect(images[0].getAttribute("src")).toBe(item.preview_url);
    expect(images[0].getAttribute("alt")).toBe(""); // decorative: the button around it is named (below)
    // nothing that could run or show a file as part of the page, and no element but the <img> and the download links carries a link to a file
    expect(container.querySelectorAll("object, embed, iframe, script, [srcdoc]")).toHaveLength(0);
    expect(Array.from(container.querySelectorAll("[src]")).every((element) => element.tagName === "IMG")).toBe(true);
    expect(container.innerHTML).not.toContain("<svg xmlns"); // (the page's own icons are <svg> too, but none is the file's)
  });

  it("says the colours found against the colours asked, and the smallest feature against the limit", () => {
    render(<VectorCard {...cardProps(run)} />);
    expect(card().querySelector("[data-vector-colours]")?.textContent).toBe("4 colours found, 4 asked. White, the bare material, is one of them and has no layer.");
    expect(card().querySelector("[data-vector-feature]")?.textContent).toBe("Smallest feature: 0.62 mm (your limit is 0.5 mm).");
    expect(card().querySelector(".vector-summary")).toBeNull(); // the old heading of the version's block is gone: the card is one for the run
  });

  it("has one preview, a button that opens the viewer at that version, named for what it opens", () => {
    const props = cardProps(run);
    render(<VectorCard {...props} />);
    const thumb = screen.getByRole("button", { name: "Open the vector (seed 1234, 3 layers)" });
    expect(thumb.getAttribute("data-vector-id")).toBe(item.id);
    expect(thumb.querySelector(".vt-number")).toBeNull(); // a lone version has no place among others to number
    expect(thumb.querySelector(".vt-layers")?.textContent).toBe("3 layers");
    fireEvent.click(thumb);
    expect(props.onOpenVersion).toHaveBeenCalledWith(0);
  });

  it("reserves the sheet's own shape for the preview before it arrives, so the card does not jump", () => {
    const wide = makeVectorRun({ vectors: [makeVectorItem(0, { width_mm: 600, height_mm: 307 })] });
    render(<VectorCard {...cardProps(wide)} />);
    expect((card().querySelector("img") as HTMLImageElement).style.aspectRatio).toBe("600 / 307");
  });

  it("lists every layer: its swatch, name, paths and cut length, and the picture's own colour only where it differs", () => {
    render(<VectorCard {...cardProps(run)} />);
    const table = within(card()).getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Layer", "Paths", "Cut length"]);
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.getAttribute("data-layer-id"))).toEqual(["layer-1-black", "layer-2-red", "layer-3-green"]);
    expect(rows.map((row) => row.querySelector(".layer-name")?.textContent?.trim())).toEqual(["Layer 1 (black)", "Layer 2 (red)", "Layer 3 (green)"]);
    expect(rows.map((row) => Array.from(row.querySelectorAll("td")).map((cell) => cell.textContent))).toEqual([["7", "412.6 mm"], ["3", "188.2 mm"], ["2", "96.4 mm"]]);
    // the swatch is the layer's own colour (the file's), exactly
    const swatchColours = rows.map((row) => (row.querySelector(".layer-name .swatch") as HTMLElement).style.background);
    expect(swatchColours).toEqual(["rgb(0, 0, 0)", "rgb(255, 0, 0)", "rgb(0, 170, 0)"]);
    // black differs from what the picture had (#101214); red and green do not
    expect(rows[0].querySelector(".layer-colour")?.textContent).toBe("#000000 · in the picture  #101214");
    expect(rows[1].querySelector(".layer-colour")?.textContent).toBe("#FF0000");
    expect(rows[2].querySelector(".layer-colour")?.textContent).toBe("#00AA00");
  });

  it("makes the table reachable by keyboard (it scrolls inside its own box on a phone) and names it", () => {
    render(<VectorCard {...cardProps(run)} />);
    const region = within(card()).getByRole("region", { name: "Layers" });
    expect(region.getAttribute("tabindex")).toBe("0");
  });

  it("shows every note the check has, each with its code", () => {
    const notes = [
      { code: "specks_removed", message: "5 specks smaller than 0.5 mm were removed." },
      { code: "fewer_colours", message: "The picture has only 3 colours, so there are 2 layers, not 3." },
      { code: "hanging_island", message: "Layer 1 has a piece that would fall out of a stencil." },
      { code: "coarse_drawing", message: "The drawing is too coarse to show 0.05 mm." },
    ];
    const noted = makeVectorRun({ vectors: [makeVectorItem(0, { check: { ...item.check, notes } })] });
    render(<VectorCard {...cardProps(noted)} />);
    const list = within(card()).getByRole("list", { name: "Notes" });
    const shown = Array.from(list.querySelectorAll("li"));
    expect(shown.map((li) => li.getAttribute("data-note-code"))).toEqual(notes.map((note) => note.code));
    expect(shown.map((li) => li.textContent?.trim())).toEqual(notes.map((note) => note.message));
  });

  it("lists a single note as a list of one, and a single layer as \"1 layer\"", () => {
    render(<VectorCard {...cardProps(run)} />);
    const list = within(card()).getByRole("list", { name: "Notes" });
    expect(Array.from(list.querySelectorAll("li")).map((li) => li.textContent?.trim())).toEqual(["5 specks smaller than 0.5 mm were removed."]);
    cleanup();
    const one = makeVectorRun({ vectors: [makeVectorItem(0, { layers: 1, check: { ...item.check, layers: [item.check.layers[0]] } })] });
    render(<VectorCard {...cardProps(one)} />);
    expect(card().querySelector(".vt-layers")?.textContent).toBe("1 layer");
    expect(screen.getByRole("button", { name: "Open the vector (seed 1234, 1 layer)" })).toBeTruthy();
  });

  it("has no list of notes when there are none", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ vectors: [makeVectorItem(0, { check: { ...item.check, notes: [] } })] }))} />);
    expect(within(card()).queryByRole("list", { name: "Notes" })).toBeNull();
  });

  it("says no smallest feature was found, and shows no table when there are no layers", () => {
    const empty = makeVectorRun({ vectors: [makeVectorItem(0, { check: { ...item.check, smallest_feature_mm: null, layers: [] } })] });
    render(<VectorCard {...cardProps(empty)} />);
    expect(card().querySelector("[data-vector-feature]")?.textContent).toBe("Smallest feature: none found (your limit is 0.5 mm).");
    expect(within(card()).queryByRole("table")).toBeNull();
  });

  it("offers the three downloads and Copy SVG code on a run with one version, with the server's own links, and no PNG box: that is the viewer's", () => {
    const props = cardProps(run);
    render(<VectorCard {...props} />);
    const svg = byAction("download-svg") as HTMLAnchorElement;
    expect(svg.textContent).toContain("Download SVG");
    expect(svg.getAttribute("href")).toBe(item.download_url);
    expect(svg.hasAttribute("download")).toBe(true);
    const zip = byAction("download-zip") as HTMLAnchorElement;
    expect(zip.textContent).toContain("Download layers (ZIP)");
    expect(zip.getAttribute("href")).toBe(item.zip_url);
    fireEvent.click(screen.getByRole("button", { name: "Copy SVG code" }));
    expect(props.onCopySvg).toHaveBeenCalledWith(item);
    expect(byAction("download-png")).toBeNull();
    expect(screen.queryByLabelText(/PNG size/)).toBeNull();
    expect(byAction("download-more")).toBeNull(); // Download… is for a run with several versions
  });

  it("has Keep, Reuse, Project and Delete on a finished run, and no Cancel, Retry, Restore, Make 4K or Delete picture", () => {
    render(<VectorCard {...cardProps(run)} />);
    for (const action of ["keep", "reuse", "project", "delete"]) expect(byAction(action), action).not.toBeNull();
    for (const action of ["cancel", "restore", "make-4k", "enlarge", "delete-picture", "edit-this", "regenerate-larger", "delete-version"]) expect(byAction(action), action).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(card().querySelectorAll("button.vector-thumb")).toHaveLength(1); // the preview is a button now, as on an Images card
  });

  it("says what Delete takes: the run alone, or the run and all its versions", () => {
    render(<VectorCard {...cardProps(run)} />);
    expect(byAction("delete")?.getAttribute("title")).toBe("Delete this run and its files");
    cleanup();
    const three = makeVectorRun({ options: { ...run.options, num_images: 3 }, vectors: [makeVectorItem(0), makeVectorItem(1), makeVectorItem(2)] });
    render(<VectorCard {...cardProps(three)} />);
    expect(byAction("delete")?.getAttribute("title")).toBe("Delete this run and all 3 versions");
    expect(byAction("delete")?.textContent).toContain("Delete"); // the label is the same as ever
  });

  it("hands each button's press to the page", () => {
    const props = cardProps(run);
    render(<VectorCard {...props} />);
    fireEvent.click(byAction("reuse") as HTMLElement);
    fireEvent.click(byAction("keep") as HTMLElement);
    fireEvent.click(byAction("delete") as HTMLElement);
    expect([props.onReuse, props.onToggleKeep, props.onDelete].map((spy) => (spy as ReturnType<typeof vi.fn>).mock.calls.length)).toEqual([1, 1, 1]);
  });

  it("shows the Kept badge only on a kept run", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ pinned: false }))} />);
    expect(card().querySelector(".badge-kept")).toBeNull();
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun({ pinned: true }))} />);
    expect(card().querySelector(".badge-kept")?.textContent).toContain("Kept");
  });

  it("shows a project chip even before the page knows the project's name", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ project_id: "c".repeat(32), pinned: true }))} />);
    expect(card().querySelector("[data-project-chip]")?.textContent).toContain("Project");
    expect(screen.getByRole("button", { name: "Project: a project. Change the project" })).toBeTruthy();
  });

  it("shows Keep pressed on a kept run, and locked, with the reason, on a filed one", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ pinned: true }))} />);
    expect(byAction("keep")?.getAttribute("aria-pressed")).toBe("true");
    cleanup();
    const props = cardProps(makeVectorRun({ pinned: true, project_id: LOGO.id }));
    render(<VectorCard {...props} />);
    expect(byAction("keep")?.getAttribute("aria-disabled")).toBe("true");
    expect(byAction("keep")?.getAttribute("title")).toBe("Kept because it is in the project “Logo”. Take it out of the project first.");
    fireEvent.click(byAction("keep") as HTMLElement);
    expect(props.onToggleKeep).not.toHaveBeenCalled();
    expect(card().querySelector("[data-project-chip]")?.textContent).toContain("Logo");
    expect(screen.getByRole("button", { name: "Project: Logo. Change the project" })).toBeTruthy();
  });

  it("says when the run will be deleted, unless it is kept", () => {
    const soon = makeVectorRun({ expires_at: "2026-10-13T02:00:00.000Z" });
    render(<VectorCard {...cardProps(soon)} />);
    expect(card().querySelector(".run-expiry")?.textContent).toContain("Will be deleted in 2 days");
    cleanup();
    render(<VectorCard {...cardProps({ ...soon, pinned: true })} />);
    expect(card().querySelector(".run-expiry")).toBeNull();
  });

  it("renders without any browser-only code (the markup is the same on the server)", () => {
    const html = renderToStaticMarkup(<VectorCard {...cardProps(run)} />);
    expect(html).toContain("vector-card");
    expect(html).toContain(item.preview_url);
  });
});

describe("a Vector run with several versions (DESIGN.md §35.2)", () => {
  const options = { ...makeVectorRun().options, num_images: 3, seed: 100 };
  const run = makeVectorRun({ options, vectors: [makeVectorItem(0, { seed: 100 }), makeVectorItem(2, { seed: 102, id: "second0000000000000000000000000000" })] });

  it("is one compact card: a grid of previews, and none of the blocks, tables and buttons that each version used to have", () => {
    render(<VectorCard {...cardProps(run)} />);
    expect(card().querySelector(".vector-media")?.className).toContain("grid-2");
    expect(Array.from(card().querySelectorAll("button.vector-thumb img")).map((img) => img.getAttribute("src"))).toEqual([run.vectors[0].preview_url, run.vectors[1].preview_url]);
    expect(card().querySelectorAll(".vector-item, .vector-lone, table, [data-vector-colours], [data-vector-feature]")).toHaveLength(0);
    for (const action of ["download-svg", "download-zip", "copy-svg", "download-png"]) expect(card().querySelectorAll(`[data-action="${action}"]`), action).toHaveLength(0);
    expect(card().querySelector(".run-meta")?.textContent).toContain("seeds 100–102");
  });

  it("names each preview by its place among the versions the run has now, and shows that place in its corner", () => {
    render(<VectorCard {...cardProps(run)} />);
    const thumbs = Array.from(card().querySelectorAll("button.vector-thumb"));
    // the run was asked for 3 and has 2: a version that was deleted leaves no gap, the others are numbered again by position and keep their seeds
    expect(thumbs.map((b) => b.getAttribute("aria-label"))).toEqual(["Open version 1 of 2 (seed 100, 3 layers)", "Open version 2 of 2 (seed 102, 3 layers)"]);
    expect(thumbs.map((b) => b.querySelector(".vt-number")?.textContent)).toEqual(["1", "2"]);
    expect(thumbs.map((b) => b.querySelector(".vt-layers")?.textContent)).toEqual(["3 layers", "3 layers"]);
  });

  it("opens the viewer at the version that is pressed", () => {
    const props = cardProps(run);
    render(<VectorCard {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Open version 2 of 2 (seed 102, 3 layers)" }));
    expect(props.onOpenVersion).toHaveBeenCalledWith(1);
  });

  it("has Download…, which opens the viewer at the first version, where each version's downloads are", () => {
    const props = cardProps(run);
    render(<VectorCard {...props} />);
    const more = byAction("download-more") as HTMLElement;
    expect(more.textContent).toContain("Download…");
    expect(more.getAttribute("title")).toBe("Open the first of the 2 versions: each one's downloads are there");
    fireEvent.click(more);
    expect(props.onOpenVersion).toHaveBeenCalledWith(0);
  });

  it("shows four previews at most and counts the rest on the fourth", () => {
    const six = makeVectorRun({ options: { ...options, num_images: 6 }, vectors: [0, 1, 2, 3, 4, 5].map((i) => makeVectorItem(i, { id: `six${i}`.padEnd(32, "0") })) });
    render(<VectorCard {...cardProps(six)} />);
    expect(card().querySelectorAll("button.vector-thumb")).toHaveLength(4);
    expect(card().querySelector(".vector-media")?.className).toContain("grid-4");
    expect(card().querySelectorAll(".more")).toHaveLength(1);
    expect(card().querySelector(".more")?.textContent).toBe("+2");
  });

  it("hides the number and the layer count in a preview's corner from a screen reader (the button around them is named), and counts the rest only past four", () => {
    render(<VectorCard {...cardProps(run)} />);
    for (const corner of Array.from(card().querySelectorAll(".vt-number, .vt-layers"))) expect(corner.getAttribute("aria-hidden"), corner.className).toBe("true");
    cleanup();
    const four = makeVectorRun({ options: { ...options, num_images: 4 }, vectors: [0, 1, 2, 3].map((i) => makeVectorItem(i, { id: `four${i}`.padEnd(32, "0") })) });
    render(<VectorCard {...cardProps(four)} />);
    expect(card().querySelectorAll("button.vector-thumb")).toHaveLength(4);
    expect(card().querySelectorAll(".more")).toHaveLength(0); // all four are shown: nothing is left to count
  });

  it("shows a version in the bin only in the Deleted view: the run's own card holds the ones that are not in the bin", () => {
    const withBinned = { ...run, binned_vectors: [makeBinnedVector(1, { seed: 101 })] };
    render(<VectorCard {...cardProps(withBinned)} />);
    expect(card().querySelectorAll("button.vector-thumb")).toHaveLength(2);
    expect(card().textContent).not.toContain("seed 101");
  });
});

describe("the empty preview of a run with no version yet (DESIGN.md §35.2)", () => {
  const media = () => card().querySelector(".run-media.empty") as HTMLElement;

  it("is hidden from a screen reader, and shows a shimmer while the run waits or works, an alert icon once it failed, and a stop icon once it was canceled", () => {
    for (const status of ["queued", "running"] as const) {
      render(<VectorCard {...cardProps(makeVectorRun({ status, vectors: [] }))} />);
      expect(media().getAttribute("aria-hidden")).toBe("true");
      expect(media().querySelector(".shimmer")).not.toBeNull();
      expect(media().querySelector("svg")).toBeNull();
      cleanup();
    }
    const html = (node: React.ReactElement) => renderToStaticMarkup(node);
    render(<VectorCard {...cardProps(makeVectorRun({ status: "failed", vectors: [], error: { message: "x", hint: null } }))} />);
    expect(media().querySelector(".shimmer")).toBeNull();
    expect(media().innerHTML).toBe(html(<AlertIcon />));
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun({ status: "canceled", vectors: [] }))} />);
    expect(media().querySelector(".shimmer")).toBeNull();
    expect(media().innerHTML).toBe(html(<StopIcon />));
    expect(html(<AlertIcon />)).not.toBe(html(<StopIcon />)); // (so that the two above say different things)
  });
});

describe("a Vector run of exactly two versions", () => {
  it("has Download… already (two are several), and says the seeds of both", () => {
    const options = { ...makeVectorRun().options, num_images: 2 };
    const two = makeVectorRun({ options, vectors: [makeVectorItem(0), makeVectorItem(1, { id: "other00000000000000000000000000" })] });
    render(<VectorCard {...cardProps(two)} />);
    expect(byAction("download-more")).not.toBeNull();
    expect(card().querySelector(".run-meta")?.textContent).toContain("seeds 1234–1235");
  });
});

describe("the card of a Vector run that is not finished or not a success", () => {
  it("shows the two stages while it works: drawing by its steps, tracing as it goes, and what it is doing", () => {
    const working = makeVectorRun({ status: "running", vectors: [], progress: { image: 1, of: 1, step: 10, steps: 40, stage: "draw" } });
    render(<VectorCard {...cardProps(working)} />);
    const phases = Array.from(card().querySelectorAll("[data-phase]"));
    expect(phases.map((phase) => phase.getAttribute("data-phase"))).toEqual(["draw", "trace"]);
    expect(phases.map((phase) => phase.querySelector(".phase-label")?.textContent)).toEqual(["Drawing", "Tracing"]);
    expect(screen.getByRole("progressbar", { name: "Drawing progress" }).getAttribute("aria-valuenow")).toBe("25");
    expect(screen.getByRole("progressbar", { name: "Tracing progress" }).getAttribute("aria-valuenow")).toBe("0");
    for (const bar of screen.getAllByRole("progressbar")) expect([bar.getAttribute("aria-valuemin"), bar.getAttribute("aria-valuemax")]).toEqual(["0", "100"]);
    expect(card().querySelector(".progress p")?.textContent).toBe("drawing, step 10 of 40");
    expect(card().querySelector(".badge-running")?.textContent).toBe("Making vector");
    expect(byAction("cancel")).not.toBeNull();
    expect((byAction("delete") as HTMLButtonElement).disabled).toBe(true);
  });

  it("while tracing, drawing is full and tracing has a bar that moves, not one that fills", () => {
    const tracing = makeVectorRun({ status: "running", vectors: [], progress: { image: 2, of: 3, step: 1, steps: 1, stage: "trace" } });
    render(<VectorCard {...cardProps(tracing)} />);
    expect(screen.getByRole("progressbar", { name: "Drawing progress" }).getAttribute("aria-valuenow")).toBe("100");
    expect(screen.queryByRole("progressbar", { name: "Tracing progress" })).toBeNull(); // no value to report: it is not a progressbar
    expect(card().querySelector('[data-phase="trace"] .progress-track')?.className).toContain("indeterminate");
    expect(card().querySelector(".progress p")?.textContent).toBe("Version 2 of 3 · tracing the drawing into layers…");
  });

  it("says it is starting, or that the model is loading, before the first step", () => {
    const starting = makeVectorRun({ status: "running", vectors: [], progress: null });
    render(<VectorCard {...cardProps(starting)} />);
    expect(card().querySelector(".progress p")?.textContent).toBe("Starting…");
    cleanup();
    render(<VectorCard {...cardProps(starting, { workerState: "loading" })} />);
    expect(card().querySelector(".progress p")?.textContent).toBe("Loading the model… (the first load takes a while)");
    cleanup();
    render(<VectorCard {...cardProps({ ...starting, canceling: true })} />);
    expect(card().querySelector(".progress p")?.textContent).toContain("Stopping…");
    expect(byAction("cancel")?.getAttribute("aria-disabled")).toBe("true");
  });

  it("names each state in the badge: waiting (with or without a place), making, stopping, done, failed, canceled", () => {
    const label = (patch: Partial<VectorRun>) => {
      render(<VectorCard {...cardProps(makeVectorRun({ vectors: [], ...patch }))} />);
      const text = card().querySelector(".run-head .badge:not(.badge-kept):not(.badge-project):not(.badge-deleted)")?.textContent;
      cleanup();
      return text;
    };
    expect(label({ status: "queued", queue_position: null })).toBe("Queued");
    expect(label({ status: "queued", queue_position: 3 })).toBe("Queued · #3");
    expect(label({ status: "running" })).toBe("Making vector");
    expect(label({ status: "running", canceling: true })).toBe("Stopping");
    expect(label({ status: "done" })).toBe("Done");
    expect(label({ status: "failed", error: { message: "x", hint: null } })).toBe("Failed");
    expect(label({ status: "canceled" })).toBe("Canceled");
  });

  it("announces its progress to a screen reader (a status region), before and after the first step", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ status: "running", vectors: [], progress: null }))} />);
    expect(card().querySelector(".progress")?.getAttribute("role")).toBe("status");
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun({ status: "running", vectors: [], progress: { image: 1, of: 1, step: 1, steps: 40, stage: "draw" } }))} />);
    expect(card().querySelector(".progress")?.getAttribute("role")).toBe("status");
  });

  it("shows a queued run with its place, and Cancel", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ status: "queued", vectors: [], queue_position: 2 }))} />);
    expect(card().querySelector(".badge-queued")?.textContent).toBe("Queued · #2");
    expect(byAction("cancel")).not.toBeNull();
  });

  it("shows a failed run's error with its hint, and Retry, which hands the run to the page", () => {
    const failed = makeVectorRun({ status: "failed", vectors: [], error: { message: "The clean-up stopped unexpectedly.", hint: "Try a smaller sheet." } });
    const props = cardProps(failed);
    render(<VectorCard {...props} />);
    expect(screen.getByRole("alert").textContent).toContain("The clean-up stopped unexpectedly.");
    expect(screen.getByRole("alert").textContent).toContain("Try a smaller sheet.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
  });

  it("keeps the text that says why a version is missing on a run that is done (a run with one good version is done)", () => {
    const partial = makeVectorRun({ status: "done", error: { message: "Version 2 could not be made: nothing was left after the smallest feature.", hint: "Make the smallest feature smaller." } });
    render(<VectorCard {...cardProps(partial)} />);
    const note = card().querySelector('[data-note="partial"]') as HTMLElement;
    expect(note.textContent).toContain("Version 2 could not be made");
    expect(note.textContent).toContain("Make the smallest feature smaller.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull(); // the run is done: there is nothing to retry
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun())} />);
    expect(card().querySelector('[data-note="partial"]')).toBeNull();
  });

  it("says what a canceled run kept", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ status: "canceled", options: { ...makeVectorRun().options, num_images: 3 } }))} />);
    expect(card().querySelector(".run-note")?.textContent).toBe("Canceled. 1 of 3 versions finished and kept.");
  });

  it("says what keeps a card in a filtered view while it works", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ status: "running", vectors: [] }), { transient: true, viewScope: "project" })} />);
    expect(card().querySelector('[data-note="working-in-project"]')?.textContent).toBe(workingNote("project"));
  });

  it("cuts a prompt only when it is longer than 240 characters", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ prompt: "x".repeat(240) }))} />);
    expect(card().querySelector(".run-prompt")?.className).not.toContain("clamped");
    expect(screen.queryByRole("button", { name: "Show full prompt" })).toBeNull();
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun({ prompt: "x".repeat(241) }))} />);
    expect(card().querySelector(".run-prompt")?.className).toContain("clamped");
    expect(screen.getByRole("button", { name: "Show full prompt" })).toBeTruthy();
  });

  it("has no list of versions and no hint when a run that has made nothing has no hint to give", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ status: "running", vectors: [], progress: null }))} />);
    expect(within(card()).queryByRole("list", { name: "Versions" })).toBeNull();
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun({ status: "failed", vectors: [], error: { message: "It failed.", hint: null } }))} />);
    expect(card().querySelector(".run-error .hint")).toBeNull();
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun({ status: "done", error: { message: "Version 2 failed.", hint: null } }))} />);
    expect(card().querySelector('[data-note="partial"] .hint')).toBeNull();
  });

  it("says in the Cancel button's tooltip what it will do: leave the queue, or stop after the step, or that it is stopping", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ status: "queued", vectors: [] }))} />);
    expect(byAction("cancel")?.getAttribute("title")).toBe("Take this job out of the queue");
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun({ status: "running", vectors: [] }))} />);
    expect(byAction("cancel")?.getAttribute("title")).toBe("Stop making this vector (finished versions are kept)");
    cleanup();
    render(<VectorCard {...cardProps(makeVectorRun({ status: "running", vectors: [], canceling: true }))} />);
    expect(byAction("cancel")?.getAttribute("title")).toBe("Stopping after the current step");
  });

  it("shows a long prompt cut short, and all of it on request", () => {
    render(<VectorCard {...cardProps(makeVectorRun({ prompt: "a very long idea ".repeat(30) }))} />);
    expect(card().querySelector(".run-prompt")?.className).toContain("clamped");
    fireEvent.click(screen.getByRole("button", { name: "Show full prompt" }));
    expect(card().querySelector(".run-prompt")?.className).not.toContain("clamped");
  });
});

describe("the card of a Vector run in the bin (DESIGN.md §30.1)", () => {
  const binned = makeVectorRun({ deleted_at: "2026-10-10T02:00:00.000Z", purge_at: "2026-11-09T02:00:00.000Z" });

  it("offers Restore, Reuse and Delete forever, says since when, and has nothing that changes the run", () => {
    const props = cardProps(binned);
    render(<VectorCard {...props} />);
    expect(card().querySelector(".badge-deleted")?.textContent?.trim()).toBe("Deleted");
    expect(card().querySelector("[data-note=in-bin]")?.textContent).toMatch(/^In the bin since .*\. It will be deleted for good around .*, in 29 days\.$/);
    for (const action of ["restore", "reuse", "delete"]) expect(byAction(action), action).not.toBeNull();
    expect(byAction("delete")?.textContent).toContain("Delete forever");
    for (const action of ["keep", "cancel", "project"]) expect(byAction(action), action).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    fireEvent.click(byAction("restore") as HTMLElement);
    expect(props.onRestore).toHaveBeenCalledTimes(1);
  });

  it("still shows the preview and the downloads (they only read the run), and the project it is filed in", () => {
    render(<VectorCard {...cardProps({ ...binned, project_id: LOGO.id, pinned: true })} />);
    expect(card().querySelector("img")).not.toBeNull();
    expect(byAction("download-svg")).not.toBeNull(); // (a run with one version)
    expect(card().querySelector("[data-project-chip]")?.textContent).toContain("Logo");
    expect(screen.queryByRole("button", { name: /Project:/ })).toBeNull(); // it cannot be pressed in the bin
  });

  it("does not offer Retry on a failed run in the bin", () => {
    render(<VectorCard {...cardProps({ ...binned, status: "failed", vectors: [], error: { message: "It failed.", hint: null } })} />);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});

// ------------------------------------------------------------------ the form
function panelProps(over: Partial<ComponentProps<typeof VectorPanel>> = {}): ComponentProps<typeof VectorPanel> {
  return {
    hidden: false, caps: CAPS, status: STATUS, store: memoryStore(), runs: [], runsReady: true, filterBar: <div data-testid="filter-bar" />, loadProblem: null,
    empty: <p>Nothing here yet.</p>, isTransient: () => false, viewScope: "kept", projects: controls(), inDeletedView: false,
    binnedHandlers: { onOpen: vi.fn(), onRestore: vi.fn(), onDeleteForever: vi.fn() }, more: false, loadingOlder: false, now: Date.now(), push: vi.fn(),
    onRun: vi.fn(), onLoadOlder: vi.fn(), onOpenVersion: vi.fn(), onCopySvg: vi.fn(), onCancel: vi.fn(), onToggleKeep: vi.fn(), onRestore: vi.fn(), onDelete: vi.fn(), ...over,
  };
}
const form = () => screen.getByRole("region", { name: "New vector" });
const idea = () => within(form()).getByLabelText("Prompt") as HTMLTextAreaElement;
const recipeBox = () => within(form()).getByLabelText("What the studio sends") as HTMLTextAreaElement;
const negativeBox = () => within(form()).getByLabelText("What the studio keeps out (fixed)") as HTMLTextAreaElement;
const make = () => within(form()).getByRole("button", { name: "Make vector" }) as HTMLButtonElement;
const coloursValue = () => form().querySelector("[data-vector-colours-value]")?.textContent;
const layersLineText = () => form().querySelector("[data-vector-layers-line]")?.textContent;
const radio = (group: string, name: string) => within(within(form()).getByRole("radiogroup", { name: group })).getByRole("radio", { name }) as HTMLButtonElement;
const num = (label: string) => within(form()).getByLabelText(label) as HTMLInputElement;
const type = (box: HTMLElement, text: string) => fireEvent.change(box, { target: { value: text } });
/** Wait for the recipe box to show the words for `shown` (the answer of the fake route). */
const recipeShows = (shown: string) => waitFor(() => expect(recipeBox().value).toBe(shown));

describe("the Vector form's start", () => {
  it("shows the defaults of the capabilities: two colours, white and one ink, 100 × 100 mm, one version, no prompt", () => {
    render(<VectorPanel {...panelProps()} />);
    expect(idea().value).toBe("");
    expect(coloursValue()).toBe("2");
    expect(layersLineText()).toBe("1 layer. White, the bare material, gets none.");
    expect(num("Width (mm)").value).toBe("100");
    expect(num("Height (mm)").value).toBe("100");
    expect(radio("Kind of artwork", "Silhouette or stencil").getAttribute("aria-checked")).toBe("true");
    expect(radio("Layers by", "Colour").getAttribute("aria-checked")).toBe("true");
    expect(radio("Arrangement", "Side by side").getAttribute("aria-checked")).toBe("true");
    expect(radio("Versions", "1").getAttribute("aria-checked")).toBe("true");
    expect((num("Seed")).disabled).toBe(true); // the seed is random until it is locked
    expect(make().disabled).toBe(true); // nothing to make yet
  });

  it("takes its defaults, its choices and its presets from the capabilities it is given, not from numbers of its own", () => {
    const limits: VectorLimits = {
      ...CAPS.limits.vector, colours: { min: 3, max: 6, default: 4 }, size_mm: { min: 10, max: 900, default: [250, 120] }, versions: { min: 2, max: 3 },
      kinds: ["flat", "line"], layers_by: ["shape", "colour"], arrangements: ["stacked", "side_by_side"], defaults: { kind: "line", layers_by: "shape", arrangement: "stacked" },
      presets: [{ id: "bench", name: "Bench", width_mm: 400, height_mm: 250 }], shape_layer_cap: 12,
    };
    const caps: Capabilities = { ...CAPS, limits: { ...CAPS.limits, vector: limits } };
    render(<VectorPanel {...panelProps({ caps })} />);
    expect(coloursValue()).toBe("4");
    expect(num("Width (mm)").value).toBe("250");
    expect(num("Height (mm)").value).toBe("120");
    expect(within(within(form()).getByRole("radiogroup", { name: "Kind of artwork" })).getAllByRole("radio").map((r) => r.textContent)).toEqual(["Flat colour art", "Line art"]);
    expect(radio("Kind of artwork", "Line art").getAttribute("aria-checked")).toBe("true");
    expect(radio("Layers by", "Shape").getAttribute("aria-checked")).toBe("true");
    expect(layersLineText()).toBe("Up to 12 pieces: one layer for each separate piece. White, the bare material, gets none.");
    expect(within(within(form()).getByRole("radiogroup", { name: "Versions" })).getAllByRole("radio").map((r) => r.textContent)).toEqual(["2", "3"]);
    expect(radio("Versions", "2").getAttribute("aria-checked")).toBe("true");
    expect(within(form()).getAllByRole("button", { name: /Bench/ })).toHaveLength(1);
    expect(within(form()).queryByRole("button", { name: /xTool/ })).toBeNull();
    // the stepper stops at the server's ends, not at 2 and 8
    expect(within(form()).getByRole("button", { name: "More colours" })).toHaveProperty("disabled", false);
  });

  it("says in a line what each kind of artwork is, under the choice", () => {
    render(<VectorPanel {...panelProps()} />);
    const hint = () => within(form()).getByRole("radiogroup", { name: "Kind of artwork" }).parentElement?.querySelector(".field-hint")?.textContent;
    expect(hint()).toBe("One bold shape against nothing.");
    fireEvent.click(radio("Kind of artwork", "Flat colour art"));
    expect(hint()).toBe("Flat areas of solid colour.");
    fireEvent.click(radio("Kind of artwork", "Line art"));
    expect(hint()).toBe("Outlines only.");
  });

  it("explains itself and the model is the image model's (no model note: a Vector run loads nothing an image run would not)", () => {
    render(<VectorPanel {...panelProps()} />);
    expect(form().textContent).toContain("An SVG a laser can use");
    expect(form().textContent).not.toContain("music model");
  });
});

describe("Colours, white and the swatches", () => {
  it("steps from 2 to 8, says what the number means, and stops at each end", () => {
    render(<VectorPanel {...panelProps()} />);
    const fewer = within(form()).getByRole("button", { name: "Fewer colours" }) as HTMLButtonElement;
    const more = within(form()).getByRole("button", { name: "More colours" }) as HTMLButtonElement;
    expect(fewer.disabled).toBe(true); // 2 is the least
    expect(form().textContent).toContain("2 colours: white and 1 ink.");
    for (let n = 3; n <= 8; n++) {
      fireEvent.click(more);
      expect(coloursValue()).toBe(String(n));
    }
    expect(more.disabled).toBe(true); // 8 is the most
    expect(form().textContent).toContain("8 colours: white and 7 inks.");
    fireEvent.click(fewer);
    expect(coloursValue()).toBe("7");
    expect(more.disabled).toBe(false);
  });

  it("always shows white as the bare material with no layer, and one row for each ink, the picture choosing them until one is picked", () => {
    render(<VectorPanel {...panelProps()} />);
    const inks = within(form()).getByRole("list", { name: "Colours of the layers" });
    const white = inks.querySelector('[data-ink="white"]') as HTMLElement;
    expect(white.textContent).toBe("White, the bare material, no layer");
    expect(white.querySelector("input")).toBeNull(); // fixed: no colour input for white
    expect(inks.querySelectorAll("li")).toHaveLength(2); // white and one ink
    expect(inks.querySelector('[data-ink="1"]')?.textContent).toContain("Let the picture choose");
    fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    expect(inks.querySelectorAll("li")).toHaveLength(4); // white and three inks
    expect(inks.querySelectorAll('input[type="color"]')).toHaveLength(0); // none chosen yet
  });

  it("says how many layers it will have, live: colours less white, or up to the cap by shape", () => {
    render(<VectorPanel {...panelProps()} />);
    expect(layersLineText()).toBe("1 layer. White, the bare material, gets none.");
    fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    expect(layersLineText()).toBe("Up to 2 layers, one for each ink. White, the bare material, gets none.");
    fireEvent.click(radio("Layers by", "Shape"));
    expect(layersLineText()).toBe("Up to 30 pieces: one layer for each separate piece. White, the bare material, gets none.");
    fireEvent.click(radio("Layers by", "Colour"));
    expect(layersLineText()).toBe("Up to 2 layers, one for each ink. White, the bare material, gets none.");
  });

  it("picks a colour for an ink on request, and the colour input shows it in the form the browser wants", () => {
    render(<VectorPanel {...panelProps()} />);
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 1" }));
    const input = within(form()).getByLabelText("Ink 1 colour") as HTMLInputElement;
    expect(input.type).toBe("color");
    expect(input.value).toBe("#000000");
    expect(within(form()).getByRole("list", { name: "Colours of the layers" }).querySelector("code")?.textContent).toBe("#000000");
    type(input, "#ff8800");
    expect((within(form()).getByLabelText("Ink 1 colour") as HTMLInputElement).value).toBe("#ff8800");
    expect(form().querySelector('[data-ink="1"] code')?.textContent).toBe("#FF8800"); // kept in capitals, as the server wants it
  });

  it("never lets white become an ink: the colour input answering white is refused, with the reason, and the ink keeps its colour", () => {
    render(<VectorPanel {...panelProps()} />);
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 1" }));
    const input = within(form()).getByLabelText("Ink 1 colour") as HTMLInputElement;
    type(input, "#ff0000");
    type(input, "#ffffff");
    expect(screen.getByText("White is the bare material and has no layer. Pick another colour.")).toBeTruthy();
    expect((within(form()).getByLabelText("Ink 1 colour") as HTMLInputElement).value).toBe("#ff0000");
    // ... and the saved form has no white in it either
    type(within(form()).getByLabelText("Ink 1 colour"), "#00aa00"); // a good colour clears the note
    expect(screen.queryByText(/White is the bare material and has no layer\. Pick another/)).toBeNull();
  });

  it("has no message about a refused colour until one is refused", () => {
    render(<VectorPanel {...panelProps()} />);
    expect(form().querySelector('.field-error[role="status"]')).toBeNull();
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 1" }));
    type(within(form()).getByLabelText("Ink 1 colour"), "#ffffff");
    expect(form().querySelector('.field-error[role="status"]')).not.toBeNull();
  });

  it("refuses a colour another ink has", () => {
    render(<VectorPanel {...panelProps()} />);
    fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 1" }));
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 2" }));
    const [first, second] = [1, 2].map((n) => within(form()).getByLabelText(`Ink ${n} colour`) as HTMLInputElement);
    type(second, first.value);
    expect(screen.getByText(/is already one of the inks\. Pick another colour\./)).toBeTruthy();
    expect((within(form()).getByLabelText("Ink 2 colour") as HTMLInputElement).value).not.toBe(first.value);
  });

  it("offers the next colour only on the first ink the picture still chooses, and lets one go back to the picture", () => {
    render(<VectorPanel {...panelProps()} />);
    for (let i = 0; i < 2; i++) fireEvent.click(within(form()).getByRole("button", { name: "More colours" })); // 4 colours: three inks
    expect(within(form()).getAllByRole("button", { name: /^Choose a colour for ink/ })).toHaveLength(1);
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 1" }));
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 2" }));
    expect(within(form()).getAllByRole("button", { name: /^Choose a colour for ink/ }).map((b) => b.getAttribute("aria-label"))).toEqual(["Choose a colour for ink 3"]);
    fireEvent.click(within(form()).getByRole("button", { name: "Let the picture choose ink 1" }));
    expect(form().querySelectorAll('input[type="color"]')).toHaveLength(1);
    expect(within(form()).getByRole("button", { name: "Let the picture choose ink 1" })).toBeTruthy(); // the second colour moved up to the first row
  });

  it("drops the inks that no longer fit when the number of colours goes down, keeping the first ones", () => {
    const store = memoryStore();
    render(<VectorPanel {...panelProps({ store })} />);
    for (let i = 0; i < 2; i++) fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    for (let n = 1; n <= 3; n++) fireEvent.click(within(form()).getByRole("button", { name: `Choose a colour for ink ${n}` }));
    expect(form().querySelectorAll('input[type="color"]')).toHaveLength(3);
    fireEvent.click(within(form()).getByRole("button", { name: "Fewer colours" }));
    expect(form().querySelectorAll('input[type="color"]')).toHaveLength(2);
    expect(JSON.parse(store.data[VECTOR_FORM_KEY]).palette).toEqual(["#000000", "#FF0000"]);
  });
});

describe("Size, layers, arrangement and More", () => {
  it("sets both sides from a machine preset, shows it pressed, and not pressed once a side is changed", () => {
    render(<VectorPanel {...panelProps()} />);
    const preset = within(form()).getByRole("button", { name: "xTool P2 bed · 600 × 307 mm" });
    expect(preset.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(preset);
    expect(num("Width (mm)").value).toBe("600");
    expect(num("Height (mm)").value).toBe("307");
    expect(preset.getAttribute("aria-pressed")).toBe("true");
    type(num("Height (mm)"), "300");
    expect(preset.getAttribute("aria-pressed")).toBe("false");
  });

  it("takes a size with a decimal point and refuses one outside the server's range without changing the size", () => {
    const store = memoryStore();
    render(<VectorPanel {...panelProps({ store })} />);
    type(num("Width (mm)"), "123.5");
    expect(JSON.parse(store.data[VECTOR_FORM_KEY]).widthMm).toBe(123.5);
    type(num("Width (mm)"), "1201");
    expect(form().textContent).toContain("Enter a number from 5 to 1200.");
    expect(JSON.parse(store.data[VECTOR_FORM_KEY]).widthMm).toBe(123.5); // not committed
  });

  it("greys Arrangement when the layers are by shape, shows side by side as what will be used, and says why", () => {
    render(<VectorPanel {...panelProps()} />);
    fireEvent.click(radio("Arrangement", "Stacked (depth)"));
    expect(radio("Arrangement", "Stacked (depth)").getAttribute("aria-checked")).toBe("true");
    expect(radio("Arrangement", "Stacked (depth)").disabled).toBe(false);
    fireEvent.click(radio("Layers by", "Shape"));
    for (const name of ["Side by side", "Stacked (depth)"]) expect(radio("Arrangement", name).disabled, name).toBe(true);
    expect(radio("Arrangement", "Side by side").getAttribute("aria-checked")).toBe("true");
    expect(radio("Arrangement", "Stacked (depth)").getAttribute("aria-checked")).toBe("false");
    expect(within(form()).getByRole("radiogroup", { name: "Arrangement" }).getAttribute("aria-disabled")).toBe("true");
    expect(form().textContent).toContain("Layers by shape are always side by side.");
    // back to colour: the choice the person made is still there
    fireEvent.click(radio("Layers by", "Colour"));
    expect(radio("Arrangement", "Stacked (depth)").getAttribute("aria-checked")).toBe("true");
    expect(radio("Arrangement", "Stacked (depth)").disabled).toBe(false);
  });

  it("says what each arrangement does, and that by shape there is only one", () => {
    render(<VectorPanel {...panelProps()} />);
    const hint = () => within(form()).getByRole("radiogroup", { name: "Arrangement" }).parentElement?.querySelector(".field-hint")?.textContent;
    expect(hint()).toBe("Each layer holds only its own colour and nothing overlaps: for inlay and engraving.");
    fireEvent.click(radio("Arrangement", "Stacked (depth)"));
    expect(hint()).toBe("Each sheet holds its own colour and everything in front of it, so the sheets build up into relief: with 6 layers the front colour stands 6 sheets high.");
    fireEvent.click(radio("Layers by", "Shape"));
    expect(hint()).toBe("Layers by shape are always side by side.");
    const layersHint = () => within(form()).getByRole("radiogroup", { name: "Layers by" }).parentElement?.querySelector(".field-hint")?.textContent;
    expect(layersHint()).toBe("Each separate piece is its own layer, up to 30.");
    fireEvent.click(radio("Layers by", "Colour"));
    expect(layersHint()).toBe("One layer for each ink.");
  });

  it("takes a decimal for each side and each of the three small settings, and steps by a size that suits each", () => {
    const store = memoryStore();
    render(<VectorPanel {...panelProps({ store })} />);
    type(num("Height (mm)"), "45.25");
    expect(JSON.parse(store.data[VECTOR_FORM_KEY]).heightMm).toBe(45.25);
    type(num("Smallest feature (mm)"), "0.75");
    type(num("Kerf (mm)"), "0.125");
    fireEvent.click(radio("Arrangement", "Stacked (depth)"));
    type(num("Bleed (mm)"), "1.25");
    expect(JSON.parse(store.data[VECTOR_FORM_KEY])).toMatchObject({ minFeatureMm: 0.75, kerfMm: 0.125, bleedMm: 1.25 });
    expect([num("Width (mm)"), num("Height (mm)"), num("Smallest feature (mm)"), num("Kerf (mm)"), num("Bleed (mm)")].map((box) => box.getAttribute("step"))).toEqual(["1", "1", "0.05", "0.05", "0.1"]);
  });

  it("greys Bleed unless the layers are stacked, under More", () => {
    render(<VectorPanel {...panelProps()} />);
    const more = form().querySelector("details.vector-more") as HTMLDetailsElement;
    expect(more.querySelector("summary")?.textContent).toBe("More");
    for (const label of ["Smallest feature (mm)", "Kerf (mm)", "Bleed (mm)"]) expect(more.contains(num(label)), label).toBe(true);
    expect(num("Smallest feature (mm)").value).toBe("0.5");
    expect(num("Kerf (mm)").value).toBe("0");
    expect(num("Bleed (mm)").value).toBe("0.5");
    expect(num("Bleed (mm)").disabled).toBe(true); // side by side
    expect(form().textContent).toContain("Used only when the layers are Stacked (depth).");
    expect(form().textContent).not.toContain(BLEED_HINT); // what Bleed does is said when it can be used
    fireEvent.click(radio("Arrangement", "Stacked (depth)"));
    expect(num("Bleed (mm)").disabled).toBe(false);
    expect(form().textContent).toContain(BLEED_HINT);
    fireEvent.click(radio("Layers by", "Shape"));
    expect(num("Bleed (mm)").disabled).toBe(true); // by shape they are side by side
    expect(num("Smallest feature (mm)").disabled).toBe(false);
    expect(num("Kerf (mm)").disabled).toBe(false);
  });

  it("checks the three More settings against the server's limits", () => {
    render(<VectorPanel {...panelProps()} />);
    type(num("Smallest feature (mm)"), "11");
    expect(form().textContent).toContain("Enter a number from 0.05 to 10.");
    type(num("Kerf (mm)"), "2");
    expect(form().textContent).toContain("Enter a number from 0 to 1.");
    fireEvent.click(radio("Arrangement", "Stacked (depth)"));
    type(num("Bleed (mm)"), "6");
    expect(form().textContent).toContain("Enter a number from 0 to 5.");
  });

  it("lists a back sheet order the page has no words for by its own name, so that a newer server's choice is still offered", () => {
    const vector = { ...CAPS.limits.vector, stack_orders: [...CAPS.limits.vector.stack_orders, "newest_first"] as never };
    render(<VectorPanel {...panelProps({ caps: { ...CAPS, limits: { ...CAPS.limits, vector } } })} />);
    const options = Array.from(form().querySelectorAll("[data-back-sheet] select option")).map((o) => o.textContent);
    expect(options).toEqual(["The colour with the most ink", "The colour with the least ink", "The first ink in my list", "newest_first"]);
  });

  it("locks the seed on request, and the number is only for a locked seed", () => {
    render(<VectorPanel {...panelProps()} />);
    const lock = within(form()).getByLabelText("Lock seed") as HTMLInputElement;
    expect(lock.checked).toBe(false);
    expect(form().textContent).toContain("A new random seed for every run.");
    fireEvent.click(lock);
    expect(num("Seed").disabled).toBe(false);
    expect(form().textContent).toContain("make the same drawing, and the same SVG");
    type(num("Seed"), "77");
    expect((num("Seed")).value).toBe("77");
  });
});

describe("Back sheet (DESIGN.md §35.2)", () => {
  const backSheet = () => form().querySelector("[data-back-sheet] select") as HTMLSelectElement;
  const backHint = () => form().querySelector("[data-back-sheet] .field-hint")?.textContent;

  it("offers the three orders in the spec's words, with the colour with the most ink first, and starts there", () => {
    render(<VectorPanel {...panelProps()} />);
    expect(Array.from(backSheet().options).map((o) => [o.value, o.textContent])).toEqual([
      ["area_desc", "The colour with the most ink"], ["area_asc", "The colour with the least ink"], ["palette", "The first ink in my list"],
    ]);
    expect(backSheet().value).toBe("area_desc");
    expect(within(form()).getByLabelText("Back sheet")).toBe(backSheet());
  });

  it("is greyed, with the reason, until the layers are stacked by colour: side by side has no back sheet", () => {
    render(<VectorPanel {...panelProps()} />);
    expect(backSheet().disabled).toBe(true);
    expect(backHint()).toBe("Used only when the layers are Stacked (depth).");
    fireEvent.click(radio("Arrangement", "Stacked (depth)"));
    expect(backSheet().disabled).toBe(false);
    expect(backHint()).toBe(STACK_ORDER_HINT);
    fireEvent.click(radio("Layers by", "Shape"));
    expect(backSheet().disabled).toBe(true);
    expect(backHint()).toBe("Layers by shape are always side by side, so there is no back sheet.");
  });

  it("says, for the list order, that the first ink row is the back sheet and what follows it", () => {
    render(<VectorPanel {...panelProps()} />);
    fireEvent.click(radio("Arrangement", "Stacked (depth)"));
    fireEvent.change(backSheet(), { target: { value: "palette" } });
    expect(backHint()).toContain("The order of the ink rows above is the order here: the first is at the back");
  });

  it("is remembered with the rest of the form, and sent as stack_order", async () => {
    fakeRecipe();
    const store = memoryStore();
    const props = panelProps({ store });
    const created = vi.spyOn(api, "createVectorRun").mockResolvedValue(makeVectorRun());
    render(<VectorPanel {...props} />);
    type(idea(), "a fox head");
    fireEvent.click(radio("Arrangement", "Stacked (depth)"));
    fireEvent.change(backSheet(), { target: { value: "area_asc" } });
    expect(JSON.parse(store.data[VECTOR_FORM_KEY]).stackOrder).toBe("area_asc");
    await waitFor(() => expect(make().disabled).toBe(false));
    fireEvent.click(make());
    await waitFor(() => expect(created).toHaveBeenCalled());
    const body = created.mock.calls[0][0];
    expect(body.options.stack_order).toBe("area_asc");
    expect(body.options.arrangement).toBe("stacked");
  });

  it("uses the labels of the spec and no others", () => {
    expect(STACK_ORDER_LABELS).toEqual({ area_desc: "The colour with the most ink", area_asc: "The colour with the least ink", palette: "The first ink in my list" });
  });
});

describe("What the studio sends", () => {
  it("is asked of the server for the prompt, kind, colours and palette after a pause, once for a burst of typing, and shown", async () => {
    const asked = fakeRecipe();
    render(<VectorPanel {...panelProps()} />);
    expect(recipeBox().value).toBe("");
    expect(recipeBox().placeholder).toContain("Write your idea above");
    type(idea(), "a");
    type(idea(), "a fo");
    type(idea(), "a fox");
    await recipeShows("WORDS[a fox|silhouette|2|]");
    expect(asked).toHaveBeenCalledTimes(1); // the three keystrokes made one request
    expect(asked).toHaveBeenCalledWith({ prompt: "a fox", kind: "silhouette", colours: 2, palette: [] });
    expect(negativeBox().value).toBe("NEGATIVE-FIXED");
  });

  it("waits 350 ms after the last key before asking, and says it is writing while it waits and until the answer comes", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      let answer: (value: { positive: string; negative: string }) => void = () => undefined;
      const asked = vi.spyOn(api, "vectorRecipe").mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
      render(<VectorPanel {...panelProps()} />);
      expect(recipeBox().getAttribute("aria-busy")).toBe("false");
      type(idea(), "a fox");
      expect(recipeBox().getAttribute("aria-busy")).toBe("true"); // it will ask: busy from the first key
      expect(form().textContent).toContain("Writing these words…");
      await act(async () => { vi.advanceTimersByTime(349); });
      expect(asked).not.toHaveBeenCalled(); // not yet
      await act(async () => { vi.advanceTimersByTime(1); });
      expect(asked).toHaveBeenCalledTimes(1); // after exactly the pause
      expect(recipeBox().getAttribute("aria-busy")).toBe("true"); // still waiting for the answer
      await act(async () => { answer({ positive: "THE WORDS", negative: "N" }); });
      expect(recipeBox().value).toBe("THE WORDS");
      expect(recipeBox().getAttribute("aria-busy")).toBe("false");
      expect(form().textContent).toContain("Written by the studio from your prompt, the kind and the colours. Change it by hand if you like.");
      expect(form().textContent).not.toContain("Writing these words…");
      // the hint under the box is what the box is described by
      expect(document.getElementById(recipeBox().getAttribute("aria-describedby") ?? "")?.textContent).toContain("Written by the studio");
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops saying it is writing when the words could not be written", async () => {
    vi.spyOn(api, "vectorRecipe").mockRejectedValue(new ApiError("No.", 500));
    render(<VectorPanel {...panelProps()} />);
    type(idea(), "a fox");
    await waitFor(() => expect(form().textContent).toContain("Couldn't write these words just now (No.)"));
    expect(recipeBox().getAttribute("aria-busy")).toBe("false");
    expect(form().textContent).not.toContain("Writing these words…");
  });

  it("asks again when the kind, the colours or an ink changes (the box follows them while it is not edited)", async () => {
    const asked = fakeRecipe();
    render(<VectorPanel {...panelProps()} />);
    type(idea(), "a fox");
    await recipeShows("WORDS[a fox|silhouette|2|]");
    fireEvent.click(radio("Kind of artwork", "Line art"));
    await recipeShows("WORDS[a fox|line|2|]");
    fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    await recipeShows("WORDS[a fox|line|3|]");
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 1" }));
    await recipeShows("WORDS[a fox|line|3|#000000]");
    expect(asked).toHaveBeenCalledTimes(4);
    // a change that is not part of the words asks nothing
    fireEvent.click(radio("Layers by", "Shape"));
    type(num("Width (mm)"), "200");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });
    expect(asked).toHaveBeenCalledTimes(4);
  });

  it("asks nothing for an empty prompt, and nothing while the tab is not on show", async () => {
    const asked = fakeRecipe();
    const { rerender } = render(<VectorPanel {...panelProps({ hidden: true, store: memoryStore({ [VECTOR_FORM_KEY]: JSON.stringify({ idea: "a remembered idea" }) }) })} />);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });
    expect(asked).not.toHaveBeenCalled(); // a remembered prompt does not make a request on a page load of another tab
    rerender(<VectorPanel {...panelProps({ hidden: false, store: memoryStore({ [VECTOR_FORM_KEY]: JSON.stringify({ idea: "a remembered idea" }) }) })} />);
    await recipeShows("WORDS[a remembered idea|silhouette|2|]");
    expect(asked).toHaveBeenCalledTimes(1);
    type(idea(), "   ");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });
    expect(asked).toHaveBeenCalledTimes(1);
    expect(recipeBox().value).toBe(""); // no prompt: the box does not keep showing the words of one
    expect(recipeBox().getAttribute("aria-busy")).toBe("false"); // and is not waiting for any
  });

  it("is editable: an edit is kept, the form stops rewriting it, and Rebuild gives the server's words back", async () => {
    fakeRecipe();
    render(<VectorPanel {...panelProps()} />);
    const rebuild = within(form()).getByRole("button", { name: "Rebuild" }) as HTMLButtonElement;
    type(idea(), "a fox");
    await recipeShows("WORDS[a fox|silhouette|2|]");
    expect(rebuild.disabled).toBe(true); // nothing to rebuild yet
    type(recipeBox(), "My own words, exactly.");
    expect(recipeBox().value).toBe("My own words, exactly.");
    expect(form().textContent).toContain("You changed this by hand");
    expect(rebuild.disabled).toBe(false);
    // the prompt and the kind no longer rewrite it
    type(idea(), "a fox on a hill");
    fireEvent.click(radio("Kind of artwork", "Flat colour art"));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });
    expect(recipeBox().value).toBe("My own words, exactly.");
    fireEvent.click(rebuild);
    await recipeShows("WORDS[a fox on a hill|flat|2|]");
    expect(rebuild.disabled).toBe(true);
    expect(form().textContent).not.toContain("You changed this by hand");
  });

  it("shows how long the words are only once they are close to the limit (80 per cent of it)", () => {
    const store = memoryStore({ [VECTOR_FORM_KEY]: JSON.stringify({ idea: "a fox", recipeEdited: true, recipeText: "x".repeat(1600) }) });
    render(<VectorPanel {...panelProps({ store })} />);
    expect(form().textContent).not.toContain("1600 / 2000");
    type(recipeBox(), "x".repeat(1601));
    expect(form().textContent).toContain("1601 / 2000");
  });

  it("shows the fixed negative text as read-only", async () => {
    fakeRecipe();
    render(<VectorPanel {...panelProps()} />);
    expect(negativeBox().readOnly).toBe(true);
    type(idea(), "a fox");
    await waitFor(() => expect(negativeBox().value).toBe("NEGATIVE-FIXED"));
    fireEvent.change(negativeBox(), { target: { value: "changed" } }); // a change event on a read-only box is not a change the person can make: it is not kept
    expect(recipeBox().value).toBe("WORDS[a fox|silhouette|2|]");
  });

  it("says when the words could not be written, and that the studio writes them when it sends", async () => {
    vi.spyOn(api, "vectorRecipe").mockRejectedValue(new ApiError("Can't reach the studio server.", 0));
    render(<VectorPanel {...panelProps()} />);
    type(idea(), "a fox");
    await waitFor(() => expect(form().textContent).toContain("Couldn't write these words just now (Can't reach the studio server.)"));
    expect(make().disabled).toBe(false); // the run can still be made: the server builds the words itself
  });

  it("never shows words for an older prompt: an answer that arrives late is dropped", async () => {
    const answers: Record<string, (value: { positive: string; negative: string }) => void> = {};
    vi.spyOn(api, "vectorRecipe").mockImplementation((body) => new Promise((resolve) => { answers[body.prompt] = resolve; }));
    render(<VectorPanel {...panelProps()} />);
    type(idea(), "first");
    await waitFor(() => expect(answers.first).toBeDefined());
    type(idea(), "second");
    await waitFor(() => expect(answers.second).toBeDefined());
    await act(async () => { answers.second({ positive: "SECOND WORDS", negative: "N" }); });
    await act(async () => { answers.first({ positive: "FIRST WORDS", negative: "N" }); }); // the older request answers last
    expect(recipeBox().value).toBe("SECOND WORDS");
  });
});

describe("making a vector", () => {
  const made = makeVectorRun({ id: "made-run" });

  it("sends exactly the request the form describes, and hands the run it gets back to the page", async () => {
    fakeRecipe();
    const created = vi.spyOn(api, "createVectorRun").mockResolvedValue(made);
    const props = panelProps();
    render(<VectorPanel {...props} />);
    type(idea(), "  a fox head ");
    fireEvent.click(radio("Kind of artwork", "Flat colour art"));
    for (let i = 0; i < 2; i++) fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 1" }));
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 2" }));
    type(within(form()).getByLabelText("Ink 2 colour"), "#00aa00"); // (the second first: a colour another ink has is refused, and ink 2 starts as red)
    type(within(form()).getByLabelText("Ink 1 colour"), "#ff0000");
    fireEvent.click(within(form()).getByLabelText("Lock seed"));
    type(num("Seed"), "1234");
    expect(make().disabled).toBe(false);
    fireEvent.click(make());
    await waitFor(() => expect(props.onRun).toHaveBeenCalledWith(made));
    // the same as the contract's example request: 4 colours, an ink palette without white, the fox's settings
    expect(created).toHaveBeenCalledWith(VECTOR_CONTRACT.create_request);
  });

  it("sends the hand-written words as `recipe`, and a random seed as null", async () => {
    fakeRecipe();
    const created = vi.spyOn(api, "createVectorRun").mockResolvedValue(made);
    render(<VectorPanel {...panelProps()} />);
    type(idea(), "a fox");
    await recipeShows("WORDS[a fox|silhouette|2|]");
    type(recipeBox(), "My own words.");
    fireEvent.click(make());
    await waitFor(() => expect(created).toHaveBeenCalled());
    const body = created.mock.calls[0][0];
    expect(body.options.recipe).toBe("My own words.");
    expect(body.options.seed).toBeNull();
    expect(body.prompt).toBe("a fox");
  });

  it("sends with Ctrl+Enter from the form, as the other tabs do", async () => {
    fakeRecipe();
    const created = vi.spyOn(api, "createVectorRun").mockResolvedValue(made);
    render(<VectorPanel {...panelProps()} />);
    type(idea(), "a fox");
    fireEvent.keyDown(idea(), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(created).toHaveBeenCalledTimes(1));
    fireEvent.keyDown(idea(), { key: "Enter" }); // a plain Enter is a new line in the box, not a send
    await act(async () => undefined);
    expect(created).toHaveBeenCalledTimes(1);
  });

  it("makes one run for a double press: the button waits for the answer", async () => {
    fakeRecipe();
    let finish: (run: VectorRun) => void = () => undefined;
    const created = vi.spyOn(api, "createVectorRun").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    render(<VectorPanel {...panelProps()} />);
    type(idea(), "a fox");
    const button = make();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.keyDown(idea(), { key: "Enter", ctrlKey: true });
    expect(created).toHaveBeenCalledTimes(1);
    expect(button.textContent).toContain("Sending…");
    expect(button.disabled).toBe(true);
    await act(async () => { finish(made); });
    expect(button.textContent).toContain("Make vector");
  });

  it("says what is wrong before sending when the form is wrong, and sends nothing", async () => {
    fakeRecipe();
    const created = vi.spyOn(api, "createVectorRun").mockResolvedValue(made);
    render(<VectorPanel {...panelProps()} />);
    type(idea(), "a fox");
    await recipeShows("WORDS[a fox|silhouette|2|]");
    type(recipeBox(), "   ");
    fireEvent.click(make());
    expect(screen.getByRole("alert").textContent).toMatch(/words the studio sends are empty/);
    expect(created).not.toHaveBeenCalled();
    fireEvent.click(within(form()).getByRole("button", { name: "Rebuild" })); // the message goes as soon as the form changes
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the server's refusal (422) in the form, and a failure of any other kind as a toast", async () => {
    fakeRecipe();
    const created = vi.spyOn(api, "createVectorRun").mockRejectedValueOnce(new ApiError("options.palette: #FF0000 is listed twice.", 422));
    const props = panelProps();
    render(<VectorPanel {...props} />);
    type(idea(), "a fox");
    fireEvent.click(make());
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("options.palette: #FF0000 is listed twice."));
    expect(props.push).not.toHaveBeenCalled();
    created.mockRejectedValueOnce(new ApiError("The queue is full.", 429));
    fireEvent.click(make());
    await waitFor(() => expect(props.push).toHaveBeenCalledWith("error", "The queue is full."));
    created.mockRejectedValueOnce(new ApiError("Boom", 500));
    fireEvent.click(make());
    await waitFor(() => expect(props.push).toHaveBeenCalledWith("error", "Couldn't start the run: Boom"));
  });
});

describe("what the form remembers", () => {
  it("saves the form as it is changed, and a new page starts from it", () => {
    const store = memoryStore();
    const { unmount } = render(<VectorPanel {...panelProps({ store })} />);
    type(idea(), "an owl");
    fireEvent.click(radio("Kind of artwork", "Line art"));
    fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    fireEvent.click(within(form()).getByRole("button", { name: "Choose a colour for ink 1" }));
    type(num("Width (mm)"), "321.5");
    fireEvent.click(radio("Layers by", "Shape"));
    unmount();
    cleanup();
    render(<VectorPanel {...panelProps({ store })} />);
    expect(idea().value).toBe("an owl");
    expect(radio("Kind of artwork", "Line art").getAttribute("aria-checked")).toBe("true");
    expect(coloursValue()).toBe("3");
    expect(form().querySelectorAll('input[type="color"]')).toHaveLength(1);
    expect(num("Width (mm)").value).toBe("321.5");
    expect(radio("Layers by", "Shape").getAttribute("aria-checked")).toBe("true");
  });

  it("starts from the defaults when what is saved is damaged, without a word", () => {
    render(<VectorPanel {...panelProps({ store: memoryStore({ [VECTOR_FORM_KEY]: "{broken" }) })} />);
    expect(coloursValue()).toBe("2");
    expect(idea().value).toBe("");
  });

  it("works with storage blocked: it starts from the defaults, takes changes, and makes a run", async () => {
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } });
    const store = browserStore();
    expect(store.available).toBe(false);
    fakeRecipe();
    const created = vi.spyOn(api, "createVectorRun").mockResolvedValue(makeVectorRun());
    render(<VectorPanel {...panelProps({ store })} />);
    expect(coloursValue()).toBe("2");
    type(idea(), "a fox");
    fireEvent.click(within(form()).getByRole("button", { name: "More colours" }));
    expect(coloursValue()).toBe("3");
    fireEvent.click(make());
    await waitFor(() => expect(created).toHaveBeenCalled());
    expect(created.mock.calls[0][0].options.colours).toBe(3);
  });

  it("remembers a hand-written recipe, so it is still the text that is sent", async () => {
    fakeRecipe();
    const store = memoryStore();
    const { unmount } = render(<VectorPanel {...panelProps({ store })} />);
    type(idea(), "a fox");
    type(recipeBox(), "My own words.");
    unmount();
    cleanup();
    render(<VectorPanel {...panelProps({ store })} />);
    expect(recipeBox().value).toBe("My own words.");
    expect((within(form()).getByRole("button", { name: "Rebuild" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("when Vector cannot run here", () => {
  it("says so on the tab, in the server's own words, instead of the form", () => {
    const asked = fakeRecipe();
    const caps: Capabilities = { ...CAPS, modes: ["generate", "edit", "music"], vector: { available: false, reason: "The Vector libraries are not installed here (vtracer).", hint: "They are in backend/requirements.txt: rebuild the image." } };
    render(<VectorPanel {...panelProps({ caps, store: memoryStore({ [VECTOR_FORM_KEY]: JSON.stringify({ idea: "a remembered idea" }) }) })} />);
    expect(screen.queryByRole("region", { name: "New vector" })).toBeNull();
    const notice = screen.getByRole("region", { name: "Vector is not available" });
    expect(notice.textContent).toContain("Vector isn't available on this server");
    expect(within(notice).getByRole("status").textContent).toBe("The Vector libraries are not installed here (vtracer).");
    expect(notice.textContent).toContain("They are in backend/requirements.txt: rebuild the image.");
    expect(notice.textContent).toContain("Images work as usual.");
    expect(asked).not.toHaveBeenCalled();
    expect(screen.getByTestId("filter-bar")).toBeTruthy(); // the history is still there
  });

  it("has a line for the hint only when the server gave one", () => {
    const withHint: Capabilities = { ...CAPS, vector: { available: false, reason: "No.", hint: "Rebuild it." } };
    render(<VectorPanel {...panelProps({ caps: withHint })} />);
    expect(screen.getByRole("region", { name: "Vector is not available" }).querySelectorAll(".hint")).toHaveLength(2); // the hint, and "Images work as usual."
    cleanup();
    render(<VectorPanel {...panelProps({ caps: { ...CAPS, vector: { available: false, reason: "No.", hint: null } } })} />);
    expect(screen.getByRole("region", { name: "Vector is not available" }).querySelectorAll(".hint")).toHaveLength(1);
  });

  it("shows the queue only while Vector can run", () => {
    const busy = { ...STATUS, queue: { ...STATUS.queue, running: "some-run", queued: 1 } };
    render(<VectorPanel {...panelProps({ status: busy })} />);
    expect(document.querySelector(".queue-bar")?.textContent).toContain("Generating 1 run");
    cleanup();
    render(<VectorPanel {...panelProps({ status: busy, caps: { ...CAPS, vector: { available: false, reason: "No.", hint: null } } })} />);
    expect(document.querySelector(".queue-bar")).toBeNull();
  });

  it("says it in its own sentence when the server gave no reason", () => {
    render(<VectorPanel {...panelProps({ caps: { ...CAPS, vector: { available: false, reason: null, hint: null } } })} />);
    expect(screen.getByRole("status").textContent).toBe("Vector can't run on this server as it is set up.");
  });
});

describe("the list under the form", () => {
  it("shows a card for each run, the filter bar, and the empty state when there are none", () => {
    render(<VectorPanel {...panelProps({ runs: [makeVectorRun({ id: "v1", prompt: "first idea" }), makeVectorRun({ id: "v2", prompt: "second idea" })] })} />);
    expect(screen.getByTestId("filter-bar")).toBeTruthy();
    expect(Array.from(document.querySelectorAll("article.run-card")).map((c) => c.getAttribute("data-run-id"))).toEqual(["v1", "v2"]);
    cleanup();
    render(<VectorPanel {...panelProps()} />);
    expect(screen.getByText("Nothing here yet.")).toBeTruthy();
  });

  it("says it is loading, or why the list could not be loaded, before the first page", () => {
    render(<VectorPanel {...panelProps({ runsReady: false })} />);
    expect(screen.getByText("Loading your vectors…")).toBeTruthy();
    cleanup();
    render(<VectorPanel {...panelProps({ runsReady: false, loadProblem: <p>It could not be loaded.</p> })} />);
    expect(screen.getByText("It could not be loaded.")).toBeTruthy();
  });

  it("offers older runs, and asks for them", () => {
    const props = panelProps({ more: true });
    render(<VectorPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Load older runs" }));
    expect(props.onLoadOlder).toHaveBeenCalledTimes(1);
    cleanup();
    render(<VectorPanel {...panelProps({ more: true, loadingOlder: true })} />);
    expect((screen.getByRole("button", { name: "Loading…" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("passes a card's Keep, Delete, Cancel and Restore to the page, with the run", () => {
    const run = makeVectorRun({ id: "v1" });
    const props = panelProps({ runs: [run] });
    render(<VectorPanel {...props} />);
    fireEvent.click(byAction("keep") as HTMLElement);
    fireEvent.click(byAction("delete") as HTMLElement);
    expect(props.onToggleKeep).toHaveBeenCalledWith(run);
    expect(props.onDelete).toHaveBeenCalledWith(run);
    cleanup();
    const working = makeVectorRun({ id: "v2", status: "running", vectors: [] });
    const props2 = panelProps({ runs: [working] });
    render(<VectorPanel {...props2} />);
    fireEvent.click(byAction("cancel") as HTMLElement);
    expect(props2.onCancel).toHaveBeenCalledWith(working);
    cleanup();
    const binned = makeVectorRun({ id: "v3", deleted_at: "2026-10-10T00:00:00.000Z", purge_at: "2026-11-09T00:00:00.000Z" });
    const props3 = panelProps({ runs: [binned] });
    render(<VectorPanel {...props3} />);
    fireEvent.click(byAction("restore") as HTMLElement);
    expect(props3.onRestore).toHaveBeenCalledWith(binned);
  });

  it("shows only the vectors it is given, and is hidden (not removed) when another tab is open", () => {
    const { container } = render(<VectorPanel {...panelProps({ hidden: true })} />);
    expect((container.querySelector('[role="tabpanel"]') as HTMLElement).hidden).toBe(true);
    expect(container.querySelector('[role="tabpanel"]')?.getAttribute("id")).toBe("panel-vector");
    expect(container.querySelector('[role="tabpanel"]')?.getAttribute("aria-labelledby")).toBe("tab-vector");
  });
});

describe("Reuse and Retry from a card", () => {
  // a run that differs from the form's defaults in every field
  const options = {
    ...makeVectorRun().options, colours: 6, palette: ["#0000FF", "#FFA500"], kind: "line" as const, width_mm: 321.5, height_mm: 45, layers_by: "shape" as const,
    arrangement: "stacked" as const, stack_order: "palette" as const, bleed_mm: 1.5, min_feature_mm: 0.8, kerf_mm: 0.2, num_images: 3, seed: 555, recipe: wordsFor({ prompt: "an owl", kind: "line", colours: 6, palette: ["#0000FF", "#FFA500"] }),
  };
  const run = makeVectorRun({ id: "reuse-me", prompt: "an owl", options });

  it("restores the prompt and every setting to the form, with the seed locked, scrolls to it and says so", async () => {
    fakeRecipe();
    const props = panelProps({ runs: [run] });
    render(<VectorPanel {...props} />);
    fireEvent.click(byAction("reuse") as HTMLElement);
    expect(idea().value).toBe("an owl");
    expect(radio("Kind of artwork", "Line art").getAttribute("aria-checked")).toBe("true");
    expect(coloursValue()).toBe("6");
    expect(Array.from(form().querySelectorAll('input[type="color"]')).map((input) => (input as HTMLInputElement).value)).toEqual(["#0000ff", "#ffa500"]);
    expect(num("Width (mm)").value).toBe("321.5");
    expect(num("Height (mm)").value).toBe("45");
    expect(radio("Layers by", "Shape").getAttribute("aria-checked")).toBe("true");
    // by shape the arrangement is greyed and shows side by side, but the choice the run recorded is what the form holds: stacked
    expect(radio("Arrangement", "Side by side").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(radio("Layers by", "Colour"));
    expect(radio("Arrangement", "Stacked (depth)").getAttribute("aria-checked")).toBe("true");
    expect((form().querySelector("[data-back-sheet] select") as HTMLSelectElement).value).toBe("palette");
    expect(num("Bleed (mm)").value).toBe("1.5");
    expect(num("Smallest feature (mm)").value).toBe("0.8");
    expect(num("Kerf (mm)").value).toBe("0.2");
    expect(radio("Versions", "3").getAttribute("aria-checked")).toBe("true");
    expect((within(form()).getByLabelText("Lock seed") as HTMLInputElement).checked).toBe(true);
    expect(num("Seed").value).toBe("555");
    expect(props.push).toHaveBeenCalledWith("info", "Loaded the prompt and settings. Seed locked to 555.");
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "smooth" }); // to the top, where the form is
    expect(document.activeElement).toBe(idea());
    // ... and focus goes to the prompt without the page jumping a second time
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    fireEvent.click(byAction("reuse") as HTMLElement);
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });

  it("keeps the box automatic when the run used the server's own words", async () => {
    const asked = fakeRecipe();
    render(<VectorPanel {...panelProps({ runs: [run] })} />);
    fireEvent.click(byAction("reuse") as HTMLElement);
    await recipeShows(options.recipe);
    await act(async () => undefined);
    expect(asked).toHaveBeenCalledWith({ prompt: "an owl", kind: "line", colours: 6, palette: ["#0000FF", "#FFA500"] });
    expect(form().textContent).not.toContain("You changed this by hand");
    expect((within(form()).getByRole("button", { name: "Rebuild" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps the run's own words as an edit when they are not what the server writes (it was edited by hand)", async () => {
    fakeRecipe();
    const handmade = makeVectorRun({ id: "hand", prompt: "an owl", options: { ...options, recipe: "An owl, my way." } });
    render(<VectorPanel {...panelProps({ runs: [handmade] })} />);
    fireEvent.click(byAction("reuse") as HTMLElement);
    await waitFor(() => expect(recipeBox().value).toBe("An owl, my way."));
    expect(form().textContent).toContain("You changed this by hand");
    expect((within(form()).getByRole("button", { name: "Rebuild" }) as HTMLButtonElement).disabled).toBe(false);
    // and that is the text a new run would send
    const created = vi.spyOn(api, "createVectorRun").mockResolvedValue(makeVectorRun());
    fireEvent.click(make());
    await waitFor(() => expect(created).toHaveBeenCalled());
    expect(created.mock.calls[0][0].options.recipe).toBe("An owl, my way.");
  });

  it("keeps the run's words when the server's could not be had", async () => {
    vi.spyOn(api, "vectorRecipe").mockRejectedValue(new ApiError("Can't reach the studio server.", 0));
    render(<VectorPanel {...panelProps({ runs: [run] })} />);
    fireEvent.click(byAction("reuse") as HTMLElement);
    await waitFor(() => expect(recipeBox().value).toBe(options.recipe));
    expect(form().textContent).toContain("You changed this by hand");
  });

  it("does not put the run's words over something the person did meanwhile", async () => {
    const answers: Record<string, (value: { positive: string; negative: string }) => void> = {};
    vi.spyOn(api, "vectorRecipe").mockImplementation((body) => new Promise((resolve) => { answers[body.prompt] = resolve; }));
    const handmade = makeVectorRun({ id: "hand", prompt: "an owl", options: { ...options, recipe: "An owl, my way." } });
    render(<VectorPanel {...panelProps({ runs: [handmade] })} />);
    fireEvent.click(byAction("reuse") as HTMLElement);
    await waitFor(() => expect(answers["an owl"]).toBeDefined());
    type(recipeBox(), "I typed this meanwhile.");
    await act(async () => { answers["an owl"]({ positive: "SERVER WORDS", negative: "N" }); });
    expect(recipeBox().value).toBe("I typed this meanwhile.");
  });

  it("settles at once when the words for the restored form are already known", async () => {
    fakeRecipe();
    const same = makeVectorRun({ id: "same", prompt: "a fox", options: { ...options, colours: 2, palette: [], kind: "silhouette", recipe: "A different text." } });
    render(<VectorPanel {...panelProps({ runs: [same] })} />);
    type(idea(), "a fox");
    await recipeShows("WORDS[a fox|silhouette|2|]");
    fireEvent.click(byAction("reuse") as HTMLElement); // same prompt, kind and colours: the key does not change, so nothing new is asked
    expect(recipeBox().value).toBe("A different text.");
    expect(form().textContent).toContain("You changed this by hand");
  });

  it("Retry makes the same run again, with the same seed and words", async () => {
    const created = vi.spyOn(api, "createVectorRun").mockResolvedValue(makeVectorRun({ id: "again" }));
    const failed = makeVectorRun({ id: "f", status: "failed", vectors: [], error: { message: "It failed.", hint: null } });
    const props = panelProps({ runs: [failed] });
    render(<VectorPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(props.onRun).toHaveBeenCalledTimes(1));
    expect(created).toHaveBeenCalledWith({
      mode: "vector", prompt: "a fox head",
      options: { ...VECTOR_CONTRACT.create_request.options, recipe: "A fox head, flat colour artwork ..." },
    });
  });

  it("tells the person when a Retry could not be made", async () => {
    vi.spyOn(api, "createVectorRun").mockRejectedValue(new ApiError("The queue is full.", 429));
    const props = panelProps({ runs: [makeVectorRun({ id: "f", status: "failed", vectors: [], error: { message: "It failed.", hint: null } })] });
    render(<VectorPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(props.push).toHaveBeenCalledWith("error", "Couldn't retry: The queue is full."));
  });
});

/** The page's way of using the hook, in miniature: a card whose Copy SVG code button goes through it, and the words it tells, shown where the test can read them. */
function CopyHarness({ run, told }: { run: VectorRun; told: (kind: string, text: string) => void }) {
  const copy = useCopyVectorSvg(told);
  return <VectorCard {...cardProps(run, { onCopySvg: (item) => void copy(item) })} />;
}

describe("Copy SVG code", () => {
  const run = makeVectorRun({ id: "copy-me" });
  const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm"><g id="THE-FILES-OWN-MARKER"/></svg>';
  const clipboard = () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    return writeText;
  };

  it("fetches the file as text, copies it, and says so; the file is never put in the page", async () => {
    const writeText = clipboard();
    const fetched = vi.spyOn(api, "fetchText").mockResolvedValue(SVG);
    const told = vi.fn();
    const { container } = render(<CopyHarness run={run} told={told} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy SVG code" }));
    await waitFor(() => expect(told).toHaveBeenCalledWith("success", "SVG code copied."));
    expect(fetched).toHaveBeenCalledWith(run.vectors[0].url);
    expect(writeText).toHaveBeenCalledWith(SVG);
    expect(container.innerHTML).not.toContain("THE-FILES-OWN-MARKER"); // not as markup, not as text: only on the clipboard
  });

  it("says so when the file could not be had, and when it could not be copied", async () => {
    clipboard();
    const fetched = vi.spyOn(api, "fetchText").mockRejectedValue(new ApiError("That vector no longer exists.", 404));
    const told = vi.fn();
    render(<CopyHarness run={run} told={told} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy SVG code" }));
    await waitFor(() => expect(told).toHaveBeenCalledWith("error", "Couldn't get the SVG: That vector no longer exists."));
    fetched.mockResolvedValue(SVG);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn(async () => { throw new Error("denied"); }) }, configurable: true });
    document.execCommand = vi.fn(() => false); // the older way fails too
    fireEvent.click(screen.getByRole("button", { name: "Copy SVG code" }));
    await waitFor(() => expect(told).toHaveBeenCalledWith("error", "Couldn't copy. Use Download SVG instead."));
  });

  it("fetches once for a double press, and again after the first has finished", async () => {
    clipboard();
    let finish: (text: string) => void = () => undefined;
    const fetched = vi.spyOn(api, "fetchText").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const told = vi.fn();
    render(<CopyHarness run={run} told={told} />);
    const button = screen.getByRole("button", { name: "Copy SVG code" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(fetched).toHaveBeenCalledTimes(1);
    await act(async () => { finish(SVG); });
    await waitFor(() => expect(told).toHaveBeenCalledTimes(1));
    fireEvent.click(button);
    expect(fetched).toHaveBeenCalledTimes(2);
    await act(async () => { finish(SVG); });
  });

  it("tells the latest place that asked, even for a request already under way (a card's toast becomes the viewer's note when the viewer opens)", async () => {
    clipboard();
    let finish: (text: string) => void = () => undefined;
    vi.spyOn(api, "fetchText").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<CopyHarness run={run} told={first} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy SVG code" }));
    rerender(<CopyHarness run={run} told={second} />);
    await act(async () => { finish(SVG); });
    await waitFor(() => expect(second).toHaveBeenCalledWith("success", "SVG code copied."));
    expect(first).not.toHaveBeenCalled();
  });
});

describe("the api client", () => {
  it("fetches an SVG as text, and reports a refusal as the studio's own error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<svg/>", { status: 200 })));
    expect(await api.fetchText("/api/vectors/x/svg")).toBe("<svg/>");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "No such vector." }), { status: 404 })));
    await expect(api.fetchText("/api/vectors/x/svg")).rejects.toMatchObject({ status: 404, message: "No such vector." });
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    await expect(api.fetchText("/api/vectors/x/svg")).rejects.toMatchObject({ status: 0, message: "Can't reach the studio server." });
  });

  it("posts a run and a recipe request as JSON with the header every write carries", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return new Response(JSON.stringify({ positive: "p", negative: "n" }), { status: 200 });
    }));
    await api.vectorRecipe(VECTOR_CONTRACT.recipe_request as VectorRecipeRequest);
    await api.createVectorRun(VECTOR_CONTRACT.create_request as never);
    expect(seen.map((call) => [call.url, call.init.method])).toEqual([["/api/vector/recipe", "POST"], ["/api/runs", "POST"]]);
    for (const call of seen) expect((call.init.headers as Record<string, string>)["X-Studio-Client"]).toBe("1");
    expect(JSON.parse(seen[0].init.body as string)).toEqual(VECTOR_CONTRACT.recipe_request);
    expect(JSON.parse(seen[1].init.body as string)).toEqual(VECTOR_CONTRACT.create_request);
  });
});

// ------------------------------------------------------------------ the parts the three tabs share
describe("the tab bar", () => {
  it("has Images, Music and Vector, with Vector last, and arrow keys that go round the three", () => {
    const onTab = vi.fn();
    render(<Tabs tab="music" onTab={onTab} busy={{ images: false, music: false, vector: false }} />);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Images", "Music", "Vector"]);
    expect(screen.getByRole("tab", { name: "Vector" }).getAttribute("aria-controls")).toBe("panel-vector");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Music" }), { key: "ArrowRight" });
    expect(onTab).toHaveBeenLastCalledWith("vector");
    cleanup();
    render(<Tabs tab="vector" onTab={onTab} busy={{ images: false, music: false, vector: false }} />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Vector" }), { key: "ArrowRight" });
    expect(onTab).toHaveBeenLastCalledWith("images");
    expect(screen.getByRole("tab", { name: "Vector" }).getAttribute("tabindex")).toBe("0"); // one tab stop for the bar
    expect(screen.getByRole("tab", { name: "Images" }).getAttribute("tabindex")).toBe("-1");
  });

  it("shows work going on in the Vector tab while another tab is open", () => {
    render(<Tabs tab="images" onTab={nothing} busy={{ images: false, music: false, vector: true }} />);
    expect(screen.getByRole("tab", { name: /Vector/ }).textContent).toContain("(working)");
    expect(screen.getByRole("tab", { name: /Images/ }).textContent).not.toContain("(working)");
  });
});

describe("the filter bar and the empty states on the Vector tab", () => {
  const PROJECTS: Project[] = [
    { id: "a".repeat(32), name: "Logo", created_at: "2026-10-02T10:00:00.000Z", counts: { image: 4, music: 1, vector: 2 } },
    { id: "b".repeat(32), name: "Pitch", created_at: "2026-10-03T10:00:00.000Z", counts: { image: 0, music: 2 } }, // no Vector count in this list
  ];
  const options = (html: string) => [...html.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((m) => m[2]);

  it("counts the folders by the Vector tab's runs, and shows no number where the server's list gave none (never a 0)", () => {
    const html = renderToStaticMarkup(<FilterBar filter={NO_FILTER} keptCount={1} deletedCount={2} binTotal={3} projects={PROJECTS} tab="vector" onChange={nothing} onEmptyBin={nothing} onManageProjects={nothing} />);
    expect(options(html)).toEqual(["Any project", "No project", "Logo (2)", "Pitch"]);
    // the other tabs are counted as they were
    const images = renderToStaticMarkup(<FilterBar filter={NO_FILTER} keptCount={1} deletedCount={2} binTotal={3} projects={PROJECTS} tab="image" onChange={nothing} onEmptyBin={nothing} onManageProjects={nothing} />);
    expect(options(images)).toEqual(["Any project", "No project", "Logo (4)", "Pitch (0)"]);
  });

  it("has the same All, Kept and Deleted, with Empty bin in the Deleted view", () => {
    const html = renderToStaticMarkup(<FilterBar filter={ONLY_DELETED} keptCount={0} deletedCount={4} binTotal={4} projects={[]} tab="vector" onChange={nothing} onEmptyBin={nothing} onManageProjects={nothing} />);
    expect(html).toContain("Deleted<span class=\"filter-count\"> 4</span>");
    expect(html).toContain("Empty bin");
  });

  it("says what an empty Vector list means in each view, naming the tab for a project", () => {
    const empty = (filter = NO_FILTER, project: Project | null = null) =>
      renderToStaticMarkup(<EmptyHistory kind="vector" filter={filter} binDays={30} project={project} onShowAll={nothing} onShowAnyProject={nothing} />);
    expect(empty()).toContain("No vectors yet");
    expect(empty()).toContain("Describe your idea above and press Make vector. Every SVG lands here with its settings and its layers.");
    expect(empty(ONLY_KEPT)).toContain("No kept vectors yet");
    expect(empty(ONLY_DELETED)).toContain("No deleted vectors");
    expect(empty(ONLY_DELETED)).toContain("Runs you delete stay here for 30 days before they are gone for good.");
    expect(empty(withProject(NO_FILTER, PROJECTS[0].id), PROJECTS[0])).toContain("Nothing in “Logo” on Vector yet");
    expect(empty(withProject(ONLY_DELETED, PROJECTS[0].id), PROJECTS[0])).toContain("Nothing deleted from “Logo” on Vector");
  });
});

describe("the questions about a Vector run", () => {
  const run = makeVectorRun({ options: { ...makeVectorRun().options, num_images: 2 }, vectors: [makeVectorItem(0), makeVectorItem(1)] });

  it("Delete: a run with several versions says that all of them go; a run with one is asked as it always was", () => {
    render(<ConfirmDelete run={run} binDays={30} projectName={null} onCancel={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("alertdialog").textContent).toContain(
      "It has 2 versions. All of them move to Deleted and stay there for 30 days; you can restore the run from there. After that they are gone for good.");
    cleanup();
    render(<ConfirmDelete run={makeVectorRun()} binDays={30} projectName={null} onCancel={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("alertdialog").textContent).toContain("It moves to Deleted and stays there for 30 days; you can restore it from there.");
    expect(screen.getByRole("alertdialog").textContent).not.toContain("All of them");
  });

  it("Delete: without a bin it says its versions are removed, and in the bin it says it is for good", () => {
    render(<ConfirmDelete run={run} binDays={0} projectName={null} onCancel={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("alertdialog").textContent).toContain("Its 2 versions are removed from the Spark.");
    cleanup();
    render(<ConfirmDelete run={makeVectorRun({ deleted_at: "2026-10-10T00:00:00.000Z" })} binDays={30} projectName={null} onCancel={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("alertdialog").textContent).toContain("Its version is removed from the Spark.");
    expect(screen.getByRole("button", { name: "Delete forever" })).toBeTruthy();
  });

  it("Cancel names the work for each kind of run, so that the Vector words did not change the others", () => {
    render(<ConfirmCancel run={makeRun({ status: "running" })} onBack={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("button", { name: "Stop generating" })).toBeTruthy();
    cleanup();
    render(<ConfirmCancel run={makeMusicRun({ status: "running" })} onBack={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("button", { name: "Stop making music" })).toBeTruthy();
  });

  it("Cancel: says how many versions are kept, and the button names the work", () => {
    render(<ConfirmCancel run={{ ...run, status: "running" }} onBack={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("alertdialog").textContent).toContain("The 2 versions already finished are kept. The one being made now is discarded.");
    expect(screen.getByRole("button", { name: "Stop making the vector" })).toBeTruthy();
    cleanup();
    render(<ConfirmCancel run={{ ...run, status: "running", vectors: [] }} onBack={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("alertdialog").textContent).toContain("Nothing has been finished yet, so nothing is kept. The version being made now is discarded.");
  });
});

describe("a default form saved by the test helper reads back as it was", () => {
  it("keeps the helper honest: saveVectorForm and the panel agree on the key", () => {
    const store = memoryStore();
    saveVectorForm({ ...defaultVectorForm(CAPS), idea: "from a helper" }, store);
    render(<VectorPanel {...panelProps({ store })} />);
    expect(idea().value).toBe("from a helper");
  });
});
