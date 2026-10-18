"""RS256 tokens from auth-service, validated locally against cached JWKS."""

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings

from common import auth
from common.auth import JWKSCache
from orders.models import Order

from .conftest import CUSTOMER_ID, checkout

pytestmark = pytest.mark.django_db

JWKS_URL = "http://auth-service.test/oauth2/jwks"


def make_key(kid):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return private, {**public, "kid": kid, "use": "sig", "alg": "RS256"}


KEY_A, JWK_A = make_key("key-a")
KEY_B, JWK_B = make_key("key-b")


def rs256(private, kid, **claims):
    now = int(time.time())
    body = {"sub": str(CUSTOMER_ID), "roles": ["customer"], "iss": settings.JWT_ISSUER, "iat": now,
            "exp": now + 300, "email": "customer@shop.local", **claims}
    return jwt.encode(body, private, algorithm="RS256", headers={"kid": kid})


class FakeAuthService:
    """Stands in for GET /oauth2/jwks; can be taken down and rotate keys."""

    def __init__(self, *jwks):
        self.keys, self.up, self.calls = list(jwks), True, 0

    def __call__(self, url):
        self.calls += 1
        if not self.up:
            raise ConnectionRefusedError("auth-service down")
        return {"keys": self.keys}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def auth_service(monkeypatch, settings):
    fake, clock = FakeAuthService(JWK_A), Clock()
    settings.JWT_JWKS_URL = JWKS_URL
    settings.JWT_DEV_HS256_SECRET = ""  # production-like: only auth-service tokens
    monkeypatch.setattr(auth, "_jwks_caches", {JWKS_URL: JWKSCache(JWKS_URL, fetch=fake, clock=clock)})
    fake.clock = clock
    return fake


def get_orders(client, token):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client.get("/api/commerce/orders")


def test_auth_service_token_is_accepted(anon, auth_service):
    assert get_orders(anon, rs256(KEY_A, "key-a")).status_code == 200


def test_email_claim_is_recorded_on_the_order(anon, auth_service, make_product):
    anon.credentials(HTTP_AUTHORIZATION=f"Bearer {rs256(KEY_A, 'key-a')}")
    order_id = checkout(anon, [(make_product(), 1)]).json()["id"]
    assert Order.objects.get(id=order_id).customer_email == "customer@shop.local"


def test_keys_are_cached_not_fetched_per_request(anon, auth_service):
    for _ in range(5):
        get_orders(anon, rs256(KEY_A, "key-a"))
    assert auth_service.calls == 1


def test_tokens_keep_working_while_auth_service_is_down(anon, auth_service):
    token = rs256(KEY_A, "key-a")
    assert get_orders(anon, token).status_code == 200

    auth_service.up = False
    auth_service.clock.now += 3600  # well past the refresh interval
    assert get_orders(anon, token).status_code == 200
    assert auth_service.calls == 2  # it tried, failed, and kept the last good keys


def test_cold_start_without_auth_service_rejects_tokens(anon, auth_service):
    auth_service.up = False
    resp = get_orders(anon, rs256(KEY_A, "key-a"))
    assert resp.status_code == 401
    assert "signing keys unavailable" in resp.json()["detail"]


def test_rotated_key_is_picked_up_on_unknown_kid(anon, auth_service):
    assert get_orders(anon, rs256(KEY_A, "key-a")).status_code == 200
    auth_service.keys = [JWK_A, JWK_B]
    auth_service.clock.now += 60
    assert get_orders(anon, rs256(KEY_B, "key-b")).status_code == 200


def test_unknown_kid_refetch_is_rate_limited(anon, auth_service):
    get_orders(anon, rs256(KEY_A, "key-a"))
    for _ in range(10):
        assert get_orders(anon, rs256(KEY_B, "forged-kid")).status_code == 401
    assert auth_service.calls == 1  # no refetch storm inside the 30 s window


def test_token_signed_by_another_key_is_rejected(anon, auth_service):
    assert get_orders(anon, rs256(KEY_B, "key-a")).status_code == 401


def test_wrong_issuer_is_rejected(anon, auth_service):
    assert get_orders(anon, rs256(KEY_A, "key-a", iss="http://evil.example")).status_code == 401


def test_audience_is_enforced_when_configured(anon, auth_service, settings):
    settings.JWT_AUDIENCE = "bff"
    assert get_orders(anon, rs256(KEY_A, "key-a", aud="bff")).status_code == 200
    assert get_orders(anon, rs256(KEY_A, "key-a", aud="some-other-client")).status_code == 401
    assert get_orders(anon, rs256(KEY_A, "key-a")).status_code == 401  # no aud at all


def test_hs256_is_refused_when_dev_tokens_are_disabled(anon, auth_service):
    # Classic alg-confusion: an HS256 token "signed" with the public key. PyJWT
    # refuses to create one, so build it by hand the way an attacker would.
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "kid": "key-a"}).encode())
    payload = b64(json.dumps({"sub": str(CUSTOMER_ID), "roles": ["shop_owner"], "iss": settings.JWT_ISSUER,
                              "exp": int(time.time()) + 60}).encode())
    signature = b64(hmac.new(json.dumps(JWK_A).encode(), header + b"." + payload, hashlib.sha256).digest())
    forged = b".".join([header, payload, signature]).decode()
    assert get_orders(anon, forged).status_code == 401
