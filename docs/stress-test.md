# The stress arena

> **Try it in your browser:** the Pages site has a live, gamified
> [Stress Arena](../site/stress.html) that reproduces this demo client-side — thousands of
> shoppers and a red team whose every attack is refused — with no backend to call. On a
> deployed site it is at `/<repo>/stress.html` (for this repo,
> `https://udogg.github.io/ecom/stress.html`). What follows is the real thing: the same
> scoreboard driven by actual traffic and actual `403`s.

`./run.sh stress` runs a gamified load-and-attack demo against a live stack and scores it
in the terminal. It exists to show two claims at once, under genuine pressure:

1. the shop stays fast and keeps taking orders while thousands of shoppers hit it; and
2. the gate holds — a core service answers **nothing** that did not come through the BFF,
   even to an attacker already inside the network.

The code lives in [`stress/`](../stress/README.md); this page is the why and the how it is
wired, for the architecture record.

## What runs

| Part | Mechanism | Talks to |
|---|---|---|
| Anonymous browsers (thousands) | asyncio tasks, one keep-alive connection each ([`stress/aiolite.py`](../stress/aiolite.py)) | the BFF, `GET` only: catalog, search, product pages, categories, recommendations |
| Buyers (dozens) | a thread pool over [`scripts/bff.py`](../scripts/bff.py) | the BFF: sign in, cart, checkout with an `Idempotency-Key`, pay, orders |
| Red team — host plane | the arena process, outside the mesh | tries to open the core service ports directly (refused: no host port), and scans the BFF's front door |
| Red team — mesh plane | a container on the internal `app` network (compose profile `stress`) | hits every core service directly with unsigned / forged / expired / fake-privileged requests |

Browsing is asyncio because the point is *concurrency*: thousands of in-flight sessions
cost kilobytes each, where a thread apiece would cost gigabytes of stack. The stateful
buyer journey is messier (OIDC sign-in, CSRF, idempotency) so it reuses the already-tested
`scripts/bff.py` client in a modest thread pool. Together: a realistic mix of a large
browsing crowd and a smaller stream of real purchases.

## Why the attacker proves something

Every core service runs the same gateway guard (commerce-core in Django, catalog-reader in
Go, auth-service in Spring, intelligence and payments-sim in FastAPI). It refuses any
request without a valid per-request HMAC signature — before routing, parsing or any
database work — unless the request carries a verified SYSTEM or break-glass token. See
[request signing](request-signing.md) and [core lockdown](core-lockdown.md).

The red team attacks that guard the way a real attacker would have to:

* **Network isolation (host plane).** In `docker-compose.yml` the core services join only
  the `internal: true` `app` and `data` networks and publish **no host port**. From the
  host, a direct TCP connection to them is refused before a byte is sent. The only
  published port is the BFF's. The arena shows each refused connection as a block.

* **The signature (mesh plane).** Run from a container on the `app` network, the attacker
  is already past the firewall — it can open a socket to `commerce-core:8000` and the rest.
  It still gets `403` with an empty body for every request, because it has **no
  `GATEWAY_SECRET`**. It forges signatures with the wrong key (byte-for-byte the shape the
  guards expect, thanks to the shared vectors), back-dates timestamps, and presents a
  fake privileged bearer token. All refused. Open liveness paths (`/healthz`, `/readyz`)
  *are* reachable and shown as such — a heartbeat worth nothing to an attacker.

The mesh attacker is deliberately given no secret in `docker-compose.yml`; that is the
whole demonstration. A **breach** is defined as any protected endpoint answering with real
data, and the scoreboard turns red and the verdict fails if one ever happens. In guarded
configuration it does not. If someone runs the stack with `GATEWAY_ALLOW_UNGUARDED=true`,
the arena catches it immediately — so it doubles as a config check.

## Scoring and output

The live board shows throughput, in-flight concurrency and peak, latency p50/p95/p99,
orders paid and revenue on one side; a defence meter, the per-technique and per-target
breakdown, and bytes exfiltrated (expected: `0`) on the other; and achievements that unlock
as thresholds are crossed (`Thundering Herd`, `Checkout Storm`, `Fortress`, `Zero Trust`,
`Flawless Defence`, …).

Each run writes `stress/reports/run-<timestamp>.jsonl` (every event) and a standalone HTML
scorecard (`run-<timestamp>.html` + `latest.html`) in the storefront's colours, with a
throughput sparkline and the final verdict. The reports folder is git-ignored; rebuild a
scorecard from any log with `python3 stress/report.py <log.jsonl>`.

## Measuring peak throughput

The BFF rate-limits by default, so a flat-out crowd will see `429`s (the board counts them
separately — absorbing a flood is itself worth watching). To measure raw capacity instead,
lift the limit when starting the stack:

```bash
RATE_DEFAULT_PER_MINUTE=100000000 ./run.sh
./run.sh stress --users 4000 --duration 90
```

This is the same knob [`bench/run.sh`](../bench/run.sh) documents; the arena and the
benchmark measure the same BFF, one as a game, one as a report.

## Caveats

* The arena is a demo and a smoke test, not a calibrated benchmark. For clean latency and
  throughput numbers use `./run.sh bench`, which fixes concurrency and warms up first.
* Both sides are standard library only (asyncio client, `http.client` attacker), so the
  numbers include a Python client's own overhead. The server-side latency percentiles are
  the honest half of the picture; treat client-side throughput as a floor.
* The mesh plane needs Docker. Without it the arena still runs the host plane and says so.
