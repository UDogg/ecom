// Optional shared backend for the GitHub Pages demo. The demo is a static site with an in-browser
// backend, so by default every browser keeps its own catalog in localStorage. Point it at a tiny
// key-value endpoint (deploy/pages-backend — a free Cloudflare Worker) and products a shop owner adds
// are shared with every future visitor, and anonymous usage counters are recorded for the /metrics page.
//
// The URL is resolved, in order, from: a `?sync=<url>` query param (remembered), a saved value, or the
// build-time DEMO_SYNC_URL (vite define). Everything here is best-effort with a short timeout: if no URL
// is set, or the backend is slow or down, the demo falls back to local-only and never blocks or breaks.

const BUILD_URL = typeof __SYNC_URL__ !== "undefined" ? __SYNC_URL__ : "";
const LS_KEY = "bb-sync-url";
const TIMEOUT = 4000;

function clean(u: string): string {
  return (u || "").trim().replace(/\/+$/, "");
}

export function syncBase(): string {
  try {
    const q = new URL(location.href).searchParams.get("sync");
    if (q !== null) {
      const v = clean(q);
      try { localStorage.setItem(LS_KEY, v); } catch { /* ignore */ }
      return v;
    }
    const saved = localStorage.getItem(LS_KEY);
    if (saved !== null) return clean(saved);
  } catch { /* ignore */ }
  return clean(BUILD_URL);
}

export function syncEnabled(): boolean {
  return syncBase() !== "";
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T | null> {
  const base = syncBase();
  if (!base) return null;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const res = await fetch(base + path, {
      method,
      headers: body !== undefined ? { "content-type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      mode: "cors",
    });
    if (!res.ok) return null;
    return (await res.json().catch(() => null)) as T | null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export interface SharedState {
  products?: unknown[];
  categories?: { slug: string; name: string }[];
}

/** The community catalog overlay (products added or edited by any owner), or null if unavailable. */
export function pullState(): Promise<SharedState | null> {
  return call<SharedState>("GET", "/state");
}

/** Share one product with every future visitor. Best-effort and non-blocking. */
export function pushProduct(product: unknown): void {
  if (!syncEnabled()) return;
  void call("POST", "/product", { product });
}

/** Record one anonymous usage event (for the /metrics page). Best-effort, same transport as the rest. */
export function sendEvent(name: string, vid: string): void {
  if (!syncEnabled()) return;
  void call("POST", "/event", { name, vid });
}

export interface Metrics {
  events: Record<string, number>;
  visitors: number;
  products_shared: number;
  since?: string | null;
}

export function fetchMetrics(): Promise<Metrics | null> {
  return call<Metrics>("GET", "/metrics");
}
