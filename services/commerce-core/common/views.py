from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Liveness: the process is up. Never touches dependencies."""
    return JsonResponse({"status": "ok", "service": "commerce-core"})


def readyz(request):
    """Readiness: critical dependencies (PostgreSQL) are reachable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - any failure means not ready
        return JsonResponse({"status": "unavailable", "postgres": str(exc.__class__.__name__)}, status=503)
    return JsonResponse({"status": "ready", "postgres": "ok"})
