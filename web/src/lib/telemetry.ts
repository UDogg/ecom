// Privacy-friendly, dependency-free telemetry for the demo. No cookies, no third parties, no personal
// data — just anonymous counts of a handful of product events, kept locally and (when a shared backend
// is configured, see api/sync.ts) sent to it so the /metrics page can show how the demo is being used.
// A random visitor id in localStorage lets the backend count unique visitors without identifying anyone.
import { sendEvent, syncEnabled } from "../api/sync";

const COUNTS = "bb-telemetry-v1";
const VID = "bb-visitor";

function visitorId(): string {
  try {
    let v = localStorage.getItem(VID);
    if (!v) {
      v = crypto.randomUUID();
      localStorage.setItem(VID, v);
    }
    return v;
  } catch {
    return "anon";
  }
}

/** This browser's own event tally — shown on the /metrics page when no shared backend is configured. */
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

export function track(name: string): void {
  bump(name);
  try { sendEvent(name, visitorId()); } catch { /* best-effort */ }
}

let visited = false;
/** Count one visit per page load (deduped within the session). */
export function trackVisit(): void {
  if (visited) return;
  visited = true;
  track("visit");
}

export { syncEnabled };
