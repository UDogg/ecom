import time

import jwt
import pytest
from django.conf import settings

from .conftest import CUSTOMER_ID, TEST_SECRET

pytestmark = pytest.mark.django_db


def bearer(client, token):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def claims(**overrides):
    now = int(time.time())
    base = {"sub": str(CUSTOMER_ID), "roles": ["customer"], "iss": settings.JWT_ISSUER, "iat": now, "exp": now + 60}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


@pytest.mark.parametrize("token", [
    jwt.encode(claims(exp=int(time.time()) - 3600), TEST_SECRET, algorithm="HS256"),
    jwt.encode(claims(), "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256"),
    jwt.encode(claims(iss="https://evil.example"), TEST_SECRET, algorithm="HS256"),
    jwt.encode(claims(sub=None), TEST_SECRET, algorithm="HS256"),
    jwt.encode(claims(), None, algorithm="none"),
    "not-a-jwt",
])
def test_invalid_tokens_are_rejected(anon, token):
    resp = bearer(anon, token).get("/api/commerce/orders")
    assert resp.status_code == 401
    assert resp["WWW-Authenticate"] == "Bearer"


def test_unknown_roles_grant_nothing(anon):
    token = jwt.encode(claims(roles=["superadmin"]), TEST_SECRET, algorithm="HS256")
    assert bearer(anon, token).get("/api/commerce/orders").status_code == 403


def test_single_role_claim_is_accepted_without_escalation(anon):
    token = jwt.encode(claims(roles=None, role="customer"), TEST_SECRET, algorithm="HS256")
    client = bearer(anon, token)
    assert client.get("/api/commerce/orders").status_code == 200
    assert client.get("/api/commerce/admin/products").status_code == 403


def test_dev_tokens_are_disabled_without_secret(anon, settings):
    token = jwt.encode(claims(), TEST_SECRET, algorithm="HS256")
    settings.JWT_DEV_HS256_SECRET = ""
    assert bearer(anon, token).get("/api/commerce/orders").status_code == 401


def test_problem_details_shape_and_traceparent(customer):
    trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    resp = customer.get("/api/commerce/orders/00000000-0000-4000-8000-00000000dead",
                        HTTP_TRACEPARENT=f"00-{trace}-00f067aa0ba902b7-01")
    assert resp.status_code == 404
    assert resp["Content-Type"] == "application/problem+json"
    assert resp.json() == {
        "type": "https://shop.local/errors/not-found",
        "title": "Not found",
        "status": 404,
        "detail": "Order not found.",
        "trace_id": trace,
    }


def test_health_endpoints(anon):
    assert anon.get("/healthz").json()["status"] == "ok"
    assert anon.get("/readyz").json()["status"] == "ready"
