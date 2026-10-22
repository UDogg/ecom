# Demo persistence & metrics

The GitHub Pages [demo](../site/index.html) is a static site with an in-browser backend, so by default
each visitor has their own catalog in their own browser (saved in `localStorage`, so it survives
reloads). Two things need a shared datastore, which a static site can't provide alone:

1. **A product one shop owner adds should be seen by every future visitor** — not just that browser.
2. **Usage metrics** across everyone — shown on the site's [`/metrics`](../site/metrics.html) page.

Both are handled by an **optional**, free [Cloudflare Worker](../deploy/pages-backend/README.md) with one
KV namespace. Deploy it and point the demo at it, and the two features light up for everyone. Skip it and
the demo still works fully — per-browser — and the metrics page shows your own browser's tally.

## How it works

| Layer | Without the backend | With the backend |
|---|---|---|
| Catalog you add to | `localStorage`, this browser only | shared: `POST /product` on save, merged from `GET /state` on load |
| Orders & cart | this browser (private, by design) | unchanged — never shared between visitors |
| Metrics | this browser's own counts | global counts via `POST /event` → `GET /metrics` |

The client side is small and fails safe (`web/src/api/sync.ts`, `web/src/lib/telemetry.ts`): every call
has a short timeout, and if the backend is unset, slow or down, the demo silently falls back to local
state and never blocks or breaks. Orders and carts are deliberately **not** shared — one visitor's basket
must never show up for another.

## Turning it on

1. Deploy the Worker — see [`deploy/pages-backend/README.md`](../deploy/pages-backend/README.md)
   (`wrangler deploy`, ~5 minutes, free tier).
2. Add a repository **Actions variable** `DEMO_SYNC_URL` with the Worker's URL.
3. Re-run the **Pages** workflow. The build bakes the URL into the demo and the site config.

To try it without rebuilding, append `?sync=<worker-url>` to the demo once; the browser remembers it.

## Privacy

No cookies and no third parties. Telemetry is a fixed set of anonymous event counts (visit, product
view, add-to-cart, checkout, order paid, product added, stress-arena open) plus a random visitor id kept
in `localStorage` purely so unique visitors can be counted. No personal data is collected or sent.
