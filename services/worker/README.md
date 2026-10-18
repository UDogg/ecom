# worker

Consumes `commerce.events` (see `docs/adr/010-event-consumers-use-plain-amqp.md`).

| Consumer | Events | Does |
|---|---|---|
| `order-email` | `order.placed.v1` | Order confirmation to the customer (Mailpit locally) |
| `low-stock` | `inventory.changed.v1` | Alerts the owner when stock drops to `LOW_STOCK_THRESHOLD` or below |

Each consumer gets `worker.<name>` (main), `worker.<name>.retry.<i>` (TTL
from `WORKER_RETRY_DELAYS_MS`) and `worker.<name>.dlq`. Handling is
idempotent per `(consumer, event id)` through `worker.processed_event`.

```bash
python -m worker run                  # default: all consumers
python -m worker status               # queue depths (make worker-status)
python -m worker replay order-email   # DLQ back to main (make dlq-replay CONSUMER=order-email)
```

Operations: `docs/runbooks/broker-outage-and-replay.md`, `docs/runbooks/queue-backlog.md`.

```bash
POSTGRES_HOST=localhost WORKER_DB_USER=postgres WORKER_DB_PASSWORD=... \
RABBITMQ_URL=amqp://user:pass@localhost:5672/%2F pytest
```
