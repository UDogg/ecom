# ADR 010: Event consumers use plain AMQP, not Celery tasks

**Status:** Accepted (Phase 3)

## Context

The README lists "Celery + RabbitMQ" for asynchronous jobs. The work Phase 3
actually needs is consuming **domain events** that the outbox publishes:
`order.placed.v1`, `inventory.changed.v1`, and so on. Those events are a
language-neutral contract (`docs/contracts/events/`). Later consumers include
Go (`catalog-reader` cache invalidation) and possibly Java.

Celery's task protocol is a Python RPC format: a message names a Python task
function and carries Celery-specific headers. Publishing events as Celery
tasks would tie every producer and consumer to that wire format, and would
make the outbox publisher decide which Python function handles each event.

## Decision

- `outbox-publisher` publishes envelopes to the `commerce.events` topic
  exchange, with the routing key `<type>.v<version>`.
- `worker` consumes them with plain AMQP (pika). Each consumer has its own
  queue, TTL-based retry queues, and a dead-letter queue, all declared in
  `services/worker/worker/consumer.py`.
- Idempotency, retry, and dead-lettering live in the worker (about 150
  lines), where they can be read and tested directly.
- Celery stays available for **internal scheduled jobs** if one later
  justifies it. Reservation expiry already runs as a small loop
  (`commerce-expirer`), so none does yet.

## Consequences

- Any language can consume events by binding a queue. No Celery dependency.
- We own the retry and DLQ topology instead of using Celery's `autoretry_for`.
  This is covered by `services/worker/tests/`.
- The README's runtime table still says Celery. Read it as "a Python worker
  on RabbitMQ"; this ADR is the source of truth.
