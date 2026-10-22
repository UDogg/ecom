// A local, in-memory stand-in for jsonblob.com (the demo's default shared backend), so you can develop
// and test the demo's persistence/metrics without the real service: the same create / GET / PUT a JSON
// document semantics the client uses.
//   node deploy/pages-backend/mock-server.mjs
//   JSONBLOB_API=http://127.0.0.1:8787/api/jsonBlob node site/provision-backend.mjs   # create one
//   open the demo with ?sync=http://127.0.0.1:8787/api/jsonBlob/<id>
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.PORT || 8787);
const store = new Map();
const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, PUT, OPTIONS",
  "access-control-allow-headers": "content-type, accept",
  "access-control-expose-headers": "Location",
};

createServer((req, res) => {
  if (req.method === "OPTIONS") return res.writeHead(204, cors).end();
  const url = new URL(req.url, "http://x");
  const path = url.pathname;
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    // Create: POST /api/jsonBlob  -> 201 + Location
    if (req.method === "POST" && path === "/api/jsonBlob") {
      const id = randomUUID();
      store.set(id, raw || "{}");
      return res.writeHead(201, { ...cors, location: `/api/jsonBlob/${id}` }).end(raw || "{}");
    }
    const m = path.match(/^\/api\/jsonBlob\/([\w-]+)$/);
    if (m) {
      const id = m[1];
      if (req.method === "GET") {
        if (!store.has(id)) return res.writeHead(404, cors).end();
        return res.writeHead(200, { ...cors, "content-type": "application/json" }).end(store.get(id));
      }
      if (req.method === "PUT") {
        store.set(id, raw || "{}");
        return res.writeHead(200, { ...cors, "content-type": "application/json" }).end(raw || "{}");
      }
    }
    res.writeHead(404, cors).end();
  });
}).listen(PORT, () => console.log(`mock jsonblob on http://127.0.0.1:${PORT}/api/jsonBlob`));
