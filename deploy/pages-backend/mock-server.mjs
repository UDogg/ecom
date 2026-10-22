// A local, in-memory stand-in for worker.js: the same routes on http://127.0.0.1:8787, for developing
// the demo's shared-persistence and metrics against something real without deploying to Cloudflare.
//   node deploy/pages-backend/mock-server.mjs   then open the demo with ?sync=http://127.0.0.1:8787
import { createServer } from "node:http";

const PORT = Number(process.env.PORT || 8787);
const EVENTS = ["visit", "product_view", "add_to_cart", "checkout", "order_paid", "product_created", "stress_open"];
const catalog = { products: [], categories: [], rev: 0 };
const metrics = { events: {}, visitors: new Set(), since: new Date().toISOString() };

const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type",
};
const send = (res, status, body) => res.writeHead(status, { "content-type": "application/json", ...cors }).end(JSON.stringify(body));

function cleanProduct(p) {
  if (!p || typeof p !== "object" || typeof p.id !== "string") return null;
  if (!p.sku || !p.name) return null;
  return { ...p, status: p.status === "archived" ? "archived" : "active", updated_at: new Date().toISOString() };
}

createServer((req, res) => {
  if (req.method === "OPTIONS") return res.writeHead(204, cors).end();
  const path = new URL(req.url, "http://x").pathname.replace(/\/+$/, "") || "/";
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    const body = raw ? JSON.parse(raw) : {};
    if (req.method === "GET" && (path === "/" || path === "/health")) return send(res, 200, { ok: true });
    if (req.method === "GET" && path === "/state") return send(res, 200, { products: catalog.products, categories: catalog.categories });
    if (req.method === "POST" && path === "/product") {
      const p = cleanProduct(body.product);
      if (!p) return send(res, 400, { ok: false });
      const i = catalog.products.findIndex((x) => x.id === p.id);
      if (i >= 0) catalog.products[i] = p; else catalog.products.push(p);
      if (!catalog.categories.some((c) => c.slug === p.category)) catalog.categories.push({ slug: p.category, name: p.category });
      return send(res, 200, { ok: true });
    }
    if (req.method === "POST" && path === "/event") {
      if (EVENTS.includes(body.name)) { metrics.events[body.name] = (metrics.events[body.name] || 0) + 1; if (body.vid) metrics.visitors.add(body.vid); }
      return send(res, 200, { ok: true });
    }
    if (req.method === "GET" && path === "/metrics")
      return send(res, 200, { events: metrics.events, visitors: metrics.visitors.size, products_shared: catalog.products.length, since: metrics.since });
    return send(res, 404, { error: "not found" });
  });
}).listen(PORT, () => console.log(`mock demo backend on http://127.0.0.1:${PORT}`));
