import type { BuilderStatus, BuilderTest } from "../builderTypes";
import { WORDS } from "../builder";
import { EjectIcon } from "./icons";

/** The Prompt builder's connection to Ollama (DESIGN.md §37.2, criteria 222, 223, 249): whether it is set up, whether the server and the model are there, and the two buttons that
 *  ask Ollama something: Test connection, and Unload from memory (which only asks; the studio never starts or stops Ollama). The address is never shown: the page does not have it. */
export function ConnectionBar({ status, problem, test, testing, unloading, onTest, onUnload }: {
  status: BuilderStatus | null;
  problem: string | null;
  test: BuilderTest | null;
  testing: boolean;
  unloading: boolean;
  onTest: () => void;
  onUnload: () => void;
}) {
  if (status && !status.configured) return <NotSetUp />;
  const ready = !!status && status.reachable && !status.message;
  const words = problem ?? status?.message ?? null;
  return (
    <section className="builder-connection" aria-label="Ollama connection">
      <p className={`builder-connection-line${ready ? " ready" : words ? " trouble" : ""}`} role="status">
        <span className="builder-dot" aria-hidden="true" />
        <span>
          {status === null && !problem ? "Checking Ollama…" : ready ? `Ollama is ready: ${status!.model}` : words}
          {!ready && status?.hint ? <span className="hint"> {status.hint}</span> : null}
        </span>
      </p>
      <div className="builder-connection-buttons">
        <button type="button" className="button small" onClick={onTest} disabled={testing} aria-busy={testing || undefined}>{testing ? "Testing…" : "Test connection"}</button>
        <button type="button" className="button small ghost" onClick={onUnload} disabled={unloading || !status?.reachable}
          title="Asks Ollama to take its model out of memory, so that the image model has the room. The studio never does this by itself.">
          <EjectIcon /> {unloading ? "Unloading…" : "Unload from memory"}
        </button>
      </div>
      {test && <TestResult test={test} />}
    </section>
  );
}

export function NotSetUp() {
  return (
    <section className="empty-state builder-not-set-up" aria-label="The Prompt builder is not set up">
      <p className="empty-title">The Prompt builder isn't set up</p>
      <p>{WORDS.notSetUp}</p>
      <p className="hint">Saved prompts, versions, comparing, projects and preferences below work without it.</p>
    </section>
  );
}

/** What Test connection found (criterion 223): Ollama's version, its models, whether yours is among them, and whether it can look at pictures. */
export function TestResult({ test }: { test: BuilderTest }) {
  return (
    <dl className="builder-test" aria-label="What Test connection found">
      <dt>Ollama</dt>
      <dd>{test.reachable ? `version ${test.version}` : test.message ?? "can't be reached"}</dd>
      {test.reachable && (
        <>
          <dt>Models</dt>
          <dd>{test.models.length ? test.models.join(", ") : "none"}</dd>
          <dt>{test.model}</dt>
          <dd>{test.model_present ? "is there" : "is not there"}{test.hint && !test.model_present ? ` — ${test.hint}` : ""}</dd>
          <dt>Pictures</dt>
          <dd>{test.vision ? "the model can look at pictures" : "the model can't look at pictures"}{test.context_length ? `; it allows ${test.context_length.toLocaleString()} tokens` : ""}</dd>
        </>
      )}
    </dl>
  );
}
