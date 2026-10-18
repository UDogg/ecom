"""Gateway guard for the FastAPI services: nothing gets in except through the BFF.

A raw ASGI middleware, so it runs before FastAPI routes, parses or validates
anything: an unsigned request gets an empty 403 and no application code runs.

Signature (docs/request-signing.md):
    hex HMAC-SHA256(secret, "v1\\n" ts "\\n" nonce "\\n" METHOD "\\n" path "\\n" query "\\n" hex(sha256(Authorization)))
over the raw path and raw query (ASGI's raw_path / query_string), good for one
call, for 60 seconds, once. Fail closed: no secret, no start.
"""

import hashlib
import hmac
import logging
import threading
import time

log = logging.getLogger("intelligence.gateway")

VERSION = "v1"
WINDOW_SECONDS = 60
MAX_NONCES = 200_000
MIN_SECRET_LENGTH = 32


class GatewayMisconfigured(RuntimeError):
    pass


def canonical(ts, nonce, method, path, query, authorization):
    return (f"{VERSION}\n{ts}\n{nonce}\n{method.upper()}\n{path}\n{query}\n"
            + hashlib.sha256((authorization or "").encode()).hexdigest())


def sign(secret, ts, nonce, method, path, query, authorization):
    key = secret if isinstance(secret, bytes) else secret.encode()
    return hmac.new(key, canonical(ts, nonce, method, path, query, authorization).encode(), hashlib.sha256).hexdigest()


class GatewayGuard:
    def __init__(self, app, secret, allow_unguarded=False, open_paths=("/healthz", "/readyz"), clock=time.time):
        if allow_unguarded:
            log.warning("GATEWAY_ALLOW_UNGUARDED is on: requests that bypass the BFF are NOT refused. "
                        "Local development and tests only.")
        elif not secret:
            raise GatewayMisconfigured("GATEWAY_SECRET is not set, so this service would answer anyone who can reach it.")
        elif len(secret) < MIN_SECRET_LENGTH:
            raise GatewayMisconfigured("GATEWAY_SECRET is shorter than 32 characters (openssl rand -hex 32).")
        self.app, self.secret, self.open_paths, self.clock = app, (secret or "").encode(), frozenset(open_paths), clock
        self._seen, self._lock = {}, threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.secret or scope["path"] in self.open_paths or self._signed(scope):
            await self.app(scope, receive, send)
            return
        log.info("refused a request that did not come through the BFF: %s %s", scope["method"], scope["path"])
        await send({"type": "http.response.start", "status": 403, "headers": [(b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})

    def _signed(self, scope):
        headers = {}
        for k, v in scope.get("headers", []):
            headers.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
        ts = headers.get("x-gateway-timestamp", "")
        nonce = headers.get("x-gateway-nonce", "")
        signature = headers.get("x-gateway-signature", "")
        if not (ts.isdigit() and len(ts) <= 12 and 16 <= len(nonce) <= 64 and len(signature) == 64):
            return False
        now = self.clock()
        if abs(now - int(ts)) > WINDOW_SECONDS:
            return False
        path = (scope.get("raw_path") or scope["path"].encode()).decode("latin-1")
        query = scope.get("query_string", b"").decode("latin-1")
        expected = sign(self.secret, ts, nonce, scope["method"], path, query, headers.get("authorization", ""))
        if not hmac.compare_digest(expected.encode(), signature.encode()):
            return False
        return self._remember(nonce, now)

    def _remember(self, nonce, now):
        """Only after the signature checks out. A full cache refuses rather than forgets."""
        with self._lock:
            if len(self._seen) >= MAX_NONCES:
                for k in [k for k, t in self._seen.items() if now - t > WINDOW_SECONDS * 2]:
                    del self._seen[k]
                if len(self._seen) >= MAX_NONCES:
                    return False
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True
