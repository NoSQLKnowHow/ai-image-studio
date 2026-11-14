import * as Dialog from "@radix-ui/react-dialog";
import { useId, useRef, useState, type FormEvent, type RefObject } from "react";
import { entryCountText, entryReady, sayProblem, takenBy, triedNothing, usedLines, wordProblem } from "../pronunciation";
import type { PronunciationEntry, PronunciationLimits, PronunciationTry } from "../speechTypes";
import { isProblem, type PronunciationList, type Problem } from "../usePronunciations";

// The pronunciation list (DESIGN.md §44.3): the words an engine says wrongly and how they should be said, kept once for every engine and every voice. The dialog adds,
// changes and deletes entries, and has Try it, which shows exactly what would be spoken for a sentence (the words only: no engine runs and no sound is made).
//
// Every box has a label and the buttons that act on one entry say which (“Change Nguyen”), so that a screen reader can use the list; Enter sends a form and Escape
// leaves the box that is being changed; and what an action did is said in a status line, since a deleted row leaves nothing on the screen to show it.

function ProblemText({ problem }: { problem: Problem }) {
  return (
    <p className="form-error" role="alert">
      {problem.message}
      {problem.hint && <span className="hint"> {problem.hint}</span>}
    </p>
  );
}

interface EntryFormProps {
  limits: PronunciationLimits;
  initial: { word: string; say: string; match_case: boolean };
  entries: readonly PronunciationEntry[];
  exceptId?: string; // the entry being changed: its own word is not "taken"
  submitLabel: string;
  busy: boolean;
  problem: Problem | null;
  onSubmit: (value: { word: string; say: string; match_case: boolean }) => void;
  onCancel?: () => void;
  wordRef?: RefObject<HTMLInputElement | null>;
  labelPrefix?: string; // for the boxes of one row: "Nguyen: " keeps their names apart from the boxes of the add form
  autoFocus?: boolean;
}

/** The three boxes of an entry, for adding one and for changing one. The checks that can be made here are made here (so a request that is certain to be refused is not sent), and the
 *  server's own words, for everything else, are shown under the boxes. */
function EntryForm({ limits, initial, entries, exceptId, submitLabel, busy, problem, onSubmit, onCancel, wordRef, labelPrefix = "", autoFocus }: EntryFormProps) {
  const id = useId();
  const [word, setWord] = useState(initial.word);
  const [say, setSay] = useState(initial.say);
  const [matchCase, setMatchCase] = useState(initial.match_case);
  const wordWrong = wordProblem(word, limits);
  const sayWrong = sayProblem(say, word, limits);
  const taken = word.trim() && !wordWrong ? takenBy(entries, word, exceptId) : null;
  const takenText = taken ? `“${taken.word}” is already on the list. Change that entry instead.` : null;
  const ready = entryReady(word, say, limits) && !taken;
  const send = (event: FormEvent) => {
    event.preventDefault();
    if (ready && !busy) onSubmit({ word, say, match_case: matchCase });
  };
  const wrongWord = wordWrong ?? takenText;
  return (
    <form className="pron-form" data-editing={onCancel ? "true" : undefined} onSubmit={send} onKeyDown={(e) => { if (e.key === "Escape" && onCancel) { e.preventDefault(); e.stopPropagation(); onCancel(); } }}>
      <div className="pron-field">
        <label htmlFor={`${id}-word`}>{labelPrefix}Word or phrase</label>
        <input id={`${id}-word`} ref={wordRef} type="text" value={word} onChange={(e) => setWord(e.target.value)} disabled={busy} autoComplete="off" autoCapitalize="off" spellCheck={false}
          autoFocus={autoFocus} aria-invalid={wrongWord ? true : undefined} aria-describedby={wrongWord ? `${id}-word-wrong` : undefined} />
        {wrongWord && <p id={`${id}-word-wrong`} className="form-error">{wrongWord}</p>}
      </div>
      <div className="pron-field">
        <label htmlFor={`${id}-say`}>{labelPrefix}Said like</label>
        <input id={`${id}-say`} type="text" value={say} onChange={(e) => setSay(e.target.value)} disabled={busy} autoComplete="off" autoCapitalize="off" spellCheck={false}
          aria-invalid={sayWrong ? true : undefined} aria-describedby={sayWrong ? `${id}-say-wrong` : undefined} />
        {sayWrong && <p id={`${id}-say-wrong`} className="form-error">{sayWrong}</p>}
      </div>
      <label className="pron-case">
        <input type="checkbox" checked={matchCase} onChange={(e) => setMatchCase(e.target.checked)} disabled={busy} />
        <span>{labelPrefix}Match capitals exactly</span>
      </label>
      {problem && <ProblemText problem={problem} />}
      <div className="speech-actions">
        <button type="submit" className="button primary" disabled={!ready || busy}>{busy ? "Saving…" : submitLabel}</button>
        {onCancel && <button type="button" className="button" onClick={onCancel} disabled={busy}>Cancel</button>}
      </div>
    </form>
  );
}

interface RowProps {
  entry: PronunciationEntry;
  list: PronunciationList;
  limits: PronunciationLimits;
  onSaid: (text: string) => void; // what happened, for the status line
  onDeleted: () => void; // where focus goes when the row is gone
}

/** One entry: its words, and Change and Delete. Change turns the row into the form; focus goes back to Change when it is done. */
function EntryRow({ entry, list, limits, onSaid, onDeleted }: RowProps) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const changeButton = useRef<HTMLButtonElement>(null);

  const done = () => {
    setEditing(false);
    setProblem(null);
    setTimeout(() => changeButton.current?.focus(), 0); // after the row has drawn its buttons again
  };
  const save = async (value: { word: string; say: string; match_case: boolean }) => {
    setBusy(true);
    setProblem(null);
    const wrong = await list.change(entry.id, value);
    setBusy(false);
    if (wrong) return setProblem(wrong);
    onSaid(`Changed “${value.word.trim()}”.`);
    done();
  };
  const remove = async () => {
    setBusy(true);
    const wrong = await list.remove(entry.id);
    setBusy(false);
    if (wrong) return setProblem(wrong);
    onSaid(`Deleted “${entry.word}”.`);
    onDeleted();
  };

  if (editing) {
    return (
      <li className="pron-row editing">
        <EntryForm limits={limits} initial={entry} entries={list.entries} exceptId={entry.id} submitLabel="Save" busy={busy} problem={problem} onSubmit={(v) => void save(v)}
          onCancel={done} labelPrefix={`${entry.word}: `} autoFocus />
      </li>
    );
  }
  return (
    <li className="pron-row">
      <span className="pron-text">
        <strong className="pron-word">{entry.word}</strong>
        <span className="sr-only"> is said like </span>
        <span aria-hidden="true"> → </span>
        <span className="pron-say">{entry.say}</span>
        {entry.match_case && <span className="badge" title="Only the capitals as written are matched">Match capitals</span>}
      </span>
      <span className="pron-actions">
        <button ref={changeButton} type="button" className="button small" onClick={() => setEditing(true)} aria-label={`Change ${entry.word}`}>Change</button>
        <button type="button" className="button small ghost danger" onClick={() => void remove()} disabled={busy} aria-label={`Delete ${entry.word}`}>Delete</button>
      </span>
      {problem && <ProblemText problem={problem} />}
    </li>
  );
}

/** Try it: a sentence in, what would be spoken out. */
function TryIt({ list, limits }: { list: PronunciationList; limits: PronunciationLimits }) {
  const id = useId();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<PronunciationTry | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const tooLong = text.length > limits.try_chars;

  const run = async () => {
    setBusy(true);
    setProblem(null);
    const result = await list.tryIt(text);
    setBusy(false);
    if (isProblem(result)) {
      setAnswer(null);
      setProblem(result);
    } else {
      setAnswer(result);
    }
  };
  return (
    <section className="pron-try" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>Try it</h3>
      <label htmlFor={`${id}-text`}>A sentence to try</label>
      <textarea id={`${id}-text`} rows={2} value={text} onChange={(e) => { setText(e.target.value); setAnswer(null); }} aria-invalid={tooLong ? true : undefined}
        placeholder="Nguyen's winter was long." />
      {tooLong && <p className="form-error">Try a sentence or a paragraph: at most {limits.try_chars.toLocaleString("en")} characters.</p>}
      <div className="speech-actions">
        <button type="button" className="button" onClick={() => void run()} disabled={!text.trim() || tooLong || busy}>{busy ? "Working…" : "Show how it will be said"}</button>
      </div>
      {problem && <ProblemText problem={problem} />}
      <div aria-live="polite" className="pron-answer">
        {answer && (
          <>
            <p><strong>Spoken as:</strong> <span className="pron-spoken">{answer.spoken}</span></p>
            {answer.changed ? (
              <ul className="pron-used" aria-label="The entries that changed it">{usedLines(answer.used).map((line) => <li key={line}>{line}</li>)}</ul>
            ) : (
              <p className="hint">{triedNothing}</p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  list: PronunciationList;
  limits: PronunciationLimits;
}

/** The dialog. It is mounted while open, so what was half typed in it is not kept after it is closed. */
export function PronunciationDialog({ open, onOpenChange, list, limits }: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="voice-dialog pronunciation-dialog"
          // Escape in the boxes of an entry that is being changed leaves that entry (the form does it) and not the whole dialog; Radix hears the key first, so it is told here
          onEscapeKeyDown={(event) => { if ((event.target as HTMLElement | null)?.closest?.("[data-editing]")) event.preventDefault(); }}>
          <Dialog.Title>Pronunciation list</Dialog.Title>
          <Dialog.Description className="hint">
            Words an engine says wrongly, and how to say them. It is one list for every engine and voice, it changes only what is spoken and never what you read, and a reading keeps
            the list it was made with: to hear a change in a reading you have made, make it again.
          </Dialog.Description>
          {open && <Body list={list} limits={limits} />}
          <div className="confirm-actions">
            <Dialog.Close className="button">Close</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Body({ list, limits }: { list: PronunciationList; limits: PronunciationLimits }) {
  const id = useId();
  const [adding, setAdding] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [said, setSaid] = useState("");
  const [formKey, setFormKey] = useState(0); // a new form (and so empty boxes) after each entry is added
  const addWord = useRef<HTMLInputElement>(null);
  const full = list.entries.length >= limits.entries_max;

  const add = async (value: { word: string; say: string; match_case: boolean }) => {
    setAdding(true);
    setProblem(null);
    const wrong = await list.add(value);
    setAdding(false);
    if (wrong) return setProblem(wrong);
    setSaid(`Added “${value.word.trim()}”.`);
    setFormKey((n) => n + 1);
    setTimeout(() => addWord.current?.focus(), 0); // ready for the next one
  };

  return (
    <div className="voice-body pron-body">
      <section aria-labelledby={`${id}-add`}>
        <h3 id={`${id}-add`}>Add a word</h3>
        {full ? (
          <p className="form-error" role="status">The list holds {limits.entries_max.toLocaleString("en")} entries and is full. Delete one you no longer need.</p>
        ) : (
          <EntryForm key={formKey} limits={limits} initial={{ word: "", say: "", match_case: false }} entries={list.entries} submitLabel="Add" busy={adding} problem={problem}
            onSubmit={(v) => void add(v)} wordRef={addWord} />
        )}
      </section>

      <section aria-labelledby={`${id}-list`}>
        <h3 id={`${id}-list`}>Your list <span className="hint">({entryCountText(list.entries.length)})</span></h3>
        <p role="status" className="pron-said">{said}</p>
        {list.problem && <ProblemText problem={list.problem} />}
        {!list.loaded && !list.problem ? (
          <p role="status">Loading the list…</p>
        ) : list.entries.length === 0 ? (
          <p className="hint">Nothing here yet. Add a word an engine says wrongly and write it the way it should sound: <em>Nguyen</em> said like <em>win</em>.</p>
        ) : (
          <ul className="pron-list" aria-label="Your pronunciation list">
            {list.entries.map((entry) => (
              <EntryRow key={entry.id} entry={entry} list={list} limits={limits} onSaid={setSaid} onDeleted={() => addWord.current?.focus()} />
            ))}
          </ul>
        )}
      </section>

      <TryIt list={list} limits={limits} />
    </div>
  );
}
