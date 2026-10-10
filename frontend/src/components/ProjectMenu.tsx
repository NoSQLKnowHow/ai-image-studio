import { useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Project, Run } from "../types";
import { CheckIcon, ChevronDownIcon, FolderIcon, PlusIcon } from "./icons";

// The Project button on a card and its drop-down (DESIGN.md §32.1): file the run in a project, move it to another, make a new project right
// there, or take it out. The page has no menu library, so this is a small menu of its own: a button that opens a list of choices, which the
// keyboard can move through and which closes on Escape, on a click elsewhere, and when focus leaves it.

/** Everything a card needs to offer projects, handed down together so the cards (and the Music panel between them and the page) pass one thing. */
export interface ProjectControls {
  projects: Project[]; // every project, A to Z
  nameMax: number; // the longest a name may be, to say so in the field's hint
  onFile: (run: Run, projectId: string) => void; // file the run in a project (also moves a filed run), and keep it
  onUnfile: (run: Run) => void; // take the run out of its project; it stays kept
  /** Make a project with this name and file the run in it. Resolves with a message to show when the project could not be made, else null. */
  onCreate: (run: Run, name: string) => Promise<string | null>;
}

/** The project a run is filed in, if the page knows it (its list may not have arrived yet, or may be a moment behind the run). */
export const projectOf = (run: Pick<Run, "project_id">, projects: readonly Project[]): Project | null =>
  run.project_id === null ? null : projects.find((project) => project.id === run.project_id) ?? null;

export function ProjectMenu({ run, controls }: { run: Run; controls: ProjectControls }) {
  const { projects, nameMax } = controls;
  const filed = run.project_id !== null;
  const current = projectOf(run, projects);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false); // the menu has turned into the "Name of the new project" field
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null); // why the name was refused
  const [busy, setBusy] = useState(false); // a new project is being made
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popover = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const menuId = useId();
  const hintId = useId();

  // Close the menu. Focus goes back to the button that opened it (unless the menu is closing because the person clicked elsewhere).
  const close = (returnFocus = true) => {
    setOpen(false);
    setCreating(false);
    setProblem(null);
    setName("");
    if (returnFocus) trigger.current?.focus({ preventScroll: true });
  };

  // The choices, in order, for the arrow keys: every button that is an item of the menu.
  const items = (): HTMLElement[] => Array.from(popover.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []);

  // When the menu opens, focus goes to the project the run is in (or the first choice); when it turns into the name field, to the field.
  useEffect(() => {
    if (!open) return;
    if (creating) {
      field.current?.focus();
      return;
    }
    const choices = items();
    (choices.find((item) => item.getAttribute("aria-checked") === "true") ?? choices[0])?.focus({ preventScroll: true });
  }, [open, creating]);

  // A click or touch anywhere outside closes the menu. Focus is left where the person put it.
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) close(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  // Keep the pop-up inside the window: it starts under the left edge of its button, and is moved left if that would run it off the right of
  // the screen, and right if that would run it off the left (on a phone the button can be near the left and the menu nearly as wide as the
  // screen). Measured after every change of what it shows, since the list and the name field are different widths.
  useLayoutEffect(() => {
    const box = popover.current;
    const host = root.current;
    if (!open || !box || !host) return;
    box.style.left = "0px";
    const hostLeft = host.getBoundingClientRect().left;
    const widest = window.innerWidth - box.getBoundingClientRect().width - 8;
    box.style.left = `${Math.max(8, Math.min(hostLeft, widest)) - hostLeft}px`;
  }, [open, creating]);

  // Keys inside the list: arrows, Home and End move between the choices (wrapping round); Escape closes; Tab lets focus move on and closes.
  const onListKey = (event: KeyboardEvent) => {
    const choices = items();
    const at = choices.indexOf(document.activeElement as HTMLElement);
    const go = (to: number) => {
      event.preventDefault();
      choices[(to + choices.length) % choices.length]?.focus({ preventScroll: true });
    };
    if (event.key === "ArrowDown") go(at + 1);
    else if (event.key === "ArrowUp") go(at < 0 ? choices.length - 1 : at - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(choices.length - 1);
    else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") close(false);
  };

  const choose = (action: () => void) => {
    close();
    action();
  };

  // Create: make the project and file the run in it. A refused name stays in the field with the reason, so it can be changed.
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!name.trim()) {
      setProblem("Give the project a name.");
      field.current?.focus();
      return;
    }
    setBusy(true);
    const refused = await controls.onCreate(run, name);
    setBusy(false);
    if (refused === null) close();
    else {
      setProblem(refused);
      field.current?.focus();
    }
  };

  const label = current ? current.name : filed ? "In a project" : "Add to project";
  return (
    <div className="project-menu" ref={root} data-project-menu>
      <button
        ref={trigger}
        type="button"
        className={`button small ghost project-button${filed ? " active" : ""}`}
        data-action="project"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={filed ? `Project: ${current?.name ?? "a project"}. Change the project` : "Add to project"}
        title={filed ? "Move this run to another project, or take it out of its project" : "File this run in a project. It is kept as well, so it is not deleted by accident."}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <FolderIcon /> <span className="project-button-label">{label}</span> <ChevronDownIcon />
      </button>
      {open && (
        <div className="project-popover" ref={popover} id={menuId}>
          {creating ? (
            // the menu has turned into one line: the name, Create (Enter) and Cancel (Escape takes it back to the list)
            <form
              className="project-new"
              onSubmit={(event) => void submit(event)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  setCreating(false);
                  setProblem(null);
                }
              }}
            >
              <label htmlFor={`${menuId}-name`} className="project-new-label">Name of the new project</label>
              <input
                id={`${menuId}-name`}
                ref={field}
                type="text"
                value={name}
                autoComplete="off"
                aria-describedby={hintId}
                aria-invalid={problem ? true : undefined}
                onChange={(event) => {
                  setName(event.target.value);
                  setProblem(null);
                }}
              />
              <p id={hintId} className={problem ? "field-error" : "field-hint"} role={problem ? "alert" : undefined}>
                {problem ?? `Up to ${nameMax} characters. The run is filed in it, and kept.`}
              </p>
              <div className="project-new-actions">
                <button type="button" className="button small" onClick={() => { setCreating(false); setProblem(null); }}>Cancel</button>
                <button type="submit" className="button small primary" disabled={busy} data-action="create-project">Create</button>
              </div>
            </form>
          ) : (
            <ul role="menu" aria-label="Projects" className="project-list" onKeyDown={onListKey}>
              {projects.map((project) => (
                <li role="none" key={project.id}>
                  <button type="button" role="menuitemradio" aria-checked={project.id === run.project_id} className="project-item" data-project-id={project.id}
                    onClick={() => choose(() => controls.onFile(run, project.id))}>
                    <span className="project-tick">{project.id === run.project_id && <CheckIcon />}</span>
                    <span className="project-item-name">{project.name}</span>
                  </button>
                </li>
              ))}
              {projects.length === 0 && <li role="none" className="project-none">No projects yet</li>}
              <li role="separator" className="project-rule" />
              <li role="none">
                <button type="button" role="menuitem" className="project-item" data-action="new-project" onClick={() => setCreating(true)}>
                  <span className="project-tick"><PlusIcon /></span>
                  <span className="project-item-name">New project…</span>
                </button>
              </li>
              {filed && (
                <li role="none">
                  <button type="button" role="menuitem" className="project-item" data-action="unfile" onClick={() => choose(() => controls.onUnfile(run))}>
                    <span className="project-tick" />
                    <span className="project-item-name">Take out of project</span>
                  </button>
                </li>
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
