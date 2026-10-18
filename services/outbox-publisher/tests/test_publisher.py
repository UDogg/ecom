"""Integration tests: real PostgreSQL and RabbitMQ.

    POSTGRES_HOST=localhost OUTBOX_PUBLISHER_DB_USER=postgres \
    RABBITMQ_URL=amqp://guest:guest@localhost:5672/%2F pytest
"""

import json
import os
import sys
import threading
import uuid
from pathlib import Path

import pika
import pika.exceptions
import psycopg
import pytest
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publisher  # noqa: E402

def _contracts_dir():
    if os.environ.get("CONTRACTS_DIR"):
        return Path(os.environ["CONTRACTS_DIR"])
    parents = Path(__file__).resolve().parents
    return parents[3] / "docs/contracts/events" if len(parents) > 3 else Path("/nonexistent")


CONTRACTS = _contracts_dir()

# Mirrors commerce-core's outbox_event (outbox/migrations/0001_initial.py) for
# the columns the publisher touches.
DDL = """
CREATE TABLE {schema}.outbox_event (
    id uuid PRIMARY KEY,
    event_type varchar(100) NOT NULL,
    schema_ver smallint NOT NULL,
    aggregate_type varchar(50) NOT NULL,
    aggregate_id varchar(64) NOT NULL,
    payload jsonb NOT NULL,
    trace_ctx jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    published_at timestamptz,
    publish_attempts integer NOT NULL DEFAULT 0
)
"""


@pytest.fixture
def cfg():
    os.environ.setdefault("POSTGRES_HOST", "localhost")
    os.environ.setdefault("OUTBOX_PUBLISHER_DB_USER", "postgres")
    os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")
    schema = f"outbox_test_{uuid.uuid4().hex[:8]}"
    config = publisher.Config()
    config.table = f"{schema}.outbox_event"
    config.exchange = f"test.events.{uuid.uuid4().hex[:8]}"
    config.batch_size = 10
    with psycopg.connect(config.dsn, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(DDL.format(schema=schema))
    yield config
    with psycopg.connect(config.dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def db(cfg):
    with psycopg.connect(cfg.dsn, row_factory=psycopg.rows.dict_row) as conn:
        yield conn


@pytest.fixture
def broker(cfg):
    connection = pika.BlockingConnection(pika.URLParameters(cfg.rabbitmq_url))
    channel = connection.channel()
    channel.confirm_delivery()
    publisher.declare_topology(channel, cfg.exchange)
    capture = f"{cfg.exchange}.capture"
    channel.queue_declare(capture, exclusive=True)
    channel.queue_bind(capture, cfg.exchange, routing_key="#")
    channel.capture = capture
    yield channel
    channel.exchange_delete(cfg.exchange)
    channel.exchange_delete(cfg.exchange + publisher.UNROUTED_SUFFIX)
    channel.queue_delete(cfg.exchange + publisher.UNROUTED_SUFFIX)
    connection.close()


def add_event(db, cfg, event_type="product.created.v1", data=None, trace=None):
    event_id = uuid.uuid4()
    db.execute(
        publisher.sql.SQL("INSERT INTO {} (id, event_type, schema_ver, aggregate_type, aggregate_id, payload, trace_ctx) "
                          "VALUES (%s, %s, 1, 'product', %s, %s, %s)").format(publisher.table_identifier(cfg.table)),
        [event_id, event_type, str(uuid.uuid4()), Jsonb(data or {"sku": "X"}), Jsonb(trace) if trace else None],
    )
    db.commit()
    return event_id


def drain(channel, queue):
    messages = []
    while True:
        method, props, body = channel.basic_get(queue, auto_ack=True)
        if method is None:
            return messages
        messages.append((method, props, json.loads(body)))


def pending(db, cfg):
    return db.execute(publisher.sql.SQL("SELECT count(*) AS n FROM {} WHERE published_at IS NULL")
                      .format(publisher.table_identifier(cfg.table))).fetchone()["n"]


def test_publishes_in_commit_order_with_envelope_and_properties(db, cfg, broker):
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    ids = [add_event(db, cfg, trace={"traceparent": traceparent}),
           add_event(db, cfg, "order.placed.v1"),
           add_event(db, cfg, "inventory.changed.v1")]

    assert publisher.publish_batch(db, broker, cfg) == 3
    messages = drain(broker, broker.capture)

    assert [m[2]["id"] for m in messages] == [str(i) for i in ids]
    method, props, env = messages[0]
    assert method.routing_key == "product.created.v1"
    assert env["type"] == "product.created" and env["version"] == 1 and env["occurred_at"].endswith("Z")
    assert props.message_id == env["id"]
    assert props.delivery_mode == 2  # persistent
    assert props.content_type == "application/json"
    assert props.headers["traceparent"] == traceparent
    assert pending(db, cfg) == 0
    assert publisher.publish_batch(db, broker, cfg) == 0


def test_envelope_matches_the_published_contract(db, cfg, broker):
    jsonschema = pytest.importorskip("jsonschema")
    if not CONTRACTS.exists():
        pytest.skip("contracts directory not available")
    schema = json.loads((CONTRACTS / "envelope.v1.schema.json").read_text())
    add_event(db, cfg)
    publisher.publish_batch(db, broker, cfg)
    [(_, _, env)] = drain(broker, broker.capture)
    jsonschema.validate(env, schema, format_checker=jsonschema.FormatChecker())


def test_events_with_no_bound_queue_are_kept_not_lost(db, cfg, broker):
    broker.queue_unbind(broker.capture, cfg.exchange, routing_key="#")
    add_event(db, cfg)
    assert publisher.publish_batch(db, broker, cfg) == 1
    assert len(drain(broker, cfg.exchange + publisher.UNROUTED_SUFFIX)) == 1


class FlakyChannel:
    """Delegates to a real channel but fails from the n-th publish on."""

    def __init__(self, real, fail_from):
        self.real, self.fail_from, self.count = real, fail_from, 0

    def basic_publish(self, **kwargs):
        self.count += 1
        if self.count >= self.fail_from:
            raise pika.exceptions.StreamLostError("connection reset")
        return self.real.basic_publish(**kwargs)


def test_broker_failure_mid_batch_keeps_unsent_rows(db, cfg, broker):
    for _ in range(4):
        add_event(db, cfg)

    with pytest.raises(publisher.BrokerUnavailable):
        publisher.publish_batch(db, FlakyChannel(broker, fail_from=3), cfg)

    assert len(drain(broker, broker.capture)) == 2
    assert pending(db, cfg) == 2
    attempts = db.execute(publisher.sql.SQL("SELECT publish_attempts FROM {} WHERE published_at IS NULL "
                                            "ORDER BY created_at").format(publisher.table_identifier(cfg.table))
                          ).fetchall()
    assert [r["publish_attempts"] for r in attempts] == [1, 0]

    # Broker back: the backlog drains, nothing duplicated or lost.
    assert publisher.publish_batch(db, broker, cfg) == 2
    assert len(drain(broker, broker.capture)) == 2
    assert pending(db, cfg) == 0


def test_concurrent_publishers_never_send_a_row_twice(db, cfg, broker):
    for _ in range(30):
        add_event(db, cfg)
    cfg.batch_size = 5
    barrier = threading.Barrier(3)
    totals = []

    def worker():
        conn = pika.BlockingConnection(pika.URLParameters(cfg.rabbitmq_url))
        channel = conn.channel()
        channel.confirm_delivery()
        with psycopg.connect(cfg.dsn, row_factory=psycopg.rows.dict_row) as mydb:
            barrier.wait()
            n = 0
            while (batch := publisher.publish_batch(mydb, channel, cfg)):
                n += batch
            totals.append(n)
        conn.close()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = [m[2]["id"] for m in drain(broker, broker.capture)]
    assert sum(totals) == 30
    assert len(ids) == len(set(ids)) == 30


def test_run_loop_recovers_after_broker_outage(db, cfg, broker):
    """Scenario C in miniature: start with a dead broker URL, then fix it."""
    good_url = cfg.rabbitmq_url
    cfg.rabbitmq_url = "amqp://guest:guest@localhost:1/%2F"
    cfg.max_backoff_seconds = 0.2
    cfg.poll_seconds = 0.05
    add_event(db, cfg)
    stop, health = threading.Event(), publisher.Health()
    thread = threading.Thread(target=publisher.run, args=(cfg, stop, health))
    thread.start()
    try:
        stop.wait(0.5)
        assert pending(db, cfg) == 1  # broker down: the event waits in the outbox
        cfg.rabbitmq_url = good_url
        for _ in range(100):
            if pending(db, cfg) == 0:
                break
            stop.wait(0.1)
        assert pending(db, cfg) == 0
        assert health.ready()
    finally:
        stop.set()
        thread.join(5)
    assert len(drain(broker, broker.capture)) == 1


def test_backoff_is_capped_and_jittered():
    delays = publisher.backoff_delays(max_seconds=4, base=0.5)
    sample = [next(delays) for _ in range(20)]
    assert all(0 <= d <= 4 for d in sample)
    assert len(set(sample)) > 1


def test_table_name_cannot_inject_sql():
    ident = publisher.table_identifier('commerce.outbox_event"; DROP TABLE x; --')
    assert "DROP TABLE x" in ident.as_string(None) and ident.as_string(None).count('"') >= 4
