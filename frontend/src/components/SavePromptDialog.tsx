import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import { FOR_KINDS, FOR_LABELS } from "../builder";
import type { ForKind } from "../builderTypes";
import { useReturnFocus } from "../hooks";
import type { Project } from "../types";

/** Save as prompt (DESIGN.md §37.2, criterion 229): a title the builder suggested, tags it suggested, the project (the one being looked at by default) and what the prompt is for. A
 *  prompt is only made when this is pressed (§37.6 choice 12). */
export interface SaveFields {
  title: string;
  tags: string[];
  project_id: string | null;
  for: ForKind;
  note: string;
}

export function SavePromptDialog({ open, suggestion, projects, limits, onSave, onClose }: {
  open: boolean;
  suggestion: SaveFields;
  projects: Project[];
  limits: { title_chars: number; tags_max: number; tag_chars: number; note_chars: number };
  onSave: (fields: SaveFields) => Promise<string | null>; // null when saved, else the words for why not
  onClose: () => void;
}) {
  const [title, setTitle] = useState(suggestion.title);
  const [tags, setTags] = useState(suggestion.tags.join(", "));
  const [project, setProject] = useState(suggestion.project_id ?? "");
  const [kind, setKind] = useState<ForKind>(suggestion.for);
  const [note, setNote] = useState(suggestion.note);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const { props: returnFocus } = useReturnFocus();

  useEffect(() => {
    if (!open) return;
    setTitle(suggestion.title);
    setTags(suggestion.tags.join(", "));
    setProject(suggestion.project_id ?? "");
    setKind(suggestion.for);
    setNote(suggestion.note);
    setProblem(null);
  }, [open, suggestion]);

  const submit = async () => {
    setSaving(true);
    setProblem(null);
    const words = await onSave({ title: title.trim(), tags: tags.split(",").map((t) => t.trim()).filter(Boolean), project_id: project || null, for: kind, note: note.trim() });
    setSaving(false);
    if (words) setProblem(words);
  };

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="confirm builder-save" {...returnFocus}>
          <Dialog.Title>Save as prompt</Dialog.Title>
          <Dialog.Description className="hint">Version 1 is the words as they are now. Every change you save later is a new version; nothing is ever written over.</Dialog.Description>
          <form className="builder-save-form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
            <label>Title <input value={title} maxLength={limits.title_chars} onChange={(e) => setTitle(e.target.value)} required autoFocus /></label>
            <label>Tags <input value={tags} onChange={(e) => setTags(e.target.value)} aria-describedby="builder-save-tags-help" />
              <span id="builder-save-tags-help" className="hint">Separated by commas, up to {limits.tags_max}.</span></label>
            <label>For <select value={kind} onChange={(e) => setKind(e.target.value as ForKind)}>{FOR_KINDS.map((k) => <option key={k} value={k}>{FOR_LABELS[k]}</option>)}</select></label>
            <label>Project <select value={project} onChange={(e) => setProject(e.target.value)}>
              <option value="">No project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            <label>Note <input value={note} maxLength={limits.note_chars} onChange={(e) => setNote(e.target.value)} placeholder="Optional: what this version is" /></label>
            {problem && <p className="form-error" role="alert">{problem}</p>}
            <div className="confirm-actions">
              <Dialog.Close className="button" type="button">Cancel</Dialog.Close>
              <button type="submit" className="button primary" disabled={saving || !title.trim()}>{saving ? "Saving…" : "Save"}</button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
