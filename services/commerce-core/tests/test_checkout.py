import uuid

import pytest

from catalog.models import Inventory
from orders.models import CheckoutIdempotency, Order, OrderItem
from outbox.models import OutboxEvent

from .conftest import CUSTOMER_ID, checkout

pytestmark = pytest.mark.django_db


def stock(product):
    return Inventory.objects.get(product=product).available_quantity


def test_checkout_requires_idempotency_key(customer, make_product):
    product = make_product()
    resp = customer.post("/api/commerce/checkout", {"items": [{"product_id": str(product.id), "quantity": 1}]},
                         format="json")
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/idempotency-key-required")


def test_checkout_requires_sign_in(anon, make_product):
    assert checkout(anon, [(make_product(), 1)]).status_code == 401


def test_checkout_reserves_stock_and_records_order_and_events(customer, make_product):
    mug, pen = make_product(price_minor=1299, initial_stock=5), make_product(price_minor=300, initial_stock=2)

    resp = checkout(customer, [(mug, 2), (pen, 1), (mug, 1)])  # duplicate lines merge

    assert resp.status_code == 201, resp.json()
    assert resp["Idempotent-Replayed"] == "false"
    body = resp.json()
    assert body["status"] == "pending_payment"
    assert body["total_minor"] == 3 * 1299 + 300
    assert {(i["sku"], i["quantity"]) for i in body["items"]} == {(mug.sku, 3), (pen.sku, 1)}
    assert stock(mug) == 2 and stock(pen) == 1

    order = Order.objects.get(id=body["id"])
    assert order.customer_id == CUSTOMER_ID
    assert order.reservations.count() == 2
    assert OutboxEvent.objects.filter(event_type="order.placed.v1", aggregate_id=str(order.id)).count() == 1
    assert OutboxEvent.objects.filter(event_type="inventory.changed.v1").count() == 2


def test_order_keeps_price_snapshot_after_price_change(owner, customer, make_product):
    product = make_product(price_minor=1000)
    order_id = checkout(customer, [(product, 1)]).json()["id"]

    owner.patch(f"/api/commerce/admin/products/{product.id}", {"price_minor": 9999, "name": "Renamed"},
                format="json", HTTP_IF_MATCH='"1"')

    order = customer.get(f"/api/commerce/orders/{order_id}").json()
    assert order["total_minor"] == 1000
    assert order["items"][0]["unit_price_minor"] == 1000
    assert order["items"][0]["name"] == product.name


def test_retry_with_same_key_returns_original_order(customer, make_product):
    product = make_product(initial_stock=5)
    key = "checkout-abc"

    first = checkout(customer, [(product, 2)], key=key)
    second = checkout(customer, [(product, 2)], key=key)

    assert first.status_code == second.status_code == 201
    assert second["Idempotent-Replayed"] == "true"
    assert first.json() == second.json()
    assert Order.objects.count() == 1
    assert stock(product) == 3


def test_same_key_with_different_request_is_rejected(customer, make_product):
    product = make_product(initial_stock=5)
    checkout(customer, [(product, 1)], key="k1")

    resp = checkout(customer, [(product, 2)], key="k1")

    assert resp.status_code == 422
    assert Order.objects.count() == 1


def test_idempotency_keys_are_scoped_per_customer(customer, other_customer, make_product):
    product = make_product()
    a = checkout(customer, [(product, 1)], key="shared")
    b = checkout(other_customer, [(product, 1)], key="shared")
    assert a.json()["id"] != b.json()["id"]


def test_insufficient_stock_rolls_back_everything(customer, make_product):
    plenty, scarce = make_product(initial_stock=10), make_product(initial_stock=1)

    resp = checkout(customer, [(plenty, 2), (scarce, 3)], key="k")

    assert resp.status_code == 409
    problem = resp.json()
    assert problem["type"].endswith("/insufficient-stock")
    assert problem["detail"] == "Requested 3, available 1."
    assert stock(plenty) == 10 and stock(scarce) == 1
    assert not Order.objects.exists()
    assert not CheckoutIdempotency.objects.exists()
    assert not OutboxEvent.objects.filter(event_type="order.placed.v1").exists()

    # A failed attempt is not cached: the same key works once the cart is valid.
    assert checkout(customer, [(plenty, 2), (scarce, 1)], key="k").status_code == 201


def test_archived_and_unknown_products_cannot_be_bought(owner, customer, make_product):
    product = make_product()
    owner.delete(f"/api/commerce/admin/products/{product.id}")

    resp = checkout(customer, [(product, 1)])
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/product-unavailable")

    ghost = type("Ghost", (), {"id": uuid.uuid4()})
    assert checkout(customer, [(ghost, 1)]).status_code == 409


def test_customers_only_see_their_own_orders(customer, other_customer, owner, make_product):
    order_id = checkout(customer, [(make_product(), 1)]).json()["id"]

    assert customer.get(f"/api/commerce/orders/{order_id}").status_code == 200
    assert other_customer.get(f"/api/commerce/orders/{order_id}").status_code == 404
    assert other_customer.get("/api/commerce/orders").json()["total"] == 0
    assert owner.get(f"/api/commerce/orders/{order_id}").status_code == 200
    assert owner.get("/api/commerce/admin/orders").json()["total"] == 1
    assert customer.get("/api/commerce/admin/orders").status_code == 403


def test_archived_product_order_history_still_renders(owner, customer, make_product):
    product = make_product()
    order_id = checkout(customer, [(product, 1)]).json()["id"]
    owner.delete(f"/api/commerce/admin/products/{product.id}")

    order = customer.get(f"/api/commerce/orders/{order_id}").json()
    assert order["items"][0]["sku"] == product.sku
    assert OrderItem.objects.filter(product=product).exists()


def test_cart_round_trip_and_unavailable_flag(owner, customer, make_product):
    a, b = make_product(initial_stock=3), make_product(initial_stock=3)

    resp = customer.put("/api/commerce/cart", {"items": [{"product_id": str(a.id), "quantity": 2},
                                                          {"product_id": str(b.id), "quantity": 1}]}, format="json")
    assert resp.status_code == 200
    assert resp.json()["subtotal_minor"] == 3000

    owner.delete(f"/api/commerce/admin/products/{b.id}")
    cart = customer.get("/api/commerce/cart").json()
    assert {i["product_id"]: i["available"] for i in cart["items"]} == {str(a.id): True, str(b.id): False}
    assert cart["subtotal_minor"] == 2000

    checkout(customer, [(a, 2)])
    assert [i["product_id"] for i in customer.get("/api/commerce/cart").json()["items"]] == [str(b.id)]


def test_cart_rejects_unknown_products(customer):
    resp = customer.put("/api/commerce/cart", {"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}]},
                        format="json")
    assert resp.status_code == 400
