import { useEffect, useMemo, useState } from "react";
import { FOR_KINDS, FOR_LABELS } from "../builder";
import type { BuilderLimits, Comparison, DiffRun, ForKind, PromptDetail, PromptVersion, VersionRun, VersionSource } from "../builderTypes";
import type { Project } from "../types";

/** A saved prompt opened (DESIGN.md §37.2, criteria 229-231, 245, 246): its title, star, tags, project and what it is for; every version, newest first, with the pictures made
 *  from each; Make this the current one (a copy as the newest: nothing is ever written over); Compare two versions with the changed words marked in text; Flesh out; Delete. */
export const SOURCE_LABELS: Record<VersionSource, string> = { builder: "the builder", hand: "by hand", wording: "a wording", revision: "a revision" };

const settingLines = (settings: Record<string, unknown>): string[] =>
  Object.entries(settings).map(([key, value]) => `${key}: ${typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}`);

export function Diff({ runs, label }: { runs: DiffRun[]; label: string }) {
  return (
    <p className="builder-text" aria-label={label}>
      {runs.map((run, i) => run.op === "same" ? <span key={i}>{run.text} </span> : run.op === "del"
        ? <del key={i} aria-label={`removed: ${run.text}`}>{run.text} </del> : <ins key={i} aria-label={`added: ${run.text}`}>{run.text} </ins>)}
    </p>
  );
}

function ComparisonView({ result }: { result: Comparison }) {
  const rows = result.settings;
  return (
    <section className="builder-compare" aria-label={`Version ${result.a.n} compared with version ${result.b.n}`}>
      <h3 className="builder-heading">Version {result.a.n} → version {result.b.n}</h3>
      {!result.changed && <p className="hint">No differences.</p>}
      <h4 className="builder-label">Words</h4>
      <Diff runs={result.text} label="Changed words" />
      {result.negative.length > 0 && <><h4 className="builder-label">Negative prompt</h4><Diff runs={result.negative} label="Changed negative prompt" /></>}
      {rows.length > 0 && (
        <table className="builder-settings-table" aria-label="Settings compared">
          <thead><tr><th>Setting</th><th>Version {result.a.n}</th><th>Version {result.b.n}</th><th>Change</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} data-status={r.status}>
                <th scope="row">{r.key}</th>
                <td>{r.before === undefined || r.before === null ? "—" : String(r.before)}</td>
                <td>{r.after === undefined || r.after === null ? "—" : String(r.after)}</td>
                <td>{r.status === "same" ? "same" : r.status === "changed" ? "changed" : r.status === "added" ? "added" : "removed"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function RunThumbs({ runs, onRefine }: { runs: VersionRun[]; onRefine?: (run: VersionRun) => void }) {
  if (!runs.length) return null;
  return (
    <ul className="builder-version-runs" aria-label="Pictures made from this version">
      {runs.map((run) => (
        <li key={run.run_id}>
          {run.images.length > 0
            ? run.images.map((img) => <img key={img.id} src={img.thumb_url} alt={`Made from this version${run.draft ? " (quick try)" : ""}`} width={64} height={64} loading="lazy" />)
            : <span className="badge">{run.mode} · {run.status}</span>}
          {run.draft && <span className="badge">Draft</span>}
          {onRefine && <button type="button" className="button small ghost" onClick={() => onRefine(run)}>Refine in Prompt builder</button>}
        </li>
      ))}
    </ul>
  );
}

export function PromptDetailView({ prompt, projects, binDays, limits, problem, compare, onClose, onChange, onMakeCurrent, onFlesh, onBin, onRestore, onForever, onOpenChat, onUse, onGenerate, onQuick, onRefine, quickProblem }: {
  prompt: PromptDetail; projects: Project[]; binDays: number; limits: BuilderLimits; problem: string | null;
  compare: (a: number, b: number) => Promise<Comparison>;
  onClose: () => void;
  onChange: (fields: { title?: string; for?: ForKind; favourite?: boolean; tags?: string[]; project_id?: string | null }) => Promise<string | null>;
  onMakeCurrent: (n: number) => Promise<void>;
  onFlesh: () => void; onBin: () => void; onRestore: () => void; onForever: () => void; onOpenChat: (id: string) => void;
  onUse?: (version: PromptVersion) => void; onGenerate?: (version: PromptVersion) => void; onQuick?: (version: PromptVersion) => void;
  onRefine?: (run: VersionRun, version: PromptVersion) => void;
  quickProblem: string | null;
}) {
  const [title, setTitle] = useState(prompt.title);
  const [tags, setTags] = useState(prompt.tags.join(", "));
  const [note, setNote] = useState<string | null>(null);
  const [chosen, setChosen] = useState<number[]>([]);
  const [result, setResult] = useState<Comparison | null>(null);
  const [comparing, setComparing] = useState(false);
  const inBin = !!prompt.deleted_at;
  // what is typed is replaced only when the prompt itself changed: a fresh copy from the server (after another change, or from another page) must not wipe the words half-typed
  const savedTags = prompt.tags.join(", ");
  useEffect(() => { setTitle(prompt.title); setTags(savedTags); }, [prompt.id, prompt.title, savedTags]);
  useEffect(() => { setChosen([]); setResult(null); }, [prompt.id]);

  // two versions chosen: compare them (oldest first, so the arrow reads forward in time)
  useEffect(() => {
    if (chosen.length !== 2) { setResult(null); return; }
    const [a, b] = [...chosen].sort((x, y) => x - y);
    let live = true;
    setComparing(true);
    compare(a, b).then((r) => live && setResult(r)).catch((e) => live && setNote(e instanceof Error ? e.message : "They could not be compared.")).finally(() => live && setComparing(false));
    return () => { live = false; };
  }, [chosen, compare, prompt.versions.length]);

  const toggle = (n: number) => setChosen((c) => (c.includes(n) ? c.filter((x) => x !== n) : c.length >= 2 ? [c[1], n] : [...c, n]));
  const save = async (fields: Parameters<typeof onChange>[0]) => setNote(await onChange(fields));
  const newest = prompt.versions[0];
  const project = useMemo(() => projects.find((p) => p.id === prompt.project_id)?.name ?? null, [projects, prompt.project_id]);

  return (
    <section className="builder-detail" aria-label={`Prompt “${prompt.title}”`}>
      <p><button type="button" className="link-button" onClick={onClose}>← Back to the chat</button></p>
      {inBin && (
        <div className="builder-bin-banner" role="status">
          <p>In Deleted{prompt.purge_at ? `, gone for good ${prompt.purge_at.slice(0, 10)}` : ""}. Its pictures are untouched.</p>
          <button type="button" className="button small" onClick={onRestore}>Restore</button>{" "}
          <button type="button" className="button small danger-solid" onClick={onForever}>Delete forever</button>
        </div>
      )}
      <header className="builder-detail-head">
        <label className="sr-only" htmlFor="builder-prompt-title">Title</label>
        <input id="builder-prompt-title" className="builder-title-input" value={title} maxLength={limits.title_chars} disabled={inBin} onChange={(e) => setTitle(e.target.value)}
          onBlur={() => title.trim() && title.trim() !== prompt.title ? void save({ title: title.trim() }) : setTitle(prompt.title)} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
        <button type="button" className="icon-button builder-star" aria-pressed={prompt.favourite} aria-label={prompt.favourite ? "Take the star off" : "Star this prompt"} disabled={inBin} onClick={() => void save({ favourite: !prompt.favourite })}>{prompt.favourite ? "★" : "☆"}</button>
      </header>
      <div className="builder-detail-fields">
        <label>For <select value={prompt.for} disabled={inBin} onChange={(e) => void save({ for: e.target.value as ForKind })}>{FOR_KINDS.map((k) => <option key={k} value={k}>{FOR_LABELS[k]}</option>)}</select></label>
        <label>Project <select value={prompt.project_id ?? ""} disabled={inBin} onChange={(e) => void save({ project_id: e.target.value || null })} aria-label="Project of this prompt">
          <option value="">No project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label>Tags <input value={tags} disabled={inBin} onChange={(e) => setTags(e.target.value)}
          onBlur={() => { const next = tags.split(",").map((t) => t.trim()).filter(Boolean); if (next.join(",") !== prompt.tags.join(",")) void save({ tags: next }); }} aria-label="Tags" /></label>
      </div>
      {project && <p className="hint">In the project “{project}”.</p>}
      {(problem || note) && <p className="form-error" role="alert">{problem ?? note}</p>}

      {!inBin && (
        <div className="builder-prompt-buttons">
          <button type="button" className="button primary" onClick={onFlesh}>Flesh out</button>
          {onUse && newest && <button type="button" className="button" onClick={() => onUse(newest)}>Use in {prompt.for === "vector" ? "Vector" : prompt.for === "music" ? "Music" : "Images"}</button>}
          {onGenerate && newest && <button type="button" className="button" onClick={() => onGenerate(newest)}>Generate now</button>}
          {onQuick && newest && <button type="button" className="button" onClick={() => onQuick(newest)} disabled={!!quickProblem} title={quickProblem ?? undefined}>Quick try</button>}
          <button type="button" className="button ghost" onClick={onBin}>Delete</button>
        </div>
      )}

      <h2 className="builder-heading">Versions</h2>
      <p className="hint">{prompt.versions.length === 1 ? "One version." : `${prompt.versions.length} versions.`} Saving a change adds the next one; nothing is written over. Choose two to compare.</p>
      <ol className="builder-versions" aria-label="Versions">
        {prompt.versions.map((v, index) => (
          <li key={v.n} className="builder-version">
            <div className="builder-version-head">
              <strong>Version {v.n}</strong>
              <span className="hint">{SOURCE_LABELS[v.source]} · {v.created_at.slice(0, 16).replace("T", " ")}</span>
              {index === 0 && <span className="badge">Current</span>}
              <label className="builder-compare-pick"><input type="checkbox" checked={chosen.includes(v.n)} onChange={() => toggle(v.n)} aria-label={`Compare version ${v.n}`} /> Compare</label>
            </div>
            {v.note && <p className="hint">{v.note}</p>}
            <p className="builder-text">{v.text}</p>
            {v.negative && <p className="builder-text hint">Negative prompt: {v.negative}</p>}
            {settingLines(v.settings).length > 0 && <ul className="builder-settings-list">{settingLines(v.settings).map((l) => <li key={l}>{l}</li>)}</ul>}
            {!inBin && (
              <div className="builder-version-buttons">
                {index > 0 && <button type="button" className="button small" onClick={() => void onMakeCurrent(v.n)}>Make this the current one</button>}
                {index > 0 && onUse && <button type="button" className="button small ghost" onClick={() => onUse(v)}>Use this one</button>}
              </div>
            )}
            <RunThumbs runs={v.runs} onRefine={!inBin && onRefine ? (run) => onRefine(run, v) : undefined} />
          </li>
        ))}
      </ol>
      {comparing && <p className="hint">Comparing…</p>}
      {result && <ComparisonView result={result} />}

      {prompt.chats.length > 0 && (
        <>
          <h2 className="builder-heading">Chats about it</h2>
          <ul className="builder-detail-chats">{prompt.chats.map((c) => <li key={c.id}><button type="button" className="link-button" onClick={() => onOpenChat(c.id)}>{c.title}</button></li>)}</ul>
        </>
      )}
      {binDays > 0 && !inBin && <p className="hint">Delete moves it to Deleted for {binDays} {binDays === 1 ? "day" : "days"}. Its pictures stay and say it was deleted.</p>}
    </section>
  );
}
