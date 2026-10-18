// A small router on signals: the location is state, links are plain <a href> intercepted on click.
// The live app uses real paths (the BFF serves index.html for them); the static demo uses #/paths so
// any static host can serve it.
import { signal } from "@preact/signals";

const HASH = __DEMO__;

export interface Location { path: string; query: URLSearchParams }

function read(): Location {
  if (HASH) {
    const [path = "/", search = ""] = (location.hash.slice(1) || "/").split("?");
    return { path, query: new URLSearchParams(search) };
  }
  return { path: location.pathname, query: new URLSearchParams(location.search) };
}

export const loc = signal<Location>(read());

/** The href for an in-app path. */
export const href = (to: string) => (HASH ? "#" + to : to);

export function navigate(to: string, opts: { replace?: boolean; keepScroll?: boolean } = {}) {
  const url = href(to);
  const current = HASH ? location.hash || "#/" : location.pathname + location.search;
  if (url !== current) history[opts.replace ? "replaceState" : "pushState"](null, "", url);
  const apply = () => {
    loc.value = read();
    if (!opts.keepScroll) window.scrollTo({ top: 0 });
  };
  // Cross-fade between pages where the browser supports it.
  const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
  if (doc.startViewTransition && !opts.keepScroll && !matchMedia("(prefers-reduced-motion: reduce)").matches) doc.startViewTransition(apply);
  else apply();
}

/** Replace some query parameters of the current page (filters, paging) without a history entry per keystroke. */
export function setQuery(changes: Record<string, string | number | undefined | null>, replace = true) {
  const q = new URLSearchParams(loc.value.query);
  for (const [k, v] of Object.entries(changes)) {
    if (v === undefined || v === null || v === "") q.delete(k);
    else q.set(k, String(v));
  }
  const s = q.toString();
  navigate(loc.value.path + (s ? "?" + s : ""), { replace, keepScroll: true });
}

addEventListener("popstate", () => (loc.value = read()));
if (HASH) addEventListener("hashchange", () => (loc.value = read()));

// Same-origin links navigate in-app; modified clicks, downloads and external links behave normally.
document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = (e.target as Element).closest?.("a");
  if (!a || a.target || a.hasAttribute("download") || a.dataset.native !== undefined) return;
  const raw = a.getAttribute("href");
  if (!raw) return;
  if (HASH ? !raw.startsWith("#/") : !raw.startsWith("/") || raw.startsWith("//")) return;
  e.preventDefault();
  navigate(HASH ? raw.slice(1) : raw);
});

/** Matches a pattern such as /products/:id; returns the parameters or null. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const p = pattern.split("/"), s = path.split("/");
  if (p.length !== s.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < p.length; i++) {
    const seg = p[i]!, val = s[i]!;
    if (seg.startsWith(":")) out[seg.slice(1)] = decodeURIComponent(val);
    else if (seg !== val) return null;
  }
  return out;
}
