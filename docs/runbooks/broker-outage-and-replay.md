# Runbook: broker outage and dead-letter replay

## What happens during a RabbitMQ outage

Nothing a customer or shop owner does fails. Orders, product edits, and
stock changes commit to PostgreSQL together with their outbox rows.
`outbox-publisher` cannot publish, so the rows wait (`published_at IS NULL`).
It retries with capped exponential backoff and jitter (up to
`OUTBOX_MAX_BACKOFF_SECONDS`, 30 seconds by default).

Symptoms:

- The admin dashboard's `system.outbox_pending` grows.
- `outbox-publisher` and `worker` log `retrying in` / `reconnecting in` and
  report 503 on `/readyz`.
- Order e-mails and low-stock alerts are delayed, not lost.

## Recovery

1. Restore RabbitMQ (`docker compose --profile core start rabbitmq`).
2. Watch the backlog drain:
   `make api ROLE=owner CALL="GET /api/admin/dashboard"` (look at `system`)
3. Within one backoff interval the publisher reconnects and publishes in
   commit order. Duplicates are possible (at-least-once delivery), and the
   worker drops them by event id.

Rehearse it with `make scenario-c` (stop/start; `SCENARIO_C_MODE=pause`
freezes the broker instead).

## Dead-lettered messages

A message lands in `worker.<consumer>.dlq` when it is malformed, or when it
still fails after every retry in `WORKER_RETRY_DELAYS_MS`.

1. Inspect: `make worker-status`. Each message keeps `x-last-error`,
   `x-attempt` and `x-dead-lettered-at` headers. View them in the RabbitMQ
   UI at http://localhost:15672 (Queues, then *dlq*, then Get messages).
2. Fix the cause (for example, Mailpit or SMTP being down).
3. Replay: `make dlq-replay CONSUMER=order-email`. The messages go back to
   the main queue with a fresh retry budget. Messages that were already
   handled are skipped (idempotent consumers), so replaying is always safe.

Messages flagged `x-permanent: true` are malformed. Replaying them only
dead-letters them again; fix the producer instead.

## Events nobody consumes

Events with no bound queue go to `commerce.events.unrouted` through the
exchange's alternate exchange, never into the void. When a new consumer is
added, decide whether to backfill from that queue.
