import { useEffect, useRef, useState, type RefObject } from "react";
import { untilText } from "../format";
import { useNarrow } from "../hooks";
import { MODEL_LABEL, modelAction, modelActionLabel, modelActionTitle, modelAnnouncement, type ModelAction, type ModelAsk } from "../model";
import { MUSIC_MODEL_NAME } from "../music";
import { NEXT_THEME, applyTheme, readTheme, type ThemeChoice } from "../theme";
import type { ModelName, Status, WorkerState, WorkerStatus } from "../types";
import { ChipIcon, EjectIcon, MonitorIcon, MoonIcon, SparkleIcon, SunIcon } from "./icons";

// The pill names the model the worker holds (DESIGN.md §26.1): "Image model ready", "Loading music model…".
function pillLabel(state: WorkerState, model: ModelName): string {
  const name = MODEL_LABEL[model];
  switch (state) {
    case "unloaded":
      return "Model not loaded"; // no model is loaded, so there is none to name
    case "loading":
      return `Loading ${name.toLowerCase()}…`;
    case "ready":
      return `${name} ready`;
    case "busy":
      return model === "music" ? "Making music" : "Generating";
    case "error":
      return `${name} problem`;
    case "unavailable":
      return `${name} unavailable`;
  }
}

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
  unloaded: "It loads automatically for the next run, which takes a while. The Load button loads it now instead, so it is ready when you are.",
  loading: "Loading the model into memory.",
  ready: "Loaded. It unloads after the idle timeout to give the Spark's memory back. Only one model is loaded at a time.",
  busy: "Working on a run.",
  error: "The last attempt failed. The Load button, or the next run, tries again.",
  unavailable: "The server can't run the model as it's set up.",
};

function pipelineName(w: WorkerStatus): string {
  if (w.model === "music") return w.pipeline === "fake" ? "Test pipeline (fake music, no GPU)" : MUSIC_MODEL_NAME;
  return w.pipeline === "fake" ? "Test pipeline (fake images, no GPU)" : "Qwen-Image-2.1";
}

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
  const needs = w.model === "music" ? mem.music_min_free_gb : mem.min_free_gb;
  const full = pillLabel(w.state, w.model);

  return (
    <div className="pill-wrap" ref={wrap}>
      <button type="button" ref={pillRef} className={`pill pill-${w.state}`} aria-expanded={open} aria-controls="model-details" aria-label={narrow ? full : undefined}
        onClick={() => setOpen((o) => !o)}>
        <span className="dot" aria-hidden="true" />
        <span className="pill-label">{narrow ? SHORT_LABELS[w.state] : full}</span>
        {until && <span className="pill-sub">· unloads in {until}</span>}
      </button>
      {open && (
        <div id="model-details" className="pill-panel" role="region" aria-label="Model details">
          <p className="pill-panel-title">{full}</p>
          <p>{EXPLAIN[w.state]}</p>
          {w.detail && <p className="pill-detail">{w.detail}</p>}
          {w.hint && <p className="hint">{w.hint}</p>}
          <dl>
            <dt>Pipeline</dt>
            <dd>{pipelineName(w)}</dd>
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
                  {needs !== null && needs !== undefined ? ` (needs ${needs} GB to load)` : ""}
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

/** Load model / Unload model beside the pill (DESIGN.md §25, §26.1). Which one is offered is `modelAction`'s rule. */
function ModelButton({ action, worker, busy, onPress }: { action: ModelAction; worker: WorkerStatus; busy: boolean; onPress: () => void }) {
  const label = modelActionLabel(action);
  return (
    <button type="button" className="button model-action" data-action={action.kind} data-model={action.model} disabled={busy} aria-label={label}
      title={modelActionTitle(action, worker)} onClick={onPress}>
      {action.kind === "load" ? <ChipIcon /> : <EjectIcon />}
      <span className="model-action-label">{label}</span>
    </button>
  );
}

interface HeaderProps {
  status: Status | null;
  now: number;
  busy: boolean; // a Load or Unload request is on its way
  tab: ModelName; // the model the open tab is about: the button acts on it
  musicAvailable: boolean;
  onModel: (action: ModelAction) => Promise<boolean>; // asks the server; false if it was refused or failed
}

export function Header({ status, now, busy, tab, musicAvailable, onModel }: HeaderProps) {
  const pill = useRef<HTMLButtonElement>(null);
  const [ask, setAsk] = useState<ModelAsk | null>(null);
  const [said, setSaid] = useState("");
  const worker = status?.worker ?? null;
  const action = worker ? modelAction(worker, tab, musicAvailable) : null;

  // Say what became of a Load or Unload the user asked for, since the button they pressed is gone by then.
  useEffect(() => {
    if (!ask || !worker) return;
    const heard = modelAnnouncement(ask, worker);
    if (!heard) return;
    setSaid(heard.text);
    if (heard.settled) setAsk(null);
  }, [ask, worker]);

  const press = async (chosen: ModelAction) => {
    if (!worker) return;
    setAsk({ ...chosen, from: worker.state, fromModel: worker.model });
    setSaid(chosen.kind === "load" ? `Loading the ${chosen.model} model…` : `Unloading the ${chosen.model} model…`);
    pill.current?.focus(); // the button is about to go away, and focus must not be lost with it
    if (!(await onModel(chosen))) {
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
          {action && worker && <ModelButton action={action} worker={worker} busy={busy} onPress={() => void press(action)} />}
          <ModelPill status={status} now={now} pillRef={pill} />
          <ThemeToggle />
        </div>
      </div>
      <p className="sr-only" role="status" aria-live="polite">{said}</p>
    </header>
  );
}
