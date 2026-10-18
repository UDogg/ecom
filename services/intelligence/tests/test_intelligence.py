"""Guard tests run anywhere; recommendation tests need PostgreSQL (INTEL_TEST_DATABASE_URL)."""

import json
import os
import secrets
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gateway
from app.main import Recommender, create_app

SECRET = "intel-test-secret-0123456789abcdef0123456789"
_parents = Path(__file__).resolve().parents
VECTORS = (_parents[3] / "docs/contracts/gateway-signing.vectors.json") if len(_parents) > 3 else Path("/nonexistent")


class StubRecommender:
    pool = None

    def recommend(self, product_id, limit):
        return {"items": [], "strategy": "popular", "degraded": False, "asked": [product_id, limit]}


def client(**kw):
    return TestClient(create_app(recommender=StubRecommender(), gateway_secret=SECRET, allow_unguarded=False, **kw))


def signed(method, path, query="", authorization="", secret=SECRET, ts=None):
    ts = str(int(time.time()) if ts is None else ts)
    nonce = secrets.token_hex(12)
    return {"X-Gateway-Timestamp": ts, "X-Gateway-Nonce": nonce,
            "X-Gateway-Signature": gateway.sign(secret, ts, nonce, method, path, query, authorization)}


@pytest.mark.skipif(not VECTORS.exists(), reason="shared vectors not available")
def test_matches_the_shared_cross_language_vectors():
    data = json.loads(VECTORS.read_text())
    for v in data["vectors"]:
        args = (v["timestamp"], v["nonce"], v["method"], v["path"], v["query"], v["authorization"])
        assert gateway.canonical(*args) == v["canonical"], v["name"]
        assert gateway.sign(data["secret"], *args) == v["signature"], v["name"]


def test_unsigned_gets_an_empty_403_before_fastapi_runs():
    resp = client().get("/api/intelligence/recommendations?limit=3")
    assert resp.status_code == 403 and resp.content == b""


def test_signed_gets_through_and_is_single_use():
    c, path, query = client(), "/api/intelligence/recommendations", "limit=3"
    headers = signed("GET", path, query)
    resp = c.get(f"{path}?{query}", headers=headers)
    assert resp.status_code == 200 and resp.json()["asked"] == [None, 3]
    assert c.get(f"{path}?{query}", headers=headers).status_code == 403


def test_signature_bound_to_query_and_fresh():
    c, path = client(), "/api/intelligence/recommendations"
    assert c.get(f"{path}?limit=9", headers=signed("GET", path, "limit=3")).status_code == 403
    assert c.get(f"{path}?limit=3", headers=signed("GET", path, "limit=3", ts=int(time.time()) - 120)).status_code == 403
    assert c.get(f"{path}?limit=3", headers=signed("GET", path, "limit=3", secret="x" * 40)).status_code == 403


def test_validation_errors_are_problems():
    c, path = client(), "/api/intelligence/recommendations"
    resp = c.get(f"{path}?limit=99", headers=signed("GET", path, "limit=99"))
    assert resp.status_code == 400 and resp.headers["content-type"] == "application/problem+json"
    assert "limit" in resp.json()["errors"]


def test_docs_and_openapi_are_not_served():
    c = client()
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert c.get(path, headers=signed("GET", path)).status_code == 404


def test_health_is_open():
    assert client().get("/healthz").status_code == 200


@pytest.mark.parametrize("secret", ["", "short"])
def test_refuses_to_start_without_a_proper_secret(secret):
    with pytest.raises(gateway.GatewayMisconfigured):
        create_app(recommender=StubRecommender(), gateway_secret=secret, allow_unguarded=False)


def test_sheds_load_when_saturated():
    rec = Recommender(pool=None, max_concurrent=1)
    rec.slots.acquire()  # one inference already running
    with pytest.raises(Exception) as exc:
        rec.recommend(None, 3)
    assert getattr(exc.value, "status_code", None) == 503


# ---- against PostgreSQL ------------------------------------------------------

@pytest.fixture
def db_recommender():
    dsn = os.environ.get("INTEL_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("INTEL_TEST_DATABASE_URL not set")
    import psycopg
    from psycopg_pool import ConnectionPool

    schema = f"intel_test_{uuid.uuid4().hex[:8]}"
    ids = {name: str(uuid.uuid4()) for name in ("mug", "kettle", "beans", "candle")}
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"""CREATE SCHEMA {schema};
            CREATE TABLE {schema}.intel_product_popularity (product_id uuid, sku text, name text, price_minor bigint,
                currency text, units bigint);
            CREATE TABLE {schema}.intel_copurchase (product_id uuid, other_id uuid, together bigint);""")
        for name, units in [("mug", 10), ("kettle", 3), ("beans", 7), ("candle", 50)]:
            conn.execute(f"INSERT INTO {schema}.intel_product_popularity VALUES (%s, %s, %s, 1000, 'USD', %s)",
                         [ids[name], name.upper(), name.title(), units])
        conn.execute(f"INSERT INTO {schema}.intel_copurchase VALUES (%s, %s, 5), (%s, %s, 2)",
                     [ids["kettle"], ids["beans"], ids["kettle"], ids["mug"]])
    pool = ConnectionPool(dsn, min_size=1, max_size=2, open=True)
    yield Recommender(pool, schema), ids
    pool.close()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


def test_popular_when_no_product_given(db_recommender):
    rec, ids = db_recommender
    out = rec.recommend(None, 2)
    assert out["strategy"] == "popular" and [i["sku"] for i in out["items"]] == ["CANDLE", "MUG"]


def test_co_purchase_first_then_popular_fill_without_repeats(db_recommender):
    rec, ids = db_recommender
    out = rec.recommend(ids["kettle"], 3)
    assert out["strategy"] == "co_purchase"
    assert [i["sku"] for i in out["items"]] == ["BEANS", "MUG", "CANDLE"]  # never the kettle itself


def test_unknown_product_falls_back_to_popular(db_recommender):
    rec, _ = db_recommender
    out = rec.recommend(str(uuid.uuid4()), 1)
    assert out["strategy"] == "popular" and out["items"][0]["sku"] == "CANDLE"
