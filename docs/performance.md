# Performance and footprint

Measured on one 4-vCPU, 16 GB machine with the whole stack and the load generator on it (so
every number below is pessimistic for a real deployment, where the load comes from elsewhere).
`bench/run.sh` reproduces them: 32 concurrent connections, 15 s per scenario, the BFF's rate limit
lifted so the limiter is not what gets measured.

```bash
RATE_DEFAULT_PER_MINUTE=100000000 ./run.sh && bench/run.sh
```

## Results

| Scenario (through the BFF) | Baseline | Now | |
|---|---|---|---|
| Catalog, first page (anonymous, as deployed) | 1,763 rps, p50 16.1 ms, p99 53 ms | **10,848 rps, p50 2.2 ms, p99 13 ms** | 6.2x |
| Catalog search `q=mug` | 1,677 rps, p50 17.2 ms, p99 49 ms | **13,321 rps, p50 1.8 ms, p99 10 ms** | 7.9x |
| Category page, 4 items | 2,162 rps, p50 13.1 ms, p99 42 ms | **12,383 rps, p50 1.9 ms, p99 11 ms** | 5.7x |
| Category page, 4 items, micro-cache **off** | 2,162 rps, p99 42 ms | 2,515 rps, p50 11.3 ms, p99 36 ms | +16% |
| Catalog, first page, micro-cache **off** | 1,763 rps (10 products a page) | 1,517 rps (24 products a page) | 2.4x the data |
| The storefront (`/`, Brotli) | (did not exist) | 13,014 rps, p50 1.9 ms | |
| Signed-in: order history | 183 rps, p50 82 ms | 408 rps | 2.2x |
| Signed-in: cart | | 776 rps | |

| Footprint | Baseline | Now |
|---|---|---|
| auth-service image | 345 MB | 270 MB (jlink runtime 87 MB, plus a 47 MB class-data archive that buys the start-up below) |
| auth-service start-up | 4.7 s | **2.9 s** |
| commerce-core image (production) | 301 MB | 272 MB |
| intelligence image | 254 MB | 280 MB (uvloop and httptools: space traded for a faster event loop and parser) |
| BFF image, now including the whole storefront | 16 MB | 18 MB |
| Shop JavaScript and CSS sent to a browser | | 23 KB + 6 KB (Brotli) |

Image sizes are `docker images` figures, as in the baseline. The Python images share one 135 MB
base, which the disk stores once.

## What changed

**The request path**

- **BFF micro-cache with request coalescing** (`services/bff/internal/server/cache.go`): public,
  anonymous catalog answers are shared for 1 to 5 seconds, and identical concurrent requests wait for
  one upstream call. Only endpoints the route table marks public and GET may use it; the table is
  checked at start-up. Stock shown to shoppers is at most a second old; checkout always reads live.
- **Pass-through of exact answers**: when an upstream answer contains only fields the contract
  exposes (the normal case), the BFF checks it and sends the bytes as they are instead of rebuilding
  and re-encoding them. Anything with an unlisted field is still shaped.
- **One round trip for a catalog page**: the page and its total come from one query
  (`count(*) OVER ()`); a page past the end asks for the count alone.
- **Indexes that match the queries**: full-text expression indexes for product and category search,
  partial indexes for the active catalog by newest and by price (`catalog/0004`), and order indexes
  by customer and by status, newest first (`orders/0005`).
- **No N+1 on order lists**: lines and payment attempts for a whole page in two queries, guarded by
  a test that fails if the number of queries grows with the number of orders.
- **The storefront from memory**: precompressed Brotli and gzip, strong ETags, a year of caching for
  fingerprinted assets.
- **Uvicorn with uvloop and httptools**; **gunicorn** with preloaded, threaded workers and no second
  access log (the BFF logs every request with its trace id).
- **Postgres tuned to its container** (`db/tune/postgres.sh`): 25% of memory for shared buffers, the
  rest assumed as page cache, per-connection work memory from what is left, SSD costs, parallel
  workers per CPU, JIT off (it slows short OLTP queries down).

**Start-up and size**

- auth-service runs on a **jlink-ed Java runtime** holding only the modules the application uses,
  with a **class-data-sharing archive** recorded at build time from a training start that stops
  before anything touches the database.
- Python services get their dependencies in a **virtualenv built in a throwaway stage**: no pip, no
  build caches, no third-party test suites; commerce-core's production image also drops translations
  (i18n is off), the admin's and the browsable API's static files, and GeoDjango, and the build runs
  `manage.py check` to prove it still boots. Application bytecode is compiled at build time.

## Scaling up without editing anything

Every service sizes itself from the limits of its own container, and `./run.sh` sets those limits as
shares of the machine's RAM (`scripts/sizing.py`). Moving to a bigger instance therefore needs no
configuration change: restart and the whole stack uses it.

| Service | Reads | Sizes |
|---|---|---|
| Postgres | memory and CPU limits | buffers, cache estimate, work memory, parallel workers |
| auth-service | memory limit | heap at 75%; the JVM picks Serial or G1 from CPUs and memory |
| BFF, catalog-reader | memory limit, CPU quota | Go memory limit at 90%; `GOMAXPROCS`; catalog DB pool 4 per CPU |
| commerce-core | CPU quota, memory limit | gunicorn workers: CPUs + 1, fewer if memory is tight |
| intelligence | CPU quota | uvicorn workers, one per CPU |

There are no CPU caps: any service may use every core while the others are idle, and `cpu_shares`
decides who wins under contention (the BFF first, then the catalog and Postgres, background work
last). Memory ceilings are ceilings, not reservations.

| Instance | RAM | Postgres | auth heap | commerce workers |
|---|---|---|---|---|
| t4g.small | 2 GB | 448 MB | 240 MB | 2 |
| t4g.medium | 4 GB | 942 MB | 395 MB | 3 |
| t4g.large | 8 GB | 1.9 GB | 791 MB | 3 |
| t4g.xlarge | 16 GB | 3.8 GB | 1.6 GB | 5 |

Override any ceiling with `MEM_<SERVICE>=...` in `.env` (for example `MEM_POSTGRES=2G`), or a
worker count with `WEB_CONCURRENCY`.
