import uuid

from django.db import models
from django.db.models import Q

from catalog.models import Product


ORDER_STATUSES = ["pending_payment", "paid", "payment_failed", "expired", "cancelled", "fulfilled", "refunded"]


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment"
        PAID = "paid"
        PAYMENT_FAILED = "payment_failed"
        EXPIRED = "expired"
        CANCELLED = "cancelled"
        FULFILLED = "fulfilled"
        REFUNDED = "refunded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_id = models.UUIDField(db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT)
    total_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(total_minor__gte=0), name="order_total_non_negative"),
            models.CheckConstraint(condition=Q(status__in=ORDER_STATUSES), name="order_status_valid"),
        ]


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    # PROTECT: an ordered product can never be hard-deleted (README section 9).
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    # Snapshots: history never reads live product data.
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    unit_price_minor = models.BigIntegerField()

    class Meta:
        db_table = "order_item"
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="order_item_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price_minor__gte=0), name="order_item_price_non_negative"),
            models.UniqueConstraint(fields=["order", "product"], name="order_item_product_unique"),
        ]


class InventoryReservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="reservations")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_reservation"
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="reservation_quantity_positive")]
        indexes = [
            models.Index(fields=["expires_at"], name="reservation_open", condition=Q(released_at__isnull=True)),
        ]


class CheckoutIdempotency(models.Model):
    """Maps (customer, Idempotency-Key) to exactly one order."""

    id = models.BigAutoField(primary_key=True)
    customer_id = models.UUIDField()
    key = models.CharField(max_length=200)
    request_hash = models.BinaryField()
    order = models.OneToOneField(Order, null=True, on_delete=models.CASCADE, related_name="idempotency")
    response_status = models.PositiveSmallIntegerField(null=True)
    response_body = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "checkout_idempotency"
        constraints = [models.UniqueConstraint(fields=["customer_id", "key"], name="checkout_idempotency_key")]


class Cart(models.Model):
    customer_id = models.UUIDField(primary_key=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cart"


class CartItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="+")
    quantity = models.PositiveIntegerField()

    class Meta:
        db_table = "cart_item"
        constraints = [
            models.UniqueConstraint(fields=["cart", "product"], name="cart_item_product_unique"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="cart_item_quantity_positive"),
        ]
