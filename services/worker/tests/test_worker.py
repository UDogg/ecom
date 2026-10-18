"""Integration tests: real PostgreSQL and RabbitMQ, fake SMTP.

    POSTGRES_HOST=localhost WORKER_DB_USER=postgres \
    RABBITMQ_URL=amqp://guest:guest@localhost:5672/%2F pytest
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pika
import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from worker import handlers, store  # noqa: E402
from worker.config import Config  # noqa: E402
from worker.consumer import Worker  # noqa: E402

def _contracts_dir():
    if os.environ.get("CONTRACTS_DIR"):
        return Path(os.environ["CONTRACTS_DIR"])
    parents = Path(__file__).resolve().parents
    return parents[3] / "docs/contracts/events" if len(parents) > 3 else Path("/nonexistent")


CONTRACTS = _contracts_dir()


class FakeMailer:
    def __init__(self, fail_times=0, always_fail=False):
        self.sent, self.fail_times, self.always_fail, self.calls = [], fail_times, always_fail, 0

    def send(self, to, subject, body, message_id=None):
        self.calls += 1
        if self.always_fail or self.calls <= self.fail_times:
            raise ConnectionRefusedError("smtp down")
        self.sent.append({"to": to, "subject": subject, "body": body, "message_id": message_id})


@pytest.fixture
def cfg():
    os.environ.setdefault("POSTGRES_HOST", "localhost")
    os.environ.setdefault("WORKER_DB_USER", "postgres")
    os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")
    config = Config()
    tag = uuid.uuid4().hex[:8]
    config.db_schema = f"worker_test_{tag}"
    config.exchange = f"test.events.{tag}"
    config.queue_prefix = f"test.worker.{tag}"
    config.retry_delays_ms = (50, 100)
    yield config
    with psycopg.connect(config.dsn, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{config.db_schema}" CASCADE')


@pytest.fixture
def connection(cfg):
    conn = pika.BlockingConnection(pika.URLParameters(cfg.rabbitmq_url))
    yield conn
    cleanup = conn.channel()
    for consumer in handlers.CONSUMERS.values():
        main = f"{cfg.queue_prefix}.{consumer.name}"
        for queue in [main, f"{main}.dlq", *(f"{main}.retry.{i}" for i in range(len(cfg.retry_delays_ms)))]:
            cleanup.queue_delete(queue)
    for name in (cfg.exchange, cfg.exchange + ".unrouted"):
        cleanup.exchange_delete(name)
    cleanup.queue_delete(cfg.exchange + ".unrouted")
    conn.close()


def make_worker(cfg, connection, mailer=None, consumers=None):
    db = psycopg.connect(cfg.dsn)
    store.ensure_schema(db, cfg.db_schema)
    channel = connection.channel()
    channel.confirm_delivery()
    worker = Worker(cfg, consumers or list(handlers.CONSUMERS.values()), mailer or FakeMailer(), db, channel)
    worker.declare()
    return worker


def order_placed(email="customer@shop.local", event_id=None):
    order_id = str(uuid.uuid4())
    return {
        "id": event_id or str(uuid.uuid4()),
        "type": "order.placed",
        "version": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "producer": "commerce-core",
        "trace": {},
        "data": {
            "id": order_id, "status": "pending_payment", "total_minor": 2899, "currency": "USD",
            "customer_id": str(uuid.uuid4()), "customer_email": email,
            "items": [{"product_id": str(uuid.uuid4()), "sku": "MUG-001", "name": "Ceramic Mug",
                       "quantity": 1, "unit_price_minor": 1299},
                      {"product_id": str(uuid.uuid4()), "sku": "PEN-001", "name": "Brass Pen",
                       "quantity": 1, "unit_price_minor": 1600}],
        },
    }


def inventory_changed(quantity, delta):
    env = order_placed()
    env.update(type="inventory.changed", data={"product_id": str(uuid.uuid4()), "available_quantity": quantity,
                                               "delta": delta, "reason": "checkout"})
    return env


def publish(worker, envelope, routing_key=None, body=None):
    key = routing_key or f"{envelope['type']}.v{envelope['version']}"
    worker.channel.basic_publish(worker.config.exchange, key, body if body is not None else json.dumps(envelope),
                                 pika.BasicProperties(message_id=envelope.get("id"), delivery_mode=2))


def depth(worker, queue):
    return worker.channel.queue_declare(queue, passive=True).method.message_count


def test_order_placed_sends_one_confirmation(cfg, connection):
    mailer = FakeMailer()
    worker = make_worker(cfg, connection, mailer)
    publish(worker, order_placed())

    worker.drain(idle_seconds=0.3)

    [mail] = mailer.sent
    assert mail["to"] == "customer@shop.local"
    assert mail["subject"].startswith("Order ")
    assert "Total: 28.99 USD" in mail["body"] and "Ceramic Mug" in mail["body"]


def test_duplicate_deliveries_are_processed_once(cfg, connection):
    mailer = FakeMailer()
    worker = make_worker(cfg, connection, mailer)
    event = order_placed()
    for _ in range(3):  # the outbox is at-least-once; so is the broker
        publish(worker, event)

    worker.drain(idle_seconds=0.3)

    assert len(mailer.sent) == 1
    assert worker.stats["duplicates"] == 2


def test_transient_failure_is_retried_with_backoff_then_succeeds(cfg, connection):
    mailer = FakeMailer(fail_times=2)
    worker = make_worker(cfg, connection, mailer)
    publish(worker, order_placed())

    worker.drain(idle_seconds=0.5)

    assert len(mailer.sent) == 1
    assert mailer.calls == 3
    assert worker.stats["retried"] == 2
    assert depth(worker, worker.dlq("order-email")) == 0


def test_exhausted_retries_dead_letter_and_replay_recovers(cfg, connection):
    mailer = FakeMailer(always_fail=True)
    worker = make_worker(cfg, connection, mailer)
    publish(worker, order_placed())

    worker.drain(idle_seconds=0.5)

    assert mailer.calls == cfg.max_attempts == 3
    dlq = worker.dlq("order-email")
    assert depth(worker, dlq) == 1
    assert worker.status()[dlq] == 1
    method, props, _ = worker.channel.basic_get(dlq)
    assert "smtp down" in props.headers["x-last-error"] and props.headers["x-attempt"] == 3
    worker.channel.basic_nack(method.delivery_tag, requeue=True)

    # SMTP is fixed; an operator replays the DLQ.
    mailer.always_fail = False
    assert worker.replay("order-email") == 1
    worker.drain(idle_seconds=0.3)
    assert len(mailer.sent) == 1
    assert depth(worker, dlq) == 0


def test_malformed_messages_skip_retries(cfg, connection):
    worker = make_worker(cfg, connection)
    publish(worker, {"id": "x"}, routing_key="order.placed.v1", body="{not json")
    publish(worker, {"id": str(uuid.uuid4())}, routing_key="order.placed.v1", body=json.dumps({"id": "nope"}))
    bad_data = order_placed()
    del bad_data["data"]["items"]
    publish(worker, bad_data)

    worker.drain(idle_seconds=0.3)

    assert worker.stats["retried"] == 0
    assert depth(worker, worker.dlq("order-email")) == 3


def test_message_held_by_a_crashed_worker_is_redelivered(cfg, connection):
    mailer = FakeMailer()
    worker = make_worker(cfg, connection, mailer)
    event = order_placed()
    publish(worker, event)

    # A worker takes the message and dies before acking (channel closes).
    doomed = connection.channel()
    method, _, _ = doomed.basic_get(worker.main_queue("order-email"))
    assert method is not None
    doomed.close()

    worker.drain(idle_seconds=0.3)
    assert len(mailer.sent) == 1


def test_low_stock_alert_only_when_stock_drops_into_the_low_zone(cfg, connection):
    mailer = FakeMailer()
    worker = make_worker(cfg, connection, mailer)
    publish(worker, inventory_changed(quantity=3, delta=-2))   # alert
    publish(worker, inventory_changed(quantity=50, delta=-1))  # plenty left
    publish(worker, inventory_changed(quantity=2, delta=+1))   # restock

    worker.drain(idle_seconds=0.3)

    [mail] = mailer.sent
    assert mail["to"] == cfg.owner_alert_email
    assert mail["subject"] == "Low stock: 3 left"


def test_order_without_email_is_recorded_as_skipped(cfg, connection):
    mailer = FakeMailer()
    worker = make_worker(cfg, connection, mailer)
    event = order_placed(email=None)
    publish(worker, event)
    worker.drain(idle_seconds=0.3)
    assert mailer.sent == []
    outcome = worker.db.execute(f'SELECT outcome FROM "{cfg.db_schema}".processed_event WHERE event_id = %s',
                                [event["id"]]).fetchone()[0]
    assert outcome.startswith("skipped")


def test_consumer_fixtures_match_the_contracts():
    jsonschema = pytest.importorskip("jsonschema")
    if not CONTRACTS.exists():
        pytest.skip("contracts directory not available")
    load = lambda name: json.loads((CONTRACTS / name).read_text())  # noqa: E731
    checker = jsonschema.FormatChecker()
    for env, data_schema in [(order_placed(), "order.placed.v1.schema.json"),
                             (inventory_changed(3, -1), "inventory.changed.v1.schema.json")]:
        jsonschema.validate(env, load("envelope.v1.schema.json"), format_checker=checker)
        jsonschema.validate(env["data"], load(data_schema), format_checker=checker)
