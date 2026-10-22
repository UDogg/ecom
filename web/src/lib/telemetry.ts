// Privacy-friendly, dependency-free telemetry for the demo. No cookies, no third parties, no personal
// data — just anonymous counts of a few product events, kept locally and (when a shared backend is
// configured, see api/sync.ts) folded into it so the /metrics page can show how the demo is being used.
// A random visitor id in localStorage lets the backend count unique visitors without identifying anyone.
//
// Events are batched and flushed a couple of seconds apart (and when the tab is hidden), so the shared
// document is written rarely rather than on every click.
import { flushEvents, syncEnabled } from "../api/sync";

const COUNTS = "bb-telemetry-v1";
const VID = "bb-visitor";
const FLUSH_MS = 2500;

function visitorId(): string {
  try {
    let v = localStorage.getItem(VID);
    if (!v) { v = crypto.randomUUID(); localStorage.setItem(VID, v); }
    return v;
  } catch {
    return "anon";
  }
}

export function localCounts(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(COUNTS) || "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

function bump(name: string): void {
  try {
    const c = localCounts();
    c[name] = (c[name] || 0) + 1;
    localStorage.setItem(COUNTS, JSON.stringify(c));
  } catch { /* ignore */ }
}

let pending: Record<string, number> = {};
let timer: ReturnType<typeof setTimeout> | undefined;

function flush(): void {
  if (timer) { clearTimeout(timer); timer = undefined; }
  const batch = pending;
  pending = {};
  if (Object.keys(batch).length) flushEvents(batch, visitorId());
}

export function track(name: string): void {
  bump(name);
  if (!syncEnabled()) return;
  pending[name] = (pending[name] || 0) + 1;
  if (!timer) timer = setTimeout(flush, FLUSH_MS);
}

let visited = false;
export function trackVisit(): void {
  if (visited) return;
  visited = true;
  track("visit");
}

// Flush on the way out so a short visit still counts.
try {
  addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
  addEventListener("pagehide", flush);
} catch { /* non-browser */ }

export { syncEnabled };
