# Runbook: queue backlog

**Signal:** `make worker-status` shows `worker.<consumer>` growing, or the
outbox pending count growing while the broker is healthy.

1. **Is the worker consuming?** Check `docker compose ps worker` and
   `curl localhost:8090/readyz` (inside the container). If it is
   reconnecting, see `broker-outage-and-replay.md`.
2. **Is a dependency slow?** Retry queues filling (`.retry.N`) means the
   handler fails transiently. For the e-mail consumers, check SMTP or
   Mailpit.
3. **Scale out.** Workers are stateless. Run more replicas with
   `docker compose --profile core up -d --scale worker=3`. Idempotency
   (`worker.processed_event`) makes concurrent consumers safe.
   `WORKER_PREFETCH` bounds how many messages each one holds.
4. **Outbox backlog with a healthy broker.** The publisher is the
   bottleneck. Raise `OUTBOX_BATCH_SIZE`. Extra publisher replicas are safe
   (SKIP LOCKED) but give up strict per-aggregate ordering.
