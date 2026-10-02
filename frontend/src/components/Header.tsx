import { useEffect, useRef, useState } from "react";
import { untilText } from "../format";
import { NEXT_THEME, applyTheme, readTheme, type ThemeChoice } from "../theme";
import type { Status, WorkerState } from "../types";
import { MonitorIcon, MoonIcon, SparkleIcon, SunIcon } from "./icons";

const LABELS: Record<WorkerState, string> = {
  unloaded: "Model not loaded",
  loading: "Loading model…",
  ready: "Model ready",
  busy: "Generating",
  error: "Model problem",
  unavailable: "Model unavailable",
};

const EXPLAIN: Record<WorkerState, string> = {
  unloaded: "It loads automatically for the next run. The first load takes a while.",
  loading: "Loading the model into memory.",
  ready: "Loaded. It unloads after the idle timeout to give the Spark's memory back.",
  busy: "Working on a run.",
  error: "The last attempt failed. The next run tries again.",
  unavailable: "The server can't run the model as it's set up.",
};

function ModelPill({ status, now }: { status: Status | null; now: number }) {
  const [open, setOpen] = useState(false);
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
      <button type="button" className={`pill pill-${w.state}`} aria-expanded={open} aria-controls="model-details"
        onClick={() => setOpen((o) => !o)}>
        <span className="dot" aria-hidden="true" />
        <span className="pill-label">{LABELS[w.state]}</span>
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

export function Header({ status, now }: { status: Status | null; now: number }) {
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
          <ModelPill status={status} now={now} />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
