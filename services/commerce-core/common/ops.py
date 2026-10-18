"""Operational endpoints: SYSTEM tokens only (a service account's super credentials).

They let an operator see and nudge the system when the BFF is not the way in,
without any customer or shop-owner power.
"""

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import InventoryReservation, Order
from orders.services import expire_reservations
from outbox.models import OutboxEvent

from .permissions import IsSystem


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z") if dt else None


class OpsStatus(APIView):
    permission_classes = [IsSystem]

    def get(self, request):
        pending = OutboxEvent.objects.filter(published_at__isnull=True)
        oldest = pending.order_by("created_at").first()
        now = timezone.now()
        open_reservations = InventoryReservation.objects.filter(released_at__isnull=True)
        return Response({
            "outbox": {"pending": pending.count(), "oldest_pending_at": _iso(oldest.created_at) if oldest else None},
            "orders": {"pending_payment": Order.objects.filter(status=Order.Status.PENDING_PAYMENT).count()},
            "reservations": {"open": open_reservations.count(),
                             "overdue": open_reservations.filter(expires_at__lte=now).count()},
            "checked_at": _iso(now),
        })


class OpsExpireReservations(APIView):
    """Run the expirer now (normally commerce-expirer does it every 30 s). Idempotent."""

    permission_classes = [IsSystem]

    def post(self, request):
        return Response({"expired_orders": expire_reservations()})
