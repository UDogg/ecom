"""payments-sim against a real PostgreSQL (PAYMENTS_TEST_DATABASE_URL); webhooks captured in memory."""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import store as ledger
from app.main import create_app
from app.webhooks import Deliverer, backoff_seconds

SECRET = "payments-test-secret-0123456789abcdef0123456789"
CALLERS = {
    "commerce": {"sub": "svc:svc_commerce", "roles": ["system"], "service": "commerce"},
    "ops": {"sub": "svc:svc_ops", "roles": ["system"], "service": "ops"},
    "payments": {"sub": "svc:svc_payments", "roles": ["system"], "service": "payments"},
    "customer": {"sub": "u1", "roles": ["customer"], "break_glass": True},
}


def privileged(authorization):
    return CALLERS.get(authorization.removeprefix("Bearer "))


@pytest.fixture
def env():
    dsn = os.environ.get("PAYMENTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("PAYMENTS_TEST_DATABASE_URL not set")
    schema = f"payments_test_{uuid.uuid4().hex[:8]}"
    os.environ["PAYMENTS_DB_SCHEMA"] = schema
    pool = ConnectionPool(dsn, min_size=1, max_size=4, kwargs={"row_factory": dict_row}, open=True)
    sent = []
    deliverer = Deliverer(pool, schema, token=None, send=lambda payload: sent.append(payload) or 200)
    app = create_app(pool=pool, gateway_secret=SECRET, allow_unguarded=False, privileged=privileged,
                     chaos_enabled=True, start_webhooks=False)
    with TestClient(app) as client:
        yield client, deliverer, sent, pool, schema
    with ConnectionPool(dsn, min_size=1, max_size=1, open=True) as p, p.connection() as c:
        c.execute(f"DROP SCHEMA {schema} CASCADE")


def as_(who):
    return {"Authorization": f"Bearer {who}"}


def charge(client, source="tok_visa", key=None, amount=2599, who="commerce"):
    return client.post("/api/payments/charges", headers=as_(who), json={
        "idempotency_key": key or f"att_{uuid.uuid4().hex}", "order_ref": "order-1", "amount_minor": amount,
        "currency": "USD", "source": source})


def test_only_commerce_may_charge(env):
    client, *_ = env
    assert client.post("/api/payments/charges", json={}).status_code == 403          # no token: the guard
    assert charge(client, who="payments").status_code == 403                      # wrong service
    assert charge(client, who="customer").status_code == 403                      # a person, even break-glass
    assert charge(client).status_code == 201


def test_success_and_decline_with_webhooks(env):
    client, deliverer, sent, *_ = env
    ok = charge(client).json()
    bad = charge(client, "tok_declined").json()
    assert (ok["status"], ok["version"]) == ("succeeded", 1)
    assert (bad["status"], bad["failure_code"]) == ("failed", "card_declined")
    assert deliverer.deliver_due() == 2
    assert sorted(e["type"] for e in sent) == ["charge.failed", "charge.succeeded"]
    assert len({e["event_id"] for e in sent}) == 2


def test_charges_are_idempotent(env):
    client, deliverer, sent, *_ = env
    first = charge(client, key="att_same_key_123")
    again = charge(client, key="att_same_key_123")
    assert first.status_code == 201 and again.status_code == 200 and first.json() == again.json()
    assert charge(client, key="att_same_key_123", amount=1).status_code == 409
    deliverer.deliver_due()
    assert len(sent) == 1  # one charge, one webhook


def test_status_lookup_for_reconciliation(env):
    client, *_ = env
    key = f"att_{uuid.uuid4().hex}"
    charge(client, key=key)
    assert client.get(f"/api/payments/charges/{key}", headers=as_("commerce")).json()["status"] == "succeeded"
    assert client.get("/api/payments/charges/att_never_sent_1", headers=as_("commerce")).status_code == 404


def test_refunds_bump_the_version_and_are_idempotent(env):
    client, deliverer, sent, *_ = env
    key = f"att_{uuid.uuid4().hex}"
    charge(client, key=key, amount=1000)
    r1 = client.post(f"/api/payments/charges/{key}/refunds", headers=as_("commerce"),
                     json={"idempotency_key": "refund_key_1", "amount_minor": 1000}).json()
    r2 = client.post(f"/api/payments/charges/{key}/refunds", headers=as_("commerce"),
                     json={"idempotency_key": "refund_key_1", "amount_minor": 1000}).json()
    assert r1 == r2 and (r1["status"], r1["version"], r1["refunded_minor"]) == ("refunded", 2, 1000)
    assert client.post(f"/api/payments/charges/{key}/refunds", headers=as_("commerce"),
                       json={"idempotency_key": "refund_key_2", "amount_minor": 1}).status_code == 409
    deliverer.deliver_due()
    assert [e["type"] for e in sent] == ["charge.succeeded", "charge.refunded"]


def test_timeout_after_capture(env):
    client, *_ = env
    key = f"att_{uuid.uuid4().hex}"
    started = time.monotonic()
    resp = charge(client, "tok_timeout", key=key)
    assert time.monotonic() - started >= 5  # the caller would have given up long before this
    assert resp.json()["status"] == "succeeded"
    assert client.get(f"/api/payments/charges/{key}", headers=as_("commerce")).json()["status"] == "succeeded"


def test_chaos_is_ops_only_and_works(env):
    client, deliverer, sent, *_ = env
    assert client.put("/api/payments/chaos", headers=as_("commerce"), json={"mode": "decline_all"}).status_code == 403
    assert client.put("/api/payments/chaos", headers=as_("ops"), json={"mode": "decline_all"}).status_code == 200
    assert charge(client).json()["status"] == "failed"
    client.put("/api/payments/chaos", headers=as_("ops"), json={"mode": "duplicate_webhooks"})
    charge(client)
    sent.clear()
    deliverer.deliver_due()
    succeeded = [e for e in sent if e["type"] == "charge.succeeded"]
    assert len(succeeded) == 2 and succeeded[0]["charge"] == succeeded[1]["charge"]  # same charge, two deliveries
    assert succeeded[0]["event_id"] != succeeded[1]["event_id"]


def test_failed_deliveries_are_retried_with_backoff(env):
    client, deliverer, sent, pool, schema = env
    charge(client)
    deliverer._send = lambda payload: 503
    assert deliverer.deliver_due() == 0
    with pool.connection() as c:
        row = c.execute(f"SELECT attempts, last_error, next_attempt_at > now() AS later FROM {schema}.webhook").fetchone()
    assert (row["attempts"], row["last_error"], row["later"]) == (1, "HTTP 503", True)
    assert 0 < backoff_seconds(3) <= 8 and backoff_seconds(50) <= 300


def test_validation(env):
    client, *_ = env
    assert charge(client, source="tok_stolen").status_code == 400
    assert charge(client, amount=0).status_code == 400
    assert charge(client, key="bad key!").status_code == 400
