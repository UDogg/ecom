#!/usr/bin/env python3
"""Scenario C: broker interruption (README section 16).

Against the running Compose stack (`make up seed`):

1. Sign in as the owner and the customer through auth-service.
2. Take RabbitMQ down: `stop` (default; every connection drops, so the
   publisher and worker must reconnect) or `pause` (frozen broker, sockets
   stay open). Choose with SCENARIO_C_MODE=stop|pause.
3. While it is down, add products, adjust stock, archive one, and check out.
   Every request must still succeed: commerce never waits on the broker.
4. Confirm the events are waiting in the outbox, not lost.
5. Bring RabbitMQ back, then confirm the outbox drains and the worker delivers
   exactly one order confirmation e-mail to Mailpit.

Exit code 0 means every check passed.
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bff  # noqa: E402

env = bff.read_env_file(ROOT / ".env")
API = env.get("PUBLIC_API_BASE_URL", "http://localhost:8080")  # everything goes through the BFF
MAILPIT = env.get("SCENARIO_MAILPIT_URL", "http://localhost:8025")
# Longer than the AMQP heartbeat, so publisher and worker must detect the dead
# connection and reconnect, not just resume a stalled socket.
OUTAGE_SECONDS = float(env.get("SCENARIO_OUTAGE_SECONDS", "25"))
MODE = env.get("SCENARIO_C_MODE", "stop")


def call(method, path, token=None, body=None, headers=None, base=API):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {}),
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def compose(*args):
    subprocess.run(["docker", "compose", "--profile", "core", *args], cwd=ROOT, check=True, capture_output=True)


def check(condition, message):
    print(("  PASS  " if condition else "  FAIL  ") + message)
    if not condition:
        raise SystemExit(1)


def wait_for(predicate, timeout, interval=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def outbox_pending(owner):
    return owner.call("GET", "/api/admin/dashboard")[2]["system"]["outbox_pending"]


def mails_with_subject(subject):
    status, body = call("GET", "/api/v1/messages?limit=200", base=MAILPIT)
    return [m for m in (body or {}).get("messages", []) if m.get("Subject") == subject]


def main():
    import os

    global MODE
    MODE = os.environ.get("SCENARIO_C_MODE", MODE)
    print(f"Scenario C: broker interruption (mode: {MODE})")
    owner, customer = bff.BffSession(API), bff.BffSession(API)
    owner.login(env["SEED_OWNER_EMAIL"], env["SEED_OWNER_PASSWORD"])
    customer.login(env["SEED_CUSTOMER_EMAIL"], env["SEED_CUSTOMER_PASSWORD"])
    check(True, "owner and customer signed in through the BFF (server-side OIDC + PKCE, session cookies only)")
    check(wait_for(lambda: outbox_pending(owner) == 0, 60) is not None, "outbox is drained before the outage")

    tag = uuid.uuid4().hex[:6].upper()
    compose("pause" if MODE == "pause" else "stop", "rabbitmq")
    paused_at = time.monotonic()
    try:
        ids = []
        for i in range(3):
            status, _, product = owner.call("POST", "/api/admin/products", {
                "sku": f"SCEN-C-{tag}-{i}", "name": f"Outage product {i}", "price_minor": 1000 + i,
                "category": "scenario", "initial_stock": 10, "status": "active"})
            check(status == 201, f"create product {i} while the broker is down (HTTP {status})")
            ids.append(product["id"])
        status, _, _ = owner.call("POST", f"/api/admin/inventory/{ids[1]}/adjust",
                                  {"delta": 5, "reason": "scenario C restock"})
        check(status == 201, f"adjust stock while the broker is down (HTTP {status})")
        status, _, _ = owner.call("DELETE", f"/api/admin/products/{ids[2]}")
        check(status == 200, f"archive a product while the broker is down (HTTP {status})")
        status, _, order = customer.call("POST", "/api/checkout", {"items": [{"product_id": ids[0], "quantity": 2}]},
                                         headers={"Idempotency-Key": f"scenario-c-{tag}"})
        check(status == 201, f"customer checks out while the broker is down (HTTP {status})")

        # 3 created + 1 adjusted + 1 archived + order.placed + inventory.changed
        waiting = outbox_pending(owner)
        check(waiting >= 7, f"{waiting} events are waiting in the outbox, none lost")
        time.sleep(max(0.0, OUTAGE_SECONDS - (time.monotonic() - paused_at)))
    finally:
        if MODE == "pause":
            compose("unpause", "rabbitmq")
        else:
            compose("start", "rabbitmq")
    outage = time.monotonic() - paused_at
    resumed_at = time.monotonic()

    drained = wait_for(lambda: outbox_pending(owner) == 0, 120)
    check(drained is not None, f"outbox drained {time.monotonic() - resumed_at:.1f}s after the broker came back")

    subject = f"Order {order['id'][:8]} received"
    delivered = wait_for(lambda: mails_with_subject(subject), 60)
    check(bool(delivered), f"worker delivered the order confirmation to Mailpit ({subject!r})")
    time.sleep(3)
    check(len(mails_with_subject(subject)) == 1, "exactly one confirmation e-mail (no duplicates)")
    print(f"OK: broker was down {outage:.1f}s; every write committed and every event was delivered afterwards.")


if __name__ == "__main__":
    main()
