import uuid

from django.db import models
from django.db.models import Q


class OutboxEvent(models.Model):
    """An event written in the same transaction as the change it describes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=100)
    schema_ver = models.PositiveSmallIntegerField()
    aggregate_type = models.CharField(max_length=50)
    aggregate_id = models.CharField(max_length=64)
    payload = models.JSONField()
    trace_ctx = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    publish_attempts = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "outbox_event"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["created_at"], name="outbox_unpublished", condition=Q(published_at__isnull=True)),
        ]

    def envelope(self):
        """The wire format documented in README section 11."""
        name, _, _ = self.event_type.rpartition(".v")
        return {
            "id": str(self.id),
            "type": name or self.event_type,
            "version": self.schema_ver,
            "occurred_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "producer": "commerce-core",
            "trace": self.trace_ctx or {},
            "data": self.payload,
        }
