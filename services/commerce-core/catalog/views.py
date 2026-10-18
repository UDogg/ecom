from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import Q
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from common.errors import NotFound
from common.http import etag, paginate, parse_if_match
from common.permissions import IsShopOwner
from outbox.service import trace_ctx_from

from . import services
from .models import Category, InventoryAdjustment, Product
from .serializers import (
    InventoryAdjustSerializer,
    ProductCreateSerializer,
    ProductUpdateSerializer,
    product_out,
)


def products_qs():
    return Product.objects.select_related("category", "inventory").prefetch_related("images")


def _get_product(product_id, qs=None):
    product = (qs if qs is not None else products_qs()).filter(id=product_id).first()
    if product is None:
        raise NotFound("Product not found.")
    return product


def _with_etag(response, product):
    response["ETag"] = etag(product.version)
    return response


def apply_text_search(qs, q):
    vector = SearchVector("name", weight="A", config="english") + SearchVector("description", weight="B",
                                                                                config="english")
    query = SearchQuery(q, search_type="websearch", config="english")
    return (qs.annotate(rank=SearchRank(vector, query))
            .filter(Q(rank__gt=0) | Q(name__icontains=q) | Q(sku__iexact=q))
            .order_by("-rank", "name"))


# ---------------------------------------------------------------------------
# Admin (shop_owner)
# ---------------------------------------------------------------------------

class AdminProductList(APIView):
    permission_classes = [IsShopOwner]

    def get(self, request):
        qs = products_qs()
        if status := request.query_params.get("status"):
            qs = qs.filter(status=status)
        if category := request.query_params.get("category"):
            qs = qs.filter(category__slug=category)
        if q := request.query_params.get("q", "").strip():
            qs = apply_text_search(qs, q)
        items, page = paginate(request, qs)
        return Response({"items": [product_out(p, admin=True) for p in items], **page})

    def post(self, request):
        data = ProductCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        product = services.create_product(data.validated_data, actor_id=request.user.id,
                                          trace_ctx=trace_ctx_from(request))
        return _with_etag(Response(product_out(_get_product(product.id), admin=True), status=201), product)


class AdminProductDetail(APIView):
    permission_classes = [IsShopOwner]

    def get(self, request, product_id):
        product = _get_product(product_id)
        return _with_etag(Response(product_out(product, admin=True)), product)

    def patch(self, request, product_id):
        expected = parse_if_match(request)
        data = ProductUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.update_product(product_id, expected, data.validated_data, actor_id=request.user.id,
                                trace_ctx=trace_ctx_from(request))
        product = _get_product(product_id)
        return _with_etag(Response(product_out(product, admin=True)), product)

    def delete(self, request, product_id):
        permanent = request.query_params.get("permanent", "").lower() in {"1", "true", "yes"}
        if permanent:
            services.delete_product_permanently(product_id, actor_id=request.user.id,
                                                trace_ctx=trace_ctx_from(request))
            return Response(status=204)
        services.archive_product(product_id, actor_id=request.user.id, trace_ctx=trace_ctx_from(request))
        product = _get_product(product_id)
        return _with_etag(Response(product_out(product, admin=True)), product)


class AdminProductRestore(APIView):
    permission_classes = [IsShopOwner]

    def post(self, request, product_id):
        services.restore_product(product_id, actor_id=request.user.id, trace_ctx=trace_ctx_from(request))
        product = _get_product(product_id)
        return _with_etag(Response(product_out(product, admin=True)), product)


def adjustment_out(a):
    return {
        "id": str(a.id),
        "product_id": str(a.product_id),
        "delta": a.delta,
        "resulting_quantity": a.resulting_quantity,
        "reason": a.reason,
        "actor_id": str(a.actor_id) if a.actor_id else None,
        "created_at": a.created_at.isoformat().replace("+00:00", "Z"),
    }


class AdminInventoryAdjust(APIView):
    permission_classes = [IsShopOwner]

    def post(self, request, product_id):
        data = InventoryAdjustSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        adjustment = services.adjust_inventory(product_id, data.validated_data["delta"],
                                               data.validated_data["reason"], actor_id=request.user.id,
                                               trace_ctx=trace_ctx_from(request))
        return Response(adjustment_out(adjustment), status=201)


class AdminInventoryHistory(APIView):
    permission_classes = [IsShopOwner]

    def get(self, request, product_id):
        _get_product(product_id, Product.objects.all())
        items, page = paginate(request, InventoryAdjustment.objects.filter(product_id=product_id))
        return Response({"items": [adjustment_out(a) for a in items], **page})


# ---------------------------------------------------------------------------
# Public catalog (anonymous). In the polyglot system catalog-reader serves
# these routes; commerce-core keeps the same contract as the baseline path.
# ---------------------------------------------------------------------------

class PublicProductList(APIView):
    authentication_classes = []

    def get(self, request):
        qs = products_qs().filter(status=Product.Status.ACTIVE)
        params = request.query_params
        if category := params.get("category"):
            qs = qs.filter(category__slug=category)
        try:
            if (low := params.get("min_price")) is not None:
                qs = qs.filter(price_minor__gte=int(low))
            if (high := params.get("max_price")) is not None:
                qs = qs.filter(price_minor__lte=int(high))
        except ValueError:
            raise serializers.ValidationError({"price": "min_price and max_price are integers in minor units."}) \
                from None
        if q := params.get("q", "").strip():
            qs = apply_text_search(qs, q)
        items, page = paginate(request, qs)
        return Response({"items": [product_out(p) for p in items], **page})


class PublicProductDetail(APIView):
    authentication_classes = []

    def get(self, request, product_id):
        return Response(product_out(_get_product(product_id, products_qs().filter(status=Product.Status.ACTIVE))))


class PublicCategoryList(APIView):
    authentication_classes = []

    def get(self, request):
        categories = Category.objects.all()
        children = {}
        for c in categories:
            children.setdefault(c.parent_id, []).append(c)

        def node(c):
            return {"slug": c.slug, "name": c.name, "children": [node(k) for k in children.get(c.id, [])]}

        return Response({"items": [node(c) for c in children.get(None, [])]})
