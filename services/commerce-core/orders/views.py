from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Inventory, Product
from common.errors import NotFound, problem
from common.http import paginate
from common.permissions import IsCustomer, IsShopOwner
from outbox.models import OutboxEvent
from outbox.service import trace_ctx_from

from . import services
from .models import Cart, CartItem, Order

MAX_LINES = 50
MAX_QUANTITY = 1000


class LineSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY)


class LinesSerializer(serializers.Serializer):
    items = LineSerializer(many=True, allow_empty=True, max_length=MAX_LINES)


def cart_out(customer_id):
    lines = (CartItem.objects.filter(cart_id=customer_id)
             .select_related("product", "product__inventory").order_by("product__name"))
    items = []
    for line in lines:
        p = line.product
        available = getattr(getattr(p, "inventory", None), "available_quantity", 0)
        items.append({
            "product_id": str(p.id),
            "sku": p.sku,
            "name": p.name,
            "quantity": line.quantity,
            "unit_price_minor": p.price_minor,
            "currency": p.currency,
            # Archived or drafted products stay visible but can't be bought.
            "available": p.status == Product.Status.ACTIVE and available >= line.quantity,
        })
    return {"items": items, "subtotal_minor": sum(i["unit_price_minor"] * i["quantity"] for i in items if i["available"])}


class CartView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        return Response(cart_out(request.user.id))

    def put(self, request):
        data = LinesSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        lines = services.normalize_items(data.validated_data["items"])
        known = set(map(str, Product.objects.filter(id__in=[pid for pid, _ in lines])
                        .exclude(status=Product.Status.ARCHIVED).values_list("id", flat=True)))
        unknown = [pid for pid, _ in lines if pid not in known]
        if unknown:
            raise serializers.ValidationError({"items": f"Unknown or archived products: {', '.join(unknown)}"})
        with transaction.atomic():
            cart, _ = Cart.objects.get_or_create(customer_id=request.user.id)
            cart.items.all().delete()
            CartItem.objects.bulk_create(CartItem(cart=cart, product_id=pid, quantity=q) for pid, q in lines)
            cart.save(update_fields=["updated_at"])
        return Response(cart_out(request.user.id))


class CheckoutView(APIView):
    permission_classes = [IsCustomer]

    def post(self, request):
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 200 or not key.isprintable():
            return problem(400, "idempotency-key-required", "Idempotency-Key required",
                           "Send a unique Idempotency-Key header (1 to 200 printable characters) per checkout.",
                           request)
        data = LinesSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        if not data.validated_data["items"]:
            raise serializers.ValidationError({"items": "Checkout needs at least one item."})
        status, body, replayed = services.checkout(request.user.id, key, data.validated_data["items"],
                                                   trace_ctx=trace_ctx_from(request))
        response = Response(body, status=status)
        response["Idempotent-Replayed"] = "true" if replayed else "false"
        return response


def _orders_qs():
    return Order.objects.prefetch_related("items")


class OrderList(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        items, page = paginate(request, _orders_qs().filter(customer_id=request.user.id))
        return Response({"items": [services.order_body(o) for o in items], **page})


class OrderDetail(APIView):
    permission_classes = [IsCustomer]

    def get(self, request, order_id):
        # Object-level authorization: someone else's order is indistinguishable
        # from a missing one. Shop owners can read any order.
        qs = _orders_qs().filter(id=order_id)
        if not request.user.is_shop_owner:
            qs = qs.filter(customer_id=request.user.id)
        order = qs.first()
        if order is None:
            raise NotFound("Order not found.")
        return Response(services.order_body(order))


class AdminOrderList(APIView):
    permission_classes = [IsShopOwner]

    def get(self, request):
        qs = _orders_qs()
        if status := request.query_params.get("status"):
            qs = qs.filter(status=status)
        items, page = paginate(request, qs)
        return Response({"items": [{**services.order_body(o), "customer_id": str(o.customer_id)} for o in items],
                         **page})


class AdminDashboard(APIView):
    permission_classes = [IsShopOwner]

    def get(self, request):
        recent = list(_orders_qs()[:10])
        low = (Inventory.objects.select_related("product")
               .filter(product__status=Product.Status.ACTIVE,
                       available_quantity__lte=settings.LOW_STOCK_THRESHOLD)
               .order_by("available_quantity")[:20])
        oldest = OutboxEvent.objects.filter(published_at__isnull=True).order_by("created_at").first()
        return Response({
            "recent_orders": [services.order_body(o) for o in recent],
            "low_stock": [{"product_id": str(i.product_id), "sku": i.product.sku, "name": i.product.name,
                           "available_quantity": i.available_quantity} for i in low],
            "system": {
                "outbox_pending": OutboxEvent.objects.filter(published_at__isnull=True).count(),
                "outbox_oldest_pending_at": oldest.created_at.isoformat().replace("+00:00", "Z") if oldest else None,
            },
        })
