from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from catalog.models import Inventory
from orders.models import Order
from orders.services import expire_reservations, purge_idempotency_keys
from orders.state import can_transition
from outbox.models import OutboxEvent
from outbox.relay import relay_batch
from outbox.service import OutboxOutsideTransaction, emit

from .conftest import checkout

pytestmark = pytest.mark.django_db(transaction=True)


class ListSink:
    def __init__(self, fail_after=None):
        self.sent, self.fail_after = [], fail_after

    def publish(self, envelope):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise ConnectionError("broker down")
        self.sent.append(envelope)


def test_emit_refuses_to_run_outside_a_transaction():
    with pytest.raises(OutboxOutsideTransaction):
        emit("product.created", "product", "x", {})


def test_relay_publishes_in_order_and_marks_rows(make_product):
    a, b = make_product(), make_product()
    sink = ListSink()

    assert relay_batch(sink) == 2
    assert [e["data"]["sku"] for e in sink.sent] == [a.sku, b.sku]
    envelope = sink.sent[0]
    assert envelope["type"] == "product.created" and envelope["version"] == 1
    assert envelope["producer"] == "commerce-core" and envelope["occurred_at"].endswith("Z")
    assert not OutboxEvent.objects.filter(published_at__isnull=True).exists()
    assert relay_batch(sink) == 0


def test_broker_outage_keeps_events_until_it_returns(make_product):
    """Scenario C in miniature: nothing committed is lost while the sink is down."""
    for _ in range(3):
        make_product()

    assert relay_batch(ListSink(fail_after=1)) == 1
    pending = OutboxEvent.objects.filter(published_at__isnull=True)
    assert pending.count() == 2
    assert pending.order_by("created_at").first().publish_attempts == 1

    recovered = ListSink()
    assert relay_batch(recovered) == 2


def test_rolled_back_change_leaves_no_event():
    with pytest.raises(RuntimeError), transaction.atomic():
        emit("product.created", "product", "x", {})
        raise RuntimeError("business failure")
    assert not OutboxEvent.objects.exists()


def test_expired_reservations_release_stock(customer, make_product):
    product = make_product(initial_stock=5)
    order_id = checkout(customer, [(product, 3)]).json()["id"]
    assert Inventory.objects.get(product=product).available_quantity == 2

    assert expire_reservations(now=timezone.now()) == 0  # not yet due
    assert expire_reservations(now=timezone.now() + timedelta(hours=1)) == 1

    order = Order.objects.get(id=order_id)
    assert order.status == "expired"
    assert order.reservations.filter(released_at__isnull=True).count() == 0
    assert Inventory.objects.get(product=product).available_quantity == 5
    assert OutboxEvent.objects.filter(event_type="reservation.expired.v1").count() == 1
    assert expire_reservations(now=timezone.now() + timedelta(hours=1)) == 0  # idempotent


def test_paid_orders_are_never_expired(customer, make_product):
    product = make_product(initial_stock=5)
    order_id = checkout(customer, [(product, 1)]).json()["id"]
    Order.objects.filter(id=order_id).update(status="paid")

    assert expire_reservations(now=timezone.now() + timedelta(hours=1)) == 0
    assert Inventory.objects.get(product=product).available_quantity == 4


def test_old_idempotency_keys_are_purged(customer, make_product):
    checkout(customer, [(make_product(), 1)])
    assert purge_idempotency_keys() == 0
    assert purge_idempotency_keys(now=timezone.now() + timedelta(days=2)) == 1


@pytest.mark.parametrize("current,target,allowed", [
    ("pending_payment", "paid", True),
    ("pending_payment", "fulfilled", False),
    ("paid", "pending_payment", False),
    ("payment_failed", "pending_payment", True),
    ("expired", "cancelled", True),
    ("refunded", "paid", False),
])
def test_order_state_machine(current, target, allowed):
    assert can_transition(current, target) is allowed
