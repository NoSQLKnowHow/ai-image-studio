import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BinnedItemsCard } from "./components/BinnedItemsCard";
import { StackView } from "./components/StackView";
import { LayerTable } from "./components/VersionFacts";
import { VectorViewer } from "./components/VectorViewer";
import { makeBinnedVector, makeVectorItem, makeVectorRun } from "./testdata";
import type { VectorItem, VectorRun } from "./types";
import { layerHeight, stackCaption } from "./vector";

// The Vector viewer, the stack view, the Height column and the card of deleted versions (DESIGN.md §35.2, criteria 186 to 204), in the browser stand-in.
// The card of a run and the form are in vector.test.tsx; what is here is what opens from a preview, and the Deleted view's card of versions.

afterEach(() => cleanup());

const RANGE = { min: 16, max: 8192 };

/** A version made Stacked (depth): three layers whose colours stand 1, 2 and 3 sheets high, the biggest ink at the back. */
function stackedItem(idx = 0, overrides: Partial<VectorItem> = {}): VectorItem {
  const base = makeVectorItem(idx, overrides);
  const heights = [1, 2, 3];
  return {
    ...base,
    check: { ...base.check, arrangement: "stacked", stack_order: "area_desc", layers: base.check.layers.map((layer, i) => ({ ...layer, height: heights[i] })) },
  };
}

function viewerProps(run: VectorRun | null, over: Partial<ComponentProps<typeof VectorViewer>> = {}): ComponentProps<typeof VectorViewer> {
  return { run, index: 0, readOnly: false, pngRange: RANGE, notice: null, onIndex: vi.fn(), onCopySvg: vi.fn(), onDeleteVersion: vi.fn(), onClose: vi.fn(), ...over };
}
const dialog = () => screen.getByRole("dialog");
const action = (name: string) => dialog().querySelector(`[data-action="${name}"]`) as HTMLElement | null;
const title = () => dialog().querySelector("[data-viewer-title]")?.textContent;
const three = () => makeVectorRun({ options: { ...makeVectorRun().options, num_images: 3 }, vectors: [0, 1, 2].map((i) => makeVectorItem(i, { id: `viewer${i}`.padEnd(32, "0") })) });

describe("the viewer of a Vector run's versions", () => {
  it("is a dialog titled with the version's place, seed, size, layers and weight (a lone version is named by its seed)", () => {
    render(<VectorViewer {...viewerProps(makeVectorRun())} />);
    expect(title()).toBe("Seed 1234 · 100 × 100 mm · 3 layers · 18.4 KB");
    cleanup();
    render(<VectorViewer {...viewerProps(three(), { index: 1 })} />);
    expect(title()).toBe("Version 2 of 3 · seed 1235 · 100 × 100 mm · 3 layers · 18.4 KB");
  });

  it("shows nothing when there is no run, or the place is past the last version", () => {
    render(<VectorViewer {...viewerProps(null)} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    cleanup();
    render(<VectorViewer {...viewerProps(makeVectorRun(), { index: 5 })} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the drawing beside the vector, each in its own panel, with the drawing's size and a way to download it", () => {
    const run = makeVectorRun();
    const item = run.vectors[0];
    render(<VectorViewer {...viewerProps(run)} />);
    const drawing = dialog().querySelector('[data-panel="drawing"]') as HTMLElement;
    const vector = dialog().querySelector('[data-panel="vector"]') as HTMLElement;
    expect(within(drawing).getByText("The drawing")).toBeTruthy();
    expect(within(drawing).getByRole("img").getAttribute("src")).toBe(item.drawing_url);
    expect(within(drawing).getByText("What Qwen-Image drew, 1536 × 1536 px")).toBeTruthy();
    const download = within(drawing).getByRole("link", { name: /Download drawing/ });
    expect(download.getAttribute("href")).toBe(item.drawing_download_url);
    expect(download.hasAttribute("download")).toBe(true);
    expect(within(vector).getByText("The vector")).toBeTruthy();
    expect(within(vector).getByRole("img").getAttribute("src")).toBe(item.url);
    expect(within(vector).getByText("100 × 100 mm, 18.4 KB")).toBeTruthy();
    // in the document order the drawing is first: on a phone, where they stack, the drawing is on top
    expect(drawing.compareDocumentPosition(vector) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("reserves each picture's own shape, so that the two are the same size when the sheet is square and the vector keeps the sheet's shape when it is not", () => {
    const run = makeVectorRun({ vectors: [makeVectorItem(0, { width_mm: 600, height_mm: 307 })] });
    render(<VectorViewer {...viewerProps(run)} />);
    const frames = Array.from(dialog().querySelectorAll(".vv-frame")) as HTMLElement[];
    expect(frames.map((f) => f.style.aspectRatio)).toEqual(["1536 / 1536", "600 / 307"]);
    expect(frames[1].className).toContain("white"); // the vector is on white, the material
  });

  it("says the drawing is no longer on the disk when its file is gone, and changes nothing else", () => {
    render(<VectorViewer {...viewerProps(makeVectorRun())} />);
    const drawing = dialog().querySelector('[data-panel="drawing"]') as HTMLElement;
    fireEvent.error(within(drawing).getByRole("img"));
    expect(within(drawing).getByRole("status").textContent).toBe("The drawing is no longer on the disk.");
    expect(within(drawing).queryByRole("img")).toBeNull();
    expect(action("download-drawing")).toBeNull(); // nothing to download
    expect(dialog().querySelector('[data-panel="vector"] img')).not.toBeNull();
    expect(action("download-svg")).not.toBeNull();
  });

  it("puts the SVG only in <img>s: nothing in the dialog could run a file", () => {
    render(<VectorViewer {...viewerProps(makeVectorRun({ vectors: [stackedItem()] }))} />);
    expect(dialog().querySelectorAll("object, embed, iframe, script, [srcdoc]")).toHaveLength(0);
    const withSrc = Array.from(dialog().querySelectorAll("[src]"));
    expect(withSrc.length).toBeGreaterThan(2);
    expect(withSrc.every((element) => element.tagName === "IMG")).toBe(true);
    expect(dialog().innerHTML).not.toContain("<svg xmlns");
  });

  it("has the facts of the version: the colours line, the layer table, the smallest feature and every note", () => {
    render(<VectorViewer {...viewerProps(makeVectorRun())} />);
    expect(dialog().querySelector("[data-vector-colours]")?.textContent).toBe("4 colours found, 4 asked. White, the bare material, is one of them and has no layer.");
    expect(within(dialog()).getByRole("table")).toBeTruthy();
    expect(dialog().querySelector("[data-vector-feature]")?.textContent).toBe("Smallest feature: 0.62 mm (your limit is 0.5 mm).");
    expect(within(dialog()).getByRole("list", { name: "Notes" }).textContent).toContain("5 specks smaller than 0.5 mm were removed.");
  });

  it("has the three downloads of the version with the server's own links, and Copy SVG code hands the version to the page", () => {
    const props = viewerProps(makeVectorRun());
    render(<VectorViewer {...props} />);
    const item = props.run!.vectors[0];
    expect(action("download-svg")?.getAttribute("href")).toBe(item.download_url);
    expect(action("download-zip")?.getAttribute("href")).toBe(item.zip_url);
    fireEvent.click(action("copy-svg") as HTMLElement);
    expect(props.onCopySvg).toHaveBeenCalledWith(item);
  });

  it("has one PNG size box for the viewer, starting at 2048, whose link follows it, switched off for a size the server would refuse", () => {
    const run = three();
    const props = viewerProps(run);
    const { rerender } = render(<VectorViewer {...props} />);
    const boxNow = () => within(dialog()).getByLabelText("PNG size, long side") as HTMLInputElement;
    const box = boxNow();
    expect(within(dialog()).getAllByLabelText("PNG size, long side")).toHaveLength(1);
    expect(box.value).toBe("2048");
    expect(action("download-png")?.getAttribute("href")).toBe(`${run.vectors[0].png_download_url}&size=2048`);
    fireEvent.change(box, { target: { value: "512" } });
    expect(action("download-png")?.getAttribute("href")).toBe(`${run.vectors[0].png_download_url}&size=512`);
    // the size is the viewer's, not the version's: it stays when the viewer moves to the next version
    rerender(<VectorViewer {...props} index={2} />);
    expect(boxNow().value).toBe("512"); // (the box is made again for each version, so it is looked for again)
    expect(action("download-png")?.getAttribute("href")).toBe(`${run.vectors[2].png_download_url}&size=512`);
    for (const bad of ["15", "8193", "100.5", "", "abc", "-4"]) {
      fireEvent.change(boxNow(), { target: { value: bad } });
      expect(action("download-png")?.getAttribute("href"), `"${bad}"`).toBeNull();
      expect(action("download-png")?.getAttribute("aria-disabled")).toBe("true");
      expect(boxNow().getAttribute("aria-invalid")).toBe("true");
      expect(within(dialog()).getByRole("alert").textContent).toBe("Enter a whole number from 16 to 8192.");
    }
    fireEvent.change(boxNow(), { target: { value: "8192" } });
    expect(within(dialog()).queryByRole("alert")).toBeNull();
  });

  it("describes the vector for a screen reader, with the layers counted in the singular or the plural", () => {
    render(<VectorViewer {...viewerProps(makeVectorRun())} />);
    expect(dialog().querySelector("[data-vector-image]")?.getAttribute("alt")).toBe("The vector: 100 by 100 mm, 3 layers");
    cleanup();
    render(<VectorViewer {...viewerProps(makeVectorRun({ vectors: [makeVectorItem(0, { layers: 1, width_mm: 60.5, height_mm: 40 })] }))} />);
    expect(dialog().querySelector("[data-vector-image]")?.getAttribute("alt")).toBe("The vector: 60.5 by 40 mm, 1 layer");
  });

  it("says what the PNG is when the size is right, and says it as an error (and only then) when it is not; both ends of the range are right", () => {
    render(<VectorViewer {...viewerProps(makeVectorRun())} />);
    const help = () => dialog().querySelector('[id$="-png-help"]') as HTMLElement;
    expect(help().textContent).toBe("Drawn from the SVG, so it is sharp at any size.");
    expect(help().className).toBe("field-hint");
    expect(help().hasAttribute("role")).toBe(false);
    const box = within(dialog()).getByLabelText("PNG size, long side");
    fireEvent.change(box, { target: { value: "15" } });
    expect(help().className).toBe("field-error");
    expect(help().getAttribute("role")).toBe("alert");
    fireEvent.change(box, { target: { value: "16" } }); // the lowest the server takes
    expect(within(dialog()).queryByRole("alert")).toBeNull();
    expect(help().className).toBe("field-hint");
    expect(action("download-png")?.getAttribute("href")).toContain("&size=16");
    expect(dialog().querySelector(".vector-png-row span[aria-hidden='true']")?.textContent).toBe("px"); // the unit is not read out after the number
  });

  it("moves with the arrow keys between two versions, and does nothing for a lone one", () => {
    const two = makeVectorRun({ options: { ...makeVectorRun().options, num_images: 2 }, vectors: [makeVectorItem(0), makeVectorItem(1, { id: "two".padEnd(32, "0") })] });
    const props = viewerProps(two);
    render(<VectorViewer {...props} />);
    fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(props.onIndex).toHaveBeenLastCalledWith(1);
    fireEvent.keyDown(document, { key: "ArrowLeft" });
    expect(props.onIndex).toHaveBeenLastCalledWith(1); // from the first, back goes round to the second
    cleanup();
    const lone = viewerProps(makeVectorRun());
    render(<VectorViewer {...lone} />);
    fireEvent.keyDown(document, { key: "ArrowRight" });
    fireEvent.keyDown(document, { key: "ArrowLeft" });
    expect(lone.onIndex).not.toHaveBeenCalled();
  });

  it("takes the PNG range from what it is given (the server's capabilities)", () => {
    render(<VectorViewer {...viewerProps(makeVectorRun(), { pngRange: { min: 100, max: 3000 } })} />);
    const box = within(dialog()).getByLabelText("PNG size, long side");
    fireEvent.change(box, { target: { value: "3001" } });
    expect(within(dialog()).getByRole("alert").textContent).toBe("Enter a whole number from 100 to 3000.");
    fireEvent.change(box, { target: { value: "3000" } });
    expect(within(dialog()).queryByRole("alert")).toBeNull();
  });

  describe("Delete version", () => {
    it("is in the viewer, and hands the version, its place among those the run has now and how many there are to the page", () => {
      const run = three();
      const props = viewerProps(run, { index: 1 });
      render(<VectorViewer {...props} />);
      const button = action("delete-version") as HTMLElement;
      expect(button.textContent).toContain("Delete version");
      expect(button.getAttribute("title")).toBe("Delete this version. It goes to Deleted for a while, and can be restored from there.");
      fireEvent.click(button);
      expect(props.onDeleteVersion).toHaveBeenCalledWith(run.vectors[1], 2, 3);
    });

    it("is not offered when the viewer is read-only (a version in the bin, or a run in the bin), nor while the run is still working", () => {
      const inTheBin = { ...three(), deleted_at: "2026-10-14T09:30:00.000Z", purge_at: "2026-11-13T09:30:00.000Z" };
      render(<VectorViewer {...viewerProps(inTheBin, { readOnly: true })} />);
      expect(action("delete-version")).toBeNull();
      expect(action("download-svg")).not.toBeNull(); // downloads only read
      cleanup();
      render(<VectorViewer {...viewerProps({ ...three(), binned_vectors: [makeBinnedVector(3)] }, { readOnly: true })} />);
      expect(action("delete-version")).toBeNull();
      cleanup();
      render(<VectorViewer {...viewerProps({ ...three(), status: "running" })} />);
      expect(action("delete-version")).toBeNull();
    });
  });

  describe("moving between versions", () => {
    it("has Previous and Next for several versions, going round at the ends, and none for one", () => {
      const props = viewerProps(three(), { index: 2 });
      render(<VectorViewer {...props} />);
      fireEvent.click(screen.getByRole("button", { name: "Next version" }));
      expect(props.onIndex).toHaveBeenLastCalledWith(0);
      fireEvent.click(screen.getByRole("button", { name: "Previous version" }));
      expect(props.onIndex).toHaveBeenLastCalledWith(1);
      cleanup();
      render(<VectorViewer {...viewerProps(makeVectorRun())} />);
      expect(screen.queryByRole("button", { name: "Next version" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Previous version" })).toBeNull();
    });

    it("moves with the arrow keys, but not when the key is meant for a box (the PNG size, the Sheets shown range)", () => {
      const props = viewerProps(three(), { index: 0 });
      render(<VectorViewer {...props} />);
      fireEvent.keyDown(document, { key: "ArrowLeft" });
      expect(props.onIndex).toHaveBeenLastCalledWith(2);
      fireEvent.keyDown(document, { key: "ArrowRight" });
      expect(props.onIndex).toHaveBeenLastCalledWith(1);
      (props.onIndex as ReturnType<typeof vi.fn>).mockClear();
      fireEvent.keyDown(within(dialog()).getByLabelText("PNG size, long side"), { key: "ArrowRight" });
      expect(props.onIndex).not.toHaveBeenCalled();
    });

    it("closes with Escape", () => {
      const props = viewerProps(makeVectorRun());
      render(<VectorViewer {...props} />);
      fireEvent.keyDown(dialog(), { key: "Escape" });
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });
  });

  it("says inside the viewer how a request made from it went (the page behind a modal is hidden from screen readers)", () => {
    render(<VectorViewer {...viewerProps(makeVectorRun(), { notice: { kind: "info", text: "SVG code copied." } })} />);
    const region = dialog().querySelector(".lightbox-notice-region") as HTMLElement;
    expect(region.getAttribute("role")).toBe("status");
    expect(region.textContent).toBe("SVG code copied.");
    expect(region.querySelector(".error")).toBeNull();
    cleanup();
    render(<VectorViewer {...viewerProps(makeVectorRun(), { notice: { kind: "error", text: "Couldn't copy. Use Download SVG instead." } })} />);
    expect(dialog().querySelector(".lightbox-notice.error")?.textContent).toBe("Couldn't copy. Use Download SVG instead.");
  });

  describe("on the versions in the bin (the Deleted view, DESIGN.md §35.2)", () => {
    const run = makeVectorRun({ vectors: [makeVectorItem(0)], binned_vectors: [makeBinnedVector(1, { id: "bin1".padEnd(32, "0") }), makeBinnedVector(2, { id: "bin2".padEnd(32, "0") })] });

    it("pages through the deleted versions, names each as deleted by its own place and seed, and reads the files that are still there", () => {
      render(<VectorViewer {...viewerProps(run, { readOnly: true, index: 1 })} />);
      expect(title()).toBe("Version 3 · seed 1236 · deleted");
      expect(dialog().querySelector('[data-panel="drawing"] img')?.getAttribute("src")).toBe(run.binned_vectors[1].drawing_url);
      expect(dialog().querySelector('[data-panel="vector"] img')?.getAttribute("src")).toBe(run.binned_vectors[1].url);
      expect(action("delete-version")).toBeNull();
      for (const name of ["download-svg", "download-zip", "copy-svg", "download-png", "download-drawing"]) expect(action(name), name).not.toBeNull();
      expect(screen.getByRole("button", { name: "Next version" })).toBeTruthy(); // two deleted versions: it pages between them, not to the live one
    });

    it("looks at the versions the run has when the run itself is in the bin (it does not list versions deleted before it)", () => {
      const binnedRun = { ...run, deleted_at: "2026-10-14T09:30:00.000Z", purge_at: "2026-11-13T09:30:00.000Z", binned_vectors: [] };
      render(<VectorViewer {...viewerProps(binnedRun, { readOnly: true })} />);
      expect(title()).toBe("Seed 1234 · 100 × 100 mm · 3 layers · 18.4 KB");
      expect(action("delete-version")).toBeNull();
    });
  });

  describe("a version made Stacked (depth)", () => {
    it("shows the Height column and the stack view; a side by side version shows neither", () => {
      render(<VectorViewer {...viewerProps(makeVectorRun({ vectors: [stackedItem()] }))} />);
      expect(within(dialog()).getByRole("columnheader", { name: "Height" })).toBeTruthy();
      expect(dialog().querySelector("[data-stack-view]")).not.toBeNull();
      cleanup();
      render(<VectorViewer {...viewerProps(makeVectorRun())} />);
      expect(within(dialog()).queryByRole("columnheader", { name: "Height" })).toBeNull();
      expect(dialog().querySelector("[data-stack-view]")).toBeNull();
    });

    it("starts the next version with all its sheets again", () => {
      const run = makeVectorRun({ options: { ...makeVectorRun().options, num_images: 2 }, vectors: [stackedItem(0), stackedItem(1)] });
      const props = viewerProps(run);
      const { rerender } = render(<VectorViewer {...props} />);
      fireEvent.change(dialog().querySelector("[data-stack-shown]") as HTMLInputElement, { target: { value: "1" } });
      expect(dialog().querySelectorAll(".stack-sheet")).toHaveLength(1);
      rerender(<VectorViewer {...props} index={1} />);
      expect(dialog().querySelectorAll(".stack-sheet")).toHaveLength(3);
    });

    it("shows the stack view for a stacked version of a single layer too: one sheet, the back one", () => {
      const base = stackedItem();
      const one = { ...base, layers: 1, layer_urls: base.layer_urls.slice(0, 1), check: { ...base.check, layers: base.check.layers.slice(0, 1) } };
      render(<VectorViewer {...viewerProps(makeVectorRun({ vectors: [one] }))} />);
      expect(dialog().querySelector("[data-stack-view]")).not.toBeNull();
      expect(dialog().querySelectorAll(".stack-sheet")).toHaveLength(1);
    });

    it("shows no stack view for a version whose layer files cannot be offered", () => {
      render(<VectorViewer {...viewerProps(makeVectorRun({ vectors: [{ ...stackedItem(), layer_urls: [] }] }))} />);
      expect(dialog().querySelector("[data-stack-view]")).toBeNull();
    });
  });
});

describe("the layer table's Height column (DESIGN.md §35.2)", () => {
  it("has a fourth column with how many sheets high each colour stands, for a stacked version only", () => {
    render(<LayerTable item={stackedItem()} />);
    expect(within(screen.getByRole("table")).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Layer", "Paths", "Cut length", "Height"]);
    expect(Array.from(document.querySelectorAll("td[data-height]")).map((td) => td.textContent)).toEqual(["1", "2", "3"]);
    cleanup();
    render(<LayerTable item={makeVectorItem(0)} />);
    expect(within(screen.getByRole("table")).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Layer", "Paths", "Cut length"]);
    expect(document.querySelectorAll("td[data-height]")).toHaveLength(0);
  });

  it("reads a version made before 1.18, which has no heights, as each layer standing as high as its number", () => {
    const old = stackedItem();
    const layers = old.check.layers.map(({ height: _height, ...layer }) => layer) as unknown as VectorItem["check"]["layers"];
    render(<LayerTable item={{ ...old, check: { ...old.check, layers, stack_order: undefined as never } }} />);
    expect(Array.from(document.querySelectorAll("td[data-height]")).map((td) => td.textContent)).toEqual(["1", "2", "3"]);
    expect(layerHeight({ index: 3 }, { arrangement: "stacked" })).toBe(3);
    expect(layerHeight({ index: 3 }, { arrangement: "side_by_side" })).toBe(1);
  });
});

describe("the stack view (DESIGN.md §35.2)", () => {
  const item = stackedItem();
  const sheets = () => Array.from(document.querySelectorAll(".stack-sheet")) as HTMLElement[];
  const slider = () => document.querySelector("[data-stack-shown]") as HTMLInputElement;

  it("draws each layer's own SVG as a sheet, back to front, each a step further up and to the right than the one behind it", () => {
    render(<StackView item={item} />);
    expect(sheets().map((s) => s.querySelector("img")?.getAttribute("src"))).toEqual(item.layer_urls);
    expect(sheets().map((s) => s.style.zIndex)).toEqual(["1", "2", "3"]); // the back sheet is lowest
    expect(sheets().map((s) => s.style.transform)).toEqual([
      "translate(calc(0 * var(--step)), calc(0 * var(--step) * -1))",
      "translate(calc(1 * var(--step)), calc(1 * var(--step) * -1))",
      "translate(calc(2 * var(--step)), calc(2 * var(--step) * -1))",
    ]);
    expect((document.querySelector(".stack-frame") as HTMLElement).style.aspectRatio).toBe("100 / 100");
    expect((document.querySelector(".stack-stage") as HTMLElement).style.getPropertyValue("--sheets")).toBe("3"); // room for every sheet's offset
  });

  it("puts the sheets in <img>s only, so that a file from a model never runs as part of the page", () => {
    const { container } = render(<StackView item={item} />);
    expect(container.querySelectorAll("object, embed, iframe, script, [srcdoc]")).toHaveLength(0);
    expect(Array.from(container.querySelectorAll("[src]")).every((e) => e.tagName === "IMG")).toBe(true);
  });

  it("says in words, for each sheet, its number, its colour and how high it stands, with back and front named", () => {
    render(<StackView item={item} />);
    const captions = Array.from(document.querySelectorAll("[data-sheet-caption]")).map((li) => li.textContent?.trim());
    expect(captions).toEqual(["Layer 1, black, 1 high (back)", "Layer 2, red, 2 high", "Layer 3, green, 3 high (front)"]);
    expect(sheets().map((s) => s.querySelector("img")?.getAttribute("alt"))).toEqual(captions);
    expect(stackCaption(item.check.layers[0], item.check, 3)).toBe("Layer 1, black, 1 high (back)");
  });

  it("peels the stack from the front with Sheets shown, one sheet at a time, and says how many of how many", () => {
    render(<StackView item={item} />);
    expect(screen.getByLabelText("Sheets shown")).toBe(slider());
    expect([slider().min, slider().max, slider().value]).toEqual(["1", "3", "3"]);
    expect(document.querySelector("[data-stack-shown-text]")?.textContent).toBe("3 of 3");
    fireEvent.change(slider(), { target: { value: "2" } });
    expect(sheets()).toHaveLength(2);
    expect(sheets().map((s) => s.getAttribute("data-sheet"))).toEqual(["1", "2"]); // the front one went
    expect(document.querySelector("[data-stack-shown-text]")?.textContent).toBe("2 of 3");
    expect(slider().getAttribute("aria-valuetext")).toBe("2 of 3");
    expect(screen.getByRole("group", { name: "2 of 3 sheets shown" })).toBeTruthy();
    // the list still names every sheet, the hidden one marked so
    expect(Array.from(document.querySelectorAll("[data-sheet-caption]")).map((li) => li.className)).toEqual(["", "", "hidden-sheet"]);
    fireEvent.change(slider(), { target: { value: "1" } });
    expect(sheets()).toHaveLength(1);
    fireEvent.change(slider(), { target: { value: "3" } });
    expect(sheets()).toHaveLength(3);
  });

  it("names a sheet by its number when the check lists fewer layers than there are sheets (a file that disagrees with its check)", () => {
    const short = { ...item, check: { ...item.check, layers: item.check.layers.slice(0, 2) } };
    render(<StackView item={short} />);
    expect(sheets().map((s) => s.querySelector("img")?.getAttribute("alt"))).toEqual(["Layer 1, black, 1 high (back)", "Layer 2, red, 2 high", "Layer 3"]);
  });

  it("names its sheets by their own colour word, and by the colour's code where the name has none (layers by shape)", () => {
    expect(stackCaption({ ...item.check.layers[1], name: "Piece 2" }, item.check, 3)).toBe("Layer 2, #FF0000, 2 high");
  });
});

describe("the card of deleted versions in the Deleted view (DESIGN.md §35.2)", () => {
  const now = Date.parse("2026-10-15T00:00:00.000Z");
  const run = makeVectorRun({ vectors: [makeVectorItem(0)], binned_vectors: [makeBinnedVector(1, { seed: 41 }), makeBinnedVector(2, { seed: 42, id: "bin2".padEnd(32, "0") })] });
  const handlers = () => ({ onOpen: vi.fn(), onRestore: vi.fn(), onDeleteForever: vi.fn() });

  it("is one card for the run, with the run's prompt, and each deleted version with its preview, place, seed, size and layers", () => {
    render(<BinnedItemsCard run={run} now={now} projectName={null} handlers={handlers()} />);
    const card = document.querySelector("article") as HTMLElement;
    expect(card.getAttribute("data-card")).toBe("binned-items");
    expect(card.querySelector(".badge-deleted")?.textContent?.trim()).toBe("2 deleted versions");
    expect(card.querySelector(".run-prompt")?.textContent).toBe("a fox head");
    const rows = Array.from(card.querySelectorAll("li.binned-item"));
    expect(rows.map((li) => li.querySelector("strong")?.textContent)).toEqual(["Version 2 · seed 41", "Version 3 · seed 42"]);
    expect(rows.map((li) => li.querySelector(".binned-text span")?.textContent)).toEqual(["100 × 100 mm · 3 layers", "100 × 100 mm · 3 layers"]);
    expect(rows.map((li) => li.querySelector("img")?.getAttribute("src"))).toEqual([run.binned_vectors[0].preview_url, run.binned_vectors[1].preview_url]);
    expect(card.querySelector("[data-note=in-bin]")?.textContent).toMatch(/^In the bin since .*\. It will be deleted for good around .*, in 29 days\.$/);
  });

  it("says one deleted version in the singular", () => {
    render(<BinnedItemsCard run={{ ...run, binned_vectors: [run.binned_vectors[0]] }} now={now} projectName={null} handlers={handlers()} />);
    expect(document.querySelector(".badge-deleted")?.textContent?.trim()).toBe("1 deleted version");
  });

  it("opens a deleted version in the viewer by its place in the list, restores it, and deletes it for good (the page asks before that)", () => {
    const h = handlers();
    render(<BinnedItemsCard run={run} now={now} projectName={null} handlers={h} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Version 3 · seed 42" }));
    expect(h.onOpen).toHaveBeenCalledWith(run, 1);
    fireEvent.click(screen.getByRole("button", { name: "Restore Version 2 · seed 41" }));
    expect(h.onRestore).toHaveBeenCalledWith(run, run.binned_vectors[0]);
    fireEvent.click(screen.getByRole("button", { name: "Delete Version 3 · seed 42 for good" }));
    expect(h.onDeleteForever).toHaveBeenCalledWith(run, run.binned_vectors[1]);
  });

  it("shows the project the run is filed in, as the run's own card does", () => {
    render(<BinnedItemsCard run={{ ...run, project_id: "a".repeat(32) }} now={now} projectName="Logo" handlers={handlers()} />);
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Logo");
  });
});
