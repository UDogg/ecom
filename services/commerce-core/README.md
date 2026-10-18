# commerce-core

Django service that is the **single authoritative owner** of products,
inventory, carts, orders and the transactional outbox (ADR 001, 002). It also
serves the database-backed public catalog, which is both the baseline path and
the fallback contract for `catalog-reader`.

## What's implemented

| Area | Where | Notes |
|---|---|---|
| Domain model + DB-enforced invariants | `catalog/models.py`, `orders/models.py` | `CHECK (available_quantity >= 0)`, unique SKU, price snapshots, order items `PROTECT` products |
| Admin product lifecycle | `catalog/services.py` | create, edit (`If-Match` optimistic concurrency), archive, restore, permanent delete only if never ordered |
| Audited stock adjustments | `adjust_inventory` | conditional update, can never go negative |
| Cart + idempotent checkout | `orders/services.py` | atomic conditional reservation, `(customer, Idempotency-Key)` unique, replay returns the original order, different body returns `422` |
| Order state machine | `orders/state.py` | transitions from README section 8.2 |
| Reservation expiry | `expire_reservations` command | `SKIP LOCKED`, re-checks status under lock, releases stock |
| Transactional outbox | `outbox/` | `emit()` refuses to run outside a transaction; relay uses `SKIP LOCKED`, at-least-once |
| Local JWT validation | `common/auth.py` | RS256 via cached JWKS from auth-service; keeps the last good keys through an auth outage; HS256 dev tokens only when `JWT_DEV_HS256_SECRET` is set |
| Problem-details errors | `common/errors.py` | `trace_id` taken from `traceparent` |
| `catalog_public` view | `catalog/migrations/0002_*` | the only relation the `catalog_reader` role can read |

Events leave through `services/outbox-publisher` (to RabbitMQ) and are
consumed by `services/worker`. The `outbox_publish` command here is a
log-sink relay for debugging only.

Not yet: payments (`payments-sim`), presigned uploads (MinIO), and OpenAPI
docs. See the roadmap in the root README.

## Run the tests natively

You need a PostgreSQL 16 you can connect to as a role with `CREATEDB`:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export POSTGRES_HOST=localhost COMMERCE_DB_USER=postgres COMMERCE_DB_PASSWORD=...
export JWT_DEV_HS256_SECRET=any-long-local-secret
pytest
```

Or from the repo root with Docker: `make test`.

The concurrency tests (`tests/test_concurrency.py`) use real threads and real
connections: 40 buyers race for 10 units, 20 duplicate submissions share one
key, adjustments race to zero, and checkout races permanent delete.

## Talk to it

```bash
make up && make seed
OWNER=$(make -s token ROLE=owner)    # real sign-in through auth-service

curl -s -X POST localhost:8000/api/commerce/admin/products \
  -H "Authorization: Bearer $OWNER" -H 'Content-Type: application/json' \
  -d '{"sku":"MUG-100","name":"Mug","price_minor":1299,"category":"kitchen","initial_stock":5,"status":"active"}'
```

Until the gateway exists, commerce-core is reachable directly on
`http://localhost:8000` (routes already carry the `/api/commerce` and
`/api/catalog` prefixes the gateway will forward unchanged).
