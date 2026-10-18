"""Product lifecycle and stock adjustments.

Every mutation runs in one transaction together with its outbox event, so
the event exists if and only if the change committed.
"""

from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from common.errors import (
    DomainError,
    InsufficientStock,
    InvalidStateTransition,
    NotFound,
    ProductInUse,
    VersionConflict,
)
from outbox.service import emit

from .models import Category, Inventory, InventoryAdjustment, Product, ProductImage


class SkuTaken(DomainError):
    slug = "sku-taken"
    title = "SKU already exists"


def event_data(product, **extra):
    return {
        "product_id": str(product.id),
        "sku": product.sku,
        "name": product.name,
        "price_minor": product.price_minor,
        "currency": product.currency,
        "category": product.category.slug,
        "status": product.status,
        "version": product.version,
        **extra,
    }


def resolve_category(slug, name=None):
    slug = slugify(slug)
    category, _ = Category.objects.get_or_create(slug=slug, defaults={"name": name or slug.replace("-", " ").title()})
    return category


def _replace_images(product, image_keys):
    product.images.all().delete()
    ProductImage.objects.bulk_create(
        ProductImage(product=product, object_key=key, position=i) for i, key in enumerate(image_keys)
    )


def create_product(data, actor_id=None, trace_ctx=None):
    try:
        with transaction.atomic():
            category = resolve_category(data["category"], data.get("category_name"))
            product = Product.objects.create(
                sku=data["sku"],
                name=data["name"],
                description=data.get("description", ""),
                price_minor=data["price_minor"],
                currency=data["currency"],
                category=category,
                status=data.get("status", Product.Status.DRAFT),
            )
            stock = data.get("initial_stock", 0)
            Inventory.objects.create(product=product, available_quantity=stock)
            if stock:
                InventoryAdjustment.objects.create(
                    product=product, delta=stock, resulting_quantity=stock, reason="initial stock", actor_id=actor_id
                )
            _replace_images(product, data.get("image_keys", []))
            emit("product.created", "product", product.id, event_data(product, available_quantity=stock),
                 trace_ctx=trace_ctx)
    except IntegrityError as exc:
        if Product.objects.filter(sku=data["sku"]).exists():
            raise SkuTaken(f"SKU {data['sku']!r} is already in use.") from exc
        raise
    return product


def update_product(product_id, expected_version, changes, actor_id=None, trace_ctx=None):
    """Optimistic concurrency: the write only lands if the version still matches."""
    changes = dict(changes)
    image_keys = changes.pop("image_keys", None)
    category_slug = changes.pop("category", None)
    category_name = changes.pop("category_name", None)

    with transaction.atomic():
        current = Product.objects.filter(id=product_id).values("status", "version").first()
        if current is None:
            raise NotFound("Product not found.")
        if "status" in changes and current["status"] == Product.Status.ARCHIVED:
            raise InvalidStateTransition("Archived products are brought back with Restore, not by editing status.")
        if category_slug:
            changes["category"] = resolve_category(category_slug, category_name)

        updated = Product.objects.filter(id=product_id, version=expected_version).update(
            **changes, version=F("version") + 1, updated_at=timezone.now()
        )
        if not updated:
            raise VersionConflict(
                f"Product was modified (current version {current['version']}, you sent {expected_version}).",
                current_version=current["version"],
            )
        product = Product.objects.select_related("category").get(id=product_id)
        if image_keys is not None:
            _replace_images(product, image_keys)
        emit("product.updated", "product", product.id, event_data(product, changed=sorted(
            [*changes, *(["image_keys"] if image_keys is not None else [])])), trace_ctx=trace_ctx)
    return product


def _locked(product_id):
    try:
        return Product.objects.select_for_update().select_related("category").get(id=product_id)
    except Product.DoesNotExist:
        raise NotFound("Product not found.") from None


def archive_product(product_id, actor_id=None, trace_ctx=None):
    with transaction.atomic():
        product = _locked(product_id)
        if product.status == Product.Status.ARCHIVED:
            return product  # idempotent: archiving twice is not a new event
        product.status_before_archive = product.status
        product.status = Product.Status.ARCHIVED
        product.version += 1
        product.save(update_fields=["status", "status_before_archive", "version", "updated_at"])
        emit("product.archived", "product", product.id, event_data(product), trace_ctx=trace_ctx)
    return product


def restore_product(product_id, actor_id=None, trace_ctx=None):
    with transaction.atomic():
        product = _locked(product_id)
        if product.status != Product.Status.ARCHIVED:
            raise InvalidStateTransition("Only archived products can be restored.")
        product.status = product.status_before_archive or Product.Status.DRAFT
        product.status_before_archive = None
        product.version += 1
        product.save(update_fields=["status", "status_before_archive", "version", "updated_at"])
        emit("product.updated", "product", product.id, event_data(product, changed=["status"], restored=True),
             trace_ctx=trace_ctx)
    return product


def delete_product_permanently(product_id, actor_id=None, trace_ctx=None):
    """Hard delete, allowed only for products that never appeared in an order.

    The row lock blocks a concurrent checkout (which takes FOR SHARE on the
    product), and the database foreign keys are the final backstop.
    """
    from orders.models import InventoryReservation, OrderItem

    try:
        with transaction.atomic():
            product = _locked(product_id)
            if (OrderItem.objects.filter(product_id=product_id).exists()
                    or InventoryReservation.objects.filter(product_id=product_id).exists()):
                raise ProductInUse("This product appears in orders; archive it instead.")
            data = event_data(product)
            product.delete()
            emit("product.deleted", "product", product_id, data, trace_ctx=trace_ctx)
    except IntegrityError as exc:
        raise ProductInUse("This product appears in orders; archive it instead.") from exc


def adjust_inventory(product_id, delta, reason, actor_id=None, trace_ctx=None):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory SET available_quantity = available_quantity + %s, updated_at = now() "
                "WHERE product_id = %s AND available_quantity + %s >= 0 RETURNING available_quantity",
                [delta, product_id, delta],
            )
            row = cursor.fetchone()
        if row is None:
            inventory = Inventory.objects.filter(product_id=product_id).first()
            if inventory is None:
                raise NotFound("Product not found.")
            raise InsufficientStock(
                f"Cannot remove {-delta}; available {inventory.available_quantity}.",
                available=inventory.available_quantity,
            )
        quantity = row[0]
        adjustment = InventoryAdjustment.objects.create(
            product_id=product_id, delta=delta, resulting_quantity=quantity, reason=reason, actor_id=actor_id
        )
        emit("inventory.changed", "product", product_id,
             {"product_id": str(product_id), "available_quantity": quantity, "delta": delta, "reason": reason},
             trace_ctx=trace_ctx)
    return adjustment
