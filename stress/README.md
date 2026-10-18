# Stress Arena

A gamified load-and-attack demo for Babel Bazaar. It does three things at once against a
running stack and scores them live in the terminal:

1. **Thousands of shoppers through the BFF.** An asyncio crowd browses the catalog,
   searches with filters and sorts, opens product pages and asks for recommendations; a
   smaller thread pool signs in and buys for real — cart, checkout with an
   `Idempotency-Key`, pay with a test card, read orders back. Real orders, real revenue.

2. **A red team trying to reach the core without the BFF.** The whole architecture rests
   on one rule: nothing reaches a core service except a request the BFF signed
   (`docs/request-signing.md`). The red team attacks that rule from two sides and every
   attempt should fail:

   | Plane | Where it runs | What it proves |
   |---|---|---|
   | **host** | the arena process, outside the mesh | the core services publish **no host port** — a direct connection is refused before a byte is sent. It also scans the BFF's front door: admin routes with no session, honeypot paths, forged gateway headers. |
   | **mesh** | a container on the internal `app` network (compose profile `stress`) | the attacker is already *past* the firewall, standing next to every service, and still gets a `403` with an empty body for every **unsigned**, **forged** (valid-looking signature, wrong secret), **time-expired** and **fake-privileged** request — refused before routing, parsing or a single query. It has no `GATEWAY_SECRET`, which is exactly the attacker's situation. |

3. **A live scoreboard.** Throughput, concurrency, latency percentiles, orders and
   revenue on the left; a defence meter, a per-technique breakdown and a big `0 bytes
   stolen` on the right; unlockable achievements along the bottom.

A **breach** — any core service answering an unsigned request with real data — turns the
board red and fails the verdict. In normal (guarded) config that never happens; if someone
sets `GATEWAY_ALLOW_UNGUARDED=true`, the arena will catch it.

## Run it

```bash
./run.sh                       # start the stack first
./run.sh stress                # 2000 browsers, 60 buyers, 45s, mesh attack if Docker is present
```

Direct, with options:

```bash
python3 stress/arena.py --users 4000 --buyers 120 --duration 90
python3 stress/arena.py --no-mesh --plain          # host-plane only, log-friendly output
```

| Flag | Default | Meaning |
|---|---|---|
| `--users` | 2000 | concurrent anonymous browsers (asyncio) |
| `--buyers` | 60 | concurrent buyers doing the full checkout (threads) |
| `--duration` | 45 | seconds to run |
| `--ramp` | 5 | seconds to bring the browsers up over |
| `--red-intensity` | 24 | attacker threads inside the mesh |
| `--no-mesh` | off | skip the in-network (Docker) attack plane |
| `--plain` | off | plain log lines instead of the full-screen board (pipes, CI) |

To measure peak throughput rather than the rate limiter, start the stack with the limit
lifted: `RATE_DEFAULT_PER_MINUTE=100000000 ./run.sh`. Otherwise shoppers that exceed the
limit are counted as `429`s (shown separately), which is itself a fair thing to watch.

## Afterwards

Each run writes, under `stress/reports/` (git-ignored except this folder):

* `run-<timestamp>.jsonl` — every event, one JSON object per line;
* `run-<timestamp>.html` and `latest.html` — a standalone gamified scorecard using the
  storefront's own colours, with a throughput sparkline, the attack breakdown and the
  verdict. Rebuild one from a log at any time: `python3 stress/report.py <log.jsonl>`.

## How it fits together

```
arena.py        orchestrates everything; draws the board; writes the report
├─ aiolite.py   tiny asyncio HTTP/1.1 client (the browser crowd)
├─ shoppers.py  browser journeys (async) + buyer journeys (threads, via scripts/bff.py)
├─ redteam.py   the attacker: mesh plane (container) + host plane (isolation + scans)
├─ gatewaysig.py the gateway HMAC, re-implemented so the red team can forge and fail
├─ metrics.py   one thread-safe tally + the achievements engine
├─ dashboard.py the live ANSI scoreboard
└─ report.py    JSONL → HTML scorecard
```

Standard library only, on both sides. `gatewaysig.py` is checked against the committed
cross-language vectors (`docs/contracts/gateway-signing.vectors.json`), so the signatures
the red team forges are byte-for-byte what the real guards expect — and reject.
