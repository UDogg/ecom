"""The order state machine from README section 8.2."""

from common.errors import InvalidStateTransition

from .models import Order

S = Order.Status

TRANSITIONS = {
    S.PENDING_PAYMENT: {S.PAID, S.PAYMENT_FAILED, S.EXPIRED},
    S.PAYMENT_FAILED: {S.PENDING_PAYMENT, S.CANCELLED},
    S.EXPIRED: {S.CANCELLED},
    S.PAID: {S.FULFILLED, S.REFUNDED},
    S.FULFILLED: {S.REFUNDED},
    S.CANCELLED: set(),
    S.REFUNDED: set(),
}

# States in which the order still holds reserved stock.
HOLDS_STOCK = {S.PENDING_PAYMENT, S.PAYMENT_FAILED}


def can_transition(current, target):
    return target in TRANSITIONS.get(current, set())


def transition(order, target):
    """Move a row-locked order to `target`, or refuse."""
    if not can_transition(order.status, target):
        raise InvalidStateTransition(f"Order cannot move from {order.status} to {target}.")
    order.status = target
    order.save(update_fields=["status", "updated_at"])
    return order
