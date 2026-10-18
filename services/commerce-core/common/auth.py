"""Local JWT validation.

commerce-core never calls auth-service per request: it verifies tokens itself
against cached JWKS (RS256), so an auth-service outage does not invalidate
tokens that were already issued. An HS256 shared secret is accepted only when
JWT_DEV_HS256_SECRET is set, for local development and tests.
"""

import uuid
from dataclasses import dataclass, field
from functools import lru_cache

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

CUSTOMER = "customer"
SHOP_OWNER = "shop_owner"
KNOWN_ROLES = {CUSTOMER, SHOP_OWNER}


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    roles: frozenset = field(default_factory=frozenset)

    is_authenticated = True
    is_anonymous = False

    def has_role(self, role):
        return role in self.roles

    @property
    def is_shop_owner(self):
        return SHOP_OWNER in self.roles


@lru_cache(maxsize=4)
def _jwks_client(url):
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=300, timeout=3)


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
        key = _jwks_client(settings.JWT_JWKS_URL).get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], **common)
    if alg == "HS256" and settings.JWT_DEV_HS256_SECRET:
        return jwt.decode(token, settings.JWT_DEV_HS256_SECRET, algorithms=["HS256"], **common)
    raise jwt.InvalidTokenError(f"unsupported or disabled signing algorithm: {alg}")


def roles_from_claims(claims):
    raw = claims.get("roles", claims.get("role", []))
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    return frozenset(r for r in raw if r in KNOWN_ROLES)


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
            principal = Principal(id=uuid.UUID(str(claims["sub"])), roles=roles_from_claims(claims))
        except (jwt.PyJWTError, ValueError) as exc:
            raise exceptions.AuthenticationFailed(f"Invalid token: {exc}") from exc
        return principal, claims

    def authenticate_header(self, request):
        return self.keyword


def issue_dev_token(sub, roles, ttl_seconds=600):
    """Mint an HS256 token for local development and tests."""
    import time

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
    if settings.JWT_AUDIENCE:
        claims["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(claims, settings.JWT_DEV_HS256_SECRET, algorithm="HS256")
