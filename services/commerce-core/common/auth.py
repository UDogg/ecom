"""Local JWT validation.

commerce-core never calls auth-service per request: it verifies tokens itself
against cached JWKS (RS256), so an auth-service outage does not invalidate
tokens that were already issued. An HS256 shared secret is accepted only when
JWT_DEV_HS256_SECRET is set, for local development and tests.
"""

import json
import logging
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

CUSTOMER = "customer"
SHOP_OWNER = "shop_owner"
SYSTEM = "system"
KNOWN_ROLES = {CUSTOMER, SHOP_OWNER, SYSTEM}
PRIVILEGED_AUDIENCE = "core"


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID | None  # None for a service account (SYSTEM): it is nobody's customer
    roles: frozenset = field(default_factory=frozenset)
    email: str | None = None
    service: str | None = None          # service account name, for SYSTEM tokens
    break_glass_by: str | None = None   # operator's client id, for break-glass tokens

    is_authenticated = True
    is_anonymous = False

    def has_role(self, role):
        return role in self.roles

    @property
    def is_shop_owner(self):
        return SHOP_OWNER in self.roles

    @property
    def is_system(self):
        return SYSTEM in self.roles


def _fetch_jwks(url):
    with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - URL comes from settings
        return json.load(resp)


class JWKSCache:
    """Signing keys from auth-service, cached locally.

    Keys refresh every `refresh_seconds`. An unknown `kid` (key rotation)
    triggers an early refetch, at most once per `min_refetch_seconds`. If
    auth-service is unreachable the last good keys stay in use, so tokens it
    already issued keep working through an outage (README section 8.4).
    """

    def __init__(self, url, refresh_seconds=300, min_refetch_seconds=30, fetch=_fetch_jwks, clock=time.monotonic):
        self.url, self.refresh_seconds, self.min_refetch_seconds = url, refresh_seconds, min_refetch_seconds
        self._fetch, self._clock = fetch, clock
        self._keys = None
        self._fetched_at = self._attempted_at = float("-inf")
        self._lock = threading.Lock()

    def _refresh(self, now):
        self._attempted_at = now
        try:
            keyset = jwt.PyJWKSet.from_dict(self._fetch(self.url))
        except Exception as exc:  # noqa: BLE001 - any fetch/parse failure degrades the same way
            log.warning("JWKS refresh from %s failed: %s", self.url, exc.__class__.__name__)
            return
        self._keys = {k.key_id: k for k in keyset.keys}
        self._fetched_at = now

    def signing_key(self, kid):
        with self._lock:
            now = self._clock()
            if now - self._fetched_at >= self.refresh_seconds and now - self._attempted_at >= self.min_refetch_seconds:
                self._refresh(now)
            key = (self._keys or {}).get(kid)
            if key is None and now - self._attempted_at >= self.min_refetch_seconds:
                self._refresh(now)
                key = (self._keys or {}).get(kid)
        if key is None:
            raise jwt.InvalidTokenError("signing keys unavailable" if self._keys is None else "unknown signing key")
        return key.key


_jwks_caches = {}


def jwks_cache(url):
    if url not in _jwks_caches:
        _jwks_caches[url] = JWKSCache(url)
    return _jwks_caches[url]


def decode_token(token):
    options = {"require": ["exp", "sub"]}
    common = {
        "issuer": settings.JWT_ISSUER,
        "audience": settings.JWT_AUDIENCE,
        "leeway": settings.JWT_LEEWAY_SECONDS,
        "options": {**options, "verify_aud": settings.JWT_AUDIENCE is not None},
    }
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")

    if alg == "RS256" and settings.JWT_JWKS_URL:
        key = jwks_cache(settings.JWT_JWKS_URL).signing_key(header.get("kid"))
        return jwt.decode(token, key, algorithms=["RS256"], **common)
    if alg == "HS256" and settings.JWT_DEV_HS256_SECRET:
        return jwt.decode(token, settings.JWT_DEV_HS256_SECRET, algorithms=["HS256"], **common)
    raise jwt.InvalidTokenError(f"unsupported or disabled signing algorithm: {alg}")


def roles_from_claims(claims):
    raw = claims.get("roles", claims.get("role", []))
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    roles = frozenset(r for r in raw if r in KNOWN_ROLES)
    # SYSTEM belongs to service accounts only, never to a person's token.
    if SYSTEM in roles and not str(claims.get("sub", "")).startswith("svc:"):
        roles -= {SYSTEM}
    return roles


def audiences(claims):
    aud = claims.get("aud", [])
    return {aud} if isinstance(aud, str) else set(aud or [])


def is_privileged(claims):
    """SYSTEM or break-glass: the only tokens that may skip the BFF (aud=core, which the BFF's tokens never carry)."""
    if PRIVILEGED_AUDIENCE not in audiences(claims):
        return False
    if claims.get("break_glass") is True:
        return True
    return SYSTEM in roles_from_claims(claims)


def principal_from_claims(claims):
    roles = roles_from_claims(claims)
    sub = str(claims["sub"])
    email = claims.get("email")
    if sub.startswith("svc:"):
        if SYSTEM not in roles:
            raise ValueError("service subject without the system role")
        return Principal(id=None, roles=frozenset({SYSTEM}), service=str(claims.get("service", sub)))
    return Principal(id=uuid.UUID(sub), roles=roles, email=email if isinstance(email, str) else None,
                     break_glass_by=str(claims.get("bg_by")) if claims.get("break_glass") is True else None)


class JWTAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise exceptions.AuthenticationFailed("Malformed Authorization header.")
        try:
            claims = decode_token(parts[1])
            principal = principal_from_claims(claims)
        except (jwt.PyJWTError, ValueError) as exc:
            raise exceptions.AuthenticationFailed(f"Invalid token: {exc}") from exc
        return principal, claims

    def authenticate_header(self, request):
        return self.keyword


def issue_dev_token(sub, roles, ttl_seconds=600, email=None):
    """Mint an HS256 token for local development and tests."""
    if not settings.JWT_DEV_HS256_SECRET:
        raise RuntimeError("JWT_DEV_HS256_SECRET is not set; dev tokens are disabled.")
    now = int(time.time())
    claims = {
        "sub": str(sub),
        "roles": sorted(roles),
        "iss": settings.JWT_ISSUER,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if email:
        claims["email"] = email
    if settings.JWT_AUDIENCE:
        claims["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(claims, settings.JWT_DEV_HS256_SECRET, algorithm="HS256")
