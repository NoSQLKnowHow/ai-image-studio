import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BinnedItemsCard } from "./components/BinnedItemsCard";
import { ConfirmDeleteItem, ConfirmEmptyBin, Lightbox, type ItemAsk } from "./components/Dialogs";
import { TrackCard } from "./components/TrackCard";
import {
  binContents, deleteItemQuestion, deleteItemTitle, emptiedText, emptyBinQuestion, itemBinnedText, itemDeletedText, itemName, itemRestoredText, runMadeText,
  type DeleteItemAsk, type ItemRef,
} from "./format";
import { matches, NO_FILTER, ONLY_DELETED } from "./history";
import { makeBinnedImage, makeBinnedTrack, makeImage, makeMusicRun, makeRun, makeTrack } from "./testdata";
import type { ProjectControls } from "./components/ProjectMenu";

// One picture or track of a run in the bin, on its own (DESIGN.md §33): the words, the Delete picture button in the viewer, the question, the card the
// Deleted view shows for a run that has pictures in the bin, and Delete track on a track's row.

afterEach(cleanup);

const nothing = () => undefined;
const picture = (over: Partial<ItemRef> = {}): ItemRef => ({ kind: "image", position: 2, of: 4, seed: 1234, ...over });
const track = (over: Partial<ItemRef> = {}): ItemRef => ({ kind: "track", position: 2, of: 3, seed: 5, ...over });
const ask = (over: Partial<DeleteItemAsk> = {}): DeleteItemAsk => ({ item: picture(), binDays: 30, last: false, forever: false, filed: false, projectName: null, ...over });

// ------------------------------------------------------------------ the words
describe("how a picture or track is named", () => {
  it("says which one of how many, and its seed; a lone one, or one in the bin, is only its seed", () => {
    expect(itemName(picture())).toBe("image 2 of 4, seed 1234");
    expect(itemName(track())).toBe("version 2 of 3, seed 5");
    expect(itemName(picture({ of: 1, position: 1 }))).toBe("seed 1234");
    expect(itemName(picture({ position: null }))).toBe("seed 1234"); // in the bin: it has no place until it is restored
  });

  it("asks 'Delete this picture?', 'Delete this track?', and for good when it is in the bin already", () => {
    expect(deleteItemTitle(picture(), false)).toBe("Delete this picture?");
    expect(deleteItemTitle(track(), false)).toBe("Delete this track?");
    expect(deleteItemTitle(picture(), true)).toBe("Delete this picture for good?");
  });
});

describe("the question before a picture or track is deleted (DESIGN.md §33.1)", () => {
  it("moves to Deleted for the bin's days, and takes its 4K and Enlarge copies with it", () => {
    expect(deleteItemQuestion(ask())).toBe(
      "(image 2 of 4, seed 1234.) It moves to Deleted and stays there for 30 days; you can restore it from there. Its 4K and Enlarge copies go with it.");
    expect(deleteItemQuestion(ask({ binDays: 1 }))).toContain("stays there for 1 day;");
  });

  it("says that the run stays in its project with its other pictures, or tracks", () => {
    expect(deleteItemQuestion(ask({ filed: true, projectName: "Logo" }))).toMatch(/ The run stays in the project “Logo” with its other pictures\.$/);
    expect(deleteItemQuestion(ask({ filed: true, projectName: null }))).toMatch(/ The run stays in a project with its other pictures\.$/);
    expect(deleteItemQuestion(ask({ item: track(), filed: true, projectName: "Logo" }))).toMatch(/ The run stays in the project “Logo” with its other tracks\.$/);
  });

  it("for a track says nothing about 4K copies", () => {
    expect(deleteItemQuestion(ask({ item: track() }))).toBe("(version 2 of 3, seed 5.) It moves to Deleted and stays there for 30 days; you can restore it from there.");
  });

  it("says, for the last one, that the whole run moves to Deleted, and what that means for its project", () => {
    expect(deleteItemQuestion(ask({ item: picture({ of: 1, position: 1 }), last: true }))).toBe(
      "(seed 1234.) This is the last picture of this run, so the whole run moves to Deleted and stays there for 30 days; you can restore it from there.");
    expect(deleteItemQuestion(ask({ last: true, filed: true, projectName: "Logo" }))).toMatch(/ Restoring it puts it back in the project “Logo”\.$/);
    expect(deleteItemQuestion(ask({ item: track(), last: true }))).toContain("This is the last track of this run");
  });

  it("with no bin, says it is deleted for good and what is removed, and the same for the last one", () => {
    expect(deleteItemQuestion(ask({ binDays: 0 }))).toBe(
      "(image 2 of 4, seed 1234.) It is deleted for good: its file, its thumbnail and its 4K and Enlarge copies are removed from the Spark. This can't be undone.");
    expect(deleteItemQuestion(ask({ binDays: 0, item: track() }))).toContain("It is deleted for good: its file is removed from the Spark.");
    expect(deleteItemQuestion(ask({ binDays: 0, last: true, filed: true, projectName: "Logo" }))).toBe(
      "(image 2 of 4, seed 1234.) This is the last picture of this run, so the whole run is deleted for good, with its files. This can't be undone. It is in the project “Logo”, which loses it.");
  });

  it("for one that is in the bin already (Delete forever) says what is removed", () => {
    expect(deleteItemQuestion(ask({ forever: true, item: picture({ position: null }) }))).toBe(
      "(seed 1234.) Its file, its thumbnail and its 4K and Enlarge copies are removed from the Spark. This can't be undone.");
    expect(deleteItemQuestion(ask({ forever: true, item: track({ position: null }) }))).toBe("(seed 5.) Its file is removed from the Spark. This can't be undone.");
  });
});

describe("the toasts about one picture or track", () => {
  const run = { prompt: "a red fox in the snow" };
  it("say which one, of which run, and for how long it stays", () => {
    expect(itemBinnedText(run, picture(), 30)).toBe("Deleted image 2 of “a red fox in the snow”. It stays in Deleted for 30 days.");
    expect(itemBinnedText(run, track(), 1)).toBe("Deleted version 2 of “a red fox in the snow”. It stays in Deleted for 1 day.");
    expect(itemDeletedText(run, picture())).toBe("Deleted image 2 of “a red fox in the snow” for good.");
    expect(itemRestoredText(run, picture({ position: 3 }))).toBe("Restored image 3 of “a red fox in the snow”.");
  });

  it("say 'a picture' when its place is not known", () => {
    expect(itemRestoredText(run, picture({ position: null }))).toBe("Restored a picture of “a red fox in the snow”.");
    expect(itemDeletedText(run, track({ position: null }))).toBe("Deleted a track of “a red fox in the snow” for good.");
  });

  it("shorten a long prompt, as the other toasts do", () => {
    expect(itemBinnedText({ prompt: "x".repeat(200) }, picture(), 30)).toMatch(/^Deleted image 2 of “x{47}…”\./);
  });

  it("give the date a card of deleted pictures is from", () => {
    expect(runMadeText({ created_at: "2026-10-12T10:00:00.000Z" }, { locale: "en-GB", timeZone: "UTC" })).toBe("From a run made on 12 Oct");
  });
});

describe("what Empty bin says (DESIGN.md §30.1, §33.1)", () => {
  const bin = (image: [number, number], music: [number, number]) => ({ image: { runs: image[0], items: image[1] }, music: { runs: music[0], items: music[1] } });

  it("is the question it always was when only runs are in the bin", () => {
    expect(emptyBinQuestion(bin([3, 0], [1, 0]), false)).toBe("Delete 4 runs for good (3 on Images, 1 on Music), with their files. This can't be undone.");
    expect(emptyBinQuestion(bin([1, 0], [0, 0]), false)).toBe("Delete 1 run for good (1 on Images), with their files. This can't be undone.");
  });

  it("says how many runs and how many pictures, and what is on each tab", () => {
    expect(emptyBinQuestion(bin([3, 2], [1, 0]), false)).toBe(
      "Delete 4 runs and 2 pictures for good (3 runs and 2 pictures on Images, 1 run on Music), with their files. This can't be undone.");
    expect(emptyBinQuestion(bin([0, 1], [0, 0]), false)).toBe("Delete 1 picture for good (1 picture on Images), with their files. This can't be undone.");
  });

  it("says tracks for the Music tab, and lists runs, pictures and tracks together when there are all three", () => {
    expect(emptyBinQuestion(bin([0, 0], [0, 3]), false)).toContain("Delete 3 tracks for good (3 tracks on Music)");
    expect(emptyBinQuestion(bin([1, 2], [0, 1]), false)).toBe(
      "Delete 1 run, 2 pictures and 1 track for good (1 run and 2 pictures on Images, 1 track on Music), with their files. This can't be undone.");
  });

  it("says that it is the whole bin when a project is chosen", () => {
    expect(emptyBinQuestion(bin([1, 1], [0, 0]), true)).toMatch(/ This is the whole bin, not only the project you are looking at\.$/);
    expect(emptyBinQuestion(bin([1, 1], [0, 0]), false)).not.toContain("whole bin");
  });

  it("splits the server's count of things into runs and pictures, per tab", () => {
    expect(binContents({ image: { deleted: 5, deleted_items: 2 }, music: { deleted: 1, deleted_items: 1 } })).toEqual(bin([3, 2], [0, 1]));
  });

  it("tells afterwards what was deleted, and says which kind of thing the pictures were", () => {
    expect(emptiedText(0)).toBe("The bin was already empty.");
    expect(emptiedText(3)).toBe("Emptied the bin: 3 runs deleted for good.");
    expect(emptiedText(2, 3, { pictures: 3, tracks: 0 })).toBe("Emptied the bin: 2 runs and 3 pictures deleted for good.");
    expect(emptiedText(0, 2, { pictures: 0, tracks: 2 })).toBe("Emptied the bin: 2 tracks deleted for good.");
    expect(emptiedText(1, 3, { pictures: 2, tracks: 1 })).toBe("Emptied the bin: 1 run, 2 pictures and 1 track deleted for good.");
    // the answer and the counts disagree (something changed in between): the answer's total is what is certain
    expect(emptiedText(1, 5, { pictures: 2, tracks: 1 })).toBe("Emptied the bin: 1 run and 5 pictures deleted for good.");
  });

  it("is asked by the dialog with those words, and answered with Empty bin or Cancel", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<ConfirmEmptyBin open bin={bin([3, 2], [1, 0])} wholeBin={false} onCancel={onCancel} onConfirm={onConfirm} />);
    expect(screen.getByRole("alertdialog").textContent).toContain("Delete 4 runs and 2 pictures for good");
    fireEvent.click(screen.getByRole("button", { name: "Empty bin" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

// ------------------------------------------------------------------ the rule for the Deleted view
describe("which runs the Deleted view holds (the shared table has the rest)", () => {
  it("holds a run that is in the history but has a picture in the bin, and the history holds it too", () => {
    const withBinned = makeRun({ binned_images: [makeBinnedImage(1)] });
    expect(matches(withBinned, ONLY_DELETED)).toBe(true);
    expect(matches(withBinned, NO_FILTER)).toBe(true); // the run itself was not deleted
    expect(matches(makeRun(), ONLY_DELETED)).toBe(false);
    expect(matches(makeMusicRun({ binned_tracks: [makeBinnedTrack(0)] }), ONLY_DELETED)).toBe(true);
  });
});

// ------------------------------------------------------------------ Delete picture in the viewer
describe("Delete picture in the viewer (DESIGN.md §33.1)", () => {
  const three = makeRun({ id: "run-three", images: [makeImage(0), makeImage(1), makeImage(2)] });
  const props = (over: Partial<Parameters<typeof Lightbox>[0]> = {}) => ({
    run: three, index: 1, notice: null, canEdit: false, readOnly: false, making4k: new Set<string>(), enlarging: new Set<string>(), enlargeWaiting: new Set<string>(),
    upscaler: null, onIndex: nothing, onRegenerateLarger: nothing, onMake4K: nothing, onEnlarge: nothing, onEditThis: () => true, onDeletePicture: vi.fn(), onClose: nothing, ...over,
  });
  const button = () => document.querySelector('[data-action="delete-picture"]') as HTMLButtonElement | null;

  it("is offered on a result of a finished run, and says which one it is, of how many", () => {
    const p = props();
    render(<Lightbox {...p} />);
    expect(button()).not.toBeNull();
    fireEvent.click(button() as HTMLButtonElement);
    expect(p.onDeletePicture).toHaveBeenCalledWith(three.images[1], 2, 3); // image 2 of 3 (its place among the pictures that are there, not its idx)
  });

  it("is not offered on a run that is still working", () => {
    for (const status of ["queued", "running"] as const) {
      render(<Lightbox {...props({ run: { ...three, status } })} />);
      expect(button()).toBeNull();
      cleanup();
    }
  });

  it("is offered on a failed or canceled run that has pictures", () => {
    for (const status of ["failed", "canceled"] as const) {
      render(<Lightbox {...props({ run: { ...three, status } })} />);
      expect(button()).not.toBeNull();
      cleanup();
    }
  });

  it("is not offered on a run in the bin, or on a deleted picture, which the viewer shows read-only", () => {
    render(<Lightbox {...props({ readOnly: true })} />);
    expect(button()).toBeNull();
  });

  it("is not offered on an edit's source image, only on its results", () => {
    const edit = makeRun({
      id: "run-edit", mode: "edit", images: [makeImage(0)],
      inputs: [{ position: 1, role: "reference", id: "src1", width: 8, height: 8, has_alpha: false, url: "/api/images/src1", thumb_url: null, can_4k: false, four_k_size: null, can_enlarge: false, enlarge_size: null, four_k: null }],
    });
    const p = props({ run: edit, index: 0 }); // the viewer pages sources first: index 0 is the source
    render(<Lightbox {...p} />);
    expect(button()).toBeNull();
    cleanup();
    const q = props({ run: edit, index: 1 });
    render(<Lightbox {...q} />);
    expect(button()).not.toBeNull();
    fireEvent.click(button() as HTMLButtonElement);
    expect(q.onDeletePicture).toHaveBeenCalledWith(edit.images[0], 1, 1); // "Result 1 of 1": the source is not counted
  });

  it("waits while Make 4K or Enlarge is working on that picture, and says why", () => {
    for (const busy of ["making4k", "enlarging", "enlargeWaiting"] as const) {
      render(<Lightbox {...props({ [busy]: new Set([three.images[1].id]) })} />);
      expect(button()?.disabled).toBe(true);
      expect(button()?.getAttribute("title")).toMatch(/Make 4K or Enlarge is working on this picture/);
      cleanup();
    }
    // another picture of the same run being worked on does not hold this one
    render(<Lightbox {...props({ making4k: new Set([three.images[0].id]) })} />);
    expect(button()?.disabled).toBe(false);
  });
});

// ------------------------------------------------------------------ the question, as a dialog
describe("the question about one picture, as a dialog", () => {
  const run = makeRun({ project_id: "p".repeat(32), images: [makeImage(0), makeImage(1)] });
  const itemAsk = (over: Partial<ItemAsk> = {}): ItemAsk => ({ run, item: { ...picture({ of: 2, position: 1 }), id: "image0" }, last: false, forever: false, ...over });

  it("asks with the question's words, names the project, and is answered with Delete or Cancel", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<ConfirmDeleteItem ask={itemAsk()} binDays={30} projectName="Logo" onCancel={onCancel} onConfirm={onConfirm} />);
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText("Delete this picture?")).toBeTruthy();
    expect(dialog.textContent).toContain("It moves to Deleted and stays there for 30 days");
    expect(dialog.textContent).toContain("The run stays in the project “Logo” with its other pictures.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("says Delete forever for a picture that is in the bin already, and shows nothing when there is no question", () => {
    render(<ConfirmDeleteItem ask={itemAsk({ forever: true })} binDays={30} projectName={null} onCancel={nothing} onConfirm={nothing} />);
    expect(screen.getByText("Delete this picture for good?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete forever" })).toBeTruthy();
    cleanup();
    render(<ConfirmDeleteItem ask={null} binDays={30} projectName={null} onCancel={nothing} onConfirm={nothing} />);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});

// ------------------------------------------------------------------ the card of deleted pictures
describe("the card the Deleted view shows for a run with pictures in the bin (DESIGN.md §33.1)", () => {
  const now = Date.parse("2026-10-15T10:00:00.000Z");
  const handlers = () => ({ onOpen: vi.fn(), onRestore: vi.fn(), onDeleteForever: vi.fn() });
  const run = makeRun({ id: "run-binned", prompt: "a red fox in the snow", binned_images: [makeBinnedImage(1), makeBinnedImage(2, { seed: 777 })], images: [makeImage(0)] });

  it("is one card for the run, with its prompt, how many pictures are deleted, and each of them listed with its own place and seed", () => {
    render(<BinnedItemsCard run={run} now={now} projectName={null} handlers={handlers()} />);
    const card = document.querySelector('[data-card="binned-items"]') as HTMLElement;
    expect(card.getAttribute("data-run-id")).toBe("run-binned");
    expect(card.textContent).toContain("2 deleted pictures");
    expect(card.textContent).toContain("a red fox in the snow");
    expect(card.textContent).toMatch(/From a run made on /);
    const rows = card.querySelectorAll("li.binned-item");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Image 2 · seed 101"); // idx 1: its own place in the run, where Restore puts it back
    expect(rows[1].textContent).toContain("Image 3 · seed 777");
  });

  it("says since when each is in the bin and until when", () => {
    render(<BinnedItemsCard run={run} now={now} projectName={null} handlers={handlers()} />);
    const notes = [...document.querySelectorAll('[data-note="in-bin"]')].map((n) => n.textContent ?? "");
    expect(notes).toHaveLength(2);
    for (const note of notes) expect(note).toMatch(/^In the bin since .+\. It will be deleted for good around .+, in 29 days\.$/);
  });

  it("opens a deleted picture in the viewer, restores it, or deletes it for good, each by its own button and for that picture", () => {
    const h = handlers();
    render(<BinnedItemsCard run={run} now={now} projectName={null} handlers={h} />);
    const second = screen.getByText(/Image 3 · seed 777/).closest("li") as HTMLElement;
    fireEvent.click(within(second).getByRole("button", { name: /Open Image 3/ }));
    expect(h.onOpen).toHaveBeenCalledWith(run, 1); // the second deleted picture: its place in the list of deleted ones
    fireEvent.click(within(second).getByRole("button", { name: /Restore Image 3/ }));
    expect(h.onRestore).toHaveBeenCalledWith(run, run.binned_images[1]);
    fireEvent.click(within(second).getByRole("button", { name: /Delete Image 3 · seed 777 for good/ }));
    expect(h.onDeleteForever).toHaveBeenCalledWith(run, run.binned_images[1]);
    expect(h.onOpen).toHaveBeenCalledTimes(1);
    expect(h.onRestore).toHaveBeenCalledTimes(1);
  });

  it("shows the project the run is filed in, with its name or a stand-in while the name is not known", () => {
    const filed = makeRun({ ...run, project_id: "p".repeat(32), binned_images: [makeBinnedImage(0)] });
    render(<BinnedItemsCard run={filed} now={now} projectName="Logo" handlers={handlers()} />);
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Logo");
    cleanup();
    render(<BinnedItemsCard run={filed} now={now} projectName={null} handlers={handlers()} />);
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Project");
    cleanup();
    render(<BinnedItemsCard run={run} now={now} projectName={null} handlers={handlers()} />);
    expect(document.querySelector("[data-project-chip]")).toBeNull(); // a run in no project has no chip
  });

  it("lists deleted tracks of a music run, each with a player, its length and the same buttons", () => {
    const music = makeMusicRun({ id: "music-binned", binned_tracks: [makeBinnedTrack(1, { seconds: 47 })], tracks: [makeTrack(0)] });
    const h = handlers();
    render(<BinnedItemsCard run={music} now={now} projectName={null} handlers={h} />);
    const card = document.querySelector('[data-card="binned-items"]') as HTMLElement;
    expect(card.textContent).toContain("1 deleted track");
    expect(card.textContent).toContain("Version 2 · seed 201");
    expect(card.textContent).toContain("0:47");
    expect(card.querySelector("audio")?.getAttribute("src")).toBe("/api/audio/track1");
    expect(card.querySelector('[data-action="open-deleted"]')).toBeNull(); // a track has no viewer
    fireEvent.click(screen.getByRole("button", { name: /Restore Version 2/ }));
    expect(h.onRestore).toHaveBeenCalledWith(music, music.binned_tracks[0]);
  });
});

// ------------------------------------------------------------------ Delete track on a track's row
describe("Delete track on a track's row (DESIGN.md §33.1)", () => {
  const controls: ProjectControls = { projects: [], nameMax: 60, onFile: nothing, onUnfile: nothing, onCreate: async () => null };
  const cardProps = (run: ReturnType<typeof makeMusicRun>) => ({
    run, now: Date.parse("2026-10-15T10:00:00.000Z"), workerState: "ready" as const, transient: false, viewScope: "kept" as const, projects: controls,
    onReuse: nothing, onRetry: nothing, onCancel: nothing, onToggleKeep: nothing, onRestore: nothing, onDelete: nothing, onDeleteTrack: vi.fn(), onCopy: nothing,
  });
  const three = () => makeMusicRun({ options: { duration: 30, steps: 30, seed: 7, seed_was_random: false, tracks: 3, instrumental: true, fields: {} }, tracks: [makeTrack(0), makeTrack(1), makeTrack(2)] });

  it("is on each row of a finished run that has more than one track, and names the track, its place and how many", () => {
    const p = cardProps(three());
    render(<TrackCard {...p} />);
    const buttons = screen.getAllByRole("button", { name: /^Delete track:/ });
    expect(buttons).toHaveLength(3);
    fireEvent.click(buttons[1]);
    expect(p.onDeleteTrack).toHaveBeenCalledWith(p.run.tracks[1], 2, 3);
  });

  it("is not on a run with a single track (its Delete is the run's), on a run still being made, or on a run in the bin", () => {
    render(<TrackCard {...cardProps(makeMusicRun({ tracks: [makeTrack(0)] }))} />);
    expect(screen.queryByRole("button", { name: /^Delete track:/ })).toBeNull();
    cleanup();
    render(<TrackCard {...cardProps({ ...three(), status: "running" })} />);
    expect(screen.queryByRole("button", { name: /^Delete track:/ })).toBeNull();
    cleanup();
    render(<TrackCard {...cardProps({ ...three(), deleted_at: "2026-10-14T10:00:00.000Z", purge_at: "2026-11-13T10:00:00.000Z" })} />);
    expect(screen.queryByRole("button", { name: /^Delete track:/ })).toBeNull();
  });

  it("numbers the tracks that are there again by position, after one was deleted", () => {
    // asked for three, one is in the bin: the card of a finished run says "of 2", not "of 3"
    const run = { ...three(), tracks: [makeTrack(0), makeTrack(2)], binned_tracks: [makeBinnedTrack(1)] };
    render(<TrackCard {...cardProps(run)} />);
    const names = [...document.querySelectorAll(".track-name")].map((n) => n.textContent ?? "");
    expect(names[0]).toMatch(/^Version 1 of 2/);
    expect(names[1]).toMatch(/^Version 2 of 2/);
  });

  it("numbers a run that is still being made by how many it was asked for", () => {
    const run = { ...three(), status: "running" as const, tracks: [makeTrack(0)] };
    render(<TrackCard {...cardProps(run)} />);
    expect(document.querySelector(".track-name")?.textContent).toMatch(/^Version 1 of 3/);
  });
});
