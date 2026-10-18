"""Checkout, reservations and expiry.

Checkout is one transaction: idempotency record, stock reservations, order,
order items and outbox events either all commit or none do.
"""

import hashlib
import json
from collections import OrderedDict
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework import status

from catalog.models import Inventory, Product
from common.errors import DomainError, IdempotencyKeyReused, InsufficientStock, ProductUnavailable
from outbox.service import emit

from . import state
from .models import CartItem, CheckoutIdempotency, InventoryReservation, Order, OrderItem


class MixedCurrency(DomainError):
    status = status.HTTP_422_UNPROCESSABLE_ENTITY
    slug = "mixed-currency"
    title = "Mixed currencies"


def normalize_items(items):
    """Merge duplicate lines and sort, so equivalent requests hash equally."""
    merged = OrderedDict()
    for item in items:
        pid = str(item["product_id"])
        merged[pid] = merged.get(pid, 0) + int(item["quantity"])
    return sorted(merged.items())


def request_hash(normalized):
    return hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode()).digest()


def order_body(order):
    from .payments import payment_summary

    return {
        "payment": payment_summary(order),
        "id": str(order.id),
        "status": order.status,
        "total_minor": order.total_minor,
        "currency": order.currency,
        "created_at": order.created_at.isoformat().replace("+00:00", "Z"),
        "items": [
            {
                "product_id": str(item.product_id),
                "sku": item.sku,
                "name": item.name,
                "quantity": item.quantity,
                "unit_price_minor": item.unit_price_minor,
            }
            for item in order.items.order_by("sku")
        ],
    }


def _lock_products_for_share(product_ids):
    """FOR SHARE: concurrent checkouts proceed together, while archive and
    delete (FOR UPDATE) wait for them and vice versa."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM product WHERE id = ANY(%s::uuid[]) ORDER BY id FOR SHARE", [product_ids])
    return {str(p.id): p for p in Product.objects.filter(id__in=product_ids)}


def _reserve(product_id, quantity):
    """Conditional atomic decrement: zero rows means not enough stock."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE inventory SET available_quantity = available_quantity - %s, updated_at = now() "
            "WHERE product_id = %s AND available_quantity >= %s RETURNING available_quantity",
            [quantity, product_id, quantity],
        )
        row = cursor.fetchone()
    return None if row is None else row[0]


def release_stock(product_id, quantity):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE inventory SET available_quantity = available_quantity + %s, updated_at = now() "
            "WHERE product_id = %s RETURNING available_quantity",
            [quantity, product_id],
        )
        return cursor.fetchone()[0]


def checkout(customer_id, idempotency_key, items, trace_ctx=None, customer_email=None):
    """Place an order. Returns (http_status, body, replayed)."""
    normalized = normalize_items(items)
    digest = request_hash(normalized)

    with transaction.atomic():
        try:
            with transaction.atomic():
                record = CheckoutIdempotency.objects.create(
                    customer_id=customer_id, key=idempotency_key, request_hash=digest
                )
        except IntegrityError:
            # A concurrent duplicate blocks on the unique index until the first
            # request commits, so the original result is visible by now.
            existing = CheckoutIdempotency.objects.get(customer_id=customer_id, key=idempotency_key)
            if bytes(existing.request_hash) != digest:
                raise IdempotencyKeyReused("This Idempotency-Key was already used for a different order.") from None
            return existing.response_status, existing.response_body, True

        product_ids = [pid for pid, _ in normalized]
        products = _lock_products_for_share(product_ids)
        for pid in product_ids:
            product = products.get(pid)
            if product is None or product.status != Product.Status.ACTIVE:
                raise ProductUnavailable(f"Product {pid} is not available for purchase.", product_id=pid)
        currencies = {p.currency for p in products.values()}
        if len(currencies) > 1:
            raise MixedCurrency("All items in one order must share a currency.")

        remaining = {}
        for pid, quantity in normalized:  # sorted by id: consistent lock order, no deadlocks
            left = _reserve(pid, quantity)
            if left is None:
                available = Inventory.objects.filter(product_id=pid).values_list("available_quantity", flat=True).first()
                raise InsufficientStock(
                    f"Requested {quantity}, available {available or 0}.",
                    product_id=pid, requested=quantity, available=available or 0,
                )
            remaining[pid] = left

        order = Order.objects.create(
            customer_id=customer_id,
            customer_email=customer_email,
            total_minor=sum(products[pid].price_minor * qty for pid, qty in normalized),
            currency=currencies.pop(),
        )
        OrderItem.objects.bulk_create(
            OrderItem(order=order, product_id=pid, sku=products[pid].sku, name=products[pid].name,
                      quantity=qty, unit_price_minor=products[pid].price_minor)
            for pid, qty in normalized
        )
        expires_at = timezone.now() + timedelta(seconds=settings.RESERVATION_TTL_SECONDS)
        InventoryReservation.objects.bulk_create(
            InventoryReservation(order=order, product_id=pid, quantity=qty, expires_at=expires_at)
            for pid, qty in normalized
        )

        body = order_body(order)
        emit("order.placed", "order", order.id,
             {**body, "customer_id": str(customer_id), "customer_email": customer_email}, trace_ctx=trace_ctx)
        for pid, qty in normalized:
            emit("inventory.changed", "product", pid,
                 {"product_id": pid, "available_quantity": remaining[pid], "delta": -qty, "reason": "checkout"},
                 trace_ctx=trace_ctx)

        record.order = order
        record.response_status = status.HTTP_201_CREATED
        record.response_body = body
        record.save(update_fields=["order", "response_status", "response_body"])

        CartItem.objects.filter(cart_id=customer_id, product_id__in=product_ids).delete()

    return status.HTTP_201_CREATED, body, False


def _release_reservations(order, reason):
    now = timezone.now()
    for reservation in order.reservations.filter(released_at__isnull=True).select_for_update():
        left = release_stock(reservation.product_id, reservation.quantity)
        reservation.released_at = now
        reservation.save(update_fields=["released_at"])
        emit("inventory.changed", "product", reservation.product_id,
             {"product_id": str(reservation.product_id), "available_quantity": left,
              "delta": reservation.quantity, "reason": reason})


def expire_reservations(now=None, batch_size=100):
    """Expire unpaid orders whose reservations timed out and return their stock.

    SKIP LOCKED lets several runners work in parallel, and the order row lock
    serializes expiry against a concurrent payment capture.
    """
    now = now or timezone.now()
    expired = 0
    with transaction.atomic():
        holding = [Order.Status.PENDING_PAYMENT, Order.Status.PAYMENT_FAILED]
        due_ids = list(
            Order.objects.filter(status__in=holding,
                                 reservations__released_at__isnull=True,
                                 reservations__expires_at__lte=now)
            .values_list("id", flat=True).distinct()[:batch_size]
        )
        # The status filter is re-checked under the lock, so an order paid in
        # the meantime is left alone. An order whose payment is still in flight
        # (outcome unknown) gets a grace period for reconciliation first.
        locked = (Order.objects.select_for_update(skip_locked=True).filter(id__in=due_ids, status__in=holding)
                  .exclude(payment_attempts__status="pending",
                           payment_attempts__created_at__gt=now - timedelta(minutes=5)))
        for order in list(locked):
            state.transition(order, Order.Status.EXPIRED if order.status == Order.Status.PENDING_PAYMENT
                             else Order.Status.CANCELLED)
            _release_reservations(order, reason="reservation expired")
            emit("reservation.expired", "order", order.id, {"order_id": str(order.id)})
            expired += 1
    return expired


def purge_idempotency_keys(now=None):
    cutoff = (now or timezone.now()) - timedelta(hours=settings.IDEMPOTENCY_RETENTION_HOURS)
    deleted, _ = CheckoutIdempotency.objects.filter(created_at__lt=cutoff).delete()
    return deleted
