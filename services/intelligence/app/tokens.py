"""Verifying the privileged tokens that may skip the BFF (docs/break-glass.md).

SYSTEM (a service account) or break-glass (an operator acting as one user),
always RS256 from auth-service with aud=core. Keys come from a local JWKS
cache that keeps the last good keys if auth-service is unreachable.
"""

import json
import logging
import threading
import time
import urllib.request

import jwt

log = logging.getLogger("intelligence.tokens")


def _fetch(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # internal network: never a proxy
    with opener.open(url, timeout=3) as resp:  # noqa: S310 - URL comes from configuration
        return json.load(resp)


class JWKSCache:
    def __init__(self, url, refresh_seconds=300, min_refetch_seconds=30, fetch=_fetch, clock=time.monotonic):
        self.url, self.refresh_seconds, self.min_refetch_seconds = url, refresh_seconds, min_refetch_seconds
        self._fetch, self._clock = fetch, clock
        self._keys, self._fetched_at, self._attempted_at = None, float("-inf"), float("-inf")
        self._lock = threading.Lock()

    def _refresh(self, now):
        self._attempted_at = now
        try:
            self._keys = {k.key_id: k for k in jwt.PyJWKSet.from_dict(self._fetch(self.url)).keys}
            self._fetched_at = now
        except Exception as exc:  # noqa: BLE001 - keep the last good keys
            log.warning("JWKS refresh failed: %s", exc.__class__.__name__)

    def key(self, kid):
        with self._lock:
            now = self._clock()
            if now - self._fetched_at >= self.refresh_seconds and now - self._attempted_at >= self.min_refetch_seconds:
                self._refresh(now)
            found = (self._keys or {}).get(kid)
            if found is None and now - self._attempted_at >= self.min_refetch_seconds:
                self._refresh(now)
                found = (self._keys or {}).get(kid)
        if found is None:
            raise jwt.InvalidTokenError("unknown signing key")
        return found.key


class PrivilegedTokens:
    def __init__(self, keys, issuer, audience="core"):
        self.keys, self.issuer, self.audience = keys, issuer, audience

    def __call__(self, authorization):
        """A description for the audit log if this is a verified SYSTEM or break-glass token, else None."""
        parts = (authorization or "").split()
        if len(parts) != 2 or parts[0] != "Bearer":
            return None
        try:
            header = jwt.get_unverified_header(parts[1])
            if header.get("alg") != "RS256":
                return None
            claims = jwt.decode(parts[1], self.keys.key(header.get("kid")), algorithms=["RS256"],
                                issuer=self.issuer, audience=self.audience, leeway=30,
                                options={"require": ["exp", "sub", "iss", "aud"]})
        except Exception:  # noqa: BLE001 - any failure to verify is "no"
            return None
        if claims.get("break_glass") is True:
            return (f"BREAK-GLASS {claims.get('bg_by')} as {claims.get('sub')} "
                    f"(grant {claims.get('bg_grant')}, reason {claims.get('bg_reason')!r})")
        roles = claims.get("roles") or []
        if "system" in roles and str(claims.get("sub", "")).startswith("svc:"):
            return f"SYSTEM {claims.get('service')}"
        return None
