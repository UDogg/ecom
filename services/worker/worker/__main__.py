"""python -m worker run | status | replay <consumer> [--limit N]"""

import argparse
import json
import logging
import os
import random
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pika
import pika.exceptions
import psycopg

from . import store
from .config import Config
from .consumer import Worker
from .handlers import CONSUMERS, SmtpSender

log = logging.getLogger("worker")


def amqp_parameters(url):
    """Short heartbeats so a hung or paused broker is noticed in seconds, not minutes."""
    params = pika.URLParameters(url)
    params.heartbeat = int(os.environ.get("AMQP_HEARTBEAT_SECONDS", "10"))
    params.blocked_connection_timeout = 30
    params.socket_timeout = 10
    return params


def connect(cfg, consumer_names):
    db = psycopg.connect(cfg.dsn)
    store.ensure_schema(db, cfg.db_schema)
    connection = pika.BlockingConnection(amqp_parameters(cfg.rabbitmq_url))
    channel = connection.channel()
    channel.confirm_delivery()
    worker = Worker(cfg, [CONSUMERS[n] for n in consumer_names], SmtpSender(cfg.smtp_host, cfg.smtp_port, cfg.mail_from),
                    db, channel)
    worker.declare()
    return worker, db, connection


def serve_health(port, state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                code, body = 200, {"status": "ok", "service": "worker"}
            elif self.path == "/readyz":
                ok = state.get("connected", False)
                code, body = (200 if ok else 503), {"status": "ready" if ok else "unavailable", **state.get("stats", {})}
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


def run(cfg, consumer_names, stop):
    state = {"connected": False}
    serve_health(cfg.health_port, state)
    attempt, last_purge = 0, 0.0
    while not stop.is_set():
        db = connection = None
        try:
            worker, db, connection = connect(cfg, consumer_names)
            worker.consume()
            state.update(connected=True, stats=worker.stats)
            attempt = 0
            log.info("consuming %s", ", ".join(worker.main_queue(n) for n in consumer_names))
            while not stop.is_set():
                connection.process_data_events(time_limit=1)
                if time.monotonic() - last_purge > 3600:
                    store.purge(db, cfg.db_schema)
                    last_purge = time.monotonic()
        except (pika.exceptions.AMQPError, psycopg.OperationalError, OSError) as exc:
            state["connected"] = False
            delay = random.uniform(0, min(cfg.max_backoff_seconds, 0.5 * 2 ** attempt))
            attempt += 1
            log.warning("%s: %s; reconnecting in %.1fs", exc.__class__.__name__, exc, delay)
            stop.wait(delay)
        finally:
            for closeable in (connection, db):
                try:
                    if closeable is not None:
                        closeable.close()
                except Exception:  # noqa: BLE001 - best effort on the way out
                    pass


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(),
                        format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"worker","msg":"%(message)s"}')
    logging.getLogger("pika").setLevel(logging.WARNING)  # its INFO is per-connection chatter
    parser = argparse.ArgumentParser(prog="python -m worker")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run", help="consume events until stopped")
    run_cmd.add_argument("--consumers", default=os.environ.get("WORKER_CONSUMERS", ",".join(CONSUMERS)))
    sub.add_parser("status", help="print queue depths, including retry and dead-letter queues")
    replay_cmd = sub.add_parser("replay", help="move dead-lettered messages back to the main queue")
    replay_cmd.add_argument("consumer", choices=sorted(CONSUMERS))
    replay_cmd.add_argument("--limit", type=int)
    args = parser.parse_args()
    cfg = Config()

    if args.command == "run":
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        run(cfg, [n.strip() for n in args.consumers.split(",") if n.strip()], stop)
        return

    worker, db, connection = connect(cfg, sorted(CONSUMERS))
    try:
        if args.command == "status":
            print(json.dumps(worker.status(), indent=2))
        else:
            print(f"replayed {worker.replay(args.consumer, args.limit)} message(s) to {worker.main_queue(args.consumer)}")
    finally:
        connection.close()
        db.close()


if __name__ == "__main__":
    main()
