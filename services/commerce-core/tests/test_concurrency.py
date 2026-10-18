"""Scenario A in miniature: real threads, real PostgreSQL connections."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from catalog.models import Inventory, Product
from catalog.services import adjust_inventory, delete_product_permanently
from common.errors import DomainError
from orders.models import Order, OrderItem
from orders.services import checkout

pytestmark = pytest.mark.django_db(transaction=True)


def run_concurrently(fn, n, workers=None):
    barrier = threading.Barrier(n)

    def task(i):
        try:
            barrier.wait()
            return fn(i)
        except DomainError as exc:
            return exc
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=workers or n) as pool:
        return list(pool.map(task, range(n)))


def test_flash_sale_never_oversells(make_product):
    product = make_product(initial_stock=10)

    results = run_concurrently(
        lambda i: checkout(uuid.uuid4(), f"k{i}", [{"product_id": product.id, "quantity": 1}]), 40)

    succeeded = [r for r in results if isinstance(r, tuple)]
    assert len(succeeded) == 10
    assert all(type(r).__name__ == "InsufficientStock" for r in results if not isinstance(r, tuple))
    assert Inventory.objects.get(product=product).available_quantity == 0
    assert OrderItem.objects.filter(product=product).count() == 10


def test_duplicate_submissions_create_one_order(make_product):
    product = make_product(initial_stock=10)
    customer_id = uuid.uuid4()

    results = run_concurrently(
        lambda i: checkout(customer_id, "same-key", [{"product_id": product.id, "quantity": 2}]), 20)

    assert all(isinstance(r, tuple) and r[0] == 201 for r in results), results
    assert len({r[1]["id"] for r in results}) == 1
    assert sum(1 for r in results if not r[2]) == 1  # exactly one non-replayed
    assert Order.objects.count() == 1
    assert Inventory.objects.get(product=product).available_quantity == 8


def test_concurrent_adjustments_cannot_go_negative(make_product):
    product = make_product(initial_stock=5)

    results = run_concurrently(lambda i: adjust_inventory(product.id, -1, "shrinkage"), 12)

    assert sum(1 for r in results if not isinstance(r, DomainError)) == 5
    assert Inventory.objects.get(product=product).available_quantity == 0


def test_checkout_racing_permanent_delete_stays_consistent(make_product):
    """Either the order wins (delete refused) or the delete wins (checkout refused)."""
    for _ in range(5):
        product = make_product(initial_stock=3)

        def act(i, pid=product.id):
            if i == 0:
                return delete_product_permanently(pid) or "deleted"
            return checkout(uuid.uuid4(), "k", [{"product_id": pid, "quantity": 1}])

        results = run_concurrently(act, 2)
        exists = Product.objects.filter(id=product.id).exists()
        ordered = OrderItem.objects.filter(product_id=product.id).exists()
        if exists:
            assert ordered and type(results[0]).__name__ == "ProductInUse"
        else:
            assert not ordered and results[0] == "deleted"
            assert type(results[1]).__name__ == "ProductUnavailable"
