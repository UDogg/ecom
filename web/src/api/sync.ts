// Optional shared backend for the GitHub Pages demo, as a single JSON document.
//
// The demo is a static site with an in-browser backend, so by default each browser keeps its own
// catalog. When a backend URL is set, the whole shared state — community products, their categories and
// anonymous usage counters — lives in one JSON document that the client reads and writes with plain GET
// and PUT. That is exactly what a jsonblob.com blob is, so the Pages build can provision one with no
// account and bake its URL in (site/provision-backend.mjs); a small Cloudflare Worker or anything else
// that serves GET/PUT of a JSON doc works just as well.
//
// The URL comes from `?sync=<url>` (remembered), a saved value, or the build-time DEMO_SYNC_URL. Writes
// are serialised per tab and done read-merge-write, so a product add and a metrics update don't clobber
// each other. Everything is best-effort with a short timeout: no URL, or a slow/err backend, and the
// demo falls back to local-only — it never blocks or breaks.

const BUILD_URL = typeof __SYNC_URL__ !== "undefined" ? __SYNC_URL__ : "";
const LS_KEY = "bb-sync-url";
const TIMEOUT = 5000;
const MAX_PRODUCTS = 500;
const MAX_VISITORS = 20000;

export interface Doc {
  products: SharedProduct[];
  categories: { slug: string; name: string }[];
  metrics: { events: Record<string, number>; visitors: string[] };
  since?: string;
}
/** A shared product: id is required; any other catalog fields ride along at runtime. */
export interface SharedProduct { id: string; sku?: string; name?: string; category?: string; updated_at?: string }

function emptyDoc(): Doc {
  return { products: [], categories: [], metrics: { events: {}, visitors: [] }, since: new Date().toISOString() };
}

export function syncBase(): string {
  try {
    const q = new URL(location.href).searchParams.get("sync");
    if (q !== null) {
      const v = q.trim().replace(/\/+$/, "");
      try { localStorage.setItem(LS_KEY, v); } catch { /* ignore */ }
      return v;
    }
    const saved = localStorage.getItem(LS_KEY);
    if (saved !== null) return saved.trim().replace(/\/+$/, "");
  } catch { /* ignore */ }
  return (BUILD_URL || "").trim().replace(/\/+$/, "");
}

export function syncEnabled(): boolean {
  return syncBase() !== "";
}

async function fetchJson(url: string, init?: RequestInit): Promise<unknown | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const res = await fetch(url, { mode: "cors", signal: ctrl.signal, ...init });
    if (!res.ok) return null;
    return await res.json().catch(() => null);
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Read the shared document (or null if there is no backend / it is unreachable). */
export async function getDoc(): Promise<Doc | null> {
  const url = syncBase();
  if (!url) return null;
  const d = (await fetchJson(url)) as Partial<Doc> | null;
  if (!d || typeof d !== "object") return null;
  return {
    products: Array.isArray(d.products) ? d.products : [],
    categories: Array.isArray(d.categories) ? d.categories : [],
    metrics: { events: (d.metrics?.events as Record<string, number>) || {}, visitors: Array.isArray(d.metrics?.visitors) ? d.metrics!.visitors : [] },
    since: d.since,
  };
}

async function putDoc(doc: Doc): Promise<void> {
  const url = syncBase();
  if (!url) return;
  await fetchJson(url, {
    method: "PUT",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(doc),
  });
}

// Serialise writes within the tab so read-merge-write steps don't overlap.
let chain: Promise<unknown> = Promise.resolve();
function enqueue(task: () => Promise<void>): Promise<void> {
  const next = chain.then(task, task);
  chain = next.catch(() => {});
  return next;
}

/** The community catalog overlay (products any owner added), or null. */
export async function pullState(): Promise<{ products: SharedProduct[]; categories: { slug: string; name: string }[] } | null> {
  const d = await getDoc();
  return d ? { products: d.products, categories: d.categories } : null;
}

/** Share one product with every future visitor (read-merge-write). */
export function pushProduct(product: SharedProduct): void {
  if (!syncEnabled() || !product || typeof product.id !== "string") return;
  void enqueue(async () => {
    const d = (await getDoc()) || emptyDoc();
    const i = d.products.findIndex((p) => p.id === product.id);
    if (i >= 0) d.products[i] = product; else d.products.push(product);
    if (product.category && !d.categories.some((c) => c.slug === product.category)) {
      d.categories.push({ slug: product.category, name: String(product.category) });
    }
    if (d.products.length > MAX_PRODUCTS) {
      d.products.sort((a, b) => (String(a.updated_at) < String(b.updated_at) ? 1 : -1));
      d.products = d.products.slice(0, MAX_PRODUCTS);
    }
    await putDoc(d);
  });
}

/** Fold a batch of anonymous event counts + this visitor into the shared metrics (read-merge-write). */
export function flushEvents(counts: Record<string, number>, vid: string): void {
  if (!syncEnabled() || Object.keys(counts).length === 0) return;
  void enqueue(async () => {
    const d = (await getDoc()) || emptyDoc();
    for (const [name, n] of Object.entries(counts)) d.metrics.events[name] = (d.metrics.events[name] || 0) + n;
    if (vid && !d.metrics.visitors.includes(vid)) {
      d.metrics.visitors.push(vid);
      if (d.metrics.visitors.length > MAX_VISITORS) d.metrics.visitors = d.metrics.visitors.slice(-MAX_VISITORS);
    }
    await putDoc(d);
  });
}

export interface Metrics {
  events: Record<string, number>;
  visitors: number;
  products_shared: number;
  since?: string | null;
}

export async function fetchMetrics(): Promise<Metrics | null> {
  const d = await getDoc();
  if (!d) return null;
  return { events: d.metrics.events, visitors: d.metrics.visitors.length, products_shared: d.products.length, since: d.since || null };
}
