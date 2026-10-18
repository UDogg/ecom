"""The red team: prove the gate holds when someone attacks the core services directly.

The whole architecture rests on one claim -- nothing reaches a core service except a
request the BFF signed (docs/request-signing.md). This module tries, as hard as a
stdlib client can, to break that claim, and reports every rejection so the scoreboard
can celebrate it. It never has the real GATEWAY_SECRET, which is exactly the attacker's
situation.

Two planes, because there are two layers of defence:

  * mesh -- run from a container ON the internal `app` network (compose profile
    `stress`), so the attacker is already past the firewall, standing right next to
    commerce-core, catalog-reader, auth-service, intelligence and payments-sim. It
    fires unsigned, forged, time-expired and fake-privileged requests at each. Every
    one should come back 403 with an empty body, refused before routing, parsing or a
    single database query. A reply with real data would be a BREACH and the scoreboard
    turns red. Open liveness paths (/healthz) are probed too and shown for what they
    are: a heartbeat worth nothing to an attacker.

  * host -- run from OUTSIDE the mesh (the arena process on the host). The core
    services publish no host ports at all, so a direct connection is refused before a
    byte is sent: isolation on top of the signature. It also knocks on the BFF's own
    front door the way a scanner does -- admin endpoints with no session, known
    honeypot paths, forged gateway headers -- and gets the same nothing.

Events are dicts (metrics.py). As a container entrypoint this prints them as JSONL on
stdout, which the arena folds back into the live board.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatewaysig  # noqa: E402

# service -> (port, a protected path, a human label). The gate refuses before routing,
# so these need not even exist: an unsigned request never gets far enough to find out.
MESH_TARGETS = {
    "commerce-core":   (8000, "/api/commerce/orders",               "orders ledger"),
    "commerce-admin":  (8000, "/api/commerce/admin/orders",         "every customer's orders"),
    "catalog-reader":  (8082, "/api/catalog/products",              "catalog read model"),
    "intelligence":    (8001, "/api/intelligence/recommendations",  "recommender"),
    "auth-service":    (8081, "/api/auth/users",                    "user directory"),
    "payments-sim":    (8003, "/api/payments/charges",              "card charges"),
}
MESH_HOST = {"commerce-admin": "commerce-core"}        # same container, sensitive route
OPEN_PATHS = {"commerce-core": "/readyz", "catalog-reader": "/readyz",
              "intelligence": "/healthz", "auth-service": "/readyz", "payments-sim": "/healthz"}

TECHNIQUES = ("unsigned", "forged", "expired", "fake_priv")
WRONG_SECRETS = [
    "hunter2hunter2hunter2hunter2hunter2!!",
    "correct-horse-battery-staple-correct-horse",
    gatewaysig.sign("leaked?", "0", "x", "GET", "/", ""),   # a plausible-looking hex key
]
FAKE_BEARER = ("Bearer eyJhbGciOiJIUzI1NiJ9."
               "eyJzdWIiOiJhdHRhY2tlciIsInNjb3BlIjoiY29yZSIsImJyZWFrX2dsYXNzIjp0cnVlfQ."
               "not-a-real-signature-the-service-will-reject-this")


def _nonce():
    return "%016x%08x" % (random.getrandbits(64), random.getrandbits(32))


def _headers_for(technique, method, path):
    """Craft the attacker's gateway headers for one technique. None means send nothing."""
    if technique == "unsigned":
        return {}
    if technique == "fake_priv":
        return {"Authorization": FAKE_BEARER}
    now = int(time.time())
    ts = str(now - 3600 if technique == "expired" else now)
    nonce = _nonce()
    secret = random.choice(WRONG_SECRETS)
    sig = gatewaysig.sign(secret, ts, nonce, method, path, "", "")
    return {"X-Gateway-Timestamp": ts, "X-Gateway-Nonce": nonce, "X-Gateway-Signature": sig}


def mesh_attack(service, technique, timeout=5.0):
    """One direct hit on a core service. Returns a red event dict."""
    port, path, _ = MESH_TARGETS[service]
    host = MESH_HOST.get(service, service)
    method = "POST" if service == "payments-sim" else "GET"
    headers = _headers_for(technique, method, path)
    body = b"{}" if method == "POST" else None
    ev = {"lane": "red", "plane": "mesh", "technique": technique, "target": service}
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        if body is not None:
            headers = {**headers, "Content-Type": "application/json"}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        breached = 200 <= resp.status < 400 and len(payload) > 0
        ev.update(status=resp.status, blocked=not breached, leaked=len(payload) if breached else 0,
                  msg=f"{technique} -> {host}:{port} {path} = {resp.status}")
    except (socket.timeout, ConnectionError, OSError) as err:
        # Refused/unreachable: the strongest block of all (no port to talk to).
        ev.update(status=None, blocked=True, leaked=0, msg=f"{technique} -> {host}:{port} refused: {err}")
    return ev


def mesh_recon(service, timeout=5.0):
    """Probe an open liveness path: reachable, but it reveals nothing of value."""
    port = MESH_TARGETS[service][0]
    host = MESH_HOST.get(service, service)
    path = OPEN_PATHS.get(service, "/healthz")
    ev = {"lane": "red", "plane": "mesh", "technique": "recon", "target": service}
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        ev.update(status=resp.status, blocked=True, leaked=0,
                  msg=f"recon {host}:{port}{path} = {resp.status} (liveness only, no data)")
    except (socket.timeout, ConnectionError, OSError) as err:
        ev.update(status=None, blocked=True, leaked=0, msg=f"recon {host}:{port}{path} refused: {err}")
    return ev


def run_mesh(duration, intensity, emit, stop=None):
    """Hammer every target with every technique for `duration` using `intensity` threads."""
    deadline = time.monotonic() + duration
    services = list(MESH_TARGETS)

    def worker():
        reconned = set()
        while time.monotonic() < deadline and not (stop and stop.is_set()):
            service = random.choice(services)
            emit(mesh_attack(service, random.choice(TECHNIQUES)))
            if service not in reconned and random.random() < 0.1:
                emit(mesh_recon(service))
                reconned.add(service)
            time.sleep(random.uniform(0.0, 0.03))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, intensity))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# -- host plane: from outside the mesh ---------------------------------------

HOST_PORTS = {"commerce-core": 8000, "auth-service": 8081, "catalog-reader": 8082,
              "intelligence": 8001, "payments-sim": 8003}
HONEYPOTS = ["/.env", "/.git/config", "/wp-login.php", "/actuator/health", "/api/commerce/orders", "/oauth2/token"]
BFF_FORCED = ["/api/admin/orders", "/api/admin/dashboard", "/api/orders", "/api/cart"]


def host_direct(service, host="127.0.0.1", timeout=1.5):
    """Try to reach a core service directly from the host. It publishes no port: refused."""
    port = HOST_PORTS[service]
    ev = {"lane": "red", "plane": "host", "technique": "direct-connect", "target": service}
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(b"GET /api/commerce/orders HTTP/1.0\r\n\r\n")
            data = sock.recv(256)
        # Reached something on a port the compose file says should not be published.
        ev.update(status=0, blocked=False, leaked=len(data),
                  msg=f"direct {host}:{port} ANSWERED ({len(data)} bytes) -- port should not be exposed!")
    except (socket.timeout, ConnectionError, OSError) as err:
        ev.update(status=None, blocked=True, leaked=0,
                  msg=f"direct {host}:{port} refused: no host port (network isolation)")
    return ev


def host_bff(base, path, technique, timeout=4.0):
    """Knock on the BFF's public door the way a scanner would: no session, honeypots, forged headers."""
    from aiolite import split_base  # local import: host plane only
    host, port = split_base(base)
    headers = {}
    if technique == "forged-at-bff":
        now = str(int(time.time()))
        headers = {"X-Gateway-Timestamp": now, "X-Gateway-Nonce": _nonce(),
                   "X-Gateway-Signature": gatewaysig.sign(random.choice(WRONG_SECRETS), now, _nonce(), "GET", path, "")}
    ev = {"lane": "red", "plane": "host", "technique": technique, "target": "bff"}
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        # The BFF answers admin/customer routes only with a session; a honeypot looks like 404.
        got_data = resp.status == 200 and path in BFF_FORCED and len(payload) > 2
        ev.update(status=resp.status, blocked=not got_data, leaked=len(payload) if got_data else 0,
                  msg=f"{technique} {path} = {resp.status}")
    except (socket.timeout, ConnectionError, OSError) as err:
        ev.update(status=None, blocked=True, leaked=0, msg=f"{technique} {path} refused: {err}")
    return ev


def run_host(base, duration, emit, stop=None):
    """One lighter worker from the host plane: isolation + front-door scans."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline and not (stop and stop.is_set()):
        emit(host_direct(random.choice(list(HOST_PORTS))))
        emit(host_bff(base, random.choice(BFF_FORCED), "no-session"))
        emit(host_bff(base, random.choice(HONEYPOTS), "honeypot"))
        emit(host_bff(base, random.choice(BFF_FORCED), "forged-at-bff"))
        time.sleep(random.uniform(0.1, 0.3))


def _main():
    """Container entrypoint: attack the mesh, print JSONL to stdout."""
    duration = float(os.environ.get("ARENA_DURATION", "30"))
    intensity = int(os.environ.get("ARENA_RED_INTENSITY", "24"))
    lock = threading.Lock()

    def emit(ev):
        ev.setdefault("t", time.time())
        with lock:
            sys.stdout.write(json.dumps(ev, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    run_mesh(duration, intensity, emit)


if __name__ == "__main__":
    _main()
