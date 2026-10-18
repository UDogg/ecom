"""The gateway guard: only signed requests from the BFF get through."""

import json
import os
import secrets
import time
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from rest_framework.test import APIClient

from common import gateway

from .conftest import CUSTOMER_ID, client_for

pytestmark = pytest.mark.django_db

SECRET = "guard-test-secret-0123456789abcdef0123456789"


def _vectors_file():
    if os.environ.get("CONTRACTS_DIR"):
        return Path(os.environ["CONTRACTS_DIR"]).parent / "gateway-signing.vectors.json"
    parents = Path(__file__).resolve().parents
    return parents[3] / "docs/contracts/gateway-signing.vectors.json" if len(parents) > 3 else Path("/nonexistent")


VECTORS = _vectors_file()


@pytest.fixture
def guarded():
    with override_settings(GATEWAY_SECRET=SECRET, GATEWAY_ALLOW_UNGUARDED=False):
        yield


def signed_headers(method, path, query="", authorization="", ts=None, nonce=None, secret=SECRET):
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or secrets.token_hex(12)
    return {
        "HTTP_X_GATEWAY_TIMESTAMP": ts,
        "HTTP_X_GATEWAY_NONCE": nonce,
        "HTTP_X_GATEWAY_SIGNATURE": gateway.sign(secret, ts, nonce, method, path, query, authorization),
    }


@pytest.mark.skipif(not VECTORS.exists(), reason="shared vectors not available")
def test_matches_the_shared_cross_language_vectors():
    data = json.loads(VECTORS.read_text())
    for v in data["vectors"]:
        args = (v["timestamp"], v["nonce"], v["method"], v["path"], v["query"], v["authorization"])
        assert gateway.canonical(*args) == v["canonical"], v["name"]
        assert gateway.sign(data["secret"], *args) == v["signature"], v["name"]


def test_unsigned_request_gets_an_empty_403(guarded, anon):
    resp = anon.get("/api/catalog/products")
    assert resp.status_code == 403
    assert resp.content == b""


def test_signed_request_gets_through(guarded, anon):
    assert anon.get("/api/catalog/products", **signed_headers("GET", "/api/catalog/products")).status_code == 200


def test_signature_covers_the_query_string(guarded, anon):
    headers = signed_headers("GET", "/api/catalog/products", "q=mug")
    assert anon.get("/api/catalog/products?q=mug", **headers).status_code == 200
    headers = signed_headers("GET", "/api/catalog/products", "q=mug")
    assert anon.get("/api/catalog/products?q=other", **headers).status_code == 403


def test_signature_is_bound_to_method_and_path(guarded, anon):
    assert anon.post("/api/catalog/products", **signed_headers("GET", "/api/catalog/products")).status_code == 403
    assert anon.get("/api/commerce/orders", **signed_headers("GET", "/api/catalog/products")).status_code == 403


def test_signature_is_bound_to_the_users_token(guarded):
    customer = client_for(CUSTOMER_ID, {"customer"})
    token = customer._credentials["HTTP_AUTHORIZATION"]
    ok = signed_headers("GET", "/api/commerce/orders", authorization=token)
    assert customer.get("/api/commerce/orders", **ok).status_code == 200

    # A captured signature cannot be reused with someone else's token.
    other = client_for(CUSTOMER_ID, {"customer", "shop_owner"})
    stolen = signed_headers("GET", "/api/commerce/admin/products", authorization=token)
    assert other.get("/api/commerce/admin/products", **stolen).status_code == 403


def test_replayed_nonce_is_refused(guarded, anon):
    headers = signed_headers("GET", "/api/catalog/products")
    assert anon.get("/api/catalog/products", **headers).status_code == 200
    assert anon.get("/api/catalog/products", **headers).status_code == 403


@pytest.mark.parametrize("skew", [-61, 61])
def test_stale_or_future_timestamp_is_refused(guarded, anon, skew):
    headers = signed_headers("GET", "/api/catalog/products", ts=int(time.time()) + skew)
    assert anon.get("/api/catalog/products", **headers).status_code == 403


def test_wrong_secret_is_refused(guarded, anon):
    headers = signed_headers("GET", "/api/catalog/products", secret="an-attackers-guess-0123456789abcdef0123")
    assert anon.get("/api/catalog/products", **headers).status_code == 403


def test_malformed_headers_are_refused(guarded, anon):
    for headers in [{"HTTP_X_GATEWAY_TIMESTAMP": "abc", "HTTP_X_GATEWAY_NONCE": "x" * 24, "HTTP_X_GATEWAY_SIGNATURE": "0" * 64},
                    {**signed_headers("GET", "/api/catalog/products"), "HTTP_X_GATEWAY_NONCE": "short"}]:
        assert anon.get("/api/catalog/products", **headers).status_code == 403


def test_a_valid_token_alone_is_not_enough(guarded):
    """A stolen user token used directly against the core (curl, Postman) is refused."""
    customer = client_for(CUSTOMER_ID, {"customer"})
    assert customer.get("/api/commerce/orders").status_code == 403


def test_health_probes_stay_open(guarded, anon):
    assert anon.get("/healthz").status_code == 200
    assert anon.get("/readyz").status_code == 200


def test_refused_requests_never_reach_the_database(guarded, anon, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert anon.post("/api/commerce/checkout", {"items": []}, format="json").status_code == 403


def test_nonce_cache_refuses_when_full():
    cache = gateway.NonceCache(max_size=2)
    now = time.time()
    assert cache.remember("a" * 24, now) and cache.remember("b" * 24, now)
    assert not cache.remember("c" * 24, now)        # full of live nonces: refuse, never forget
    assert cache.remember("c" * 24, now + 500)      # old ones expired: room again


@pytest.mark.parametrize("secret,allow,ok", [("", False, False), ("short", False, False), (SECRET, False, True),
                                             ("", True, True)])
def test_fails_closed_without_a_proper_secret(secret, allow, ok):
    if ok:
        gateway.check_configuration(secret, allow)
    else:
        with pytest.raises(ImproperlyConfigured):
            gateway.check_configuration(secret, allow)
        with override_settings(GATEWAY_SECRET=secret, GATEWAY_ALLOW_UNGUARDED=allow), \
                pytest.raises(ImproperlyConfigured):
            APIClient().get("/healthz")
