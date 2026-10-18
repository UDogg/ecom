import re

from django.conf import settings
from rest_framework import serializers

from .errors import PreconditionRequired

_ETAG = re.compile(r'^(?:W/)?"?(\d+)"?$')


def etag(version):
    return f'"{version}"'


def parse_if_match(request):
    raw = request.headers.get("If-Match", "").strip()
    if not raw:
        raise PreconditionRequired("Send If-Match with the product version you are editing.")
    match = _ETAG.match(raw)
    if not match:
        raise serializers.ValidationError({"If-Match": "Expected a product version such as \"3\"."})
    return int(match.group(1))


def paginate(request, queryset, max_page_size=100):
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
        size = min(max(int(request.query_params.get("page_size", settings.CATALOG_PAGE_SIZE)), 1), max_page_size)
    except ValueError:
        raise serializers.ValidationError({"page": "page and page_size must be integers."}) from None
    total = queryset.count()
    items = list(queryset[(page - 1) * size: page * size])
    return items, {"page": page, "page_size": size, "total": total, "pages": (total + size - 1) // size}
