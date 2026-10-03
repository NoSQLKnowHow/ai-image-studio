import { useEffect, useRef, useState, type RefObject } from "react";
import { untilText } from "../format";
import { useNarrow } from "../hooks";
import { MODEL_ACTION_LABEL, MODEL_ACTION_TITLE, modelAction, modelAnnouncement, type ModelAsk, type ModelKind } from "../model";
import { NEXT_THEME, applyTheme, readTheme, type ThemeChoice } from "../theme";
import type { Status, WorkerState } from "../types";
import { ChipIcon, EjectIcon, MonitorIcon, MoonIcon, SparkleIcon, SunIcon } from "./icons";

const LABELS: Record<WorkerState, string> = {
  unloaded: "Model not loaded",
  loading: "Loading model…",
  ready: "Model ready",
  busy: "Generating",
  error: "Model problem",
  unavailable: "Model unavailable",
};

// On a phone the header has room for the Load/Unload button and a short pill, not the full words (DESIGN.md §25.1).
const SHORT_LABELS: Record<WorkerState, string> = {
  unloaded: "Unloaded",
  loading: "Loading…",
  ready: "Ready",
  busy: "Working",
  error: "Problem",
  unavailable: "Unavailable",
};

const EXPLAIN: Record<WorkerState, string> = {
  unloaded: "It loads automatically for the next run, which takes a while. Load model loads it now instead, so it is ready when you are.",
  loading: "Loading the model into memory.",
  ready: "Loaded. It unloads after the idle timeout to give the Spark's memory back.",
  busy: "Working on a run.",
  error: "The last attempt failed. Load model, or the next run, tries again.",
  unavailable: "The server can't run the model as it's set up.",
};

function ModelPill({ status, now, pillRef }: { status: Status | null; now: number; pillRef: RefObject<HTMLButtonElement | null> }) {
  const [open, setOpen] = useState(false);
  const narrow = useNarrow();
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onPointer = (e: PointerEvent) => wrap.current && !wrap.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  if (!status) return <span className="pill pill-unknown"><span className="dot" />Connecting…</span>;
  const w = status.worker;
  const until = w.state === "ready" ? untilText(w.unload_at, now) : null;
  const mem = status.memory;

  return (
    <div className="pill-wrap" ref={wrap}>
      <button type="button" ref={pillRef} className={`pill pill-${w.state}`} aria-expanded={open} aria-controls="model-details"
        onClick={() => setOpen((o) => !o)}>
        <span className="dot" aria-hidden="true" />
        <span className="pill-label">{(narrow ? SHORT_LABELS : LABELS)[w.state]}</span>
        {until && <span className="pill-sub">· unloads in {until}</span>}
      </button>
      {open && (
        <div id="model-details" className="pill-panel" role="region" aria-label="Model details">
          <p className="pill-panel-title">{LABELS[w.state]}</p>
          <p>{EXPLAIN[w.state]}</p>
          {w.detail && <p className="pill-detail">{w.detail}</p>}
          {w.hint && <p className="hint">{w.hint}</p>}
          <dl>
            <dt>Pipeline</dt>
            <dd>{w.pipeline === "fake" ? "Test pipeline (fake images, no GPU)" : "Qwen-Image-2.1"}</dd>
            {w.device?.name && (
              <>
                <dt>GPU</dt>
                <dd>{w.device.name}{w.device.capability ? ` (compute ${w.device.capability})` : ""}</dd>
              </>
            )}
            {mem.available_gb !== undefined && mem.total_gb !== undefined && (
              <>
                <dt>Memory free</dt>
                <dd>
                  {mem.available_gb} of {mem.total_gb} GB
                  {mem.min_free_gb !== null ? ` (needs ${mem.min_free_gb} GB to load)` : ""}
                </dd>
              </>
            )}
            {until && (
              <>
                <dt>Unloads in</dt>
                <dd>{until}</dd>
              </>
            )}
          </dl>
        </div>
      )}
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeChoice>(readTheme);
  const names: Record<ThemeChoice, string> = { system: "System", light: "Light", dark: "Dark" };
  const icon = theme === "light" ? <SunIcon /> : theme === "dark" ? <MoonIcon /> : <MonitorIcon />;
  return (
    <button type="button" className="button ghost theme-toggle"
      aria-label={`Theme: ${names[theme]}. Click for ${names[NEXT_THEME[theme]]}.`}
      onClick={() => {
        const next = NEXT_THEME[theme];
        applyTheme(next);
        setTheme(next);
      }}>
      {icon}
      <span className="theme-label">{names[theme]}</span>
    </button>
  );
}

/** Load model / Unload model beside the pill (DESIGN.md §25). Which one is offered is `modelAction`'s rule. */
function ModelButton({ kind, busy, onPress }: { kind: ModelKind; busy: boolean; onPress: () => void }) {
  const label = MODEL_ACTION_LABEL[kind];
  return (
    <button type="button" className="button model-action" data-action={kind} disabled={busy} aria-label={label} title={MODEL_ACTION_TITLE[kind]}
      onClick={onPress}>
      {kind === "load" ? <ChipIcon /> : <EjectIcon />}
      <span className="model-action-label">{label}</span>
    </button>
  );
}

interface HeaderProps {
  status: Status | null;
  now: number;
  busy: boolean; // a Load or Unload request is on its way
  onModel: (kind: ModelKind) => Promise<boolean>; // asks the server; false if it was refused or failed
}

export function Header({ status, now, busy, onModel }: HeaderProps) {
  const pill = useRef<HTMLButtonElement>(null);
  const [ask, setAsk] = useState<ModelAsk | null>(null);
  const [said, setSaid] = useState("");
  const worker = status?.worker ?? null;
  const action = worker ? modelAction(worker) : null;

  // Say what became of a Load or Unload the user asked for, since the button they pressed is gone by then.
  useEffect(() => {
    if (!ask || !worker) return;
    const heard = modelAnnouncement(ask, worker);
    if (!heard) return;
    setSaid(heard.text);
    if (heard.settled) setAsk(null);
  }, [ask, worker]);

  const press = async (kind: ModelKind) => {
    if (!worker) return;
    setAsk({ kind, from: worker.state });
    setSaid(kind === "load" ? "Loading the model…" : "Unloading the model…");
    pill.current?.focus(); // the button is about to go away, and focus must not be lost with it
    if (!(await onModel(kind))) {
      setAsk(null);
      setSaid(""); // the refusal is reported on screen by whoever asked
    }
  };

  return (
    <header className="app-header">
      <div className="header-inner">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true"><SparkleIcon /></span>
          <h1>
            AI Image Studio
            {status && <span className="brand-version"> v{status.version}</span>}
          </h1>
        </div>
        <div className="header-right">
          {action && <ModelButton kind={action} busy={busy} onPress={() => void press(action)} />}
          <ModelPill status={status} now={now} pillRef={pill} />
          <ThemeToggle />
        </div>
      </div>
      <p className="sr-only" role="status" aria-live="polite">{said}</p>
    </header>
  );
}
