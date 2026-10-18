from django.db import connection

from .models import OutboxEvent


class OutboxOutsideTransaction(RuntimeError):
    pass


def emit(event_type, aggregate_type, aggregate_id, data, *, version=1, trace_ctx=None):
    """Record an event inside the caller's transaction.

    Refuses to run outside a transaction: an event written on its own could
    describe a change that later rolls back, which is the exact failure window
    the outbox exists to close.
    """
    if not connection.in_atomic_block:
        raise OutboxOutsideTransaction(f"{event_type} must be emitted inside transaction.atomic()")
    return OutboxEvent.objects.create(
        event_type=f"{event_type}.v{version}",
        schema_ver=version,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload=data,
        trace_ctx=trace_ctx,
    )


def trace_ctx_from(request):
    traceparent = request.META.get("HTTP_TRACEPARENT") if request is not None else None
    return {"traceparent": traceparent} if traceparent else None
