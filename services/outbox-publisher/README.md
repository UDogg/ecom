# outbox-publisher

A single-file Python process (`publisher.py`) that moves committed rows from
`commerce.outbox_event` to the `commerce.events` topic exchange. Its
guarantees are listed in the module docstring:

- **at-least-once**: a row is marked only after the broker confirms it
- **nothing dropped**: unroutable events go to `commerce.events.unrouted`
- **commit order** with a single replica
- **broker outages**: rows wait; reconnects back off with jitter

Its database role (`outbox_publisher`) may SELECT the outbox and UPDATE
`published_at` and `publish_attempts`, and nothing else.

```bash
POSTGRES_HOST=localhost OUTBOX_PUBLISHER_DB_USER=postgres OUTBOX_PUBLISHER_DB_PASSWORD=... \
RABBITMQ_URL=amqp://user:pass@localhost:5672/%2F pytest
```
