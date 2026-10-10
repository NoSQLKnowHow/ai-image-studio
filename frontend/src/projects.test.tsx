import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfirmDelete, ConfirmEmptyBin } from "./components/Dialogs";
import { ProjectMenu, projectOf, type ProjectControls } from "./components/ProjectMenu";
import { ProjectsDialog } from "./components/ProjectsDialog";
import { RunCard } from "./components/RunCard";
import { TrackCard } from "./components/TrackCard";
import {
  deleteProjectQuestion, filedText, keepLockedTitle, leftTheViewText, movedText, projectContents, restoredText, takenOutText, workingNote,
} from "./format";
import { makeMusicRun, makeRun } from "./testdata";
import type { Project } from "./types";

// Project folders on the page (DESIGN.md §32.1, §32.3): the words, the card's drop-down, the Manage dialog, the lock on Keep, and the questions.

afterEach(cleanup);

const LOGO: Project = { id: "a".repeat(32), name: "Logo", created_at: "2026-10-02T10:00:00.000Z", counts: { image: 3, music: 1 } };
const PITCH: Project = { id: "b".repeat(32), name: "Pitch deck", created_at: "2026-10-03T10:00:00.000Z", counts: { image: 0, music: 0 } };
const nothing = () => undefined;

// what a card is handed to offer projects, with spies for what it asks the page to do
function controls(over: Partial<ProjectControls> = {}): ProjectControls & { onFile: ReturnType<typeof vi.fn>; onUnfile: ReturnType<typeof vi.fn>; onCreate: ReturnType<typeof vi.fn> } {
  return { projects: [LOGO, PITCH], nameMax: 60, onFile: vi.fn(), onUnfile: vi.fn(), onCreate: vi.fn(async () => null), ...over } as never;
}

// ------------------------------------------------------------------ the words
describe("the words about projects", () => {
  it("says what a project holds, tab by tab", () => {
    expect(projectContents({ image: 3, music: 1 })).toBe("3 pictures, 1 track");
    expect(projectContents({ image: 1, music: 0 })).toBe("1 picture");
    expect(projectContents({ image: 0, music: 2 })).toBe("2 tracks");
    expect(projectContents({ image: 0, music: 0 })).toBe("no runs");
  });

  it("says what each filing did, and that the run is kept", () => {
    const run = { prompt: "a red fox in the snow" };
    expect(filedText(run, "Logo")).toBe("Filed “a red fox in the snow” in Logo. It is kept.");
    expect(movedText(run, "Logo", "Pitch deck")).toBe("Moved “a red fox in the snow” from Logo to Pitch deck.");
    expect(takenOutText("Logo")).toBe("Taken out of Logo. It is still kept.");
  });

  it("shortens a long prompt in the toast, as the other toasts do", () => {
    expect(filedText({ prompt: "x".repeat(200) }, "Logo")).toMatch(/^Filed “x{47}…” in Logo\. It is kept\.$/);
  });

  it("explains the locked Keep button, and still can when the project's name is not known yet", () => {
    expect(keepLockedTitle("Logo")).toBe("Kept because it is in the project “Logo”. Take it out of the project first.");
    expect(keepLockedTitle(null)).toBe("Kept because it is in a project. Take it out of the project first.");
  });

  it("asks before deleting a project, and says that its runs are not deleted", () => {
    expect(deleteProjectQuestion(LOGO)).toBe("Delete the project “Logo”? Its 4 runs (3 pictures, 1 track) are not deleted: they stay kept and are no longer in a project.");
    expect(deleteProjectQuestion({ name: "One", counts: { image: 1, music: 0 } })).toBe("Delete the project “One”? Its 1 run (1 picture) is not deleted: it stays kept and is no longer in a project.");
    expect(deleteProjectQuestion(PITCH)).toBe("Delete the project “Pitch deck”? It has no runs.");
  });

  it("says what keeps a card in a view while it works: Keep it, or add it to the project", () => {
    expect(workingNote("kept")).toBe("Shown while it works. It stays in this view only if you Keep it.");
    expect(workingNote("project")).toBe("Shown while it works. It stays in this view only if you add it to this project.");
    expect(leftTheViewText({ prompt: "a fox", status: "done" })).toContain("It is not kept, so it is not in this view.");
    expect(leftTheViewText({ prompt: "a fox", status: "done" }, "project")).toContain("It is not in this project, so it is not in this view.");
  });

  it("tells a restored filed run that it is back in its project", () => {
    const run = { prompt: "a fox", pinned: true, expires_at: null };
    expect(restoredText(run, Date.now(), "Logo")).toBe("Restored “a fox”. It is back in the project “Logo”, and still kept.");
    expect(restoredText(run, Date.now(), null)).toBe("Restored “a fox”. It is still kept."); // unfiled (its project was deleted meanwhile)
  });

  it("finds the project a run is in, and none for a run that is in none or in a project the list does not have", () => {
    expect(projectOf({ project_id: LOGO.id }, [LOGO, PITCH])).toBe(LOGO);
    expect(projectOf({ project_id: null }, [LOGO])).toBeNull();
    expect(projectOf({ project_id: "c".repeat(32) }, [LOGO])).toBeNull();
  });
});

// ------------------------------------------------------------------ the card's drop-down
describe("the Project button on a card (DESIGN.md §32.1)", () => {
  const unfiled = makeRun({ id: "run-a" });
  const filed = makeRun({ id: "run-b", project_id: LOGO.id, pinned: true });
  const open = (run = unfiled, c = controls()) => {
    render(<ProjectMenu run={run} controls={c} />);
    fireEvent.click(screen.getByRole("button", { name: /project/i }));
    return c;
  };

  it("says Add to project on a run in none, and the project's name on a filed run", () => {
    render(<ProjectMenu run={unfiled} controls={controls()} />);
    expect(screen.getByRole("button", { name: "Add to project" })).toBeTruthy();
    cleanup();
    render(<ProjectMenu run={filed} controls={controls()} />);
    const button = screen.getByRole("button", { name: "Project: Logo. Change the project" });
    expect(button.textContent).toContain("Logo");
    expect(button.getAttribute("aria-haspopup")).toBe("menu");
  });

  it("lists the projects, then New project…, and Take out of project only on a filed run", () => {
    open(unfiled);
    expect(screen.getAllByRole("menuitemradio").map((item) => item.textContent)).toEqual(["Logo", "Pitch deck"]);
    expect(screen.getByRole("menuitem", { name: /New project/ })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /Take out of project/ })).toBeNull();
    cleanup();
    open(filed);
    expect(screen.getByRole("menuitem", { name: /Take out of project/ })).toBeTruthy();
    // a tick marks the project the run is in
    const checked = screen.getAllByRole("menuitemradio").filter((item) => item.getAttribute("aria-checked") === "true");
    expect(checked.map((item) => item.textContent)).toEqual(["Logo"]);
  });

  it("says so when there are no projects yet, and still offers to make one", () => {
    open(unfiled, controls({ projects: [] }));
    expect(screen.getByText("No projects yet")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /New project/ })).toBeTruthy();
  });

  it("puts focus on the project the run is in (or the first choice), and the arrow keys move through the choices and round", () => {
    open(filed);
    const [logo, pitch] = screen.getAllByRole("menuitemradio");
    expect(document.activeElement).toBe(logo); // the run is in Logo
    fireEvent.keyDown(logo, { key: "ArrowDown" });
    expect(document.activeElement).toBe(pitch);
    fireEvent.keyDown(pitch, { key: "End" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: /Take out of project/ }));
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "ArrowDown" });
    expect(document.activeElement).toBe(logo); // wraps round
    fireEvent.keyDown(logo, { key: "ArrowUp" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: /Take out of project/ }));
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Home" });
    expect(document.activeElement).toBe(logo);
  });

  it("starts on the first choice for a run in no project", () => {
    open(unfiled);
    expect(document.activeElement).toBe(screen.getAllByRole("menuitemradio")[0]);
  });

  it("files the run when a project is chosen, closes, and gives focus back to the button", () => {
    const c = open(unfiled);
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Pitch deck" }));
    expect(c.onFile).toHaveBeenCalledWith(unfiled, PITCH.id);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add to project" }));
  });

  it("takes the run out when Take out of project is chosen", () => {
    const c = open(filed);
    fireEvent.click(screen.getByRole("menuitem", { name: /Take out of project/ }));
    expect(c.onUnfile).toHaveBeenCalledWith(filed);
    expect(c.onFile).not.toHaveBeenCalled();
  });

  it("closes on Escape and returns focus to the button, filing nothing", () => {
    const c = open(unfiled);
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add to project" }));
    expect(c.onFile).not.toHaveBeenCalled();
  });

  it("closes on a click elsewhere without taking focus from where the person put it", () => {
    open(unfiled);
    const elsewhere = document.createElement("button");
    document.body.appendChild(elsewhere);
    elsewhere.focus();
    fireEvent.pointerDown(elsewhere);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(elsewhere);
    elsewhere.remove();
  });

  it("closes when Tab moves focus on", () => {
    open(unfiled);
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Tab" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens and closes with the button, and says whether it is open", () => {
    render(<ProjectMenu run={unfiled} controls={controls()} />);
    const button = screen.getByRole("button", { name: "Add to project" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(button);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(button.getAttribute("aria-expanded")).toBe("false");
  });

  describe("New project…", () => {
    const toForm = (c = controls()) => {
      open(unfiled, c);
      fireEvent.click(screen.getByRole("menuitem", { name: /New project/ }));
      return { c, field: screen.getByLabelText("Name of the new project") as HTMLInputElement };
    };

    it("turns the menu into a field for the name, with the limit in its hint, and puts focus in it", () => {
      const { field } = toForm();
      expect(document.activeElement).toBe(field);
      expect(screen.queryByRole("menu")).toBeNull();
      expect(screen.getByText(/Up to 60 characters/)).toBeTruthy();
    });

    it("makes the project with what was typed, and closes when it is made", async () => {
      const { c, field } = toForm();
      fireEvent.change(field, { target: { value: "  Brand work " } });
      await act(async () => {
        fireEvent.submit(field.closest("form") as HTMLFormElement);
      });
      expect(c.onCreate).toHaveBeenCalledWith(unfiled, "  Brand work "); // the server trims it; the page sends what was typed
      expect(screen.queryByLabelText("Name of the new project")).toBeNull();
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add to project" }));
    });

    it("asks for a name rather than sending an empty one", async () => {
      const { c, field } = toForm();
      fireEvent.change(field, { target: { value: "   " } });
      await act(async () => {
        fireEvent.submit(field.closest("form") as HTMLFormElement);
      });
      expect(c.onCreate).not.toHaveBeenCalled();
      expect(screen.getByRole("alert").textContent).toBe("Give the project a name.");
    });

    it("keeps the name in the field and shows the reason when the server refuses it, and clears the reason when the name is changed", async () => {
      const refusing = controls({ onCreate: vi.fn(async () => "There is a project with that name already.") as never });
      const { field } = toForm(refusing);
      fireEvent.change(field, { target: { value: "Logo" } });
      await act(async () => {
        fireEvent.submit(field.closest("form") as HTMLFormElement);
      });
      expect(screen.getByRole("alert").textContent).toBe("There is a project with that name already.");
      expect(field.value).toBe("Logo");
      expect(field.getAttribute("aria-invalid")).toBe("true");
      fireEvent.change(field, { target: { value: "Logos" } });
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("goes back to the list on Escape or Cancel, and a second Escape closes the menu", () => {
      const { field } = toForm();
      fireEvent.keyDown(field, { key: "Escape" });
      expect(screen.getByRole("menu")).toBeTruthy(); // back to the list
      fireEvent.click(screen.getByRole("menuitem", { name: /New project/ }));
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.getByRole("menu")).toBeTruthy();
      fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Escape" });
      expect(screen.queryByRole("menu")).toBeNull();
    });
  });
});

// ------------------------------------------------------------------ the Manage dialog
describe("the Manage projects dialog (DESIGN.md §32.1)", () => {
  const show = (over: Partial<Parameters<typeof ProjectsDialog>[0]> = {}) => {
    const handlers = { onClose: vi.fn(), onCreate: vi.fn(async () => null), onRename: vi.fn(async () => null), onDelete: vi.fn(async () => null) };
    render(<ProjectsDialog open projects={[LOGO, PITCH]} nameMax={60} {...handlers} {...over} />);
    return handlers;
  };
  const row = (name: string) => screen.getByText(name).closest("li") as HTMLElement;

  it("lists each project with what it holds", () => {
    show();
    expect(within(row("Logo")).getByText("3 pictures, 1 track")).toBeTruthy();
    expect(within(row("Pitch deck")).getByText("no runs")).toBeTruthy();
  });

  it("says so when there are none", () => {
    show({ projects: [] });
    expect(screen.getByText("No projects yet.")).toBeTruthy();
  });

  it("makes a project, and clears the field for the next one", async () => {
    const h = show();
    const field = screen.getByLabelText("New project") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "Poster" } });
    await act(async () => {
      fireEvent.submit(field.closest("form") as HTMLFormElement);
    });
    expect(h.onCreate).toHaveBeenCalledWith("Poster");
    expect(field.value).toBe("");
  });

  it("keeps a refused name in the field with the reason", async () => {
    show({ onCreate: vi.fn(async () => "A project name is at most 60 characters; this one is 61.") as never });
    const field = screen.getByLabelText("New project") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "x".repeat(61) } });
    await act(async () => {
      fireEvent.submit(field.closest("form") as HTMLFormElement);
    });
    expect(screen.getByRole("alert").textContent).toContain("at most 60 characters");
    expect(field.value).toHaveLength(61);
  });

  it("renames in place: Enter (the form) saves, Escape puts the old name back", async () => {
    const h = show();
    fireEvent.click(within(row("Logo")).getByRole("button", { name: "Rename Logo" }));
    const field = screen.getByLabelText("New name for Logo") as HTMLInputElement;
    expect(field.value).toBe("Logo");
    fireEvent.change(field, { target: { value: "Brand" } });
    fireEvent.keyDown(field, { key: "Escape" });
    // Escape in the field ends the rename and nothing more: the dialog is not asked to close (it hears Escape on the whole document, before
    // the field does, so it has to be told to leave this one alone)
    expect(h.onClose).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("New name for Logo")).toBeNull();
    expect(screen.getByText("Logo")).toBeTruthy(); // the old name is back
    fireEvent.click(within(row("Logo")).getByRole("button", { name: "Rename Logo" }));
    const again = screen.getByLabelText("New name for Logo") as HTMLInputElement;
    expect(again.value).toBe("Logo"); // not the abandoned "Brand"
    fireEvent.change(again, { target: { value: "Brand" } });
    await act(async () => {
      fireEvent.submit(again.closest("form") as HTMLFormElement);
    });
    expect(h.onRename).toHaveBeenCalledWith(LOGO, "Brand");
  });

  it("closes on Escape when it is pressed anywhere but in a rename field (so the test above is about the field, not about a dead handler)", () => {
    const h = show();
    fireEvent.keyDown(screen.getByLabelText("New project"), { key: "Escape" });
    expect(h.onClose).toHaveBeenCalledTimes(1);
  });

  it("shows why a rename was refused, and stays in the field", async () => {
    show({ onRename: vi.fn(async () => "There is a project with that name already.") as never });
    fireEvent.click(within(row("Logo")).getByRole("button", { name: "Rename Logo" }));
    const field = screen.getByLabelText("New name for Logo") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "Pitch deck" } });
    await act(async () => {
      fireEvent.submit(field.closest("form") as HTMLFormElement);
    });
    expect(screen.getByRole("alert").textContent).toBe("There is a project with that name already.");
    expect(screen.getByLabelText("New name for Logo")).toBeTruthy();
  });

  it("asks before deleting, says that the runs are not deleted, and deletes only when confirmed", async () => {
    const h = show();
    fireEvent.click(within(row("Logo")).getByRole("button", { name: "Delete Logo" }));
    expect(h.onDelete).not.toHaveBeenCalled();
    expect(screen.getByText(deleteProjectQuestion(LOGO))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(deleteProjectQuestion(LOGO))).toBeNull(); // asking can be backed out of
    expect(h.onDelete).not.toHaveBeenCalled();
    fireEvent.click(within(row("Logo")).getByRole("button", { name: "Delete Logo" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete project" }));
    });
    expect(h.onDelete).toHaveBeenCalledWith(LOGO);
  });
});

// ------------------------------------------------------------------ the card
describe("a card of a run that is filed in a project (DESIGN.md §32.3)", () => {
  const cardProps = (run: ReturnType<typeof makeRun>, c = controls()) => ({
    run, now: Date.parse("2026-10-05T12:00:00.000Z"), workerState: "ready" as const, canEdit: true, making4k: new Set<string>(), enlarging: new Set<string>(),
    enlargeWaiting: new Set<string>(), transient: false, viewScope: "kept" as const, projects: c, upscaler: null, onReuse: nothing, onRegenerateLarger: nothing,
    onMake4K: nothing, onEnlarge: nothing, onEditThis: nothing, onRetry: nothing, onCancel: nothing, onToggleKeep: vi.fn(), onRestore: nothing, onDelete: nothing,
    onCopy: nothing, onOpenImage: nothing,
  });
  const keepButton = () => screen.getByRole("button", { name: /Keep/ });

  it("shows the project as a chip, and Keep pressed and locked, with the reason as its tooltip", () => {
    render(<RunCard {...cardProps(makeRun({ project_id: LOGO.id, pinned: true }))} />);
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Logo");
    const keep = keepButton();
    expect(keep.getAttribute("aria-pressed")).toBe("true");
    expect(keep.getAttribute("aria-disabled")).toBe("true");
    expect(keep.hasAttribute("data-locked")).toBe(true);
    expect(keep.getAttribute("title")).toBe(keepLockedTitle("Logo"));
  });

  it("does nothing when the locked Keep is pressed", () => {
    const props = cardProps(makeRun({ project_id: LOGO.id, pinned: true }));
    render(<RunCard {...props} />);
    fireEvent.click(keepButton());
    expect(props.onToggleKeep).not.toHaveBeenCalled();
  });

  it("leaves Keep as it was on a run that is in no project: it works, is not locked, and the Project button says Add to project", () => {
    const props = cardProps(makeRun({ pinned: false }));
    render(<RunCard {...props} />);
    const keep = keepButton();
    expect(keep.hasAttribute("aria-disabled")).toBe(false);
    expect(document.querySelector("[data-project-chip]")).toBeNull();
    fireEvent.click(keep);
    expect(props.onToggleKeep).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Add to project" })).toBeTruthy();
  });

  it("still locks Keep, and says it is in a project, when the page does not know the project's name yet", () => {
    render(<RunCard {...cardProps(makeRun({ project_id: "c".repeat(32), pinned: true }))} />);
    expect(keepButton().getAttribute("title")).toBe(keepLockedTitle(null));
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Project");
  });

  it("in the bin shows the project as a chip that cannot be pressed, with no Project button", () => {
    render(<RunCard {...cardProps(makeRun({ project_id: LOGO.id, pinned: true, deleted_at: "2026-10-02T10:00:00.000Z", purge_at: "2026-11-01T10:00:00.000Z" }))} />);
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Logo");
    expect(document.querySelector('[data-action="project"]')).toBeNull();
    expect(screen.queryByRole("button", { name: /Keep/ })).toBeNull();
  });

  it("says what keeps a working card in a project's view, not what keeps it in the Kept view", () => {
    render(<RunCard {...cardProps(makeRun({ status: "running" }))} transient viewScope="project" />);
    expect(document.querySelector('[data-note="working-in-project"]')?.textContent).toBe(workingNote("project"));
    expect(document.querySelector('[data-note="working-in-kept"]')).toBeNull();
  });

  it("is the same for a music card: a chip, Keep locked, and a Project button", () => {
    // a track card takes the props a picture card does, less the picture-only ones; it ignores the extra ones it is not asked for
    const props = { ...cardProps(makeRun()), run: makeMusicRun({ project_id: LOGO.id, pinned: true }) } as unknown as Parameters<typeof TrackCard>[0];
    render(<TrackCard {...props} />);
    expect(document.querySelector("[data-project-chip]")?.textContent).toContain("Logo");
    expect(keepButton().getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("button", { name: "Project: Logo. Change the project" })).toBeTruthy();
  });

  it("renders without any browser-only code (the markup is the same on the server)", () => {
    const html = renderToStaticMarkup(<RunCard {...cardProps(makeRun({ project_id: LOGO.id, pinned: true }))} />);
    expect(html).toContain("data-project-chip");
    expect(html).toContain('data-locked="true"');
  });
});

// ------------------------------------------------------------------ the questions
describe("the Delete question for a run that is filed in a project (DESIGN.md §32.3 item 5)", () => {
  const ask = (run: ReturnType<typeof makeRun>, binDays = 30, name: string | null = "Logo") =>
    render(<ConfirmDelete run={run} binDays={binDays} projectName={name} onCancel={nothing} onConfirm={nothing} />);

  it("names the project, and says that Restore puts the run back in it", () => {
    ask(makeRun({ project_id: LOGO.id, pinned: true }));
    const text = screen.getByRole("alertdialog").textContent ?? "";
    expect(text).toContain("It is in the project “Logo”. It moves to Deleted and stays there for 30 days; restoring it puts it back in the project. After that it is gone for good.");
    expect(text).not.toContain("You marked it Keep"); // a filed run is kept anyway; the project is the fact that matters
  });

  it("says it is in a project when the name is not known", () => {
    ask(makeRun({ project_id: LOGO.id, pinned: true }), 30, null);
    expect(screen.getByRole("alertdialog").textContent).toContain("It is in a project. It moves to Deleted");
  });

  it("with no bin, says that the project loses the run", () => {
    ask(makeRun({ project_id: LOGO.id, pinned: true }), 0);
    expect(screen.getByRole("alertdialog").textContent).toContain("It is in the project “Logo”, which loses it. This can't be undone.");
  });

  it("is the question it always was for a run in no project", () => {
    ask(makeRun({ pinned: true }));
    const text = screen.getByRole("alertdialog").textContent ?? "";
    expect(text).toContain("It moves to Deleted and stays there for 30 days; you can restore it from there.");
    expect(text).toContain("You marked it Keep.");
    expect(text).not.toContain("project");
  });
});

describe("Empty bin while a project is chosen (DESIGN.md §30.1, §32)", () => {
  it("says that it empties the whole bin when a project is chosen, and says nothing extra otherwise", () => {
    render(<ConfirmEmptyBin open images={3} music={1} wholeBin onCancel={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("alertdialog").textContent).toContain("Delete 4 runs for good (3 on Images, 1 on Music)");
    expect(screen.getByRole("alertdialog").textContent).toContain("This is the whole bin, not only the project you are looking at.");
    cleanup();
    render(<ConfirmEmptyBin open images={3} music={1} wholeBin={false} onCancel={nothing} onConfirm={nothing} />);
    expect(screen.getByRole("alertdialog").textContent).not.toContain("whole bin");
  });
});
