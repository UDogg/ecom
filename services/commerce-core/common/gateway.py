"""Gateway guard: nothing reaches commerce-core except through the BFF.

Every request must carry the BFF's signature (docs/request-signing.md):

    X-Gateway-Timestamp  unix seconds
    X-Gateway-Nonce      one-time value
    X-Gateway-Signature  hex HMAC-SHA256(secret, "v1\\n" ts "\\n" nonce "\\n" METHOD "\\n" path "\\n" query "\\n"
                                         hex(sha256(Authorization header)))

so a signature is good for one method, path, query and user token, for 60
seconds, once. Anything else gets an empty 403 before authentication, parsing
or any database work. Fail closed: no GATEWAY_SECRET means the web process
refuses to start, unless GATEWAY_ALLOW_UNGUARDED=true (local dev and tests).

The same function lives in the Go BFF, the Go and FastAPI services and the
Spring auth-service; docs/contracts/gateway-signing.vectors.json keeps them
in step (and in step with Ensuretech's GatewaySeal).
"""

import hashlib
import hmac
import logging
import threading
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse

log = logging.getLogger(__name__)

VERSION = "v1"
WINDOW_SECONDS = 60
MAX_NONCES = 200_000
MIN_SECRET_LENGTH = 32


def canonical(ts, nonce, method, path, query, authorization):
    return (f"{VERSION}\n{ts}\n{nonce}\n{method.upper()}\n{path}\n{query}\n"
            + hashlib.sha256((authorization or "").encode()).hexdigest())


def sign(secret, ts, nonce, method, path, query, authorization):
    key = secret if isinstance(secret, bytes) else secret.encode()
    return hmac.new(key, canonical(ts, nonce, method, path, query, authorization).encode(), hashlib.sha256).hexdigest()


def check_configuration(secret, allow_unguarded):
    """Raise unless the guard can actually protect this process."""
    if allow_unguarded:
        log.warning("GATEWAY_ALLOW_UNGUARDED is on: requests that bypass the BFF are NOT refused. "
                    "Local development and tests only.")
        return
    if not secret:
        raise ImproperlyConfigured("GATEWAY_SECRET is not set, so commerce-core would answer anyone who can reach it. "
                                   "Set it to the BFF's secret; GATEWAY_ALLOW_UNGUARDED=true is for local development only.")
    if len(secret) < MIN_SECRET_LENGTH:
        raise ImproperlyConfigured(f"GATEWAY_SECRET is shorter than {MIN_SECRET_LENGTH} characters "
                                   "(use `openssl rand -hex 32`).")


class NonceCache:
    """Seen nonces for one process. A full cache refuses rather than forgets."""

    def __init__(self, max_size=MAX_NONCES, window=WINDOW_SECONDS):
        self.max_size, self.window = max_size, window
        self._seen = {}
        self._lock = threading.Lock()

    def remember(self, nonce, now):
        with self._lock:
            if len(self._seen) >= self.max_size:
                expired = [k for k, t in self._seen.items() if now - t > self.window * 2]
                for k in expired:
                    del self._seen[k]
                if len(self._seen) >= self.max_size:
                    return False
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


def verify(secret, nonces, method, path, query, headers, now=None):
    ts = headers.get("X-Gateway-Timestamp", "")
    nonce = headers.get("X-Gateway-Nonce", "")
    signature = headers.get("X-Gateway-Signature", "")
    if not (ts.isdigit() and len(ts) <= 12 and 16 <= len(nonce) <= 64 and len(signature) == 64):
        return False
    now = time.time() if now is None else now
    if abs(now - int(ts)) > WINDOW_SECONDS:
        return False
    expected = sign(secret, ts, nonce, method, path, query, headers.get("Authorization", ""))
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        return False
    return nonces.remember(nonce, now)  # only after the signature checks out


class GatewayGuardMiddleware:
    """Must be first in MIDDLEWARE."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.secret = settings.GATEWAY_SECRET or ""
        self.allow_unguarded = settings.GATEWAY_ALLOW_UNGUARDED
        check_configuration(self.secret, self.allow_unguarded)
        self.open_paths = frozenset(settings.GATEWAY_OPEN_PATHS)
        self.nonces = NonceCache()

    def __call__(self, request):
        if self.secret and request.path not in self.open_paths:
            # Sign over the raw target when the server exposes it. A framework
            # that decodes the path makes encoded paths fail closed.
            raw = request.META.get("RAW_URI", "")
            path = raw.split("?", 1)[0] if raw else request.path
            if not verify(self.secret, self.nonces, request.method, path, request.META.get("QUERY_STRING", ""),
                          request.headers):
                log.info("refused a request that did not come through the BFF: %s %s from %s",
                         request.method, request.path, request.META.get("REMOTE_ADDR"))
                return HttpResponse(status=403)
        elif not self.secret and not self.allow_unguarded:  # unreachable after check_configuration; belt and braces
            return HttpResponse(status=403)
        return self.get_response(request)
