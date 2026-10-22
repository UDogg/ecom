// Babel Bazaar demo backend — a tiny, free Cloudflare Worker that gives the static GitHub Pages demo
// two things it can't have on its own: shared persistence (a product one shop owner adds is seen by
// every future visitor) and anonymous usage counters for the /metrics page. State lives in one KV
// namespace; there is no database and no personal data.
//
// Routes (all CORS-enabled):
//   GET  /state    -> { products, categories }      the shared catalog overlay
//   POST /product  -> { ok }                         body { product }: upsert one product
//   POST /event    -> { ok }                         body { name, vid }: count one usage event
//   GET  /metrics  -> { events, visitors, products_shared, since }
//   GET  /health   -> { ok: true }
//
// Deploy: see README.md (wrangler, one KV namespace, ~5 minutes, free tier).

const CATALOG = "catalog";
const METRICS = "metrics";
const MAX_PRODUCTS = 500;      // cap the shared overlay
const MAX_VISITORS = 20000;    // cap the unique-visitor set (count is approximate beyond this)
const EVENTS = ["visit", "product_view", "add_to_cart", "checkout", "order_paid", "product_created", "stress_open"];
const STR = { sku: 40, name: 200, description: 2000, slug: 80, currency: 3 };

const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type",
  "access-control-max-age": "86400",
};
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...cors } });

const str = (v, max) => (typeof v === "string" ? v.slice(0, max) : "");
const int = (v, lo, hi) => { const n = Math.trunc(Number(v)); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : lo; };

// Accept only known fields, clamped — never trust the client's shape.
function cleanProduct(p) {
  if (!p || typeof p !== "object" || typeof p.id !== "string" || p.id.length > 64) return null;
  const sku = str(p.sku, STR.sku).trim();
  const name = str(p.name, STR.name).trim();
  if (!sku || !name) return null;
  return {
    id: p.id,
    sku,
    name,
    description: str(p.description, STR.description),
    price_minor: int(p.price_minor, 0, 1_000_000_000_000),
    currency: str(p.currency, STR.currency) || "USD",
    category: str(p.category, STR.slug) || "home",
    status: p.status === "archived" ? "archived" : "active",
    version: int(p.version, 1, 1_000_000),
    available_quantity: int(p.available_quantity, 0, 1_000_000_000),
    in_stock: int(p.available_quantity, 0, 1_000_000_000) > 0,
    created_at: str(p.created_at, 40) || new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

async function getCatalog(env) {
  return (await env.BAZAAR.get(CATALOG, "json")) || { products: [], categories: [], rev: 0 };
}

async function upsertProduct(env, raw) {
  const p = cleanProduct(raw);
  if (!p) return false;
  const cat = await getCatalog(env);
  const i = cat.products.findIndex((x) => x.id === p.id);
  if (i >= 0) cat.products[i] = p;
  else cat.products.push(p);
  if (!cat.categories.some((c) => c.slug === p.category)) {
    cat.categories.push({ slug: p.category, name: p.category });
  }
  // Keep the newest MAX_PRODUCTS by updated_at.
  if (cat.products.length > MAX_PRODUCTS) {
    cat.products.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
    cat.products = cat.products.slice(0, MAX_PRODUCTS);
  }
  cat.rev = (cat.rev || 0) + 1;
  cat.updated = new Date().toISOString();
  await env.BAZAAR.put(CATALOG, JSON.stringify(cat));
  return true;
}

async function recordEvent(env, name, vid) {
  if (!EVENTS.includes(name)) return;
  const m = (await env.BAZAAR.get(METRICS, "json")) || { events: {}, visitors: [], since: new Date().toISOString() };
  m.events[name] = (m.events[name] || 0) + 1;
  if (typeof vid === "string" && vid && !m.visitors.includes(vid)) {
    m.visitors.push(vid);
    if (m.visitors.length > MAX_VISITORS) m.visitors = m.visitors.slice(-MAX_VISITORS);
  }
  await env.BAZAAR.put(METRICS, JSON.stringify(m));
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    try {
      if (request.method === "GET" && (path === "/" || path === "/health")) return json({ ok: true });

      if (request.method === "GET" && path === "/state") {
        const cat = await getCatalog(env);
        return json({ products: cat.products, categories: cat.categories });
      }

      if (request.method === "POST" && path === "/product") {
        const body = await request.json().catch(() => null);
        const ok = body && (await upsertProduct(env, body.product));
        return json({ ok: !!ok }, ok ? 200 : 400);
      }

      if (request.method === "POST" && path === "/event") {
        const body = await request.json().catch(() => null);
        if (body && typeof body.name === "string") await recordEvent(env, body.name, body.vid);
        return json({ ok: true });
      }

      if (request.method === "GET" && path === "/metrics") {
        const [cat, m] = await Promise.all([getCatalog(env), env.BAZAAR.get(METRICS, "json")]);
        const metrics = m || { events: {}, visitors: [], since: null };
        return json({
          events: metrics.events || {},
          visitors: (metrics.visitors || []).length,
          products_shared: (cat.products || []).length,
          since: metrics.since || null,
        });
      }

      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: "server error", detail: String(err && err.message || err) }, 500);
    }
  },
};
