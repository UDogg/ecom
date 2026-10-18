"""RabbitMQ topology and delivery handling.

For every consumer `<name>`:

    commerce.events --(routing keys)--> worker.<name>            main queue
    worker.<name>.retry.<i>   TTL = retry_delays_ms[i], dead-letters back to the main queue
    worker.<name>.dlq         parked messages; `python -m worker replay <name>` sends them back

A failed delivery is re-published to the next retry queue (with an
`x-attempt` header) and only then acked, so nothing is lost between the two.
After the last retry it goes to the DLQ with the error attached. Malformed
messages skip the retries. Prefetch bounds in-flight work per worker.
"""

import json
import logging
import time
import uuid

import pika
import pika.exceptions
import psycopg

from . import store
from .handlers import PermanentError

log = logging.getLogger("worker")

REQUIRED_ENVELOPE_KEYS = ("id", "type", "version", "data")


class Context:
    def __init__(self, config, mailer):
        self.config, self.mailer = config, mailer


def parse_envelope(body):
    try:
        envelope = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PermanentError(f"not JSON: {exc}") from exc
    if not isinstance(envelope, dict) or any(k not in envelope for k in REQUIRED_ENVELOPE_KEYS):
        raise PermanentError("not an event envelope")
    try:
        uuid.UUID(str(envelope["id"]))
    except ValueError as exc:
        raise PermanentError("event id is not a UUID") from exc
    if not isinstance(envelope["data"], dict):
        raise PermanentError("data must be an object")
    return envelope


class Worker:
    def __init__(self, config, consumers, mailer, db, channel):
        self.config, self.consumers, self.db, self.channel = config, consumers, db, channel
        self.ctx = Context(config, mailer)
        self.stats = {"processed": 0, "duplicates": 0, "retried": 0, "dead_lettered": 0}

    # -- names -------------------------------------------------------------
    def main_queue(self, name):
        return f"{self.config.queue_prefix}.{name}"

    def retry_queue(self, name, i):
        return f"{self.main_queue(name)}.retry.{i}"

    def dlq(self, name):
        return f"{self.main_queue(name)}.dlq"

    # -- topology ----------------------------------------------------------
    def declare(self):
        ch, exchange = self.channel, self.config.exchange
        unrouted = exchange + ".unrouted"
        # Same declaration as outbox-publisher; whichever starts first creates it.
        ch.exchange_declare(unrouted, exchange_type="fanout", durable=True)
        ch.queue_declare(unrouted, durable=True)
        ch.queue_bind(unrouted, unrouted)
        ch.exchange_declare(exchange, exchange_type="topic", durable=True, arguments={"alternate-exchange": unrouted})
        for consumer in self.consumers:
            main = self.main_queue(consumer.name)
            ch.queue_declare(main, durable=True)
            for key in consumer.routing_keys:
                ch.queue_bind(main, exchange, routing_key=key)
            for i, delay in enumerate(self.config.retry_delays_ms):
                ch.queue_declare(self.retry_queue(consumer.name, i), durable=True, arguments={
                    "x-message-ttl": delay,
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": main,
                })
            ch.queue_declare(self.dlq(consumer.name), durable=True)

    def _republish(self, queue, body, props, headers):
        self.channel.basic_publish(exchange="", routing_key=queue, body=body, properties=pika.BasicProperties(
            message_id=props.message_id, type=props.type, content_type=props.content_type,
            delivery_mode=pika.DeliveryMode.Persistent, timestamp=props.timestamp,
            headers={**(props.headers or {}), **headers},
        ))

    # -- delivery ----------------------------------------------------------
    def handle(self, consumer, method, props, body):
        attempt = int((props.headers or {}).get("x-attempt", 0))
        try:
            envelope = parse_envelope(body)
            outcome = store.run_once(self.db, self.config.db_schema, consumer.name, envelope["id"],
                                     lambda: consumer.handle(envelope, self.ctx))
            self.stats["duplicates" if outcome == "duplicate" else "processed"] += 1
            log.info("%s %s %s: %s", consumer.name, envelope["type"], envelope["id"], outcome)
        except psycopg.OperationalError:
            raise  # our database is gone: don't ack; the reconnect loop takes over and the message is redelivered
        except PermanentError as exc:
            self._dead_letter(consumer, props, body, attempt, exc)
        except Exception as exc:  # noqa: BLE001 - anything else is treated as transient
            if attempt + 1 >= self.config.max_attempts:
                self._dead_letter(consumer, props, body, attempt, exc)
            else:
                self._republish(self.retry_queue(consumer.name, attempt), body, props,
                                {"x-attempt": attempt + 1, "x-last-error": repr(exc)[:500]})
                self.stats["retried"] += 1
                log.warning("%s attempt %d failed (%r); retrying in %d ms", consumer.name, attempt + 1, exc,
                            self.config.retry_delays_ms[attempt])
        self.channel.basic_ack(method.delivery_tag)

    def _dead_letter(self, consumer, props, body, attempt, exc):
        self._republish(self.dlq(consumer.name), body, props, {
            "x-attempt": attempt + 1, "x-last-error": repr(exc)[:500],
            "x-dead-lettered-at": int(time.time()), "x-permanent": isinstance(exc, PermanentError),
        })
        self.stats["dead_lettered"] += 1
        log.error("%s dead-lettered message %s after %d attempt(s): %r", consumer.name, props.message_id,
                  attempt + 1, exc)

    # -- loops ---------------------------------------------------------------
    def consume(self):
        self.channel.basic_qos(prefetch_count=self.config.prefetch)
        for consumer in self.consumers:
            self.channel.basic_consume(
                self.main_queue(consumer.name),
                on_message_callback=lambda ch, m, p, b, c=consumer: self.handle(c, m, p, b),
            )

    def drain(self, idle_seconds=1.0, timeout=30.0):
        """Process until every main queue stays empty for `idle_seconds` (tests, one-off jobs)."""
        deadline, idle_since = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            got = False
            for consumer in self.consumers:
                method, props, body = self.channel.basic_get(self.main_queue(consumer.name))
                if method is not None:
                    got = True
                    self.handle(consumer, method, props, body)
            if got:
                idle_since = None
                continue
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since >= idle_seconds:
                return
            self.channel.connection.sleep(0.05)
        raise TimeoutError("queues did not go idle")

    # -- operations ----------------------------------------------------------
    def replay(self, consumer_name, limit=None):
        """Move dead-lettered messages back to the main queue with a fresh retry budget."""
        moved = 0
        while limit is None or moved < limit:
            method, props, body = self.channel.basic_get(self.dlq(consumer_name))
            if method is None:
                break
            replays = int((props.headers or {}).get("x-replays", 0)) + 1
            self._republish(self.main_queue(consumer_name), body, props, {"x-attempt": 0, "x-replays": replays})
            self.channel.basic_ack(method.delivery_tag)
            moved += 1
        return moved

    def status(self):
        depths = {}
        for consumer in self.consumers:
            names = [self.main_queue(consumer.name), self.dlq(consumer.name),
                     *(self.retry_queue(consumer.name, i) for i in range(len(self.config.retry_delays_ms)))]
            for name in names:
                depths[name] = self.channel.queue_declare(name, passive=True).method.message_count
        return depths
