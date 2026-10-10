import * as Dialog from "@radix-ui/react-dialog";
import { useState, type FormEvent } from "react";
import { deleteProjectQuestion, projectContents } from "../format";
import { useReturnFocus } from "../hooks";
import type { Project } from "../types";
import { CloseIcon, EditIcon, TrashIcon } from "./icons";

// The Manage projects dialog (DESIGN.md §32.1): make a project, rename one, delete one. Deleting asks first, and says that the runs in it are
// not deleted. Every action answers with a message to show when it could not be done (a name that is taken, say), or null when it was.

export interface ProjectsDialogProps {
  open: boolean;
  projects: Project[]; // A to Z
  nameMax: number;
  onClose: () => void;
  onCreate: (name: string) => Promise<string | null>;
  onRename: (project: Project, name: string) => Promise<string | null>;
  onDelete: (project: Project) => Promise<string | null>;
}

/** The one-line form that makes a project: a name and Create. */
function NewProject({ nameMax, onCreate }: { nameMax: number; onCreate: (name: string) => Promise<string | null> }) {
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!name.trim()) return setProblem("Give the project a name.");
    setBusy(true);
    const refused = await onCreate(name);
    setBusy(false);
    // made: the field is ready for the next one; refused: the name stays, with the reason, so it can be changed
    if (refused === null) setName("");
    else setProblem(refused);
  };
  return (
    <form className="project-add" onSubmit={(event) => void submit(event)}>
      <label htmlFor="new-project-name" className="project-new-label">New project</label>
      <div className="project-add-row">
        <input id="new-project-name" type="text" value={name} autoComplete="off" aria-describedby="new-project-hint" aria-invalid={problem ? true : undefined}
          onChange={(event) => { setName(event.target.value); setProblem(null); }} />
        <button type="submit" className="button primary" disabled={busy} data-action="create-project">Create</button>
      </div>
      <p id="new-project-hint" className={problem ? "field-error" : "field-hint"} role={problem ? "alert" : undefined}>
        {problem ?? `Up to ${nameMax} characters.`}
      </p>
    </form>
  );
}

/** One project in the list: its name and what it holds, with Rename and Delete. Rename turns the name into a field in place; Delete asks. */
function ProjectRow({ project, onRename, onDelete }: { project: Project; onRename: ProjectsDialogProps["onRename"]; onDelete: ProjectsDialogProps["onDelete"] }) {
  const [mode, setMode] = useState<"show" | "rename" | "delete">("show");
  const [name, setName] = useState(project.name);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!name.trim()) return setProblem("Give the project a name.");
    setBusy(true);
    const refused = await onRename(project, name);
    setBusy(false);
    if (refused === null) setMode("show");
    else setProblem(refused);
  };
  const remove = async () => {
    setBusy(true);
    const refused = await onDelete(project);
    setBusy(false);
    if (refused !== null) setProblem(refused);
  };

  return (
    <li className="project-row" data-project-row={project.id}>
      {mode === "rename" ? (
        // Rename in place: Enter saves, Escape puts the old name back (and keeps the dialog open: Escape here is the field's, not the dialog's)
        <form className="project-rename" onSubmit={(event) => void save(event)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              setMode("show");
              setName(project.name);
              setProblem(null);
            }
          }}>
          <label htmlFor={`rename-${project.id}`} className="sr-only">New name for {project.name}</label>
          <input id={`rename-${project.id}`} type="text" value={name} autoFocus autoComplete="off" aria-invalid={problem ? true : undefined}
            onChange={(event) => { setName(event.target.value); setProblem(null); }} />
          <button type="submit" className="button small primary" disabled={busy} data-action="save-rename">Save</button>
          <button type="button" className="button small" onClick={() => { setMode("show"); setName(project.name); setProblem(null); }}>Cancel</button>
        </form>
      ) : (
        <>
          <span className="project-row-name">{project.name}</span>
          <span className="project-row-count">{projectContents(project.counts)}</span>
          <span className="project-row-actions">
            <button type="button" className="button small ghost" data-action="rename-project" onClick={() => { setMode("rename"); setProblem(null); }}
              aria-label={`Rename ${project.name}`}>
              <EditIcon /> Rename
            </button>
            <button type="button" className="button small ghost danger" data-action="delete-project" onClick={() => { setMode("delete"); setProblem(null); }}
              aria-label={`Delete ${project.name}`}>
              <TrashIcon /> Delete
            </button>
          </span>
        </>
      )}
      {mode === "delete" && (
        <div className="project-row-confirm" role="group" aria-label={`Delete ${project.name}?`}>
          <p>{deleteProjectQuestion(project)}</p>
          <div className="confirm-actions">
            <button type="button" className="button small" onClick={() => setMode("show")}>Cancel</button>
            <button type="button" className="button small danger-solid" disabled={busy} data-action="confirm-delete-project" onClick={() => void remove()}>Delete project</button>
          </div>
        </div>
      )}
      {problem && <p className="field-error" role="alert">{problem}</p>}
    </li>
  );
}

export function ProjectsDialog({ open, projects, nameMax, onClose, onCreate, onRename, onDelete }: ProjectsDialogProps) {
  const { props: returnFocus } = useReturnFocus();
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        {/* Escape in the rename field ends the rename and nothing more. The dialog hears Escape on the whole document before the field does, so
            it is told here to leave this one alone (the field's own handler puts the old name back). */}
        <Dialog.Content className="confirm projects-dialog" {...returnFocus}
          onEscapeKeyDown={(event) => {
            if ((event.target as HTMLElement | null)?.closest?.(".project-rename")) event.preventDefault();
          }}>
          <div className="projects-head">
            <Dialog.Title>Projects</Dialog.Title>
            <Dialog.Close className="button small ghost icon-only" aria-label="Close"><CloseIcon /></Dialog.Close>
          </div>
          <Dialog.Description>A project is a folder of runs. Putting a run in one keeps it, so it is not deleted by accident.</Dialog.Description>
          <NewProject nameMax={nameMax} onCreate={onCreate} />
          {projects.length === 0 ? (
            <p className="project-none">No projects yet.</p>
          ) : (
            <ul className="project-rows" aria-label="Your projects">
              {projects.map((project) => <ProjectRow key={project.id} project={project} onRename={onRename} onDelete={onDelete} />)}
            </ul>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
