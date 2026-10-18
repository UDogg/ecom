import pytest

from catalog.models import Inventory, InventoryAdjustment, Product
from outbox.models import OutboxEvent

from .conftest import checkout

pytestmark = pytest.mark.django_db

ADMIN = "/api/commerce/admin/products"

MUG = {
    "sku": "MUG-001",
    "name": "Ceramic Mug",
    "description": "350 ml, dishwasher safe.",
    "price_minor": 1299,
    "currency": "USD",
    "category": "kitchen",
    "initial_stock": 40,
    "image_keys": ["uploads/2026/10/mug-front.webp"],
    "status": "active",
}


def events(event_type):
    return list(OutboxEvent.objects.filter(event_type=event_type))


def test_owner_creates_product_with_stock_images_and_event(owner):
    resp = owner.post(ADMIN, MUG, format="json")

    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["sku"] == "MUG-001"
    assert body["available_quantity"] == 40
    assert body["version"] == 1
    assert resp["ETag"] == '"1"'
    assert body["images"][0]["url"].endswith("/uploads/2026/10/mug-front.webp")
    assert body["category"]["slug"] == "kitchen"

    product = Product.objects.get(sku="MUG-001")
    assert Inventory.objects.get(product=product).available_quantity == 40
    assert InventoryAdjustment.objects.get(product=product).reason == "initial stock"
    [event] = events("product.created.v1")
    assert event.payload["sku"] == "MUG-001"
    assert event.envelope()["type"] == "product.created"


def test_new_active_product_is_visible_in_public_catalog(owner, anon):
    product_id = owner.post(ADMIN, MUG, format="json").json()["id"]

    listing = anon.get("/api/catalog/products").json()
    assert [p["id"] for p in listing["items"]] == [product_id]
    assert "available_quantity" not in listing["items"][0]  # admin-only field
    assert anon.get(f"/api/catalog/products/{product_id}").status_code == 200
    assert anon.get("/api/catalog/search", {"q": "mug"}).json()["total"] == 1


def test_draft_product_is_hidden_from_public(owner, anon):
    product_id = owner.post(ADMIN, {**MUG, "status": "draft"}, format="json").json()["id"]

    assert anon.get("/api/catalog/products").json()["total"] == 0
    assert anon.get(f"/api/catalog/products/{product_id}").status_code == 404


def test_duplicate_sku_is_a_conflict(owner):
    assert owner.post(ADMIN, MUG, format="json").status_code == 201
    resp = owner.post(ADMIN, {**MUG, "name": "Another"}, format="json")

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/sku-taken")
    assert Product.objects.count() == 1


@pytest.mark.parametrize("bad", [
    {"name": ""},
    {"name": "x" * 201},
    {"price_minor": -1},
    {"initial_stock": -5},
    {"sku": "has spaces"},
    {"currency": "usd"},
    {"status": "archived"},
    {"image_keys": ["../../etc/passwd.png"]},
    {"image_keys": ["uploads/evil.exe"]},
    {"image_keys": [f"uploads/{i}.png" for i in range(9)]},
])
def test_create_validation(owner, bad):
    resp = owner.post(ADMIN, {**MUG, **bad}, format="json")

    assert resp.status_code == 400, resp.json()
    assert resp["Content-Type"] == "application/problem+json"
    assert set(resp.json()["errors"]) == set(bad)
    assert not Product.objects.exists()


def test_customers_and_anonymous_users_cannot_use_admin_api(customer, anon, make_product):
    product = make_product()
    for method, url in [("get", ADMIN), ("post", ADMIN), ("patch", f"{ADMIN}/{product.id}"),
                        ("delete", f"{ADMIN}/{product.id}"),
                        ("post", f"/api/commerce/admin/inventory/{product.id}/adjust")]:
        assert getattr(customer, method)(url, {}, format="json").status_code == 403, (method, url)
        assert getattr(anon, method)(url, {}, format="json").status_code == 401, (method, url)
    assert Product.objects.get(id=product.id).status == "active"


def test_edit_requires_if_match(owner, make_product):
    product = make_product()
    resp = owner.patch(f"{ADMIN}/{product.id}", {"price_minor": 1500}, format="json")
    assert resp.status_code == 428


def test_edit_with_current_version_bumps_version_and_emits_event(owner, make_product):
    product = make_product()
    resp = owner.patch(f"{ADMIN}/{product.id}", {"price_minor": 1500, "name": "Renamed"}, format="json",
                       HTTP_IF_MATCH='"1"')

    assert resp.status_code == 200, resp.json()
    assert resp.json()["price_minor"] == 1500
    assert resp.json()["version"] == 2
    assert resp["ETag"] == '"2"'
    assert events("product.updated.v1")[0].payload["changed"] == ["name", "price_minor"]


def test_stale_edit_is_rejected_not_silently_overwritten(owner, make_product):
    product = make_product()
    assert owner.patch(f"{ADMIN}/{product.id}", {"name": "First"}, format="json",
                       HTTP_IF_MATCH='"1"').status_code == 200
    resp = owner.patch(f"{ADMIN}/{product.id}", {"name": "Second"}, format="json", HTTP_IF_MATCH='"1"')

    assert resp.status_code == 409
    assert resp.json()["current_version"] == 2
    assert Product.objects.get(id=product.id).name == "First"


def test_sku_is_immutable(owner, make_product):
    product = make_product()
    resp = owner.patch(f"{ADMIN}/{product.id}", {"sku": "NEW"}, format="json", HTTP_IF_MATCH='"1"')
    assert resp.status_code == 400
    assert Product.objects.get(id=product.id).sku == product.sku


def test_archive_hides_product_and_restore_brings_it_back(owner, anon, make_product):
    product = make_product()

    resp = owner.delete(f"{ADMIN}/{product.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"
    assert anon.get(f"/api/catalog/products/{product.id}").status_code == 404
    assert anon.get("/api/catalog/products").json()["total"] == 0
    assert len(events("product.archived.v1")) == 1

    # Archiving again is a no-op, not a second event.
    assert owner.delete(f"{ADMIN}/{product.id}").status_code == 200
    assert len(events("product.archived.v1")) == 1

    resp = owner.post(f"{ADMIN}/{product.id}/restore")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    assert anon.get(f"/api/catalog/products/{product.id}").status_code == 200


def test_restoring_a_non_archived_product_is_refused(owner, make_product):
    product = make_product()
    assert owner.post(f"{ADMIN}/{product.id}/restore").status_code == 409


def test_archived_product_status_cannot_be_changed_by_edit(owner, make_product):
    product = make_product()
    owner.delete(f"{ADMIN}/{product.id}")
    resp = owner.patch(f"{ADMIN}/{product.id}", {"status": "active"}, format="json", HTTP_IF_MATCH='"2"')
    assert resp.status_code == 409


def test_permanent_delete_of_never_ordered_product(owner, make_product):
    product = make_product(image_keys=["uploads/a.png"])

    resp = owner.delete(f"{ADMIN}/{product.id}?permanent=true")

    assert resp.status_code == 204
    assert not Product.objects.filter(id=product.id).exists()
    assert not Inventory.objects.filter(product_id=product.id).exists()
    [event] = events("product.deleted.v1")
    assert event.payload["sku"] == product.sku


def test_permanent_delete_of_ordered_product_is_refused(owner, customer, make_product):
    product = make_product()
    assert checkout(customer, [(product, 1)]).status_code == 201

    resp = owner.delete(f"{ADMIN}/{product.id}?permanent=true")

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/product-in-use")
    assert Product.objects.filter(id=product.id).exists()


def test_inventory_adjustments_are_audited(owner, make_product):
    product = make_product(initial_stock=5)
    url = f"/api/commerce/admin/inventory/{product.id}/adjust"

    resp = owner.post(url, {"delta": 7, "reason": "restock"}, format="json")
    assert resp.status_code == 201
    assert resp.json()["resulting_quantity"] == 12

    resp = owner.post(url, {"delta": -20, "reason": "damaged"}, format="json")
    assert resp.status_code == 409
    assert resp.json()["available"] == 12

    assert owner.post(url, {"delta": 0, "reason": "nothing"}, format="json").status_code == 400

    history = owner.get(f"/api/commerce/admin/inventory/{product.id}/adjustments").json()
    assert [a["reason"] for a in history["items"]] == ["restock", "initial stock"]
    assert Inventory.objects.get(product=product).available_quantity == 12
    assert len(events("inventory.changed.v1")) == 1


def test_dashboard_reports_low_stock_and_outbox_backlog(owner, make_product):
    make_product(initial_stock=2)
    make_product(initial_stock=50)
    body = owner.get("/api/commerce/admin/dashboard").json()
    assert len(body["low_stock"]) == 1
    assert body["system"]["outbox_pending"] == 2
