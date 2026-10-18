"""Delivers webhooks to commerce-core: at least once, retried with backoff, unordered on purpose under chaos."""

import json
import logging
import random
import threading
import urllib.error
import urllib.request

from psycopg import sql

log = logging.getLogger("payments.webhooks")

# Fixed: the private network's name for commerce-core's narrow webhook endpoint.
TARGET = "http://commerce-core:8000/api/commerce/payments/webhook"
MAX_ATTEMPTS = 10


def backoff_seconds(attempts):
    return min(300, 2 ** attempts) * random.uniform(0.5, 1.0)


class Deliverer:
    def __init__(self, pool, schema, token, target=TARGET, send=None):
        self.pool, self.schema, self.token, self.target = pool, sql.Identifier(schema), token, target
        self._send = send or self._post
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _post(self, payload):
        req = urllib.request.Request(self.target, data=json.dumps(payload).encode(), method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.token.get()}"})
        try:
            with self._opener.open(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            if err.code == 401:
                self.token.forget()
            return err.code

    def deliver_due(self, limit=20):
        """One pass over due webhooks. Returns how many were delivered."""
        delivered = 0
        q = lambda t: sql.SQL(t).format(s=self.schema)  # noqa: E731
        with self.pool.connection() as conn, conn.transaction():
            mode = conn.execute(q("SELECT mode FROM {s}.chaos WHERE id = 1")).fetchone()["mode"]
            due = conn.execute(q("""
                SELECT id, payload, attempts FROM {s}.webhook
                WHERE delivered_at IS NULL AND NOT dead AND next_attempt_at <= now()
                ORDER BY next_attempt_at LIMIT %s FOR UPDATE SKIP LOCKED"""), [limit]).fetchall()
            if mode == "out_of_order":
                random.shuffle(due)
            for hook in due:
                try:
                    status = self._send(hook["payload"])
                    error = None if 200 <= status < 300 else f"HTTP {status}"
                except Exception as exc:  # noqa: BLE001 - any failure is retried
                    error = exc.__class__.__name__
                if error is None:
                    conn.execute(q("UPDATE {s}.webhook SET delivered_at = now(), attempts = attempts + 1 WHERE id = %s"),
                                 [hook["id"]])
                    delivered += 1
                    continue
                attempts = hook["attempts"] + 1
                conn.execute(q("""
                    UPDATE {s}.webhook SET attempts = %s, last_error = %s, dead = %s,
                        next_attempt_at = now() + make_interval(secs => %s)
                    WHERE id = %s"""), [attempts, error, attempts >= MAX_ATTEMPTS, backoff_seconds(attempts), hook["id"]])
                log.warning("webhook %s failed (%s), attempt %d", hook["payload"]["type"], error, attempts)
        return delivered

    def run(self, stop, interval=0.5):
        while not stop.is_set():
            try:
                if self.deliver_due() == 0:
                    stop.wait(interval)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive; the database may be restarting
                log.warning("webhook loop: %s", exc)
                stop.wait(2)

    def start(self):
        stop = threading.Event()
        threading.Thread(target=self.run, args=(stop,), daemon=True, name="webhooks").start()
        return stop
