import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunCard } from "./components/RunCard";
import { TrackCard } from "./components/TrackCard";
import { VectorCard } from "./components/VectorCard";
import { PromptLinksContext, RefineButton, RunFrom, type PromptLinks } from "./promptLinks";
import { makeImage, makeMusicRun, makeRun, makeVectorRun } from "./testdata";
import type { PromptRef } from "./types";

afterEach(cleanup);

// Where a run came from, and Refine in Prompt builder, on the cards (DESIGN.md §37.2 "On the Images tab", §37.3 rule 3; criteria 237, 241).

const ref = (over: Partial<PromptRef> = {}): PromptRef => ({ id: "p1", title: "Lighthouse", version: 3, deleted: false, edited: false, ...over });
const links = (over: Partial<PromptLinks> = {}): PromptLinks => ({ open: vi.fn(), refine: vi.fn(), ...over });
const within = (value: PromptLinks | null, node: React.ReactNode) => render(<PromptLinksContext.Provider value={value}>{node}</PromptLinksContext.Provider>);

describe("the line on a run made from a prompt", () => {
  it("says which prompt and version, and opens the prompt", () => {
    const l = links();
    within(l, <RunFrom run={makeRun({ prompt_ref: ref() })} />);
    fireEvent.click(screen.getByRole("button", { name: 'From "Lighthouse" v3' }));
    expect(l.open).toHaveBeenCalledWith("p1");
  });

  it("says edited when the words sent are not the version's, and deleted when the prompt is in the bin, and can still be opened", () => {
    within(links(), <RunFrom run={makeRun({ prompt_ref: ref({ edited: true }) })} />);
    expect(screen.getByRole("button").textContent).toBe('From "Lighthouse" v3, edited');
    cleanup();
    within(links(), <RunFrom run={makeRun({ prompt_ref: ref({ deleted: true }) })} />);
    expect(screen.getByRole("button").textContent).toBe('From "Lighthouse" v3, deleted');
    cleanup();
    within(links(), <RunFrom run={makeRun({ prompt_ref: ref({ deleted: true, edited: true }) })} />);
    expect(screen.getByRole("button").textContent).toBe('From "Lighthouse" v3, edited, deleted');
  });

  it("for a prompt deleted for good says so, as plain text with nothing to open", () => {
    within(links(), <RunFrom run={makeRun({ prompt_ref: ref({ title: null, deleted: true }) })} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector("[data-note=from-prompt]")?.textContent).toBe("From a deleted prompt, v3");
  });

  it("is plain text where the page gave no way to open a prompt", () => {
    within(null, <RunFrom run={makeRun({ prompt_ref: ref() })} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector("[data-note=from-prompt]")?.textContent).toBe('From "Lighthouse" v3');
  });

  it("is not there for a run made by hand", () => {
    within(links(), <RunFrom run={makeRun({ prompt_ref: null })} />);
    expect(document.querySelector("[data-note=from-prompt]")).toBeNull();
  });
});

describe("Refine in Prompt builder", () => {
  const done = makeRun({ status: "done", images: [makeImage(0)] });

  it("hands the page the run and the picture", () => {
    const l = links();
    within(l, <RefineButton run={done} image={done.images[0]} />);
    fireEvent.click(screen.getByRole("button", { name: "Refine in Prompt builder" }));
    expect(l.refine).toHaveBeenCalledWith(done, done.images[0]);
  });

  it("is not offered when the builder is not set up, for a run that has not finished, or without a picture", () => {
    within(links({ refine: null }), <RefineButton run={done} image={done.images[0]} />);
    expect(screen.queryByRole("button")).toBeNull();
    cleanup();
    within(links(), <RefineButton run={makeRun({ status: "running" })} image={done.images[0]} />);
    expect(screen.queryByRole("button")).toBeNull();
    cleanup();
    within(links(), <RefineButton run={makeRun({ status: "failed" })} image={null} />);
    expect(screen.queryByRole("button")).toBeNull();
    cleanup();
    within(null, <RefineButton run={done} image={done.images[0]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("on the cards", () => {
  const cardProps = (run: ReturnType<typeof makeRun>): ComponentProps<typeof RunCard> => ({
    run, now: Date.parse("2026-10-05T12:00:00.000Z"), workerState: "ready", canEdit: true, making4k: new Set<string>(), enlarging: new Set<string>(), enlargeWaiting: new Set<string>(),
    transient: false, viewScope: "kept", upscaler: null, onReuse: vi.fn(), onRegenerateLarger: vi.fn(), onMake4K: vi.fn(), onEnlarge: vi.fn(), onEditThis: vi.fn(), onRetry: vi.fn(),
    onCancel: vi.fn(), onToggleKeep: vi.fn(), onRestore: vi.fn(), onDelete: vi.fn(), onCopy: vi.fn(), onOpenImage: vi.fn(),
    projects: { projects: [], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null) },
  });

  it("a picture's card says where it came from and has Refine beside Reuse", () => {
    const l = links();
    const run = makeRun({ status: "done", images: [makeImage(0), makeImage(1)], prompt_ref: ref() });
    within(l, <RunCard {...cardProps(run)} />);
    expect(screen.getByRole("button", { name: 'From "Lighthouse" v3' })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Refine in Prompt builder" }));
    expect(l.refine).toHaveBeenCalledWith(run, run.images[0]); // the first picture of the run
  });

  it("a card of a run made by hand has no line, and still has Refine: it works on a picture that was not made from a prompt", () => {
    within(links(), <RunCard {...cardProps(makeRun({ status: "done", images: [makeImage(0)], prompt_ref: null }))} />);
    expect(document.querySelector("[data-note=from-prompt]")).toBeNull();
    expect(screen.getByRole("button", { name: "Refine in Prompt builder" })).toBeTruthy();
  });

  it("a deleted run in the bin has the line but no Refine (nothing there makes a new chat about it)", () => {
    const run = makeRun({ status: "done", images: [makeImage(0)], prompt_ref: ref(), deleted_at: "2026-10-01T00:00:00", purge_at: "2026-10-31T00:00:00" });
    within(links(), <RunCard {...cardProps(run)} />);
    expect(screen.getByRole("button", { name: 'From "Lighthouse" v3' })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Refine in Prompt builder" })).toBeNull();
  });

  it("a track's card and a vector's card say where they came from too", () => {
    const track = makeMusicRun({ prompt_ref: ref({ title: "Sea song", version: 2 }) });
    const props = { run: track, now: Date.now(), workerState: "ready", transient: false, viewScope: "kept", projects: { projects: [], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null) },
      onRestore: vi.fn(), onReuse: vi.fn(), onRetry: vi.fn(), onCancel: vi.fn(), onToggleKeep: vi.fn(), onDelete: vi.fn(), onDeleteTrack: vi.fn(), onCopy: vi.fn() } as unknown as ComponentProps<typeof TrackCard>;
    within(links(), <TrackCard {...props} />);
    expect(screen.getByRole("button", { name: 'From "Sea song" v2' })).toBeTruthy();
    cleanup();
    const vector = makeVectorRun({ id: "v1", prompt_ref: ref({ title: "Fox", version: 4, edited: true }) });
    const vprops: ComponentProps<typeof VectorCard> = {
      run: vector, now: Date.now(), workerState: "ready", transient: false, viewScope: "kept", projects: { projects: [], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null) },
      onOpenVersion: vi.fn(), onReuse: vi.fn(), onRetry: vi.fn(), onCancel: vi.fn(), onToggleKeep: vi.fn(), onRestore: vi.fn(), onDelete: vi.fn(), onCopySvg: vi.fn(),
    };
    const l = links();
    within(l, <VectorCard {...vprops} />);
    fireEvent.click(screen.getByRole("button", { name: 'From "Fox" v4, edited' }));
    expect(l.open).toHaveBeenCalledWith("p1");
  });
});
