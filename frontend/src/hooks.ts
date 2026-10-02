import { useCallback, useEffect, useRef, useState } from "react";

/** Current time, refreshed on an interval (for "3 min ago" and countdowns). */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export type ToastKind = "info" | "success" | "error";
export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

export function useToasts(timeoutMs = 6000) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);
  const push = useCallback(
    (kind: ToastKind, text: string) => {
      const id = nextId.current++;
      setToasts((all) => [...all.slice(-3), { id, kind, text }]);
      setTimeout(() => dismiss(id), kind === "error" ? timeoutMs * 2 : timeoutMs);
    },
    [dismiss, timeoutMs],
  );
  return { toasts, push, dismiss };
}

/** Copy text. navigator.clipboard only exists on https/localhost, and the studio is usually opened
 *  over plain http on the LAN, so fall back to the older execCommand route there. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

/** Radix only returns focus to a Dialog.Trigger, and the studio's dialogs are opened from elsewhere
 *  (the Options button, a thumbnail, a card's Delete), so focus would drop to <body> on close.
 *  Spread `props` onto Dialog.Content: it remembers what had focus when the dialog opened and puts
 *  it back, falling back to the prompt box when that element is gone. `redirectTo` picks a different
 *  target for the next close, for when the opener is about to disappear (a deleted run's card). */
export function useReturnFocus() {
  const opener = useRef<HTMLElement | null>(null);
  const redirect = useRef<HTMLElement | null>(null);
  const props = {
    onOpenAutoFocus: () => {
      // Radix fires this before it moves focus into the dialog.
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    },
    onCloseAutoFocus: (event: Event) => {
      event.preventDefault();
      const target = [redirect.current, opener.current].find((el) => el?.isConnected) ?? document.getElementById("prompt");
      opener.current = redirect.current = null;
      target?.focus({ preventScroll: true });
    },
  };
  const redirectTo = (element: HTMLElement | null) => {
    redirect.current = element;
  };
  return { props, redirectTo };
}
