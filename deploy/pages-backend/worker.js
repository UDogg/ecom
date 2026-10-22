// Optional private backend for the GitHub Pages demo — a tiny, free Cloudflare Worker that stores the
// demo's shared state as one JSON document in KV. You do NOT need this: the Pages build provisions a
// free jsonblob.com document automatically (site/provision-backend.mjs). Deploy this only if you want a
// private, rate-limitable backend you control; then set a repo variable DEMO_SYNC_URL to this Worker's
// URL and the demo uses it instead.
//
// It serves exactly what the client needs (api/sync.ts): one JSON document.
//   GET  /   -> the document { products, categories, metrics, since }
//   PUT  /   -> replace the document (validated and capped)
// CORS is permissive so the static site can call it. KV is last-write-wins; for a busy public demo put
// a Cloudflare Rate limiting rule in front.

const KEY = "doc";
const MAX_PRODUCTS = 500;
const MAX_VISITORS = 20000;
const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, PUT, OPTIONS",
  "access-control-allow-headers": "content-type, accept",
};
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...cors } });

const str = (v, n) => (typeof v === "string" ? v.slice(0, n) : "");

function clean(doc) {
  const d = doc && typeof doc === "object" ? doc : {};
  const products = Array.isArray(d.products) ? d.products.filter((p) => p && typeof p.id === "string").slice(0, MAX_PRODUCTS) : [];
  const categories = Array.isArray(d.categories) ? d.categories.filter((c) => c && c.slug).map((c) => ({ slug: str(c.slug, 80), name: str(c.name, 120) || str(c.slug, 80) })) : [];
  const ev = d.metrics && typeof d.metrics.events === "object" ? d.metrics.events : {};
  const events = {};
  for (const k of Object.keys(ev).slice(0, 50)) if (typeof ev[k] === "number") events[str(k, 40)] = Math.min(ev[k], 1e12);
  const visitors = Array.isArray(d.metrics?.visitors) ? d.metrics.visitors.filter((v) => typeof v === "string").slice(-MAX_VISITORS) : [];
  return { products, categories, metrics: { events, visitors }, since: str(d.since, 40) || new Date().toISOString() };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    try {
      if (request.method === "GET") {
        const doc = (await env.BAZAAR.get(KEY, "json")) || { products: [], categories: [], metrics: { events: {}, visitors: [] } };
        return json(doc);
      }
      if (request.method === "PUT") {
        const body = await request.json().catch(() => null);
        if (!body) return json({ error: "bad json" }, 400);
        await env.BAZAAR.put(KEY, JSON.stringify(clean(body)));
        return json({ ok: true });
      }
      return json({ error: "method not allowed" }, 405);
    } catch (err) {
      return json({ error: String(err && err.message || err) }, 500);
    }
  },
};
