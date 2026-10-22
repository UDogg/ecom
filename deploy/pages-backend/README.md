# Demo backend (optional — you don't need to do anything)

The GitHub Pages demo keeps its shared state — products any shop owner adds, and anonymous usage
counters for the [`/metrics`](../../site/metrics.html) page — in **one JSON document**. The Pages build
**provisions that document automatically** on a free [jsonblob.com](https://jsonblob.com) (no account, no
key), caches its URL across deploys, and bakes it in. So:

- **A product one owner adds is seen by every future visitor.**
- **The metrics page shows everyone's usage.**

…with **no setup**. Merge to `main`, Pages builds, and it works. If provisioning ever fails (service down,
offline), the build still succeeds and the demo runs per-browser — it never breaks.

This folder is the **optional upgrade**: a tiny Cloudflare Worker that stores the same document in your
own KV, so you get a **private, rate-limitable** backend instead of a public blob. Deploy it only if you
want that.

## Optional: use your own Cloudflare Worker

```bash
npm install -g wrangler
cd deploy/pages-backend
wrangler login
wrangler kv namespace create BAZAAR          # paste the printed id into wrangler.toml
wrangler deploy                               # → https://babel-bazaar-demo.<sub>.workers.dev
```

Then add a repository **Actions variable** `DEMO_SYNC_URL` = the Worker URL
(repo **Settings → Secrets and variables → Actions → Variables**) and re-run the **Pages** workflow. The
build uses your Worker instead of auto-provisioning a blob.

## The contract

The client (`web/src/api/sync.ts`) treats the URL as one JSON document:

| Method | Returns |
|---|---|
| `GET /`  | the document `{ products, categories, metrics, since }` |
| `PUT /`  | replace it (validated and capped) |

A jsonblob URL (`https://jsonblob.com/api/jsonBlob/<id>`) serves exactly this natively, which is why no
server code is needed by default. Writes are read-merge-write and best-effort; the Worker validates and
caps input (500 products, 20k visitors) and is last-write-wins, so put a Cloudflare **Rate limiting** rule
in front for a busy public demo.

## Local development

```bash
node deploy/pages-backend/mock-server.mjs                                   # a local jsonblob stand-in
JSONBLOB_API=http://127.0.0.1:8787/api/jsonBlob node site/provision-backend.mjs   # create a document
# then open the demo with ?sync=http://127.0.0.1:8787/api/jsonBlob/<id>
```
