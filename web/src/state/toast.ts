import { signal } from "@preact/signals";

export interface Toast { id: number; text: string; tone: "ok" | "error" | "info"; action?: { label: string; run: () => void } }

export const toasts = signal<Toast[]>([]);
let next = 1;

export function toast(text: string, tone: Toast["tone"] = "ok", action?: Toast["action"]) {
  const t: Toast = { id: next++, text, tone, action };
  // A success replaces an earlier error; never more than three at once.
  const keep = tone === "error" ? toasts.value : toasts.value.filter((x) => x.tone !== "error");
  toasts.value = [...keep.slice(-2), t];
  setTimeout(() => dismiss(t.id), tone === "error" ? 6000 : 3500);
}

export const dismiss = (id: number) => (toasts.value = toasts.value.filter((t) => t.id !== id));
