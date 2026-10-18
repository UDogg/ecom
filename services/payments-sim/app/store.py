"""The simulated provider's ledger.

Every charge has a `version` that goes up on each change. Webhooks carry the
charge snapshot with its version, so a consumer can apply them idempotently
and in any order (keep the highest version): exactly what a real provider's
at-least-once, unordered webhooks demand.
"""

import secrets
import uuid

from psycopg import sql
from psycopg.types.json import Jsonb

# What a card token does. Anything else is refused as an invalid source.
OUTCOMES = {
    "tok_visa": ("succeeded", None),
    "tok_mastercard": ("succeeded", None),
    "tok_declined": ("failed", "card_declined"),
    "tok_insufficient": ("failed", "insufficient_funds"),
    "tok_timeout": ("succeeded", None),  # succeeds at the provider, but the answer arrives too late
}
CHAOS_MODES = ("none", "decline_all", "slow", "timeout_after_capture", "duplicate_webhooks", "out_of_order")


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def ensure_schema(conn, schema):
    s = sql.Identifier(schema)
    if not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", [schema]).fetchone():
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(s))
    conn.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {s}.charge (
            id              text        PRIMARY KEY,
            idempotency_key text        NOT NULL UNIQUE,
            order_ref       text        NOT NULL,
            amount_minor    bigint      NOT NULL CHECK (amount_minor > 0),
            currency        char(3)     NOT NULL,
            source          text        NOT NULL,
            status          text        NOT NULL CHECK (status IN ('succeeded', 'failed', 'refunded')),
            failure_code    text,
            refunded_minor  bigint      NOT NULL DEFAULT 0 CHECK (refunded_minor >= 0),
            version         int         NOT NULL DEFAULT 1,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CHECK (refunded_minor <= amount_minor)
        );
        CREATE TABLE IF NOT EXISTS {s}.refund (
            idempotency_key text        PRIMARY KEY,
            charge_id       text        NOT NULL REFERENCES {s}.charge (id),
            amount_minor    bigint      NOT NULL CHECK (amount_minor > 0),
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS {s}.webhook (
            id              uuid        PRIMARY KEY,
            charge_id       text        NOT NULL,
            payload         jsonb       NOT NULL,
            attempts        int         NOT NULL DEFAULT 0,
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            delivered_at    timestamptz,
            dead            boolean     NOT NULL DEFAULT false,
            last_error      text
        );
        CREATE INDEX IF NOT EXISTS webhook_due ON {s}.webhook (next_attempt_at) WHERE delivered_at IS NULL AND NOT dead;
        CREATE TABLE IF NOT EXISTS {s}.chaos (
            id         int  PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            mode       text NOT NULL DEFAULT 'none',
            latency_ms int  NOT NULL DEFAULT 0
        );
        INSERT INTO {s}.chaos (id) VALUES (1) ON CONFLICT DO NOTHING;
    """).format(s=s))
    conn.commit()


def charge_view(row):
    return {"id": row["id"], "idempotency_key": row["idempotency_key"], "order_ref": row["order_ref"],
            "amount_minor": row["amount_minor"], "currency": row["currency"], "status": row["status"],
            "failure_code": row["failure_code"], "refunded_minor": row["refunded_minor"], "version": row["version"]}


class Store:
    def __init__(self, pool, schema="payments"):
        self.pool, self.schema = pool, sql.Identifier(schema)

    def q(self, text):
        return sql.SQL(text).format(s=self.schema)

    def chaos(self, conn=None):
        """(mode, latency_ms). Rows are dicts, so unpack explicitly."""
        def read(c):
            row = c.execute(self.q("SELECT mode, latency_ms FROM {s}.chaos WHERE id = 1")).fetchone()
            return row["mode"], row["latency_ms"]

        if conn is not None:
            return read(conn)
        with self.pool.connection() as c:
            return read(c)

    def set_chaos(self, mode, latency_ms):
        with self.pool.connection() as c:
            c.execute(self.q("UPDATE {s}.chaos SET mode = %s, latency_ms = %s WHERE id = 1"), [mode, latency_ms])

    def _enqueue(self, conn, charge, event_type, copies=1):
        payload = {"event_id": "", "type": event_type, "charge": charge_view(charge)}
        for _ in range(copies):
            event_id = str(uuid.uuid4())
            conn.execute(self.q("INSERT INTO {s}.webhook (id, charge_id, payload) VALUES (%s, %s, %s)"),
                         [event_id, charge["id"], Jsonb({**payload, "event_id": event_id})])

    def create_charge(self, idempotency_key, order_ref, amount_minor, currency, source):
        """Idempotent by key: a retry gets the original charge; a different request with the same key is a conflict."""
        with self.pool.connection() as conn, conn.transaction():
            mode, _ = self.chaos(conn)
            status, failure = OUTCOMES[source]
            if mode == "decline_all":
                status, failure = "failed", "card_declined"
            row = conn.execute(self.q("""
                INSERT INTO {s}.charge (id, idempotency_key, order_ref, amount_minor, currency, source, status, failure_code)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING *"""), ["ch_" + secrets.token_hex(12), idempotency_key, order_ref, amount_minor, currency, source,
                                  status, failure]).fetchone()
            if row is None:
                row = conn.execute(self.q("SELECT * FROM {s}.charge WHERE idempotency_key = %s"), [idempotency_key]).fetchone()
                if (row["order_ref"], row["amount_minor"], row["currency"]) != (order_ref, amount_minor, currency):
                    raise Conflict("idempotency key reused with a different charge")
                return charge_view(row), False
            self._enqueue(conn, row, "charge." + status, copies=2 if mode == "duplicate_webhooks" else 1)
            return charge_view(row), True

    def get_charge(self, idempotency_key):
        with self.pool.connection() as conn:
            row = conn.execute(self.q("SELECT * FROM {s}.charge WHERE idempotency_key = %s"), [idempotency_key]).fetchone()
        if row is None:
            raise NotFound(idempotency_key)
        return charge_view(row)

    def refund(self, charge_key, refund_key, amount_minor):
        """Idempotent by refund key. Refunds the remaining amount at most."""
        with self.pool.connection() as conn, conn.transaction():
            charge = conn.execute(self.q("SELECT * FROM {s}.charge WHERE idempotency_key = %s FOR UPDATE"),
                                  [charge_key]).fetchone()
            if charge is None:
                raise NotFound(charge_key)
            done = conn.execute(self.q("SELECT 1 FROM {s}.refund WHERE idempotency_key = %s"), [refund_key]).fetchone()
            if done:
                return charge_view(charge)
            if charge["status"] == "failed":
                raise Conflict("a failed charge cannot be refunded")
            remaining = charge["amount_minor"] - charge["refunded_minor"]
            if amount_minor > remaining:
                raise Conflict(f"only {remaining} left to refund")
            conn.execute(self.q("INSERT INTO {s}.refund (idempotency_key, charge_id, amount_minor) VALUES (%s, %s, %s)"),
                         [refund_key, charge["id"], amount_minor])
            refunded = charge["refunded_minor"] + amount_minor
            charge = conn.execute(self.q("""
                UPDATE {s}.charge SET refunded_minor = %s, status = CASE WHEN %s = amount_minor THEN 'refunded' ELSE status END,
                    version = version + 1, updated_at = now()
                WHERE id = %s RETURNING *"""), [refunded, refunded, charge["id"]]).fetchone()
            mode, _ = self.chaos(conn)
            self._enqueue(conn, charge, "charge.refunded", copies=2 if mode == "duplicate_webhooks" else 1)
            return charge_view(charge)
