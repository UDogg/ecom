"""Problem-details errors (RFC 9457 shape) shared by every endpoint."""

import re
import uuid

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

ERROR_TYPE_BASE = "https://shop.local/errors/"
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


class DomainError(Exception):
    """A business rule refusal. Subclasses set the HTTP mapping."""

    status = status.HTTP_409_CONFLICT
    slug = "conflict"
    title = "Conflict"

    def __init__(self, detail=None, **extra):
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra


class NotFound(DomainError):
    status = status.HTTP_404_NOT_FOUND
    slug = "not-found"
    title = "Not found"


class InsufficientStock(DomainError):
    slug = "insufficient-stock"
    title = "Insufficient stock"


class VersionConflict(DomainError):
    slug = "version-conflict"
    title = "Version conflict"


class PreconditionRequired(DomainError):
    status = status.HTTP_428_PRECONDITION_REQUIRED
    slug = "precondition-required"
    title = "Precondition required"


class ProductInUse(DomainError):
    slug = "product-in-use"
    title = "Product has been ordered"


class InvalidStateTransition(DomainError):
    slug = "invalid-state-transition"
    title = "Invalid state transition"


class ProductUnavailable(DomainError):
    slug = "product-unavailable"
    title = "Product unavailable"


class IdempotencyKeyReused(DomainError):
    status = status.HTTP_422_UNPROCESSABLE_ENTITY
    slug = "idempotency-key-reused"
    title = "Idempotency key reused with a different request"


def trace_id_for(request):
    if request is not None:
        match = _TRACEPARENT.match(request.META.get("HTTP_TRACEPARENT", ""))
        if match:
            return match.group(1)
        cached = getattr(request, "_trace_id", None)
        if cached:
            return cached
    tid = uuid.uuid4().hex
    if request is not None:
        request._trace_id = tid
    return tid


def problem(status_code, slug, title, detail, request=None, **extra):
    body = {
        "type": ERROR_TYPE_BASE + slug,
        "title": title,
        "status": status_code,
        "detail": detail,
        "trace_id": trace_id_for(request),
        **extra,
    }
    return Response(body, status=status_code, content_type="application/problem+json")


def problem_exception_handler(exc, context):
    request = context.get("request")
    if isinstance(exc, DomainError):
        return problem(exc.status, exc.slug, exc.title, exc.detail, request, **exc.extra)

    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, exceptions.ValidationError):
        resp = problem(400, "validation", "Invalid request", "One or more fields are invalid.", request,
                       errors=response.data)
    else:
        detail = response.data.get("detail", str(exc)) if isinstance(response.data, dict) else str(exc)
        slug = getattr(exc, "default_code", "error").replace("_", "-")
        title = slug.replace("-", " ").capitalize()
        resp = problem(response.status_code, slug, title, str(detail), request)
    for header in ("WWW-Authenticate", "Retry-After", "Allow"):
        if header in response:
            resp[header] = response[header]
    return resp
