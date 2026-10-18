import os
import uuid

# .env leaves this empty (production-like), so fall back rather than setdefault.
os.environ["JWT_DEV_HS256_SECRET"] = (os.environ.get("JWT_DEV_HS256_SECRET")
                                      or "test-only-secret-that-is-long-enough-for-hs256")

import pytest  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from catalog.services import create_product  # noqa: E402
from common.auth import issue_dev_token  # noqa: E402

TEST_SECRET = os.environ["JWT_DEV_HS256_SECRET"]

OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
CUSTOMER_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
OTHER_CUSTOMER_ID = uuid.UUID("00000000-0000-4000-8000-000000000003")


@pytest.fixture(autouse=True)
def _dev_secret():
    with override_settings(JWT_DEV_HS256_SECRET=TEST_SECRET):
        yield


def client_for(sub, roles):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_dev_token(sub, roles)}")
    return client


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture
def owner():
    return client_for(OWNER_ID, {"customer", "shop_owner"})


@pytest.fixture
def customer():
    return client_for(CUSTOMER_ID, {"customer"})


@pytest.fixture
def other_customer():
    return client_for(OTHER_CUSTOMER_ID, {"customer"})


_counter = iter(range(1, 10**6))


@pytest.fixture
def make_product():
    def make(**overrides):
        n = next(_counter)
        data = {
            "sku": f"TEST-{n:05d}",
            "name": f"Test product {n}",
            "description": "A product for tests.",
            "price_minor": 1000,
            "currency": "USD",
            "category": "test",
            "initial_stock": 10,
            "status": "active",
            "image_keys": [],
        }
        data.update(overrides)
        return create_product(data, actor_id=OWNER_ID)

    return make


def checkout(client, items, key=None):
    return client.post(
        "/api/commerce/checkout",
        {"items": [{"product_id": str(p.id), "quantity": q} for p, q in items]},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )
