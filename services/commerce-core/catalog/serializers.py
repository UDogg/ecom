import re

from django.conf import settings
from rest_framework import serializers

from .models import Product

SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
IMAGE_KEY_RE = re.compile(r"^uploads/[A-Za-z0-9][A-Za-z0-9/_.-]{0,250}\.(jpe?g|png|webp)$", re.IGNORECASE)
EDITABLE_STATUSES = [Product.Status.DRAFT, Product.Status.ACTIVE]


def validate_image_keys(keys):
    if len(keys) > settings.MAX_PRODUCT_IMAGES:
        raise serializers.ValidationError(f"At most {settings.MAX_PRODUCT_IMAGES} images.")
    if len(set(keys)) != len(keys):
        raise serializers.ValidationError("Image keys must be unique.")
    for key in keys:
        if ".." in key or "//" in key or not IMAGE_KEY_RE.match(key):
            raise serializers.ValidationError(f"Invalid image key: {key!r}.")
    return keys


class ProductFields(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=200, trim_whitespace=True)
    # Stored as plain text / Markdown; the frontend renders it escaped.
    description = serializers.CharField(max_length=20000, allow_blank=True, required=False, trim_whitespace=False)
    price_minor = serializers.IntegerField(min_value=0, max_value=10**12)
    currency = serializers.RegexField(r"^[A-Z]{3}$", required=False, default=settings.DEFAULT_CURRENCY)
    category = serializers.SlugField(max_length=80)
    category_name = serializers.CharField(max_length=120, required=False)
    image_keys = serializers.ListField(child=serializers.CharField(max_length=300), required=False,
                                       validators=[validate_image_keys])
    status = serializers.ChoiceField(choices=EDITABLE_STATUSES, required=False)


class ProductCreateSerializer(ProductFields):
    sku = serializers.RegexField(SKU_RE.pattern, max_length=64)
    initial_stock = serializers.IntegerField(min_value=0, max_value=10**9, required=False, default=0)
    status = serializers.ChoiceField(choices=EDITABLE_STATUSES, required=False, default=Product.Status.DRAFT)
    image_keys = serializers.ListField(child=serializers.CharField(max_length=300), required=False, default=list,
                                       validators=[validate_image_keys])


class ProductUpdateSerializer(ProductFields):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("partial", True)
        super().__init__(*args, **kwargs)

    def validate(self, attrs):
        if "sku" in self.initial_data:
            raise serializers.ValidationError({"sku": "SKU is immutable after creation."})
        if "initial_stock" in self.initial_data:
            raise serializers.ValidationError({"initial_stock": "Use the inventory adjust endpoint to change stock."})
        if "currency" not in self.initial_data:
            attrs.pop("currency", None)  # don't apply the create-time default on PATCH
        if not attrs:
            raise serializers.ValidationError("No changes supplied.")
        return attrs


class InventoryAdjustSerializer(serializers.Serializer):
    delta = serializers.IntegerField(min_value=-(10**9), max_value=10**9)
    reason = serializers.CharField(min_length=1, max_length=200)

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError("Delta must be non-zero.")
        return value


def image_url(key):
    base = settings.PUBLIC_ASSET_BASE_URL.rstrip("/")
    return f"{base}/{key}"


def product_out(product, admin=False):
    inventory = getattr(product, "inventory", None)
    available = inventory.available_quantity if inventory else 0
    body = {
        "id": str(product.id),
        "sku": product.sku,
        "name": product.name,
        "description": product.description,
        "price_minor": product.price_minor,
        "currency": product.currency,
        "category": {"slug": product.category.slug, "name": product.category.name},
        "in_stock": available > 0,
        "images": [{"key": i.object_key, "url": image_url(i.object_key), "position": i.position}
                   for i in product.images.all()],
    }
    if admin:
        body.update({
            "status": product.status,
            "version": product.version,
            "available_quantity": available,
            "created_at": product.created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": product.updated_at.isoformat().replace("+00:00", "Z"),
        })
    return body
