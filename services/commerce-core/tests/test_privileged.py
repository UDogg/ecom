"""SYSTEM and break-glass tokens: the only ways past the gateway guard without the BFF's signature."""

import time
from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from common import auth
from common.auth import JWKSCache
from orders.models import Order

from .conftest import CUSTOMER_ID, OTHER_CUSTOMER_ID
from .test_jwks import JWK_A, KEY_A, Clock, FakeAuthService, rs256

pytestmark = pytest.mark.django_db

JWKS_URL = "http://auth-service.test/oauth2/jwks"
SECRET = "privileged-test-secret-0123456789abcdef0123"


@pytest.fixture
def gated(monkeypatch, settings):
    """Production-like: guard on, only auth-service RS256 tokens, audiences bff and core."""
    settings.GATEWAY_SECRET, settings.GATEWAY_ALLOW_UNGUARDED = SECRET, False
    settings.JWT_JWKS_URL, settings.JWT_DEV_HS256_SECRET = JWKS_URL, ""
    settings.JWT_AUDIENCE = ["bff", "core"]
    monkeypatch.setattr(auth, "_jwks_caches", {JWKS_URL: JWKSCache(JWKS_URL, fetch=FakeAuthService(JWK_A), clock=Clock())})


def system_token(**extra):
    claims = {"sub": "svc:svc_ops0001", "roles": ["system"], "aud": ["core"], "client_id": "svc_ops0001",
              "service": "ops", "email": None, **extra}
    return rs256(KEY_A, "key-a", **claims)


def break_glass_token(sub=CUSTOMER_ID, roles=("customer",), **extra):
    return rs256(KEY_A, "key-a", sub=str(sub), roles=list(roles), aud=["core"], break_glass=True,
                 bg_by="svc_ops0001", bg_reason="BFF down, finishing an order", bg_grant="g-1", **extra)


def call(anon, method, path, token, **kw):
    anon.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return getattr(anon, method)(path, **kw)


def test_system_token_reaches_ops_without_the_bff(gated, anon, caplog):
    resp = call(anon, "get", "/api/commerce/ops/status", system_token())
    assert resp.status_code == 200
    assert set(resp.json()) == {"outbox", "orders", "reservations", "checked_at"}
    assert "SYSTEM request from service ops" in caplog.text


def test_system_token_has_no_customer_or_owner_powers(gated, anon):
    for path in ("/api/commerce/orders", "/api/commerce/cart", "/api/commerce/admin/products"):
        assert call(anon, "get", path, system_token()).status_code == 403, path


def test_system_role_on_a_person_is_ignored(gated, anon):
    # A person's token cannot become SYSTEM by claiming the role.
    forged = rs256(KEY_A, "key-a", sub=str(CUSTOMER_ID), roles=["system"], aud=["core"])
    resp = call(anon, "get", "/api/commerce/ops/status", forged)
    assert resp.status_code == 403 and resp.content == b""  # not even past the guard


def test_service_subject_without_system_role_is_invalid(gated, anon):
    token = rs256(KEY_A, "key-a", sub="svc:x", roles=["customer"], aud=["core"], break_glass=True)
    assert call(anon, "get", "/api/commerce/orders", token).status_code == 401


def test_break_glass_acts_as_that_user_and_only_that_user(gated, anon, caplog):
    order_id = str(Order.objects.create(customer_id=CUSTOMER_ID, total_minor=1000, currency="USD").id)

    resp = call(anon, "get", f"/api/commerce/orders/{order_id}", break_glass_token())
    assert resp.status_code == 200 and resp.json()["id"] == order_id
    assert "BREAK-GLASS request: svc_ops0001 acting as" in caplog.text
    assert "BFF down, finishing an order" in caplog.text

    # The same operator acting as someone else sees nothing of the first customer's.
    other = call(anon, "get", f"/api/commerce/orders/{order_id}", break_glass_token(sub=OTHER_CUSTOMER_ID))
    assert other.status_code == 404


def test_break_glass_has_the_users_powers_not_more(gated, anon):
    assert call(anon, "get", "/api/commerce/admin/products", break_glass_token()).status_code == 403
    assert call(anon, "get", "/api/commerce/ops/status", break_glass_token()).status_code == 403
    owner = break_glass_token(roles=("customer", "shop_owner"))
    assert call(anon, "get", "/api/commerce/admin/products", owner).status_code == 200


def test_an_ordinary_user_token_still_cannot_skip_the_bff(gated, anon):
    # A valid RS256 user token with the BFF's audience, used directly: refused at the guard.
    user = rs256(KEY_A, "key-a", aud="bff")
    resp = call(anon, "get", "/api/commerce/orders", user)
    assert resp.status_code == 403 and resp.content == b""
    # Claiming break_glass on a bff-audience token does not help either.
    sneaky = rs256(KEY_A, "key-a", aud="bff", break_glass=True)
    assert call(anon, "get", "/api/commerce/orders", sneaky).status_code == 403


def test_expired_break_glass_token_is_refused(gated, anon):
    stale = break_glass_token(exp=int(time.time()) - 3600)
    assert call(anon, "get", "/api/commerce/orders", stale).status_code == 403


def test_ops_expire_reservations_runs_the_expirer(gated, anon, make_product):
    product = make_product(initial_stock=5)
    order = Order.objects.create(customer_id=CUSTOMER_ID, total_minor=1000, currency="USD")
    order.reservations.create(product=product, quantity=2, expires_at=timezone.now() - timedelta(minutes=1))
    resp = call(anon, "post", "/api/commerce/ops/reservations/expire", system_token())
    assert resp.status_code == 200 and resp.json() == {"expired_orders": 1}
    assert Order.objects.get(id=order.id).status == "expired"
    assert call(anon, "post", "/api/commerce/ops/reservations/expire", break_glass_token()).status_code == 403
