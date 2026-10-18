"""outbox-publisher: moves committed outbox rows onto RabbitMQ.

    PostgreSQL commerce.outbox_event --(this process)--> exchange commerce.events

Guarantees, and their limits:

* At-least-once. A row is marked published only after the broker confirms the
  message (publisher confirms), so a crash between the two re-sends it.
  Consumers dedupe by event id.
* Nothing silently dropped. The exchange has an alternate exchange, so an
  event no queue is bound to yet lands in `commerce.events.unrouted` instead
  of vanishing.
* Per-aggregate order holds for a single publisher replica (rows go out in
  commit order). Running several replicas is safe (SKIP LOCKED never sends a
  row twice concurrently) but can interleave across aggregates.
* Broker outage: rows simply stay unpublished; the loop reconnects with
  capped exponential backoff and full jitter, then drains the backlog.

Its database role may only SELECT the outbox and UPDATE two columns of it.
"""

import json
import logging
import os
import random
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pika
import pika.exceptions
import psycopg
from psycopg import sql

log = logging.getLogger("outbox-publisher")

UNROUTED_SUFFIX = ".unrouted"


@dataclass
class Config:
    db_host: str = field(default_factory=lambda: os.environ.get("POSTGRES_HOST", "postgres"))
    db_port: str = field(default_factory=lambda: os.environ.get("POSTGRES_PORT", "5432"))
    db_name: str = field(default_factory=lambda: os.environ.get("POSTGRES_DB", "shop"))
    db_user: str = field(default_factory=lambda: os.environ.get("OUTBOX_PUBLISHER_DB_USER", "outbox_publisher"))
    db_password: str = field(default_factory=lambda: os.environ.get("OUTBOX_PUBLISHER_DB_PASSWORD", ""))
    table: str = field(default_factory=lambda: os.environ.get("OUTBOX_TABLE", "commerce.outbox_event"))
    rabbitmq_url: str = field(default_factory=lambda: os.environ.get(
        "RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F"))
    exchange: str = field(default_factory=lambda: os.environ.get("EVENTS_EXCHANGE", "commerce.events"))
    batch_size: int = field(default_factory=lambda: int(os.environ.get("OUTBOX_BATCH_SIZE", "100")))
    poll_seconds: float = field(default_factory=lambda: float(os.environ.get("OUTBOX_POLL_SECONDS", "0.5")))
    max_backoff_seconds: float = field(default_factory=lambda: float(os.environ.get("OUTBOX_MAX_BACKOFF_SECONDS", "30")))
    health_port: int = field(default_factory=lambda: int(os.environ.get("HEALTH_PORT", "8091")))

    @property
    def dsn(self):
        return psycopg.conninfo.make_conninfo(host=self.db_host, port=self.db_port, dbname=self.db_name,
                                              user=self.db_user, password=self.db_password, connect_timeout=5)


class BrokerUnavailable(Exception):
    pass


def table_identifier(name):
    schema, _, table = name.rpartition(".")
    return sql.Identifier(schema, table) if schema else sql.Identifier(table)


def iso_utc(ts):
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def envelope(row):
    """The wire format from docs/contracts/events/envelope.v1.schema.json."""
    event_type = row["event_type"]
    name = event_type.rsplit(".v", 1)[0]
    return {
        "id": str(row["id"]),
        "type": name,
        "version": row["schema_ver"],
        "occurred_at": iso_utc(row["created_at"]),
        "producer": "commerce-core",
        "trace": row["trace_ctx"] or {},
        "data": row["payload"],
    }


def declare_topology(channel, exchange):
    unrouted = exchange + UNROUTED_SUFFIX
    channel.exchange_declare(unrouted, exchange_type="fanout", durable=True)
    channel.queue_declare(unrouted, durable=True)
    channel.queue_bind(unrouted, unrouted)
    channel.exchange_declare(exchange, exchange_type="topic", durable=True,
                             arguments={"alternate-exchange": unrouted})


def amqp_parameters(url):
    """Short heartbeats so a hung or paused broker is noticed in seconds, not minutes."""
    params = pika.URLParameters(url)
    params.heartbeat = int(os.environ.get("AMQP_HEARTBEAT_SECONDS", "10"))
    params.blocked_connection_timeout = 30
    params.socket_timeout = 10
    return params


def publish_one(channel, exchange, row):
    env = envelope(row)
    headers = {"x-aggregate-type": row["aggregate_type"], "x-aggregate-id": row["aggregate_id"]}
    if env["trace"].get("traceparent"):
        headers["traceparent"] = env["trace"]["traceparent"]
    channel.basic_publish(
        exchange=exchange,
        routing_key=row["event_type"],
        body=json.dumps(env, separators=(",", ":")).encode(),
        properties=pika.BasicProperties(
            message_id=env["id"],
            type=row["event_type"],
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
            timestamp=int(row["created_at"].timestamp()),
            app_id="outbox-publisher",
            headers=headers,
        ),
    )  # in confirm mode this blocks until the broker acks, and raises on nack


def publish_batch(db, channel, cfg):
    """Publish up to one batch. Returns how many rows were published.

    Rows confirmed before a broker failure are still marked, and the failing
    row's attempt counter goes up, before BrokerUnavailable is raised.
    """
    table = table_identifier(cfg.table)
    with db.transaction():
        rows = db.execute(
            sql.SQL("SELECT id, event_type, schema_ver, aggregate_type, aggregate_id, payload, trace_ctx, created_at "
                    "FROM {} WHERE published_at IS NULL ORDER BY created_at, id LIMIT %s "
                    "FOR UPDATE SKIP LOCKED").format(table),
            [cfg.batch_size],
        ).fetchall()
        published, failure = [], None
        for row in rows:
            try:
                publish_one(channel, cfg.exchange, row)
            except (pika.exceptions.AMQPError, OSError) as exc:
                failure = (row["id"], exc)
                break
            published.append(row["id"])
        if published:
            db.execute(sql.SQL("UPDATE {} SET published_at = now() WHERE id = ANY(%s)").format(table), [published])
        if failure:
            db.execute(sql.SQL("UPDATE {} SET publish_attempts = publish_attempts + 1 WHERE id = %s").format(table),
                       [failure[0]])
    if failure:
        raise BrokerUnavailable(f"publish failed after {len(published)} events: {failure[1]!r}") from failure[1]
    return len(published)


def backoff_delays(max_seconds, base=0.5):
    """Capped exponential backoff with full jitter: 0..min(cap, base * 2**n)."""
    attempt = 0
    while True:
        yield random.uniform(0, min(max_seconds, base * 2 ** attempt))
        attempt += 1


class Health:
    def __init__(self):
        self.last_cycle = 0.0
        self.published_total = 0

    def ready(self, stale_after=30):
        return time.monotonic() - self.last_cycle < stale_after

    def serve(self, port):
        health = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/healthz":
                    code, body = 200, {"status": "ok", "service": "outbox-publisher"}
                elif self.path == "/readyz":
                    ok = health.ready()
                    code, body = (200 if ok else 503), {"status": "ready" if ok else "unavailable",
                                                        "published_total": health.published_total}
                else:
                    code, body = 404, {"status": "not found"}
                payload = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def run(cfg, stop, health):
    delays = backoff_delays(cfg.max_backoff_seconds)
    while not stop.is_set():
        connection = None
        try:
            with psycopg.connect(cfg.dsn, row_factory=psycopg.rows.dict_row) as db:
                connection = pika.BlockingConnection(amqp_parameters(cfg.rabbitmq_url))
                channel = connection.channel()
                channel.confirm_delivery()
                declare_topology(channel, cfg.exchange)
                log.info("connected; publishing to %s", cfg.exchange)
                delays = backoff_delays(cfg.max_backoff_seconds)  # healthy again: reset backoff
                while not stop.is_set():
                    count = publish_batch(db, channel, cfg)
                    health.last_cycle = time.monotonic()
                    health.published_total += count
                    if count:
                        log.info("published %d events", count)
                    if count < cfg.batch_size:
                        # Sleep through the broker so heartbeats keep flowing.
                        connection.process_data_events(time_limit=cfg.poll_seconds)
        except (BrokerUnavailable, pika.exceptions.AMQPError, psycopg.OperationalError, OSError) as exc:
            delay = next(delays)
            log.warning("%s: %s; retrying in %.1fs", exc.__class__.__name__, exc, delay)
            stop.wait(delay)
        finally:
            if connection is not None and connection.is_open:
                try:
                    connection.close()
                except pika.exceptions.AMQPError:
                    pass


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(),
                        format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"outbox-publisher","msg":"%(message)s"}')
    logging.getLogger("pika").setLevel(logging.WARNING)  # its INFO is per-connection chatter
    cfg, stop, health = Config(), threading.Event(), Health()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    health.serve(cfg.health_port)
    run(cfg, stop, health)


if __name__ == "__main__":
    main()
