# Demo backend — shared persistence & metrics (optional)

The GitHub Pages demo is a static site with an in-browser backend, so by default each visitor has their
own catalog in their own browser. This optional ~80-line Cloudflare Worker gives the demo two things a
static site can't do alone:

- **Shared persistence** — a product a shop owner adds in the demo is stored centrally, so **every future
  visitor sees it** (not just that one browser).
- **Anonymous metrics** — counts of a few product events (visits, product views, add-to-cart, checkout,
  orders, products added), shown on the site's [`/metrics`](../../site/metrics.html) page. No cookies,
  no personal data — just a random visitor id in `localStorage` so unique visitors can be counted.

It runs on Cloudflare's **free tier** (100k reads/day, 1k writes/day) and stores everything in one KV
namespace. There is no database and nothing to maintain. If you never deploy it, the demo still works
fully — it just stays per-browser and the metrics page shows your own browser's tally.

## Deploy (~5 minutes, free)

```bash
npm install -g wrangler        # Cloudflare's CLI
cd deploy/pages-backend
wrangler login

# 1. Create the KV namespace and copy the printed id into wrangler.toml (id = "…").
wrangler kv namespace create BAZAAR

# 2. Ship it.
wrangler deploy
# → https://babel-bazaar-demo.<your-subdomain>.workers.dev
```

## Point the demo at it

Bake the Worker URL into the Pages build with a repository variable, then redeploy Pages:

- GitHub → repo **Settings → Secrets and variables → Actions → Variables → New variable**
  - name `DEMO_SYNC_URL`, value `https://babel-bazaar-demo.<your-subdomain>.workers.dev`

The Pages workflow passes it to the build (`DEMO_SYNC_URL`), which bakes it into the demo and the site's
config. That's it — added products now persist for everyone and `/metrics` fills in.

**Try it without rebuilding:** append `?sync=<worker-url>` to the demo once (e.g.
`…/demo/?sync=https://babel-bazaar-demo.<sub>.workers.dev`); the URL is remembered in that browser.

## API

| Method & path | Body | Returns |
|---|---|---|
| `GET /state` | — | `{ products, categories }` — the shared catalog overlay |
| `POST /product` | `{ product }` | `{ ok }` — validate and upsert one product |
| `POST /event` | `{ name, vid }` | `{ ok }` — count one usage event |
| `GET /metrics` | — | `{ events, visitors, products_shared, since }` |
| `GET /health` | — | `{ ok: true }` |

The Worker validates and clamps every product field, only counts a fixed set of event names, caps the
overlay at 500 products and the visitor set at 20,000, and returns permissive CORS headers so the static
site can call it. For a high-traffic public demo, add a Cloudflare **Rate limiting** rule in front of it
(KV is last-write-wins, so bursts of writes can race — fine for a demo, not a system of record).

## Local check

`node ../../deploy/pages-backend/mock-server.mjs` runs a stand-in of this API on `localhost` (same
routes, in-memory) so you can point the demo at it with `?sync=http://127.0.0.1:8787` while developing.
