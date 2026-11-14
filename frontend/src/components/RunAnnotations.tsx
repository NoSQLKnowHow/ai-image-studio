import { useEffect, useId, useRef, useState } from "react";
import {
  DEFAULT_LIMITS, addedText, autocompleteFor, canAnnotate, noteCounter, noteExcerpt, noteSavedText, planAdd, removedText, suggestWhy, tooManyText, typedTags, useAnnotations,
  type Reachability,
} from "../annotations";
import type { Run, TagCount } from "../types";
import { ApiError } from "../api";
import { CloseIcon, TagIcon } from "./icons";

// The tags and the note of a run (DESIGN.md §45.3): shown on a card, and edited there and in the viewer. One component for the four kinds of card and the two viewers; it reaches the
// server through `AnnotationsContext`, so no panel has to pass anything through, and a card shown without it (a test, a read-only list) still shows the tags and the note.

/** The tags as small chips, and the note: what a card shows when the editor is closed. */
export function AnnotationsView({ run }: { run: Run }) {
  const [all, setAll] = useState(false);
  const excerpt = noteExcerpt(run.notes);
  if (run.tags.length === 0 && run.notes === "") return null;
  return (
    <div className="annotations-view" data-annotations-view>
      {run.tags.length > 0 && (
        <ul className="tag-list" aria-label="Tags">
          {run.tags.map((tag) => <li key={tag} className="tag-chip" data-tag={tag}>{tag}</li>)}
        </ul>
      )}
      {run.notes !== "" && (
        <>
          <p className="run-notes" data-annotation="note">{all || !excerpt.cut ? run.notes : excerpt.text}</p>
          {excerpt.cut && (
            <button type="button" className="link-button" aria-expanded={all} onClick={() => setAll(!all)}>{all ? "Show less" : "Show all"}</button>
          )}
        </>
      )}
    </div>
  );
}

/** The editor: the tags with a box to add more (with the tags already in use offered as you type), and the note with Save and Clear. `onDone` closes it (Esc closes the tag list first). */
export function AnnotationsEditor({ run, autoFocus = true }: { run: Run; autoFocus?: boolean }) {
  const actions = useAnnotations();
  const limits = actions?.limits ?? DEFAULT_LIMITS;
  const [typed, setTyped] = useState("");
  const [known, setKnown] = useState<TagCount[] | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [status, setStatus] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(run.notes);
  // The tags as the server last had them, and a line of saves: two adds in quick succession (a comma, then Enter) must each start from what the one before made, not from the run as it was
  // drawn when the key was pressed, or the second would undo the first.
  const tagsNow = useRef(run.tags);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  // Suggest (DESIGN.md §45.6): whether Ollama could be reached when the editor opened, what is being asked, and the suggestions that are shown (none is applied until it is pressed)
  const [reach, setReach] = useState<Reachability | null>(null);
  const [asking, setAsking] = useState(false);
  const [suggestions, setSuggestions] = useState<string[] | null>(null);
  const savedNotes = useRef(run.notes); // the note as the server last had it, to tell an edit from a change that came from another page
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const tagLabel = useId();
  const noteId = useId();
  const options = autocompleteFor(known ?? [], typed, run.tags);

  // The note the server has changed from another page (an event brings the run again) replaces the box only if the person has not been editing it
  useEffect(() => {
    if (draft === savedNotes.current) setDraft(run.notes);
    savedNotes.current = run.notes;
  }, [run.notes]);

  useEffect(() => {
    tagsNow.current = run.tags;
  }, [run.tags]);

  // Look at whether Ollama can be reached, once, when the editor opens and Suggest is otherwise available: a button that is greyed with the reason is better than one that fails
  useEffect(() => {
    if (!actions?.suggest.available) return;
    let stale = false;
    void actions.reachability().then((answer) => { if (!stale) setReach(answer); }).catch(() => undefined);
    return () => { stale = true; };
  }, []);

  useEffect(() => {
    if (autoFocus) input.current?.focus();
  }, []);

  if (!actions) return <AnnotationsView run={run} />;

  // Say what happened (polite) or what went wrong (an alert); each replaces the other, so the words under the box are always about the last thing done
  const say = (text: string) => { setProblem(""); setStatus(text); };
  const refuse = (text: string) => { setStatus(""); setProblem(text); };
  const words = (error: unknown) => (error instanceof Error ? error.message : String(error));

  // Read the tags in use the first time the box is used (and again a few seconds later, which the actions look after)
  const loadKnown = () => {
    void actions.knownTags().then(setKnown).catch(() => undefined);
  };

  // Save a new list of tags. Busy while the request is out, so that two quick presses are not two requests; the run that comes back is what the card shows.
  const save = (tags: string[], done: string): Promise<void> => {
    const turn = queue.current.then(async () => {
      setBusy(true);
      try {
        const answer = await actions.setTags({ ...run, tags: tagsNow.current }, tags);
        tagsNow.current = answer.tags;
        say(done);
        loadKnown();
      } catch (error) {
        tagsNow.current = run.tags; // the save did not happen: what the server has is what the card shows
        refuse(`Couldn't save the tags: ${words(error)}`);
      } finally {
        setBusy(false);
      }
    });
    queue.current = turn;
    return turn;
  };

  // Add what is typed (or one tag chosen from the list)
  // `keepBox`: the text after a comma stays in the box (a comma ends one tag and starts the next)
  const add = async (text: string, keepBox = false) => {
    const plan = planAdd(tagsNow.current, typedTags(text, limits.tag_chars), limits.tags_max);
    if (plan.added.length === 0 && plan.already.length === 0) return; // nothing usable was typed
    if (plan.added.length === 0) { if (!keepBox) setTyped(""); say(addedText(plan.added, plan.already)); return; }
    if (plan.tooMany) { refuse(tooManyText(limits.tags_max)); return; }
    if (!keepBox) setTyped("");
    setListOpen(false);
    setActive(-1);
    tagsNow.current = plan.next; // the next add, before this save has answered, starts from here
    await save(plan.next, addedText(plan.added, plan.already));
  };

  const remove = async (tag: string) => {
    await save(tagsNow.current.filter((other) => other !== tag), removedText(tag));
    input.current?.focus(); // the chip that was pressed is gone: keep the person's place
  };

  const saveNote = async (text: string) => {
    setBusy(true);
    try {
      const answer = await actions.setNotes(run, text);
      savedNotes.current = answer.notes;
      setDraft(answer.notes);
      say(noteSavedText(answer.notes));
    } catch (error) {
      refuse(`Couldn't save the note: ${words(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (options.length === 0) return;
      event.preventDefault();
      setListOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      // from nothing chosen, Down goes to the first and Up to the last
      setActive(active < 0 ? (step === 1 ? 0 : options.length - 1) : (active + step + options.length) % options.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      void add(listOpen && options[active] ? options[active].tag : typed);
    } else if (event.key === "Escape" && listOpen) {
      event.preventDefault();
      event.stopPropagation(); // closes the list, and not also the viewer or the card's editor
      setListOpen(false);
      setActive(-1);
    }
  };

  // Ask the model for tags. Only a suggestion is made: it is shown, and each one is added when it is pressed
  const why = suggestWhy(actions.suggest, reach);
  const suggest = async () => {
    setAsking(true);
    setSuggestions(null);
    setProblem("");
    try {
      const found = (await actions.suggestTags(run)).filter((tag) => !tagsNow.current.includes(tag));
      setSuggestions(found);
      say(found.length === 0 ? "Nothing new to suggest." : `${found.length} ${found.length === 1 ? "tag" : "tags"} suggested. Press one to add it.`);
    } catch (error) {
      refuse(`Couldn't get suggestions: ${words(error)}${error instanceof ApiError && error.hint ? ` ${error.hint}` : ""}`);
    } finally {
      setAsking(false);
    }
  };
  const take = async (tag: string) => {
    setSuggestions((list) => (list ? list.filter((other) => other !== tag) : list));
    await add(tag);
  };

  const overNote = draft.length > limits.notes_chars;
  return (
    <div className="annotations-editor" role="group" aria-label="Tags and note" data-annotations-editor>
      <p className="annotation-label" id={tagLabel}>Tags</p>
      {run.tags.length > 0 && (
        <ul className="tag-list" aria-labelledby={tagLabel}>
          {run.tags.map((tag) => (
            <li key={tag} className="tag-chip editable" data-tag={tag}>
              {tag}
              <button type="button" className="tag-remove" data-action="remove-tag" aria-label={`Remove the tag “${tag}”`} disabled={busy} onClick={() => void remove(tag)}><CloseIcon /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="tag-add">
        <span className="tag-combo">
          <input ref={input} type="text" className="tag-input" data-tag-input role="combobox" aria-label="Add a tag" aria-autocomplete="list" aria-expanded={listOpen && options.length > 0}
            aria-controls={listId} aria-activedescendant={listOpen && active >= 0 ? `${listId}-${active}` : undefined} placeholder="Add a tag" autoComplete="off" spellCheck={false}
            maxLength={limits.tag_chars * 4} value={typed}
            onFocus={() => { setListOpen(true); if (known === null) loadKnown(); }}
            onBlur={() => window.setTimeout(() => setListOpen(false), 120)} // a press on an option comes after the blur: the list stays long enough to be pressed
            onChange={(event) => {
              const text = event.target.value;
              setListOpen(true);
              setActive(-1);
              // a comma ends a tag: what is before it is added, and what is after it stays in the box
              if (text.includes(",")) {
                const at = text.lastIndexOf(",");
                setTyped(text.slice(at + 1));
                void add(text.slice(0, at), true);
              } else setTyped(text);
            }}
            onKeyDown={onKeyDown} />
          {listOpen && options.length > 0 && (
            <ul className="tag-options" id={listId} role="listbox" aria-label="Tags you use">
              {options.map((option, index) => (
                <li key={option.tag} id={`${listId}-${index}`} role="option" aria-selected={index === active} className={index === active ? "active" : undefined} data-tag-option={option.tag}
                  onMouseDown={(event) => event.preventDefault()} onClick={() => void add(option.tag)}>
                  {option.tag} <span className="tag-option-count">{option.count}</span>
                </li>
              ))}
            </ul>
          )}
        </span>
        <button type="button" className="button small" data-action="add-tag" disabled={busy || typed.trim() === ""} onClick={() => void add(typed)}>Add</button>
        <button type="button" className="button small" data-action="suggest" disabled={!!why || asking || canAnnotate(run) === false} aria-describedby={why ? `${tagLabel}-why` : undefined} onClick={() => void suggest()}
          title="Ask your Ollama model for a few tags from this run's words. They are only shown: you add each one yourself.">
          {asking ? "Asking…" : "Suggest"}
        </button>
      </div>
      {/* a greyed Suggest says why, in words: it must not be a button that is dead for no reason a person can see */}
      {why && <p className="annotation-status" id={`${tagLabel}-why`} data-suggest-why>{why}</p>}
      {suggestions !== null && suggestions.length > 0 && (
        <div className="tag-suggestions" data-suggestions>
          <ul className="tag-list" aria-label="Suggested tags">
            {suggestions.map((tag) => (
              <li key={tag}><button type="button" className="tag-chip suggestion" data-suggestion={tag} aria-label={`Add “${tag}”`} disabled={busy} onClick={() => void take(tag)}>+ {tag}</button></li>
            ))}
          </ul>
          <button type="button" className="link-button" data-action="dismiss-suggestions" onClick={() => setSuggestions(null)}>Dismiss suggestions</button>
        </div>
      )}
      <div className="annotation-note">
        <label className="annotation-label" htmlFor={noteId}>Note</label>
        <textarea id={noteId} className="note-input" data-note-input rows={3} maxLength={limits.notes_chars} value={draft} placeholder="Why this one matters" aria-describedby={`${noteId}-count`}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !overNote && draft !== savedNotes.current) { event.preventDefault(); void saveNote(draft); } }} />
        <div className="note-actions">
          <span className="note-count" id={`${noteId}-count`}>{noteCounter(draft, limits.notes_chars)}</span>
          <button type="button" className="button small primary" data-action="save-note" disabled={busy || overNote || draft.trim() === savedNotes.current.trim()} onClick={() => void saveNote(draft)}>Save</button>
          <button type="button" className="button small ghost" data-action="clear-note" disabled={busy || (draft === "" && savedNotes.current === "")} onClick={() => void saveNote("")}>Clear</button>
        </div>
      </div>
      {/* What happened, in words: polite for a confirmation, an alert for a refusal (the viewer's page behind it is hidden from a screen reader, so the answer is shown inside it) */}
      <p className="annotation-status" role="status" aria-live="polite" data-annotation-status>{status}</p>
      {problem && <p className="annotation-problem" role="alert" data-annotation-problem>{problem}</p>}
    </div>
  );
}

/** The block on a run's card: its tags and note, and a button that opens the editor in place. A run in the bin only reads, so it has no button. */
export function RunAnnotations({ run }: { run: Run }) {
  const actions = useAnnotations();
  const [open, setOpen] = useState(false);
  const editable = !!actions && canAnnotate(run);
  const label = run.tags.length > 0 || run.notes !== "" ? "Tags and notes" : "Add tags or a note";
  return (
    <div className="annotations" data-annotations>
      {!open && <AnnotationsView run={run} />}
      {editable && (
        <button type="button" className="link-button annotations-toggle" data-action="annotations" aria-expanded={open} onClick={() => setOpen(!open)}
          title="Tag this run and write a note about it: both can be searched">
          <TagIcon /> {open ? "Close tags and notes" : label}
        </button>
      )}
      {open && editable && <AnnotationsEditor run={run} />}
    </div>
  );
}

/** The button in a viewer's row of buttons that opens the tags and notes panel (DESIGN.md §45.3). It says how many tags the run has, so that a closed panel still tells something. */
export function ViewerAnnotationsButton({ run, open, panelId, onToggle, buttonRef }: {
  run: Run;
  open: boolean;
  panelId: string;
  onToggle: () => void;
  buttonRef?: React.Ref<HTMLButtonElement>;
}) {
  const actions = useAnnotations();
  if (!actions) return null;
  const count = run.tags.length;
  return (
    <button type="button" ref={buttonRef} className="button small" data-action="annotations" aria-expanded={open} aria-controls={panelId} onClick={onToggle}
      title="The tags and the note of this run: both can be searched">
      <TagIcon /> Tags and notes{count > 0 ? ` · ${count}` : ""}
    </button>
  );
}

/** The panel a viewer shows over the picture: the editor, or, for a run in the bin, which only reads, the tags and the note as they are. It is for the RUN whose picture is on the
 *  screen: tags and notes belong to the run, as Keep and projects do (§43.1). */
export function ViewerAnnotationsPanel({ run, panelId }: { run: Run; panelId: string }) {
  return (
    <div className="lightbox-panel" id={panelId} role="region" aria-label="Tags and notes" data-annotations-panel>
      {canAnnotate(run) ? <AnnotationsEditor run={run} /> : <AnnotationsView run={run} />}
      {!canAnnotate(run) && run.tags.length === 0 && run.notes === "" && <p className="annotation-status">No tags or note. A run in the bin only reads: restore it to add some.</p>}
    </div>
  );
}

/** What Esc does in a viewer while the panel is open (DESIGN.md §45.3): it does not close the viewer. If the tag list is open it is the box's to close; otherwise the panel closes and
 *  focus goes back to its button. Returns whether it took the key, so the viewer can leave the dialog open. */
export function panelEscape(event: { target: EventTarget | null; preventDefault: () => void }, open: boolean, close: () => void): boolean {
  if (!open) return false;
  event.preventDefault();
  const target = event.target as HTMLElement | null;
  if (target?.getAttribute?.("aria-expanded") !== "true") close();
  return true;
}
