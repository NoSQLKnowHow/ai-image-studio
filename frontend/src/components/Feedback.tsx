import type { Toast } from "../hooks";
import type { Connection } from "../store";
import type { Status } from "../types";
import { CloseIcon } from "./icons";

export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="toasts" aria-live="polite" aria-atomic="false">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
          <span>{t.text}</span>
          {t.action && (
            <button type="button" className="button small" data-action="toast-action" onClick={() => { t.action?.run(); onDismiss(t.id); }}>
              {t.action.label}
            </button>
          )}
          <button type="button" className="button ghost icon-only small" aria-label="Dismiss" onClick={() => onDismiss(t.id)}>
            <CloseIcon />
          </button>
        </div>
      ))}
    </div>
  );
}

export function ConnectionBanner({ connection, serverStopping }: { connection: Connection; serverStopping: boolean }) {
  if (connection === "open") return null;
  if (serverStopping) return <div className="banner" role="status">The studio server is restarting. Reconnecting…</div>;
  if (connection === "lost") return <div className="banner" role="status">Lost the connection to the studio. Reconnecting…</div>;
  return null;
}

export function QueueBar({ status }: { status: Status | null }) {
  if (!status) return null;
  const { running, queued, cap } = status.queue;
  if (!running && !queued) return null;
  return (
    <p className="queue-bar" role="status">
      {running ? "Generating 1 run" : "Starting"}
      {queued ? ` · ${queued} waiting (room for ${Math.max(0, cap - queued)} more)` : ""}
    </p>
  );
}
