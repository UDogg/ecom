import uuid

from django.db import models
from django.db.models import Q


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "category"
        ordering = ["name"]

    def __str__(self):
        return self.slug


class Product(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    price_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    # Remembered so Restore returns an archived product to where it was.
    status_before_archive = models.CharField(max_length=16, choices=Status.choices, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(price_minor__gte=0), name="product_price_non_negative"),
            models.CheckConstraint(condition=Q(status__in=["draft", "active", "archived"]), name="product_status_valid"),
            models.CheckConstraint(condition=~Q(sku=""), name="product_sku_not_empty"),
            models.CheckConstraint(condition=~Q(name=""), name="product_name_not_empty"),
        ]
        indexes = [models.Index(fields=["status", "category"], name="product_status_category")]

    def __str__(self):
        return self.sku


class Inventory(models.Model):
    product = models.OneToOneField(Product, primary_key=True, on_delete=models.CASCADE, related_name="inventory")
    available_quantity = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory"
        constraints = [
            # The core no-overselling invariant, enforced by the database.
            models.CheckConstraint(condition=Q(available_quantity__gte=0), name="inventory_available_non_negative"),
        ]


class InventoryAdjustment(models.Model):
    """Audit trail: every stock change outside checkout has a reason and actor."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="adjustments")
    delta = models.IntegerField()
    resulting_quantity = models.IntegerField()
    reason = models.CharField(max_length=200)
    actor_id = models.UUIDField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_adjustment"
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=~Q(delta=0), name="adjustment_delta_non_zero")]


class ProductImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    object_key = models.CharField(max_length=300)
    position = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "product_image"
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["product", "position"], name="product_image_position_unique"),
        ]
