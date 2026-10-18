#!/usr/bin/env python3
"""The Babel Bazaar stress arena: load, attack, and a gamified scoreboard, in one process.

It runs three things at once against a running stack and scores them live:

  1. a crowd of virtual shoppers through the BFF -- thousands browsing (asyncio) and a
     pool buying for real (threads): cart, checkout with an Idempotency-Key, pay, orders;
  2. a red team trying to reach the core services WITHOUT the BFF -- from the host
     (where the services publish no port) and, if Docker is here, from inside the
     internal network (compose profile `stress`), firing unsigned, forged, time-expired
     and fake-privileged requests at every service;
  3. a full-screen scoreboard with throughput, latency, orders, revenue, a defence
     meter, a per-technique breakdown and unlockable achievements.

    ./run.sh stress                       # 2000 browsers, 60 buyers, 45s, mesh attack if Docker
    python3 stress/arena.py --users 4000 --buyers 120 --duration 90
    python3 stress/arena.py --no-mesh --plain          # host-only, log-friendly output

Afterwards it writes stress/reports/run-<ts>.jsonl and a gamified HTML scorecard
(run-<ts>.html + latest.html). Standard library only; raise the BFF's rate limit first
(RATE_DEFAULT_PER_MINUTE) or the shoppers mostly measure the limiter.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))

from metrics import Metrics                           # noqa: E402
from dashboard import Dashboard, final_card           # noqa: E402
import shoppers                                        # noqa: E402
import redteam                                         # noqa: E402
import report                                          # noqa: E402


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.split(" #")[0].strip()
    # expand ${VARS} that the .env derives from earlier lines
    for key, value in list(values.items()):
        for other in values:
            value = value.replace("${%s}" % other, values[other])
        values[key] = value
    return values


def make_session_factory(base):
    """A BffSession factory bound to the arena's base URL (scripts/bff.py)."""
    from bff import BffSession
    return lambda: BffSession(base)


def mesh_available():
    try:
        out = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=5)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def start_mesh(duration, intensity, metrics, env):
    """Launch the red-team container on the internal network; fold its JSONL into metrics."""
    cmd = ["docker", "compose", "--profile", "stress", "run", "--rm", "--no-deps", "-T",
           "-e", f"ARENA_DURATION={duration}", "-e", f"ARENA_RED_INTENSITY={intensity}", "redteam"]
    try:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError as err:
        metrics.record({"lane": "red", "plane": "mesh", "technique": "setup", "target": "mesh",
                        "blocked": True, "status": None, "msg": f"mesh attacker could not start: {err}"})
        return None

    def pump():
        import json
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                metrics.record(json.loads(line))
            except ValueError:
                pass
    threading.Thread(target=pump, daemon=True).start()
    return proc


async def wait_for_bff(base, metrics, attempts=40):
    from aiolite import Connection, HTTPError, split_base
    host, port = split_base(base)
    for _ in range(attempts):
        conn = Connection(host, port, timeout=3.0)
        try:
            resp = await conn.request("GET", "/healthz")
            if resp.status < 500:
                await conn.close()
                return True
        except HTTPError:
            pass
        await conn.close()
        await asyncio.sleep(0.5)
    return False


async def drive(args, metrics):
    base = args.base
    if not await wait_for_bff(base, metrics):
        print(f"\nThe BFF is not answering at {base}. Start the stack first: ./run.sh\n", file=sys.stderr)
        return False

    product_ids = await shoppers.discover_products(base)
    if not product_ids:
        metrics.record({"lane": "shop", "act": "discover", "status": None, "ok": False,
                        "ms": None, "msg": "no products found; browsers run on catalog listings only"})

    deadline = time.monotonic() + args.duration
    stop = threading.Event()
    env = read_env(ROOT / ".env")
    cards = ["tok_visa", "tok_mastercard", "tok_visa", "tok_declined"]

    # Buyers: a thread pool of stateful journeys via scripts/bff.py.
    buyer_threads = []
    if args.buyers > 0 and product_ids and env.get("SEED_CUSTOMER_EMAIL"):
        factory = make_session_factory(base)
        for _ in range(args.buyers):
            th = threading.Thread(target=shoppers.buyer_journey, args=(
                factory, env["SEED_CUSTOMER_EMAIL"], env.get("SEED_CUSTOMER_PASSWORD", ""),
                product_ids, metrics, deadline, cards), daemon=True)
            th.start()
            buyer_threads.append(th)
            await asyncio.sleep(0.02)

    # Red team: host plane always; mesh plane if Docker is here and not disabled.
    threading.Thread(target=redteam.run_host, args=(base, args.duration, metrics.record, stop), daemon=True).start()
    mesh_proc = None
    if not args.no_mesh and mesh_available():
        mesh_proc = start_mesh(args.duration, args.red_intensity, metrics, env)
    elif not args.no_mesh:
        metrics.record({"lane": "red", "plane": "mesh", "technique": "setup", "target": "mesh", "blocked": True,
                        "status": None, "msg": "Docker not found: mesh-plane attack skipped (host plane still runs)"})

    # Browsers: the asyncio crowd.
    browsers = asyncio.create_task(
        shoppers.run_browsers(base, args.users, args.duration, metrics, product_ids, ramp=args.ramp))

    dash = Dashboard(use_color=not args.no_color, interactive=not args.plain and sys.stdout.isatty())
    dash.start()
    try:
        while time.monotonic() < deadline or not browsers.done():
            dash.render(metrics.snapshot(), args.duration)
            await asyncio.sleep(1.0 / args.fps if args.fps else 0.2)
            if browsers.done() and time.monotonic() >= deadline:
                break
    finally:
        stop.set()
        await asyncio.gather(browsers, return_exceptions=True)
        for th in buyer_threads:
            th.join(timeout=2.0)
        if mesh_proc:
            try:
                mesh_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                mesh_proc.kill()
        dash.render(metrics.snapshot(), args.duration)
        dash.stop()
    return True


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    env = read_env(ROOT / ".env")
    p.add_argument("--base", default=env.get("PUBLIC_API_BASE_URL", "http://localhost:8080"),
                   help="the BFF's public URL (default from .env)")
    p.add_argument("--users", type=int, default=2000, help="concurrent anonymous browsers (asyncio)")
    p.add_argument("--buyers", type=int, default=60, help="concurrent buyers doing the full checkout (threads)")
    p.add_argument("--duration", type=float, default=45.0, help="seconds to run")
    p.add_argument("--ramp", type=float, default=5.0, help="seconds to ramp browsers up over")
    p.add_argument("--red-intensity", type=int, default=24, help="attacker threads inside the mesh")
    p.add_argument("--no-mesh", action="store_true", help="skip the in-network (Docker) attack plane")
    p.add_argument("--fps", type=float, default=8.0, help="scoreboard repaints per second")
    p.add_argument("--plain", action="store_true", help="plain log lines instead of the full-screen board")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    args = p.parse_args(argv)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    reports = ROOT / "stress" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    log_path = reports / f"run-{stamp}.jsonl"
    metrics = Metrics(log_path=str(log_path))

    try:
        ok = asyncio.run(drive(args, metrics))
    except KeyboardInterrupt:
        ok = True
        print("\ninterrupted — writing the scorecard for what ran so far…", file=sys.stderr)
    finally:
        metrics.close()

    snap = report.aggregate(_read_jsonl(log_path))
    print(final_card({**snap, "elapsed": snap["duration"],
                      "shop": {**snap["shop"], "peak_active": args.users},
                      "unlocked": {a: 0 for a in snap["unlocked"]}}, args.duration))
    html_path, _ = report.write_report(log_path)
    print(f"\n  scorecard  {html_path}")
    print(f"  event log  {log_path}")
    return 0 if ok else 1


def _read_jsonl(path):
    import json
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    return events


if __name__ == "__main__":
    sys.exit(main())
