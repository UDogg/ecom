"""The scoreboard's brain: one thread-safe tally every producer writes into.

The arena mixes asyncio shoppers, a thread pool of buyers, a host-plane prober and
(optionally) a red-team container whose JSONL is piped back in. They all land here
through record(), which is guarded by a single lock, so the dashboard can read a
consistent snapshot at any instant. record() also appends the raw event to the run's
JSONL log, which stress/report.py turns into the HTML scorecard afterwards.

Two lanes:
  * shop  -- virtual shoppers through the BFF. ok = the BFF served it.
  * red   -- the attacker. blocked = the gate refused it (good); a breach is any
             protected endpoint that answered with real data (should never happen).
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter, deque

# (id, title, blurb, predicate(snapshot) -> bool). Once earned, kept.
ACHIEVEMENTS = [
    ("warm_up",       "Warm-up",            "First 100 requests served",            lambda s: s["shop"]["served"] >= 100),
    ("herd",          "Thundering Herd",    "1,000 shoppers browsing at once",      lambda s: s["shop"]["peak_active"] >= 1000),
    ("ten_k",         "Ten-K Club",         "10,000 requests served",               lambda s: s["shop"]["served"] >= 10_000),
    ("first_order",   "Cha-ching",          "First order placed and paid",          lambda s: s["shop"]["orders"] >= 1),
    ("order_storm",   "Checkout Storm",     "100 orders paid",                      lambda s: s["shop"]["orders"] >= 100),
    ("big_money",     "Big Spender",        "£1,000+ in revenue",                   lambda s: s["shop"]["revenue_minor"] >= 100_000),
    ("snappy",        "Snappy",             "p95 under 25 ms while under load",     lambda s: 0 < s["shop"]["p95"] <= 25 and s["shop"]["served"] >= 2000),
    ("first_repel",   "Not Today",          "First attack repelled",                lambda s: s["red"]["blocked"] >= 1),
    ("fortress",      "Fortress",           "1,000 attacks, every one refused",     lambda s: s["red"]["attempts"] >= 1000 and s["red"]["breached"] == 0),
    ("zero_trust",    "Zero Trust",         "Every forged signature bounced",       lambda s: s["red"]["forged"] >= 200 and s["red"]["breached"] == 0),
    ("no_loot",       "Zero Bytes Stolen",  "Not one byte of data exfiltrated",     lambda s: s["red"]["attempts"] >= 500 and s["red"]["bytes_leaked"] == 0),
    ("flawless",      "Flawless Defence",   "25k served + 2k repelled, no breach",  lambda s: s["shop"]["served"] >= 25_000 and s["red"]["blocked"] >= 2000 and s["red"]["breached"] == 0),
]


class Metrics:
    def __init__(self, log_path=None, keep_latencies=40_000, keep_log=14):
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._keep_lat = keep_latencies
        self._logfh = open(log_path, "w", buffering=1) if log_path else None

        self.shop = dict(requests=0, served=0, errors=0, rate_limited=0, active=0,
                         peak_active=0, orders=0, revenue_minor=0)
        self.shop_by_act = Counter()
        self._lat = deque(maxlen=keep_latencies)
        self._served_ts = deque()           # monotonic timestamps for a sliding rps

        self.red = dict(attempts=0, blocked=0, breached=0, forged=0, replayed=0,
                        expired=0, unsigned=0, bytes_leaked=0)
        self.red_by_tech = {}               # technique -> [attempts, blocked, breached]
        self.red_by_target = {}             # service -> [attempts, blocked]

        self.events = deque(maxlen=keep_log)   # recent, for the live log tail
        self.unlocked = {}                     # achievement id -> monotonic time earned

    # -- producers -------------------------------------------------------
    def begin(self):
        with self._lock:
            self.shop["active"] += 1
            self.shop["peak_active"] = max(self.shop["peak_active"], self.shop["active"])

    def end(self):
        with self._lock:
            self.shop["active"] = max(0, self.shop["active"] - 1)

    def record(self, ev):
        """ev: a dict (see module docstring). Thread-safe; also written to the log."""
        ev.setdefault("t", time.time())
        with self._lock:
            if self._logfh:
                self._logfh.write(json.dumps(ev, separators=(",", ":")) + "\n")
            if ev["lane"] == "shop":
                self._record_shop(ev)
            else:
                self._record_red(ev)
            self.events.append(ev)

    def _record_shop(self, ev):
        s = self.shop
        s["requests"] += 1
        self.shop_by_act[ev.get("act", "?")] += 1
        status = ev.get("status")
        if ev.get("ms") is not None:
            self._lat.append(ev["ms"])
        if status == 429:
            s["rate_limited"] += 1
        elif ev.get("ok"):
            s["served"] += 1
            self._served_ts.append(time.monotonic())
            if ev.get("act") == "pay" and ev.get("amount_minor"):
                s["orders"] += 1
                s["revenue_minor"] += ev["amount_minor"]
        else:
            s["errors"] += 1

    def _record_red(self, ev):
        r = self.red
        r["attempts"] += 1
        tech = ev.get("technique", "?")
        target = ev.get("target", "?")
        blocked = bool(ev.get("blocked"))
        tv = self.red_by_tech.setdefault(tech, [0, 0, 0])
        tv[0] += 1
        gv = self.red_by_target.setdefault(target, [0, 0])
        gv[0] += 1
        for key in ("forged", "replayed", "expired", "unsigned"):
            if tech == key:
                r[key] += 1
        if blocked:
            r["blocked"] += 1
            tv[1] += 1
            gv[1] += 1
        else:
            r["breached"] += 1
            tv[2] += 1
            r["bytes_leaked"] += int(ev.get("leaked", 0))

    # -- consumer --------------------------------------------------------
    def snapshot(self):
        with self._lock:
            lat = sorted(self._lat)
            now = time.monotonic()
            while self._served_ts and now - self._served_ts[0] > 3.0:
                self._served_ts.popleft()
            rps = len(self._served_ts) / 3.0
            snap = {
                "elapsed": now - self._started,
                "rps": rps,
                "shop": {**self.shop,
                         "p50": _pct(lat, 50), "p95": _pct(lat, 95), "p99": _pct(lat, 99),
                         "by_act": dict(self.shop_by_act)},
                "red": {**self.red,
                        "by_tech": {k: list(v) for k, v in self.red_by_tech.items()},
                        "by_target": {k: list(v) for k, v in self.red_by_target.items()}},
                "events": list(self.events),
            }
            newly = []
            for aid, title, blurb, pred in ACHIEVEMENTS:
                if aid not in self.unlocked:
                    try:
                        if pred(snap):
                            self.unlocked[aid] = now
                            newly.append(aid)
                    except (KeyError, ZeroDivisionError):
                        pass
            snap["unlocked"] = dict(self.unlocked)
            snap["newly_unlocked"] = newly
        return snap

    def close(self):
        with self._lock:
            if self._logfh:
                self._logfh.close()
                self._logfh = None


def _pct(sorted_values, p):
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def defence_score(snap):
    """0-100: share of attacks refused (100 when nothing has been attempted yet)."""
    r = snap["red"]
    return 100.0 if r["attempts"] == 0 else 100.0 * r["blocked"] / r["attempts"]
