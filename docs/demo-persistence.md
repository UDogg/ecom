# Demo persistence & metrics

The GitHub Pages [demo](../site/index.html) is a static site with an in-browser backend, so by default
each visitor has their own catalog in their own browser (saved in `localStorage`, so it survives
reloads). Two things need a shared datastore, which a static site can't provide alone:

1. **A product one shop owner adds should be seen by every future visitor** — not just that browser.
2. **Usage metrics** across everyone — shown on the site's [`/metrics`](../site/metrics.html) page.

Both work **with no setup**. The Pages build automatically provisions a free
[jsonblob.com](https://jsonblob.com) document (no account, no key), caches its URL across deploys, and
bakes it in — so merging to `main` is all it takes. If provisioning ever fails, the build still succeeds
and the demo runs per-browser; it never breaks. For a **private, rate-limited** backend you control, point
it at the optional [Cloudflare Worker](../deploy/pages-backend/README.md) instead (one repo variable).

## How it works

| Layer | Without the backend | With the backend |
|---|---|---|
| Catalog you add to | `localStorage`, this browser only | shared: written to the document on save, merged from it on load |
| Orders & cart | this browser (private, by design) | unchanged — never shared between visitors |
| Metrics | this browser's own counts | global counts in the shared document |

The client side is small and fails safe (`web/src/api/sync.ts`, `web/src/lib/telemetry.ts`): every call
has a short timeout, and if the backend is unset, slow or down, the demo silently falls back to local
state and never blocks or breaks. Orders and carts are deliberately **not** shared — one visitor's basket
must never show up for another.

## Turning it on

Nothing to do — merge to `main`. The Pages build runs `site/provision-backend.mjs`, which creates a free
jsonblob document (no account), caches its URL (`actions/cache`) so the same document is reused on every
later deploy, and bakes it into the demo and the site config. A `DEMO_SYNC_URL` repo variable, if set,
overrides this with your own backend. To try a specific backend without rebuilding, append `?sync=<url>`
to the demo once; the browser remembers it.

## Privacy

No cookies. Telemetry is a fixed set of anonymous event counts (visit, product view, add-to-cart,
checkout, order paid, product added) plus a random visitor id kept in `localStorage` purely so unique
visitors can be counted — no personal data, ever. The shared document (anonymous counts, community
products, random visitor ids) lives on a free jsonblob.com store by default; point `DEMO_SYNC_URL` at the
Cloudflare Worker to keep it on infrastructure you control instead.
