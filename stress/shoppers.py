"""The crowd: thousands of virtual shoppers, all through the BFF, doing different things.

Two kinds, because real traffic is mostly browsing with a trickle of buying:

  * Browsers (asyncio, the thousands). Anonymous GETs: the catalog, search with
    filters and sorts, category trees, a product page, recommendations. Each owns one
    keep-alive connection and loops a randomised journey until time runs out. These
    create the concurrency the dashboard brags about.

  * Buyers (a thread pool, dozens). The full stateful journey through scripts/bff.py:
    sign in as the seeded customer, read the catalog, fill a cart, check out with an
    Idempotency-Key, pay with a test card, read their orders back. These create real
    orders and revenue, and exercise auth, CSRF, the commerce core and payments-sim.

Everything a shopper does is reported to the shared Metrics; nothing here draws.
"""

from __future__ import annotations

import asyncio
import random
import time
import urllib.parse

from aiolite import Connection, HTTPError, split_base

SEARCH_TERMS = ["mug", "lamp", "chair", "rug", "clock", "vase", "knife", "pan", "shelf", "spice", ""]
SORTS = ["relevance", "newest", "price_asc", "price_desc", "name"]
CATEGORIES = ["kitchen", "home", "lighting", "textiles", "", ""]


def _catalog_targets():
    """A weighted mix of anonymous read journeys, as request targets for the BFF."""
    t = []
    for _ in range(6):
        q = urllib.parse.urlencode({k: v for k, v in {
            "q": random.choice(SEARCH_TERMS),
            "category": random.choice(CATEGORIES),
            "sort": random.choice(SORTS),
            "page": random.randint(1, 3),
            "page_size": random.choice([4, 12, 24]),
        }.items() if v != ""})
        t.append(("search" if random.random() < 0.5 else "browse",
                  ("/api/catalog/search?" if random.random() < 0.5 else "/api/catalog/products?") + q))
    t.append(("categories", "/api/catalog/categories"))
    return t


class Browser:
    """One anonymous shopper on one keep-alive connection."""

    def __init__(self, base, metrics, deadline, product_ids):
        self.host, self.port = split_base(base)
        self.metrics = metrics
        self.deadline = deadline
        self.product_ids = product_ids
        self.conn = Connection(self.host, self.port, timeout=15.0)

    async def run(self):
        self.metrics.begin()
        try:
            while time.monotonic() < self.deadline:
                act, target = random.choice(_catalog_targets())
                await self._hit(act, target)
                # A product page view and a recommendation, the way a listing leads on.
                if self.product_ids and random.random() < 0.6:
                    pid = random.choice(self.product_ids)
                    await self._hit("product", f"/api/catalog/products/{pid}")
                    if random.random() < 0.4:
                        await self._hit("recommend", f"/api/recommendations?product_id={pid}&limit=6")
                await asyncio.sleep(random.uniform(0.0, 0.05))  # think-time jitter
        finally:
            await self.conn.close()
            self.metrics.end()

    async def _hit(self, act, target):
        try:
            resp = await self.conn.request("GET", target)
            self.metrics.record({"lane": "shop", "act": act, "status": resp.status,
                                 "ok": 200 <= resp.status < 400, "ms": resp.elapsed * 1000,
                                 "msg": f"{act} {resp.status}"})
        except HTTPError as err:
            self.metrics.record({"lane": "shop", "act": act, "status": None, "ok": False,
                                 "ms": None, "msg": f"{act} dropped: {err}"})


async def discover_products(base, limit=48):
    """Pull a page of product IDs through the BFF so browsers and buyers use real ones."""
    host, port = split_base(base)
    conn = Connection(host, port)
    try:
        resp = await conn.request("GET", f"/api/catalog/products?page_size={limit}")
        items = (resp.json() or {}).get("items", []) if resp.status == 200 else []
        return [it["id"] for it in items if "id" in it]
    except (HTTPError, ValueError):
        return []
    finally:
        await conn.close()


async def run_browsers(base, count, duration, metrics, product_ids, ramp=5.0):
    """Launch `count` browsers over a short ramp, run for `duration`, then stop."""
    deadline = time.monotonic() + duration
    tasks = []
    for i in range(count):
        tasks.append(asyncio.create_task(Browser(base, metrics, deadline, product_ids).run()))
        if ramp and count > 1:
            await asyncio.sleep(ramp / count)
    await asyncio.gather(*tasks, return_exceptions=True)


# -- buyers: stateful journeys in threads, via scripts/bff.py ------------------

def buyer_journey(session_factory, email, password, product_ids, metrics, deadline, cards):
    """Sign in once, then loop cart -> checkout -> pay -> orders until time is up."""
    try:
        session = session_factory()
        session.login(email, password)
    except SystemExit as err:
        metrics.record({"lane": "shop", "act": "login", "status": None, "ok": False,
                        "ms": None, "msg": f"buyer login failed: {err}"})
        return
    except Exception as err:  # noqa: BLE001 - a buyer that cannot connect just sits out
        metrics.record({"lane": "shop", "act": "login", "status": None, "ok": False,
                        "ms": None, "msg": f"buyer login error: {err}"})
        return

    metrics.record({"lane": "shop", "act": "login", "status": 200, "ok": True, "ms": None, "msg": "buyer signed in"})
    n = 0
    while time.monotonic() < deadline:
        n += 1
        pid = random.choice(product_ids) if product_ids else None
        if not pid:
            break
        items = [{"product_id": pid, "quantity": random.randint(1, 3)}]
        _timed(metrics, "cart", lambda: session.call("PUT", "/api/cart", {"items": items}))
        key = f"arena-{id(session):x}-{n}"
        status, _, order = _timed(metrics, "checkout",
                                  lambda: session.call("POST", "/api/checkout", {"items": items},
                                                       headers={"Idempotency-Key": key}))
        if status not in (200, 201) or not isinstance(order, dict) or "id" not in order:
            continue
        source = random.choice(cards)
        pay_status, _, paid = _timed(metrics, "pay",
                                     lambda: session.call("POST", f"/api/orders/{order['id']}/pay", {"source": source}),
                                     amount_if_ok=order.get("total_minor", 0))
        _timed(metrics, "orders", lambda: session.call("GET", "/api/orders?page_size=5"))
        time.sleep(random.uniform(0.1, 0.4))


def _timed(metrics, act, call, amount_if_ok=0):
    start = time.monotonic()
    try:
        status, _, body = call()
    except SystemExit as err:
        metrics.record({"lane": "shop", "act": act, "status": None, "ok": False, "ms": None, "msg": str(err)})
        return None, None, None
    except Exception as err:  # noqa: BLE001
        metrics.record({"lane": "shop", "act": act, "status": None, "ok": False, "ms": None,
                        "msg": f"{act}: {err}"})
        return None, None, None
    ms = (time.monotonic() - start) * 1000
    ok = status is not None and 200 <= status < 400
    ev = {"lane": "shop", "act": act, "status": status, "ok": ok, "ms": ms, "msg": f"{act} {status}"}
    # Count revenue only when a payment actually succeeded (the gateway "paid" state).
    if act == "pay" and ok and amount_if_ok and isinstance(body, dict) and body.get("status") == "paid":
        ev["amount_minor"] = amount_if_ok
    metrics.record(ev)
    return status, None, body
