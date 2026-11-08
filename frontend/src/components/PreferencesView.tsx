import { useState } from "react";
import type { BuilderLimits, Preference } from "../builderTypes";
import type { Project } from "../types";

/** The standing preferences (DESIGN.md §37.2, criterion 233): short notes the builder is told in every reply, each with a switch, Edit and Delete, and where it applies: everywhere or one
 *  project. A note of a project that has been deleted is turned off and kept, listed apart, and cannot be switched on until it has a place again. */
function Row({ pref, projects, limit, onChange, onDelete }: {
  pref: Preference; projects: Project[]; limit: number;
  onChange: (id: string, fields: { text?: string; enabled?: boolean; project_id?: string | null }) => Promise<string | null>; onDelete: (id: string) => void;
}) {
  const [text, setText] = useState(pref.text);
  const [editing, setEditing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const run = async (fields: Parameters<typeof onChange>[1]) => setProblem(await onChange(pref.id, fields));
  return (
    <li className="builder-pref">
      <label className="builder-switch"><input type="checkbox" checked={pref.enabled} onChange={(e) => void run({ enabled: e.target.checked })} aria-label={`${pref.enabled ? "Turn off" : "Turn on"}: ${pref.text}`} /></label>
      {editing ? (
        <input value={text} maxLength={limit} aria-label="Edit the note" autoFocus onChange={(e) => setText(e.target.value)}
          onBlur={() => { setEditing(false); if (text.trim() && text.trim() !== pref.text) void run({ text: text.trim() }); else setText(pref.text); }}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { setText(pref.text); setEditing(false); } }} />
      ) : (
        <span className="builder-pref-text">{pref.text}{pref.source === "suggestion" && <span className="hint"> (suggested)</span>}</span>
      )}
      <select value={pref.orphaned ? "" : pref.project_id ?? "everywhere"} aria-label={`Where “${pref.text}” applies`}
        onChange={(e) => void run({ project_id: e.target.value === "everywhere" ? null : e.target.value })}>
        {pref.orphaned && <option value="" disabled>No longer in a project</option>}
        <option value="everywhere">Everywhere</option>
        {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      <button type="button" className="button small ghost" onClick={() => setEditing(true)} aria-label={`Edit “${pref.text}”`}>Edit</button>
      <button type="button" className="button small ghost" onClick={() => onDelete(pref.id)} aria-label={`Delete “${pref.text}”`}>Delete</button>
      {problem && <span className="form-error" role="alert">{problem}</span>}
    </li>
  );
}

export function PreferencesView({ preferences, projects, limits, onAdd, onChange, onDelete }: {
  preferences: Preference[]; projects: Project[]; limits: BuilderLimits;
  onAdd: (text: string, projectId: string | null) => Promise<string | null>;
  onChange: (id: string, fields: { text?: string; enabled?: boolean; project_id?: string | null }) => Promise<string | null>;
  onDelete: (id: string) => void;
}) {
  const [text, setText] = useState("");
  const [where, setWhere] = useState("everywhere");
  const [problem, setProblem] = useState<string | null>(null);
  const live = preferences.filter((p) => !p.orphaned);
  const orphaned = preferences.filter((p) => p.orphaned);
  const add = async () => {
    const words = await onAdd(text.trim(), where === "everywhere" ? null : where);
    setProblem(words);
    if (!words) setText("");
  };
  return (
    <section className="builder-preferences" aria-label="Preferences">
      <h2 className="builder-heading">Preferences</h2>
      <p className="hint">Standing notes the builder is told in every reply, such as “photographic, natural light”. It may suggest one; only you add it. {preferences.length} of {limits.preferences_max}.</p>
      <form className="builder-pref-add" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void add(); }}>
        <label className="sr-only" htmlFor="builder-pref-text">New note</label>
        <input id="builder-pref-text" value={text} maxLength={limits.preference_chars} placeholder="A new standing note" onChange={(e) => setText(e.target.value)} />
        <select value={where} onChange={(e) => setWhere(e.target.value)} aria-label="Where the new note applies">
          <option value="everywhere">Everywhere</option>{projects.map((p) => <option key={p.id} value={p.id}>This project: {p.name}</option>)}
        </select>
        <button type="submit" className="button primary" disabled={!text.trim()}>Add</button>
      </form>
      {problem && <p className="form-error" role="alert">{problem}</p>}
      {live.length === 0 && <p className="hint">No notes yet.</p>}
      <ul className="builder-pref-list" aria-label="Notes">{live.map((p) => <Row key={p.id} pref={p} projects={projects} limit={limits.preference_chars} onChange={onChange} onDelete={onDelete} />)}</ul>
      {orphaned.length > 0 && (
        <>
          <h3 className="builder-label">No longer in a project</h3>
          <p className="hint">These belonged to a project that was deleted. They are off. Give one a place to switch it on.</p>
          <ul className="builder-pref-list" aria-label="Notes no longer in a project">{orphaned.map((p) => <Row key={p.id} pref={p} projects={projects} limit={limits.preference_chars} onChange={onChange} onDelete={onDelete} />)}</ul>
        </>
      )}
    </section>
  );
}
