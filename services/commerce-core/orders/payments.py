"""Paying for orders, against payments-sim (README section 9: payment reconciliation).

The rules:

* An attempt is written and committed **before** the provider is called; its
  id is the provider's idempotency key. A crash or timeout leaves a `pending`
  attempt, never a lost or doubled charge.
* A timeout is never treated as a failure. Reconciliation asks the provider
  what really happened.
* Webhooks and reconciliation go through `apply_charge`, which keeps only the
  highest charge version it has seen: duplicates and out-of-order deliveries
  are no-ops.
* A capture that arrives for an order that can no longer take it (expired,
  cancelled, already paid) is flagged and refunded automatically.
"""

import json
import logging
import uuid
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from common.errors import DomainError, InvalidStateTransition, NotFound
from common.service_token import ServiceToken
from outbox.service import emit

from . import state
from .models import Order, PaymentAttempt, PaymentEvent

log = logging.getLogger(__name__)

SOURCES = ("tok_visa", "tok_mastercard", "tok_declined", "tok_insufficient", "tok_timeout")
CHARGE_STATUS = {"succeeded": PaymentAttempt.Status.CAPTURED, "failed": PaymentAttempt.Status.FAILED,
                 "refunded": PaymentAttempt.Status.REFUNDED}


class PaymentInProgress(DomainError):
    slug = "payment-in-progress"
    title = "Payment in progress"


class ProviderUnavailable(Exception):
    """No answer (timeout, connection refused, 5xx). The outcome is unknown."""


class ProviderRefused(Exception):
    def __init__(self, status_code, body):
        super().__init__(f"provider answered {status_code}")
        self.status_code, self.body = status_code, body


_token = None


def service_token():
    global _token
    if _token is None:
        _token = ServiceToken(settings.COMMERCE_SERVICE_CLIENT_ID, settings.COMMERCE_SERVICE_CLIENT_SECRET)
    return _token


class Provider:
    """payments-sim over the private network, as commerce-core's service account."""

    _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(settings.PAYMENTS_URL + path, data=data, method=method, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Authorization": f"Bearer {service_token().get()}"})
        try:
            with self._opener.open(req, timeout=settings.PAYMENT_PROVIDER_TIMEOUT_SECONDS) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            raw = err.read()
            if err.code == 401:
                service_token().forget()
            if err.code >= 500 or err.code == 401:
                raise ProviderUnavailable(f"HTTP {err.code}") from err
            raise ProviderRefused(err.code, raw) from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise ProviderUnavailable(str(err)) from err

    def charge(self, attempt):
        return self.call("POST", "/api/payments/charges", {
            "idempotency_key": str(attempt.id), "order_ref": str(attempt.order_id),
            "amount_minor": attempt.amount_minor, "currency": attempt.currency, "source": attempt.source})

    def lookup(self, attempt):
        return self.call("GET", f"/api/payments/charges/{attempt.id}")

    def refund(self, attempt, amount_minor):
        return self.call("POST", f"/api/payments/charges/{attempt.id}/refunds",
                         {"idempotency_key": f"rf_{attempt.id}", "amount_minor": amount_minor})


provider = Provider()


def payment_summary(order):
    # .all(): uses the list view's prefetch instead of one query per order.
    attempt = max(order.payment_attempts.all(), key=lambda a: a.created_at, default=None)
    if attempt is None:
        return {"status": None, "failure_code": None}
    return {"status": attempt.status, "failure_code": attempt.failure_code or None}


def start(order_id, customer_id, source, as_owner=False):
    """Begin paying. Returns the attempt (pending, captured or failed)."""
    if source not in SOURCES:
        raise DomainError("Unknown payment source.")
    with transaction.atomic():
        qs = Order.objects.select_for_update().filter(id=order_id)
        if not as_owner:
            qs = qs.filter(customer_id=customer_id)
        order = qs.first()
        if order is None:
            raise NotFound("Order not found.")
        if order.status == Order.Status.PAID:
            raise InvalidStateTransition("This order is already paid.")
        if order.status == Order.Status.PAYMENT_FAILED:
            state.transition(order, Order.Status.PENDING_PAYMENT)  # the customer retries
        elif order.status != Order.Status.PENDING_PAYMENT:
            raise InvalidStateTransition(f"This order is {order.status.replace('_', ' ')} and cannot be paid.")
        if order.payment_attempts.filter(status=PaymentAttempt.Status.PENDING).exists():
            raise PaymentInProgress("A payment for this order is already being processed.")
        try:
            attempt = PaymentAttempt.objects.create(order=order, amount_minor=order.total_minor, currency=order.currency,
                                                    source=source)
        except IntegrityError as exc:  # the partial unique index lost a race
            raise PaymentInProgress("A payment for this order is already being processed.") from exc
    # Committed. Whatever happens now, the attempt exists and can be reconciled.
    try:
        charge = provider.charge(attempt)
    except ProviderUnavailable as exc:
        log.warning("payment %s: provider did not answer (%s); left pending for reconciliation", attempt.id, exc)
        return PaymentAttempt.objects.get(id=attempt.id)
    except ProviderRefused as exc:
        log.error("payment %s refused by the provider: %s", attempt.id, exc.body[:200])
        mark_failed(attempt.id, "provider_refused")
        return PaymentAttempt.objects.get(id=attempt.id)
    apply_charge(charge)
    return PaymentAttempt.objects.get(id=attempt.id)


def mark_failed(attempt_id, code):
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().get(id=attempt_id)
        if attempt.status != PaymentAttempt.Status.PENDING:
            return
        attempt.status, attempt.failure_code = PaymentAttempt.Status.FAILED, code
        attempt.save(update_fields=["status", "failure_code", "updated_at"])
        order = Order.objects.select_for_update().get(id=attempt.order_id)
        if order.status == Order.Status.PENDING_PAYMENT:
            state.transition(order, Order.Status.PAYMENT_FAILED)
            emit("payment.failed", "order", order.id, {"order_id": str(order.id), "failure_code": code})


def apply_charge(charge):
    """Apply a provider charge snapshot. Idempotent and order-independent. Returns what happened."""
    try:
        version = int(charge["version"])
        new_status = CHARGE_STATUS[charge["status"]]
        key = str(charge["idempotency_key"])
    except (KeyError, TypeError, ValueError):
        return "malformed"
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().filter(id=key).first() if _is_uuid(key) else None
        if attempt is None:
            return "unknown"
        if version <= attempt.provider_version:
            return "stale"
        order = Order.objects.select_for_update().get(id=attempt.order_id)
        previous = attempt.status
        attempt.status = new_status
        attempt.provider_version = version
        attempt.provider_charge_id = str(charge.get("id", ""))[:64]
        attempt.failure_code = str(charge.get("failure_code") or "")[:40]
        attempt.refunded_minor = int(charge.get("refunded_minor") or 0)

        if new_status == PaymentAttempt.Status.CAPTURED and previous != PaymentAttempt.Status.CAPTURED:
            if order.status == Order.Status.PENDING_PAYMENT:
                state.transition(order, Order.Status.PAID)
                emit("payment.captured", "order", order.id, {
                    "order_id": str(order.id), "attempt_id": str(attempt.id), "amount_minor": attempt.amount_minor,
                    "currency": attempt.currency, "customer_id": str(order.customer_id),
                    "customer_email": order.customer_email})
            else:
                attempt.needs_refund = True  # e.g. the reservation expired while the provider was slow
                log.warning("payment %s captured for order %s in state %s: will be refunded", attempt.id, order.id,
                            order.status)
        elif new_status == PaymentAttempt.Status.FAILED and order.status == Order.Status.PENDING_PAYMENT:
            state.transition(order, Order.Status.PAYMENT_FAILED)
            emit("payment.failed", "order", order.id, {"order_id": str(order.id), "failure_code": attempt.failure_code})
        elif new_status == PaymentAttempt.Status.REFUNDED:
            was_auto_refund = attempt.needs_refund
            attempt.needs_refund = False
            if order.status in (Order.Status.PAID, Order.Status.FULFILLED) and not was_auto_refund:
                state.transition(order, Order.Status.REFUNDED)
                emit("payment.refunded", "order", order.id, {"order_id": str(order.id), "amount_minor": attempt.refunded_minor})
        attempt.save()
        return f"{previous}->{new_status}"


def _is_uuid(value):
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def handle_webhook(event):
    """At-least-once delivery: each event id is applied once; the charge version handles ordering."""
    try:
        event_id = event["event_id"]
        charge = event["charge"]
    except (KeyError, TypeError):
        return "malformed"
    with transaction.atomic():
        try:
            with transaction.atomic():
                PaymentEvent.objects.create(event_id=event_id)
        except (IntegrityError, ValueError):
            return "duplicate"
        return apply_charge(charge)


def refund(order_id):
    """Refund a paid order in full (shop owner). The webhook or the response settles the state."""
    attempt = (PaymentAttempt.objects.filter(order_id=order_id, status=PaymentAttempt.Status.CAPTURED)
               .order_by("-created_at").first())
    order = Order.objects.filter(id=order_id).first()
    if order is None:
        raise NotFound("Order not found.")
    if attempt is None or order.status not in (Order.Status.PAID, Order.Status.FULFILLED):
        raise InvalidStateTransition(f"Only paid orders can be refunded (this one is {order.status.replace('_', ' ')}).")
    try:
        charge = provider.refund(attempt, attempt.amount_minor - attempt.refunded_minor)
    except ProviderUnavailable as exc:
        raise DomainError("The payment provider is not answering; try again shortly.") from exc
    except ProviderRefused as exc:
        raise DomainError(f"The payment provider refused the refund ({exc.status_code}).") from exc
    apply_charge(charge)
    return Order.objects.get(id=order_id)


def reconcile(now=None):
    """Settle attempts whose outcome is unknown, and refund captures nobody can keep. Safe to run anywhere, any time."""
    now = now or timezone.now()
    settled = refunded = 0
    stale = PaymentAttempt.objects.filter(status=PaymentAttempt.Status.PENDING,
                                          created_at__lte=now - timedelta(seconds=settings.PAYMENT_RECONCILE_AFTER_SECONDS))
    for attempt in stale[:200]:
        try:
            apply_charge(provider.lookup(attempt))
            settled += 1
        except ProviderRefused as exc:
            if exc.status_code == 404 and attempt.created_at <= now - timedelta(minutes=2):
                mark_failed(attempt.id, "not_received")  # the provider never got it
                settled += 1
        except ProviderUnavailable:
            break  # try again next round
    for attempt in PaymentAttempt.objects.filter(status=PaymentAttempt.Status.CAPTURED, needs_refund=True)[:100]:
        try:
            apply_charge(provider.refund(attempt, attempt.amount_minor - attempt.refunded_minor))
            refunded += 1
        except (ProviderUnavailable, ProviderRefused) as exc:
            log.warning("automatic refund of %s failed: %s", attempt.id, exc)
    return {"settled": settled, "refunded": refunded}

