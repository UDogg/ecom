"""Moves committed outbox rows to a sink (a broker, in Phase 3).

Delivery is at-least-once: a row is marked published only after the sink
accepts it, so a crash in between re-sends it. Consumers dedupe by event id.
"""

from django.db import transaction
from django.utils import timezone

from .models import OutboxEvent


class LogSink:
    def __init__(self, write):
        self.write = write

    def publish(self, envelope):
        self.write(envelope)


def relay_batch(sink, batch_size=100):
    with transaction.atomic():
        # SKIP LOCKED lets several publishers run without double-sending a batch.
        events = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(published_at__isnull=True)
            .order_by("created_at")[:batch_size]
        )
        published = []
        for event in events:
            try:
                sink.publish(event.envelope())
            except Exception:  # noqa: BLE001 - stop at the first failure, keep ordering
                OutboxEvent.objects.filter(id=event.id).update(publish_attempts=event.publish_attempts + 1)
                break
            published.append(event.id)
        if published:
            OutboxEvent.objects.filter(id__in=published).update(published_at=timezone.now())
    return len(published)
