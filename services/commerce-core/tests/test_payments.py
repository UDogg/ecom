"""Paying, webhooks, reconciliation and refunds against a fake provider."""

import threading
import time
import uuid
from datetime import timedelta

import jwt
import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Inventory
from orders import payments
from orders.models import Order, PaymentAttempt
from outbox.models import OutboxEvent

from .conftest import CUSTOMER_ID, TEST_SECRET, checkout

pytestmark = pytest.mark.django_db


class FakeProvider:
    """Behaves like payments-sim: versioned charges keyed by attempt id."""

    def __init__(self):
        self.charges, self.calls = {}, []
        self.unavailable = False
        self.outcome = {}

    def _snapshot(self, key):
        return dict(self.charges[key])

    def charge(self, attempt):
        self.calls.append(("charge", attempt.source))
        key = str(attempt.id)
        status, code = {"tok_declined": ("failed", "card_declined"),
                        "tok_insufficient": ("failed", "insufficient_funds")}.get(attempt.source, ("succeeded", None))
        self.charges.setdefault(key, {"id": "ch_" + key[:8], "idempotency_key": key, "status": status,
                                      "failure_code": code, "refunded_minor": 0, "version": 1,
                                      "amount_minor": attempt.amount_minor})
        if self.unavailable or attempt.source == "tok_timeout":
            raise payments.ProviderUnavailable("timed out")
        return self._snapshot(key)

    def lookup(self, attempt):
        key = str(attempt.id)
        if self.unavailable:
            raise payments.ProviderUnavailable("down")
        if key not in self.charges:
            raise payments.ProviderRefused(404, b"{}")
        return self._snapshot(key)

    def refund(self, attempt, amount):
        c = self.charges[str(attempt.id)]
        if c["refunded_minor"] == 0:
            c.update(refunded_minor=c["amount_minor"], status="refunded", version=c["version"] + 1)
        return self._snapshot(str(attempt.id))


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(payments, "provider", fake)
    return fake


def place(customer, make_product, qty=1, price=2500):
    product = make_product(price_minor=price, initial_stock=10)
    return checkout(customer, [(product, qty)]).json()["id"], product


def pay(client, order_id, source="tok_visa"):
    return client.post(f"/api/commerce/orders/{order_id}/pay", {"source": source}, format="json")


def service_client(service):
    client = APIClient()
    token = jwt.encode({"sub": f"svc:svc_{service}", "roles": ["system"], "service": service, "iss": "http://localhost:8080",
                        "exp": int(time.time()) + 60}, TEST_SECRET, algorithm="HS256")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def events(kind):
    return OutboxEvent.objects.filter(event_type=kind).count()


def test_successful_payment_marks_the_order_paid(customer, make_product, provider):
    order_id, _ = place(customer, make_product)
    resp = pay(customer, order_id)
    assert resp.status_code == 201
    assert resp.json()["status"] == "paid" and resp.json()["payment"] == {"status": "captured", "failure_code": None}
    assert events("payment.captured.v1") == 1


def test_decline_then_retry_with_another_card(customer, make_product, provider):
    order_id, _ = place(customer, make_product)
    declined = pay(customer, order_id, "tok_declined").json()
    assert declined["status"] == "payment_failed" and declined["payment"]["failure_code"] == "card_declined"
    assert pay(customer, order_id, "tok_mastercard").json()["status"] == "paid"
    assert PaymentAttempt.objects.filter(order_id=order_id).count() == 2


def test_timeout_is_not_a_failure_and_reconciliation_settles_it(customer, make_product, provider, settings):
    order_id, _ = place(customer, make_product)
    resp = pay(customer, order_id, "tok_timeout")
    assert resp.status_code == 202 and resp.json()["status"] == "pending_payment"
    assert resp.json()["payment"]["status"] == "pending"

    # No second charge while the first one's outcome is unknown.
    again = pay(customer, order_id)
    assert again.status_code == 409 and again.json()["type"].endswith("/payment-in-progress")

    settings.PAYMENT_RECONCILE_AFTER_SECONDS = 0
    assert payments.reconcile() == {"settled": 1, "refunded": 0}
    assert Order.objects.get(id=order_id).status == "paid"  # the provider had taken the money


def test_webhooks_are_idempotent_and_order_independent(customer, make_product, provider):
    order_id, _ = place(customer, make_product)
    pay(customer, order_id, "tok_timeout")
    key = str(PaymentAttempt.objects.get(order_id=order_id).id)
    hook = service_client("payments")
    succeeded = {"event_id": str(uuid.uuid4()), "type": "charge.succeeded", "charge": provider._snapshot(key)}

    assert hook.post("/api/commerce/payments/webhook", succeeded, format="json").json()["result"] == "pending->captured"
    assert hook.post("/api/commerce/payments/webhook", succeeded, format="json").json()["result"] == "duplicate"
    # The same charge again under a new event id (a redelivery): stale by version.
    resent = {**succeeded, "event_id": str(uuid.uuid4())}
    assert hook.post("/api/commerce/payments/webhook", resent, format="json").json()["result"] == "stale"
    assert events("payment.captured.v1") == 1
    assert Order.objects.get(id=order_id).status == "paid"


def test_capture_for_an_expired_order_is_refunded_automatically(customer, make_product, provider, settings):
    order_id, product = place(customer, make_product, qty=2)
    pay(customer, order_id, "tok_timeout")
    Order.objects.filter(id=order_id).update(status="expired")  # the reservation ran out meanwhile

    settings.PAYMENT_RECONCILE_AFTER_SECONDS = 0
    payments.reconcile()   # learns of the capture: order cannot take it, so it is flagged...
    result = payments.reconcile()  # ...and refunded
    attempt = PaymentAttempt.objects.get(order_id=order_id)
    assert result["refunded"] == 1 or attempt.status == "refunded"
    assert attempt.status == "refunded" and not attempt.needs_refund
    assert Order.objects.get(id=order_id).status == "expired"  # never sold, money returned


def test_provider_never_received_it(customer, make_product, provider, settings):
    order_id, _ = place(customer, make_product)
    provider.unavailable = True
    pay(customer, order_id)
    provider.unavailable = False
    provider.charges.clear()  # nothing reached the provider
    PaymentAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=5))
    settings.PAYMENT_RECONCILE_AFTER_SECONDS = 0
    payments.reconcile()
    assert PaymentAttempt.objects.get(order_id=order_id).failure_code == "not_received"
    assert Order.objects.get(id=order_id).status == "payment_failed"


def test_only_payments_sim_may_post_webhooks(customer, provider):
    body = {"event_id": str(uuid.uuid4()), "type": "charge.succeeded", "charge": {}}
    assert customer.post("/api/commerce/payments/webhook", body, format="json").status_code == 403
    assert service_client("ops").post("/api/commerce/payments/webhook", body, format="json").status_code == 403
    assert service_client("payments").post("/api/commerce/payments/webhook", body, format="json").status_code == 400


def test_customers_cannot_pay_someone_elses_order(customer, other_customer, make_product, provider):
    order_id, _ = place(customer, make_product)
    assert pay(other_customer, order_id).status_code == 404
    assert provider.calls == []


def test_paid_orders_cannot_be_paid_again(customer, make_product, provider):
    order_id, _ = place(customer, make_product)
    pay(customer, order_id)
    assert pay(customer, order_id).status_code == 409
    assert len(provider.calls) == 1


def test_owner_refunds_a_paid_order(owner, customer, make_product, provider):
    order_id, _ = place(customer, make_product)
    pay(customer, order_id)
    resp = owner.post(f"/api/commerce/admin/orders/{order_id}/refund")
    assert resp.status_code == 200 and resp.json()["status"] == "refunded"
    assert events("payment.refunded.v1") == 1
    assert customer.post(f"/api/commerce/admin/orders/{order_id}/refund").status_code == 403


def test_payment_failed_orders_eventually_cancel_and_release_stock(customer, make_product, provider):
    order_id, product = place(customer, make_product, qty=3)
    pay(customer, order_id, "tok_declined")
    assert Inventory.objects.get(product=product).available_quantity == 7
    from orders.services import expire_reservations
    assert expire_reservations(now=timezone.now() + timedelta(hours=1)) == 1
    assert Order.objects.get(id=order_id).status == "cancelled"
    assert Inventory.objects.get(product=product).available_quantity == 10


def test_in_flight_payment_holds_off_expiry(customer, make_product, provider):
    from orders.models import InventoryReservation
    from orders.services import expire_reservations

    order_id, _ = place(customer, make_product)
    pay(customer, order_id, "tok_timeout")  # outcome unknown, a moment before the reservation runs out
    InventoryReservation.objects.filter(order_id=order_id).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert expire_reservations(now=timezone.now() + timedelta(minutes=1)) == 0  # reconciliation gets its chance
    assert Order.objects.get(id=order_id).status == "pending_payment"
    # Still unsettled long after: stop holding the stock.
    assert expire_reservations(now=timezone.now() + timedelta(minutes=10)) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_pays_charge_once(customer, make_product, provider):
    order_id, _ = place(customer, make_product)
    provider.unavailable = True  # both would hang on the provider: the first must block the second
    results, barrier = [], threading.Barrier(5)

    def go():
        barrier.wait()
        try:
            payments.start(order_id, CUSTOMER_ID, "tok_visa")
            results.append("started")
        except payments.PaymentInProgress:
            results.append("in-progress")
        finally:
            connection.close()

    threads = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("started") == 1 and results.count("in-progress") == 4
    assert PaymentAttempt.objects.filter(order_id=order_id).count() == 1


def test_order_list_queries_do_not_grow_with_orders(customer, make_product, provider, django_assert_max_num_queries):
    for source in ("tok_visa", "tok_declined", "tok_visa", "tok_visa", "tok_declined"):
        order_id, _ = place(customer, make_product)
        pay(customer, order_id, source)
    # count, page, lines, payment attempts (plus a savepoint or two): never one query per order
    with django_assert_max_num_queries(6):
        body = customer.get("/api/commerce/orders").json()
    assert body["total"] == 5 and all(o["payment"]["status"] in ("captured", "failed") for o in body["items"])
